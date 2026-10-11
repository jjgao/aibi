"""The core's checks on what a pack's importer returns, and on what it raises (SPEC §10.1, §14,
D400), with the test-only ``birds`` pack's importer wrapped to return or raise something else."""

import json
import logging
import os
import sys
from collections.abc import Callable
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Any, ClassVar, cast
from zoneinfo import ZoneInfo

import pytest

import aibi
from aibi.core.importers.errors import ImportRefused
from aibi.core.schema.descriptors import TableDescriptor
from aibi.core.schema.limits import ImportLimits
from aibi.core.schema.output import DataSegment, Segment, TextSegment, data, text
from aibi.core.schema.pack_api import (
    ConfinedPath,
    ImportNote,
    ImportOptions,
    ImportResult,
    PackRegistry,
    Refused,
    Reshaped,
)
from aibi.core.schema.refusals import Refusal
from aibi.core.store.build import Layout
from aibi.core.store.sources import ErrorCell, TextSource, TypedSource

Roots = Any
Importing = Any
Birds = Any
Change = Callable[[ImportResult], object]
SECRET = "s3cr3t-cell"


class Wrapped:
    """The birds importer, its result changed by ``change``, or ``raises`` raised instead."""

    def __init__(self, inner: Any, change: Change | None, raises: BaseException | None) -> None:
        self.inner, self.change, self.raises = inner, change, raises

    def import_source(self, source: ConfinedPath, options: ImportOptions) -> Any:
        if self.raises is not None:
            raise self.raises
        result = self.inner.import_source(source, options)
        return result if self.change is None else self.change(result)


def _import(
    roots: Roots,
    importing: Importing,
    birds: Birds,
    change: Change | None = None,
    *,
    raises: BaseException | None = None,
    survey: dict[str, Any] | None = None,
    **options: Any,
) -> Any:
    written = birds.survey() if survey is None else survey
    path = roots.write("survey.json", json.dumps(written).encode())
    pack = replace(birds.pack, importer=Wrapped(birds.importer, change, raises))
    registry = PackRegistry([pack], core_version=aibi.__version__)
    return importing(path, registry=registry, pack="birds", **options)


def _refused(
    roots: Roots,
    importing: Importing,
    birds: Birds,
    change: Change | None = None,
    **given: Any,
) -> Refusal:
    with pytest.raises(ImportRefused) as refused:
        _import(roots, importing, birds, change, **given)
    [refusal] = refused.value.refusals
    return refusal


def _limit(refusal: Refusal) -> str | None:
    return None if refusal.limit is None else refusal.limit.name


def _said(refusal: Refusal) -> str:
    return "".join(json.dumps(segment.model_dump()) for segment in refusal.message)


def _with_source(result: ImportResult, name: str, source: Any) -> ImportResult:
    return replace(result, sources={**result.sources, name: source})


def _with_counts(result: ImportResult, rows: Any) -> ImportResult:
    counts = result.sources["counts"]
    assert isinstance(counts, TypedSource)
    return _with_source(result, "counts", _bare_typed(counts.columns, tuple(rows)))


def _plain(note: ImportNote) -> tuple[Any, ...]:
    """A note as the copy holds it, for comparison: its sequences as tuples."""
    return (note.kind, note.subject, tuple(note.message), note.count, tuple(note.rows))


def _kept(note: ImportNote, imported: Any) -> bool:
    return _plain(note) in {_plain(found) for found in imported.notes}


def _with_notes(*notes: Any) -> Change:
    return lambda result: replace(result, notes=list(notes))


def test_a_checked_result_imports_as_before(
    roots: Roots, importing: Importing, birds: Birds, store: Any
) -> None:
    note = ImportNote("renamed", "counts", [text("The original name was "), data("Counts")])
    imported = _import(roots, importing, birds, _with_notes(note))
    assert _kept(note, imported)
    assert store.labels("d")


# --- What the importer raises -------------------------------------------------------------------


def test_a_refusal_of_the_reader_refuses_the_import_as_it_is(
    roots: Roots, importing: Importing, birds: Birds
) -> None:
    refusal = _refused(roots, importing, birds, limits=ImportLimits(import_bytes=10))
    assert refusal.code == "LIMIT_EXCEEDED"
    assert _limit(refusal) == "import_bytes"


def test_an_exception_of_the_importer_is_pack_failed_and_quotes_nothing(
    roots: Roots, importing: Importing, birds: Birds, caplog: pytest.LogCaptureFixture
) -> None:
    class SurveyError(Exception):
        pass

    for raised in (ValueError(SECRET), SurveyError(SECRET), RecursionError(SECRET)):
        with caplog.at_level(logging.WARNING):
            refusal = _refused(roots, importing, birds, raises=raised)
        assert refusal.code == "PACK_FAILED"
        assert "birds" in _said(refusal)
        assert SECRET not in _said(refusal)
    assert SECRET not in caplog.text
    assert "SurveyError" not in caplog.text
    assert "ValueError" in caplog.text


def test_a_refusal_the_importer_raises_refuses_the_import(
    roots: Roots, importing: Importing, birds: Birds
) -> None:
    raised = Refused([Refusal(code="birds.NO_SURVEY", path=None, message=[text("No survey")])])
    assert _refused(roots, importing, birds, raises=raised).code == "birds.NO_SURVEY"


def test_the_importer_running_out_of_memory_names_import_bytes(
    roots: Roots, importing: Importing, birds: Birds
) -> None:
    refusal = _refused(roots, importing, birds, raises=MemoryError())
    assert refusal.code == "LIMIT_EXCEEDED"
    assert _limit(refusal) == "import_bytes"


# --- The result's shape ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "change",
    [
        lambda result: None,
        lambda result: replace(result, sources=[("sites", result.sources["sites"])]),
        lambda result: _with_source(result, "sites", b"site_id\ns1\n"),
        lambda result: replace(result, layouts={**result.layouts, "sites": ("sites", ())}),
        lambda result: replace(
            result, layouts={**result.layouts, "sites": Layout("elsewhere", ())}
        ),
        lambda result: _with_source(result, "sites", TextSource(cast(Any, "site_id\ns1\n"))),
        lambda result: replace(
            result, layouts={**result.layouts, "sites": Layout("sites", cast(Any, (("site_id",),)))}
        ),
        lambda result: replace(
            result,
            layouts={**result.layouts, "sites": Layout("sites", ["site_id"])},  # type: ignore[arg-type]
        ),
        lambda result: replace(result, descriptors=None),
    ],
    ids=[
        "none",
        "listed sources",
        "bytes",
        "a tuple layout",
        "a layout on no source",
        "text that is not bytes",
        "a column that is not a pair",
        "columns that are not pairs",
        "no descriptors",
    ],
)
def test_a_result_of_another_shape_is_pack_failed(
    roots: Roots, importing: Importing, birds: Birds, store: Any, change: Change
) -> None:
    assert _refused(roots, importing, birds, change).code == "PACK_FAILED"
    assert store.labels("d") == []


def test_a_source_no_table_reads_is_pack_failed(
    roots: Roots, importing: Importing, birds: Birds, store: Any
) -> None:
    """A raw snapshot no table reads would be a blob that no typed row, and so no erasure's hold
    check, sees: it could keep a person's key in a live release after they were erased."""
    kept = TextSource(b"checklist_id\nc1\n")
    refusal = _refused(roots, importing, birds, lambda result: _with_source(result, "x", kept))
    assert refusal.code == "PACK_FAILED"
    assert "no table reads" in _said(refusal)
    assert store.labels("d") == []


# --- The limits -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("limits", "name"),
    [
        (ImportLimits(import_tables=4), "import_tables"),
        (ImportLimits(table_columns=3), "table_columns"),
        (ImportLimits(import_cells=30), "import_cells"),
        (ImportLimits(decoded_bytes=100), "decoded_bytes"),
    ],
)
def test_a_result_over_a_limit_is_refused_naming_it(
    roots: Roots, importing: Importing, birds: Birds, limits: ImportLimits, name: str
) -> None:
    refusal = _refused(roots, importing, birds, limits=limits)
    assert refusal.code == "LIMIT_EXCEEDED"
    assert _limit(refusal) == name


def test_the_limits_are_those_the_core_s_importers_count(
    roots: Roots, importing: Importing, birds: Birds
) -> None:
    """Five tables of 2, 4, 3, 2 and 3 columns, with 2, 2, 3, 2 and 3 rows: 34 cells; their
    names' and strings' UTF-8 bytes are 215."""
    _import(roots, importing, birds, limits=ImportLimits(import_tables=5, table_columns=4))
    _import(roots, importing, birds, limits=ImportLimits(import_cells=34, decoded_bytes=215))
    for limits in (ImportLimits(import_cells=33), ImportLimits(decoded_bytes=214)):
        assert _refused(roots, importing, birds, limits=limits).code == "LIMIT_EXCEEDED"


def test_a_long_string_is_refused_for_its_decoded_bytes(
    roots: Roots, importing: Importing, birds: Birds, store: Any
) -> None:
    rows = [("c1", "wren", 3), ("c2", "x" * 100_000, 2)]
    refusal = _refused(
        roots,
        importing,
        birds,
        lambda result: _with_counts(result, rows),
        limits=ImportLimits(decoded_bytes=1000),
    )
    assert _limit(refusal) == "decoded_bytes"
    assert store.labels("d") == []


def test_a_cell_longer_than_a_text_file_s_field_is_unparseable(
    roots: Roots, importing: Importing, birds: Birds
) -> None:
    rows = [("c1", "wren", 3), ("c2", SECRET * 20_000, 2)]
    refusal = _refused(roots, importing, birds, lambda result: _with_counts(result, rows))
    assert refusal.code == "UNPARSEABLE_SOURCE"
    assert SECRET not in _said(refusal)


def test_a_lone_surrogate_in_a_cell_is_unparseable(
    roots: Roots, importing: Importing, birds: Birds
) -> None:
    rows = [("c1", "wren", 3), ("c2", "\ud800x", 2)]
    refusal = _refused(roots, importing, birds, lambda result: _with_counts(result, rows))
    assert refusal.code == "UNPARSEABLE_SOURCE"


def _sites_as_text(content: bytes) -> Change:
    """The birds result with ``sites`` as a text file the core parses, as a pack may keep one."""
    source = {
        "kind": "file",
        "name": "sites",
        "original_name": "sites.csv",
        "parse": {
            "format": "csv",
            "delimiter": ",",
            "quote": '"',
            "header_row": 0,
            "skip_rows": 0,
            "encoding": "utf-8",
        },
    }

    def change(result: ImportResult) -> ImportResult:
        descriptors = []
        for descriptor in result.descriptors:
            if isinstance(descriptor, TableDescriptor) and descriptor.id == "sites":
                written = descriptor.model_dump(mode="json")
                written["fields"]["source"] = source
                written["curation"]["/fields/source"] = written["curation"]["/fields/role"]
                descriptor = TableDescriptor.model_validate(written)
            descriptors.append(descriptor)
        return replace(_with_source(result, "sites", TextSource(content)), descriptors=descriptors)

    return change


def test_a_text_file_a_pack_keeps_is_parsed_by_the_core(
    roots: Roots, importing: Importing, birds: Birds, store: Any
) -> None:
    content = b"site_id,habitat\ns1,wood\ns2,marsh\n"
    imported = _import(roots, importing, birds, _sites_as_text(content))
    release = store.load(imported.built.manifest.hash)
    assert [row["site_id"].value for row in release.tables["sites"].rows] == ["s1", "s2"]


def test_a_text_file_a_pack_keeps_is_counted_under_the_limits(
    roots: Roots, importing: Importing, birds: Birds
) -> None:
    content = b"site_id,habitat\ns1,wood\ns2,marsh\n" + b"s9,wood\n" * 20
    change = _sites_as_text(content)
    refusal = _refused(roots, importing, birds, change, limits=ImportLimits(import_cells=60))
    assert _limit(refusal) == "import_cells"
    refusal = _refused(roots, importing, birds, change, limits=ImportLimits(import_bytes=150))
    assert _limit(refusal) == "import_bytes"


# --- The notes ------------------------------------------------------------------------------------


def _note(**given: Any) -> ImportNote:
    written: dict[str, Any] = {
        "kind": "not_proposed",
        "subject": "counts",
        "message": [text("Nothing proposed")],
    }
    written.update(given)
    return ImportNote(**written)


def _unwritable(value: str) -> object:
    """A segment of the exact type that holds what its validator refuses: ``TextSegment`` cannot
    be built unchecked (D399), so a pack's is made as the attacker's code would, in place."""
    segment = text("x")
    vars(segment)["text"] = value
    return segment


@pytest.mark.parametrize(
    "note",
    [
        "a note",
        _note(kind="bogus"),
        _note(kind="dropped"),
        _note(kind="unparsed"),
        _note(kind="gap"),
        _note(kind="reimported"),
        _note(subject="x" * 4097),
        _note(subject="￾"),
        _note(subject=3),
        _note(message="Nothing proposed"),
        _note(message=[text("x")] * 65),
        _note(message=[text("x" * 10_001)]),
        _note(message=[text("x" * 6000), text("x" * 6000)]),
        _note(message=[_unwritable("\ud800")]),
        _note(message=[DataSegment.model_construct(data="x" * 201)]),
        _note(message=[{"text": "x"}]),
        _note(count=-1),
        _note(count=True),
        _note(count=2**53),
        _note(count=1.0),
        _note(count=3, rows=[1, 2, 3, 4, 5, 6]),
        _note(count=3, rows=[0]),
        _note(count=3, rows="1"),
    ],
)
def test_a_note_an_importer_does_not_write_is_pack_failed(
    roots: Roots, importing: Importing, birds: Birds, store: Any, note: Any
) -> None:
    """Without this a note of another kind was published, and every curation queue of the dataset
    then failed on it; and a pack could write the core's own notes."""
    refusal = _refused(roots, importing, birds, _with_notes(note))
    assert refusal.code == "PACK_FAILED"
    assert store.labels("d") == []


def test_notes_within_the_limits_are_kept(roots: Roots, importing: Importing, birds: Birds) -> None:
    notes = [
        _note(kind="skipped_source", subject=None, message=[text("x" * 5000), data("y")]),
        _note(count=0),
        _note(count=2**53 - 1, rows=[1, 2, 3, 4, 5]),
        _note(message=[text("x")] * 64),
    ]
    imported = _import(roots, importing, birds, _with_notes(*notes))
    assert all(_kept(note, imported) for note in notes)


def test_too_many_notes_are_pack_failed(roots: Roots, importing: Importing, birds: Birds) -> None:
    notes = [_note()] * 10_001
    assert _refused(roots, importing, birds, _with_notes(*notes)).code == "PACK_FAILED"
    _import(roots, importing, birds, _with_notes(*[_note()] * 10_000))


# --- Round 1: what passed the checks and still failed, and the checks' own gaps ----------------


def _renamed_source(result: ImportResult) -> ImportResult:
    sources = {("sites.csv" if name == "sites" else name): s for name, s in result.sources.items()}
    layouts = {
        table: Layout("sites.csv", layout.columns) if table == "sites" else layout
        for table, layout in result.layouts.items()
    }
    return replace(result, sources=sources, layouts=layouts)


def _relabelled(label: str) -> Change:
    def change(result: ImportResult) -> ImportResult:
        descriptors = [
            descriptor.model_copy(update={"label": label})
            if descriptor.id == "sites"
            else descriptor
            for descriptor in result.descriptors
        ]
        return replace(result, descriptors=descriptors)

    return change


class _Lying(str):
    """A string whose length and encoding say it is short."""

    __slots__ = ()

    def __len__(self) -> int:
        return 1

    def encode(self, encoding: str = "utf-8", errors: str = "strict") -> bytes:
        return b"x"


class _Always(str):
    """A string equal to every other, hashed as ``renamed``."""

    __slots__ = ()

    def __eq__(self, other: object) -> bool:
        return True

    def __hash__(self) -> int:
        return hash("renamed")


class _Raising(dict[str, Any]):
    def items(self) -> Any:
        raise RuntimeError(SECRET)


def _bare_typed(columns: Any, rows: Any) -> TypedSource:
    """A typed source built without its own checks."""
    made = object.__new__(TypedSource)
    object.__setattr__(made, "columns", columns)
    object.__setattr__(made, "rows", rows)
    return made


@pytest.mark.parametrize(
    "change",
    [
        _renamed_source,
        lambda result: replace(result, descriptors=[*result.descriptors, None]),
        lambda result: replace(result, descriptors=[*result.descriptors, "dataset"]),
        _relabelled("x" * 200_000),
        _relabelled("\ud800" + SECRET),
        lambda result: _with_counts(result, [("c1", "wren", 10**4200)]),
        lambda result: _with_counts(result, [("c1", "wren", 2**64)]),
        lambda result: _with_counts(result, [("c1", "wren", -(2**64))]),
        lambda result: _with_counts(result, [("c1", _Lying("x" * 200_000), 3)]),
        lambda result: _with_source(
            result, "counts", _bare_typed(("a", "b", "c"), (r for r in [("c1", "wren", 3)]))
        ),
        lambda result: _with_source(result, "counts", _bare_typed(("a", "b"), (("c1", 1, 2),))),
        lambda result: replace(result, sources=_Raising(result.sources)),
        lambda result: replace(result, layouts={**result.layouts, 1: result.layouts["sites"]}),
        lambda result: replace(
            result, layouts={**result.layouts, "sites": Layout(cast(Any, 1), ())}
        ),
        lambda result: replace(
            result,
            layouts={**result.layouts, "sites": Layout("sites", ((cast(Any, 1), "site_id"),))},
        ),
    ],
    ids=[
        "a source named as a file",
        "a descriptor that is none",
        "a descriptor that is a string",
        "a descriptor whose label is too long",
        "a descriptor whose label is no text",
        "an integer of 4,201 digits",
        "an integer of 65 bits",
        "a negative integer of 65 bits",
        "a string that says it is short",
        "rows that are a generator",
        "a row wider than its names",
        "sources whose items raise",
        "a table id that is no string",
        "a layout on a source that is no string",
        "a column id that is no string",
    ],
)
def test_a_result_the_store_cannot_take_is_pack_failed_quoting_nothing(
    roots: Roots,
    importing: Importing,
    birds: Birds,
    store: Any,
    caplog: pytest.LogCaptureFixture,
    change: Change,
) -> None:
    """Each of these passed the first checks and then failed in the store or a later check, as an
    internal error whose log quoted what the pack gave."""
    with caplog.at_level(logging.DEBUG):
        refusal = _refused(roots, importing, birds, change)
    assert refusal.code == "PACK_FAILED"
    assert SECRET not in _said(refusal)
    assert SECRET not in caplog.text
    assert store.labels("d") == []


def test_a_long_cell_under_a_name_that_is_no_text_is_refused_without_its_name(
    roots: Roots, importing: Importing, birds: Birds
) -> None:
    def change(result: ImportResult) -> ImportResult:
        rows = (("c1", "wren", "x" * 200_000),)
        return _with_source(
            result, "counts", TypedSource(("checklist_id", "species", "h\ud800"), rows)
        )

    refusal = _refused(roots, importing, birds, change)
    assert refusal.code == "UNPARSEABLE_SOURCE"
    assert "\ud800" not in _said(refusal)


def test_integers_within_64_bits_are_kept(
    roots: Roots, importing: Importing, birds: Birds, store: Any
) -> None:
    rows = [("c1", "wren", 2**64 - 1), ("c2", "curlew", 2**63), ("c2", "wren", 1)]
    imported = _import(roots, importing, birds, lambda result: _with_counts(result, rows))
    report = imported.built.reports["counts"]["count"]
    assert report.unparsed_cells == 2


@pytest.mark.parametrize(
    "raised",
    [
        Refused(()),
        ImportRefused([]),
        ImportRefused([Refusal(code="LIMIT_EXCEEDED", path=None, message=[text("Forged")])]),
    ],
    ids=["an empty Refused", "an empty ImportRefused", "a forged ImportRefused"],
)
def test_a_refusal_that_is_none_or_not_the_reader_s_is_pack_failed(
    roots: Roots, importing: Importing, birds: Birds, raised: BaseException
) -> None:
    assert _refused(roots, importing, birds, raises=raised).code == "PACK_FAILED"


def test_a_refused_holding_what_are_not_refusals_is_pack_failed(
    roots: Roots, importing: Importing, birds: Birds
) -> None:
    raised = Refused.__new__(Refused)
    raised.refusals = cast(Any, ("x",))
    assert _refused(roots, importing, birds, raises=raised).code == "PACK_FAILED"


def test_every_base_exception_is_contained_but_an_interrupt_and_an_exit(
    roots: Roots, importing: Importing, birds: Birds, caplog: pytest.LogCaptureFixture
) -> None:
    class Stop(BaseException):
        pass

    for raised in (GeneratorExit(SECRET), Stop(SECRET)):
        with caplog.at_level(logging.WARNING):
            refusal = _refused(roots, importing, birds, raises=raised)
        assert refusal.code == "PACK_FAILED"
    assert SECRET not in caplog.text
    for passed in (KeyboardInterrupt, SystemExit):
        with pytest.raises(passed) as raised_again:
            _import(roots, importing, birds, raises=passed(SECRET))
        assert type(raised_again.value) is passed
        assert SECRET not in str(raised_again.value)
        assert raised_again.value.__context__ is None


def test_running_out_of_memory_while_the_result_is_checked_names_import_bytes(
    roots: Roots, importing: Importing, birds: Birds, monkeypatch: pytest.MonkeyPatch
) -> None:
    from aibi.core.importers import checks

    def run_out(*given: Any) -> None:
        raise MemoryError

    monkeypatch.setattr(checks, "_limits", run_out)
    refusal = _refused(roots, importing, birds)
    assert (refusal.code, _limit(refusal)) == ("LIMIT_EXCEEDED", "import_bytes")


def test_a_text_file_over_import_bytes_is_refused_by_the_check(
    roots: Roots, importing: Importing, birds: Birds
) -> None:
    """``import_bytes`` is a limit per file, as the core's importers apply it: the survey file
    is within it, the text file the pack keeps is not."""
    content = b"site_id,habitat\ns1,wood\ns2,marsh\n" + b"".join(
        f"s{i},wood\n".encode() for i in range(10, 160)
    )
    survey = len(json.dumps(birds.survey()).encode())
    assert survey < 1000 < len(content)
    refusal = _refused(
        roots, importing, birds, _sites_as_text(content), limits=ImportLimits(import_bytes=1000)
    )
    assert _limit(refusal) == "import_bytes"
    assert "A text file of the source has more than 1000 bytes" in _said(refusal)
    _import(
        roots, importing, birds, _sites_as_text(content), limits=ImportLimits(import_bytes=1700)
    )


def test_a_layout_on_no_source_names_why(roots: Roots, importing: Importing, birds: Birds) -> None:
    def change(result: ImportResult) -> ImportResult:
        return replace(result, layouts={**result.layouts, "sites": Layout("elsewhere", ())})

    assert "did not return" in _said(_refused(roots, importing, birds, change))


def test_a_table_with_no_rows_counts_its_header(
    roots: Roots, importing: Importing, birds: Birds
) -> None:
    """``counts`` without rows holds 3 cells, its header's, not 0: 34 − 9 + 3 = 28."""

    def change(result: ImportResult) -> ImportResult:
        return _with_counts(result, [])

    _import(roots, importing, birds, change, limits=ImportLimits(import_cells=28))
    refusal = _refused(roots, importing, birds, change, limits=ImportLimits(import_cells=27))
    assert _limit(refusal) == "import_cells"


def test_decoded_bytes_count_utf_8_and_error_cells(
    roots: Roots, importing: Importing, birds: Birds
) -> None:
    """``é`` is 2 bytes in UTF-8 and ``#N/A`` 4: the counts table's strings grow by 1 + 4 bytes
    over the 215 of the survey."""
    from aibi.core.store.sources import ErrorCell

    rows = [("c1", "wrén", 3), ("c2", "curlew", ErrorCell("#N/A")), ("c2", "wren", 1)]

    def change(result: ImportResult) -> ImportResult:
        return _with_counts(result, rows)

    _import(roots, importing, birds, change, limits=ImportLimits(decoded_bytes=220))
    refusal = _refused(roots, importing, birds, change, limits=ImportLimits(decoded_bytes=219))
    assert _limit(refusal) == "decoded_bytes"


@pytest.mark.parametrize(
    "note",
    [
        _note(kind=["renamed"]),
        _note(kind=_Always("zzz")),
        _note(message=[DataSegment.model_construct(data="x", truncated=False)]),
        _note(subject=_Lying("x" * 5000)),
    ],
    ids=[
        "a kind that is a list",
        "a kind equal to every kind",
        "a cut that is false",
        "a subject that says it is short",
    ],
)
def test_a_note_of_another_type_is_pack_failed(
    roots: Roots, importing: Importing, birds: Birds, note: Any
) -> None:
    assert _refused(roots, importing, birds, _with_notes(note)).code == "PACK_FAILED"


def test_notes_at_the_limits_of_their_strings_are_kept(
    roots: Roots, importing: Importing, birds: Birds
) -> None:
    notes = [_note(subject="s" * 4096), _note(message=[text("x" * 6000), text("y" * 4000)])]
    imported = _import(roots, importing, birds, _with_notes(*notes))
    assert all(_kept(note, imported) for note in notes)


def test_notes_of_more_text_than_the_queue_holds_are_pack_failed(
    roots: Roots, importing: Importing, birds: Birds
) -> None:
    notes = [_note(message=[text("x" * 10_000)])] * 900
    refusal = _refused(roots, importing, birds, _with_notes(*notes))
    assert refusal.code == "PACK_FAILED"
    assert "bytes of text" in _said(refusal)


def test_a_reimport_checks_the_result_too(
    roots: Roots, birds: Birds, lifecycle: Any, store: Any
) -> None:
    path = roots.write("survey.json", json.dumps(birds.survey()).encode())
    lifecycle.import_(path, registry=birds.registry, pack="birds")
    for change in (
        _with_notes(_note(kind="dropped")),
        lambda result: _with_source(result, "x", TextSource(b"checklist_id\nc1\n")),
    ):
        pack = replace(birds.pack, importer=Wrapped(birds.importer, change, None))
        registry = PackRegistry([pack], core_version=aibi.__version__)
        with pytest.raises(ImportRefused) as refused:
            lifecycle.reimport(path, registry=registry, pack="birds")
        assert [r.code for r in refused.value.refusals] == ["PACK_FAILED"]
    assert [label.label for label in store.labels("d")] == [1]


# --- Round 2: ids, the guard around a Refused, the copy's types and fields --------------------


def _with_table_id(table: str) -> Change:
    def change(result: ImportResult) -> ImportResult:
        layouts = {
            (table if name == "sites" else name): lay for name, lay in result.layouts.items()
        }
        return replace(result, layouts=layouts)

    return change


def _with_column_id(column: str) -> Change:
    def change(result: ImportResult) -> ImportResult:
        sites = result.layouts["sites"]
        columns = ((column, sites.columns[0][1]), *sites.columns[1:])
        return replace(result, layouts={**result.layouts, "sites": Layout("sites", columns)})

    return change


@pytest.mark.parametrize(
    "change",
    [
        _with_table_id("s﷐" + SECRET),
        _with_table_id("s\ud800" + SECRET),
        _with_table_id("s" * 5000),
        _with_column_id("s￾" + SECRET),
        _with_column_id("Site ID"),
    ],
    ids=[
        "a table id with a noncharacter",
        "a table id with a surrogate",
        "a long table id",
        "a column id with a noncharacter",
        "a column id that is no identifier",
    ],
)
def test_ids_that_are_not_identifiers_are_pack_failed_quoting_nothing(
    roots: Roots, importing: Importing, birds: Birds, change: Change
) -> None:
    """These reached the build, whose messages put the id in a text segment: a 500 for an id
    that is no Unicode text, and the id quoted as text otherwise (A6)."""
    refusal = _refused(roots, importing, birds, change)
    assert refusal.code == "PACK_FAILED"
    assert SECRET not in _said(refusal)
    assert "sssss" not in _said(refusal)


def _pack_refusal(code: str) -> Refusal:
    return Refusal(code=code, path=None, message=[text("No survey")])


@pytest.mark.parametrize(
    "raised",
    [
        Refused([_pack_refusal("LIMIT_EXCEEDED")]),
        Refused([_pack_refusal("bees.NO_SURVEY")]),
        Refused([_pack_refusal("birds.NO_SURVEY"), _pack_refusal("PACK_FAILED")]),
    ],
    ids=["a core code", "another pack's code", "a core code beside its own"],
)
def test_a_pack_refuses_only_in_its_own_codes(
    roots: Roots, importing: Importing, birds: Birds, raised: BaseException
) -> None:
    """A pack that refuses with the core's codes could pass for the reader, whose refusals alone
    stand as they are (§8.6)."""
    refusal = _refused(roots, importing, birds, raises=raised)
    assert refusal.code == "PACK_FAILED"


def test_a_refused_of_another_type_is_pack_failed_and_runs_nothing_of_the_pack_s(
    roots: Roots, importing: Importing, birds: Birds, caplog: pytest.LogCaptureFixture
) -> None:
    class Lying(Refused):
        @property
        def refusals(self) -> Any:  # type: ignore[override]
            raise RuntimeError(SECRET)

        @refusals.setter
        def refusals(self, value: Any) -> None:
            pass

    class Kin(Refusal):
        pass

    lying = Lying([_pack_refusal("birds.NO_SURVEY")])
    kin = Refused([Kin(code="birds.NO_SURVEY", path=None, message=[text("No survey")])])
    for raised in (lying, kin):
        with caplog.at_level(logging.DEBUG):
            assert _refused(roots, importing, birds, raises=raised).code == "PACK_FAILED"
    assert SECRET not in caplog.text


def test_a_pack_s_refusals_are_copies(roots: Roots, importing: Importing, birds: Birds) -> None:
    given = _pack_refusal("birds.NO_SURVEY")
    refusal = _refused(roots, importing, birds, raises=Refused([given]))
    assert refusal == given
    assert refusal is not given


class _Catching:
    """An importer that reads past a limit, catches the reader's refusal, and does ``then``."""

    def __init__(
        self, then: Callable[[ImportRefused, ImportOptions], BaseException], own: int | None = None
    ) -> None:
        self.then = then
        self.own = own

    def import_source(self, source: ConfinedPath, options: ImportOptions) -> Any:
        limit = options.limits.import_bytes if self.own is None else self.own
        try:
            options.reader.read(source, limit)
        except ImportRefused as error:
            raise self.then(error, options) from None
        raise AssertionError("the reader read past its limit")


def _forged() -> ImportRefused:
    return ImportRefused([Refusal(code="LIMIT_EXCEEDED", path=None, message=[text(SECRET)])])


def _emptied(error: ImportRefused, options: ImportOptions) -> BaseException:
    error.refusals = ()
    return error


def _added(error: ImportRefused, options: ImportOptions) -> BaseException:
    error.refusals = (*error.refusals, *_forged().refusals)
    return error


def _reached(error: ImportRefused, options: ImportOptions) -> BaseException:
    reader: Any = options.reader
    for name in ("refused", "record", "_record"):
        assert not hasattr(reader, name)
    return _forged()


@pytest.mark.parametrize("then", [_emptied, _added, _reached], ids=["emptied", "added", "forged"])
def test_the_reader_s_refusal_stands_as_the_reader_made_it(
    roots: Roots,
    importing: Importing,
    birds: Birds,
    then: Callable[[ImportRefused, ImportOptions], BaseException],
) -> None:
    path = roots.write("survey.json", json.dumps(birds.survey()).encode())
    pack = replace(birds.pack, importer=_Catching(then))
    registry = PackRegistry([pack], core_version=aibi.__version__)
    with pytest.raises(ImportRefused) as refused:
        importing(path, registry=registry, pack="birds", limits=ImportLimits(import_bytes=10))
    refusals = refused.value.refusals
    if then is _reached:
        assert [r.code for r in refusals] == ["PACK_FAILED"]
    else:
        assert [(r.code, _limit(r)) for r in refusals] == [("LIMIT_EXCEEDED", "import_bytes")]
    assert all(SECRET not in _said(r) for r in refusals)


def _let_out(error: ImportRefused, options: ImportOptions) -> BaseException:
    return error


@pytest.mark.parametrize("own", [1, 9])
def test_a_limit_the_pack_chose_below_the_operator_s_is_the_pack_s_own(
    roots: Roots, importing: Importing, birds: Birds, own: int
) -> None:
    # The reader reads at most the operator's import_bytes; a smaller limit is the pack's
    # choice, so a refusal under it is the pack's failure, not the operator's limit.
    path = roots.write("survey.json", json.dumps(birds.survey()).encode())
    pack = replace(birds.pack, importer=_Catching(_let_out, own=own))
    registry = PackRegistry([pack], core_version=aibi.__version__)
    with pytest.raises(ImportRefused) as refused:
        importing(path, registry=registry, pack="birds", limits=ImportLimits(import_bytes=10))
    assert [r.code for r in refused.value.refusals] == ["PACK_FAILED"]


@pytest.mark.parametrize("limit", [10, 11, 10**9])
def test_a_limit_at_or_above_the_operator_s_is_the_operator_s(
    roots: Roots, importing: Importing, birds: Birds, limit: int
) -> None:
    path = roots.write("survey.json", json.dumps(birds.survey()).encode())
    pack = replace(birds.pack, importer=_Catching(_let_out, own=limit))
    registry = PackRegistry([pack], core_version=aibi.__version__)
    with pytest.raises(ImportRefused) as refused:
        importing(path, registry=registry, pack="birds", limits=ImportLimits(import_bytes=10))
    refusals = refused.value.refusals
    assert [(r.code, _limit(r)) for r in refusals] == [("LIMIT_EXCEEDED", "import_bytes")]


def test_a_descriptor_that_does_not_serialise_is_pack_failed_without_a_warning(
    roots: Roots, importing: Importing, birds: Birds, capsys: pytest.CaptureFixture[str]
) -> None:
    def change(result: ImportResult) -> ImportResult:
        descriptors = [
            d.model_copy(update={"fields": {"role": "entity", "x": SECRET}})
            if d.id == "sites"
            else d
            for d in result.descriptors
        ]
        return replace(result, descriptors=descriptors)

    refusal = _refused(roots, importing, birds, change)
    assert refusal.code == "PACK_FAILED"
    captured = capsys.readouterr()
    assert SECRET not in captured.out + captured.err


def test_the_copy_is_immutable(birds: Birds, roots: Roots) -> None:
    """A validator is given the copy, and can change nothing the store reads."""
    from types import MappingProxyType

    from aibi.core.importers.checks import checked

    path = roots.write("survey.json", json.dumps(birds.survey()).encode())
    result = birds.importer.import_source(roots.confinement.confine(path), roots.options())
    note = _note()
    copy = checked(replace(result, notes=[note]), ImportLimits())
    assert type(copy.sources) is MappingProxyType
    assert type(copy.layouts) is MappingProxyType
    assert type(copy.descriptors) is tuple
    assert type(copy.notes) is tuple
    assert type(copy.notes[0].message) is tuple
    assert type(copy.notes[0].rows) is tuple


def test_the_classes_the_copy_rebuilds_have_the_fields_it_copies() -> None:
    """A field added to one of these would be dropped from the copy without a word, so adding
    one fails here until ``checks`` copies it."""
    from dataclasses import fields

    from aibi.core.store.sources import ErrorCell

    found = {
        cls.__name__: tuple(f.name for f in fields(cls))
        for cls in (ImportResult, ImportNote, Layout, TypedSource, TextSource, ErrorCell, Reshaped)
    }
    assert found == {
        "ImportResult": ("sources", "layouts", "descriptors", "notes", "reshaped"),
        "Reshaped": ("column", "absent", "dropped", "digest", "empty"),
        "ImportNote": ("kind", "subject", "message", "count", "rows"),
        "Layout": ("source", "columns"),
        "TypedSource": ("columns", "rows"),
        "TextSource": ("data",),
        "ErrorCell": ("text",),
    }
    assert tuple(TextSegment.model_fields) == ("text",)
    assert tuple(DataSegment.model_fields) == ("data", "truncated")


# --- Round 2's surviving mutants: exact types, bounds and counts --------------------------------


def _sub(base: type) -> type:
    return type("Sub" + base.__name__, (base,), {})


def _counts_value(value: Any) -> Change:
    return lambda result: _with_counts(result, [("c1", "wren", value)])


def _species_value(value: Any) -> Change:
    return lambda result: _with_counts(result, [("c1", value, 3)])


@pytest.mark.parametrize(
    "change",
    [
        lambda result: _sub(ImportResult)(
            **{f: getattr(result, f) for f in ("sources", "layouts", "descriptors", "notes")}
        ),
        lambda result: replace(result, descriptors=_sub(list)(result.descriptors)),
        lambda result: replace(result, sources=_sub(dict)(result.sources)),
        lambda result: replace(
            result,
            descriptors=[
                _sub(type(d)).model_validate(d.model_dump()) if d.id == "sites" else d
                for d in result.descriptors
            ],
        ),
        lambda result: _with_source(result, "sites", TextSource(_sub(bytes)(b"site_id\n"))),
        lambda result: _with_source(
            result, "counts", _sub(TypedSource)(("a", "b", "c"), (("c1", "wren", 3),))
        ),
        _counts_value(_sub(float)(3.0)),
        _counts_value(_sub(int)(3)),
        _counts_value(_sub(date)(2026, 1, 1)),
        _counts_value(_sub(datetime)(2026, 1, 1)),
        _counts_value(datetime(2026, 1, 1, tzinfo=ZoneInfo("Europe/London"))),
        _counts_value(datetime(2026, 1, 1, tzinfo=timezone(timedelta(seconds=30)))),
        _counts_value(ErrorCell(cast(Any, 3))),
        _counts_value(ErrorCell("#" * 131_073)),
        lambda result: _with_source(
            result,
            "counts",
            TypedSource(cast(Any, ("checklist_id", "species", _Lying("count"))), ()),
        ),
        lambda result: replace(
            result,
            layouts={
                **result.layouts,
                "sites": _sub(Layout)("sites", result.layouts["sites"].columns),
            },
        ),
        lambda result: replace(
            result,
            layouts={
                **result.layouts,
                "sites": Layout("sites", cast(Any, (("site_id", "site_id", "x"),))),
            },
        ),
        _with_notes(_sub(ImportNote)("renamed", "counts", [text("x")])),
    ],
    ids=[
        "a result subclass",
        "a list subclass",
        "a dict subclass",
        "a descriptor subclass",
        "a bytes subclass",
        "a typed source subclass",
        "a float subclass",
        "an int subclass",
        "a date subclass",
        "a datetime subclass",
        "a zone of a place",
        "an offset of seconds",
        "an error cell of no text",
        "a long error cell",
        "a column name that lies",
        "a layout subclass",
        "a column of three parts",
        "a note subclass",
    ],
)
def test_values_of_another_type_are_pack_failed(
    roots: Roots, importing: Importing, birds: Birds, change: Change
) -> None:
    assert _refused(roots, importing, birds, change).code == "PACK_FAILED"


def test_a_subject_s_bytes_count_toward_the_queue(
    roots: Roots, importing: Importing, birds: Birds
) -> None:
    """2,100 subjects of 4,096 characters are 8,601,600 bytes, over the queue's 8,388,608."""
    notes = [_note(subject="s" * 4096, message=[text("x")])] * 2100
    assert _refused(roots, importing, birds, _with_notes(*notes)).code == "PACK_FAILED"


def test_notes_count_utf_8_bytes_not_characters(
    roots: Roots, importing: Importing, birds: Birds
) -> None:
    """900 messages of 5,000 ``é`` are 4.5M characters but 9M bytes, over the queue's bytes."""
    notes = [_note(message=[text("é" * 5000)])] * 900
    assert _refused(roots, importing, birds, _with_notes(*notes)).code == "PACK_FAILED"


def test_import_bytes_is_inclusive(roots: Roots, importing: Importing, birds: Birds) -> None:
    content = b"site_id,habitat\ns1,wood\ns2,marsh\n" + b"".join(
        f"s{i},wood\n".encode() for i in range(10, 160)
    )
    change = _sites_as_text(content)
    _import(roots, importing, birds, change, limits=ImportLimits(import_bytes=len(content)))
    limits = ImportLimits(import_bytes=len(content) - 1)
    assert _limit(_refused(roots, importing, birds, change, limits=limits)) == "import_bytes"


def test_a_text_table_with_no_rows_counts_its_header(
    roots: Roots, importing: Importing, birds: Birds
) -> None:
    """``sites`` as a header alone holds 2 cells: 34 − 4 + 2 = 32 in all. Without its rows the
    checklists' sites dangle, so the import is refused after the checks, not by them."""
    change = _sites_as_text(b"site_id,habitat\n")
    assert (
        _limit(_refused(roots, importing, birds, change, limits=ImportLimits(import_cells=31)))
        == "import_cells"
    )
    with pytest.raises(ImportRefused) as refused:
        _import(roots, importing, birds, change, limits=ImportLimits(import_cells=32))
    assert "LIMIT_EXCEEDED" not in [r.code for r in refused.value.refusals]


def test_a_text_file_its_settings_cannot_read_is_refused_by_the_build(
    roots: Roots, importing: Importing, birds: Birds
) -> None:
    change = _sites_as_text(b'site_id,habitat\n"s1,wood\n')
    assert _refused(roots, importing, birds, change).code == "UNPARSEABLE_SOURCE"


# --- Round 3: exceptions whose type runs code, the checks' own exceptions, bounds --------------


class _Unhashable(type):
    """A metaclass with ``__eq__`` and no ``__hash__``: its classes cannot be hashed."""

    def __eq__(cls, other: object) -> bool:
        return cls is other


class _HashRaises(type):
    def __hash__(cls) -> int:
        raise RuntimeError(SECRET)

    def __eq__(cls, other: object) -> bool:
        raise RuntimeError(SECRET)


class _HashInterrupts(type):
    def __hash__(cls) -> int:
        raise KeyboardInterrupt(SECRET)


class _SaysValueError(type):
    """Hashed and compared as ``ValueError``."""

    def __hash__(cls) -> int:
        return hash(ValueError)

    def __eq__(cls, other: object) -> bool:
        return True


def _of(meta: type) -> BaseException:
    made = meta("SurveyError", (Exception,), {})
    return made(SECRET)


_METAS = [_Unhashable, _HashRaises, _HashInterrupts, _SaysValueError]
_META_IDS = ["unhashable", "hash raises", "hash interrupts", "says ValueError"]


@pytest.mark.parametrize("meta", _METAS, ids=_META_IDS)
def test_an_exception_whose_type_runs_code_is_pack_failed_and_quotes_nothing(
    roots: Roots, importing: Importing, birds: Birds, caplog: pytest.LogCaptureFixture, meta: type
) -> None:
    with caplog.at_level(logging.WARNING):
        refusal = _refused(roots, importing, birds, raises=_of(meta))
    assert refusal.code == "PACK_FAILED"
    assert SECRET not in _said(refusal)
    assert SECRET not in caplog.text
    assert "an exception of its own" in caplog.text


@pytest.mark.parametrize("meta", _METAS, ids=_META_IDS)
def test_pack_failed_runs_no_code_of_the_exception_s_type(
    caplog: pytest.LogCaptureFixture, meta: type
) -> None:
    from aibi.core.engine.resolve import pack_failed

    with caplog.at_level(logging.WARNING):
        pack_failed("birds", "importer", _of(meta))
        pack_failed("birds", "importer", ValueError(SECRET))
    assert [r.getMessage() for r in caplog.records] == [
        "pack birds: its importer raised an exception of its own",
        "pack birds: its importer raised ValueError",
    ]


def test_an_exception_equal_to_every_other_is_not_the_reader_s(
    roots: Roots, importing: Importing, birds: Birds
) -> None:
    class Equal(ImportRefused):
        def __eq__(self, other: object) -> bool:
            return True

        __hash__ = ImportRefused.__hash__

    def then(error: ImportRefused, options: ImportOptions) -> BaseException:
        return Equal([Refusal(code="LIMIT_EXCEEDED", path=None, message=[text(SECRET)])])

    path = roots.write("survey.json", json.dumps(birds.survey()).encode())
    pack = replace(birds.pack, importer=_Catching(then))
    registry = PackRegistry([pack], core_version=aibi.__version__)
    with pytest.raises(ImportRefused) as refused:
        importing(path, registry=registry, pack="birds", limits=ImportLimits(import_bytes=10))
    assert [r.code for r in refused.value.refusals] == ["PACK_FAILED"]


def _subclassed(base: type[BaseException]) -> BaseException:
    return type("Sub" + base.__name__, (base,), {})(SECRET)


def _plain_refused() -> BaseException:
    class Plain(Refused):
        pass

    made = Plain.__new__(Plain)
    made.__dict__["refusals"] = (_pack_refusal("birds.NO_SURVEY"),)
    return made


@pytest.mark.parametrize(
    "raised",
    [
        lambda: _plain_refused(),
        lambda: _subclassed(MemoryError),
        lambda: _subclassed(KeyboardInterrupt),
        lambda: _subclassed(SystemExit),
    ],
    ids=["a Refused subclass", "a MemoryError subclass", "an interrupt subclass", "an exit's"],
)
def test_a_subclass_of_what_the_guard_names_is_pack_failed(
    roots: Roots, importing: Importing, birds: Birds, raised: Callable[[], BaseException]
) -> None:
    assert _refused(roots, importing, birds, raises=raised()).code == "PACK_FAILED"


def _checks_own() -> list[Callable[[], BaseException]]:
    from aibi.core.importers import checks

    failed: Any = checks._Failed  # pyright: ignore[reportPrivateUsage]
    made: Any = checks._Checked  # pyright: ignore[reportPrivateUsage]
    sub = type("SubFailed", (failed,), {})
    limit = Refusal(code="LIMIT_EXCEEDED", path=None, message=[text(SECRET)])
    return [
        lambda: failed(" quoted " + SECRET),
        lambda: failed("\ud800" + SECRET),
        lambda: sub("x"),
        lambda: made([]),
        lambda: made([limit]),
    ]


@pytest.mark.parametrize(
    "at", range(5), ids=["a _Failed", "a _Failed of no text", "a subclass", "empty", "forged"]
)
def test_the_checks_own_exceptions_raised_by_a_pack_are_pack_failed(
    roots: Roots, importing: Importing, birds: Birds, at: int
) -> None:
    refusal = _refused(roots, importing, birds, raises=_checks_own()[at]())
    assert refusal.code == "PACK_FAILED"
    assert SECRET not in _said(refusal)


def test_a_pack_that_runs_the_checks_itself_is_pack_failed(
    roots: Roots, importing: Importing, birds: Birds
) -> None:
    from aibi.core.importers.checks import checked

    def change(result: ImportResult) -> ImportResult:
        return checked(result, ImportLimits(import_cells=1))

    refusal = _refused(roots, importing, birds, change)
    assert refusal.code == "PACK_FAILED"
    assert refusal.limit is None


def _too_many(field: str, many: int, each: str) -> Refused:
    segments: list[Segment] = [text(each)] * many
    given: dict[str, Any] = {"message": [text("x")], field: segments}
    return Refused([Refusal(code="birds.NO_SURVEY", path=None, **given)])


@pytest.mark.parametrize(
    "raised",
    [
        lambda: Refused([_pack_refusal("birds.NO_SURVEY")] * 1001),
        lambda: _too_many("message", 65, "x"),
        lambda: _too_many("message", 1, "x" * 10_001),
        lambda: _too_many("message", 2, "x" * 5001),
        lambda: _too_many("alternatives", 65, "x"),
        lambda: _too_many("alternatives", 1, "x" * 10_001),
    ],
    ids=[
        "too many refusals",
        "too many segments",
        "a long segment",
        "long segments",
        "too many alternatives",
        "a long alternative",
    ],
)
def test_a_pack_s_refusals_past_their_bounds_are_pack_failed(
    roots: Roots, importing: Importing, birds: Birds, raised: Callable[[], Refused]
) -> None:
    assert _refused(roots, importing, birds, raises=raised()).code == "PACK_FAILED"


@pytest.mark.parametrize(
    "raised",
    [
        lambda: Refused([_pack_refusal("birds.NO_SURVEY")] * 1000),
        lambda: _too_many("message", 64, "x"),
        lambda: _too_many("message", 1, "x" * 10_000),
        lambda: _too_many("alternatives", 2, "x" * 5000),
    ],
    ids=["refusals", "segments", "a segment", "alternatives"],
)
def test_a_pack_s_refusals_at_their_bounds_stand(
    roots: Roots, importing: Importing, birds: Birds, raised: Callable[[], Refused]
) -> None:
    with pytest.raises(ImportRefused) as refused:
        _import(roots, importing, birds, raises=raised())
    assert {r.code for r in refused.value.refusals} == {"birds.NO_SURVEY"}


class _Probing:
    """An importer that asks the reader for ``how`` and lets out what it raises."""

    def __init__(self, how: Callable[[ConfinedPath, ImportOptions], object]) -> None:
        self.how = how

    def import_source(self, source: ConfinedPath, options: ImportOptions) -> Any:
        self.how(source, options)
        raise AssertionError("the reader did not refuse")


@pytest.mark.parametrize(
    "how",
    [
        lambda source, options: options.reader.files(source),
        lambda source, options: options.reader.location(ConfinedPath(Path("/nowhere/survey.json"))),
    ],
    ids=["files of a file", "the location of a path in no root"],
)
def test_a_refusal_of_the_reader_s_listing_or_location_stands_as_the_reader_s(
    roots: Roots,
    importing: Importing,
    birds: Birds,
    how: Callable[[ConfinedPath, ImportOptions], object],
) -> None:
    path = roots.write("survey.json", json.dumps(birds.survey()).encode())
    pack = replace(birds.pack, importer=_Probing(how))
    registry = PackRegistry([pack], core_version=aibi.__version__)
    with pytest.raises(ImportRefused) as refused:
        importing(path, registry=registry, pack="birds")
    assert [r.code for r in refused.value.refusals] == ["PATH_NOT_CONFINED"]


def test_an_error_cell_of_a_field_s_characters_is_kept(
    roots: Roots, importing: Importing, birds: Birds
) -> None:
    _import(roots, importing, birds, _counts_value(ErrorCell("#" * 131_072)))


class _Str(str):
    __slots__ = ()


def test_a_layout_s_source_and_column_sources_are_exact_strings(
    roots: Roots, importing: Importing, birds: Birds
) -> None:
    def source(result: ImportResult) -> ImportResult:
        sites = result.layouts["sites"]
        return replace(
            result, layouts={**result.layouts, "sites": Layout(_Str(sites.source), sites.columns)}
        )

    def column(result: ImportResult) -> ImportResult:
        sites = result.layouts["sites"]
        columns = ((sites.columns[0][0], _Str(sites.columns[0][1])), *sites.columns[1:])
        return replace(result, layouts={**result.layouts, "sites": Layout("sites", columns)})

    for change in (source, column):
        assert _refused(roots, importing, birds, change).code == "PACK_FAILED"


def test_notes_of_exactly_the_queue_s_bytes_are_kept(
    roots: Roots, importing: Importing, birds: Birds
) -> None:
    """1,024 messages of 8,192 bytes are the queue's 8,388,608 bytes; one more byte is not."""
    notes = [_note(subject=None, message=[text("x" * 8192)])] * 1024
    _import(roots, importing, birds, _with_notes(*notes))
    over = [*notes[:-1], _note(subject=None, message=[text("x" * 8193)])]
    assert _refused(roots, importing, birds, _with_notes(*over)).code == "PACK_FAILED"


def test_a_typed_source_its_own_checks_refuse_is_the_checks_failure(
    roots: Roots, importing: Importing, birds: Birds, caplog: pytest.LogCaptureFixture
) -> None:
    change = _counts_value(datetime(2026, 1, 1, tzinfo=timezone(timedelta(seconds=30))))
    with caplog.at_level(logging.WARNING):
        refusal = _refused(roots, importing, birds, change)
    assert refusal.code == "PACK_FAILED"
    assert "not source values" in _said(refusal)
    assert "raised" not in caplog.text


# --- Round 1 after the redesign: no pack code in an attributed region, one snapshot ------------


def _raising_meta(raised: Callable[[], BaseException]) -> type:
    """A class whose metaclass raises ``raised()`` when the class is compared with ``==``."""

    class Meta(type):
        def __eq__(cls, other: object) -> bool:
            raise raised()

        __hash__ = type.__hash__

    return Meta("Lying", (), {})


def _checks_exceptions() -> list[Callable[[], BaseException]]:
    from aibi.core.importers import checks

    failed: Any = checks._Failed  # pyright: ignore[reportPrivateUsage]
    made: Any = checks._Checked  # pyright: ignore[reportPrivateUsage]
    forged = Refusal(code="LIMIT_EXCEEDED", path=None, message=[text(SECRET)])
    return [
        lambda: made([forged]),
        lambda: failed(" " + SECRET),
        lambda: failed("\ud800" + SECRET),
        lambda: failed(SECRET * 2000),
    ]


@pytest.mark.parametrize("at", range(4), ids=["_Checked", "_Failed", "a surrogate", "long"])
def test_pack_code_never_runs_inside_the_checks(
    roots: Roots, importing: Importing, birds: Birds, caplog: pytest.LogCaptureFixture, at: int
) -> None:
    """A descriptor whose type's metaclass raises the checks' own exceptions from ``__eq__``:
    the checks compare types by identity, so it never runs, and the result is no descriptor."""
    lying = _raising_meta(_checks_exceptions()[at])

    def change(result: ImportResult) -> ImportResult:
        return replace(result, descriptors=[*result.descriptors, lying()])

    with caplog.at_level(logging.DEBUG):
        refusal = _refused(roots, importing, birds, change)
    assert refusal.code == "PACK_FAILED"
    assert "not a list of descriptors" in _said(refusal)
    assert SECRET not in _said(refusal)
    assert SECRET not in caplog.text


class _Fspath:
    """A path-like whose ``__fspath__`` raises a forged refusal of the reader's."""

    def __fspath__(self) -> str:
        raise ImportRefused(
            [Refusal(code="LIMIT_EXCEEDED", path=None, message=[text(SECRET)] * 1000)]
        )


class _SubPath(type(Path())):  # type: ignore[misc]
    def __fspath__(self) -> str:
        return _Fspath().__fspath__()


@pytest.mark.parametrize(
    "how",
    [
        lambda source, options: options.reader.read(_Fspath(), options.limits.import_bytes),
        lambda source, options: options.reader.read(_SubPath(source), 10**9),
        lambda source, options: options.reader.files(_Fspath()),
        lambda source, options: options.reader.location(_SubPath(source)),
    ],
    ids=["read", "read a subclass", "files", "location"],
)
def test_the_reader_runs_no_code_of_the_pack_s(
    roots: Roots,
    importing: Importing,
    birds: Birds,
    how: Callable[[ConfinedPath, ImportOptions], object],
) -> None:
    path = roots.write("survey.json", json.dumps(birds.survey()).encode())
    pack = replace(birds.pack, importer=_Probing(how))
    registry = PackRegistry([pack], core_version=aibi.__version__)
    with pytest.raises(ImportRefused) as refused:
        importing(path, registry=registry, pack="birds")
    assert [r.code for r in refused.value.refusals] == ["PACK_FAILED"]
    assert all(SECRET not in _said(r) for r in refused.value.refusals)


def _meddled(error: ImportRefused, options: ImportOptions) -> BaseException:
    first = error.refusals[0]
    first.message.append(text(SECRET))
    first.alternatives.extend([text("x" * 9000)] * 500)
    return error


def test_a_refusal_of_the_reader_s_changed_in_place_stands_as_it_was_made(
    roots: Roots, importing: Importing, birds: Birds
) -> None:
    path = roots.write("survey.json", json.dumps(birds.survey()).encode())
    pack = replace(birds.pack, importer=_Catching(_meddled))
    registry = PackRegistry([pack], core_version=aibi.__version__)
    with pytest.raises(ImportRefused) as refused:
        importing(path, registry=registry, pack="birds", limits=ImportLimits(import_bytes=10))
    [refusal] = refused.value.refusals
    assert (refusal.code, _limit(refusal)) == ("LIMIT_EXCEEDED", "import_bytes")
    assert SECRET not in _said(refusal)
    assert refusal.alternatives == []


@pytest.mark.parametrize("limit", [1.5, "10", None], ids=["a float", "a string", "none"])
def test_a_limit_that_is_no_int_is_the_operator_s(
    roots: Roots, importing: Importing, birds: Birds, limit: Any
) -> None:
    def how(source: ConfinedPath, options: ImportOptions) -> object:
        return options.reader.read(source, limit)

    path = roots.write("survey.json", json.dumps(birds.survey()).encode())
    pack = replace(birds.pack, importer=_Probing(how))
    registry = PackRegistry([pack], core_version=aibi.__version__)
    with pytest.raises(ImportRefused) as refused:
        importing(path, registry=registry, pack="birds", limits=ImportLimits(import_bytes=10))
    [refusal] = refused.value.refusals
    assert (refusal.code, _limit(refusal)) == ("LIMIT_EXCEEDED", "import_bytes")


def test_a_refusal_of_confinement_under_a_smaller_limit_is_the_reader_s(
    roots: Roots, importing: Importing, birds: Birds
) -> None:
    def how(source: ConfinedPath, options: ImportOptions) -> object:
        return options.reader.read(ConfinedPath(Path("/nowhere/survey.json")), 1)

    path = roots.write("survey.json", json.dumps(birds.survey()).encode())
    pack = replace(birds.pack, importer=_Probing(how))
    registry = PackRegistry([pack], core_version=aibi.__version__)
    with pytest.raises(ImportRefused) as refused:
        importing(path, registry=registry, pack="birds")
    assert [r.code for r in refused.value.refusals] == ["PATH_NOT_CONFINED"]


class _Lifting:
    """The birds importer, which lifts the import's limits on the object it was given."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner

    def import_source(self, source: ConfinedPath, options: ImportOptions) -> Any:
        object.__setattr__(options.limits, "import_cells", 10**12)
        return self.inner.import_source(source, options)


def test_the_limits_are_the_operator_s_whatever_the_pack_does_to_its_copy(
    roots: Roots, importing: Importing, birds: Birds
) -> None:
    path = roots.write("survey.json", json.dumps(birds.survey()).encode())
    pack = replace(birds.pack, importer=_Lifting(birds.importer))
    registry = PackRegistry([pack], core_version=aibi.__version__)
    limits = ImportLimits(import_cells=33)
    with pytest.raises(ImportRefused) as refused:
        importing(path, registry=registry, pack="birds", limits=limits)
    [refusal] = refused.value.refusals
    assert (refusal.code, _limit(refusal)) == ("LIMIT_EXCEEDED", "import_cells")
    assert limits.import_cells == 33


def _refused_with(**given: Any) -> Refused:
    return Refused([Refusal(code="birds.NO_SURVEY", path=None, message=[text("x")], **given)])


@pytest.mark.parametrize(
    "raised",
    [
        lambda: _refused_with(limit={"name": "import_bytes", "max": 10}),
        lambda: _refused_with(counts=[]),
    ],
    ids=["a core limit", "counts"],
)
def test_a_pack_s_refusal_names_no_limit_and_holds_no_counts(
    roots: Roots, importing: Importing, birds: Birds, raised: Callable[[], Refused]
) -> None:
    assert _refused(roots, importing, birds, raises=raised()).code == "PACK_FAILED"


def test_a_typed_source_of_no_columns_is_pack_failed(
    roots: Roots, importing: Importing, birds: Birds
) -> None:
    """Its rows count no cells, so it would hold any number of them past ``import_cells``."""

    def change(result: ImportResult) -> ImportResult:
        return _with_source(result, "counts", _bare_typed((), ((),) * 1000))

    refusal = _refused(roots, importing, birds, change, limits=ImportLimits(import_cells=40))
    assert refusal.code == "PACK_FAILED"


class _Meddling:
    """The birds validator, which first changes what it is given in place."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.given: list[ImportResult] = []

    def validate_source(self, source: Any, result: ImportResult) -> Any:
        self.given.append(result)
        for descriptor in result.descriptors:
            if descriptor.id == "dataset":
                descriptor.extensions["birds"] = {"protocol": SECRET}
            if descriptor.id == "sites":
                object.__setattr__(descriptor, "label", SECRET)
        return self.inner.validate_source(source, result)

    def validate_descriptors(self, release: Any) -> Any:
        return self.inner.validate_descriptors(release)


def test_a_validator_changes_nothing_that_is_built(
    roots: Roots, importing: Importing, birds: Birds, store: Any
) -> None:
    meddling = _Meddling(birds.pack.validator)
    path = roots.write("survey.json", json.dumps(birds.survey()).encode())
    pack = replace(birds.pack, validator=meddling)
    registry = PackRegistry([pack], core_version=aibi.__version__)
    imported = importing(path, registry=registry, pack="birds")
    built = json.dumps([d.model_dump(mode="json") for d in imported.built.descriptors])
    assert SECRET not in built
    [given] = meddling.given
    assert type(given.descriptors) is tuple
    assert type(given.sources) is MappingProxyType
    assert type(given.layouts) is MappingProxyType


def test_the_copy_holds_none_of_the_pack_s_objects(birds: Birds, roots: Roots) -> None:
    from aibi.core.importers.checks import checked

    path = roots.write("survey.json", json.dumps(birds.survey()).encode())
    result = birds.importer.import_source(roots.confinement.confine(path), roots.options())
    cell = ErrorCell("#N/A")
    counts = result.sources["counts"]
    assert isinstance(counts, TypedSource)
    note = _note(message=[text("a"), data("b")])
    given = replace(
        _with_source(
            _with_source(result, "counts", TypedSource(counts.columns, (("c1", "wren", cell),))),
            "notes_text",
            TextSource(b"x\n1\n"),
        ),
        layouts={**result.layouts, "notes_text": Layout("notes_text", (("x", "x"),))},
        notes=[note],
    )
    copy = checked(given, ImportLimits())
    copied = copy.sources["counts"]
    assert isinstance(copied, TypedSource)
    assert copied.rows[0][2] == cell
    assert copied.rows[0][2] is not cell
    assert copy.sources["notes_text"] is not given.sources["notes_text"]
    assert copy.notes[0].message[0] is not note.message[0]


def test_a_message_of_more_characters_than_a_text_holds_is_pack_failed(
    roots: Roots, importing: Importing, birds: Birds
) -> None:
    notes = [_note(message=[text("x" * 5000), text("y" * 5001)])]
    assert _refused(roots, importing, birds, _with_notes(*notes)).code == "PACK_FAILED"


def test_a_built_in_base_exception_is_logged_by_its_name(
    roots: Roots, importing: Importing, birds: Birds, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING):
        _refused(roots, importing, birds, raises=GeneratorExit(SECRET))
    assert "GeneratorExit" in caplog.text
    assert SECRET not in caplog.text


def test_an_error_cell_whose_text_is_no_str_is_the_checks_failure(
    roots: Roots, importing: Importing, birds: Birds, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING):
        refusal = _refused(roots, importing, birds, _counts_value(ErrorCell(cast(Any, 3))))
    assert "error cell whose text is not a cell's" in _said(refusal)
    assert "raised" not in caplog.text


def test_a_descriptor_that_warns_is_pack_failed_whatever_the_warning_filter(
    roots: Roots, importing: Importing, birds: Birds
) -> None:
    import warnings

    def change(result: ImportResult) -> ImportResult:
        descriptors = [
            d.model_copy(update={"fields": {"role": "entity", "x": SECRET}})
            if d.id == "sites"
            else d
            for d in result.descriptors
        ]
        return replace(result, descriptors=descriptors)

    with warnings.catch_warnings():
        warnings.simplefilter("always")
        refusal = _refused(roots, importing, birds, change)
    assert refusal.code == "PACK_FAILED"


@pytest.mark.parametrize(
    "raising",
    [
        lambda: RuntimeError(SECRET),
        lambda: _subclassed(KeyboardInterrupt),
        lambda: GeneratorExit(SECRET),
    ],
    ids=["an exception", "an interrupt's subclass", "a base exception"],
)
def test_a_refusal_that_cannot_be_built_is_the_constant_pack_failed(
    roots: Roots,
    importing: Importing,
    birds: Birds,
    monkeypatch: pytest.MonkeyPatch,
    raising: Callable[[], BaseException],
) -> None:
    """Whatever building the refusal raises, the import is refused ``PACK_FAILED`` in the core's
    fixed words, with no exception as its context."""
    from aibi.core.importers import checks

    def broken(*given: Any) -> ImportRefused:
        raise raising()

    monkeypatch.setattr(checks, "_pack_refused", broken)
    raised = Refused([_pack_refusal("birds.NO_SURVEY")])
    with pytest.raises(ImportRefused) as refused:
        _import(roots, importing, birds, raises=raised)
    [refusal] = refused.value.refusals
    assert refusal.code == "PACK_FAILED"
    assert SECRET not in _said(refusal)
    assert refused.value.__context__ is None


# --- Round 2: no pack code inside an attributed region, and copies that share nothing ----------


class _Evil(str):
    """A ``str`` subclass whose length is a forged refusal of the reader's: a path keeps the text
    it was made from and parses it when first read."""

    def __len__(self) -> int:
        raise ImportRefused(
            [Refusal(code="LIMIT_EXCEEDED", path=None, message=[text(SECRET)] * 1000)]
        )


def _evil(source: ConfinedPath) -> ConfinedPath:
    return ConfinedPath(type(Path())(_Evil(str(source))))


_READS: list[Callable[[ConfinedPath, ImportOptions], object]] = [
    lambda source, options: options.reader.read(_evil(source), options.limits.import_bytes),
    lambda source, options: options.reader.read(_evil(source), 10**12),
    lambda source, options: options.reader.files(_evil(source)),
    lambda source, options: options.reader.location(_evil(source)),
    lambda source, options: options.reader.read(
        cast(ConfinedPath, _SubPath(source)), options.limits.import_bytes
    ),
    lambda source, options: options.reader.location(cast(ConfinedPath, _SubPath(source))),
]
_READ_IDS = [
    "read at the operator's limit",
    "read above it",
    "files",
    "location",
    "a subclass read at the operator's limit",
    "a subclass's location",
]


@pytest.mark.parametrize("how", _READS, ids=_READ_IDS)
def test_a_path_whose_text_runs_code_is_the_pack_s_failure(
    roots: Roots,
    importing: Importing,
    birds: Birds,
    how: Callable[[ConfinedPath, ImportOptions], object],
) -> None:
    path = roots.write("survey.json", json.dumps(birds.survey()).encode())
    pack = replace(birds.pack, importer=_Probing(how))
    registry = PackRegistry([pack], core_version=aibi.__version__)
    with pytest.raises(ImportRefused) as refused:
        importing(path, registry=registry, pack="birds")
    assert [r.code for r in refused.value.refusals] == ["PACK_FAILED"]
    assert all(SECRET not in _said(r) for r in refused.value.refusals)


def _ran_inside(regions: set[str], run: Callable[[], object]) -> list[str]:
    """The functions of this module that ran while one of ``checks``' functions named in
    ``regions`` was on the stack: the pack's code, here, run inside an attributed region."""
    import sys

    from aibi.core.importers import checks

    ran: list[str] = []

    def profile(frame: Any, event: str, arg: object) -> None:
        if event != "call" or frame.f_code.co_filename != __file__:
            return
        up = frame.f_back
        while up is not None:
            code = up.f_code
            if code.co_filename == checks.__file__ and code.co_name in regions:
                ran.append(frame.f_code.co_name)
                return
            up = up.f_back

    sys.setprofile(profile)
    try:
        run()
    except BaseException:
        pass
    finally:
        sys.setprofile(None)
    return ran


@pytest.mark.parametrize("how", _READS, ids=_READ_IDS)
def test_no_code_of_the_pack_s_runs_while_the_reader_records(
    roots: Roots,
    importing: Importing,
    birds: Birds,
    how: Callable[[ConfinedPath, ImportOptions], object],
) -> None:
    """The structure the reader's attribution rests on: no function of the pack's (this module's)
    runs while ``call`` runs the inner reader or while a refusal is recorded."""
    path = roots.write("survey.json", json.dumps(birds.survey()).encode())
    pack = replace(birds.pack, importer=_Probing(how))
    registry = PackRegistry([pack], core_version=aibi.__version__)
    ran = _ran_inside(
        {"call", "record", "own", "always"},
        lambda: importing(path, registry=registry, pack="birds"),
    )
    assert ran == []


class _Swapping:
    """The birds validator, which first changes what it is given through the objects' own
    ``__dict__`` and ``object.__setattr__``, as frozen dataclasses and models allow."""

    def __init__(self, inner: Any, how: Callable[[ImportResult], None]) -> None:
        self.inner, self.how = inner, how

    def validate_source(self, source: Any, result: ImportResult) -> Any:
        self.how(result)
        return self.inner.validate_source(source, result)

    def validate_descriptors(self, release: Any) -> Any:
        return self.inner.validate_descriptors(release)


class _Raises(str):
    def __len__(self) -> int:
        raise RuntimeError(SECRET)


def _counts_rows(result: ImportResult) -> TypedSource:
    counts = result.sources["counts"]
    assert isinstance(counts, TypedSource)
    return counts


def _more_rows(result: ImportResult) -> None:
    counts = _counts_rows(result)
    object.__setattr__(counts, "rows", counts.rows * 5000)


def _long_cell(result: ImportResult) -> None:
    counts = _counts_rows(result)
    first = (counts.rows[0][0], "x" * 500_000, counts.rows[0][2])
    vars(counts)["rows"] = (first, *counts.rows[1:])


def _raising_cell(result: ImportResult) -> None:
    counts = _counts_rows(result)
    first = (counts.rows[0][0], _Raises("wren"), counts.rows[0][2])
    object.__setattr__(counts, "rows", (first, *counts.rows[1:]))


def _error_cell(result: ImportResult) -> None:
    for row in _counts_rows(result).rows:
        for value in row:
            if isinstance(value, ErrorCell):
                object.__setattr__(value, "text", "x" * 500_000)


def _long_segment(result: ImportResult) -> None:
    for note in result.notes:
        vars(note.message[0])["text"] = SECRET * 400_000
        object.__setattr__(note, "subject", SECRET * 400_000)


def _layout(result: ImportResult) -> None:
    for layout in result.layouts.values():
        object.__setattr__(layout, "columns", ())


def _text_source(result: ImportResult) -> None:
    for source in result.sources.values():
        if isinstance(source, TextSource):
            object.__setattr__(source, "data", b"x" * 500_000)


def _with_error_cell_and_note(result: ImportResult) -> ImportResult:
    """The birds result with an error cell, a note and ``sites`` as a text file."""
    counts = _counts_rows(result)
    first = (counts.rows[0][0], counts.rows[0][1], ErrorCell("#N/A"))
    note = _note(message=[text("a"), data("b")])
    texts = _sites_as_text(b"site_id,habitat\ns1,wood\ns2,marsh\n")
    return replace(
        _with_source(
            cast(ImportResult, texts(result)),
            "counts",
            TypedSource(counts.columns, (first, *counts.rows[1:])),
        ),
        notes=[note],
    )


@pytest.mark.parametrize(
    "how",
    [_more_rows, _long_cell, _raising_cell, _error_cell, _long_segment, _layout, _text_source],
    ids=[
        "more rows",
        "a long cell",
        "a raising cell",
        "an error cell",
        "a segment",
        "a layout",
        "a text source",
    ],
)
def test_a_validator_changes_nothing_that_is_built_through_the_objects_it_is_given(
    roots: Roots, importing: Importing, birds: Birds, how: Callable[[ImportResult], None]
) -> None:
    path = roots.write("survey.json", json.dumps(birds.survey()).encode())
    limits = ImportLimits(import_cells=200)
    plain = PackRegistry(
        [replace(birds.pack, importer=Wrapped(birds.importer, _with_error_cell_and_note, None))],
        core_version=aibi.__version__,
    )
    expected = importing(path, registry=plain, pack="birds", dataset="d", limits=limits)
    swapping = replace(
        birds.pack,
        importer=Wrapped(birds.importer, _with_error_cell_and_note, None),
        validator=_Swapping(birds.pack.validator, how),
    )
    registry = PackRegistry([swapping], core_version=aibi.__version__)
    imported = importing(path, registry=registry, pack="birds", dataset="e", limits=limits)

    def built(found: Any) -> Any:
        return found.built.manifest.model_dump(mode="json", exclude={"dataset"})

    assert built(imported) == built(expected)
    assert imported.notes == expected.notes


def _reachable(root: object) -> dict[int, object]:
    """Every object reachable from ``root``, by id: through containers, ``__dict__`` and slots."""
    found: dict[int, object] = {}
    stack = [root]
    while stack:
        item = stack.pop()
        if id(item) in found:
            continue
        found[id(item)] = item
        if isinstance(item, str | bytes | int | float | type(None) | date | timezone):
            continue
        if isinstance(item, tuple | list | set | frozenset):
            stack.extend(cast(Any, item))
        elif isinstance(item, dict | MappingProxyType):
            stack.extend(cast(Any, item).keys())
            stack.extend(cast(Any, item).values())
        else:
            stack.extend(vars(item).values() if hasattr(item, "__dict__") else ())
            for kind in type(item).__mro__:
                for slot in getattr(kind, "__slots__", ()):
                    if slot != "__dict__" and hasattr(item, slot):
                        stack.append(getattr(item, slot))
    return found


def _immutable(item: object) -> bool:
    from enum import Enum

    if type(item) in (str, bytes, int, float, bool, type(None), date, datetime, timezone):
        return True
    if isinstance(item, Enum):
        return True
    if type(item) is tuple or type(item) is frozenset:
        return all(_immutable(one) for one in cast(Any, item))
    return False


def test_a_validator_s_copy_shares_nothing_but_immutable_built_ins(
    birds: Birds, roots: Roots
) -> None:
    from aibi.core.importers.checks import checked, for_validator

    path = roots.write("survey.json", json.dumps(birds.survey()).encode())
    result = birds.importer.import_source(roots.confinement.confine(path), roots.options())
    given = _with_source(_with_error_cell_and_note(result), "notes_text", TextSource(b"x\n1\n"))
    given = replace(
        given, layouts={**given.layouts, "notes_text": Layout("notes_text", (("x", "x"),))}
    )
    built = checked(given, ImportLimits())
    handed = for_validator(built)
    ours, theirs = _reachable(built), _reachable(handed)
    shared = [ours[i] for i in ours.keys() & theirs.keys()]
    assert [item for item in shared if not _immutable(item)] == []
    assert any(isinstance(item, ErrorCell) for item in theirs.values())
    assert handed == built


def test_a_refusal_s_path_is_the_pack_s_failure(
    roots: Roots, importing: Importing, birds: Birds
) -> None:
    """An importer's refusal points into no document, and a path is unbounded."""
    raised = Refused(
        [Refusal(code="birds.NO_SURVEY", path="/" + SECRET * 1000, message=[text("x")])]
    )
    assert _refused(roots, importing, birds, raises=raised).code == "PACK_FAILED"


def test_running_out_of_memory_while_the_descriptors_are_copied_names_import_bytes(
    roots: Roots, importing: Importing, birds: Birds, monkeypatch: pytest.MonkeyPatch
) -> None:
    from aibi.core.importers import checks

    def exhausted(*given: Any, **named: Any) -> Any:
        raise MemoryError

    monkeypatch.setattr(checks, "canonical", exhausted)
    refusal = _refused(roots, importing, birds)
    assert (refusal.code, _limit(refusal)) == ("LIMIT_EXCEEDED", "import_bytes")


def test_running_out_of_memory_while_a_pack_s_refusal_is_read_names_import_bytes(
    roots: Roots, importing: Importing, birds: Birds, monkeypatch: pytest.MonkeyPatch
) -> None:
    def exhausted(cls: type, *given: Any, **named: Any) -> Any:
        raise MemoryError

    monkeypatch.setattr(Refusal, "model_validate", classmethod(exhausted))
    raised = Refused([_pack_refusal("birds.NO_SURVEY")])
    refusal = _refused(roots, importing, birds, raises=raised)
    assert (refusal.code, _limit(refusal)) == ("LIMIT_EXCEEDED", "import_bytes")


def test_a_descriptor_s_own_model_dump_is_never_called(
    roots: Roots, importing: Importing, birds: Birds
) -> None:
    called: list[str] = []

    def change(result: ImportResult) -> ImportResult:
        for descriptor in result.descriptors:
            if descriptor.id == "sites":

                def dump(*given: Any, **named: Any) -> Any:
                    called.append("model_dump")
                    raise RuntimeError(SECRET)

                object.__setattr__(descriptor, "model_dump", dump)
        return result

    _import(roots, importing, birds, change)
    assert called == []


def test_a_refused_whose_dict_is_no_plain_dict_is_pack_failed_and_runs_nothing(
    roots: Roots, importing: Importing, birds: Birds
) -> None:
    called: list[str] = []

    class _Held(dict[str, Any]):
        def get(self, *given: Any) -> Any:
            called.append("get")
            return (_pack_refusal("birds.NO_SURVEY"),)

        def __getitem__(self, key: str) -> Any:
            called.append("getitem")
            return (_pack_refusal("birds.NO_SURVEY"),)

    raised = Refused([_pack_refusal("birds.NO_SURVEY")])
    raised.__dict__ = _Held(refusals=(_pack_refusal("birds.NO_SURVEY"),))
    assert _refused(roots, importing, birds, raises=raised).code == "PACK_FAILED"
    assert called == []


def test_a_pack_s_refusal_that_warns_is_pack_failed_and_warns_nothing(
    roots: Roots, importing: Importing, birds: Birds
) -> None:
    import warnings

    made = Refusal.model_construct(
        code="birds.NO_SURVEY",
        path=None,
        message=[{"text": SECRET}],
        alternatives=[],
        limit=None,
        counts=None,
    )
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        refusal = _refused(roots, importing, birds, raises=Refused([made]))
    assert refusal.code == "PACK_FAILED"
    assert caught == []


def test_a_descriptor_that_warns_but_would_hold_is_pack_failed_and_warns_nothing(
    roots: Roots, importing: Importing, birds: Birds
) -> None:
    import warnings

    def change(result: ImportResult) -> ImportResult:
        descriptors = [
            d.model_copy(update={"label": cast(Any, [SECRET])}) if d.id == "sites" else d
            for d in result.descriptors
        ]
        return replace(result, descriptors=descriptors)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        refusal = _refused(roots, importing, birds, change)
    assert refusal.code == "PACK_FAILED"
    assert caught == []


def test_the_copy_s_descriptors_and_layouts_are_its_own(birds: Birds, roots: Roots) -> None:
    from aibi.core.importers.checks import checked

    path = roots.write("survey.json", json.dumps(birds.survey()).encode())
    result = birds.importer.import_source(roots.confinement.confine(path), roots.options())
    copy = checked(result, ImportLimits())
    assert copy.descriptors == tuple(result.descriptors)
    assert all(c is not g for c, g in zip(copy.descriptors, result.descriptors, strict=True))
    assert all(copy.layouts[t] is not result.layouts[t] for t in result.layouts)
    assert dict(copy.layouts) == dict(result.layouts)


# --- The redesign at round 3: attribution by origin -----------------------------------------------

_ARMED: list[Callable[[str, tuple[Any, ...]], None]] = []
"""What the audit hook does while a test arms it (an audit hook cannot be removed)."""


def _audit(event: str, given: tuple[Any, ...]) -> None:
    for hook in list(_ARMED):
        hook(event, given)


sys.addaudithook(_audit)


def _forged_limit() -> ImportRefused:
    return ImportRefused([Refusal(code="LIMIT_EXCEEDED", path=None, message=[text(SECRET)] * 1000)])


def _reads_its_source(source: ConfinedPath, options: ImportOptions) -> object:
    return options.reader.read(source, options.limits.import_bytes)


def test_a_refusal_an_audit_hook_raises_inside_the_reader_is_the_pack_s(
    roots: Roots, importing: Importing, birds: Birds
) -> None:
    """P1: the pack's code, run by a hook inside the reader's frames, raises a refusal of the
    reader's kind; it was not raised by the core, so it is the pack's failure."""

    def hook(event: str, given: tuple[Any, ...]) -> None:
        if event == "open" and str(given[0]).endswith("survey.json"):
            raise _forged_limit()

    path = roots.write("survey.json", json.dumps(birds.survey()).encode())
    pack = replace(birds.pack, importer=_Probing(_reads_its_source))
    registry = PackRegistry([pack], core_version=aibi.__version__)
    _ARMED.append(hook)
    try:
        with pytest.raises(ImportRefused) as refused:
            importing(path, registry=registry, pack="birds")
    finally:
        _ARMED.remove(hook)
    assert [r.code for r in refused.value.refusals] == ["PACK_FAILED"]
    assert all(SECRET not in _said(r) for r in refused.value.refusals)


def test_a_refusal_a_profile_hook_raises_inside_the_reader_is_the_pack_s(
    roots: Roots, importing: Importing, birds: Birds
) -> None:
    """P2: as P1, by a profile function on the reader's call of ``os.open``."""
    import os

    def profile(frame: Any, event: str, arg: Any) -> None:
        if event == "c_call" and arg is os.open:
            sys.setprofile(None)
            raise _forged_limit()

    def how(source: ConfinedPath, options: ImportOptions) -> object:
        sys.setprofile(profile)
        try:
            return options.reader.read(source, options.limits.import_bytes)
        finally:
            sys.setprofile(None)

    path = roots.write("survey.json", json.dumps(birds.survey()).encode())
    pack = replace(birds.pack, importer=_Probing(how))
    registry = PackRegistry([pack], core_version=aibi.__version__)
    with pytest.raises(ImportRefused) as refused:
        importing(path, registry=registry, pack="birds")
    assert [r.code for r in refused.value.refusals] == ["PACK_FAILED"]
    assert all(SECRET not in _said(r) for r in refused.value.refusals)


class _Arming:
    """The birds importer, which arms a profile function as it returns; the function raises
    ``made()`` at the first call of a built-in inside the checks."""

    def __init__(self, inner: Any, made: Callable[[], BaseException]) -> None:
        self.inner, self.made = inner, made

    def import_source(self, source: ConfinedPath, options: ImportOptions) -> Any:
        result = self.inner.import_source(source, options)
        made = self.made

        def profile(frame: Any, event: str, arg: Any) -> None:
            if event == "c_call":
                sys.setprofile(None)
                raise made()

        sys.setprofile(profile)
        return result


def _checks_forged() -> BaseException:
    from aibi.core.importers import checks

    return checks._Checked(_forged_limit().refusals)  # pyright: ignore[reportPrivateUsage]


def _failed_forged() -> BaseException:
    from aibi.core.importers import checks

    return checks._Failed(SECRET)  # pyright: ignore[reportPrivateUsage]


@pytest.mark.parametrize("made", [_checks_forged, _failed_forged], ids=["_Checked", "_Failed"])
def test_the_checks_own_exceptions_raised_by_a_hook_inside_them_are_the_pack_s(
    roots: Roots, importing: Importing, birds: Birds, made: Callable[[], BaseException]
) -> None:
    """P3, P4: a profile function armed as the importer returns raises the checks' own types
    from inside ``checked``; they were not raised by the checks, so they are the pack's failure."""
    path = roots.write("survey.json", json.dumps(birds.survey()).encode())
    pack = replace(birds.pack, importer=_Arming(birds.importer, made))
    registry = PackRegistry([pack], core_version=aibi.__version__)
    try:
        with pytest.raises(ImportRefused) as refused:
            importing(path, registry=registry, pack="birds")
    finally:
        sys.setprofile(None)
    assert [r.code for r in refused.value.refusals] == ["PACK_FAILED"]
    assert all(SECRET not in _said(r) for r in refused.value.refusals)


def test_an_exception_raised_outside_the_core_is_not_the_core_s() -> None:
    """The core's own refusals standing as its own is held by every test of the reader's and the
    checks' refusals; this is the other side, for an exception raised outside the core."""
    from aibi.core.importers import checks

    def raised_here() -> BaseException:
        try:
            raise ValueError("x")
        except ValueError as error:
            return error

    assert not checks._raised_by_core(raised_here())  # pyright: ignore[reportPrivateUsage]


class _Twice(str):
    """A path's text that behaves when first parsed and forges a refusal of the reader's when
    parsed again, as a path the core passed on unchanged would be, inside the reader's record."""

    parsed: ClassVar[list[int]] = []

    def __len__(self) -> int:
        _Twice.parsed.append(1)
        if len(_Twice.parsed) > 1:
            raise _forged_limit()
        return str.__len__(self)


def _twice(source: ConfinedPath) -> ConfinedPath:
    _Twice.parsed.clear()
    return ConfinedPath(type(Path())(_Twice(str(source))))


@pytest.mark.parametrize(
    "how",
    [
        lambda source, options: options.reader.read(_twice(source), options.limits.import_bytes),
        lambda source, options: options.reader.files(_twice(source)),
        lambda source, options: options.reader.location(_twice(source)),
    ],
    ids=["read", "files", "location"],
)
def test_the_reader_reads_a_path_of_its_own_making(
    roots: Roots,
    importing: Importing,
    birds: Birds,
    how: Callable[[ConfinedPath, ImportOptions], object],
) -> None:
    """m1: the reader passes on the path it made, never the pack's, whose text a second parse
    would read inside the record."""
    path = roots.write("survey.json", json.dumps(birds.survey()).encode())
    pack = replace(birds.pack, importer=_Probing(how))
    registry = PackRegistry([pack], core_version=aibi.__version__)
    with pytest.raises(ImportRefused) as refused:
        importing(path, registry=registry, pack="birds")
    assert all(r.code != "LIMIT_EXCEEDED" for r in refused.value.refusals)
    assert all(SECRET not in _said(r) for r in refused.value.refusals)


class _Serializer:
    """A serializer an instance holds in place of its class's, which records each use."""

    used: ClassVar[list[str]] = []

    def __getattr__(self, name: str) -> Any:
        _Serializer.used.append(name)
        raise RuntimeError(SECRET)


def test_a_pack_s_refusal_is_dumped_by_its_class_never_by_what_it_holds(
    roots: Roots, importing: Importing, birds: Birds
) -> None:
    """m3: an instance's ``model_dump_json`` and ``__pydantic_serializer__`` are never used; the
    class's serializer finds what the instance holds that is no output's (``check_values``), so
    the refusal is not one of the pack's own."""
    _Serializer.used.clear()
    refusal = _pack_refusal("birds.NO_SURVEY")
    held = vars(refusal)

    def dump(*given: Any, **named: Any) -> str:
        _Serializer.used.append("model_dump_json")
        return _pack_refusal("birds.NO_SURVEY").model_dump_json()

    held["model_dump_json"] = dump
    held["__pydantic_serializer__"] = _Serializer()
    with pytest.raises(ImportRefused) as refused:
        _import(roots, importing, birds, raises=Refused([refusal]))
    assert [str(r.code) for r in refused.value.refusals] == ["PACK_FAILED"]
    assert _Serializer.used == []


def test_a_descriptor_is_dumped_by_its_class_never_by_what_it_holds(
    roots: Roots, importing: Importing, birds: Birds
) -> None:
    _Serializer.used.clear()

    def change(result: ImportResult) -> ImportResult:
        for descriptor in result.descriptors:
            vars(descriptor)["__pydantic_serializer__"] = _Serializer()
        return result

    _import(roots, importing, birds, change)
    assert _Serializer.used == []


def _caught_through(*files: str) -> BaseException:
    """An exception caught in a frame of the first file, raised in a frame of the last, through a
    frame of each file between: functions compiled under those names, as frames carry them."""
    namespace: dict[str, Any] = {}
    last = len(files) - 1
    for at, name in enumerate(files):
        body = "raise ValueError('x')" if at == last else f"return f{at + 1}()"
        if at == 0:
            body = (
                f"try:\n        f{at + 1}()\n    except ValueError as error:\n        return error"
            )
        exec(compile(f"def f{at}():\n    {body}\n", name, "exec"), namespace)
    return cast(BaseException, namespace["f0"]())


def test_the_core_is_the_directory_its_frames_name() -> None:
    """m1: the prefix is taken from a code object of the core's, as tracebacks carry it."""
    from aibi.core.importers import checks

    core = checks._CORE  # pyright: ignore[reportPrivateUsage]
    named = checks._raised_by_core.__code__.co_filename  # pyright: ignore[reportPrivateUsage]
    assert named.startswith(core)
    assert core.endswith(os.sep)
    assert os.path.basename(core.rstrip(os.sep)) == "core"


@pytest.mark.parametrize(
    ("middle", "raiser", "core"),
    [
        ("importers/a.py", "store/b.py", True),
        ("../packs/onco/a.py", "store/b.py", False),
        ("/elsewhere/pack.py", "store/b.py", False),
        ("importers/a.py", "../core_extra/b.py", False),
        ("importers/a.py", "../packs/onco/b.py", False),
    ],
    ids=[
        "the core's alone",
        "a pack's frame between",
        "another file between",
        "a sibling of the core's directory raising",
        "aibi's packs raising",
    ],
)
def test_an_exception_is_the_core_s_only_through_the_core_s_frames(
    middle: str, raiser: str, core: bool
) -> None:
    """m2: every frame from the catch to the raise is the core's, not the raising frame alone; and
    the core is ``aibi/core/`` itself, not a sibling directory nor ``aibi/``."""
    from aibi.core.importers import checks

    root = checks._CORE  # pyright: ignore[reportPrivateUsage]

    def placed(name: str) -> str:
        return name if name.startswith("/") else os.path.normpath(root + name)

    error = _caught_through(placed("engine/c.py"), placed(middle), placed(raiser))
    assert checks._raised_by_core(error) is core  # pyright: ignore[reportPrivateUsage]


class _Key:
    """A key whose hash is a field name's and whose comparison records that it ran."""

    compared: ClassVar[list[str]] = []

    def __init__(self, name: str) -> None:
        self.name = name

    def __hash__(self) -> int:
        return hash(self.name)

    def __eq__(self, other: object) -> bool:
        _Key.compared.append(self.name)
        return False


def _foreign_key(target: Callable[[ImportResult], object], name: str) -> Change:
    """A change that puts a key of the pack's, hashed as ``name`` and first in its probe sequence,
    into the instance dictionary of the object ``target`` picks from the result: a lookup of
    ``name`` there compares the key with it, running the key's code."""

    def change(result: ImportResult) -> ImportResult:
        held = target(result)
        given = vars(held)
        fields: dict[object, object] = {_Key(name): given[name]}
        fields.update(given)
        object.__setattr__(held, "__dict__", fields)
        _Key.compared.clear()  # inserting it compared it with the name; the checks must not
        return result

    return change


def _with_text_note(result: ImportResult) -> ImportResult:
    return replace(_with_error_cell_and_note(result), notes=[_note(message=[text("a"), data("b")])])


_TARGETS: list[tuple[Callable[[ImportResult], object], str]] = [
    (lambda result: result, "sources"),
    (lambda result: _counts_rows(result), "rows"),
    (lambda result: result.sources["sites"], "data"),
    (lambda result: next(iter(result.layouts.values())), "columns"),
    (lambda result: result.notes[0], "kind"),
    (lambda result: result.notes[0].message[0], "text"),
    (lambda result: result.notes[0].message[1], "data"),
]


@pytest.mark.parametrize(
    ("target", "name"),
    _TARGETS,
    ids=["a result", "a typed source", "a text source", "a layout", "a note", "a text", "a datum"],
)
def test_an_object_whose_fields_hold_a_key_of_the_pack_s_is_pack_failed_and_runs_nothing(
    roots: Roots,
    importing: Importing,
    birds: Birds,
    target: Callable[[ImportResult], object],
    name: str,
) -> None:
    """M1 (round 2 after the second redesign): the checks read a field only from a plain
    instance dictionary of exact ``str`` keys, so no key's comparison runs within them."""
    _Key.compared.clear()

    def change(result: ImportResult) -> ImportResult:
        return cast(ImportResult, _foreign_key(target, name)(_with_text_note(result)))

    refusal = _refused(roots, importing, birds, change)
    assert refusal.code == "PACK_FAILED"
    assert _Key.compared == []


def test_an_object_whose_fields_are_no_plain_dict_is_pack_failed(
    roots: Roots, importing: Importing, birds: Birds
) -> None:
    class _Fields(dict[str, Any]):
        pass

    def change(result: ImportResult) -> ImportResult:
        layout = next(iter(result.layouts.values()))
        object.__setattr__(layout, "__dict__", _Fields(vars(layout)))
        return result

    assert _refused(roots, importing, birds, change).code == "PACK_FAILED"


class _TextKey(str):
    """A key of text, hashed as a field's name, whose comparison records that it ran."""

    __slots__ = ()

    def __hash__(self) -> int:
        return str.__hash__(self)

    def __eq__(self, other: object) -> bool:
        _Key.compared.append(str.__str__(self))
        return False


def test_a_field_keyed_by_a_text_subclass_is_pack_failed_and_runs_nothing(
    roots: Roots, importing: Importing, birds: Birds
) -> None:
    """m2 (round 3 after the second redesign): a key must be exactly a ``str``; a subclass of
    ``str`` is refused before any lookup compares it."""
    _Key.compared.clear()

    def change(result: ImportResult) -> ImportResult:
        layout = next(iter(result.layouts.values()))
        given = vars(layout)
        fields: dict[object, object] = {_TextKey("columns"): given["columns"]}
        fields.update(given)
        object.__setattr__(layout, "__dict__", fields)
        _Key.compared.clear()
        return result

    assert _refused(roots, importing, birds, change).code == "PACK_FAILED"
    assert _Key.compared == []


def test_an_object_missing_a_field_is_the_checks_own_failure(
    roots: Roots, importing: Importing, birds: Birds, caplog: pytest.LogCaptureFixture
) -> None:
    """m2 (round 3 after the second redesign): a missing field is the checks' failure in their
    own words, never the pack's ``KeyError``."""

    def change(result: ImportResult) -> ImportResult:
        layout = next(iter(result.layouts.values()))
        given = dict(vars(layout))
        del given["columns"]
        object.__setattr__(layout, "__dict__", given)
        return result

    with caplog.at_level(logging.WARNING):
        refusal = _refused(roots, importing, birds, change)
    assert refusal.code == "PACK_FAILED"
    assert "fields are not its own" in _said(refusal)
    assert "KeyError" not in caplog.text


def test_a_refused_whose_fields_hold_a_key_of_the_pack_s_runs_nothing(
    roots: Roots, importing: Importing, birds: Birds
) -> None:
    """m1 (round 3 after the second redesign): a ``Refused``'s ``refusals`` is read as every
    field of a pack-made object is, so no key's comparison runs in the handler."""
    raised = Refused([_pack_refusal("birds.NO_SURVEY")])
    fields: dict[object, object] = {_Key("refusals"): None}
    fields.update(vars(raised))
    object.__setattr__(raised, "__dict__", fields)
    _Key.compared.clear()
    assert _refused(roots, importing, birds, raises=raised).code == "PACK_FAILED"
    assert _Key.compared == []


class _Counted(list[Any]):
    iterated: ClassVar[list[str]] = []

    def __iter__(self) -> Any:
        _Counted.iterated.append("iter")
        return super().__iter__()


def test_a_hook_s_refusal_is_never_read_before_its_origin_is(
    roots: Roots, importing: Importing, birds: Birds
) -> None:
    """m2 (round 2 after the second redesign): under a limit the pack chose, the reader reads a
    refusal's ``refusals`` only once the core's own code is shown to have raised it."""
    _Counted.iterated.clear()

    def hook(event: str, given: tuple[Any, ...]) -> None:
        if event == "open" and str(given[0]).endswith("survey.json"):
            forged = _forged_limit()
            object.__setattr__(forged, "refusals", _Counted(forged.refusals))
            raise forged

    def how(source: ConfinedPath, options: ImportOptions) -> object:
        return options.reader.read(source, 1)

    path = roots.write("survey.json", json.dumps(birds.survey()).encode())
    pack = replace(birds.pack, importer=_Probing(how))
    registry = PackRegistry([pack], core_version=aibi.__version__)
    _ARMED.append(hook)
    try:
        with pytest.raises(ImportRefused) as refused:
            importing(path, registry=registry, pack="birds")
    finally:
        _ARMED.remove(hook)
    assert [r.code for r in refused.value.refusals] == ["PACK_FAILED"]
    assert _Counted.iterated == []


def test_a_core_directory_that_is_not_aibi_s_core_matches_no_frame(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """m1 (round 2 after the second redesign): code compiled under a bare name fails closed."""
    from aibi.core.importers import checks

    namespace: dict[str, Any] = {}
    exec(compile("def f():\n    pass\n", "importers/checks.py", "exec"), namespace)
    monkeypatch.setattr(checks, "_raised_by_core", namespace["f"])
    assert checks._core_directory() == "\0"  # pyright: ignore[reportPrivateUsage]

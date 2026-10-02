"""Building releases (SPEC §12.2, D220): a table is rebuilt only when a field that affects its
parsing changes, and a change that would give it other source columns is a re-import."""

from typing import Any

import pytest

from aibi.core.engine import build
from aibi.core.store.build import BuildRefused, Built, Layout, read_descriptors
from aibi.core.store.manifest import Manifest
from aibi.core.store.sources import TextSource, TypedSource
from aibi.core.store.store import Store

Library = Any


def _change(store: Store, base: str, descriptors: list[Any]) -> Built:
    """A release changed from ``base``, published so that the sweep keeps it."""
    with store.pin() as pin:
        built = store.change_release(pin, base, descriptors)
        store.publish("lib", built.manifest.hash, "operator:ada")
    return built


def _hashes(manifest: Manifest) -> dict[str, str]:
    return {entry.id: entry.hash for entry in manifest.tables}


def test_a_missing_code_change_rebuilds_its_table_only(
    store: Store, imported: str, library: Library
) -> None:
    changed = library.descriptors(age_codes={"NA": "UNKNOWN", "refused": "NOT_ASSESSED"})
    built = _change(store, imported, changed)
    assert built.rebuilt == {"members"}
    before, after = _hashes(store.manifest(imported)), _hashes(built.manifest)
    assert before["members"] != after["members"]
    assert before["loans"] == after["loans"]
    assert before["books"] == after["books"]
    ages = store.load(built.manifest.hash).rows("members")
    assert ages.cell(2, "age").state.value == "NOT_ASSESSED"


def test_a_change_that_does_not_affect_parsing_reuses_every_blob(
    store: Store, imported: str, library: Library
) -> None:
    relabelled = library.descriptors()
    relabelled[1] = build.table(
        "members",
        ["member_id"],
        source={
            "kind": "file",
            "name": "members",
            "original_name": "members.csv",
            "parse": {
                "format": "csv",
                "delimiter": ",",
                "quote": '"',
                "header_row": 0,
                "skip_rows": 0,
                "encoding": "utf-8",
            },
        },
    ).model_copy(update={"label": "Library members"})
    built = _change(store, imported, relabelled)
    assert built.rebuilt == frozenset()
    assert _hashes(built.manifest) == _hashes(store.manifest(imported))
    assert built.manifest.hash != imported  # the descriptors differ
    stored = read_descriptors(store.blobs.read(built.manifest.descriptors))
    assert [d.label for d in stored if d.id == "members"] == ["Library members"]


def test_a_units_change_rebuilds_the_derivations_that_read_them(
    store: Store, imported: str, library: Library
) -> None:
    codes = {"NA": "UNKNOWN", "refused": "NOT_APPLICABLE"}
    in_months = [
        build.column("members.age", "integer", units="mo", missing_codes=codes)
        if descriptor.id == "members.age"
        else descriptor
        for descriptor in library.descriptors()
    ]
    built = _change(store, imported, in_months)
    assert built.rebuilt == {"members"}
    members = store.load(built.manifest.hash).rows("members")
    assert members.cell(0, "age_months").value == 36.0


def test_changing_the_same_descriptors_twice_gives_the_same_release(
    store: Store, imported: str, library: Library
) -> None:
    changed = library.descriptors(age_codes={"NA": "UNKNOWN"})
    first, second = _change(store, imported, changed), _change(store, imported, changed)
    assert first.manifest.hash == second.manifest.hash


def test_parse_settings_that_give_the_same_header_rebuild(store: Store, library: Library) -> None:
    """With the quote character changed, the header reads the same, so the table is rebuilt."""
    with store.pin() as pin:
        base = store.import_release(
            pin, "lib", library.descriptors(), library.sources(), library.layouts
        )
        store.publish("lib", base.manifest.hash, "operator:ada")
    settings = {
        "format": "csv",
        "delimiter": ",",
        "quote": "'",
        "header_row": 0,
        "skip_rows": 0,
        "encoding": "utf-8",
    }
    built = _change(store, base.manifest.hash, library.descriptors(members_parse=settings))
    assert built.rebuilt == {"members"}


def test_parse_settings_that_change_the_header_are_a_re_import(
    store: Store, imported: str, library: Library
) -> None:
    settings = {
        "format": "csv",
        "delimiter": ",",
        "quote": '"',
        "header_row": 0,
        "skip_rows": 1,  # the header is read from the first member's row
        "encoding": "utf-8",
    }
    with pytest.raises(BuildRefused) as refused:
        _change(store, imported, library.descriptors(members_parse=settings))
    [refusal] = refused.value.refusals
    assert (refusal.code, refusal.path) == ("COLUMNS_CHANGED", "/1/fields/source/parse")


def test_a_column_neither_read_nor_derived_is_refused(
    store: Store, imported: str, library: Library
) -> None:
    added = library.descriptors(extra=[build.column("members.email", "string")])
    with pytest.raises(BuildRefused) as refused:
        _change(store, imported, added)
    assert [(r.code, r.path) for r in refused.value.refusals] == [
        ("COLUMNS_CHANGED", f"/{len(added) - 1}")
    ]


def test_an_import_lays_out_every_table(store: Store, library: Library) -> None:
    layouts = {name: layout for name, layout in library.layouts.items() if name != "books"}
    with store.pin() as pin, pytest.raises(BuildRefused) as refused:
        store.import_release(pin, "lib", library.descriptors(), library.sources(), layouts)
    assert [r.code for r in refused.value.refusals] == ["COLUMNS_CHANGED"]


def test_a_token_beyond_a_decimal_s_exponents_is_reported_not_raised(
    store: Store, library: Library
) -> None:
    members = library.members.replace(b"36", b"0e99999999999999999999")
    with store.pin() as pin:
        built = store.import_release(
            pin, "lib", library.descriptors(), library.sources(members=members), library.layouts
        )
    assert built.reports["members"]["age"].unparsed == (("0e99999999999999999999", 1), ("x", 1))


def test_a_source_that_does_not_parse_is_refused_at_its_settings(
    store: Store, library: Library
) -> None:
    sources = library.sources()
    sources["members"] = TextSource(b"member_id,name\n\xff\xfe,bad\n")
    with store.pin() as pin, pytest.raises(BuildRefused) as refused:
        store.import_release(pin, "lib", library.descriptors(), sources, library.layouts)
    [refusal] = refused.value.refusals
    assert (refusal.code, refusal.path) == ("UNPARSEABLE_SOURCE", "/1/fields/source/parse")


def test_a_header_other_than_the_layout_is_refused(store: Store, library: Library) -> None:
    layouts = dict(library.layouts)
    layouts["books"] = Layout("books", (("book_id", "id"), ("title", "title"), ("genre", "genre")))
    with store.pin() as pin, pytest.raises(BuildRefused) as refused:
        store.import_release(pin, "lib", library.descriptors(), library.sources(), layouts)
    assert [r.code for r in refused.value.refusals] == ["COLUMNS_CHANGED"]


def test_a_header_name_that_no_descriptor_can_hold_is_refused(
    store: Store, library: Library
) -> None:
    for name, code in (("x" * 4097, "LIMIT_EXCEEDED"), ("tags\ufffe", "UNPARSEABLE_SOURCE")):
        sources = library.sources(members=library.members.replace(b"interests", name.encode()))
        layouts = dict(library.layouts)
        layouts["members"] = Layout(
            "members", (*library.layouts["members"].columns[:4], ("interests", name))
        )
        with store.pin() as pin, pytest.raises(BuildRefused) as refused:
            store.import_release(pin, "lib", library.descriptors(), sources, layouts)
        [refusal] = refused.value.refusals
        assert (refusal.code, refusal.path) == (code, "/1/fields/source/parse")


def test_a_typed_source_utf8_cannot_carry_is_refused_at_its_table(
    store: Store, library: Library
) -> None:
    for columns, row in (
        (("Loan", "Member", "Book", "Borrowed", "Days"), (1, "m-\ud800", "b-1", None, None)),
        (("Loan", "Member", "Book", "Borrowed", "\udfff"), (1, "m-1", "b-1", None, None)),
    ):
        sources = library.sources()
        sources["loans"] = TypedSource(columns, (row,))
        with store.pin() as pin, pytest.raises(BuildRefused) as refused:
            store.import_release(pin, "lib", library.descriptors(), sources, library.layouts)
        [refusal] = refused.value.refusals
        loans = next(i for i, d in enumerate(library.descriptors()) if d.id == "loans")
        assert (refusal.code, refusal.path) == ("UNPARSEABLE_SOURCE", f"/{loans}")


def test_a_manifest_lists_its_sources_and_tables_once_each_in_order(
    store: Store, imported: str
) -> None:
    manifest = store.manifest(imported)
    for update in (
        {"sources": tuple(reversed(manifest.sources))},
        {"sources": (manifest.sources[0], *manifest.sources)},
        {"tables": tuple(reversed(manifest.tables))},
    ):
        with pytest.raises(ValueError, match="sorted"):
            Manifest.model_validate({**manifest.model_dump(), **update})


def test_the_manifest_is_canonical_and_names_every_blob(store: Store, imported: str) -> None:
    manifest = store.manifest(imported)
    data = store.blobs.read(imported.removeprefix("sha256:"))
    assert Manifest.from_bytes(data) == manifest
    assert [source.name for source in manifest.sources] == ["books", "loans", "members"]
    assert [table.id for table in manifest.tables] == ["books", "loans", "members"]
    assert {source.kind for source in manifest.sources} == {"text", "rows"}
    assert all(store.blobs.exists(blob) for blob in manifest.blobs())
    with pytest.raises(ValueError, match="RFC 8785"):
        Manifest.from_bytes(data.replace(b",", b", ", 1))

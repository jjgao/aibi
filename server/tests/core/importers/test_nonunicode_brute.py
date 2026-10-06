"""Names that are not Unicode text, brute force over every entry point and every string slot a
name arrives in (SPEC A6, §8.1, §14, D226, D232, D307, D309, D398).

The table of what happens is not prose: it is generated here from the code. Each entry point has
its slots, and the slots that are kinds come from the code's own sets (``describe``'s origins by
reflection, ``files.READ``, ``files.REFUSED``, ``files._SKIPPED_KINDS``), so a new field or kind
fails until it is given a slot or an exclusion with its reason. Each slot is run

- alone, in every class of code point its medium can carry, with the refusal's code and the
  server's words expected (or an import, with a note, for a kind that is only shown);
- with every other slot of its entry point, the winner computed from the entry point's declared
  precedence (``PRECEDENCE``), so the order of the checks is executable;
- with a name that is text in its place (the twin), which imports.

Every segment of every refusal and note is checked: Unicode text, at most 200 characters, never
cut inside an escape, naming no internal class (``nonunicode.check_shown``). Imports run through
``import_dataset``, so the store, the import slot and the report are exercised too.
"""

import dataclasses
import io
import itertools
import os
import socket
import sqlite3
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from functools import partial
from pathlib import Path
from typing import Any

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from tests.core.importers.builders import Entry, build_ods, build_xlsx, build_zip
from tests.core.importers.nonunicode import (
    BYTES,
    DISK,
    LIBRARY,
    TEXT,
    UTF8,
    XML,
    Bad,
    Outcome,
    matches,
    outcome,
)

from aibi.core.importers import files
from aibi.core.importers.confine import Confinement
from aibi.core.importers.databases import Connection, resolve
from aibi.core.importers.describe import DatasetOrigin, TableOrigin, describe
from aibi.core.importers.infer import SourceTable, infer
from aibi.core.importers.run import import_dataset
from aibi.core.importers.snapshot import Kind
from aibi.core.importers.uploads import UploadArea
from aibi.core.schema.limits import MAX_STRING, ImportLimits
from aibi.core.schema.pack_api import ImportOptions
from aibi.core.store.store import Store

AT = "2026-01-01T00:00:00Z"
UNPARSEABLE = "UNPARSEABLE_SOURCE"
LONG = Bad("long", "n" * (MAX_STRING + 1), None)
"""A name over ``MAX_STRING`` characters: ``LIMIT_EXCEEDED``, which has its place in the order."""


# --- describe, by reflection over its origins -------------------------------------------------

WHAT: dict[tuple[str, str], str] = {
    ("DatasetOrigin", "name"): "The dataset's name",
    ("DatasetOrigin", "location"): "The source's location",
    ("TableOrigin", "original_name"): "A table's original name",
    ("TableOrigin", "label"): "A table's original name",
    ("TableOrigin", "columns"): "A column's name",
    ("TableOrigin", "comment"): "A table's comment",
    ("TableOrigin", "column_comments"): "A column's comment",
    ("TableOrigin", "parse_evidence"): "A table's parse evidence",
    ("DatasetOrigin", "packs"): "A pack's name",
}
"""What ``describe`` says of each string field that is not Unicode text."""
EXCLUDED: dict[tuple[str, str], str] = {
    ("DatasetOrigin", "kind"): "a Literal the importer chooses",
    ("TableOrigin", "kind"): "a Literal the importer chooses",
    ("TableOrigin", "parse"): "ParseSettings, a model whose strings are checked when it is built",
}
DESCRIBE_ORDER = [
    ("DatasetOrigin", "name"),
    ("DatasetOrigin", "location"),
    ("TableOrigin", "original_name"),
    ("TableOrigin", "label"),
    ("TableOrigin", "columns"),
    ("TableOrigin", "comment"),
    ("TableOrigin", "column_comments"),
    ("TableOrigin", "parse_evidence"),
    ("DatasetOrigin", "packs"),
]
"""The order ``describe`` checks in: the dataset's name and location, then each table's fields in
this order, then the packs (D398). Within a table's fields, tables go in order."""


def test_every_field_of_describe_s_origins_has_a_slot_or_an_exclusion() -> None:
    fields = {
        (origin.__name__, found.name)
        for origin in (DatasetOrigin, TableOrigin)
        for found in dataclasses.fields(origin)
    }
    assert set(WHAT) | set(EXCLUDED) == fields
    assert not set(WHAT) & set(EXCLUDED)
    assert sorted(DESCRIBE_ORDER) == sorted(WHAT)


_INFERRED = infer([SourceTable("t", ("a",), (("1",),), {}), SourceTable("u", ("b",), (), {})])


def _describe(bad: dict[tuple[str, str], Bad], table: dict[tuple[str, str], int]) -> Outcome:
    """``describe`` with each field of ``bad`` holding its class, in table 0 or 1 (``table``)."""

    def given(key: tuple[str, str], good: str, index: int = 0) -> str:
        if key in bad and table.get(key, 0) == index:
            return "x" + bad[key].text
        return good

    def origin(index: int, column: str) -> TableOrigin:
        def one(key: tuple[str, str], good: str) -> str:
            return given(key, good, index)

        return TableOrigin(
            "file",
            one(("TableOrigin", "original_name"), f"t{index}.csv"),
            one(("TableOrigin", "label"), f"t{index}"),
            (one(("TableOrigin", "columns"), column),),
            parse_evidence=one(("TableOrigin", "parse_evidence"), "detected"),
            comment=one(("TableOrigin", "comment"), "a table"),
            column_comments=(one(("TableOrigin", "column_comments"), "a column"),),
        )

    dataset = DatasetOrigin(
        given(("DatasetOrigin", "name"), "d"),
        given(("DatasetOrigin", "location"), "l"),
        (given(("DatasetOrigin", "packs"), "p"),),
    )
    origins = {"t": origin(0, "a"), "u": origin(1, "b")}
    return outcome(lambda: describe(dataset, origins, _INFERRED, by="importer:x@1", at=AT))


def _describe_rank(key: tuple[str, str], table: int) -> tuple[int, ...]:
    index = DESCRIBE_ORDER.index(key)
    if key[0] == "DatasetOrigin":
        return (0 if index < 2 else 2, index)
    return (1, table, index)


@pytest.mark.parametrize(
    ("key", "bad"),
    [(key, bad) for key in DESCRIBE_ORDER for bad in (*LIBRARY, TEXT)],
    ids=[f"{key[1]}-{bad.label}" for key in DESCRIBE_ORDER for bad in (*LIBRARY, TEXT)],
)
def test_describe_alone(key: tuple[str, str], bad: Bad) -> None:
    expected = None if bad is TEXT else (UNPARSEABLE, WHAT[key])
    assert matches(_describe({key: bad}, {}), expected)


_DESCRIBE_PAIRS = [
    ((a, ta), (b, tb))
    for (a, ta), (b, tb) in itertools.combinations(
        [(key, t) for key in DESCRIBE_ORDER for t in ((0, 1) if key[0] == "TableOrigin" else (0,))],
        2,
    )
    if a != b
]


@pytest.mark.parametrize(
    ("first", "second"),
    _DESCRIBE_PAIRS,
    ids=[f"{a[1]}@{ta}+{b[1]}@{tb}" for (a, ta), (b, tb) in _DESCRIBE_PAIRS],
)
def test_describe_pair(
    first: tuple[tuple[str, str], int], second: tuple[tuple[str, str], int]
) -> None:
    (a, ta), (b, tb) = first, second
    bad = LIBRARY[0]
    winner = min((_describe_rank(a, ta), a), (_describe_rank(b, tb), b))[1]
    assert matches(_describe({a: bad, b: bad}, {a: ta, b: tb}), (UNPARSEABLE, WHAT[winner]))


# --- The store all imports go into --------------------------------------------------------------


@pytest.fixture(scope="module")
def store(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Store]:
    ticks = itertools.count()
    start = datetime(2026, 1, 1, tzinfo=UTC)
    opened = Store(
        tmp_path_factory.mktemp("store") / "data",
        clock=lambda: start + timedelta(seconds=next(ticks)),
    )
    yield opened
    opened.close()


_DATASETS = itertools.count()


def _import(store: Store, source: Any, confinement: Confinement, **given: Any) -> object:
    options = ImportOptions(
        dataset=f"d{next(_DATASETS)}",
        reader=confinement,
        limits=ImportLimits(),
        at=AT,
        **given,
    )
    return import_dataset(store, source, options, "operator:a")


# --- Slots ---------------------------------------------------------------------------------------


@dataclass
class Case:
    """Where one case writes its source, and what it gives the import."""

    root: Path
    directory: Path
    given: dict[str, Any] = field(default_factory=dict[str, Any])
    slots: dict[str, Bad] = field(default_factory=dict[str, Bad])


Place = Callable[[Case, Bad], None]


@dataclass(frozen=True)
class Slot:
    classes: tuple[Bad, ...]
    place: Place
    rank: tuple[int, ...] | None
    """Its place in the entry point's precedence; ``None`` for a name that is only shown."""
    outcome: tuple[str, str] | None
    """``(code, the server's words)`` when it alone is refused; ``None`` when it imports."""
    unless: tuple[str, ...] = ()
    """Slots that, present, make this one's name unused (an option's name over the file's)."""
    twin: tuple[str, str] | None = None
    """What its twin gives, when that is not an import (a kind that is not read whatever its
    name)."""


def _write(case: Case, name: bytes, content: bytes, directory: Path | None = None) -> None:
    where = os.fsencode(directory or case.directory)
    with open(os.path.join(where, name), "wb") as file:
        file.write(content)


def _disk(bad: Bad) -> bytes:
    assert bad.disk is not None
    return bad.disk


def _parquet(table: pa.Table) -> bytes:
    buffer = io.BytesIO()
    pq.write_table(table, buffer, store_schema=False)
    return buffer.getvalue()


def _renamed(content: bytes, bad: Bad) -> bytes:
    """A Parquet file whose name ``QQQQ`` is written as bytes that are not UTF-8."""
    return content.replace(b"QQQQ", b"Q" + _disk(bad)[:1] + b"QQ")


CSV = b"x,y\n1,2\n"
CONTENT: dict[str, bytes] = {
    "csv": CSV,
    "tsv": b"x\ty\n1\t2\n",
    "txt": CSV,
    "xlsx": build_xlsx([("s", [["h"], ["v"]])]),
    "xlsm": build_xlsx([("s", [["h"], ["v"]])]),
    "ods": build_ods([("s", [["h"], ["v"]])]),
    "parquet": _parquet(pa.table({"c": pa.array(["v"])})),
}
"""A file of each kind ``files.READ`` reads."""
PARQUET_NAME = _parquet(pa.table({"QQQQ": pa.array(["v"])}))
PARQUET_FIELD = _parquet(pa.table({"s": pa.array([{"QQQQ": 1}])}))


def _listed(stem: bytes, extension: bytes, content: bytes) -> Place:
    return lambda case, bad: _write(case, stem + _disk(bad) + extension, content)


def _special(kind: str) -> Place:
    def place(case: Case, bad: Bad) -> None:
        path = os.path.join(os.fsencode(case.directory), b"m_" + kind.encode() + _disk(bad))
        if kind == "directory":
            os.mkdir(path)
        elif kind == "symlink":
            os.symlink(b"zz_ok.csv", path)
        elif kind == "fifo":
            os.mkfifo(path)
        else:
            sock = socket.socket(socket.AF_UNIX)
            try:
                sock.bind(path)
            finally:
                sock.close()

    return place


def _header(stem: str, separator: str = ",") -> Place:
    return lambda case, bad: _write(
        case, f"{stem}.csv".encode(), f"a{bad.text}{separator}b\n1{separator}2\n".encode()
    )


def _sheet(file: str, build: Callable[[Any], bytes], header: bool = False) -> Place:
    def place(case: Case, bad: Bad) -> None:
        if header:
            content = build([("s", [["h" + bad.text], ["v"]])])
        else:
            content = build([("s" + bad.text, [["h"], ["v"]])])
        _write(case, file.encode(), content)

    return place


def _empty_sheet(case: Case, bad: Bad) -> None:
    _write(case, b"g_empty.xlsx", build_xlsx([("e" + bad.text, []), ("s", [["h"], ["v"]])]))


def _option(name: str, value: Callable[[Bad], str]) -> Place:
    def place(case: Case, bad: Bad) -> None:
        case.given[name] = value(bad)

    return place


def _nothing(case: Case, bad: Bad) -> None:
    """The slot is placed by the case itself (the directory's own names)."""


# --- The directory entry point ---------------------------------------------------------------
#
# PRECEDENCE["directory"]: 0 the dataset's name over MAX_STRING; 1 listing, by file name (a file
# of a kind read whose name is not text); 2 reading, by file name (a Parquet file's names, a
# name over MAX_STRING); 3 describe, in DESCRIBE_ORDER, its tables in file order. Every file of a
# slot is named so that its place in the order is fixed; the dataset's own file is zz_ok.csv.

FILE_ORDER = {
    **{f"read.{kind}": index for index, kind in enumerate(files.READ)},
    "csv.header": 20,
    "sheet.name": 21,
    "sheet.header": 22,
    "ods.sheet": 23,
    "parquet.name.bytes": 24,
    "parquet.field.bytes": 25,
    "parquet.name.text": 26,
    "csv.header.long": 27,
}


def _table(slot: str, field_: tuple[str, str]) -> tuple[int, ...]:
    return (3, 1, FILE_ORDER[slot], DESCRIBE_ORDER.index(field_))


DIRECTORY: dict[str, Slot] = {
    # 1: a file of each kind read whose own name is not text, refused as it is listed
    **{
        f"read.{kind}": Slot(
            DISK,
            _listed(f"a{index:02}_".encode(), f".{kind}".encode(), CONTENT[kind]),
            (1, FILE_ORDER[f"read.{kind}"]),
            (UNPARSEABLE, "A file's name"),
        )
        for index, kind in enumerate(files.READ)
    },
    # only shown: each kind the listing skips and notes
    **{
        f"refused.{kind}": Slot(DISK, _listed(b"m_r", f".{kind}".encode(), b"x"), None, None)
        for kind in files.REFUSED
    },
    "skipped.directory": Slot(DISK, _special("directory"), None, None),
    "skipped.symlink": Slot(DISK, _special("symlink"), None, None),
    "skipped.other": Slot(DISK, _special("fifo"), None, None),
    "skipped.socket": Slot(DISK, _special("socket"), None, None),
    "hidden": Slot(DISK, _listed(b".h", b".csv", CSV), None, None),
    "archive": Slot(DISK, _listed(b"m_z", b".zip", build_zip([Entry("a.csv", CSV)])), None, None),
    "not.read": Slot(DISK, _listed(b"m_b", b".bin", b"x"), None, None),
    "extension": Slot(DISK, lambda c, b: _write(c, b"m_e.cs" + _disk(b), CSV), None, None),
    "empty.sheet": Slot(XML, _empty_sheet, None, None),
    # 2: what the reader finds
    "parquet.name.bytes": Slot(
        BYTES,
        lambda c, b: _write(c, b"h_name.parquet", _renamed(PARQUET_NAME, b)),
        (2, FILE_ORDER["parquet.name.bytes"]),
        (UNPARSEABLE, "Not a Parquet file that can be read: a column's or a field's name"),
    ),
    "parquet.field.bytes": Slot(
        BYTES,
        lambda c, b: _write(c, b"i_field.parquet", _renamed(PARQUET_FIELD, b)),
        (2, FILE_ORDER["parquet.field.bytes"]),
        (UNPARSEABLE, "Not a Parquet file that can be read: a column's or a field's name"),
        twin=("UNSUPPORTED_FORMAT", "A Parquet column's type is not read"),
    ),
    "csv.header.long": Slot(
        (LONG,),
        _header("k_long"),
        (2, FILE_ORDER["csv.header.long"]),
        ("LIMIT_EXCEEDED", "A column's name has more than"),
    ),
    # 3: describe
    "option.name": Slot(
        LIBRARY,
        _option("name", lambda bad: "n" + bad.text),
        (3, 0, DESCRIBE_ORDER.index(("DatasetOrigin", "name"))),
        (UNPARSEABLE, "The dataset's name"),
    ),
    "directory.name": Slot(
        DISK,
        _nothing,
        (3, 0, DESCRIBE_ORDER.index(("DatasetOrigin", "name"))),
        (UNPARSEABLE, "The dataset's name"),
        unless=("option.name", "option.name.long"),
    ),
    "directory.location": Slot(
        DISK,
        _nothing,
        (3, 0, DESCRIBE_ORDER.index(("DatasetOrigin", "location"))),
        (UNPARSEABLE, "The source's location"),
    ),
    "csv.header": Slot(
        UTF8,
        _header("d_head"),
        _table("csv.header", ("TableOrigin", "columns")),
        (UNPARSEABLE, "A column's name"),
    ),
    "sheet.name": Slot(
        XML,
        _sheet("e_sheet.xlsx", build_xlsx),
        _table("sheet.name", ("TableOrigin", "original_name")),
        (UNPARSEABLE, "A table's original name"),
    ),
    "sheet.header": Slot(
        XML,
        _sheet("f_head.xlsx", build_xlsx, header=True),
        _table("sheet.header", ("TableOrigin", "columns")),
        (UNPARSEABLE, "A column's name"),
    ),
    "ods.sheet": Slot(
        XML,
        _sheet("f_sheet.ods", build_ods),
        _table("ods.sheet", ("TableOrigin", "original_name")),
        (UNPARSEABLE, "A table's original name"),
    ),
    "parquet.name.text": Slot(
        UTF8,
        lambda c, b: _write(c, b"j_name.parquet", _parquet(pa.table({"q" + b.text: ["v"]}))),
        _table("parquet.name.text", ("TableOrigin", "columns")),
        (UNPARSEABLE, "A column's name"),
    ),
    # 0: the dataset's name over MAX_STRING
    "option.name.long": Slot(
        (LONG,),
        _option("name", lambda bad: bad.text),
        (0,),
        ("LIMIT_EXCEEDED", "The dataset's name has more than"),
    ),
}

KINDS_NOT_A_SLOT = {
    "unconfined": "a file swapped while it is listed; tests/core/importers/test_confine.py",
}
"""Kinds of ``files._SKIPPED_KINDS`` no slot makes, with why."""


def test_every_kind_the_file_importer_knows_has_a_slot() -> None:
    kinds = {f"read.{kind}" for kind in files.READ}
    kinds |= {f"refused.{kind}" for kind in files.REFUSED}
    kinds |= {f"skipped.{kind}" for kind in files._SKIPPED_KINDS}  # pyright: ignore[reportPrivateUsage]
    assert kinds - set(DIRECTORY) == {f"skipped.{kind}" for kind in KINDS_NOT_A_SLOT}


def _directory_case(root: Path, bad: dict[str, Bad]) -> Case:
    def name(slot: str, stem: bytes) -> bytes:
        return stem + (_disk(bad[slot]) if slot in bad else b"")

    path = os.path.join(
        os.fsencode(root),
        name("directory.location", b"d"),
        name("directory.name", b"set"),
    )
    os.makedirs(path)
    case = Case(root, Path(os.fsdecode(path)), slots=bad)
    _write(case, b"zz_ok.csv", CSV)
    for slot, found in bad.items():
        DIRECTORY[slot].place(case, found)
    return case


def _expected(slots: dict[str, Slot], bad: dict[str, Bad]) -> tuple[str, str] | None:
    refusing = [
        (found.rank, found.outcome)
        for name, found in ((name, slots[name]) for name in bad)
        if bad[name] is not TEXT
        and found.rank is not None
        and not any(other in bad and bad[other] is not TEXT for other in found.unless)
    ]
    return min(refusing, key=lambda pair: pair[0])[1] if refusing else None


def _shown(bad: dict[str, Bad], expected: tuple[str, str] | None) -> list[str]:
    """The names an import with no refusal must note: every bad name, since each is only shown
    (a refusal, when one is expected, stands in for the notes)."""
    return [] if expected is not None else [f.text for f in bad.values() if f is not TEXT]


def _run_directory(store: Store, root: Path, bad: dict[str, Bad]) -> Outcome:
    case = _directory_case(root, bad)
    confinement = Confinement.of(root)
    return outcome(
        lambda: _import(store, confinement.confine(case.directory), confinement, **case.given),
        _shown(bad, _expected(DIRECTORY, bad)),
    )


def _singles(slots: dict[str, Slot]) -> list[tuple[str, Bad]]:
    return [(name, bad) for name, found in slots.items() for bad in found.classes]


def _pairs(slots: dict[str, Slot], apart: Sequence[set[str]] = ()) -> list[tuple[str, str]]:
    return [
        (a, b)
        for a, b in itertools.combinations(slots, 2)
        if not any({a, b} <= together for together in apart)
    ]


def _id(pair: tuple[str, Bad] | tuple[str, str]) -> str:
    return f"{pair[0]}-{pair[1]!r}" if isinstance(pair[1], Bad) else f"{pair[0]}+{pair[1]}"


@pytest.mark.parametrize(("slot", "bad"), _singles(DIRECTORY), ids=map(_id, _singles(DIRECTORY)))
def test_directory_alone(store: Store, tmp_path: Path, slot: str, bad: Bad) -> None:
    got = _run_directory(store, tmp_path, {slot: bad})
    assert matches(got, _expected(DIRECTORY, {slot: bad})), got


@pytest.mark.parametrize("slot", list(DIRECTORY))
def test_directory_twin(store: Store, tmp_path: Path, slot: str) -> None:
    if DIRECTORY[slot].classes == (LONG,):
        pytest.skip("the twin of a long name is any name the other tests import")
    assert matches(_run_directory(store, tmp_path, {slot: TEXT}), DIRECTORY[slot].twin)


@pytest.mark.parametrize(("a", "b"), _pairs(DIRECTORY), ids=map(_id, _pairs(DIRECTORY)))
def test_directory_pair(store: Store, tmp_path: Path, a: str, b: str) -> None:
    bad = {a: DIRECTORY[a].classes[0], b: DIRECTORY[b].classes[0]}
    got = _run_directory(store, tmp_path, bad)
    assert matches(got, _expected(DIRECTORY, bad)), got


# --- A single file, given by its path or as an upload ----------------------------------------
#
# PRECEDENCE["file"]: 0 the dataset's name over MAX_STRING; 1 the upload's original name over
# MAX_STRING, then the file's kind (its extension); 2 reading; 3 describe.


def _single_file(case: Case, bad: Bad) -> None:
    """Placed by ``_file_case``."""


FILE: dict[str, Slot] = {
    "file.stem": Slot(
        DISK,
        _single_file,
        (3, 0, 0),
        (UNPARSEABLE, "The dataset's name"),
        unless=("option.name", "option.name.long", "original.name", "original.name.long"),
    ),
    "file.extension": Slot(
        DISK,
        _single_file,
        (1, 1),
        ("UNSUPPORTED_FORMAT", "Files of this kind are not imported"),
        twin=("UNSUPPORTED_FORMAT", "Files of this kind are not imported"),
    ),
    "file.directory": Slot(DISK, _nothing, (3, 0, 1), (UNPARSEABLE, "The source's location")),
    "original.name": Slot(
        LIBRARY,
        _option("original_name", lambda bad: "o" + bad.text + ".csv"),
        (3, 0, 0),
        (UNPARSEABLE, "The dataset's name"),
        unless=("option.name", "option.name.long"),
    ),
    "option.name": Slot(
        LIBRARY,
        _option("name", lambda bad: "n" + bad.text),
        (3, 0, 0),
        (UNPARSEABLE, "The dataset's name"),
    ),
    "csv.header": Slot(UTF8, _single_file, (3, 1, 0, 4), (UNPARSEABLE, "A column's name")),
    "csv.header.long": Slot(
        (LONG,), _single_file, (2,), ("LIMIT_EXCEEDED", "A column's name has more than")
    ),
    "option.name.long": Slot(
        (LONG,),
        _option("name", lambda bad: bad.text),
        (0,),
        ("LIMIT_EXCEEDED", "The dataset's name has more than"),
    ),
    "original.name.long": Slot(
        (LONG,),
        _option("original_name", lambda bad: bad.text + ".csv"),
        (1, 0),
        ("LIMIT_EXCEEDED", "The upload's original name has more than"),
        # the dataset's name, the original name's stem, is over MAX_STRING first
    ),
}


def _file_expected(bad: dict[str, Bad]) -> tuple[str, str] | None:
    given = {name for name, found in bad.items() if found is not TEXT}
    if "original.name.long" in given and not given & {"option.name", "option.name.long"}:
        return ("LIMIT_EXCEEDED", "The dataset's name has more than")
    return _expected(FILE, bad)


def _run_file(store: Store, root: Path, bad: dict[str, Bad]) -> Outcome:
    directory = os.path.join(
        os.fsencode(root), b"d" + (_disk(bad["file.directory"]) if "file.directory" in bad else b"")
    )
    os.makedirs(directory)
    stem = b"s" + (_disk(bad["file.stem"]) if "file.stem" in bad else b"")
    extension = b".cs" + _disk(bad["file.extension"]) if "file.extension" in bad else b".csv"
    header = "a" + (bad["csv.header"].text if "csv.header" in bad else "")
    if "csv.header.long" in bad:
        header = bad["csv.header.long"].text
    path = os.path.join(directory, stem + extension)
    with open(path, "wb") as file:
        file.write(f"{header},b\n1,2\n".encode())
    case = Case(root, Path(os.fsdecode(directory)))
    for slot, found in bad.items():
        FILE[slot].place(case, found)
    confinement = Confinement.of(root)
    return outcome(
        lambda: _import(store, confinement.confine(os.fsdecode(path)), confinement, **case.given)
    )


_FILE_APART = [{"csv.header", "csv.header.long"}]
"""Slots that are one name: a pair of them is one slot."""


@pytest.mark.parametrize(("slot", "bad"), _singles(FILE), ids=map(_id, _singles(FILE)))
def test_file_alone(store: Store, tmp_path: Path, slot: str, bad: Bad) -> None:
    got = _run_file(store, tmp_path, {slot: bad})
    assert matches(got, _file_expected({slot: bad})), got


@pytest.mark.parametrize("slot", [name for name in FILE if FILE[name].classes != (LONG,)])
def test_file_twin(store: Store, tmp_path: Path, slot: str) -> None:
    assert matches(_run_file(store, tmp_path, {slot: TEXT}), FILE[slot].twin)


@pytest.mark.parametrize(
    ("a", "b"), _pairs(FILE, _FILE_APART), ids=map(_id, _pairs(FILE, _FILE_APART))
)
def test_file_pair(store: Store, tmp_path: Path, a: str, b: str) -> None:
    bad = {a: FILE[a].classes[0], b: FILE[b].classes[0]}
    got = _run_file(store, tmp_path, bad)
    assert matches(got, _file_expected(bad)), got


# --- A zip archive ----------------------------------------------------------------------------
#
# PRECEDENCE["zip"]: 0 each member's archive checks, in the archive's order (absolute, leading
# out, a link, encrypted, a nested archive); 1 a member's own name, in name order; 3 describe.

X = "\uffff"
ZIP: dict[str, tuple[Entry, tuple[int, ...] | None, tuple[str, str] | None]] = {
    "absolute": (
        Entry("/a" + X + ".csv", CSV),
        (0, 0),
        ("ARCHIVE_REFUSED", "An archive entry has an absolute name"),
    ),
    "leads.out": (
        Entry("../b" + X + ".csv", CSV),
        (0, 1),
        ("ARCHIVE_REFUSED", "An archive entry leads out"),
    ),
    "link": (
        Entry("c1" + X + ".csv", b"zz_ok.csv", symlink=True),
        (0, 2),
        ("ARCHIVE_REFUSED", "An archive entry is a symbolic link"),
    ),
    "encrypted": (
        Entry("c2" + X + ".csv", CSV, encrypted=True),
        (0, 3),
        ("ARCHIVE_REFUSED", "An archive entry is encrypted"),
    ),
    "nested": (
        Entry("c3" + X + ".zip", build_zip([Entry("a.csv", CSV)])),
        (0, 4),
        ("ARCHIVE_REFUSED", "An archive holds another archive"),
    ),
    "member": (Entry("d" + X + ".csv", CSV), (1, 5), (UNPARSEABLE, "A file's name")),
    "folder": (Entry("e" + X + "/x.csv", CSV), None, None),
    "hidden": (Entry(".f" + X + ".csv", CSV), None, None),
    "not.read": (Entry("g" + X + ".bin", CSV), None, None),
    "header": (
        Entry("h.csv", ("a" + X + ",b\n1,2\n").encode()),
        (3, 1, 7, 4),
        (UNPARSEABLE, "A column's name"),
    ),
}


def _run_zip(store: Store, root: Path, slots: Sequence[str], twin: bool = False) -> Outcome:
    entries = [Entry("zz_ok.csv", CSV)]
    for slot in slots:
        entry = ZIP[slot][0]
        if twin:
            entry = dataclasses.replace(
                entry,
                name=entry.name.replace(X, "z"),
                content=entry.content.replace(X.encode(), b"z"),
            )
        entries.append(entry)
    path = root / "s.zip"
    path.write_bytes(build_zip(entries))
    confinement = Confinement.of(root)
    # a member in a folder is imported as a file of its own, and its folder's name shown nowhere
    only_shown = (
        not twin and _zip_expected(slots) is None and bool({"hidden", "not.read"} & set(slots))
    )
    return outcome(
        lambda: _import(store, confinement.confine(path), confinement), [X] if only_shown else []
    )


def _zip_expected(slots: Sequence[str]) -> tuple[str, str] | None:
    found = [(ZIP[slot][1], ZIP[slot][2]) for slot in slots if ZIP[slot][1] is not None]
    return min(found, key=lambda pair: pair[0])[1] if found else None  # pyright: ignore


_ZIP_CASES = [(slot,) for slot in ZIP] + list(itertools.combinations(ZIP, 2))


@pytest.mark.parametrize("slots", _ZIP_CASES, ids=["+".join(case) for case in _ZIP_CASES])
def test_zip(store: Store, tmp_path: Path, slots: tuple[str, ...]) -> None:
    got = _run_zip(store, tmp_path, slots)
    assert matches(got, _zip_expected(slots)), got


@pytest.mark.parametrize("slot", [s for s in ZIP if s not in ("absolute", "leads.out", "link")])
def test_zip_twin(store: Store, tmp_path: Path, slot: str) -> None:
    expected = ZIP[slot][2] if slot in ("encrypted", "nested") else None
    assert matches(_run_zip(store, tmp_path, (slot,), twin=True), expected)


# --- A single workbook and a single Parquet file ----------------------------------------------
#
# PRECEDENCE["workbook"]: describe, the sheets in order. PRECEDENCE["parquet"]: 2 a name that is
# not UTF-8 (the file does not open), then a nested field's type (not read); 3 describe.

WORKBOOK: dict[str, tuple[tuple[Bad, ...], tuple[int, ...] | None, tuple[str, str] | None]] = {
    "first.sheet": (XML, (3, 0, 0), (UNPARSEABLE, "A table's original name")),
    "first.header": (XML, (3, 0, 4), (UNPARSEABLE, "A column's name")),
    "empty.sheet": (XML, None, None),
    "second.sheet": (XML, (3, 1, 0), (UNPARSEABLE, "A table's original name")),
    "second.header": (XML, (3, 1, 4), (UNPARSEABLE, "A column's name")),
}


def _run_workbook(store: Store, root: Path, bad: dict[str, Bad], ods: bool = False) -> Outcome:
    def name(slot: str, good: str) -> str:
        return good + (bad[slot].text if slot in bad else "")

    sheets = [
        (name("first.sheet", "a"), [[name("first.header", "h")], ["v"]]),
        (name("empty.sheet", "b"), []),
        (name("second.sheet", "c"), [[name("second.header", "h")], ["v"]]),
    ]
    path = root / ("w.ods" if ods else "w.xlsx")
    path.write_bytes((build_ods if ods else build_xlsx)(sheets))
    confinement = Confinement.of(root)
    return outcome(
        lambda: _import(store, confinement.confine(path), confinement),
        _shown(bad, _table_expected(WORKBOOK, bad)),
    )


def _table_expected(
    table: dict[str, tuple[tuple[Bad, ...], tuple[int, ...] | None, tuple[str, str] | None]],
    bad: dict[str, Bad],
) -> tuple[str, str] | None:
    found = [
        (table[slot][1], table[slot][2])
        for slot in bad
        if bad[slot] is not TEXT and table[slot][1] is not None
    ]
    return min(found, key=lambda pair: pair[0])[1] if found else None  # pyright: ignore


_WORKBOOK_SINGLES = [(slot, bad) for slot, spec in WORKBOOK.items() for bad in (*spec[0], TEXT)]


@pytest.mark.parametrize("ods", [False, True], ids=["xlsx", "ods"])
@pytest.mark.parametrize(("slot", "bad"), _WORKBOOK_SINGLES, ids=map(_id, _WORKBOOK_SINGLES))
def test_workbook_alone(store: Store, tmp_path: Path, slot: str, bad: Bad, ods: bool) -> None:
    got = _run_workbook(store, tmp_path, {slot: bad}, ods)
    assert matches(got, _table_expected(WORKBOOK, {slot: bad})), got


@pytest.mark.parametrize("pair", list(itertools.combinations(WORKBOOK, 2)), ids="+".join)
def test_workbook_pair(store: Store, tmp_path: Path, pair: tuple[str, str]) -> None:
    bad = {slot: WORKBOOK[slot][0][0] for slot in pair}
    got = _run_workbook(store, tmp_path, bad)
    assert matches(got, _table_expected(WORKBOOK, bad)), got


PARQUET: dict[str, tuple[tuple[Bad, ...], tuple[int, ...] | None, tuple[str, str] | None]] = {
    "name.bytes": (
        BYTES,
        (2, 0),
        (UNPARSEABLE, "Not a Parquet file that can be read: a column's or a field's name"),
    ),
    "field.bytes": (
        BYTES,
        (2, 0),
        (UNPARSEABLE, "Not a Parquet file that can be read: a column's or a field's name"),
    ),
    "field.text": (UTF8, (2, 1), ("UNSUPPORTED_FORMAT", "A Parquet column's type is not read")),
    "name.text": (UTF8, (3, 0, 4), (UNPARSEABLE, "A column's name")),
}


def _run_parquet(store: Store, root: Path, bad: dict[str, Bad]) -> Outcome:
    columns: dict[str, pa.Array] = {}
    columns["c" + (bad["name.text"].text if "name.text" in bad else "")] = pa.array(["v"])
    if "name.bytes" in bad:
        columns["QQQQ"] = pa.array(["v"])
    if "field.bytes" in bad or "field.text" in bad:
        inner = "QQQQ" if "field.bytes" in bad else "f" + bad["field.text"].text
        columns["s"] = pa.array([{inner: 1}])
    content = _parquet(pa.table(columns))
    renaming = [bad[slot] for slot in ("name.bytes", "field.bytes") if slot in bad]
    if renaming:
        assert b"QQQQ" in content
        content = _renamed(content, renaming[0])
    path = root / "p.parquet"
    path.write_bytes(content)
    confinement = Confinement.of(root)
    return outcome(lambda: _import(store, confinement.confine(path), confinement))


_PARQUET_SINGLES = [(slot, bad) for slot, spec in PARQUET.items() for bad in spec[0]]
_PARQUET_PAIRS = [
    pair
    for pair in itertools.combinations(PARQUET, 2)
    if set(pair) != {"field.bytes", "field.text"}
]


@pytest.mark.parametrize(("slot", "bad"), _PARQUET_SINGLES, ids=map(_id, _PARQUET_SINGLES))
def test_parquet_alone(store: Store, tmp_path: Path, slot: str, bad: Bad) -> None:
    got = _run_parquet(store, tmp_path, {slot: bad})
    assert matches(got, _table_expected(PARQUET, {slot: bad})), got


@pytest.mark.parametrize("pair", _PARQUET_PAIRS, ids="+".join)
def test_parquet_pair(store: Store, tmp_path: Path, pair: tuple[str, str]) -> None:
    bad = {slot: PARQUET[slot][0][0] for slot in pair}
    got = _run_parquet(store, tmp_path, bad)
    assert matches(got, _table_expected(PARQUET, bad)), got


def test_parquet_twin(store: Store, tmp_path: Path) -> None:
    assert _run_parquet(store, tmp_path, {"name.text": TEXT}) is None


# --- A bad header at any position among a file's columns ----------------------------------------


def _headed(kind: str, headers: list[str]) -> bytes:
    values = ["v"] * len(headers)
    if kind == "csv":
        return (",".join(headers) + "\n" + ",".join(values) + "\n").encode()
    if kind == "xlsx":
        return build_xlsx([("s", [headers, values])])
    return _parquet(pa.table({header: ["v"] for header in headers}))


_HEADER_POSITIONS = {
    "first": lambda bad: [bad, "a", "b"],
    "middle": lambda bad: ["a", bad, "b"],
    "last": lambda bad: ["a", "b", bad],
    "two": lambda bad: [bad, "a", bad + "x"],
}


@pytest.mark.parametrize("bad", XML, ids=repr)
@pytest.mark.parametrize("position", _HEADER_POSITIONS)
@pytest.mark.parametrize("kind", ["csv", "xlsx", "parquet"])
def test_a_bad_header_wherever_it_sits(
    store: Store, tmp_path: Path, kind: str, position: str, bad: Bad
) -> None:
    """Every column's name is checked, not the first's alone."""
    headers = _HEADER_POSITIONS[position]("h" + bad.text)
    path = tmp_path / f"p.{kind}"
    path.write_bytes(_headed(kind, headers))
    confinement = Confinement.of(tmp_path)
    got = outcome(lambda: _import(store, confinement.confine(path), confinement))
    assert matches(got, (UNPARSEABLE, "A column's name")), got


# --- A database, through resolve ------------------------------------------------------------------
#
# PRECEDENCE["database"]: 0 resolve (the file's location, then the import directory's real path);
# 1 reading, which refuses the table limit (counting every table listed, a bad name among them)
# before any name (test_nonunicode_targeted.py: ``test_a_database_with_too_many_tables...``);
# 2 the snapshot's names, per table (its name, its comment, then each column and its comment);
# 3 describe (the dataset's name, the location, ...). A view or other relation is skipped and noted.

# The bytes a kind of database can carry in a name, and what each gives (SPEC D398's table): SQLite
# stores a name as the bytes its client wrote, so its tables', columns', views' and foreign keys'
# names also come in bytes that are not UTF-8 (BYTES), which no Python client can write and which
# the cases below make by editing the catalogue (``_patched``); DuckDB validates a name as UTF-8,
# so it carries none (``DUCKDB`` has no BYTES); a Postgres database in SQL_ASCII carries them and
# is refused whole, a residual that tests/core/importers/test_databases.py pins behind the
# ``databases`` marker; MySQL is not tested. A foreign key's parent is only read: the key is
# dropped, with a note on the child's columns that names no parent.

DATABASE: dict[str, tuple[tuple[Bad, ...], tuple[int, ...] | None, tuple[str, str] | None]] = {
    "directory": (DISK, (0, 0), (UNPARSEABLE, "The source's location")),
    "file": (DISK, (0, 0), (UNPARSEABLE, "The source's location")),
    "view": ((*UTF8, *BYTES), None, None),
    "table": ((*UTF8, *BYTES), (2, 0), (UNPARSEABLE, "A table's name")),
    "column": ((*UTF8, *BYTES), (2, 2), (UNPARSEABLE, "A column's name")),
    "foreign.key": ((*UTF8, *BYTES), None, None),
    "connection": (LIBRARY, (3, 0), (UNPARSEABLE, "The dataset's name")),
    "table.long": ((LONG,), (2, 0), ("LIMIT_EXCEEDED", "A table's name has more than")),
}
DUCKDB: dict[str, tuple[tuple[Bad, ...], tuple[int, ...] | None, tuple[str, str] | None]] = {
    **{
        key: (tuple(bad for bad in DATABASE[key][0] if bad not in BYTES), *DATABASE[key][1:])
        for key in ("file", "view", "table", "column", "connection")
    },
    "table.comment": (UTF8, (2, 1), (UNPARSEABLE, "The comment of the table ")),
    "column.comment": (UTF8, (2, 3), (UNPARSEABLE, "The comment of the column ")),
    "schema": (UTF8, (3, 1), (UNPARSEABLE, "The source's location")),
}


_STAND_IN = {"table": "TQQ", "column": "CQQ", "view": "VQQ", "foreign.key": "PQQ"}
"""What a name in bytes that are not UTF-8 is written as, until ``_patched`` replaces it."""


def _patched(path: bytes, names: dict[str, str]) -> None:
    """Replaces each stand-in in ``path``'s catalogue by the bytes of its name: a surrogate the
    file system's way (U+DC80-DCFF is the byte). Written as SQLite's C API would, since Python's
    ``sqlite3`` encodes every statement as UTF-8."""
    connection = sqlite3.connect(path)
    connection.text_factory = bytes
    connection.execute("PRAGMA writable_schema = ON")
    rows = connection.execute("SELECT rowid, name, tbl_name, sql FROM sqlite_schema").fetchall()
    for rowid, *fields in rows:
        for stand_in, name in names.items():
            raw = name.encode("utf-8", "surrogateescape")
            fields = [
                None if field_ is None else field_.replace(_STAND_IN[stand_in].encode(), raw)
                for field_ in fields
            ]
        connection.execute(
            "UPDATE sqlite_schema SET name = CAST(? AS TEXT), tbl_name = CAST(? AS TEXT), "
            "sql = CAST(? AS TEXT) WHERE rowid = ?",
            [*fields, rowid],
        )
    connection.commit()
    connection.close()


Shape = tuple[tuple[tuple[str, bool], ...], tuple[str, ...]]
"""``t``'s columns, each a name and whether it takes the ``column`` slot's name, and the text-named
tables beside ``t``, which sort before it or after it."""
ALONE: Shape = ((("c", True),), ())
SHAPES: dict[str, Shape] = {
    "second": ((("a", False), ("c", True)), ()),
    "middle": ((("a", False), ("c", True), ("z", False)), ()),
    "last": ((("a", False), ("z", False), ("c", True)), ()),
    "two": ((("c", True), ("a", False), ("d", True)), ()),
    "table.last": (ALONE[0], ("a",)),
    "table.first": (ALONE[0], ("z",)),
    "table.middle": (ALONE[0], ("a", "z")),
}
"""Where the bad name sits among the others (``_sqlite``, ``_duckdb``): a name that is not the
first of a table's columns or of the file's tables, and two columns that are bad."""


def _sqlite(path: bytes, names: dict[str, str], shape: Shape = ALONE) -> None:
    """A table ``t`` with the columns of ``shape``, a view or a table with a foreign key when
    ``names`` has them, and ``shape``'s other tables, each name as it gives it: its stand-in where
    it is not encodable."""
    broken: dict[str, str] = {}

    def name(slot: str, good: str) -> str:
        if slot not in names:
            return good
        try:
            names[slot].encode("utf-8")
        except UnicodeEncodeError:
            broken[slot] = good[0] + names[slot]
            return good[0] + _STAND_IN[slot]
        return good[0] + names[slot]

    table = name("table", "t")
    columns = [name("column", given) if bad else given for given, bad in shape[0]]
    with sqlite3.connect(path) as connection:
        declared = ", ".join(f'"{c}" TEXT' for c in columns)
        connection.execute(f'CREATE TABLE "{table}" ({declared})')
        marks = ", ".join("?" for _ in columns)
        connection.execute(f'INSERT INTO "{table}" VALUES ({marks})', ["v"] * len(columns))
        for other in shape[1]:
            connection.execute(f'CREATE TABLE "{other}" (c TEXT)')
        if "view" in names:
            connection.execute(f'CREATE VIEW "{name("view", "v")}" AS SELECT 1')
        if "foreign.key" in names:
            parent = name("foreign.key", "p")
            connection.execute(f'CREATE TABLE k (pid INTEGER REFERENCES "{parent}" (id))')
    connection.close()
    if broken:
        _patched(path, {slot: broken[slot][1:] for slot in broken})


def _duckdb(path: bytes, bad: dict[str, Bad], shape: Shape = ALONE) -> str:
    def name(slot: str, good: str) -> str:
        return good + (bad[slot].text if slot in bad else "")

    schema = name("schema", "main") if "schema" in bad else "main"
    table = name("table", "t")
    columns = [name("column", given) if is_bad else given for given, is_bad in shape[0]]
    column = columns[0]
    made = os.path.join(os.path.dirname(path), b"made.duckdb")
    with duckdb.connect(os.fsdecode(made)) as connection:
        if schema != "main":
            connection.execute(f'CREATE SCHEMA "{schema}"')
        declared = ", ".join(f'"{c}" VARCHAR' for c in columns)
        connection.execute(f'CREATE TABLE "{schema}"."{table}" ({declared})')
        values = ", ".join("'v'" for _ in columns)
        connection.execute(f'INSERT INTO "{schema}"."{table}" VALUES ({values})')
        for other in shape[1]:
            connection.execute(f'CREATE TABLE "{schema}"."{other}" (c VARCHAR)')
        if "view" in bad:
            connection.execute(f'CREATE VIEW "{schema}"."{name("view", "v")}" AS SELECT 1')
        if "table.comment" in bad:
            comment = name("table.comment", "a table")
            connection.execute(f'COMMENT ON TABLE "{schema}"."{table}" IS \'{comment}\'')
        if "column.comment" in bad:
            comment = name("column.comment", "a column")
            connection.execute(
                f'COMMENT ON COLUMN "{schema}"."{table}"."{column}" IS \'{comment}\''
            )
    os.rename(made, path)
    return schema


def _run_database(
    store: Store, root: Path, bad: dict[str, Bad], kind: Kind = "sqlite", shape: Shape = ALONE
) -> Outcome:
    imports = root / "imports"
    imports.mkdir()
    folder = os.fsencode(imports)
    if "directory" in bad:
        folder = os.path.join(folder, b"s" + _disk(bad["directory"]))
        os.mkdir(folder)
    stem = b"db" + (_disk(bad["file"]) if "file" in bad else b"")
    path = os.path.join(folder, stem + (b".sqlite" if kind == "sqlite" else b".duckdb"))
    schema = None
    if kind == "sqlite":
        names = {slot: found.text for slot, found in bad.items() if slot in _STAND_IN}
        if "table.long" in bad:
            names["table"] = bad["table.long"].text[1:]
        _sqlite(path, names, shape)
    else:
        schema = _duckdb(path, bad, shape)
    name = "c" + (bad["connection"].text if "connection" in bad else "")
    confinement = Confinement.of(imports)

    def run() -> object:
        connection = Connection(name, kind, Path(os.fsdecode(path)), schema=schema)
        return _import(store, resolve(connection, confinement, {}), confinement)

    expected = _table_expected(DATABASE if kind == "sqlite" else DUCKDB, bad)
    viewed = bad.get("view", TEXT)
    return outcome(run, [viewed.text] if expected is None and viewed is not TEXT else [])


_DATABASE_APART = [{"directory", "file"}, {"table", "table.long"}]


@pytest.mark.parametrize(
    ("slot", "bad"),
    [(slot, bad) for slot, spec in DATABASE.items() for bad in (*spec[0], TEXT)],
    ids=[_id((slot, bad)) for slot, spec in DATABASE.items() for bad in (*spec[0], TEXT)],
)
def test_sqlite_alone(store: Store, tmp_path: Path, slot: str, bad: Bad) -> None:
    if bad is TEXT and slot == "table.long":
        pytest.skip("the twin of a long name is any name the other tests import")
    got = _run_database(store, tmp_path, {slot: bad})
    assert matches(got, _table_expected(DATABASE, {slot: bad})), got


_SQLITE_PAIRS = [
    pair
    for pair in itertools.combinations(DATABASE, 2)
    if not any(set(pair) <= together for together in _DATABASE_APART)
]


@pytest.mark.parametrize("pair", _SQLITE_PAIRS, ids="+".join)
def test_sqlite_pair(store: Store, tmp_path: Path, pair: tuple[str, str]) -> None:
    bad = {slot: DATABASE[slot][0][0] for slot in pair}
    got = _run_database(store, tmp_path, bad)
    assert matches(got, _table_expected(DATABASE, bad)), got


_BYTES_SLOTS = [slot for slot, spec in DATABASE.items() if BYTES[0] in spec[0]]


@pytest.mark.parametrize("pair", list(itertools.combinations(_BYTES_SLOTS, 2)), ids="+".join)
def test_sqlite_pair_in_bytes(store: Store, tmp_path: Path, pair: tuple[str, str]) -> None:
    bad = dict.fromkeys(pair, BYTES[0])
    got = _run_database(store, tmp_path, bad)
    assert matches(got, _table_expected(DATABASE, bad)), got


_PLACED = [
    (slot, shape, bad)
    for slot in ("table", "column")
    for shape in SHAPES
    for bad in (*BYTES, *UTF8[:1], TEXT)
    if slot == "column" or shape.startswith("table.")
]
"""The slots a shape moves: a column in a table's columns, a table among the file's tables."""
_PLACED_IDS = [f"{slot}-{shape}-{bad!r}" for slot, shape, bad in _PLACED]


@pytest.mark.parametrize(("slot", "shape", "bad"), _PLACED, ids=_PLACED_IDS)
def test_sqlite_a_bad_name_wherever_it_sits(
    store: Store, tmp_path: Path, slot: str, shape: str, bad: Bad
) -> None:
    """Each bad column is checked, not only a table's first (the table's own query is built from
    them all), and a bad table is checked among good ones: the outcome is the lone slot's."""
    got = _run_database(store, tmp_path, {slot: bad}, "sqlite", SHAPES[shape])
    assert matches(got, _table_expected(DATABASE, {slot: bad})), got


_PLACED_IN_DUCKDB = [case for case in _PLACED if case[2] not in BYTES]
"""DuckDB validates a name as UTF-8: it carries no byte that is not."""


@pytest.mark.parametrize(
    ("slot", "shape", "bad"),
    _PLACED_IN_DUCKDB,
    ids=[f"{slot}-{shape}-{bad!r}" for slot, shape, bad in _PLACED_IN_DUCKDB],
)
def test_duckdb_a_bad_name_wherever_it_sits(
    store: Store, tmp_path: Path, slot: str, shape: str, bad: Bad
) -> None:
    got = _run_database(store, tmp_path, {slot: bad}, "duckdb", SHAPES[shape])
    assert matches(got, _table_expected(DUCKDB, {slot: bad})), got


@pytest.mark.parametrize(
    ("slot", "bad"),
    [(slot, bad) for slot, spec in DUCKDB.items() for bad in (spec[0][0], TEXT)],
    ids=[_id((slot, bad)) for slot, spec in DUCKDB.items() for bad in (spec[0][0], TEXT)],
)
def test_duckdb_alone(store: Store, tmp_path: Path, slot: str, bad: Bad) -> None:
    got = _run_database(store, tmp_path, {slot: bad}, "duckdb")
    assert matches(got, _table_expected(DUCKDB, {slot: bad})), got


@pytest.mark.parametrize("pair", list(itertools.combinations(DUCKDB, 2)), ids="+".join)
def test_duckdb_pair(store: Store, tmp_path: Path, pair: tuple[str, str]) -> None:
    bad = {slot: DUCKDB[slot][0][0] for slot in pair}
    got = _run_database(store, tmp_path, bad, "duckdb")
    assert matches(got, _table_expected(DUCKDB, bad)), got


@pytest.mark.parametrize("bad", UTF8, ids=repr)
def test_a_url_s_database_that_is_not_text(bad: Bad) -> None:
    encoded = "".join(f"%{byte:02X}" for byte in bad.text.encode())
    servers: tuple[tuple[Kind, str], ...] = (("postgres", "postgresql"), ("mysql", "mysql"))
    for kind, scheme in servers:
        connection = Connection("c", kind, None, "U")
        environ = {"U": f"{scheme}://u@h/db{encoded}"}
        got = outcome(partial(resolve, connection, Confinement.of(Path("/")), environ))
        assert got is not None
        assert got[0] == "INVALID_VALUE"
        assert "holds a URL whose database, decoded, is not Unicode text" in got[1]


# --- The upload area --------------------------------------------------------------------------
#
# PRECEDENCE["upload"]: the extension, then the dataset's id.

UPLOAD = {
    "extension": ((*LIBRARY,), 0, ("UNSUPPORTED_FORMAT", "Files of this kind are not imported")),
    "dataset": ((*LIBRARY,), 1, ("INVALID_VALUE", "A dataset id is an identifier")),
}


def _put(root: Path, bad: dict[str, Bad]) -> Outcome:
    area = UploadArea(root / "data")
    dataset = "d" + (bad["dataset"].text if "dataset" in bad else "")
    extension = "cs" + bad["extension"].text if "extension" in bad else "csv"
    return outcome(lambda: area.put(dataset, [CSV], extension, limit=1000))


@pytest.mark.parametrize(
    ("slot", "bad"),
    [(slot, bad) for slot, spec in UPLOAD.items() for bad in spec[0]],
    ids=[_id((slot, bad)) for slot, spec in UPLOAD.items() for bad in spec[0]],
)
def test_upload_alone(tmp_path: Path, slot: str, bad: Bad) -> None:
    assert matches(_put(tmp_path, {slot: bad}), UPLOAD[slot][2])


def test_upload_pair(tmp_path: Path) -> None:
    bad = {"extension": LIBRARY[0], "dataset": LIBRARY[0]}
    assert matches(_put(tmp_path, bad), UPLOAD["extension"][2])


def test_upload_twin(tmp_path: Path) -> None:
    assert _put(tmp_path, {"extension": TEXT}) is not None  # "csz" is not a kind read
    assert _put(tmp_path, {"dataset": TEXT}) is None

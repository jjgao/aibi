"""Typed tables (SPEC §12.2, D218): built from raw snapshots, stored as Parquet with state
companions, read back as the evaluator's tables, and the same bytes every time."""

import os
import subprocess
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest

from aibi.core.engine import build
from aibi.core.engine.data import EMPTY, PRESENT, Cell
from aibi.core.schema.descriptors import ColumnDescriptor, ColumnFields
from aibi.core.schema.semantics import ObservationState
from aibi.core.store import parquet, tables
from aibi.core.store.sources import Parsed
from aibi.core.store.store import Store

UNKNOWN = Cell(ObservationState.UNKNOWN)
NOT_APPLICABLE = Cell(ObservationState.NOT_APPLICABLE)


def present(value: object) -> Cell:
    return Cell(PRESENT, value)  # type: ignore[arg-type]


def listed(*items: object) -> Cell:
    return present(tuple(present(item) if item is not None else UNKNOWN for item in items))


def _fields(column: str, datatype: str, **given: Any) -> ColumnFields:
    descriptor = build.column(column, datatype, **given)
    assert isinstance(descriptor, ColumnDescriptor)
    return descriptor.fields


def _column(store: Store, manifest: str, table: str) -> dict[str, parquet.Column]:
    entry = store.manifest(manifest).table(table)
    assert entry is not None
    return {column.name: column for column in parquet.read(store.blobs.path(entry.hash))}


def test_a_release_reads_back_with_every_state_and_item(store: Store, imported: str) -> None:
    release = store.load(imported)
    members = release.rows("members")
    column = lambda name: [members.cell(row, name) for row in range(4)]  # noqa: E731
    assert column("member_id") == [present(key) for key in ("m-1", "m-17", "m-3", "m-4")]
    assert column("joined") == [
        present(date(2020, 1, 2)),
        present(date(2021, 3, 4)),
        present(date(2019, 11, 30)),
        UNKNOWN,  # "not yet" does not parse
    ]
    assert column("age") == [present(36), UNKNOWN, NOT_APPLICABLE, UNKNOWN]
    assert column("interests") == [
        listed("poetry", "maths"),
        UNKNOWN,
        listed("maths", None),
        UNKNOWN,
    ]
    assert column("age_months") == [present(432.0), UNKNOWN, NOT_APPLICABLE, UNKNOWN]
    loans = release.rows("loans")
    assert [loans.cell(row, "loan_id") for row in range(4)] == [present(n) for n in (1, 2, 3, 4)]
    assert [loans.cell(row, "borrowed") for row in range(4)] == [
        present(datetime(2024, 1, 2, 10, tzinfo=UTC)),  # no offset: UTC
        present(datetime(2024, 2, 3, 11, 30, tzinfo=UTC)),
        EMPTY,
        present(datetime(2024, 3, 4, 3, 6, 7, tzinfo=UTC)),
    ]
    assert [loans.cell(row, "overdue") for row in range(4)] == [
        present(6.0),
        present(-11.0),
        UNKNOWN,
        UNKNOWN,  # NaN days is no value
    ]


def test_companions_exist_exactly_where_a_cell_or_item_is_not_present(
    store: Store, imported: str
) -> None:
    books = _column(store, imported, "books")
    assert list(books) == ["book_id", "title", "genre"]
    members = _column(store, imported, "members")
    assert list(members) == [
        "member_id",
        "name",
        "joined",
        "joined__state",
        "age",
        "age__state",  # declares missing codes
        "interests",
        "interests__state",
        "interests__item_state",
        "age_months",
        "age_months__state",
    ]
    types = {name: column.type for name, column in members.items()}
    assert types["age"] == "int64"
    assert types["joined"] == "date32"
    assert types["interests"] == "strings"
    assert types["age_months"] == "float64"
    assert members["age"].values == [36, None, None, None]
    assert members["age__state"].values == ["PRESENT", "UNKNOWN", "NOT_APPLICABLE", "UNKNOWN"]
    assert members["interests__item_state"].values[2] == ["PRESENT", "UNKNOWN"]
    loans = _column(store, imported, "loans")
    assert loans["borrowed"].type == "timestamp"
    assert "loan_id__state" not in loans


def test_a_declared_missing_code_gives_companions_though_every_cell_is_present() -> None:
    """The companions follow the descriptor as well as the data, so a table whose cells happen to
    be PRESENT has the columns its codes promise (D218)."""
    columns = {
        "n": _fields("t.n", "integer", missing_codes={"NA": "UNKNOWN"}),
        "tags": _fields("t.tags", "list<category>", missing_codes={"NA": "UNKNOWN"}),
        "plain": _fields("t.plain", "string"),
    }
    typed = tables.build_table(
        "t", Parsed(("n", "tags", "plain"), [("1", "a;b", "x")]), ["n", "tags", "plain"], columns
    )
    stored = {column.name: column for column in parquet.read(tables.encode(typed, columns))}
    assert list(stored) == ["n", "n__state", "tags", "tags__state", "tags__item_state", "plain"]
    assert stored["n__state"].values == ["PRESENT"]
    assert stored["tags__item_state"].values == [["PRESENT", "PRESENT"]]


def test_a_corrupt_blob_is_refused_when_read(tmp_path: Path) -> None:
    present_null = parquet.write(
        [
            parquet.Column("x", "int64", [None]),
            parquet.Column("x__state", "string", ["PRESENT"]),
        ]
    )
    with pytest.raises(tables.CorruptTableError):
        tables.decode("t", present_null)
    missing_with_value = parquet.write(
        [parquet.Column("x", "int64", [1]), parquet.Column("x__state", "string", ["UNKNOWN"])]
    )
    with pytest.raises(tables.CorruptTableError):
        tables.decode("t", missing_with_value)


_HASH_SCRIPT = """
import sys, tempfile
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import conftest
from aibi.core.store.store import Store
store = Store(Path(tempfile.mkdtemp()) / "data")
with store.pin() as pin:
    built = store.import_release(pin, "lib", conftest.library_descriptors(),
                                 conftest.library_sources(), conftest.LAYOUTS)
print(built.manifest.hash)
"""


def test_the_same_inputs_give_the_same_release_in_another_process(
    store: Store, imported: str
) -> None:
    """Every blob is a function of the raw snapshots and the descriptors, so the manifest hash
    is too, whatever the process, its hash seed or its clock."""
    here = str(Path(__file__).parent)
    hashes = {
        subprocess.run(
            [sys.executable, "-c", _HASH_SCRIPT, here],
            capture_output=True,
            text=True,
            check=True,
            env={**os.environ, "PYTHONHASHSEED": seed},
        ).stdout.strip()
        for seed in ("0", "1")
    }
    assert hashes == {imported}

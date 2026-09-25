"""Database snapshots (SPEC §13.1, §14, D305–D307): SQLite and DuckDB files, and Postgres and
MySQL servers behind the ``databases`` marker, which run when ``AIBI_TEST_POSTGRES_URL`` or
``AIBI_TEST_MYSQL_URL`` names a database the tests may fill and empty.

The datasets are a lending library's: authors, books and loans. DuckDB runs only in fresh
interpreters (``in_duckdb``), never in the tests' own process, as it never runs in the server's."""

import hashlib
import json
import os
import secrets
import sqlite3
import subprocess
import sys
import threading
import urllib.parse
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from aibi.core.importers.databases import Connection, Resolved, resolve
from aibi.core.importers.errors import ImportRefused
from aibi.core.importers.run import Published, build_import, import_dataset, reimport_dataset
from aibi.core.importers.snapshot import DATABASE_TYPES, SQLITE_VALUES, Kind
from aibi.core.schema.descriptors import (
    ColumnDescriptor,
    DatasetDescriptor,
    Descriptor,
    RelationshipDescriptor,
    TableDescriptor,
)
from aibi.core.schema.limits import MAX_STRING, MAX_TEXT, ImportLimits
from aibi.core.schema.output import DataSegment
from aibi.core.schema.refusals import Refusal, RefusalCode
from aibi.core.store.sources import TypedSource
from aibi.core.store.store import Store

LIBRARY = """
CREATE TABLE authors (id INTEGER PRIMARY KEY, name TEXT NOT NULL);
CREATE TABLE books (
    code TEXT PRIMARY KEY, author_id INTEGER REFERENCES authors (id), title TEXT, year INTEGER
);
CREATE TABLE shelves (room INTEGER, shelf INTEGER, label TEXT, PRIMARY KEY (room, shelf));
CREATE TABLE placements (code TEXT, room INTEGER, shelf INTEGER,
    FOREIGN KEY (room, shelf) REFERENCES shelves);
CREATE VIEW recent AS SELECT * FROM books WHERE year > 2000;
CREATE TRIGGER stamp AFTER INSERT ON books BEGIN UPDATE authors SET name = 'x'; END;
INSERT INTO authors VALUES (2, 'Brook'), (1, 'Ash'), (3, 'Cole');
INSERT INTO books VALUES ('b2', 1, 'Second', 2004), ('b1', 1, 'First', 1999),
    ('b3', 2, 'Third', 'NA');
INSERT INTO shelves VALUES (1, 1, 'Fiction'), (1, 2, 'Poetry');
INSERT INTO placements VALUES ('b1', 1, 1), ('b2', 1, 2), ('b3', 1, 2);
"""


def sqlite_file(directory: Path, script: str = LIBRARY, name: str = "library.sqlite") -> Path:
    path = directory / name
    connection = sqlite3.connect(path)
    connection.executescript(script)
    connection.commit()
    connection.close()
    return path


def in_duckdb(code: str, given: object) -> str:
    """What ``code`` prints, run in a fresh interpreter with ``duckdb`` imported and the JSON of
    ``given`` as ``given``."""
    found = subprocess.run(
        [sys.executable, "-c", f"import duckdb, json, sys\ngiven = json.load(sys.stdin)\n{code}"],
        input=json.dumps(given),
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
    )
    return found.stdout


def duckdb_file(directory: Path, script: str, name: str = "library.duckdb") -> Path:
    path = directory / name
    code = "c = duckdb.connect(given['path'])\nc.execute(given['script'])\nc.close()"
    in_duckdb(code, {"path": str(path), "script": script})
    return path


DUCKDB_LIBRARY = """
CREATE TABLE authors (id INTEGER PRIMARY KEY, name VARCHAR, born DATE);
COMMENT ON TABLE authors IS 'People who wrote a book of the library';
COMMENT ON COLUMN authors.name IS 'As printed on the cover';
CREATE TABLE books (code VARCHAR PRIMARY KEY, author_id INTEGER REFERENCES authors (id),
    price DECIMAL(6, 2), added TIMESTAMPTZ, tags VARCHAR[]);
CREATE VIEW recent AS SELECT * FROM books;
INSERT INTO authors VALUES (1, 'Ash', DATE '1970-01-02'), (2, 'Brook', NULL);
INSERT INTO books VALUES ('b1', 1, 9.50, TIMESTAMPTZ '2024-05-01 10:00:00+02', ['new']),
    ('b2', 2, NULL, NULL, NULL);
"""


def by_id(descriptors: tuple[Descriptor, ...]) -> dict[str, Descriptor]:
    return {descriptor.id: descriptor for descriptor in descriptors}


def status(descriptor: Descriptor, pointer: str) -> str:
    return descriptor.curation[pointer].status


def refusal_of(error: pytest.ExceptionInfo[ImportRefused]) -> Refusal:
    [refusal] = error.value.refusals
    return refusal


def shown(refusal: Refusal) -> str:
    return json.dumps(refusal.model_dump(mode="json"))


def published(store: Store, found: Published) -> dict[str, Descriptor]:
    return by_id(store.descriptors(found.manifest))


@pytest.fixture
def connect(roots: Any) -> Any:
    def make(kind: Kind, path: Path | None = None, **given: Any) -> Resolved:
        environ = given.pop("environ", {})
        connection = Connection("library", kind, path, **given)
        return resolve(connection, roots.confinement, environ)

    return make


# --- SQLite ------------------------------------------------------------------------------------


def test_a_sqlite_file_is_imported_with_the_keys_it_declares_as_imported(
    roots: Any, store: Store, connect: Any
) -> None:
    source = connect("sqlite", sqlite_file(roots.inside))
    found = published(store, import_dataset(store, source, roots.options("library"), "operator:a"))
    authors, books, placements = found["authors"], found["books"], found["placements"]
    assert isinstance(authors, TableDescriptor)
    assert isinstance(placements, TableDescriptor)
    assert authors.fields.primary_key == ["id"]
    assert status(authors, "/fields/primary_key") == "imported"
    assert status(found["shelves"], "/fields/primary_key") == "imported"
    assert status(books, "/fields/grain") == "proposed"
    assert placements.fields.primary_key is None or status(placements, "/fields/primary_key") == (
        "proposed"
    )
    written = found["rel:books.author_id"]
    assert isinstance(written, RelationshipDescriptor)
    assert (written.fields.parent_table, written.fields.parent_columns) == ("authors", ["id"])
    for field in ("child_table", "child_columns", "parent_table", "parent_columns"):
        assert status(written, f"/fields/{field}") == "imported"
    assert status(written, "/fields/cardinality") == "proposed"
    composite = found["rel:placements.room+shelf"]
    assert isinstance(composite, RelationshipDescriptor)
    assert composite.fields.parent_columns == ["room", "shelf"]
    year = found["books.year"]
    assert isinstance(year, ColumnDescriptor)
    assert (year.fields.datatype, status(year, "/fields/datatype")) == ("integer", "proposed")


def test_a_view_in_a_sqlite_file_is_not_imported_and_is_noted(
    roots: Any, store: Store, connect: Any
) -> None:
    source = connect("sqlite", sqlite_file(roots.inside))
    done = import_dataset(store, source, roots.options("library"), "operator:a")
    assert "recent" not in published(store, done)
    [note] = [note for note in done.notes if note.kind == "skipped_source"]
    assert [part for part in note.message if isinstance(part, DataSegment)] == [
        DataSegment(data="recent")
    ]


def test_reading_a_sqlite_file_changes_nothing_in_it_and_runs_no_trigger(
    roots: Any, store: Store, connect: Any
) -> None:
    path = sqlite_file(roots.inside)
    before = hashlib.sha256(path.read_bytes()).hexdigest(), os.stat(path).st_mtime_ns
    import_dataset(store, connect("sqlite", path), roots.options("library"), "operator:a")
    assert (hashlib.sha256(path.read_bytes()).hexdigest(), os.stat(path).st_mtime_ns) == before
    assert sorted(entry.name for entry in roots.inside.iterdir()) == ["library.sqlite"]


def test_sqlites_own_tables_are_not_read_and_virtual_tables_are_noted(
    roots: Any, store: Store, connect: Any
) -> None:
    arguments = {"fts5": "body", "fts4": "body", "rtree": "id, lo, hi"}
    probe = sqlite3.connect(":memory:")
    compiled = {row[0] for row in probe.execute("SELECT name FROM pragma_module_list")}
    probe.close()
    modules = [module for module in arguments if module in compiled]
    if not modules:
        pytest.skip("this SQLite has no virtual table module")
    script = f"""
    CREATE TABLE authors (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT);
    INSERT INTO authors (name) VALUES ('Ash'), ('Brook');
    CREATE VIRTUAL TABLE words USING {modules[0]}({arguments[modules[0]]});
    ANALYZE;
    """
    source = connect("sqlite", sqlite_file(roots.inside, script))
    done = import_dataset(store, source, roots.options("library"), "operator:a")
    tables = {d.id for d in published(store, done).values() if isinstance(d, TableDescriptor)}
    assert tables == {"authors"}
    noted = {
        part.data
        for note in done.notes
        if note.kind == "skipped_source"
        for part in note.message
        if isinstance(part, DataSegment)
    }
    assert "words" in noted
    assert not any(name.startswith("sqlite_") for name in noted)


def test_a_blob_in_a_sqlite_file_is_refused_listing_what_is_read(
    roots: Any, store: Store, connect: Any
) -> None:
    script = "CREATE TABLE covers (id INTEGER, image BLOB); INSERT INTO covers VALUES (1, x'00');"
    source = connect("sqlite", sqlite_file(roots.inside, script))
    with pytest.raises(ImportRefused) as refused:
        import_dataset(store, source, roots.options("library"), "operator:a")
    refusal = refusal_of(refused)
    assert refusal.code == RefusalCode.UNSUPPORTED_FORMAT
    assert [alternative.model_dump()["text"] for alternative in refusal.alternatives] == list(
        SQLITE_VALUES
    )


def test_a_declared_foreign_key_that_the_rows_break_refuses_the_import(
    roots: Any, store: Store, connect: Any
) -> None:
    script = LIBRARY + "INSERT INTO books VALUES ('b9', 9, 'Lost', 2001);"
    source = connect("sqlite", sqlite_file(roots.inside, script))
    with pytest.raises(ImportRefused) as refused:
        import_dataset(store, source, roots.options("library"), "operator:a")
    assert refusal_of(refused).code == RefusalCode.DANGLING_REFERENCE


def test_names_a_foreign_key_writes_in_another_case_are_matched(
    roots: Any, store: Store, connect: Any
) -> None:
    script = """
    CREATE TABLE Authors (Id INTEGER PRIMARY KEY);
    CREATE TABLE books (code TEXT PRIMARY KEY, author INTEGER REFERENCES AUTHORS (ID));
    INSERT INTO Authors VALUES (1); INSERT INTO books VALUES ('b1', 1);
    """
    source = connect("sqlite", sqlite_file(roots.inside, script))
    found = published(store, import_dataset(store, source, roots.options("library"), "operator:a"))
    written = found["rel:books.author"]
    assert isinstance(written, RelationshipDescriptor)
    assert (written.fields.parent_table, written.fields.parent_columns) == ("authors", ["id"])


def test_a_foreign_key_to_a_table_that_is_not_read_is_noted_and_not_kept(
    roots: Any, store: Store, connect: Any
) -> None:
    script = """
    CREATE TABLE books (code TEXT PRIMARY KEY, author INTEGER REFERENCES writers (id));
    INSERT INTO books VALUES ('b1', 1);
    """
    source = connect("sqlite", sqlite_file(roots.inside, script))
    done = import_dataset(store, source, roots.options("library"), "operator:a")
    assert not any(isinstance(d, RelationshipDescriptor) for d in published(store, done).values())
    [note] = [note for note in done.notes if note.subject == "books.author"]
    assert "its parent table is not read" in note.message[0].model_dump()["text"]


def test_a_table_is_read_in_the_order_of_its_key_or_else_of_all_its_columns(
    roots: Any, store: Store, connect: Any
) -> None:
    script = """
    CREATE TABLE authors (id INTEGER PRIMARY KEY, name TEXT);
    INSERT INTO authors VALUES (3, 'c'), (1, 'a'), (2, 'b');
    CREATE TABLE notes (author INTEGER, body TEXT);
    INSERT INTO notes VALUES (2, 'y'), (1, 'z'), (2, 'x');
    """
    source = connect("sqlite", sqlite_file(roots.inside, script))
    with store.pin() as pin:
        imported = build_import(store, pin, source, roots.options("library"))
    authors, notes = imported.result.sources["authors"], imported.result.sources["notes"]
    assert isinstance(authors, TypedSource)
    assert isinstance(notes, TypedSource)
    assert authors.rows == ((1, "a"), (2, "b"), (3, "c"))
    assert notes.rows == ((1, "z"), (2, "x"), (2, "y"))


def test_a_table_is_read_in_the_order_of_its_key_wherever_the_key_is(
    roots: Any, store: Store, connect: Any
) -> None:
    script = """
    CREATE TABLE authors (name TEXT, id INTEGER PRIMARY KEY);
    INSERT INTO authors VALUES ('a', 3), ('c', 1), ('b', 2);
    """
    source = connect("sqlite", sqlite_file(roots.inside, script))
    with store.pin() as pin:
        imported = build_import(store, pin, source, roots.options("library"))
    authors = imported.result.sources["authors"]
    assert isinstance(authors, TypedSource)
    assert authors.rows == (("c", 1), ("b", 2), ("a", 3))


def test_a_table_named_as_sqlites_own_is_skipped_and_noted(
    roots: Any, store: Store, connect: Any
) -> None:
    path = roots.inside / "library.sqlite"
    with sqlite3.connect(path) as writer:
        writer.execute("CREATE TABLE authors (id INTEGER PRIMARY KEY AUTOINCREMENT)")
        writer.execute("INSERT INTO authors DEFAULT VALUES")
        writer.execute("PRAGMA writable_schema = ON")
        writer.execute("CREATE TABLE sqlite_planted (id INTEGER)")
    done = import_dataset(store, connect("sqlite", path), roots.options("library"), "operator:a")
    noted = {
        part.data
        for note in done.notes
        if note.kind == "skipped_source"
        for part in note.message
        if isinstance(part, DataSegment)
    }
    assert noted == {"sqlite_planted"}


def test_a_foreign_key_maps_its_columns_to_the_parent_columns_it_names_in_their_order(
    roots: Any, store: Store, connect: Any
) -> None:
    script = """
    CREATE TABLE shelves (room INTEGER, shelf INTEGER, PRIMARY KEY (room, shelf));
    CREATE TABLE books (code TEXT PRIMARY KEY, shelf INTEGER, room INTEGER,
        FOREIGN KEY (shelf, room) REFERENCES shelves (shelf, room));
    INSERT INTO shelves VALUES (1, 2); INSERT INTO books VALUES ('b1', 2, 1);
    """
    source = connect("sqlite", sqlite_file(roots.inside, script))
    found = published(store, import_dataset(store, source, roots.options("library"), "operator:a"))
    written = found["rel:books.shelf+room"]
    assert isinstance(written, RelationshipDescriptor)
    assert written.fields.child_columns == ["shelf", "room"]
    assert written.fields.parent_columns == ["shelf", "room"]


def test_a_foreign_key_to_a_column_that_is_not_its_parents_key_is_noted_and_not_kept(
    roots: Any, store: Store, connect: Any
) -> None:
    script = """
    CREATE TABLE authors (id INTEGER PRIMARY KEY, code TEXT UNIQUE);
    CREATE TABLE books (id INTEGER PRIMARY KEY, author TEXT REFERENCES authors (code));
    INSERT INTO authors VALUES (1, 'ash'), (2, 'brook');
    INSERT INTO books VALUES (10, 'ash'), (11, 'ash');
    """
    source = connect("sqlite", sqlite_file(roots.inside, script))
    done = import_dataset(store, source, roots.options("library"), "operator:a")
    assert not any(isinstance(d, RelationshipDescriptor) for d in published(store, done).values())
    [note] = [note for note in done.notes if note.subject == "books.author"]
    assert (
        "its parent columns are not its parent's primary key"
        in (note.message[0].model_dump()["text"])
    )


def test_a_foreign_key_whose_parent_two_tables_match_ignoring_case_is_noted_and_not_kept(
    roots: Any, store: Store, connect: Any
) -> None:
    script = """
    CREATE TABLE "Émile" (id INTEGER PRIMARY KEY);
    CREATE TABLE "émile" (id INTEGER PRIMARY KEY);
    CREATE TABLE books (code TEXT PRIMARY KEY, author INTEGER REFERENCES "ÉMILE" (id));
    INSERT INTO "Émile" VALUES (1); INSERT INTO "émile" VALUES (1);
    INSERT INTO books VALUES ('b1', 1);
    """
    source = connect("sqlite", sqlite_file(roots.inside, script))
    done = import_dataset(store, source, roots.options("library"), "operator:a")
    assert not any(isinstance(d, RelationshipDescriptor) for d in published(store, done).values())
    said = [note.message[0].model_dump()["text"] for note in done.notes]
    assert "A foreign key the database declares is not kept: its parent table is not read" in said


def test_a_second_foreign_key_on_the_same_columns_is_noted_and_not_kept(
    roots: Any, store: Store, connect: Any
) -> None:
    script = """
    CREATE TABLE authors (id INTEGER PRIMARY KEY);
    CREATE TABLE editors (id INTEGER PRIMARY KEY);
    CREATE TABLE books (code TEXT PRIMARY KEY, person INTEGER REFERENCES authors (id),
        FOREIGN KEY (person) REFERENCES editors (id));
    INSERT INTO authors VALUES (1); INSERT INTO editors VALUES (1);
    INSERT INTO books VALUES ('b1', 1);
    """
    source = connect("sqlite", sqlite_file(roots.inside, script))
    done = import_dataset(store, source, roots.options("library"), "operator:a")
    kept = [d for d in published(store, done).values() if isinstance(d, RelationshipDescriptor)]
    assert len(kept) == 1
    [note] = [note for note in done.notes if note.subject == "books.person"]
    assert (
        "another foreign key of the table has the same columns"
        in (note.message[0].model_dump()["text"])
    )


def test_a_foreign_key_with_fewer_columns_than_its_parents_key_is_noted_and_not_kept(
    roots: Any, store: Store, connect: Any
) -> None:
    script = """
    CREATE TABLE shelves (room INTEGER, shelf INTEGER, PRIMARY KEY (room, shelf));
    CREATE TABLE books (code TEXT PRIMARY KEY, room INTEGER, FOREIGN KEY (room) REFERENCES shelves);
    INSERT INTO shelves VALUES (1, 1); INSERT INTO books VALUES ('b1', 1);
    """
    source = connect("sqlite", sqlite_file(roots.inside, script))
    done = import_dataset(store, source, roots.options("library"), "operator:a")
    assert not any(isinstance(d, RelationshipDescriptor) for d in published(store, done).values())
    [note] = [note for note in done.notes if note.subject == "books.room"]
    assert "its columns do not match its parent's columns" in note.message[0].model_dump()["text"]


def test_a_declared_foreign_key_without_a_present_value_is_not_one_to_one(
    roots: Any, store: Store, connect: Any
) -> None:
    script = """
    CREATE TABLE authors (id INTEGER PRIMARY KEY);
    CREATE TABLE books (code TEXT PRIMARY KEY, author INTEGER REFERENCES authors (id));
    INSERT INTO authors VALUES (1); INSERT INTO books VALUES ('b1', NULL), ('b2', NULL);
    """
    source = connect("sqlite", sqlite_file(roots.inside, script))
    found = published(store, import_dataset(store, source, roots.options("library"), "operator:a"))
    written = found["rel:books.author"]
    assert isinstance(written, RelationshipDescriptor)
    assert written.fields.cardinality == "many-to-one"


def test_the_cells_of_every_table_may_reach_the_limit_but_not_pass_it(
    roots: Any, store: Store, connect: Any
) -> None:
    script = "CREATE TABLE authors (id INTEGER, name TEXT); INSERT INTO authors VALUES (1, 'a');"
    path = sqlite_file(roots.inside, script + "INSERT INTO authors VALUES (2, 'b'), (3, 'c');")
    at_limit = roots.options("library", limits=ImportLimits(import_cells=6))
    import_dataset(store, connect("sqlite", path), at_limit, "operator:a")
    over = roots.options("other", limits=ImportLimits(import_cells=5))
    with pytest.raises(ImportRefused) as refused:
        import_dataset(store, connect("sqlite", path), over, "operator:a")
    refusal = refusal_of(refused)
    assert refusal.limit is not None
    assert (refusal.limit.name, refusal.limit.max) == ("import_cells", 5)


def test_a_long_cell_is_named_by_its_row_in_the_table_after_the_first_batch(
    roots: Any, store: Store, connect: Any
) -> None:
    path = roots.inside / "library.sqlite"
    with sqlite3.connect(path) as writer:
        writer.execute("CREATE TABLE notes (id INTEGER PRIMARY KEY, body TEXT)")
        writer.executemany("INSERT INTO notes VALUES (?, 'short')", [(n,) for n in range(1, 5001)])
        writer.execute("UPDATE notes SET body = ? WHERE id = 4500", ("x" * 131_073,))
    with pytest.raises(ImportRefused) as refused:
        import_dataset(store, connect("sqlite", path), roots.options("library"), "operator:a")
    refusal = refusal_of(refused)
    assert refusal.code == RefusalCode.UNPARSEABLE_SOURCE
    assert "row 4500 of the table" in shown(refusal)


@pytest.mark.parametrize(
    ("script", "code", "limit"),
    [
        (f'CREATE TABLE "{"t" * (MAX_STRING + 1)}" (id INTEGER);', "LIMIT_EXCEEDED", "string"),
        ('CREATE TABLE authors ("name\ufffe" TEXT);', "UNPARSEABLE_SOURCE", None),
    ],
)
def test_a_name_over_a_strings_length_or_not_unicode_text_is_refused(
    roots: Any, store: Store, connect: Any, script: str, code: str, limit: str | None
) -> None:
    source = connect("sqlite", sqlite_file(roots.inside, script))
    with pytest.raises(ImportRefused) as refused:
        import_dataset(store, source, roots.options("library"), "operator:a")
    refusal = refusal_of(refused)
    assert refusal.code == code
    assert (refusal.limit.name if refusal.limit else None) == (
        None if limit is None else f"{limit}_characters"
    )


def test_the_sqlite_file_must_still_be_the_one_confined_when_the_snapshot_ends(
    roots: Any, store: Store, connect: Any
) -> None:
    path = roots.inside / "library.sqlite"
    with sqlite3.connect(path) as writer:
        writer.execute("CREATE TABLE notes (id INTEGER PRIMARY KEY, body TEXT)")
        writer.executemany("INSERT INTO notes VALUES (?, 'body')", [(n,) for n in range(400_000)])
    source = connect("sqlite", path)
    swapped = threading.Event()

    def swap() -> None:
        while not swapped.is_set():
            probe = sqlite3.connect(path, timeout=0, isolation_level=None)
            try:
                probe.execute("BEGIN EXCLUSIVE")
                probe.execute("ROLLBACK")
            except sqlite3.OperationalError:
                path.rename(roots.inside / "moved.sqlite")
                sqlite_file(roots.inside)
                swapped.set()
            finally:
                probe.close()

    swapping = threading.Thread(target=swap)
    swapping.start()
    try:
        with pytest.raises(ImportRefused) as refused:
            import_dataset(store, source, roots.options("library"), "operator:a")
    finally:
        swapped.set()
        swapping.join()
    assert refusal_of(refused).code == RefusalCode.PATH_NOT_CONFINED


_OPENED = """
import json, sys
from aibi.core.importers.errors import ImportRefused
from aibi.core.importers.snapshot import Target, _open_files, _opened_confined
path, identity, other = json.load(sys.stdin)
target = Target("sqlite", path=path, identity=tuple(identity))
before = _open_files()
with open(other, "rb"):
    try:
        _opened_confined(target, before)
    except ImportRefused as refused:
        print(refused.refusals[0].code)
"""


@pytest.mark.skipif(not os.path.isdir("/proc/self/fd"), reason="no /proc/self/fd here")
def test_a_reader_that_opened_a_file_other_than_the_one_confined_is_refused(roots: Any) -> None:
    path, other = sqlite_file(roots.inside), sqlite_file(roots.outside)
    identity = [os.stat(path).st_dev, os.stat(path).st_ino]
    found = subprocess.run(
        [sys.executable, "-c", _OPENED],
        input=json.dumps([str(path), identity, str(other)]),
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    assert found.stdout.strip() == "PATH_NOT_CONFINED"


@pytest.mark.parametrize("suffix", ["-wal", "-shm", "-journal"])
def test_a_link_beside_a_sqlite_file_is_refused(
    roots: Any, store: Store, connect: Any, suffix: str
) -> None:
    path = sqlite_file(roots.inside)
    (roots.inside / f"library.sqlite{suffix}").symlink_to(roots.outside / "elsewhere")
    with pytest.raises(ImportRefused) as refused:
        import_dataset(store, connect("sqlite", path), roots.options("library"), "operator:a")
    refusal = refusal_of(refused)
    assert refusal.code == RefusalCode.PATH_NOT_CONFINED
    assert DataSegment(data=f"{path}{suffix}") in refusal.message


def test_a_re_import_of_an_unchanged_database_changes_nothing(
    roots: Any, store: Store, connect: Any
) -> None:
    path = sqlite_file(roots.inside)
    import_dataset(store, connect("sqlite", path), roots.options("library"), "operator:a")
    with pytest.raises(ImportRefused) as refused:
        reimport_dataset(store, connect("sqlite", path), roots.options("library"), "operator:a")
    assert refusal_of(refused).code == RefusalCode.NO_CHANGE
    with sqlite3.connect(path) as writer:
        writer.execute("INSERT INTO authors VALUES (4, 'Dale')")
    again = reimport_dataset(store, connect("sqlite", path), roots.options("library"), "operator:a")
    assert again.label == 2


def test_a_snapshot_of_a_sqlite_file_being_written_sees_one_state_of_it(
    roots: Any, store: Store, connect: Any
) -> None:
    path = roots.inside / "ledger.sqlite"
    with sqlite3.connect(path) as setup:
        setup.execute("PRAGMA journal_mode = WAL")
        setup.execute("CREATE TABLE debits (id INTEGER PRIMARY KEY)")
        setup.execute("CREATE TABLE credits (id INTEGER PRIMARY KEY)")
        rows = [(n,) for n in range(20_000)]
        setup.executemany("INSERT INTO debits VALUES (?)", rows)
        setup.executemany("INSERT INTO credits VALUES (?)", rows)
    stop = threading.Event()

    def write() -> None:
        with sqlite3.connect(path, isolation_level=None) as writer:
            number = 20_000
            while not stop.is_set():
                writer.execute("BEGIN IMMEDIATE")
                writer.execute("INSERT INTO debits VALUES (?)", (number,))
                writer.execute("INSERT INTO credits VALUES (?)", (number,))
                writer.execute("COMMIT")
                number += 1

    writing = threading.Thread(target=write)
    writing.start()
    try:
        with store.pin() as pin:
            imported = build_import(store, pin, connect("sqlite", path), roots.options("ledger"))
    finally:
        stop.set()
        writing.join()
    debits, credits = imported.result.sources["debits"], imported.result.sources["credits"]
    assert isinstance(debits, TypedSource)
    assert isinstance(credits, TypedSource)
    assert debits.rows == credits.rows


def test_the_database_file_must_be_the_one_confined(roots: Any, store: Store, connect: Any) -> None:
    path = sqlite_file(roots.inside)
    source = connect("sqlite", path)
    path.rename(roots.inside / "moved.sqlite")
    sqlite_file(roots.inside)
    with pytest.raises(ImportRefused) as refused:
        import_dataset(store, source, roots.options("library"), "operator:a")
    assert refusal_of(refused).code == RefusalCode.PATH_NOT_CONFINED


def test_a_file_connection_outside_every_import_directory_is_refused(
    roots: Any, connect: Any
) -> None:
    outside = sqlite_file(roots.outside)
    (roots.inside / "link.sqlite").symlink_to(outside)
    for path in (outside, roots.inside / "link.sqlite", roots.inside):
        with pytest.raises(ImportRefused) as refused:
            connect("sqlite", path)
        assert refusal_of(refused).code == RefusalCode.PATH_NOT_CONFINED


def test_a_named_connection_is_never_read_by_a_packs_importer(
    roots: Any, store: Store, connect: Any
) -> None:
    source = connect("sqlite", sqlite_file(roots.inside))
    with pytest.raises(ImportRefused) as refused:
        import_dataset(store, source, roots.options("library"), "operator:a", pack="birds")
    assert refusal_of(refused).code == RefusalCode.CONFLICTING_MEMBERS


def test_the_cells_of_every_table_are_bounded_before_rows_are_read(
    roots: Any, store: Store, connect: Any
) -> None:
    source = connect("sqlite", sqlite_file(roots.inside))
    options = roots.options("library", limits=ImportLimits(import_cells=10))
    with pytest.raises(ImportRefused) as refused:
        import_dataset(store, source, options, "operator:a")
    refusal = refusal_of(refused)
    assert refusal.limit is not None
    assert refusal.limit.name == "import_cells"


def test_provenance_of_a_file_is_its_location_and_nothing_else(
    roots: Any, store: Store, connect: Any
) -> None:
    (roots.inside / "shelf").mkdir()
    path = sqlite_file(roots.inside / "shelf")
    done = import_dataset(store, connect("sqlite", path), roots.options("library"), "operator:a")
    dataset = published(store, done)["dataset"]
    assert isinstance(dataset, DatasetDescriptor)
    assert dataset.fields.source is not None
    assert dataset.fields.source.model_dump() == {
        "kind": "database",
        "location": "shelf/library.sqlite",
    }
    assert dataset.fields.name == "library"


# --- DuckDB ------------------------------------------------------------------------------------


def test_a_duckdb_file_gives_its_types_comments_and_keys_as_imported(
    roots: Any, store: Store, connect: Any
) -> None:
    source = connect("duckdb", duckdb_file(roots.inside, DUCKDB_LIBRARY))
    done = import_dataset(store, source, roots.options("library"), "operator:a")
    found = published(store, done)
    authors, name, born = found["authors"], found["authors.name"], found["authors.born"]
    assert authors.definition == "People who wrote a book of the library"
    assert status(authors, "/definition") == "imported"
    assert name.definition == "As printed on the cover"
    assert isinstance(born, ColumnDescriptor)
    assert (born.fields.datatype, status(born, "/fields/datatype")) == ("date", "imported")
    written = found["rel:books.author_id"]
    assert status(written, "/fields/parent_columns") == "imported"
    assert "recent" not in found
    dataset = found["dataset"]
    assert isinstance(dataset, DatasetDescriptor)
    assert dataset.fields.source is not None
    assert dataset.fields.source.location == "library.duckdb/main"


def test_a_duckdb_connection_reads_the_schema_it_names(
    roots: Any, store: Store, connect: Any
) -> None:
    script = DUCKDB_LIBRARY + "CREATE SCHEMA archive; CREATE TABLE archive.old (id INTEGER);"
    path = duckdb_file(roots.inside, script)
    done = import_dataset(
        store, connect("duckdb", path, schema="archive"), roots.options("old"), "operator:a"
    )
    tables = {d.id for d in published(store, done).values() if isinstance(d, TableDescriptor)}
    assert tables == {"old"}
    with pytest.raises(ImportRefused) as refused:
        import_dataset(
            store, connect("duckdb", path, schema="missing"), roots.options("x"), "operator:a"
        )
    refusal = refusal_of(refused)
    assert refusal.code == RefusalCode.INVALID_VALUE
    assert DataSegment(data="archive") in refusal.alternatives
    assert DataSegment(data="main") in refusal.alternatives


def test_a_duckdb_view_that_reads_a_file_is_neither_imported_nor_run(
    roots: Any, store: Store, connect: Any
) -> None:
    secret = roots.outside / "secret.csv"
    secret.write_text("word\nhidden\n")
    script = f"CREATE TABLE t (id INTEGER); CREATE VIEW leak AS SELECT * FROM '{secret}';"
    source = connect("duckdb", duckdb_file(roots.inside, script))
    done = import_dataset(store, source, roots.options("library"), "operator:a")
    assert set(published(store, done)) == {"dataset", "t", "t.id"}


def test_a_column_of_a_type_that_is_not_read_is_refused_naming_it(
    roots: Any, store: Store, connect: Any
) -> None:
    script = "CREATE TABLE covers (id INTEGER, image BLOB); INSERT INTO covers VALUES (1, 'x');"
    source = connect("duckdb", duckdb_file(roots.inside, script))
    with pytest.raises(ImportRefused) as refused:
        import_dataset(store, source, roots.options("library"), "operator:a")
    refusal = refusal_of(refused)
    assert refusal.code == RefusalCode.UNSUPPORTED_FORMAT
    assert DataSegment(data="image") in refusal.message
    assert [alternative.model_dump()["text"] for alternative in refusal.alternatives] == list(
        DATABASE_TYPES
    )


def test_a_file_that_is_no_duckdb_database_is_unparseable(
    roots: Any, store: Store, connect: Any
) -> None:
    path = roots.inside / "broken.duckdb"
    path.write_bytes(b"not a database" * 100)
    with pytest.raises(ImportRefused) as refused:
        import_dataset(store, connect("duckdb", path), roots.options("library"), "operator:a")
    assert refusal_of(refused).code == RefusalCode.UNPARSEABLE_SOURCE


def test_a_generated_column_of_a_duckdb_file_is_refused_naming_it(
    roots: Any, store: Store, connect: Any
) -> None:
    script = """
    CREATE TABLE settings (id INTEGER DEFAULT 1,
        leak VARCHAR GENERATED ALWAYS AS (current_setting('allowed_paths')::VARCHAR));
    INSERT INTO settings (id) VALUES (1);
    """
    source = connect("duckdb", duckdb_file(roots.inside, script))
    with pytest.raises(ImportRefused) as refused:
        import_dataset(store, source, roots.options("library"), "operator:a")
    refusal = refusal_of(refused)
    assert refusal.code == RefusalCode.UNSUPPORTED_FORMAT
    assert DataSegment(data="leak") in refusal.message
    assert "allowed_paths" not in shown(refusal)


def test_a_duckdb_column_with_a_default_is_read(roots: Any, store: Store, connect: Any) -> None:
    script = "CREATE TABLE t (id INTEGER, n INTEGER DEFAULT 7); INSERT INTO t (id) VALUES (1);"
    source = connect("duckdb", duckdb_file(roots.inside, script))
    with store.pin() as pin:
        imported = build_import(store, pin, source, roots.options("library"))
    read = imported.result.sources["t"]
    assert isinstance(read, TypedSource)
    assert read.rows == ((1, 7),)


_SESSION = """
from aibi.core.importers.snapshot import Target, _opened
from aibi.core.schema.limits import ImportLimits
path, identity, names = given
session = _opened(Target("duckdb", path=path, identity=tuple(identity)), ImportLimits())
found = [session.execute("SELECT current_setting($n)", {"n": n}).fetchone()[0] for n in names]
print(json.dumps(found))
"""


def test_a_duckdb_files_session_is_locked_without_external_access_or_stored_secrets(
    roots: Any,
) -> None:
    path = duckdb_file(roots.inside, DUCKDB_LIBRARY)
    identity = [os.stat(path).st_dev, os.stat(path).st_ino]
    names = ["lock_configuration", "enable_external_access", "allow_persistent_secrets"]
    found = json.loads(in_duckdb(_SESSION, [str(path), identity, names]))
    assert found == [True, False, False]


@pytest.mark.parametrize(
    ("comment", "code", "limit"),
    [
        ("x" * (MAX_TEXT + 1), RefusalCode.LIMIT_EXCEEDED, "text_characters"),
        ("As printed￾", RefusalCode.UNPARSEABLE_SOURCE, None),
        ("As printed\x1b[2J", RefusalCode.UNPARSEABLE_SOURCE, None),
    ],
    ids=["long", "noncharacter", "control"],
)
def test_a_comment_over_a_texts_length_or_not_plain_unicode_text_is_refused_naming_its_column(
    roots: Any, store: Store, connect: Any, comment: str, code: RefusalCode, limit: str | None
) -> None:
    script = (
        "CREATE TABLE authors (id INTEGER, name VARCHAR); "
        f"COMMENT ON COLUMN authors.name IS '{comment}';"
    )
    source = connect("duckdb", duckdb_file(roots.inside, script))
    with pytest.raises(ImportRefused) as refused:
        import_dataset(store, source, roots.options("library"), "operator:a")
    refusal = refusal_of(refused)
    assert refusal.code == code
    assert (refusal.limit.name if refusal.limit else None) == limit
    assert DataSegment(data="name") in refusal.message


def test_a_comment_with_tabs_and_line_breaks_is_a_definition(
    roots: Any, store: Store, connect: Any
) -> None:
    script = "CREATE TABLE t (id INTEGER); COMMENT ON TABLE t IS 'One\tTwo\r\nThree';"
    source = connect("duckdb", duckdb_file(roots.inside, script))
    found = published(store, import_dataset(store, source, roots.options("library"), "operator:a"))
    assert found["t"].definition == "One\tTwo\r\nThree"


def test_a_link_beside_a_duckdb_file_is_refused(roots: Any, store: Store, connect: Any) -> None:
    path = duckdb_file(roots.inside, DUCKDB_LIBRARY)
    (roots.inside / "library.duckdb.wal").symlink_to(roots.outside / "elsewhere")
    with pytest.raises(ImportRefused) as refused:
        import_dataset(store, connect("duckdb", path), roots.options("library"), "operator:a")
    assert refusal_of(refused).code == RefusalCode.PATH_NOT_CONFINED


# --- Servers: resolving their URLs, and what a failure says -------------------------------------


@pytest.mark.parametrize(
    ("kind", "url", "location"),
    [
        ("postgres", "postgresql://reader:pw@db.example.org:6543/lending?ssl=on", "public"),
        ("postgres", "postgres://reader@db.example.org/lending", "public"),
        ("mysql", "mysql://reader:pw@db.example.org:3307/lending", "lending"),
    ],
)
def test_a_servers_provenance_is_its_host_database_and_schema(
    connect: Any, kind: Kind, url: str, location: str
) -> None:
    resolved = connect(kind, url_env="LIBRARY_URL", environ={"LIBRARY_URL": url})
    assert resolved.source.location == f"db.example.org/lending/{location}"
    assert "pw" not in repr(resolved)
    assert "reader" not in repr(resolved)


@pytest.mark.parametrize(
    "url",
    [
        "",
        "mysql://reader:hunter2@db.example.org/lending",
        "postgresql://reader:hunter2@/lending",
        "postgresql://reader:hunter2@db.example.org/",
        "postgresql://reader:hunter2@db.example.org:port/lending",
        "host=db.example.org password=hunter2",
    ],
)
def test_a_server_connection_whose_variable_holds_no_url_is_refused_without_its_value(
    connect: Any, url: str
) -> None:
    with pytest.raises(ImportRefused) as refused:
        connect("postgres", url_env="LIBRARY_URL", environ={"LIBRARY_URL": url})
    refusal = refusal_of(refused)
    assert refusal.code == RefusalCode.INVALID_VALUE
    assert DataSegment(data="LIBRARY_URL") in refusal.message
    assert "hunter2" not in shown(refusal)


@pytest.mark.parametrize("kind", ["postgres", "mysql"])
def test_a_server_that_cannot_be_reached_is_refused_without_its_url(
    roots: Any, store: Store, connect: Any, kind: Kind
) -> None:
    scheme = "postgresql" if kind == "postgres" else "mysql"
    url = f"{scheme}://reader:hunter2@127.0.0.1:1/lending"
    source = connect(kind, url_env="LIBRARY_URL", environ={"LIBRARY_URL": url})
    with pytest.raises(ImportRefused) as refused:
        import_dataset(store, source, roots.options("library"), "operator:a")
    refusal = refusal_of(refused)
    assert refusal.code == RefusalCode.UNPARSEABLE_SOURCE
    assert "hunter2" not in shown(refusal)
    assert "127.0.0.1" not in shown(refusal)
    assert "hunter2" not in str(refused.value)


def test_the_scanners_are_bundled_for_this_duckdb() -> None:
    code = (
        "from aibi.core.importers.snapshot import extension_path\n"
        "print(all(extension_path(kind) is not None for kind in given))"
    )
    assert in_duckdb(code, ["postgres", "mysql"]).strip() == "True"


def test_the_servers_process_never_loads_duckdb_to_import_a_database() -> None:
    code = (
        "import sys; import aibi.core.importers.run, aibi.core.importers.databases, "
        "aibi.core.operator.router, aibi.core.api.serve; print('duckdb' in sys.modules)"
    )
    found = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True, timeout=60
    )
    assert found.stdout.strip() == "False"


# --- Postgres and MySQL, behind the marker -------------------------------------------------------


def _server_url(variable: str) -> str:
    url = os.environ.get(variable)
    if not url:
        pytest.skip(f"{variable} names no database")
    return url


_EXECUTE = """
from aibi.core.importers.snapshot import extension_path
kind, url, statements = given
session = duckdb.connect()
session.load_extension(str(extension_path(kind)))
session.execute(f"ATTACH '{url}' AS target (TYPE {kind})")
for statement in statements:
    session.execute(f"CALL {kind}_execute('target', $sql)", {"sql": statement})
session.close()
"""


def _execute(kind: Kind, url: str, statements: list[str]) -> None:
    in_duckdb(_EXECUTE, [kind, url, statements])


@pytest.fixture
def postgres_schema() -> Iterator[tuple[str, str]]:
    url = _server_url("AIBI_TEST_POSTGRES_URL")
    schema = f"aibi_{secrets.token_hex(4)}"
    _execute(
        "postgres",
        url,
        [
            f"CREATE SCHEMA {schema}",
            f"CREATE TABLE {schema}.authors (id integer PRIMARY KEY, name text)",
            f"COMMENT ON TABLE {schema}.authors IS 'People who wrote a book'",
            f"COMMENT ON COLUMN {schema}.authors.name IS 'As printed'",
            f"CREATE TABLE {schema}.loans (id integer, day date, author integer "
            f"REFERENCES {schema}.authors (id), PRIMARY KEY (id, day)) PARTITION BY RANGE (day)",
            f"CREATE TABLE {schema}.loans_2024 PARTITION OF {schema}.loans "
            "FOR VALUES FROM ('2024-01-01') TO ('2025-01-01')",
            f"CREATE VIEW {schema}.named AS SELECT name FROM {schema}.authors",
            f"CREATE MATERIALIZED VIEW {schema}.counted AS SELECT count(*) FROM {schema}.authors",
            f"INSERT INTO {schema}.authors VALUES (1, 'Ash'), (2, 'Brook')",
            f"INSERT INTO {schema}.loans VALUES (1, '2024-02-01', 1), (2, '2024-03-01', 2)",
        ],
    )
    yield url, schema
    _execute("postgres", url, [f"DROP SCHEMA {schema} CASCADE"])


@pytest.mark.databases
def test_a_postgres_snapshot_reads_base_tables_keys_and_comments(
    roots: Any, store: Store, connect: Any, postgres_schema: tuple[str, str]
) -> None:
    url, schema = postgres_schema
    source = connect("postgres", url_env="LIBRARY_URL", schema=schema, environ={"LIBRARY_URL": url})
    done = import_dataset(store, source, roots.options("library"), "operator:a")
    found = published(store, done)
    tables = {d.id for d in found.values() if isinstance(d, TableDescriptor)}
    assert tables == {"authors", "loans"}
    assert found["authors"].definition == "People who wrote a book"
    assert found["authors.name"].definition == "As printed"
    assert status(found["loans"], "/fields/primary_key") == "imported"
    assert status(found["rel:loans.author"], "/fields/child_columns") == "imported"
    noted = {
        part.data
        for note in done.notes
        for part in note.message
        if note.kind == "skipped_source" and isinstance(part, DataSegment)
    }
    assert noted == {"counted", "named"}
    written = url.partition("://")[2].partition("@")[0].partition(":")[2]
    stored = [path.read_bytes() for path in roots.stores.rglob("*") if path.is_file()]
    for password in {written, urllib.parse.unquote(written)} - {""}:
        assert not any(password.encode() in content for content in stored)


@pytest.fixture
def postgres_empty() -> Iterator[tuple[str, str]]:
    """An empty schema of the test database, and one beside it, both dropped after the test."""
    url = _server_url("AIBI_TEST_POSTGRES_URL")
    schema = f"aibi_{secrets.token_hex(4)}"
    _execute("postgres", url, [f"CREATE SCHEMA {schema}", f"CREATE SCHEMA {schema}_other"])
    yield url, schema
    _execute(
        "postgres",
        url,
        [f"DROP SCHEMA {schema} CASCADE", f"DROP SCHEMA {schema}_other CASCADE"],
    )


def _postgres_import(
    store: Store, roots: Any, connect: Any, url: str, schema: str
) -> tuple[Any, dict[str, Descriptor]]:
    source = connect("postgres", url_env="LIBRARY_URL", schema=schema, environ={"LIBRARY_URL": url})
    with store.pin() as pin:
        imported = build_import(store, pin, source, roots.options("library"))
    return imported, by_id(tuple(imported.result.descriptors))


@pytest.mark.databases
def test_a_postgres_decimal_wider_than_duckdbs_is_read_as_its_exact_text(
    roots: Any, store: Store, connect: Any, postgres_empty: tuple[str, str]
) -> None:
    url, schema = postgres_empty
    _execute(
        "postgres",
        url,
        [
            f"CREATE DOMAIN {schema}.amount AS numeric",
            f"CREATE DOMAIN {schema}.total AS {schema}.amount",
            f"CREATE TABLE {schema}.sums (id int PRIMARY KEY, n numeric, wide numeric(40, 5), "
            f"narrow numeric(10, 2), total {schema}.total)",
            f"INSERT INTO {schema}.sums VALUES (1, 12345678901234567890.123456789, "
            "123456789012345678901234567890.12345, 1.25, 98765432109876543210.5)",
        ],
    )
    imported, found = _postgres_import(store, roots, connect, url, schema)
    sums = imported.result.sources["sums"]
    assert isinstance(sums, TypedSource)
    assert sums.rows == (
        (
            1,
            "12345678901234567890.123456789",
            "123456789012345678901234567890.12345",
            "1.25",
            "98765432109876543210.5",
        ),
    )
    for column in ("n", "wide", "narrow", "total"):
        assert status(found[f"sums.{column}"], "/fields/datatype") == "proposed"


@pytest.mark.databases
def test_postgres_columns_whose_names_differ_only_in_case_keep_their_names_and_comments(
    roots: Any, store: Store, connect: Any, postgres_empty: tuple[str, str]
) -> None:
    url, schema = postgres_empty
    _execute(
        "postgres",
        url,
        [
            f'CREATE TABLE {schema}.cased (id int PRIMARY KEY, "Name" text, name text)',
            f"COMMENT ON COLUMN {schema}.cased.\"Name\" IS 'Upper'",
            f"COMMENT ON COLUMN {schema}.cased.name IS 'Lower'",
            f"INSERT INTO {schema}.cased VALUES (1, 'A', 'a')",
        ],
    )
    imported, found = _postgres_import(store, roots, connect, url, schema)
    cased = imported.result.sources["cased"]
    assert isinstance(cased, TypedSource)
    assert (cased.columns, cased.rows) == (("id", "Name", "name"), ((1, "A", "a"),))
    described = {
        (d.label, d.definition)
        for d in found.values()
        if isinstance(d, ColumnDescriptor) and d.id != "cased.id"
    }
    assert described == {("Name", "Upper"), ("name", "Lower")}


@pytest.mark.databases
def test_a_postgres_table_others_inherit_from_is_read_without_their_rows(
    roots: Any, store: Store, connect: Any, postgres_empty: tuple[str, str]
) -> None:
    url, schema = postgres_empty
    _execute(
        "postgres",
        url,
        [
            f"CREATE TABLE {schema}.items (id int PRIMARY KEY)",
            f"CREATE TABLE {schema}.books (title text) INHERITS ({schema}.items)",
            f"INSERT INTO {schema}.items VALUES (1)",
            f"INSERT INTO {schema}.books VALUES (2, 'Second')",
        ],
    )
    imported, _ = _postgres_import(store, roots, connect, url, schema)
    items, books = imported.result.sources["items"], imported.result.sources["books"]
    assert isinstance(items, TypedSource)
    assert isinstance(books, TypedSource)
    assert (items.rows, books.rows) == (((1,),), ((2, "Second"),))


@pytest.mark.databases
def test_a_postgres_foreign_key_to_another_schema_is_noted_and_not_kept(
    roots: Any, store: Store, connect: Any, postgres_empty: tuple[str, str]
) -> None:
    url, schema = postgres_empty
    _execute(
        "postgres",
        url,
        [
            f"CREATE TABLE {schema}_other.authors (id int PRIMARY KEY)",
            f"CREATE TABLE {schema}.books (id int PRIMARY KEY, "
            f"author int REFERENCES {schema}_other.authors (id))",
        ],
    )
    imported, found = _postgres_import(store, roots, connect, url, schema)
    assert not any(isinstance(d, RelationshipDescriptor) for d in found.values())
    [note] = [note for note in imported.result.notes if note.subject == "books.author"]
    assert "its parent table is not read" in note.message[0].model_dump()["text"]


@pytest.mark.databases
def test_a_postgres_schema_that_is_not_there_is_refused_listing_every_schema(
    roots: Any, store: Store, connect: Any, postgres_empty: tuple[str, str]
) -> None:
    url, schema = postgres_empty
    with pytest.raises(ImportRefused) as refused:
        _postgres_import(store, roots, connect, url, f"{schema}_missing")
    refusal = refusal_of(refused)
    assert refusal.code == RefusalCode.INVALID_VALUE
    for listed in (schema, f"{schema}_other", "public"):
        assert DataSegment(data=listed) in refusal.alternatives
    with pytest.raises(ImportRefused) as empty:
        _postgres_import(store, roots, connect, url, schema)
    assert refusal_of(empty).code == RefusalCode.EMPTY_SOURCE


@pytest.mark.databases
def test_a_mysql_snapshot_reads_base_tables_keys_and_comments(
    roots: Any, store: Store, connect: Any
) -> None:
    url = _server_url("AIBI_TEST_MYSQL_URL")
    names = ["aibi_authors", "aibi_books"]
    _execute(
        "mysql",
        url,
        [
            "CREATE TABLE aibi_authors (id INTEGER PRIMARY KEY, name TEXT COMMENT 'As printed') "
            "COMMENT 'People who wrote a book'",
            "CREATE TABLE aibi_books (code VARCHAR(8) PRIMARY KEY, author INTEGER, "
            "FOREIGN KEY (author) REFERENCES aibi_authors (id))",
            "CREATE VIEW aibi_named AS SELECT name FROM aibi_authors",
            "INSERT INTO aibi_authors VALUES (1, 'Ash')",
            "INSERT INTO aibi_books VALUES ('b1', 1)",
        ],
    )
    try:
        source = connect("mysql", url_env="LIBRARY_URL", environ={"LIBRARY_URL": url})
        found = published(
            store, import_dataset(store, source, roots.options("library"), "operator:a")
        )
        assert {"aibi_authors", "aibi_books"} <= set(found)
        assert "aibi_named" not in found
        assert found["aibi_authors"].definition == "People who wrote a book"
        assert status(found["rel:aibi_books.author"], "/fields/parent_table") == "imported"
    finally:
        _execute(
            "mysql",
            url,
            ["DROP VIEW aibi_named", *(f"DROP TABLE {name}" for name in reversed(names))],
        )

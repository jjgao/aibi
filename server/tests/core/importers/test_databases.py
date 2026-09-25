"""Database snapshots (SPEC §13.1, §14, D305–D307): SQLite and DuckDB files, and Postgres and
MySQL servers behind the ``databases`` marker, which run when ``AIBI_TEST_POSTGRES_URL`` or
``AIBI_TEST_MYSQL_URL`` names a database the tests may fill and empty.

The datasets are a lending library's: authors, books and loans. DuckDB runs only in fresh
interpreters (``in_duckdb``), never in the tests' own process, as it never runs in the server's."""

import dataclasses
import hashlib
import json
import os
import secrets
import select
import sqlite3
import subprocess
import sys
import threading
import urllib.parse
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from datetime import datetime
from pathlib import Path
from typing import Any, cast

import pytest
from hypothesis import assume, example, given
from hypothesis import strategies as st

from aibi.core.importers import databases, urls
from aibi.core.importers.databases import Connection, DatabaseImporter, Resolved, resolve
from aibi.core.importers.errors import ImportRefused
from aibi.core.importers.run import Published, build_import, import_dataset, reimport_dataset
from aibi.core.importers.snapshot import (
    _POSTGRES_OWNER_CODE,
    DATABASE_TYPES,
    SQLITE_VALUES,
    Kind,
    Target,
    _collided,
    _defensive,
    _key,
    _mysql_catalog,
    _mysql_collisions,
    _mysql_tables,
    _opened_database,
    _postgres_catalog,
    _postgres_lock,
    _postgres_read,
    _Relation,
    read_snapshot,
)
from aibi.core.schema.descriptors import (
    ColumnDescriptor,
    DatasetDescriptor,
    Descriptor,
    RelationshipDescriptor,
    TableDescriptor,
)
from aibi.core.schema.limits import MAX_STRING, MAX_TEXT, ImportLimits
from aibi.core.schema.output import DataSegment, TextSegment
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


def in_duckdb(
    code: str, given: object, cwd: Path | None = None, env: dict[str, str] | None = None
) -> str:
    """What ``code`` prints, run in a fresh interpreter with ``duckdb`` imported and the JSON of
    ``given`` as ``given``, in ``cwd`` and with ``env`` when given."""
    found = subprocess.run(
        [sys.executable, "-c", f"import duckdb, json, sys\ngiven = json.load(sys.stdin)\n{code}"],
        input=json.dumps(given),
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
        cwd=cwd,
        env=env,
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
    assert note.message[-1].model_dump()["text"] == ": a view, not a base table"


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


def test_a_virtual_table_whose_sql_hides_its_keyword_is_still_noted(
    roots: Any, store: Store, connect: Any
) -> None:
    arguments = {"fts4": "body", "fts5": "body", "rtree": "id, lo, hi"}
    probe = sqlite3.connect(":memory:")
    compiled = {row[0] for row in probe.execute("SELECT name FROM pragma_module_list")}
    probe.close()
    modules = [module for module in arguments if module in compiled]
    if not modules:
        pytest.skip("this SQLite has no virtual table module")
    module = modules[0]
    path = sqlite_file(
        roots.inside,
        f"CREATE TABLE authors (id INTEGER PRIMARY KEY); INSERT INTO authors VALUES (1);"
        f"CREATE VIRTUAL TABLE words USING {module}({arguments[module]});",
    )
    with sqlite3.connect(path) as writer:
        writer.execute("PRAGMA writable_schema = ON")
        writer.execute(
            "UPDATE sqlite_schema SET sql = ? WHERE name = 'words'",
            (f"CREATE/*x*/VIRTUAL TABLE words USING {module}({arguments[module]})",),
        )
    done = import_dataset(store, connect("sqlite", path), roots.options("library"), "operator:a")
    tables = {d.id for d in published(store, done).values() if isinstance(d, TableDescriptor)}
    assert tables == {"authors"}
    said = [part for note in done.notes for part in note.message]
    assert DataSegment(data="words") in said


def test_a_virtual_table_whose_root_page_is_forged_is_still_noted_with_its_shadow_tables(
    roots: Any, store: Store, connect: Any
) -> None:
    probe = sqlite3.connect(":memory:")
    compiled = {row[0] for row in probe.execute("SELECT name FROM pragma_module_list")}
    probe.close()
    if "fts4" not in compiled:
        pytest.skip("this SQLite has no fts4")
    path = sqlite_file(
        roots.inside,
        "CREATE TABLE a (id INTEGER PRIMARY KEY); INSERT INTO a VALUES (1);"
        "CREATE VIRTUAL TABLE g USING fts4(body);",
    )
    writer = sqlite3.connect(path)
    with writer:
        writer.execute("PRAGMA writable_schema = ON")
        writer.execute("UPDATE sqlite_schema SET rootpage = 2 WHERE name = 'g'")
    writer.close()
    done = import_dataset(store, connect("sqlite", path), roots.options("library"), "operator:a")
    tables = {d.id for d in published(store, done).values() if isinstance(d, TableDescriptor)}
    assert tables == {"a"}
    noted = {
        part.data for note in done.notes for part in note.message if isinstance(part, DataSegment)
    }
    assert {"g", "g_content", "g_segdir", "g_segments"} <= noted


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
        writer.execute("CREATE TABLE SQLITE_Shouted (id INTEGER)")
    done = import_dataset(store, connect("sqlite", path), roots.options("library"), "operator:a")
    noted = {
        part.data
        for note in done.notes
        if note.kind == "skipped_source"
        for part in note.message
        if isinstance(part, DataSegment)
    }
    assert noted == {"sqlite_planted", "SQLITE_Shouted"}


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


def test_a_foreign_key_matches_its_parent_ignoring_the_case_of_ascii_letters_alone(
    roots: Any, store: Store, connect: Any
) -> None:
    script = """
    CREATE TABLE "Émile" (id INTEGER PRIMARY KEY);
    CREATE TABLE "émile" (id INTEGER PRIMARY KEY);
    CREATE TABLE "straße" (id INTEGER PRIMARY KEY);
    CREATE TABLE books (code TEXT PRIMARY KEY, author INTEGER REFERENCES "ÉMILE" (id),
        street INTEGER REFERENCES "STRASSE" (id));
    INSERT INTO "Émile" VALUES (1); INSERT INTO "émile" VALUES (2); INSERT INTO "straße" VALUES (3);
    INSERT INTO books VALUES ('b1', 1, 3);
    """
    source = connect("sqlite", sqlite_file(roots.inside, script))
    done = import_dataset(store, source, roots.options("library"), "operator:a")
    found = published(store, done)
    [kept] = [
        d
        for d in found.values()
        if isinstance(d, RelationshipDescriptor) and status(d, "/fields/parent_table") == "imported"
    ]
    assert kept.id == "rel:books.author"
    assert found[kept.fields.parent_table].label == "Émile"
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
    assert DataSegment(data=f"library.sqlite{suffix}") in refusal.message
    assert str(roots.inside) not in shown(refusal)


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


@pytest.mark.parametrize(
    ("script", "subject"),
    [
        (
            "CREATE TABLE shelves (room INTEGER, shelf INTEGER, PRIMARY KEY (room, shelf));"
            "CREATE TABLE books (code TEXT PRIMARY KEY, room INTEGER,"
            "    FOREIGN KEY (room, room) REFERENCES shelves (room, shelf));",
            "books.room+room",
        ),
        (
            "CREATE TABLE rooms (id INTEGER PRIMARY KEY);"
            "CREATE TABLE books (code TEXT PRIMARY KEY, room INTEGER, wing INTEGER,"
            "    FOREIGN KEY (room, wing) REFERENCES rooms (id, id));",
            "books.room+wing",
        ),
    ],
    ids=["child", "parent"],
)
def test_a_foreign_key_that_repeats_a_column_is_noted_and_not_kept(
    roots: Any, store: Store, connect: Any, script: str, subject: str
) -> None:
    source = connect("sqlite", sqlite_file(roots.inside, script))
    done = import_dataset(store, source, roots.options("library"), "operator:a")
    assert not any(isinstance(d, RelationshipDescriptor) for d in published(store, done).values())
    [note] = [note for note in done.notes if note.subject == subject]
    assert "a column repeats in it" in note.message[0].model_dump()["text"]


def test_a_relationship_proposed_by_containment_in_a_snapshot_stays_proposed(
    roots: Any, store: Store, connect: Any
) -> None:
    script = """
    CREATE TABLE authors (id INTEGER PRIMARY KEY);
    CREATE TABLE books (code TEXT PRIMARY KEY, author_id INTEGER);
    INSERT INTO authors VALUES (1), (2), (3);
    INSERT INTO books VALUES ('b1', 1), ('b2', 1), ('b3', 2);
    """
    source = connect("sqlite", sqlite_file(roots.inside, script))
    found = published(store, import_dataset(store, source, roots.options("library"), "operator:a"))
    proposed = found["rel:books.author_id"]
    for field in ("child_table", "child_columns", "parent_table", "parent_columns"):
        assert status(proposed, f"/fields/{field}") == "proposed"


def test_a_view_whose_name_is_not_unicode_text_is_noted_with_its_name_escaped(
    roots: Any, store: Store, connect: Any
) -> None:
    script = 'CREATE TABLE t (id INTEGER); CREATE VIEW "odd￾" AS SELECT 1 AS one;'
    source = connect("sqlite", sqlite_file(roots.inside, script))
    done = import_dataset(store, source, roots.options("library"), "operator:a")
    [note] = [note for note in done.notes if note.kind == "skipped_source"]
    assert DataSegment(data="odd\\ufffe") in note.message


def test_relations_skipped_past_import_tables_are_counted_in_one_note(
    roots: Any, store: Store, connect: Any
) -> None:
    views = "".join(f"CREATE VIEW v{n} AS SELECT 1 AS one;" for n in range(5))
    source = connect("sqlite", sqlite_file(roots.inside, "CREATE TABLE t (id INTEGER);" + views))
    options = roots.options("library", limits=ImportLimits(import_tables=2))
    done = import_dataset(store, source, options, "operator:a")
    skipped = [note for note in done.notes if note.kind == "skipped_source"]
    assert sorted(note.count or 0 for note in skipped) == [0, 0, 3]
    three = "".join(f"CREATE VIEW w{n} AS SELECT 1 AS one;" for n in range(3))
    fewer = sqlite_file(roots.inside, "CREATE TABLE t (id INTEGER);" + three, "fewer.sqlite")
    options = roots.options("fewer", limits=ImportLimits(import_tables=2))
    done = import_dataset(store, connect("sqlite", fewer), options, "operator:a")
    skipped = [note for note in done.notes if note.kind == "skipped_source"]
    assert sorted(note.count or 0 for note in skipped) == [0, 0, 1]


@pytest.mark.parametrize("kind", ["sqlite", "duckdb"])
def test_rows_a_collation_or_a_type_ties_are_read_in_one_order_whatever_their_storage(
    roots: Any, store: Store, connect: Any, kind: Kind
) -> None:
    rows = ["('a', 1)", "('A', 1)"]
    if kind == "sqlite":
        rows += ["('x', 1.0)", "('x', 1)", "('z', -0.0)", "('z', 0.0)"]
    found: list[tuple[tuple[object, ...], ...]] = []
    for place, order in enumerate([rows, rows[::-1]]):
        if kind == "sqlite":
            script = (
                "CREATE TABLE words (word TEXT COLLATE NOCASE, n);"
                f"INSERT INTO words VALUES {', '.join(order)};"
            )
            path = sqlite_file(roots.inside, script, f"words{place}.sqlite")
        else:
            script = (
                "CREATE TABLE words (word VARCHAR COLLATE NOCASE, n INTEGER);"
                f"INSERT INTO words VALUES {', '.join(order)};"
            )
            path = duckdb_file(roots.inside, script, f"words{place}.duckdb")
        with store.pin() as pin:
            imported = build_import(store, pin, connect(kind, path), roots.options(f"w{place}"))
        words = imported.result.sources["words"]
        assert isinstance(words, TypedSource)
        found.append(tuple(tuple(repr(value) for value in row) for row in words.rows))
    assert found[0] == found[1]


def test_a_virtual_generated_column_of_a_sqlite_file_is_refused_and_a_stored_one_read(
    roots: Any, store: Store, connect: Any
) -> None:
    stored = """
    CREATE TABLE names (id INTEGER PRIMARY KEY, name TEXT,
        shout TEXT GENERATED ALWAYS AS (upper(name)) STORED);
    INSERT INTO names (id, name) VALUES (1, 'ash');
    """
    with store.pin() as pin:
        source = connect("sqlite", sqlite_file(roots.inside, stored))
        imported = build_import(store, pin, source, roots.options("library"))
    names = imported.result.sources["names"]
    assert isinstance(names, TypedSource)
    assert names.rows == ((1, "ash", "ASH"),)
    computed = stored.replace("STORED", "VIRTUAL")
    source = connect("sqlite", sqlite_file(roots.inside, computed, "computed.sqlite"))
    with pytest.raises(ImportRefused) as refused:
        import_dataset(store, source, roots.options("other"), "operator:a")
    refusal = refusal_of(refused)
    assert refusal.code == RefusalCode.UNSUPPORTED_FORMAT
    assert DataSegment(data="shout") in refusal.message


def test_a_text_sqlite_cannot_decode_is_refused_naming_its_table(
    roots: Any, store: Store, connect: Any
) -> None:
    script = (
        "CREATE TABLE notes (id INTEGER, body TEXT);"
        "INSERT INTO notes VALUES (1, CAST(x'ff' AS TEXT));"
    )
    source = connect("sqlite", sqlite_file(roots.inside, script))
    with pytest.raises(ImportRefused) as refused:
        import_dataset(store, source, roots.options("library"), "operator:a")
    refusal = refusal_of(refused)
    assert refusal.code == RefusalCode.UNPARSEABLE_SOURCE
    assert DataSegment(data="notes") in refusal.message


def test_an_empty_tables_columns_count_against_import_cells(
    roots: Any, store: Store, connect: Any
) -> None:
    source = connect("sqlite", sqlite_file(roots.inside, "CREATE TABLE wide (a, b, c);"))
    options = roots.options("library", limits=ImportLimits(import_cells=2))
    with pytest.raises(ImportRefused) as refused:
        import_dataset(store, source, options, "operator:a")
    refusal = refusal_of(refused)
    assert refusal.limit is not None
    assert (refusal.limit.name, refusal.limit.max) == ("import_cells", 2)


@pytest.mark.parametrize(
    ("limits", "name"),
    [
        (ImportLimits(import_tables=2), None),
        (ImportLimits(import_tables=1), "import_tables"),
        (ImportLimits(table_columns=3), None),
        (ImportLimits(table_columns=2), "table_columns"),
    ],
)
def test_the_tables_and_a_tables_columns_may_reach_their_limits_but_not_pass_them(
    roots: Any, store: Store, connect: Any, limits: ImportLimits, name: str | None
) -> None:
    script = "CREATE TABLE a (x, y, z); CREATE TABLE b (x);"
    source = connect("sqlite", sqlite_file(roots.inside, script))
    options = roots.options("library", limits=limits)
    if name is None:
        import_dataset(store, source, options, "operator:a")
        return
    with pytest.raises(ImportRefused) as refused:
        import_dataset(store, source, options, "operator:a")
    refusal = refusal_of(refused)
    assert refusal.limit is not None
    assert refusal.limit.name == name


def test_a_re_import_keeps_a_tables_id_when_a_name_that_normalises_to_it_comes_first(
    roots: Any, store: Store, connect: Any
) -> None:
    path = sqlite_file(roots.inside, "CREATE TABLE a_b (id INTEGER); INSERT INTO a_b VALUES (1);")
    import_dataset(store, connect("sqlite", path), roots.options("library"), "operator:a")
    with sqlite3.connect(path) as writer:
        writer.execute('CREATE TABLE "A b" (id INTEGER)')
    again = reimport_dataset(store, connect("sqlite", path), roots.options("library"), "operator:a")
    found = published(store, again)
    assert (found["a_b"].label, found["a_b_2"].label) == ("a_b", "A b")


@pytest.mark.parametrize("mode", [0o666, 0o620, 0o602])
def test_a_connection_file_its_group_or_others_can_write_is_refused(
    roots: Any, connect: Any, mode: int
) -> None:
    path = sqlite_file(roots.inside)
    path.chmod(mode)
    with pytest.raises(ImportRefused) as refused:
        connect("sqlite", path)
    refusal = refusal_of(refused)
    assert refusal.code == RefusalCode.PATH_NOT_CONFINED
    assert DataSegment(data="library.sqlite") in refusal.message
    path.chmod(0o644)
    assert connect("sqlite", path).source.location == "library.sqlite"


def test_a_connection_file_in_a_directory_others_can_write_is_refused_unless_it_is_sticky(
    roots: Any, connect: Any
) -> None:
    shared = roots.inside / "shared"
    shared.mkdir()
    path = sqlite_file(shared)
    for mode in (0o777, 0o775):
        shared.chmod(mode)
        with pytest.raises(ImportRefused) as refused:
            connect("sqlite", path)
        refusal = refusal_of(refused)
        assert refusal.code == RefusalCode.PATH_NOT_CONFINED
        assert DataSegment(data="shared") in refusal.message
    shared.chmod(0o1777)
    assert connect("sqlite", path).source.location == "shared/library.sqlite"


_SWAPPED = """
import json, os, sys
from aibi.core.importers import snapshot
from aibi.core.importers.errors import ImportRefused
from aibi.core.schema.limits import ImportLimits
path, outside = json.load(sys.stdin)
status = os.stat(path)
target = snapshot.Target("sqlite", path=path, identity=(status.st_dev, status.st_ino))
checked = snapshot._same_file
calls = []

def swapped(given):
    if calls:
        os.remove(path)
        os.rename(path + ".moved", path)
    checked(given)
    if not calls:
        os.rename(path, path + ".moved")
        os.symlink(outside, path)
    calls.append(given)

snapshot._same_file = swapped
try:
    snapshot.read_snapshot(target, ImportLimits())
    print("read")
except ImportRefused as refused:
    print(refused.refusals[0].code)
"""


@pytest.mark.skipif(not os.path.isdir("/proc/self/fd"), reason="no /proc/self/fd here")
def test_a_sqlite_file_swapped_for_a_link_after_its_check_is_refused_once_opened(
    roots: Any,
) -> None:
    path, outside = sqlite_file(roots.inside), sqlite_file(roots.outside)
    found = subprocess.run(
        [sys.executable, "-c", _SWAPPED],
        input=json.dumps([str(path), str(outside)]),
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    assert found.stdout.strip() == "PATH_NOT_CONFINED"


def test_a_sqlite_connection_is_made_defensive_before_anything_is_read() -> None:
    connection = sqlite3.connect(":memory:")
    try:
        _defensive(connection)
        assert connection.getconfig(sqlite3.SQLITE_DBCONFIG_DEFENSIVE)
        for option in (
            sqlite3.SQLITE_DBCONFIG_ENABLE_VIEW,
            sqlite3.SQLITE_DBCONFIG_ENABLE_TRIGGER,
            sqlite3.SQLITE_DBCONFIG_TRUSTED_SCHEMA,
            sqlite3.SQLITE_DBCONFIG_ENABLE_LOAD_EXTENSION,
        ):
            assert not connection.getconfig(option)
        assert connection.execute("PRAGMA query_only").fetchone() == (1,)
    finally:
        connection.close()


def test_mysql_names_equal_by_the_servers_weight_or_by_ascii_case_are_refused() -> None:
    _collided("databases", "db", [("db", "1"), ("other", "2")])
    for weighed in ([("db", "1"), ("Db", "2")], [("db", "1"), ("dé", "1")]):
        with pytest.raises(ImportRefused) as refused:
            _collided("databases", "db", weighed)
        assert refusal_of(refused).code == RefusalCode.UNSUPPORTED_FORMAT


class _Weighed:
    """A stand-in for a MySQL session's ``mysql_query``: every statement gives ``rows``."""

    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self.rows = rows

    def execute(self, sql: str, parameters: object) -> "_Weighed":
        return self

    def fetchall(self) -> list[tuple[object, ...]]:
        return self.rows


def test_mysql_names_whose_weights_the_server_does_not_give_are_not_taken_as_equal() -> None:
    session = _Weighed([("db", None, "BASE TABLE"), ("other", None, "BASE TABLE")])
    _mysql_collisions(cast(Any, session), "db")


def test_a_mariadb_system_versioned_table_is_checked_for_names_equal_to_its_own() -> None:
    session = _Weighed(
        [("db", "1", "BASE TABLE"), ("T", "2", "SYSTEM VERSIONED"), ("t", "3", "VIEW")]
    )
    with pytest.raises(ImportRefused) as refused:
        _mysql_collisions(cast(Any, session), "db")
    refusal = refusal_of(refused)
    assert refusal.code == RefusalCode.UNSUPPORTED_FORMAT
    assert DataSegment(data="T") in refusal.message


@pytest.mark.parametrize("kind", ["postgres", "mysql"])
def test_a_server_that_opened_a_database_other_than_its_urls_is_refused_naming_it(
    kind: Kind,
) -> None:
    target = Target(kind, database="lending")
    assert _opened_database(cast(Any, _Weighed([("lending",)])), target) == "lending"
    for other in ("Lending", "LENDING", "lending2", None):
        with pytest.raises(ImportRefused) as refused:
            _opened_database(cast(Any, _Weighed([(other,)])), target)
        refusal = refusal_of(refused)
        assert refusal.code == RefusalCode.INVALID_VALUE
        assert DataSegment(data=str(other)) in refusal.message


@pytest.mark.parametrize(
    ("name", "quotable", "as_constant"),
    [
        ("loans", True, True),
        ("a b-c.d$e#f@g", True, True),
        ('say "hi"', True, True),
        ("o'brien", True, False),
        ("émile_ß_東京", True, True),
        ("cover` UNION SELECT 1 -- z", False, False),
        ("cover\\", False, False),
        ("tab\tbed", False, False),
        ("zero\u200bwidth", False, False),
        ("private\ue000use", False, False),
        ("un\u0378assigned", False, False),
        ("del\x7f", False, False),
    ],
)
def test_a_mysql_name_is_read_only_of_characters_the_scanner_quotes_safely(
    name: str, quotable: bool, as_constant: bool
) -> None:
    assert urls.mysql_quotable(name) is quotable
    assert urls.mysql_quotable(name, constant=True) is as_constant


class _Catalogue:
    """A stand-in for a MySQL session's attached catalogue: a statement gives the rows of the
    first of ``rows``'s ``information_schema`` tables it reads."""

    def __init__(self, rows: dict[str, list[tuple[object, ...]]]) -> None:
        self.rows = rows
        self.found: list[tuple[object, ...]] = []

    def execute(self, sql: str, parameters: object) -> "_Catalogue":
        self.found = next((rows for table, rows in self.rows.items() if table in sql), [])
        return self

    def fetchall(self) -> list[tuple[object, ...]]:
        return self.found


@pytest.mark.parametrize(
    "name", ["cover` UNION SELECT amount FROM elsewhere.salaries -- z", "cover\\", "a\tb"]
)
def test_a_mysql_relation_whose_name_the_scanner_cannot_quote_is_refused_before_any_row(
    name: str,
) -> None:
    session = _Catalogue(
        {"TABLES": [("kept", "BASE TABLE", "", "YES"), (name, "BASE TABLE", "", "YES")]}
    )
    with pytest.raises(ImportRefused) as refused:
        _mysql_tables(cast(Any, session), "db")
    refusal = refusal_of(refused)
    assert refusal.code == RefusalCode.UNSUPPORTED_FORMAT
    assert DataSegment(data=name) in refusal.message
    assert "cannot quote safely" in shown(refusal)


def test_mysql_names_the_scanner_never_writes_or_quotes_safely_are_read() -> None:
    session = _Catalogue(
        {
            "TABLES": [
                ("it's", "BASE TABLE", "", "YES"),
                ("o'brien", "SYSTEM VERSIONED", "", "YES"),
                ("cover\\", "VIEW", "", None),
                ("v` UNION SELECT 1 -- z", "VIEW", "", None),
            ],
            "COLUMNS": [
                ("it's", "c'q", "", "int", 10),
                ("cover\\", "n` FROM x -- z", "", "int", 10),
            ],
        }
    )
    relations, skipped = _mysql_catalog(cast(Any, session), "db")
    assert [relation.name for relation in relations] == ["it's", "o'brien"]
    assert relations[0].columns == ("c'q",)
    assert {item.name for item in skipped} == {"cover\\", "v` UNION SELECT 1 -- z"}


def test_a_mysql_url_whose_database_the_server_names_only_in_another_case_is_refused() -> None:
    session = _Weighed([("MyDB", "1", "BASE TABLE"), ("other", "2", "BASE TABLE")])
    with pytest.raises(ImportRefused) as refused:
        _mysql_collisions(cast(Any, session), "mydb")
    refusal = refusal_of(refused)
    assert refusal.code == RefusalCode.INVALID_VALUE
    assert DataSegment(data="mydb") in refusal.message
    assert refusal.alternatives == [DataSegment(data="MyDB")]


def test_a_mysql_column_whose_name_the_scanner_cannot_quote_is_refused_naming_its_table() -> None:
    session = _Catalogue(
        {
            "TABLES": [("kept", "BASE TABLE", "", "YES")],
            "COLUMNS": [("kept", "id", "", "int", 10), ("kept", "n` FROM x -- ", "", "int", 10)],
        }
    )
    with pytest.raises(ImportRefused) as refused:
        _mysql_catalog(cast(Any, session), "db")
    refusal = refusal_of(refused)
    assert refusal.code == RefusalCode.UNSUPPORTED_FORMAT
    assert DataSegment(data="kept") in refusal.message
    assert DataSegment(data="n` FROM x -- ") in refusal.message


class _PostgresCatalogue:
    """A stand-in for a Postgres session's ``postgres_query``: a statement gives the rows of the
    first of ``rows``'s fragments it holds."""

    def __init__(self, rows: dict[str, list[tuple[object, ...]]]) -> None:
        self.rows = rows
        self.found: list[tuple[object, ...]] = []
        self.queries: list[str] = []

    def execute(self, sql: str, parameters: dict[str, str]) -> "_PostgresCatalogue":
        query = parameters["query"]
        self.queries.append(query)
        self.found = next((rows for part, rows in self.rows.items() if part in query), [])
        return self

    def fetchall(self) -> list[tuple[object, ...]]:
        return self.found


def test_a_postgres_table_whose_reading_would_run_its_owners_code_is_noted_and_not_read() -> None:
    session = _PostgresCatalogue(
        {
            "pg_toast": [("public",)],
            "row_security": [
                ("kept", "r", None, False, None, False, False, False, False),
                ("guarded", "r", None, False, None, False, False, True, False),
                ("computed", "p", None, False, None, False, False, False, True),
            ],
            "col_description": [("kept", "id", None, 23, False)],
        }
    )
    relations, skipped = _postgres_catalog(cast(Any, session), "public", ImportLimits())
    assert [relation.name for relation in relations] == ["kept"]
    reasons = {item.name: item.reason for item in skipped}
    assert set(reasons) == {"guarded", "computed"}
    assert "row security" in reasons["guarded"]
    assert "virtual generated column" in reasons["computed"]


def test_a_declared_key_naming_a_column_that_is_not_read_is_not_the_tables_key() -> None:
    assert _key(("id",), ("id", "name")) == ("id",)
    assert _key(("id", "gone"), ("id", "name")) is None
    assert _key((), ("id",)) is None


def test_a_second_foreign_key_on_the_same_columns_in_another_order_is_noted_and_not_kept(
    roots: Any, store: Store, connect: Any
) -> None:
    script = """
    CREATE TABLE p1 (x INTEGER, y INTEGER, PRIMARY KEY (x, y));
    CREATE TABLE p2 (x INTEGER, y INTEGER, PRIMARY KEY (x, y));
    CREATE TABLE c (id INTEGER PRIMARY KEY, a INTEGER, b INTEGER,
        FOREIGN KEY (a, b) REFERENCES p1 (x, y), FOREIGN KEY (b, a) REFERENCES p2 (x, y));
    INSERT INTO p1 VALUES (1, 2); INSERT INTO p2 VALUES (2, 1); INSERT INTO c VALUES (1, 1, 2);
    """
    source = connect("sqlite", sqlite_file(roots.inside, script))
    done = import_dataset(store, source, roots.options("library"), "operator:a")
    kept = [d for d in published(store, done).values() if isinstance(d, RelationshipDescriptor)]
    assert len(kept) == 1
    said = [(note.subject, note.message[0].model_dump()["text"]) for note in done.notes]
    held = "A foreign key the database declares is not kept: another foreign key of the table has "
    assert any(
        subject in ("c.a+b", "c.b+a") and text == held + "the same columns"
        for subject, text in said
    )


def test_a_snapshot_names_at_most_import_tables_skipped_relations(roots: Any) -> None:
    views = "".join(f"CREATE VIEW v{n} AS SELECT 1 AS one;" for n in range(5))
    path = sqlite_file(roots.inside, "CREATE TABLE t (id INTEGER);" + views)
    status = os.stat(path)
    target = Target("sqlite", path=str(path), identity=(status.st_dev, status.st_ino))
    snapshot = read_snapshot(target, ImportLimits(import_tables=2))
    assert ([skipped.name for skipped in snapshot.skipped], snapshot.more_skipped) == (
        ["v0", "v1"],
        3,
    )


def _status(uid: int) -> os.stat_result:
    return os.stat_result((0o100600, 1, 1, 1, uid, 0, 0, 0, 0, 0))


@pytest.mark.parametrize("mine", [0, 4321])
def test_only_the_servers_user_or_root_owns_a_connection_file(
    monkeypatch: pytest.MonkeyPatch, mine: int
) -> None:
    monkeypatch.setattr(os, "geteuid", lambda: mine)
    assert databases._owned(_status(mine))
    assert databases._owned(_status(0))
    assert not databases._owned(_status(mine + 1))
    assert not databases._owned(_status(65534))


@pytest.mark.parametrize("whose", ["directory", "file"])
def test_a_connection_file_or_its_directory_that_another_user_owns_is_refused(
    roots: Any, connect: Any, monkeypatch: pytest.MonkeyPatch, whose: str
) -> None:
    shared = roots.inside / "shared"
    shared.mkdir(mode=0o755)
    shared.chmod(0o1777)
    path = sqlite_file(shared)
    path.chmod(0o600)
    theirs = shared if whose == "directory" else path
    if os.geteuid() == 0:
        os.chown(theirs, 65534, -1)
    elif whose == "directory":
        monkeypatch.setattr(os, "geteuid", lambda: os.stat(shared).st_uid + 1)
    else:
        pytest.skip("only root can give a file to another user")
    with pytest.raises(ImportRefused) as refused:
        connect("sqlite", path)
    refusal = refusal_of(refused)
    assert refusal.code == RefusalCode.PATH_NOT_CONFINED
    named = "shared" if whose == "directory" else "shared/library.sqlite"
    assert DataSegment(data=named) in refusal.message


def test_the_server_running_out_of_memory_describing_a_snapshot_names_decoded_bytes(
    roots: Any, store: Store, connect: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    def exhausted(*_: object) -> None:
        raise MemoryError

    monkeypatch.setattr(DatabaseImporter, "import_database", exhausted)
    source = connect("sqlite", sqlite_file(roots.inside))
    with pytest.raises(ImportRefused) as refused:
        import_dataset(store, source, roots.options("library"), "operator:a")
    refusal = refusal_of(refused)
    assert refusal.limit is not None
    assert refusal.limit.name == "decoded_bytes"


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


def test_a_duckdb_file_time_with_a_time_zone_is_refused_naming_its_column(
    roots: Any, store: Store, connect: Any
) -> None:
    script = (
        "CREATE TABLE clocks (id INTEGER PRIMARY KEY, t TIMETZ); "
        "INSERT INTO clocks VALUES (1, TIMETZ '10:11:12+02'), (2, TIMETZ '10:11:12-05');"
    )
    source = connect("duckdb", duckdb_file(roots.inside, script))
    with pytest.raises(ImportRefused) as refused:
        import_dataset(store, source, roots.options("library"), "operator:a")
    refusal = refusal_of(refused)
    assert refusal.code == RefusalCode.UNSUPPORTED_FORMAT
    assert DataSegment(data="clocks") in refusal.message
    assert DataSegment(data="t") in refusal.message


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


_PLANTED = """
from aibi.core.importers.snapshot import _session, extension_path
from aibi.core.schema.limits import ImportLimits
path = str(extension_path("postgres"))
plain = duckdb.connect()
plain.load_extension(path)
plain.execute("CREATE PERSISTENT SECRET __default_postgres (TYPE postgres, PORT 5439)")
planted = plain.execute("SELECT count(*) FROM duckdb_secrets()").fetchone()[0]
plain.close()
session = _session(ImportLimits())
session.load_extension(path)
print(json.dumps([planted, session.execute("SELECT count(*) FROM duckdb_secrets()").fetchone()[0]]))
"""


def test_a_persistent_secret_in_the_workers_directory_is_never_read_by_a_snapshot(
    tmp_path: Path,
) -> None:
    environ = {key: value for key, value in os.environ.items() if key != "HOME"}
    found = json.loads(in_duckdb(_PLANTED, None, cwd=tmp_path, env=environ))
    assert (tmp_path / ".duckdb" / "stored_secrets").is_dir()
    assert found == [1, 0]


@pytest.mark.parametrize(
    ("comment", "code", "limit"),
    [
        ("x" * (MAX_TEXT + 1), RefusalCode.LIMIT_EXCEEDED, "text_characters"),
        ("As printed￾", RefusalCode.UNPARSEABLE_SOURCE, None),
        ("As printed\x1b[2J", RefusalCode.UNPARSEABLE_SOURCE, None),
        ("As printed\x0b", RefusalCode.UNPARSEABLE_SOURCE, None),
        ("As printed\x7f", RefusalCode.UNPARSEABLE_SOURCE, None),
        ("As printed\x85", RefusalCode.UNPARSEABLE_SOURCE, None),
    ],
    ids=["long", "noncharacter", "control", "vertical tab", "delete", "next line"],
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


def test_a_comment_of_exactly_a_texts_length_is_a_definition(
    roots: Any, store: Store, connect: Any
) -> None:
    comment = "x" * MAX_TEXT
    script = f"CREATE TABLE t (id INTEGER); COMMENT ON TABLE t IS '{comment}';"
    source = connect("duckdb", duckdb_file(roots.inside, script))
    found = published(store, import_dataset(store, source, roots.options("library"), "operator:a"))
    assert found["t"].definition == comment


def test_a_schema_whose_name_is_not_unicode_text_is_listed_escaped(
    roots: Any, store: Store, connect: Any
) -> None:
    path = duckdb_file(roots.inside, DUCKDB_LIBRARY + 'CREATE SCHEMA "odd￾";')
    source = connect("duckdb", path, schema="missing")
    with pytest.raises(ImportRefused) as refused:
        import_dataset(store, source, roots.options("library"), "operator:a")
    refusal = refusal_of(refused)
    assert refusal.code == RefusalCode.INVALID_VALUE
    assert DataSegment(data="odd\\ufffe") in refusal.alternatives


def test_a_link_beside_a_duckdb_file_is_refused_before_duckdb_opens_it(
    roots: Any, store: Store, connect: Any
) -> None:
    path = duckdb_file(roots.inside, DUCKDB_LIBRARY)
    blocking = roots.outside / "blocking"
    os.mkfifo(blocking)
    (roots.inside / "library.duckdb.wal").symlink_to(blocking)
    options = roots.options("library", limits=ImportLimits(reader_seconds=20))
    with pytest.raises(ImportRefused) as refused:
        import_dataset(store, connect("duckdb", path), options, "operator:a")
    assert refusal_of(refused).code == RefusalCode.PATH_NOT_CONFINED


# --- Servers: resolving their URLs, and what a failure says -------------------------------------


@pytest.mark.parametrize(
    ("kind", "url", "location"),
    [
        (
            "postgres",
            "postgresql://reader:pw@db.example.org:6543/lending?sslmode=require",
            "public",
        ),
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
        "postgresql://reader:hunter2@db.example.org/lending/more",
        "POSTGRESQL://reader:hunter2@db.example.org/lending",
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


@pytest.mark.parametrize(
    ("parameter", "said"),
    [
        ("host", "sets host,"),
        ("hostaddr", "sets hostaddr,"),
        ("dbname", "sets dbname,"),
        ("service", "sets service,"),
        ("options", "sets options,"),
        ("%68ost", "does not name plainly"),
        ("ho+st", "does not name plainly"),
    ],
)
def test_a_server_url_whose_query_sets_a_parameter_not_allowed_is_refused_naming_it(
    connect: Any, parameter: str, said: str
) -> None:
    url = f"postgresql://reader:hunter2@db.example.org/lending?sslmode=require&{parameter}=decoy"
    with pytest.raises(ImportRefused) as refused:
        connect("postgres", url_env="LIBRARY_URL", environ={"LIBRARY_URL": url})
    refusal = refusal_of(refused)
    assert refusal.code == RefusalCode.INVALID_VALUE
    assert said in shown(refusal)
    assert "hunter2" not in shown(refusal)
    assert "decoy" not in shown(refusal)


@pytest.mark.parametrize(
    ("url", "said"),
    [
        ("postgresql://postgres:hunter2@127.0.0.2:55432,@127.0.0.1/aibi", "exactly one @"),
        ("postgresql://postgres:hunter2@%2Ftmp%2Fpgs@127.0.0.1:55432/aibi", "exactly one @"),
        ("postgresql://127.0.0.2/aibi", "exactly one @"),
        ("postgresql://postgres:hunter2@127.0.0.2,127.0.0.1/aibi", "not one name or address"),
        ("postgresql://postgres:hunter2@%2Ftmp%2Fpgs/aibi", "not one name or address"),
        ("postgresql://postgres:hunter2@[fe80::1%25eth0]/aibi", "not one name or address"),
    ],
)
def test_a_postgres_url_whose_authority_is_not_one_user_and_one_host_is_refused(
    connect: Any, url: str, said: str
) -> None:
    with pytest.raises(ImportRefused) as refused:
        connect("postgres", url_env="LIBRARY_URL", environ={"LIBRARY_URL": url})
    refusal = refusal_of(refused)
    assert refusal.code == RefusalCode.INVALID_VALUE
    assert said in shown(refusal)
    for secret in ("hunter2", "127.0.0.1", "127.0.0.2", "pgs"):
        assert secret not in shown(refusal)


@pytest.mark.parametrize(
    ("kind", "url"),
    [
        (
            "postgres",
            "postgresql://tester:pw@decoy.example:5439/decoydb#?host=127.0.0.1&dbname=db%23",
        ),
        ("postgres", "postgresql://reader:hunter2@decoy.example/db#?host=127.0.0.1"),
        ("postgres", "postgresql://reader:hunter2@decoy.example/db#?dbname=other"),
        ("mysql", "mysql://reader:hunter2@decoy.example/decoy#x"),
        ("postgres", "postgresql://u:hunter2@decoy.example/db?application_name=x#&host=127.0.0.1"),
    ],
)
def test_a_server_url_holding_a_hash_is_refused_without_its_value(
    connect: Any, kind: Kind, url: str
) -> None:
    with pytest.raises(ImportRefused) as refused:
        connect(kind, url_env="LIBRARY_URL", environ={"LIBRARY_URL": url})
    refusal = refusal_of(refused)
    assert refusal.code == RefusalCode.INVALID_VALUE
    assert "#" in shown(refusal)
    for secret in ("pw", "hunter2", "decoy", "127.0.0.1"):
        assert secret not in shown(refusal)


def test_a_plus_in_a_server_urls_query_is_itself_in_the_connection_built(connect: Any) -> None:
    url = "postgresql://reader:hunter2@db.example.org/lending?application_name=a+b%27c"
    resolved = connect("postgres", url_env="LIBRARY_URL", environ={"LIBRARY_URL": url})
    assert resolved.source.location == "db.example.org/lending/public"
    assert _conninfo(resolved.target.dsn)["application_name"] == "a+b'c"


_LONG_LABEL = "a" * 63
_PG = "postgresql://reader:hunter2@db.example.org"


@pytest.mark.parametrize(
    ("kind", "url", "said"),
    [
        (
            "postgres",
            f"postgresql://reader:hunter2@{'.'.join([_LONG_LABEL] * 4)}/db",
            "longer than",
        ),
        ("postgres", f"postgresql://reader:hunter2@{'a' * 64}.org/db", "outside the grammar"),
        ("postgres", "postgresql://u%00:hunter2@db.example.org/db", "user has a NUL"),
        ("postgres", "postgresql://reader:p%00@db.example.org/db", "password has a NUL"),
        ("postgres", f"{_PG}/db%00", "database has a NUL"),
        ("postgres", f"{_PG}/db?sslmode=%zz", "begins no percent-escape"),
        ("postgres", f"{_PG}/db?sslmode=%C3%28", "not UTF-8"),
        ("postgres", f"{_PG}/db?sslmode=a&sslmode=b", "sets sslmode twice"),
        ("postgres", f"{_PG}/db?application_name=a b", "does not hold"),
        ("postgres", f"{_PG}/db?application_name=a\x7fb", "does not hold"),
        ("postgres", f"{_PG}/db?application_name=a\u00e9", "does not hold"),
        ("postgres", f"{_PG}:0/db", "port is not from 1 to 65535"),
        ("postgres", f"{_PG}:65536/db", "port is not from 1 to 65535"),
        ("postgres", f"{_PG}/d%2Fb", "holds a /"),
        ("postgres", f"{_PG}/db?SSLMODE=require", "does not name plainly"),
        ("postgres", f"{_PG}/db?sslmode", "gives sslmode no value"),
        ("postgres", f"{_PG}/db%1B%5B31mred", "control character"),
        ("postgres", f"{_PG}/db%C2%85", "control character"),
        ("postgres", f"{_PG}/db%7F", "control character"),
        ("postgres", f"{_PG}/{'d' * 64}", "63 bytes"),
        ("postgres", f"{_PG}/{'d' * 63}EXTRA%1B%5B31mRED", "control character"),
        ("postgres", f"{_PG}/{'%C3%A9' * 32}", "63 bytes"),
        ("postgres", f"postgresql://{'u' * 64}:hunter2@db.example.org/db", "user is longer"),
        ("mysql", f"mysql://reader:hunter2@db.example.org/{'d' * 65}", "64 characters"),
        ("mysql", "mysql://reader:hunter2@db.example.org/db%09", "control character"),
        ("postgres", "postgresql://reader:hunter2@127.1/db", "dotted decimal"),
        ("postgres", "postgresql://reader:hunter2@0177.0.0.1/db", "dotted decimal"),
        ("mysql", "mysql://reader:hunter2@0177.0.0.1/db", "dotted decimal"),
        ("postgres", "postgresql://reader:hunter2@2130706433/db", "dotted decimal"),
        ("postgres", "postgresql://reader:hunter2@0x7f.0.0.1/db", "dotted decimal"),
        ("postgres", "postgresql://reader:hunter2@127.0.0.0x1/db", "dotted decimal"),
        ("postgres", "postgresql://reader:hunter2@1.2.3.4.5/db", "dotted decimal"),
        ("postgres", "postgresql://reader:hunter2@db.example.123/db", "dotted decimal"),
        ("postgres", "postgresql://reader:hunter2@[fe80::1::2]/db", "not an IPv6 address"),
        ("mysql", "mysql://reader:hunter2@db.example.org/d%60b", "cannot quote safely"),
        ("mysql", "mysql://reader:hunter2@db.example.org/d%5Cb", "cannot quote safely"),
        ("mysql", "mysql://reader:hunter2@db.example.org/d%27b", "cannot quote safely"),
        ("mysql", "mysql://reader:hunter2@db.example.org/d%E2%80%8Bb", "cannot quote safely"),
        ("mysql", "mysql://reader:hunter2@localhost:3309/db", "host is localhost"),
        ("mysql", "mysql://reader:hunter2@LocalHost/db", "host is localhost"),
        ("mysql", "mysql://reader:hunter2@localhost/db?ssl_mode=REQUIRED", "host is localhost"),
        ("mysql", "mysql://reader:a%3Fb@db.example.org/db", "password has a ?"),
        ("mysql", "mysql://reader:a%3Fport%3D3310@db.example.org/db", "password has a ?"),
        ("mysql", "mysql://re%3Fader:hunter2@db.example.org/db", "user has a ?"),
        ("mysql", "mysql://reader:hunter2@db.example.org/d%3Fb", "database has a ?"),
        (
            "mysql",
            "mysql://reader:hunter2@db.example.org/db?ssl_cipher=a%3Fsocket%3Dx",
            "parameter ssl_cipher has a ?",
        ),
    ],
)
def test_a_server_url_outside_the_grammar_is_refused_saying_why_without_its_values(
    kind: urls.ServerKind, url: str, said: str
) -> None:
    with pytest.raises(urls.UrlError) as refused:
        urls.parse(kind, url)
    assert said in str(refused.value)
    for secret in ("hunter2", "example", "reader", "127", "0177"):
        assert secret not in str(refused.value)


@pytest.mark.parametrize(
    ("kind", "url", "host", "database"),
    [
        ("postgres", "postgresql://r@DB.Example.ORG/db", "db.example.org", "db"),
        ("postgres", "postgresql://r@[0:0::1]/db", "::1", "db"),
        ("postgres", "postgresql://r@[::FFFF:127.0.0.1]/db", "::ffff:127.0.0.1", "db"),
        ("postgres", "postgresql://r@10.0.0.1/db", "10.0.0.1", "db"),
        ("mysql", "mysql://r@10.0.0.1/db", "10.0.0.1", "db"),
        ("postgres", "postgresql://r@0x7f.example.org/db", "0x7f.example.org", "db"),
        ("postgres", f"postgresql://r@{'.'.join(['a' * 63] * 3)}.{'a' * 61}/db", None, "db"),
        ("postgres", f"postgresql://r@h/{'d' * 63}", "h", "d" * 63),
        ("postgres", f"postgresql://r@h/{'%C3%A9' * 31}d", "h", "é" * 31 + "d"),
        ("postgres", f"postgresql://{'u' * 63}@h/db", "h", "db"),
        ("mysql", f"mysql://r@h/{'%C3%A9' * 64}", "h", "é" * 64),
        ("mysql", "mysql://r@h/d%22b%20%24c", "h", 'd"b $c'),
        ("mysql", "mysql://r@localhost/db?socket=%2Frun%2Fmysqld%2Fmysqld.sock", "localhost", "db"),
        ("mysql", "mysql://r@127.0.0.1/db", "127.0.0.1", "db"),
        ("postgres", "postgresql://r:a%3Fb@localhost/d%3Fb", "localhost", "d?b"),
        ("postgres", "postgresql://r@h/d%27b%5Cc%60", "h", "d'b\\c`"),
    ],
)
def test_a_server_urls_host_is_recorded_in_its_canonical_form_and_a_name_up_to_the_servers_length(
    kind: urls.ServerKind, url: str, host: str | None, database: str
) -> None:
    read = urls.parse(kind, url)
    assert read.host == (host or read.host.lower())
    assert read.database == database
    assert _conninfo(read.connection())["host"] == read.host


@pytest.mark.parametrize("kind", ["postgres", "mysql"])
@pytest.mark.parametrize(
    ("user", "password"),
    [
        ("a\\", "\\\\\\"),
        ("we ird'u\\", "a\\b'c\"d e=f"),
        ('q"u\\ =x', 'it\'s "x" = y'),
        ("nl", "a\nb\\"),
        ("empty", ""),
        ("none", None),
    ],
)
def test_a_user_and_password_of_quotes_and_backslashes_reach_the_driver_as_written(
    kind: urls.ServerKind, user: str, password: str | None
) -> None:
    quoted = urllib.parse.quote
    scheme = "postgresql" if kind == "postgres" else "mysql"
    secret = "" if password is None else ":" + quoted(password, safe="")
    read = urls.parse(kind, f"{scheme}://{quoted(user, safe='')}{secret}@h:1/d")
    built = _conninfo(read.connection())
    assert (built["user"], built.get("password")) == (user, password)
    assert (built["host"], built["port"]) == ("h", "1")


def test_a_postgres_connection_resolves_names_in_pg_catalog_and_refuses_row_security() -> None:
    built = _conninfo(urls.parse("postgres", "postgresql://r:p@h/d").connection())
    assert built["options"] == urls.POSTGRES_OPTIONS
    settings = {}
    for part in built["options"].removeprefix("-c ").split(" -c "):
        key, _, value = part.partition("=")
        settings[key] = value
    assert settings == {
        "search_path": "pg_catalog,pg_temp",
        "row_security": "off",
        "DateStyle": "ISO,YMD",
        "IntervalStyle": "postgres",
        "TimeZone": "UTC",
        "client_encoding": "UTF8",
        "extra_float_digits": "3",
        "bytea_output": "hex",
        "lc_numeric": "C",
        "lc_monetary": "C",
        "quote_all_identifiers": "off",
    }
    assert "options" not in _conninfo(urls.parse("mysql", "mysql://r:p@h/d").connection())


def _conninfo(dsn: str) -> dict[str, str]:
    """A ``keyword=value`` connection string read as libpq and the MySQL scanner read theirs:
    a value quoted by ``'`` or ``"``, a backslash escaping the next character."""
    found: dict[str, str] = {}
    at = 0
    while at < len(dsn):
        if dsn[at] == " ":
            at += 1
            continue
        equals = dsn.index("=", at)
        key, quote, at = dsn[at:equals], dsn[equals + 1], equals + 2
        assert quote in "'\""
        value: list[str] = []
        while dsn[at] != quote:
            if dsn[at] == "\\":
                at += 1
            value.append(dsn[at])
            at += 1
        found[key] = "".join(value)
        at += 1
    return found


_LABELS = st.from_regex(r"[a-z]([a-z0-9-]{0,8}[a-z0-9])?", fullmatch=True)
_HOSTS = st.one_of(
    st.lists(_LABELS, min_size=1, max_size=3).map(".".join),
    st.ip_addresses(v=4).map(str),
    st.ip_addresses(v=6).map(lambda address: f"[{address}]"),
)
_TEXT = st.text(
    st.characters(blacklist_categories=("Cs",), blacklist_characters="\x00"),
    min_size=1,
    max_size=12,
)
_NAMES = st.text(
    st.characters(blacklist_categories=("Cs", "Cc"), blacklist_characters="/"),
    min_size=1,
    max_size=12,
)


@given(
    kind=st.sampled_from(["postgres", "mysql"]),
    user=_TEXT,
    password=st.none() | _TEXT,
    host=_HOSTS,
    port=st.none() | st.integers(1, 65535),
    database=_NAMES,
    name=st.none() | _TEXT,
)
@example(kind="postgres", user="a\\", password="'\\", host="h", port=None, database="d'b", name="x")
@example(kind="mysql", user='u"\\', password="", host="h", port=1, database='d"b $', name="\n")
@example(
    kind="postgres", user="u", password="a\nb", host="[::1]", port=None, database="é", name=None
)
def test_the_connection_built_reads_the_host_port_and_database_the_provenance_records(
    kind: str,
    user: str,
    password: str | None,
    host: str,
    port: int | None,
    database: str,
    name: str | None,
) -> None:
    quoted = urllib.parse.quote
    scheme = "postgresql" if kind == "postgres" else "mysql"
    secret = "" if password is None else ":" + quoted(password, safe="")
    at = "" if port is None else f":{port}"
    query = ""
    if name is not None:
        key = "application_name" if kind == "postgres" else "ssl_cipher"
        query = f"?{key}={quoted(name, safe='')}"
    url = (
        f"{scheme}://{quoted(user, safe='')}{secret}@{host}{at}/{quoted(database, safe='')}{query}"
    )
    assume(kind != "mysql" or urls.mysql_quotable(database, constant=True))
    assume(kind != "mysql" or host != "localhost")
    assume(kind != "mysql" or "?" not in "".join([user, password or "", database, name or ""]))
    connection = Connection("library", cast(Kind, kind), url_env="U")
    resolved = resolve(connection, cast(Any, None), {"U": url})
    built = _conninfo(cast(str, resolved.target.dsn))
    recorded_host, recorded_database, _ = resolved.source.location.split("/")
    assert built["host"] == recorded_host
    assert built["dbname" if kind == "postgres" else "database"] == recorded_database == database
    assert built.get("port") == (None if port is None else str(port))
    assert built["user"] == user
    assert built.get("password") == password


_ISOLATION = """
from aibi.core.importers.errors import ImportRefused
from aibi.core.importers.snapshot import _mysql_isolation

class Session:
    def execute(self, sql, parameters):
        if "tx_isolation" in parameters["query"] or given == "":
            raise duckdb.IOException("Unknown system variable")
        return self

    def fetchall(self):
        return [(given,)]

try:
    _mysql_isolation(Session())
    print("read")
except ImportRefused as refused:
    print(refused.refusals[0].code, json.dumps(refused.refusals[0].model_dump(mode="json")))
"""


@pytest.mark.parametrize(
    ("level", "said"),
    [
        ("REPEATABLE-READ", "read"),
        ("SERIALIZABLE", "read"),
        ("READ-COMMITTED", "UNSUPPORTED_FORMAT"),
        ("READ-UNCOMMITTED", "UNSUPPORTED_FORMAT"),
        ("", "UNPARSEABLE_SOURCE"),
    ],
)
def test_a_mysql_session_whose_transactions_read_no_snapshot_is_refused(
    level: str, said: str
) -> None:
    found = in_duckdb(_ISOLATION, level).strip()
    assert found.partition(" ")[0] == said
    if not level:
        assert "IOException" in found


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


_ATTACHED = """
from aibi.core.importers.snapshot import extension_path
from aibi.core.importers.urls import parse
kind, url, statements, more, alone = given
session = duckdb.connect()
session.load_extension(str(extension_path(kind)))
written = " ".join([parse(kind, url).connection(), *more]).replace("'", "''")
session.execute(f"ATTACH '{written}' AS target (TYPE {kind})")
call = f"CALL {kind}_execute('target', $sql{', use_transaction := false' if alone else ''})"
"""
"""A session of a fresh interpreter with the test's server attached as ``target``, writable, by
the connection string the server builds from the URL (``urls``) and the more of it given, and
``call``, which runs a statement there, outside a transaction when ``alone`` (Postgres only)."""
_EXECUTE = (
    _ATTACHED
    + """
for statement in statements:
    session.execute(call, {"sql": statement})
session.close()
"""
)


def _execute(kind: Kind, url: str, statements: list[str], *more: str, alone: bool = False) -> None:
    in_duckdb(_EXECUTE, [kind, url, statements, list(more), alone])


@pytest.fixture
def postgres_schema() -> Iterator[tuple[str, str]]:
    url = _server_url("AIBI_TEST_POSTGRES_URL")
    schema = f"aibi_{secrets.token_hex(4)}"
    _execute("postgres", url, [f"CREATE SCHEMA {schema}"])
    try:
        _fill_library(url, schema)
        yield url, schema
    finally:
        _execute("postgres", url, [f"DROP SCHEMA {schema} CASCADE"])


def _fill_library(url: str, schema: str) -> None:
    _execute(
        "postgres",
        url,
        [
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
    roots: Any, store: Store, connect: Any, mysql_tables: tuple[str, list[str]]
) -> None:
    url, dropping = mysql_tables
    dropping.extend(
        [
            "DROP TABLE IF EXISTS aibi_authors",
            "DROP TABLE IF EXISTS aibi_books",
            "DROP VIEW IF EXISTS aibi_named",
        ]
    )
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
    source = connect("mysql", url_env="LIBRARY_URL", environ={"LIBRARY_URL": url})
    found = published(store, import_dataset(store, source, roots.options("library"), "operator:a"))
    assert {"aibi_authors", "aibi_books"} <= set(found)
    assert "aibi_named" not in found
    assert found["aibi_authors"].definition == "People who wrote a book"
    assert status(found["rel:aibi_books.author"], "/fields/parent_table") == "imported"


_WRITE = (
    _ATTACHED
    + """
print("writing", flush=True)
number = 1_000_000
while True:
    for statement in statements:
        session.execute(call, {"sql": statement.format(number)})
    number += 1
"""
)


@contextmanager
def _writing(kind: Kind, url: str, statements: list[str]) -> Iterator[None]:
    """A fresh interpreter that runs ``statements``, each a template of one number, for one
    number after another, each statement committed on its own, until the block ends."""
    with subprocess.Popen(
        [sys.executable, "-c", f"import duckdb, json, sys\ngiven = json.load(sys.stdin)\n{_WRITE}"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    ) as writer:
        try:
            assert writer.stdin is not None
            assert writer.stdout is not None
            writer.stdin.write(json.dumps([kind, url, statements, [], False]))
            writer.stdin.close()
            ready, _, _ = select.select([writer.stdout], [], [], 120)
            assert ready, "the writer did not start"
            assert writer.stdout.readline().strip() == "writing"
            yield
        finally:
            writer.kill()


_ROWS = 50_000


@pytest.mark.databases
def test_a_postgres_snapshot_of_a_database_being_written_sees_one_state_of_it(
    roots: Any, store: Store, connect: Any, postgres_empty: tuple[str, str]
) -> None:
    url, schema = postgres_empty
    _execute(
        "postgres",
        url,
        [
            f"CREATE TABLE {schema}.a_parents (id bigint PRIMARY KEY)",
            f"CREATE TABLE {schema}.b_children (id bigint PRIMARY KEY, "
            f"parent bigint REFERENCES {schema}.a_parents (id))",
            f"INSERT INTO {schema}.a_parents SELECT n FROM generate_series(1, {_ROWS}) AS n",
            f"INSERT INTO {schema}.b_children SELECT n, n FROM generate_series(1, {_ROWS}) AS n",
        ],
    )
    written = [
        f"INSERT INTO {schema}.a_parents VALUES ({{0}})",
        f"INSERT INTO {schema}.b_children VALUES ({{0}}, {{0}})",
    ]
    source = connect("postgres", url_env="LIBRARY_URL", schema=schema, environ={"LIBRARY_URL": url})
    with _writing("postgres", url, written):
        for number in range(2):
            import_dataset(store, source, roots.options(f"ledger{number}"), "operator:a")


@pytest.mark.databases
def test_a_postgres_numeric_is_read_as_its_exact_text_nan_and_money_too(
    roots: Any, store: Store, connect: Any, postgres_empty: tuple[str, str]
) -> None:
    url, schema = postgres_empty
    _execute(
        "postgres",
        url,
        [
            f"CREATE DOMAIN {schema}.wide AS numeric(50, 2)",
            f"CREATE TABLE {schema}.prices (id int PRIMARY KEY, rate numeric(10, 2), "
            f"price money, wide {schema}.wide)",
            f"INSERT INTO {schema}.prices VALUES (1, 'NaN', '12.34', 1.5), (2, 1.5, '-3', NULL)",
        ],
    )
    imported, _ = _postgres_import(store, roots, connect, url, schema)
    prices = imported.result.sources["prices"]
    assert isinstance(prices, TypedSource)
    assert prices.rows == ((1, "NaN", "12.34", "1.50"), (2, "1.50", "-3.00", None))


@pytest.mark.databases
def test_a_postgres_numeric_of_a_negative_scale_or_one_over_its_precision_is_read_as_text(
    roots: Any, store: Store, connect: Any, postgres_empty: tuple[str, str]
) -> None:
    url, schema = postgres_empty
    try:
        _execute(
            "postgres",
            url,
            [
                f"CREATE TABLE {schema}.odd (id int PRIMARY KEY, round numeric(5, -3), tiny "
                "numeric(3, 5))"
            ],
        )
    except subprocess.CalledProcessError:
        pytest.skip("this Postgres has neither negative scales nor scales over the precision")
    _execute("postgres", url, [f"INSERT INTO {schema}.odd VALUES (1, 12345, 0.00123)"])
    imported, _ = _postgres_import(store, roots, connect, url, schema)
    odd = imported.result.sources["odd"]
    assert isinstance(odd, TypedSource)
    assert odd.rows == ((1, "12000", "0.00123"),)


@pytest.mark.databases
def test_a_postgres_foreign_key_to_another_schemas_table_of_a_read_tables_name_is_not_kept(
    roots: Any, store: Store, connect: Any, postgres_empty: tuple[str, str]
) -> None:
    url, schema = postgres_empty
    _execute(
        "postgres",
        url,
        [
            f"CREATE TABLE {schema}_other.authors (id int PRIMARY KEY)",
            f"CREATE TABLE {schema}.authors (id int PRIMARY KEY)",
            f"CREATE TABLE {schema}.books (id int PRIMARY KEY, "
            f"author int REFERENCES {schema}_other.authors (id))",
            f"CREATE TABLE {schema}.bare ()",
        ],
    )
    imported, found = _postgres_import(store, roots, connect, url, schema)
    assert not any(isinstance(d, RelationshipDescriptor) for d in found.values())
    said = [part for note in imported.result.notes for part in note.message]
    assert TextSegment(text=": a base table without columns, which is not read") in said


@pytest.fixture
def mysql_tables() -> Iterator[tuple[str, list[str]]]:
    """A database of the test's own, made beside the test database and dropped after the test
    (the test database itself when its user cannot make one), and the statements that drop what
    the test made in it, which run in the reverse order after the test."""
    url = _server_url("AIBI_TEST_MYSQL_URL")
    name = f"aibi_{secrets.token_hex(4)}"
    try:
        _execute("mysql", url, [f"CREATE DATABASE `{name}`"])
    except subprocess.CalledProcessError:
        name = ""
    here = _rewritten("mysql", url, database=name) if name else url
    dropping: list[str] = []
    try:
        yield here, dropping
    finally:
        _execute("mysql", here, list(reversed(dropping)))
        if name:
            _execute("mysql", url, [f"DROP DATABASE `{name}`"])


def _mysql_import(store: Store, roots: Any, connect: Any, url: str) -> Any:
    source = connect("mysql", url_env="LIBRARY_URL", environ={"LIBRARY_URL": url})
    with store.pin() as pin:
        return build_import(store, pin, source, roots.options("library"))


def _mysql_refusal(store: Store, roots: Any, connect: Any, url: str) -> Refusal:
    with pytest.raises(ImportRefused) as refused:
        _mysql_import(store, roots, connect, url)
    return refusal_of(refused)


@pytest.mark.databases
def test_a_mysql_snapshot_of_a_database_being_written_sees_one_state_of_it(
    roots: Any, store: Store, connect: Any, mysql_tables: tuple[str, list[str]]
) -> None:
    url, dropping = mysql_tables
    digits = "(" + " UNION ALL ".join(f"SELECT {n} AS n" for n in range(10)) + ")"
    numbers = (
        f"SELECT 1 + a.n + 10 * b.n + 100 * c.n + 1000 * d.n + 10000 * e.n AS n FROM {digits} a, "
        f"{digits} b, {digits} c, {digits} d, {digits} e WHERE e.n < {_ROWS // 10_000}"
    )
    dropping.extend(["DROP TABLE IF EXISTS aibi_a_parents", "DROP TABLE IF EXISTS aibi_b_children"])
    _execute(
        "mysql",
        url,
        [
            "CREATE TABLE aibi_a_parents (id BIGINT PRIMARY KEY) ENGINE = InnoDB",
            "CREATE TABLE aibi_b_children (id BIGINT PRIMARY KEY, parent BIGINT, "
            "FOREIGN KEY (parent) REFERENCES aibi_a_parents (id)) ENGINE = InnoDB",
            f"INSERT INTO aibi_a_parents {numbers}",
            f"INSERT INTO aibi_b_children SELECT n, n FROM ({numbers}) AS numbers",
        ],
    )
    written = [
        "INSERT INTO aibi_a_parents VALUES ({0})",
        "INSERT INTO aibi_b_children VALUES ({0}, {0})",
    ]
    source = connect("mysql", url_env="LIBRARY_URL", environ={"LIBRARY_URL": url})
    with _writing("mysql", url, written):
        for number in range(2):
            import_dataset(store, source, roots.options(f"ledger{number}"), "operator:a")


@pytest.mark.databases
def test_a_mysql_column_is_read_as_it_declares_a_timestamp_in_utc(
    roots: Any, store: Store, connect: Any, mysql_tables: tuple[str, list[str]]
) -> None:
    url, dropping = mysql_tables
    dropping.append("DROP TABLE IF EXISTS aibi_kinds")
    _execute(
        "mysql",
        url,
        [
            "CREATE TABLE aibi_kinds (id INT PRIMARY KEY, flag TINYINT(1), stamp TIMESTAMP NULL, "
            "lasted TIME)",
            "INSERT INTO aibi_kinds VALUES (1, 5, "
            "CONVERT_TZ('2024-01-01 12:00:00', '+05:00', @@session.time_zone), '838:59:59')",
        ],
    )
    kinds = _mysql_import(store, roots, connect, url).result.sources["aibi_kinds"]
    assert isinstance(kinds, TypedSource)
    assert kinds.rows == ((1, 5, datetime(2024, 1, 1, 7, 0), "838:59:59"),)


@pytest.mark.databases
@pytest.mark.parametrize(
    ("statements", "named"),
    [
        (
            [
                "CREATE TABLE aibi_days (id INT PRIMARY KEY, day DATE)",
                "INSERT IGNORE INTO aibi_days VALUES (1, '2024-01-02'), (2, '0000-00-00')",
            ],
            ["aibi_days", "day"],
        ),
        (
            [
                "CREATE TABLE aibi_moments (id INT PRIMARY KEY, moment DATETIME)",
                "INSERT IGNORE INTO aibi_moments VALUES (1, '0000-00-00 00:00:00')",
            ],
            ["aibi_moments", "moment"],
        ),
        (
            [
                "CREATE TABLE aibi_stamps (id INT PRIMARY KEY, stamp TIMESTAMP NULL)",
                "INSERT IGNORE INTO aibi_stamps VALUES (1, '0000-00-00 00:00:00')",
            ],
            ["aibi_stamps", "stamp"],
        ),
        (
            ["CREATE TABLE aibi_sums (id INT PRIMARY KEY, total DECIMAL(50, 2))"],
            ["aibi_sums", "total"],
        ),
        (
            ["CREATE TABLE aibi_plain (id INT PRIMARY KEY) ENGINE = MyISAM"],
            ["aibi_plain"],
        ),
    ],
    ids=[
        "zero date",
        "zero datetime",
        "zero timestamp",
        "wide decimal",
        "engine without transactions",
    ],
)
def test_a_mysql_value_or_table_the_snapshot_cannot_keep_is_refused_naming_it(
    roots: Any,
    store: Store,
    connect: Any,
    mysql_tables: tuple[str, list[str]],
    statements: list[str],
    named: list[str],
) -> None:
    url, dropping = mysql_tables
    dropping.append(f"DROP TABLE IF EXISTS {named[0]}")
    _execute("mysql", url, statements)
    refusal = _mysql_refusal(store, roots, connect, url)
    assert refusal.code in (RefusalCode.UNPARSEABLE_SOURCE, RefusalCode.UNSUPPORTED_FORMAT)
    for name in named:
        assert DataSegment(data=name) in refusal.message


@pytest.mark.databases
def test_mysql_tables_whose_names_differ_only_in_case_are_refused_naming_both(
    roots: Any, store: Store, connect: Any, mysql_tables: tuple[str, list[str]]
) -> None:
    url, dropping = mysql_tables
    dropping.extend(["DROP TABLE IF EXISTS aibi_Cased", "DROP TABLE IF EXISTS aibi_cased"])
    try:
        _execute(
            "mysql",
            url,
            [
                "CREATE TABLE aibi_Cased (id INT PRIMARY KEY)",
                "CREATE TABLE aibi_cased (id INT PRIMARY KEY)",
            ],
        )
    except subprocess.CalledProcessError:
        pytest.skip("this MySQL folds the case of table names")
    refusal = _mysql_refusal(store, roots, connect, url)
    assert refusal.code == RefusalCode.UNSUPPORTED_FORMAT
    assert DataSegment(data="aibi_Cased") in refusal.message
    assert DataSegment(data="aibi_cased") in refusal.message


@pytest.mark.databases
def test_a_mysql_foreign_key_to_another_database_is_noted_and_not_kept(
    roots: Any, store: Store, connect: Any, mysql_tables: tuple[str, list[str]]
) -> None:
    url, dropping = mysql_tables
    other = f"aibi_{secrets.token_hex(4)}"
    try:
        _execute("mysql", url, [f"CREATE DATABASE {other}"])
    except subprocess.CalledProcessError:
        pytest.skip("the test's user cannot make a database")
    dropping.extend(
        [
            f"DROP DATABASE IF EXISTS {other}",
            "DROP TABLE IF EXISTS aibi_authors",
            "DROP TABLE IF EXISTS aibi_books",
        ]
    )
    _execute(
        "mysql",
        url,
        [
            f"CREATE TABLE {other}.aibi_authors (id INT PRIMARY KEY)",
            "CREATE TABLE aibi_authors (id INT PRIMARY KEY)",
            "CREATE TABLE aibi_books (id INT PRIMARY KEY, author INT, "
            f"FOREIGN KEY (author) REFERENCES {other}.aibi_authors (id))",
        ],
    )
    imported = _mysql_import(store, roots, connect, url)
    found = by_id(tuple(imported.result.descriptors))
    assert not any(isinstance(d, RelationshipDescriptor) for d in found.values())
    [note] = [note for note in imported.result.notes if note.subject == "aibi_books.author"]
    assert "its parent table is not read" in note.message[0].model_dump()["text"]


@pytest.mark.databases
def test_a_mariadb_sequence_is_skipped_and_a_system_versioned_table_read(
    roots: Any, store: Store, connect: Any, mysql_tables: tuple[str, list[str]]
) -> None:
    url, dropping = mysql_tables
    dropping.extend(["DROP SEQUENCE IF EXISTS aibi_counter", "DROP TABLE IF EXISTS aibi_kept"])
    try:
        _execute(
            "mysql",
            url,
            [
                "CREATE SEQUENCE aibi_counter",
                "CREATE TABLE aibi_kept (id INT PRIMARY KEY) WITH SYSTEM VERSIONING",
                "INSERT INTO aibi_kept VALUES (1)",
            ],
        )
    except subprocess.CalledProcessError:
        pytest.skip("this server is not MariaDB")
    imported = _mysql_import(store, roots, connect, url)
    kept = imported.result.sources["aibi_kept"]
    assert isinstance(kept, TypedSource)
    assert kept.rows == ((1,),)
    said = [part for note in imported.result.notes for part in note.message]
    assert TextSegment(text=": a sequence, not a base table") in said


@pytest.mark.databases
def test_a_mysql_decimal_of_38_digits_is_read_exactly(
    roots: Any, store: Store, connect: Any, mysql_tables: tuple[str, list[str]]
) -> None:
    url, dropping = mysql_tables
    dropping.append("DROP TABLE IF EXISTS aibi_exact")
    _execute(
        "mysql",
        url,
        [
            "CREATE TABLE aibi_exact (id INT PRIMARY KEY, total DECIMAL(38, 2))",
            "INSERT INTO aibi_exact VALUES (1, 123456789012345678901234567890123456.78)",
        ],
    )
    exact = _mysql_import(store, roots, connect, url).result.sources["aibi_exact"]
    assert isinstance(exact, TypedSource)
    assert exact.rows == ((1, "123456789012345678901234567890123456.78"),)


@pytest.mark.databases
@pytest.mark.parametrize(
    ("read", "other", "refused"),
    [("x", "X", True), ("cafe", "café", True), ("strasse", "straße", False)],
    ids=["case", "accent", "sharp s"],
)
def test_a_mysql_database_whose_name_the_servers_collation_equals_to_anothers_is_refused(
    roots: Any,
    store: Store,
    connect: Any,
    mysql_tables: tuple[str, list[str]],
    read: str,
    other: str,
    refused: bool,
) -> None:
    url, dropping = mysql_tables
    prefix = f"aibi_{secrets.token_hex(4)}_"
    mine, theirs = prefix + read, prefix + other
    dropping.extend([f"DROP DATABASE IF EXISTS `{mine}`", f"DROP DATABASE IF EXISTS `{theirs}`"])
    try:
        _execute("mysql", url, [f"CREATE DATABASE `{mine}`", f"CREATE DATABASE `{theirs}`"])
    except subprocess.CalledProcessError:
        pytest.skip("the test's user cannot make databases, or this MySQL folds their names")
    _execute("mysql", url, [f"CREATE TABLE `{mine}`.t (id INT PRIMARY KEY)"])
    elsewhere = url.rpartition("/")[0] + f"/{mine}"
    if not refused:
        found = _mysql_import(store, roots, connect, elsewhere).result.sources
        assert set(found) == {"t"}
        return
    refusal = _mysql_refusal(store, roots, connect, elsewhere)
    assert refusal.code == RefusalCode.UNSUPPORTED_FORMAT
    assert DataSegment(data=mine) in refusal.message
    assert DataSegment(data=theirs) in refusal.message
    assert "equal under the server's collation" in shown(refusal)


@pytest.mark.databases
def test_mysql_tables_whose_names_differ_only_in_unicode_case_are_refused_naming_both(
    roots: Any, store: Store, connect: Any, mysql_tables: tuple[str, list[str]]
) -> None:
    url, dropping = mysql_tables
    dropping.extend(["DROP TABLE IF EXISTS aibi_é", "DROP TABLE IF EXISTS aibi_É"])
    try:
        _execute(
            "mysql",
            url,
            [
                "CREATE TABLE aibi_é (id INT PRIMARY KEY)",
                "CREATE TABLE aibi_É (id INT PRIMARY KEY)",
            ],
        )
    except subprocess.CalledProcessError:
        pytest.skip("this MySQL folds the case of table names")
    refusal = _mysql_refusal(store, roots, connect, url)
    assert refusal.code == RefusalCode.UNSUPPORTED_FORMAT
    assert DataSegment(data="aibi_é") in refusal.message
    assert DataSegment(data="aibi_É") in refusal.message


_READ_COMMITTED = """
from aibi.core.importers import snapshot
from aibi.core.importers.errors import ImportRefused
from aibi.core.schema.limits import ImportLimits
from aibi.core.importers.databases import Connection, _server
snapshot._MYSQL_LEVELS = ("SELECT 'READ-COMMITTED'",)
target = _server(Connection("c", "mysql", url_env="U"), {"U": given[0]}).target
try:
    snapshot.read_snapshot(target, ImportLimits())
    print("read")
except ImportRefused as refused:
    print(refused.refusals[0].code)
"""


@pytest.mark.databases
def test_a_mysql_snapshot_is_refused_when_its_sessions_transactions_read_no_snapshot(
    mysql_tables: tuple[str, list[str]],
) -> None:
    url, _ = mysql_tables
    assert in_duckdb(_READ_COMMITTED, [url]).strip() == "UNSUPPORTED_FORMAT"


@pytest.mark.databases
@pytest.mark.parametrize("name", ["o'brien", "back\\slash"])
def test_a_postgres_schema_whose_name_holds_a_quote_or_a_backslash_is_read(
    roots: Any, store: Store, connect: Any, name: str
) -> None:
    url = _server_url("AIBI_TEST_POSTGRES_URL")
    schema = f"aibi_{secrets.token_hex(4)}_{name}"
    quoted = '"' + schema + '"'
    _execute("postgres", url, [f"CREATE SCHEMA {quoted}"])
    try:
        _execute(
            "postgres",
            url,
            [f"CREATE TABLE {quoted}.t (id int PRIMARY KEY)", f"INSERT INTO {quoted}.t VALUES (1)"],
        )
        imported, _ = _postgres_import(store, roots, connect, url, schema)
        read = imported.result.sources["t"]
        assert isinstance(read, TypedSource)
        assert read.rows == ((1,),)
    finally:
        _execute("postgres", url, [f"DROP SCHEMA {quoted} CASCADE"])


@pytest.mark.databases
def test_a_postgres_partition_of_another_schemas_table_is_noted_and_not_read(
    roots: Any, store: Store, connect: Any, postgres_empty: tuple[str, str]
) -> None:
    url, schema = postgres_empty
    _execute(
        "postgres",
        url,
        [
            f"CREATE TABLE {schema}_other.loans (id int, day date) PARTITION BY RANGE (day)",
            f"CREATE TABLE {schema}.loans_2024 PARTITION OF {schema}_other.loans "
            "FOR VALUES FROM ('2024-01-01') TO ('2025-01-01')",
            f"CREATE TABLE {schema}.kept (id int PRIMARY KEY)",
        ],
    )
    imported, _ = _postgres_import(store, roots, connect, url, schema)
    assert set(imported.result.sources) == {"kept"}
    said = [part for note in imported.result.notes for part in note.message]
    assert DataSegment(data="loans_2024") in said
    assert TextSegment(text=": a partition of a table of another schema, which is not read") in said


@pytest.mark.databases
def test_a_postgres_sub_partition_is_noted_by_the_schema_of_its_root(
    roots: Any, store: Store, connect: Any, postgres_empty: tuple[str, str]
) -> None:
    url, schema = postgres_empty
    other = f"{schema}_other"
    _execute(
        "postgres",
        url,
        [
            f"CREATE TABLE {schema}.top (id int, n int) PARTITION BY RANGE (id)",
            f"CREATE TABLE {other}.middle PARTITION OF {schema}.top FOR VALUES FROM (0) TO (100) "
            "PARTITION BY RANGE (n)",
            f"CREATE TABLE {schema}.leaf PARTITION OF {other}.middle FOR VALUES FROM (0) TO (10)",
            f"CREATE TABLE {other}.high (id int, n int) PARTITION BY RANGE (id)",
            f"CREATE TABLE {schema}.middle2 PARTITION OF {other}.high FOR VALUES FROM (0) TO (100) "
            "PARTITION BY RANGE (n)",
            f"CREATE TABLE {schema}.leaf2 PARTITION OF {schema}.middle2 "
            "FOR VALUES FROM (0) TO (10)",
            f"INSERT INTO {schema}.top VALUES (1, 1)",
        ],
    )
    imported, _ = _postgres_import(store, roots, connect, url, schema)
    top = imported.result.sources["top"]
    assert isinstance(top, TypedSource)
    assert top.rows == ((1, 1),)
    noted = {
        part.data
        for note in imported.result.notes
        for part in note.message
        if isinstance(part, DataSegment)
    }
    assert {"middle2", "leaf2"} <= noted
    assert "leaf" not in noted


@pytest.mark.databases
def test_a_postgres_partitioned_table_with_a_foreign_partition_is_noted_and_not_read(
    roots: Any, store: Store, connect: Any, postgres_empty: tuple[str, str]
) -> None:
    url, schema = postgres_empty
    server = f"{schema}_away"
    try:
        _execute(
            "postgres",
            url,
            [
                "CREATE EXTENSION IF NOT EXISTS postgres_fdw",
                f"CREATE SERVER {server} FOREIGN DATA WRAPPER postgres_fdw "
                "OPTIONS (dbname 'elsewhere')",
            ],
        )
    except subprocess.CalledProcessError:
        pytest.skip("postgres_fdw is not available to the test's user")
    try:
        _execute(
            "postgres",
            url,
            [
                f"CREATE TABLE {schema}.top (id int, v text) PARTITION BY RANGE (id)",
                f"CREATE TABLE {schema}.near PARTITION OF {schema}.top "
                "FOR VALUES FROM (0) TO (100)",
                f"CREATE FOREIGN TABLE {schema}.far PARTITION OF {schema}.top "
                f"FOR VALUES FROM (100) TO (200) SERVER {server}",
                f"CREATE TABLE {schema}.kept (id int PRIMARY KEY)",
            ],
        )
        imported, _ = _postgres_import(store, roots, connect, url, schema)
    finally:
        _execute("postgres", url, [f"DROP SERVER {server} CASCADE"])
    assert set(imported.result.sources) == {"kept"}
    said = [part for note in imported.result.notes for part in note.message]
    assert DataSegment(data="top") in said
    assert DataSegment(data="far") in said


@pytest.mark.databases
def test_a_postgres_snapshot_reads_no_libpq_default_from_the_environment(
    roots: Any,
    store: Store,
    connect: Any,
    postgres_empty: tuple[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url, schema = postgres_empty
    _execute("postgres", url, [f"CREATE TABLE {schema}.kept (id int PRIMARY KEY)"])
    monkeypatch.setenv("PGHOSTADDR", "192.0.2.1")
    monkeypatch.setenv("PGCONNECT_TIMEOUT", "2")
    imported, _ = _postgres_import(store, roots, connect, url, schema)
    assert set(imported.result.sources) == {"kept"}


@pytest.mark.databases
def test_a_postgres_array_of_numerics_is_read_as_a_list_of_their_exact_text(
    roots: Any, store: Store, connect: Any, postgres_empty: tuple[str, str]
) -> None:
    url, schema = postgres_empty
    _execute(
        "postgres",
        url,
        [
            f"CREATE TABLE {schema}.rates (id int PRIMARY KEY, rates numeric(6, 2)[], "
            "prices money[])",
            f"INSERT INTO {schema}.rates VALUES (1, '{{1.5,NaN}}', '{{12.34}}')",
        ],
    )
    imported, _ = _postgres_import(store, roots, connect, url, schema)
    rates = imported.result.sources["rates"]
    assert isinstance(rates, TypedSource)
    [(_, numbers, prices)] = rates.rows
    assert (json.loads(str(numbers)), json.loads(str(prices))) == (["1.50", "NaN"], ["12.34"])


# --- What a server's session opened, behind the marker -------------------------------------------


_VARIABLES = {"postgres": "AIBI_TEST_POSTGRES_URL", "mysql": "AIBI_TEST_MYSQL_URL"}
_DEFAULT_PORTS = {"postgres": 5432, "mysql": 3306}


def _rewritten(kind: Kind, url: str, **parts: object) -> str:
    """The test's URL, with the parts given (``user``, ``password``, ``host``, ``port``,
    ``database``) in place of its own, and its own parameters."""
    read = urls.parse(cast(urls.ServerKind, kind), url)
    found: dict[str, Any] = {
        "user": read.user,
        "password": read.password,
        "host": read.host,
        "port": read.port,
        "database": read.database,
        **parts,
    }
    quoted = urllib.parse.quote
    scheme = "postgresql" if kind == "postgres" else "mysql"
    secret = "" if found["password"] is None else ":" + quoted(found["password"], safe="")
    host = f"[{found['host']}]" if ":" in found["host"] else found["host"]
    port = "" if found["port"] is None else f":{found['port']}"
    user, database = quoted(found["user"], safe=""), quoted(found["database"], safe="")
    query = "&".join(f"{name}={quoted(value, safe='')}" for name, value in read.parameters)
    return f"{scheme}://{user}{secret}@{host}{port}/{database}{'?' + query if query else ''}"


def _literal(kind: Kind, text: str) -> str:
    if kind == "mysql":
        return "'" + text.replace("\\", "\\\\").replace("'", "\\'") + "'"
    return "'" + text.replace("'", "''") + "'"


_PROBE = """
from aibi.core.importers.databases import Connection, _server
from aibi.core.importers.snapshot import SOURCE, _opened
from aibi.core.schema.limits import ImportLimits
kind, url = given
resolved = _server(Connection("c", kind, url_env="U"), {"U": url})
session = _opened(resolved.target, ImportLimits())
probe = {
    "postgres": "SELECT current_database(), current_user::text, host(inet_server_addr()), "
    "inet_server_port()",
    "mysql": "SELECT DATABASE(), CURRENT_USER(), NULL, @@port",
}[kind]
row = session.execute(f"SELECT * FROM {kind}_query($s, $q)", {"s": SOURCE, "q": probe}).fetchone()
print(json.dumps([resolved.source.location, *[None if v is None else str(v) for v in row]]))
"""


@pytest.mark.databases
@pytest.mark.parametrize("kind", ["postgres", "mysql"])
@pytest.mark.parametrize("variant", ["as given", "by name", "quoted user"])
def test_a_server_session_opens_the_port_and_database_its_provenance_names(
    kind: Kind, variant: str
) -> None:
    url = _server_url(_VARIABLES[kind])
    read = urls.parse(cast(urls.ServerKind, kind), url)
    user, made = read.user, False
    scope = "'127.0.0.1'" if read.host == "127.0.0.1" else "'%'"
    if variant == "by name":
        if read.host not in ("127.0.0.1", "::1"):
            pytest.skip("the test's server is not on this host")
        url = _rewritten(kind, url, host="localhost")
        if kind == "mysql":
            with pytest.raises(urls.UrlError, match="localhost"):
                urls.parse("mysql", url)
            return
    try:
        if variant == "quoted user":
            if kind == "postgres":
                user, password = "we ird'u\\", "a\\b'c\"d e=f"
                made_by = f'CREATE ROLE "{user}" LOGIN PASSWORD {_literal(kind, password)}'
            else:
                user, password = 'q"u\\ =x', "\\\\\\"
                made_by = (
                    f"CREATE USER {_literal(kind, user)}@{scope} "
                    f"IDENTIFIED BY {_literal(kind, password)}"
                )
            _execute(kind, url, [made_by])
            made = True
            if kind == "mysql":
                granted = f"GRANT SELECT ON `{read.database}`.* TO {_literal(kind, user)}@{scope}"
                _execute(kind, url, [granted])
            url = _rewritten(kind, url, user=user, password=password)
        location, database, current, address, port = json.loads(in_duckdb(_PROBE, [kind, url]))
    finally:
        if made:
            dropped = (
                f'DROP ROLE "{user}"'
                if kind == "postgres"
                else f"DROP USER {_literal(kind, user)}@{scope}"
            )
            _execute(kind, _server_url(_VARIABLES[kind]), [dropped])
    host, recorded, _ = location.split("/")
    assert recorded == database == read.database
    assert int(port) == (read.port or _DEFAULT_PORTS[kind])
    assert (current if kind == "postgres" else current.rpartition("@")[0]) == user
    if address is not None and variant != "by name":
        assert address == host


_ELSEWHERE = """
from dataclasses import replace
from aibi.core.importers.databases import Connection, _server
from aibi.core.importers.errors import ImportRefused
from aibi.core.importers.snapshot import read_snapshot
from aibi.core.schema.limits import ImportLimits
kind, url = given
target = _server(Connection("c", kind, url_env="U"), {"U": url}).target
try:
    read_snapshot(replace(target, database=target.database + "_elsewhere"), ImportLimits())
    print("read")
except ImportRefused as refused:
    print(refused.refusals[0].code)
"""


@pytest.mark.databases
@pytest.mark.parametrize("kind", ["postgres", "mysql"])
def test_a_snapshot_whose_session_opened_a_database_other_than_its_targets_is_refused(
    kind: Kind,
) -> None:
    url = _server_url(_VARIABLES[kind])
    assert in_duckdb(_ELSEWHERE, [kind, url]).strip() == "INVALID_VALUE"


@pytest.mark.databases
def test_a_postgres_database_named_by_63_bytes_is_read_and_a_longer_name_refused(
    roots: Any, store: Store, connect: Any
) -> None:
    url = _server_url("AIBI_TEST_POSTGRES_URL")
    name = f"aibi_{secrets.token_hex(4)}".ljust(63, "d")
    try:
        _execute("postgres", url, [f'CREATE DATABASE "{name}"'], alone=True)
    except subprocess.CalledProcessError:
        pytest.skip("the test's user cannot make databases")
    try:
        exact = _rewritten("postgres", url, database=name)
        _execute("postgres", exact, ["CREATE TABLE public.t (id int PRIMARY KEY)"])
        source = connect("postgres", url_env="U", environ={"U": exact})
        done = import_dataset(store, source, roots.options("library"), "operator:a")
        dataset = published(store, done)["dataset"]
        assert isinstance(dataset, DatasetDescriptor)
        assert dataset.fields.source is not None
        assert dataset.fields.source.location.split("/")[1] == name
        for longer, said in (("EXTRA", "63 bytes"), ("EXTRA\x1b[31mRED", "control character")):
            environ = {"U": _rewritten("postgres", url, database=name + longer)}
            with pytest.raises(ImportRefused) as refused:
                connect("postgres", url_env="U", environ=environ)
            assert refusal_of(refused).code == RefusalCode.INVALID_VALUE
            assert said in shown(refusal_of(refused))
    finally:
        _execute("postgres", url, [f'DROP DATABASE "{name}" WITH (FORCE)'], alone=True)


@pytest.mark.databases
def test_a_servers_provenance_records_the_database_the_server_says_it_opened(
    roots: Any, store: Store, connect: Any, postgres_empty: tuple[str, str]
) -> None:
    url, schema = postgres_empty
    _execute("postgres", url, [f"CREATE TABLE {schema}.kept (id int PRIMARY KEY)"])
    source = connect("postgres", url_env="U", schema=schema, environ={"U": url})
    stale = dataclasses.replace(source, source=dataclasses.replace(source.source, location="x"))
    done = import_dataset(store, stale, roots.options("library"), "operator:a")
    dataset = published(store, done)["dataset"]
    assert isinstance(dataset, DatasetDescriptor)
    assert dataset.fields.source is not None
    assert dataset.fields.source.location == source.source.location


@pytest.mark.databases
def test_a_mysql_provenance_records_the_database_the_server_says_it_opened(
    roots: Any, store: Store, connect: Any, mysql_tables: tuple[str, list[str]]
) -> None:
    url, dropping = mysql_tables
    dropping.append("DROP TABLE IF EXISTS aibi_kept")
    _execute("mysql", url, ["CREATE TABLE aibi_kept (id INT PRIMARY KEY)"])
    source = connect("mysql", url_env="U", environ={"U": url})
    stale = dataclasses.replace(source, source=dataclasses.replace(source.source, location="x"))
    with store.pin() as pin:
        imported = build_import(store, pin, stale, roots.options("library"))
    dataset = by_id(tuple(imported.result.descriptors))["dataset"]
    assert isinstance(dataset, DatasetDescriptor)
    assert dataset.fields.source is not None
    assert dataset.fields.source.location == source.source.location


_HOLDING = (
    _ATTACHED
    + """
print("holding", flush=True)
for statement in statements:
    session.execute(call, {"sql": statement})
"""
)


@pytest.mark.databases
def test_a_postgres_partition_left_pending_detach_is_noted_and_not_read(
    roots: Any, store: Store, connect: Any, postgres_empty: tuple[str, str]
) -> None:
    url, schema = postgres_empty
    _execute(
        "postgres",
        url,
        [
            f"CREATE TABLE {schema}.p (id int, v text) PARTITION BY RANGE (id)",
            f"CREATE TABLE {schema}.p1 PARTITION OF {schema}.p FOR VALUES FROM (0) TO (10)",
            f"CREATE TABLE {schema}.p2 PARTITION OF {schema}.p FOR VALUES FROM (10) TO (20) "
            "PARTITION BY RANGE (id)",
            f"CREATE TABLE {schema}.p21 PARTITION OF {schema}.p2 FOR VALUES FROM (10) TO (20)",
            f"INSERT INTO {schema}.p SELECT g, 'x' FROM generate_series(0, 19) g",
        ],
    )
    with subprocess.Popen(
        [
            sys.executable,
            "-c",
            f"import duckdb, json, sys\ngiven = json.load(sys.stdin)\n{_HOLDING}",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    ) as holder:
        assert holder.stdin is not None
        assert holder.stdout is not None
        holding = f"SELECT count(*) FROM {schema}.p; SELECT pg_sleep(8)"
        holder.stdin.write(json.dumps(["postgres", url, [holding], [], False]))
        holder.stdin.close()
        assert holder.stdout.readline().strip() == "holding"
        with pytest.raises(subprocess.CalledProcessError):
            _execute(
                "postgres",
                url,
                [f"ALTER TABLE {schema}.p DETACH PARTITION {schema}.p2 CONCURRENTLY"],
                "options='-c statement_timeout=3000'",
                alone=True,
            )
        holder.wait(timeout=60)
    imported, _ = _postgres_import(store, roots, connect, url, schema)
    read = imported.result.sources["p"]
    assert isinstance(read, TypedSource)
    assert len(read.rows) == 10
    detaching = TextSegment(
        text=": a partition being detached, or a partition of one, whose rows its table no longer "
        "holds, which is not read"
    )
    for name in ("p2", "p21"):
        [note] = [n for n in imported.result.notes if DataSegment(data=name) in n.message]
        assert detaching in note.message


_PLANT = """
from aibi.core.importers.snapshot import extension_path
from aibi.core.importers.urls import parse
kind, port, password, bare = given
session = duckdb.connect()
session.load_extension(str(extension_path(kind)))
session.execute(
    f"CREATE PERSISTENT SECRET __default_{kind} (TYPE {kind}, PORT {port}, PASSWORD $password)"
    .replace("$password", "'" + password.replace("'", "''") + "'")
)
written = parse(kind, bare).connection().replace("'", "''")
session.execute(f"ATTACH '{written}' AS target (TYPE {kind}, READ_ONLY)")
print("reached")
"""


@pytest.mark.databases
@pytest.mark.parametrize("kind", ["postgres", "mysql"])
def test_a_persistent_secret_that_would_complete_a_servers_url_is_not_read(
    roots: Any,
    store: Store,
    connect: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: Kind,
) -> None:
    url = _server_url(_VARIABLES[kind])
    read = urls.parse(cast(urls.ServerKind, kind), url)
    if read.port in (None, _DEFAULT_PORTS[kind]) or read.password is None:
        pytest.skip("the test's URL gives no port and password for a secret to supply")
    bare = _rewritten(kind, url, port=None, password=None)
    environ = {key: value for key, value in os.environ.items() if key != "HOME"}
    planted = [kind, read.port, read.password, bare]
    assert in_duckdb(_PLANT, planted, cwd=tmp_path, env=environ).strip() == "reached"
    monkeypatch.chdir(tmp_path)
    source = connect(kind, url_env="U", environ={"U": bare})
    with pytest.raises(ImportRefused) as refused:
        import_dataset(store, source, roots.options("library"), "operator:a")
    assert refusal_of(refused).code == RefusalCode.UNPARSEABLE_SOURCE


@pytest.mark.databases
def test_a_mariadb_system_versioned_table_whose_name_a_views_equals_is_refused(
    roots: Any, store: Store, connect: Any, mysql_tables: tuple[str, list[str]]
) -> None:
    url, dropping = mysql_tables
    dropping.extend(["DROP TABLE IF EXISTS aibi_Kept", "DROP VIEW IF EXISTS aibi_kept"])
    try:
        _execute(
            "mysql",
            url,
            [
                "CREATE TABLE aibi_Kept (id INT PRIMARY KEY) WITH SYSTEM VERSIONING",
                "CREATE VIEW aibi_kept AS SELECT 1 AS one",
            ],
        )
    except subprocess.CalledProcessError:
        pytest.skip("this server is not MariaDB, or folds the case of table names")
    refusal = _mysql_refusal(store, roots, connect, url)
    assert refusal.code == RefusalCode.UNSUPPORTED_FORMAT
    assert DataSegment(data="aibi_Kept") in refusal.message
    assert DataSegment(data="aibi_kept") in refusal.message


_PAYLOAD = "cover` UNION SELECT amount FROM {secret}.salaries -- z"


@pytest.mark.databases
@pytest.mark.parametrize("cover", [True, False], ids=["with its cover view", "alone"])
def test_a_mysql_table_whose_name_would_inject_into_the_scanners_read_is_refused(
    roots: Any, store: Store, connect: Any, mysql_tables: tuple[str, list[str]], cover: bool
) -> None:
    url, dropping = mysql_tables
    read = urls.parse("mysql", url)
    secret = f"aibi_{secrets.token_hex(3)}"
    payload = _PAYLOAD.format(secret=secret)
    quoted = "`" + payload.replace("`", "``") + "`"
    dropping.extend(
        [
            f"DROP DATABASE IF EXISTS `{secret}`",
            f"DROP TABLE IF EXISTS {quoted}",
            "DROP VIEW IF EXISTS `cover\\`",
        ]
    )
    try:
        _execute("mysql", url, [f"CREATE DATABASE `{secret}`"])
    except subprocess.CalledProcessError:
        pytest.skip("the test's user cannot make databases")
    _execute(
        "mysql",
        url,
        [
            f"CREATE TABLE `{secret}`.salaries (id INT PRIMARY KEY, amount INT)",
            f"INSERT INTO `{secret}`.salaries VALUES (1, 987654)",
            *(["CREATE VIEW `cover\\` AS SELECT 0 AS n"] if cover else []),
            f"CREATE TABLE {quoted} (n INT PRIMARY KEY)",
            f"INSERT INTO {quoted} VALUES (11)",
        ],
    )
    refusal = _mysql_refusal(store, roots, connect, url)
    assert refusal.code == RefusalCode.UNSUPPORTED_FORMAT
    named = {part.data.lower() for part in refusal.message if isinstance(part, DataSegment)}
    assert named == {payload.lower()}
    assert "987654" not in shown(refusal)
    assert read.database not in named


@pytest.mark.databases
def test_a_mysql_column_whose_name_would_inject_into_the_scanners_read_is_refused(
    roots: Any, store: Store, connect: Any, mysql_tables: tuple[str, list[str]]
) -> None:
    url, dropping = mysql_tables
    dropping.append("DROP TABLE IF EXISTS aibi_kept")
    _execute(
        "mysql",
        url,
        [
            "CREATE TABLE aibi_kept (id INT PRIMARY KEY, `n`` FROM mysql.user -- x` INT)",
            "INSERT INTO aibi_kept VALUES (1, 2)",
        ],
    )
    refusal = _mysql_refusal(store, roots, connect, url)
    assert refusal.code == RefusalCode.UNSUPPORTED_FORMAT
    assert DataSegment(data="aibi_kept") in refusal.message
    assert DataSegment(data="n` FROM mysql.user -- x") in refusal.message


_FOLDING = (
    _ATTACHED
    + """
row = session.execute(
    "SELECT * FROM mysql_query('target', 'SELECT @@lower_case_table_names')"
).fetchone()
print(row[0])
"""
)


@pytest.mark.databases
def test_a_mysql_server_that_folds_a_databases_case_is_refused_for_the_urls_other_case(
    roots: Any, store: Store, connect: Any, mysql_tables: tuple[str, list[str]]
) -> None:
    url, dropping = mysql_tables
    if in_duckdb(_FOLDING, ["mysql", url, [], [], False]).strip() != "1":
        pytest.skip("this MySQL keeps the case of database names (lower_case_table_names is not 1)")
    name = f"aibi_{secrets.token_hex(4)}"
    dropping.append(f"DROP DATABASE IF EXISTS `{name}`")
    _execute(
        "mysql", url, [f"CREATE DATABASE `{name}`", f"CREATE TABLE `{name}`.t (id INT PRIMARY KEY)"]
    )
    assert set(
        _mysql_import(store, roots, connect, _rewritten("mysql", url, database=name)).result.sources
    ) == {"t"}
    refusal = _mysql_refusal(store, roots, connect, _rewritten("mysql", url, database=name.upper()))
    assert refusal.code == RefusalCode.INVALID_VALUE
    assert DataSegment(data=name) in refusal.message


_SETTINGS = """
from aibi.core.importers.databases import Connection, _server
from aibi.core.importers.snapshot import SOURCE, _opened
from aibi.core.schema.limits import ImportLimits
url, schema = given
target = _server(Connection("c", "postgres", url_env="U"), {"U": url}).target
session = _opened(target, ImportLimits())
found = session.execute(
    "SELECT * FROM postgres_query($s, $q)",
    {
        "s": SOURCE,
        "q": "SELECT pg_catalog.current_setting('search_path') AS a, "
        "pg_catalog.current_setting('row_security') AS b",
    },
).fetchone()
try:
    session.execute(
        "SELECT * FROM postgres_query($s, $q)",
        {"s": SOURCE, "q": f'SELECT pg_catalog.count(*) AS n FROM "{schema}".guarded'},
    ).fetchall()
    guarded = "read"
except duckdb.Error:
    guarded = "refused"
print(json.dumps([*found, guarded]))
"""


@contextmanager
def _postgres_roles(url: str) -> Iterator[tuple[str, str, str, str]]:
    """An owner role, a reader role that logs in, a schema's name and the reader's password,
    the roles and the schema dropped after the test whatever it left. The test is skipped when
    the test's user cannot make roles or act as those it makes (``SET ROLE``, which a
    ``CREATEROLE`` user of PostgreSQL 16 can only when its ``createrole_self_grant`` holds
    ``set``)."""
    tag = secrets.token_hex(4)
    owner, reader, schema = f"aibi_owner_{tag}", f"aibi_reader_{tag}", f"aibi_{tag}"
    password = secrets.token_hex(12)
    try:
        try:
            _execute("postgres", url, [f"CREATE ROLE {owner} NOLOGIN"])
            _execute("postgres", url, [f"CREATE ROLE {reader} LOGIN PASSWORD '{password}'"])
        except subprocess.CalledProcessError:
            pytest.skip("the test's user cannot make roles")
        try:
            _execute("postgres", url, [f"SET ROLE {owner}", f"SET ROLE {reader}", "RESET ROLE"])
        except subprocess.CalledProcessError:
            pytest.skip("the test's user cannot act as the roles it makes (createrole_self_grant)")
        yield owner, reader, schema, password
    finally:
        for statement in (
            f"DROP SCHEMA IF EXISTS {schema} CASCADE",
            f"DROP OWNED BY {reader}",
            f"DROP ROLE IF EXISTS {reader}",
            f"DROP ROLE IF EXISTS {owner}",
        ):
            with suppress(subprocess.CalledProcessError):
                _execute("postgres", url, [statement])


@pytest.mark.databases
def test_a_postgres_snapshot_of_another_roles_schema_pins_its_settings_and_skips_row_security(
    roots: Any, store: Store, connect: Any
) -> None:
    url = _server_url("AIBI_TEST_POSTGRES_URL")
    with _postgres_roles(url) as (owner, reader, schema, password):
        _execute(
            "postgres",
            url,
            [
                f"CREATE SCHEMA {schema} AUTHORIZATION {owner}",
                f"CREATE TABLE {schema}.open_rows (id int PRIMARY KEY)",
                f"CREATE TABLE {schema}.guarded (id int PRIMARY KEY)",
                f"INSERT INTO {schema}.open_rows VALUES (1), (2)",
                f"INSERT INTO {schema}.guarded VALUES (1), (2)",
                f"ALTER TABLE {schema}.open_rows OWNER TO {owner}",
                f"ALTER TABLE {schema}.guarded OWNER TO {owner}",
                f"ALTER TABLE {schema}.guarded ENABLE ROW LEVEL SECURITY",
                f"CREATE POLICY first_only ON {schema}.guarded USING (id OPERATOR(pg_catalog.<) 2)",
                f"GRANT USAGE ON SCHEMA {schema} TO {reader}",
                f"GRANT SELECT ON ALL TABLES IN SCHEMA {schema} TO {reader}",
                f"ALTER ROLE {reader} SET search_path = {schema}, public",
                f"ALTER ROLE {reader} SET row_security = on",
            ],
        )
        as_reader = _rewritten("postgres", url, user=reader, password=password)
        settings = json.loads(in_duckdb(_SETTINGS, [as_reader, schema]))
        assert settings == ["pg_catalog,pg_temp", "off", "refused"]
        source = connect("postgres", url_env="U", schema=schema, environ={"U": as_reader})
        with store.pin() as pin:
            imported = build_import(store, pin, source, roots.options("library"))
    read = imported.result.sources["open_rows"]
    assert isinstance(read, TypedSource)
    assert read.rows == ((1,), (2,))
    assert set(imported.result.sources) == {"open_rows"}
    [note] = [n for n in imported.result.notes if DataSegment(data="guarded") in n.message]
    assert any("row security" in getattr(part, "text", "") for part in note.message)


@pytest.mark.databases
def test_mysql_names_the_scanner_quotes_safely_or_never_writes_are_read_on_a_server(
    roots: Any, store: Store, connect: Any, mysql_tables: tuple[str, list[str]]
) -> None:
    url, dropping = mysql_tables
    dropping.extend(["DROP VIEW IF EXISTS `cover\\`", "DROP TABLE IF EXISTS `it's`"])
    _execute(
        "mysql",
        url,
        [
            "CREATE TABLE `it's` (id INT PRIMARY KEY, `c'q` INT)",
            "INSERT INTO `it's` VALUES (1, 2)",
            "CREATE VIEW `cover\\` AS SELECT 0 AS n",
        ],
    )
    imported = _mysql_import(store, roots, connect, url)
    read = imported.result.sources["it_s"]
    assert isinstance(read, TypedSource)
    assert (read.columns, read.rows) == (("id", "c'q"), ((1, 2),))
    said = [part for note in imported.result.notes for part in note.message]
    assert DataSegment(data="cover\\") in said


_BASE_TYPE = [
    "CREATE TYPE {schema}.word",
    "CREATE FUNCTION {schema}.word_in(cstring) RETURNS {schema}.word "
    "AS 'textin' LANGUAGE internal IMMUTABLE STRICT",
    "CREATE FUNCTION pg_catalog.{schema}_word_out({schema}.word) RETURNS cstring "
    "AS 'textout' LANGUAGE internal IMMUTABLE STRICT",
    "CREATE TYPE {schema}.word (input = {schema}.word_in, output = pg_catalog.{schema}_word_out, "
    "like = text)",
    "CREATE FUNCTION {schema}.word_send({schema}.word) RETURNS bytea LANGUAGE plpgsql AS "
    "$$ begin perform pg_catalog.nextval('{schema}.ran'); return ''::bytea; end $$",
    "ALTER TYPE {schema}.word SET (SEND = {schema}.word_send)",
]
"""A base type whose output function is in ``pg_catalog`` (a superuser's) and whose send
function is not: only the send function makes its column the owner's code to read."""


@pytest.mark.databases
@pytest.mark.parametrize(
    ("read", "target", "made", "column", "value"),
    [
        (True, "varchar", [], "label", "'a'"),
        (True, "text", [], "label", "'a'"),
        (True, "varchar", ["CREATE DOMAIN {schema}.held AS {schema}.label"], "held", "'a'"),
        (True, "varchar", [], "label[]", "'{{a,b}}'"),
        (
            True,
            "varchar",
            ["CREATE TYPE {schema}.span AS RANGE (subtype = {schema}.label)"],
            "span",
            "'[a,b]'",
        ),
        (
            True,
            "varchar",
            [
                "CREATE TYPE {schema}.span AS RANGE "
                "(subtype = {schema}.label, multirange_type_name = {schema}.spans)"
            ],
            "spans",
            "'{{[a,b]}}'",
        ),
        (True, "varchar", ["CREATE TYPE {schema}.pair AS (l {schema}.label)"], "pair", "ROW('a')"),
        (False, "varchar", _BASE_TYPE, "word", "'hello'"),
    ],
    ids=[
        "a cast to varchar",
        "a cast to text",
        "a domain",
        "an array element",
        "a range subtype",
        "a multirange",
        "a composite field",
        "a base type's send function",
    ],
)
def test_a_postgres_owner_cast_is_not_run_on_a_read_and_a_user_base_type_is_skipped(
    roots: Any,
    store: Store,
    connect: Any,
    read: bool,
    target: str,
    made: list[str],
    column: str,
    value: str,
) -> None:
    """A column whose type is outside ``pg_catalog`` is read through the type's output function,
    so a schema owner's function cast to text or varchar is never resolved and never runs, and
    the table is read (``read``); a user base type, whose own output or send function is the
    owner's, is still skipped and noted (defence in depth). The owner's function bumps a sequence
    the reader can see, which never advances either way."""
    url = _server_url("AIBI_TEST_POSTGRES_URL")
    with _postgres_roles(url) as (owner, reader, schema, password):
        _execute(
            "postgres",
            url,
            [
                f"CREATE SCHEMA {schema} AUTHORIZATION {owner}",
                f"GRANT USAGE ON SCHEMA {schema} TO {reader}",
            ],
        )
        if not read:
            try:
                _execute("postgres", url, [part.format(schema=schema) for part in made])
            except subprocess.CalledProcessError:
                pytest.skip("making a base type needs a superuser")
        _execute(
            "postgres",
            url,
            [
                f"SET ROLE {owner}",
                f"CREATE SEQUENCE {schema}.ran",
                f"CREATE TYPE {schema}.label AS ENUM ('a', 'b')",
                f"CREATE FUNCTION {schema}.label_text({schema}.label) RETURNS {target} "
                "LANGUAGE plpgsql AS "
                f"$$ begin perform pg_catalog.nextval('{schema}.ran'); return 'x'; end $$",
                f"CREATE CAST ({schema}.label AS {target}) "
                f"WITH FUNCTION {schema}.label_text({schema}.label)",
                *([] if not read else [part.format(schema=schema) for part in made]),
                f"CREATE TABLE {schema}.marked (id int PRIMARY KEY, m {schema}.{column})",
                f"INSERT INTO {schema}.marked VALUES (1, {value.format(schema=schema)})",
                f"CREATE TABLE {schema}.plain (id int PRIMARY KEY, n int)",
                f"INSERT INTO {schema}.plain VALUES (1, 10)",
                f"GRANT SELECT ON ALL TABLES IN SCHEMA {schema} TO {reader}",
                f"GRANT SELECT ON {schema}.ran TO {reader}",
                "RESET ROLE",
            ],
        )
        as_reader = _rewritten("postgres", url, user=reader, password=password)
        source = connect("postgres", url_env="U", schema=schema, environ={"U": as_reader})
        with store.pin() as pin:
            imported = build_import(store, pin, source, roots.options("library"))
        ran = _sequence_value(url, schema)
    assert ran == 0
    if read:
        assert set(imported.result.sources) == {"marked", "plain"}
        marked = imported.result.sources["marked"]
        assert isinstance(marked, TypedSource)
        [(_, m)] = marked.rows
        assert m is not None
    else:
        assert set(imported.result.sources) == {"plain"}
        [note] = [n for n in imported.result.notes if DataSegment(data="marked") in n.message]
        assert any(
            "neither pg_catalog's nor an installed extension's" in getattr(part, "text", "")
            for part in note.message
        )
        assert not any("owner" in getattr(part, "text", "") for part in note.message)


_RACE = """
import dataclasses
from aibi.core.importers.databases import Connection, _server
from aibi.core.importers.snapshot import (
    SOURCE,
    _Tables,
    _opened,
    _opened_database,
    _postgres_catalog,
    extension_path,
)
from aibi.core.importers.urls import parse
from aibi.core.schema.limits import ImportLimits
as_reader, admin, schema, owner = given
target = _server(Connection("c", "postgres", url_env="U", schema=schema), {"U": as_reader}).target
conn = _opened(target, ImportLimits())
_opened_database(conn, target)
conn.execute("BEGIN TRANSACTION")
conn.execute(
    "SELECT * FROM postgres_query($s, $q)", {"s": SOURCE, "q": "SELECT 1"}
).fetchall()
relations, _ = _postgres_catalog(conn, schema, ImportLimits())
writer = duckdb.connect()
writer.load_extension(str(extension_path("postgres")))
dsn = parse("postgres", admin).connection().replace("'", "''")
writer.execute(f"ATTACH '{dsn}' AS w (TYPE postgres)")
for statement in [
    f"SET ROLE {owner}",
    f"CREATE FUNCTION {schema}.mid_text({schema}.label) RETURNS varchar LANGUAGE plpgsql "
    f"AS $$ begin perform pg_catalog.nextval('{schema}.ran'); return 'z'; end $$",
    f"CREATE CAST ({schema}.label AS varchar) WITH FUNCTION {schema}.mid_text({schema}.label)",
]:
    writer.execute("CALL postgres_execute('w', $sql, use_transaction := false)", {"sql": statement})
writer.close()
[marked] = [r for r in relations if r.name == "marked"]
reader = _Tables(conn, "postgres", schema)
arrow = reader.rows(marked, marked.primary_key or marked.columns).to_arrow_table()
conn.execute("COMMIT")
conn.close()
print(json.dumps(arrow.num_rows))
"""


@pytest.mark.databases
def test_a_postgres_cast_added_mid_snapshot_is_not_run_because_the_read_resolves_no_cast(
    roots: Any, store: Store, connect: Any
) -> None:
    """The race the round-9 sign-off was held on: Postgres resolves a cast against its current
    catalogue, not the transaction's snapshot, so a function cast a schema owner adds after the
    snapshot has begun would run on a later read in it. The read reaches every non-``pg_catalog``
    column through its output function, resolving no cast, so the owner's function never runs. The
    cast is created from a second connection after the snapshot is fixed and before the row read;
    the owner's function bumps a sequence, which never advances."""
    url = _server_url("AIBI_TEST_POSTGRES_URL")
    with _postgres_roles(url) as (owner, reader, schema, password):
        _execute(
            "postgres",
            url,
            [
                f"CREATE SCHEMA {schema} AUTHORIZATION {owner}",
                f"GRANT USAGE ON SCHEMA {schema} TO {reader}",
                f"SET ROLE {owner}",
                f"CREATE SEQUENCE {schema}.ran",
                f"CREATE TYPE {schema}.label AS ENUM ('a', 'b')",
                f"CREATE TABLE {schema}.marked (id int PRIMARY KEY, m {schema}.label)",
                f"INSERT INTO {schema}.marked VALUES (1, 'a')",
                f"GRANT SELECT ON {schema}.marked TO {reader}",
                f"GRANT SELECT ON {schema}.ran TO {reader}",
                "RESET ROLE",
            ],
        )
        as_reader = _rewritten("postgres", url, user=reader, password=password)
        rows = int(json.loads(in_duckdb(_RACE, [as_reader, url, schema, owner])))
        ran = _sequence_value(url, schema)
    assert rows == 1
    assert ran == 0


_SEQ = """
from aibi.core.importers.databases import Connection, _server
from aibi.core.importers.snapshot import SOURCE, _opened
from aibi.core.schema.limits import ImportLimits
url, schema = given
target = _server(Connection("c", "postgres", url_env="U"), {"U": url}).target
session = _opened(target, ImportLimits())
value = session.execute(
    "SELECT * FROM postgres_query($s, $q)",
    {"s": SOURCE, "q": f'SELECT last_value, is_called FROM "{schema}".ran'},
).fetchone()
print(json.dumps(int(value[0]) if value[1] else 0))
"""


def _sequence_value(url: str, schema: str) -> int:
    return int(json.loads(in_duckdb(_SEQ, [url, schema])))


@pytest.mark.databases
def test_a_postgres_range_reads_the_same_whatever_the_roles_output_settings(
    roots: Any, store: Store, connect: Any
) -> None:
    url = _server_url("AIBI_TEST_POSTGRES_URL")
    with _postgres_roles(url) as (_, reader, schema, password):
        _execute(
            "postgres",
            url,
            [
                f"CREATE SCHEMA {schema}",
                f"CREATE TABLE {schema}.spans (id int PRIMARY KEY, d daterange, t tstzrange, "
                "r regclass)",
                f"INSERT INTO {schema}.spans VALUES (1, '[2024-01-01,2024-02-01)', "
                "'[2024-01-01 00:00:00+00,2024-02-01 00:00:00+00)', 'pg_catalog.pg_class')",
                f"GRANT USAGE ON SCHEMA {schema} TO {reader}",
                f"GRANT SELECT ON {schema}.spans TO {reader}",
                f"ALTER ROLE {reader} SET datestyle = 'SQL, DMY'",
                f"ALTER ROLE {reader} SET timezone = 'Asia/Kolkata'",
                f"ALTER ROLE {reader} SET intervalstyle = 'sql_standard'",
                f"ALTER ROLE {reader} SET quote_all_identifiers = on",
            ],
        )
        as_reader = _rewritten("postgres", url, user=reader, password=password)
        source = connect("postgres", url_env="U", schema=schema, environ={"U": as_reader})
        with store.pin() as pin:
            imported = build_import(store, pin, source, roots.options("library"))
    spans = imported.result.sources["spans"]
    assert isinstance(spans, TypedSource)
    [(_, d, t, r)] = spans.rows
    assert str(d) == "[2024-01-01,2024-02-01)"
    assert str(t) == '["2024-01-01 00:00:00+00","2024-02-01 00:00:00+00")'
    assert r == "pg_class"


@pytest.mark.databases
def test_a_postgres_extension_type_is_read_through_its_output_function(
    roots: Any, store: Store, connect: Any, postgres_empty: tuple[str, str]
) -> None:
    url, schema = postgres_empty
    try:
        _execute("postgres", url, [f"CREATE EXTENSION citext SCHEMA {schema}"])
    except subprocess.CalledProcessError:
        pytest.skip("the server has no citext extension the test's user can create")
    try:
        _execute(
            "postgres",
            url,
            [
                f"CREATE TABLE {schema}.words (id int PRIMARY KEY, w {schema}.citext)",
                f"INSERT INTO {schema}.words VALUES (1, 'Mixed Case'), (2, NULL)",
            ],
        )
        imported, _ = _postgres_import(store, roots, connect, url, schema)
    finally:
        _execute("postgres", url, ["DROP EXTENSION IF EXISTS citext CASCADE"])
    words = imported.result.sources["words"]
    assert isinstance(words, TypedSource)
    assert words.rows == ((1, "Mixed Case"), (2, None))
    assert not [n for n in imported.result.notes if DataSegment(data="words") in n.message]


@pytest.mark.databases
def test_a_postgres_null_of_a_type_outside_pg_catalog_is_read_as_null_not_empty_text(
    roots: Any, store: Store, connect: Any, postgres_empty: tuple[str, str]
) -> None:
    url, schema = postgres_empty
    _execute(
        "postgres",
        url,
        [
            f"CREATE TYPE {schema}.label AS ENUM ('a', 'b')",
            f"CREATE TYPE {schema}.pair AS (l {schema}.label, n int)",
            f"CREATE TABLE {schema}.marks (id int PRIMARY KEY, m {schema}.label, "
            f"p {schema}.pair, ms {schema}.label[])",
            f"INSERT INTO {schema}.marks VALUES (1, 'a', ROW('b', 2), '{{a}}'), "
            "(2, NULL, NULL, NULL)",
        ],
    )
    imported, _ = _postgres_import(store, roots, connect, url, schema)
    marks = imported.result.sources["marks"]
    assert isinstance(marks, TypedSource)
    assert marks.rows == ((1, "a", "(b,2)", "{a}"), (2, None, None, None))


@pytest.mark.databases
def test_a_postgres_timetz_is_read_as_its_exact_text_keeping_its_offset(
    roots: Any, store: Store, connect: Any, postgres_empty: tuple[str, str]
) -> None:
    url, schema = postgres_empty
    _execute(
        "postgres",
        url,
        [
            f"CREATE TABLE {schema}.zoned (id int PRIMARY KEY, t timetz, many timetz[])",
            f"INSERT INTO {schema}.zoned VALUES (1, '10:11:12+02', '{{10:11:12+02}}'), "
            "(2, '10:11:12-05', '{10:11:12-05}')",
        ],
    )
    imported, _ = _postgres_import(store, roots, connect, url, schema)
    zoned = imported.result.sources["zoned"]
    assert isinstance(zoned, TypedSource)
    assert {row[1] for row in zoned.rows} == {"10:11:12+02", "10:11:12-05"}
    assert {tuple(json.loads(str(row[2]))) for row in zoned.rows} == {
        ("10:11:12+02",),
        ("10:11:12-05",),
    }


@pytest.mark.databases
def test_a_postgres_infinity_date_is_refused_naming_its_column(
    roots: Any, store: Store, connect: Any, postgres_empty: tuple[str, str]
) -> None:
    url, schema = postgres_empty
    _execute(
        "postgres",
        url,
        [
            f"CREATE TABLE {schema}.far (id int PRIMARY KEY, d date)",
            f"INSERT INTO {schema}.far VALUES (1, 'infinity')",
        ],
    )
    source = connect("postgres", url_env="U", schema=schema, environ={"U": url})
    with pytest.raises(ImportRefused) as refused:
        import_dataset(store, source, roots.options("library"), "operator:a")
    refusal = refusal_of(refused)
    assert refusal.code == RefusalCode.UNPARSEABLE_SOURCE
    assert DataSegment(data="far") in refusal.message
    assert DataSegment(data="d") in refusal.message


@pytest.mark.databases
@pytest.mark.parametrize("force", [False, True], ids=["enabled", "force only"])
def test_a_postgres_partitioned_or_forced_row_security_table_is_noted_not_refused(
    roots: Any, store: Store, connect: Any, postgres_empty: tuple[str, str], force: bool
) -> None:
    url, schema = postgres_empty
    security = "FORCE ROW LEVEL SECURITY" if force else "ENABLE ROW LEVEL SECURITY"
    _execute(
        "postgres",
        url,
        [
            f"CREATE TABLE {schema}.part (id int, v text) PARTITION BY RANGE (id)",
            f"CREATE TABLE {schema}.part_1 PARTITION OF {schema}.part FOR VALUES FROM (0) TO (10)",
            f"INSERT INTO {schema}.part VALUES (1, 'x')",
            f"ALTER TABLE {schema}.part {security}",
            f"CREATE TABLE {schema}.kept (id int PRIMARY KEY)",
        ],
    )
    imported, _ = _postgres_import(store, roots, connect, url, schema)
    assert set(imported.result.sources) == {"kept"}
    [note] = [n for n in imported.result.notes if DataSegment(data="part") in n.message]
    assert any("row security" in getattr(part, "text", "") for part in note.message)


_COUNTED = (
    _ATTACHED
    + """
import time
[query, tries] = statements
for _ in range(int(tries)):
    [(found,)] = session.execute(
        "SELECT * FROM postgres_query('target', $q)", {"q": query}
    ).fetchall()
    if found:
        break
    time.sleep(0.1)
print(json.dumps(found))
"""
)


def _counted(url: str, query: str, tries: int = 1) -> int:
    """The count ``query`` gives on the test's server, asked up to ``tries`` times, a tenth of
    a second apart, until it is not zero."""
    return int(json.loads(in_duckdb(_COUNTED, ["postgres", url, [query, str(tries)], [], False])))


def _locking(url: str, schema: str) -> None:
    _execute(
        "postgres",
        url,
        [
            f"CREATE TABLE {schema}.t (id int PRIMARY KEY)",
            f"INSERT INTO {schema}.t VALUES (1)",
            f"CREATE TABLE {schema}_other.kid () INHERITS ({schema}.t)",
            f"CREATE TABLE {schema}.part (id int) PARTITION BY RANGE (id)",
            f"CREATE TABLE {schema}.part_1 PARTITION OF {schema}.part FOR VALUES FROM (0) TO (10)",
        ],
    )


@contextmanager
def _locked(url: str, relation: str) -> Iterator[None]:
    """``relation`` locked ``ACCESS EXCLUSIVE`` by another session, in one statement that then
    sleeps, until the block ends and the session is ended."""
    holds = (
        f"FROM pg_catalog.pg_locks WHERE relation OPERATOR(pg_catalog.=) "
        f"'{relation}'::pg_catalog.regclass AND mode OPERATOR(pg_catalog.=) 'AccessExclusiveLock'"
    )
    holding = (
        f"DO $$ BEGIN LOCK TABLE {relation} IN ACCESS EXCLUSIVE MODE; "
        "PERFORM pg_catalog.pg_sleep(60); END $$"
    )
    with subprocess.Popen(
        [
            sys.executable,
            "-c",
            f"import duckdb, json, sys\ngiven = json.load(sys.stdin)\n{_HOLDING}",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    ) as holder:
        try:
            assert holder.stdin is not None
            assert holder.stdout is not None
            holder.stdin.write(json.dumps(["postgres", url, [holding], [], True]))
            holder.stdin.close()
            assert holder.stdout.readline().strip() == "holding"
            assert _counted(url, f"SELECT pg_catalog.count(*) {holds} AND granted", 300) == 1
            yield
        finally:
            with suppress(subprocess.CalledProcessError):
                _execute("postgres", url, [f"SELECT pg_catalog.pg_terminate_backend(pid) {holds}"])
            holder.kill()


_WAITING = (
    "SELECT pg_catalog.count(*) FROM pg_catalog.pg_stat_activity "
    "WHERE wait_event_type OPERATOR(pg_catalog.=) 'Lock'"
)


@pytest.mark.databases
@pytest.mark.parametrize(
    ("held", "named"),
    [("t", "t"), ("part_1", "part")],
    ids=["a table", "a partition of a table"],
)
def test_a_postgres_table_another_session_keeps_locked_is_refused_once_the_lock_wait_runs_out(
    roots: Any,
    store: Store,
    connect: Any,
    postgres_empty: tuple[str, str],
    held: str,
    named: str,
) -> None:
    url, schema = postgres_empty
    _locking(url, schema)
    source = connect("postgres", url_env="U", schema=schema, environ={"U": url})
    options = roots.options("library", limits=ImportLimits(reader_seconds=10))
    with _locked(url, f"{schema}.{held}"):
        with store.pin() as pin, pytest.raises(ImportRefused) as refused:
            build_import(store, pin, source, options)
        waiting = _counted(url, _WAITING)
    assert waiting == 0
    refusal = refusal_of(refused)
    assert refusal.code == RefusalCode.LIMIT_EXCEEDED
    assert DataSegment(data=named) in refusal.message
    assert "held a lock on it for more than 5 seconds" in shown(refusal)


@pytest.mark.databases
def test_a_postgres_table_is_read_while_another_session_keeps_a_child_of_it_locked(
    roots: Any, store: Store, connect: Any, postgres_empty: tuple[str, str]
) -> None:
    url, schema = postgres_empty
    _locking(url, schema)
    source = connect("postgres", url_env="U", schema=schema, environ={"U": url})
    options = roots.options("library", limits=ImportLimits(reader_seconds=10))
    with _locked(url, f"{schema}_other.kid"), store.pin() as pin:
        imported = build_import(store, pin, source, options)
    read = imported.result.sources["t"]
    assert isinstance(read, TypedSource)
    assert read.rows == ((1,),)


def test_postgres_lock_names_its_table_quoted_and_an_ordinary_one_only() -> None:
    assert _postgres_lock('s"1', 'a"b', True) == (
        'SELECT pg_catalog.count(*) FROM (SELECT FROM ONLY "s""1"."a""b" LIMIT 0) AS l'
    )
    assert _postgres_lock("s", "p", False) == (
        'SELECT pg_catalog.count(*) FROM (SELECT FROM "s"."p" LIMIT 0) AS l'
    )


def test_a_postgres_snapshot_bounds_its_lock_waits_then_locks_each_table_before_its_columns() -> (
    None
):
    session = _PostgresCatalogue(
        {
            "pg_toast": [("public",)],
            "row_security": [
                ("kept", "r", None, False, None, False, False, False, False),
                ("parted", "p", None, False, None, False, False, False, False),
            ],
            "col_description": [("kept", "id", None, 23, False), ("parted", "id", None, 23, False)],
        }
    )
    _postgres_catalog(cast(Any, session), "public", ImportLimits(reader_seconds=8))
    waits = next(i for i, q in enumerate(session.queries) if "lock_timeout" in q)
    locks = [i for i, q in enumerate(session.queries) if "LIMIT 0" in q]
    columns = next(i for i, q in enumerate(session.queries) if "col_description" in q)
    assert "'4000'" in session.queries[waits]
    assert [session.queries[i] for i in locks] == [
        _postgres_lock("public", "kept", True),
        _postgres_lock("public", "parted", False),
    ]
    assert waits < min(locks)
    assert max(locks) < columns


def test_the_postgres_owner_code_check_follows_every_way_a_column_reaches_a_user_base_type() -> (
    None
):
    for part in (
        "t.typbasetype",
        "t.typelem",
        "r.rngsubtype",
        "'rngmultitypid'",
        "t.typrelid",
        "ARRAY[t.typoutput, t.typsend]",
    ):
        assert part in _POSTGRES_OWNER_CODE
    assert "pg_cast" not in _POSTGRES_OWNER_CODE


class _LockFailing(_PostgresCatalogue):
    """A stand-in whose lock statement raises what the scanner raises, with ``message``."""

    def __init__(self, rows: dict[str, list[tuple[object, ...]]], message: str) -> None:
        super().__init__(rows)
        self.message = message

    def execute(self, sql: str, parameters: dict[str, str]) -> "_PostgresCatalogue":
        if "LIMIT 0" in parameters["query"]:
            import duckdb

            raise duckdb.IOException(self.message)
        return super().execute(sql, parameters)


@pytest.mark.parametrize(
    ("message", "code"),
    [
        ("ERROR:  canceling statement due to lock timeout", RefusalCode.LIMIT_EXCEEDED),
        ('ERROR:  relation "public.kept" does not exist', RefusalCode.UNPARSEABLE_SOURCE),
    ],
    ids=["a lock timeout", "another error"],
)
def test_a_postgres_lock_error_is_a_limit_only_when_the_lock_wait_ran_out(
    message: str, code: RefusalCode
) -> None:
    session = _LockFailing(
        {
            "pg_toast": [("public",)],
            "row_security": [("kept", "r", None, False, None, False, False, False, False)],
            "col_description": [("kept", "id", None, 23, False)],
        },
        message,
    )
    with pytest.raises(ImportRefused) as refused:
        _postgres_catalog(cast(Any, session), "public", ImportLimits())
    refusal = refusal_of(refused)
    assert refusal.code == code
    assert DataSegment(data="kept") in refusal.message
    assert "does not exist" not in shown(refusal)


def test_the_postgres_read_of_a_type_outside_pg_catalog_uses_its_output_function_and_no_cast() -> (
    None
):
    relation = _Relation(
        "t",
        None,
        ("shape", "amount", "plain"),
        {},
        None,
        (),
        casts={"amount": ("text",)},
        output=frozenset({"shape"}),
    )
    shape = _postgres_read(relation, "shape").sql(dialect="postgres")
    assert "pg_catalog.format('%s', \"shape\")" in shape.lower()
    assert '"shape" IS NULL' in shape
    assert "::" not in shape
    assert "cast(" not in shape.lower()
    amount = _postgres_read(relation, "amount").sql(dialect="postgres")
    assert "cast(" in amount.lower()
    assert _postgres_read(relation, "plain").sql(dialect="postgres") == '"plain"'

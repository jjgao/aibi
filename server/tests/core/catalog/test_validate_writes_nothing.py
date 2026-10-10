"""``validate_document`` writes nothing (SPEC §11.1, §12.2, D300, D422): the place policy lets a
page call it on load, so it must change no row of any table of the app DB and no file of the store
outside it. The digest is over every table in ``sqlite_master``, read through SQL, never over the
DB's bytes: the app DB runs in WAL mode, whose ``-wal`` and ``-shm`` files change on reads, and
whose rows are not all in the main file. ``count_cohort`` is the control: it records issuances,
and the digest sees each table it changes."""

import hashlib
import sqlite3
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from pydantic import JsonValue

from aibi.core.catalog.service import Catalog
from aibi.core.engine.worker import Workers
from aibi.core.schema.cohorts import AnalysisResults, CohortCounts, DocumentValidation
from aibi.core.schema.limits import QueryLimits
from aibi.core.schema.output import Output
from aibi.core.schema.refusals import Refusal
from aibi.core.store.store import APP_DB, Store

World = Any
Orchard = Callable[..., dict[str, bytes]]
LOG_TABLES = {"derivations", "issuances", "log_texts"}


def _quoted(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def table_digests(connection: sqlite3.Connection) -> dict[str, str]:
    """A digest of the rows of every table in ``sqlite_master`` (``sqlite_master`` and
    ``sqlite_sequence`` included), by table: the rows' ``repr``, sorted, so that it does not
    depend on a table's physical order."""
    names = [
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
        )
    ]
    found: dict[str, str] = {}
    for name in ["sqlite_master", *names]:
        rows = sorted(repr(row) for row in connection.execute(f"SELECT * FROM {_quoted(name)}"))
        found[name] = hashlib.sha256("\n".join(rows).encode()).hexdigest()
    return found


def file_digest(root: Path) -> str:
    """A digest of every file and directory under ``root`` but the app DB and its ``-wal`` and
    ``-shm`` files: each path, kind and, for a file, its bytes."""
    skipped = {APP_DB, f"{APP_DB}-wal", f"{APP_DB}-shm"}
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if relative.as_posix() in skipped:
            continue
        digest.update(relative.as_posix().encode() + (b"/" if path.is_dir() else b":"))
        if path.is_file():
            digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def settled(store: Store) -> sqlite3.Connection:
    """The store's housekeeping is done (a pin's release in the background has run), and a read
    connection of its own, which sees the WAL's rows as any reader does."""
    assert store.housekept(10)
    connection = sqlite3.connect(f"file:{store.root / APP_DB}?mode=ro", uri=True)
    return connection


TALL: dict[str, JsonValue] = {"kind": "value", "column": "trees.height_m", "range": {"gte": 5}}
HEAVY: dict[str, JsonValue] = {
    "kind": "exists",
    "table": "harvests",
    "where": [{"kind": "value", "column": "harvests.kg", "range": {"gte": 15}}],
}


def document(cohorts: Mapping[str, list[JsonValue]]) -> dict[str, JsonValue]:
    return {
        "aibi": "1",
        "dataset": "orchard",
        "unit": "trees",
        "cohorts": {name: {"all": clauses} for name, clauses in cohorts.items()},
    }


def run(world: World, catalog: Catalog, name: str, body: JsonValue) -> Output | list[Refusal]:
    return world.tool(catalog, name, body)


def test_the_digest_covers_every_table_of_the_app_db_and_sees_a_change_in_each(
    world: World, orchard: Orchard
) -> None:
    world.publish("orchard", orchard())
    store: Store = world.store
    names = {
        row[0]
        for row in store.db.connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert len(names) >= 25
    assert names >= LOG_TABLES
    digests = table_digests(store.db.connection)
    assert set(digests) == {*names, "sqlite_master"}
    changed_somewhere: set[str] = set()
    for name in sorted(names):
        # a scratch copy, whose triggers and foreign keys are gone, with one row of the table
        # deleted: the digest differs in that table alone (a table with no rows is skipped)
        copy = sqlite3.connect(":memory:")
        store.db.connection.backup(copy)
        assert table_digests(copy) == digests
        for (trigger,) in copy.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger'"
        ).fetchall():
            copy.execute(f"DROP TRIGGER {_quoted(trigger)}")
        base = table_digests(copy)
        copy.execute("PRAGMA foreign_keys = OFF")
        table = _quoted(name)
        if not copy.execute(
            f"DELETE FROM {table} WHERE rowid IN (SELECT rowid FROM {table} LIMIT 1)"
        ).rowcount:
            copy.close()
            continue
        after = table_digests(copy)
        assert {key for key in after if after[key] != base[key]} == {name}, name
        changed_somewhere.add(name)
        copy.close()
    assert {"manifests", "labels", "audit"} <= changed_somewhere


APPLE: dict[str, JsonValue] = {"kind": "value", "column": "trees.variety", "values": ["apple"]}
PEAR: dict[str, JsonValue] = {"kind": "value", "column": "trees.variety", "values": ["pear"]}
EXISTENCE: dict[str, JsonValue] = {
    "analysis": "compare.existence",
    "cohorts": ["apple", "pear"],
    "params": {"predicates": [TALL, HEAVY]},
}


def with_a_view(threshold: int = 5) -> dict[str, JsonValue]:
    """A document with two cohorts and a view, whose predicates move with ``threshold``."""
    tall: dict[str, JsonValue] = {**TALL, "range": {"gte": threshold}}
    view: dict[str, JsonValue] = {**EXISTENCE, "params": {"predicates": [tall, HEAVY]}}
    return {
        **document({"apple": [APPLE], "pear": [PEAR]}),
        "views": [view],
    }


def digests_after(store: Store) -> tuple[dict[str, str], str]:
    assert store.housekept(10)
    reader = settled(store)
    try:
        return table_digests(reader), file_digest(store.root)
    finally:
        reader.close()


def validate_all(world: World, catalog: Catalog, written: dict[str, JsonValue]) -> None:
    unknown = {**document({"tall": [TALL]}), "dataset": "nowhere"}
    bodies: list[tuple[JsonValue, bool]] = [
        ({"document": written}, True),
        ({"document": written}, True),  # the same document again
        ({"document": document({"other": [TALL]})}, True),  # a document nobody asked about
        ({"document": with_a_view(7)}, True),  # a document with a view, one nobody ran
        ({"document": document({"bad": [{"kind": "nonsense"}]})}, False),  # a refused document
        ({"document": unknown}, False),  # a dataset that is not there
    ]
    for body, valid in bodies:
        found = run(world, catalog, "validate_document", body)
        assert isinstance(found, DocumentValidation), found
        assert found.valid is valid, found


def test_validate_document_changes_no_row_and_no_file(world: World, orchard: Orchard) -> None:
    world.publish("orchard", orchard())
    catalog = world.catalog()
    store: Store = world.store
    # No call before the digests: a first call that wrote a row or a file once would hide in
    # the "before".
    before_rows, before_files = digests_after(store)
    validate_all(world, catalog, document({"tall": [TALL], "heavy": [HEAVY], "all": []}))
    assert not store.db.connection.in_transaction
    after_rows, after_files = digests_after(store)
    assert after_rows == before_rows, sorted(
        k for k in after_rows if after_rows[k] != before_rows[k]
    )
    assert after_files == before_files


def test_validate_document_changes_nothing_in_a_populated_log_and_cache(
    world: World, orchard: Orchard
) -> None:
    """The log and the result cache hold rows (a count, an analysis run twice), so that a
    validate that touched a row of them, evicted one or deleted one would show."""
    world.publish("orchard", orchard())
    catalog = Catalog(world.store, workers=Workers(QueryLimits()))
    store: Store = world.store
    counted = run(world, catalog, "count_cohort", {"document": with_a_view(5)})
    assert isinstance(counted, CohortCounts), counted
    for _ in range(2):
        ran = run(world, catalog, "run_analysis", {"document": with_a_view(5)})
        assert isinstance(ran, AnalysisResults), ran
    reader = settled(store)
    try:
        for name in ("derivations", "issuances", "log_texts", "result_cache"):
            assert reader.execute(f"SELECT count(*) FROM {name}").fetchone()[0] > 0, name
    finally:
        reader.close()
    before_rows, before_files = digests_after(store)
    validate_all(world, catalog, with_a_view(5))  # the documents that were counted and run
    assert not store.db.connection.in_transaction
    after_rows, after_files = digests_after(store)
    assert after_rows == before_rows, sorted(
        k for k in after_rows if after_rows[k] != before_rows[k]
    )
    assert after_files == before_files


def test_the_digest_sees_a_row_updated_in_place_and_not_only_a_count_of_rows() -> None:
    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE TABLE used (id INTEGER PRIMARY KEY, n INTEGER, text TEXT)")
    connection.execute("CREATE TABLE other (a TEXT)")
    connection.executemany("INSERT INTO used VALUES (?, ?, ?)", [(1, 0, "a"), (2, 0, "b")])
    before = table_digests(connection)
    counts = {n: connection.execute(f"SELECT count(*) FROM {n}").fetchone() for n in before}
    connection.execute("UPDATE used SET n = n + 1 WHERE id = 1")
    after = table_digests(connection)
    assert {k for k in after if after[k] != before[k]} == {"used"}
    assert counts == {n: connection.execute(f"SELECT count(*) FROM {n}").fetchone() for n in after}
    connection.execute("UPDATE used SET n = 0 WHERE id = 1")
    assert table_digests(connection) == before, "the same rows, the same digest"
    connection.execute("UPDATE used SET text = 'c' WHERE id = 2")
    assert table_digests(connection)["used"] != before["used"]
    connection.close()


def test_count_cohort_is_the_control_and_the_digest_sees_the_log(
    world: World, orchard: Orchard
) -> None:
    world.publish("orchard", orchard())
    catalog = Catalog(world.store, workers=Workers(QueryLimits()))
    store: Store = world.store
    before_rows = table_digests(store.db.connection)
    before_files = file_digest(store.root)
    found = run(world, catalog, "count_cohort", {"document": document({"tall": [TALL]})})
    assert isinstance(found, CohortCounts), found
    reader = settled(store)
    try:
        after_rows = table_digests(reader)
    finally:
        reader.close()
    changed = {key for key in after_rows if after_rows[key] != before_rows[key]}
    assert changed >= LOG_TABLES, changed
    assert file_digest(store.root) == before_files


def test_the_files_digest_sees_a_changed_added_and_removed_file_and_directory(
    tmp_path: Path,
) -> None:
    (tmp_path / "blobs").mkdir()
    (tmp_path / "blobs" / "a").write_bytes(b"1")
    (tmp_path / APP_DB).write_bytes(b"db")
    (tmp_path / f"{APP_DB}-wal").write_bytes(b"w")
    (tmp_path / f"{APP_DB}-shm").write_bytes(b"s")
    base = file_digest(tmp_path)
    (tmp_path / APP_DB).write_bytes(b"other")
    (tmp_path / f"{APP_DB}-wal").write_bytes(b"other")
    (tmp_path / f"{APP_DB}-shm").unlink()
    assert file_digest(tmp_path) == base, "the DB's own files are not in it"
    (tmp_path / "blobs" / "a").write_bytes(b"2")
    changed = file_digest(tmp_path)
    assert changed != base
    (tmp_path / "blobs" / "a").write_bytes(b"1")
    (tmp_path / "blobs" / "b").write_bytes(b"")
    added = file_digest(tmp_path)
    assert added != base
    (tmp_path / "blobs" / "b").unlink()
    (tmp_path / "empty").mkdir()
    assert file_digest(tmp_path) not in (base, changed, added)
    (tmp_path / "empty").rmdir()
    assert file_digest(tmp_path) == base


def test_d422_says_the_claim_is_for_an_aibi_document() -> None:
    """A document with a ``format`` goes through a pack's translator first (D303), which the
    core's tests cannot load: D422 does not claim more than the test shows."""
    root = Path(__file__).resolve().parents[4]
    [row] = [
        line
        for line in (root / "SPEC.md").read_text(encoding="utf-8").splitlines()
        if line.startswith("| D422 |")
    ]
    assert "**`validate_document` writes nothing for an aibi document**" in row
    assert "with a `format`, a pack's translator runs first, D303" in row
    assert "the core's tests load no pack" in row

"""A release built from raw snapshots, loaded from the store and evaluated by the reference
evaluator (SPEC §12.2, §13.3): what the importers will hand the query engine."""

import json
import os
import time
from pathlib import Path
from typing import Any

import pytest

from aibi.core.engine.evaluate import evaluate
from aibi.core.engine.queries import run_cohorts
from aibi.core.engine.resolve import resolve
from aibi.core.engine.worker import Workers
from aibi.core.schema.limits import QueryLimits
from aibi.core.schema.loading import load_document
from aibi.core.store.blobs import CorruptBlobError, MissingBlobError
from aibi.core.store.store import Store


def _count(store: Store, manifest: str, clauses: list[Any], unit: str = "members") -> Any:
    written = {"aibi": "1", "dataset": "lib", "unit": unit, "cohorts": {"c": {"all": clauses}}}
    loaded = load_document(json.dumps(written))
    assert loaded.document is not None
    resolution = resolve(loaded.document, {"lib": store.load(manifest)}, loaded.positions)
    assert resolution.refusals == []
    return evaluate(resolution.cohorts["c"])


def test_a_stored_release_answers_questions(store: Store, imported: str) -> None:
    overdue = {
        "kind": "exists",
        "table": "loans",
        "where": [{"kind": "value", "column": "loans.overdue", "range": {"gt": 0}}],
    }
    result = _count(store, imported, [overdue])
    # m-1's loan is 6 days overdue; m-17's are 11 days early and unknown; the others have none
    # under coverage undeclared, or a loan with no days.
    assert result.values[0].value.value == "TRUE"
    assert (result.n_true, result.n_false + result.n_unknown) == (1, 3)
    older = _count(
        store,
        imported,
        [{"kind": "value", "column": "members.age", "range": {"gte": 30}, "units": "a"}],
    )
    assert [value.value.value for value in older.values] == ["TRUE", "UNKNOWN", "FALSE", "UNKNOWN"]
    interested = _count(
        store, imported, [{"kind": "value", "column": "members.interests", "values": ["maths"]}]
    )
    assert [value.value.value for value in interested.values] == [
        "TRUE",
        "UNKNOWN",
        "TRUE",
        "UNKNOWN",
    ]


def test_a_stored_release_is_counted_in_a_worker_as_the_evaluator_counts_it(
    store: Store, imported: str
) -> None:
    """The SQL reads the table blobs in place, under a pin, from the release's outline."""
    clauses: list[Any] = [
        {
            "kind": "exists",
            "table": "loans",
            "where": [{"kind": "value", "column": "loans.overdue", "range": {"gt": 0}}],
        },
        {"kind": "value", "column": "members.age_months", "range": {"gte": 360}},
        {"kind": "value", "column": "members.interests", "values": ["maths"], "match": "all"},
        {"kind": "value", "column": "members.joined", "range": {"lt": "2024-01-01"}},
    ]
    for clause in clauses:
        written = {
            "aibi": "1",
            "dataset": "lib",
            "unit": "members",
            "cohorts": {"c": {"all": [clause]}},
        }
        loaded = load_document(json.dumps(written))
        assert loaded.document is not None
        outline = resolve(loaded.document, {"lib": store.outline(imported)}, loaded.positions)
        assert outline.refusals == []
        with store.pin() as pin:
            pin.manifest(imported)
            [counted] = run_cohorts(
                [outline.cohorts["c"]],
                {imported: store.sources(imported)},
                Workers(QueryLimits()),
                values=True,
            )
        expected = _count(store, imported, [clause])
        assert counted.values is not None
        assert tuple(counted.values) == expected.values
        assert (counted.accounting.n_true, counted.accounting.n_unknown) == (
            expected.n_true,
            expected.n_unknown,
        )
        recorded = [value for value in counted.parameters.values() if isinstance(value, str)]
        members = store.manifest(imported).table("members")
        assert members is not None
        assert members.hash in recorded


def test_a_release_has_no_rows_in_its_outline(store: Store, imported: str) -> None:
    outline = store.outline(imported)
    assert outline.descriptors == store.load(imported).descriptors
    assert outline.tables == {}
    assert {source.path for source in store.sources(imported).values()} == {
        str(store.blobs.path(entry.hash)) for entry in store.manifest(imported).tables
    }


def test_the_blobs_a_query_reads_are_verified_as_a_load_verifies_them(
    store: Store, imported: str
) -> None:
    """D221 on the SQL path: a damaged blob is refused rather than counted, a missing one is
    ``MissingBlobError``, and a blob changed after it was verified is verified again."""
    store.sources(imported)
    members = store.manifest(imported).table("members")
    assert members is not None
    path = store.blobs.path(members.hash)
    data = path.read_bytes()
    path.chmod(0o644)
    path.write_bytes(bytes([data[0] ^ 1]) + data[1:])
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
    with pytest.raises(CorruptBlobError):
        store.sources(imported)
    path.unlink()
    with pytest.raises(MissingBlobError):
        store.sources(imported)


def test_a_blob_written_again_with_its_modification_time_set_back_is_verified_again(
    store: Store, imported: str
) -> None:
    """The change time, which a write sets and no caller can set back, is part of what makes a
    file the one verified (D293)."""
    store.sources(imported)
    members = store.manifest(imported).table("members")
    assert members is not None
    path = store.blobs.path(members.hash)
    data = path.read_bytes()
    before = path.stat()
    path.chmod(0o644)
    time.sleep(0.01)
    with open(path, "r+b") as file:
        file.write(bytes([data[0] ^ 1]))
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    after = path.stat()
    assert (after.st_ino, after.st_size, after.st_mtime_ns) == (
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
    )
    with pytest.raises(CorruptBlobError):
        store.sources(imported)


def test_a_link_in_a_blob_s_place_is_refused_even_to_a_whole_copy(
    store: Store, imported: str, tmp_path: Path
) -> None:
    members = store.manifest(imported).table("members")
    assert members is not None
    path = store.blobs.path(members.hash)
    copy = tmp_path / "copy"
    copy.write_bytes(path.read_bytes())
    path.chmod(0o644)
    path.unlink()
    path.symlink_to(copy)
    with pytest.raises(CorruptBlobError):
        store.sources(imported)

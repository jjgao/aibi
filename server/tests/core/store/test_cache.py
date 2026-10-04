"""The result cache (SPEC §8.1, §12.2, §14; D375): a result's content by result id, filled by an
issuance of it whose queries ran, bounded by ``result_bytes``, and gone with its filler, with
any release it read when that is withdrawn, and with everything else when a person is erased."""

import json
import logging
import re
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from pydantic import JsonValue

from aibi.core.engine.canonical import CanonicalCohort, ViewIdentity, canonicalise
from aibi.core.schema.limits import CacheLimits
from aibi.core.schema.loading import load_document
from aibi.core.store import appdb
from aibi.core.store.appdb import MIGRATIONS
from aibi.core.store.cache import ResultCache
from aibi.core.store.erasure import erase
from aibi.core.store.store import Store

Library = Any
ADA = "operator:ada"
ENGINE = "aibi 0.0.1"
SQL: JsonValue = [{"sql": "SELECT count(*) FROM members", "parameters": []}]
WRITTEN: JsonValue = {"aibi": "1", "dataset": "lib", "unit": "members", "cohorts": {}}


def cohort_of(store: Store, manifest: str) -> CanonicalCohort:
    """The cohort of every member of the release."""
    written = {"aibi": "1", "dataset": "lib", "unit": "members", "cohorts": {"c": {"all": []}}}
    loaded = load_document(json.dumps(written))
    assert loaded.document is not None
    found = canonicalise(
        loaded.document,
        {"lib": store.load(manifest)},
        labels={manifest: 1},
        positions=loaded.positions,
    )
    return found.cohorts["c"]


def result_of(
    store: Store,
    cohort: CanonicalCohort,
    n: int = 0,
    *,
    values_from: str | None = None,
    engine: str = ENGINE,
    k: int | None = None,
    packs: dict[str, JsonValue] | None = None,
) -> tuple[str, str]:
    """A result over the cohort at ``k``, told apart by ``n``, and an issuance of it by
    ``engine``; with ``values_from``, a hit of that issuance, which runs no query."""
    view = ViewIdentity(
        analysis="lib.tally",
        version="1",
        cohorts=(cohort.identity,),
        params={"n": n},
        packs={},
        disclosure=k,
    )
    issued = store.derivations.issue(
        derivation=view.id,
        kind="result",
        hashed=view.hashed(),
        releases=[cohort.release.model_dump(mode="json")],
        tool="run_analysis",
        written=WRITTEN,
        params={},
        sql=None if values_from is not None else SQL,
        engine=engine,
        packs={} if packs is None else packs,
        values_from=values_from,
    )
    return view.id, issued


def fill(
    store: Store,
    result: str,
    issuance: str,
    content: bytes = b"{}",
    *,
    cache: ResultCache | None = None,
) -> bool:
    with store.db.transaction() as db:
        return (store.results if cache is None else cache).fill(
            db, result=result, issuance=issuance, content=content
        )


def rows(store: Store) -> dict[str, int]:
    with store.db.lock:
        return {
            table: store.db.connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in ("result_cache", "result_cache_contents")
        }


def test_a_filled_result_is_found_by_its_id_engine_and_packs_alone(
    store: Store, imported: str
) -> None:
    cohort = cohort_of(store, imported)
    result, issuance = result_of(store, cohort)
    assert store.results.lookup(result, ENGINE, {}) is None
    assert fill(store, result, issuance, b'{"values": 2.0}')
    found = store.results.lookup(result, ENGINE, {})
    assert found is not None
    assert (found.result, found.issuance, found.content) == (result, issuance, b'{"values": 2.0}')
    assert "values" not in repr(found)
    assert store.results.lookup(result, "aibi 0.0.2", {}) is None
    other = {"p": {"version": "1.0.0", "results_version": 1}}
    assert store.results.lookup(result, ENGINE, other) is None
    assert store.results.usage() > 0


def test_only_an_issuance_of_the_result_whose_queries_ran_fills_it(
    store: Store, imported: str
) -> None:
    cohort = cohort_of(store, imported)
    result, issuance = result_of(store, cohort)
    other, _ = result_of(store, cohort, 1)
    hit = result_of(store, cohort, values_from=issuance)[1]
    with pytest.raises(sqlite3.IntegrityError, match="whose queries ran"):
        fill(store, other, issuance)
    with pytest.raises(sqlite3.IntegrityError, match="whose queries ran"):
        fill(store, result, hit)
    assert rows(store) == {
        "result_cache": 0,
        "result_cache_contents": 0,
    }


def test_a_fill_outside_a_transaction_is_refused(store: Store, imported: str) -> None:
    cohort = cohort_of(store, imported)
    result, issuance = result_of(store, cohort)
    with store.db.lock, pytest.raises(RuntimeError, match="transaction"):
        store.results.fill(
            store.db.connection,
            result=result,
            issuance=issuance,
            content=b"{}",
        )


def test_a_result_goes_with_its_filler_when_pruning_takes_it(store: Store, imported: str) -> None:
    cohort = cohort_of(store, imported)
    result, issuance = result_of(store, cohort)
    assert fill(store, result, issuance)
    cutoff = store.now()
    assert store.derivations.prune(cutoff) == 1
    assert rows(store) == {
        "result_cache": 0,
        "result_cache_contents": 0,
    }
    assert store.results.usage() == 0


def test_a_result_goes_when_a_release_it_read_is_withdrawn_and_others_stay(
    store: Store, library: Library, imported: str
) -> None:
    cohort = cohort_of(store, imported)
    kept, filler = result_of(store, cohort)
    assert fill(store, kept, filler)
    second = republished(store, library)
    later = cohort_of(store, second)
    gone, filled = result_of(store, later)
    assert fill(store, gone, filled)
    store.withdraw("lib", second, ADA)
    assert store.results.lookup(gone, ENGINE, {}) is None
    assert store.results.lookup(kept, ENGINE, {}) is not None
    assert not fill(store, gone, filled)
    with pytest.raises(sqlite3.IntegrityError, match="published releases"):
        inserted(store, gone, filled)


def republished(store: Store, library: Library, key: str = "m-17") -> str:
    """The library imported and published again without the member ``key`` and their loans;
    the manifest."""
    members = b"\n".join(
        line for line in library.members.split(b"\n") if not line.startswith(key.encode() + b",")
    )
    loans = [loan for loan in library.loans if key not in loan]
    with store.pin() as pin:
        built = store.import_release(
            pin,
            "lib",
            library.descriptors(),
            library.sources(members=members, loans=loans),
            library.layouts,
        )
        store.publish("lib", built.manifest.hash, ADA)
    return built.manifest.hash


def test_erasure_empties_the_whole_cache_even_what_read_no_erased_release(
    store: Store, library: Library, imported: str
) -> None:
    second = republished(store, library)
    later = cohort_of(store, second)
    result, issuance = result_of(store, later)
    assert fill(store, result, issuance, b'{"note": "m-17"}')
    assert erase(store, "lib", "members", ["m-17"], ADA).redacted
    assert rows(store) == {
        "result_cache": 0,
        "result_cache_contents": 0,
    }
    assert store.results.usage() == 0
    token = re.compile(rb"(?<![0-9A-Za-z])m-17(?![0-9A-Za-z])")
    files = sorted(Path(store.db.path).parent.glob("app.db*"))
    assert files
    assert [path.name for path in files if token.search(path.read_bytes())] == []


def test_the_least_recently_used_results_are_evicted_to_make_room(
    store: Store, imported: str
) -> None:
    cohort = cohort_of(store, imported)
    made = [result_of(store, cohort, n) for n in range(5)]
    content = b"x" * 3000
    small = ResultCache(store.db, CacheLimits(result_bytes=0))
    assert not fill(store, *made[0], content, cache=small)
    probe = ResultCache(store.db, CacheLimits(result_bytes=1 << 30))
    assert fill(store, *made[0], content, cache=probe)
    one = probe.usage()
    probe.clear()
    four = ResultCache(store.db, CacheLimits(result_bytes=4 * one))
    for result, issuance in made[:4]:
        assert fill(store, result, issuance, content, cache=four)
    with store.db.transaction() as db:
        four.touch(db, made[0][0])
    assert fill(store, *made[4], content, cache=four)
    kept = [four.lookup(result, ENGINE, {}) is not None for result, _ in made]
    assert kept == [True, False, True, True, True]
    assert four.usage() <= 4 * one


def test_a_result_larger_than_a_quarter_of_the_bound_is_not_cached(
    store: Store, imported: str
) -> None:
    cohort = cohort_of(store, imported)
    result, issuance = result_of(store, cohort)
    cache = ResultCache(store.db, CacheLimits(result_bytes=40_000))
    assert not fill(store, result, issuance, b"x" * 10_000, cache=cache)
    assert fill(store, result, issuance, b"x" * 5_000, cache=cache)


def test_a_second_fill_keeps_the_first_and_one_from_another_engine_replaces_it(
    store: Store, imported: str, caplog: pytest.LogCaptureFixture
) -> None:
    cohort = cohort_of(store, imported)
    result, first = result_of(store, cohort)
    _, second = result_of(store, cohort)
    assert fill(store, result, first, b"1")
    assert not fill(store, result, second, b"1")
    assert caplog.records == []
    with caplog.at_level(logging.ERROR):
        assert not fill(store, result, second, b"2")
    assert [r.getMessage() for r in caplog.records] == [
        "result cache: a second fill of one result gave another content"
    ]
    found = store.results.lookup(result, ENGINE, {})
    assert found is not None
    assert (found.issuance, found.content) == (first, b"1")
    _, third = result_of(store, cohort, engine="aibi 0.0.2")
    assert fill(store, result, third, b"3")
    assert store.results.lookup(result, ENGINE, {}) is None
    again = store.results.lookup(result, "aibi 0.0.2", {})
    assert again is not None
    assert (again.issuance, again.content) == (third, b"3")
    assert rows(store) == {
        "result_cache": 1,
        "result_cache_contents": 1,
    }


def test_results_cached_under_a_lower_floor_are_purged(store: Store, imported: str) -> None:
    cohort = cohort_of(store, imported)
    made = [result_of(store, cohort, k=k) for k in (None, 3, 5, 11)]
    for result, issuance in made:
        assert fill(store, result, issuance)
    assert store.results.purge(None) == 0
    assert store.results.purge(5) == 2
    assert [store.results.lookup(r, ENGINE, {}) is not None for r, _ in made] == [
        False,
        False,
        True,
        True,
    ]


def test_the_cache_s_bytes_are_not_the_log_s(store: Store, imported: str) -> None:
    cohort = cohort_of(store, imported)
    result, issuance = result_of(store, cohort)
    before = store.derivations.usage()
    assert fill(store, result, issuance, b"x" * 20_000)
    assert store.derivations.usage() == before
    assert store.results.usage() >= 20_000
    assert store.results.clear() == 1
    assert store.results.usage() == 0


def test_migration_7_adds_an_empty_cache_to_a_log_that_holds_issuances(
    tmp_path: Path, library: Library, monkeypatch: pytest.MonkeyPatch
) -> None:
    with monkeypatch.context() as patched:
        patched.setattr(appdb, "MIGRATIONS", MIGRATIONS[:6])
        patched.setattr(ResultCache, "trim", lambda self: 0)
        # Migration 8's link registry (D408) is not there yet either.
        patched.setattr(Store, "record_known_links", lambda self, dataset=None: None)
        patched.setattr(Store, "_record_links", lambda self, db, dataset, descriptors: None)
        old = Store(tmp_path / "data")
        try:
            assert old.db.version == 6
            with old.pin() as pin:
                built = old.import_release(
                    pin, "lib", library.descriptors(), library.sources(), library.layouts
                )
                old.publish("lib", built.manifest.hash, ADA)
            result, issuance = result_of(old, cohort_of(old, built.manifest.hash))
        finally:
            old.close()
    opened = Store(tmp_path / "data")
    try:
        assert opened.db.version == len(MIGRATIONS) == 8
        assert opened.derivations.issuances(result) == [issuance]
        assert opened.results.usage() == 0
        assert fill(opened, result, issuance)
        assert opened.results.lookup(result, ENGINE, {}) is not None
    finally:
        opened.close()


def inserted(store: Store, result: str, issuance: str, **changed: Any) -> None:
    """Write a row as ``fill`` would, but with ``changed`` columns, past ``fill``."""
    with store.db.transaction() as db:
        engine, packs, k = db.execute(
            "SELECT i.engine, i.packs, json_extract(d.hashed, '$.disclosure.min_cell_count')"
            " FROM issuances i JOIN derivations d ON d.id = i.derivation WHERE i.id = ?",
            (issuance,),
        ).fetchone()
        row = {"engine": engine, "packs": packs, "k": k, "bytes": 1000, "used": 1} | changed
        db.execute(
            "INSERT INTO result_cache (result, issuance, engine, packs, k, bytes, used)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (result, issuance, row["engine"], row["packs"], row["k"], row["bytes"], row["used"]),
        )


@pytest.mark.parametrize(
    "changed",
    [{"engine": "aibi 9"}, {"packs": '{"p":1}'}, {"k": 7}],
    ids=["engine", "packs", "k"],
)
def test_a_row_names_the_engine_packs_and_k_the_log_holds_for_its_filler(
    store: Store, imported: str, changed: dict[str, Any]
) -> None:
    """So a hit's lookup, which matches engine and packs, names a source this engine ran, and
    ``purge`` reads the *k* its id holds."""
    cohort = cohort_of(store, imported)
    result, issuance = result_of(store, cohort, k=3)
    with pytest.raises(sqlite3.IntegrityError, match="whose queries ran"):
        inserted(store, result, issuance, **changed)
    inserted(store, result, issuance)
    with store.db.lock:
        found = store.db.connection.execute(
            "SELECT engine, packs, k FROM result_cache WHERE result = ?", (result,)
        ).fetchone()
    assert tuple(found) == (ENGINE, "{}", 3)


def test_a_filled_row_reads_the_releases_its_result_read_and_goes_when_one_is_withdrawn(
    store: Store, library: Library, imported: str
) -> None:
    """The manifests are the result's own in the log, never the caller's."""
    second = republished(store, library)
    later = cohort_of(store, second)
    result, issuance = result_of(store, later)
    assert fill(store, result, issuance)
    store.withdraw("lib", second, ADA)
    assert rows(store) == {"result_cache": 0, "result_cache_contents": 0}
    with pytest.raises(sqlite3.IntegrityError, match="published releases"):
        inserted(store, result, issuance)


def test_a_result_whose_derivation_erasure_took_is_never_filled(
    store: Store, imported: str
) -> None:
    cohort = cohort_of(store, imported)
    result, issuance = result_of(store, cohort)
    with store.db.transaction() as db:
        db.execute("INSERT INTO log_permits (kind, before) VALUES ('redaction', NULL)")
        db.execute("UPDATE derivations SET hashed = NULL WHERE id = ?", (result,))
        db.execute("DELETE FROM log_permits WHERE kind = 'redaction'")
    with pytest.raises(sqlite3.IntegrityError, match="whose queries ran"):
        fill(store, result, issuance)


FIXED = [
    "UPDATE result_cache SET engine = 'aibi 9'",
    "UPDATE result_cache SET k = 7",
    "UPDATE result_cache SET bytes = 1",
    "UPDATE result_cache SET issuance = issuance",
    "UPDATE result_cache_contents SET content = zeroblob(100000)",
    "DELETE FROM result_cache_contents",
    "INSERT OR REPLACE INTO result_cache SELECT * FROM result_cache",
    "INSERT OR REPLACE INTO result_cache_contents (result, content)"
    " SELECT result, x'39' FROM result_cache",
    "INSERT INTO result_cache_contents (result, content)"
    " SELECT result, zeroblob(bytes + 1) FROM result_cache",
]


@pytest.mark.parametrize("statement", FIXED)
def test_a_filled_row_changes_only_by_being_used(
    store: Store, imported: str, statement: str
) -> None:
    """Whatever path writes the app DB, the bytes counted are the rows' and a row's content is
    the one it was filled with."""
    cohort = cohort_of(store, imported)
    result, issuance = result_of(store, cohort)
    assert fill(store, result, issuance, b"1234")
    with pytest.raises(sqlite3.IntegrityError), store.db.transaction() as db:
        db.execute(statement)
    found = store.results.lookup(result, ENGINE, {})
    assert found is not None
    assert found.content == b"1234"
    with store.db.lock:
        counted = store.db.connection.execute(
            "SELECT (SELECT bytes FROM result_cache_usage), (SELECT sum(bytes) FROM result_cache)"
        ).fetchone()
    assert counted[0] == counted[1] == store.results.usage()


@pytest.mark.parametrize("bound", [0, 1])
def test_a_cache_over_a_lowered_bound_is_trimmed_when_the_store_opens(
    tmp_path: Path, library: Library, bound: int
) -> None:
    store = Store(tmp_path / "data")
    try:
        with store.pin() as pin:
            built = store.import_release(
                pin, "lib", library.descriptors(), library.sources(), library.layouts
            )
            store.publish("lib", built.manifest.hash, ADA)
        cohort = cohort_of(store, built.manifest.hash)
        made = [result_of(store, cohort, n) for n in range(3)]
        for result, issuance in made:
            assert fill(store, result, issuance, b"x" * 3000)
        with store.db.transaction() as db:
            store.results.touch(db, made[0][0])
        one = store.results.usage() // 3
    finally:
        store.close()
    limit = bound * (one + one // 2)
    opened = Store(tmp_path / "data", cache=CacheLimits(result_bytes=limit))
    try:
        assert opened.results.usage() <= limit
        kept = [opened.results.lookup(r, ENGINE, {}) is not None for r, _ in made]
        assert kept == ([True, False, False] if bound else [False, False, False])
    finally:
        opened.close()


def test_a_row_s_content_is_counted_in_its_bytes(store: Store, imported: str) -> None:
    cohort = cohort_of(store, imported)
    result, issuance = result_of(store, cohort)
    inserted(store, result, issuance, bytes=10)
    with (
        pytest.raises(sqlite3.IntegrityError, match="counted in its bytes"),
        store.db.transaction() as db,
    ):
        db.execute(
            "INSERT INTO result_cache_contents (result, content) VALUES (?, zeroblob(11))",
            (result,),
        )
    with store.db.transaction() as db:
        db.execute(
            "INSERT INTO result_cache_contents (result, content) VALUES (?, zeroblob(10))",
            (result,),
        )


def test_a_refused_fill_leaves_the_cache_as_it_was(
    store: Store, imported: str, caplog: pytest.LogCaptureFixture
) -> None:
    """Its removal of the row it replaces and its evictions are undone with it, and a filler of
    another result is refused, not taken for a second fill of this one."""
    cohort = cohort_of(store, imported)
    result, issuance = result_of(store, cohort)
    other, filler = result_of(store, cohort, 1)
    assert fill(store, result, issuance, b"1")
    assert fill(store, other, filler, b"2")
    before = store.results.usage()
    with caplog.at_level(logging.ERROR), store.db.transaction() as db:
        for wrong in (filler, "iss:01J0000000000000000000000Z"):
            with pytest.raises(sqlite3.IntegrityError, match="whose queries ran"):
                store.results.fill(db, result=result, issuance=wrong, content=b"3")
    assert caplog.records == []
    assert store.results.usage() == before
    for key, content in ((result, b"1"), (other, b"2")):
        found = store.results.lookup(key, ENGINE, {})
        assert found is not None
        assert found.content == content


def test_a_result_over_a_release_not_published_is_never_cached(
    store: Store, library: Library
) -> None:
    """A draft's result is declined before anything is written, and refused past ``fill``."""
    with store.pin() as pin:
        built = store.import_release(
            pin, "lib", library.descriptors(), library.sources(), library.layouts
        )
        cohort = cohort_of(store, built.manifest.hash)
        result, issuance = result_of(store, cohort)
        assert not fill(store, result, issuance)
        assert rows(store) == {"result_cache": 0, "result_cache_contents": 0}
        with pytest.raises(sqlite3.IntegrityError, match="published releases"):
            inserted(store, result, issuance)


def _pages(store: Store) -> int:
    connection = store.db.connection
    pages, free, size = (
        int(connection.execute(f"PRAGMA {pragma}").fetchone()[0])
        for pragma in ("page_count", "freelist_count", "page_size")
    )
    return (pages - free) * size


@pytest.mark.parametrize("packs", [0, 3, 8])
def test_the_cache_counts_at_least_the_pages_its_rows_take(
    store: Store, imported: str, packs: int
) -> None:
    """So ``result_bytes`` bounds the app DB's pages the cache takes, whatever the packs' text
    and however small the contents (§14)."""
    versions: dict[str, JsonValue] = {
        f"pack_{n:02d}": {"version": f"1.{n}.0", "results_version": n} for n in range(packs)
    }
    cohort = cohort_of(store, imported)
    made = [result_of(store, cohort, n, packs=versions) for n in range(300)]
    with store.db.lock:
        pages, used = _pages(store), store.results.usage()
    for result, issuance in made:
        assert fill(store, result, issuance)
    with store.db.lock:
        ratio = (_pages(store) - pages) / (store.results.usage() - used)
    assert 0.45 <= ratio <= 1


def test_a_dropped_result_is_gone_and_is_dropped_only_in_a_transaction(
    store: Store, imported: str
) -> None:
    cohort = cohort_of(store, imported)
    result, issuance = result_of(store, cohort)
    assert fill(store, result, issuance)
    with store.db.lock, pytest.raises(RuntimeError, match="transaction"):
        store.results.drop(store.db.connection, result)
    with store.db.transaction() as db:
        store.results.drop(db, result)
    assert rows(store) == {"result_cache": 0, "result_cache_contents": 0}
    assert store.results.usage() == 0

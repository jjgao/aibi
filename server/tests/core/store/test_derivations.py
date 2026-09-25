"""The derivation log (SPEC §7.6, §12.2; D289, D290): derivations recorded once with the object
their id hashes, issuances, pruning, what ``explain`` resolves ids to, and erasure."""

import functools
import json
import math
import random
import re
import sqlite3
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, cast

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from pydantic import JsonValue

from aibi.core.engine import build
from aibi.core.engine.canonical import (
    CanonicalCohort,
    CohortIdentity,
    ViewIdentity,
    as_document,
    canonicalise,
)
from aibi.core.engine.evaluate import evaluate
from aibi.core.engine.resolve import resolve
from aibi.core.engine.units import scale
from aibi.core.schema.curation import ChangeRequest
from aibi.core.schema.document import Clause, PackLeaf
from aibi.core.schema.ids import ISSUANCE_ID_RE
from aibi.core.schema.jsonio import number_text
from aibi.core.schema.loading import load_document
from aibi.core.schema.output import Segment, text
from aibi.core.schema.pack_api import Pack, PackManifest, PackRegistry, ReleaseView
from aibi.core.store import redaction
from aibi.core.store.build import Layout
from aibi.core.store.derivations import (
    DerivationConflictError,
    DerivationLog,
    Ulids,
    WithdrawnReleaseError,
    new_issuance_id,
    stamp,
    text_of,
    ulid,
    utc,
)
from aibi.core.store.erasure import erase
from aibi.core.store.redaction import MARK, Terms, redact
from aibi.core.store.sessions import change, discard, open_session
from aibi.core.store.sources import TextSource, TypedSource
from aibi.core.store.store import Store

Library = Any
ADA = "operator:ada"
KEY = "m-17"
SQL: JsonValue = [{"sql": "SELECT count(*) FROM members", "parameters": []}]


def _loaded(written: JsonValue) -> Any:
    """The document as the loader gives it; the loader must accept it."""
    loaded = load_document(json.dumps(written))
    assert loaded.refusals == [], loaded.refusals
    assert loaded.document is not None
    return loaded


def _cohort(
    store: Store,
    manifest: str,
    clauses: list[Any],
    label: Any = 1,
    notes: str | None = None,
    *,
    unit: str = "members",
    extra: dict[str, Any] | None = None,
    dataset: str = "lib",
    registry: PackRegistry | None = None,
) -> tuple[CanonicalCohort, JsonValue]:
    """The canonical cohort ``c`` of a document over the release, and the document as written;
    ``extra`` adds members to the document (``params``, other cohorts, views), every cohort of
    which must canonicalise."""
    written: dict[str, Any] = {
        "aibi": "1",
        "dataset": dataset if label != "draft" else f"{dataset}@draft",
        "unit": unit,
        "cohorts": {"c": {"all": clauses}},
    }
    if notes is not None:
        written["notes"] = notes
    for key, member in (extra or {}).items():
        written[key] = {**written[key], **member} if key == "cohorts" else member
    loaded = _loaded(written)
    result = canonicalise(
        loaded.document,
        {written["dataset"]: store.load(manifest)},
        labels={manifest: label},
        registry=registry,
        positions=loaded.positions,
    )
    assert result.refusals == [], result.refusals
    return result.cohorts["c"], loaded.written


def _issue(
    store: Store,
    cohort: CanonicalCohort,
    written: JsonValue,
    *,
    tool: Any = "count_cohort",
    values_from: str | None = None,
    params: JsonValue = None,
    log: DerivationLog | None = None,
    sql: JsonValue = SQL,
) -> str:
    _loaded(written)
    return (store.derivations if log is None else log).issue(
        derivation=cohort.id,
        kind="cohort",
        hashed=cohort.identity.hashed(),
        releases=[cohort.release.model_dump(mode="json")],
        tool=tool,
        written=written,
        params={} if params is None else params,
        sql=None if values_from is not None else sql,
        engine="aibi 0.0.1",
        packs={},
        values_from=values_from,
    )


AGE = {"kind": "value", "column": "members.age", "range": {"gte": 30}}


def test_a_derivation_is_recorded_once_with_the_object_its_id_hashes(
    store: Store, imported: str
) -> None:
    cohort, written = _cohort(store, imported, [AGE])
    first = _issue(store, cohort, written)
    second = _issue(store, cohort, written)
    record = store.derivations.derivation(cohort.id)
    assert record is not None
    assert (record.kind, record.hashed) == ("cohort", cohort.identity.hashed())
    assert record.releases == [
        {"dataset": "lib", "label": 1, "manifest": imported, "status": "published"}
    ]
    assert store.derivations.issuances(cohort.id) == [first, second]
    issuance = store.derivations.issuance(first)
    assert issuance is not None
    assert (issuance.written, issuance.sql, issuance.cache_hit) == (written, SQL, False)
    assert store.db.connection.execute("SELECT count(*) FROM derivations").fetchone() == (1,)


def test_a_derivation_id_is_recorded_only_with_the_object_it_is_the_hash_of(
    store: Store, imported: str
) -> None:
    cohort, _ = _cohort(store, imported, [AGE])
    with store.db.transaction() as db, pytest.raises(DerivationConflictError):
        store.derivations.record(db, cohort.id, "cohort", {"cohort": {}}, [])
    assert store.derivations.derivation(cohort.id) is None


def test_issuance_ids_increase_within_a_millisecond_and_when_the_clock_steps_back() -> None:
    times = iter([5_000_000, 5_000_000, 5_000_000, 4_000_000, 6_000_000])
    ulids = Ulids(lambda: next(times), lambda size: b"\xff" * size)
    made = [ulids() for _ in range(5)]
    assert made == sorted(made)
    assert len(set(made)) == 5
    assert made[1] == ulid(6, bytes(10))  # the randomness spent, the next millisecond
    assert made[2] == ulid(6, (1).to_bytes(10, "big"))
    assert made[3] == ulid(6, (2).to_bytes(10, "big"))
    many = [new_issuance_id() for _ in range(1_000)]
    assert many == sorted(many)
    assert len(set(many)) == len(many)


def test_issuance_ids_are_ulids_in_crockford_s_base_32() -> None:
    assert ulid(0, bytes(10)) == "0" * 26
    assert ulid(2**48 - 1, b"\xff" * 10) == "7" + "Z" * 25
    assert ulid(1, bytes(10)) > ulid(0, b"\xff" * 10)
    with pytest.raises(ValueError, match="ULID"):
        ulid(2**48, bytes(10))


def test_a_cache_hit_names_the_issuance_whose_sql_produced_its_values(
    store: Store, imported: str
) -> None:
    cohort, written = _cohort(store, imported, [AGE])
    first = _issue(store, cohort, written)
    hit = _issue(store, cohort, written, values_from=first)
    assert ISSUANCE_ID_RE.fullmatch(hit)
    record = store.derivations.issuance(hit)
    assert record is not None
    assert (record.sql, record.values_from, record.cache_hit) == (None, first, True)
    with pytest.raises(ValueError, match="cache hit"):
        store.derivations.issue(
            derivation=cohort.id,
            kind="cohort",
            hashed=cohort.identity.hashed(),
            releases=[],
            tool="count_cohort",
            written=written,
            params={},
            sql=SQL,
            engine="aibi 0.0.1",
            packs={},
            values_from=first,
        )
    other, other_written = _cohort(store, imported, [])
    for source in (hit, first):
        with pytest.raises(ValueError, match="same derivation"):
            _issue(store, other if source == first else cohort, other_written, values_from=source)
    assert store.derivations.issuances(other.id) == []


@pytest.mark.parametrize(
    "statement",
    [
        "DELETE FROM derivations",
        "UPDATE derivations SET kind = 'result'",
        "UPDATE derivations SET hashed = '{}'",
        "DELETE FROM derivation_releases",
        "UPDATE derivation_releases SET dataset = 'other'",
        "UPDATE issuances SET values_from = 'iss:00000000000000000000000000'",
        "UPDATE issuances SET at = '2000-01-01T00:00:00.000000Z'",
        "INSERT OR REPLACE INTO derivations SELECT id, 'result', '{}', '[]', recorded_at"
        " FROM derivations",
        "INSERT INTO derivations VALUES ('drv:' || printf('%064d', 0), 'cohort', NULL, '[]', '')",
        "INSERT OR REPLACE INTO derivation_releases SELECT * FROM derivation_releases",
        "INSERT INTO derivation_releases SELECT derivation, 'other', manifest"
        " FROM derivation_releases",
        "INSERT OR REPLACE INTO issuances SELECT * FROM issuances",
        "UPDATE issuances SET written = '{}'",
        "UPDATE issuances SET params = '{\"[erased]\"'",
        "UPDATE issuances SET sql = NULL",
        "UPDATE issuances SET engine = 'other'",
        "DELETE FROM issuances",
    ],
)
def test_the_log_changes_only_by_erasure_redaction_and_pruning(
    store: Store, imported: str, statement: str
) -> None:
    cohort, written = _cohort(store, imported, [AGE])
    _issue(store, cohort, written)
    with pytest.raises(sqlite3.DatabaseError), store.db.transaction() as db:
        db.execute(statement)


def test_pruning_s_permit_admits_removing_old_count_issuances_only(
    store: Store, imported: str
) -> None:
    cohort, written = _cohort(store, imported, [AGE])
    counted = _issue(store, cohort, written)
    analysed = _issue(store, cohort, written, tool="run_analysis")
    with store.db.transaction() as db:
        db.execute("INSERT INTO log_permits VALUES ('pruning', '9999')")
    with pytest.raises(sqlite3.DatabaseError), store.db.transaction() as db:
        db.execute("DELETE FROM issuances WHERE id = ?", (analysed,))
    with store.db.transaction() as db:
        db.execute("DELETE FROM issuances WHERE id = ?", (counted,))
    assert store.derivations.issuances(cohort.id) == [analysed]


def test_pruning_s_permit_admits_no_removal_of_a_count_issuance_a_kept_one_names(
    store: Store, imported: str
) -> None:
    cohort, written = _cohort(store, imported, [AGE])
    named = _issue(store, cohort, written)
    cutoff = store.now()
    _issue(store, cohort, written, values_from=named)
    with store.db.transaction() as db:
        db.execute("INSERT INTO log_permits VALUES ('pruning', ?)", (cutoff,))
    with pytest.raises(sqlite3.DatabaseError), store.db.transaction() as db:
        db.execute("DELETE FROM issuances WHERE id = ?", (named,))


def test_pruning_leaves_no_permit_behind(store: Store, imported: str) -> None:
    times = iter(["2026-01-01T00:00:09.000000Z"] * 2 + ["2026-01-01T00:00:01.000000Z"])
    log = DerivationLog(store.db, lambda: next(times))
    cohort, written = _cohort(store, imported, [AGE])
    _issue(store, cohort, written, log=log)
    assert log.prune("2026-01-01T00:00:05Z") == 0
    late = _issue(store, cohort, written, log=log)
    assert store.db.connection.execute("SELECT count(*) FROM log_permits").fetchone() == (0,)
    with pytest.raises(sqlite3.DatabaseError), store.db.transaction() as db:
        db.execute("DELETE FROM issuances WHERE id = ?", (late,))


def test_pruning_compares_times_as_instants_whatever_their_spelling(
    store: Store, imported: str
) -> None:
    cohort, written = _cohort(store, imported, [AGE])
    old = _issue(store, cohort, written)
    kept = _issue(store, cohort, written)
    at = store.derivations.issuance(kept).at  # type: ignore[union-attr]
    shifted = datetime.fromisoformat(at).astimezone(timezone(timedelta(hours=2)))
    assert store.derivations.prune(shifted.isoformat()) == 1
    assert store.derivations.issuances(cohort.id) == [kept]
    assert store.derivations.issuance(old) is None
    for spelling in ("2026-01-01T00:00:00", "yesterday"):
        with pytest.raises(ValueError, match="RFC 3339"):
            store.derivations.prune(spelling)


def test_a_permit_is_one_of_each_kind_and_never_changes(store: Store, imported: str) -> None:
    for statement in (
        "INSERT INTO log_permits VALUES ('redaction', '9999')",
        "INSERT INTO log_permits VALUES ('pruning', NULL)",
        "INSERT INTO log_permits VALUES ('other', NULL)",
    ):
        with pytest.raises(sqlite3.DatabaseError), store.db.transaction() as db:
            db.execute(statement)
    with store.db.transaction() as db:
        db.execute("INSERT INTO log_permits VALUES ('pruning', '9999')")
    with pytest.raises(sqlite3.DatabaseError), store.db.transaction() as db:
        db.execute("UPDATE log_permits SET before = '0'")


def test_a_rewrite_of_an_issuance_without_redaction_s_permit_is_refused(
    store: Store, imported: str
) -> None:
    cohort, written = _cohort(store, imported, [AGE])
    _issue(store, cohort, written)
    with pytest.raises(sqlite3.DatabaseError), store.db.transaction() as db:
        db.execute('UPDATE issuances SET written = \'{"forged":"m-99","x":"[erased]"}\'')
    with pytest.raises(sqlite3.DatabaseError), store.db.transaction() as db:
        db.execute("UPDATE derivations SET hashed = NULL")
    assert store.explain(cohort.id).status == "issued"


def test_a_redaction_never_takes_an_issuance_s_sql_to_or_from_none(
    store: Store, imported: str
) -> None:
    cohort, written = _cohort(store, imported, [AGE])
    ran = _issue(store, cohort, written)
    hit = _issue(store, cohort, written, values_from=ran)
    with store.db.transaction() as db:
        db.execute("INSERT INTO log_permits (kind) VALUES ('redaction')")
    for identifier, sql in ((ran, None), (hit, '["[erased]"]')):
        with pytest.raises(sqlite3.DatabaseError), store.db.transaction() as db:
            db.execute("UPDATE issuances SET sql = ? WHERE id = ?", (sql, identifier))


def test_a_redaction_s_rewrite_of_an_issuance_is_admitted(store: Store, imported: str) -> None:
    cohort, written = _cohort(store, imported, [AGE])
    issued = _issue(store, cohort, written)
    with store.db.transaction() as db:
        db.execute("INSERT INTO log_permits (kind) VALUES ('redaction')")
        db.execute(
            "UPDATE issuances SET written = ?, params = ?, sql = ? WHERE id = ?",
            ('{"notes":"[erased]"}', '{"who":"[erased]"}', '["[erased]"]', issued),
        )
        db.execute("DELETE FROM log_permits")
    record = store.derivations.issuance(issued)
    assert record is not None
    assert (record.written, record.params, record.sql) == (
        {"notes": MARK},
        {"who": MARK},
        [MARK],
    )


def test_a_derivation_erased_or_of_another_kind_takes_no_issuance(
    store: Store, imported: str
) -> None:
    cohort, written = _cohort(store, imported, [AGE])
    with store.db.transaction() as db, pytest.raises(DerivationConflictError, match="kind"):
        store.derivations.record(db, cohort.id, "result", cohort.identity.hashed(), [])
    issued = _issue(store, cohort, written)
    with store.db.transaction() as db:
        db.execute("INSERT INTO log_permits (kind) VALUES ('redaction')")
        db.execute("UPDATE derivations SET hashed = NULL WHERE id = ?", (cohort.id,))
        db.execute("DELETE FROM log_permits")
    before = store.derivations.issuances(cohort.id)
    with pytest.raises(DerivationConflictError, match="erased"):
        _issue(store, cohort, written)
    with pytest.raises(sqlite3.DatabaseError), store.db.transaction() as db:
        db.execute(
            "INSERT INTO issuances SELECT ?, derivation, tool, written, params, sql, ?, engine,"
            " packs, at FROM issuances WHERE id = ?",
            (forged := new_issuance_id(), forged, issued),
        )
    with pytest.raises(sqlite3.DatabaseError), store.db.transaction() as db:
        db.execute("DELETE FROM issuances WHERE id = ?", (issued,))
    assert store.derivations.issuances(cohort.id) == before


def test_pruning_removes_old_count_issuances_but_those_a_kept_one_names(
    store: Store, imported: str
) -> None:
    cohort, written = _cohort(store, imported, [AGE])
    named = _issue(store, cohort, written)
    old = _issue(store, cohort, written)
    analysis = _issue(store, cohort, written, tool="run_analysis")
    cutoff = store.now()
    hit = _issue(store, cohort, written, values_from=named)
    assert store.derivations.prune(cutoff) == 1
    assert store.derivations.issuances(cohort.id) == [named, analysis, hit]
    assert store.derivations.issuance(old) is None
    assert store.explain(old).status == "unknown"
    assert store.derivations.derivation(cohort.id) is not None


def test_explain_resolves_an_id_as_the_log_and_its_releases_stand(
    store: Store, imported: str
) -> None:
    cohort, written = _cohort(store, imported, [AGE])
    assert store.explain(cohort.id).status == "not_issued"
    issued = _issue(store, cohort, written)
    explained = store.explain(issued)
    assert (explained.status, explained.issuance, explained.derivation) == (
        "issued",
        store.derivations.issuance(issued),
        store.derivations.derivation(cohort.id),
    )
    opened = open_session(store, "lib", ADA)
    relabel = {"op": "set", "descriptor": "members", "pointer": "/label", "value": "Readers"}
    edits = ChangeRequest.model_validate({"edits": [relabel]})
    draft = change(store, "lib", opened.handle, opened.draft, edits, ADA)
    on_draft, draft_written = _cohort(store, draft, [AGE], label="draft")
    _issue(store, on_draft, draft_written)
    assert on_draft.release.status == "draft"
    assert store.explain(on_draft.id).status == "issued"
    discard(store, "lib", opened.handle, draft, ADA)
    assert store.explain(on_draft.id).status == "discarded"
    store.withdraw("lib", 1, ADA)
    assert store.explain(cohort.id).status == "withdrawn"
    assert store.explain(issued).status == "withdrawn"


def test_a_derivation_s_issuances_are_listed_by_time_then_id(store: Store, imported: str) -> None:
    times = iter(["2026-01-01T00:00:09Z", "2026-01-01T00:00:08Z", "2026-01-01T00:00:07Z"])
    log = DerivationLog(store.db, lambda: next(times))
    cohort, written = _cohort(store, imported, [AGE])
    made = [
        log.issue(
            derivation=cohort.id,
            kind="cohort",
            hashed=cohort.identity.hashed(),
            releases=[cohort.release.model_dump(mode="json")],
            tool="count_cohort",
            written=written,
            params={},
            sql=SQL,
            engine="aibi 0.0.1",
            packs={},
        )
        for _ in range(2)
    ]
    assert log.issuances(cohort.id) == [made[1], made[0]]


def _issued(store: Store, **fields: Any) -> str:
    """An issuance recorded with these fields, its document as written one the loader
    accepts."""
    _loaded(fields["written"])
    return store.derivations.issue(**fields)


EVERY_ROW: JsonValue = {"all": []}
"""The canonical tree of a cohort of no clauses."""


def _derivation(
    store: Store, releases: list[dict[str, Any]], tree: JsonValue = None, unit: str = "members"
) -> str:
    """A cohort derivation over the releases, recorded; its tree over each is ``tree`` (every
    row, by default), which must read back as a cohort the loader accepts (D281)."""
    trees: dict[str, JsonValue] = {}
    for release in releases:
        found = EVERY_ROW if tree is None else tree
        datasets = {release["manifest"]: release["dataset"]}
        _loaded(
            {
                "aibi": "1",
                "dataset": release["dataset"],
                "unit": unit,
                "cohorts": {"c": {"all": [as_document(found, datasets)]}},
            }
        )
        trees[release["manifest"]] = found
    identity = CohortIdentity(form=trees, unit=unit, packs={}, disclosure=None)
    with store.db.transaction() as db:
        store.derivations.record(db, identity.id, "cohort", identity.hashed(), releases)
    return identity.id


def _result(store: Store, cohort: CanonicalCohort, params: JsonValue) -> str:
    """A result derivation over the cohort's release whose view of it has the canonical
    ``params``."""
    view = ViewIdentity(
        analysis="lib.tally",
        version="1",
        cohorts=(cohort.identity,),
        params=params,
        packs={},
        disclosure=None,
    )
    with store.db.transaction() as db:
        store.derivations.record(
            db, view.id, "result", view.hashed(), [cohort.release.model_dump(mode="json")]
        )
    return view.id


def _tree(store: Store, manifest: str, clause: Any, unit: str = "members") -> JsonValue:
    """The canonical tree of a cohort of the one clause over the release (D281)."""
    cohort, _ = _cohort(store, manifest, [clause], label=2, unit=unit)
    return cohort.form[manifest]


def test_explain_puts_withdrawn_before_discarded_before_an_unknown_release(
    store: Store, library: Library, imported: str
) -> None:
    with store.pin() as pin:
        built = store.import_release(
            pin, "lib2", library.descriptors(), library.sources(), library.layouts
        )
        store.publish("lib2", built.manifest.hash, ADA)
    opened = open_session(store, "lib2", ADA)
    relabel = {"op": "set", "descriptor": "members", "pointer": "/label", "value": "Readers"}
    edits = ChangeRequest.model_validate({"edits": [relabel]})
    draft = change(store, "lib2", opened.handle, opened.draft, edits, ADA)
    discard(store, "lib2", opened.handle, draft, ADA)
    withdrawn = {"dataset": "lib", "manifest": imported}
    discarded = {"dataset": "lib2", "manifest": draft}
    unknown = {"dataset": "gone", "manifest": "sha256:" + "0" * 64}
    expected = {
        _derivation(store, [unknown, discarded, withdrawn]): "withdrawn",
        _derivation(store, [unknown, discarded]): "discarded",
        _derivation(store, [unknown]): "unknown_release",
        _derivation(store, [{"dataset": "lib2", "manifest": built.manifest.hash}]): "issued",
    }
    store.withdraw("lib", 1, ADA)
    assert {identifier: store.explain(identifier).status for identifier in expected} == expected


def _without(store: Store, library: Library, key: str) -> str:
    """Re-import and publish the library without the member ``key`` and their loans; the new
    release's manifest hash."""
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


LOAN = {"kind": "exists", "table": "loans"}


@pytest.mark.parametrize(
    ("key", "clauses"),
    [
        ("m-1", []),
        ("m-1", [AGE]),
        ("m-17", [{**AGE, "range": {"gte": 3}}]),
        ("m-17", [{**LOAN, "min_count": 2}]),
        ("m-17", [{**LOAN, "where": [{"kind": "value", "column": "loans.days", "values": [3]}]}]),
        ("m-17", [{"kind": "value", "column": "members.name", "values": ["2"]}]),
    ],
)
def test_erasure_keeps_the_derivations_over_a_live_release_that_hold_none_of_the_person_s_data(
    store: Store, library: Library, imported: str, key: str, clauses: list[Any]
) -> None:
    live = _without(store, library, key)
    cohort, written = _cohort(store, live, clauses, label=2)
    issued = _issue(store, cohort, written)
    assert erase(store, "lib", "members", [key], ADA).redacted
    assert store.explain(cohort.id).status == "issued"
    assert store.derivations.issuances(cohort.id) == [issued]
    record = store.derivations.derivation(cohort.id)
    assert record is not None
    assert record.hashed == cohort.identity.hashed()


@pytest.mark.parametrize(
    ("key", "clauses"),
    [
        ("m-17", [{"kind": "value", "column": "members.member_id", "values": ["m-17"]}]),
        ("m-17", [{"kind": "value", "column": "members.name", "values": ["for m-17 only"]}]),
        (
            "m-17",
            [{**LOAN, "where": [{"kind": "value", "column": "loans.loan_id", "values": [2]}]}],
        ),
        (
            "m-17",
            [
                {
                    **LOAN,
                    "where": [{"kind": "value", "column": "loans.member_id", "values": ["m-17"]}],
                }
            ],
        ),
        (
            "m-1",
            [
                {
                    **LOAN,
                    "where": [{"kind": "value", "column": "loans.loan_id", "range": {"lte": 1}}],
                }
            ],
        ),
    ],
)
def test_erasure_takes_the_derivations_over_a_live_release_that_hold_the_person_s_data(
    store: Store, library: Library, imported: str, key: str, clauses: list[Any]
) -> None:
    live = _without(store, library, key)
    cohort, written = _cohort(store, live, clauses, label=2)
    _issue(store, cohort, written)
    assert erase(store, "lib", "members", [key], ADA).redacted
    assert store.explain(cohort.id).status == "erased"
    assert store.derivations.issuances(cohort.id) == []


def test_erasure_redacts_the_issuances_that_name_the_dataset_and_their_sql(
    store: Store, library: Library, imported: str
) -> None:
    other = {"dataset": "other", "manifest": "sha256:" + "1" * 64}
    elsewhere = _derivation(store, [other])
    naming = {
        "aibi": "1",
        "dataset": "other",
        "unit": "members",
        "cohorts": {"c": {"dataset": "lib", "all": [], "notes": f"see {KEY}"}},
    }
    issue = functools.partial(_issued, store)
    common: dict[str, Any] = {
        "derivation": elsewhere,
        "kind": "cohort",
        "releases": [other],
        "tool": "count_cohort",
        "engine": "aibi 0.0.1",
        "packs": {},
    }
    recorded = store.derivations.derivation(elsewhere)
    assert recorded is not None
    hashed = recorded.hashed
    named = issue(**common, hashed=hashed, written=naming, params={"who": KEY}, sql=[KEY])
    unnamed = issue(
        **common,
        hashed=hashed,
        written={**naming, "cohorts": {"c": {"all": [], "notes": f"see {KEY}"}}},
        params={},
        sql=[KEY],
    )
    cohort, written = _cohort(store, imported, [AGE])
    sql: JsonValue = [{"sql": "SELECT count(*) FROM members WHERE id != ?", "parameters": [KEY]}]
    own = _issued(
        store,
        **{**common, "derivation": cohort.id, "releases": [cohort.release.model_dump(mode="json")]},
        hashed=cohort.identity.hashed(),
        written=written,
        params={},
        sql=sql,
    )
    _without(store, library, KEY)
    assert erase(store, "lib", "members", [KEY], ADA).redacted
    found: dict[str, Any] = {}
    for identifier in (named, unnamed, own):
        record = store.derivations.issuance(identifier)
        assert record is not None
        found[identifier] = record
    assert found[named].written["cohorts"]["c"]["notes"] == f"see {MARK}"
    assert (found[named].params, found[named].sql) == ({"who": MARK}, [MARK])
    assert found[unnamed].sql == [KEY]
    assert found[own].sql == [
        {"sql": "SELECT count(*) FROM members WHERE id != ?", "parameters": [MARK]}
    ]


def _without_the_member(store: Store, library: Library) -> str:
    members = b"\n".join(line for line in library.members.split(b"\n") if KEY.encode() not in line)
    loans = [loan for loan in library.loans if KEY not in loan]
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


def _holds(store: Store, text: str) -> list[str]:
    token = re.compile(rb"(?<![0-9A-Za-z])" + re.escape(text.encode()) + rb"(?![0-9A-Za-z])")
    return [
        path.name
        for path in Path(store.db.path).parent.glob("app.db*")
        if token.search(path.read_bytes())
    ]


def test_erasure_takes_the_derivations_that_hold_the_person_and_redacts_the_rest(
    store: Store, library: Library, imported: str
) -> None:
    named, named_written = _cohort(
        store, imported, [{"kind": "ids", "ids": [f"lib:{KEY}", "lib:m-1"]}]
    )
    other, other_written = _cohort(store, imported, [AGE], notes=f"asked about {KEY}")
    for cohort, written in ((named, named_written), (other, other_written)):
        _issue(store, cohort, written)
    [kept] = store.derivations.issuances(other.id)
    [named_issuance] = store.derivations.issuances(named.id)
    shown = repr(store.explain(named_issuance))
    assert KEY not in shown
    assert named.id in shown
    _without_the_member(store, library)
    assert erase(store, "lib", "members", [KEY], ADA).redacted
    erased = store.explain(named.id)
    assert erased.status == "erased"
    assert erased.derivation is not None
    assert erased.derivation.hashed is None
    assert store.derivations.issuances(named.id) == []
    assert store.explain(other.id).status == "withdrawn"
    issuance = store.derivations.issuance(kept)
    assert issuance is not None
    assert issuance.written["notes"] == f"asked about {MARK}"  # type: ignore[index]
    assert store.derivations.derivation(other.id).hashed == other.identity.hashed()  # type: ignore[union-attr]
    assert _holds(store, KEY) == []


def _import(
    store: Store,
    library: Library,
    members: bytes,
    loans: list[Any],
    descriptors: list[Any] | None = None,
    books: bytes | None = None,
    *,
    extra: Mapping[str, Any] | None = None,
    layouts: Mapping[str, Layout] | None = None,
) -> str:
    """Import and publish a release of the library from these rows, and the ``extra`` sources
    laid out by ``layouts``; its manifest hash."""
    sources = library.sources(members=members, loans=loans)
    if books is not None:
        sources["books"] = TextSource(books)
    sources.update(extra or {})
    with store.pin() as pin:
        built = store.import_release(
            pin,
            "lib",
            library.descriptors() if descriptors is None else descriptors,
            sources,
            library.layouts if layouts is None else layouts,
        )
        store.publish("lib", built.manifest.hash, ADA)
    return built.manifest.hash


NUMBER_KEY = "104233"


@pytest.mark.parametrize(
    "clause",
    [
        {"kind": "value", "column": "members.name", "values": [NUMBER_KEY]},
        {"kind": "value", "column": "members.name", "values": [f"ref {NUMBER_KEY}"]},
        {**LOAN, "where": [{"kind": "value", "column": "loans.member_id", "values": [NUMBER_KEY]}]},
    ],
)
def test_erasure_takes_a_text_key_that_reads_as_a_number_wherever_it_may_name_the_person(
    store: Store, library: Library, clause: dict[str, Any]
) -> None:
    members = library.members.replace(KEY.encode(), NUMBER_KEY.encode())
    loans = [
        tuple(NUMBER_KEY if value == KEY else value for value in loan) for loan in library.loans
    ]
    _import(store, library, members, loans)
    live = _import(
        store,
        library,
        b"\n".join(line for line in members.split(b"\n") if NUMBER_KEY.encode() not in line),
        [loan for loan in loans if NUMBER_KEY not in loan],
    )
    cohort, written = _cohort(store, live, [clause], label=2)
    _issue(store, cohort, written, params={"who": NUMBER_KEY})
    assert erase(store, "lib", "members", [NUMBER_KEY], ADA).redacted
    assert store.explain(cohort.id).status == "erased"
    assert store.derivations.issuances(cohort.id) == []
    assert _holds(store, NUMBER_KEY) == []


def test_erasure_keeps_the_numbers_of_the_issuances_it_redacts_that_name_no_one(
    store: Store, library: Library, imported: str
) -> None:
    live = _without(store, library, KEY)
    named = {**LOAN, "where": [{"kind": "value", "column": "loans.loan_id", "values": ["$n"]}]}
    cohort, written = _cohort(
        store,
        live,
        [{**LOAN, "min_count": "$k"}, {**AGE, "range": {"gte": 3}}],
        label=2,
        extra={"params": {"k": 2, "n": 2}, "cohorts": {"d": {"all": [named]}}},
    )
    issued = _issue(store, cohort, written, params={"k": 2, "n": 2})
    assert erase(store, "lib", "members", [KEY], ADA).redacted
    assert store.explain(cohort.id).status == "issued"
    record = store.derivations.issuance(issued)
    assert record is not None
    assert record.params == {"k": 2, "n": MARK}
    assert record.written == {**cast(dict[str, Any], written), "params": {"k": 2, "n": MARK}}


def test_erasure_keeps_the_format_version_of_the_issuances_it_redacts(
    store: Store, library: Library, imported: str
) -> None:
    live = _without(store, library, "m-1")
    cohort, written = _cohort(store, live, [], label=2, notes="about m-1")
    issued = _issue(store, cohort, written, params={"k": 1})
    assert erase(store, "lib", "members", ["m-1"], ADA).redacted
    record = store.derivations.issuance(issued)
    assert record is not None
    assert record.written == {**cast(dict[str, Any], written), "notes": f"about {MARK}"}
    assert record.params == {"k": 1}


@pytest.mark.parametrize(
    "releases",
    [
        [],
        [{"dataset": "lib", "manifest": "sha256:" + "0" * 64}],
        [{"dataset": "lib"}],
        "twice",
    ],
)
def test_a_cohort_s_releases_are_exactly_those_its_object_is_over(
    store: Store, imported: str, releases: Any
) -> None:
    cohort, _ = _cohort(store, imported, [AGE])
    given = cohort.release.model_dump(mode="json")
    listed = [given, given] if releases == "twice" else releases
    with store.db.transaction() as db, pytest.raises(DerivationConflictError, match="releases"):
        store.derivations.record(db, cohort.id, "cohort", cohort.identity.hashed(), listed)
    assert store.derivations.derivation(cohort.id) is None


def test_a_cohort_canonicalised_before_an_erasure_is_not_issued_after_it(
    store: Store, library: Library, imported: str
) -> None:
    held = [{"kind": "value", "column": "members.member_id", "values": [KEY]}]
    cohort, written = _cohort(store, imported, held)
    _without_the_member(store, library)
    assert erase(store, "lib", "members", [KEY], ADA).redacted
    with pytest.raises(WithdrawnReleaseError):
        _issue(store, cohort, written)
    assert store.derivations.derivation(cohort.id) is None
    assert _holds(store, KEY) == []


def test_a_derivation_takes_no_issuance_once_a_release_of_it_is_withdrawn(
    store: Store, imported: str
) -> None:
    cohort, written = _cohort(store, imported, [AGE])
    other, _ = _cohort(store, imported, [])
    issued = _issue(store, cohort, written)
    store.withdraw("lib", 1, ADA)
    with pytest.raises(WithdrawnReleaseError):
        _issue(store, cohort, written)
    with pytest.raises(sqlite3.DatabaseError), store.db.transaction() as db:
        db.execute(
            "INSERT INTO issuances SELECT ?, derivation, tool, written, params, sql, ?, engine,"
            " packs, at FROM issuances WHERE id = ?",
            (forged := new_issuance_id(), forged, issued),
        )
    hashed = text_of(other.identity.hashed())
    with store.db.transaction() as db:
        db.execute(
            "INSERT INTO derivations VALUES (?, 'cohort', ?, ?, '')",
            (other.id, hashed, text_of([{"dataset": "lib", "manifest": imported}])),
        )
    with pytest.raises(sqlite3.DatabaseError), store.db.transaction() as db:
        db.execute("INSERT INTO derivation_releases VALUES (?, 'lib', ?)", (other.id, imported))
    assert store.derivations.issuances(cohort.id) == [issued]


def test_a_cache_hit_recorded_by_hand_names_an_issuance_of_its_derivation(
    store: Store, imported: str
) -> None:
    cohort, written = _cohort(store, imported, [AGE])
    other, other_written = _cohort(store, imported, [])
    issued = _issue(store, cohort, written)
    elsewhere = _issue(store, other, other_written)
    with pytest.raises(sqlite3.DatabaseError), store.db.transaction() as db:
        db.execute(
            "INSERT INTO issuances SELECT ?, derivation, tool, written, params, NULL, ?, engine,"
            " packs, at FROM issuances WHERE id = ?",
            (new_issuance_id(), elsewhere, issued),
        )


def _numbered(library: Library) -> tuple[list[Any], bytes, list[Any], bytes]:
    """The library with numbers for keys: members, loans and books keyed by integers, a loan's
    book an identifier (a foreign key to another table's rows) and its days one too."""
    numbered = {
        "members.member_id": build.column("members.member_id", "integer", identifier=True),
        "loans.member_id": build.column("loans.member_id", "integer"),
        "loans.book_id": build.column("loans.book_id", "integer", identifier=True),
        "loans.days": build.column("loans.days", "number", units="d", identifier=True),
        "books.book_id": build.column("books.book_id", "integer"),
    }
    descriptors = [numbered.get(descriptor.id, descriptor) for descriptor in library.descriptors()]
    members = library.members.replace(b"m-17", b"17").replace(b"m-1,", b"1,").replace(b"m-", b"")
    books = b"book_id\ttitle\tgenre\n1\tSICP\tcs\n2\tDune\tfiction\n"
    loans = [
        (loan, int(str(member).removeprefix("m-")), int(str(book).removeprefix("b-")), *rest)
        for loan, member, book, *rest in library.loans
    ]
    return descriptors, members, loans, books


@pytest.mark.parametrize(
    ("unit", "clause", "status"),
    [
        ("members", {"kind": "ids", "ids": ["lib:17"]}, "erased"),
        ("members", {"kind": "value", "column": "members.name", "values": ["17"]}, "erased"),
        ("members", {"kind": "value", "column": "members.name", "values": ["17.0"]}, "issued"),
        ("members", {"kind": "value", "column": "members.name", "values": ["+17"]}, "issued"),
        ("members", {"kind": "value", "column": "members.name", "values": ["no. 17"]}, "erased"),
        ("books", {"kind": "ids", "ids": ["lib:2"]}, "issued"),
        (
            "members",
            {**LOAN, "where": [{"kind": "value", "column": "loans.days", "values": [3]}]},
            "erased",
        ),
        (
            "members",
            {**LOAN, "where": [{"kind": "value", "column": "loans.member_id", "values": [17]}]},
            "erased",
        ),
        (
            "members",
            {**LOAN, "where": [{"kind": "value", "column": "loans.book_id", "values": [2]}]},
            "issued",
        ),
        ("members", {"kind": "ids", "ids": ["lib:3"]}, "issued"),
        ("members", {"kind": "value", "column": "members.member_id", "values": [3]}, "issued"),
        ("members", {"kind": "value", "column": "members.member_id", "values": [2]}, "issued"),
        ("members", {"kind": "value", "column": "members.member_id", "values": [17]}, "erased"),
        ("loans", {"kind": "ids", "ids": ["lib:17"]}, "issued"),
        ("loans", {"kind": "ids", "ids": ["lib:3"]}, "erased"),
        ("loans", {"kind": "value", "column": "loans.loan_id", "values": [17]}, "issued"),
        (
            "members",
            {**LOAN, "where": [{"kind": "value", "column": "loans.member_id", "values": [3]}]},
            "issued",
        ),
        (
            "members",
            {**LOAN, "where": [{"kind": "value", "column": "loans.days", "values": [17]}]},
            "issued",
        ),
    ],
)
def test_erasure_takes_numbers_only_on_the_keys_and_columns_that_name_the_person_s_rows(
    store: Store, library: Library, unit: str, clause: dict[str, Any], status: str
) -> None:
    descriptors, members, loans, books = _numbered(library)
    _import(store, library, members, loans, descriptors, books)
    live = _import(
        store,
        library,
        b"\n".join(line for line in members.split(b"\n") if not line.startswith(b"17,")),
        [loan for loan in loans if loan[1] != 17],
        descriptors,
        books,
    )
    cohort, written = _cohort(store, live, [clause], label=2, unit=unit)
    issued = _issue(store, cohort, written)
    assert erase(store, "lib", "members", [17], ADA).redacted
    assert store.explain(cohort.id).status == status
    record = store.derivations.issuance(issued)
    assert (record is None) == (status == "erased")
    assert record is None or record.written == written


def _numbered_live(store: Store, library: Library) -> str:
    """The numbered library (``_numbered``) imported and published, then published again
    without member 17's rows; the live release's manifest hash."""
    descriptors, members, loans, books = _numbered(library)
    _import(store, library, members, loans, descriptors, books)
    return _import(
        store,
        library,
        b"\n".join(line for line in members.split(b"\n") if not line.startswith(b"17,")),
        [loan for loan in loans if loan[1] != 17],
        descriptors,
        books,
    )


def _days(value: JsonValue, units: str | None) -> dict[str, Any]:
    leaf: dict[str, Any] = {"kind": "value", "column": "loans.days", "values": [value]}
    return {**LOAN, "where": [leaf if units is None else {**leaf, "units": units}]}


@pytest.mark.parametrize(
    ("units", "value", "status"),
    [
        (None, 3, "erased"),
        ("d", 3, "erased"),
        ("h", 72, "erased"),
        ("min", 4320, "erased"),
        ("s", 259200, "erased"),
        ("h", 3, "issued"),
        ("h", 48, "issued"),
        ("wk", 3, "issued"),
    ],
)
def test_erasure_reads_a_number_in_other_units_converted_to_its_column_s_as_evaluation_does(
    store: Store, library: Library, units: str | None, value: int, status: str
) -> None:
    live = _numbered_live(store, library)
    old = store.load(store.labels("lib")[0].manifest)
    written = {"aibi": "1", "dataset": "lib", "unit": "members"}
    selected = []
    for clause in (_days(3, None), _days(value, units)):
        loaded = _loaded({**written, "cohorts": {"c": {"all": [clause]}}})
        resolution = resolve(loaded.document, {"lib": old}, loaded.positions)
        selected.append(evaluate(resolution.cohorts["c"]).values)
    assert (selected[0] == selected[1]) == (status == "erased")
    cohort, written_as = _cohort(store, live, [_days(value, units)], label=2)
    _issue(store, cohort, written_as)
    assert erase(store, "lib", "members", [17], ADA).redacted
    assert store.explain(cohort.id).status == status


def test_an_issuance_s_number_in_other_units_is_redacted_converted_and_through_a_parameter(
    store: Store, library: Library
) -> None:
    live = _numbered_live(store, library)
    by_units = {**LOAN, "where": [{**_days("$d", None)["where"][0], "units": "$u"}]}
    extra = {
        "params": {"d": 72, "u": "h", "hours": 3},
        "cohorts": {
            "d": {"all": [_days(72, "h")]},
            "e": {"all": [by_units]},
            "f": {"all": [_days("$hours", "h")]},
            "g": {"all": [_days(3, "h")]},
        },
    }
    cohort, written = _cohort(store, live, [_days(48, "h")], label=2, extra=extra)
    issued = _issue(store, cohort, written)
    assert erase(store, "lib", "members", [17], ADA).redacted
    assert store.explain(cohort.id).status == "issued"
    record = store.derivations.issuance(issued)
    assert record is not None
    document = cast(dict[str, Any], record.written)
    assert document["cohorts"]["c"] == cast(dict[str, Any], written)["cohorts"]["c"]
    assert document["cohorts"]["d"]["all"][0]["where"][0]["values"] == [MARK]
    assert document["cohorts"]["f"] == cast(dict[str, Any], written)["cohorts"]["f"]
    assert document["cohorts"]["g"] == cast(dict[str, Any], written)["cohorts"]["g"]
    assert document["params"] == {"d": MARK, "u": "h", "hours": 3}


def _visits(library: Library, *, named: bool = False) -> tuple[list[Any], dict[str, Layout]]:
    """The library with visits, keyed by their member and a number, their room recorded; with
    ``named``, a member's name and a visit's room are identifiers."""
    descriptors = [
        *library.descriptors(),
        build.table("visits", ["member_id", "seq"], role="event", source=_sheet("visits")),
        build.column("visits.member_id", "string"),
        build.column("visits.seq", "integer"),
        build.column("visits.room", "string", identifier=named),
        build.relationship("visits", ["member_id"], "members", role="visitor"),
    ]
    if named:
        name = build.column("members.name", "string", identifier=True)
        descriptors = [name if d.id == "members.name" else d for d in descriptors]
    layouts = {
        **library.layouts,
        "visits": Layout("visits", (("member_id", "Member"), ("seq", "Seq"), ("room", "Room"))),
    }
    return descriptors, layouts


def _visited(store: Store, library: Library, *, named: bool = False) -> str:
    """The library with visits (``_visits``): ``m-17``'s visits 1 and 2, in rooms r1 and r2,
    and ``m-1``'s 1 and 2, in r3 and r4, imported and published, then published again without
    ``m-17``'s rows; the live release's manifest hash."""
    descriptors, layouts = _visits(library, named=named)
    rows = (("m-17", 1, "r1"), ("m-17", 2, "r2"), ("m-1", 1, "r3"), ("m-1", 2, "r4"))
    for kept in (rows, rows[2:]):
        visits = TypedSource(("Member", "Seq", "Room"), kept)
        members, loans = library.members, list(library.loans)
        if kept is not rows:
            members = b"\n".join(line for line in members.split(b"\n") if b"m-17" not in line)
            loans = [loan for loan in loans if KEY not in loan]
        live = _import(
            store, library, members, loans, descriptors, extra={"visits": visits}, layouts=layouts
        )
    return live


def _visit(*keys: list[Any]) -> dict[str, Any]:
    return {"kind": "ids", "ids": [{"dataset": "lib", "key": key} for key in keys]}


def _seq(**predicate: Any) -> dict[str, Any]:
    leaf = {"kind": "value", "column": "visits.seq", **predicate}
    return {"kind": "exists", "table": "visits", "where": [leaf]}


@pytest.mark.parametrize(
    ("unit", "clause", "status"),
    [
        ("visits", _visit(["m-17", 1]), "erased"),
        ("visits", _visit(["m-17", 9]), "erased"),
        ("visits", _visit(["m-1", 1]), "issued"),
        ("visits", _visit(["m-1", 9]), "issued"),
        ("members", _seq(values=[1]), "issued"),
        ("members", _seq(range={"gte": 2}), "issued"),
        (
            "members",
            {
                "kind": "exists",
                "table": "visits",
                "where": [{"kind": "value", "column": "visits.member_id", "values": [KEY]}],
            },
            "erased",
        ),
    ],
)
def test_a_part_of_a_composite_key_names_a_row_only_through_the_whole_key(
    store: Store, library: Library, unit: str, clause: dict[str, Any], status: str
) -> None:
    live = _visited(store, library)
    cohort, written = _cohort(store, live, [clause], label=2, unit=unit)
    _issue(store, cohort, written)
    assert erase(store, "lib", "members", [KEY], ADA).redacted
    assert store.explain(cohort.id).status == status


def test_an_issuance_s_composite_unit_key_is_redacted_whole_only_when_it_is_the_person_s(
    store: Store, library: Library
) -> None:
    live = _visited(store, library)
    extra = {
        "params": {"k": {"dataset": "lib", "key": ["m-17", 2]}},
        "cohorts": {
            "d": {"all": [_visit(["m-1", 1], ["m-1", 2])]},
            "e": {"all": [{"kind": "ids", "ids": ["$k"]}]},
        },
    }
    seq = {"kind": "value", "column": "visits.seq", "values": [1]}
    cohort, written = _cohort(store, live, [seq], label=2, unit="visits", extra=extra)
    issued = _issue(store, cohort, written)
    assert erase(store, "lib", "members", [KEY], ADA).redacted
    assert store.explain(cohort.id).status == "issued"
    record = store.derivations.issuance(issued)
    assert record is not None
    document = cast(dict[str, Any], record.written)
    assert document["cohorts"] == {
        **cast(dict[str, Any], written)["cohorts"],
        "e": {"all": [{"kind": "ids", "ids": ["$k"]}]},
    }
    assert document["params"] == {"k": {"dataset": "lib", "key": [MARK, MARK]}}


def _room(*values: str) -> dict[str, Any]:
    leaf = {"kind": "value", "column": "visits.room", "values": list(values)}
    return {"kind": "exists", "table": "visits", "where": [leaf]}


@pytest.mark.parametrize(
    ("clause", "status"),
    [
        (_room("Grace"), "erased"),
        (_room("with Grace"), "erased"),
        (_room("r1"), "erased"),
        (_room("r3"), "issued"),
        (_room("Ada"), "issued"),
        ({"kind": "value", "column": "members.name", "values": ["r1"]}, "erased"),
        ({"kind": "value", "column": "members.name", "values": ["r3"]}, "issued"),
    ],
)
def test_an_identifier_column_holds_the_person_s_identifying_values_from_any_table(
    store: Store, library: Library, clause: dict[str, Any], status: str
) -> None:
    live = _visited(store, library, named=True)
    cohort, written = _cohort(store, live, [clause], label=2)
    _issue(store, cohort, written)
    assert erase(store, "lib", "members", [KEY], ADA).redacted
    assert store.explain(cohort.id).status == status


def _loan(column: str, value: JsonValue) -> dict[str, Any]:
    return {**LOAN, "where": [{"kind": "value", "column": column, "values": [value]}]}


@pytest.mark.parametrize(
    ("unit", "clause", "status"),
    [
        ("members", _loan("loans.book_id", KEY), "erased"),
        ("members", _loan("loans.member_id", "Grace"), "erased"),
        ("members", _loan("loans.book_id", "Grace"), "erased"),
        ("books", {"kind": "ids", "ids": ["lib:Grace"]}, "erased"),
        ("members", _loan("loans.book_id", "r1"), "erased"),
        ("members", _loan("loans.book_id", "Ada"), "issued"),
        ("members", _loan("loans.member_id", "r3"), "issued"),
        ("members", _loan("loans.book_id", "b-2"), "issued"),
        ("books", {"kind": "ids", "ids": ["lib:b-1"]}, "issued"),
    ],
)
def test_a_key_or_foreign_key_loses_the_texts_that_identify_the_person_and_no_one_else_s(
    store: Store, library: Library, unit: str, clause: dict[str, Any], status: str
) -> None:
    live = _visited(store, library, named=True)
    cohort, written = _cohort(store, live, [clause], label=2, unit=unit)
    issued = _issue(store, cohort, written)
    assert erase(store, "lib", "members", [KEY], ADA).redacted
    assert store.explain(cohort.id).status == status
    record = store.derivations.issuance(issued)
    assert (record is None) == (status == "erased")
    assert record is None or record.written == written


@pytest.mark.parametrize(
    ("unit", "clause", "status"),
    [
        ("books", {"kind": "ids", "ids": ["lib:b-2"]}, "erased"),
        ("books", {"kind": "ids", "ids": [{"dataset": "lib", "key": ["b-2"]}]}, "erased"),
        ("members", _loan("loans.book_id", "b-2"), "erased"),
        ("members", {"kind": "value", "column": "members.member_id", "values": ["b-2"]}, "erased"),
        ("members", {"kind": "value", "column": "members.name", "values": ["b-2"]}, "erased"),
        ("books", {"kind": "ids", "ids": ["lib:b-1"]}, "issued"),
        ("members", _loan("loans.book_id", "b-1"), "issued"),
    ],
)
def test_a_key_of_a_table_that_holds_none_of_the_person_s_rows_loses_their_text_key_alone(
    store: Store, library: Library, unit: str, clause: dict[str, Any], status: str
) -> None:
    members = library.members.replace(KEY.encode(), b"b-2")
    loans = [tuple("b-2" if value == KEY else value for value in loan) for loan in library.loans]
    _import(store, library, members, loans)
    live = _import(
        store,
        library,
        b"\n".join(line for line in members.split(b"\n") if not line.startswith(b"b-2,")),
        [loan for loan in loans if loan[1] != "b-2"],
    )
    cohort, written = _cohort(store, live, [clause], label=2, unit=unit)
    issued = _issue(store, cohort, written)
    assert erase(store, "lib", "members", ["b-2"], ADA).redacted
    assert store.explain(cohort.id).status == status
    record = store.derivations.issuance(issued)
    assert (record is None) == (status == "erased")
    assert record is None or record.written == written


def test_a_clause_parameter_referred_to_only_from_another_dataset_s_cohort_is_matched_as_one(
    store: Store, library: Library, imported: str
) -> None:
    live = _without(store, library, KEY)
    loan = {**LOAN, "where": [{"kind": "value", "column": "loans.loan_id", "values": [2]}]}
    cohort, written = _cohort(store, live, [AGE], label=2, extra={"params": {"p": loan}})
    document = cast(dict[str, Any], written)
    others = {"d": {"dataset": "other", "all": ["$p"]}, "e": {"all": ["$q"]}}
    document = {
        **document,
        "params": {"p": loan, "q": loan},
        "cohorts": {**document["cohorts"], **others},
    }
    issued = _issue(store, cohort, cast(JsonValue, document))
    assert erase(store, "lib", "members", [KEY], ADA).redacted
    record = store.derivations.issuance(issued)
    assert record is not None
    redacted = {**LOAN, "where": [{"kind": "value", "column": "loans.loan_id", "values": [MARK]}]}
    assert cast(dict[str, Any], record.written)["params"] == {"p": loan, "q": redacted}


HELD = ("member_id", "book_id", "loan_id", "borrowed", "days")
"""The columns of ``holds``, the coverage table: one row per loan whose time and days are
known, as the loans have them."""


def _holding(descriptors: list[Any]) -> list[Any]:
    """The descriptors with ``holds`` and two coverages that use it: of a member's loans,
    scoped by the loans' ids, times and days, and of a book's loans, scoped by their members
    (the reviewer's shape, ``cov:loans.member``, and a foreign key into members as a scope)."""
    types = {d.id: cast(Any, d).fields.datatype for d in descriptors if d.id.startswith("loans.")}
    return [
        *descriptors,
        build.table("holds", ["loan_id"], role="coverage", source=_sheet("holds")),
        *(build.column(f"holds.{name}", types[f"loans.{name}"]) for name in HELD),
        build.coverage(
            "rel:loans.member",
            {
                "table": "holds",
                "parent_columns": {"member_id": "member_id"},
                "scope_columns": {"loan_id": "loan_id", "borrowed": "borrowed", "days": "days"},
            },
        ),
        build.coverage(
            "rel:loans.book",
            {
                "table": "holds",
                "parent_columns": {"book_id": "book_id"},
                "scope_columns": {"member_id": "member_id"},
            },
        ),
    ]


def _live(store: Store, library: Library, variant: str = "plain") -> tuple[str, Any]:
    """The library, with ``holds``, imported and published, then published again without
    ``m-17``'s rows: the live release's manifest hash and ``m-17``'s key. ``numbered`` keys it
    all by integers (``_numbered``), ``borrowed`` makes the time a loan was borrowed a datetime
    identifier, ``boolean`` a boolean one (true for loan 2, unknown for ``m-17``'s other
    loan, false for the rest), ``seven`` gives ``m-17`` the
    text key ``7`` and ``named`` the text key ``kgrace``, which is a name a document may
    give."""
    descriptors, books = library.descriptors(), None
    members, loans, key = library.members, list(library.loans), KEY
    if variant == "numbered":
        descriptors, members, loans, books = _numbered(library)
        key = 17
    elif variant == "borrowed":
        descriptors = _borrowed(library)
    elif variant == "boolean":
        descriptors = _borrowed(library, "boolean")
        flags = {2: True, 3: None}
        loans = [(*loan[:3], flags.get(cast(int, loan[0]), False), *loan[4:]) for loan in loans]
    elif variant in ("seven", "named"):
        key = "7" if variant == "seven" else "kgrace"
        members = members.replace(KEY.encode(), key.encode())
        loans = [tuple(key if value == KEY else value for value in loan) for loan in loans]
    descriptors = _holding(descriptors)
    layouts = {
        **library.layouts,
        "holds": Layout("holds", tuple((name, name.title()) for name in HELD)),
    }
    person = f"{key},".encode()

    def published(members: bytes, loans: list[Any]) -> str:
        sources = library.sources(members=members, loans=loans)
        sources["holds"] = TypedSource(
            tuple(name.title() for name in HELD),
            tuple(
                (member, book, loan, borrowed, days)
                for loan, member, book, borrowed, days in loans
                if borrowed is not None and days is not None and not math.isnan(days)
            ),
        )
        if books is not None:
            sources["books"] = TextSource(books)
        return _published(store, "lib", descriptors, sources, layouts)

    published(members, loans)
    live = published(
        b"\n".join(line for line in members.split(b"\n") if not line.startswith(person)),
        [loan for loan in loans if loan[1] != key],
    )
    return live, key


@pytest.mark.parametrize(
    ("variant", "unit", "scope", "status"),
    [
        ("plain", "members", {"loan_id": [2]}, "erased"),
        ("plain", "members", {"loan_id": [4]}, "issued"),
        ("plain", "members", {"days": [3]}, "issued"),
        ("plain", "books", {"member_id": [KEY]}, "erased"),
        ("plain", "books", {"member_id": ["m-1"]}, "issued"),
        ("numbered", "books", {"member_id": [17]}, "erased"),
        ("numbered", "books", {"member_id": [4]}, "issued"),
        ("borrowed", "members", {"borrowed": ["2024-02-03T11:30:00Z"]}, "erased"),
        ("borrowed", "members", {"borrowed": ["2024-02-03T12:30:00+01:00"]}, "erased"),
        ("borrowed", "members", {"borrowed": ["2024-01-02T10:00:00Z"]}, "issued"),
    ],
)
def test_erasure_takes_a_coverage_scope_that_holds_the_person_s_data_on_its_table_s_columns(
    store: Store, library: Library, variant: str, unit: str, scope: JsonValue, status: str
) -> None:
    live, key = _live(store, library, variant)
    covered = {"kind": "covered", "table": "loans", "scope": scope}
    cohort, written = _cohort(store, live, [covered], label=2, unit=unit)
    _issue(store, cohort, written)
    assert erase(store, "lib", "members", [key], ADA).redacted
    assert store.explain(cohort.id).status == status


KEPT: dict[str, Any] = {
    "members": AGE,
    "loans": {"kind": "value", "column": "loans.days", "range": {"gte": 30}},
    "books": {"kind": "value", "column": "books.genre", "values": ["cs"]},
}
"""A clause of each unit that holds none of the person's data."""


@pytest.mark.parametrize(
    ("variant", "unit", "scope", "judged", "named"),
    [
        (
            "plain",
            "members",
            {"loan_id": [2, 4], "days": [3]},
            {"loan_id": [MARK, 4], "days": [3]},
            {"loan_id": [MARK, 4], "days": [MARK]},
        ),
        ("numbered", "books", {"member_id": [17, 4]}, {"member_id": [MARK, 4]}, None),
        (
            "borrowed",
            "members",
            {"borrowed": ["2024-02-03T11:30:00Z", "2024-01-02T10:00:00Z"]},
            {"borrowed": [MARK, "2024-01-02T10:00:00Z"]},
            None,
        ),
    ],
)
def test_an_issuance_s_coverage_scope_is_judged_by_the_columns_of_its_table(
    store: Store,
    library: Library,
    variant: str,
    unit: str,
    scope: dict[str, Any],
    judged: JsonValue,
    named: JsonValue,
) -> None:
    live, key = _live(store, library, variant)
    covered = {"kind": "covered", "table": "loans", "scope": scope}
    extra = {
        "params": {"t": "loans"},
        "cohorts": {"d": {"all": [covered]}, "e": {"all": [{**covered, "table": "$t"}]}},
    }
    cohort, written = _cohort(store, live, [KEPT[unit]], label=2, unit=unit, extra=extra)
    issued = _issue(store, cohort, written, params={"t": "loans"})
    assert erase(store, "lib", "members", [key], ADA).redacted
    assert store.explain(cohort.id).status == "issued"
    record = store.derivations.issuance(issued)
    assert record is not None
    document = cast(dict[str, Any], record.written)
    assert document["cohorts"]["d"]["all"][0]["scope"] == judged
    assert document["cohorts"]["e"]["all"][0]["scope"] == (judged if named is None else named)
    assert document["params"] == {"t": "loans"}


@pytest.mark.parametrize(
    ("variant", "settings", "clauses", "status"),
    [
        ("plain", {"label": f"about {KEY}"}, {}, "erased"),
        (
            "plain",
            {},
            {"where": ("loans", {"kind": "value", "column": "loans.loan_id", "values": [2]})},
            "erased",
        ),
        ("plain", {}, {"where": ("members", {"kind": "ids", "ids": ["lib:m-1"]})}, "issued"),
        ("plain", {"k": 2, "label": "loans"}, {}, "issued"),
        (
            "plain",
            {},
            {"where": ("loans", {"kind": "value", "column": "loans.days", "values": [2]})},
            "issued",
        ),
        (
            "plain",
            {},
            {"by": ("members", {"kind": "value", "column": "members.name", "values": [KEY]})},
            "erased",
        ),
        ("numbered", {"label": "17.0"}, {}, "erased"),
        (
            "numbered",
            {},
            {"where": ("members", {"kind": "value", "column": "members.name", "values": ["17.0"]})},
            "issued",
        ),
        (
            "numbered",
            {},
            {"where": ("members", {"kind": "value", "column": "members.name", "values": ["17"]})},
            "erased",
        ),
        ("numbered", {}, {"where": ("members", {"kind": "ids", "ids": ["lib:17"]})}, "erased"),
    ],
)
def test_erasure_takes_a_result_whose_parameters_hold_the_person_s_data(
    store: Store,
    library: Library,
    variant: str,
    settings: dict[str, Any],
    clauses: dict[str, tuple[str, Any]],
    status: str,
) -> None:
    live, key = _live(store, library, variant)
    params = dict(settings)
    for name, (unit, clause) in clauses.items():
        params[name] = _tree(store, live, clause, unit)
    cohort, _ = _cohort(store, live, [AGE], label=2)
    identifier = _result(store, cohort, cast(JsonValue, params))
    assert erase(store, "lib", "members", [key], ADA).redacted
    assert store.explain(identifier).status == status


@pytest.mark.parametrize(
    ("cohort", "params"),
    [
        ({"datasets": ["other", "lib"]}, {}),
        ({}, {"datasets": ["other", "lib@2"]}),
    ],
)
def test_erasure_redacts_an_issuance_naming_the_dataset_among_a_cohort_s_datasets_or_a_list(
    store: Store, library: Library, imported: str, cohort: dict[str, Any], params: JsonValue
) -> None:
    other = {"dataset": "other", "manifest": "sha256:" + "1" * 64}
    elsewhere = _derivation(store, [other])
    recorded = store.derivations.derivation(elsewhere)
    assert recorded is not None
    common: dict[str, Any] = {
        "derivation": elsewhere,
        "kind": "cohort",
        "hashed": recorded.hashed,
        "releases": [other],
        "tool": "count_cohort",
        "engine": "aibi 0.0.1",
        "packs": {},
        "sql": [KEY],
    }
    written = {
        "aibi": "1",
        "dataset": "other",
        "unit": "core:person",
        "cohorts": {"c": {"all": [], "notes": f"see {KEY}", **cohort}},
    }
    named = _issued(store, **common, written=written, params=params)
    unnamed = _issued(
        store,
        **common,
        written={**written, "cohorts": {"c": {"all": [], "notes": f"see {KEY}"}}},
        params={"datasets": ["other"]},
    )
    _without(store, library, KEY)
    assert erase(store, "lib", "members", [KEY], ADA).redacted
    found = store.derivations.issuance(named)
    left = store.derivations.issuance(unnamed)
    assert found is not None
    assert left is not None
    assert (found.written["cohorts"]["c"]["notes"], found.sql) == (f"see {MARK}", [MARK])  # type: ignore[index, call-overload]
    assert left.sql == [KEY]


@pytest.mark.parametrize(("unit", "status"), [("core:person", "erased"), ("books", "issued")])
def test_a_concept_unit_s_keys_are_held_as_those_of_a_table_that_holds_the_person(
    store: Store, library: Library, imported: str, unit: str, status: str
) -> None:
    live = _without(store, library, KEY)
    tree: JsonValue = {"all": [{"kind": "ids", "ids": [{"dataset": live, "key": [2]}]}]}
    identifier = _derivation(store, [{"dataset": "lib", "manifest": live}], tree, unit)
    assert erase(store, "lib", "members", [KEY], ADA).redacted
    assert store.explain(identifier).status == status


def _contains(store: Store, text: str) -> list[str]:
    """The app DB's files that hold the text anywhere, bounded or not."""
    return [
        path.name
        for path in Path(store.db.path).parent.glob("app.db*")
        if text.encode() in path.read_bytes()
    ]


BORROWED = "2024-02-03T11:30:00Z"
SPELLINGS = [BORROWED, "2024-02-03T11:30:00+00:00", "2024-02-03T12:30:00+01:00"]
"""One instant, loan 2's, as the canonical form, a cell's canonical string and a client write it."""
INSTANT_TEXTS = ["2024-02-03T11:30", "2024-02-03 11:30", "2024-02-03T12:30"]


def _borrowed(library: Library, datatype: str = "datetime") -> list[Any]:
    """The library's descriptors with the time a loan was borrowed an identifier."""
    marked = build.column("loans.borrowed", datatype, identifier=True)
    return [marked if d.id == "loans.borrowed" else d for d in library.descriptors()]


@pytest.mark.parametrize("spelling", SPELLINGS)
@pytest.mark.parametrize("table", ["loans", "members"])
def test_erasure_takes_a_datetime_identifier_in_every_spelling_of_its_instant(
    store: Store, library: Library, spelling: str, table: str
) -> None:
    descriptors = _borrowed(library)
    _import(store, library, library.members, list(library.loans), descriptors)
    if table == "loans":
        key: Any = 2
        members, loans = library.members, [loan for loan in library.loans if loan[0] != 2]
        unit, held = "loans", {"kind": "value", "column": "loans.borrowed", "values": [spelling]}
    else:
        key = KEY
        members = b"\n".join(line for line in library.members.split(b"\n") if b"m-17" not in line)
        loans = [loan for loan in library.loans if KEY not in loan]
        at = {"kind": "value", "column": "loans.borrowed", "values": [spelling]}
        unit, held = "members", {**LOAN, "where": [at]}
    live = _import(store, library, members, loans, descriptors)
    cohort, written = _cohort(store, live, [held], label=2, unit=unit, notes=f"at {spelling}")
    _issue(store, cohort, written, params={"when": spelling})
    other = {"kind": "value", "column": "loans.borrowed", "values": ["2024-01-02T10:00:00Z"]}
    kept_clause = other if unit == "loans" else {**LOAN, "where": [other]}
    kept, kept_written = _cohort(
        store, live, [kept_clause], label=2, unit=unit, notes=f"not ({spelling})"
    )
    issued = _issue(store, kept, kept_written, params={"when": spelling, "k": [spelling]})
    assert erase(store, "lib", table, [key], ADA).redacted
    assert store.explain(cohort.id).status == "erased"
    assert store.derivations.issuances(cohort.id) == []
    assert store.explain(kept.id).status == "issued"
    record = store.derivations.issuance(issued)
    assert record is not None
    assert record.params == {"when": MARK, "k": [MARK]}
    assert record.written["notes"] == f"not ({MARK})"  # type: ignore[index, call-overload]
    assert [text for text in INSTANT_TEXTS if _contains(store, text)] == []


@pytest.mark.parametrize(
    ("ids", "given", "redacted"),
    [("$k", ["lib:2"], [f"lib:{MARK}"]), (["$k"], "lib:2", f"lib:{MARK}")],
)
def test_erasure_redacts_the_unit_keys_an_ids_clause_takes_from_a_parameter(
    store: Store, library: Library, imported: str, ids: Any, given: Any, redacted: Any
) -> None:
    live = _without(store, library, KEY)
    days = {"kind": "value", "column": "loans.days", "values": [3]}
    cohort, written = _cohort(
        store,
        live,
        [days],
        label=2,
        unit="loans",
        extra={"params": {"k": given}, "cohorts": {"d": {"all": [{"kind": "ids", "ids": ids}]}}},
    )
    issued = _issue(store, cohort, written, params={"k": given})
    assert erase(store, "lib", "members", [KEY], ADA).redacted
    assert store.explain(cohort.id).status == "issued"
    record = store.derivations.issuance(issued)
    assert record is not None
    assert record.params == {"k": redacted}
    assert record.written["params"] == {"k": redacted}  # type: ignore[index, call-overload]


def test_a_release_listed_under_another_dataset_than_its_manifest_s_is_refused(
    store: Store, imported: str
) -> None:
    held = [{"kind": "value", "column": "members.member_id", "values": [KEY]}]
    cohort, _ = _cohort(store, imported, held)
    elsewhere: list[JsonValue] = [{"dataset": "zzz", "manifest": imported}]
    with store.db.transaction() as db, pytest.raises(DerivationConflictError, match="dataset"):
        store.derivations.record(db, cohort.id, "cohort", cohort.identity.hashed(), elsewhere)
    with store.db.transaction() as db:
        db.execute(
            "INSERT INTO derivations VALUES (?, 'cohort', ?, ?, '')",
            (cohort.id, text_of(cohort.identity.hashed()), text_of(cast(JsonValue, elsewhere))),
        )
    with pytest.raises(sqlite3.DatabaseError), store.db.transaction() as db:
        db.execute("INSERT INTO derivation_releases VALUES (?, 'zzz', ?)", (cohort.id, imported))
    rows = store.db.connection.execute("SELECT count(*) FROM derivation_releases").fetchone()
    assert rows == (0,)


def test_a_release_list_with_anything_but_releases_in_it_is_refused(
    store: Store, imported: str
) -> None:
    cohort, _ = _cohort(store, imported, [AGE])
    listed: list[JsonValue] = [cohort.release.model_dump(mode="json"), "x"]
    with store.db.transaction() as db, pytest.raises(DerivationConflictError, match="releases"):
        store.derivations.record(db, cohort.id, "cohort", cohort.identity.hashed(), listed)
    assert store.derivations.derivation(cohort.id) is None


@pytest.mark.parametrize(
    "spelling",
    [
        "20260925T100000Z",
        "2026-W39-5T10:00:00Z",
        "2026-09-25T10:00:00+02",
        "2026-09-25 10:00:00Z",
        "2026-09-25T10:00:00",
        "0000-01-01T00:00:00Z",
        "9999-12-31T23:59:59-01:00",
        "999-01-01T00:00:00Z",
    ],
)
def test_a_pruning_cutoff_is_rfc_3339_within_the_years_a_time_is_written_in(
    store: Store, spelling: str
) -> None:
    with pytest.raises(ValueError, match="RFC 3339"):
        store.derivations.prune(spelling)


def test_a_time_before_the_year_1000_is_written_with_four_digits(
    store: Store, imported: str
) -> None:
    cohort, written = _cohort(store, imported, [AGE])
    issued = _issue(store, cohort, written)
    assert utc("0999-01-01t00:00:00z") == "0999-01-01T00:00:00.000000Z"
    assert stamp(datetime(999, 1, 1, tzinfo=timezone(timedelta(hours=-1)))) == (
        "0999-01-01T01:00:00.000000Z"
    )
    assert store.derivations.prune("0999-01-01T00:00:00Z") == 0
    assert store.derivations.issuances(cohort.id) == [issued]


@pytest.mark.parametrize(
    ("column", "status"), [("core:loan_number", "erased"), ("loans.days", "issued")]
)
def test_a_value_concept_s_numbers_are_held_as_those_of_a_column_that_names_the_person(
    store: Store, library: Library, imported: str, column: str, status: str
) -> None:
    live = _without(store, library, KEY)
    tree: JsonValue = {"all": [{"kind": "value", "column": column, "values": [2]}]}
    identifier = _derivation(store, [{"dataset": "lib", "manifest": live}], tree)
    assert erase(store, "lib", "members", [KEY], ADA).redacted
    assert store.explain(identifier).status == status


def test_a_redaction_is_refused_outside_a_transaction(store: Store, imported: str) -> None:
    with pytest.raises(RuntimeError, match="transaction"):
        redact(store.db.connection, "lib", Terms([KEY]))
    assert store.db.connection.execute("SELECT count(*) FROM log_permits").fetchone() == (0,)


def test_erasure_leaves_no_permit_behind(store: Store, library: Library, imported: str) -> None:
    cohort, written = _cohort(store, imported, [AGE], notes=f"about {KEY}")
    issued = _issue(store, cohort, written)
    _without_the_member(store, library)
    assert erase(store, "lib", "members", [KEY], ADA).redacted
    assert store.db.connection.execute("SELECT count(*) FROM log_permits").fetchone() == (0,)
    with pytest.raises(sqlite3.DatabaseError), store.db.transaction() as db:
        db.execute("UPDATE issuances SET written = '{}' WHERE id = ?", (issued,))


def test_erasure_reads_the_issuances_in_batches_and_redacts_every_one_that_names_the_dataset(
    store: Store, library: Library, imported: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(redaction, "ISSUANCE_BATCH", 2)
    cohort, written = _cohort(store, imported, [AGE], notes=f"about {KEY}")
    made = [_issue(store, cohort, written) for _ in range(5)]
    other: dict[str, Any] = {"dataset": "library", "manifest": "sha256:" + "2" * 64}
    elsewhere = _derivation(store, [other])
    recorded = store.derivations.derivation(elsewhere)
    assert recorded is not None
    unnamed = _issued(
        store,
        derivation=elsewhere,
        kind="cohort",
        hashed=recorded.hashed,
        releases=[other],
        tool="count_cohort",
        written={
            "aibi": "1",
            "dataset": "library",
            "unit": "members",
            "notes": KEY,
            "cohorts": {"c": {"all": []}},
        },
        params={"who": KEY},
        sql=SQL,
        engine="aibi 0.0.1",
        packs={},
    )
    _without_the_member(store, library)
    assert erase(store, "lib", "members", [KEY], ADA).redacted
    notes = [store.derivations.issuance(identifier).written["notes"] for identifier in made]  # type: ignore[union-attr, index, call-overload]
    assert notes == [f"about {MARK}"] * 5
    left = store.derivations.issuance(unnamed)
    assert left is not None
    assert (left.written["notes"], left.params) == (KEY, {"who": KEY})  # type: ignore[index, call-overload]


@pytest.mark.parametrize("held", [1, 2, 3, 4])
def test_erasure_reads_the_derivations_in_batches_and_takes_every_one_that_holds_the_person(
    store: Store, library: Library, imported: str, monkeypatch: pytest.MonkeyPatch, held: int
) -> None:
    monkeypatch.setattr(redaction, "DERIVATION_BATCH", 2)
    live = _without(store, library, KEY)
    named = [_value("members.name", [f"{KEY} {n}"])["all"][0] for n in range(held)]
    aged = [{**AGE, "range": {"gte": 30 + n}} for n in range(3)]
    holding = [_cohort(store, live, [clause], label=2) for clause in named]
    keeping = [_cohort(store, live, [clause], label=2) for clause in aged]
    for cohort, written in holding + keeping:
        _issue(store, cohort, written)
    assert erase(store, "lib", "members", [KEY], ADA).redacted
    assert [store.explain(cohort.id).status for cohort, _ in holding] == ["erased"] * held
    assert [store.explain(cohort.id).status for cohort, _ in keeping] == ["issued"] * 3


@pytest.mark.parametrize(
    ("column", "status"), [("members.member_id", "erased"), ("members.name", "issued")]
)
def test_a_string_on_a_column_that_names_the_person_s_rows_holds_their_number_in_any_spelling(
    store: Store, library: Library, column: str, status: str
) -> None:
    live, key = _live(store, library, "seven")
    cohort, written = _cohort(store, live, [_value(column, ["no. 07"])["all"][0]], label=2)
    _issue(store, cohort, written)
    assert erase(store, "lib", "members", [key], ADA).redacted
    assert store.explain(cohort.id).status == status


class _Equal:
    """The probe pack's one leaf kind, ``probe.equal``: its ``column`` equals its ``value``, on
    the unit's table or, given a ``table``, on one of its rows there. Any other member is the
    leaf's own and reaches no clause."""

    schema: Mapping[str, JsonValue] = {"type": "object"}

    def compile(self, leaf: PackLeaf, release: ReleaseView, pack_version: str) -> Sequence[Clause]:
        members = leaf.model_extra or {}
        clause: dict[str, Any] = {
            "kind": "value",
            "column": members["column"],
            "values": [members["value"]],
        }
        if "table" in members:
            clause = {"kind": "exists", "table": members["table"], "where": [clause]}
        loaded = _loaded(
            {"aibi": "1", "dataset": "d", "unit": "u", "cohorts": {"c": {"all": [clause]}}}
        )
        return list(loaded.document.cohorts["c"].all)

    def summary(self, leaf: PackLeaf) -> Sequence[Segment]:
        return [text("rows whose column equals the value")]


PROBE = PackRegistry(
    [
        Pack(
            manifest=PackManifest(
                id="probe", version="1.0.0", results_version=1, requires_core=">=0"
            ),
            leaf_kinds={"probe.equal": _Equal()},
        )
    ],
    core_version="0.0.1",
)
"""A test-only pack whose leaves the core cannot read as written."""


def _redacted(
    store: Store,
    library: Library,
    cohorts: dict[str, Any],
    params: dict[str, Any],
    *,
    unit: str = "members",
    sql: JsonValue = SQL,
    variant: str = "plain",
    views: list[Any] | None = None,
    others: bool = False,
) -> tuple[Any, Any, Any]:
    """An issuance of a derivation that holds none of the person's data, whose document as
    written has these cohorts, parameters and views, after erasing the person (``_live``'s
    variant): its document, parameters and SQL. The document is loaded and canonicalised, pack
    leaves by the probe pack; with ``others``, over ``other`` too, a dataset of the library's
    rows."""
    live, key = _live(store, library, variant)
    releases = {"lib": store.load(live)}
    labels: dict[str, Any] = {live: 2}
    if others:
        other = _published(
            store, "other", library.descriptors(), library.sources(), library.layouts
        )
        releases["other"] = store.load(other)
        labels[other] = 1
    table = cast(str, params[unit[1:]]) if unit.startswith("$") else unit
    written: dict[str, Any] = {
        "aibi": "1",
        "dataset": "lib",
        "unit": unit,
        "cohorts": {"c": {"all": [KEPT[table]]}, **cohorts},
        "params": params,
    }
    if views is not None:
        written["views"] = views
    loaded = _loaded(written)
    result = canonicalise(
        loaded.document, releases, labels=labels, registry=PROBE, positions=loaded.positions
    )
    assert result.refusals == [], result.refusals
    cohort = result.cohorts["c"]
    issued = _issue(store, cohort, loaded.written, params=cast(JsonValue, params), sql=sql)
    assert erase(store, "lib", "members", [key], ADA).redacted
    assert store.explain(cohort.id).status == "issued"
    record = store.derivations.issuance(issued)
    assert record is not None
    return record.written, record.params, record.sql


def _value(column: str, values: list[Any]) -> dict[str, Any]:
    return {"all": [{"kind": "value", "column": column, "values": values}]}


def test_a_parameter_that_names_the_column_names_the_person_s_rows(
    store: Store, library: Library
) -> None:
    written, params, _ = _redacted(
        store, library, {"d": _value("$col", [2, 7])}, {"col": "loans.days"}, unit="loans"
    )
    assert written["cohorts"]["d"]["all"][0]["values"] == [MARK, 7]
    assert params == {"col": "loans.days"}


@pytest.mark.parametrize(("unit", "names"), [("loans", True), ("$u", True), ("books", False)])
def test_an_issuance_s_unit_keys_are_split_and_judged_by_their_unit(
    store: Store, library: Library, unit: str, names: bool
) -> None:
    ids = ["lib:2", {"dataset": "lib", "key": [2]}, "lib:9"]
    written, _, _ = _redacted(
        store,
        library,
        {"d": {"all": [{"kind": "ids", "ids": ids}]}},
        {"u": "books"},
        unit=unit,
        variant="numbered",
    )
    held = MARK if names else 2
    assert written["cohorts"]["d"]["all"][0]["ids"] == [
        f"lib:{held}",
        {"dataset": "lib", "key": [held]},
        "lib:9",
    ]


def test_a_pack_leaf_as_written_loses_every_number_that_is_a_term_and_its_parameters_too(
    store: Store, library: Library
) -> None:
    leaf = {"kind": "probe.equal", "column": "loans.days", "value": "$p", "n": 2, "m": 7}
    written, params, _ = _redacted(store, library, {"d": {"all": [leaf]}}, {"p": 2}, unit="loans")
    assert written["cohorts"]["d"]["all"][0] == {**leaf, "n": MARK}
    assert params == {"p": MARK}


@pytest.mark.parametrize(("given", "held"), [(True, MARK), (False, False)])
def test_a_pack_leaf_as_written_loses_a_json_boolean_that_is_a_term(
    store: Store, library: Library, given: bool, held: JsonValue
) -> None:
    leaf = {"kind": "probe.equal", "column": "loans.days", "value": 3, "flag": given}
    written, _, _ = _redacted(
        store, library, {"d": {"all": [leaf]}}, {}, unit="loans", variant="boolean"
    )
    assert written["cohorts"]["d"]["all"][0] == {**leaf, "value": MARK, "flag": held}


def test_a_path_given_as_a_parameter_names_no_one_and_is_kept_as_it_is(
    store: Store, library: Library
) -> None:
    via = {"all": [{**LOAN, "via": "$v"}]}
    path = [{"rel": "rel:loans.member", "dir": "down"}]
    written, params, _ = _redacted(store, library, {"d": via}, {"v": path})
    assert params == {"v": path}
    assert written["cohorts"]["d"] == via


@pytest.mark.parametrize("first", ["loans.loan_id", "loans.days"])
def test_a_parameter_used_on_a_column_that_names_the_person_s_rows_is_held_wherever_else_it_is(
    store: Store, library: Library, first: str
) -> None:
    second = "loans.days" if first == "loans.loan_id" else "loans.loan_id"
    cohorts = {"a": _value(first, ["$n"]), "b": _value(second, ["$n"])}
    _, params, _ = _redacted(store, library, cohorts, {"n": 2, "k": 2}, unit="loans")
    assert params == {"n": MARK, "k": 2}


def test_parameter_values_and_sql_are_taken_verbatim_so_no_dollar_string_escapes_redaction(
    store: Store, library: Library
) -> None:
    sql: JsonValue = [{"sql": "SELECT count(*) FROM members", "parameters": [f"${KEY}"]}]
    _, params, redacted_sql = _redacted(
        store, library, {"d": _value("members.name", ["$p"])}, {"p": f"${KEY}"}, sql=sql
    )
    assert params == {"p": f"${MARK}"}
    assert redacted_sql == [{"sql": "SELECT count(*) FROM members", "parameters": [f"${MARK}"]}]


def test_a_cohort_over_another_dataset_puts_its_constants_where_that_dataset_s_columns_do(
    store: Store, library: Library
) -> None:
    clause = {"kind": "value", "column": "loans.loan_id", "values": [2, 3]}
    cohorts = {
        "d": {"dataset": "other", "all": [clause]},
        "e": {"all": [clause]},
        "f": {"dataset": "lib", "all": [clause]},
    }
    written, _, _ = _redacted(store, library, cohorts, {}, unit="loans", others=True)
    assert [written["cohorts"][name]["all"][0]["values"] for name in "def"] == [
        [2, 3],
        [MARK, MARK],
        [MARK, MARK],
    ]


def test_a_view_s_clause_parameters_are_redacted_as_clauses(store: Store, library: Library) -> None:
    where = {"kind": "value", "column": "loans.loan_id", "values": [2]}
    views = [{"analysis": "lib.tally", "cohorts": ["c"], "params": {"where": where, "k": 2}}]
    written, _, _ = _redacted(store, library, {}, {}, unit="loans", views=views)
    assert written["views"][0]["params"] == {"where": {**where, "values": [MARK]}, "k": 2}


VIEWED: dict[str, Any] = {
    "numbered": {"kind": "value", "column": "members.member_id", "values": [17]},
    "plain": {"kind": "value", "column": "loans.loan_id", "values": [2]},
    "borrowed": {
        "kind": "value",
        "column": "loans.borrowed",
        "values": ["2024-02-03T12:30:00+01:00"],
    },
}
"""For each of ``_live``'s variants, a clause that holds the person's data only on the
dataset erased."""


@pytest.mark.parametrize(
    ("variant", "over", "held"),
    [
        ("numbered", ["c"], True),
        ("plain", ["c"], True),
        ("borrowed", ["c"], True),
        ("numbered", ["d"], False),
        ("plain", ["d"], False),
        ("borrowed", ["d"], False),
        ("numbered", None, True),
        ("plain", None, True),
        ("numbered", "$cs", True),
    ],
)
def test_a_view_s_clause_parameters_are_judged_by_the_datasets_of_the_cohorts_it_is_over(
    store: Store, library: Library, variant: str, over: list[str] | str | None, held: bool
) -> None:
    live, key = _live(store, library, variant)
    other = _published(store, "other", library.descriptors(), library.sources(), library.layouts)
    where = VIEWED[variant]
    view: dict[str, Any] = {"analysis": "lib.tally", "params": {"where": where}}
    if over is not None:
        view["cohorts"] = over
    written: dict[str, Any] = {
        "aibi": "1",
        "dataset": "other",
        "unit": "members",
        "cohorts": {"c": {"dataset": "lib", "all": [AGE]}},
        "views": [view],
    }
    if over is not None:
        written["cohorts"]["d"] = {"all": [AGE]}
    if over == "$cs":
        written["params"] = {"cs": ["d"]}
    loaded = _loaded(written)
    result = canonicalise(
        loaded.document,
        {"lib": store.load(live), "other": store.load(other)},
        labels={live: 2, other: 1},
        positions=loaded.positions,
    )
    assert result.refusals == [], result.refusals
    cohort = result.cohorts["c"]
    issued = _issue(store, cohort, loaded.written)
    assert erase(store, "lib", "members", [key], ADA).redacted
    assert store.explain(cohort.id).status == "issued"
    record = store.derivations.issuance(issued)
    assert record is not None
    redacted = record.written["views"][0]["params"]["where"]  # type: ignore[index, call-overload]
    assert redacted == ({**where, "values": [MARK]} if held else where)


def test_an_issuance_names_the_dataset_through_its_document_s_own_parameters(
    store: Store, library: Library
) -> None:
    live, key = _live(store, library)
    other = _published(store, "other", library.descriptors(), library.sources(), library.layouts)
    named = {"kind": "value", "column": "loans.member_id", "values": [key]}
    written = {
        "aibi": "1",
        "dataset": "other",
        "unit": "members",
        "params": {"d": "lib"},
        "cohorts": {
            "c": {"all": [AGE]},
            "e": {"dataset": "$d", "all": [{**LOAN, "where": [named]}]},
        },
    }
    loaded = _loaded(written)
    result = canonicalise(
        loaded.document,
        {"lib": store.load(live), "other": store.load(other)},
        labels={live: 2, other: 1},
        positions=loaded.positions,
    )
    assert result.refusals == [], result.refusals
    cohort = result.cohorts["c"]
    issued = _issue(store, cohort, loaded.written, params={})
    assert erase(store, "lib", "members", [key], ADA).redacted
    record = store.derivations.issuance(issued)
    assert record is not None
    document = cast(dict[str, Any], record.written)
    assert document["cohorts"]["e"]["all"][0]["where"][0]["values"] == [MARK]
    assert document["params"] == {"d": "lib"}
    assert _holds(store, key) == []


@pytest.mark.parametrize(
    ("cohort", "params", "held"),
    [
        ({"datasets": ["other", "lib"]}, {}, True),
        ({"datasets": ["lib", "other"]}, {}, True),
        ({"datasets": "$ds"}, {"ds": ["other", "lib"]}, True),
        ({"datasets": ["other", "another"]}, {"ds": ["lib"]}, False),
    ],
)
def test_a_cross_dataset_cohort_may_be_over_the_dataset_through_any_of_its_datasets(
    store: Store, library: Library, imported: str, cohort: dict[str, Any], params: Any, held: bool
) -> None:
    other = {"dataset": "other", "manifest": "sha256:" + "1" * 64}
    elsewhere = _derivation(store, [other])
    recorded = store.derivations.derivation(elsewhere)
    assert recorded is not None
    loan = {"kind": "value", "column": "loans.loan_id", "values": [2]}
    written = {
        "aibi": "1",
        "dataset": "other",
        "unit": "core:person",
        "params": params,
        "cohorts": {"c": {"all": [{**LOAN, "where": [loan]}], "unmapped": "allow", **cohort}},
    }
    issued = _issued(
        store,
        derivation=elsewhere,
        kind="cohort",
        hashed=recorded.hashed,
        releases=[other],
        tool="count_cohort",
        written=written,
        params=params,
        sql=SQL,
        engine="aibi 0.0.1",
        packs={},
    )
    _without(store, library, KEY)
    assert erase(store, "lib", "members", [KEY], ADA).redacted
    record = store.derivations.issuance(issued)
    assert record is not None
    values = record.written["cohorts"]["c"]["all"][0]["where"][0]["values"]  # type: ignore[index, call-overload]
    assert values == ([MARK] if held else [2])


def test_a_parameter_that_stands_for_structure_loses_its_numbers_as_a_naming_place_s(
    store: Store, library: Library
) -> None:
    views = [{"analysis": "lib.tally", "cohorts": "$cs", "params": "$p"}]
    written, params, _ = _redacted(
        store, library, {}, {"cs": ["c"], "p": {"k": 2, "n": 7}}, unit="loans", views=views
    )
    assert params == {"cs": ["c"], "p": {"k": MARK, "n": 7}}
    assert written["views"] == views
    assert written["params"] == params


def test_a_name_that_is_a_term_is_redacted_alike_wherever_it_is_referred_to(
    store: Store, library: Library
) -> None:
    cohorts = {
        "kgrace": {"all": [{"kind": "value", "column": "members.name", "values": ["$kgrace"]}]},
        "d": {"all": [{"kind": "cohort", "cohort": "kgrace"}]},
    }
    views = [{"analysis": "lib.tally", "cohorts": ["kgrace", "d"], "reference": "kgrace"}]
    written, params, _ = _redacted(
        store, library, cohorts, {"kgrace": "Ada"}, variant="named", views=views
    )
    assert set(written["cohorts"]) == {"c", "d", MARK}
    assert written["cohorts"][MARK]["all"][0]["values"] == [f"${MARK}"]
    assert written["cohorts"]["d"]["all"][0] == {"kind": "cohort", "cohort": MARK}
    assert written["views"][0] == {**views[0], "cohorts": [MARK, "d"], "reference": MARK}
    assert written["params"] == params == {MARK: "Ada"}


@pytest.mark.parametrize("where", ["loan 2", "loan 3"])
def test_a_term_that_is_text_in_any_of_the_person_s_cells_is_erased_as_a_token(
    store: Store, library: Library, where: str
) -> None:
    descriptors = _borrowed(library, "string")
    marked = 2 if where == "loan 2" else 3
    loans = [(*loan[:3], "2" if loan[0] == marked else loan[3], loan[4]) for loan in library.loans]
    _import(store, library, library.members, loans, descriptors)
    members = b"\n".join(line for line in library.members.split(b"\n") if b"m-17" not in line)
    left = [loan for loan in loans if KEY not in loan]
    live = _import(store, library, members, left, descriptors)
    cohort, written = _cohort(store, live, [AGE], label=2, notes="see page 2")
    issued = _issue(store, cohort, written)
    assert erase(store, "lib", "members", [KEY], ADA).redacted
    record = store.derivations.issuance(issued)
    assert record is not None
    assert record.written["notes"] == f"see page {MARK}"  # type: ignore[index, call-overload]


def test_a_boolean_identifier_below_the_person_is_text_not_a_number(
    store: Store, library: Library
) -> None:
    descriptors = _borrowed(library, "boolean")
    loans = [(*loan[:3], loan[0] == 2, *loan[4:]) for loan in library.loans]
    _import(store, library, library.members, loans, descriptors)
    members = b"\n".join(line for line in library.members.split(b"\n") if b"m-17" not in line)
    left = [loan for loan in loans if KEY not in loan]
    live = _import(store, library, members, left, descriptors)
    cohort, written = _cohort(store, live, [AGE], label=2, notes="overdue true, 2 days")
    issued = _issue(store, cohort, written)
    assert erase(store, "lib", "members", [KEY], ADA).redacted
    record = store.derivations.issuance(issued)
    assert record is not None
    assert record.written["notes"] == f"overdue {MARK}, 2 days"  # type: ignore[index, call-overload]


def test_erasure_keeps_the_format_version_whatever_the_person_s_terms(
    store: Store, library: Library
) -> None:
    descriptors, members, loans, books = _numbered(library)
    _import(store, library, members, loans, descriptors, books)
    live = _import(
        store,
        library,
        b"\n".join(line for line in members.split(b"\n") if not line.startswith(b"1,")),
        [loan for loan in loans if loan[1] != 1],
        descriptors,
        books,
    )
    cohort, written = _cohort(store, live, [AGE], label=2, notes="member 1")
    issued = _issue(store, cohort, written)
    assert erase(store, "lib", "members", [1], ADA).redacted
    assert store.explain(cohort.id).status == "issued"
    record = store.derivations.issuance(issued)
    assert record is not None
    assert record.written == {**cast(dict[str, Any], written), "notes": f"member {MARK}"}


DATATYPES = ["integer", "number", "string", "date", "datetime"]


def _sheet(name: str) -> dict[str, str]:
    return {"kind": "sheet", "name": name, "original_name": name.title()}


def _drawn(kind: str, draw: random.Random) -> Any:
    """A value of the datatype that nothing else in the app DB spells by chance."""
    if kind == "integer":
        return draw.randrange(10_000_000, 100_000_000)
    if kind == "number":
        return draw.randrange(10_000_000, 100_000_000) + draw.choice([0.25, 0.5, 0.75])
    if kind == "string":
        return "k" + "".join(draw.choice("abcdefghijklmnopqrstuvwxyz") for _ in range(9))
    if kind == "date":
        return date(1900, 1, 1) + timedelta(days=draw.randrange(36_000))
    moment = datetime(1950, 1, 1, tzinfo=UTC) + timedelta(
        seconds=draw.randrange(1, 1_500_000_000), microseconds=draw.choice([0, 250_000])
    )
    return moment if moment.second or moment.microsecond else moment + timedelta(seconds=1)


def _spelled(value: Any, draw: random.Random) -> JsonValue:
    """The value as a client may write it as a constant: a datetime at a random offset."""
    if isinstance(value, datetime):
        minutes = draw.choice([0, 0, 60, -300, 330, 765, -600])
        return value.astimezone(timezone(timedelta(minutes=minutes))).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return cast(JsonValue, value)


def _texts(value: Any, spellings: list[JsonValue]) -> list[str]:
    """What the app DB must not hold once the value is erased: its canonical strings and, for a
    datetime, every spelling of its date and time used, at any offset, without the offset."""
    if isinstance(value, datetime):
        utc_time = value.astimezone(UTC)
        found = {f"{utc_time:%Y-%m-%dT%H:%M:%S}", f"{utc_time:%Y-%m-%d %H:%M:%S}"}
        found.update(str(spelling)[:19] for spelling in spellings)
        return sorted(found)
    if isinstance(value, date):
        return [value.isoformat()]
    if isinstance(value, float):
        return [str(value)]
    return [str(value)]


@settings(
    max_examples=25,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow],
)
@given(
    key=st.sampled_from(DATATYPES),
    tag=st.sampled_from(DATATYPES),
    at=st.sampled_from(DATATYPES),
    seed=st.integers(0, 2**32 - 1),
)
def test_after_erasure_no_spelling_of_the_person_s_values_is_left_and_the_rest_stays_issued(
    tmp_path_factory: pytest.TempPathFactory, key: str, tag: str, at: str, seed: int
) -> None:
    draw = random.Random(seed)
    values: dict[str, list[Any]] = {"key": [], "tag": [], "at": []}
    for name, kind, count in (("key", key, 2), ("tag", tag, 2), ("at", at, 3)):
        while len(values[name]) < count:
            value = _drawn(kind, draw)
            taken = [v for found in values.values() for v in found]
            if all(str(value)[:10] != str(other)[:10] for other in taken):
                values[name].append(value)
    people = (
        (values["key"][0], values["tag"][0], "the person"),
        (values["key"][1], values["tag"][1], "someone else"),
    )
    events = (
        (1, values["key"][0], values["at"][0]),
        (2, values["key"][0], None),
        (3, values["key"][1], values["at"][2]),
    )
    descriptors = [
        build.dataset(),
        build.table("people", ["pid"], source=_sheet("people")),
        build.column("people.pid", key),
        build.column("people.tag", tag, identifier=True),
        build.column("people.note", "string"),
        build.table("events", ["eid"], role="event", source=_sheet("events")),
        build.column("events.eid", "integer"),
        build.column("events.pid", key),
        build.column("events.at", at, identifier=True),
        build.relationship("events", ["pid"], "people", role="person"),
    ]
    layouts = {
        "people": Layout("people", (("pid", "Pid"), ("tag", "Tag"), ("note", "Note"))),
        "events": Layout("events", (("eid", "Eid"), ("pid", "Pid"), ("at", "At"))),
    }
    ticks = iter(range(1_000_000))
    store = Store(
        tmp_path_factory.mktemp("store") / "data",
        clock=lambda: datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=next(ticks)),
    )
    try:
        manifests: list[str] = []
        for kept in (slice(0, 2), slice(1, 2)):
            chosen = people[kept]
            sources: dict[str, Any] = {
                "people": TypedSource(("Pid", "Tag", "Note"), chosen),
                "events": TypedSource(
                    ("Eid", "Pid", "At"),
                    tuple(event for event in events if event[1] in {p[0] for p in chosen}),
                ),
            }
            with store.pin() as pin:
                built = store.import_release(pin, "pd", descriptors, sources, layouts)
                store.publish("pd", built.manifest.hash, ADA)
            manifests.append(built.manifest.hash)
        live = manifests[1]
        person = {name: _spelled(values[name][0], draw) for name in ("key", "tag", "at")}
        others = {"key": values["key"][1], "tag": values["tag"][1], "at": values["at"][2]}
        other = {name: _spelled(value, draw) for name, value in others.items()}

        def value(column: str, given: JsonValue) -> dict[str, Any]:
            return {"kind": "value", "column": column, "values": [given]}

        def happened(given: JsonValue) -> dict[str, Any]:
            return {"kind": "exists", "table": "events", "where": [value("events.at", given)]}

        def issued(clause: dict[str, Any], notes: str) -> tuple[str, str]:
            written: dict[str, Any] = {
                "aibi": "1",
                "dataset": "pd",
                "unit": "people",
                "notes": notes,
                "cohorts": {"c": {"all": [clause]}},
            }
            loaded = load_document(json.dumps(written))
            assert loaded.document is not None, loaded.refusals
            result = canonicalise(
                loaded.document,
                {"pd": store.load(live)},
                labels={live: 2},
                positions=loaded.positions,
            )
            assert result.refusals == [], result.refusals
            cohort = result.cohorts["c"]
            return cohort.id, _issue(store, cohort, loaded.written, params={"who": notes})

        told = " ".join(f"({spelling})" for spelling in person.values())
        holding = [
            issued(value("people.pid", person["key"]), "held"),
            issued(value("people.tag", person["tag"]), "held"),
            issued(happened(person["at"]), "held"),
            issued({"kind": "ids", "ids": [{"dataset": "pd", "key": [person["key"]]}]}, "held"),
        ]
        keeping = [
            issued(value("people.pid", other["key"]), told),
            issued(value("people.tag", other["tag"]), told),
            issued(happened(other["at"]), told),
        ]
        assert erase(store, "pd", "people", [values["key"][0]], ADA).redacted
        assert [store.explain(derivation).status for derivation, _ in holding] == ["erased"] * 4
        assert [store.explain(derivation).status for derivation, _ in keeping] == ["issued"] * 3
        texts = [
            *_texts(values["key"][0], [person["key"]]),
            *_texts(values["tag"][0], [person["tag"]]),
            *(_texts(values["at"][0], [person["at"]]) if at not in ("integer", "number") else []),
        ]
        bounded = [text for text in texts if not re.fullmatch(r"[0-9T:\- ]+", text)]
        assert [text for text in bounded if _holds(store, text)] == []
        assert [text for text in texts if text not in bounded and _contains(store, text)] == []
    finally:
        store.close()


ENGINE_SPELLINGS = [
    "2024-02-03T12:30:00.0000000000+01:00",
    "2024-02-03t11:30:00.000000000000z",
    "2024-02-03T06:30:00.0000000-05:00",
]
"""Loan 2's instant as the engine accepts a constant, with more fraction digits than a cell."""


@pytest.mark.parametrize("spelling", ENGINE_SPELLINGS)
def test_erasure_takes_every_spelling_of_a_datetime_the_engine_accepts_wherever_it_is_written(
    store: Store, library: Library, spelling: str
) -> None:
    descriptors = _borrowed(library)
    _import(store, library, library.members, list(library.loans), descriptors)
    left = [loan for loan in library.loans if loan[0] != 2]
    live = _import(store, library, library.members, left, descriptors)
    at = {"kind": "value", "column": "loans.borrowed", "values": [spelling]}
    other = {"kind": "value", "column": "loans.borrowed", "values": ["2024-01-02T10:00:00Z"]}
    leaf = {"kind": "probe.equal", "column": "loans.borrowed", "value": spelling}
    extra = {"cohorts": {"d": {"all": [at]}, "p": {"all": [leaf]}}, "params": {"when": spelling}}
    kept, written = _cohort(
        store,
        live,
        [other],
        label=2,
        unit="loans",
        notes=f"not ({spelling})",
        extra=extra,
        registry=PROBE,
    )
    held, held_written = _cohort(store, live, [at], label=2, unit="loans")
    run: JsonValue = [{"sql": "SELECT count(*) FROM loans WHERE b = ?", "parameters": [spelling]}]
    issued = _issue(store, kept, written, params={"when": spelling}, sql=run)
    _issue(store, held, held_written)
    assert erase(store, "lib", "loans", [2], ADA).redacted
    assert store.explain(held.id).status == "erased"
    assert store.explain(kept.id).status == "issued"
    record = store.derivations.issuance(issued)
    assert record is not None
    document = cast(dict[str, Any], record.written)
    assert record.params == {"when": MARK}
    assert document["params"] == {"when": MARK}
    assert document["notes"] == f"not ({MARK})"
    assert document["cohorts"]["d"]["all"][0]["values"] == [MARK]
    assert document["cohorts"]["p"]["all"][0] == {**leaf, "value": MARK}
    assert record.sql == [{"sql": "SELECT count(*) FROM loans WHERE b = ?", "parameters": [MARK]}]
    assert _contains(store, spelling[:19]) == []
    assert [text for text in INSTANT_TEXTS if _contains(store, text)] == []


def _joined(library: Library) -> list[Any]:
    """The library's descriptors with the day a member joined an identifier."""
    marked = build.column("members.joined", "date", identifier=True)
    return [marked if d.id == "members.joined" else d for d in library.descriptors()]


def _without_rows(
    store: Store,
    library: Library,
    members: bytes,
    loans: list[Any],
    key: str,
    descriptors: list[Any],
) -> str:
    """Import these rows, then publish them without the member ``key`` and their loans; the
    live release's manifest hash."""
    _import(store, library, members, loans, descriptors)
    return _import(
        store,
        library,
        b"\n".join(
            line for line in members.split(b"\n") if not line.startswith(f"{key},".encode())
        ),
        [loan for loan in loans if key not in loan],
        descriptors,
    )


def _seven(library: Library) -> tuple[bytes, list[Any]]:
    """The library with ``m-17``'s key the text ``7``."""
    members = library.members.replace(KEY.encode(), b"7")
    loans = [tuple("7" if value == KEY else value for value in loan) for loan in library.loans]
    return members, loans


def _midnight(library: Library) -> list[Any]:
    """The library's loans with ``m-17``'s loan 3 borrowed at midnight on 2024-01-01."""
    return [
        (*loan[:3], datetime(2024, 1, 1), *loan[4:]) if loan[0] == 3 else loan
        for loan in library.loans
    ]


@pytest.mark.parametrize(
    ("case", "clause"),
    [
        (
            "joined",
            {
                **LOAN,
                "where": [
                    {
                        "kind": "value",
                        "column": "loans.borrowed",
                        "range": {"gte": "2020-01-02T00:00:00Z"},
                    }
                ],
            },
        ),
        ("borrowed", {"kind": "value", "column": "members.joined", "range": {"gte": "2024-01-01"}}),
        ("seven", {"kind": "value", "column": "members.name", "values": ["07"]}),
        ("seven", {"kind": "value", "column": "members.name", "values": ["7.0"]}),
        (
            "seven",
            {**LOAN, "where": [{"kind": "value", "column": "loans.book_id", "values": ["+7"]}]},
        ),
    ],
)
def test_erasure_keeps_a_constant_on_a_column_that_names_no_one_when_only_its_value_is_a_term_s(
    store: Store, library: Library, case: str, clause: dict[str, Any]
) -> None:
    if case == "joined":
        key, members, loans, descriptors = (
            "m-1",
            library.members,
            list(library.loans),
            _joined(library),
        )
    elif case == "borrowed":
        key, members, loans = KEY, library.members, _midnight(library)
        descriptors = _borrowed(library)
    else:
        key, descriptors = "7", library.descriptors()
        members, loans = _seven(library)
    live = _without_rows(store, library, members, loans, key, descriptors)
    held, held_written = _cohort(store, live, [{"kind": "ids", "ids": [f"lib:{key}"]}], label=2)
    cohort, written = _cohort(store, live, [clause], label=2)
    issued = _issue(store, cohort, written)
    _issue(store, held, held_written)
    assert erase(store, "lib", "members", [key], ADA).redacted
    assert store.explain(held.id).status == "erased"
    assert store.explain(cohort.id).status == "issued"
    assert store.derivations.issuances(cohort.id) == [issued]
    record = store.derivations.issuance(issued)
    assert record is not None
    assert record.written == written
    kept = store.derivations.derivation(cohort.id)
    assert kept is not None
    assert kept.hashed == cohort.identity.hashed()


NOTED = [
    "2024-02-03T12:30:00+0100",
    "2024-02-03T12:30:00+01",
    "2024-02-03 12:30:00 +01:00",
    "2024-02-03T11:30Z",
    "2024-02-03 11:30:00Z",
    "2024-02-03T11:30:00.0000000Z",
    "2024-02-03T11:30:00.000000000Z",
    "2024-02-03 11:30 UTC",
]
"""Loan 2's instant in free text, in spellings neither a cell nor the engine reads."""


@pytest.mark.parametrize("spelling", NOTED)
def test_erasure_takes_an_instant_from_free_text_in_every_listed_spelling(
    store: Store, library: Library, spelling: str
) -> None:
    descriptors = _borrowed(library)
    _import(store, library, library.members, list(library.loans), descriptors)
    left = [loan for loan in library.loans if loan[0] != 2]
    live = _import(store, library, library.members, left, descriptors)
    other = {"kind": "value", "column": "loans.borrowed", "values": ["2024-01-02T10:00:00Z"]}
    kept, written = _cohort(store, live, [other], label=2, unit="loans", notes=f"at {spelling}.")
    issued = _issue(store, kept, written)
    assert erase(store, "lib", "loans", [2], ADA).redacted
    record = store.derivations.issuance(issued)
    assert record is not None
    assert record.written["notes"] == f"at {MARK}."  # type: ignore[index, call-overload]


def test_erasure_takes_a_numeric_key_from_free_text_in_its_source_s_spelling_and_others(
    store: Store, library: Library
) -> None:
    descriptors, members, loans, books = _numbered(library)
    members = members.replace(b"\n17,", b"\n0017,")
    _import(store, library, members, loans, descriptors, books)
    live = _import(
        store,
        library,
        b"\n".join(line for line in members.split(b"\n") if not line.startswith(b"0017,")),
        [loan for loan in loans if loan[1] != 17],
        descriptors,
        books,
    )
    notes = "member 0017 (1.7e1, +17, 17.0); not 170 or 1.75"
    cohort, written = _cohort(store, live, [AGE], label=2, notes=notes)
    issued = _issue(store, cohort, written)
    assert erase(store, "lib", "members", [17], ADA).redacted
    record = store.derivations.issuance(issued)
    assert record is not None
    assert record.written["notes"] == (  # type: ignore[index, call-overload]
        f"member {MARK} ({MARK}, +{MARK}, {MARK}.0); not 170 or 1.75"
    )


def test_erasure_takes_each_item_of_a_list_in_an_identifier_column(
    store: Store, library: Library
) -> None:
    marked = build.column(
        "members.interests", "list<category>", identifier=True, missing_codes={"NA": "UNKNOWN"}
    )
    descriptors = [marked if d.id == "members.interests" else d for d in library.descriptors()]
    members = library.members.replace(
        b"m-1,Ada,2020-01-02,36,poetry;maths", b"m-1,Ada,2020-01-02,36,zq-alias-1;maths"
    )
    live = _without_rows(store, library, members, list(library.loans), "m-1", descriptors)
    alias = {"kind": "value", "column": "members.interests", "values": ["zq-alias-1"]}
    held, held_written = _cohort(store, live, [alias], label=2)
    kept, kept_written = _cohort(store, live, [AGE], label=2, notes="aka zq-alias-1")
    _issue(store, held, held_written)
    issued = _issue(store, kept, kept_written)
    assert erase(store, "lib", "members", ["m-1"], ADA).redacted
    assert store.explain(held.id).status == "erased"
    record = store.derivations.issuance(issued)
    assert record is not None
    assert record.written["notes"] == f"aka {MARK}"  # type: ignore[index, call-overload]
    assert _holds(store, "zq-alias-1") == []


@pytest.mark.parametrize("given", [True, False])
def test_erasure_takes_a_json_boolean_on_an_identifier_column_below_the_person(
    store: Store, library: Library, given: bool
) -> None:
    descriptors = _borrowed(library, "boolean")
    loans = [(*loan[:3], loan[0] == 2, *loan[4:]) for loan in library.loans]
    live = _without_rows(store, library, library.members, loans, KEY, descriptors)
    clause = {**LOAN, "where": [{"kind": "value", "column": "loans.borrowed", "values": [given]}]}
    cohort, written = _cohort(store, live, [clause], label=2)
    _issue(store, cohort, written)
    assert erase(store, "lib", "members", [KEY], ADA).redacted
    assert store.explain(cohort.id).status == "erased"


def test_an_issuance_s_unit_key_is_split_at_its_first_colon_so_a_key_may_hold_one(
    store: Store, library: Library
) -> None:
    key = "m:17"
    members = library.members.replace(KEY.encode(), key.encode())
    loans = [tuple(key if value == KEY else value for value in loan) for loan in library.loans]
    live = _without_rows(store, library, members, loans, key, library.descriptors())
    ids = {"kind": "ids", "ids": [f"lib:{key}", "lib:m-1"]}
    cohort, written = _cohort(store, live, [AGE], label=2, extra={"cohorts": {"d": {"all": [ids]}}})
    issued = _issue(store, cohort, written)
    assert erase(store, "lib", "members", [key], ADA).redacted
    record = store.derivations.issuance(issued)
    assert record is not None
    assert record.written["cohorts"]["d"]["all"][0]["ids"] == [f"lib:{MARK}", "lib:m-1"]  # type: ignore[index, call-overload]


def test_a_redaction_empties_the_cache_of_compiled_patterns_however_it_ends(
    store: Store, imported: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    purged: list[None] = []
    monkeypatch.setattr(re, "purge", lambda: purged.append(None))
    with store.db.transaction() as db:
        redact(db, "lib", Terms([KEY]))
    assert len(purged) == 1

    def failing(db: sqlite3.Connection, dataset: str, terms: Terms) -> int:
        raise LookupError("a redactor failed")

    monkeypatch.setitem(redaction.REDACTORS, "failing", failing)
    with pytest.raises(LookupError), store.db.transaction() as db:
        redact(db, "lib", Terms([KEY]))
    assert len(purged) == 2


def _published(
    store: Store, dataset: str, descriptors: list[Any], sources: dict[str, Any], layouts: Any
) -> str:
    with store.pin() as pin:
        built = store.import_release(pin, dataset, descriptors, sources, layouts)
        store.publish(dataset, built.manifest.hash, ADA)
    return built.manifest.hash


def _canonical(store: Store, manifest: str, written: dict[str, Any]) -> tuple[Any, JsonValue]:
    """A document over the release canonicalised, pack leaves by the probe pack: its cohorts,
    and the document as written."""
    loaded = _loaded(written)
    dataset = cast(str, written["dataset"])
    result = canonicalise(
        loaded.document,
        {dataset: store.load(manifest)},
        labels={manifest: 2},
        registry=PROBE,
        positions=loaded.positions,
    )
    assert result.refusals == [], result.refusals
    return result.cohorts, loaded.written


def _fresh(tmp_path_factory: pytest.TempPathFactory) -> Store:
    ticks = iter(range(1_000_000))
    return Store(
        tmp_path_factory.mktemp("store") / "data",
        clock=lambda: datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=next(ticks)),
    )


def _wall(moment: datetime, separator: str = "T") -> str:
    return f"{moment:%Y-%m-%d}{separator}{moment:%H:%M:%S}"


def _engine_spellings(value: Any, draw: random.Random) -> list[JsonValue]:
    """The value as the engine accepts a constant of its datatype: a datetime at three offsets,
    with ``T`` or ``t``, ``Z``, ``z`` or ``+00:00`` at UTC, and a fraction padded with zeros
    beyond the microsecond; any other value as JSON gives it."""
    if isinstance(value, datetime):
        found: list[JsonValue] = []
        for minutes in draw.sample([0, 60, -300, 330, 765, -600, -59], 3):
            local = value.astimezone(timezone(timedelta(minutes=minutes)))
            micro = f"{local.microsecond:06d}".rstrip("0") if local.microsecond else ""
            fraction = micro + "0" * draw.randrange(0 if micro else 1, 12)
            sign = "-" if minutes < 0 else "+"
            zone = f"{sign}{abs(minutes) // 60:02d}:{abs(minutes) % 60:02d}"
            if minutes == 0:
                zone = draw.choice(["Z", "z", "+00:00"])
            separator = draw.choice("Tt")
            found.append(f"{_wall(local, separator)}.{fraction}{zone}")
        return found
    if isinstance(value, date):
        return [value.isoformat()]
    return [cast(JsonValue, value)]


def _exponent(text: str) -> str:
    """A decimal as a mantissa and an exponent: ``12345678.25`` as ``1.234567825e7``."""
    whole, _, fraction = text.partition(".")
    return f"{whole[0]}.{whole[1:]}{fraction}e{len(whole) - 1}"


def _key_texts(value: Any, draw: random.Random) -> list[str]:
    """The value as the engine accepts the key of a unit key written as text."""
    if isinstance(value, bool):
        return ["true" if value else "false"]
    if isinstance(value, int):
        return [str(value)]
    if isinstance(value, float):
        return [number_text(value), _exponent(number_text(value))]
    return [str(spelling) for spelling in _engine_spellings(value, draw)]


MINUTES = "min"
"""The units of a numeric ``at`` in the property tests."""


def _in_other_units(value: Any, integer: bool) -> tuple[JsonValue, str] | None:
    """A constant in other units than ``MINUTES`` that evaluation converts to the value, by one
    multiplication of doubles by the factor rounded to a double (§6.4), and its units; ``None``
    if no near one does."""
    for units in ("h", "s", "ms"):
        ratio = cast(float, scale(units, MINUTES))
        guess = value / ratio
        candidates: list[Any] = (
            [round(guess)]
            if integer
            else [guess, math.nextafter(guess, math.inf), math.nextafter(guess, -math.inf)]
        )
        for candidate in candidates:
            if float(candidate) * ratio == value and candidate != value:
                return candidate, units
    return None


def _free_texts(value: Any, draw: random.Random) -> list[str]:
    """The value as free text may spell it: as a key is written, and a number also as a source
    writes it (a leading zero, a sign, a fraction)."""
    found = _key_texts(value, draw)
    if isinstance(value, int | float) and not isinstance(value, bool):
        text = number_text(value)
        found += [f"0{text}", f"+{text}"] + ([f"{text}.0"] if isinstance(value, int) else [])
    return found


@settings(
    max_examples=20,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow],
)
@given(
    key=st.sampled_from(DATATYPES),
    tag=st.sampled_from(DATATYPES),
    at=st.sampled_from(DATATYPES),
    seed=st.integers(0, 2**32 - 1),
)
def test_no_spelling_the_engine_accepts_of_an_identifying_value_survives_erasure_wherever_it_is(
    tmp_path_factory: pytest.TempPathFactory, key: str, tag: str, at: str, seed: int
) -> None:
    draw = random.Random(seed)
    values: dict[str, list[Any]] = {"key": [], "tag": [], "at": []}
    for name, kind, count in (("key", key, 2), ("tag", tag, 2), ("at", at, 3)):
        while len(values[name]) < count:
            value = _drawn(kind, draw)
            taken = [v for found in values.values() for v in found]
            if all(str(value)[:10] != str(other)[:10] for other in taken):
                values[name].append(value)
    aliases = [_drawn("string", draw) for _ in range(2)]
    rooms = [_drawn("string", draw) for _ in range(2)]
    numeric_at = at in ("integer", "number")
    people = (
        (values["key"][0], values["tag"][0], f"{aliases[0]};shared"),
        (values["key"][1], values["tag"][1], f"{aliases[1]};shared"),
    )
    events = (
        (1, values["key"][0], values["at"][0]),
        (2, values["key"][0], values["at"][1]),
        (3, values["key"][1], values["at"][2]),
    )
    descriptors = [
        build.dataset(),
        build.table("people", ["pid"], source=_sheet("people")),
        build.column("people.pid", key),
        build.column("people.tag", tag, identifier=True),
        build.column("people.aliases", "list<category>", identifier=True),
        build.table("events", ["eid"], role="event", source=_sheet("events")),
        build.column("events.eid", "integer"),
        build.column("events.pid", key),
        build.column(
            "events.at", at, identifier=True, **({"units": MINUTES} if numeric_at else {})
        ),
        build.relationship("events", ["pid"], "people", role="person"),
        build.table("visits", ["pid", "seq"], role="event", source=_sheet("visits")),
        build.column("visits.pid", key),
        build.column("visits.seq", "integer"),
        build.column("visits.room", "string", identifier=True),
        build.relationship("visits", ["pid"], "people", role="visitor"),
        build.table("watch", ["owner", "at"], role="coverage", source=_sheet("watch")),
        build.column("watch.owner", key),
        build.column("watch.pid", key),
        build.column("watch.at", at),
        build.coverage(
            "rel:events.person",
            {
                "table": "watch",
                "parent_columns": {"owner": "pid"},
                "scope_columns": {"pid": "pid", "at": "at"},
            },
        ),
    ]
    layouts = {
        "people": Layout("people", (("pid", "Pid"), ("tag", "Tag"), ("aliases", "Aliases"))),
        "events": Layout("events", (("eid", "Eid"), ("pid", "Pid"), ("at", "At"))),
        "watch": Layout("watch", (("owner", "Owner"), ("pid", "Pid"), ("at", "At"))),
        "visits": Layout("visits", (("pid", "Pid"), ("seq", "Seq"), ("room", "Room"))),
    }
    visits = ((values["key"][0], 1, rooms[0]), (values["key"][1], 1, rooms[1]))
    store = _fresh(tmp_path_factory)
    try:
        for kept in (slice(0, 2), slice(1, 2)):
            chosen = people[kept]
            owners = {person[0] for person in chosen}
            sources: dict[str, Any] = {
                "people": TypedSource(("Pid", "Tag", "Aliases"), chosen),
                "events": TypedSource(
                    ("Eid", "Pid", "At"), tuple(e for e in events if e[1] in owners)
                ),
                "visits": TypedSource(
                    ("Pid", "Seq", "Room"), tuple(v for v in visits if v[0] in owners)
                ),
                "watch": TypedSource(
                    ("Owner", "Pid", "At"), ((values["key"][1], values["key"][1], events[2][2]),)
                ),
            }
            live = _published(store, "pd", descriptors, sources, layouts)
        other_tag = _engine_spellings(values["tag"][1], draw)[0]
        kept_clause = {"kind": "value", "column": "people.tag", "values": [other_tag]}
        identifying = [
            ("key", "people.pid", values["key"][0], True),
            ("tag", "people.tag", values["tag"][0], True),
            ("alias", "people.aliases", aliases[0], True),
            ("at", "events.at", values["at"][0], not numeric_at),
            ("room", "visits.room", rooms[0], True),
        ]
        kept_ids: list[str] = []
        held_ids: list[str] = []
        texts: list[str] = []
        for name, column, value, in_text in identifying:
            spellings = _engine_spellings(value, draw)
            written_as = _free_texts(value, draw) if in_text else []
            table = column.split(".", 1)[0]

            def placed(clause: dict[str, Any], table: str = table) -> dict[str, Any]:
                return clause if table == "people" else {**LOAN, "table": table, "where": [clause]}

            clauses: list[Any] = []
            params: dict[str, Any] = {"col": column}
            for index, spelling in enumerate(spellings):
                params[f"v{index}"] = spelling
                leaf = {"kind": "probe.equal", "column": column, "value": spelling}
                clauses += [
                    placed({"kind": "value", "column": column, "values": [spelling]}),
                    placed({"kind": "value", "column": "$col", "values": [spelling]}),
                    placed({"kind": "value", "column": column, "values": [f"$v{index}"]}),
                    leaf if table == "people" else {**leaf, "table": table},
                ]
                if name in ("tag", "alias"):
                    room = {"kind": "value", "column": "visits.room", "values": [str(spelling)]}
                    clauses.append(placed(room, "visits"))
                if name == "room":
                    clauses.append(
                        {"kind": "value", "column": "people.aliases", "values": [spelling]}
                    )
                if name == "key":
                    clauses += [
                        {"kind": "ids", "ids": [{"dataset": "pd", "key": [spelling]}]},
                        {
                            **LOAN,
                            "table": "events",
                            "where": [
                                {"kind": "value", "column": "events.pid", "values": [spelling]}
                            ],
                        },
                        placed(
                            {"kind": "value", "column": "visits.pid", "values": [spelling]},
                            "visits",
                        ),
                    ]
                if name in ("key", "at"):
                    scope = {"pid" if name == "key" else "at": [spelling]}
                    clauses.append({"kind": "covered", "table": "events", "scope": scope})
            if name == "key":
                clauses += [
                    {"kind": "ids", "ids": [f"pd:{text}"]} for text in _key_texts(value, draw)
                ]
            converted = None
            if name == "at" and numeric_at:
                converted = _in_other_units(value, at == "integer")
            if converted is not None:
                leaf = {"kind": "value", "column": column, "values": [converted[0]]}
                clauses.append(placed({**leaf, "units": converted[1]}))
            for index, text in enumerate(written_as):
                params[f"x{index}"] = text
            keyed = {str(spelling): index for index, spelling in enumerate(spellings)}
            written: dict[str, Any] = {
                "aibi": "1",
                "dataset": "pd",
                "unit": "people",
                "notes": " ".join(f"({text})" for text in written_as),
                "params": params,
                "cohorts": {"c": {"all": [kept_clause]}, "d": {"all": [{"any": clauses}]}},
                "views": [{"analysis": "pd.tally", "cohorts": ["c"], "params": keyed}],
            }
            cohorts, as_written = _canonical(store, live, written)
            for cohort, ids in ((cohorts["c"], kept_ids), (cohorts["d"], held_ids)):
                _issue(store, cohort, as_written, params=cast(JsonValue, params))
                ids.append(cohort.id)
            with store.db.transaction() as db:
                store.db.add_proposal(
                    db,
                    dataset="pd",
                    release=live,
                    descriptor="people.tag",
                    pointer="/fields/label",
                    value=cast(JsonValue, {"said": spellings, "keyed": keyed}),
                    proposer="agent:helper",
                    evidence=" ".join(f"({text})" for text in written_as),
                    at=store.now(),
                )
                store.db.audit(
                    db,
                    store.now(),
                    "pd",
                    ADA,
                    "note",
                    cast(JsonValue, {"said": spellings, **keyed}),
                )
            texts += [str(spelling) for spelling in spellings] + written_as
            if isinstance(value, datetime):
                utc_time = value.astimezone(UTC)
                texts += [_wall(utc_time), _wall(utc_time, " ")]
                texts += [str(spelling)[:19] for spelling in spellings]
        own = {"dataset": "pd", "key": [_engine_spellings(values["key"][1], draw)[0], 1]}
        visited: dict[str, Any] = {
            "aibi": "1",
            "dataset": "pd",
            "unit": "visits",
            "cohorts": {
                "c": {"all": [{"kind": "ids", "ids": [own]}]},
                "d": {
                    "all": [
                        {"kind": "ids", "ids": [{"dataset": "pd", "key": [spelling, 1]}]}
                        for spelling in _engine_spellings(values["key"][0], draw)
                    ]
                },
            },
        }
        cohorts, as_written = _canonical(store, live, visited)
        for cohort, ids in ((cohorts["c"], kept_ids), (cohorts["d"], held_ids)):
            _issue(store, cohort, as_written)
            ids.append(cohort.id)
        assert erase(store, "pd", "people", [values["key"][0]], ADA).redacted
        assert [store.explain(identifier).status for identifier in held_ids] == ["erased"] * 6
        assert [store.explain(identifier).status for identifier in kept_ids] == ["issued"] * 6
        anywhere = [text for text in texts if re.fullmatch(r"[0-9Tt:\- .]+", text[:19])]
        assert [text for text in texts if text not in anywhere and _holds(store, text)] == []
        assert [text for text in anywhere if _contains(store, text)] == []
    finally:
        store.close()


@settings(
    max_examples=20,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow],
)
@given(seed=st.integers(0, 2**32 - 1), midnight=st.booleans(), numeric=st.booleans())
def test_erasure_keeps_constants_that_name_none_of_the_person_s_rows_but_share_a_value_with_a_term(
    tmp_path_factory: pytest.TempPathFactory, seed: int, midnight: bool, numeric: bool
) -> None:
    draw = random.Random(seed)
    base = draw.randrange(1_000_000, 10_000_000)
    near = base * 10 + draw.randrange(10)
    keys: list[Any] = [base, near] if numeric else [f"0{base}", f"0{near}"]
    days = [date(1950, 1, 1) + timedelta(days=draw.randrange(0, 30_000)) for _ in range(2)]
    ats = [
        datetime(2060, 1, 1, tzinfo=UTC)
        + timedelta(days=draw.randrange(0, 30_000), seconds=draw.randrange(1, 86_400))
        for _ in range(2)
    ]
    if midnight:
        ats[0] = ats[0].replace(hour=0, minute=0, second=0)
    seqs = [draw.randrange(1, 1_000), draw.randrange(1_000, 2_000)]
    span = draw.randrange(100_000, 1_000_000) + 0.5
    label = str(keys[0])
    descriptors = [
        build.dataset(),
        build.table("people", ["pid"], source=_sheet("people")),
        build.column("people.pid", "integer" if numeric else "string"),
        build.column("people.tag", "date", identifier=True),
        build.column("people.note", "string"),
        build.column("people.born", "date"),
        build.table("events", ["eid"], role="event", source=_sheet("events")),
        build.column("events.eid", "integer"),
        build.column("events.pid", "integer" if numeric else "string"),
        build.column("events.at", "datetime", identifier=True),
        build.column("events.when", "datetime"),
        build.column("events.count", "number"),
        build.column("events.span", "number", units=MINUTES, identifier=True),
        build.column("events.lid", "string"),
        build.relationship("events", ["pid"], "people", role="person"),
        build.relationship("events", ["lid"], "labels", role="label"),
        build.table("things", ["tid"], source=_sheet("things")),
        build.column("things.tid", "integer"),
        build.table("labels", ["lid"], source=_sheet("labels")),
        build.column("labels.lid", "string"),
        build.table("visits", ["pid", "seq"], role="event", source=_sheet("visits")),
        build.column("visits.pid", "integer" if numeric else "string"),
        build.column("visits.seq", "integer"),
        build.relationship("visits", ["pid"], "people", role="visitor"),
    ]
    layouts = {
        "people": Layout(
            "people", (("pid", "Pid"), ("tag", "Tag"), ("note", "Note"), ("born", "Born"))
        ),
        "events": Layout(
            "events",
            (
                ("eid", "Eid"),
                ("pid", "Pid"),
                ("at", "At"),
                ("when", "When"),
                ("count", "Count"),
                ("span", "Span"),
                ("lid", "Lid"),
            ),
        ),
        "things": Layout("things", (("tid", "Tid"),)),
        "labels": Layout("labels", (("lid", "Lid"),)),
        "visits": Layout("visits", (("pid", "Pid"), ("seq", "Seq"))),
    }
    number = str(base)
    people = (
        (keys[0], days[0], "", None),
        (keys[1], days[1], f"{number} {days[0]}", days[0]),
    )
    eids = (near, base) if numeric else (1, 2)
    events = (
        (eids[0], keys[0], ats[0], None, None, span, None),
        (eids[1], keys[1], ats[1], ats[0], base, span + 1, label),
    )
    visits = ((keys[0], seqs[0]), (keys[1], seqs[0]), (keys[1], seqs[1]))
    store = _fresh(tmp_path_factory)
    try:
        for kept in (slice(0, 2), slice(1, 2)):
            owners = {person[0] for person in people[kept]}
            sources: dict[str, Any] = {
                "people": TypedSource(("Pid", "Tag", "Note", "Born"), people[kept]),
                "events": TypedSource(
                    ("Eid", "Pid", "At", "When", "Count", "Span", "Lid"),
                    tuple(e for e in events if e[1] in owners),
                ),
                "things": TypedSource(("Tid",), ((base,), (near,))),
                "labels": TypedSource(("Lid",), ((label,),)),
                "visits": TypedSource(("Pid", "Seq"), tuple(v for v in visits if v[0] in owners)),
            }
            live = _published(store, "pd", descriptors, sources, layouts)
        day, at = days[0], ats[0]
        before = day - timedelta(days=1)
        when = [
            f"{day}T00:00:00Z",
            f"{day}t00:00:00.000z",
            f"{day}T01:00:00+01:00",
            f"{before}T23:00:00-01:00",
            at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            at.astimezone(timezone(timedelta(hours=5, minutes=30))).isoformat(),
            at.astimezone(timezone(timedelta(hours=-3))).isoformat().replace("T", "t"),
        ]

        def value(column: str, given: JsonValue, bound: bool = False) -> dict[str, Any]:
            clause: dict[str, Any] = {"kind": "value", "column": column}
            clause.update({"range": {"gte": given}} if bound else {"values": [given]})
            return {**LOAN, "table": "events", "where": [clause]} if column[0] == "e" else clause

        spelled = [
            f"{number}.0",
            f"+{number}",
            _exponent(number),
            f"0{number}" if numeric else number,
        ]
        other_key: JsonValue = keys[1]
        clauses = [
            *(value("people.note", text) for text in spelled),
            value("events.count", base),
            value("events.count", float(base), bound=True),
            *(value("events.when", text, bound=index % 2 == 1) for index, text in enumerate(when)),
            value("people.pid", other_key),
            {"kind": "ids", "ids": [{"dataset": "pd", "key": [other_key]}]},
            *(
                {
                    **LOAN,
                    "table": "visits",
                    "where": [{"kind": "value", "column": "visits.seq", **given}],
                }
                for given in ({"values": [seqs[0]]}, {"range": {"lte": seqs[0]}})
            ),
            {
                **LOAN,
                "table": "events",
                "where": [
                    {"kind": "value", "column": "events.span", "values": [span], "units": "h"}
                ],
            },
        ]
        converted = _in_other_units(span, integer=False)
        assert converted is not None
        slower = {
            "kind": "value",
            "column": "events.span",
            "values": [cast(float, converted[0]) * 2],
        }
        clauses.append({**LOAN, "table": "events", "where": [{**slower, "units": converted[1]}]})
        if midnight:
            clauses.append(value("people.born", at.date().isoformat()))
        if numeric:
            clauses += [value("events.pid", eids[0]), value("events.eid", eids[1])]
        units = [("people", clause) for clause in clauses]
        if numeric:
            units += [
                ("events", {"kind": "ids", "ids": [{"dataset": "pd", "key": [eids[1]]}]}),
                ("events", {"kind": "ids", "ids": [f"pd:{eids[1]}"]}),
            ]
        things = [{"dataset": "pd", "key": [base]}] + ([] if numeric else [f"pd:{number}"])
        units += [("things", {"kind": "ids", "ids": [key]}) for key in things]
        labelled = [{"dataset": "pd", "key": [label]}, f"pd:{label}"]
        quoting = [("people", value("events.lid", label))]
        quoting += [("labels", {"kind": "ids", "ids": [key]}) for key in labelled]
        units.append(
            ("visits", {"kind": "ids", "ids": [{"dataset": "pd", "key": [keys[1], seqs[0]]}]})
        )
        issued: list[tuple[Any, JsonValue, str]] = []
        for unit, clause in units + quoting:
            written = {
                "aibi": "1",
                "dataset": "pd",
                "unit": unit,
                "cohorts": {"c": {"all": [clause]}},
            }
            cohorts, as_written = _canonical(store, live, written)
            issued.append((cohorts["c"], as_written, _issue(store, cohorts["c"], as_written)))
        holding = {
            "aibi": "1",
            "dataset": "pd",
            "unit": "people",
            "cohorts": {
                "c": {"all": [{"kind": "value", "column": "people.pid", "values": [keys[0]]}]}
            },
        }
        held, held_written = _canonical(store, live, holding)
        _issue(store, held["c"], held_written)
        assert erase(store, "pd", "people", [keys[0]], ADA).redacted
        assert store.explain(held["c"].id).status == "erased"
        quoted = issued[len(units) :]
        assert [store.explain(cohort.id).status for cohort, _, _ in quoted] == [
            "issued" if numeric else "erased"
        ] * len(quoting)
        for cohort, as_written, issuance in issued[: len(units)] if not numeric else issued:
            assert store.explain(cohort.id).status == "issued", cohort.form
            record = store.derivations.issuance(issuance)
            assert record is not None
            assert record.written == as_written
            derivation = store.derivations.derivation(cohort.id)
            assert derivation is not None
            assert derivation.hashed == cohort.identity.hashed()
    finally:
        store.close()


@pytest.mark.parametrize(
    ("over", "clause", "status"),
    [
        (
            "other",
            {**LOAN, "where": [{"kind": "value", "column": "loans.loan_id", "values": [1]}]},
            "issued",
        ),
        (
            "lib",
            {**LOAN, "where": [{"kind": "value", "column": "loans.loan_id", "values": [1]}]},
            "erased",
        ),
        (
            "other",
            {"kind": "value", "column": "members.name", "values": ["2020-01-02T00:00:00Z"]},
            "issued",
        ),
        ("other", {"kind": "value", "column": "members.name", "values": ["m-1"]}, "erased"),
    ],
)
def test_a_tree_over_another_dataset_s_release_is_matched_by_its_text_alone(
    store: Store, library: Library, over: str, clause: dict[str, Any], status: str
) -> None:
    live = _without_rows(
        store, library, library.members, list(library.loans), "m-1", _joined(library)
    )
    other = _published(store, "other", _joined(library), library.sources(), library.layouts)
    held, _ = _cohort(store, live if over == "lib" else other, [clause], dataset=over)
    trees = {live: EVERY_ROW, other: EVERY_ROW}
    trees[live if over == "lib" else other] = held.form[held.release.manifest]
    identity = CohortIdentity(form=trees, unit="members", packs={}, disclosure=None)
    releases: list[JsonValue] = [
        {"dataset": "lib", "manifest": live},
        {"dataset": "other", "manifest": other},
    ]
    with store.db.transaction() as db:
        store.derivations.record(db, identity.id, "cohort", identity.hashed(), releases)
    assert erase(store, "lib", "members", ["m-1"], ADA).redacted
    assert store.explain(identity.id).status == status


@pytest.mark.parametrize(("table", "status"), [("core:loan", "erased"), ("loans", "issued")])
def test_a_concept_s_coverage_scope_is_held_as_one_on_a_column_that_names_the_person(
    store: Store, library: Library, imported: str, table: str, status: str
) -> None:
    live = _without(store, library, KEY)
    tree: JsonValue = {"kind": "covered", "table": table, "scope": {"days": [3]}}
    identifier = _derivation(store, [{"dataset": "lib", "manifest": live}], tree)
    assert erase(store, "lib", "members", [KEY], ADA).redacted
    assert store.explain(identifier).status == status

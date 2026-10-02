"""``run_analysis`` and ``list_analyses`` over a store (SPEC §7.4, §8, §9, §11.1, §12.2, §13.4;
D316–D323): the orchard, a domain-neutral dataset imported by the core's file importer, as both
transports call them. The SQL a query worker runs is held to the reference evaluator by the
digest: the same document run both ways gives one digest."""

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from typing import Any, cast

import pytest
from pydantic import JsonValue

from aibi.core.analyses import views
from aibi.core.analyses.existence import CohortAt, compare
from aibi.core.analyses.registry import Analyses
from aibi.core.analyses.results import envelope
from aibi.core.catalog import analyses as analyses_module
from aibi.core.catalog.service import Catalog
from aibi.core.catalog.tools import BY_NAME, call
from aibi.core.engine import worker as worker_module
from aibi.core.engine.canonical import canonicalise
from aibi.core.engine.evaluate import evaluate
from aibi.core.engine.queries import run_cohorts
from aibi.core.engine.resolve import ResolvedCohort
from aibi.core.engine.resolved import flipped
from aibi.core.engine.sql import TruthValues, cross
from aibi.core.engine.worker import QueryRefused, Workers
from aibi.core.schema.analyses import ExistenceParams
from aibi.core.schema.catalog import AnalysisListing, DatasetDescription
from aibi.core.schema.caveats import CaveatCode
from aibi.core.schema.cohorts import (
    AnalysisResults,
    CohortCounts,
    DocumentValidation,
    Explanation,
)
from aibi.core.schema.limits import QueryLimits
from aibi.core.schema.loading import load_document
from aibi.core.schema.output import Output
from aibi.core.schema.refusals import Refusal, RefusalCode
from aibi.core.schema.results import ResultEnvelope

World = Any
Orchard = Callable[..., dict[str, bytes]]
WORKERS = Workers(QueryLimits())
APPLE = {"kind": "value", "column": "trees.variety", "values": ["apple"]}
PEAR = {"kind": "value", "column": "trees.variety", "values": ["pear"]}
OLD = {"kind": "value", "column": "trees.tags", "values": ["old"]}
TALL = {"kind": "value", "column": "trees.height_m", "range": {"gte": 5}}
HEAVY = {
    "kind": "exists",
    "table": "harvests",
    "where": [{"kind": "value", "column": "harvests.kg", "range": {"gte": 15}}],
}


def document(
    cohorts: Mapping[str, Sequence[Any]] | None = None,
    view: Mapping[str, Any] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    given = cohorts if cohorts is not None else {"apple": [APPLE], "pear": [PEAR]}
    return {
        "aibi": "1",
        "dataset": "orchard",
        "unit": "trees",
        "cohorts": {name: {"all": list(clauses)} for name, clauses in given.items()},
        "views": [
            dict(
                view
                or {
                    "analysis": "compare.existence",
                    "cohorts": ["apple", "pear"],
                    "params": {"predicates": [TALL, HEAVY]},
                }
            )
        ],
        **extra,
    }


def catalog_of(world: World, **given: Any) -> Catalog:
    return Catalog(world.store, workers=given.pop("workers", WORKERS), **given)


def answer(catalog: Catalog, name: str, body: JsonValue) -> Output | list[Refusal]:
    return call(catalog, BY_NAME[name], json.dumps(body).encode())


def run(catalog: Catalog, written: Mapping[str, Any]) -> AnalysisResults:
    found = answer(catalog, "run_analysis", {"document": dict(written)})
    assert isinstance(found, AnalysisResults), found
    return found


def refused(found: Output | list[Refusal]) -> Refusal:
    assert isinstance(found, list), found
    return found[0]


def explained(catalog: Catalog, identifier: str) -> Explanation:
    found = answer(catalog, "explain", {"id": identifier})
    assert isinstance(found, Explanation), found
    return found


def _lifted(cohort: ResolvedCohort) -> TruthValues | None:
    other = tuple(flipped(clause) for clause in cohort.clauses)
    if other == cohort.clauses:
        return None
    return TruthValues.of(evaluate(replace(cohort, clauses=other)).values)


def evaluated(
    world: World, manifest: str, written: Mapping[str, Any], *, floor: int | None = None
) -> list[ResultEnvelope]:
    """The document's views run by the reference evaluator over the release in memory."""
    release = world.store.load(manifest)
    loaded = load_document(json.dumps(written))
    assert loaded.document is not None
    parsed, refusals = views.parse(loaded.document, loaded.positions, Analyses())
    assert refusals == []
    canonical = canonicalise(
        loaded.document,
        {written["dataset"]: release},
        labels={manifest: 1},
        floor=floor,
        positions=loaded.positions,
        predicates=[p for view in parsed for p in view.predicates],
    )
    found: list[ResultEnvelope] = []
    for view in views.checked(loaded.document, parsed, canonical)[0]:
        positions = [CohortAt(c, evaluate(c.resolved)) for c in view.cohorts]
        crossing = cross(
            [TruthValues.of(evaluate(c.resolved).values) for c in view.cohorts],
            [TruthValues.of(evaluate(p.resolved).values) for p in view.predicates],
            [_lifted(p.resolved) for p in view.predicates],
        )
        assert isinstance(view.params, ExistenceParams)
        outcome = compare(
            positions,
            view.predicates,
            crossing,
            view.params,
            reference=view.reference,
            overlap=bool(crossing.overlapping()) and view.overlap,
            k=view.disclosure,
        )
        found.append(
            envelope(
                view,
                outcome,
                issuance="iss:01J0000000000000000000000A",
                written=dict(written),
                params={},
                engine="aibi test",
            )
        )
    return found


# --- run_analysis ------------------------------------------------------------------------------


def test_run_analysis_computes_what_the_reference_evaluator_computes(
    world: World, orchard: Orchard
) -> None:
    published = world.publish("orchard", orchard())
    written = document()
    [result] = run(catalog_of(world), written).results
    [expected] = evaluated(world, published.manifest, written)
    assert result.digest == expected.digest
    assert result.derivation.id == expected.derivation.id
    assert result.values == expected.values
    assert [cohort.id for cohort in result.cohorts] == [cohort.id for cohort in expected.cohorts]
    assert result.labels[0].data == "apple"
    assert result.derivation.analysis.id == "compare.existence"
    assert result.derivation.releases[0].manifest == published.manifest
    assert result.charts
    assert "$schema" not in result.charts[0]


def test_under_a_floor_the_sql_and_the_evaluator_still_agree(
    world: World, orchard: Orchard
) -> None:
    published = world.publish("orchard", orchard(40))
    written = document({"apple": [APPLE], "pear": [PEAR], "old": [OLD]}, None)
    written["views"] = [
        {
            "analysis": "compare.existence",
            "cohorts": ["apple", "pear", "old"],
            "params": {"predicates": [TALL, HEAVY, OLD]},
            "overlap": "allow",
        }
    ]
    for floor in (2, 3, 5):
        [result] = run(catalog_of(world, floor=floor), written).results
        [expected] = evaluated(world, published.manifest, written, floor=floor)
        assert result.digest == expected.digest
        assert result.derivation.disclosure.min_cell_count == floor
        assert CaveatCode.SUPPRESSED in {caveat.code for caveat in result.caveats}


def test_a_question_through_two_down_steps_runs_under_both_lift_rules_by_sql(
    world: World, orchard: Orchard
) -> None:
    published = world.publish("orchard", orchard(sites=True))
    written = {
        "aibi": "1",
        "dataset": "orchard",
        "unit": "sites",
        "cohorts": {"all": {"all": []}},
        "views": [
            {
                "analysis": "compare.existence",
                "cohorts": ["all"],
                "params": {"predicates": [{"kind": "exists", "table": "trees", "where": [HEAVY]}]},
            }
        ],
    }
    [result] = run(catalog_of(world), written).results
    [expected] = evaluated(world, published.manifest, written)
    assert result.digest == expected.digest
    issuance = explained(catalog_of(world), result.issuance.id).issuance
    assert issuance is not None
    assert isinstance(issuance.sql, dict)
    counts, crossing = cast(list[dict[str, list[str]]], issuance.sql["queries"])
    assert len(counts["statements"]) == 1
    assert '"f0"."v" <> "p0"."v"' in crossing["statements"][0]


def test_a_result_s_population_is_its_cohort_s_count(world: World, orchard: Orchard) -> None:
    world.publish("orchard", orchard())
    catalog = catalog_of(world, floor=3)
    [result] = run(catalog, document()).results
    counts = answer(catalog, "count_cohort", {"document": document()})
    assert isinstance(counts, CohortCounts)
    by_id = {named.count.id: named.count.population for named in counts.counts}
    assert [by_id[cohort.id] for cohort in result.cohorts] == result.population


def test_a_result_is_issued_with_its_cohorts_and_explain_resolves_every_id(
    world: World, orchard: Orchard
) -> None:
    world.publish("orchard", orchard())
    catalog = catalog_of(world)
    before = answer(catalog, "validate_document", {"document": document()})
    assert isinstance(before, DocumentValidation)
    [check] = before.views
    assert check.status == "not_issued"
    [result] = run(catalog, document()).results
    assert check.id == result.derivation.id
    derivation = explained(catalog, result.derivation.id)
    assert derivation.status == "issued"
    assert derivation.derivation is not None
    assert derivation.derivation.kind == "result"
    assert derivation.derivation.object is not None
    assert set(derivation.derivation.object) == {"view", "packs", "disclosure"}
    for cohort in result.cohorts:
        assert explained(catalog, cohort.id).status == "issued"
    issuance = explained(catalog, result.issuance.id)
    assert issuance.issuance is not None
    assert issuance.issuance.tool == "run_analysis"
    sql = issuance.issuance.sql
    assert isinstance(sql, dict)
    queries = sql["queries"]
    assert isinstance(queries, list)
    assert len(queries) == len(result.cohorts) + 1
    crossing = cast(dict[str, list[str]], queries[-1])
    assert len(crossing["statements"]) == len(result.cohorts) + 1
    after = answer(catalog, "validate_document", {"document": document()})
    assert isinstance(after, DocumentValidation)
    assert after.views[0].status == "issued"


def test_a_run_made_again_gives_one_id_and_one_digest_in_a_new_issuance(
    world: World, orchard: Orchard
) -> None:
    world.publish("orchard", orchard())
    catalog = catalog_of(world)
    [first] = run(catalog, document()).results
    renamed = document({"a": [APPLE], "p": [PEAR]}, None)
    renamed["views"] = [
        {
            "analysis": "compare.existence",
            "cohorts": ["a", "p"],
            "params": {"predicates": [TALL, HEAVY]},
        }
    ]
    [second] = run(catalog, renamed).results
    assert (first.derivation.id, first.digest) == (second.derivation.id, second.digest)
    assert first.issuance.id != second.issuance.id
    assert not second.issuance.cache_hit


def test_cohorts_that_share_units_are_refused_with_the_count_of_what_they_share(
    world: World, orchard: Orchard
) -> None:
    world.publish("orchard", orchard())
    catalog = catalog_of(world)
    written = document({"apple": [APPLE], "old": [OLD]}, None)
    written["views"] = [
        {
            "analysis": "compare.existence",
            "cohorts": ["apple", "old"],
            "params": {"predicates": [TALL]},
        }
    ]
    refusal = refused(answer(catalog, "run_analysis", {"document": written}))
    assert (refusal.code, refusal.path) == (RefusalCode.COHORTS_OVERLAP, "/views/0")
    assert refusal.counts is not None
    [shared] = refusal.counts
    both = document({"both": [{"kind": "cohort", "cohort": "apple"}, OLD], "apple": [APPLE]}, None)
    del both["views"]
    counted = answer(catalog, "count_cohort", {"document": both})
    assert isinstance(counted, CohortCounts)
    by_name = {named.cohort.data: named.count for named in counted.counts}
    assert shared.id == by_name["both"].id
    assert shared.population.n_true == by_name["both"].population.n_true
    assert explained(catalog, shared.issuance.id).status == "issued"


def _sharing() -> dict[str, Any]:
    written = document({"apple": [APPLE], "old": [OLD]}, None)
    written["views"] = [
        {
            "analysis": "compare.existence",
            "cohorts": ["apple", "old"],
            "params": {"predicates": [TALL]},
        }
    ]
    return written


def test_the_log_is_freed_of_every_run_analysis_call_refused_or_not(
    world: World, orchard: Orchard
) -> None:
    """Each call's issuances, its results', its cohorts' and those of the units cohorts share
    in a call that is refused, are pruned like ``count_cohort``'s (D318)."""
    world.publish("orchard", orchard())
    catalog = catalog_of(world)
    log = world.store.derivations
    for index in range(4):
        run(catalog, document(params={"call": index}))
        sharing = {**_sharing(), "params": {"call": index}}
        refused(answer(catalog, "run_analysis", {"document": sharing}))
    assert log.usage() > 0
    kinds = {
        row[0]
        for row in world.store.db.connection.execute(
            "SELECT d.kind FROM issuances i JOIN derivations d ON d.id = i.derivation"
        )
    }
    assert kinds == {"cohort", "result"}
    assert log.prune("9999-01-01T00:00:00Z") > 0
    assert log.usage() == log.measured() == 0


@pytest.mark.parametrize(("leaves", "code"), [(64, "COHORTS_OVERLAP"), (65, "LIMIT_EXCEEDED")])
def test_the_units_two_cohorts_share_are_counted_within_a_cohort_s_caps(
    world: World, orchard: Orchard, leaves: int, code: str
) -> None:
    """The cohort of the units two cohorts share is held to the cap on leaves (§7.1), and
    counted up to it; its depth is its deeper cohort's, so no cap on depth applies (D318)."""
    world.publish("orchard", orchard())
    tall = [
        {"kind": "value", "column": "trees.height_m", "range": {"gte": -index}}
        for index in range(1, 32)
    ]
    short = [
        {"kind": "value", "column": "trees.height_m", "range": {"lte": 1000 + index}}
        for index in range(1, leaves - 32)
    ]
    written = document({"apple": [APPLE, *tall], "old": [OLD, *short]}, None)
    written["views"] = _sharing()["views"]
    refusal = refused(answer(catalog_of(world), "run_analysis", {"document": written}))
    assert (refusal.code, refusal.path) == (RefusalCode(code), "/views/0")
    if code == "LIMIT_EXCEEDED":
        assert refusal.limit is not None
        assert (refusal.limit.name, refusal.limit.max) == ("leaves_per_cohort", 64)
    else:
        assert refusal.counts is not None
        assert len(refusal.counts) == 1


def test_cohorts_that_share_units_are_described_alone_when_the_view_allows_it(
    world: World, orchard: Orchard
) -> None:
    world.publish("orchard", orchard())
    written = document({"apple": [APPLE], "old": [OLD]}, None)
    written["views"] = [
        {
            "analysis": "compare.existence",
            "cohorts": ["apple", "old"],
            "overlap": "allow",
            "params": {"predicates": [TALL]},
        }
    ]
    [result] = run(catalog_of(world), written).results
    assert CaveatCode.COHORTS_OVERLAP in {caveat.code for caveat in result.caveats}
    view: Any = result.values.view
    assert view["predicates"][0]["test"]["not_estimable"] == {"/p": "overlapping_cohorts"}


def test_several_views_give_one_result_each_in_document_order(
    world: World, orchard: Orchard
) -> None:
    world.publish("orchard", orchard())
    written = document()
    written["views"].append(
        {"analysis": "compare.existence", "cohorts": ["pear"], "params": {"predicates": [HEAVY]}}
    )
    first, second = run(catalog_of(world), written).results
    assert [c.id for c in second.cohorts] == [first.cohorts[1].id]
    assert first.issuance.id != second.issuance.id


def test_a_query_two_views_ask_runs_once(
    world: World, orchard: Orchard, monkeypatch: pytest.MonkeyPatch
) -> None:
    world.publish("orchard", orchard())
    catalog = catalog_of(world)
    ran: list[tuple[int, int]] = []
    given = analyses_module.run_crossed

    def counted(cohorts: Sequence[Any], crossings: Sequence[Any], *args: Any, **kwargs: Any) -> Any:
        ran.append((len(cohorts), len(crossings)))
        return given(cohorts, crossings, *args, **kwargs)

    monkeypatch.setattr(analyses_module, "run_crossed", counted)
    [once] = run(catalog, document()).results
    written = document()
    written["views"] = written["views"] * 4 + [
        {"analysis": "compare.existence", "cohorts": ["pear"], "params": {"predicates": [APPLE]}}
    ]
    found = run(catalog, written).results
    assert ran[1] == (ran[0][0], ran[0][1] + 1)
    assert [result.digest for result in found[:4]] == [once.digest] * 4


def test_a_document_without_views_is_refused(world: World, orchard: Orchard) -> None:
    world.publish("orchard", orchard())
    written = document()
    del written["views"]
    refusal = refused(answer(catalog_of(world), "run_analysis", {"document": written}))
    assert (refusal.code, refusal.path) == (RefusalCode.MISSING_MEMBER, "/views")


def test_a_catalogue_without_query_workers_runs_nothing(world: World, orchard: Orchard) -> None:
    world.publish("orchard", orchard())
    catalog = Catalog(world.store)
    refusal = refused(answer(catalog, "run_analysis", {"document": document()}))
    assert refusal.code == RefusalCode.NOT_SUPPORTED


def test_a_view_refused_refuses_count_cohort_too(world: World, orchard: Orchard) -> None:
    world.publish("orchard", orchard())
    written = document(view={"analysis": "compare.nothing", "cohorts": ["apple"]})
    refusal = refused(answer(catalog_of(world), "count_cohort", {"document": written}))
    assert (refusal.code, refusal.path) == (RefusalCode.UNKNOWN_ANALYSIS, "/views/0/analysis")


def test_a_draft_s_result_says_so_and_is_never_a_cache_hit(world: World, orchard: Orchard) -> None:
    world.publish("orchard", orchard())
    world.open("orchard")
    [result] = run(catalog_of(world), document(dataset="orchard@draft")).results
    assert result.derivation.releases[0].status == "draft"
    assert CaveatCode.DRAFT_RELEASE in {caveat.code for caveat in result.caveats}
    assert not result.issuance.cache_hit


# --- list_analyses, describe_dataset and resources -----------------------------------------


def test_list_analyses_gives_every_entry_and_its_applicability(
    world: World, orchard: Orchard
) -> None:
    world.publish("orchard", orchard())
    catalog = catalog_of(world)
    found = answer(catalog, "list_analyses", {})
    assert isinstance(found, AnalysisListing)
    assert [entry["id"] for entry in found.analyses] == ["compare.existence"]
    assert found.applicable is None
    found = answer(catalog, "list_analyses", {"dataset": "orchard", "unit": "trees"})
    assert isinstance(found, AnalysisListing)
    assert found.applicable is not None
    assert [(a.analysis, a.status) for a in found.applicable] == [
        ("compare.existence", "available")
    ]
    assert found.release is not None
    assert found.release.label == 1
    refusal = refused(answer(catalog, "list_analyses", {"dataset": "orchard", "unit": "roots"}))
    assert (refusal.code, refusal.path) == (RefusalCode.UNKNOWN_TABLE, "/unit")
    refusal = refused(answer(catalog, "list_analyses", {"unit": "trees"}))
    assert refusal.code == RefusalCode.CONFLICTING_MEMBERS


def test_describe_dataset_names_the_analyses_that_apply(world: World, orchard: Orchard) -> None:
    world.publish("orchard", orchard())
    found = answer(catalog_of(world), "describe_dataset", {"dataset": "orchard"})
    assert isinstance(found, DatasetDescription)
    assert [a.analysis for a in found.applicable_analyses] == ["compare.existence"]


def test_every_analysis_is_a_resource(world: World) -> None:
    catalog = catalog_of(world)
    assert ("aibi://analysis/compare.existence@1.0.0", "compare.existence") in catalog.resources()
    read = json.loads(catalog.read_resource("aibi://analysis/compare.existence@1.0.0"))
    assert read["kind"] == "analysis"
    assert read["version"] == "1.0.0"


def test_sixteen_predicates_over_many_units_fit_an_answer_far_smaller_than_one_value_a_unit(
    world: World, orchard: Orchard, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The answer cap scaled down with the units (D318): 16 predicates over 4,000 units, whose
    truth values a unit each would take more than the cap, as a million units would take more
    than ``MAX_QUERY_ANSWER_BYTES``, are crossed within it, since a crossing's answer depends on
    the number of cohorts and predicates alone."""
    published = world.publish("orchard", orchard(4000, harvests=400))
    cap = 32 << 10
    monkeypatch.setattr(worker_module, "MAX_QUERY_ANSWER_BYTES", cap)
    predicates = [
        {"kind": "value", "column": "trees.height_m", "range": {"gte": 1 + index * 60}}
        for index in range(15)
    ] + [HEAVY]
    written = document(
        view={
            "analysis": "compare.existence",
            "cohorts": ["apple", "pear"],
            "params": {"predicates": predicates},
        }
    )
    [result] = run(catalog_of(world), written).results
    assert len(result.values.model_dump(mode="json")["positions"][0]["predicates"]) == 16
    sources = {published.manifest: world.store.sources(published.manifest)}
    loaded = load_document(json.dumps(written))
    assert loaded.document is not None
    parsed, _ = views.parse(loaded.document, loaded.positions, Analyses())
    canonical = canonicalise(
        loaded.document,
        {"orchard": world.store.load(published.manifest)},
        labels={published.manifest: 1},
        positions=loaded.positions,
        predicates=[p for view in parsed for p in view.predicates],
    )
    [view], _ = views.checked(loaded.document, parsed, canonical)
    with pytest.raises(QueryRefused) as refusal:
        run_cohorts([p.resolved for p in view.predicates], sources, WORKERS, values=True)
    assert refusal.value.refusal.limit is not None
    assert refusal.value.refusal.limit.name == "query_answer_bytes"


def test_an_answer_over_the_cap_names_the_widest_view(
    world: World, orchard: Orchard, monkeypatch: pytest.MonkeyPatch
) -> None:
    world.publish("orchard", orchard())
    monkeypatch.setattr(worker_module, "MAX_QUERY_ANSWER_BYTES", 64)
    written = document()
    written["views"].insert(
        0, {"analysis": "compare.existence", "cohorts": ["pear"], "params": {"predicates": [TALL]}}
    )
    refusal = refused(answer(catalog_of(world), "run_analysis", {"document": written}))
    assert (refusal.code, refusal.path) == (RefusalCode.LIMIT_EXCEEDED, "/views/1")

"""``run_analysis`` of ``survival.km`` over a store (SPEC §5.8, §8.4, §9.5, §12.2; D347–D351): the
orchard, imported by the core's file importer, its trees' months until felled curated as an
endpoint. The rows a query worker lists are held to the reference evaluator's by the result's
digest."""

import json
import math
import time
from collections.abc import Mapping, Sequence
from typing import Any, cast

import pytest
from pydantic import JsonValue

from aibi.core.analyses import survival, timetoevent, views
from aibi.core.analyses.existence import CohortAt
from aibi.core.analyses.registry import Analyses
from aibi.core.analyses.results import envelope
from aibi.core.catalog import analyses as analyses_module
from aibi.core.catalog.cohorts import RECORD_SECONDS
from aibi.core.catalog.service import Catalog, Deadline, within
from aibi.core.catalog.tools import BY_NAME, call
from aibi.core.engine.canonical import canonicalise
from aibi.core.engine.evaluate import evaluate
from aibi.core.engine.inputs import listed, ordered, shared
from aibi.core.engine.worker import Workers
from aibi.core.schema.analyses import SurvivalParams
from aibi.core.schema.catalog import AnalysisListing
from aibi.core.schema.cohorts import AnalysisResults, Explanation
from aibi.core.schema.limits import QueryLimits
from aibi.core.schema.loading import load_document
from aibi.core.schema.output import Output
from aibi.core.schema.refusals import Refusal, RefusalCode
from aibi.core.schema.results import ResultEnvelope

World = Any
WORKERS = Workers(QueryLimits())
APPLE = {"kind": "value", "column": "trees.variety", "values": ["apple"]}
PEAR = {"kind": "value", "column": "trees.variety", "values": ["pear"]}
FELLED = {
    "kind": "endpoint",
    "id": "ep:felled",
    "label": "Felled",
    "fields": {
        "table": "trees",
        "time_column": "months",
        "status_column": "fell",
        "event_coding": {"event": [True], "censored": [False]},
        "entry": {"column": "since"},
    },
}


Tree = tuple[str, int, str, int]
"""A tree's variety, months until it was felled or last seen, whether it was felled, and the
month it was first recorded."""


def orchard(rows: int = 40) -> list[Tree]:
    """Trees of three varieties, each with the months until it was felled or last seen (1 to
    37), whether it was felled (every eleventh unknown) and the month it was first recorded."""
    found: list[Tree] = []
    for n in range(1, rows + 1):
        months = (n * 11) % 37 + 1
        fell = "NA" if n % 11 == 0 else ("yes" if n % 4 else "no")
        since = 0 if n % 3 == 0 else min(n % 5, months - 1)
        found.append((("apple", "pear", "plum")[n % 3], months, fell, since))
    return found


def files(trees: Sequence[Tree]) -> dict[str, bytes]:
    lines = ["tree_id,variety,months,fell,since"]
    lines += [f"tree{n},{','.join(map(str, tree))}" for n, tree in enumerate(trees, 1)]
    return {"trees.csv": ("\n".join(lines) + "\n").encode()}


def published(world: World, trees: Sequence[Tree] | None = None) -> None:
    world.publish("orchard", files(orchard() if trees is None else trees))
    world.curate(
        "orchard",
        *(
            {"op": "set", "descriptor": f"trees.{name}", "pointer": pointer, "value": value}
            for name in ("months", "since")
            for pointer, value in (("/fields/datatype", "time_offset"), ("/fields/units", "mo"))
        ),
        {"op": "put", "descriptor": FELLED},
    )


def document(cohorts: Sequence[str] = ("apple", "pear"), **params: Any) -> dict[str, Any]:
    return {
        "aibi": "1",
        "dataset": "orchard",
        "unit": "trees",
        "cohorts": {"apple": {"all": [APPLE]}, "pear": {"all": [PEAR]}, "every": {"all": []}},
        "views": [{"analysis": "survival.km", "cohorts": list(cohorts), "params": params}],
    }


def catalog_of(world: World, **given: Any) -> Catalog:
    return Catalog(world.store, workers=WORKERS, **given)


def answer(catalog: Catalog, name: str, body: JsonValue) -> Output | list[Refusal]:
    return call(catalog, BY_NAME[name], json.dumps(body).encode())


def run(catalog: Catalog, written: Mapping[str, Any]) -> AnalysisResults:
    found = answer(catalog, "run_analysis", {"document": dict(written)})
    assert isinstance(found, AnalysisResults), found
    return found


def refusal_of(found: Output | list[Refusal]) -> Refusal:
    assert isinstance(found, list), found
    return found[0]


def evaluated(world: World, manifest: str, written: Mapping[str, Any]) -> list[ResultEnvelope]:
    """The document's survival views run on rows the reference evaluator lists."""
    analyses = Analyses()
    release = world.store.load(manifest)
    loaded = load_document(json.dumps(written))
    assert loaded.document is not None
    parsed, refusals = views.parse(loaded.document, loaded.positions, analyses)
    assert refusals == []
    canonical = canonicalise(
        loaded.document,
        {written["dataset"]: release},
        labels={manifest: 2},
        positions=loaded.positions,
        endpoints=[e for view in parsed for e in view.endpoints],
    )
    found: list[ResultEnvelope] = []
    for view in views.checked(loaded.document, parsed, canonical, analyses)[0]:
        assert isinstance(view.params, SurvivalParams)
        [endpoint] = view.endpoints
        given = [ordered(listed(c.resolved, endpoint.variables)) for c in view.cohorts]
        outcome = survival.survive(
            [CohortAt(c, evaluate(c.resolved)) for c in view.cohorts],
            [survival.endpoint_rows(endpoint, one) for one in given],
            view.params,
            reference=view.reference,
            overlap=bool(shared(given)) and view.overlap,
            computation=view.identity.computation_id,
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


def manifest_of(result: ResultEnvelope) -> str:
    return result.derivation.releases[0].manifest


@pytest.mark.parametrize(
    "params", [{}, {"landmarks": [6, 12, 60], "level": 0.9}, {"grid": [0, 6, 12, 24, 36]}]
)
def test_survival_run_by_sql_gives_the_reference_evaluator_s_result(
    world: World, params: dict[str, Any]
) -> None:
    published(world)
    written = document(**params)
    [result] = run(catalog_of(world), written).results
    [expected] = evaluated(world, manifest_of(result), written)
    assert result.digest == expected.digest
    assert result.derivation.id == expected.derivation.id
    assert result.values == expected.values
    assert result.analysed == expected.analysed
    view: Any = result.values.view
    assert view["test"]["p"] is not None
    assert [effect["measure"] for effect in view["effects"]] == [
        "median_difference",
        "hazard_ratio",
    ]
    assert result.charts


def test_the_issuance_records_the_listing_and_names_no_unit(world: World) -> None:
    published(world)
    catalog = catalog_of(world)
    [result] = run(catalog, document()).results
    found = answer(catalog, "explain", {"id": result.issuance.id})
    assert isinstance(found, Explanation)
    issuance = found.issuance
    assert issuance is not None
    assert isinstance(issuance.sql, dict)
    recorded = json.dumps(issuance.model_dump(mode="json"))
    assert '"tree1"' not in recorded


def test_views_that_list_the_same_rows_share_their_run(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    published(world)
    listings: list[int] = []
    given = analyses_module.run_views

    def counted(*args: Any, **kw: Any) -> Any:
        listings.append(len(kw["inputs"]))
        return given(*args, **kw)

    monkeypatch.setattr(analyses_module, "run_views", counted)
    written = document()
    written["views"].append({**written["views"][0], "params": {"landmarks": [12]}})
    first, second = run(catalog_of(world), written).results
    assert listings == [1]
    assert first.digest != second.digest


def test_cohorts_that_share_units_are_refused_unless_the_view_allows_overlap(
    world: World,
) -> None:
    published(world)
    written = document(("apple", "every"))
    refusal = refusal_of(answer(catalog_of(world), "run_analysis", {"document": written}))
    assert (refusal.code, refusal.path) == (RefusalCode.COHORTS_OVERLAP, "/views/0")
    written["views"][0]["overlap"] = "allow"
    [result] = run(catalog_of(world), written).results
    view: Any = result.values.view
    assert view["test"]["not_estimable"] == {
        "/statistic": "overlapping_cohorts",
        "/p": "overlapping_cohorts",
    }
    assert "COHORTS_OVERLAP" in {caveat.code for caveat in result.caveats}


def test_a_curve_of_more_steps_than_a_result_reports_is_refused_at_the_view_s_grid(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    published(world)
    monkeypatch.setattr(survival, "MAX_CURVE_STEPS", 3)
    refusal = refusal_of(answer(catalog_of(world), "run_analysis", {"document": document()}))
    assert (refusal.code, refusal.path) == (RefusalCode.LIMIT_EXCEEDED, "/views/0/params/grid")
    assert refusal.limit is not None
    assert refusal.limit.name == "curve_steps"
    [result] = run(catalog_of(world), document(grid=[6, 12])).results
    assert result.digest


@pytest.mark.parametrize("params", [{}, {"grid": [5, 10]}, {"landmarks": [5]}])
@pytest.mark.parametrize(
    ("months", "since", "refused"),
    [(2**53, 0, True), (10**17, 0, True), (2**53 - 1, 0, False), (7, -(2**60), True)],
)
def test_a_time_or_entry_no_output_holds_is_refused_at_the_view_whatever_the_curve(
    world: World, params: dict[str, Any], months: int, since: int, refused: bool
) -> None:
    published(world, [*orchard(), ("apple", months, "yes", since)])
    found = answer(catalog_of(world), "run_analysis", {"document": document(**params)})
    if refused:
        refusal = refusal_of(found)
        assert (refusal.code, refusal.path) == (RefusalCode.NOT_SUPPORTED, "/views/0")
    else:
        assert isinstance(found, AnalysisResults), found
        values: Any = found.results[0].values
        assert values.positions[0]["last_follow_up"] == months


@pytest.mark.parametrize(
    ("cohorts", "sign"), [(("apple", "pear"), 1), (("pear", "apple"), -1)], ids=["over", "under"]
)
def test_a_newton_step_out_of_exp_s_range_is_halved_and_the_fit_converges(
    world: World, cohorts: tuple[str, str], sign: int
) -> None:
    trees: list[Tree] = [("apple", n, "yes", 0) for n in range(1, 2001)]
    trees += [("pear", 1, "yes", 0)] * 3
    published(world, trees)
    [result] = run(catalog_of(world), document(cohorts)).results
    view: Any = result.values.view
    [difference, ratio] = view["effects"]
    assert math.log(ratio["estimate"]) == pytest.approx(sign * 8.257881570691953, rel=1e-6)
    assert ratio.get("not_estimable") is None
    assert difference["estimate"] == pytest.approx(sign * (1 - 1000.5))
    assert view["proportional_hazards"]["not_estimable"] == {
        "/statistic": "zero_variance",
        "/p": "zero_variance",
    }


def test_the_analysis_runs_by_the_call_s_deadline_less_the_time_to_record(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    published(world)
    seen: list[float | None] = []
    running = survival.survive

    def spy(*args: Any, **kwargs: Any) -> Any:
        seen.append(kwargs.get("ends"))
        return running(*args, **kwargs)

    monkeypatch.setattr(analyses_module.survival, "survive", spy)
    deadline = Deadline(time.monotonic() + 20, 30.0)
    body: JsonValue = {"document": document()}
    found = within(deadline, lambda: answer(catalog_of(world), "run_analysis", body))
    assert not isinstance(found, list)
    assert seen == [deadline.at - RECORD_SECONDS]


def test_a_view_that_needs_an_endpoint_the_release_lacks_is_refused_where_it_is_named(
    world: World,
) -> None:
    world.publish("orchard", files(orchard()))
    found = answer(catalog_of(world), "validate_document", {"document": document()})
    dumped = json.dumps(
        [refusal.model_dump(mode="json") for refusal in found]
        if isinstance(found, list)
        else found.model_dump(mode="json")
    )
    assert "MISSING_MEMBER" in dumped
    assert '"/views/0/params/endpoint"' in dumped


@pytest.mark.parametrize("floor", [2, 5])
def test_under_a_floor_no_tool_runs_it_or_names_a_value(world: World, floor: int) -> None:
    published(world)
    catalog = catalog_of(world, floor=floor)
    for tool in ("validate_document", "count_cohort", "run_analysis"):
        found = answer(catalog, tool, {"document": document()})
        dumped = json.dumps(
            [refusal.model_dump(mode="json") for refusal in found]
            if isinstance(found, list)
            else found.model_dump(mode="json")
        )
        assert "NOT_SUPPORTED" in dumped
        assert '"/views/0/analysis"' in dumped
        assert '"tree1"' not in dumped
        assert '"curve"' not in dumped
    found = answer(catalog, "list_analyses", {"dataset": "orchard"})
    assert isinstance(found, AnalysisListing)
    assert found.applicable is not None
    [item] = [a for a in found.applicable if a.analysis == "survival.km"]
    assert (item.status, item.missing) == ("unavailable", ["min_cell_count"])
    unfloored = answer(catalog_of(world), "list_analyses", {"dataset": "orchard"})
    assert isinstance(unfloored, AnalysisListing)
    assert unfloored.applicable is not None
    [item] = [a for a in unfloored.applicable if a.analysis == "survival.km"]
    assert (item.status, item.missing) == ("available", [])


def test_a_result_names_no_unit_s_key(world: World) -> None:
    published(world)
    [result] = run(catalog_of(world), document()).results
    shown = json.dumps(cast(Any, result).model_dump(mode="json"))
    assert all(f'"tree{n}"' not in shown for n in range(1, 41))


EXTREMES: dict[str, list[Tree]] = {
    "all ties": [("apple", 5, "yes", 0)] * 20 + [("pear", 5, "yes", 0)] * 20,
    "a single event": [("apple", 1, "yes", 0), ("pear", 2, "no", 0)],
    "2000 steps against 3": [("apple", n, "yes", 0) for n in range(1, 2001)]
    + [("pear", 1, "yes", 0)] * 3,
    "1 against 1500": [("apple", 1, "yes", 0)] + [("pear", n, "yes", 0) for n in range(1, 1501)],
    "separation": [("apple", n, "yes", 0) for n in range(100, 112)]
    + [("pear", n, "yes", 0) for n in range(1, 13)],
    "no events": [("apple", n, "no", 0) for n in range(1, 13)]
    + [("pear", n, "no", 0) for n in range(1, 13)],
    "the largest time": [("apple", 2**53 - 1, "yes", 0), ("pear", 0, "yes", 0)],
    "beyond": [("apple", 2**53, "yes", 0), ("pear", 1, "yes", 0)],
}


@pytest.mark.parametrize("shape", list(EXTREMES))
def test_extreme_data_gives_a_result_or_a_coded_refusal_through_the_tool(
    world: World, shape: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(timetoevent, "REPLICATES", 20)
    published(world, EXTREMES[shape])
    catalog = catalog_of(world)
    for params in ({}, {"grid": [1, 10]}, {"landmarks": [1, 2**53 - 1]}):
        for cohorts in (("apple", "pear"), ("pear", "apple"), ("every",)):
            found = answer(catalog, "run_analysis", {"document": document(cohorts, **params)})
            if isinstance(found, list):
                [refusal] = found
                assert shape == "beyond"
                assert (refusal.code, refusal.path) == (RefusalCode.NOT_SUPPORTED, "/views/0")
                continue
            assert shape != "beyond"
            assert isinstance(found, AnalysisResults)
            json.dumps(found.model_dump(mode="json"), allow_nan=False)

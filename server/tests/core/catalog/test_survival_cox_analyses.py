"""``run_analysis`` of ``survival.cox`` over a store (SPEC §8.1, §8.4, §9.5, §13.3; D366–D369):
an orchard imported by the core's file importer, its trees' months until felled curated as an
endpoint, and its trees' and harvests' columns as covariates of every kind. The rows a query
worker lists are held to the reference evaluator's by the result's digest, for each kind."""

import json
from collections.abc import Mapping, Sequence
from typing import Any

import pytest
from pydantic import JsonValue

from aibi.core.analyses import cox, views
from aibi.core.analyses.existence import CohortAt
from aibi.core.analyses.registry import Analyses
from aibi.core.analyses.results import envelope
from aibi.core.catalog import analyses as analyses_module
from aibi.core.catalog.service import Catalog
from aibi.core.catalog.tools import BY_NAME, call
from aibi.core.engine.canonical import canonicalise
from aibi.core.engine.evaluate import evaluate
from aibi.core.engine.inputs import listed, ordered, shared
from aibi.core.engine.worker import Workers
from aibi.core.schema.analyses import CoxParams
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
SOILS = ("sand", "loam", "clay")
GRADES = ("C", "B", "A")
COVERAGE = {
    "kind": "coverage",
    "id": "cov:harvests.tree_id",
    "label": "Every tree's harvests are recorded",
    "fields": {"relationship": "rel:harvests.tree_id", "parents": "all"},
}
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


def files(
    rows: int = 60, *, girth: Any = None, soil: Any = None, months: Any = None
) -> dict[str, bytes]:
    """Trees of three varieties, each with the months until it was felled or last seen,
    whether it was felled (every eleventh unknown), the month it was first recorded, its girth
    (every ninth missing), whether it was grafted, its soil and its tags; and one to three
    harvests of each, with a weight and a grade. ``girth``, ``soil`` and ``months`` replace a
    column's values by a function of the tree's number."""
    trees = ["tree_id,variety,months,fell,since,girth,grafted,soil,tags"]
    for n in range(1, rows + 1):
        felled = (n * 11) % 37 + 1 if months is None else months(n)
        fell = "NA" if n % 11 == 0 else ("yes" if n % 4 else "no")
        since = 0 if n % 3 == 0 else min(n % 5, felled - 1)
        size = ("NA" if n % 9 == 0 else f"{20 + (n * 13) % 40}") if girth is None else girth(n)
        grafted = "yes" if n % 5 < 2 else "no"
        ground = SOILS[(n // 3) % 3] if soil is None else soil(n)
        tags = ("old;tall", "old", "young;tall", "young")[n % 4]
        trees.append(
            f"tree{n},{('apple', 'pear', 'plum')[n % 3]},{felled},{fell},{since},{size},"
            f"{grafted},{ground},{tags}"
        )
    crops = ["harvest_id,tree_id,kg,grade"]
    count = 0
    for n in range(1, rows + 1):
        for k in range(1 + n % 4):
            count += 1
            crops.append(f"h{count},tree{n},{10 + (n + k) % 9},{GRADES[(n + 2 * k) % 3]}")
    return {
        "trees.csv": ("\n".join(trees) + "\n").encode(),
        "harvests.csv": ("\n".join(crops) + "\n").encode(),
    }


def ordered_values(values: Sequence[str]) -> dict[str, JsonValue]:
    return {"values": [{"value": value} for value in values], "ordered": True}


def published(world: World, given: Mapping[str, bytes] | None = None) -> None:
    world.publish("orchard", dict(files() if given is None else given))
    edits: list[dict[str, JsonValue]] = [
        {"op": "set", "descriptor": f"trees.{name}", "pointer": pointer, "value": value}
        for name in ("months", "since")
        for pointer, value in (("/fields/datatype", "time_offset"), ("/fields/units", "mo"))
    ]
    edits += [
        {
            "op": "set",
            "descriptor": "trees.soil",
            "pointer": "/fields/datatype",
            "value": "category",
        },
        {
            "op": "set",
            "descriptor": "trees.soil",
            "pointer": "/fields/permissible_values",
            "value": ordered_values(SOILS),
        },
        {
            "op": "set",
            "descriptor": "harvests.grade",
            "pointer": "/fields/datatype",
            "value": "category",
        },
        {
            "op": "set",
            "descriptor": "harvests.grade",
            "pointer": "/fields/permissible_values",
            "value": ordered_values(GRADES),
        },
        {"op": "put", "descriptor": FELLED},
        {"op": "put", "descriptor": COVERAGE},
    ]
    world.curate("orchard", *edits)


CHECKED = {
    "kind": "coverage",
    "id": "cov:checks.harvest_id",
    "label": "Harvests of grades A and B are checked where a checked harvest is listed",
    "fields": {
        "relationship": "rel:checks.harvest_id",
        "parents": {"table": "checked_harvests", "parent_columns": {"harvest_id": "harvest_id"}},
        "parent_scope": {"kind": "value", "column": "harvests.grade", "values": ["A", "B"]},
    },
}
CHECKS = {
    "kind": "relationship",
    "id": "rel:checks.harvest_id",
    "label": "A check of a harvest",
    "fields": {
        "child_table": "checks",
        "child_columns": ["harvest_id"],
        "parent_table": "harvests",
        "parent_columns": ["harvest_id"],
        "cardinality": "many-to-one",
    },
}


def checked(world: World) -> None:
    """The orchard with checks of its harvests, two down steps from a tree: every other harvest
    is listed as checked, one in three of those has a check, and only grades A and B are in the
    checks' scope, so a question through them depends on its lift (§6.5)."""
    given = files()
    checks = ["check_id,harvest_id,result"]
    listed = ["harvest_id"]
    for n in range(1, 151):
        if n % 2:
            listed.append(f"h{n}")
            if n % 3 == 0:
                checks.append(f"c{n},h{n},{'ok' if n % 4 else 'bad'}")
    given["checks.csv"] = ("\n".join(checks) + "\n").encode()
    given["checked_harvests.csv"] = ("\n".join(listed) + "\n").encode()
    published(world, given)
    world.curate(
        "orchard",
        {"op": "put", "descriptor": CHECKS},
        {
            "op": "set",
            "descriptor": "checked_harvests",
            "pointer": "/fields/role",
            "value": "coverage",
        },
        {"op": "put", "descriptor": CHECKED},
    )


def document(
    covariates: Sequence[Any] | str = (),
    cohorts: Sequence[str] = ("apple", "pear"),
    **params: Any,
) -> dict[str, Any]:
    return {
        "aibi": "1",
        "dataset": "orchard",
        "unit": "trees",
        "cohorts": {"apple": {"all": [APPLE]}, "pear": {"all": [PEAR]}, "every": {"all": []}},
        "views": [
            {
                "analysis": "survival.cox",
                "cohorts": list(cohorts),
                "params": {
                    "covariates": covariates if isinstance(covariates, str) else list(covariates),
                    **params,
                },
            }
        ],
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
    """The document's views run on cells the reference evaluator lists."""
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
        variables=[v for view in parsed for v in view.variables],
        predicates=[p for view in parsed for p in view.predicates],
    )
    checked, wrong = views.checked(loaded.document, parsed, canonical, analyses)
    assert wrong == []
    found: list[ResultEnvelope] = []
    for view in checked:
        assert isinstance(view.params, CoxParams)
        [endpoint] = view.endpoints
        resolved = [variable.resolved for variable in view.variables]
        read = cox.listed_variables(resolved, endpoint)
        given = [ordered(listed(c.resolved, read)) for c in view.cohorts]
        outcome = cox.analyse(
            [CohortAt(c, evaluate(c.resolved)) for c in view.cohorts],
            given,
            endpoint,
            resolved,
            view.params,
            reference=view.reference,
            overlap=bool(shared(given)) and view.overlap,
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


GIRTH = {"column": "trees.girth"}
KINDS: dict[str, list[Any]] = {
    "cohorts alone": [],
    "a number with missing values": [GIRTH],
    "a boolean": [{"column": "trees.grafted"}],
    "an ordered category": [{"column": "trees.soil"}],
    "a category": [{"column": "trees.variety"}],
    "some of a list": [{"column": "trees.tags", "aggregate": "some", "values": ["tall"]}],
    "every of a list": [{"column": "trees.tags", "aggregate": "every", "values": ["old"]}],
    "a count": [{"column": "harvests.harvest_id", "aggregate": "count"}],
    "a mean": [{"column": "harvests.kg", "aggregate": "mean"}],
    "a max of numbers": [{"column": "harvests.kg", "aggregate": "max"}],
    "a min of numbers": [{"column": "harvests.kg", "aggregate": "min"}],
    "a max of an ordered category": [{"column": "harvests.grade", "aggregate": "max"}],
    "a min of an ordered category": [{"column": "harvests.grade", "aggregate": "min"}],
    "a some of rows": [{"column": "harvests.grade", "aggregate": "some", "values": ["A"]}],
    "a time offset": [{"column": "trees.since"}],
    "several": [GIRTH, {"column": "trees.soil"}, {"column": "trees.grafted"}],
}


@pytest.mark.parametrize("name", sorted(KINDS))
def test_a_model_run_by_sql_gives_the_reference_evaluator_s_result(world: World, name: str) -> None:
    published(world)
    cohorts = ("apple", "pear") if name != "a category" else ("every",)
    written = document(KINDS[name], cohorts)
    [result] = run(catalog_of(world), written).results
    [expected] = evaluated(world, manifest_of(result), written)
    assert result.digest == expected.digest
    assert result.derivation.id == expected.derivation.id
    assert result.values == expected.values
    assert result.analysed == expected.analysed
    view: Any = result.values.view
    assert any(term["estimate"] is not None for term in view["terms"])
    assert result.charts


@pytest.mark.parametrize(
    "params",
    [{"stratum": {"column": "trees.grafted"}}, {"stratum": {"column": "trees.soil"}, "level": 0.9}],
)
def test_a_stratified_model_run_by_sql_gives_the_reference_evaluator_s_result(
    world: World, params: dict[str, Any]
) -> None:
    published(world)
    written = document([GIRTH], **params)
    [result] = run(catalog_of(world), written).results
    [expected] = evaluated(world, manifest_of(result), written)
    assert result.digest == expected.digest


def test_the_issuance_records_the_listing_and_names_no_unit(world: World) -> None:
    published(world)
    catalog = catalog_of(world)
    [result] = run(catalog, document([GIRTH])).results
    found = answer(catalog, "explain", {"id": result.issuance.id})
    assert isinstance(found, Explanation)
    issuance = found.issuance
    assert issuance is not None
    assert isinstance(issuance.sql, dict)
    recorded = json.dumps(issuance.model_dump(mode="json"))
    assert '"tree1"' not in recorded
    assert '"tree1"' not in json.dumps(result.model_dump(mode="json"))


def test_views_that_list_the_same_cells_share_their_run_a_km_view_of_cohorts_alone_too(
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
    written["views"].append({"analysis": "survival.km", "cohorts": ["apple", "pear"]})
    written["views"].append({**document([GIRTH])["views"][0]})
    written["views"].append({**document([GIRTH], level=0.9)["views"][0]})
    run(catalog_of(world), written)
    assert listings == [2]


def test_cohorts_that_share_units_are_refused_unless_the_view_allows_overlap(
    world: World,
) -> None:
    published(world)
    written = document([GIRTH], ("apple", "every"))
    refusal = refusal_of(answer(catalog_of(world), "run_analysis", {"document": written}))
    assert (refusal.code, refusal.path) == (RefusalCode.COHORTS_OVERLAP, "/views/0")
    assert refusal.counts
    written["views"][0]["overlap"] = "allow"
    [result] = run(catalog_of(world), written).results
    view: Any = result.values.view
    assert {term["not_estimable"]["/estimate"] for term in view["terms"]} == {"overlapping_cohorts"}
    assert "COHORTS_OVERLAP" in {caveat.code for caveat in result.caveats}


@pytest.mark.parametrize(
    ("given", "covariates", "params", "expected"),
    [
        (
            {"soil": lambda n: f"s{n % 10}"},
            [{"column": "trees.soil"}],
            {},
            (
                RefusalCode.LIMIT_EXCEEDED,
                "/views/0/params/covariates",
                ("cox_parameters", 8),
                "9 columns",
            ),
        ),
        (
            {"soil": lambda n: f"s{n % 101}"},
            [GIRTH],
            {"stratum": {"column": "trees.soil"}},
            (RefusalCode.LIMIT_EXCEEDED, "/views/0/params/stratum", ("strata", 100), "101 levels"),
        ),
        (
            {"girth": lambda n: "1.5e18" if n == 4 else f"{n % 5}.5"},
            [{"column": "trees.grafted"}, GIRTH],
            {},
            (RefusalCode.NOT_SUPPORTED, "/views/0/params/covariates/1", None, "exact double"),
        ),
        (
            {"girth": lambda n: f"{(n % 2) * 1e-200:g}"},
            [{"column": "trees.grafted"}, GIRTH],
            {},
            (RefusalCode.NOT_SUPPORTED, "/views/0/params/covariates/1", None, "to scale"),
        ),
        (
            {"soil": lambda n: "x" * 10_001 if n % 2 else "y"},
            [{"column": "trees.soil"}],
            {},
            (
                RefusalCode.LIMIT_EXCEEDED,
                "/views/0/params/covariates/0",
                ("text_characters", 10_000),
                "characters",
            ),
        ),
        (
            {"soil": lambda n: "s\ufdd0" if n % 2 else "y"},
            [{"column": "trees.soil"}],
            {},
            (RefusalCode.NOT_SUPPORTED, "/views/0/params/covariates/0", None, "not Unicode text"),
        ),
        (
            {"months": lambda n: 2**60 if n == 1 else n + 1},
            [GIRTH],
            {},
            (RefusalCode.NOT_SUPPORTED, "/views/0", None, "time or entry"),
        ),
    ],
    ids=[
        "parameters",
        "strata",
        "a huge number",
        "unscalable",
        "a long level",
        "a level that is not Unicode text",
        "a huge time",
    ],
)
def test_what_only_the_data_show_is_refused_where_it_is_written(
    world: World,
    given: dict[str, Any],
    covariates: list[Any],
    params: dict[str, Any],
    expected: tuple[RefusalCode, str, tuple[str, int] | None, str],
) -> None:
    trees = 1010 if "stratum" in params else 60
    world.publish("orchard", files(trees, **given))
    edits: list[dict[str, JsonValue]] = [
        {"op": "set", "descriptor": f"trees.{name}", "pointer": "/fields/datatype", "value": kind}
        for name, kind in (("months", "time_offset"), ("since", "time_offset"), ("soil", "string"))
    ]
    world.curate("orchard", *edits, {"op": "put", "descriptor": FELLED})
    found = answer(catalog_of(world), "run_analysis", {"document": document(covariates, **params)})
    refusal = refusal_of(found)
    code, path, limit, words = expected
    assert (refusal.code, refusal.path) == (code, path)
    assert (None if refusal.limit is None else (refusal.limit.name, refusal.limit.max)) == limit
    message = json.dumps([segment.model_dump() for segment in refusal.message])
    assert words in message
    assert "tree" not in message


@pytest.mark.parametrize("floor", [2, 5])
def test_under_a_floor_no_tool_runs_it(world: World, floor: int) -> None:
    published(world)
    catalog = catalog_of(world, floor=floor)
    refusal = refusal_of(answer(catalog, "run_analysis", {"document": document([GIRTH])}))
    assert (refusal.code, refusal.path) == (RefusalCode.WITHHELD_UNDER_K, "/views/0/analysis")
    listing: Any = answer(catalog, "list_analyses", {"dataset": "orchard", "unit": "trees"})
    [status] = [a.status for a in listing.applicable if a.analysis == "survival.cox"]
    assert status == "unavailable"


def test_with_an_endpoint_the_analysis_applies(world: World) -> None:
    published(world)
    listing: Any = answer(
        catalog_of(world), "list_analyses", {"dataset": "orchard", "unit": "trees"}
    )
    [status] = [a.status for a in listing.applicable if a.analysis == "survival.cox"]
    assert status == "available"


def test_a_run_time_refusal_in_a_parameter_s_value_is_pointed_at_its_reference(
    world: World,
) -> None:
    given = files(60, girth=lambda n: "1.5e18" if n == 4 else f"{n % 5}.5")
    published(world, given)
    written = document(["$g"])
    written["params"] = {"g": GIRTH}
    refusal = refusal_of(answer(catalog_of(world), "run_analysis", {"document": written}))
    assert (refusal.code, refusal.path) == (
        RefusalCode.NOT_SUPPORTED,
        "/views/0/params/covariates/0",
    )
    assert {"data": "g"} in [segment.model_dump() for segment in refusal.message]
    written = document("$c")
    written["params"] = {"c": [GIRTH]}
    refusal = refusal_of(answer(catalog_of(world), "run_analysis", {"document": written}))
    assert (refusal.code, refusal.path) == (RefusalCode.NOT_SUPPORTED, "/views/0/params/covariates")


def test_a_run_over_its_caps_names_the_first_view_that_lists_inputs(world: World) -> None:
    published(world)
    manifest = world.store.latest("orchard").manifest
    release = world.store.load(manifest)
    written = document([GIRTH])
    written["views"].insert(
        0,
        {
            "analysis": "compare.columns",
            "cohorts": ["apple", "pear"],
            "params": {"columns": [GIRTH]},
        },
    )
    loaded = load_document(json.dumps(written))
    assert loaded.document is not None
    analyses = Analyses()
    parsed, _ = views.parse(loaded.document, loaded.positions, analyses)
    canonical = canonicalise(
        loaded.document,
        {"orchard": release},
        labels={manifest: 2},
        positions=loaded.positions,
        endpoints=[e for view in parsed for e in view.endpoints],
        variables=[v for view in parsed for v in view.variables],
        predicates=[p for view in parsed for p in view.predicates],
    )
    checked, _ = views.checked(loaded.document, parsed, canonical, analyses)
    widest = analyses_module._widest(checked)  # pyright: ignore[reportPrivateUsage]
    assert widest.index == 1


def test_a_document_whose_views_are_a_parameter_s_empty_value_is_refused_at_its_reference(
    world: World,
) -> None:
    published(world)
    written = document([GIRTH])
    written["views"] = "$v"
    written["params"] = {"v": []}
    refusal = refusal_of(answer(catalog_of(world), "run_analysis", {"document": written}))
    assert (refusal.code, refusal.path) == (RefusalCode.MISSING_MEMBER, "/views")
    assert {"data": "v"} in [segment.model_dump() for segment in refusal.message]


TALL = {"kind": "value", "column": "trees.tags", "values": ["tall"], "match": "any"}
HEAVY = {
    "kind": "exists",
    "table": "harvests",
    "quantifier": "some",
    "where": [{"kind": "value", "column": "harvests.kg", "range": {"gte": 14}}],
}
PREDICATES: dict[str, list[Any]] = {
    "a value of the unit": [
        {"predicate": {"kind": "value", "column": "trees.grafted", "values": [True]}}
    ],
    "a list": [{"predicate": TALL}],
    "rows below": [{"predicate": HEAVY}],
    "every row below": [{"predicate": {**HEAVY, "quantifier": "every"}}],
    "not and known": [
        {
            "predicate": {
                "not": {"known": {"kind": "value", "column": "trees.girth", "range": {"gte": 30}}}
            }
        }
    ],
    "unknown": [
        {"predicate": {"unknown": {"kind": "value", "column": "trees.girth", "range": {"gte": 30}}}}
    ],
    "any of two": [{"predicate": {"any": [TALL, HEAVY]}}],
    "beside columns": [GIRTH, {"predicate": HEAVY}, {"column": "trees.soil"}],
}


@pytest.mark.parametrize("name", sorted(PREDICATES))
def test_a_model_of_predicates_run_by_sql_gives_the_reference_evaluator_s_result(
    world: World, name: str
) -> None:
    published(world)
    written = document(
        PREDICATES[name], stratum={"column": "trees.grafted"} if name == "rows below" else None
    )
    if name != "rows below":
        written["views"][0]["params"].pop("stratum")
    [result] = run(catalog_of(world), written).results
    [expected] = evaluated(world, manifest_of(result), written)
    assert result.digest == expected.digest
    assert result.values == expected.values
    assert result.analysed == expected.analysed


def test_views_that_differ_only_in_a_predicate_list_their_cells_apart(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    published(world)
    listings: list[int] = []
    given = analyses_module.run_views

    def counted(*args: Any, **kw: Any) -> Any:
        listings.append(len(kw["inputs"]))
        return given(*args, **kw)

    monkeypatch.setattr(analyses_module, "run_views", counted)
    written = document([{"predicate": TALL}])
    written["views"].append(document([{"predicate": HEAVY}])["views"][0])
    first, second = run(catalog_of(world), written).results
    assert listings == [2]
    assert first.digest != second.digest


CHECKED_HARVEST = {
    "kind": "exists",
    "table": "harvests",
    "where": [{"kind": "exists", "table": "checks", "where": []}],
}


@pytest.mark.parametrize(
    "covariates",
    [
        [{"predicate": CHECKED_HARVEST}],
        [{"predicate": {**CHECKED_HARVEST, "lift": "assessed"}}, GIRTH],
        [GIRTH, {"predicate": {"not": CHECKED_HARVEST}}, {"column": "trees.soil"}],
    ],
    ids=["strict", "assessed beside a number", "negated between columns"],
)
def test_a_predicate_whose_lift_changes_its_truth_runs_by_sql_as_by_the_evaluator(
    world: World, covariates: list[Any]
) -> None:
    checked(world)
    written = document(covariates)
    [result] = run(catalog_of(world), written).results
    [expected] = evaluated(world, manifest_of(result), written)
    assert result.digest == expected.digest
    assert result.values == expected.values
    positions: Any = result.values.positions
    counts = [lift["lift_differs"] for at in positions for lift in at["lifts"]]
    assert any(counts)
    assert "LIFT_DIFFERS" in {caveat.code for caveat in result.caveats}


CHECKED_A_HARVEST = {
    "kind": "exists",
    "table": "harvests",
    "where": [
        {"kind": "value", "column": "harvests.grade", "values": ["A"]},
        {"kind": "exists", "table": "checks", "where": []},
    ],
}


def test_two_lifted_predicates_are_each_counted_against_their_own_flip_as_existence_counts_them(
    world: World,
) -> None:
    checked(world)
    clauses = [CHECKED_HARVEST, CHECKED_A_HARVEST]
    alone = document([{"predicate": clause} for clause in clauses])
    written = document([{"predicate": clause} for clause in clauses])
    written["views"].append(
        {
            "analysis": "compare.existence",
            "cohorts": ["apple", "pear"],
            "params": {"predicates": clauses},
        }
    )
    modelled, compared = run(catalog_of(world), written).results
    [expected] = evaluated(world, manifest_of(modelled), alone)
    assert modelled.values == expected.values
    by_model: Any = modelled.values.positions
    by_existence: Any = compared.values.positions
    counts = [
        [(lift["covariate"], lift["lift_differs"]) for lift in at["lifts"]] for at in by_model
    ]
    assert counts == [
        [(k, position["predicates"][k]["lift_differs"]) for k in range(2)]
        for position in by_existence
    ]
    assert any(first != second for (_, first), (_, second) in counts)

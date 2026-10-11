"""``run_analysis`` and ``list_analyses`` over a store (SPEC §7.4, §8, §9, §11.1, §12.2, §13.4;
D316–D323): the orchard, a domain-neutral dataset imported by the core's file importer, as both
transports call them. The SQL a query worker runs is held to the reference evaluator by the
digest: the same document run both ways gives one digest."""

import json
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from typing import Any, cast

import pytest
from pydantic import JsonValue

from aibi.core.analyses import columns, distribution, members, views
from aibi.core.analyses.existence import CohortAt, compare
from aibi.core.analyses.registry import Analyses
from aibi.core.analyses.results import Outcome, envelope
from aibi.core.catalog import analyses as analyses_module
from aibi.core.catalog.cohorts import RECORD_SECONDS
from aibi.core.catalog.service import Catalog, Deadline, within
from aibi.core.catalog.tools import BY_NAME, call
from aibi.core.engine import sql as sql_module
from aibi.core.engine import worker as worker_module
from aibi.core.engine.canonical import canonicalise
from aibi.core.engine.evaluate import evaluate
from aibi.core.engine.members import keys, ordered
from aibi.core.engine.memberships import evaluated as evaluated_variable
from aibi.core.engine.memberships import materialise_over
from aibi.core.engine.queries import run_cohorts
from aibi.core.engine.resolve import ResolvedCohort
from aibi.core.engine.resolved import flipped
from aibi.core.engine.sql import TruthValues, cross
from aibi.core.engine.worker import CallerDeadline, QueryRefused, Workers
from aibi.core.schema.analyses import (
    ColumnsParams,
    DistributionParams,
    ExistenceParams,
    MembersParams,
)
from aibi.core.schema.catalog import AnalysisListing, DatasetDescription
from aibi.core.schema.caveats import CaveatCode
from aibi.core.schema.cohorts import (
    AnalysisResults,
    CohortCounts,
    DocumentValidation,
    Explanation,
)
from aibi.core.schema.limits import MAX_LISTED, MAX_TEXT, QueryLimits
from aibi.core.schema.loading import load_document
from aibi.core.schema.output import Output
from aibi.core.schema.refusals import Limit, Refusal, RefusalCode
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
        variables=[v for view in parsed for v in view.variables],
    )
    found: list[ResultEnvelope] = []
    for view in views.checked(loaded.document, parsed, canonical)[0]:
        positions = [CohortAt(c, evaluate(c.resolved)) for c in view.cohorts]
        outcome: Outcome
        if isinstance(view.params, MembersParams):
            [position] = positions
            listed = ordered(keys(position.cohort.resolved))
            outcome = members.list_members(position, listed, view.params, k=view.disclosure)
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
            continue
        if isinstance(view.params, DistributionParams | ColumnsParams):
            resolved = [variable.resolved for variable in view.variables]
            given = [evaluated_variable(variable) for variable in resolved]
            materialised = []
            held: list[set[int]] = []
            for cohort in view.cohorts:
                units = [
                    row
                    for row, value in enumerate(evaluate(cohort.resolved).values)
                    if value.is_true
                ]
                held.append(set(units))
                materialised.append(
                    materialise_over(resolved, given, units, declared=view.disclosure is not None)
                )
            if isinstance(view.params, ColumnsParams):
                shared = any(a & b for i, a in enumerate(held) for b in held[i + 1 :])
                outcome = columns.compare_columns(
                    positions,
                    view.variables,
                    materialised,
                    view.params,
                    reference=view.reference,
                    overlap=shared and view.overlap,
                    k=view.disclosure,
                    computation=view.identity.computation_id,
                )
            else:
                outcome = distribution.summarise(
                    positions, view.variables, materialised, view.params, k=view.disclosure
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
            continue
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

COLUMNS: list[dict[str, Any]] = [
    {"column": "trees.variety"},
    {"column": "trees.height_m", "bins": [0, 2, 4, 6, 8]},
    {"column": "harvests.kg", "aggregate": "mean", "bins": [0, 12, 14, 16, 20]},
    {"column": "harvests.kg", "aggregate": "max", "empty": 0, "bins": [0, 12, 14, 16, 20]},
    {
        "column": "harvests.harvest_id",
        "aggregate": "count",
        "where": [{"kind": "value", "column": "harvests.grade", "values": ["A"]}],
        "bins": [0, 1, 2, 3],
    },
    {"column": "trees.tags", "aggregate": "some", "values": ["old"]},
]
"""A distribution view's columns over the orchard: a category, a number with missing values,
the mean and the greatest of a column below the unit (a tree with no harvest takes 0 as its
greatest), a count of the rows that meet a condition, and a question about a list."""


def distribution_document(columns: Sequence[Mapping[str, Any]] = COLUMNS) -> dict[str, Any]:
    return document(
        view={
            "analysis": "summary.distribution",
            "cohorts": ["apple", "pear"],
            "params": {"columns": [dict(column) for column in columns]},
        }
    )


@pytest.mark.parametrize("floor", [None, 2, 3, 5])
def test_a_distribution_run_by_sql_gives_the_reference_evaluator_s_result(
    world: World, orchard: Orchard, floor: int | None
) -> None:
    published = world.publish("orchard", orchard(40, harvests=60))
    written = distribution_document()
    [result] = run(catalog_of(world, floor=floor), written).results
    [expected] = evaluated(world, published.manifest, written, floor=floor)
    assert result.digest == expected.digest
    assert result.derivation.id == expected.derivation.id
    assert result.values == expected.values
    assert result.derivation.analysis.id == "summary.distribution"
    assert len(result.charts) == len(COLUMNS)


ROWS: list[dict[str, Any]] = [
    {"column": "harvests.grade", "count": "rows"},
    {"column": "harvests.kg", "count": "rows", "bins": [0, 12, 14, 16, 20]},
    {
        "column": "harvests.kg",
        "count": "rows",
        "where": [{"kind": "value", "column": "harvests.grade", "values": ["A"]}],
    },
]
"""A distribution view's counts of rows over the orchard (D378): the harvests' grades, their
weights, and the weights of the grade A harvests."""


def test_a_distribution_of_rows_run_by_sql_gives_the_reference_evaluator_s_result(
    world: World, orchard: Orchard
) -> None:
    published = world.publish("orchard", orchard(40, harvests=60))
    written = distribution_document([COLUMNS[0], *ROWS])
    [result] = run(catalog_of(world), written).results
    [expected] = evaluated(world, published.manifest, written)
    assert result.digest == expected.digest
    assert result.derivation.id == expected.derivation.id
    assert result.values == expected.values
    dumped = result.values.model_dump(mode="json")
    kinds = [one["kind"] for one in dumped["positions"][0]["columns"]]
    assert kinds == ["categories", "category_rows", "number_rows", "number_rows"]
    assert len(result.charts) == 1 + len(ROWS)


def test_under_a_floor_a_count_of_rows_is_withheld_and_no_query_runs(
    world: World, orchard: Orchard, monkeypatch: pytest.MonkeyPatch
) -> None:
    world.publish("orchard", orchard())
    catalog = catalog_of(world, floor=3)
    ran: list[object] = []
    monkeypatch.setattr(analyses_module, "_run", lambda *args, **kwargs: ran.append(args))
    written = distribution_document([COLUMNS[0], ROWS[0]])
    for tool in ("validate_document", "count_cohort", "run_analysis"):
        found = answer(catalog, tool, {"document": written})
        dumped = json.dumps(
            [refusal.model_dump(mode="json") for refusal in found]
            if isinstance(found, list)
            else found.model_dump(mode="json")
        )
        assert "WITHHELD_UNDER_K" in dumped, tool
        assert '"/views/0/params/columns/1/count"' in dumped, tool
        assert '"tree1"' not in dumped
    assert ran == []


def test_a_withheld_count_of_rows_that_reached_run_analysis_would_raise_before_any_query(
    world: World, orchard: Orchard, monkeypatch: pytest.MonkeyPatch
) -> None:
    world.publish("orchard", orchard())
    catalog = catalog_of(world, floor=3)
    ran: list[object] = []
    monkeypatch.setattr(views, "_withheld_form", lambda *_: None)
    monkeypatch.setattr(analyses_module, "_run", lambda *args, **kwargs: ran.append(args))
    with pytest.raises(ValueError, match="never run"):
        run(catalog, distribution_document([ROWS[0]]))
    assert ran == []


EACH: list[dict[str, Any]] = [
    {"column": "harvests.grade", "each": "category"},
    {"column": "harvests.grade", "each": "category", "lift": "assessed"},
    {"column": "trees.tags", "each": "category"},
]
"""A distribution view's memberships over the orchard (D382): the harvests' grades under both
lifts, and the trees' tags, a list."""


def test_memberships_run_by_sql_give_the_reference_evaluator_s_result(
    world: World, orchard: Orchard
) -> None:
    """D382: memberships beside a column, counted by SQL as pairs plus default (D381), give the
    reference evaluator's values, joint counts and digest."""
    published = world.publish("orchard", orchard(40, harvests=60))
    written = distribution_document([COLUMNS[0], *EACH])
    [result] = run(catalog_of(world), written).results
    [expected] = evaluated(world, published.manifest, written)
    assert result.digest == expected.digest
    assert result.derivation.id == expected.derivation.id
    assert result.values == expected.values
    assert result.analysed == expected.analysed
    dumped = result.values.model_dump(mode="json")
    kinds = [one["kind"] for one in dumped["positions"][0]["columns"]]
    assert kinds == ["categories", "memberships", "memberships", "memberships"]
    tags = dumped["positions"][0]["columns"][3]["categories"]
    assert [row["values"][0]["data"] for row in tags] == ["old", "tall", "young"]
    assert len(result.charts) == 1 + len(EACH)


def test_under_a_floor_memberships_run_by_sql_list_their_declared_categories_alone(
    world: World, orchard: Orchard
) -> None:
    """D383, D384: under a floor, memberships run, counted by SQL over their declared
    categories alone (``compile_materialised(…, declared=True)``), and give the reference
    evaluator's result over the same (``materialise_over(…, declared=True)``): values, joint
    counts and digest; a column that declares no category lists none, and no undeclared value is
    named, here the grade ``A`` every other harvest holds, which the curated grades do not
    declare; each row's ``excluded`` and the view's ``analysed`` are suppressed."""
    world.publish("orchard", orchard(40, harvests=60))
    world.curate(
        "orchard",
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
            "value": {"values": [{"value": v} for v in ("E", "B")], "ordered": False},
        },
    )
    published = world.store.latest("orchard")
    assert published is not None
    written = distribution_document([COLUMNS[0], *EACH])
    [result] = run(catalog_of(world, floor=3), written).results
    [expected] = evaluated(world, published.manifest, written, floor=3)
    assert result.digest == expected.digest
    assert result.derivation.id == expected.derivation.id
    assert result.values == expected.values
    assert result.analysed == expected.analysed
    dumped = result.values.model_dump(mode="json")
    columns = dumped["positions"][0]["columns"]
    assert [one["kind"] for one in columns] == ["categories", *["memberships"] * len(EACH)]
    release = world.store.load(published.manifest)
    for column, given in zip(columns[1:], EACH, strict=True):
        table, name = given["column"].split(".")
        descriptor = release.column(table, name)
        assert descriptor is not None
        allowed = descriptor.fields.permissible_values
        declared = [] if allowed is None else [entry.value for entry in allowed.values]
        assert [row["values"][0]["data"] for row in column["categories"]] == declared
        assert all(row["proportion"]["excluded"] is None for row in column["categories"])
    everything = json.dumps(result.model_dump(mode="json"))
    for undeclared in ('"old"', '"tall"', '"young"', '"A"'):
        assert undeclared not in everything
    assert [row["values"][0]["data"] for row in columns[1]["categories"]] == ["E", "B"]
    for analysed in result.analysed:
        assert analysed.variables is not None
        assert all(one.n is None for one in analysed.variables[1:])


TEN_APPLES = {"kind": "value", "column": "trees.variety", "values": ["apple"]}


def _keyed_orchard(
    world: World,
    crops: Sequence[str],
    key: str,
    key_type: str,
    key_values: Sequence[str] | None,
    record_filter: Mapping[str, list[str]] | None,
    grades: str,
) -> None:
    """Ten apple trees whose harvests are keyed by their tree and ``key`` (round 1 of #74's
    review, B1), their grades declaring ``grades``, every tree's harvests recorded."""
    trees = ["tree_id,variety"] + [f"tree{n},apple" for n in range(1, 11)]
    world.publish(
        "orchard",
        {
            "trees.csv": ("\n".join(trees) + "\n").encode(),
            "harvests.csv": ("\n".join(crops) + "\n").encode(),
        },
    )
    edits: list[Any] = [
        {
            "op": "set",
            "descriptor": "harvests",
            "pointer": "/fields/primary_key",
            "value": ["tree_id", key],
        },
        {"op": "set", "descriptor": "harvests", "pointer": "/fields/role", "value": "event"},
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
            "value": {"values": [{"value": v} for v in grades], "ordered": False},
        },
        {
            "op": "set",
            "descriptor": f"harvests.{key}",
            "pointer": "/fields/datatype",
            "value": key_type,
        },
        {
            "op": "put",
            "descriptor": {
                "kind": "relationship",
                "id": "rel:harvests.tree_id",
                "label": "A tree's harvest",
                "fields": {
                    "child_table": "harvests",
                    "child_columns": ["tree_id"],
                    "parent_table": "trees",
                    "parent_columns": ["tree_id"],
                    "cardinality": "many-to-one",
                },
            },
        },
        {
            "op": "put",
            "descriptor": {
                "kind": "coverage",
                "id": "cov:harvests.tree_id",
                "label": "Harvests are recorded",
                "fields": {
                    "relationship": "rel:harvests.tree_id",
                    "parents": "all",
                    **({"record_filter": dict(record_filter)} if record_filter else {}),
                },
            },
        },
    ]
    if key_values is not None:
        edits.append(
            {
                "op": "set",
                "descriptor": f"harvests.{key}",
                "pointer": "/fields/permissible_values",
                "value": {"values": [{"value": v} for v in key_values], "ordered": False},
            }
        )
    world.curate("orchard", *edits)


def _grades(world: World, floor: int | None) -> Output | list[Refusal]:
    written = {
        "aibi": "1",
        "dataset": "orchard",
        "unit": "trees",
        "cohorts": {"all": {"all": [TEN_APPLES]}},
        "views": [
            {
                "analysis": "summary.distribution",
                "cohorts": ["all"],
                "params": {"columns": [{"column": "harvests.grade", "each": "category"}]},
            }
        ],
    }
    return answer(catalog_of(world, floor=floor), "run_analysis", {"document": written})


def _rows(found: Output | list[Refusal]) -> list[tuple[Any, Any, Any]]:
    assert isinstance(found, AnalysisResults), found
    column = found.results[0].values.model_dump(mode="json")["positions"][0]["columns"][0]
    return [
        (row["values"][0]["data"], row["proportion"]["numerator"], row["proportion"]["denominator"])
        for row in column["categories"]
    ]


def test_memberships_of_one_row_per_unit_by_key_and_record_filter_are_withheld_under_a_floor(
    world: World,
) -> None:
    """Round 1 of #74's review, B1, repro 1: harvests keyed by their tree and a season the
    record filter fixes to ``x1`` reach one row per tree, so of a, b and c held by 5, 4 and 1 a
    shown 5 and 4 would pin c's 1. Under a floor the memberships are withheld (D383); without
    one they run."""
    crops = ["tree_id,season,grade"] + [
        f"tree{n},x1,{g}" for n, g in enumerate(["a"] * 5 + ["b"] * 4 + ["c"], start=1)
    ]
    _keyed_orchard(world, crops, "season", "category", ["x1"], {"season": ["x1"]}, "abc")
    found = refused(_grades(world, 3))
    assert (found.code, found.path) == (
        RefusalCode.WITHHELD_UNDER_K,
        "/views/0/params/columns/0/each",
    )
    assert _rows(_grades(world, None)) == [("a", 5, 10), ("b", 4, 10), ("c", 1, 10)]


def test_memberships_of_two_rows_per_unit_by_a_boolean_in_the_key_are_withheld_under_a_floor(
    world: World,
) -> None:
    """Round 1 of #74's review, B1, repro 2: harvests keyed by their tree and a boolean reach
    two rows per tree, so of four grades a shown 10, 5 and 4 would pin c's 1. Under a floor the
    memberships are withheld (D383); without one they run."""
    crops = ["tree_id,first,grade"]
    for n, g in enumerate(["b"] * 5 + ["d"] * 4 + ["c"], start=1):
        crops += [f"tree{n},true,a", f"tree{n},false,{g}"]
    _keyed_orchard(world, crops, "first", "boolean", None, None, "abcd")
    found = refused(_grades(world, 3))
    assert (found.code, found.path) == (
        RefusalCode.WITHHELD_UNDER_K,
        "/views/0/params/columns/0/each",
    )
    assert _rows(_grades(world, None))[2] == ("c", 1, 10)


ONE_EACH = ["a"] * 5 + ["b"] * 4 + ["c"]
"""Grades of ten trees' harvests, one each: shown 5 and 4 of 10 would pin c's 1 (round 2 of
#74's review, B2)."""


def _set(descriptor: str, at: str, value: Any) -> dict[str, Any]:
    return {"op": "set", "descriptor": descriptor, "pointer": at, "value": value}


def _put(kind: str, id: str, **fields: Any) -> dict[str, Any]:
    return {"op": "put", "descriptor": {"kind": kind, "id": id, "label": id, "fields": fields}}


def _link(
    child: str,
    columns: Sequence[str],
    parent: str,
    parent_columns: Sequence[str],
    *,
    role: str | None = None,
    one_to_one: bool = False,
) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "child_table": child,
        "child_columns": list(columns),
        "parent_table": parent,
        "parent_columns": list(parent_columns),
        "cardinality": "one-to-one" if one_to_one else "many-to-one",
        **({"role": role} if role else {}),
    }
    return _put("relationship", f"rel:{child}.{role or '+'.join(columns)}", **fields)


def _covered(relationship: str, record_filter: Mapping[str, list[str]] | None = None) -> Any:
    given = {"record_filter": dict(record_filter)} if record_filter else {}
    return _put(
        "coverage", "cov:" + relationship[4:], relationship=relationship, parents="all", **given
    )


def _graded(grades: str) -> list[dict[str, Any]]:
    values = {"values": [{"value": v} for v in grades], "ordered": False}
    return [
        _set("harvests.grade", "/fields/datatype", "category"),
        _set("harvests.grade", "/fields/permissible_values", values),
        _set("harvests", "/fields/role", "event"),
    ]


def _orchard_of(world: World, files: Mapping[str, Sequence[str]], *edits: Any) -> None:
    """Ten apple trees, the tables ``files`` gives as CSV rows, curated by ``edits``."""
    trees = ["tree_id,variety"] + [f"tree{n},apple" for n in range(1, 11)]
    blobs = {"trees.csv": ("\n".join(trees) + "\n").encode()}
    blobs |= {name: ("\n".join(rows) + "\n").encode() for name, rows in files.items()}
    world.publish("orchard", blobs)
    world.curate("orchard", *edits)


def _offers_each(world: World) -> bool:
    """Whether the grades asked bare under a floor are refused offering their memberships
    (``resolve.bounded_rows``, which ``_offered`` reads)."""
    written = {
        "aibi": "1",
        "dataset": "orchard",
        "unit": "trees",
        "cohorts": {"all": {"all": [TEN_APPLES]}},
        "views": [
            {
                "analysis": "summary.distribution",
                "cohorts": ["all"],
                "params": {"columns": [{"column": "harvests.grade"}]},
            }
        ],
    }
    found = refused(answer(catalog_of(world, floor=3), "run_analysis", {"document": written}))
    assert found.code == RefusalCode.AGGREGATE_REQUIRED
    return {"text": 'each: "category"'} in [a.model_dump() for a in found.alternatives]


def _withheld_and_pinned(world: World) -> None:
    """Under a floor the grades' memberships are withheld (D383), and not offered for the bare
    column; without one, c's count is 1, which a shown 5 and 4 of 10 would have pinned."""
    found = refused(_grades(world, 3))
    assert (found.code, found.path) == (
        RefusalCode.WITHHELD_UNDER_K,
        "/views/0/params/columns/0/each",
    )
    assert not _offers_each(world)
    assert _rows(_grades(world, None)) == [("a", 5, 10), ("b", 4, 10), ("c", 1, 10)]


def test_memberships_bounded_by_another_coverage_s_record_filter_are_withheld_under_a_floor(
    world: World,
) -> None:
    """B2, P1: harvests keyed by their tree and a season that the record filter of another
    relationship's coverage (to their pickers) fixes to ``x1`` reach one row per tree."""
    crops = ["tree_id,season,picker_id,grade"] + [
        f"tree{n},x1,p1,{g}" for n, g in enumerate(ONE_EACH, start=1)
    ]
    _orchard_of(
        world,
        {"harvests.csv": crops, "pickers.csv": ["picker_id", "p1"]},
        _set("harvests", "/fields/primary_key", ["tree_id", "season"]),
        _set("harvests.season", "/fields/datatype", "category"),
        *_graded("abc"),
        _link("harvests", ["tree_id"], "trees", ["tree_id"]),
        _covered("rel:harvests.tree_id"),
        _link("harvests", ["picker_id"], "pickers", ["picker_id"]),
        _covered("rel:harvests.picker_id", {"season": ["x1"]}),
    )
    _withheld_and_pinned(world)


def test_memberships_of_a_keyless_child_whose_columns_are_a_parent_s_are_withheld_under_a_floor(
    world: World,
) -> None:
    """B2, P2 (and m2): harvests without a key, whose tree is the parent columns of another
    relationship (from notes), which the gate keeps unique (D230): one row per tree."""
    crops = ["harvest_id,tree_id,grade"] + [
        f"{n},tree{n},{g}" for n, g in enumerate(ONE_EACH, start=1)
    ]
    _orchard_of(
        world,
        {"harvests.csv": crops, "notes.csv": ["note_id,tree_ref", "1,tree1"]},
        {"op": "remove", "descriptor": "harvests", "pointer": "/fields/primary_key"},
        _set("harvests.harvest_id", "/fields/datatype", "integer"),
        *_graded("abc"),
        _link("harvests", ["tree_id"], "trees", ["tree_id"]),
        _covered("rel:harvests.tree_id"),
        _link("notes", ["tree_ref"], "harvests", ["tree_id"]),
    )
    _withheld_and_pinned(world)


def test_memberships_of_a_keyless_child_nothing_bounds_are_disclosed_under_a_floor(
    world: World,
) -> None:
    """m2: harvests without a key, and no set of their columns the gate keeps unique, reach any
    number of rows per tree: their memberships are disclosed under a floor, c's 1 hidden."""
    crops = ["tree_id,grade"] + [f"tree{n},{g}" for n, g in enumerate(ONE_EACH, start=1)]
    _orchard_of(
        world,
        {"harvests.csv": crops},
        {"op": "remove", "descriptor": "harvests", "pointer": "/fields/primary_key"},
        *_graded("abc"),
        _link("harvests", ["tree_id"], "trees", ["tree_id"]),
        _covered("rel:harvests.tree_id"),
    )
    assert _rows(_grades(world, 3)) == [("a", 5, 10), ("b", 4, 10), ("c", None, 10)]
    assert _offers_each(world)


def test_memberships_bounded_by_another_one_to_one_relationship_are_withheld_under_a_floor(
    world: World,
) -> None:
    """B2, P3: harvests keyed by their own id, with a second, one-to-one relationship over their
    tree (to a registry), which the gate refuses two children of (``CARDINALITY_VIOLATED``)."""
    crops = ["harvest_id,tree_id,grade"] + [
        f"{n},tree{n},{g}" for n, g in enumerate(ONE_EACH, start=1)
    ]
    registry = ["reg_id"] + [f"tree{n}" for n in range(1, 11)]
    _orchard_of(
        world,
        {"harvests.csv": crops, "registry.csv": registry},
        _set("harvests", "/fields/primary_key", ["harvest_id"]),
        _set("harvests.harvest_id", "/fields/datatype", "integer"),
        _set("registry", "/fields/primary_key", ["reg_id"]),
        *_graded("abc"),
        _link("harvests", ["tree_id"], "trees", ["tree_id"], role="tree"),
        _covered("rel:harvests.tree"),
        _link("harvests", ["tree_id"], "registry", ["reg_id"], role="registered", one_to_one=True),
    )
    _withheld_and_pinned(world)


def test_memberships_bounded_by_a_derived_column_in_the_key_are_withheld_under_a_floor(
    world: World,
) -> None:
    """B2, P5: harvests keyed by their tree and a column a value map derives onto one value,
    which the gate checks the key's uniqueness on: one row per tree."""
    crops = ["tree_id,season,grade"] + [f"tree{n},x1,{g}" for n, g in enumerate(ONE_EACH, start=1)]
    derived = {"op": "value_map", "input": "season", "map": {"x1": "k", "x2": "k"}}
    _orchard_of(
        world,
        {"harvests.csv": crops},
        _put("column", "harvests.s", datatype="category", derived=derived),
        _set("harvests", "/fields/primary_key", ["tree_id", "s"]),
        _set("harvests.season", "/fields/datatype", "category"),
        *_graded("abc"),
        _link("harvests", ["tree_id"], "trees", ["tree_id"]),
        _covered("rel:harvests.tree_id"),
    )
    _withheld_and_pinned(world)


VALUE_OF = {"category": "x1", "integer": "1", "boolean": "true"}


def _two_steps(
    world: World,
    visit: tuple[str, str, Mapping[str, list[str]] | None],
    crop: tuple[str, str],
) -> None:
    """m1: trees, their visits keyed by the tree and ``visit``'s column, and the visits'
    harvests keyed by the visit (a composite foreign key) and ``crop``'s column; one visit and
    one harvest per tree."""
    column, datatype, record_filter = visit
    crop_column, crop_type = crop
    visits = [f"tree_id,{column}"] + [f"tree{n},{VALUE_OF[datatype]}" for n in range(1, 11)]
    crops = [f"tree_id,{column},{crop_column},grade"] + [
        f"tree{n},{VALUE_OF[datatype]},{VALUE_OF[crop_type]},{g}"
        for n, g in enumerate(ONE_EACH, start=1)
    ]
    via = f"rel:harvests.tree_id+{column}"
    _orchard_of(
        world,
        {"visits.csv": visits, "harvests.csv": crops},
        _set("visits", "/fields/primary_key", ["tree_id", column]),
        _set(f"visits.{column}", "/fields/datatype", datatype),
        _set("harvests", "/fields/primary_key", ["tree_id", column, crop_column]),
        _set(f"harvests.{column}", "/fields/datatype", datatype),
        _set(f"harvests.{crop_column}", "/fields/datatype", crop_type),
        *_graded("abc"),
        _link("visits", ["tree_id"], "trees", ["tree_id"]),
        _covered("rel:visits.tree_id", record_filter),
        _link("harvests", ["tree_id", column], "visits", ["tree_id", column]),
        _covered(via),
    )


@pytest.mark.parametrize(
    ("visit", "crop"),
    [
        (("season", "category", {"season": ["x1"]}), ("n", "integer")),
        (("visit", "integer", None), ("first", "boolean")),
        (("season", "category", {"season": ["x1"]}), ("first", "boolean")),
        (("visit", "integer", None), ("n", "integer")),
    ],
    ids=["bounded-open", "open-bounded", "bounded-bounded", "open-open"],
)
def test_memberships_over_two_down_steps_are_withheld_under_a_floor(
    world: World,
    visit: tuple[str, str, Mapping[str, list[str]] | None],
    crop: tuple[str, str],
) -> None:
    """m1 of round 2, and round 3's redesign: a path of two down steps over a composite foreign
    key is withheld under a floor, and the bare column offers no ``each``, whichever of its
    steps is open (D383: only a one-step path is proven open per unit)."""
    _two_steps(world, visit, crop)
    _withheld_and_pinned(world)


def _visits_then_harvests(world: World, key: Sequence[str]) -> None:
    """Round 3 of #74's review, B3: two visits per tree, keyed by the tree and an integer (an
    open step), and harvests under a visit (the foreign key ``(tree_id, visit)``) keyed by
    ``key``, within the tree and a boolean, so that each tree has one harvest, or two."""
    visits = ["tree_id,visit"] + [f"tree{n},{v}" for n in range(1, 11) for v in (1, 2)]
    if "first" in key:
        crops = ["tree_id,visit,first,grade"]
        for n, grade in enumerate(["b"] * 5 + ["d"] * 4 + ["c"], start=1):
            crops += [f"tree{n},1,true,a", f"tree{n},2,false,{grade}"]
    else:
        crops = ["tree_id,visit,grade"] + [
            f"tree{n},1,{g}" for n, g in enumerate(ONE_EACH, start=1)
        ]
    _orchard_of(
        world,
        {"visits.csv": visits, "harvests.csv": crops},
        _set("visits", "/fields/primary_key", ["tree_id", "visit"]),
        _set("visits.visit", "/fields/datatype", "integer"),
        _set("harvests", "/fields/primary_key", list(key)),
        _set("harvests.visit", "/fields/datatype", "integer"),
        *([_set("harvests.first", "/fields/datatype", "boolean")] if "first" in key else []),
        *_graded("abcd" if "first" in key else "abc"),
        _link("visits", ["tree_id"], "trees", ["tree_id"]),
        _covered("rel:visits.tree_id"),
        _link("harvests", ["tree_id", "visit"], "visits", ["tree_id", "visit"]),
        _covered("rel:harvests.tree_id+visit"),
    )
    release = world.store.load(world.store.latest("orchard").manifest)
    proposed = [
        {"op": "remove_descriptor", "descriptor": id}
        for id in ("cov:harvests.tree_id", "rel:harvests.tree_id")
        if id in release.by_id
    ]
    if proposed:
        # The importer may propose harvests' tree as a relationship to trees, a second path.
        world.curate("orchard", *proposed)


def test_memberships_over_an_open_step_then_a_child_keyed_by_the_unit_are_withheld(
    world: World,
) -> None:
    """B3, Q1: the visits step is open, but harvests keyed by the tree alone give one harvest
    per tree, so a shown 5 and 4 of 10 would pin c's 1: withheld under a floor, with no
    ``each`` offered."""
    _visits_then_harvests(world, ["tree_id"])
    _withheld_and_pinned(world)


def test_memberships_over_an_open_step_then_a_child_keyed_by_the_unit_and_a_boolean_are_withheld(
    world: World,
) -> None:
    """B3, Q2: harvests keyed by the tree and a boolean give two harvests per tree, so of four
    grades a shown 10, 5 and 4 would pin c's 1 (20 − 19): withheld under a floor, with no
    ``each`` offered."""
    _visits_then_harvests(world, ["tree_id", "first"])
    found = refused(_grades(world, 3))
    assert (found.code, found.path) == (
        RefusalCode.WITHHELD_UNDER_K,
        "/views/0/params/columns/0/each",
    )
    assert not _offers_each(world)
    assert _rows(_grades(world, None)) == [
        ("a", 10, 10),
        ("b", 5, 10),
        ("c", 1, 10),
        ("d", 4, 10),
    ]


def test_memberships_over_a_relationship_of_the_unit_table_to_itself_are_withheld(
    world: World,
) -> None:
    """Round 2 after the redesign, m1 (S1): trees each the child of the next under a self
    relationship reach one open step, but its child rows are the units themselves, so the
    view's own ten units bound the grades' TRUE counts and a shown 5 and 4 would pin c's 1:
    withheld under a floor, with no ``each`` offered (D383)."""
    trees = ["tree_id,variety,parent_id,grade"] + [
        f"tree{n},apple,tree{n % 10 + 1},{g}" for n, g in enumerate(ONE_EACH, start=1)
    ]
    world.publish("orchard", {"trees.csv": ("\n".join(trees) + "\n").encode()})
    values = {"values": [{"value": v} for v in "abc"], "ordered": False}
    world.curate(
        "orchard",
        _set("trees.grade", "/fields/datatype", "category"),
        _set("trees.grade", "/fields/permissible_values", values),
        _link("trees", ["parent_id"], "trees", ["tree_id"], role="parent"),
        _covered("rel:trees.parent"),
    )
    via = [{"rel": "rel:trees.parent", "dir": "down"}]

    def asked(floor: int | None, column: Mapping[str, Any]) -> Output | list[Refusal]:
        written = {
            "aibi": "1",
            "dataset": "orchard",
            "unit": "trees",
            "cohorts": {"all": {"all": []}},
            "views": [
                {
                    "analysis": "summary.distribution",
                    "cohorts": ["all"],
                    "params": {"columns": [dict(column)]},
                }
            ],
        }
        return answer(catalog_of(world, floor=floor), "run_analysis", {"document": written})

    each = {"column": "trees.grade", "via": via, "each": "category"}
    found = refused(asked(3, each))
    assert (found.code, found.path) == (
        RefusalCode.WITHHELD_UNDER_K,
        "/views/0/params/columns/0/each",
    )
    bare = refused(asked(3, {"column": "trees.grade", "via": via}))
    assert bare.code == RefusalCode.AGGREGATE_REQUIRED
    assert {"text": 'each: "category"'} not in [a.model_dump() for a in bare.alternatives]
    assert _rows(asked(None, each)) == [("a", 5, 10), ("b", 4, 10), ("c", 1, 10)]


def test_withheld_memberships_that_reached_run_analysis_would_raise_before_any_query(
    world: World, orchard: Orchard, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D383: memberships the analysis withholds under a floor (each unit holding one category
    at most, ``distribution.withheld_under_k``) are refused in phase 2, and ``run_analysis``
    raises before any query if one reached it."""
    world.publish("orchard", orchard())
    catalog = catalog_of(world, floor=3)
    ran: list[object] = []
    monkeypatch.setattr(views, "_withheld_form", lambda *_: None)
    monkeypatch.setattr(distribution, "withheld_under_k", lambda *_: "withheld")
    monkeypatch.setattr(analyses_module, "_run", lambda *args, **kwargs: ran.append(args))
    with pytest.raises(ValueError, match="never run"):
        run(catalog, distribution_document([EACH[0]]))
    assert ran == []


def test_memberships_with_more_categories_than_a_result_lists_refuse_the_call(
    world: World, orchard: Orchard, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Past ``MAX_CATEGORIES`` SQL counts no membership and gives no joint count (D380), and the
    call is refused at the column, beside another column or alone (D382)."""
    world.publish("orchard", orchard())
    monkeypatch.setattr(sql_module, "MAX_CATEGORIES", 1)
    for given in ([COLUMNS[1], EACH[0]], [EACH[0]], [EACH[0], EACH[0]]):
        refusal = refused(
            answer(catalog_of(world), "run_analysis", {"document": distribution_document(given)})
        )
        at = given.index(EACH[0])
        assert (refusal.code, refusal.path) == (
            RefusalCode.LIMIT_EXCEEDED,
            f"/views/0/params/columns/{at}",
        )
        assert refusal.limit is not None
        assert refusal.limit.name == "categories"


LONG = "x" * (MAX_TEXT + 1)
UNWRITTEN = {
    "longer": LONG,
    "holding a noncharacter": "pe\ufdd0ar",
    "a noncharacter": "\ufffe",
}
"""Cells a result cannot write as data (``common.unwritable``, D271's rule): more than
``MAX_TEXT`` characters, and text that is not Unicode, which a CSV file's cells may hold."""


def _tagged(trees: bytes, tree: str, tag: str) -> bytes:
    """The trees' CSV text with ``tag`` added to the tags of ``tree``."""
    lines = trees.decode().split("\n")
    at = lines[0].split(",").index("tags")
    for n, line in enumerate(lines):
        cells = line.split(",")
        if cells[0] == tree:
            cells[at] = f"{cells[at]};{tag}"
            lines[n] = ",".join(cells)
    return "\n".join(lines).encode()


@pytest.mark.parametrize("unwritten", list(UNWRITTEN))
@pytest.mark.parametrize(
    ("analysis", "given"),
    [
        ("summary.distribution", {"column": "trees.variety"}),
        ("summary.distribution", ROWS[0]),
        ("summary.distribution", EACH[0]),
        ("summary.distribution", EACH[2]),
        ("compare.columns", {"column": "trees.variety"}),
    ],
    ids=[
        "categories",
        "a count of rows",
        "memberships",
        "a list's memberships",
        "compared categories",
    ],
)
def test_a_category_longer_than_a_result_writes_or_not_unicode_refuses_the_call_at_its_column(
    world: World,
    orchard: Orchard,
    caplog: pytest.LogCaptureFixture,
    analysis: str,
    given: dict[str, Any],
    unwritten: str,
) -> None:
    """m2 of #72's review and m1 of its round 2 (D368, D382): a tree's variety, a harvest's grade
    and a tree's tag of more than ``MAX_TEXT`` characters, which no output's text holds, are
    refused as ``LIMIT_EXCEEDED`` at the column, naming ``text_characters``, as a covariate's
    long level is (``cox.LongLevel``), and those that are not Unicode text, holding a
    noncharacter, as ``NOT_SUPPORTED`` there, naming no limit, never raised as an internal
    error, for categories, a count of rows' and memberships alike, a list's included, every
    tree's harvests recorded so that the count pools them; neither the refusal nor the log
    quotes the cell."""
    label = UNWRITTEN[unwritten]
    files = orchard()
    files["trees.csv"] = files["trees.csv"].replace(b"\ntree4,pear,", f"\ntree4,{label},".encode())
    files["trees.csv"] = _tagged(files["trees.csv"], "tree4", label)
    files["harvests.csv"] = files["harvests.csv"].replace(
        b"\nh2,tree3,12,A\n", f"\nh2,tree3,12,{label}\n".encode()
    )
    world.publish("orchard", files)
    coverage = {
        "kind": "coverage",
        "id": "cov:harvests.tree_id",
        "label": "Every tree's harvests are recorded",
        "fields": {"relationship": "rel:harvests.tree_id", "parents": "all"},
    }
    world.curate("orchard", {"op": "put", "descriptor": coverage})
    cohorts = {"apple": [APPLE], "old": [OLD]}
    written = (
        columns_document([given], cohorts, overlap="allow")
        if analysis == "compare.columns"
        else document(
            cohorts,
            view={
                "analysis": analysis,
                "cohorts": list(cohorts),
                "params": {"columns": [COLUMNS[1], given]},
            },
        )
    )
    refusal = refused(answer(catalog_of(world), "run_analysis", {"document": written}))
    at = 0 if analysis == "compare.columns" else 1
    long = label == LONG
    assert (refusal.code, refusal.path) == (
        RefusalCode.LIMIT_EXCEEDED if long else RefusalCode.NOT_SUPPORTED,
        f"/views/0/params/columns/{at}",
    )
    assert refusal.limit == (Limit(name="text_characters", max=MAX_TEXT) if long else None)
    said = "".join(str(one.model_dump().get("text") or "") for one in refusal.message)
    assert ("characters" if long else "not Unicode text") in said
    assert label not in refusal.model_dump_json()
    assert label not in caplog.text


def test_a_distribution_s_issuance_records_its_materialisation_after_its_cohorts_counts(
    world: World, orchard: Orchard
) -> None:
    world.publish("orchard", orchard())
    catalog = catalog_of(world)
    [result] = run(catalog, distribution_document(COLUMNS[:2])).results
    issuance = explained(catalog, result.issuance.id).issuance
    assert issuance is not None
    assert isinstance(issuance.sql, dict)
    queries = cast(list[dict[str, list[str]]], issuance.sql["queries"])
    assert len(queries) == 3
    assert len(queries[2]["statements"]) == 2 * 3


def test_views_that_read_the_same_materialisation_share_its_run(
    world: World, orchard: Orchard, monkeypatch: pytest.MonkeyPatch
) -> None:
    world.publish("orchard", orchard())
    ran: list[int] = []
    given = analyses_module.run_views

    def counted(
        cohorts: Sequence[Any], crossings: Sequence[Any], read: Sequence[Any], *args: Any, **kw: Any
    ) -> Any:
        ran.append(len(read))
        return given(cohorts, crossings, read, *args, **kw)

    monkeypatch.setattr(analyses_module, "run_views", counted)
    written = distribution_document(COLUMNS[:1])
    written["views"] = written["views"] * 3
    first, second, third = run(catalog_of(world), written).results
    assert ran == [1]
    assert first.digest == second.digest == third.digest


def test_a_column_with_more_categories_than_a_result_lists_is_refused(
    world: World, orchard: Orchard, monkeypatch: pytest.MonkeyPatch
) -> None:
    world.publish("orchard", orchard())
    monkeypatch.setattr(distribution, "MAX_CATEGORIES", 2)
    written = distribution_document([COLUMNS[1], COLUMNS[0]])
    written["cohorts"]["apple"] = {"all": []}
    refusal = refused(answer(catalog_of(world), "run_analysis", {"document": written}))
    assert (refusal.code, refusal.path) == (
        RefusalCode.LIMIT_EXCEEDED,
        "/views/0/params/columns/1",
    )
    assert refusal.limit is not None
    assert refusal.limit.name == "categories"


def test_under_a_floor_a_number_without_bins_or_a_declared_range_is_refused(
    world: World, orchard: Orchard
) -> None:
    world.publish("orchard", orchard())
    written = distribution_document([{"column": "trees.height_m"}])
    refusal = refused(answer(catalog_of(world, floor=3), "run_analysis", {"document": written}))
    assert (refusal.code, refusal.path) == (
        RefusalCode.MISSING_MEMBER,
        "/views/0/params/columns/0/bins",
    )
    [result] = run(catalog_of(world), written).results
    positions: Any = result.values.positions
    height = positions[0]["columns"][0]
    assert height["histogram"]["edges_from"] == "data"


def members_document(**view: Any) -> dict[str, Any]:
    return document(view={"analysis": "summary.members", "cohorts": ["apple"], **view})


COMPARED: list[dict[str, Any]] = [
    {"column": "trees.tags", "aggregate": "some", "values": ["old"]},
    {"column": "trees.height_m"},
    {"column": "harvests.kg", "aggregate": "mean"},
    {"column": "harvests.kg", "aggregate": "max", "empty": 0},
    {
        "column": "harvests.harvest_id",
        "aggregate": "count",
        "where": [{"kind": "value", "column": "harvests.grade", "values": ["A"]}],
    },
]
"""A comparison's columns over the orchard: a question about a list, a number with missing
values, the mean and the greatest of a column below the unit, and a count of rows."""


def columns_document(
    given: Sequence[Mapping[str, Any]] = COMPARED,
    cohorts: Mapping[str, Sequence[Any]] | None = None,
    **view: Any,
) -> dict[str, Any]:
    names = list(cohorts or {"apple": [APPLE], "pear": [PEAR]})
    return document(
        cohorts,
        view={
            "analysis": "compare.columns",
            "cohorts": names,
            "params": {"columns": [dict(column) for column in given]},
            **view,
        },
    )


@pytest.mark.parametrize("floor", [None, 2, 3, 5])
def test_a_comparison_run_by_sql_gives_the_reference_evaluator_s_result(
    world: World, orchard: Orchard, floor: int | None
) -> None:
    world.publish("orchard", orchard(40, harvests=60))
    coverage = {
        "kind": "coverage",
        "id": "cov:harvests.tree_id",
        "label": "Every tree's harvests are recorded",
        "fields": {"relationship": "rel:harvests.tree_id", "parents": "all"},
    }
    world.curate("orchard", {"op": "put", "descriptor": coverage})
    manifest = world.store.latest("orchard").manifest
    written = columns_document(
        cohorts={"apple": [APPLE], "pear": [PEAR], "plum": [{**APPLE, "values": ["plum"]}]}
    )
    [result] = run(catalog_of(world, floor=floor), written).results
    [expected] = evaluated(world, manifest, written, floor=floor)
    assert result.digest == expected.digest
    assert result.derivation.id == expected.derivation.id
    assert result.values == expected.values
    assert result.derivation.analysis.id == "compare.columns"
    assert len(result.charts) == len(COMPARED)
    tests = [column["test"] for column in cast(Any, result.values.view)["columns"]]
    if floor is None:
        assert [test["method"] for test in tests] == [
            "chi_squared",
            "welch_anova",
            "welch_anova",
            "welch_anova",
            "welch_anova",
        ]
        assert all(test["p"] is not None for test in tests)


def test_a_comparison_s_issuance_records_its_materialisation_and_the_units_its_cohorts_share(
    world: World, orchard: Orchard
) -> None:
    world.publish("orchard", orchard())
    catalog = catalog_of(world)
    [result] = run(catalog, columns_document(COMPARED[:2])).results
    issuance = explained(catalog, result.issuance.id).issuance
    assert issuance is not None
    assert isinstance(issuance.sql, dict)
    queries = cast(list[dict[str, list[str]]], issuance.sql["queries"])
    assert len(queries) == 3
    assert len(queries[2]["statements"]) == 2 * 3 + 1
    assert "FILTER" in queries[2]["statements"][-1].upper()


def test_a_comparison_of_cohorts_that_share_units_is_refused_with_their_shared_count(
    world: World, orchard: Orchard
) -> None:
    world.publish("orchard", orchard())
    catalog = catalog_of(world)
    written = columns_document(COMPARED[1:2], cohorts={"apple": [APPLE], "old": [OLD]})
    refusal = refused(answer(catalog, "run_analysis", {"document": written}))
    assert (refusal.code, refusal.path) == (RefusalCode.COHORTS_OVERLAP, "/views/0")
    assert refusal.counts is not None
    [shared] = refusal.counts
    assert shared.population.n_true
    allowed = columns_document(
        COMPARED[1:2], cohorts={"apple": [APPLE], "old": [OLD]}, overlap="allow"
    )
    [result] = run(catalog, allowed).results
    compared = cast(Any, result.values.view)["columns"][0]
    assert compared["test"]["not_estimable"]["/p"] == "overlapping_cohorts"
    assert CaveatCode.COHORTS_OVERLAP in {caveat.code for caveat in result.caveats}


def test_a_comparison_and_a_distribution_of_one_column_share_one_materialisation(
    world: World, orchard: Orchard, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The comparison's count of the units its cohorts share is added to the one run, and the
    distribution records its statements without it, as it would alone (D339)."""
    world.publish("orchard", orchard())
    ran: list[tuple[int, list[bool]]] = []
    given = analyses_module.run_views

    def counted(
        cohorts: Sequence[Any], crossings: Sequence[Any], read: Sequence[Any], *args: Any, **kw: Any
    ) -> Any:
        ran.append((len(read), list(kw["shared"])))
        return given(cohorts, crossings, read, *args, **kw)

    monkeypatch.setattr(analyses_module, "run_views", counted)
    catalog = catalog_of(world, cache=False)
    alone = run(catalog, distribution_document(COMPARED[1:2])).results[0]
    written = columns_document(COMPARED[1:2])
    written["views"].insert(
        0,
        {
            "analysis": "summary.distribution",
            "cohorts": ["apple", "pear"],
            "params": {"columns": [dict(COMPARED[1])]},
        },
    )
    described, compared = run(catalog, written).results
    assert ran == [(1, [False]), (1, [True])]
    assert described.digest == alone.digest

    def statements(result: Any) -> list[list[str]]:
        issuance = explained(catalog, result.issuance.id).issuance
        assert issuance is not None
        assert isinstance(issuance.sql, dict)
        queries = cast(list[dict[str, list[str]]], issuance.sql["queries"])
        return [query["statements"] for query in queries]

    assert statements(described) == statements(alone)
    assert statements(compared)[-1] == [*statements(alone)[-1], statements(compared)[-1][-1]]
    assert "FILTER" in statements(compared)[-1][-1].upper()


def test_under_a_floor_a_comparison_and_a_distribution_show_one_column_s_rows_alike(
    world: World, orchard: Orchard
) -> None:
    world.publish("orchard", orchard(40, harvests=60))
    written = columns_document([{"column": "trees.variety"}, COMPARED[0]])
    written["views"].append(
        {
            "analysis": "summary.distribution",
            "cohorts": ["apple", "pear"],
            "params": {"columns": [{"column": "trees.variety"}, COMPARED[0]]},
        }
    )
    compared, described = run(catalog_of(world, floor=3), written).results
    for position in (0, 1):
        for column in (0, 1):
            assert (
                cast(Any, compared.values.positions[position])["columns"][column]
                == cast(Any, described.values.positions[position])["columns"][column]
            )


def test_a_comparison_s_bootstrap_is_the_same_by_sql_and_on_every_run(
    world: World, orchard: Orchard
) -> None:
    world.publish("orchard", orchard(40, harvests=60))
    catalog = catalog_of(world)
    written = columns_document(COMPARED[1:3])
    first = run(catalog, written).results[0]
    again = run(catalog, written).results[0]
    assert first.digest == again.digest
    assert first.issuance.id != again.issuance.id
    medians = [
        e
        for e in cast(Any, first.values.view)["columns"][0]["effects"]
        if e["measure"] == "median_difference"
    ]
    assert medians
    assert all(effect["ci"]["low"] is not None for effect in medians)


def test_members_listed_by_sql_are_the_reference_evaluator_s_page_for_page(
    world: World, orchard: Orchard
) -> None:
    published = world.publish("orchard", orchard(40))
    for params in ({}, {"offset": 3, "limit": 4}, {"offset": 12, "limit": 5}, {"offset": 99}):
        written = members_document(params=params)
        [result] = run(catalog_of(world), written).results
        [expected] = evaluated(world, published.manifest, written)
        assert result.digest == expected.digest
        assert result.derivation.id == expected.derivation.id
        assert result.values == expected.values
        assert result.charts == []
    [whole] = run(catalog_of(world), members_document(params={"limit": 1000})).results
    [position] = whole.values.model_dump(mode="json")["positions"]
    assert len(position["keys"]) == whole.population[0].n_true
    assert position["columns"] == [{"data": "trees.tree_id"}]


def test_a_members_issuance_records_its_cohort_s_count_then_a_listing_that_holds_no_key(
    world: World, orchard: Orchard
) -> None:
    world.publish("orchard", orchard())
    catalog = catalog_of(world)
    [result] = run(catalog, members_document(params={"limit": 1000})).results
    [position] = result.values.model_dump(mode="json")["positions"]
    listed = {key[0]["data"] for key in position["keys"]}
    assert listed
    issuance = explained(catalog, result.issuance.id).issuance
    assert issuance is not None
    assert isinstance(issuance.sql, dict)
    queries = cast(list[dict[str, Any]], issuance.sql["queries"])
    assert [len(query["statements"]) for query in queries] == [1, 1]
    [counting], [listing] = (query["statements"] for query in queries)
    assert "COUNT(" in counting.upper()
    assert f"LIMIT {MAX_LISTED + 1}" not in counting
    assert listing.endswith(f"LIMIT {MAX_LISTED + 1}")
    recorded = json.dumps(issuance.model_dump(mode="json"))
    assert [key for key in listed if f'"{key}"' in recorded] == []


def test_views_that_list_two_cohorts_members_in_one_call_give_each_cohort_s_own_keys(
    world: World, orchard: Orchard
) -> None:
    published = world.publish("orchard", orchard(40))
    written = distribution_document(COLUMNS[:1])
    for name in ("pear", "apple"):
        written["views"].append(
            {"analysis": "summary.members", "cohorts": [name], "params": {"limit": 1000}}
        )
    _, pears, apples = run(catalog_of(world), written).results
    _, expected_pears, expected_apples = evaluated(world, published.manifest, written)
    assert (pears.values, apples.values) == (expected_pears.values, expected_apples.values)
    assert (pears.digest, apples.digest) == (expected_pears.digest, expected_apples.digest)
    listed = [
        {key[0]["data"] for key in result.values.model_dump(mode="json")["positions"][0]["keys"]}
        for result in (pears, apples)
    ]
    assert all(listed)
    assert not listed[0] & listed[1]


def test_views_that_list_one_cohort_s_members_share_its_listing(
    world: World, orchard: Orchard, monkeypatch: pytest.MonkeyPatch
) -> None:
    world.publish("orchard", orchard())
    listings: list[int] = []
    given = analyses_module.run_views

    def counted(*args: Any, **kw: Any) -> Any:
        listings.append(len(kw["members"]))
        return given(*args, **kw)

    monkeypatch.setattr(analyses_module, "run_views", counted)
    written = members_document()
    written["views"].append({"analysis": "summary.members", "cohorts": ["apple"]})
    written["views"].append(
        {"analysis": "summary.members", "cohorts": ["apple"], "params": {"offset": 1}}
    )
    first, second, third = run(catalog_of(world), written).results
    assert listings == [1]
    assert first.digest == second.digest
    assert third.derivation.id != first.derivation.id


@pytest.mark.parametrize("floor", [2, 3, 5])
def test_under_a_floor_no_tool_lists_or_names_a_member_s_key(
    world: World, orchard: Orchard, floor: int
) -> None:
    world.publish("orchard", orchard())
    written = members_document()
    catalog = catalog_of(world, floor=floor)
    [result] = run(catalog_of(world), members_document(params={"limit": 1000})).results
    listed = [
        key[0]["data"] for key in result.values.model_dump(mode="json")["positions"][0]["keys"]
    ]
    assert listed
    for tool in ("validate_document", "count_cohort", "run_analysis"):
        found = answer(catalog, tool, {"document": written})
        dumped = json.dumps(
            [refusal.model_dump(mode="json") for refusal in found]
            if isinstance(found, list)
            else found.model_dump(mode="json")
        )
        assert [key for key in listed if f'"{key}"' in dumped] == []
        assert "WITHHELD_UNDER_K" in dumped
        assert '"/views/0/analysis"' in dumped
    bodies: list[JsonValue] = [{"dataset": "orchard", "unit": "trees"}, {"dataset": "orchard"}]
    for body in bodies:
        found = answer(catalog, "list_analyses", body)
        assert isinstance(found, AnalysisListing)
        assert found.applicable is not None
        [entry] = [a for a in found.applicable if a.analysis == "summary.members"]
        assert (entry.status, entry.missing) == ("unavailable", ["min_cell_count"])
    described = answer(catalog, "describe_dataset", {"dataset": "orchard"})
    assert isinstance(described, DatasetDescription)
    [entry] = [a for a in described.applicable_analyses if a.analysis == "summary.members"]
    assert (entry.status, entry.missing) == ("unavailable", ["min_cell_count"])


def test_a_draft_under_its_published_release_s_setting_is_refused_naming_that_setting(
    world: World, orchard: Orchard
) -> None:
    world.publish("orchard", orchard())
    world.curate(
        "orchard",
        {
            "op": "set",
            "descriptor": "dataset",
            "pointer": "/fields/disclosure",
            "value": {"min_cell_count": 5, "allow_row_ids": True},
        },
    )
    opened = world.open("orchard")
    world.change(
        "orchard",
        opened,
        opened.draft,
        {
            "op": "set",
            "descriptor": "dataset",
            "pointer": "/fields/disclosure",
            "value": {"min_cell_count": 2, "allow_row_ids": True},
        },
    )
    written = members_document()
    written["dataset"] = "orchard@draft"
    refusal = refused(answer(catalog_of(world), "run_analysis", {"document": written}))
    assert (refusal.code, refusal.path) == (RefusalCode.WITHHELD_UNDER_K, "/views/0/analysis")
    said = "".join(segment.model_dump().get("text", "") for segment in refusal.message)
    assert "the latest published release's min_cell_count, 5" in said
    assert "floor" not in said


def test_an_answer_over_the_cap_names_the_first_view_that_lists_keys(
    world: World, orchard: Orchard, monkeypatch: pytest.MonkeyPatch
) -> None:
    world.publish("orchard", orchard())
    monkeypatch.setattr(worker_module, "MAX_QUERY_ANSWER_BYTES", 64)
    written = distribution_document(COLUMNS[:2])
    written["views"].append({"analysis": "summary.members", "cohorts": ["pear"]})
    written["views"].append({"analysis": "summary.members", "cohorts": ["apple"]})
    refusal = refused(answer(catalog_of(world), "run_analysis", {"document": written}))
    assert (refusal.code, refusal.path) == (RefusalCode.LIMIT_EXCEEDED, "/views/1")


def test_a_cohort_with_more_members_than_a_listing_reads_refuses_the_call(
    world: World, orchard: Orchard, monkeypatch: pytest.MonkeyPatch
) -> None:
    world.publish("orchard", orchard())
    monkeypatch.setattr(members, "MAX_LISTED", 3)
    written = members_document()
    written["views"].insert(0, {"analysis": "summary.members", "cohorts": ["apple"]})
    refusal = refused(answer(catalog_of(world), "run_analysis", {"document": written}))
    assert (refusal.code, refusal.path) == (RefusalCode.LIMIT_EXCEEDED, "/views/0")
    assert refusal.limit == Limit(name="listed_members", max=3)


@pytest.mark.parametrize("longest", ["t" * (MAX_TEXT + 1), "t\ufdd0"], ids=["longer", "not text"])
def test_a_page_holding_a_key_longer_than_a_result_writes_or_not_unicode_refuses_the_call(
    world: World, orchard: Orchard, longest: str
) -> None:
    """D331, m1 of round 2 of #72's review: a page holding a key of more than ``MAX_TEXT``
    characters refuses the call as ``LIMIT_EXCEEDED`` naming ``text_characters``, and one holding
    a key that is not Unicode text as ``NOT_SUPPORTED`` naming no limit, each naming the key
    column and never the key; a cohort whose page holds neither runs."""
    files = orchard()
    files["trees.csv"] = files["trees.csv"].replace(b"\ntree1,", f"\n{longest},".encode())
    world.publish("orchard", files)
    written = members_document(cohorts=["pear"])
    found = answer(catalog_of(world), "run_analysis", {"document": written})
    refusal = refused(found)
    long = len(longest) > MAX_TEXT
    assert (refusal.code, refusal.path) == (
        RefusalCode.LIMIT_EXCEEDED if long else RefusalCode.NOT_SUPPORTED,
        "/views/0",
    )
    assert refusal.limit == (Limit(name="text_characters", max=MAX_TEXT) if long else None)
    assert {"data": "trees.tree_id"} in [
        segment.model_dump(exclude_none=True) for segment in refusal.message
    ]
    assert "ttt" not in refusal.model_dump_json()
    assert longest not in refusal.model_dump_json()
    [result] = run(catalog_of(world), members_document(cohorts=["apple"])).results
    assert result.values.model_dump(mode="json")["positions"][0]["keys"]


def test_a_page_is_taken_by_the_call_s_deadline_less_the_time_to_record(
    world: World, orchard: Orchard, monkeypatch: pytest.MonkeyPatch
) -> None:
    world.publish("orchard", orchard())
    seen: list[float | None] = []
    selecting = members.select

    def select(*args: Any, **kwargs: Any) -> Any:
        seen.append(args[3] if len(args) > 3 else kwargs.get("ends"))
        return selecting(*args, **kwargs)

    monkeypatch.setattr(members, "select", select)
    deadline = Deadline(time.monotonic() + 20, 30.0)
    body: JsonValue = {"document": members_document()}
    found = within(deadline, lambda: answer(catalog_of(world), "run_analysis", body))
    assert not isinstance(found, list)
    assert seen == [deadline.at - RECORD_SECONDS]


def test_a_page_the_deadline_stops_refuses_the_call_as_late(
    world: World, orchard: Orchard, monkeypatch: pytest.MonkeyPatch
) -> None:
    world.publish("orchard", orchard())

    def late(*args: Any, **kwargs: Any) -> Any:
        raise CallerDeadline

    monkeypatch.setattr(members, "select", late)
    deadline = Deadline(time.monotonic() + 20, 30.0)
    body: JsonValue = {"document": members_document()}
    refusal = refused(within(deadline, lambda: answer(catalog_of(world), "run_analysis", body)))
    assert refusal.limit == Limit(name="tool_seconds", max=30)


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


def test_a_run_made_again_gives_one_id_and_one_digest_in_a_hit_of_the_first(
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
    assert second.issuance.cache_hit
    assert second.issuance.values_from == first.issuance.id
    [third] = run(catalog_of(world, cache=False), renamed).results
    assert (third.digest, third.issuance.cache_hit) == (first.digest, False)


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
    given = analyses_module.run_views

    def counted(cohorts: Sequence[Any], crossings: Sequence[Any], *args: Any, **kwargs: Any) -> Any:
        ran.append((len(cohorts), len(crossings)))
        return given(cohorts, crossings, *args, **kwargs)

    monkeypatch.setattr(analyses_module, "run_views", counted)
    [once] = run(catalog, document()).results
    catalog = catalog_of(world, cache=False)
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
    """Not even of the published result a draft over the same release has the id of."""
    world.publish("orchard", orchard())
    catalog = catalog_of(world)
    [published] = run(catalog, document()).results
    used = world.store.results.usage()
    assert used > 0
    world.open("orchard")
    run(catalog, document(dataset="orchard@draft"))
    [result] = run(catalog, document(dataset="orchard@draft")).results
    assert result.derivation.releases[0].status == "draft"
    assert CaveatCode.DRAFT_RELEASE in {caveat.code for caveat in result.caveats}
    assert not result.issuance.cache_hit
    assert result.derivation.id == published.derivation.id
    assert world.store.results.usage() == used


# --- list_analyses, describe_dataset and resources -----------------------------------------


def test_list_analyses_gives_every_entry_and_its_applicability(
    world: World, orchard: Orchard
) -> None:
    world.publish("orchard", orchard())
    catalog = catalog_of(world)
    found = answer(catalog, "list_analyses", {})
    assert isinstance(found, AnalysisListing)
    assert [entry.id for entry in found.analyses] == [
        "compare.columns",
        "compare.existence",
        "summary.distribution",
        "summary.members",
        "survival.cox",
        "survival.km",
    ]
    assert found.applicable is None
    found = answer(catalog, "list_analyses", {"dataset": "orchard", "unit": "trees"})
    assert isinstance(found, AnalysisListing)
    assert found.applicable is not None
    assert [(a.analysis, a.status) for a in found.applicable] == [
        ("compare.columns", "available_with_caveats"),
        ("compare.existence", "available"),
        ("summary.distribution", "available_with_caveats"),
        ("summary.members", "available"),
        ("survival.cox", "unavailable"),
        ("survival.km", "unavailable"),
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
    assert [a.analysis for a in found.applicable_analyses] == [
        "compare.columns",
        "compare.existence",
        "summary.distribution",
        "summary.members",
        "survival.cox",
        "survival.km",
    ]


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


@pytest.mark.parametrize(
    ("limit", "most"), [("query_seconds", 25), ("query_memory", 2 << 30), ("query_answer_bytes", 1)]
)
def test_a_run_over_its_seconds_memory_or_answer_names_the_widest_materialising_view(
    world: World, orchard: Orchard, monkeypatch: pytest.MonkeyPatch, limit: str, most: int
) -> None:
    world.publish("orchard", orchard())

    def slow(*args: Any, **kwargs: Any) -> Any:
        raise QueryRefused(
            Refusal(
                code=RefusalCode.LIMIT_EXCEEDED,
                path=None,
                message=[],
                limit=Limit(name=limit, max=most),
            )
        )

    monkeypatch.setattr(analyses_module, "run_views", slow)
    written = document()
    written["views"].append(distribution_document(COLUMNS[:2])["views"][0])
    refusal = refused(answer(catalog_of(world), "run_analysis", {"document": written}))
    assert (refusal.code, refusal.path) == (RefusalCode.LIMIT_EXCEEDED, "/views/1")


def test_a_variable_a_view_reads_twice_is_materialised_once(
    world: World, orchard: Orchard, monkeypatch: pytest.MonkeyPatch
) -> None:
    published = world.publish("orchard", orchard())
    read: list[int] = []
    given = analyses_module.run_views

    def counted(
        cohorts: Sequence[Any],
        crossings: Sequence[Any],
        asked: Sequence[Any],
        *args: Any,
        **kw: Any,
    ) -> Any:
        read.extend(len(variables) for _, variables in asked)
        return given(cohorts, crossings, asked, *args, **kw)

    monkeypatch.setattr(analyses_module, "run_views", counted)
    written = distribution_document([COLUMNS[2], COLUMNS[2]])
    [result] = run(catalog_of(world), written).results
    [expected] = evaluated(world, published.manifest, written)
    assert read == [1]
    assert result.digest == expected.digest


def test_the_server_reads_and_summarises_a_materialisation_by_the_call_s_deadline(
    world: World, orchard: Orchard, monkeypatch: pytest.MonkeyPatch
) -> None:
    world.publish("orchard", orchard())
    seen: list[tuple[str, float | None]] = []
    reading, summarising = sql_module.CompiledMaterialised.read, distribution.summarise

    def read(self: Any, answers: Any, ends: float | None = None) -> Any:
        seen.append(("read", ends))
        return reading(self, answers, ends)

    def summarise(*args: Any, **kwargs: Any) -> Any:
        seen.append(("summarise", kwargs.get("ends")))
        return summarising(*args, **kwargs)

    monkeypatch.setattr(sql_module.CompiledMaterialised, "read", read)
    monkeypatch.setattr(distribution, "summarise", summarise)
    deadline = Deadline(time.monotonic() + 20, 30.0)
    body: JsonValue = {"document": distribution_document(COLUMNS[:2])}
    found = within(deadline, lambda: answer(catalog_of(world), "run_analysis", body))
    assert not isinstance(found, list)
    ends = deadline.at - RECORD_SECONDS
    assert seen == [("read", ends), ("summarise", ends)]


def test_a_summary_the_deadline_stops_refuses_the_call_as_late(
    world: World, orchard: Orchard, monkeypatch: pytest.MonkeyPatch
) -> None:
    world.publish("orchard", orchard())

    def late(*args: Any, **kwargs: Any) -> Any:
        raise CallerDeadline

    monkeypatch.setattr(distribution, "summarise", late)
    deadline = Deadline(time.monotonic() + 20, 30.0)
    body: JsonValue = {"document": distribution_document(COLUMNS[:2])}
    refusal = refused(within(deadline, lambda: answer(catalog_of(world), "run_analysis", body)))
    assert refusal.limit == Limit(name="tool_seconds", max=30)

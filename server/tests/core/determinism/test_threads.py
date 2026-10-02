"""The thread-count determinism tests (SPEC §9.3, §13, D372): every tool whose answer carries an id
or a digest from DuckDB's queries (``count_cohort``, ``run_analysis``) gives the same answer, bar
its issuance, whatever DuckDB's ``query_threads``, over an orchard of a million trees.

Each call drives the SQL paths its analysis reads (D372): cohort counts with a lift; the crossing of
predicates, the other lift rule's included; materialisations (joint values, a ``mean``'s value rows,
extremes, empty rows, counts of rows two down steps deep) and the units cohorts share; a member
listing; input listings with an endpoint's rows, aggregates and a predicate. The cheap calls run at
one thread, at four twice and at three (an uneven split), the others at one and four.
``validate_document`` and ``explain`` run no query and are not compared.

They run only with ``-m million`` (``addopts`` deselects them), as
``uv run pytest tests/core/determinism -m million``; CI runs them in a job of their own."""

from collections.abc import Callable, Mapping, Sequence
from typing import Any, Protocol

import pytest
from pydantic import JsonValue

from aibi.core.catalog.service import Catalog
from aibi.core.schema.cohorts import AnalysisResults, CohortCounts
from aibi.core.schema.output import Output
from aibi.core.schema.pack_api import PackRegistry
from aibi.core.schema.refusals import Refusal

pytestmark = pytest.mark.million

Answer = Callable[..., Output | list[Refusal]]


class Orchard(Protocol):
    def catalog(self, threads: int, registry: PackRegistry | None = None) -> Catalog: ...

    def categories(self, column: str) -> tuple[bool, int]: ...

    def rows(self) -> dict[str, int]: ...

    def row_groups(self) -> dict[str, int]: ...


CHEAP = (1, 4, 4, 3)
COSTLY = (1, 4)

APPLE = {"kind": "value", "column": "trees.variety", "values": ["apple"]}
PEAR = {"kind": "value", "column": "trees.variety", "values": ["pear"]}
PLUM = {"kind": "value", "column": "trees.variety", "values": ["plum"]}
WIDE = {"kind": "value", "column": "trees.girth", "range": {"gte": 59}}
CLAY = {"kind": "value", "column": "trees.soil", "values": ["clay"]}
WEIGHED = {
    "kind": "exists",
    "table": "harvests",
    "where": [{"kind": "exists", "table": "weighings", "where": []}],
}
"""A question two down steps deep, through the weighings' partial coverage: its lift matters."""
PREDICATES: list[JsonValue] = [
    {"kind": "value", "column": "trees.soil", "values": ["loam"]},
    {"kind": "value", "column": "trees.girth", "range": {"gte": 40}},
    {"kind": "value", "column": "trees.ring", "range": {"lt": 100}},
    {"kind": "value", "column": "trees.tags", "values": ["old"], "match": "any"},
    {"kind": "value", "column": "trees.grafted", "values": [True]},
    {"kind": "exists", "table": "harvests", "where": []},
    {"kind": "exists", "table": "harvests", "min_count": 2, "where": []},
    {
        "kind": "exists",
        "table": "harvests",
        "where": [{"kind": "value", "column": "harvests.grade", "values": ["A"]}],
    },
    {
        "kind": "exists",
        "table": "harvests",
        "quantifier": "every",
        "where": [{"kind": "value", "column": "harvests.kg", "range": {"gte": 12}}],
    },
    WEIGHED,
    {**WEIGHED, "lift": "assessed"},
    {
        "kind": "exists",
        "table": "harvests",
        "where": [
            {
                "kind": "exists",
                "table": "weighings",
                "quantifier": "every",
                "where": [{"kind": "value", "column": "weighings.grams", "range": {"gte": 500}}],
            }
        ],
    },
    {"known": {"kind": "value", "column": "trees.girth", "range": {"gte": 30}}},
    {"not": CLAY},
    {
        "any": [
            {"kind": "value", "column": "trees.girth", "range": {"gte": 55}},
            {"kind": "value", "column": "trees.ring", "range": {"gt": 150}},
        ]
    },
    {"unknown": {"kind": "value", "column": "trees.fell", "values": [True]}},
]
COHORTS: dict[str, JsonValue] = {
    "apple": {"all": [APPLE]},
    "pear": {"all": [PEAR]},
    "plum": {"all": [PLUM]},
    "every": {"all": []},
    "wide_apple": {"all": [APPLE, WIDE]},
    "wide_plum": {"all": [PLUM, WIDE]},
    "wide_clay_apple": {"all": [APPLE, WIDE, CLAY]},
}


KG = {"column": "harvests.kg"}
VIEWS: list[dict[str, Any]] = [
    {
        "analysis": "summary.distribution",
        "cohorts": ["every"],
        "params": {
            "columns": [
                {"column": "trees.girth"},
                {"column": "trees.ring"},
                {"column": "trees.block"},
                {"column": "trees.soil"},
                {**KG, "aggregate": "mean"},
                {**KG, "aggregate": "max"},
                {"column": "harvests.harvest_id", "aggregate": "count"},
                {"column": "harvests.grade", "aggregate": "some", "values": ["A"]},
            ]
        },
    },
    {
        "analysis": "summary.distribution",
        "cohorts": ["apple", "pear"],
        "params": {
            "columns": [
                {**KG, "count": "rows"},
                {"column": "harvests.grade", "count": "rows"},
                {"column": "weighings.grams", "count": "rows", "lift": "assessed"},
            ]
        },
    },
    {
        "analysis": "compare.columns",
        "cohorts": ["apple", "pear"],
        "params": {
            "columns": [
                {"column": "trees.ring"},
                {"column": "trees.soil"},
                {"column": "trees.block"},
                {**KG, "aggregate": "mean"},
            ]
        },
    },
    {
        "analysis": "summary.members",
        "cohorts": ["apple"],
        "params": {"offset": 150_000, "limit": 1000},
    },
    {"analysis": "survival.km", "cohorts": ["wide_apple", "wide_plum"], "params": {}},
    {
        "analysis": "survival.cox",
        "cohorts": ["wide_apple", "wide_plum"],
        "params": {
            "covariates": [
                {"column": "trees.ring"},
                {"column": "trees.soil"},
                {**KG, "aggregate": "mean"},
                {"predicate": WEIGHED},
            ],
            "stratum": {"column": "trees.grafted"},
        },
    },
]
"""A view of each core analysis but ``compare.existence``, whose crossing has a test of its own, and
a second of ``summary.distribution``, of counts of rows (D378)."""
ECHO: dict[str, Any] = {
    "analysis": "echoes.echo",
    "cohorts": ["wide_clay_apple"],
    "params": {
        "columns": {
            "measure": [
                {"column": "trees.ring"},
                {"column": "harvests.kg", "aggregate": "mean"},
                {"column": "trees.tags", "aggregate": "some", "values": ["old"]},
            ]
        }
    },
}
ROWS = {
    "trees": 1_000_000,
    "harvests": 1_558_441,
    "weighings": 519_480,
    "weighed_harvests": 779_221,
}
ROW_GROUPS = {"trees": 8, "harvests": 12, "weighings": 4, "weighed_harvests": 6}


def document(views: Sequence[Mapping[str, Any]], cohorts: Sequence[str]) -> dict[str, Any]:
    return {
        "aibi": "1",
        "dataset": "orchard",
        "unit": "trees",
        "cohorts": {name: COHORTS[name] for name in cohorts},
        "views": [dict(view) for view in views],
    }


def comparable(found: Output) -> JsonValue:
    """An answer as JSON without its issuances, which name each call apart; each of them computed
    its values, none taken from a cache (§8.1), so no answer is compared with another's."""
    issued: list[JsonValue] = []

    def without(value: JsonValue) -> JsonValue:
        if isinstance(value, dict):
            if "issuance" in value:
                issued.append(value["issuance"])
            return {k: without(v) for k, v in value.items() if k != "issuance"}
        if isinstance(value, list):
            return [without(v) for v in value]
        return value

    kept = without(found.model_dump(mode="json"))
    assert issued
    assert all(isinstance(one, dict) and one["cache_hit"] is False for one in issued), issued
    return kept


def at_each(
    orchard: Orchard,
    answer: Answer,
    name: str,
    written: Mapping[str, Any],
    threads: Sequence[int],
    registry: PackRegistry | None = None,
) -> list[Output]:
    """The call's answer at each thread count, each checked equal to the first's, at one thread."""
    assert threads[0] == 1, threads
    assert len(set(threads)) > 1, threads
    found: list[Output] = []
    for count in threads:
        given = answer(orchard.catalog(count, registry), name, written)
        assert not isinstance(given, list), (count, given)
        found.append(given)
        assert comparable(given) == comparable(found[0]), count
    return found


def test_the_orchard_holds_a_million_trees_in_as_many_row_groups_as_threads_or_more(
    orchard: Orchard,
) -> None:
    assert orchard.rows() == ROWS
    groups = orchard.row_groups()
    assert groups == ROW_GROUPS
    assert orchard.categories("trees.block") == (False, 127)
    assert orchard.categories("trees.soil") == (True, 3)
    assert all(count >= max((*CHEAP, *COSTLY)) for count in groups.values()), groups


def test_a_cohort_s_count_is_the_same_whatever_duckdb_s_threads(
    orchard: Orchard, answer: Answer
) -> None:
    old = {"kind": "value", "column": "trees.tags", "values": ["old"], "match": "any"}
    written = document([], [])
    written["cohorts"] = {
        "loam": {"all": [PREDICATES[0]]},
        "weighed": {"all": [WEIGHED, {"not": old}]},
        "twice": {"all": [PREDICATES[6], PREDICATES[8]]},
    }
    [first, *_] = at_each(orchard, answer, "count_cohort", written, CHEAP)
    assert isinstance(first, CohortCounts)
    sizes = [named.count.size.numerator for named in first.counts]
    assert all(size is not None and 0 < size < 1_000_000 for size in sizes), sizes


def test_the_crossing_of_sixteen_predicates_is_the_same_whatever_duckdb_s_threads(
    orchard: Orchard, answer: Answer
) -> None:
    view = {
        "analysis": "compare.existence",
        "cohorts": ["apple", "pear", "plum"],
        "params": {"predicates": PREDICATES},
    }
    written = document([view], ["apple", "pear", "plum"])
    [first, *_] = at_each(orchard, answer, "run_analysis", written, CHEAP)
    assert isinstance(first, AnalysisResults)
    [result] = first.results
    positions: Any = result.values.positions
    assert any(p["lift_differs"] for at in positions for p in at["predicates"])


def test_every_core_analysis_gives_the_same_result_whatever_duckdb_s_threads(
    orchard: Orchard, answer: Answer
) -> None:
    written = document(VIEWS, ["every", "apple", "pear", "wide_apple", "wide_plum"])
    [first, *_] = at_each(orchard, answer, "run_analysis", written, COSTLY)
    assert isinstance(first, AnalysisResults)
    assert [r.derivation.analysis.id for r in first.results] == [v["analysis"] for v in VIEWS]
    cox: Any = first.results[-1].values.positions
    assert any(lift["lift_differs"] for at in cox for lift in at.get("lifts") or [])


def test_a_pack_s_analysis_is_handed_the_same_inputs_whatever_duckdb_s_threads(
    orchard: Orchard, answer: Answer, echoes: PackRegistry
) -> None:
    written = document([ECHO], ["wide_clay_apple"])
    [first, *_] = at_each(orchard, answer, "run_analysis", written, COSTLY, echoes)
    assert isinstance(first, AnalysisResults)
    [result] = first.results
    positions: Any = result.values.positions
    assert positions[0]["units"] > 0
    [rows] = positions[0]["endpoints"]
    assert any(row is not None for row in rows)

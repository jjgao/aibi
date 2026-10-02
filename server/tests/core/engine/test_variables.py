"""Variables, one value per unit (§9.2; D325, D326): each kind resolved, evaluated by the
reference evaluator and materialised by the SQL compiler, which must agree."""

import json
import math
import pickle
import subprocess
import sys
import time
from array import array
from collections.abc import Callable
from dataclasses import replace
from fractions import Fraction
from pathlib import Path
from typing import Any

import duckdb
import pytest
import sqlglot
from hypothesis import given
from hypothesis import strategies as st
from sqlglot import exp

from aibi.core.analyses import stats
from aibi.core.engine import build
from aibi.core.engine import sql as sql_module
from aibi.core.engine.data import Release
from aibi.core.engine.evaluate import evaluate
from aibi.core.engine.resolve import Resolution
from aibi.core.engine.sql import CompiledMaterialised, compile_materialised
from aibi.core.engine.truth import Mark
from aibi.core.engine.variables import (
    aggregated,
    evaluate_variable,
    exact_mean,
    exact_sum,
    joint,
    materialise,
)
from aibi.core.engine.worker import CallerDeadline, QueryError, Rows
from aibi.core.schema.semantics import Flag
from aibi.core.store import parquet
from aibi.core.store.tables import TableSource

City = Callable[..., Release]
Doc = Callable[..., dict[str, Any]]
INSPECTED = "rel:inspections.establishment"


def _agree(resolution: Resolution, materialised: Callable[..., Any]) -> list[Any]:
    cohorts = list(resolution.cohorts.values())
    variables = [resolution.variables[key] for key in sorted(resolution.variables)]
    found = materialised(cohorts, variables)
    expected: list[Any] = []
    for cohort, (read, together) in zip(cohorts, found, strict=True):
        members = evaluate(cohort).members
        values = [evaluate_variable(variable) for variable in variables]
        mine = tuple(
            materialise(value, members, rows=variable.kind == "rows")
            for value, variable in zip(values, variables, strict=True)
        )
        assert read == mine, (read, mine)
        if len(variables) > 1:
            assert together == joint(values, members)
        expected.append(mine)
    return expected


def test_a_column_of_the_unit_and_of_a_row_it_looks_up_give_each_unit_its_value(
    city: City, doc: Doc, variables_of: Callable[..., Resolution], materialised: Callable[..., Any]
) -> None:
    release = city(
        {
            "owners": [{"owner_id": "o0", "region": "north"}, {"owner_id": "o1", "region": None}],
            "establishments": [
                {"establishment_id": "e0", "seats": 10, "grade": "A", "owner_id": "o0"},
                {"establishment_id": "e1", "seats": None, "grade": "pending", "owner_id": "o1"},
                {"establishment_id": "e2", "seats": 40, "grade": "exempt", "owner_id": "gone"},
                {"establishment_id": "e3", "seats": 10, "grade": "B", "chain": True},
            ],
        }
    )
    resolution = variables_of(
        doc([]),
        release,
        [
            {"column": "establishments.seats"},
            {"column": "establishments.grade"},
            {"column": "owners.region"},
            {"column": "establishments.chain"},
        ],
    )
    assert resolution.refusals == []
    [(seats, grade, region, chain)] = _agree(resolution, materialised)
    assert dict(seats.values) == {10: 2, 40: 1}
    assert dict(grade.values) == {"A": 1, "B": 1}
    assert grade.excluded_units == 2
    assert dict(region.values) == {"north": 1}
    assert region.excluded["NO_PARENT"] == 2
    assert dict(chain.values) == {True: 1}


def test_aggregates_pool_the_rows_below_each_unit(
    city: City, doc: Doc, variables_of: Callable[..., Resolution], materialised: Callable[..., Any]
) -> None:
    release = city(
        {
            "establishments": [{"establishment_id": f"e{n}"} for n in range(4)],
            "inspections": [
                {"inspection_id": "i0", "establishment_id": "e0", "score": 40, "kind": "routine"},
                {"inspection_id": "i1", "establishment_id": "e0", "score": 90, "kind": "routine"},
                {"inspection_id": "i2", "establishment_id": "e1", "score": None},
                {"inspection_id": "i3", "establishment_id": "e2", "score": 70, "kind": "courtesy"},
            ],
            "complaints": [
                {"complaint_id": "c0", "establishment_id": "e0", "channel": "web", "severity": 4},
                {"complaint_id": "c1", "establishment_id": "e0", "channel": "letter"},
                {"complaint_id": "c2", "establishment_id": "e1", "channel": None, "severity": 1},
            ],
            "licence_types": [{"type_id": "t1", "tier": 1}, {"type_id": "t3", "tier": 3}],
            "licences": [
                {"licence_id": "l0", "establishment_id": "e0", "type_id": "t1"},
                {"licence_id": "l1", "establishment_id": "e0", "type_id": "t3"},
                {"licence_id": "l2", "establishment_id": "e1", "type_id": "gone"},
            ],
        }
    )
    resolution = variables_of(
        doc([]),
        release,
        [
            {"column": "inspections.score", "aggregate": "mean"},
            {"column": "inspections.score", "aggregate": "max", "empty": 0},
            {"column": "inspections.inspection_id", "aggregate": "count"},
            {"column": "complaints.severity", "aggregate": "min"},
            {"column": "licence_types.tier", "aggregate": "mean"},
            {"column": "inspections.score", "aggregate": "some", "values": [90]},
            {
                "column": "inspections.score",
                "aggregate": "count",
                "where": [{"kind": "value", "column": "inspections.kind", "values": ["routine"]}],
            },
        ],
    )
    assert resolution.refusals == []
    [(mean, most, count, least, tier, some, routine)] = _agree(resolution, materialised)
    assert dict(mean.values) == {65.0: 1, 70.0: 1}
    assert dict(most.values) == {90: 1, 70: 1, 0: 1}
    assert dict(count.values) == {2: 1, 1: 2, 0: 1}
    assert dict(least.values) == {4: 1}
    assert least.excluded["NO_INFORMATION"] == 1
    assert dict(tier.values) == {2.0: 1}
    assert dict(some.values) == {True: 1, False: 2}
    assert dict(routine.values) == {2: 1, 0: 2}


def test_the_greatest_of_an_ordered_category_follows_its_listed_order(
    city: City, doc: Doc, variables_of: Callable[..., Resolution], materialised: Callable[..., Any]
) -> None:
    release = city(
        {
            "establishments": [{"establishment_id": f"e{n}"} for n in range(5)],
            "inspections": [
                {"inspection_id": "i0", "establishment_id": "e0", "rating": "high"},
                {"inspection_id": "i1", "establishment_id": "e0", "rating": "low"},
                {"inspection_id": "i2", "establishment_id": "e1", "rating": "mid"},
                {"inspection_id": "i3", "establishment_id": "e1", "rating": "later"},
                {"inspection_id": "i4", "establishment_id": "e2", "rating": "low"},
                {"inspection_id": "i5", "establishment_id": "e2", "rating": None},
            ],
        },
    )
    resolution = variables_of(
        doc([]),
        release,
        [
            {"column": "inspections.rating", "aggregate": "max"},
            {"column": "inspections.rating", "aggregate": "min", "empty": "mid"},
        ],
    )
    assert resolution.refusals == []
    [(most, least)] = _agree(resolution, materialised)
    assert dict(most.values) == {"high": 1}
    assert (most.excluded["NOT_ASSESSED"], most.excluded["NO_INFORMATION"]) == (1, 1)
    assert most.excluded["NO_ROWS"] == 2
    assert dict(least.values) == {"low": 1, "mid": 2}


def test_a_value_not_applicable_is_skipped_and_one_not_assessed_excludes_the_unit(
    city: City, doc: Doc, variables_of: Callable[..., Resolution]
) -> None:
    release = city(
        {
            "establishments": [{"establishment_id": f"e{n}"} for n in range(3)],
            "complaints": [
                {"complaint_id": "c0", "establishment_id": "e0", "channel": "web", "severity": 2},
                {"complaint_id": "c1", "establishment_id": "e0", "channel": "web", "severity": 4},
                {"complaint_id": "c2", "establishment_id": "e1", "channel": "web", "severity": 3},
                {"complaint_id": "c3", "establishment_id": "e1", "channel": "phone"},
            ],
        }
    )
    resolution = variables_of(
        doc([]), release, [{"column": "complaints.severity", "aggregate": "mean"}]
    )
    [variable] = resolution.variables.values()
    values = evaluate_variable(variable)
    assert values[0].value == 3.0
    assert values[1].excluded == {"NO_INFORMATION"}
    assert values[2].excluded == {"NO_ROWS"}


def test_a_step_over_rows_whose_coverage_is_unknown_excludes_the_unit_under_its_reasons(
    city: City, doc: Doc, variables_of: Callable[..., Resolution]
) -> None:
    release = city(
        {
            "establishments": [{"establishment_id": "e0"}],
            "inspections": [{"inspection_id": "i0", "establishment_id": "e0", "kind": "routine"}],
            "readings": [{"reading_id": "r0", "inspection_id": "i0", "appliance": "fridge"}],
        }
    )
    where = [{"kind": "value", "column": "readings.appliance", "values": ["fridge"]}]
    resolution = variables_of(
        doc([]),
        release,
        [{"column": "readings.celsius", "aggregate": "count", "where": where}],
    )
    assert resolution.refusals == []
    [variable] = resolution.variables.values()
    [value] = evaluate_variable(variable)
    assert value.excluded == {"NOT_COVERED"}


@pytest.mark.parametrize(
    ("variable", "code", "at"),
    [
        ({"column": "core:sex"}, "NOT_SUPPORTED", "/column"),
        ({"column": "establishments.nothing"}, "UNKNOWN_COLUMN", "/column"),
        ({"column": "establishments.notes"}, "UNDECLARED_DATATYPE", "/column"),
        ({"column": "inspections.score"}, "AGGREGATE_REQUIRED", "/column"),
        ({"column": "violations.code"}, "AGGREGATE_REQUIRED", "/column"),
        ({"column": "establishments.tags"}, "AGGREGATE_REQUIRED", "/column"),
        (
            {"column": "establishments.seats", "aggregate": "max"},
            "AGGREGATE_NOT_ALLOWED",
            "/aggregate",
        ),
        (
            {"column": "inspections.kind", "aggregate": "mean"},
            "AGGREGATE_NOT_ALLOWED",
            "/aggregate",
        ),
        ({"column": "inspections.kind", "aggregate": "max"}, "AGGREGATE_NOT_ALLOWED", "/aggregate"),
        (
            {"column": "establishments.tags", "aggregate": "mean"},
            "AGGREGATE_NOT_ALLOWED",
            "/aggregate",
        ),
        (
            {"column": "inspections.score", "aggregate": "mean", "empty": "none"},
            "INVALID_CONSTANT",
            "/empty",
        ),
        ({"column": "violations.severity", "aggregate": "mean"}, "OPEN_SCOPE", "/where"),
    ],
)
def test_a_variable_its_column_does_not_take_is_refused_where_it_is_written(
    city: City,
    doc: Doc,
    variables_of: Callable[..., Resolution],
    variable: dict[str, Any],
    code: str,
    at: str,
) -> None:
    resolution = variables_of(doc([]), city(), [variable])
    assert [(r.code, r.path) for r in resolution.refusals] == [
        (code, "/views/0/params/columns/0" + at)
    ]
    assert resolution.variables == {}


def test_an_identifier_column_is_not_read_where_row_ids_are_not_allowed(
    city: City, doc: Doc, variables_of: Callable[..., Resolution]
) -> None:
    release = city(allow_row_ids=False)
    resolution = variables_of(doc([]), release, [{"column": "establishments.establishment_id"}])
    assert [(r.code, r.path) for r in resolution.refusals] == [
        ("ROW_IDS_NOT_ALLOWED", "/views/0/params/columns/0/column")
    ]
    counted = variables_of(
        doc([]), release, [{"column": "inspections.inspection_id", "aggregate": "count"}]
    )
    assert counted.refusals == []


def test_max_of_an_ordered_category_with_a_value_outside_its_list_has_no_information(
    city: City, doc: Doc, variables_of: Callable[..., Resolution], materialised: Callable[..., Any]
) -> None:
    release = city(
        {
            "establishments": [{"establishment_id": f"e{n}"} for n in range(2)],
            "inspections": [
                {"inspection_id": "i0", "establishment_id": "e0", "rating": "high"},
                {"inspection_id": "i1", "establishment_id": "e0", "rating": "extreme"},
                {"inspection_id": "i2", "establishment_id": "e1", "rating": "low"},
            ],
        },
    )
    resolution = variables_of(
        doc([]), release, [{"column": "inspections.rating", "aggregate": "max"}]
    )
    assert resolution.refusals == []
    [(most,)] = _agree(resolution, materialised)
    assert dict(most.values) == {"low": 1}
    assert most.excluded["NO_INFORMATION"] == 1


def test_a_number_column_s_negative_zero_is_zero(
    city: City, doc: Doc, variables_of: Callable[..., Resolution], materialised: Callable[..., Any]
) -> None:
    release = city({"establishments": [{"establishment_id": "e0", "frontage": -0.0}]})
    resolution = variables_of(doc([]), release, [{"column": "establishments.frontage"}])
    [(frontage,)] = _agree(resolution, materialised)
    [value] = frontage.values
    assert value == 0.0
    assert math.copysign(1.0, float(value)) == 1.0


def test_the_mean_of_integers_beyond_two_to_the_53_is_their_exact_mean_correctly_rounded() -> None:
    rows = [(2**53 + 1, 1), (2**53 + 5, 1)]
    assert aggregated("mean", rows, 2) == float(2**53 + 4)
    assert stats.mean(rows) == float(2**53 + 4)


def test_the_mean_of_huge_doubles_does_not_overflow() -> None:
    assert aggregated("mean", [(1e308, 2)], 2) == 1e308
    assert stats.mean([(1e308, 2)]) == 1e308
    assert stats.sd([(-1e200, 1), (1e200, 1)], 0.0) == math.sqrt(2) * 1e200


def test_a_multi_valued_category_without_an_aggregate_requires_one_or_its_memberships(
    city: City, doc: Doc, variables_of: Callable[..., Resolution]
) -> None:
    """D382: a bare multi-valued category is ``AGGREGATE_REQUIRED``, a descriptive analysis
    offered its memberships beside the aggregates its path allows; no part number is named. The
    violations' code scopes their grouped coverage, so no aggregate that pools rows, no count of
    them and no ``every`` of it is offered (D377, D380), and its memberships are."""
    resolution = variables_of(doc([]), city(), [{"column": "violations.code"}])
    [refusal] = resolution.refusals
    assert (refusal.code, refusal.path) == (
        "AGGREGATE_REQUIRED",
        "/views/0/params/columns/0/column",
    )
    said = json.dumps([part.model_dump() for part in refusal.message])
    assert "M3.2e" not in said
    assert [one.model_dump().get("text") for one in refusal.alternatives or []] == [
        "some",
        'each: "category"',
    ]
    each = {"column": "violations.code", "each": "category"}
    assert variables_of(doc([]), city(), [each]).refusals == []


def test_a_count_of_rows_reads_each_pooled_row_once_for_every_path_that_reaches_it(
    city: City, doc: Doc, variables_of: Callable[..., Resolution], materialised: Callable[..., Any]
) -> None:
    """A count of rows (D378) pools as an aggregate does: an establishment's grade looked up
    from each of its inspections is one row per inspection, a unit's rows excluded by the
    cell's state, and a unit with no inspection pooled with none."""
    release = city(
        {
            "establishments": [
                {"establishment_id": "e0", "grade": "A"},
                {"establishment_id": "e1", "grade": "exempt"},
                {"establishment_id": "e2", "grade": "pending"},
                {"establishment_id": "e3", "grade": "B"},
            ],
            "inspections": [
                {"inspection_id": "i0", "establishment_id": "e0", "rating": "high"},
                {"inspection_id": "i1", "establishment_id": "e0", "rating": "n/a"},
                {"inspection_id": "i2", "establishment_id": "e0", "rating": "extreme"},
                {"inspection_id": "i3", "establishment_id": "e1", "rating": "later"},
                {"inspection_id": "i4", "establishment_id": "e2", "rating": None},
            ],
        }
    )
    grades = {
        "column": "establishments.grade",
        "via": [{"rel": INSPECTED, "dir": "down"}, {"rel": INSPECTED, "dir": "up"}],
        "count": "rows",
    }
    resolution = variables_of(
        doc([]), release, [grades, {"column": "inspections.rating", "count": "rows"}]
    )
    assert resolution.refusals == []
    [(graded, rated)] = _agree(resolution, materialised)
    assert dict(graded.values) == {3: 1, 1: 2, 0: 1}
    assert graded.excluded_units == 0
    assert graded.rows is not None
    assert dict(graded.rows.values) == {"A": 3}
    assert (graded.rows.excluded["NOT_APPLICABLE"], graded.rows.excluded["NOT_ASSESSED"]) == (1, 1)
    assert rated.rows is not None
    assert dict(rated.rows.values) == {"high": 1, "extreme": 1}
    assert (rated.rows.excluded["NOT_APPLICABLE"], rated.rows.excluded["NOT_ASSESSED"]) == (1, 1)
    assert rated.rows.excluded["NO_INFORMATION"] == 1


def test_a_row_reached_by_two_paths_within_one_unit_is_counted_once_for_each_path(
    city: City, doc: Doc, variables_of: Callable[..., Resolution], materialised: Callable[..., Any]
) -> None:
    """Down, up and down the same relationship (D378): each of an establishment's two
    inspections leads back to both, so its rows are four, each inspection twice, read directly
    or through a lookup after the last down step; its aggregate ``count`` pools the same four."""
    release = city(
        {
            "establishments": [{"establishment_id": "e0", "grade": "A"}],
            "inspections": [
                {"inspection_id": "i0", "establishment_id": "e0", "score": 10},
                {"inspection_id": "i1", "establishment_id": "e0", "score": 20},
            ],
        }
    )
    again = [
        {"rel": INSPECTED, "dir": "down"},
        {"rel": INSPECTED, "dir": "up"},
        {"rel": INSPECTED, "dir": "down"},
    ]
    resolution = variables_of(
        doc([]),
        release,
        [
            {"column": "inspections.score", "via": again, "count": "rows"},
            {
                "column": "establishments.grade",
                "via": [*again, {"rel": INSPECTED, "dir": "up"}],
                "count": "rows",
            },
            {"column": "inspections.score", "via": again, "aggregate": "count"},
        ],
    )
    assert resolution.refusals == []
    [(scores, grades, counted)] = _agree(resolution, materialised)
    assert dict(scores.values) == {4: 1}
    assert scores.rows is not None
    assert dict(scores.rows.values) == {10: 2, 20: 2}
    assert dict(grades.values) == {4: 1}
    assert grades.rows is not None
    assert dict(grades.rows.values) == {"A": 4}
    assert dict(counted.values) == {4: 1}


def test_a_count_of_rows_excludes_a_unit_for_its_pooling_and_a_row_for_its_value(
    city: City, doc: Doc, variables_of: Callable[..., Resolution], materialised: Callable[..., Any]
) -> None:
    """An UNKNOWN ``where`` or coverage excludes the unit, its rows unread; an unknown value, or a
    lookup that reaches no row, excludes the row alone (D378)."""
    release = city(
        {
            "establishments": [{"establishment_id": f"e{n}"} for n in range(3)],
            "inspections": [
                {"inspection_id": "i0", "establishment_id": "e0", "kind": "routine", "score": 40},
                {"inspection_id": "i1", "establishment_id": "e0", "kind": "routine"},
                {"inspection_id": "i2", "establishment_id": "e1", "score": 90},
            ],
            "licence_types": [{"type_id": "t1", "tier": 1}],
            "licences": [
                {"licence_id": "l0", "establishment_id": "e0", "type_id": "t1"},
                {"licence_id": "l1", "establishment_id": "e0", "type_id": "gone"},
                {"licence_id": "l2", "establishment_id": "e2", "type_id": "t1"},
            ],
        }
    )
    routine = [{"kind": "value", "column": "inspections.kind", "values": ["routine"]}]
    resolution = variables_of(
        doc([]),
        release,
        [
            {"column": "inspections.score", "where": routine, "count": "rows"},
            {"column": "licence_types.tier", "count": "rows"},
        ],
    )
    assert resolution.refusals == []
    [(scores, tiers)] = _agree(resolution, materialised)
    assert (scores.excluded_units, scores.excluded["NO_INFORMATION"]) == (1, 1)
    assert dict(scores.values) == {2: 1, 0: 1}
    assert scores.rows is not None
    assert dict(scores.rows.values) == {40: 1}
    assert scores.rows.excluded["NO_INFORMATION"] == 1
    assert tiers.rows is not None
    assert dict(tiers.rows.values) == {1: 2}
    assert tiers.rows.excluded["NO_PARENT"] == 1
    assert dict(tiers.values) == {2: 1, 0: 1, 1: 1}


@pytest.mark.parametrize(
    ("variable", "code", "at"),
    [
        ({"column": "establishments.seats", "count": "rows"}, "INVALID_VALUE", "/count"),
        ({"column": "establishments.tags", "count": "rows"}, "INVALID_VALUE", "/count"),
        ({"column": "violations.severity", "count": "rows"}, "OPEN_SCOPE", "/where"),
        ({"column": "establishments.notes", "count": "rows"}, "UNDECLARED_DATATYPE", "/column"),
    ],
)
def test_a_count_of_rows_its_column_does_not_take_is_refused_where_it_is_written(
    city: City,
    doc: Doc,
    variables_of: Callable[..., Resolution],
    variable: dict[str, Any],
    code: str,
    at: str,
) -> None:
    resolution = variables_of(doc([]), city(), [variable])
    assert [(r.code, r.path) for r in resolution.refusals] == [
        (code, "/views/0/params/columns/0" + at)
    ]


def test_a_count_of_rows_reads_no_identifier_column_where_row_ids_are_not_allowed(
    city: City, doc: Doc, variables_of: Callable[..., Resolution]
) -> None:
    rows = {"column": "inspections.inspection_id", "count": "rows"}
    resolution = variables_of(doc([]), city(allow_row_ids=False), [rows])
    assert [(r.code, r.path) for r in resolution.refusals] == [
        ("ROW_IDS_NOT_ALLOWED", "/views/0/params/columns/0/column")
    ]


def test_a_count_of_rows_is_never_listed_per_member(
    city: City, doc: Doc, variables_of: Callable[..., Resolution], blobs: Callable[..., Any]
) -> None:
    release = city()
    resolution = variables_of(doc([]), release, [{"column": "inspections.kind", "count": "rows"}])
    [cohort] = resolution.cohorts.values()
    with pytest.raises(sql_module.CompileError, match="never listed per member"):
        sql_module.compile_inputs([cohort], list(resolution.variables.values()), blobs(release))


def test_the_server_stops_reading_a_materialisation_at_the_call_s_deadline(
    city: City, doc: Doc, variables_of: Callable[..., Resolution], materialised: Callable[..., Any]
) -> None:
    release = city(
        {
            "establishments": [{"establishment_id": "e0"}],
            "inspections": [{"inspection_id": "i0", "establishment_id": "e0", "score": 4}],
        }
    )
    resolution = variables_of(
        doc([]), release, [{"column": "inspections.score", "aggregate": "mean"}]
    )
    cohorts, variables = list(resolution.cohorts.values()), list(resolution.variables.values())
    with pytest.raises(CallerDeadline):
        materialised(cohorts, variables, ends=time.monotonic() - 1)
    [((mean,), _)] = materialised(cohorts, variables, ends=time.monotonic() + 60)
    assert dict(mean.values) == {4.0: 1}


def test_max_and_min_are_picked_in_sql_and_mean_alone_is_read_unit_by_unit(
    city: City,
    doc: Doc,
    variables_of: Callable[..., Resolution],
    blobs: Callable[[Release], dict[str, Any]],
) -> None:
    release = city(
        {
            "establishments": [{"establishment_id": "e0"}],
            "inspections": [{"inspection_id": "i0", "establishment_id": "e0", "score": 4}],
        }
    )
    resolution = variables_of(
        doc([]),
        release,
        [{"column": "inspections.score", "aggregate": f} for f in ("max", "min", "mean")],
    )
    cohorts = list(resolution.cohorts.values())
    variables = [resolution.variables[key] for key in sorted(resolution.variables)]
    compiled = compile_materialised(cohorts, variables, blobs(release))
    most, least, mean = compiled.statements[:3]
    picks = ('MAX("g"."val")', 'MIN("g"."val")')
    assert [pick in most for pick in picks] == [True, False]
    assert [pick in least for pick in picks] == [False, True]
    assert [pick in mean for pick in picks] == [False, False]
    assert '"g"."rid" AS "r"' in mean
    assert '"g"."rid" AS "r"' not in most + least


@pytest.mark.parametrize("function", ["max", "min"])
def test_a_not_applicable_row_is_skipped_by_max_of_an_ordered_category_in_sql_as_in_the_evaluator(
    city: City,
    doc: Doc,
    variables_of: Callable[..., Resolution],
    materialised: Callable[..., Any],
    function: str,
) -> None:
    release = city(
        {
            "establishments": [{"establishment_id": f"e{n}"} for n in range(3)],
            "inspections": [
                {"inspection_id": "i0", "establishment_id": "e0", "rating": "high"},
                {"inspection_id": "i1", "establishment_id": "e0", "rating": "n/a"},
                {"inspection_id": "i2", "establishment_id": "e1", "rating": "n/a"},
            ],
        }
    )
    resolution = variables_of(
        doc([]), release, [{"column": "inspections.rating", "aggregate": function}]
    )
    assert resolution.refusals == []
    [(extreme,)] = _agree(resolution, materialised)
    assert dict(extreme.values) == {"high": 1}
    assert (extreme.excluded["NO_ROWS"], extreme.excluded["NO_INFORMATION"]) == (2, 0)


def _means(
    city: City,
    doc: Doc,
    variables_of: Callable[..., Resolution],
    blobs: Callable[[Release], dict[str, Any]],
) -> CompiledMaterialised:
    release = city({"establishments": [{"establishment_id": "e0"}]})
    resolution = variables_of(
        doc([]), release, [{"column": "inspections.score", "aggregate": "mean"}]
    )
    cohorts, variables = list(resolution.cohorts.values()), list(resolution.variables.values())
    return compile_materialised(cohorts, variables, blobs(release))


def test_the_server_looks_at_the_deadline_every_65536_rows_and_every_65536_units(
    city: City,
    doc: Doc,
    variables_of: Callable[..., Resolution],
    blobs: Callable[[Release], dict[str, Any]],
    packed_rows: Callable[..., Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compiled = _means(city, doc, variables_of, blobs)
    [width] = compiled.columns
    units = 70_000
    rows = [(0, unit, unit % 100, 0, 1, *[0] * (width - 5)) for unit in range(units)]
    answer = packed_rows(width, rows, compiled.values[0])
    looks: list[float] = []
    clock = iter([0.0, 0.0, 0.0, 0.0])

    def monotonic() -> float:
        looks.append(0.0)
        return next(clock)

    monkeypatch.setattr(sql_module.time, "monotonic", monotonic)
    [((mean,), _)] = compiled.read([answer], ends=1.0)
    assert len(looks) == 4
    assert sum(mean.values.values()) == units
    later = iter([0.0, 2.0])
    looks.clear()
    monkeypatch.setattr(sql_module.time, "monotonic", lambda: looks.append(0.0) or next(later))
    with pytest.raises(CallerDeadline):
        compiled.read([answer], ends=1.0)
    assert len(looks) == 2


def test_an_empty_of_negative_zero_is_zero_in_sql_as_in_the_evaluator(
    city: City, doc: Doc, variables_of: Callable[..., Resolution], materialised: Callable[..., Any]
) -> None:
    release = city(
        {
            "establishments": [{"establishment_id": "e0"}],
            "inspections": [{"inspection_id": "i0", "establishment_id": "e0"}],
            "checked_appliances": [{"inspection_id": "i0", "appliance": "fridge"}],
        }
    )
    resolution = variables_of(
        doc([]),
        release,
        [
            {
                "column": "readings.celsius",
                "aggregate": "max",
                "where": [{"kind": "value", "column": "readings.appliance", "values": ["fridge"]}],
                "empty": -0.0,
            }
        ],
    )
    assert resolution.refusals == []
    [(expected,)] = _agree(resolution, materialised)
    [((found,), _)] = materialised(
        list(resolution.cohorts.values()), list(resolution.variables.values())
    )
    for read in (expected, found):
        [value] = read.values
        assert (value, math.copysign(1.0, value), type(value)) == (0.0, 1.0, float)


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        ([7.0, 0.1, 1e-17, -0.3333333333333333, 0.1], 1.3733333333333333),
        ([-9007199254740992.0, -1e-17, -0.3333333333333333], -3002399751580331.0),
        ([2**53 + 1, 2**53 + 5, 0.5], 6004799503160664.0),
        ([5e-324, 5e-324, 0.0], 5e-324),
    ],
)
def test_the_mean_is_the_exact_mean_correctly_rounded(
    values: list[int | float], expected: float
) -> None:
    exact = float(sum(map(Fraction, values)) / len(values))
    weighted = [(value, 1) for value in values]
    assert exact == expected
    assert aggregated("mean", weighted, len(values)) == expected
    assert stats.mean(sorted(weighted)) == expected


NUMBERS = st.one_of(
    st.floats(allow_nan=False, allow_infinity=False),
    st.floats(min_value=-1e-300, max_value=1e-300),
    st.integers(min_value=-(2**70), max_value=2**70),
)
"""Doubles of every exponent, subnormal ones included, and integers beyond 2^53."""


@given(st.lists(st.tuples(NUMBERS, st.integers(min_value=1, max_value=10**9)), min_size=1))
def test_the_exact_mean_is_the_rational_mean_correctly_rounded(
    values: list[tuple[int | float, int]],
) -> None:
    """``exact_sum`` is the rational sum of the values, each as many times as it is counted, and
    ``exact_mean`` that over the count, correctly rounded (D326)."""
    total, least, count = exact_sum(values)
    expected = sum((Fraction(value) * times for value, times in values), Fraction(0))
    assert Fraction(total, 2**-least) == expected
    assert count == sum(times for _, times in values)
    try:
        rounded = float(expected / count)
    except OverflowError:
        return
    assert exact_mean(values) == rounded


def test_a_materialisation_s_overlaps_answer_is_one_row_of_counts_none_negative() -> None:
    """The units two cohorts share, read from the answer (D339): one row of one count a pair,
    and anything else, a negative count included, a fault."""
    compiled = CompiledMaterialised(
        statements=("a", "b", "c"),
        parameters={},
        marks=(),
        cohorts=2,
        shapes=(sql_module._Shape("column", "number", None, None, None),),  # pyright: ignore[reportPrivateUsage]
        shared=True,
    )

    def rows(*values: int) -> Rows:
        return Rows([memoryview(array("q", [value])) for value in values], 1)

    assert compiled.columns == (5, 5, 1)
    assert compiled.read_shared([rows(0), rows(0), rows(3)]) == (3,)
    with pytest.raises(QueryError):
        compiled.read_shared([rows(0), rows(0), rows(-1)])
    with pytest.raises(QueryError):
        compiled.read_shared([rows(0), rows(0), rows(1, 2)])
    alone = replace(compiled, cohorts=1, statements=("a",))
    assert (alone.columns, alone.read_shared([rows(0)])) == ((5,), ())


def _inputs(shape: Any) -> sql_module.CompiledInputs:
    """A listing of one cohort's inputs over a key of one integer column and one variable."""
    return sql_module.CompiledInputs(
        statements=("keys", "variable"),
        parameters={},
        marks=(),
        cohorts=1,
        shapes=(shape,),
        kinds=("int64",),
    )


def test_the_server_stops_reading_a_listing_of_inputs_at_the_call_s_deadline(
    city: City, doc: Doc, variables_of: Callable[..., Resolution], inputs_listed: Callable[..., Any]
) -> None:
    release = city(
        {
            "establishments": [{"establishment_id": "e0"}],
            "inspections": [{"inspection_id": "i0", "establishment_id": "e0", "score": 4}],
        }
    )
    resolution = variables_of(doc([]), release, [{"column": "establishments.name"}])
    cohorts, variables = list(resolution.cohorts.values()), list(resolution.variables.values())
    with pytest.raises(CallerDeadline):
        inputs_listed(cohorts, variables, ends=time.monotonic() - 1)
    [found] = inputs_listed(cohorts, variables, ends=time.monotonic() + 60)
    assert found.members == 1


@pytest.mark.parametrize(
    "given",
    [
        [(2, 5, 7, 0, 1)],
        [(2, 0, 7, 0, 1), (2, 0, 8, 0, 1)],
        [(2, 0, 7, 0, 2)],
        [(1, 0, 0, 0, 1)],
        [(9, 0, 7, 0, 1)],
        [],
    ],
)
def test_a_listing_of_inputs_that_gives_a_member_no_row_or_two_is_a_fault(
    packed_rows: Callable[..., Any], given: list[tuple[int, ...]]
) -> None:
    """A member's variable is one row, its value or its reasons (D342): a row for another unit,
    two rows, a row of two units, reasons of no bit, a tag no query gives, and no row, are
    faults."""
    compiled = _inputs(sql_module._Shape("column", "integer", None, None, None))  # pyright: ignore[reportPrivateUsage]
    keys = packed_rows(2, [(0, 11)], compiled.values[0])
    rows = packed_rows(5, given, compiled.values[1])
    with pytest.raises(QueryError):
        compiled.read([keys, rows])


def test_a_listing_of_inputs_over_its_cap_is_known_from_its_keys(
    packed_rows: Callable[..., Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sql_module, "MAX_LISTED", 1)
    compiled = _inputs(sql_module._Shape("column", "integer", None, None, None))  # pyright: ignore[reportPrivateUsage]
    keys = packed_rows(2, [(0, 11), (1, 12)], compiled.values[0])
    rows = packed_rows(5, [(2, 0, 7, 0, 1), (2, 1, 8, 0, 1)], compiled.values[1])
    with pytest.raises(sql_module.TooManyListed):
        compiled.read([keys, rows])
    monkeypatch.setattr(sql_module, "MAX_LISTED", 2)
    [found] = compiled.read([keys, rows])
    assert (list(found.rows), found.values) == ([0, 1], ((7, 8),))


def test_a_listing_of_inputs_reads_its_flag_words_and_a_word_no_flag_sets_is_a_fault(
    packed_rows: Callable[..., Any],
) -> None:
    mark = Mark(Flag.SCOPE_PARTIAL, "rel:inspections.establishment")
    compiled = replace(
        _inputs(sql_module._Shape("column", "integer", None, None, None)),  # pyright: ignore[reportPrivateUsage]
        marks=(mark,),
    )
    keys = packed_rows(2, [(0, 11)], compiled.values[0])
    [found] = compiled.read([keys, packed_rows(6, [(2, 0, 7, 0, 1, 1)], compiled.values[1])])
    assert found.marks == (frozenset({mark}),)
    for word in (-1, 2):
        rows = packed_rows(6, [(2, 0, 7, 0, 1, word)], compiled.values[1])
        with pytest.raises(QueryError):
            compiled.read([keys, rows])


def _branches(query: object) -> list[exp.Select]:
    """The selects a statement's top-level ``UNION ALL`` stacks."""
    if isinstance(query, exp.Union):
        return [*_branches(query.this), *_branches(query.expression)]
    assert isinstance(query, exp.Select)
    return [query]


def _reads(select: exp.Select) -> str:
    """The relation a select reads first, by name."""
    source = select.args["from_"].this
    assert isinstance(source, exp.Table)
    return source.name


def test_a_listing_of_inputs_reads_every_variable_from_one_join_of_its_members_to_its_units(
    city: City,
    doc: Doc,
    variables_of: Callable[..., Resolution],
    inputs_compiled: Callable[..., Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The shape that ends over any number of units (D342): every relation is materialised; the
    members of all the positions are taken once, each cohort's first ``MAX_LISTED`` + 1 and of
    them the first ``most`` + 1; and a variable's rows are stacked from one relation, the join of
    the members to their units, which no branch joins again. DuckDB planned the members joined
    inside the branches so that it did not end over 600,000 units
    (``test_every_variable_s_listing_of_inputs_ends_over_600_000_units``)."""
    release = city(
        {
            "establishments": [{"establishment_id": "e0"}],
            "inspections": [{"inspection_id": "i0", "establishment_id": "e0", "score": 4}],
        }
    )
    written = [
        {"column": "inspections.score", "aggregate": "mean"},
        {"column": "inspections.score", "aggregate": "max"},
    ]
    monkeypatch.setattr(sql_module, "MAX_INPUT_CELLS", 10)
    resolution = variables_of(doc({"a": [], "b": []}), release, written)
    cohorts, variables = list(resolution.cohorts.values()), list(resolution.variables.values())
    compiled = inputs_compiled(cohorts, variables)
    assert (compiled.most, len(compiled.statements)) == (5, 6)
    for at, statement in enumerate(compiled.statements):
        tree = sqlglot.parse_one(statement, read="duckdb")
        ctes = {cte.alias: cte.this for cte in tree.find_all(exp.CTE)}
        assert all(cte.args.get("materialized") is True for cte in tree.find_all(exp.CTE))
        [taken] = [body for body in ctes.values() if body.args.get("limit") is not None]
        assert taken.args["limit"].expression.sql() == "6"
        stacked = _branches(taken.args["from_"].this.this)
        firsts = [branch.args["from_"].this.this for branch in stacked]
        assert [first.args["limit"].expression.sql() for first in firsts] == ["6", "6"]
        if at % 3 == 0:
            continue
        branches = _branches(tree)
        [joined] = {_reads(branch) for branch in branches}
        assert all(
            table.alias != "c" for branch in branches for table in branch.find_all(exp.Table)
        )
        body = ctes[joined]
        assert body.args["from_"].this.alias == "c"
        assert [join.this.alias for join in body.args.get("joins", [])] == ["u"]


ORCHARD_UNITS = 600_000
"""Trees in the orchard the listings of inputs are timed over: from about 500,000, DuckDB never
ended a mean two to-many steps down when the members were joined inside each branch (D342)."""

FUNCTIONS = ("mean", "max", "min", "count")
SWEPT: list[tuple[str, dict[str, Any]]] = [
    (f"{column} {aggregate}{' where' if where else ''}", written)
    for column, where_column, depth in (
        ("trees.height", None, 0),
        ("harvests.kg", "harvests.grade", 1),
        ("weighings.grams", "weighings.scale", 2),
    )
    for aggregate in (("value",) if depth == 0 else (*FUNCTIONS, "some", "every"))
    for where in ((False, True) if aggregate in FUNCTIONS else (False,))
    for written in [
        {"column": column}
        if aggregate == "value"
        else {
            "column": column,
            "aggregate": aggregate,
            **({"values": [100]} if aggregate in ("some", "every") else {}),
            **(
                {"where": [{"kind": "value", "column": where_column, "values": ["a"]}]}
                if where
                else {}
            ),
        }
    ]
]


def orchard_release() -> Release:
    """Trees, their harvests and the harvests' weighings, one row of each: the descriptors a
    listing of inputs is compiled against. The weighings' coverage is undeclared, as the importer
    leaves it, so that every tree's weighings are excluded (``NO_INFORMATION``)."""
    grades = {"values": [{"value": "a"}, {"value": "b"}]}
    return build.release(
        [
            build.dataset(),
            build.table("trees", ["tree_id"]),
            build.column("trees.tree_id", "string"),
            build.column("trees.height", "integer", units="cm"),
            build.table("harvests", ["harvest_id"], role="event"),
            build.column("harvests.harvest_id", "string"),
            build.column("harvests.tree_id", "string"),
            build.column("harvests.kg", "integer", units="kg"),
            build.column("harvests.grade", "category", permissible_values=grades),
            build.relationship("harvests", ["tree_id"], "trees", role="tree"),
            build.coverage("rel:harvests.tree", "all"),
            build.table("weighings", ["weighing_id"], role="event"),
            build.column("weighings.weighing_id", "string"),
            build.column("weighings.harvest_id", "string"),
            build.column("weighings.grams", "integer", units="g"),
            build.column("weighings.scale", "category", permissible_values=grades),
            build.relationship("weighings", ["harvest_id"], "harvests", role="harvest"),
        ],
        {
            "trees": [{"tree_id": "t0", "height": 1}],
            "harvests": [{"harvest_id": "h0", "tree_id": "t0", "kg": 1, "grade": "a"}],
            "weighings": [{"weighing_id": "w0", "harvest_id": "h0", "grams": 5, "scale": "a"}],
        },
    )


@pytest.fixture(scope="module")
def orchard_blobs(tmp_path_factory: pytest.TempPathFactory) -> dict[str, TableSource]:
    """``ORCHARD_UNITS`` trees, a harvest each and a weighing each, written by DuckDB."""
    directory = tmp_path_factory.mktemp("orchard")
    units = ORCHARD_UNITS
    rows = {
        "trees": (
            f"SELECT 't' || i AS tree_id, (i % 400)::BIGINT AS height FROM range({units}) r(i)"
        ),
        "harvests": (
            f"SELECT 'h' || i AS harvest_id, 't' || i AS tree_id, (i % 90)::BIGINT AS kg, "
            f"CASE WHEN i % 2 = 0 THEN 'a' ELSE 'b' END AS grade FROM range({units}) r(i)"
        ),
        "weighings": (
            f"SELECT 'w' || i AS weighing_id, 'h' || i AS harvest_id, (100 + i % 37)::BIGINT "
            f"AS grams, CASE WHEN i % 3 = 0 THEN 'a' ELSE 'b' END AS scale FROM range({units}) r(i)"
        ),
    }
    made = duckdb.connect()
    for name, query in rows.items():
        made.execute(f"COPY ({query}) TO '{directory / name}' (FORMAT parquet)")
    return {
        name: TableSource(str(directory / name), frozenset(parquet.names(directory / name)))
        for name in rows
    }


_TIMED = """
import pickle, sys, time
from aibi.core.engine.duck import Session, Statement, run
from aibi.core.schema.limits import QueryLimits
paths, name, sql, parameters, values = pickle.loads(open(sys.argv[1], 'rb').read())
limits = QueryLimits()
session = Session(paths, limits.query_memory, limits.query_threads)
started = time.monotonic()
[found] = run(session, [Statement(sql, parameters, values)])
print(name, len(found.rows), round(time.monotonic() - started, 2), flush=True)
"""
"""Runs one statement as a query worker's session runs it (``duck.run``, at the default
``QueryLimits``: its memory, its one thread and no spilling to disk)."""

STATEMENT_SECONDS = 15
"""The time a statement of the sweep may take, killed past it: each took 1 to 2.3 s."""


def test_every_variable_s_listing_of_inputs_ends_over_600_000_units(
    doc: Doc,
    variables_of: Callable[..., Resolution],
    orchard_blobs: dict[str, TableSource],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every kind of variable a pack's analysis is handed (a column, and each aggregate and
    question zero, one and two to-many steps down, with and without a ``where``), listed over
    ``ORCHARD_UNITS`` trees, ends in a few seconds in a query worker's session: each statement
    runs in a process of its own, killed past ``STATEMENT_SECONDS``. With the members joined to
    their units inside each branch, DuckDB never ended a ``mean``, ``max``, ``min`` or ``some``
    two steps down without a ``where`` (each over 30 s, where the others took 1 to 2.2 s) (D342)."""
    monkeypatch.setattr(sql_module, "MAX_INPUT_CELLS", ORCHARD_UNITS * len(SWEPT))
    release = orchard_release()
    resolution = variables_of(doc([], unit="trees"), release, [written for _, written in SWEPT])
    compiled = sql_module.compile_inputs(
        list(resolution.cohorts.values()), list(resolution.variables.values()), orchard_blobs
    )
    names = ["keys", *(name for name, _ in SWEPT)]
    parameters = dict(compiled.parameters)
    timed: list[str] = []
    for at, (name, statement) in enumerate(zip(names, compiled.statements, strict=True)):
        given = tmp_path / f"statement{at}.pickle"
        given.write_bytes(
            pickle.dumps((compiled.paths, name, statement, parameters, compiled.values[at]))
        )
        try:
            ran = subprocess.run(
                [sys.executable, "-c", _TIMED, str(given)],
                capture_output=True,
                text=True,
                timeout=STATEMENT_SECONDS,
            )
        except subprocess.TimeoutExpired:
            timed.append(f"{name}: over {STATEMENT_SECONDS} s")
            continue
        assert ran.returncode == 0, ran.stderr
        found, rows, _ = ran.stdout.strip().splitlines()[-1].rsplit(" ", 2)
        assert (found, int(rows)) == (name, ORCHARD_UNITS)
    assert timed == []


@pytest.mark.parametrize(
    "given",
    [
        [(0, 0, 7, 0, 2), (1, 0, 0, 1, 1)],
        [(1, 0, 0, 1, 1), (0, 0, 7, 0, 2)],
        [(0, 0, 7, 0, 2), (2, 0, 7, 0, 1)],
    ],
)
def test_a_listing_of_a_mean_that_gives_a_member_pooled_rows_and_another_row_is_a_fault(
    packed_rows: Callable[..., Any], given: list[tuple[int, ...]]
) -> None:
    """A member's pooled rows by value are its whole ``mean`` (D342): with its reasons, in
    either order, or a value of its own, they are a fault."""
    compiled = _inputs(sql_module._Shape("aggregate", "integer", "mean", None, None))  # pyright: ignore[reportPrivateUsage]
    keys = packed_rows(2, [(0, 11)], compiled.values[0])
    with pytest.raises(QueryError):
        compiled.read([keys, packed_rows(5, given, compiled.values[1])])
    pooled = packed_rows(5, [(0, 0, 7, 0, 2), (0, 0, 8, 0, 1)], compiled.values[1])
    [found] = compiled.read([keys, pooled])
    assert found.values == ((22 / 3,),)

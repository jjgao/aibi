"""Variables, one value per unit (§9.2; D325, D326): each kind resolved, evaluated by the
reference evaluator and materialised by the SQL compiler, which must agree."""

import json
import math
import time
from collections.abc import Callable
from typing import Any

import pytest

from aibi.core.analyses import stats
from aibi.core.engine import sql as sql_module
from aibi.core.engine.data import Release
from aibi.core.engine.evaluate import evaluate
from aibi.core.engine.resolve import Resolution
from aibi.core.engine.sql import CompiledMaterialised, compile_materialised
from aibi.core.engine.variables import aggregated, evaluate_variable, joint, materialise
from aibi.core.engine.worker import CallerDeadline

City = Callable[..., Release]
Doc = Callable[..., dict[str, Any]]


def _agree(resolution: Resolution, materialised: Callable[..., Any]) -> list[Any]:
    cohorts = list(resolution.cohorts.values())
    variables = [resolution.variables[key] for key in sorted(resolution.variables)]
    found = materialised(cohorts, variables)
    expected: list[Any] = []
    for cohort, (read, together) in zip(cohorts, found, strict=True):
        members = evaluate(cohort).members
        values = [evaluate_variable(variable) for variable in variables]
        mine = tuple(materialise(value, members) for value in values)
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
        ({"column": "violations.code"}, "NOT_SUPPORTED", "/column"),
        ({"column": "establishments.tags"}, "NOT_SUPPORTED", "/column"),
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


def test_counts_per_category_of_a_multi_valued_column_are_not_supported_until_their_part(
    city: City, doc: Doc, variables_of: Callable[..., Resolution]
) -> None:
    resolution = variables_of(doc([]), city(), [{"column": "violations.code"}])
    [refusal] = resolution.refusals
    assert (refusal.code, refusal.path) == ("NOT_SUPPORTED", "/views/0/params/columns/0/column")
    said = json.dumps([part.model_dump() for part in refusal.message])
    assert "M3.2c" in said
    assert "M3.2b" not in said


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

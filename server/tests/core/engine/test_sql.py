"""The SQL compiler against the reference evaluator (§13.3): differential property tests on random
city releases and documents, with every coverage form (direct, grouped, scoped, covering all,
undeclared, proposed), parent scopes that are TRUE, FALSE and UNKNOWN, missing codes of every
kind, list items, null and dangling keys, numbers in other units, dates and datetimes; and the
rules the queries keep: identifiers from descriptors only, constants bound, integer aggregates
only (D291–D294)."""

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st
from sqlglot import exp, parse_one

from aibi.core.engine import build
from aibi.core.engine import sql as compiler
from aibi.core.engine.counts import count_parts
from aibi.core.engine.data import Release
from aibi.core.engine.evaluate import evaluate
from aibi.core.engine.resolved import flipped
from aibi.core.engine.sql import CompileError, Crossing, TruthValues, compile_cohort, cross
from aibi.core.schema.semantics import Flag

City = Callable[..., Release]
Doc = Callable[..., dict[str, Any]]
Runner = Callable[..., Any]
Sql = Callable[..., Any]
Canon = Callable[..., Any]
Crossed = Callable[..., Crossing]

OWNER = "rel:establishments.owner"
INSPECTED = "rel:inspections.establishment"
LICENCES = "rel:licences.establishment"
LICENCE_TYPE = "rel:licences.type"
VIOLATIONS = "rel:violations.inspection"
READINGS = "rel:readings.inspection"

EXAMPLES = settings(
    max_examples=120,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)
FEWER = settings(EXAMPLES, max_examples=40)

GROUPED = {
    "assignment": {
        "table": "inspection_checklists",
        "parent_columns": {"inspection_id": "inspection_id"},
        "group_column": "checklist",
    },
    "groups": {
        "table": "checklist_items",
        "group_column": "checklist",
        "scope_columns": {"code": "code"},
        "covers_all_column": "all_codes",
    },
}
ROUTINE = {"kind": "value", "column": "inspections.kind", "values": ["routine", "follow_up"]}
VIOLATION_COVERAGE = [
    {"parents": GROUPED, "parent_scope": ROUTINE},
    {"parents": GROUPED, "parent_scope": ROUTINE, "statuses": {"parents": "proposed"}},
    {"parents": GROUPED},
    {"parents": "all", "parent_scope": ROUTINE},
    {"parents": "all", "statuses": {"parents": "proposed"}},
    {"parent_scope": ROUTINE},
]
INSPECTION_COVERAGE = [
    {"parents": "all"},
    {"parents": "all", "statuses": {"parents": "proposed"}},
    {},
]
MOMENTS = [
    "2025-12-31T23:00:00+00:00",
    "2026-01-01T00:30:00+01:00",
    "2026-01-01T00:00:00.000001Z",
    "2025-12-31T20:00:00-05:00",
]


# --- Releases ------------------------------------------------------------------------------------


@st.composite
def city_data(draw: st.DrawFn) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    """Rows for a small city, and the coverage options of its descriptors."""
    pick = lambda options: draw(st.sampled_from(options))  # noqa: E731
    rows: dict[str, list[dict[str, Any]]] = {
        "owners": [{"owner_id": "o0", "region": "north"}, {"owner_id": "o1", "region": None}],
        "licence_types": [{"type_id": "t1", "tier": 1}, {"type_id": "t3", "tier": 3}],
        "checklist_items": [
            {"checklist": "basic", "code": "temp", "all_codes": False},
            {"checklist": "basic", "code": "pest", "all_codes": None},
            {"checklist": "temps", "code": "temp", "all_codes": False},
            {"checklist": "all", "code": None, "all_codes": True},
        ],
    }
    for name in ("establishments", "inspections", "inspection_checklists", "violations"):
        rows[name] = []
    for name in ("complaints", "staff", "licences", "readings", "checked_appliances"):
        rows[name] = []
    for index in range(draw(st.integers(0, 4))):
        place = f"e{index}"
        rows["establishments"].append(
            {
                "establishment_id": place,
                "grade": pick(["A", "B", "C", "D", "pending", "exempt", None]),
                "cuisine": pick(["thai", "pizza", None]),
                "seats": pick([None, 0, 10, 11, 40, 2**60]),
                "frontage": pick([None, 12.5, 0.03048, 0.030480000000000004, -0.0]),
                "revenue": pick([None, 0.1, 1e300, -2.5]),
                "opened": pick([None, "2020-02-29", "2021-03-01"]),
                "last_seen": pick([None, *MOMENTS]),
                "chain": pick([None, True, False]),
                "name": pick([None, "A", "", "a'b"]),
                "tags": pick(
                    [None, "?", [], ["vegan"], ["vegan", "halal"], ["vegan", None], ["halal", "?"]]
                ),
                "owner_id": pick([None, "o0", "o1", "gone"]),
            }
        )
        for _ in range(draw(st.integers(0, 3))):
            inspection = f"i{len(rows['inspections'])}"
            rows["inspections"].append(
                {
                    "inspection_id": inspection,
                    "establishment_id": pick([place, place, place, None, "gone"]),
                    "kind": pick(["routine", "follow_up", "courtesy", None]),
                    "score": pick([None, 40, 90]),
                }
            )
            checklist = pick([None, "basic", "temps", "all", "missing"])
            if checklist is not None:
                rows["inspection_checklists"].append(
                    {"inspection_id": inspection, "checklist": checklist}
                )
            for _ in range(draw(st.integers(0, 2))):
                rows["violations"].append(
                    {
                        "violation_id": f"v{len(rows['violations'])}",
                        "inspection_id": pick([inspection, inspection, None]),
                        "code": pick(["temp", "pest", "label", None]),
                        "severity": pick([None, 1, 5, "pending", "n/a"]),
                    }
                )
            for appliance in draw(st.sets(st.sampled_from(["fridge", "freezer"]))):
                rows["checked_appliances"].append(
                    {"inspection_id": inspection, "appliance": appliance}
                )
            if draw(st.booleans()):
                rows["checked_appliances"].append({"inspection_id": inspection, "appliance": None})
            for _ in range(draw(st.integers(0, 2))):
                rows["readings"].append(
                    {
                        "reading_id": f"r{len(rows['readings'])}",
                        "inspection_id": inspection,
                        "appliance": pick(["fridge", "freezer", None]),
                        "celsius": pick([None, 2.0, 9.5]),
                    }
                )
        for _ in range(draw(st.integers(0, 2))):
            rows["complaints"].append(
                {
                    "complaint_id": f"c{len(rows['complaints'])}",
                    "establishment_id": place,
                    "channel": pick(["phone", "web", "letter", None]),
                    "severity": pick([None, 1, 4]),
                }
            )
        for _ in range(draw(st.integers(0, 2))):
            rows["staff"].append(
                {
                    "staff_id": f"s{len(rows['staff'])}",
                    "establishment_id": place,
                    "certified": pick([True, False, None]),
                }
            )
        for _ in range(draw(st.integers(0, 2))):
            rows["licences"].append(
                {
                    "licence_id": f"l{len(rows['licences'])}",
                    "establishment_id": place,
                    "type_id": pick(["t1", "t3", None, "gone"]),
                }
            )
    options = {
        "violations": pick(VIOLATION_COVERAGE),
        "inspections": pick(INSPECTION_COVERAGE),
    }
    return rows, options


# --- Clauses -------------------------------------------------------------------------------------


def _value(column: str, **predicate: Any) -> dict[str, Any]:
    return {"kind": "value", "column": column, **predicate}


def _subset(draw: st.DrawFn, options: list[Any]) -> list[Any]:
    return draw(st.lists(st.sampled_from(options), min_size=1, max_size=len(options), unique=True))


@st.composite
def leaves(draw: st.DrawFn, scoped: bool) -> dict[str, Any]:
    """A leaf a document on establishments may hold, never refused; ``scoped`` when the
    violations' coverage has scope columns."""
    pick = lambda options: draw(st.sampled_from(options))  # noqa: E731
    lift = pick([{}, {"lift": "strict"}, {"lift": "assessed"}])
    negate = {"negate": True} if draw(st.booleans()) else {}
    bound = pick(["gt", "gte", "lt", "lte"])
    choice = draw(st.integers(0, 25))
    if choice == 0:
        return _value("establishments.grade", values=_subset(draw, ["A", "B", "C"]), **negate)
    if choice == 1:
        return _value("establishments.grade", range={bound: pick(["A", "B", "C"])}, **negate)
    if choice == 2:
        op = pick(["=", "!=", ">", "<=", "<", ">="])
        return _value("establishments.seats", op=op, value=pick([0, 10, 40, str(2**60)]))
    if choice == 3:
        # Seats in per cent: the constants become doubles, some not whole, for an integer column.
        given = pick(
            [{"values": [pick([1000, 1050, 4000])]}, {"range": {bound: pick([1050, 999])}}]
        )
        return _value("establishments.seats", units="%", **given, **negate)
    if choice == 4:
        units = pick([{}, {"units": "cm"}, {"units": "[ft_i]"}])
        given = pick([{"values": [pick([0.1, 1250, 12.5])]}, {"range": {bound: pick([0.1, 1250])}}])
        return _value("establishments.frontage", **units, **given, **negate)
    if choice == 5:
        return _value("establishments.revenue", range={bound: pick([0, 0.1, -2.5])}, **negate)
    if choice == 6:
        return _value(
            "establishments.opened", range={bound: pick(["2021-01-01", "2020-02-29"])}, **negate
        )
    if choice == 7:
        given = pick([{"values": [pick(MOMENTS)]}, {"range": {bound: pick(MOMENTS)}}])
        return _value("establishments.last_seen", **given, **negate)
    if choice == 8:
        return _value("establishments.chain", values=[draw(st.booleans())], **negate)
    if choice == 9:
        return _value("establishments.name", values=_subset(draw, ["A", "", "a'b"]), **negate)
    if choice == 10:
        match = pick([{}, {"match": "any"}, {"match": "all"}])
        return _value(
            "establishments.tags", values=_subset(draw, ["vegan", "halal"]), **match, **negate
        )
    if choice == 11:
        return _value("owners.region", values=["north"], **negate)
    if choice == 12:
        keys = draw(st.sets(st.integers(0, 4), min_size=1))
        return {"kind": "ids", "ids": [f"d:e{index}" for index in keys]}
    if choice == 13:
        where = draw(
            st.lists(
                st.sampled_from(
                    [
                        _value("inspections.kind", values=["routine"]),
                        _value("inspections.kind", values=["courtesy"], negate=True),
                        _value("inspections.score", range={"gte": 50}),
                    ]
                ),
                max_size=2,
            )
        )
        quantifier = pick([{}, {"quantifier": "every"}, {"min_count": 2}])
        return {"kind": "exists", "table": "inspections", "where": where, **quantifier}
    if choice == 14:
        inner: list[Any] = [_value("violations.severity", range={"gte": 3})]
        if scoped and draw(st.booleans()):
            inner.append(_value("violations.code", values=_subset(draw, ["temp", "pest"])))
        outer: list[Any] = [{"kind": "exists", "table": "violations", "where": inner}]
        if draw(st.booleans()):
            outer.append(_value("inspections.kind", values=["routine"]))
        quantifier = pick([{}, {"min_count": 2}])
        return {"kind": "exists", "table": "inspections", "where": outer, **lift, **quantifier}
    if choice == 15:
        quantifier = pick([{}, {"quantifier": "some"}, {"quantifier": ["every", "some"]}])
        return _value(
            "violations.code",
            values=_subset(draw, ["temp", "pest", "label"]),
            **quantifier,
            **lift,
        )
    if choice == 16:
        quantifier = pick([{}, {"quantifier": "every"}, {"quantifier": ["some", "every"]}])
        return _value("violations.severity", range={"lt": 3}, **quantifier, **lift)
    if choice == 17:
        where: list[Any] = [_value("complaints.severity", range={"gte": 3})]
        quantifier = pick([{}, {"quantifier": "every"}])
        if not quantifier and draw(st.booleans()):
            where.append(_value("complaints.channel", values=_subset(draw, ["phone", "web"])))
        return {"kind": "exists", "table": "complaints", "where": where, **quantifier}
    if choice == 18:
        quantifier = pick([{}, {"quantifier": "every"}, {"min_count": 2}])
        return {
            "kind": "exists",
            "table": "staff",
            "where": [_value("staff.certified", values=[True])],
            **quantifier,
        }
    if choice == 19:
        return _value("licence_types.tier", values=[pick([1, 3])])
    if choice == 20:
        quantifier = pick([{}, {"quantifier": "every"}, {"min_count": 2}])
        return {
            "kind": "exists",
            "table": "establishments",
            "via": [{"rel": OWNER, "dir": "up"}, {"rel": OWNER, "dir": "down"}],
            "exclude_self": True,
            "where": draw(
                st.lists(
                    st.sampled_from([_value("establishments.seats", range={"gt": 5})]), max_size=1
                )
            ),
            **quantifier,
        }
    if choice == 21:
        others = {
            "kind": "exists",
            "table": "inspections",
            "via": [{"rel": INSPECTED, "dir": "up"}, {"rel": INSPECTED, "dir": "down"}],
            "exclude_self": True,
            "where": [_value("inspections.score", range={"gte": 50})],
        }
        return {"kind": "exists", "table": "inspections", "where": [others], **lift}
    if choice == 22:
        return {
            "kind": "exists",
            "table": "licence_types",
            "via": [{"rel": LICENCES, "dir": "down"}, {"rel": LICENCE_TYPE, "dir": "up"}],
            "where": [_value("licence_types.tier", values=[pick([1, 3])])],
        }
    if choice == 23:
        where = [_value("readings.celsius", range={"gt": 8})]
        if draw(st.booleans()):
            where.append(_value("readings.appliance", values=_subset(draw, ["fridge", "freezer"])))
        return {"kind": "exists", "table": "readings", "where": where}
    if choice == 24:
        scope = {"scope": {"appliance": _subset(draw, ["fridge", "freezer"])}}
        return {"kind": "covered", "table": "readings", **pick([{}, scope]), **lift}
    scope = {"scope": {"code": _subset(draw, ["temp", "pest"])}} if scoped else {}
    return {"kind": "covered", "table": "violations", **pick([{}, scope]), **lift}


def clauses(scoped: bool, size: int = 6) -> st.SearchStrategy[Any]:
    return st.recursive(
        leaves(scoped),
        lambda inner: st.one_of(
            st.lists(inner, min_size=0, max_size=3).map(lambda members: {"all": members}),
            st.lists(inner, min_size=0, max_size=3).map(lambda members: {"any": members}),
            inner.map(lambda member: {"not": member}),
            inner.map(lambda member: {"known": member}),
            inner.map(lambda member: {"unknown": member}),
        ),
        max_leaves=size,
    )


def _resolved(run: Runner, doc: Doc, release: Release, written: list[Any] | dict[str, Any]) -> Any:
    result = run(doc(written), release)
    limits = {refusal.limit.name for refusal in result.resolution.refusals if refusal.limit}
    assume(not limits & {"clause_depth", "leaves_per_cohort"})
    assert result.refusals == [], result.refusals
    return result


def _agrees(found: Any, expected: Any) -> None:
    assert found.values == expected.values
    accounting = found.accounting
    assert (
        accounting.n_true,
        accounting.n_false,
        accounting.n_unknown,
        dict(accounting.unknown_by_reason),
        accounting.unknown_by_clause,
        accounting.lift_differs,
        accounting.marks,
    ) == (
        expected.n_true,
        expected.n_false,
        expected.n_unknown,
        dict(expected.unknown_by_reason),
        expected.unknown_by_clause,
        expected.lift_differs,
        expected.marks,
    )


# --- Differential properties ----------------------------------------------------------------------


@EXAMPLES
@given(data=city_data(), extra=st.data())
def test_the_compiler_gives_every_unit_the_evaluators_truth_value_reasons_and_flags(
    run: Runner, city: City, doc: Doc, sql: Sql, data: Any, extra: st.DataObject
) -> None:
    rows, options = data
    release = city(rows, **options)
    scoped = options["violations"].get("parents") is GROUPED
    written = extra.draw(st.lists(clauses(scoped), min_size=1, max_size=3))
    result = _resolved(run, doc, release, written)
    for name, cohort in result.resolution.cohorts.items():
        _agrees(sql(cohort), result.results[name])


@FEWER
@given(data=city_data(), extra=st.data())
def test_the_compiler_agrees_with_flags_spread_over_many_words(
    run: Runner, city: City, doc: Doc, sql: Sql, data: Any, extra: st.DataObject
) -> None:
    """With one flag to a word, every combination of words is exercised."""
    rows, options = data
    release = city(rows, **options)
    scoped = options["violations"].get("parents") is GROUPED
    written = extra.draw(st.lists(clauses(scoped, 4), min_size=1, max_size=2))
    result = _resolved(run, doc, release, written)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(compiler, "MARK_BITS", 1)
        for name, cohort in result.resolution.cohorts.items():
            _agrees(sql(cohort), result.results[name])


@FEWER
@given(data=city_data(), extra=st.data())
def test_count_digests_from_the_compiler_equal_the_evaluators(
    canon: Canon, city: City, doc: Doc, sql: Sql, data: Any, extra: st.DataObject
) -> None:
    rows, options = data
    release = city(rows, **options)
    scoped = options["violations"].get("parents") is GROUPED
    written = extra.draw(st.lists(clauses(scoped, 4), min_size=1, max_size=2))
    result = canon(doc(written), release)
    limits = {refusal.limit.name for refusal in result.refusals if refusal.limit}
    assume(not limits & {"clause_depth", "leaves_per_cohort"})
    assert result.refusals == []
    cohort = result.cohorts["c"]
    expected = count_parts(cohort, evaluate(cohort.resolved))
    assert count_parts(cohort, sql(cohort.resolved).accounting) == expected


@FEWER
@given(data=city_data(), extra=st.data())
def test_a_crossing_by_the_compiler_counts_what_the_evaluator_s_truth_values_give(
    run: Runner, city: City, doc: Doc, crossed: Crossed, data: Any, extra: st.DataObject
) -> None:
    """Cohorts crossed with predicates (D318): each predicate's split of each cohort's units, by
    reason, with its lift and flags, the units for which no predicate is known by reason, and
    the units two cohorts share, counted in SQL as ``cross`` counts them from each unit's truth
    value; with one flag to a word, so that every word is read."""
    rows, options = data
    release = city(rows, **options)
    scoped = options["violations"].get("parents") is GROUPED
    written = {
        f"c{index}": extra.draw(st.lists(clauses(scoped, 4), min_size=0, max_size=2))
        for index in range(extra.draw(st.integers(min_value=1, max_value=3)))
    }
    written |= {
        f"p{index}": [extra.draw(clauses(scoped, 4))]
        for index in range(extra.draw(st.integers(min_value=1, max_value=3)))
    }
    result = _resolved(run, doc, release, written)
    cohorts = [result.resolution.cohorts[name] for name in written if name.startswith("c")]
    predicates = [result.resolution.cohorts[name] for name in written if name.startswith("p")]
    values = {
        name: TruthValues.of(result.results[name].values) for name in result.resolution.cohorts
    }
    lifted = [evaluated_lift(predicate) for predicate in predicates]
    expected = cross(
        [values[c.name] for c in cohorts],
        [values[p.name] for p in predicates],
        [None if other is None else TruthValues.of(other) for other in lifted],
    )
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(compiler, "MARK_BITS", 1)
        assert crossed(cohorts, predicates) == expected


def test_a_crossing_counts_a_lift_s_one_changed_unit_and_no_unknown_unit_as_shared(
    run: Runner, city: City, doc: Doc, crossed: Crossed
) -> None:
    """Fixed cases of the property above (D318): a predicate whose other lift rule changes one
    unit (UNKNOWN under ``strict``, an unassessed inspection; FALSE under ``assessed``), and two
    cohorts whose one such unit is TRUE in the first and UNKNOWN in the second, which they do
    not share."""
    pest = _value("violations.code", values=["pest"])
    rows: dict[str, list[dict[str, Any]]] = {
        "establishments": [{"establishment_id": f"e{index}"} for index in range(3)],
        "inspections": [
            {"inspection_id": "i0", "establishment_id": "e0", "kind": "routine"},
            {"inspection_id": "i1", "establishment_id": "e0", "kind": "routine"},
            {"inspection_id": "i2", "establishment_id": "e1", "kind": "routine"},
        ],
        "inspection_checklists": [
            {"inspection_id": "i0", "checklist": "basic"},
            {"inspection_id": "i2", "checklist": "basic"},
        ],
        "checklist_items": [{"checklist": "basic", "code": "pest", "all_codes": False}],
    }
    result = run(doc({"every": [], "pest": [pest], "asked": [pest]}), city(rows))
    assert result.refusals == []
    cohorts = [result.resolution.cohorts[name] for name in ("every", "pest")]
    predicate = result.resolution.cohorts["asked"]
    found = crossed(cohorts, [predicate])
    assert found.shared == (0,)
    assert [split.lift_differs for split in found.cohorts[0].splits] == [1]
    other = evaluated_lift(predicate)
    assert other is not None
    assert found == cross(
        [TruthValues.of(result.results[c.name].values) for c in cohorts],
        [TruthValues.of(result.results["asked"].values)],
        [TruthValues.of(other)],
    )


def evaluated_lift(cohort: Any) -> Any:
    """A cohort's truth values under the other lift rule, or ``None`` when it holds no lift."""
    other = tuple(flipped(clause) for clause in cohort.clauses)
    if other == cohort.clauses:
        return None
    return evaluate(replace(cohort, clauses=other)).values


# --- Exactness ------------------------------------------------------------------------------------


def _shelves(boxes: list[dict[str, Any]], shelves: list[dict[str, Any]]) -> Release:
    """Boxes on shelves, the box's shelf a number and the shelf's id an integer."""
    return build.release(
        [
            build.dataset(),
            build.table("shelves", ["shelf_id"]),
            build.column("shelves.shelf_id", "integer"),
            build.column("shelves.aisle", "category"),
            build.table("boxes", ["box_id"]),
            build.column("boxes.box_id", "string"),
            build.column("boxes.shelf_no", "number"),
            build.column("boxes.label", "string"),
            build.relationship("boxes", ["shelf_no"], "shelves", ["shelf_id"], role="shelf"),
            build.coverage("rel:boxes.shelf", "all"),
        ],
        {"shelves": shelves, "boxes": boxes},
    )


def test_keys_of_an_integer_and_a_double_column_match_by_value_exactly(
    run: Runner, sql: Sql
) -> None:
    release = _shelves(
        [
            {"box_id": "b0", "shelf_no": 1.0},
            {"box_id": "b1", "shelf_no": 2.5},
            {"box_id": "b2", "shelf_no": float(2**53)},
            {"box_id": "b3", "shelf_no": None},
        ],
        [
            {"shelf_id": 1, "aisle": "east"},
            {"shelf_id": 2**53 + 1, "aisle": "east"},
            {"shelf_id": 2**53, "aisle": "west"},
        ],
    )
    written = {
        "aibi": "1",
        "dataset": "d",
        "unit": "boxes",
        "cohorts": {"c": {"all": [_value("shelves.aisle", values=["east"])]}},
    }
    result = run(written, release)
    assert [value.value.value for value in result.result.values] == [
        "TRUE",
        "UNKNOWN",
        "FALSE",
        "UNKNOWN",
    ]
    _agrees(sql(result.resolution.cohorts["c"]), result.result)


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ({"range": {"gt": 1050}}, ["FALSE", "TRUE"]),  # seats above 10.5
        ({"range": {"gte": 1050}}, ["FALSE", "TRUE"]),
        ({"range": {"lt": 1050}}, ["TRUE", "FALSE"]),
        ({"range": {"lte": 1000}}, ["TRUE", "FALSE"]),
        ({"values": [1050]}, ["FALSE", "FALSE"]),  # 10.5 seats is no whole number
        ({"values": [1100]}, ["FALSE", "TRUE"]),
    ],
)
def test_an_integer_column_is_compared_exactly_with_a_bound_converted_to_a_double(
    run: Runner, city: City, doc: Doc, sql: Sql, given: dict[str, Any], expected: list[str]
) -> None:
    release = city(
        {
            "establishments": [
                {"establishment_id": "e0", "seats": 10},
                {"establishment_id": "e1", "seats": 11},
            ]
        }
    )
    result = run(doc([_value("establishments.seats", units="%", **given)]), release)
    assert [value.value.value for value in result.result.values] == expected
    _agrees(sql(result.resolution.cohorts["c"]), result.result)


def test_datetimes_are_compared_as_instants_to_the_microsecond(
    run: Runner, city: City, doc: Doc, sql: Sql
) -> None:
    moments = [
        datetime(2026, 1, 1, tzinfo=UTC) + timedelta(microseconds=delta) for delta in (-1, 0, 1)
    ]
    rows = [
        {
            "establishment_id": f"e{index}",
            "last_seen": moment.astimezone(timezone(timedelta(hours=-3))),
        }
        for index, moment in enumerate(moments)
    ]
    release = city({"establishments": rows})
    result = run(
        doc([_value("establishments.last_seen", range={"gte": "2026-01-01T01:00:00+01:00"})]),
        release,
    )
    assert [value.value.value for value in result.result.values] == ["FALSE", "TRUE", "TRUE"]
    _agrees(sql(result.resolution.cohorts["c"]), result.result)


# --- The queries' rules ---------------------------------------------------------------------------

_EVERYTHING = [
    _value("establishments.grade", range={"gte": "B"}, negate=True),
    _value("establishments.tags", values=["vegan"], match="all"),
    _value("owners.region", values=["north"]),
    {"kind": "ids", "ids": ["d:e0", "d:e1"]},
    {
        "kind": "exists",
        "table": "inspections",
        "where": [
            {
                "kind": "exists",
                "table": "violations",
                "where": [_value("violations.code", values=["pest"])],
            }
        ],
        "lift": "assessed",
    },
    {
        "kind": "exists",
        "table": "complaints",
        "where": [_value("complaints.channel", values=["phone"])],
    },
    {"kind": "covered", "table": "violations", "scope": {"code": ["temp"]}},
    _value("establishments.last_seen", range={"lt": "2026-01-01T00:00:00Z"}),
    _value("establishments.name", values=["a secret name"]),
]
_STATES = {"PRESENT", "NOT_APPLICABLE", "NOT_ASSESSED"}
_AGGREGATES = {"COUNT", "BITWISE_OR_AGG", "LOGICAL_OR", "MIN", "MAX"}
"""As SQLGlot names them: COUNT, BIT_OR, BOOL_OR, MIN and MAX."""


def _compiled(run: Runner, city: City, doc: Doc, blobs: Callable[..., Any]) -> Any:
    release = city()
    result = run(doc(_EVERYTHING), release)
    assert result.refusals == []
    return compile_cohort(result.resolution.cohorts["c"], blobs(release))


def test_no_constant_of_a_document_or_descriptor_is_written_into_the_sql(
    run: Runner, city: City, doc: Doc, blobs: Callable[..., Any]
) -> None:
    compiled = _compiled(run, city, doc, blobs)
    for query in (compiled.counts_sql, compiled.values_sql):
        literals = {
            node.this
            for node in parse_one(query, dialect="duckdb").find_all(exp.Literal)
            if node.is_string
        }
        assert literals <= _STATES
        for constant in ("north", "vegan", "pest", "phone", "temp", "a secret name", "e0"):
            assert constant not in query
    bound = [
        item for value in compiled.parameters.values() if isinstance(value, tuple) for item in value
    ]
    assert "a secret name" in bound


def test_the_sql_names_descriptor_columns_only_where_it_reads_a_blob(
    run: Runner, city: City, doc: Doc, blobs: Callable[..., Any]
) -> None:
    compiled = _compiled(run, city, doc, blobs)
    tree = parse_one(compiled.counts_sql, dialect="duckdb")
    release = city()
    names = {column for table in release.table_ids for column in release.columns(table)}
    for column in tree.find_all(exp.Column):
        if column.name in names or column.name.removesuffix("__state") in names:
            select = column.find_ancestor(exp.Select)
            assert select is not None
            [source] = [table.this for table in select.find_all(exp.Table) if table.this]
            assert source.sql(dialect="duckdb").startswith("READ_PARQUET(")
            assert column.table == "s"
    assert {parameter.name for parameter in tree.find_all(exp.Placeholder)} == set(
        compiled.parameters
    )


def test_the_counts_come_from_integer_aggregates_alone(
    run: Runner, city: City, doc: Doc, blobs: Callable[..., Any]
) -> None:
    """§9.3: no aggregate over a double; MIN and MAX read truth codes and row numbers."""
    compiled = _compiled(run, city, doc, blobs)
    tree = parse_one(compiled.counts_sql, dialect="duckdb")
    for node in tree.find_all(exp.AggFunc):
        assert node.sql_name() in _AGGREGATES, node.sql_name()
        if isinstance(node, exp.Min | exp.Max):
            assert isinstance(node.this, exp.Column)
            assert node.this.name in {"v", "rid"}


def _odd_names(columns: list[str]) -> Release:
    return build.release(
        [
            build.dataset(),
            build.table("things", ["rid"]),
            *(build.column(f"things.{name}", "integer") for name in columns),
        ],
        {
            "things": [
                {name: index * 10 + at for at, name in enumerate(columns)} for index in range(3)
            ]
        },
    )


def test_descriptor_names_that_are_the_compilers_own_do_not_collide(run: Runner, sql: Sql) -> None:
    columns = ["rid", "v", "r", "m0", "c0", "target", "aid", "s"]
    release = _odd_names(columns)
    written = {
        "aibi": "1",
        "dataset": "d",
        "unit": "things",
        "cohorts": {
            "c": {"all": [_value(f"things.{name}", range={"gte": 11}) for name in columns]}
        },
    }
    result = run(written, release)
    assert result.refusals == []
    _agrees(sql(result.resolution.cohorts["c"]), result.result)


def test_a_table_with_a_column_named_as_duckdbs_row_numbers_is_refused(
    unchecked: Runner, blobs: Callable[..., Any]
) -> None:
    release = _odd_names(["rid", "file_row_number"])
    written = {
        "aibi": "1",
        "dataset": "d",
        "unit": "things",
        "cohorts": {"c": {"all": [_value("things.rid", values=[1])]}},
    }
    result = unchecked(written, release)
    with pytest.raises(CompileError, match="file_row_number"):
        compile_cohort(result.resolution.cohorts["c"], blobs(release))


def test_a_table_without_its_blob_is_refused(
    run: Runner, city: City, doc: Doc, blobs: Callable[..., Any]
) -> None:
    release = city()
    result = run(doc([_value("owners.region", values=["north"])]), release)
    sources = {table: source for table, source in blobs(release).items() if table != "owners"}
    with pytest.raises(CompileError, match="owners"):
        compile_cohort(result.resolution.cohorts["c"], sources)


def test_the_flags_of_the_sql_name_their_relationships(
    run: Runner, city: City, doc: Doc, sql: Sql
) -> None:
    release = city(
        {
            "establishments": [{"establishment_id": "e0"}],
            "inspections": [{"inspection_id": "i0", "establishment_id": "e0", "kind": "routine"}],
            "inspection_checklists": [{"inspection_id": "i0", "checklist": "basic"}],
            "checklist_items": [{"checklist": "basic", "code": "temp", "all_codes": False}],
        },
        violations={"parents": GROUPED, "statuses": {"parents": "proposed"}},
    )
    none = {"not": {"kind": "exists", "table": "violations", "where": []}}
    result = run(doc([none]), release)
    found = sql(result.resolution.cohorts["c"])
    assert {(mark.flag, mark.relationship) for mark in found.accounting.marks} == {
        (Flag.SCOPE_PARTIAL, VIOLATIONS),
        (Flag.COVERAGE_PROPOSED, VIOLATIONS),
    }


def test_parameters_are_recorded_with_blobs_as_their_digests(
    run: Runner, city: City, doc: Doc, blobs: Callable[..., Any]
) -> None:
    compiled = _compiled(run, city, doc, blobs)
    recorded = compiled.parameters_json()
    digests = {Path(source.path).name for source in blobs(city()).values()}
    assert {recorded[name] for name in compiled.blobs} <= digests
    assert not any(isinstance(value, str) and "/" in value for value in recorded.values())


# --- Cases the random tests reach rarely ----------------------------------------------------------


def test_an_integer_a_double_cannot_hold_never_matches_a_double_scope_value(
    run: Runner, sql: Sql
) -> None:
    """``9007199254740993`` is no double: a coverage listing the double 2^53 does not close the
    parent for it (NOT_COVERED), though the double nearest to it is 2^53."""
    release = build.release(
        [
            build.dataset(),
            build.table("parcels", ["parcel_id"]),
            build.column("parcels.parcel_id", "string"),
            build.table("tags", ["tag_id"]),
            build.column("tags.tag_id", "string"),
            build.column("tags.parcel_id", "string"),
            build.column("tags.serial", "integer"),
            build.relationship("tags", ["parcel_id"], "parcels", role="parcel"),
            build.table("surveyed", ["parcel_id", "serial"], role="coverage"),
            build.column("surveyed.parcel_id", "string"),
            build.column("surveyed.serial", "number"),
            build.coverage(
                "rel:tags.parcel",
                {
                    "table": "surveyed",
                    "parent_columns": {"parcel_id": "parcel_id"},
                    "scope_columns": {"serial": "serial"},
                },
            ),
        ],
        {
            "parcels": [{"parcel_id": "p0"}],
            "tags": [],
            "surveyed": [{"parcel_id": "p0", "serial": float(2**53)}],
        },
    )
    written = {
        "aibi": "1",
        "dataset": "d",
        "unit": "parcels",
        "cohorts": {
            "c": {
                "all": [
                    {
                        "kind": "exists",
                        "table": "tags",
                        "where": [_value("tags.serial", values=[str(2**53 + 1)])],
                    }
                ]
            }
        },
    }
    result = run(written, release)
    [value] = result.result.values
    assert (value.value.value, {reason.value for reason in value.reasons}) == (
        "UNKNOWN",
        {"NOT_COVERED"},
    )
    _agrees(sql(result.resolution.cohorts["c"]), result.result)


def test_covered_under_assessed_is_false_only_when_every_kept_child_is_false(
    run: Runner, city: City, doc: Doc, sql: Sql
) -> None:
    """One inspection not covered (FALSE) and one of a missing kind (UNKNOWN, as its parent
    scope is) leave the establishment UNKNOWN."""
    release = city(
        {
            "establishments": [{"establishment_id": "e0"}],
            "inspections": [
                {"inspection_id": "i0", "establishment_id": "e0", "kind": "routine"},
                {"inspection_id": "i1", "establishment_id": "e0", "kind": None},
            ],
        }
    )
    result = run(doc([{"kind": "covered", "table": "violations", "lift": "assessed"}]), release)
    [value] = result.result.values
    assert (value.value.value, {reason.value for reason in value.reasons}) == (
        "UNKNOWN",
        {"NO_INFORMATION"},
    )
    _agrees(sql(result.resolution.cohorts["c"]), result.result)


def test_a_listing_whose_scope_cell_is_missing_does_not_close_its_parent_for_every(
    run: Runner, city: City, doc: Doc, sql: Sql
) -> None:
    """A checked appliance that is not named lists no whole scope tuple, so an inspection it
    alone lists is not closed: ``every`` over its no readings is UNKNOWN for that as well as for
    there being none. The gate refuses such a cell; a release in memory can hold one."""
    release = city(
        {
            "establishments": [{"establishment_id": "e0"}],
            "inspections": [
                {"inspection_id": "i0", "establishment_id": "e0", "kind": "routine"},
                {"inspection_id": "i1", "establishment_id": "e0", "kind": "routine"},
            ],
            "checked_appliances": [
                {"inspection_id": "i0", "appliance": None},
                {"inspection_id": "i1", "appliance": "fridge"},
            ],
        }
    )
    every = {
        "kind": "exists",
        "table": "readings",
        "quantifier": "every",
        "where": [_value("readings.celsius", range={"gt": 8})],
    }
    result = run(doc([every], unit="inspections"), release)
    assert [
        (value.value.value, sorted(reason.value for reason in value.reasons))
        for value in result.result.values
    ] == [("UNKNOWN", ["NOT_COVERED", "NO_ROWS"]), ("UNKNOWN", ["NO_ROWS"])]
    _agrees(sql(result.resolution.cohorts["c"]), result.result)


def test_a_sibling_question_from_a_row_without_a_parent_is_unknown_for_no_parent(
    run: Runner, city: City, doc: Doc, sql: Sql
) -> None:
    release = city(
        {
            "establishments": [
                {"establishment_id": "e0", "owner_id": "o0", "seats": 10},
                {"establishment_id": "e1", "owner_id": None, "seats": 10},
                {"establishment_id": "e2", "owner_id": "gone", "seats": 10},
                {"establishment_id": "e3", "owner_id": "o0", "seats": 3},
            ],
            "owners": [{"owner_id": "o0"}],
        }
    )
    siblings = {
        "kind": "exists",
        "table": "establishments",
        "via": [{"rel": OWNER, "dir": "up"}, {"rel": OWNER, "dir": "down"}],
        "exclude_self": True,
        "where": [_value("establishments.seats", range={"gt": 5})],
    }
    result = run(doc([siblings]), release)
    assert [
        (value.value.value, sorted(reason.value for reason in value.reasons))
        for value in result.result.values
    ] == [
        ("FALSE", []),
        ("UNKNOWN", ["NO_PARENT"]),
        ("UNKNOWN", ["NO_PARENT"]),
        ("TRUE", []),
    ]
    _agrees(sql(result.resolution.cohorts["c"]), result.result)


def test_a_sibling_question_through_two_relationships_takes_away_a_row_s_part_only_below_its_parent(
    run: Runner, city: City, doc: Doc, sql: Sql
) -> None:
    """Up one relationship and down another: a row is a child of another parent than the one it
    reaches, so its own part is taken away only where it is a child of that parent (D291)."""
    operator = "rel:establishments.operator"
    extra = [
        build.column("establishments.operator_id", "string"),
        build.relationship(
            "establishments", ["operator_id"], "owners", ["owner_id"], role="operator"
        ),
        build.coverage(operator, "all"),
    ]
    release = city(
        {
            "establishments": [
                {"establishment_id": "e0", "owner_id": "o0", "operator_id": "o1", "seats": 10},
                {"establishment_id": "e1", "owner_id": "o1", "operator_id": "o0", "seats": 10},
            ],
            "owners": [{"owner_id": "o0"}, {"owner_id": "o1"}],
        },
        extra=extra,
    )
    siblings = {
        "kind": "exists",
        "table": "establishments",
        "via": [{"rel": OWNER, "dir": "up"}, {"rel": operator, "dir": "down"}],
        "exclude_self": True,
        "where": [_value("establishments.seats", range={"gt": 5})],
    }
    result = run(doc([siblings]), release)
    assert [value.value.value for value in result.result.values] == ["TRUE", "TRUE"]
    _agrees(sql(result.resolution.cohorts["c"]), result.result)


def test_a_sibling_question_counts_each_parents_children_once(
    run: Runner, city: City, doc: Doc, blobs: Callable[..., Any], sql: Sql
) -> None:
    """``exclude_self`` subtracts a row's own part from its parent's counts rather than joining
    every row with its siblings: the query joins no relation to itself on the parent."""
    rows = [
        {"establishment_id": f"e{index}", "owner_id": "o0", "seats": index % 7}
        for index in range(60)
    ]
    release = city({"establishments": rows, "owners": [{"owner_id": "o0"}]})
    siblings = {
        "kind": "exists",
        "table": "establishments",
        "via": [{"rel": OWNER, "dir": "up"}, {"rel": OWNER, "dir": "down"}],
        "exclude_self": True,
        "where": [_value("establishments.seats", range={"gt": 5})],
        "min_count": 8,
    }
    result = run(doc([siblings]), release)
    assert {value.value.value for value in result.result.values} == {"TRUE", "FALSE"}
    compiled = compile_cohort(result.resolution.cohorts["c"], blobs(release))
    tree = parse_one(compiled.counts_sql, dialect="duckdb")
    for join in tree.find_all(exp.Join):
        on = join.args.get("on")
        if on is None:
            continue
        for equal in on.find_all(exp.EQ):
            names = {side.name for side in (equal.this, equal.expression)}
            assert names != {"target"}, equal.sql(dialect="duckdb")
    _agrees(sql(result.resolution.cohorts["c"]), result.result)

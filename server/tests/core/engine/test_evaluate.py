"""Evaluation, row by row (§6.2–§6.6): value predicates, lookups, existence questions in every
coverage form, covered, flags and the accounting, on the city fixture."""

from collections.abc import Callable
from dataclasses import replace
from typing import Any

import pytest

from aibi.core.engine.data import Cell, Release
from aibi.core.engine.evaluate import evaluate
from aibi.core.engine.resolved import RValue, Values
from aibi.core.schema.semantics import Flag, ObservationState, Reason

Answer = tuple[str, frozenset[str], frozenset[str]]
City = Callable[..., Release]
Doc = Callable[..., dict[str, Any]]
Runner = Callable[..., Any]

INSPECTED = "rel:inspections.establishment"
VIOLATIONS = "rel:violations.inspection"
READINGS = "rel:readings.inspection"


def true(*flags: str) -> Answer:
    return ("TRUE", frozenset(), frozenset(flags))


def false(*flags: str) -> Answer:
    return ("FALSE", frozenset(), frozenset(flags))


def unknown(*reasons: str, flags: tuple[str, ...] = ()) -> Answer:
    return ("UNKNOWN", frozenset(reasons), frozenset(flags))


def value(column: str, **predicate: Any) -> dict[str, Any]:
    return {"kind": "value", "column": column, **predicate}


def exists(table: str, *where: Any, **members: Any) -> dict[str, Any]:
    return {"kind": "exists", "table": table, "where": list(where), **members}


def answers(
    run: Runner, doc: Doc, release: Release, clause: Any, unit: str = "establishments"
) -> list[Answer]:
    """The answer for each row of the unit table, flags without their relationships."""
    result = run(doc([clause], unit=unit), release)
    assert result.refusals == []
    return [
        (
            item.value.value,
            frozenset(reason.value for reason in item.reasons),
            frozenset(mark.flag.value for mark in item.marks),
        )
        for item in result.result.values
    ]


def places(*rows: dict[str, Any]) -> list[dict[str, Any]]:
    """Establishments e0, e1, … with these members."""
    return [{"establishment_id": f"e{index}", **row} for index, row in enumerate(rows)]


# --- Value predicates by cell state (§6.4) --------------------------------------------------------


GRADES = places({"grade": "A"}, {"grade": "pending"}, {"grade": "exempt"}, {})


@pytest.mark.parametrize(
    ("predicate", "expected"),
    [
        ({"values": ["A"]}, [true(), unknown("NOT_ASSESSED"), false(), unknown("NO_INFORMATION")]),
        (
            {"op": "!=", "value": "A"},
            [false(), unknown("NOT_ASSESSED"), true(), unknown("NO_INFORMATION")],
        ),
        (
            {"range": {"lte": "B"}},
            [false(), unknown("NOT_ASSESSED"), false(), unknown("NO_INFORMATION")],
        ),
        (
            {"range": {"gt": "B"}},
            [true(), unknown("NOT_ASSESSED"), false(), unknown("NO_INFORMATION")],
        ),
    ],
)
def test_a_value_predicate_by_state(
    run: Runner, city: City, doc: Doc, predicate: dict[str, Any], expected: list[Answer]
) -> None:
    """NOT_APPLICABLE is FALSE and its negation TRUE, while a range and the opposite range are
    both FALSE; NOT_ASSESSED and UNKNOWN stay UNKNOWN with their reasons."""
    release = city({"establishments": GRADES})
    assert answers(run, doc, release, value("establishments.grade", **predicate)) == expected


def test_an_ordered_category_compares_by_its_listed_order(
    run: Runner, city: City, doc: Doc
) -> None:
    rows = places({"grade": "C"}, {"grade": "B"}, {"grade": "A"}, {"grade": "D"})
    release = city({"establishments": rows})
    between = value("establishments.grade", range={"gt": "C", "lte": "A"})
    assert answers(run, doc, release, between) == [
        false(),
        true(),
        true(),
        unknown("NO_INFORMATION"),
    ]
    # A value outside the listed ones has no place in their order, but is simply not a member.
    listed = value("establishments.grade", values=["A", "B"])
    assert answers(run, doc, release, listed) == [false(), true(), true(), false()]


def test_numbers_convert_to_the_columns_units_by_one_multiplication_of_doubles(
    run: Runner, city: City, doc: Doc
) -> None:
    rows = places({"frontage": 12.5}, {"frontage": 0.03048}, {"frontage": 0.030480000000000004})
    release = city({"establishments": rows})
    assert answers(
        run, doc, release, value("establishments.frontage", range={"gte": 1250}, units="cm")
    ) == [true(), false(), false()]
    # 0.1 ft is 0.03048 m exactly, but 0.1 * 0.3048 in doubles is 0.030480000000000004, which
    # SQL computes too.
    feet = value("establishments.frontage", values=[0.1], units="[ft_i]")
    assert answers(run, doc, release, feet) == [false(), false(), true()]


def test_integers_beyond_two_to_the_53_are_written_as_strings(
    run: Runner, city: City, doc: Doc
) -> None:
    release = city({"establishments": places({"seats": 2**60}, {"seats": 2**60 + 1})})
    exact = value("establishments.seats", values=[str(2**60)])
    assert answers(run, doc, release, exact) == [true(), false()]


def test_dates_and_datetimes_compare_as_values_datetimes_in_utc(
    run: Runner, city: City, doc: Doc
) -> None:
    rows = places(
        {"opened": "2020-02-29", "last_seen": "2026-01-01T00:30:00+01:00"},
        {"opened": "2021-03-01", "last_seen": "2025-12-31T23:30:00-01:00"},
    )
    release = city({"establishments": rows})
    opened = value("establishments.opened", range={"lt": "2021-01-01"})
    assert answers(run, doc, release, opened) == [true(), false()]
    seen = value("establishments.last_seen", op="<", value="2026-01-01T00:00:00Z")
    assert answers(run, doc, release, seen) == [true(), false()]


def test_booleans_and_strings(run: Runner, city: City, doc: Doc) -> None:
    release = city({"establishments": places({"chain": True, "name": "A"}, {"chain": False})})
    assert answers(run, doc, release, value("establishments.chain", values=[False])) == [
        false(),
        true(),
    ]
    assert answers(run, doc, release, value("establishments.name", values=["A"])) == [
        true(),
        unknown("NO_INFORMATION"),
    ]


def test_a_boolean_is_never_equal_to_a_number(run: Runner, city: City, doc: Doc) -> None:
    """Equality is as JSON values have it, also in a tree built in code, where a constant need
    not be of its column's type."""
    release = city({"establishments": places({"chain": True}, {"chain": False})})
    result = run(doc([value("establishments.chain", values=[True])]), release)
    cohort = result.resolution.cohorts["c"]
    numbers = replace(cohort, clauses=(RValue("establishments.chain", Values((1, 0))),))
    counted = evaluate(numbers)
    assert (counted.n_true, counted.n_false) == (0, 2)


# --- Lists (§6.4) ---------------------------------------------------------------------------------


LISTS = places(
    {"tags": ["vegan", "halal"]},
    {"tags": []},
    {"tags": ["vegan", None]},
    {"tags": "?"},
    {"tags": Cell(ObservationState.NOT_APPLICABLE)},
    {"tags": ["vegan", "?"]},
)


@pytest.mark.parametrize(
    ("predicate", "expected"),
    [
        (
            {"values": ["vegan"]},
            [true(), false(), true(), unknown("NOT_ASSESSED"), false(), true()],
        ),
        (
            {"values": ["vegan"], "match": "all"},
            [
                false(),
                unknown("NO_ROWS"),
                unknown("NO_INFORMATION"),
                unknown("NOT_ASSESSED"),
                false(),
                unknown("NOT_ASSESSED"),
            ],
        ),
        (
            # Some tag that is not vegan; a list that is not PRESENT keeps its base result.
            {"values": ["vegan"], "negate": True},
            [
                true(),
                false(),
                unknown("NO_INFORMATION"),
                unknown("NOT_ASSESSED"),
                false(),
                unknown("NOT_ASSESSED"),
            ],
        ),
        (
            {"values": ["vegan"], "negate": True, "match": "all"},
            [false(), unknown("NO_ROWS"), false(), unknown("NOT_ASSESSED"), false(), false()],
        ),
    ],
)
def test_a_list_is_evaluated_item_by_item(
    run: Runner, city: City, doc: Doc, predicate: dict[str, Any], expected: list[Answer]
) -> None:
    release = city({"establishments": LISTS})
    assert answers(run, doc, release, value("establishments.tags", **predicate)) == expected


def test_a_not_around_a_list_leaf_negates_the_quantified_answer(
    run: Runner, city: City, doc: Doc
) -> None:
    release = city({"establishments": LISTS})
    no_vegan = {"not": value("establishments.tags", values=["vegan"])}
    assert answers(run, doc, release, no_vegan) == [
        false(),
        true(),
        false(),
        unknown("NOT_ASSESSED"),
        true(),
        false(),
    ]


# --- Lookups and ids (§6.1, §7.2) -----------------------------------------------------------------


def test_a_lookup_through_a_null_or_dangling_key_has_no_parent(
    run: Runner, city: City, doc: Doc
) -> None:
    release = city(
        {
            "owners": [{"owner_id": "o1", "region": "north"}],
            "establishments": places({"owner_id": "o1"}, {"owner_id": None}, {"owner_id": "gone"}),
        }
    )
    region = value("owners.region", values=["north"])
    assert answers(run, doc, release, region) == [
        true(),
        unknown("NO_PARENT"),
        unknown("NO_PARENT"),
    ]


def test_ids_select_units_by_key(run: Runner, city: City, doc: Doc) -> None:
    release = city({"establishments": places({}, {}, {})})
    chosen = {"kind": "ids", "ids": ["d:e0", {"dataset": "d", "key": ["e2"]}]}
    assert answers(run, doc, release, chosen) == [true(), false(), true()]


# --- Existence over coverage all (§6.5) -----------------------------------------------------------


def _inspected(*groups: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Establishment e<i> with the inspections in group i, numbered in order."""
    inspections: list[dict[str, Any]] = []
    for index, group in enumerate(groups):
        for row in group:
            number = len(inspections)
            inspections.append(
                {"inspection_id": f"i{number}", "establishment_id": f"e{index}", **row}
            )
    return {"establishments": places(*({} for _ in groups)), "inspections": inspections}


def test_some_and_every_over_closed_parents(run: Runner, city: City, doc: Doc) -> None:
    release = city(
        _inspected(
            [{"kind": "routine", "score": 90}, {"kind": "courtesy", "score": 85}],
            [{"kind": "courtesy", "score": 90}, {"kind": "routine", "score": 70}],
            [],
            [{"kind": "routine", "score": 90}, {"score": None}],
        )
    )
    routine = exists("inspections", value("inspections.kind", values=["routine"]))
    assert answers(run, doc, release, routine) == [true(), true(), false(), true()]
    passing = exists(
        "inspections", value("inspections.score", range={"gte": 80}), quantifier="every"
    )
    assert answers(run, doc, release, passing) == [
        true(),
        false(),
        unknown("NO_ROWS"),
        unknown("NO_INFORMATION"),
    ]
    courtesy = exists("inspections", value("inspections.kind", values=["courtesy"]))
    assert answers(run, doc, release, {"not": courtesy}) == [
        false(),
        false(),
        true(),
        unknown("NO_INFORMATION"),
    ]


def test_min_count(run: Runner, city: City, doc: Doc) -> None:
    release = city(
        _inspected(
            [{"kind": "routine"}, {"kind": "routine"}],
            [{"kind": "routine"}, {}],
            [{"kind": "routine"}, {"kind": "courtesy"}],
        )
    )
    twice = exists("inspections", value("inspections.kind", values=["routine"]), min_count=2)
    assert answers(run, doc, release, twice) == [true(), unknown("NO_INFORMATION"), false()]


def test_undeclared_coverage_never_closes(run: Runner, city: City, doc: Doc) -> None:
    """Staff have no coverage: a match is still evidence, and every is never TRUE."""
    release = city(
        {
            "establishments": places({}, {}, {}),
            "staff": [
                {"staff_id": "s0", "establishment_id": "e0", "certified": True},
                {"staff_id": "s1", "establishment_id": "e1", "certified": False},
            ],
        }
    )
    certified = value("staff.certified", values=[True])
    assert answers(run, doc, release, exists("staff", certified)) == [
        true(),
        unknown("NO_INFORMATION"),
        unknown("NO_INFORMATION"),
    ]
    assert answers(run, doc, release, exists("staff", certified, quantifier="every")) == [
        unknown("NO_INFORMATION"),
        false(),
        unknown("NO_ROWS", "NO_INFORMATION"),
    ]


def test_exclude_self_leaves_out_the_row_the_path_started_from(
    run: Runner, city: City, doc: Doc
) -> None:
    release = city(_inspected([{"score": 40}, {"score": 90}], [{"score": 30}]))
    others = {
        "kind": "exists",
        "table": "inspections",
        "via": [{"rel": INSPECTED, "dir": "up"}, {"rel": INSPECTED, "dir": "down"}],
        "exclude_self": True,
        "where": [value("inspections.score", range={"lt": 50})],
    }
    assert answers(run, doc, release, others, unit="inspections") == [false(), true(), false()]


# --- Record filters (§6.5 step 1) -----------------------------------------------------------------


def _complaints(*channels_and_severities: tuple[str | None, int | None]) -> dict[str, Any]:
    """One establishment per complaint."""
    rows = [
        {"complaint_id": f"c{i}", "establishment_id": f"e{i}", "channel": channel, "severity": sev}
        for i, (channel, sev) in enumerate(channels_and_severities)
    ]
    return {"establishments": places(*({} for _ in rows)), "complaints": rows}


def test_a_record_filter_decides_a_child_only_where_the_question_does_alone(
    run: Runner, city: City, doc: Doc
) -> None:
    release = city(_complaints(("phone", 4), ("letter", 4), (None, 4), (None, 1), ("letter", 1)))
    severe = value("complaints.severity", range={"gte": 3})
    assert answers(run, doc, release, exists("complaints", severe)) == [
        true(),
        false(),
        unknown("NO_INFORMATION"),  # it satisfies the question, but may not be recorded
        false(),
        false(),
    ]
    assert answers(run, doc, release, exists("complaints", severe, quantifier="every")) == [
        true(),
        true(),  # a letter is not among the rows recorded
        true(),
        unknown("NO_INFORMATION"),  # it fails the question, but may not be recorded
        true(),
    ]


# --- Parent scopes and grouped coverage with scope columns (§6.5 steps 2 and 4) -------------------


def _checked(
    *inspections: dict[str, Any],
    checklists: dict[str, list[str]] | None = None,
    every: frozenset[str] = frozenset(),
    violations: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Inspections i0, i1, … of one establishment, with checklists by name and the codes they
    cover, and violations."""
    checklists = checklists if checklists is not None else {"basic": ["temp", "pest"]}
    rows = [
        {"inspection_id": f"i{i}", "establishment_id": "e0", **row}
        for i, row in enumerate(inspections)
    ]
    assigned = [
        {"inspection_id": row["inspection_id"], "checklist": row.pop("checklist")}
        for row in rows
        if "checklist" in row
    ]
    items = [
        {"checklist": name, "code": code, "all_codes": name in every}
        for name, codes in checklists.items()
        for code in codes
    ]
    return {
        "establishments": places({}),
        "inspections": rows,
        "inspection_checklists": assigned,
        "checklist_items": items,
        "violations": [
            {"violation_id": f"v{i}", **violation} for i, violation in enumerate(violations or [])
        ],
    }


def test_a_parent_scope_and_a_checklist_decide_what_was_assessed(
    run: Runner, city: City, doc: Doc
) -> None:
    release = city(
        _checked(
            {"kind": "courtesy", "checklist": "basic"},
            {"kind": "routine", "checklist": "basic"},
            {"kind": "routine", "checklist": "temps"},
            {"kind": "routine"},
            {"checklist": "basic"},
            {"kind": "routine", "checklist": "basic"},
            checklists={"basic": ["temp", "pest"], "temps": ["temp"]},
            violations=[{"inspection_id": "i5", "code": "pest"}],
        )
    )
    pest = exists("violations", value("violations.code", values=["pest"]))
    assert answers(run, doc, release, pest, unit="inspections") == [
        unknown("OUT_OF_SCOPE"),
        false(),  # assessed for pest, and none found
        unknown("NOT_COVERED"),  # its checklist does not cover pest
        unknown("NOT_COVERED"),  # no checklist
        false(),  # a missing kind counts as in scope; the checklist closes it
        true(),
    ]


def test_an_unknown_parent_scope_is_never_closed_by_coverage_all(
    run: Runner, city: City, doc: Doc
) -> None:
    scope = {"kind": "value", "column": "inspections.kind", "values": ["routine", "follow_up"]}
    release = city(
        _checked({"kind": "routine"}, {}, {"kind": "courtesy"}),
        violations={"parents": "all", "parent_scope": scope},
    )
    pest = exists("violations", value("violations.code", values=["pest"]))
    assert answers(run, doc, release, pest, unit="inspections") == [
        false(),
        unknown("NO_INFORMATION"),
        unknown("OUT_OF_SCOPE"),
    ]


def test_scope_columns_restrict_what_a_false_or_a_true_covers(
    run: Runner, city: City, doc: Doc
) -> None:
    release = city(
        _checked(
            {"kind": "routine", "checklist": "basic"},
            {"kind": "routine", "checklist": "full"},
            {"kind": "routine", "checklist": "temps"},
            checklists={"basic": ["temp", "pest"], "full": ["temp"], "temps": ["temp"]},
            every=frozenset({"full"}),
        )
    )
    unit = "inspections"
    # The scope column pinned: closed for the codes the checklist lists, and nothing partial.
    both = exists("violations", value("violations.code", values=["temp", "pest"]))
    assert answers(run, doc, release, both, unit=unit) == [false(), false(), unknown("NOT_COVERED")]
    # Not mentioned: a FALSE covers only the listed codes, unless a group covers every code.
    severe = exists("violations", value("violations.severity", range={"gte": 3}))
    assert answers(run, doc, release, severe, unit=unit) == [
        false("SCOPE_PARTIAL"),
        false(),
        false("SCOPE_PARTIAL"),
    ]
    # every may not mention scope columns; its TRUE covers only the listed codes.
    mild = exists("violations", value("violations.severity", range={"lt": 3}), quantifier="every")
    assert answers(run, doc, release, mild, unit=unit) == [
        unknown("NO_ROWS"),
        unknown("NO_ROWS"),
        unknown("NO_ROWS"),
    ]


def test_every_with_scope_columns_is_true_only_for_the_listed_tuples(
    run: Runner, city: City, doc: Doc
) -> None:
    release = city(
        _checked(
            {"kind": "routine", "checklist": "basic"},
            {"kind": "routine", "checklist": "full"},
            {"kind": "routine"},
            checklists={"basic": ["temp"], "full": ["temp"]},
            every=frozenset({"full"}),
            violations=[
                {"inspection_id": "i0", "code": "temp", "severity": 1},
                {"inspection_id": "i1", "code": "temp", "severity": 1},
                {"inspection_id": "i2", "code": "temp", "severity": 1},
            ],
        )
    )
    mild = exists("violations", value("violations.severity", range={"lt": 3}), quantifier="every")
    assert answers(run, doc, release, mild, unit="inspections") == [
        true("SCOPE_PARTIAL"),
        true(),
        unknown("NOT_COVERED"),
    ]


def test_direct_coverage_with_scope_columns(run: Runner, city: City, doc: Doc) -> None:
    release = city(
        {
            "establishments": places({}),
            "inspections": [
                {"inspection_id": f"i{i}", "establishment_id": "e0", "kind": "routine"}
                for i in range(3)
            ],
            "checked_appliances": [
                {"inspection_id": "i0", "appliance": "fridge"},
                {"inspection_id": "i1", "appliance": "fridge"},
            ],
            "readings": [
                {"reading_id": "r0", "inspection_id": "i0", "appliance": "fridge", "celsius": 9},
                {"reading_id": "r1", "inspection_id": "i1", "appliance": "fridge", "celsius": 4},
            ],
        }
    )
    warm_fridge = exists(
        "readings",
        value("readings.appliance", values=["fridge"]),
        value("readings.celsius", range={"gt": 8}),
    )
    assert answers(run, doc, release, warm_fridge, unit="inspections") == [
        true(),
        false(),
        unknown("NOT_COVERED"),
    ]
    warm = exists("readings", value("readings.celsius", range={"gt": 8}))
    assert answers(run, doc, release, warm, unit="inspections") == [
        true(),
        false("SCOPE_PARTIAL"),  # a direct table never lists every appliance
        unknown("NOT_COVERED"),
    ]


def _appliances(*checked: list[str]) -> dict[str, Any]:
    """Inspections i0, i1, … of one establishment, each checked for the appliances given, and
    with no readings."""
    return {
        "establishments": places({}),
        "inspections": [
            {"inspection_id": f"i{i}", "establishment_id": "e0"} for i in range(len(checked))
        ],
        "checked_appliances": [
            {"inspection_id": f"i{i}", "appliance": appliance}
            for i, appliances in enumerate(checked)
            for appliance in appliances
        ],
    }


@pytest.mark.parametrize(
    ("admitted", "expected"),
    [
        # Values admitted by several conjuncts are those all of them admit.
        (
            [["fridge", "oven"], ["fridge"]],
            [false(), false(), false(), unknown("NOT_COVERED")],
        ),
        # Every combination admitted must be listed, not just one of them.
        (
            [["fridge", "oven"]],
            [unknown("NOT_COVERED"), unknown("NOT_COVERED"), false(), unknown("NOT_COVERED")],
        ),
        # With no value admitted there is no combination to list: every inspection is closed.
        ([["fridge"], ["oven"]], [false(), false(), false(), false()]),
    ],
)
def test_closedness_needs_every_combination_the_where_admits_listed(
    run: Runner, city: City, doc: Doc, admitted: list[list[str]], expected: list[Answer]
) -> None:
    release = city(_appliances(["fridge"], ["fridge", "freezer"], ["fridge", "oven"], []))
    question = exists(
        "readings", *(value("readings.appliance", values=values) for values in admitted)
    )
    assert answers(run, doc, release, question, unit="inspections") == expected


def test_a_group_lists_a_parent_only_if_the_groups_table_has_it(
    run: Runner, city: City, doc: Doc
) -> None:
    """i1's checklist is missing from the checklists (D206), so i1 is not covered."""
    release = city(
        _checked(
            {"checklist": "basic"},
            {"checklist": "gone"},
            checklists={"basic": ["temp"]},
        ),
        violations={
            "parents": {
                "assignment": {
                    "table": "inspection_checklists",
                    "parent_columns": {"inspection_id": "inspection_id"},
                    "group_column": "checklist",
                },
                "groups": {"table": "checklist_items", "group_column": "checklist"},
            }
        },
    )
    assert answers(run, doc, release, exists("violations"), unit="inspections") == [
        false(),
        unknown("NOT_COVERED"),
    ]


# --- Flags (§6.5 step 7) --------------------------------------------------------------------------


def test_a_proposed_coverage_flags_the_answers_that_rely_on_it(
    run: Runner, city: City, doc: Doc
) -> None:
    release = city(
        _inspected([{"kind": "routine"}], [{"kind": "courtesy"}], []),
        inspections={"parents": "all", "statuses": {"parents": "proposed"}},
    )
    routine = exists("inspections", value("inspections.kind", values=["routine"]))
    assert answers(run, doc, release, routine) == [
        true(),  # evidence needs no coverage
        false("COVERAGE_PROPOSED"),
        false("COVERAGE_PROPOSED"),
    ]
    result = run(doc([routine]), release)
    [mark] = {mark for value in result.result.values for mark in value.marks}
    assert (mark.flag, mark.relationship) == (Flag.COVERAGE_PROPOSED, INSPECTED)
    assert result.result.flags == {Flag.COVERAGE_PROPOSED}
    # A proposed parents is reported through the flag, not as unconfirmed semantics.
    cohort = result.resolution.cohorts["c"]
    assert all(read.pointer != "/fields/parents" for read in cohort.unconfirmed)


def test_flags_are_carried_through_nested_questions(run: Runner, city: City, doc: Doc) -> None:
    rows = _checked({"kind": "routine", "checklist": "basic"})
    release = city(
        rows,
        violations={
            "parents": {
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
            },
            "statuses": {"parents": "proposed"},
        },
    )
    pest = value("violations.code", values=["pest"])
    result = run(doc([pest]), release)
    [answer] = result.result.values
    assert answer.value.value == "FALSE"
    assert {(m.flag, m.relationship) for m in answer.marks} == {
        (Flag.COVERAGE_PROPOSED, VIOLATIONS)
    }


# --- covered (§6.5) -------------------------------------------------------------------------------


def test_covered_in_each_of_its_cases(run: Runner, city: City, doc: Doc) -> None:
    release = city(
        _checked(
            {"kind": "courtesy", "checklist": "basic"},
            {"kind": "routine", "checklist": "basic"},
            {"kind": "routine", "checklist": "temps"},
            {"kind": "routine"},
            {"kind": "routine", "checklist": "full"},
            checklists={"basic": ["temp", "pest"], "temps": ["temp"], "full": ["temp"]},
            every=frozenset({"full"}),
        )
    )
    unit = "inspections"
    pest = {"kind": "covered", "table": "violations", "scope": {"code": ["pest"]}}
    assert answers(run, doc, release, pest, unit=unit) == [
        unknown("OUT_OF_SCOPE"),
        true(),
        false(),
        false(),
        true(),  # a group covering every code covers pest
    ]
    anything = {"kind": "covered", "table": "violations"}
    assert answers(run, doc, release, anything, unit=unit) == [
        unknown("OUT_OF_SCOPE"),
        true("SCOPE_PARTIAL"),
        true("SCOPE_PARTIAL"),
        false(),
        true(),
    ]
    staff = {"kind": "covered", "table": "staff"}
    assert answers(run, doc, city({"establishments": places({})}), staff) == [
        unknown("NO_INFORMATION")
    ]
    scope = {"kind": "value", "column": "inspections.kind", "values": ["routine", "follow_up"]}
    unknown_scope = city(
        _checked({"kind": "routine"}, {}),
        violations={"parents": "all", "parent_scope": scope},
    )
    assert answers(run, doc, unknown_scope, anything, unit=unit) == [
        true(),
        unknown("NO_INFORMATION"),
    ]


def test_covered_over_two_down_steps_under_each_lift(run: Runner, city: City, doc: Doc) -> None:
    """From an establishment: strict needs every remaining inspection covered, assessed some."""
    rows = _inspected(
        [{"kind": "routine"}, {"kind": "routine"}],
        [{"kind": "routine"}, {"kind": "courtesy"}],
        [{"kind": "routine"}],
        [],
        [{"kind": "courtesy"}],
    )
    rows["inspection_checklists"] = [
        {"inspection_id": "i0", "checklist": "basic"},
        {"inspection_id": "i2", "checklist": "basic"},
    ]
    rows["checklist_items"] = [{"checklist": "basic", "code": "pest", "all_codes": False}]
    release = city(rows)
    for lift, expected in (
        ("strict", [false(), true(), false(), unknown("NOT_COVERED"), unknown("NOT_COVERED")]),
        ("assessed", [true(), true(), false(), unknown("NOT_COVERED"), unknown("NOT_COVERED")]),
    ):
        covered = {
            "kind": "covered",
            "table": "violations",
            "scope": {"code": ["pest"]},
            "lift": lift,
        }
        assert answers(run, doc, release, covered) == expected, lift


# --- known and unknown, and the accounting (§6.3, §6.6) -------------------------------------------


def test_known_and_unknown_include_unknowns_deliberately(run: Runner, city: City, doc: Doc) -> None:
    release = city({"establishments": GRADES})
    grade = value("establishments.grade", values=["A"])
    assert answers(run, doc, release, {"known": grade}) == [true(), false(), true(), false()]
    assert answers(run, doc, release, {"unknown": grade}) == [false(), true(), false(), true()]


def test_the_accounting(run: Runner, city: City, doc: Doc) -> None:
    rows = places(
        {"grade": "A", "cuisine": "thai"},
        {"grade": "pending", "cuisine": "thai"},
        {"grade": "A"},
        {"grade": "B", "cuisine": "pizza"},
        {"cuisine": "thai"},
    )
    release = city({"establishments": rows})
    written = doc(
        [
            value("establishments.grade", values=["A"]),
            value("establishments.cuisine", values=["thai"]),
        ]
    )
    result = run(written, release).result
    assert (result.n_true, result.n_false, result.n_unknown) == (1, 1, 3)
    assert result.members == (0,)
    assert result.unknown_by_reason == {
        **dict.fromkeys(Reason, 0),
        Reason.NOT_ASSESSED: 1,
        Reason.NO_INFORMATION: 2,
    }
    assert result.unknown_by_clause == (2, 1)
    assert result.lift_differs == 0
    assert result.flags == frozenset()


def test_lift_differs_counts_the_units_the_other_lift_would_change(
    run: Runner, city: City, doc: Doc
) -> None:
    rows = _inspected([{"kind": "routine"}, {"kind": "routine"}], [{"kind": "routine"}])
    rows["inspection_checklists"] = [
        {"inspection_id": "i0", "checklist": "basic"},
        {"inspection_id": "i2", "checklist": "basic"},
    ]
    rows["checklist_items"] = [{"checklist": "basic", "code": "pest", "all_codes": False}]
    release = city(rows)
    pest = value("violations.code", values=["pest"])
    result = run(doc([pest]), release).result
    # e0 has an unassessed inspection: UNKNOWN under strict, FALSE under assessed.
    assert [value.value.value for value in result.values] == ["UNKNOWN", "FALSE"]
    assert result.lift_differs == 1

"""The consequences of §6.5 and the scenarios of §13.4, each as a test, on the city fixture.

The spec states them over its own example domain, which ``test_biomedical.py`` runs as data.
Here an establishment's inspections are assessed for the violation codes their checklist covers,
a courtesy visit is outside the violations relationship's parent scope, and a licence looks up
its type's tier.
"""

from collections.abc import Callable
from typing import Any

import pytest

from aibi.core.engine.data import Release
from aibi.core.schema.semantics import Flag

City = Callable[..., Release]
Doc = Callable[..., dict[str, Any]]
Runner = Callable[..., Any]

VIOLATIONS = "rel:violations.inspection"


def value(column: str, **predicate: Any) -> dict[str, Any]:
    return {"kind": "value", "column": column, **predicate}


PEST = value("violations.code", values=["pest"])
TEMP = value("violations.code", values=["temp"])
ROUTINE = value("inspections.kind", values=["routine"])


def exists(table: str, *where: Any, **members: Any) -> dict[str, Any]:
    return {"kind": "exists", "table": table, "where": list(where), **members}


def summary(result: Any) -> list[str]:
    """Each unit's answer: its value, and its reasons when UNKNOWN."""
    return [
        item.value.value
        if not item.is_unknown
        else "UNKNOWN(" + ",".join(sorted(reason.value for reason in item.reasons)) + ")"
        for item in result.values
    ]


def ask(
    run: Runner, doc: Doc, release: Release, clause: Any, unit: str = "establishments"
) -> list[str]:
    result = run(doc([clause], unit=unit), release)
    assert result.refusals == []
    return summary(result.result)


class Places:
    """Establishments, their inspections (with a checklist, or none) and violations."""

    def __init__(self) -> None:
        self.rows: dict[str, list[dict[str, Any]]] = {
            name: []
            for name in (
                "establishments",
                "inspections",
                "inspection_checklists",
                "checklist_items",
                "violations",
                "complaints",
                "licences",
                "licence_types",
                "owners",
                "staff",
            )
        }
        self.rows["checklist_items"] = [
            {"checklist": "all", "code": code, "all_codes": True} for code in ("temp", "pest")
        ]
        self.rows["checklist_items"] += [
            {"checklist": "pests", "code": "pest", "all_codes": False},
            {"checklist": "temps", "code": "temp", "all_codes": False},
        ]

    def place(self, *inspections: dict[str, Any], owner: str | None = None) -> None:
        """An establishment with these inspections: each a kind (or none), a checklist (or
        none, unassessed) and the codes of its violations."""
        name = f"e{len(self.rows['establishments'])}"
        self.rows["establishments"].append({"establishment_id": name, "owner_id": owner})
        for given in inspections:
            inspection = f"i{len(self.rows['inspections'])}"
            row: dict[str, Any] = {"inspection_id": inspection, "establishment_id": name}
            if "kind" in given:
                row["kind"] = given["kind"]
            self.rows["inspections"].append(row)
            if given.get("checklist"):
                self.rows["inspection_checklists"].append(
                    {"inspection_id": inspection, "checklist": given["checklist"]}
                )
            for code in given.get("found", ()):
                self.rows["violations"].append(
                    {
                        "violation_id": f"v{len(self.rows['violations'])}",
                        "inspection_id": inspection,
                        "code": code,
                    }
                )

    def release(self, city: City, **options: Any) -> Release:
        return city(self.rows, **options)


def clean(kind: str | None = "routine") -> dict[str, Any]:
    """An inspection assessed for every code, that found nothing."""
    given: dict[str, Any] = {"checklist": "all"}
    if kind is not None:
        given["kind"] = kind
    return given


def unassessed(kind: str = "routine") -> dict[str, Any]:
    return {"kind": kind}


def courtesy() -> dict[str, Any]:
    return {"kind": "courtesy", "checklist": "all"}


# --- §6.5, consequence 1 --------------------------------------------------------------------------


def test_no_pest_row_is_true_only_where_pests_were_assessed(
    run: Runner, city: City, doc: Doc
) -> None:
    """`not exists violations where code = pest`, for an inspection, is TRUE only if the
    inspection is assessed for pests and has no pest row."""
    places = Places()
    places.place(
        clean(),
        {"kind": "routine", "checklist": "temps"},
        unassessed(),
        {"kind": "routine", "checklist": "pests", "found": ["pest"]},
        {"kind": "routine", "checklist": "pests", "found": ["temp"]},
    )
    no_pests = {"not": exists("violations", PEST)}
    assert ask(run, doc, places.release(city), no_pests, unit="inspections") == [
        "TRUE",
        "UNKNOWN(NOT_COVERED)",
        "UNKNOWN(NOT_COVERED)",
        "FALSE",
        "TRUE",  # a temp row found where pests were assessed
    ]


def test_codes_in_any_are_refused_and_a_list_of_codes_accepted(
    run: Runner, city: City, doc: Doc
) -> None:
    either = exists("violations", {"any": [PEST, TEMP]})
    result = run(doc([either], unit="inspections"), city())
    assert {code for code, _ in result.refusals} == {"SCOPE_COLUMN_MENTION"}
    listed = exists("violations", value("violations.code", values=["pest", "temp"]))
    assert run(doc([listed], unit="inspections"), city()).refusals == []


# --- §6.5, consequence 2 --------------------------------------------------------------------------


def test_a_missing_severity_leaves_a_question_and_its_negation_unknown(
    run: Runner, city: City, doc: Doc
) -> None:
    release = city(
        {
            "establishments": [{"establishment_id": "e0"}],
            "complaints": [{"complaint_id": "c0", "establishment_id": "e0", "channel": "phone"}],
        }
    )
    severe = exists("complaints", value("complaints.severity", range={"gte": 3}))
    assert ask(run, doc, release, severe) == ["UNKNOWN(NO_INFORMATION)"]
    assert ask(run, doc, release, {"not": severe}) == ["UNKNOWN(NO_INFORMATION)"]


# --- §6.5, consequence 3 --------------------------------------------------------------------------


def _consequence_three(city: City) -> Release:
    places = Places()
    places.place(clean(), unassessed())  # e0: one assessed clean, one unassessed
    places.place(clean(), unassessed(), courtesy())  # e1: and a courtesy visit
    places.place()  # e2: no inspections
    places.place(courtesy())  # e3: only a courtesy visit
    places.place(clean())  # e4: assessed only
    return places.release(city)


PEST_IN_SOME = [
    PEST,
    exists("violations", PEST),
    exists("inspections", exists("violations", PEST)),
]


@pytest.mark.parametrize("form", PEST_IN_SOME, ids=["direct", "two-step", "nested"])
def test_an_unassessed_inspection_leaves_strict_unknown_and_assessed_false(
    run: Runner, city: City, doc: Doc, form: dict[str, Any]
) -> None:
    release = _consequence_three(city)
    assert ask(run, doc, release, form) == [
        "UNKNOWN(NOT_COVERED)",
        "UNKNOWN(NOT_COVERED)",
        "UNKNOWN(NOT_COVERED)",
        "UNKNOWN(NOT_COVERED)",
        "FALSE",
    ]
    assessed = {**form, "lift": "assessed"}
    if form is PEST:
        assessed = {**PEST, "lift": "assessed"}
    assert ask(run, doc, release, assessed) == [
        "FALSE",
        "FALSE",  # the courtesy visit changes nothing
        "UNKNOWN(NOT_COVERED)",
        "UNKNOWN(NOT_COVERED)",
        "FALSE",
    ]


def test_the_direct_two_step_and_nested_forms_are_one_canonical_form(
    run: Runner, city: City, doc: Doc
) -> None:
    trees = []
    for form in PEST_IN_SOME:
        result = run(doc([form]), city())
        assert result.refusals == []
        trees.append(result.resolution.cohorts["c"].tree)
    assert trees[0] == trees[1] == trees[2]


# --- §6.5, consequence 4 --------------------------------------------------------------------------


ROUTINE_PEST = exists("inspections", ROUTINE, exists("violations", PEST), lift="assessed")


@pytest.mark.parametrize("lift", ["strict", "assessed"])
def test_step_conditions_under_either_lift(run: Runner, city: City, doc: Doc, lift: str) -> None:
    """A pest violation in some routine inspection, counting only assessed ones: unassessed
    routine inspections, or none, leave it UNKNOWN (NOT_COVERED) whatever the others; so does an
    only assessed clean inspection with a missing kind."""
    places = Places()
    places.place(unassessed(), unassessed(), clean("follow_up"))  # e0: routine all unassessed
    places.place(clean("follow_up"))  # e1: no routine inspection
    places.place(clean(None))  # e2: the only assessed clean one has no kind
    places.place(clean(), unassessed())  # e3
    places.place({"kind": "routine", "checklist": "pests", "found": ["pest"]})  # e4
    question = {**ROUTINE_PEST, "lift": lift}
    assert ask(run, doc, places.release(city), question) == [
        "UNKNOWN(NOT_COVERED)",
        "UNKNOWN(NOT_COVERED)",
        "UNKNOWN(NOT_COVERED,NO_INFORMATION)",
        "UNKNOWN(NOT_COVERED)" if lift == "strict" else "FALSE",
        "TRUE",
    ]


# --- §6.5, consequence 5 --------------------------------------------------------------------------


def test_two_nested_questions_in_one_where_are_one_question_over_inspections(
    run: Runner, city: City, doc: Doc
) -> None:
    places = Places()
    places.place()  # no inspections
    places.place({"kind": "routine", "checklist": "all", "found": ["pest", "temp"]})
    places.place(
        {"kind": "routine", "checklist": "all", "found": ["pest", "temp"]},
        {"kind": "courtesy", "checklist": "all"},
    )
    places.place(
        {"kind": "routine", "checklist": "all", "found": ["pest"]},
        {"kind": "routine", "checklist": "all", "found": ["temp"]},
    )
    both = exists("inspections", exists("violations", PEST), exists("violations", TEMP))
    assert ask(run, doc, places.release(city), both) == [
        "UNKNOWN(NOT_COVERED)",
        "TRUE",
        "TRUE",  # the courtesy visit changes nothing
        "FALSE",  # both, but not in one inspection
    ]


# --- §6.5, consequence 6 --------------------------------------------------------------------------


def test_a_question_whose_last_steps_look_up_is_final(run: Runner, city: City, doc: Doc) -> None:
    """No licences is FALSE for *licensed at tier 3* when licences are closed: the tier is
    looked up from each licence, so the question is final."""
    release = city(
        {
            "establishments": [{"establishment_id": f"e{i}"} for i in range(3)],
            "licence_types": [{"type_id": "t3", "tier": 3}, {"type_id": "t1", "tier": 1}],
            "licences": [
                {"licence_id": "l0", "establishment_id": "e0", "type_id": "t3"},
                {"licence_id": "l1", "establishment_id": "e1", "type_id": "t1"},
            ],
        }
    )
    tier = value("licence_types.tier", values=[3])
    assert ask(run, doc, release, tier) == ["TRUE", "FALSE", "FALSE"]
    tree = run(doc([tier]), release).resolution.cohorts["c"].tree
    assert tree.lift is None


# --- §6.5, consequences 7 and 8 -------------------------------------------------------------------


def test_record_filters_under_some_and_every(run: Runner, city: City, doc: Doc) -> None:
    """A complaint whose channel is missing makes every UNKNOWN, not FALSE, when it fails the
    question, and some UNKNOWN, not TRUE, when it satisfies it."""
    release = city(
        {
            "establishments": [{"establishment_id": "e0"}, {"establishment_id": "e1"}],
            "complaints": [
                {"complaint_id": "c0", "establishment_id": "e0", "severity": 1},
                {"complaint_id": "c1", "establishment_id": "e1", "severity": 5},
            ],
        }
    )
    severe = value("complaints.severity", range={"gte": 3})
    assert ask(run, doc, release, exists("complaints", severe, quantifier="every")) == [
        "UNKNOWN(NO_INFORMATION)",
        "TRUE",
    ]
    assert ask(run, doc, release, exists("complaints", severe)) == [
        "FALSE",
        "UNKNOWN(NO_INFORMATION)",
    ]


def test_every_over_undeclared_coverage_is_never_true(run: Runner, city: City, doc: Doc) -> None:
    release = city(
        {
            "establishments": [{"establishment_id": "e0"}, {"establishment_id": "e1"}],
            "staff": [{"staff_id": "s0", "establishment_id": "e0", "certified": True}],
        }
    )
    all_certified = exists("staff", value("staff.certified", values=[True]), quantifier="every")
    assert ask(run, doc, release, all_certified) == [
        "UNKNOWN(NO_INFORMATION)",
        "UNKNOWN(NO_INFORMATION,NO_ROWS)",
    ]


def test_an_unknown_parent_scope_is_never_closed_by_coverage_all(
    run: Runner, city: City, doc: Doc
) -> None:
    places = Places()
    places.place(clean(None), clean())
    scope = {"kind": "value", "column": "inspections.kind", "values": ["routine", "follow_up"]}
    release = places.release(city, violations={"parents": "all", "parent_scope": scope})
    assert ask(run, doc, release, exists("violations", PEST), unit="inspections") == [
        "UNKNOWN(NO_INFORMATION)",
        "FALSE",
    ]


# --- §13.4: the other scenarios -------------------------------------------------------------------


def test_every_over_no_rows(run: Runner, city: City, doc: Doc) -> None:
    """Final: NO_ROWS. Intermediate: nothing below could be considered, NOT_COVERED."""
    places = Places()
    places.place()
    places.place(courtesy())
    release = places.release(city)
    final = exists("inspections", ROUTINE, quantifier="every")
    assert ask(run, doc, release, final) == ["UNKNOWN(NO_ROWS)", "FALSE"]
    intermediate = exists("inspections", {"not": exists("violations", PEST)}, quantifier="every")
    assert ask(run, doc, release, intermediate) == [
        "UNKNOWN(NOT_COVERED)",
        "UNKNOWN(NOT_COVERED)",  # the courtesy visit is dropped
    ]


def test_every_with_scope_columns_covers_only_the_listed_codes(
    run: Runner, city: City, doc: Doc
) -> None:
    places = Places()
    places.place({"kind": "routine", "checklist": "pests", "found": ["pest"]})
    places.place({"kind": "routine", "checklist": "all", "found": ["pest"]})
    release = places.release(city)
    mild = exists("violations", value("violations.severity", range={"lt": 3}), quantifier="every")
    result = run(doc([mild], unit="inspections"), release).result
    # Neither violation has a severity: UNKNOWN, closed or not.
    assert summary(result) == ["UNKNOWN(NO_INFORMATION)", "UNKNOWN(NO_INFORMATION)"]
    places.rows["violations"] = [{**row, "severity": 1} for row in places.rows["violations"]]
    result = run(doc([mild], unit="inspections"), places.release(city)).result
    assert summary(result) == ["TRUE", "TRUE"]
    assert [value.flags for value in result.values] == [{Flag.SCOPE_PARTIAL}, set()]


def test_min_count_over_a_nested_question(run: Runner, city: City, doc: Doc) -> None:
    places = Places()
    places.place({"kind": "routine", "checklist": "all", "found": ["pest"]}, clean())
    places.place(
        {"kind": "routine", "checklist": "all", "found": ["pest"]},
        {"kind": "routine", "checklist": "all", "found": ["pest"]},
    )
    places.place({"kind": "routine", "checklist": "all", "found": ["pest"]}, unassessed())
    twice = exists("inspections", exists("violations", PEST), min_count=2)
    assert ask(run, doc, places.release(city), twice) == [
        "FALSE",
        "TRUE",
        "UNKNOWN(NOT_COVERED)",
    ]


def test_deeper_paths_under_assessed(run: Runner, city: City, doc: Doc) -> None:
    """From an owner, three down steps: each intermediate question drops what was not
    assessed, so an establishment with only unassessed inspections drops out too."""
    places = Places()
    places.place(clean(), owner="o0")
    places.place(unassessed(), owner="o0")
    places.place(unassessed(), owner="o1")
    places.rows["owners"] = [{"owner_id": "o0"}, {"owner_id": "o1"}, {"owner_id": "o2"}]
    release = places.release(city)
    assert ask(run, doc, release, PEST, unit="owners") == [
        "UNKNOWN(NOT_COVERED)",
        "UNKNOWN(NOT_COVERED)",
        "UNKNOWN(NOT_COVERED)",
    ]
    assert ask(run, doc, release, {**PEST, "lift": "assessed"}, unit="owners") == [
        "FALSE",
        "UNKNOWN(NOT_COVERED)",
        "UNKNOWN(NOT_COVERED)",
    ]


def test_no_remaining_child_whose_conditions_are_true(run: Runner, city: City, doc: Doc) -> None:
    """Every routine inspection assessed and clean, but the question asks for follow-ups: no
    remaining child meets the step's conditions, so the answer is UNKNOWN, not FALSE."""
    places = Places()
    places.place(clean(), clean())
    follow_up = exists(
        "inspections",
        value("inspections.kind", values=["follow_up"]),
        exists("violations", PEST),
        lift="assessed",
    )
    assert ask(run, doc, places.release(city), follow_up) == ["UNKNOWN(NOT_COVERED)"]


def test_flags_are_carried_through_nested_questions(run: Runner, city: City, doc: Doc) -> None:
    places = Places()
    places.place(clean())
    grouped = {
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
    release = places.release(
        city, violations={"parents": grouped, "statuses": {"parents": "proposed"}}
    )
    result = run(doc([PEST]), release).result
    assert summary(result) == ["FALSE"]
    assert {(mark.flag, mark.relationship) for mark in result.marks} == {
        (Flag.COVERAGE_PROPOSED, VIOLATIONS)
    }


def test_covered_in_each_of_its_cases(run: Runner, city: City, doc: Doc) -> None:
    places = Places()
    places.place(
        courtesy(),
        clean(),
        {"kind": "routine", "checklist": "temps"},
        unassessed(),
        {"checklist": "pests"},
    )
    release = places.release(city)
    covered = {"kind": "covered", "table": "violations", "scope": {"code": ["pest"]}}
    assert ask(run, doc, release, covered, unit="inspections") == [
        "UNKNOWN(OUT_OF_SCOPE)",
        "TRUE",
        "FALSE",
        "FALSE",
        "TRUE",  # a missing kind counts as in scope
    ]
    assert ask(run, doc, release, {"kind": "covered", "table": "staff"}) == [
        "UNKNOWN(NO_INFORMATION)"
    ]

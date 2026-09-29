"""Memberships (§9.2, D380, D381): each category of a multi-valued categorical column asked of every
unit as an existence question, its template's value leaf given the category, by the reference
evaluator, and counted as pairs plus default by the SQL compiler, which must agree; and by
``compile_crossing`` of each category's question, a third way; over a city, and over a survey
whose finds have two scope columns, grouped coverage and filters on a scope column."""

import json
import pickle
import subprocess
import sys
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from typing import Any

import pytest
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st
from pydantic import ValidationError
from sqlglot import exp, parse_one

from aibi.core.engine import build
from aibi.core.engine import memberships as reference
from aibi.core.engine import sql as compiler
from aibi.core.engine.canonical import (
    canonical_clause,
    canonicalise,
    category_clause,
    category_key,
    variable_form,
)
from aibi.core.engine.data import Release, key_part
from aibi.core.engine.evaluate import Evaluator, evaluate
from aibi.core.engine.ids import leaf_key
from aibi.core.engine.resolve import (
    Resolution,
    ResolvedCohort,
    ResolvedVariable,
    ViewVariable,
    resolve,
)
from aibi.core.engine.sql import CompileError, compile_inputs, compile_materialised
from aibi.core.engine.variables import Memberships, evaluate_variable
from aibi.core.schema.analyses import Variable
from aibi.core.schema.limits import MAX_CATEGORIES
from aibi.core.schema.loading import load_document
from aibi.core.schema.semantics import ExclusionReason, Flag, Reason
from aibi.core.store import parquet

City = Callable[..., Release]
Doc = Callable[..., dict[str, Any]]
Resolved = Callable[..., Resolution]
Materialise = Callable[..., Any]

INSPECTED = "rel:inspections.establishment"
OWNER = "rel:establishments.owner"
VIOLATIONS = "rel:violations.inspection"
TOPIC = build.column("complaints.topic", "category")
"""A category of the complaints, which their coverage's record filter does not filter."""
AREA = build.column(
    "violations.area",
    "category",
    permissible_values={"values": [{"value": v} for v in ("kitchen", "store")]},
)
"""A category of the violations that is not their coverage's scope column: a FALSE that relies
on a checklist that lists some codes only covers those (``SCOPE_PARTIAL``, §6.5 step 4)."""

EXAMPLES = settings(
    max_examples=150,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)
FEWER = settings(EXAMPLES, max_examples=50)

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
CHAINS = {"kind": "value", "column": "establishments.chain", "values": [True, False]}
"""A parent scope of the inspections, UNKNOWN for a unit whose chain is unknown: coverage
``all`` then does not close it (§6.5, steps 2 and 4)."""
INSPECTION_COVERAGE = [
    {"parents": "all"},
    {"parents": "all", "statuses": {"parents": "proposed"}},
    {},
    {"parents": "all", "parent_scope": CHAINS},
    {"parents": "all", "parent_scope": {**CHAINS, "values": [True]}},
    {"parents": "all", "record_filter": {"kind": ["routine", "follow_up"]}},
    {"parents": "all", "parent_scope": CHAINS, "record_filter": {"kind": ["routine"]}},
]
"""The inspections' coverage, a step before the violations' and the readings' and the last of
their kind's: ``all``, proposed, undeclared, two parent scopes, and record filters on their
kind, which every step reads (§6.5, step 1)."""


def _value(column: str, **predicate: Any) -> dict[str, Any]:
    return {"kind": "value", "column": column, **predicate}


COHORTS: list[list[Any]] = [
    [],
    [],
    [],
    [_value("establishments.grade", values=["A", "B"])],
    [{"kind": "exists", "table": "inspections", "where": []}],
    [_value("establishments.chain", values=[True])],
    [{"not": {"kind": "exists", "table": "complaints", "where": []}}],
]


# --- Releases ------------------------------------------------------------------------------------


@st.composite
def city_data(draw: st.DrawFn) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    """Rows for a small city, and the coverage options of its descriptors: violations of an
    undeclared code (``mold``) that a checklist may list, lists with unknown and repeated items,
    ratings NOT_APPLICABLE, complaints on channels the record filter does or does not allow or
    none, readings of appliances checked or not, staff over undeclared coverage, and
    inspections of kinds their own record filter does or does not allow, a step before the
    violations' and the readings'."""
    pick = lambda options: draw(st.sampled_from(options))  # noqa: E731
    rows: dict[str, list[dict[str, Any]]] = {
        "owners": [{"owner_id": "o0", "region": "north"}, {"owner_id": "o1", "region": None}],
        "checklist_items": [
            {"checklist": "basic", "code": "temp", "all_codes": False},
            {"checklist": "basic", "code": "mold", "all_codes": None},
            {"checklist": "temps", "code": "temp", "all_codes": False},
            {"checklist": "all", "code": None, "all_codes": True},
        ],
    }
    for name in ("establishments", "inspections", "inspection_checklists", "violations"):
        rows[name] = []
    for name in ("complaints", "staff", "readings", "checked_appliances"):
        rows[name] = []
    for index in range(draw(st.integers(0, 5))):
        place = f"e{index}"
        rows["establishments"].append(
            {
                "establishment_id": place,
                "grade": pick(["A", "B", "C", "D", "pending", "exempt", None]),
                "chain": pick([None, True, False]),
                "tags": pick(
                    [
                        None,
                        "?",
                        [],
                        ["vegan"],
                        ["vegan", "vegan"],
                        ["vegan", "halal"],
                        ["vegan", None],
                        ["halal", "?"],
                        ["kosher"],
                    ]
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
                    "rating": pick(["n/a", "low", "mid", "high", "later", "extreme", None]),
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
                        "code": pick(["temp", "pest", "label", "mold", None]),
                        "area": pick(["kitchen", "store", "yard", None]),
                    }
                )
            for appliance in draw(st.sets(st.sampled_from(["fridge", "freezer"]))):
                rows["checked_appliances"].append(
                    {"inspection_id": inspection, "appliance": appliance}
                )
            for _ in range(draw(st.integers(0, 2))):
                rows["readings"].append(
                    {
                        "reading_id": f"r{len(rows['readings'])}",
                        "inspection_id": inspection,
                        "appliance": pick(["fridge", "freezer", "oven", None]),
                    }
                )
        for _ in range(draw(st.integers(0, 2))):
            rows["complaints"].append(
                {
                    "complaint_id": f"c{len(rows['complaints'])}",
                    "establishment_id": place,
                    "channel": pick(["phone", "web", "letter", None]),
                    "topic": pick(["noise", "smell", "hygiene", None]),
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
    options = {
        "violations": pick(VIOLATION_COVERAGE),
        "inspections": pick(INSPECTION_COVERAGE),
        "extra": [TOPIC, AREA],
    }
    return rows, options


@st.composite
def membership_variables(draw: st.DrawFn) -> dict[str, Any]:
    """A variable's memberships (``each: "category"``) of a view on establishments, never
    refused: a scope-by-value column (grouped, and direct two steps down), a record filter's
    column and another column of its rows, a list with unknown and repeated items (the unit's,
    and looked up from each inspection), NOT_APPLICABLE rows, booleans over undeclared coverage,
    down, up and down again (one relationship at two steps), and a column looked up after the
    last down step, under both lifts; the inspections' record filter filters an earlier step
    of every column below them."""
    pick = lambda options: draw(st.sampled_from(options))  # noqa: E731
    lift = pick([{}, {"lift": "strict"}, {"lift": "assessed"}])
    each = {"each": "category"}
    choice = draw(st.integers(0, 12))
    if choice == 0:
        return {"column": "violations.code", **lift, **each}
    if choice == 11:
        return {"column": "violations.area", **lift, **each}
    if choice == 12:
        through = [
            {"rel": INSPECTED, "dir": "down"},
            {"rel": VIOLATIONS, "dir": "down"},
            {"rel": VIOLATIONS, "dir": "up"},
        ]
        return {"column": "inspections.kind", "via": through, **lift, **each}
    if choice == 1:
        return {"column": "readings.appliance", **lift, **each}
    if choice == 2:
        return {"column": "complaints.channel", **each}
    if choice == 3:
        return {"column": "complaints.topic", **each}
    if choice == 4:
        return {"column": "establishments.tags", **each}
    if choice == 5:
        looked = [{"rel": INSPECTED, "dir": "down"}, {"rel": INSPECTED, "dir": "up"}]
        column = pick(["establishments.tags", "establishments.grade"])
        return {"column": column, "via": looked, **each}
    if choice == 6:
        return {"column": "inspections.rating", **each}
    if choice == 7:
        return {"column": "staff.certified", **each}
    if choice == 8:
        return {"column": "inspections.kind", **each}
    if choice == 9:
        again = [
            {"rel": INSPECTED, "dir": "down"},
            {"rel": INSPECTED, "dir": "up"},
            {"rel": INSPECTED, "dir": "down"},
        ]
        return {"column": "inspections.kind", "via": again, **lift, **each}
    via = [
        {"rel": OWNER, "dir": "up"},
        {"rel": OWNER, "dir": "down"},
        {"rel": INSPECTED, "dir": "down"},
    ]
    return {"column": "inspections.rating", "via": via, **lift, **each}


OTHERS: list[dict[str, Any]] = [
    {"column": "establishments.grade"},
    {"column": "inspections.kind", "aggregate": "count"},
    {"column": "violations.code", "aggregate": "some", "values": ["temp"]},
]


# --- The reference evaluator's counts -----------------------------------------------------------


def _expected(resolution: Resolution, variables: Sequence[ResolvedVariable]) -> list[Any]:
    """Each cohort's variables materialised by the reference evaluator, and their joint counts
    for two or more (``memberships.materialise_over``)."""
    given = [reference.evaluated(variable) for variable in variables]
    return [
        reference.materialise_over(variables, given, evaluate(cohort).members)
        for cohort in resolution.cohorts.values()
    ]


def _variables(resolution: Resolution) -> list[ResolvedVariable]:
    return [resolution.variables[f"0/{index}"] for index in range(len(resolution.variables))]


def _agree(resolution: Resolution, materialised: Materialise) -> list[Any]:
    assert resolution.refusals == [], resolution.refusals
    variables = _variables(resolution)
    expected = _expected(resolution, variables)
    assert list(materialised(list(resolution.cohorts.values()), variables)) == expected
    return expected


# --- Differential properties ----------------------------------------------------------------------


@EXAMPLES
@given(data=city_data(), extra=st.data())
def test_memberships_counted_by_the_compiler_are_those_the_evaluator_asks_category_by_category(
    city: City,
    doc: Doc,
    variables_of: Resolved,
    materialised: Materialise,
    data: Any,
    extra: st.DataObject,
) -> None:
    """Memberships over cohorts (D380, D381): each listed category's units TRUE, FALSE and
    UNKNOWN by reason with their flags, the categories listed and their order, the units known
    and excluded by reason (``membership_units``), and for two variables or more their joint
    counts, counted in SQL as pairs plus default as the reference evaluator asks each category's
    question of each unit; with one flag to a word, so that every word is read."""
    rows, options = data
    release = city(rows, **options)
    written = {
        f"c{index}": extra.draw(st.sampled_from(COHORTS))
        for index in range(extra.draw(st.integers(min_value=1, max_value=2)))
    }
    asked = extra.draw(st.lists(membership_variables(), min_size=1, max_size=2))
    others = extra.draw(st.lists(st.sampled_from(OTHERS), max_size=1))
    resolution = variables_of(doc(written), release, extra.draw(st.permutations(asked + others)))
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(compiler, "MARK_BITS", 1)
        _agree(resolution, materialised)


@FEWER
@given(data=city_data(), extra=st.data())
def test_each_listed_category_counts_what_a_crossing_of_its_question_counts(
    city: City,
    doc: Doc,
    variables_of: Resolved,
    crossed: Callable[..., Any],
    data: Any,
    extra: st.DataObject,
) -> None:
    """A third way (D381): each listed category's question, the template given its value, as
    ``compile_crossing`` counts it over a cohort (one predicate per category), gives the units
    TRUE, FALSE and UNKNOWN, those UNKNOWN under every reason, and the flags that the
    evaluator's memberships give."""
    rows, options = data
    release = city(rows, **options)
    written = {"c0": extra.draw(st.sampled_from(COHORTS))}
    resolution = variables_of(doc(written), release, [extra.draw(membership_variables())])
    assert resolution.refusals == []
    [variable] = _variables(resolution)
    [cohort] = resolution.cohorts.values()
    found = reference.materialise(reference.evaluate(variable), evaluate(cohort).members)
    assert found.memberships is not None
    listed = found.memberships.categories
    assume(listed)
    assert variable.question is not None
    predicates = [
        ResolvedCohort(
            f"q{index}",
            "d",
            release,
            variable.unit,
            (category_clause(variable.question, one.category),),
            {},
            variable.fields,
            variable.coverage,
        )
        for index, one in enumerate(listed)
    ]
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(compiler, "MARK_BITS", 1)
        crossing = crossed([cohort], predicates)
    [split_of] = crossing.cohorts
    for one, split in zip(listed, split_of.splits, strict=True):
        by_reason = {ExclusionReason(r.value): n for r, n in split.unknown_by_reason.items() if n}
        assert (split.true, split.false, split.unknown) == (one.true, one.false, one.unknown)
        assert by_reason == {r: n for r, n in one.unknown_by_reason.items() if n}
        assert split.marks == one.marks


# --- A second release: two scope columns, grouped coverage, filters on scope columns ----------

VISITED = "rel:visits.site"
FOUND = "rel:finds.visit"
KIND = "rel:finds.kind"
SURVEY_CODES = {"?": "NOT_ASSESSED", "na": "NOT_APPLICABLE"}
TWO_SCOPES = {
    "table": "find_lists",
    "parent_columns": {"visit_id": "visit_id"},
    "scope_columns": {"code": "code", "zone": "zone"},
}
ONE_SCOPE = {
    "table": "find_one",
    "parent_columns": {"visit_id": "visit_id"},
    "scope_columns": {"code": "code"},
}
GROUPED_SCOPES = {
    "assignment": {
        "table": "assign",
        "parent_columns": {"visit_id": "visit_id"},
        "group_column": "grp",
    },
    "groups": {
        "table": "groups",
        "group_column": "grp",
        "scope_columns": {"code": "code", "zone": "zone"},
        "covers_all_column": "all",
    },
}
KINDS_AB = {"kind": "value", "column": "visits.kind", "values": ["a", "b"]}
FIND_COVERAGE: list[dict[str, Any]] = [
    {"parents": TWO_SCOPES},
    {"parents": TWO_SCOPES, "parent_scope": KINDS_AB},
    {"parents": TWO_SCOPES, "statuses": {"parents": "proposed"}},
    {"parents": ONE_SCOPE},
    {"parents": GROUPED_SCOPES},
    {"parents": GROUPED_SCOPES, "parent_scope": KINDS_AB},
    {"parents": "all", "record_filter": {"code": ["x", "y", "w", "ﬁ"]}},
    {"parents": "all", "record_filter": {"zone": ["n", "s"]}},
    {"parents": "all", "record_filter": {"code": ["x", "q", "w"], "zone": ["n"]}},
    {"parents": TWO_SCOPES, "record_filter": {"code": ["x", "y"]}},
    {"parents": "all"},
    {},
]
"""The finds' coverage: two scope columns listed together, one, grouped with a group covering
every value, each with and without a parent scope or proposed; record filters on a scope
column, on another and on both; ``all``; undeclared."""
VISIT_COVERAGE: list[dict[str, Any]] = [
    {"parents": "all"},
    {"parents": "all", "statuses": {"parents": "proposed"}},
    {},
    {
        "parents": "all",
        "parent_scope": {"kind": "value", "column": "sites.region", "values": ["n"]},
    },
    {"parents": "all", "record_filter": {"kind": ["a", "c"]}},
]
"""The visits' coverage, the first step of every column below them: ``all``, proposed,
undeclared, a parent scope, and a record filter on their kind (§6.5, step 1)."""
TAG_CELLS: list[Any] = [
    None,
    "?",
    "na",
    [],
    ["a"],
    ["a", "a"],
    ["a", "?"],
    ["b", None],
    ["na", "a"],
    [""],
    ["\U0001d11e", "ﬁ"],
]
FIND_CODES: list[Any] = ["x", "y", "w", "", "ﬁ", "\U0001d11e", "?", "na", None, "~"]
"""The empty string, a value of the Basic Multilingual Plane above the surrogates and one past
U+FFFF, missing codes, and a tilde, the default's own character (``_fresh``)."""


def survey_descriptors(
    finds: Mapping[str, Any], visits: Mapping[str, Any], declared: bool, zoned: bool
) -> list[Any]:
    """Sites, their visits and the visits' finds, each find of a kind looked up, and the finds'
    coverage tables; ``declared`` and ``zoned`` declare the codes' and the zones' values."""
    column, table = build.column, build.table
    codes: dict[str, Any] = {"missing_codes": SURVEY_CODES}
    if declared:
        codes["permissible_values"] = {"values": [{"value": v} for v in ("y", "x", "q")]}
    zones: dict[str, Any] = {}
    if zoned:
        zones["permissible_values"] = {"values": [{"value": v} for v in ("n", "s")]}
    return [
        build.dataset(disclosure={"allow_row_ids": True}),
        table("sites", ["site_id"]),
        column("sites.site_id", "string", identifier=True),
        column("sites.region", "category"),
        column("sites.tags", "list<category>", missing_codes=SURVEY_CODES),
        table("visits", ["visit_id"], role="event"),
        column("visits.visit_id", "string"),
        column("visits.site_id", "string"),
        column("visits.kind", "category"),
        column("visits.tags", "list<category>", missing_codes=SURVEY_CODES),
        build.relationship("visits", ["site_id"], "sites", role="site"),
        build.coverage(VISITED, **visits),
        table("finds", ["find_id"], role="event"),
        column("finds.find_id", "string"),
        column("finds.visit_id", "string"),
        column("finds.kind_id", "string"),
        column("finds.code", "category", **codes),
        column("finds.zone", "category", **zones),
        column("finds.flag", "boolean"),
        build.relationship("finds", ["visit_id"], "visits", role="visit"),
        build.coverage(FOUND, **finds),
        table("kinds", ["kind_id"]),
        column("kinds.kind_id", "string"),
        column("kinds.label", "category"),
        build.relationship("finds", ["kind_id"], "kinds", role="kind"),
        build.coverage(KIND, "all"),
        table("find_lists", ["visit_id", "code", "zone"], role="coverage"),
        column("find_lists.visit_id", "string"),
        column("find_lists.code", "string"),
        column("find_lists.zone", "string"),
        table("find_one", ["visit_id", "code"], role="coverage"),
        column("find_one.visit_id", "string"),
        column("find_one.code", "string"),
        table("assign", ["visit_id"], role="coverage"),
        column("assign.visit_id", "string"),
        column("assign.grp", "string"),
        table("groups", ["grp", "code", "zone"], role="coverage"),
        column("groups.grp", "string"),
        column("groups.code", "string"),
        column("groups.zone", "string"),
        column("groups.all", "boolean"),
    ]


SURVEY_TABLES = ("sites", "visits", "finds", "kinds", "find_lists", "find_one", "assign", "groups")
GROUPS = [
    {"grp": "g0", "code": "x", "zone": "n", "all": False},
    {"grp": "g0", "code": "w", "zone": "s", "all": False},
    {"grp": "g1", "code": None, "zone": None, "all": True},
    {"grp": "g2", "code": "\U0001d11e", "zone": "n", "all": False},
    {"grp": "g3", "code": "x", "zone": "s", "all": True},
]
"""A group listing two tuples, one covering every value, one listing a value past U+FFFF, and
one covering every value that lists a tuple too."""


def _survey(rows: Mapping[str, list[dict[str, Any]]], options: Mapping[str, Any]) -> Release:
    given = {name: list(rows.get(name, [])) for name in SURVEY_TABLES}
    descriptors = survey_descriptors(
        options.get("finds", {}),
        options.get("visits", {"parents": "all"}),
        bool(options.get("declared")),
        bool(options.get("zoned")),
    )
    return build.release(descriptors, given, check=False)


@st.composite
def survey_data(draw: st.DrawFn) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    """Rows for a small survey, and its coverage options: visits of kinds a parent scope or a
    record filter does and does not hold, lists with missing, NOT_APPLICABLE, repeated, empty
    and non-BMP items, finds of every code and zone, some unknown, their tuples listed or not,
    or listed with no zone, and kinds looked up that are missing or dangling."""
    pick = lambda options: draw(st.sampled_from(options))  # noqa: E731
    rows: dict[str, list[dict[str, Any]]] = {name: [] for name in SURVEY_TABLES}
    rows["kinds"] = [
        {"kind_id": "k0", "label": "m"},
        {"kind_id": "k1", "label": None},
        {"kind_id": "k2", "label": "ﬁ"},
    ]
    rows["groups"] = list(GROUPS)
    listed: dict[str, set[tuple[Any, ...]]] = {"find_lists": set(), "find_one": set()}
    for index in range(draw(st.integers(0, 4))):
        site = f"s{index}"
        rows["sites"].append(
            {"site_id": site, "region": pick(["n", "s", None]), "tags": pick(TAG_CELLS)}
        )
        for _ in range(draw(st.integers(0, 3))):
            visit = f"v{len(rows['visits'])}"
            rows["visits"].append(
                {
                    "visit_id": visit,
                    "site_id": pick([site, site, None, "gone"]),
                    "kind": pick(["a", "b", "c", None]),
                    "tags": pick(TAG_CELLS),
                }
            )
            for _ in range(draw(st.integers(0, 3))):
                tuple_ = (
                    visit,
                    pick(["x", "y", "w", "q", "\U0001d11e"]),
                    pick(["n", "s", "e", None]),
                )
                listed["find_lists"].add(tuple_)
            if draw(st.booleans()):
                listed["find_one"].add((visit, pick(["x", "w", "zz"])))
            group = pick([None, "g0", "g1", "g2", "g3"])
            if group is not None:
                rows["assign"].append({"visit_id": visit, "grp": group})
            for _ in range(draw(st.integers(0, 3))):
                rows["finds"].append(
                    {
                        "find_id": f"f{len(rows['finds'])}",
                        "visit_id": pick([visit, visit, None]),
                        "kind_id": pick(["k0", "k1", "k2", None, "gone"]),
                        "code": pick(FIND_CODES),
                        "zone": pick(["n", "s", "e", None, "?"]),
                        "flag": pick([True, False, None]),
                    }
                )
    rows["find_lists"] = [
        {"visit_id": v, "code": c, "zone": z}
        for v, c, z in sorted(listed["find_lists"], key=lambda one: (one[0], one[1], one[2] or ""))
    ]
    rows["find_one"] = [{"visit_id": v, "code": c} for v, c in sorted(listed["find_one"])]
    options = {
        "finds": pick(FIND_COVERAGE),
        "visits": pick(VISIT_COVERAGE),
        "declared": draw(st.booleans()),
        "zoned": draw(st.booleans()),
    }
    return rows, options


DOWN, FIND, UP, BACK, KIND_UP = (
    {"rel": VISITED, "dir": "down"},
    {"rel": FOUND, "dir": "down"},
    {"rel": VISITED, "dir": "up"},
    {"rel": FOUND, "dir": "up"},
    {"rel": KIND, "dir": "up"},
)


@st.composite
def survey_variables(draw: st.DrawFn) -> dict[str, Any]:
    """Memberships of a view on sites, never refused: each of two scope columns, a boolean, a
    label looked up after the last step, a list on the unit and one below it, a scope column
    reached down, up and down again, and down the finds, back up and down them again (one
    relationship, and its record filter, at two steps), and the visits' kind directly and back
    up from the finds, under every lift."""
    lift = draw(st.sampled_from([{}, {"lift": "assessed"}, {"lift": "strict"}]))
    each = {"each": "category"}
    return draw(
        st.sampled_from(
            [
                {"column": "finds.code", **lift, **each},
                {"column": "finds.zone", **lift, **each},
                {"column": "finds.flag", **lift, **each},
                {"column": "kinds.label", "via": [DOWN, FIND, KIND_UP], **lift, **each},
                {"column": "visits.tags", **each},
                {"column": "sites.tags", **each},
                {"column": "visits.kind", "via": [DOWN, FIND, BACK], **lift, **each},
                {"column": "finds.code", "via": [DOWN, UP, DOWN, FIND], **lift, **each},
                {"column": "finds.code", "via": [DOWN, FIND, BACK, FIND], **lift, **each},
                {"column": "finds.zone", "via": [DOWN, FIND, BACK, FIND], **lift, **each},
                {"column": "finds.flag", "via": [DOWN, FIND, BACK, FIND], **lift, **each},
                {"column": "visits.kind", **each},
            ]
        )
    )


SURVEY_COHORTS: list[list[Any]] = [
    [],
    [],
    [_value("sites.region", values=["n"])],
    [{"kind": "exists", "table": "visits", "where": []}],
]


def _universe(release: Release, variable: ResolvedVariable) -> list[Any]:
    """Categories to ask about: every string or boolean a cell or item holds, and others none
    does."""
    found: dict[object, Any] = {}
    asked: list[Any] = [False, True] if variable.datatype == "boolean" else []
    asked += ["y", "x", "q", "n", "s", "e", "zz", "unseen", "", "~", "~~", "ﬁ"]
    asked += ["\U0001d11e", "?", "na"]
    for table in release.table_ids:
        rows = release.rows(table)
        for column in release.columns(table):
            for row in range(len(rows)):
                cell = rows.cell(row, column)
                for one in cell.value if isinstance(cell.value, tuple) else (cell,):
                    if isinstance(one.value, str | bool):
                        asked.append(one.value)
    for value in asked:
        found.setdefault(key_part(value), value)
    return list(found.values())


@FEWER
@given(data=survey_data(), extra=st.data())
def test_memberships_over_scope_tuples_groups_and_filters_are_asked_and_counted_alike(
    doc: Doc,
    variables_of: Resolved,
    materialised: Materialise,
    crossed: Callable[..., Any],
    data: Any,
    extra: st.DataObject,
) -> None:
    """A second release (D381), where the first reaches no case below: two scope columns listed
    together (``SCOPE_PARTIAL`` for one of them) or a tuple listed with one unknown, a group
    covering every value, alone or with a tuple listed, record filters on a scope column and on
    a relationship reached at two steps, lists with non-BMP items, values past U+FFFF and the
    empty string. The
    evaluator's answer for every category of a universe, held or not, but for those a filtered
    column cannot list, is ``Evaluator.truth`` of Q_c, never ``NO_ROWS``; the compiler counts
    what the evaluator does, with the joint counts; and a crossing of each listed category's Q_c
    and two unlisted ones counts what the answers give, UNKNOWN by every reason, with flags."""
    rows, options = data
    release = _survey(rows, options)
    written = {
        f"c{index}": extra.draw(st.sampled_from(SURVEY_COHORTS))
        for index in range(extra.draw(st.integers(1, 2)))
    }
    asked = extra.draw(st.lists(survey_variables(), min_size=1, max_size=2))
    others = extra.draw(
        st.lists(
            st.sampled_from(
                [{"column": "sites.region"}, {"column": "visits.kind", "aggregate": "count"}]
            ),
            max_size=1,
        )
    )
    resolution = variables_of(doc(written, unit="sites"), release, asked + others)
    assert resolution.refusals == []
    variables = _variables(resolution)
    units = range(len(release.rows("sites")))
    for variable in variables:
        if variable.kind != "memberships":
            continue
        answers = reference.evaluate(variable)
        cohort = ResolvedCohort(
            variable.key, "", release, "sites", (), {}, variable.fields, variable.coverage
        )
        evaluator = Evaluator(cohort)
        assert variable.question is not None
        for category in _universe(release, variable):
            if answers.closed and category not in answers.fixed:
                continue
            question = category_clause(variable.question, category)
            for row in units:
                found = answers.answer(row, category)
                assert evaluator.truth(question, "sites", row) == found, (category, row)
                assert Reason.NO_ROWS not in found.reasons
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(compiler, "MARK_BITS", 1)
        _agree(resolution, materialised)
    [cohort, *_] = resolution.cohorts.values()
    members = evaluate(cohort).members
    for variable in variables:
        if variable.kind != "memberships":
            continue
        answers = reference.evaluate(variable)
        listing = reference.materialise(answers, members).memberships
        assert listing is not None
        assert variable.question is not None
        listed = [one.category for one in listing.categories]
        unlisted = [] if answers.closed or variable.datatype == "boolean" else ["unseen", "w"]
        categories = listed + [one for one in unlisted if one not in listed]
        if not categories:
            continue
        predicates = [
            ResolvedCohort(
                f"q{index}",
                "d",
                release,
                "sites",
                (category_clause(variable.question, category),),
                {},
                variable.fields,
                variable.coverage,
            )
            for index, category in enumerate(categories)
        ]
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(compiler, "MARK_BITS", 1)
            crossing = crossed([cohort], predicates)
        [split_of] = crossing.cohorts
        for category, split in zip(categories, split_of.splits, strict=True):
            found = [answers.answer(row, category) for row in members]
            counts = (
                sum(one.is_true for one in found),
                sum(one.is_false for one in found),
                sum(one.is_unknown for one in found),
            )
            assert (split.true, split.false, split.unknown) == counts, category
            by_reason = Counter(reason.value for one in found for reason in one.reasons)
            assert {r.value: n for r, n in split.unknown_by_reason.items() if n} == by_reason
            assert split.marks == frozenset(mark for one in found for mark in one.marks)


def test_a_pair_is_kept_where_its_flags_alone_are_not_its_default_s(
    doc: Doc, variables_of: Resolved, materialised: Materialise
) -> None:
    """A pair differs from its row's default in its flags alone (D381): under ``assessed`` a
    site's visit whose group lists ``x`` in one zone is FALSE for ``x`` with ``SCOPE_PARTIAL``
    and ``NOT_COVERED`` by default, which drops it, while a visit whose group covers every value
    is FALSE both ways; so the site is FALSE for ``x`` with ``SCOPE_PARTIAL`` and FALSE with no
    flag by default, and ``x`` is listed with its flag, as SQL keeps a pair whose code and
    reasons are its default's."""
    rows = {
        "groups": GROUPS,
        "sites": [{"site_id": "s0", "region": "n"}],
        "visits": [
            {"visit_id": "v0", "site_id": "s0", "kind": "a"},
            {"visit_id": "v1", "site_id": "s0", "kind": "a"},
        ],
        "assign": [{"visit_id": "v0", "grp": "g0"}, {"visit_id": "v1", "grp": "g1"}],
    }
    release = _survey(rows, {"finds": {"parents": GROUPED_SCOPES}})
    asked = [{"column": "finds.code", "lift": "assessed", "each": "category"}]
    resolution = variables_of(doc([], unit="sites"), release, asked)
    [variable] = _variables(resolution)
    answers = reference.evaluate(variable)
    [default] = answers.defaults
    assert (default.is_false, default.marks) == (True, frozenset())
    x = answers.answer(0, "x")
    assert x.is_false
    assert {mark.flag for mark in x.marks} == {Flag.SCOPE_PARTIAL}
    found = _only(_agree(resolution, materialised))
    assert list(_counts(found)) == ["w", "x"]
    [listed] = [one for one in found.memberships.categories if one.category == "x"]
    assert {mark.flag for mark in listed.marks} == {Flag.SCOPE_PARTIAL}


def test_a_tuple_listed_with_an_unknown_scope_cell_closes_no_category(
    doc: Doc, variables_of: Resolved, materialised: Materialise
) -> None:
    """Closedness reads whole tuples (§6.5 step 4, D381): a visit whose list names code ``x``
    in no zone is not covered for ``x``, which is its default and so not listed, while ``w``,
    listed in zone ``n``, is FALSE with ``SCOPE_PARTIAL``, in the evaluator and in SQL."""
    rows = {
        "sites": [{"site_id": "s0", "region": "n"}],
        "visits": [{"visit_id": "v0", "site_id": "s0", "kind": "a"}],
        "find_lists": [
            {"visit_id": "v0", "code": "x", "zone": None},
            {"visit_id": "v0", "code": "w", "zone": "n"},
        ],
    }
    release = _survey(rows, {"finds": {"parents": TWO_SCOPES}})
    resolution = variables_of(
        doc([], unit="sites"), release, [{"column": "finds.code", "each": "category"}]
    )
    found = _only(_agree(resolution, materialised))
    [variable] = _variables(resolution)
    answers = reference.evaluate(variable)
    assert answers.answer(0, "x") == answers.defaults[0]
    assert answers.defaults[0].reasons == frozenset({Reason.NOT_COVERED})
    assert _counts(found) == {"w": (0, 1, 0)}
    [w] = found.memberships.categories
    assert {mark.flag for mark in w.marks} == {Flag.SCOPE_PARTIAL}


def test_a_group_covering_every_value_that_lists_a_tuple_flags_no_category_scope_partial(
    doc: Doc, variables_of: Resolved, materialised: Materialise
) -> None:
    """A group that covers every value closes each category with no ``SCOPE_PARTIAL`` (§6.5
    step 4, D381), though it lists ``x`` in one zone of two: its visit's site is FALSE for
    ``x`` as by default, so nothing is listed, in the evaluator and in SQL."""
    rows = {
        "groups": GROUPS,
        "sites": [{"site_id": "s0", "region": "n"}],
        "visits": [{"visit_id": "v0", "site_id": "s0", "kind": "a"}],
        "assign": [{"visit_id": "v0", "grp": "g3"}],
    }
    release = _survey(rows, {"finds": {"parents": GROUPED_SCOPES}})
    resolution = variables_of(
        doc([], unit="sites"), release, [{"column": "finds.code", "each": "category"}]
    )
    found = _only(_agree(resolution, materialised))
    [variable] = _variables(resolution)
    x = reference.evaluate(variable).answer(0, "x")
    assert (x.is_false, x.marks) == (True, frozenset())
    assert _counts(found) == {}
    assert found.memberships.known == 1


def test_a_boolean_scope_column_lists_no_category_its_coverage_stores_as_a_string(
    doc: Doc, variables_of: Resolved, materialised: Materialise
) -> None:
    """A value a coverage table lists is a category only where it is stored as the column's
    categories are (D381): a list that names the boolean scope column's value as the string
    ``"true"`` lists ``false`` and ``true`` alone, and closes neither, in the evaluator, which
    asks no other, and in SQL."""
    finds = {
        "parents": {
            "table": "find_one",
            "parent_columns": {"visit_id": "visit_id"},
            "scope_columns": {"code": "flag"},
        }
    }
    rows = {
        "sites": [{"site_id": "s0", "region": "n"}],
        "visits": [{"visit_id": "v0", "site_id": "s0", "kind": "a"}],
        "find_one": [{"visit_id": "v0", "code": "true"}],
        "finds": [
            {"find_id": "f0", "visit_id": "v0", "flag": True},
            {"find_id": "f1", "visit_id": "v0", "flag": False},
        ],
    }
    release = _survey(rows, {"finds": finds})
    resolution = variables_of(
        doc([], unit="sites"), release, [{"column": "finds.flag", "each": "category"}]
    )
    found = _only(_agree(resolution, materialised))
    [variable] = _variables(resolution)
    assert all(isinstance(one, bool) for one in reference.evaluate(variable).paired[0])
    assert list(_counts(found)) == [False, True]


def test_a_category_scope_column_lists_no_category_its_coverage_stores_as_a_boolean(
    doc: Doc, variables_of: Resolved, materialised: Materialise
) -> None:
    """A value a coverage table lists is a category only where it is stored as the column's
    categories are (D381): a list that names a category scope column's value as the boolean
    ``true`` lists only the code a find holds, which the list does not close, in the evaluator,
    which asks no other, and in SQL."""
    marks = "rel:finds.visit"
    release = build.release(
        [
            build.dataset(disclosure={"allow_row_ids": True}),
            build.table("visits", ["visit_id"]),
            build.column("visits.visit_id", "string"),
            build.table("finds", ["find_id"], role="event"),
            build.column("finds.find_id", "string"),
            build.column("finds.visit_id", "string"),
            build.column("finds.code", "category"),
            build.relationship("finds", ["visit_id"], "visits", role="visit"),
            build.coverage(
                marks,
                {
                    "table": "find_marks",
                    "parent_columns": {"visit_id": "visit_id"},
                    "scope_columns": {"code": "code"},
                },
            ),
            build.table("find_marks", ["visit_id", "code"], role="coverage"),
            build.column("find_marks.visit_id", "string"),
            build.column("find_marks.code", "boolean"),
        ],
        {
            "visits": [{"visit_id": "v0"}],
            "finds": [{"find_id": "f0", "visit_id": "v0", "code": "x"}],
            "find_marks": [{"visit_id": "v0", "code": True}],
        },
        check=False,
    )
    resolution = variables_of(
        doc([], unit="visits"), release, [{"column": "finds.code", "each": "category"}]
    )
    found = _only(_agree(resolution, materialised))
    [variable] = _variables(resolution)
    assert list(reference.evaluate(variable).paired[0]) == ["x"]
    assert list(_counts(found)) == ["x"]


# --- Scenarios ------------------------------------------------------------------------------------


def _only(found: list[Any]) -> Any:
    [(read, _)] = found
    return read[0]


def _counts(materialised: Any) -> dict[Any, tuple[int, int, int]]:
    """Each listed category's units TRUE, FALSE and UNKNOWN, in the listing's order."""
    return {
        one.category: (one.true, one.false, one.unknown)
        for one in materialised.memberships.categories
    }


def _unknown(materialised: Any, category: Any) -> dict[str, int]:
    [one] = [one for one in materialised.memberships.categories if one.category == category]
    return {reason.value: n for reason, n in one.unknown_by_reason.items() if n}


def test_each_category_asks_whether_some_row_has_it_and_a_not_applicable_row_has_none(
    city: City, doc: Doc, variables_of: Resolved, materialised: Materialise
) -> None:
    """Each category is its existence question (D380): a unit with rows in two categories is in
    both, a NOT_APPLICABLE rating is FALSE for every category (§6.4), one not assessed UNKNOWN,
    and a unit with no inspection, whose inspections are all recorded, FALSE; the denominator
    is the units for which it is known."""
    release = city(
        {
            "establishments": [{"establishment_id": f"e{n}"} for n in range(4)],
            "inspections": [
                {"inspection_id": "i0", "establishment_id": "e0", "rating": "high"},
                {"inspection_id": "i1", "establishment_id": "e0", "rating": "low"},
                {"inspection_id": "i2", "establishment_id": "e1", "rating": "n/a"},
                {"inspection_id": "i3", "establishment_id": "e2", "rating": "later"},
            ],
        }
    )
    found = _only(
        _agree(
            variables_of(doc([]), release, [{"column": "inspections.rating", "each": "category"}]),
            materialised,
        )
    )
    assert _counts(found) == {"low": (1, 2, 1), "mid": (0, 3, 1), "high": (1, 2, 1)}
    assert _unknown(found, "mid") == {"NOT_ASSESSED": 1}
    assert (found.n, found.excluded_units, found.excluded[ExclusionReason.NOT_ASSESSED]) == (
        3,
        1,
        1,
    )
    assert dict(found.values) == {}


def test_a_record_filter_s_column_lists_the_allowed_values_and_no_others(
    city: City, doc: Doc, variables_of: Resolved, materialised: Materialise
) -> None:
    """A column the last step's record filter filters lists its allowed values only, zeros
    included, in UTF-16 order where it declares none (D380, m10): the table records no row of
    another, so a letter is FALSE for both, a missing channel UNKNOWN."""
    release = city(
        {
            "establishments": [{"establishment_id": f"e{n}"} for n in range(4)],
            "complaints": [
                {"complaint_id": "c0", "establishment_id": "e0", "channel": "letter"},
                {"complaint_id": "c1", "establishment_id": "e1", "channel": "phone"},
                {"complaint_id": "c2", "establishment_id": "e2", "channel": None},
            ],
        }
    )
    found = _only(
        _agree(
            variables_of(doc([]), release, [{"column": "complaints.channel", "each": "category"}]),
            materialised,
        )
    )
    assert _counts(found) == {"phone": (1, 2, 1), "web": (0, 3, 1)}
    assert _unknown(found, "web") == {"NO_INFORMATION": 1}


@pytest.mark.parametrize("lift", [{}, {"lift": "assessed"}])
def test_a_record_filter_at_an_earlier_step_leaves_its_rows_out_for_every_category(
    city: City, doc: Doc, variables_of: Resolved, materialised: Materialise, lift: dict[str, Any]
) -> None:
    """Every step reads its record filter (§6.5 step 1, D381), not the last alone: the
    violation of a courtesy inspection, which the inspections' filter leaves out, makes no
    establishment TRUE for its code, in the evaluator and in SQL, as ``some`` of the code
    asks it."""
    rows = {
        "establishments": [{"establishment_id": "e0"}],
        "inspections": [
            {"inspection_id": "i0", "establishment_id": "e0", "kind": "courtesy"},
            {"inspection_id": "i1", "establishment_id": "e0", "kind": "routine"},
        ],
        "violations": [{"violation_id": "v0", "inspection_id": "i0", "code": "temp"}],
    }
    release = city(
        rows,
        inspections={"parents": "all", "record_filter": {"kind": ["routine", "follow_up"]}},
        violations={"parents": "all"},
    )
    asked = {"column": "violations.code", "each": "category", **lift}
    found = _only(_agree(variables_of(doc([]), release, [asked]), materialised))
    assert _counts(found) == {"temp": (0, 1, 0), "pest": (0, 1, 0), "label": (0, 1, 0)}
    some = {"column": "violations.code", "aggregate": "some", "values": ["temp"], **lift}
    [variable] = _variables(variables_of(doc([]), release, [some]))
    assert [one.value for one in evaluate_variable(variable)] == [False]


def test_a_relationship_at_two_steps_reads_its_record_filter_at_both(
    doc: Doc, variables_of: Resolved, materialised: Materialise
) -> None:
    """A relationship the path takes twice reads its record filter at both steps (§6.5 step 1,
    D381): a find whose zone is unknown, which the filter may keep or not, is UNKNOWN for its
    code at the first step as at the last, in the evaluator and in SQL."""
    rows = {
        "groups": GROUPS,
        "sites": [{"site_id": "s0", "region": "n"}],
        "visits": [{"visit_id": "v0", "site_id": "s0", "kind": "a"}],
        "finds": [{"find_id": "f0", "visit_id": "v0", "kind_id": "k0", "code": "x", "zone": None}],
    }
    finds = {"parents": GROUPED_SCOPES, "record_filter": {"zone": ["n", "e"]}}
    release = _survey(rows, {"finds": finds, "declared": True})
    asked = [{"column": "finds.code", "via": [DOWN, FIND, BACK, FIND], "each": "category"}]
    resolution = variables_of(doc([], unit="sites"), release, asked)
    found = _only(_agree(resolution, materialised))
    [variable] = _variables(resolution)
    assert variable.question is not None
    x = category_clause(variable.question, "x")
    assert reference.evaluate(variable).answer(0, "x") == Evaluator(
        ResolvedCohort("q", "", release, "sites", (), {}, variable.fields, variable.coverage)
    ).truth(x, "sites", 0)
    assert _counts(found)["x"] == (0, 0, 1)


def _resolved_on(
    release: Release, unit: str, variable: Mapping[str, Any], *, independent: bool = False
) -> Resolution:
    """A variable resolved on ``unit`` as a view's column would be, at
    ``/views/0/params/columns/0``."""
    written = {"aibi": "1", "dataset": "d", "unit": unit, "cohorts": {"c": {"all": []}}}
    loaded = load_document(json.dumps(written))
    assert loaded.document is not None
    given = ViewVariable(
        "0/0",
        "d",
        ("views", 0, "params", "columns", 0),
        Variable.model_validate(variable),
        independent=independent,
    )
    return resolve(loaded.document, {"d": release}, loaded.positions, variables=[given])


def test_a_filtered_column_lists_its_declared_allowed_values_in_their_order_then_the_others(
    materialised: Materialise,
) -> None:
    """The others in UTF-16 order (D380): a value past U+FFFF before one of the Basic
    Multilingual Plane above the surrogates, which code points order the other way, in the
    evaluator and in SQL."""
    orders = "rel:orders.customer"
    past, high = "t\U0001f600", "t\uffee"
    release = build.release(
        [
            build.dataset(),
            build.table("customers", ["customer_id"]),
            build.column("customers.customer_id", "string"),
            build.table("orders", ["order_id"], role="event"),
            build.column("orders.order_id", "string"),
            build.column("orders.customer_id", "string"),
            build.column(
                "orders.channel",
                "category",
                permissible_values={"values": [{"value": v} for v in ("web", "phone", "fax")]},
            ),
            build.relationship("orders", ["customer_id"], "customers", role="customer"),
            build.coverage(
                orders, "all", record_filter={"channel": [high, "phone", past, "web", "kiosk"]}
            ),
        ],
        {
            "customers": [{"customer_id": "k0"}, {"customer_id": "k1"}],
            "orders": [
                {"order_id": "o0", "customer_id": "k0", "channel": past},
                {"order_id": "o1", "customer_id": "k0", "channel": high},
                {"order_id": "o2", "customer_id": "k1", "channel": "phone"},
            ],
        },
        check=False,
    )
    resolution = _resolved_on(
        release, "customers", {"column": "orders.channel", "each": "category"}
    )
    assert resolution.refusals == []
    assert reference.fixed(resolution.variables["0/0"]) == ("web", "phone", "kiosk", past, high)
    found = _only(_agree(resolution, materialised))
    assert list(_counts(found)) == ["web", "phone", "kiosk", past, high]
    assert _counts(found)[past] == (1, 1, 0)


def test_an_undeclared_scope_value_is_true_where_held_false_where_listed_else_not_covered(
    city: City, doc: Doc, variables_of: Resolved, materialised: Materialise
) -> None:
    """A value the column does not declare is asked as §6.5 answers it (D380): TRUE where a row
    holds it, FALSE where the coverage lists it or covers every value, otherwise UNKNOWN
    (``NOT_COVERED``); it is listed where some unit's answer is not its default, as the coverage
    alone can make it (m4)."""
    rows = {
        "establishments": [{"establishment_id": f"e{n}"} for n in range(4)],
        "inspections": [
            {"inspection_id": f"i{n}", "establishment_id": f"e{n}", "kind": "routine"}
            for n in range(4)
        ],
        "inspection_checklists": [
            {"inspection_id": "i0", "checklist": "basic"},
            {"inspection_id": "i1", "checklist": "basic"},
            {"inspection_id": "i2", "checklist": "all"},
            {"inspection_id": "i3", "checklist": "temps"},
        ],
        "checklist_items": [
            {"checklist": "basic", "code": "temp", "all_codes": False},
            {"checklist": "basic", "code": "mold", "all_codes": False},
            {"checklist": "temps", "code": "temp", "all_codes": False},
            {"checklist": "all", "code": None, "all_codes": True},
        ],
        "violations": [{"violation_id": "v0", "inspection_id": "i0", "code": "mold"}],
    }
    asked = [{"column": "violations.code", "each": "category"}]
    held = _only(_agree(variables_of(doc([]), city(rows), asked), materialised))
    assert list(_counts(held)) == ["temp", "pest", "label", "mold"]
    assert _counts(held)["mold"] == (1, 2, 1)
    assert _unknown(held, "mold") == {"NOT_COVERED": 1}
    assert _counts(held)["pest"] == (0, 1, 3)
    listed = _only(
        _agree(variables_of(doc([]), city({**rows, "violations": []}), asked), materialised)
    )
    assert _counts(listed)["mold"] == (0, 3, 1)


def test_a_parent_its_coverage_does_not_list_is_not_covered_for_every_category(
    city: City, doc: Doc, variables_of: Resolved, materialised: Materialise
) -> None:
    """A unit's default is not its template's answer, whose value leaf admits no value and so is
    closed vacuously (M1): an inspection no checklist lists and with no violation is UNKNOWN
    (``NOT_COVERED``) for every category, in the evaluator and in SQL."""
    release = city(
        {
            "establishments": [{"establishment_id": "e0"}],
            "inspections": [{"inspection_id": "i0", "establishment_id": "e0", "kind": "routine"}],
        }
    )
    found = _only(
        _agree(
            variables_of(doc([]), release, [{"column": "violations.code", "each": "category"}]),
            materialised,
        )
    )
    assert _counts(found) == {"temp": (0, 0, 1), "pest": (0, 0, 1), "label": (0, 0, 1)}
    assert all(_unknown(found, code) == {"NOT_COVERED": 1} for code in _counts(found))
    assert (found.n, found.excluded[ExclusionReason.NOT_COVERED]) == (0, 1)


def test_with_no_category_listed_a_unit_counts_by_its_default_and_none_is_reclassified(
    city: City, doc: Doc, variables_of: Resolved, materialised: Materialise
) -> None:
    """Nothing listed (M2): a unit whose default is known, closed with no rows, counts as known
    and shows no category; one whose default is UNKNOWN is excluded under its reasons; none is
    ``NO_ROWS``."""
    release = city(
        {
            "establishments": [{"establishment_id": f"e{n}"} for n in range(3)],
            "complaints": [
                {"complaint_id": "c0", "establishment_id": "e1", "channel": "web", "topic": None},
                {"complaint_id": "c1", "establishment_id": "e2", "channel": "fax", "topic": "x"},
            ],
        },
        extra=[TOPIC],
    )
    found = _only(
        _agree(
            variables_of(doc([]), release, [{"column": "complaints.topic", "each": "category"}]),
            materialised,
        )
    )
    assert found.memberships.categories == ()
    assert (found.n, found.excluded_units) == (2, 1)
    assert {r.value: n for r, n in found.excluded.items() if n} == {"NO_INFORMATION": 1}


def test_a_value_held_only_under_an_unknown_filter_is_listed_with_its_units_unknown(
    city: City, doc: Doc, variables_of: Resolved, materialised: Materialise
) -> None:
    """A category is listed where some unit's answer is not its default (m4): a complaint of a
    topic on no known channel makes its unit UNKNOWN for the topic, while its default is FALSE,
    the filter's FALSE deciding a row that holds another."""
    release = city(
        {
            "establishments": [{"establishment_id": f"e{n}"} for n in range(2)],
            "complaints": [
                {"complaint_id": "c0", "establishment_id": "e0", "channel": None, "topic": "noise"}
            ],
        },
        extra=[TOPIC],
    )
    found = _only(
        _agree(
            variables_of(doc([]), release, [{"column": "complaints.topic", "each": "category"}]),
            materialised,
        )
    )
    assert _counts(found) == {"noise": (0, 1, 1)}
    assert _unknown(found, "noise") == {"NO_INFORMATION": 1}


def test_a_unit_whose_unknown_answers_are_all_the_cohort_lists_is_excluded_and_else_known(
    city: City, doc: Doc, variables_of: Resolved, materialised: Materialise
) -> None:
    """``analysed`` depends on the categories a cohort lists (m1, D382): a unit whose one
    complaint, of a topic, is on no known channel is UNKNOWN for that topic and FALSE, its
    default, for every other; in a cohort of it alone that topic is all that is listed, so it is
    excluded, and beside a unit with a complaint of another topic it is known, FALSE for that
    one, in the evaluator and in SQL, with the joint counts beside another variable."""
    release = city(
        {
            "establishments": [
                {"establishment_id": "e0", "chain": True},
                {"establishment_id": "e1", "chain": False},
            ],
            "complaints": [
                {"complaint_id": "c0", "establishment_id": "e0", "channel": None, "topic": "noise"},
                {"complaint_id": "c1", "establishment_id": "e1", "channel": "web", "topic": "dust"},
            ],
        },
        extra=[TOPIC],
    )
    chained = {"kind": "value", "column": "establishments.chain", "values": [True]}
    topics = {"column": "complaints.topic", "each": "category"}
    written = doc({"alone": [chained], "both": []})
    (alone, _), (both, _) = _agree(variables_of(written, release, [topics]), materialised)
    [read] = alone
    assert _counts(read) == {"noise": (0, 0, 1)}
    assert (read.n, read.excluded_units) == (0, 1)
    assert {r.value: n for r, n in read.excluded.items() if n} == {"NO_INFORMATION": 1}
    [read] = both
    assert _counts(read) == {"dust": (1, 1, 0), "noise": (0, 1, 1)}
    assert (read.n, read.excluded_units) == (2, 0)
    beside = variables_of(written, release, [topics, {"column": "establishments.chain"}])
    (_, lone), (_, pair) = _agree(beside, materialised)
    assert (lone.known, lone.none) == (1, 0)
    assert (pair.known, pair.none) == (2, 0)


def test_a_list_s_repeated_item_makes_its_unit_a_member_once_and_an_unknown_item_unknown(
    city: City, doc: Doc, variables_of: Resolved, materialised: Materialise
) -> None:
    """A list's categories are its PRESENT items, each once (m3), ``any`` of them: an item not
    assessed leaves every other category UNKNOWN, an empty list is FALSE for all, and a list not
    recorded UNKNOWN; the categories are the items, in UTF-16 order, the list declaring none."""
    release = city(
        {
            "establishments": [
                {"establishment_id": "e0", "tags": ["vegan", "vegan"]},
                {"establishment_id": "e1", "tags": ["halal", "?"]},
                {"establishment_id": "e2", "tags": []},
                {"establishment_id": "e3", "tags": None},
            ],
        }
    )
    found = _only(
        _agree(
            variables_of(doc([]), release, [{"column": "establishments.tags", "each": "category"}]),
            materialised,
        )
    )
    assert _counts(found) == {"halal": (1, 2, 1), "vegan": (1, 1, 2)}
    assert _unknown(found, "vegan") == {"NOT_ASSESSED": 1, "NO_INFORMATION": 1}


def test_over_150_categories_nothing_is_counted_and_the_listing_says_so(
    city: City, doc: Doc, variables_of: Resolved, materialised: Materialise
) -> None:
    """More than ``MAX_CATEGORIES`` categories listed (D380): the listing is over, and neither
    engine counts a category or a unit."""
    release = city(
        {
            "establishments": [{"establishment_id": "e0"}],
            "complaints": [
                {
                    "complaint_id": f"c{n}",
                    "establishment_id": "e0",
                    "channel": "web",
                    "topic": f"t{n:03}",
                }
                for n in range(MAX_CATEGORIES + 1)
            ],
        },
        extra=[TOPIC],
    )
    found = _only(
        _agree(
            variables_of(doc([]), release, [{"column": "complaints.topic", "each": "category"}]),
            materialised,
        )
    )
    assert found.memberships == Memberships((), 0, True)
    assert (found.n, found.excluded_units, found.marks) == (0, 0, frozenset())
    exactly = city(
        {
            "establishments": [{"establishment_id": "e0"}],
            "complaints": [
                {
                    "complaint_id": f"c{n}",
                    "establishment_id": "e0",
                    "channel": "web",
                    "topic": f"t{n:03}",
                }
                for n in range(MAX_CATEGORIES)
            ],
        },
        extra=[TOPIC],
    )
    cut = _only(
        _agree(
            variables_of(doc([]), exactly, [{"column": "complaints.topic", "each": "category"}]),
            materialised,
        )
    )
    assert len(cut.memberships.categories) == MAX_CATEGORIES


def test_past_150_categories_no_joint_count_is_given_beside_another_variable(
    city: City, doc: Doc, variables_of: Resolved, materialised: Materialise
) -> None:
    """Past ``MAX_CATEGORIES`` (D380) a unit's memberships would be over the categories SQL's
    cap kept, not over all of them, so neither engine gives their units or a joint count, and
    the other variable is counted still."""
    release = city(
        {
            "establishments": [
                {"establishment_id": "e0", "grade": "A"},
                {"establishment_id": "e1", "grade": None},
            ],
            "complaints": [
                {
                    "complaint_id": f"c{n}",
                    "establishment_id": "e0",
                    "channel": "web",
                    "topic": f"t{n:03}",
                }
                for n in range(MAX_CATEGORIES + 1)
            ],
        },
        extra=[TOPIC],
    )
    asked = [{"column": "complaints.topic", "each": "category"}, {"column": "establishments.grade"}]
    resolution = variables_of(doc([]), release, asked)
    [((topics, grades), together)] = _agree(resolution, materialised)
    assert topics.memberships.over
    assert together is None
    assert (grades.n, grades.excluded_units) == (1, 1)
    [topic, _] = _variables(resolution)
    [cohort] = resolution.cohorts.values()
    assert reference.membership_units(reference.evaluate(topic), evaluate(cohort).members) is None


def _tildes(where: str) -> dict[str, Any]:
    """Rows whose longest string is twelve tildes, held in a cell, a list's item or a
    coverage table's scope column, and the memberships of that column."""
    tildes = "~" * 12
    places: list[dict[str, Any]] = [{"establishment_id": "e0"}, {"establishment_id": "e1"}]
    if where == "a cell":
        complaints = [
            {"complaint_id": "c0", "establishment_id": "e0", "channel": "web", "topic": tildes}
        ]
        return {
            "rows": {"establishments": places, "complaints": complaints},
            "asked": {"column": "complaints.topic", "each": "category"},
        }
    if where == "an item":
        places[0]["tags"] = [tildes, "vegan"]
        return {
            "rows": {"establishments": places},
            "asked": {"column": "establishments.tags", "each": "category"},
        }
    return {
        "rows": {
            "establishments": places,
            "inspections": [
                {"inspection_id": "i0", "establishment_id": "e0", "kind": "routine"},
                {"inspection_id": "i1", "establishment_id": "e1", "kind": "routine"},
            ],
            "inspection_checklists": [
                {"inspection_id": "i0", "checklist": "basic"},
                {"inspection_id": "i1", "checklist": "all"},
            ],
            "checklist_items": [
                {"checklist": "basic", "code": tildes, "all_codes": False},
                {"checklist": "all", "code": None, "all_codes": True},
            ],
        },
        "asked": {"column": "violations.code", "each": "category"},
    }


@pytest.mark.parametrize("where", ["a cell", "an item", "a coverage table"])
def test_the_default_is_asked_of_a_string_no_cell_item_or_coverage_table_holds(
    city: City, doc: Doc, variables_of: Resolved, materialised: Materialise, where: str
) -> None:
    """The default is Q_c of a string no cell holds, nor an item nor a coverage table's value
    (D381): longer than the longest held, twelve tildes here, so that a unit that holds that
    string, or whose coverage lists it, is answered for it as for any other and it is listed,
    in the evaluator as in SQL, which asks no such string."""
    given = _tildes(where)
    release = city(given["rows"], extra=[TOPIC])
    fresh = reference._fresh(release)  # pyright: ignore[reportPrivateUsage]
    held: set[str] = set()
    for table in release.table_ids:
        rows = release.rows(table)
        for column in release.columns(table):
            for row in range(len(rows)):
                value = rows.cell(row, column).value
                listed = value if isinstance(value, tuple) else ()
                held |= {
                    one for one in (value, *(item.value for item in listed)) if isinstance(one, str)
                }
    assert "~" * 12 in held
    assert fresh not in held
    found = _only(_agree(variables_of(doc([]), release, [given["asked"]]), materialised))
    assert "~" * 12 in _counts(found)


# --- The canonical form and leaf keys (§7.6) ----------------------------------------------------


def _predicate_key(run: Callable[..., Any], doc: Doc, release: Release, clause: Any) -> str:
    result = run(doc([clause]), release)
    assert result.refusals == []
    [tree] = result.resolution.cohorts["c"].clauses
    return leaf_key(canonical_clause(tree))


@pytest.mark.parametrize("lift", [{}, {"lift": "assessed"}])
def test_a_declared_category_s_key_is_compare_existence_s_however_the_path_is_written(
    run: Callable[..., Any], city: City, doc: Doc, variables_of: Resolved, lift: dict[str, Any]
) -> None:
    """A category's leaf key (``category_key``, m7) is that of ``compare.existence``'s
    predicate ``some`` row with that value, over the variable's path and lift, written directly,
    with its path, as a two-step ``exists`` or as nested ``exists`` leaves (§6.1); and the
    variable's form, written with or without its path, is one."""
    release = city()
    path = [{"rel": INSPECTED, "dir": "down"}, {"rel": VIOLATIONS, "dir": "down"}]
    temp = _value("violations.code", values=["temp"])
    spellings = [
        {**temp, **lift},
        {**temp, "via": path, "quantifier": "some", **lift},
        {"kind": "exists", "table": "violations", "via": path, "where": [temp], **lift},
        {
            "kind": "exists",
            "table": "inspections",
            "where": [{"kind": "exists", "table": "violations", "where": [temp]}],
            **lift,
        },
    ]
    keys = {_predicate_key(run, doc, release, clause) for clause in spellings}
    resolution = variables_of(
        doc([]),
        release,
        [
            {"column": "violations.code", "each": "category", **lift},
            {"column": "violations.code", "via": path, "each": "category", **lift},
        ],
    )
    assert resolution.refusals == []
    direct, written = _variables(resolution)
    assert variable_form(direct) == variable_form(written)
    assert direct.question is not None
    assert keys == {category_key(direct.question, "temp")}
    assert category_key(direct.question, "temp") == leaf_key(
        canonical_clause(category_clause(direct.question, "temp"))
    )


def test_a_list_s_and_a_looked_up_column_s_category_keys_are_their_predicates(
    run: Callable[..., Any], city: City, doc: Doc, variables_of: Resolved
) -> None:
    release = city()
    looked = [{"rel": INSPECTED, "dir": "down"}, {"rel": INSPECTED, "dir": "up"}]
    resolution = variables_of(
        doc([]),
        release,
        [
            {"column": "establishments.tags", "each": "category"},
            {"column": "establishments.grade", "via": looked, "each": "category"},
        ],
    )
    tags, grade = _variables(resolution)
    assert tags.question is not None
    assert grade.question is not None
    listed = _value("establishments.tags", values=["vegan"], match="any")
    graded = _value("establishments.grade", values=["B"], via=looked)
    assert _predicate_key(run, doc, release, listed) == category_key(tags.question, "vegan")
    assert _predicate_key(run, doc, release, graded) == category_key(grade.question, "B")


def test_the_memberships_form_is_its_template_with_no_value_which_no_other_form_holds(
    city: City, doc: Doc, variables_of: Resolved
) -> None:
    """The canonical form (§7.6, n4): ``{"each": "category", "question"}``, its template's value
    leaf ``"values": []``, a variable form's and never a clause's, and a template of no value is
    what ``category_clause`` gives a value to."""
    resolution = variables_of(
        doc([]),
        city(),
        [
            {"column": "violations.code", "each": "category"},
            {"column": "violations.code", "aggregate": "some", "values": ["temp"]},
        ],
    )
    memberships, some = _variables(resolution)
    form = variable_form(memberships)
    assert set(form) == {"each", "question"}
    assert form["each"] == "category"
    assert '"values":[]' in json.dumps(form["question"], separators=(",", ":"))
    assert set(form) != set(variable_form(some))
    assert some.question is not None
    with pytest.raises(ValueError, match="no value"):
        category_clause(some.question, "pest")


def test_a_memberships_written_position_maps_to_no_leaf_key_having_no_where(city: City) -> None:
    """The form names no category (§7.6, D380): a variable's leaves are those of its ``where``
    as written, and memberships take none, so their canonical variable maps no position to a
    leaf key, as one with a ``where`` does."""
    release = city()
    written = {"aibi": "1", "dataset": "d", "unit": "establishments", "cohorts": {"c": {"all": []}}}
    loaded = load_document(json.dumps(written))
    assert loaded.document is not None
    asked = [
        {"column": "violations.code", "each": "category"},
        {
            "column": "violations.code",
            "aggregate": "count",
            "where": [_value("violations.code", values=["temp"])],
        },
    ]
    given = [
        ViewVariable(
            f"0/{index}",
            "d",
            ("views", 0, "params", "columns", index),
            Variable.model_validate(variable),
        )
        for index, variable in enumerate(asked)
    ]
    found = canonicalise(
        loaded.document,
        {"d": release},
        labels={release.manifest: 1},
        positions=loaded.positions,
        variables=given,
    )
    assert found.refusals == []
    memberships, counted = found.variables["0/0"], found.variables["0/1"]
    assert memberships.resolved.kind == "memberships"
    assert (memberships.resolved.leaves, memberships.leaves) == (frozenset(), {})
    assert list(counted.leaves) == ["/views/0/params/columns/1/where/0"]


def test_memberships_read_the_fields_and_coverage_a_some_of_one_value_reads(
    city: City, doc: Doc, variables_of: Resolved
) -> None:
    """The template repeats ``_value``'s checks (m1): it reads the fields and coverage that
    ``some`` of one of its values reads, a list's lookups with no down step included."""
    looked = {"via": [{"rel": INSPECTED, "dir": "up"}]}
    for unit, column, value, via in (
        ("establishments", "violations.code", "temp", {}),
        ("establishments", "establishments.tags", "vegan", {}),
        ("establishments", "complaints.channel", "phone", {}),
        ("inspections", "establishments.tags", "vegan", looked),
    ):
        resolution = variables_of(
            doc([], unit=unit),
            city(),
            [
                {"column": column, "each": "category", **via},
                {"column": column, "aggregate": "some", "values": [value], **via},
            ],
        )
        assert resolution.refusals == []
        memberships, some = _variables(resolution)
        assert (memberships.fields, memberships.coverage) == (some.fields, some.coverage)


# --- Refusals -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "given",
    [
        {"aggregate": "count"},
        {"aggregate": "some", "values": ["temp"]},
        {"aggregate": "max", "empty": 0},
        {"count": "rows"},
        {"where": [_value("violations.code", values=["temp"])]},
    ],
    ids=["an aggregate", "values", "empty", "a count of rows", "where"],
)
def test_memberships_take_no_aggregate_values_empty_count_or_where(given: dict[str, Any]) -> None:
    with pytest.raises(ValidationError) as raised:
        Variable.model_validate({"column": "violations.code", "each": "category", **given})
    assert "conflicting_members" in {error["type"] for error in raised.value.errors()}


@pytest.mark.parametrize(
    ("column", "code", "member"),
    [
        ("establishments.grade", "INVALID_VALUE", "each"),
        ("inspections.score", "INVALID_VALUE", "each"),
        ("violations.severity", "INVALID_VALUE", "each"),
        ("establishments.notes", "UNDECLARED_DATATYPE", "column"),
    ],
    ids=["one value per unit", "numbers", "numbers below the unit", "an undeclared datatype"],
)
def test_memberships_of_what_has_no_categories_or_one_per_unit_are_refused_where_written(
    city: City, doc: Doc, variables_of: Resolved, column: str, code: str, member: str
) -> None:
    resolution = variables_of(doc([]), city(), [{"column": column, "each": "category"}])
    [refusal] = resolution.refusals
    assert (refusal.code, refusal.path) == (code, f"/views/0/params/columns/0/{member}")


def test_memberships_read_no_identifier_where_row_ids_are_not_allowed(
    city: City, doc: Doc, variables_of: Resolved
) -> None:
    written = {"column": "inspections.inspection_id", "each": "category"}
    resolution = variables_of(doc([]), city(allow_row_ids=False), [written])
    [refusal] = resolution.refusals
    assert (refusal.code, refusal.path) == (
        "ROW_IDS_NOT_ALLOWED",
        "/views/0/params/columns/0/column",
    )


@pytest.mark.parametrize(
    ("column", "offered"),
    [
        ("violations.code", ["some"]),
        ("violations.area", ["some", "every"]),
        ("complaints.channel", ["count", "some", "every"]),
    ],
)
def test_an_analysis_of_one_value_per_unit_takes_no_memberships(
    city: City, column: str, offered: list[str]
) -> None:
    """An analysis that compares units refuses memberships (D380), offering the aggregates the
    column's path allows (``resolve.aggregates_over``): below the violations' coverage, scoped
    by code, no aggregate that pools rows, and no ``every`` of the code itself."""
    resolution = _resolved_on(
        city(extra=[TOPIC, AREA]),
        "establishments",
        {"column": column, "each": "category"},
        independent=True,
    )
    [refusal] = resolution.refusals
    assert (refusal.code, refusal.path) == ("INVALID_VALUE", "/views/0/params/columns/0/each")
    alternatives = [one.model_dump().get("text") for one in refusal.alternatives or []]
    assert alternatives == offered


def test_memberships_are_never_listed_per_member(
    city: City, doc: Doc, variables_of: Resolved, blobs: Callable[..., Any]
) -> None:
    release = city()
    resolution = variables_of(doc([]), release, [{"column": "violations.code", "each": "category"}])
    with pytest.raises(CompileError, match="never listed"):
        compile_inputs(list(resolution.cohorts.values()), _variables(resolution), blobs(release))


# --- The listing's order across threads (§9.3, m5) ----------------------------------------------

_THREADED = """
import pickle, sys
from aibi.core.engine import duck
paths, threads, statements = pickle.loads(sys.stdin.buffer.read())
session = duck.Session(tuple(paths), 1 << 30, threads)
found = duck.run(session, [duck.Statement(*one) for one in statements])
sys.stdout.buffer.write(pickle.dumps([(result.columns, result.rows) for result in found]))
"""
"""A helper process's DuckDB session on ``threads`` threads: DuckDB never runs in the test
process (``conftest``)."""


def _threaded(compiled: Any, threads: int, packed: Callable[..., Any]) -> Any:
    request = pickle.dumps(
        (
            list(compiled.paths),
            threads,
            [(statement, dict(compiled.parameters)) for statement in compiled.statements],
        )
    )
    done = subprocess.run(
        [sys.executable, "-c", _THREADED], input=request, capture_output=True, check=True
    )
    answers = pickle.loads(done.stdout)
    return compiled.read(
        [
            packed(columns, rows, values)
            for (columns, rows), values in zip(answers, compiled.values, strict=True)
        ]
    )


@pytest.mark.parametrize(
    "topics",
    [
        MAX_CATEGORIES - 3,
        MAX_CATEGORIES,
        MAX_CATEGORIES + 1,
        MAX_CATEGORIES + 2,
        MAX_CATEGORIES + 7,
    ],
)
def test_the_listing_and_its_counts_are_the_same_on_one_thread_or_four(
    city: City,
    doc: Doc,
    variables_of: Resolved,
    blobs: Callable[..., Any],
    packed_rows: Callable[..., Any],
    topics: int,
) -> None:
    """The categories are listed, and capped, in no order before any unit's pairs are counted
    (D381), and the server orders the undeclared ones in UTF-16 order, whatever order DuckDB
    gives rows in: over tables of many row groups, which DuckDB scans on several threads, the
    listing and its counts are one on 1, 2 and 4 threads, and the evaluator's, a value past
    U+FFFF before one below it, as UTF-16 orders them: below the cap, at it, and one, two and
    seven past it, where nothing is counted."""
    names = [f"t{n:03}" for n in range(topics - 2)] + ["t￮", "t\U0001f600"]
    complaints = [
        {
            "complaint_id": f"c{n}",
            "establishment_id": f"e{n % 9}",
            "channel": ("web", "phone", None)[n % 3],
            "topic": name,
        }
        for n, name in enumerate(names * 2)
    ]
    release = city(
        {
            "establishments": [{"establishment_id": f"e{n}"} for n in range(10)],
            "complaints": complaints,
        },
        extra=[TOPIC],
    )
    resolution = variables_of(
        doc({"c0": [], "c1": [_value("establishments.establishment_id", values=["e1", "e2"])]}),
        release,
        [{"column": "complaints.topic", "each": "category"}],
    )
    assert resolution.refusals == []
    cohorts = list(resolution.cohorts.values())
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(parquet, "ROW_GROUP", 16)
        sources = blobs(release)
    compiled = compile_materialised(cohorts, _variables(resolution), sources)
    found = {threads: _threaded(compiled, threads, packed_rows) for threads in (1, 2, 4)}
    expected = tuple(
        (tuple(read), together) for read, together in _expected(resolution, _variables(resolution))
    )
    assert found[1] == found[2] == found[4] == expected
    [(first, _), _] = expected
    if topics <= MAX_CATEGORIES:
        listed = [one.category for one in first[0].memberships.categories]
        assert listed.index("t\U0001f600") < listed.index("t￮")
    else:
        assert first[0].memberships.over


_STATES = {"PRESENT", "NOT_APPLICABLE", "NOT_ASSESSED", ""}


def test_the_memberships_sql_binds_every_constant_and_counts_with_integers_alone(
    city: City, doc: Doc, variables_of: Resolved, blobs: Callable[..., Any]
) -> None:
    """No value of a document or descriptor is written into the SQL, a category included, and
    every aggregate is a ``COUNT``, a ``BIT_OR`` or ``BOOL_OR`` (of a unit's own pairs, a list's
    items, a combinator's operands), or the ``MIN`` or ``MAX`` of integer codes and row numbers:
    the tallies are ``COUNT``s, no ``BIT_OR`` is subtracted, and nothing is summed (D294, m2)."""
    release = city(extra=[TOPIC])
    resolution = variables_of(
        doc([]),
        release,
        [
            {"column": "violations.code", "each": "category"},
            {"column": "complaints.channel", "each": "category"},
            {"column": "establishments.tags", "each": "category"},
            {"column": "readings.appliance", "each": "category", "lift": "assessed"},
        ],
    )
    compiled = compile_materialised(
        list(resolution.cohorts.values()), _variables(resolution), blobs(release)
    )
    for statement in compiled.statements:
        tree = parse_one(statement, dialect="duckdb")
        literals = {node.this for node in tree.find_all(exp.Literal) if node.is_string}
        assert literals <= _STATES
        for constant in ("temp", "pest", "label", "phone", "web", "fridge"):
            assert f"'{constant}'" not in statement
        found = {type(node).__name__.upper() for node in tree.find_all(exp.AggFunc)}
        assert found <= {"COUNT", "BITWISEORAGG", "LOGICALOR", "MAX", "MIN"}, found
    bound = [
        item for value in compiled.parameters.values() if isinstance(value, tuple) for item in value
    ]
    assert {"temp", "pest", "label", "phone", "web"} <= set(bound)

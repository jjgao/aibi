"""Properties of the three-valued logic and of resolution (§13.4), on random city releases and
random clauses: every coverage form, proposed coverage, parent scopes that are TRUE, FALSE and
UNKNOWN, missing codes, list items, and null and dangling keys."""

import random
from collections.abc import Callable
from typing import Any

from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

from aibi.core.engine.data import Release
from aibi.core.engine.resolved import RAll, document
from aibi.core.engine.truth import TruthValue, all_of, not_

City = Callable[..., Release]
Doc = Callable[..., dict[str, Any]]
Runner = Callable[..., Any]

OWNER = "rel:establishments.owner"
INSPECTED = "rel:inspections.establishment"
LICENCES = "rel:licences.establishment"
LICENCE_TYPE = "rel:licences.type"

EXAMPLES = settings(
    max_examples=120,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)

VIOLATIONS_SCOPE = {
    "kind": "value",
    "column": "inspections.kind",
    "values": ["routine", "follow_up"],
}
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
VIOLATION_COVERAGE = [
    {"parents": GROUPED, "parent_scope": VIOLATIONS_SCOPE},
    {"parents": GROUPED, "parent_scope": VIOLATIONS_SCOPE, "statuses": {"parents": "proposed"}},
    {"parents": "all", "parent_scope": VIOLATIONS_SCOPE},
    {"parents": "all"},
    {"parent_scope": VIOLATIONS_SCOPE},  # parents undeclared
]
INSPECTION_COVERAGE = [
    {"parents": "all"},
    {"parents": "all", "statuses": {"parents": "proposed"}},
    {},
]


# --- Releases -------------------------------------------------------------------------------------


@st.composite
def city_data(draw: st.DrawFn) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    """Rows for a small city, and the coverage options of its descriptors."""
    pick = lambda options: draw(st.sampled_from(options))  # noqa: E731
    count = draw(st.integers(0, 4))
    rows: dict[str, list[dict[str, Any]]] = {
        "owners": [{"owner_id": "o0", "region": "north"}, {"owner_id": "o1", "region": None}],
        "licence_types": [{"type_id": "t1", "tier": 1}, {"type_id": "t3", "tier": 3}],
        "checklist_items": [
            {"checklist": "basic", "code": "temp", "all_codes": False},
            {"checklist": "basic", "code": "pest", "all_codes": False},
            {"checklist": "temps", "code": "temp", "all_codes": False},
            {"checklist": "all", "code": "temp", "all_codes": True},
        ],
    }
    for name in ("establishments", "inspections", "inspection_checklists", "violations"):
        rows[name] = []
    for name in ("complaints", "staff", "licences", "readings", "checked_appliances"):
        rows[name] = []
    for index in range(count):
        place = f"e{index}"
        rows["establishments"].append(
            {
                "establishment_id": place,
                "grade": pick(["A", "B", "C", "D", "pending", "exempt", None]),
                "cuisine": pick(["thai", "pizza", None]),
                "seats": pick([None, 0, 10, 40]),
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
                    "establishment_id": pick([place, place, place, None]),
                    "kind": pick(["routine", "follow_up", "courtesy", None]),
                    "score": pick([None, 40, 90]),
                }
            )
            checklist = pick([None, "basic", "temps", "all"])
            if checklist is not None:
                rows["inspection_checklists"].append(
                    {"inspection_id": inspection, "checklist": checklist}
                )
            for _ in range(draw(st.integers(0, 2))):
                rows["violations"].append(
                    {
                        "violation_id": f"v{len(rows['violations'])}",
                        "inspection_id": inspection,
                        "code": pick(["temp", "pest", "label"]),
                        "severity": pick([None, 1, 5, "pending", "n/a"]),
                    }
                )
            for appliance in draw(st.sets(st.sampled_from(["fridge", "freezer"]))):
                rows["checked_appliances"].append(
                    {"inspection_id": inspection, "appliance": appliance}
                )
            if draw(st.booleans()):
                rows["readings"].append(
                    {
                        "reading_id": f"r{len(rows['readings'])}",
                        "inspection_id": inspection,
                        "appliance": pick(["fridge", "freezer"]),
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


# --- Clauses --------------------------------------------------------------------------------------


def _value(column: str, **predicate: Any) -> dict[str, Any]:
    return {"kind": "value", "column": column, **predicate}


def _subset(draw: st.DrawFn, options: list[str]) -> list[str]:
    return draw(st.lists(st.sampled_from(options), min_size=1, max_size=len(options), unique=True))


@st.composite
def leaves(draw: st.DrawFn) -> dict[str, Any]:
    """A leaf a document on establishments may hold, never refused."""
    pick = lambda options: draw(st.sampled_from(options))  # noqa: E731
    lift = pick([None, "strict", "assessed"])
    lifted = {} if lift is None else {"lift": lift}
    choice = draw(st.integers(0, 16))
    if choice == 0:
        return _value(
            "establishments.grade",
            values=_subset(draw, ["A", "B", "C"]),
            negate=draw(st.booleans()),
        )
    if choice == 1:
        bound = pick(["gt", "gte", "lt", "lte"])
        return _value("establishments.grade", range={bound: pick(["A", "B", "C"])})
    if choice == 2:
        return _value(
            "establishments.seats", op=pick(["=", "!=", ">", "<="]), value=pick([0, 10, 40])
        )
    if choice == 3:
        match = pick([None, "any", "all"])
        extra = {} if match is None else {"match": match}
        return _value(
            "establishments.tags",
            values=_subset(draw, ["vegan", "halal"]),
            negate=draw(st.booleans()),
            **extra,
        )
    if choice == 4:
        return _value("owners.region", values=["north"])
    if choice == 5:
        return {
            "kind": "ids",
            "ids": [f"d:e{index}" for index in draw(st.sets(st.integers(0, 4), min_size=1))],
        }
    if choice == 6:
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
    if choice == 7:
        inner: list[Any] = [_value("violations.severity", range={"gte": 3})]
        if draw(st.booleans()):
            inner.append(_value("violations.code", values=_subset(draw, ["temp", "pest"])))
        outer: list[Any] = [{"kind": "exists", "table": "violations", "where": inner}]
        if draw(st.booleans()):
            outer.append(_value("inspections.kind", values=["routine"]))
        return {"kind": "exists", "table": "inspections", "where": outer, **lifted}
    if choice == 8:
        quantifier = pick([{}, {"quantifier": "some"}, {"quantifier": ["every", "some"]}])
        return _value(
            "violations.code",
            values=_subset(draw, ["temp", "pest", "label"]),
            **quantifier,
            **lifted,
        )
    if choice == 9:
        quantifier = pick([{}, {"quantifier": "every"}, {"quantifier": ["some", "every"]}])
        return _value("violations.severity", range={"lt": 3}, **quantifier, **lifted)
    if choice == 10:
        where: list[Any] = [_value("complaints.severity", range={"gte": 3})]
        quantifier = pick([{}, {"quantifier": "every"}])
        if not quantifier and draw(st.booleans()):
            where.append(_value("complaints.channel", values=_subset(draw, ["phone", "web"])))
        return {"kind": "exists", "table": "complaints", "where": where, **quantifier}
    if choice == 11:
        quantifier = pick([{}, {"quantifier": "every"}])
        return {
            "kind": "exists",
            "table": "staff",
            "where": [_value("staff.certified", values=[True])],
            **quantifier,
        }
    if choice == 12:
        return _value("licence_types.tier", values=[pick([1, 3])])
    if choice == 13:
        # The other establishments of the same owner.
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
    if choice == 14:
        # An inspection with another of the same establishment, in a where.
        others = {
            "kind": "exists",
            "table": "inspections",
            "via": [{"rel": INSPECTED, "dir": "up"}, {"rel": INSPECTED, "dir": "down"}],
            "exclude_self": True,
            "where": [_value("inspections.score", range={"gte": 50})],
        }
        return {"kind": "exists", "table": "inspections", "where": [others], **lifted}
    if choice == 15:
        # Lookups after the last down step, made first in the where.
        return {
            "kind": "exists",
            "table": "licence_types",
            "via": [{"rel": LICENCES, "dir": "down"}, {"rel": LICENCE_TYPE, "dir": "up"}],
            "where": [_value("licence_types.tier", values=[pick([1, 3])])],
        }
    scope = pick([{}, {"scope": {"code": _subset(draw, ["temp", "pest"])}}])
    return {"kind": "covered", "table": "violations", **scope, **lifted}


def clauses(depth: int = 3) -> st.SearchStrategy[Any]:
    return st.recursive(
        leaves(),
        lambda inner: st.one_of(
            st.lists(inner, min_size=1, max_size=3).map(lambda members: {"all": members}),
            st.lists(inner, min_size=1, max_size=3).map(lambda members: {"any": members}),
            inner.map(lambda member: {"not": member}),
            inner.map(lambda member: {"known": member}),
            inner.map(lambda member: {"unknown": member}),
        ),
        max_leaves=depth * 2,
    )


def _within_caps(result: Any) -> None:
    """Skip an example whose canonical form is deeper than a cohort's may be (§7.1): combinators
    nested around a question chain can pass the cap."""
    limits = {refusal.limit.name for refusal in result.resolution.refusals if refusal.limit}
    assume(not limits & {"clause_depth", "leaves_per_cohort"})


def _values(
    run: Runner, doc: Doc, release: Release, cohorts: dict[str, Any]
) -> dict[str, tuple[TruthValue, ...]]:
    result = run(doc({name: [clause] for name, clause in cohorts.items()}), release)
    _within_caps(result)
    assert result.refusals == [], result.refusals
    return {name: cohort.values for name, cohort in result.results.items()}


def _fit(clause: Any, options: dict[str, Any]) -> Any:
    """The clause with the scope of its covered leaves left out, unless the violations
    coverage has scope columns (a scope names them, and is refused otherwise)."""
    if options["violations"].get("parents") is GROUPED:
        return clause
    if isinstance(clause, dict):
        if clause.get("kind") == "covered":
            return {key: member for key, member in clause.items() if key != "scope"}
        return {key: _fit(member, options) for key, member in clause.items()}
    if isinstance(clause, list):
        return [_fit(item, options) for item in clause]
    return clause


# --- Properties -----------------------------------------------------------------------------------


@EXAMPLES
@given(data=city_data(), clause=clauses())
def test_the_logic_of_three_values(
    run: Runner, city: City, doc: Doc, data: Any, clause: Any
) -> None:
    """n_true + n_false + n_unknown is the unit count; not(not C) is C; C and not C share no
    unit; known(C) selects C and not C with C's flags; unknown(C) selects the rest."""
    rows, options = data
    release = city(rows, **options)
    clause = _fit(clause, options)
    found = _values(
        run,
        doc,
        release,
        {
            "c": clause,
            "not_c": {"not": clause},
            "not_not_c": {"not": {"not": clause}},
            "known_c": {"known": clause},
            "unknown_c": {"unknown": clause},
        },
    )
    result = run(doc([clause]), release).result
    assert result.n_true + result.n_false + result.n_unknown == len(rows["establishments"])
    assert found["not_not_c"] == found["c"]
    for c, not_c, known, unknown in zip(
        found["c"], found["not_c"], found["known_c"], found["unknown_c"], strict=True
    ):
        assert not (c.is_true and not_c.is_true)
        assert known.is_true == (c.is_true or not_c.is_true)
        assert known.marks == c.marks
        assert unknown.is_true == c.is_unknown
        assert not_(c) == not_c


@EXAMPLES
@given(data=city_data(), a=clauses(2), b=clauses(2))
def test_de_morgan_in_values_reasons_and_flags(
    run: Runner, city: City, doc: Doc, data: Any, a: Any, b: Any
) -> None:
    rows, options = data
    release = city(rows, **options)
    a, b = _fit(a, options), _fit(b, options)
    found = _values(
        run,
        doc,
        release,
        {
            "not_all": {"not": {"all": [a, b]}},
            "any_not": {"any": [{"not": a}, {"not": b}]},
            "not_any": {"not": {"any": [a, b]}},
            "all_not": {"all": [{"not": a}, {"not": b}]},
            "a": a,
            "b": b,
        },
    )
    assert found["not_all"] == found["any_not"]
    assert found["not_any"] == found["all_not"]
    # A cohort's clauses are its top-level all.
    both = run(doc([a, b]), release).result.values
    assert both == tuple(all_of(pair) for pair in zip(found["a"], found["b"], strict=True))


@EXAMPLES
@given(clause=clauses())
def test_resolving_the_resolved_form_again_changes_nothing(
    run: Runner, city: City, doc: Doc, clause: Any
) -> None:
    release = city()
    first = run(doc([clause]), release)
    _within_caps(first)
    assert first.refusals == []
    tree = first.resolution.cohorts["c"].tree
    members = tree.members if isinstance(tree, RAll) else (tree,)
    written = [_with_dataset(document(member), release.manifest) for member in members]
    again = run(doc(written), release)
    assert again.refusals == []
    assert again.resolution.cohorts["c"].tree == tree


def _with_dataset(value: Any, manifest: str) -> Any:
    """The form as a document writes it: unit keys name the dataset, not its manifest hash."""
    if isinstance(value, dict):
        if value.get("dataset") == manifest and "key" in value:
            return {**value, "dataset": "d"}
        return {key: _with_dataset(member, manifest) for key, member in value.items()}
    if isinstance(value, list):
        return [_with_dataset(item, manifest) for item in value]
    return value


@EXAMPLES
@given(data=city_data(), clause=clauses(), seed=st.integers(0, 2**32 - 1))
def test_answers_do_not_depend_on_the_order_of_rows(
    run: Runner, city: City, doc: Doc, data: Any, clause: Any, seed: int
) -> None:
    rows, options = data
    clause = _fit(clause, options)
    shuffled = {name: list(table) for name, table in rows.items()}
    order = random.Random(seed)
    for name, table in shuffled.items():
        if name != "establishments":
            order.shuffle(table)
    ordered = run(doc([clause]), city(rows, **options))
    _within_caps(ordered)
    first = ordered.result.values
    second = run(doc([clause]), city(shuffled, **options)).result.values
    assert first == second


@EXAMPLES
@given(where=st.lists(st.sampled_from(["severity", "code", "both"]), min_size=1, max_size=2))
def test_the_direct_multi_step_and_nested_forms_resolve_to_one_tree(
    run: Runner, city: City, doc: Doc, where: list[str]
) -> None:
    leaf = {
        "severity": _value("violations.severity", range={"gte": 3}),
        "code": _value("violations.code", values=["pest"]),
        "both": {
            "all": [
                _value("violations.severity", values=[1]),
                _value("violations.code", values=["temp"]),
            ]
        },
    }
    conditions = [leaf[name] for name in where]
    forms = [
        {"kind": "exists", "table": "violations", "where": conditions},
        {
            "kind": "exists",
            "table": "inspections",
            "where": [{"kind": "exists", "table": "violations", "where": conditions}],
        },
    ]
    if len(conditions) == 1 and "all" not in conditions[0]:
        forms.append(conditions[0])  # the direct reference
    trees = []
    for form in forms:
        result = run(doc([form]), city())
        assert result.refusals == []
        trees.append(result.resolution.cohorts["c"].tree)
    assert all(tree == trees[0] for tree in trees)

"""The one-group path of grouped coverage held to the evaluator over a derived domain (SPEC
D449): in one release per variant, a parent for every way a parent can be assigned groups (none,
a null or dangling key, each group, each pair of groups in either order, threes, repeats, every
group), the groups made of every row a scope column of a type can hold (each physical type, a
missing code, null, a list, covers-all true, false and null) alone and in pairs, and the
questions §6.5 asks of them: ``some``, ``every``, ``min_count``, ``covered``, admitted sets
that intersect to nothing, two leaves of different admitted sets in one cohort. Variants: no
scope column, two scope columns, two coverages over one groups table and one assignment table,
two assignment tables over one groups table, group and parent keys of other and crossed types
(beyond 2^53 too), a covers-all column of another type, a membership, and a direct coverage.

Every release is inside the bound, so the engine's ``run`` holds the one-group path to the
evaluator (a membership is held to main's path, which the engine suite holds to the evaluator).
About eight minutes: CI's ``domain`` job runs it (``-m domain``)."""

import itertools
import random
from typing import Any

import pytest

from aibi.core.engine import build

pytestmark = pytest.mark.domain

TYPES: dict[str, tuple[str, Any, Any, Any, Any]] = {
    "string": ("category", "a", "b", "a", "b"),
    "int64": ("integer", 1, 2, 1, 2),
    "float64": ("number", 1.0, 2.5, 1, 2.5),
    "bool": ("boolean", True, False, True, False),
    "date32": ("date", "2020-01-01", "2020-01-02", "2020-01-01", "2020-01-02"),
    "timestamp": (
        "datetime",
        "2020-01-01T00:00:00+00:00",
        "2020-01-01T00:00:01+00:00",
        "2020-01-01T00:00:00Z",
        "2020-01-01T00:00:01Z",
    ),
    "strings": ("list<category>", ["a"], ["a", "b"], "a", "b"),
}
"""Each physical type of a scope column: its datatype, two values, and two constants."""
UNITS = {"integer": "1", "number": "1"}
KIDS = "rel:kids.parent"


def col(id: str, datatype: str, codes: bool = False) -> Any:
    extra: dict[str, Any] = {}
    if datatype in UNITS:
        extra["units"] = UNITS[datatype]
    if codes:
        extra["missing_codes"] = {"?": "NOT_ASSESSED"}
    return build.column(id, datatype, **extra)


def descriptors(
    kind: str,
    scope: list[str],
    covers: bool,
    *,
    two: bool = False,
    group_keys: tuple[str, str] = ("string", "string"),
    child: str | None = None,
    parent_keys: tuple[str, str] = ("string", "string"),
    covers_type: str = "boolean",
) -> list[Any]:
    datatype = TYPES[kind][0]
    child_datatype = "category" if kind == "strings" else TYPES[child or kind][0]
    groups: dict[str, Any] = {"table": "groups", "group_column": "grp"}
    if scope:
        groups["scope_columns"] = {c: c for c in scope}
    if covers:
        groups["covers_all_column"] = "all"
    assignment = {"table": "assign", "parent_columns": {"pid": "pid"}, "group_column": "grp"}
    found = [
        build.dataset(disclosure={"allow_row_ids": True}),
        build.table("parents", ["pid"]),
        col("parents.pid", parent_keys[0]),
        build.table("kids", ["kid"], role="event"),
        build.column("kids.kid", "string"),
        col("kids.pid", parent_keys[0]),
        col("kids.s", child_datatype),
        build.column("kids.t", "category"),
        build.column("kids.flag", "category"),
        build.relationship("kids", ["pid"], "parents", role="parent"),
        build.coverage(KIDS, {"assignment": assignment, "groups": groups}),
        build.table("assign", None, role="coverage"),
        col("assign.pid", parent_keys[1]),
        col("assign.grp", group_keys[0]),
        build.table("groups", None, role="coverage"),
        col("groups.grp", group_keys[1]),
        col("groups.s", datatype, codes=kind != "strings"),
        build.column("groups.t", "category"),
        col("groups.all", covers_type),
    ]
    if two:
        other: dict[str, Any] = {"table": "groups", "group_column": "grp"}
        if covers:
            other["scope_columns"] = {"t": "t"}
            other["covers_all_column"] = "all"
        found += [
            build.table("others", ["oid"], role="event"),
            build.column("others.oid", "string"),
            build.column("others.pid", "string"),
            build.column("others.t", "category"),
            build.column("others.flag", "category"),
            build.relationship("others", ["pid"], "parents", role="parent"),
            build.coverage("rel:others.parent", {"assignment": assignment, "groups": other}),
        ]
    return found


def _key(kind: str, index: int | None) -> Any:
    if index is None:
        return None
    return {"string": f"g{index}", "integer": index, "number": float(index)}[kind]


def release(
    kind: str,
    scope: list[str],
    covers: bool,
    *,
    two: bool = False,
    group_keys: tuple[str, str] = ("string", "string"),
    child: str | None = None,
    codes: bool = True,
    parent_keys: tuple[str, str] = ("string", "string"),
    covers_type: str = "boolean",
) -> Any:
    _, a, b, *_ = TYPES[kind]
    scope_values: list[Any] = [a, b, None] + (
        ["?"] if codes and kind != "strings" and "s" in scope else []
    )
    if "s" not in scope:
        scope_values = [a]
    second: list[Any] = ["x", None] if "t" in scope else ["x"]
    alls: list[Any] = [True, False, None] if covers else [False]
    if covers_type == "integer":
        alls = [1, 0, None]
    options = list(itertools.product(scope_values, second, alls))
    configs: list[tuple[Any, ...]] = [(), *((row,) for row in options)]
    configs += list(itertools.product(options, repeat=2))
    rows: dict[str, list[dict[str, Any]]] = {"parents": [], "kids": [], "assign": [], "groups": []}
    if two:
        rows["others"] = []
    for index, config in enumerate(configs):
        for s, t, every in config:
            rows["groups"].append({"grp": _key(group_keys[1], index), "s": s, "t": t, "all": every})
    rows["groups"].append(
        {
            "grp": None,
            "s": a,
            "t": "x",
            "all": (1 if covers else 0) if covers_type == "integer" else covers,
        }
    )
    count = len(configs)
    dangling = count + 7
    rng = random.Random(1)
    pairs = [(i, j) for i in range(count) for j in range(i + 1, count)]
    picks: list[tuple[Any, ...]] = [(), (None,), (dangling,)]
    picks += [(i,) for i in range(count)]
    picks += list(itertools.combinations_with_replacement(range(count), 2))
    picks += [(j, i) for i, j in rng.sample(pairs, min(len(pairs), 1500))]
    picks += [tuple(rng.choice(range(count)) for _ in range(3)) for _ in range(800)]
    picks += [(i, i, i) for i in rng.sample(range(count), min(count, 40))]
    picks += [(None, i) for i in rng.sample(range(count), min(count, 60))]
    picks += [(i, None) for i in rng.sample(range(count), min(count, 60))]
    picks += [(dangling, i) for i in rng.sample(range(count), min(count, 60))]
    picks += [(None, None), (dangling, dangling), (None, dangling)]
    picks += [tuple(range(count)), tuple(reversed(range(count)))]
    picks += [(1, 2)] * 5 + [(2, 1)] * 5 + [(2, 1, 2)] * 3
    kid = TYPES[child][1] if child else (a if kind != "strings" else "a")
    for parent, groups in enumerate(picks):
        pid: Any = {"string": f"p{parent}", "integer": parent, "number": float(parent)}
        rows["parents"].append({"pid": pid[parent_keys[0]]})
        for group in groups:
            rows["assign"].append({"pid": pid[parent_keys[1]], "grp": _key(group_keys[0], group)})
        rows["kids"].append(
            {"kid": f"k{parent}", "pid": pid[parent_keys[0]], "s": kid, "t": "x", "flag": "x"}
        )
        if two:
            rows["others"].append(
                {"oid": f"o{parent}", "pid": pid[parent_keys[0]], "t": "y", "flag": "x"}
            )
    found = descriptors(
        kind,
        scope,
        covers,
        two=two,
        group_keys=group_keys,
        child=child,
        parent_keys=parent_keys,
        covers_type=covers_type,
    )
    return build.release(found, rows)


def v(column: str, *values: Any, table: str = "kids") -> dict[str, Any]:
    return {"kind": "value", "column": f"{table}.{column}", "values": list(values)}


def ex(*where: Any, table: str = "kids", **extra: Any) -> dict[str, Any]:
    return {"kind": "exists", "table": table, "where": list(where), **extra}


def questions(kind: str, scope: list[str], two: bool, child: str | None = None) -> list[list[Any]]:
    _, _, _, ca, cb = TYPES[child or kind]
    found: list[list[Any]] = [
        [ex()],
        [ex(quantifier="every")],
        [ex(v("flag", "x"), min_count=2)],
        [{"kind": "covered", "table": "kids"}],
        [ex(), ex(quantifier="every")],
        [ex(v("flag", "x")), ex(v("flag", "x"), quantifier="every")],
    ]
    if "s" in scope:
        found += [
            [ex(v("s", ca))],
            [ex(v("s", ca, cb))],
            [ex(v("s", ca), min_count=2)],
            [{"not": ex(v("s", cb))}],
            [ex(v("s", ca), v("s", cb))],
            [{"kind": "covered", "table": "kids", "scope": {"s": [ca]}}],
            [{"kind": "covered", "table": "kids", "scope": {"s": [ca, cb]}}],
            [ex(v("s", ca)), {"not": ex(v("s", cb))}],
            [{"any": [ex(v("s", cb)), ex(v("s", ca, cb))]}],
            [
                {"kind": "covered", "table": "kids", "scope": {"s": [ca]}},
                {"not": {"kind": "covered", "table": "kids", "scope": {"s": [cb]}}},
            ],
        ]
    if scope == ["s", "t"]:
        found += [
            [ex(v("s", ca), v("t", "x"))],
            [ex(v("s", ca, cb), v("t", "x"))],
            [{"kind": "covered", "table": "kids", "scope": {"s": [ca], "t": ["x"]}}],
        ]
    if two:
        found += [
            [ex(table="others")],
            [ex(table="others", quantifier="every")],
            [ex(), ex(table="others", quantifier="every")],
        ]
    return found


def held(run: Any, made: Any, asked: list[list[Any]]) -> None:
    for at in range(0, len(asked), 6):
        part = asked[at : at + 6]
        written = {
            "aibi": "1",
            "dataset": "d",
            "unit": "parents",
            "cohorts": {f"c{i}": {"all": q} for i, q in enumerate(part)},
        }
        found = run(written, made)
        assert found.resolution.refusals == [], found.refusals
        assert len(found.resolution.cohorts) == len(part)


VARIANTS: list[tuple[str, tuple[str, ...], bool, bool]] = [
    (kind, ("s",), covers, False) for kind in TYPES for covers in (True, False)
]
VARIANTS += [
    ("string", (), False, False),
    ("string", ("s", "t"), True, False),
    ("string", ("s",), True, True),
]


@pytest.mark.parametrize(("kind", "scope", "covers", "two"), VARIANTS)
def test_the_domain(run: Any, kind: str, scope: tuple[str, ...], covers: bool, two: bool) -> None:
    held(run, release(kind, list(scope), covers, two=two), questions(kind, list(scope), two))


@pytest.mark.parametrize("kind", ["string", "bool", "strings"])
def test_a_membership_over_the_domain(
    variables_of: Any, materialised: Any, force: Any, kind: str
) -> None:
    made = release(kind, ["s"], True)
    written = {
        "aibi": "1",
        "dataset": "d",
        "unit": "parents",
        "cohorts": {"c0": {"all": []}},
    }
    resolution = variables_of(written, made, [{"column": "kids.s", "each": "category"}])
    assert resolution.refusals == [], resolution.refusals
    cohorts, variables = list(resolution.cohorts.values()), [resolution.variables["0/0"]]
    found = materialised(cohorts, variables)
    force("main")
    assert materialised(cohorts, variables) == found


@pytest.mark.parametrize(
    ("parent_keys", "covers_type"),
    [(("integer", "number"), "boolean"), (("string", "string"), "integer")],
)
def test_crossed_parent_keys_and_a_covers_all_column_of_another_type(
    run: Any, parent_keys: tuple[str, str], covers_type: str
) -> None:
    made = release("string", ["s"], True, parent_keys=parent_keys, covers_type=covers_type)
    held(run, made, questions("string", ["s"], False))


KEYS = [
    (("integer", "integer"), None),
    (("integer", "number"), None),
    (("number", "integer"), None),
    (("string", "string"), "int64"),
    (("string", "integer"), None),
]


@pytest.mark.parametrize(("group_keys", "child"), KEYS)
def test_group_keys_and_scope_columns_of_other_types(
    run: Any, group_keys: tuple[str, str], child: str | None
) -> None:
    kind = "float64" if child else "string"
    made = release(kind, ["s"], True, group_keys=group_keys, child=child, codes=False)
    held(run, made, questions(kind, ["s"], False, child=child))


@pytest.mark.parametrize(
    "kinds", [("integer", "number"), ("number", "integer"), ("string", "integer")]
)
def test_group_keys_beyond_two_to_the_53(run: Any, kinds: tuple[str, str]) -> None:
    def key(kind: str, value: int) -> Any:
        if kind == "string":
            return str(value)
        return float(value) if kind == "number" else value

    big = 2**53 + 1
    rows = {
        "parents": [{"pid": "p0"}, {"pid": "p1"}],
        "assign": [
            {"pid": "p0", "grp": key(kinds[0], big)},
            {"pid": "p1", "grp": key(kinds[0], 7)},
        ],
        "groups": [
            {"grp": key(kinds[1], big - 1), "s": "a", "t": "x", "all": False},
            {"grp": key(kinds[1], 7), "s": "a", "t": "x", "all": False},
        ],
        "kids": [],
    }
    made = build.release(descriptors("string", ["s"], False, group_keys=kinds), rows)
    held(
        run,
        made,
        [
            [ex()],
            [ex(quantifier="every")],
            [{"kind": "covered", "table": "kids"}],
            [ex(v("s", "a"))],
            [ex(v("flag", "x"))],
        ],
    )


@pytest.mark.parametrize(
    "kinds", [("integer", "integer"), ("number", "integer"), ("integer", "number")]
)
def test_group_keys_equal_as_doubles_are_two_groups(run: Any, kinds: tuple[str, str]) -> None:
    """2^53 and 2^53 + 1 are distinct keys (``key_part``) though equal as doubles: a parent on
    both is a parent of two groups."""

    def key(kind: str, value: int) -> Any:
        return float(value) if kind == "number" else value

    low, high = 2**53, 2**53 + 1
    rows = {
        "parents": [{"pid": f"p{i}"} for i in range(4)],
        "assign": [
            {"pid": "p0", "grp": key(kinds[0], high)},
            {"pid": "p1", "grp": key(kinds[0], low)},
            {"pid": "p2", "grp": key(kinds[0], low)},
            {"pid": "p2", "grp": key(kinds[0], high)},
            {"pid": "p3", "grp": key(kinds[0], 7)},
        ],
        "groups": [
            {"grp": key(kinds[1], low), "s": "a", "t": "x", "all": False},
            {"grp": key(kinds[1], high), "s": "b", "t": "x", "all": False},
            {"grp": key(kinds[1], 7), "s": "a", "t": "x", "all": True},
        ],
        "kids": [
            {"kid": f"k{i}", "pid": f"p{i}", "s": "a", "t": "x", "flag": "x"} for i in range(4)
        ],
    }
    made = build.release(descriptors("string", ["s"], True, group_keys=kinds), rows)
    held(
        run,
        made,
        [
            [ex()],
            [ex(quantifier="every")],
            [{"kind": "covered", "table": "kids"}],
            [ex(v("s", "a"))],
            [ex(v("s", "b"))],
            [ex(v("s", "a", "b"))],
            [{"kind": "covered", "table": "kids", "scope": {"s": ["b"]}}],
        ],
    )


def test_a_direct_coverage_over_the_domain_of_its_rows(run: Any) -> None:
    """A direct coverage takes main's path whatever the sizes."""
    descriptors = [
        build.dataset(disclosure={"allow_row_ids": True}),
        build.table("parents", ["pid"]),
        build.column("parents.pid", "string"),
        build.table("kids", ["kid"], role="event"),
        build.column("kids.kid", "string"),
        build.column("kids.pid", "string"),
        build.column("kids.s", "category"),
        build.column("kids.flag", "category"),
        build.relationship("kids", ["pid"], "parents", role="parent"),
        build.coverage(
            KIDS, {"table": "direct", "parent_columns": {"pid": "pid"}, "scope_columns": {"s": "s"}}
        ),
        build.table("direct", None, role="coverage"),
        build.column("direct.pid", "string"),
        col("direct.s", "category", codes=True),
    ]
    options = ["a", "b", None, "?"]
    configs = [(), *((o,) for o in options), *itertools.product(options, repeat=2)]
    rows: dict[str, list[dict[str, Any]]] = {"parents": [], "kids": [], "direct": []}
    for parent, config in enumerate(configs):
        rows["parents"].append({"pid": f"p{parent}"})
        rows["kids"].append({"kid": f"k{parent}", "pid": f"p{parent}", "s": "a", "flag": "x"})
        rows["direct"] += [{"pid": f"p{parent}", "s": s} for s in config]
    rows["direct"].append({"pid": None, "s": "a"})
    held(
        run,
        build.release(descriptors, rows),
        [
            [ex()],
            [ex(quantifier="every")],
            [ex(v("s", "a"))],
            [ex(v("s", "a", "b"))],
            [ex(v("s", "a"), v("s", "b"))],
            [ex(v("s", "a")), ex(v("s", "a", "b"))],
            [{"kind": "covered", "table": "kids", "scope": {"s": ["a"]}}],
            [ex(v("flag", "x"), min_count=2)],
        ],
    )


def test_two_assignment_tables_over_one_groups_table(run: Any) -> None:
    """Two coverages over one groups table through two assignment tables: what one reaches is not
    the other's."""
    found = descriptors("string", ["s"], True)
    found += [
        build.table("others", ["oid"], role="event"),
        build.column("others.oid", "string"),
        build.column("others.pid", "string"),
        build.column("others.s", "category"),
        build.relationship("others", ["pid"], "parents", role="parent"),
        build.coverage(
            "rel:others.parent",
            {
                "assignment": {
                    "table": "assign2",
                    "parent_columns": {"pid": "pid"},
                    "group_column": "grp",
                },
                "groups": {
                    "table": "groups",
                    "group_column": "grp",
                    "scope_columns": {"s": "s"},
                    "covers_all_column": "all",
                },
            },
        ),
        build.table("assign2", None, role="coverage"),
        build.column("assign2.pid", "string"),
        build.column("assign2.grp", "string"),
    ]
    groups = [("g0", "a", False), ("g1", "b", False), ("g2", "a", True), ("g3", None, False)]
    rows: dict[str, list[dict[str, Any]]] = {
        "parents": [],
        "kids": [],
        "assign": [],
        "assign2": [],
        "others": [],
        "groups": [{"grp": g, "s": s, "t": "x", "all": c} for g, s, c in groups],
    }
    picks = [(), *((i,) for i in range(4)), *itertools.combinations(range(4), 2)]
    for parent, (first, second) in enumerate(itertools.product(picks, picks)):
        pid = f"p{parent}"
        rows["parents"].append({"pid": pid})
        rows["assign"] += [{"pid": pid, "grp": groups[i][0]} for i in first]
        rows["assign2"] += [{"pid": pid, "grp": groups[i][0]} for i in second]
        rows["kids"].append({"kid": f"k{parent}", "pid": pid, "s": "a", "t": "x", "flag": "x"})
        rows["others"].append({"oid": f"o{parent}", "pid": pid, "s": "a"})
    held(
        run,
        build.release(found, rows),
        [
            [ex()],
            [ex(table="others")],
            [ex(quantifier="every"), ex(table="others", quantifier="every")],
            [ex(v("s", "a")), ex(v("s", "a", table="others"), table="others")],
            [ex(v("s", "b", table="others"), table="others")],
            [{"kind": "covered", "table": "others", "scope": {"s": ["b"]}}],
            [ex(v("s", "a", "b")), ex(v("s", "a", "b", table="others"), table="others")],
        ],
    )


def test_an_integer_scope_column_against_a_double_one_beyond_two_to_the_53(run: Any) -> None:
    """The child's scope column is int64 and the groups' float64; a constant at 2^53 + 1 is no
    double, so no group lists it."""
    rows = {
        "parents": [{"pid": "p0"}, {"pid": "p1"}],
        "assign": [{"pid": "p0", "grp": "g0"}, {"pid": "p1", "grp": "g1"}],
        "groups": [
            {"grp": "g0", "s": float(2**53), "t": "x", "all": False},
            {"grp": "g1", "s": 7.0, "t": "x", "all": False},
        ],
        "kids": [{"kid": "k0", "pid": "p0", "s": 2**53 + 1, "t": "x", "flag": "x"}],
    }
    made = build.release(descriptors("float64", ["s"], False, child="int64"), rows)
    big = str(2**53 + 1)
    held(
        run,
        made,
        [
            [ex(v("s", big))],
            [ex(v("s", big, 7))],
            [ex(v("s", str(2**53)))],
            [{"kind": "covered", "table": "kids", "scope": {"s": [big]}}],
        ],
    )

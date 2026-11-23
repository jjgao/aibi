"""The one-group path held to the evaluator on the axes it splits on (SPEC D449): parents of
none, one, two and many groups in one release, repeated assignment rows, null and dangling
group keys beside real ones, groups whose rows mix whole and non-whole tuples, one and two scope
columns, covers-all, cross-typed parent and group keys, two coverages over one groups table, a
coverage with no scope column, and three fixtures where coverages over a shared table reach
different groups. Every release's coverages are inside the bound, so ``run`` holds the one-group
path to the evaluator; a membership and a crossing with a lift are held to main's path.

The derived domain (``tests/core/domain``) is exhaustive over a smaller universe; this is its
carrier in the core suite."""

import itertools
import random
from collections.abc import Callable, Mapping, Sequence
from typing import Any

import pytest

from aibi.core.engine import build
from aibi.core.engine import sql as compiler
from aibi.core.engine.data import Release
from aibi.core.engine.resolve import Resolution

BIG = 2**53
KIDS = "rel:kids.parent"
OTHERS = "rel:others.parent"

GROUPS: list[tuple[str, list[tuple[Any, Any, Any]]]] = [
    ("g0", [("a", "x", False)]),
    ("g1", [("b", "x", False)]),
    ("g2", [("a", "x", False), ("b", "x", False)]),
    ("g3", [("a", None, False)]),
    ("g4", [(None, "x", False)]),
    ("g5", [("c", "x", True)]),
    ("g6", [("a", "y", None)]),
    ("g7", [("b", "y", False), ("a", "x", True)]),
    ("g8", [("a", "x", False), ("a", "y", False)]),
]
"""Each group's rows (s, t, covers-all): two tuples that share ``s`` in g8."""


def picks() -> list[tuple[Any, ...]]:
    """The groups of each parent: none, a null or dangling key, one group (once or repeated),
    one beside a null or dangling key, two, two repeated, and many."""
    count = len(GROUPS)
    rng = random.Random(3)
    found: list[tuple[Any, ...]] = [(), (None,), ("dangling",), (None, None), ("dangling",) * 2]
    found += [(i,) for i in range(count)] + [(i, i) for i in range(count)]
    found += [(i, "dangling") for i in range(count)] + [(None, i) for i in range(count)]
    found += list(itertools.permutations(range(count), 2))
    found += [(i, i, j) for i, j in itertools.combinations(range(count), 2)]
    found += [tuple(rng.sample(range(count), k)) for k in (3, 5, 7) for _ in range(6)]
    found += [tuple(range(count)) * 2]
    return found


def _column(id: str, kind: str) -> Any:
    return build.column(id, kind, **({"units": "1"} if kind in ("integer", "number") else {}))


def release(
    *,
    two_scope: bool = False,
    covers: bool = True,
    keys: tuple[str, str] = ("string", "string"),
    others: str | None = None,
    owners: bool = False,
) -> Release:
    """Parents and their kids, listed by an assignment of parents to groups; ``keys`` the types
    of the parents' key and the assignment's. ``others`` adds a second relationship listed
    through ``assign2`` (a second assignment table over the same groups), ``grp2`` (a second
    group column of the same assignment table, D447's shape) or ``self`` (an assignment table
    that is its own groups table)."""
    groups: dict[str, Any] = {
        "table": "groups",
        "group_column": "grp",
        "scope_columns": {"s": "s", **({"t": "t"} if two_scope else {})},
    }
    if covers:
        groups["covers_all_column"] = "all"
    descriptors = [
        build.dataset(disclosure={"allow_row_ids": True}),
        build.table("parents", ["pid"]),
        _column("parents.pid", keys[0]),
        build.table("kids", ["kid"], role="event"),
        build.column("kids.kid", "string"),
        _column("kids.pid", keys[0]),
        build.column("kids.s", "category"),
        build.column("kids.t", "category"),
        build.column("kids.flag", "category"),
        build.relationship("kids", ["pid"], "parents", role="parent"),
        build.coverage(
            KIDS,
            {
                "assignment": {
                    "table": "assign",
                    "parent_columns": {"pid": "pid"},
                    "group_column": "grp",
                },
                "groups": groups,
            },
        ),
        build.table("assign", None, role="coverage"),
        _column("assign.pid", keys[1]),
        build.column("assign.grp", "string"),
        build.column("assign.grp2", "string"),
        build.column("assign.s", "category"),
        build.table("groups", None, role="coverage"),
        build.column("groups.grp", "string"),
        build.column("groups.s", "category"),
        build.column("groups.t", "category"),
        build.column("groups.all", "boolean"),
    ]
    if others is not None:
        table = {"assign2": "assign2", "grp2": "assign", "self": "assign"}[others]
        listed: dict[str, Any] = (
            {"table": "assign", "group_column": "grp2", "scope_columns": {"s": "s"}}
            if others == "self"
            else {"table": "groups", "group_column": "grp", "scope_columns": {"s": "s"}}
        )
        descriptors += [
            build.table("others", ["oid"], role="event"),
            build.column("others.oid", "string"),
            _column("others.pid", keys[0]),
            build.column("others.s", "category"),
            build.relationship("others", ["pid"], "parents", role="parent"),
            build.coverage(
                OTHERS,
                {
                    "assignment": {
                        "table": table,
                        "parent_columns": {"pid": "pid"},
                        "group_column": "grp" if others == "assign2" else "grp2",
                    },
                    "groups": listed,
                },
            ),
        ]
        if others == "assign2":
            descriptors += [
                build.table("assign2", None, role="coverage"),
                _column("assign2.pid", keys[1]),
                build.column("assign2.grp", "string"),
            ]
    if owners:
        descriptors += [
            build.table("owners", ["wid"]),
            build.column("owners.wid", "string"),
            build.column("parents.wid", "string"),
            build.relationship("parents", ["wid"], "owners", role="owner"),
        ]
    rows: dict[str, list[dict[str, Any]]] = {
        "parents": [],
        "kids": [],
        "assign": [],
        "groups": [
            {"grp": g, "s": s, "t": t, "all": a} for g, given in GROUPS for s, t, a in given
        ],
    }
    rows["groups"].append({"grp": None, "s": "a", "t": "x", "all": covers})
    if others is not None:
        rows["others"] = []
        if others == "assign2":
            rows["assign2"] = []
    if owners:
        rows["owners"] = [{"wid": f"w{i}"} for i in range(20)]
    shapes = picks()
    rng = random.Random(5)

    def group(index: Any) -> Any:
        return None if index is None else ("gz" if index == "dangling" else GROUPS[index][0])

    def key(parent: int, kind: str) -> Any:
        # Beyond 2^53: parents int64 BIG + p, whose odd ones have no exact double (a float64
        # assignment rounds onto a neighbour, which the evaluator matches to none); parents
        # float64 BIG + 2p, whose int64 assignment BIG + 2p + (p % 2) is no double for odd p.
        if kind == "string":
            return f"p{parent}"
        if keys[0] == "number":
            return float(BIG + 2 * parent) if kind == "number" else BIG + 2 * parent + parent % 2
        return BIG + parent if kind == "integer" else float(BIG + parent)

    for parent, given in enumerate(shapes):
        pid = key(parent, keys[0])
        row: dict[str, Any] = {"pid": pid}
        if owners:
            row["wid"] = f"w{parent % 20}"
        rows["parents"].append(row)
        second = shapes[rng.randrange(len(shapes))]
        for at, index in enumerate(given):
            assigned = {"pid": key(parent, keys[1]), "grp": group(index)}
            if others in ("grp2", "self"):
                assigned["grp2"] = group(second[at % len(second)]) if second else None
                assigned["s"] = "abc"[(parent + at) % 3] if at % 4 else None
            rows["assign"].append(assigned)
        if others == "assign2":
            rows["assign2"] += [{"pid": key(parent, keys[1]), "grp": group(i)} for i in second]
        if others is not None:
            rows["others"].append({"oid": f"o{parent}", "pid": pid, "s": rng.choice("abc")})
        for k in range(parent % 3):
            rows["kids"].append(
                {
                    "kid": f"k{parent}.{k}",
                    "pid": pid,
                    "s": "abc"[(parent + k) % 3],
                    "t": "xy"[k % 2],
                    "flag": "xy"[parent % 2],
                }
            )
    return build.release(descriptors, rows)


def v(column: str, *values: Any, table: str = "kids") -> dict[str, Any]:
    return {"kind": "value", "column": f"{table}.{column}", "values": list(values)}


def ex(*where: Any, table: str = "kids", **extra: Any) -> dict[str, Any]:
    return {"kind": "exists", "table": table, "where": list(where), **extra}


def questions(*, two_scope: bool = False, others: bool = False) -> list[list[Any]]:
    found: list[list[Any]] = [
        [ex()],
        [ex(quantifier="every")],
        [ex(v("flag", "x"), min_count=2)],
        [{"kind": "covered", "table": "kids"}],
        [ex(v("s", "a"))],
        [ex(v("s", "a", "b"))],
        [ex(v("s", "c"))],
        [ex(v("s", "z"))],
        [ex(v("s", "a"), min_count=2)],
        [{"not": ex(v("s", "b"))}],
        [ex(v("s", "a"), v("s", "b"))],
        [{"kind": "covered", "table": "kids", "scope": {"s": ["a"]}}],
        [{"kind": "covered", "table": "kids", "scope": {"s": ["a", "b"]}}],
        [ex(v("s", "a")), {"not": ex(v("s", "b"))}, ex(quantifier="every")],
    ]
    if two_scope:
        found += [
            [ex(v("s", "a"), v("t", "x"))],
            [ex(v("s", "a", "b"), v("t", "x", "y"))],
            [ex(v("t", "y"))],
            [ex(v("s", "a"), v("t", "x", "y"))],
            [{"kind": "covered", "table": "kids", "scope": {"s": ["a"], "t": ["x", "y"]}}],
        ]
    if others:
        found += [
            [ex(table="others")],
            [ex(table="others", quantifier="every")],
            [ex(v("s", "a")), ex(v("s", "a", table="others"), table="others")],
            [ex(v("s", "b", table="others"), table="others"), ex(v("s", "b"))],
            [{"kind": "covered", "table": "others", "scope": {"s": ["c"]}}],
        ]
    return found


def written(cohorts: Mapping[str, Sequence[Any]], unit: str = "parents") -> dict[str, Any]:
    return {
        "aibi": "1",
        "dataset": "d",
        "unit": unit,
        "cohorts": {name: {"all": list(clauses)} for name, clauses in cohorts.items()},
    }


def held(
    run: Callable[..., Any], blobs: Callable[..., Any], made: Release, asked: list[list[Any]]
) -> None:
    """Every question, six to a document, by the one-group path against the evaluator."""
    sources = blobs(made)
    for at in range(0, len(asked), 6):
        found = run(written({f"c{i}": q for i, q in enumerate(asked[at : at + 6])}), made)
        assert found.resolution.refusals == [], found.refusals
        for cohort in found.resolution.cohorts.values():
            made_compiler = compiler._Compiler(cohort, sources)  # pyright: ignore[reportPrivateUsage]
            made_compiler.compile()
            assert made_compiler.admitted == frozenset(
                relationship for relationship in cohort.coverage if relationship in (KIDS, OTHERS)
            )


VARIANTS: list[dict[str, Any]] = [
    {},
    {"covers": False},
    {"two_scope": True},
    {"others": "assign2"},
    {"others": "grp2"},
    {"others": "self", "covers": False},
    {"keys": ("integer", "number")},
    {"keys": ("number", "integer")},
    {"keys": ("integer", "number"), "others": "assign2", "covers": False},
]


@pytest.mark.parametrize(
    "variant", VARIANTS, ids=lambda given: "-".join(f"{k}={v}" for k, v in given.items()) or "plain"
)
def test_the_one_group_path_answers_as_the_evaluator(
    run: Callable[..., Any], blobs: Callable[..., Any], variant: dict[str, Any]
) -> None:
    made = release(**variant)
    held(
        run,
        blobs,
        made,
        questions(two_scope=variant.get("two_scope", False), others="others" in variant),
    )


def _small(
    groups: list[tuple[Any, Any, Any]],
    assigned: list[tuple[Any, ...]],
    *,
    scope: bool = True,
    covers: bool = False,
    keys: tuple[str, str] = ("string", "string"),
    scope_type: str = "category",
) -> Release:
    """A release of parents, each assigned the groups (by key) of one tuple; group keys typed
    ``keys`` (the assignment's, the groups'); the groups' scope column of ``scope_type``."""
    group: dict[str, Any] = {"table": "groups", "group_column": "grp"}
    if scope:
        group["scope_columns"] = {"s": "s"}
    if covers:
        group["covers_all_column"] = "all"
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
            KIDS,
            {
                "assignment": {
                    "table": "assign",
                    "parent_columns": {"pid": "pid"},
                    "group_column": "grp",
                },
                "groups": group,
            },
        ),
        build.table("assign", None, role="coverage"),
        build.column("assign.pid", "string"),
        _column("assign.grp", keys[0]),
        build.table("groups", None, role="coverage"),
        _column("groups.grp", keys[1]),
        build.column("groups.s", scope_type),
        build.column("groups.all", "boolean"),
    ]
    rows: dict[str, list[dict[str, Any]]] = {
        "parents": [],
        "kids": [],
        "assign": [],
        "groups": [{"grp": g, "s": s, "all": a} for g, s, a in groups],
    }
    for parent, given in enumerate(assigned * 3):
        pid = f"p{parent}"
        rows["parents"].append({"pid": pid})
        rows["assign"] += [{"pid": pid, "grp": key} for key in given]
        for k in range(parent % 3):
            rows["kids"].append(
                {
                    "kid": f"k{parent}.{k}",
                    "pid": pid,
                    "s": "abc"[(parent + k) % 3],
                    "flag": "xy"[parent % 2],
                }
            )
    return build.release(descriptors, rows)


def _shapes(real: Any, other: Any, dangling: Any) -> list[tuple[Any, ...]]:
    return [
        (),
        (None,),
        (dangling,),
        (dangling, dangling),
        (None, dangling),
        (real,),
        (real, real),
        (real, dangling),
        (dangling, real),
        (real, None),
        (other,),
        (real, other),
        (other, real, other),
        (other, dangling),
    ]


SCOPED = [*questions(), [{"kind": "covered", "table": "kids", "scope": {"s": ["z"]}}]]
UNSCOPED = [
    [ex()],
    [ex(quantifier="every")],
    [{"not": ex()}],
    [{"kind": "covered", "table": "kids"}],
    [ex(v("flag", "x"), min_count=2)],
    [{"not": ex(v("flag", "y"))}],
]


def test_a_coverage_with_no_scope_column(
    run: Callable[..., Any], blobs: Callable[..., Any]
) -> None:
    """A parent of dangling keys alone is listed by no group, not read as one listed group."""
    made = _small([("A", None, False), ("B", None, False)], _shapes("A", "B", "Z"), scope=False)
    held(run, blobs, made, UNSCOPED)


@pytest.mark.parametrize("covers", [False, True])
def test_a_group_of_whole_and_non_whole_rows(
    run: Callable[..., Any], blobs: Callable[..., Any], covers: bool
) -> None:
    groups = [("M", "a", False), ("M", None, False), ("W", "b", False), ("W", "a", covers)]
    held(run, blobs, _small(groups, _shapes("M", "W", "Z"), covers=covers), SCOPED)


def test_a_group_whose_null_scope_covers_all(
    run: Callable[..., Any], blobs: Callable[..., Any]
) -> None:
    groups = [("N", None, True), ("Q", None, None), ("Q", "b", False)]
    held(run, blobs, _small(groups, _shapes("N", "Q", "Z"), covers=True), SCOPED)


@pytest.mark.parametrize("keys", [("integer", "number"), ("number", "integer")])
def test_group_keys_of_an_integer_and_a_double_column(
    run: Callable[..., Any], blobs: Callable[..., Any], keys: tuple[str, str]
) -> None:
    """Beyond 2^53 an odd integer has no exact double: ``key_equal`` matches it to no group
    (the groups' semi-join and the count pass alike), where ``=`` rounds it onto a neighbour."""
    if keys == ("integer", "number"):
        groups = [(float(BIG), "a", False), (float(BIG + 2), "b", False)]
        shapes = [*_shapes(BIG, BIG + 2, BIG + 1), (BIG + 3,), (BIG + 3, BIG)]
    else:
        groups = [(BIG, "a", False), (BIG + 2, "b", False), (BIG + 1, "c", False)]
        shapes = _shapes(float(BIG), float(BIG + 2), float(BIG + 4))
    held(run, blobs, _small(groups, shapes, keys=keys), SCOPED)


@pytest.mark.parametrize("keys", [("string", "integer"), ("integer", "string")])
def test_group_keys_of_a_string_and_an_integer_column(
    run: Callable[..., Any], blobs: Callable[..., Any], keys: tuple[str, str]
) -> None:
    """Text and integer keys never match (``key_equal``); ``=`` would cast the text, and fail on
    a cell that is no number, in the groups' semi-join."""
    if keys == ("string", "integer"):
        groups = [(98, "a", False), (7, "b", False)]
        shapes = _shapes("98", "g98", "7x")
    else:
        groups = [("98", "a", False), ("g98", "b", False)]
        shapes = _shapes(98, 7, 99)
    held(run, blobs, _small(groups, shapes, keys=keys), SCOPED)


@pytest.mark.parametrize("others", [None, "assign2", "grp2"])
def test_memberships_by_the_one_group_path_are_mains(
    variables_of: Callable[..., Resolution],
    materialised: Callable[..., Any],
    force: Callable[[str], None],
    others: str | None,
) -> None:
    """A membership's every-of and its scope values over a coverage that shares its tables with
    another (two assignment tables over one groups table; two group columns of one assignment
    table), counted by the one-group path as by main's."""
    made = release(others=others)
    variables = [{"column": "kids.s", "each": "category"}]
    if others is not None:
        variables.append({"column": "others.s", "each": "category"})
    cohorts = {"c0": [], "c1": [ex(v("s", "a"))]}
    if others is not None:
        cohorts["c2"] = [ex(v("s", "b", table="others"), table="others")]
    resolution = variables_of(written(cohorts), made, variables)
    assert resolution.refusals == [], resolution.refusals
    parts = list(resolution.cohorts.values())
    found = [resolution.variables[f"0/{index}"] for index in range(len(variables))]
    auto = materialised(parts, found)
    force("main")
    assert materialised(parts, found) == auto


@pytest.mark.parametrize("covers", [True, False])
def test_a_crossing_with_a_lift_by_the_one_group_path_is_mains(
    run: Callable[..., Any],
    crossed: Callable[..., Any],
    force: Callable[[str], None],
    covers: bool,
) -> None:
    """Units are owners; each question reaches kids through parents, so the question per parent
    reads the grouped coverage and carries a lift rule (``lift_differs``)."""
    made = release(covers=covers, owners=True)
    down = [{"rel": "rel:parents.owner", "dir": "down"}, {"rel": KIDS, "dir": "down"}]
    asked = {
        "c0": [ex(via=down)],
        "c1": [ex(v("s", "a"), via=down)],
        "p0": [ex(v("s", "a"), via=down)],
        "p1": [ex(v("flag", "x"), via=down, quantifier="every")],
        "p2": [{"kind": "covered", "table": "kids", "via": down, "scope": {"s": ["a"]}}],
        "p3": [ex(v("s", "c"), via=down, lift="assessed")],
    }
    result = run(written(asked, "owners"), made)
    assert result.refusals == [], result.refusals
    cohorts = [result.resolution.cohorts[n] for n in asked if n.startswith("c")]
    predicates = [result.resolution.cohorts[n] for n in asked if n.startswith("p")]
    auto = crossed(cohorts, predicates)
    assert any(split.lift_differs for cohort in auto.cohorts for split in cohort.splits)
    force("main")
    assert crossed(cohorts, predicates) == auto


def test_a_list_typed_scope_cell_lists_no_whole_tuple(
    run: Callable[..., Any], blobs: Callable[..., Any]
) -> None:
    """A groups table whose scope column is a list: no row lists a whole tuple, as the evaluator
    reads it, so a parent is closed only where a group covers every value. The one-group path
    reads such a cell as within no admitted tuple without comparing it, in the per-group half
    and the restricted join alike (main's path compares it, which DuckDB refuses to bind:
    D449's one intended difference)."""
    groups = [("L", ["a"], False), ("L", ["a", "b"], False), ("M", None, False), ("C", ["c"], True)]
    made = _small(
        groups,
        [*_shapes("L", "M", "Z"), ("C",), ("C", "L")],
        covers=True,
        scope_type="list<category>",
    )
    held(run, blobs, made, SCOPED)

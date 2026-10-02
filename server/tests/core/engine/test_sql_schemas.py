"""The SQL compiler against the reference evaluator on random schemas (§13.3, D295): two to four
tables in a random tree, keys stored as strings, integers or doubles (an integer key referenced
from a double column and the other way round), value columns of every stored type holding their
extremes, random missing codes, null and dangling keys, empty tables, every form of coverage
with proposed statuses, parent scopes and record filters, a second relationship between a table
and its parent, and random documents on any of the tables: lookups up the tree, questions down
it (nested, with ``min_count``, ``every`` and both lifts), ``covered`` with scopes, siblings
under ``exclude_self`` (up one relationship and down the other), ids and every combinator; a
path that crosses two relationships between the same tables is given explicitly. Each unit's
truth value, reasons and flags, and the accounting, must be the evaluator's."""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from aibi.core.engine import build
from aibi.core.engine.data import Release
from aibi.core.schema.descriptors import Descriptor

Runner = Callable[..., Any]
Sql = Callable[..., Any]

SCHEMAS = settings(
    max_examples=150,
    deadline=None,
    derandomize=True,
    database=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)
"""The same 150 examples on every run, about 20 seconds: CI's time is bounded, and a failure
seen once is seen again."""
FEWER = settings(SCHEMAS, max_examples=60)

KEYS: dict[str, list[Any]] = {
    "string": ["k0", "k1", "k'2", "k3"],
    "integer": [0, 2**53, 2**53 + 1, -(2**63)],
    "number": [0.5, 2.0**53, -0.0, 1e300],
}
"""Key values of a table, by its key's datatype."""
VALUES: dict[str, list[Any]] = {
    "integer": [0, 1, -1, 7, 2**53, 2**53 + 1, 2**63 - 1, -(2**63)],
    "number": [0.0, -0.0, 0.5, 7.0, 2.0**53, 1e300, -1e300, 2.0**63],
    "string": ["", "a", "a'b", "b\\c", "é", "n\x00l", "z" * 4096],
    "category": ["x", "y", "z", "unlisted"],
    "boolean": [True, False],
    "date": ["0001-01-01", "2020-02-29", "9999-12-31"],
    "datetime": [
        "2025-12-31T23:00:00+00:00",
        "2026-01-01T00:30:00+01:00",
        "2026-01-01T00:00:00.000001Z",
        "9999-12-31T23:59:59.999999Z",
    ],
    "list<category>": [[], ["x"], ["x", "y"], ["x", None], ["y", "?"]],
}
"""Stored values of a value column, by datatype."""
CONSTANTS: dict[str, list[Any]] = {
    "integer": [0, 1, 7, -1, str(2**53), str(2**53 + 1), str(2**63 - 1), str(-(2**63))],
    "number": [0, 0.5, 7, 1e-300, -0.0, str(2**53)],
    "string": ["", "a", "a'b", "b\\c", "é", "n\x00l", "z" * 4096],
    "category": ["x", "y", "z"],
    "date": ["0001-01-01", "2020-02-29", "9999-12-31"],
    "datetime": [
        "2025-12-31T23:00:00Z",
        "2026-01-01T00:00:00.000001+00:00",
        "9999-12-31T23:59:59.999999Z",
    ],
}
"""Constants a document compares a column of each datatype with."""
CODES = [
    {},
    {"?": "NOT_ASSESSED"},
    {"?": "NOT_APPLICABLE", "u": "UNKNOWN"},
    {"n/a": "NOT_APPLICABLE"},
]
RANGED = ("integer", "number", "date", "datetime")


@dataclass
class Column:
    name: str
    datatype: str
    codes: dict[str, str]
    units: str | None = None
    ordered: bool = False


@dataclass
class Table:
    name: str
    key: str
    parent: str | None
    fk: str | None = None
    columns: list[Column] = field(default_factory=list[Column])
    rel: str = ""
    coverage: str = "undeclared"
    scope: str | None = None
    """The child column the coverage is scoped by."""
    filtered: str | None = None
    """The category column its coverage's record filter reads."""
    allowed: list[str] = field(default_factory=list[str])
    """The values its record filter allows."""
    other: str | None = None
    """A second relationship to its parent, on another foreign key column, if it has one."""

    def rels(self) -> list[str]:
        return [self.rel] if self.other is None else [self.rel, self.other]

    def asked(self) -> list[Column]:
        """The columns a question on the table may ask about freely: neither its coverage's
        filtered column nor its scope column (§6.5)."""
        return [c for c in self.columns if c.name not in (self.filtered, self.scope)]


class Schema:
    """A random schema's tables and its release. Not a dataclass: Hypothesis prints every field
    of one in the repr it builds for each strategy made from a schema, and a release's rows
    make it too large; this repr names the tables alone."""

    def __init__(self, tables: dict[str, Table], release: Release) -> None:
        self.tables = tables
        self.release = release

    def __repr__(self) -> str:
        return f"Schema({self.tables!r})"

    def children(self, name: str) -> list[Table]:
        return [table for table in self.tables.values() if table.parent == name]

    def ancestors(self, name: str) -> list[Table]:
        found: list[Table] = []
        parent = self.tables[name].parent
        while parent is not None:
            found.append(self.tables[parent])
            parent = self.tables[parent].parent
        return found


def _other_numeric(datatype: str) -> str:
    return {"integer": "number", "number": "integer"}.get(datatype, datatype)


def _typed(value: Any, datatype: str) -> Any:
    """A key value as a column of another numeric datatype holds it."""
    if datatype == "number" and isinstance(value, int):
        return float(value)
    if datatype == "integer" and isinstance(value, float):
        return int(value) if value.is_integer() and abs(value) < 2**63 else 3
    return value


@st.composite
def schemas(draw: st.DrawFn, twice: bool = False) -> Schema:
    """A random schema; with ``twice``, every table but the root has two relationships to its
    parent, whose keys name its parent's rows."""
    pick = lambda options: draw(st.sampled_from(options))  # noqa: E731
    count = draw(st.integers(2, 4))
    tables: dict[str, Table] = {}
    descriptors: list[Descriptor] = [build.dataset(disclosure={"allow_row_ids": True})]
    rows: dict[str, list[dict[str, Any]]] = {}
    for index in range(count):
        name = f"t{index}"
        parent = None if index == 0 else f"t{draw(st.integers(0, index - 1))}"
        key_type = pick(list(KEYS))
        table = Table(name, key_type, parent)
        descriptors += [build.table(name, ["id"]), build.column(f"{name}.id", key_type)]
        for at in range(draw(st.integers(1, 3))):
            datatype = pick(list(VALUES))
            codes = pick(CODES) if datatype != "boolean" else {}
            column = Column(f"c{at}", datatype, dict(codes))
            fields: dict[str, Any] = {}
            if codes:
                fields["missing_codes"] = codes
            if datatype == "category":
                column.ordered = draw(st.booleans())
                fields["permissible_values"] = {
                    "values": [{"value": value} for value in ("x", "y", "z")],
                    "ordered": column.ordered,
                }
            if datatype in ("integer", "number") and draw(st.booleans()):
                column.units = "1" if datatype == "integer" else "m"
                fields["units"] = column.units
            table.columns.append(column)
            descriptors.append(build.column(f"{name}.{column.name}", datatype, **fields))
        rows[name] = []
        keys = KEYS[key_type][: draw(st.integers(0, 4))]
        for key in keys:
            row: dict[str, Any] = {"id": key}
            for column in table.columns:
                row[column.name] = pick([None, *VALUES[column.datatype], *column.codes])
            rows[name].append(row)
        if parent is not None:
            upper = tables[parent]
            fk: str = pick([upper.key, _other_numeric(upper.key)])
            table.fk = fk
            table.rel = build.relationship_id(name, ["up"])
            descriptors += [
                build.column(f"{name}.up", fk),
                build.relationship(name, ["up"], parent, ["id"]),
            ]
            targets = [_typed(key, fk) for key in KEYS[upper.key]]
            for row in rows[name]:
                row["up"] = pick([None, *targets, *targets, _typed(KEYS[fk][3], fk)])
            descriptors += _coverage(draw, table, upper, rows)
            if twice or draw(st.booleans()):
                table.other = build.relationship_id(name, ["up2"])
                descriptors += [
                    build.column(f"{name}.up2", fk),
                    build.relationship(name, ["up2"], parent, ["id"]),
                ]
                present = [_typed(row["id"], fk) for row in rows[parent]] or [None]
                for row in rows[name]:
                    if twice:
                        row["up"] = pick(present)
                    row["up2"] = pick(present if twice else [None, *targets, *targets])
                if draw(st.booleans()):
                    descriptors.append(build.coverage(table.other, "all"))
        tables[name] = table
    release = build.release(descriptors, rows)
    return Schema(tables, release)


def _coverage(
    draw: st.DrawFn, table: Table, parent: Table, rows: dict[str, list[dict[str, Any]]]
) -> list[Descriptor]:
    """A random coverage for the relationship of ``table`` to its parent, and its tables."""
    pick = lambda options: draw(st.sampled_from(options))  # noqa: E731
    form = pick(["undeclared", "absent", "all", "scoped all", "direct", "grouped"])
    table.coverage = form
    if form == "absent":
        return []
    options: dict[str, Any] = {}
    status = pick([{}, {"statuses": {"parents": "proposed"}}])
    categories = [c.name for c in table.columns if c.datatype == "category"]
    if categories and draw(st.booleans()):
        table.filtered = pick(categories)
        table.allowed = sorted(draw(st.sets(st.sampled_from("xyz"), min_size=1)))
        options["record_filter"] = {table.filtered: table.allowed}
    scopes = [c for c in parent.columns if c.datatype in ("category", "boolean", "integer")]
    if form == "scoped all" and scopes:
        column = pick(scopes)
        constants = [True] if column.datatype == "boolean" else CONSTANTS[column.datatype][:3]
        options["parent_scope"] = {
            "kind": "value",
            "column": f"{parent.name}.{column.name}",
            "values": draw(st.lists(st.sampled_from(constants), min_size=1, unique=True)),
        }
    if form == "undeclared":
        return [build.coverage(table.rel, **options, **status)]
    if form in ("all", "scoped all"):
        return [build.coverage(table.rel, "all", **options, **status)]
    scoped = [c for c in table.columns if c.datatype in ("category", "integer", "string")]
    scope = pick(scoped) if scoped and draw(st.booleans()) else None
    table.scope = None if scope is None else scope.name
    pid_type = pick([parent.key, _other_numeric(parent.key)])
    listed = [_typed(key, pid_type) for key in KEYS[parent.key]]
    scope_type = None if scope is None else pick([scope.datatype, _other_numeric(scope.datatype)])
    scope_type = "string" if scope_type == "category" else scope_type
    scope_values = (
        []
        if scope is None or scope_type is None
        else [_typed(v, scope_type) for v in VALUES[scope.datatype][:4] if not isinstance(v, bool)]
    )
    cover = f"{table.name}_cov"
    found: list[Descriptor] = []
    if form == "direct":
        key = ["pid"] if scope is None else ["pid", "s"]
        found += [build.table(cover, key, role="coverage"), build.column(f"{cover}.pid", pid_type)]
        parents: dict[str, Any] = {"table": cover, "parent_columns": {"pid": "id"}}
        if scope is not None and scope_type is not None:
            found.append(build.column(f"{cover}.s", scope_type))
            parents["scope_columns"] = {"s": scope.name}
        rows[cover] = []
        for listed_key in draw(st.lists(st.sampled_from(listed), max_size=3, unique=True)):
            if scope is None:
                rows[cover].append({"pid": listed_key})
                continue
            for value in draw(st.lists(st.sampled_from([None, *scope_values]), max_size=2)):
                rows[cover].append({"pid": listed_key, "s": value})
        return [*found, build.coverage(table.rel, parents, **options, **status)]
    assignment, groups = f"{table.name}_asg", f"{table.name}_grp"
    found += [
        build.table(assignment, ["pid"], role="coverage"),
        build.column(f"{assignment}.pid", pid_type),
        build.column(f"{assignment}.grp", "string"),
        build.table(groups, ["grp"] if scope is None else ["grp", "s"], role="coverage"),
        build.column(f"{groups}.grp", "string"),
    ]
    group: dict[str, Any] = {"table": groups, "group_column": "grp"}
    if scope is not None and scope_type is not None:
        found += [build.column(f"{groups}.s", scope_type), build.column(f"{groups}.all", "boolean")]
        group["scope_columns"] = {"s": scope.name}
        if draw(st.booleans()):
            group["covers_all_column"] = "all"
    rows[assignment] = [
        {"pid": listed_key, "grp": pick(["g0", "g1", "gone"])}
        for listed_key in draw(st.lists(st.sampled_from(listed), max_size=3, unique=True))
    ]
    rows[groups] = []
    for name in ("g0", "g1"):
        if scope is None:
            rows[groups].append({"grp": name})
            continue
        for value in draw(st.lists(st.sampled_from([None, *scope_values]), max_size=2)):
            rows[groups].append({"grp": name, "s": value, "all": pick([None, True, False])})
    parents = {
        "assignment": {"table": assignment, "parent_columns": {"pid": "id"}, "group_column": "grp"},
        "groups": group,
    }
    return [*found, build.coverage(table.rel, parents, **options, **status)]


# --- Documents ------------------------------------------------------------------------------------


def _predicate(draw: st.DrawFn, table: Table, column: Column) -> dict[str, Any]:
    """A value leaf on a column, with a predicate its datatype takes."""
    pick = lambda options: draw(st.sampled_from(options))  # noqa: E731
    leaf: dict[str, Any] = {"kind": "value", "column": f"{table.name}.{column.name}"}
    if draw(st.booleans()):
        leaf["negate"] = True
    datatype = column.datatype
    if datatype == "boolean":
        return {**leaf, "values": [draw(st.booleans())]}
    if datatype == "list<category>":
        values = draw(st.lists(st.sampled_from(["x", "y", "z"]), min_size=1, unique=True))
        return {**leaf, "values": values, **pick([{}, {"match": "any"}, {"match": "all"}])}
    constants = CONSTANTS[datatype]
    if column.units is not None and draw(st.booleans()):
        leaf["units"] = "%" if datatype == "integer" else "cm"
        constants = [50, 100, 150, 700]
    ranged = datatype in RANGED or (datatype == "category" and column.ordered)
    if ranged and draw(st.booleans()):
        bound = pick(["gt", "gte", "lt", "lte"])
        return {**leaf, "range": {bound: pick(constants)}}
    return {**leaf, "values": draw(st.lists(st.sampled_from(constants), min_size=1, unique=True))}


@st.composite
def leaves(draw: st.DrawFn, schema: Schema, unit: str) -> dict[str, Any]:
    pick = lambda options: draw(st.sampled_from(options))  # noqa: E731
    here = schema.tables[unit]
    children = schema.children(unit)
    ancestors = schema.ancestors(unit)
    lift = pick([{}, {"lift": "strict"}, {"lift": "assessed"}])
    kinds = ["value", "ids"]
    if ancestors:
        kinds += ["lookup", "siblings"]
    if children:
        kinds += ["exists", "exists", "quantified", "covered"]
    kind = pick(kinds)
    if kind == "value":
        return _predicate(draw, here, pick(here.columns))
    if kind == "lookup":
        upper = pick(ancestors)
        steps: list[dict[str, Any]] = []
        crossed: list[Table] = []
        below = here
        while below is not upper:
            steps.append({"rel": pick(below.rels()), "dir": "up"})
            crossed.append(below)
            assert below.parent is not None
            below = schema.tables[below.parent]
        return {**_predicate(draw, upper, pick(upper.columns)), **_via(steps, crossed)}
    if kind == "ids":
        keys = draw(st.lists(st.sampled_from(CONSTANTS_BY_KEY[here.key]), min_size=1, unique=True))
        return {"kind": "ids", "ids": [{"dataset": "d", "key": [key]} for key in keys]}
    if kind == "siblings":
        return _siblings(draw, here, pick(here.rels()), pick(here.rels()))
    child = pick(children)
    down = _via([{"rel": pick(child.rels()), "dir": "down"}], [child])
    if kind == "quantified" and child.asked():
        quantifier = pick([{}, {"quantifier": "every"}, {"quantifier": ["every"]}])
        return {**_predicate(draw, child, pick(child.asked())), **quantifier, **lift, **down}
    if kind == "covered":
        grandchildren = schema.children(child.name)
        target = pick([child, *grandchildren])
        crossed = [child] if target is child else [child, target]
        path = [{"rel": table.rel, "dir": "down"} for table in crossed]
        leaf: dict[str, Any] = {"kind": "covered", "table": target.name, **_via(path, crossed)}
        if target.scope is not None and target.coverage in ("direct", "grouped"):
            column = next(c for c in target.columns if c.name == target.scope)
            if draw(st.booleans()):
                constants = CONSTANTS[
                    "category" if column.datatype == "category" else column.datatype
                ]
                leaf["scope"] = {
                    column.name: draw(st.lists(st.sampled_from(constants), min_size=1, unique=True))
                }
        return {**leaf, **(lift if target is not child else {})}
    quantifier = pick([{}, {"quantifier": "every"}, {"min_count": 2}])
    where = _where(draw, child, quantifier)
    nested = False
    grandchildren = schema.children(child.name)
    if grandchildren and draw(st.booleans()):
        grandchild = pick(grandchildren)
        inner_quantifier = pick([{}, {"min_count": 2}])
        inner = _where(draw, grandchild, inner_quantifier)
        steps = [{"rel": pick(grandchild.rels()), "dir": "down"}]
        where.append(
            {
                "kind": "exists",
                "table": grandchild.name,
                "where": inner,
                **inner_quantifier,
                **_via(steps, [grandchild]),
            }
        )
        nested = True
    return {
        "kind": "exists",
        "table": child.name,
        "where": where,
        **quantifier,
        **(lift if nested else {}),
        **down,
    }


def _siblings(draw: st.DrawFn, here: Table, up: str, down: str) -> dict[str, Any]:
    """The rows of ``here`` that share a parent with each row, up ``up`` and down ``down``."""
    quantifier = draw(st.sampled_from([{}, {"quantifier": "every"}, {"min_count": 2}]))
    return {
        "kind": "exists",
        "table": here.name,
        "via": [{"rel": up, "dir": "up"}, {"rel": down, "dir": "down"}],
        "exclude_self": True,
        "where": _where(draw, here, quantifier),
        **quantifier,
    }


def _via(steps: list[dict[str, Any]], crossed: list[Table]) -> dict[str, Any]:
    """The path given explicitly when it crosses a table with two relationships to its parent,
    which a path found would be ambiguous through; otherwise found."""
    return {"via": steps} if any(table.other is not None for table in crossed) else {}


def _where(draw: st.DrawFn, table: Table, quantifier: dict[str, Any]) -> list[Any]:
    """A question's ``where`` on its child table: predicates on the columns it may ask about
    freely, and the filtered and scope columns in the top-level values conjuncts §6.5 allows."""
    pick = lambda options: draw(st.sampled_from(options))  # noqa: E731
    asked = table.asked()
    where = [
        _predicate(draw, table, pick(asked)) for _ in range(draw(st.integers(0, 2)) if asked else 0)
    ]
    for name, allowed in ((table.filtered, table.allowed), (table.scope, None)):
        if name is None or draw(st.booleans()):
            continue
        if name == table.scope and (
            quantifier.get("quantifier") == "every" or table.scope == table.filtered
        ):
            continue
        column = next(c for c in table.columns if c.name == name)
        constants = allowed or (
            ["x", "y", "z"] if column.datatype == "category" else CONSTANTS[column.datatype]
        )
        values = draw(st.lists(st.sampled_from(constants), min_size=1, unique=True))
        where.append({"kind": "value", "column": f"{table.name}.{name}", "values": values})
    return where


CONSTANTS_BY_KEY: dict[str, list[Any]] = {
    "string": ["k0", "k'2", "gone"],
    "integer": [0, str(2**53), str(2**53 + 1), 5],
    "number": [0.5, str(2**53), 0, 2.5],
}
"""Unit keys an ids leaf names, by the key's datatype."""


def clauses(schema: Schema, unit: str) -> st.SearchStrategy[Any]:
    return st.recursive(
        leaves(schema, unit),
        lambda inner: st.one_of(
            st.lists(inner, max_size=3).map(lambda members: {"all": members}),
            st.lists(inner, max_size=3).map(lambda members: {"any": members}),
            inner.map(lambda member: {"not": member}),
            inner.map(lambda member: {"known": member}),
            inner.map(lambda member: {"unknown": member}),
        ),
        max_leaves=5,
    )


@st.composite
def cases(draw: st.DrawFn) -> tuple[Schema, str, list[Any]]:
    """A schema, the unit table of a document on it, and the document's top-level clauses."""
    schema = draw(schemas())
    unit = draw(st.sampled_from(sorted(schema.tables)))
    return schema, unit, draw(st.lists(clauses(schema, unit), min_size=1, max_size=3))


@st.composite
def sibling_cases(draw: st.DrawFn) -> tuple[Schema, str, list[Any]]:
    """A schema whose tables have two relationships to their parents, and a document on one of
    its child tables asking about its siblings up one relationship and down the other, beside
    other clauses."""
    schema = draw(schemas(twice=True))
    here = schema.tables[draw(st.sampled_from(sorted(schema.tables)[1:]))]
    assert here.other is not None
    up, down = draw(st.permutations([here.rel, here.other]))
    siblings = _siblings(draw, here, up, down)
    others = draw(st.lists(clauses(schema, here.name), max_size=2))
    return schema, here.name, [siblings, *others]


def _agrees(run: Runner, sql: Sql, case: tuple[Schema, str, list[Any]]) -> None:
    schema, unit, written = case
    document = {"aibi": "1", "dataset": "d", "unit": unit, "cohorts": {"c": {"all": written}}}
    result = run(document, schema.release)
    limits = {refusal.limit.name for refusal in result.resolution.refusals if refusal.limit}
    if limits & {"clause_depth", "leaves_per_cohort"}:
        return
    refusals = result.refusals
    assert refusals == [], str(refusals)
    found, expected = sql(result.resolution.cohorts["c"]), result.result
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


@SCHEMAS
@given(case=cases())
def test_the_compiler_agrees_with_the_evaluator_on_random_schemas_and_documents(
    run: Runner, sql: Sql, case: tuple[Schema, str, list[Any]]
) -> None:
    _agrees(run, sql, case)


@FEWER
@given(case=sibling_cases())
def test_siblings_up_one_relationship_and_down_another_agree_on_random_schemas(
    run: Runner, sql: Sql, case: tuple[Schema, str, list[Any]]
) -> None:
    """A row is a child of the parent it reaches only where both keys agree, so its own part is
    taken away from that parent's children alone (D291)."""
    _agrees(run, sql, case)

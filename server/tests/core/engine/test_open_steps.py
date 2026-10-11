"""Which down steps ``resolve.open_step`` proves open, and which paths ``resolve.open_path``
does, checked against the gate itself (SPEC §8.4, D230, D383).

A step is open only where every set of the child table's columns the gate keeps unique holds a
free column, and a path only where it is one open down step from the unit; every other path is
taken as bounded, and ``summary.distribution`` withholds its memberships under *k*. The tests
hold that rule to the gate: every check the gate makes is classified (``resolve.GATE_CHECKS``)
and every site counted, so that a check added to it fails here until the rule reads it; over
generated descriptors, wherever a step is called open, the gate accepts a release in which one
parent holds more child rows than its column declares categories, one of each and one more
(round 2 of #74's review, B2); and over generated chains of one to three steps, wherever a path
is called open, the gate accepts one unit reaching as many rows at the last table (round 3, B3).
"""

import ast
import itertools
import random
from collections import Counter
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from aibi.core.engine import build
from aibi.core.engine.data import Release
from aibi.core.engine.graph import Step
from aibi.core.engine.resolve import (
    GATE_CHECKS,
    bounded_rows,
    free_column,
    open_path,
    open_step,
)
from aibi.core.schema.descriptors import Descriptor
from aibi.core.schema.refusals import RefusalCode
from aibi.core.schema.release import check_release
from aibi.core.store import gate
from aibi.core.store.build import BuildRefused, Layout
from aibi.core.store.sources import SourceValue, TypedSource
from aibi.core.store.store import Store

STEP = "rel:harvests.tree"
DECLARED = ("a", "b", "c")
MAPPED = {"v1": "k", "1": "k", "true": "k", "2024-01-01": "k"}
"""A value map onto one value, from every value a restricted column holds."""
HELD: Mapping[str, SourceValue] = {
    "string": "v1",
    "category": "v1",
    "integer": 1,
    "boolean": True,
    "date": "2024-01-01",
}
"""The one value a restricted column holds in every row."""


SITES = {
    ("key", "RefusalCode.KEY_NULL"): 2,
    ("key", "RefusalCode.KEY_NOT_UNIQUE"): 1,
    ("key", "fail"): 3,
    ("relationship", "RefusalCode.KEY_NOT_UNIQUE"): 1,
    ("relationship", "RefusalCode.DANGLING_REFERENCE"): 1,
    ("relationship", "RefusalCode.CARDINALITY_VIOLATED"): 1,
    ("relationship", "fail"): 3,
    ("nulls_in", "RefusalCode.COVERAGE_NULL"): 1,
    ("nulls_in", "fail"): 1,
    ("unknown_parents", "RefusalCode.COVERAGE_UNKNOWN"): 1,
    ("unknown_parents", "fail"): 1,
    ("unknown_groups", "RefusalCode.COVERAGE_UNKNOWN"): 1,
    ("unknown_groups", "fail"): 1,
    ("record_filter", "RefusalCode.OUTSIDE_RECORD_FILTER"): 1,
    ("record_filter", "RefusalCode.NOT_APPLICABLE_IN_FILTER"): 1,
    ("record_filter", "fail"): 2,
    ("_drop", "RefusalCode.UNKNOWN_DESCRIPTOR"): 1,
    ("absent_rows", "RefusalCode.INVALID_VALUE"): 2,
    ("absent_rows", "fail"): 2,
    ("declarations", "RefusalCode.INVALID_VALUE"): 4,
    ("declarations", "fail"): 4,
}
"""Every site in the gate that names a refusal code or refuses (``_Checks.fail``), by the
function it is in, as classified in ``GATE_CHECKS`` when this was written (round 3 of #74's
review, m1: a second check inside a function that already raises the same code)."""


def test_every_check_of_the_gate_is_classified() -> None:
    """Each refusal code the gate raises, by the function that raises it, is one
    ``GATE_CHECKS`` classifies as bounding a set's uniqueness, a column's values, or neither;
    and every site that names a code or refuses is counted (``SITES``), so that a check added to
    the gate, a new code, a known code raised in a new function or another check in a function
    that has one, fails here until ``open_step`` is shown to read it and the count is updated."""
    assert gate.__file__ is not None
    tree = ast.parse(Path(gate.__file__).read_text(encoding="utf-8"))
    sites: Counter[tuple[str, str]] = Counter()

    def visit(node: ast.AST, within: str) -> None:
        if isinstance(node, ast.FunctionDef):
            within = node.name
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "RefusalCode"
        ):
            sites[(within, f"RefusalCode.{node.attr}")] += 1
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "fail"
        ):
            sites[(within, "fail")] += 1
        for child in ast.iter_child_nodes(node):
            visit(child, within)

    visit(tree, "")
    assert dict(sites) == SITES
    raised = {
        (within, name.removeprefix("RefusalCode.")) for within, name in sites if name != "fail"
    }
    assert raised == set(GATE_CHECKS)
    assert all(code in RefusalCode.__members__ for _, code in GATE_CHECKS)


# --- Generated descriptors ------------------------------------------------------------------------


@dataclass(frozen=True)
class Shape:
    """A child table ``harvests`` under ``trees`` (its step ``STEP``), with its grade declaring
    ``DECLARED``, two more columns, and the descriptors that may constrain them."""

    types: tuple[str, str]
    """The datatypes of ``c1`` and ``c2``."""
    derived: str | None
    """The column ``d`` maps onto one value (``MAPPED``), if ``d`` is declared."""
    key: tuple[str, ...] | None
    one_to_one: bool
    """Whether the step itself is one-to-one."""
    step_filter: str | None
    """The column the step's coverage filters to ``v1``."""
    other: tuple[str, bool] | None
    """A relationship from ``harvests`` to ``others`` over a column, and whether it is
    one-to-one."""
    other_coverage: str | None
    """Its coverage: ``all``, ``filter:<column>`` or ``listed:<column>`` (a listing of the
    parents in ``harvests`` itself)."""
    incoming: tuple[str, ...] | None
    """The parent columns in ``harvests`` of a relationship from ``notes``."""


def _shapes(seed: int, count: int) -> Iterator[Shape]:
    """Shapes the release checks accept: a record filter names a category column, a foreign key
    to ``others`` a string column, and a relationship into ``harvests`` leads to its key where
    it declares one."""
    chosen = random.Random(seed)
    columns = ("tree_id", "grade", "c1", "c2", "d")
    datatypes = ("string", "integer", "boolean", "date", "category")
    for _ in range(count):
        types = (chosen.choice(datatypes), chosen.choice(datatypes))
        derived = chosen.choice((None, "c1", "c2"))
        present = [c for c in columns if c != "d" or derived is not None]
        key = None
        if chosen.random() < 0.75:
            key = tuple(c for c in present if chosen.random() < 0.45) or ("tree_id",)
        strings = [c for c, t in zip(("c1", "c2"), types, strict=True) if t == "string"]
        categories = [c for c, t in zip(("c1", "c2"), types, strict=True) if t == "category"]
        other = None
        if strings and chosen.random() < 0.5:
            other = (chosen.choice(strings), chosen.random() < 0.3)
        other_coverage = None
        if other is not None:
            other_coverage = chosen.choice(
                (
                    None,
                    "all",
                    *(f"filter:{c}" for c in categories),
                    *(f"listed:{c}" for c in strings),
                )
            )
        incoming = None
        if chosen.random() < 0.4:
            incoming = key or tuple(c for c in present if chosen.random() < 0.35) or ("c1",)
        yield Shape(
            types,
            derived,
            key,
            chosen.random() < 0.15,
            chosen.choice((None, *categories)),
            other,
            other_coverage,
            incoming,
        )


def _descriptors(shape: Shape) -> list[Descriptor]:
    types = dict(zip(("c1", "c2"), shape.types, strict=True))
    datatypes = {"tree_id": "string", "grade": "category", **types, "d": "category"}
    listed = shape.other_coverage is not None and shape.other_coverage.startswith("listed:")
    role = "coverage" if listed else "event"
    found: list[Descriptor] = [
        build.dataset(),
        build.table("trees", ["tree_id"]),
        build.column("trees.tree_id", "string"),
        build.table("others", ["o"]),
        build.column("others.o", "string"),
        build.table("harvests", shape.key, role=role),
        build.column("harvests.tree_id", "string"),
        build.column(
            "harvests.grade",
            "category",
            permissible_values={"values": [{"value": v} for v in DECLARED]},
        ),
        *(build.column(f"harvests.{c}", t) for c, t in types.items()),
        build.relationship(
            "harvests", ["tree_id"], "trees", role="tree", one_to_one=shape.one_to_one
        ),
        build.coverage(
            STEP,
            "all",
            record_filter=None if shape.step_filter is None else {shape.step_filter: ["v1"]},
        ),
    ]
    if shape.derived is not None:
        derivation = {"op": "value_map", "input": shape.derived, "map": MAPPED}
        found.append(build.column("harvests.d", "category", derived=derivation))
    if shape.other is not None:
        column, one_to_one = shape.other
        found.append(
            build.relationship(
                "harvests", [column], "others", ["o"], role="other", one_to_one=one_to_one
            )
        )
        chosen = shape.other_coverage
        if chosen == "all":
            found.append(build.coverage("rel:harvests.other", "all"))
        elif chosen is not None and chosen.startswith("filter:"):
            filtered = chosen.removeprefix("filter:")
            found.append(
                build.coverage("rel:harvests.other", "all", record_filter={filtered: ["v1"]})
            )
        elif chosen is not None:
            named = {"table": "harvests", "parent_columns": {chosen.removeprefix("listed:"): "o"}}
            found.append(build.coverage("rel:harvests.other", named))
    if shape.incoming is not None:
        refs = [f"ref_{c}" for c in shape.incoming]
        found += [
            build.table("notes", ["note_id"]),
            build.column("notes.note_id", "string"),
            *(build.column(f"notes.ref_{c}", datatypes[c]) for c in shape.incoming),
            build.relationship("notes", refs, "harvests", list(shape.incoming), role="note"),
        ]
    return found


def _free(index: int, datatype: str) -> SourceValue:
    """A value of a free column, one for each row."""
    if datatype == "integer":
        return 100 + index
    if datatype == "date":
        return f"2024-02-{10 + index}"
    return f"u{index}"


def _import(store: Store, descriptors: Sequence[Descriptor], release: Release) -> None:
    """Imports a release in which tree ``t1`` has one harvest of each declared grade and one of
    a grade it does not declare, every free column of ``harvests`` distinct among them, and
    every other stored column but the step's foreign key holding ``HELD``'s value
    (``BuildRefused`` if the gate refuses)."""
    stored = {
        column: release.datatype("harvests", column)
        for column in release.columns("harvests")
        if (found := release.column("harvests", column)) is not None
        and found.fields.derived is None
    }
    grades = (*DECLARED, "z")
    rows: list[tuple[SourceValue, ...]] = []
    for index, grade in enumerate(grades):
        row: list[SourceValue] = []
        for column, datatype in stored.items():
            assert datatype is not None
            if column == "grade":
                row.append(grade)
            elif column == "tree_id":
                row.append("t1")
            elif free_column(release, "harvests", column):
                row.append(_free(index, datatype))
            else:
                row.append(HELD[datatype])
        rows.append(tuple(row))
    tables: dict[str, tuple[tuple[str, ...], list[tuple[SourceValue, ...]]]] = {
        "trees": (("tree_id",), [("t1",)]),
        "others": (("o",), [("v1",)]),
        "harvests": (tuple(stored), rows),
    }
    if "notes" in release.table_ids:
        tables["notes"] = (release.columns("notes"), [])
    sources = {
        name: TypedSource(columns, tuple(given)) for name, (columns, given) in tables.items()
    }
    layouts = {
        name: Layout(name, tuple((column, column) for column in columns))
        for name, (columns, _) in tables.items()
    }
    with store.pin() as pin:
        store.import_release(pin, "orchard", descriptors, sources, layouts)


def _clock() -> Any:
    ticks = itertools.count()
    start = datetime(2026, 1, 1, tzinfo=UTC)
    return lambda: start + timedelta(seconds=next(ticks))


@pytest.fixture
def store(tmp_path: Path) -> Iterator[Store]:
    opened = Store(tmp_path / "data", clock=_clock())
    yield opened
    opened.close()


def test_a_step_called_open_lets_one_parent_hold_every_declared_category_and_more(
    store: Store,
) -> None:
    """Over generated descriptors of a child table, its key, filters, derivations and the
    relationships from and into it: wherever ``open_step`` calls the step open, the gate accepts
    one parent holding a row of each declared category and one more, the rows distinct in their
    free columns; so no step called open has its rows per parent bounded by the descriptors.
    Both answers are given often enough for the check to bite."""
    answers = {True: 0, False: 0}
    skipped = 0
    for shape in dict.fromkeys(_shapes(52, 400)):
        descriptors = _descriptors(shape)
        refusals = check_release(descriptors)
        if refusals:
            # A coverage listing its parents in ``harvests`` would restrict its columns
            # (``COVERAGE_UNKNOWN``), but no relationship joins a table a coverage names (§5.6).
            assert shape.other_coverage is not None, shape
            assert shape.other_coverage.startswith("listed:"), shape
            texts = {refusal.message[0].model_dump()["text"] for refusal in refusals}
            assert all(t.startswith("A relationship joins no coverage table") for t in texts)
            skipped += 1
            continue
        release = build.release(descriptors, {}, check=False)
        opened = open_step(release, STEP)
        answers[opened] += 1
        if opened:
            try:
                _import(store, descriptors, release)
            except BuildRefused as refused:
                raise AssertionError((shape, refused.refusals)) from None
    assert answers[True] >= 40, (answers, skipped)
    assert answers[False] >= 40, (answers, skipped)


# --- Generated paths ------------------------------------------------------------------------------


@dataclass(frozen=True)
class Level:
    """One down step of a chain below ``trees``: its child table ``t<i>``, which carries its
    parent's key as its foreign key, and two columns of its own."""

    types: tuple[str, str]
    """The datatypes of ``a<i>`` and ``b<i>``."""
    key: tuple[str, ...] | None
    one_to_one: bool
    filtered: str | None
    """The column the step's coverage filters to ``v1``."""


def _chains(seed: int, count: int) -> Iterator[tuple[Level, ...]]:
    """Chains of one to three down steps whose keys are drawn over the columns each table
    inherits and its own, the last one's key possibly undeclared (round 3 of #74's review, B3:
    a later child keyed within the columns each unit fixes)."""
    chosen = random.Random(seed)
    datatypes = ("string", "integer", "boolean", "category")
    for _ in range(count):
        depth = chosen.choice((1, 2, 2, 3))
        inherited: tuple[str, ...] = ("tree_id",)
        found: list[Level] = []
        for index in range(1, depth + 1):
            types = (chosen.choice(datatypes), chosen.choice(datatypes))
            own = (f"a{index}", f"b{index}")
            present = (*inherited, *own, *(("grade",) if index == depth else ()))
            key = tuple(c for c in present if chosen.random() < 0.4) or (present[0],)
            if index == depth and chosen.random() < 0.15:
                key = None
            categories = [c for c, t in zip(own, types, strict=True) if t == "category"]
            filtered = chosen.choice((None, *categories)) if categories else None
            found.append(Level(types, key, chosen.random() < 0.1, filtered))
            assert key is not None or index == depth
            inherited = key or inherited
        yield tuple(found)


def _chain_descriptors(chain: Sequence[Level]) -> tuple[list[Descriptor], list[str]]:
    """The chain's descriptors and its relationships' ids, from the unit down."""
    found: list[Descriptor] = [
        build.dataset(),
        build.table("trees", ["tree_id"]),
        build.column("trees.tree_id", "string"),
    ]
    steps: list[str] = []
    parent, inherited = "trees", {"tree_id": "string"}
    for index, level in enumerate(chain, start=1):
        table = f"t{index}"
        last = index == len(chain)
        own = dict(zip((f"a{index}", f"b{index}"), level.types, strict=True))
        found += [
            build.table(table, level.key, role="event"),
            *(build.column(f"{table}.{c}", t) for c, t in {**inherited, **own}.items()),
        ]
        if last:
            declared = {"values": [{"value": v} for v in DECLARED]}
            found.append(build.column(f"{table}.grade", "category", permissible_values=declared))
        columns = list(inherited)
        found.append(build.relationship(table, columns, parent, one_to_one=level.one_to_one))
        relationship = build.relationship_id(table, columns)
        steps.append(relationship)
        record_filter = None if level.filtered is None else {level.filtered: ["v1"]}
        found.append(build.coverage(relationship, "all", record_filter=record_filter))
        datatypes = {**inherited, **own, "grade": "category"}
        parent = table
        inherited = {c: datatypes[c] for c in level.key or ()}
    return found, steps


def _import_chain(store: Store, descriptors: Sequence[Descriptor], release: Release) -> None:
    """Imports a release in which tree ``t1`` reaches, at every table of the chain, one row of
    each declared grade and one more (a grade it does not declare): row *i* of each table under
    row *i* of its parent (every one under ``t1`` at the first), its foreign key its parent
    row's key, its free columns distinct among the rows, and every other column holding
    ``HELD``'s value (``BuildRefused`` if the gate refuses)."""
    grades = (*DECLARED, "z")
    tables: dict[str, tuple[tuple[str, ...], list[tuple[SourceValue, ...]]]] = {
        "trees": (("tree_id",), [("t1",)])
    }
    parents: list[dict[str, SourceValue]] = [{"tree_id": "t1"}] * len(grades)
    index = 1
    while f"t{index}" in release.table_ids:
        table = f"t{index}"
        columns = release.columns(table)
        rows: list[dict[str, SourceValue]] = []
        for row, grade in enumerate(grades):
            values: dict[str, SourceValue] = {}
            for column in columns:
                datatype = release.datatype(table, column)
                assert datatype is not None
                if column in parents[row]:
                    values[column] = parents[row][column]
                elif column == "grade":
                    values[column] = grade
                elif free_column(release, table, column):
                    values[column] = _free(10 * index + row, datatype)
                else:
                    values[column] = HELD[datatype]
            rows.append(values)
        tables[table] = (columns, [tuple(values[c] for c in columns) for values in rows])
        parents = rows
        index += 1
    sources = {
        name: TypedSource(columns, tuple(given)) for name, (columns, given) in tables.items()
    }
    layouts = {
        name: Layout(name, tuple((column, column) for column in columns))
        for name, (columns, _) in tables.items()
    }
    with store.pin() as pin:
        store.import_release(pin, "orchard", descriptors, sources, layouts)


def test_a_path_called_open_lets_one_unit_hold_every_declared_category_and_more(
    store: Store,
) -> None:
    """Over generated chains of one to three down steps from the unit, their keys drawn over
    inherited and own columns: wherever ``resolve.bounded_rows`` calls the path open (so that
    memberships over it are disclosed under *k*), the gate accepts one unit reaching a row of
    each declared category and one more at the last table; so no path called open has its rows
    per unit bounded by the descriptors. Round 3 of #74's review found the rule of round 2,
    which called a path open where any down step was, disclosing a path whose last child is
    keyed by the unit's key alone (B3); only a one-step path is called open now (D383)."""
    answers: Counter[tuple[int, bool]] = Counter()
    for chain in dict.fromkeys(_chains(75, 400)):
        descriptors, steps = _chain_descriptors(chain)
        assert check_release(descriptors) == [], chain
        release = build.release(descriptors, {}, check=False)
        path = tuple(Step(relationship, "down") for relationship in steps)
        opened = not bounded_rows(release, path, "category")
        answers[(len(chain), opened)] += 1
        if opened:
            try:
                _import_chain(store, descriptors, release)
            except BuildRefused as refused:
                raise AssertionError((chain, refused.refusals)) from None
    assert answers[(1, True)] >= 20, answers
    assert answers[(1, False)] >= 20, answers
    assert sum(n for (depth, _), n in answers.items() if depth > 1) >= 200, answers


@pytest.mark.parametrize(("checked", "opened"), [(False, True), (True, False)])
def test_a_derivation_bounds_its_inputs_only_where_the_gate_checks_it(
    checked: bool, opened: bool
) -> None:
    """Round 3 of #74's review, m3: a key column a derivation reads stays free where the
    derived column is in no set the gate keeps unique, no foreign key and no record filter,
    since the gate never checks its values; a derived column in the key bounds the column it
    reads (a value missing from a value map is UNKNOWN, so the key cell is not PRESENT)."""
    key = ("tree_id", "c1", "d") if checked else ("tree_id", "c1")
    descriptors = _descriptors(
        Shape(("string", "string"), "c1", key, False, None, None, None, None)
    )
    release = build.release(descriptors, {}, check=False)
    assert open_step(release, STEP) is opened


def test_only_a_path_of_one_down_step_is_open() -> None:
    """Round 1 after the redesign, n2: an open step read as an up step (the unit below the
    step's child), or with another step after it, is no open path; alone and down, it is."""
    shape = Shape(("integer", "string"), None, ("tree_id", "c1"), False, None, None, None, None)
    release = build.release(_descriptors(shape), {}, check=False)
    assert open_step(release, STEP)
    assert open_path(release, (Step(STEP, "down"),))
    assert not open_path(release, (Step(STEP, "up"),))
    assert not open_path(release, (Step(STEP, "down"), Step(STEP, "up")))
    assert not open_path(release, ())

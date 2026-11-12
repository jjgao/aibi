# pyright: reportIncompatibleMethodOverride=false
"""The D401 listing check in bounded memory: a grouped coverage's listing, the product of each
parent and its group's scopes, is streamed in the digest's order and never built, with the same
outcomes, refusal texts, counts and digests as the check that built it (``gate._Streamed``,
``pack_api.CellDigest``).

The fixture is neutral: ``readings`` is a table a pack unpivoted, keyed by its unit's columns
(``c*``) and its scope's (``s*``); ``plans`` assigns each unit (``o*``) a plan, ``slots`` lists
each plan's scopes (``t*``), and ``listing`` lists cells directly. The oracle is the check as main
(27c7a57, before this change) had it, copied verbatim into ``_OnMain``.
"""

import hashlib
import itertools
import random
import time
import tracemalloc
from collections.abc import Mapping, Sequence
from typing import Any

import pytest

from aibi.core.engine import build
from aibi.core.engine.data import missing as missing_cell
from aibi.core.engine.data import present
from aibi.core.schema.descriptors import (
    ColumnDescriptor,
    CoverageDescriptor,
    Descriptor,
    DirectCoverage,
    GroupedCoverage,
    RelationshipDescriptor,
    TableDescriptor,
)
from aibi.core.schema.pack_api import CellDigest, cell_digest
from aibi.core.schema.refusals import RefusalCode
from aibi.core.schema.semantics import ObservationState
from aibi.core.store import gate
from aibi.core.store.gate import Tuple, _Rows
from aibi.core.store.manifest import Declared
from aibi.core.store.tables import TypedTable

SECRET = "zz-hidden-7"
"""A value that must never appear in a message."""


class _OnMain(gate._Checks):
    """The listing check as main had it before it was streamed: ``declarations`` and
    ``listed_cells`` copied verbatim from 27c7a57 (``server/src/aibi/core/store/gate.py``), the
    oracle every outcome of the streamed check is held to."""

    def declarations(self, declared: Mapping[str, Declared]) -> None:
        """Each coverage of a relationship into a table a pack unpivoted lists exactly the cells
        its source held: every row's key among the listed cells, and the listed cells without a
        row, the absent cells, as many as the pack dropped and with their digest (D401)."""
        tables = {d.id: d for d in self.descriptors if isinstance(d, TableDescriptor)}
        places = {d.id: at for at, d in enumerate(self.descriptors)}
        columns = {d.id: d for d in self.descriptors if isinstance(d, ColumnDescriptor)}
        for table, declaration in sorted(declared.items()):
            column = columns.get(f"{table}.{declaration.column}")
            if table in places and (column is None or column.fields.absent is None):
                message = "The column the importer declared holds no absent values (D401)"
                self.fail(RefusalCode.INVALID_VALUE, places[table], (), message, _Rows(), None)
        for at, descriptor in enumerate(self.descriptors):
            if not isinstance(descriptor, CoverageDescriptor):
                continue
            relationship = self.relationships.get(descriptor.fields.relationship)
            if relationship is None:
                continue
            child = relationship.fields.child_table
            declaration = declared.get(child)
            table = tables.get(child)
            if declaration is None or table is None:
                continue
            parents = descriptor.fields.parents
            if not isinstance(parents, DirectCoverage | GroupedCoverage):
                continue
            rows = self.tuples(child, table.fields.primary_key or [])
            if rows is None:
                continue
            most = sum(1 for found in rows if found is not None) + declaration.dropped
            problem = self.listed_cells(relationship, parents, table.fields.primary_key or [], most)
            if isinstance(problem, str):
                self.fail(
                    RefusalCode.INVALID_VALUE, at, ("fields", "parents"), problem, _Rows(), None
                )
                continue
            listed = problem
            outside, held = _Rows(), set[Tuple]()
            for row, found in enumerate(rows):
                if found is None:
                    continue
                held.add(found)
                if found not in listed:
                    outside.add(row)
            if outside:
                message = "Rows lie outside the cells the coverage lists (D401), in"
                self.fail(
                    RefusalCode.INVALID_VALUE, at, ("fields", "parents"), message, outside, None
                )
                continue
            missing = listed - held
            digest = cell_digest([str(part[1]) for part in cell] for cell in missing)
            if len(missing) != declaration.dropped or digest != declaration.digest:
                counted = (
                    f"{len(missing)} listed, {declaration.dropped} dropped"
                    if len(missing) != declaration.dropped
                    else f"as many, {len(missing)}, but other cells"
                )
                message = (
                    "The coverage lists cells without a row that are not the cells the importer "
                    f"dropped as absent ({counted}) (D401)"
                )
                self.fail(
                    RefusalCode.INVALID_VALUE, at, ("fields", "parents"), message, _Rows(), None
                )

    def listed_cells(
        self,
        relationship: RelationshipDescriptor,
        parents: DirectCoverage | GroupedCoverage,
        key: Sequence[str],
        most: int,
    ) -> set[Tuple] | str:
        """The distinct cells the coverage lists, as keys of the child table in ``key``'s order
        (its parent columns and scope columns, which ``check_release`` pins to the key), or why
        they can't be read. A grouped coverage lists the product of each parent and its group's
        scopes, which is counted before it is built: a listing of more than ``most`` cells (the
        table's rows and the cells dropped as absent) cannot pass, so it is refused unbuilt."""
        fields = relationship.fields
        stands = dict(zip(fields.parent_columns, fields.child_columns, strict=True))
        if isinstance(parents, DirectCoverage):
            by_parent = {parent: own for own, parent in parents.parent_columns.items()}
            scopes = parents.scope_columns or {}
            source = parents.table
        else:
            by_parent = {parent: own for own, parent in parents.assignment.parent_columns.items()}
            scopes = parents.groups.scope_columns or {}
            source = parents.assignment.table
        if set(by_parent) != set(stands):
            return "The coverage's parent columns do not stand for the relationship's (D401)"
        order = [stands[parent] for parent in fields.parent_columns] + list(scopes.values())
        if sorted(order) != sorted(key):
            return "The coverage's columns are not the reshaped table's key (D401)"
        place = [order.index(column) for column in key]
        owners = self.tuples(source, [by_parent[parent] for parent in fields.parent_columns])
        if owners is None:
            return "The coverage's parent columns are not read"
        cells: set[Tuple] = set()

        def add(parent: Tuple, scope: Tuple) -> None:
            whole = (*parent, *scope)
            cells.add(tuple(whole[i] for i in place))

        if isinstance(parents, DirectCoverage):
            scoped = self.tuples(source, list(scopes))
            if scoped is None:
                return "The coverage's scope columns are not read"
            for owner, scope in zip(owners, scoped, strict=True):
                if owner is not None and scope is not None:
                    add(owner, scope)
            return cells
        assignment, groups = parents.assignment, parents.groups
        named = self.tuples(assignment.table, [assignment.group_column])
        group_names = self.tuples(groups.table, [groups.group_column])
        scoped = self.tuples(groups.table, list(scopes))
        if named is None or group_names is None or scoped is None:
            return "The coverage's group columns are not read"
        members: dict[Tuple, Tuple] = {}
        for owner, group in zip(owners, named, strict=True):
            if owner is None or group is None:
                continue
            if owner in members:
                return "A parent has more than one assignment row in the coverage (D401)"
            members[owner] = group
        listed: dict[Tuple, set[Tuple]] = {}
        for group, scope in zip(group_names, scoped, strict=True):
            if group is not None and scope is not None:
                listed.setdefault(group, set()).add(scope)
        size = sum(len(listed.get(group, ())) for group in members.values())
        if size > most:
            return (
                f"The coverage lists {size} cells, more than the table's rows and the cells "
                f"dropped as absent ({most}) (D401)"
            )
        for owner, group in members.items():
            for scope in listed.get(group, ()):
                add(owner, scope)
        return cells


# --- The fixture ---------------------------------------------------------------------------------


def _table(id: str, columns: Sequence[str], rows: Sequence[Sequence[Any]]) -> TypedTable:
    cells = {
        column: tuple(
            missing_cell(ObservationState.UNKNOWN) if row[i] is None else present(row[i])
            for row in rows
        )
        for i, column in enumerate(columns)
    }
    return TypedTable(id, tuple(columns), cells, len(rows), {})


def _descriptors(
    parents: int,
    scopes: int,
    key: Sequence[str],
    coverage: Mapping[str, Any],
    *,
    absent: bool = True,
) -> list[Descriptor]:
    child = [f"c{i}" for i in range(parents)]
    value: dict[str, Any] = {"permissible_values": {"values": [{"value": "1"}]}}
    if absent:
        value["absent"] = ["0"]
    return [
        build.table("readings", list(key), role="measurement"),
        build.column("readings.v", "category", **value),
        build.descriptor(
            "relationship",
            "rel:readings." + "+".join(child),
            {
                "child_table": "readings",
                "child_columns": child,
                "parent_table": "units",
                "parent_columns": [f"p{i}" for i in range(parents)],
                "cardinality": "many-to-one",
            },
        ),
        build.coverage("rel:readings." + "+".join(child), dict(coverage)),
    ]


def _grouped(parents: int, scopes: Sequence[int]) -> dict[str, Any]:
    return {
        "assignment": {
            "table": "plans",
            "parent_columns": {f"o{i}": f"p{i}" for i in range(parents)},
            "group_column": "plan",
        },
        "groups": {"table": "slots", "group_column": "plan", **_scoped(scopes)},
    }


def _scoped(scopes: Sequence[int]) -> dict[str, Any]:
    return {"scope_columns": {f"t{j}": f"s{j}" for j in scopes}} if scopes else {}


def _direct(parents: int, scopes: Sequence[int]) -> dict[str, Any]:
    return {
        "table": "listing",
        "parent_columns": {f"o{i}": f"p{i}" for i in range(parents)},
        **_scoped(scopes),
    }


def _found(checks: gate._Checks) -> list[Any]:
    return [
        (f.code, f.at, f.where, f.message[0].model_dump()["text"], f.rows) for f in checks.found
    ]


def _run(
    checks: type[gate._Checks],
    descriptors: Sequence[Descriptor],
    tables: Mapping[str, TypedTable],
    declared: Mapping[str, Declared],
) -> Any:
    found = checks(descriptors, tables)
    try:
        found.declarations(declared)
    except Exception as raised:  # the oracle's own exceptions are outcomes to match too
        return ("raised", type(raised))
    return _found(found)


def _listing(
    checks: gate._Checks, descriptors: Sequence[Descriptor], most: int
) -> tuple[Any, list[Tuple | None]]:
    relationship = next(d for d in descriptors if isinstance(d, RelationshipDescriptor))
    coverage = next(d for d in descriptors if isinstance(d, CoverageDescriptor))
    table = next(d for d in descriptors if isinstance(d, TableDescriptor))
    key = table.fields.primary_key or []
    parents = coverage.fields.parents
    assert isinstance(parents, DirectCoverage | GroupedCoverage)
    rows = checks.tuples("readings", key)
    assert rows is not None
    return checks.listed_cells(relationship, parents, key, most), rows


# --- Generated cases -----------------------------------------------------------------------------

TEXTS = (
    "",
    "a",
    "b",
    "ab",
    "ba",
    "é",
    "é",
    "\x00",
    "a\x00",
    "Ω",
    "\U0001f600",
    "x" * 255,
    "x" * 256,
    "x" * 257,
    "é" * 128,
    "z" * 200,
    "x" * 65_536,
    "é" * 32_768,
)
"""Values that order differently as Python's texts, by character length, by UTF-8 without the
length, and as the digest orders them (its length first): ``"b"``/``"ab"``, ``"é"``/``"ab"``,
``"é" * 128`` (256 bytes) and ``"z" * 200``, 255/256/257 and 65,536 bytes."""


def _value(rng: random.Random, pool: Sequence[Any]) -> Any:
    return None if rng.random() < 0.04 else rng.choice(pool)


def _case(
    rng: random.Random,
) -> tuple[list[Descriptor], dict[str, TypedTable], dict[str, Declared], set[str]]:
    """A coverage over generated values: one to three parent columns, none to three scope columns,
    the key in any order (parent columns first, scope columns first, or interleaved), grouped or
    direct, with parents assigned twice, plans without slots, units without a plan, repeated
    slots, missing cells, rows outside the listing, values that are not text, and declarations
    honest or not in count and digest; now and then a column not read, a key that is not the
    coverage's, parent columns that do not stand for the relationship's or a declared column
    without absent values. With them, the features it drew, read from what it drew: ``columns``
    (more than one parent column), ``mixed`` (texts beside numbers and booleans that print
    alike), ``long`` (a value of 65,536 bytes), ``surrogate`` and ``swapped`` (a declaration
    as many as the absent cells but with a held cell for one of them)."""
    parents = rng.choice((1, 1, 2, 3))
    scopes = list(range(rng.choice((0, 1, 1, 2, 2, 3))))
    rng.shuffle(scopes)
    columns = [f"c{i}" for i in range(parents)] + [f"s{j}" for j in scopes]
    key = list(columns)
    rng.shuffle(key)
    pool: list[Any] = list(rng.sample(TEXTS, rng.randint(2, 6)))
    if rng.random() < 0.08:
        pool = [rng.randint(0, 3) for _ in range(4)]  # integers, compared as key parts
    elif rng.random() < 0.06:
        pool = [*pool[:2], 1, True, "True", "1", 1.0]  # texts beside values that print alike
    elif rng.random() < 0.03:
        pool.append("\ud800")  # a surrogate, which the digest cannot encode
    plans = [f"g{n}" for n in range(rng.randint(1, 4))]
    drawn = [tuple(_value(rng, pool) for _ in range(parents)) for _ in range(rng.randint(0, 6))]
    units = list(dict.fromkeys(drawn))
    grouped = rng.random() < 0.8
    listed: list[tuple[Any, ...]] = []
    if grouped:
        coverage = _grouped(parents, scopes)
        assigned = [(*unit, _value(rng, plans)) for unit in units]
        if assigned and rng.random() < 0.1:
            assigned.append((*assigned[0][:-1], rng.choice(plans)))  # a unit assigned twice
        slots = [
            (_value(rng, plans), *(_value(rng, pool) for _ in scopes))
            for _ in range(rng.randint(0, 8))
        ]
        tables = {
            "plans": _table("plans", [*(f"o{i}" for i in range(parents)), "plan"], assigned),
            "slots": _table("slots", ["plan", *(f"t{j}" for j in scopes)], slots),
        }
    else:
        coverage = _direct(parents, scopes)
        listed = [(*unit, *(_value(rng, pool) for _ in scopes)) for unit in units for _ in range(2)]
        columns_of = [*(f"o{i}" for i in range(parents)), *(f"t{j}" for j in scopes)]
        tables = {"listing": _table("listing", columns_of, listed)}
    descriptors = _descriptors(parents, len(scopes), key, coverage, absent=rng.random() > 0.03)
    if rng.random() < 0.03:
        descriptors[0] = build.table("readings", [*key, "v"], role="measurement")
    if rng.random() < 0.03:
        swapped = dict(coverage)
        if grouped:
            assignment = dict(coverage["assignment"])
            assignment["parent_columns"] = {f"o{i}": f"p{i + 1}" for i in range(parents)}
            swapped["assignment"] = assignment
        else:
            swapped["parent_columns"] = {f"o{i}": f"p{i + 1}" for i in range(parents)}
        descriptors[3] = build.coverage(descriptors[2].id, swapped)
    tables["readings"] = _table("readings", [*key, "v"], [])
    checks = _OnMain(descriptors, tables)
    found, _ = _listing(checks, descriptors, 10**9)
    cells = sorted(found, key=repr) if isinstance(found, set) else []
    rows = [cell for cell in cells if rng.random() < 0.6]
    rows += [cell for cell in rows if rng.random() < 0.1]  # a key held twice
    if rng.random() < 0.1:
        rows.append(tuple((False, _value(rng, pool)) for _ in key))  # perhaps outside
    if rng.random() < 0.05 and rows:
        rows.append((*rows[0][:-1], (False, None)))
    values = [tuple(None if part[1] is None else part[1] for part in row) for row in rows]
    tables["readings"] = _table("readings", [*key, "v"], [(*v, "1") for v in values])
    if rng.random() < 0.03:
        del tables[rng.choice(sorted(t for t in tables if t != "readings"))]
    elif not grouped and scopes and rng.random() < 0.1:
        owners = [f"o{i}" for i in range(parents)]
        tables["listing"] = _table("listing", owners, [row[:parents] for row in listed])
    held = {row for row in rows if all(part[1] is not None for part in row)}
    absent = [cell for cell in cells if cell not in held]
    digest = _digest(absent)
    dropped = len(absent)
    shown = rng.random()
    other = absent
    if shown < 0.15:
        dropped += rng.choice((-2, -1, 1, 2))
    elif shown < 0.25:
        digest = digest[:-1] + ("0" if digest[-1] != "0" else "1")
    elif shown < 0.35 and absent and held:
        other = [*absent[1:], sorted(held, key=repr)[0]]
        digest = _digest(other)
    declared = {"readings": Declared(column="v", dropped=max(dropped, 0), digest=digest)}
    if rng.random() < 0.02:
        declared = {"readings": Declared(column="other", dropped=max(dropped, 0), digest=digest)}
    texts = [value for value in pool if isinstance(value, str)]
    features = {
        "columns": parents > 1,
        "mixed": any(isinstance(value, bool | float) for value in pool) and bool(texts),
        "long": any(len(value.encode("utf-8", "surrogatepass")) >= 65_536 for value in texts),
        "surrogate": any("\ud800" in value for value in texts),
        "swapped": other is not absent,
    }
    return descriptors, tables, declared, {name for name, drawn in features.items() if drawn}


def _digest(cells: Sequence[Tuple]) -> str:
    try:
        return cell_digest([str(part[1]) for part in cell] for cell in cells)
    except UnicodeEncodeError:  # a surrogate: the check raises as main's did
        return "0" * 64


def _outcome(found: Any) -> str:
    if not isinstance(found, list):
        return "raised"
    if not found:
        return "accepted"
    text = found[0][3]
    if text.startswith("The coverage lists cells without a row"):
        return "as many, other cells" if "(as many, " in text else "count differs"
    for start, named in OUTCOMES.items():
        if text.startswith(start):
            return named
    return text


OUTCOMES = {
    "The column the importer declared": "no absent values",
    "The coverage's parent columns do not stand": "parents do not stand",
    "The coverage's columns are not": "not the key",
    "The coverage's parent columns are not read": "parents not read",
    "The coverage's scope columns are not read": "scopes not read",
    "The coverage's group columns are not read": "groups not read",
    "A parent has more than one": "assigned twice",
    "The coverage lists ": "too many",
    "Rows lie outside": "outside",
}
"""Each refusal text the check can give, by its start."""


def _path(listing: Any) -> str:
    if isinstance(listing, str):
        return "refused"
    if isinstance(listing, gate._Streamed):
        return "scope first" if listing.scope_first else "parent first"
    return "built"


def test_the_streamed_check_has_the_outcomes_of_the_built_one() -> None:
    """Every outcome, refusal text, row, count and digest is the oracle's, over generated cases
    that reach each outcome and each path (streamed parent first, streamed scope first, and
    built: direct, interleaved, or a value that is not text), and each feature the generator
    draws on the paths it can take: more than one parent column, a value of 65,536 bytes and a
    declaration with a held cell for an absent one (refused as other cells) on every path, texts
    beside values that print alike and a surrogate on the built one."""
    rng = random.Random(401)
    outcomes: set[str] = set()
    paths: dict[str, set[str]] = {}
    drawn: dict[str, set[str]] = {}
    for _ in range(2500):
        descriptors, tables, declared, features = _case(rng)
        expected = _run(_OnMain, descriptors, tables, declared)
        assert _run(gate._Checks, descriptors, tables, declared) == expected
        outcomes.add(_outcome(expected))
        if "readings" in tables:
            listing, rows = _listing(gate._Checks(descriptors, tables), descriptors, 10**9)
            paths.setdefault(_path(listing), set()).add(_outcome(expected))
            if _outcome(expected) != "as many, other cells":
                features.discard("swapped")
            drawn.setdefault(_path(listing), set()).update(features)
            if isinstance(listing, gate._Streamed):
                built, _ = _listing(_OnMain(descriptors, tables), descriptors, 10**9)
                held = {row for row in rows if row is not None}
                assert listing.without(held) == gate._Built(built).without(held)
                assert all((row in listing) == (row in built) for row in held)
    checked = {"accepted", "as many, other cells", "count differs", "outside"}
    assert outcomes == {*OUTCOMES.values(), *checked, "raised"}, outcomes
    assert set(paths) == {"parent first", "scope first", "built", "refused"}, paths
    assert checked | {"too many"} <= paths["parent first"] & paths["scope first"], paths
    assert checked | {"too many", "raised"} <= paths["built"], paths
    every = {"columns", "long", "swapped"}
    assert every <= drawn["parent first"] & drawn["scope first"] & drawn["built"], drawn
    assert {"mixed", "surrogate"} <= drawn["built"], drawn


def test_an_interleaved_key_or_a_value_not_text_builds_the_listing() -> None:
    """The stream takes a key whose parent columns come first or whose scope columns come first
    (each side in any order of its own); a key that interleaves them, a direct coverage and a
    value that is not text build the listing, as main did, within the count's bound."""
    coverage = _grouped(2, [0])
    plans = _table("plans", ["o0", "o1", "plan"], [("u", "v", "g")])
    slots = _table("slots", ["plan", "t0"], [("g", "w")])
    readings = _table("readings", ["c0", "c1", "s0", "v"], [])
    tables = {"plans": plans, "slots": slots, "readings": readings}
    shapes = {
        ("c0", "c1", "s0"): "parent first",
        ("c1", "c0", "s0"): "parent first",
        ("s0", "c0", "c1"): "scope first",
        ("s0", "c1", "c0"): "scope first",
        ("c0", "s0", "c1"): "built",
        ("c1", "s0", "c0"): "built",
    }
    for key, path in shapes.items():
        descriptors = _descriptors(2, 1, key, coverage)
        tables["readings"] = _table("readings", [*key, "v"], [])
        listing, _ = _listing(gate._Checks(descriptors, tables), descriptors, 10)
        assert _path(listing) == path, key
    numbers = {**tables, "plans": _table("plans", ["o0", "o1", "plan"], [(1, 2, "g")])}
    descriptors = _descriptors(2, 1, ("c0", "c1", "s0"), coverage)
    listing, _ = _listing(gate._Checks(descriptors, numbers), descriptors, 10)
    assert _path(listing) == "built"
    direct = _descriptors(2, 1, ("c0", "c1", "s0"), _direct(2, [0]))
    listed = {**tables, "listing": _table("listing", ["o0", "o1", "t0"], [("u", "v", "w")])}
    listing, _ = _listing(gate._Checks(direct, listed), direct, 10)
    assert _path(listing) == "built"


def test_a_parent_first_stream_keys_each_parent_in_the_key_s_order() -> None:
    """A key that lists the parent columns in another order than the relationship's is streamed
    with each parent's values taken to the key's order, and so is a scope's."""
    coverage = _grouped(2, [0, 1])
    plans = _table("plans", ["o0", "o1", "plan"], [("b", "ab", "g"), ("ab", "b", "g")])
    slots = _table("slots", ["plan", "t0", "t1"], [("g", "é", "ab"), ("g", "ab", "é")])
    for key in (("c1", "c0", "s1", "s0"), ("s1", "s0", "c1", "c0"), ("c0", "c1", "s0", "s1")):
        held = [("b", "ab", "é", "ab")]  # in the relationship's order: o0, o1, t0, t1
        whole = ("c0", "c1", "s0", "s1")
        rows = [tuple(row[whole.index(column)] for column in key) for row in held]
        tables = {
            "plans": plans,
            "slots": slots,
            "readings": _table("readings", [*key, "v"], [(*row, "1") for row in rows]),
        }
        cells = [
            tuple(row[whole.index(column)] for column in key)
            for row in (
                ("b", "ab", "ab", "é"),
                ("ab", "b", "é", "ab"),
                ("ab", "b", "ab", "é"),
            )
        ]
        declared = {"readings": Declared(column="v", dropped=3, digest=cell_digest(cells))}
        descriptors = _descriptors(2, 2, key, coverage)
        assert _run(gate._Checks, descriptors, tables, declared) == []
        assert _run(_OnMain, descriptors, tables, declared) == []


# --- The digest, streamed ------------------------------------------------------------------------


def _ordered(cells: Sequence[Sequence[str]]) -> list[tuple[str, ...]]:
    def order(cell: Sequence[str]) -> list[tuple[int, bytes]]:
        return [(len(value.encode()), value.encode()) for value in cell]

    return sorted({tuple(cell) for cell in cells}, key=order)


def test_cell_digest_streamed_is_cell_digest() -> None:
    """Over random sets of cells of mixed widths, given in the digest's order (each value by its
    UTF-8 length, then its UTF-8), the streamed digest and its count are ``cell_digest``'s."""
    rng = random.Random(2)
    for _ in range(400):
        width = rng.randint(1, 3)
        cells = [
            tuple(rng.choice(TEXTS) for _ in range(rng.choice((width, width, width + 1))))
            for _ in range(rng.randint(0, 12))
        ]
        streamed = CellDigest()
        for cell in _ordered(cells):
            streamed.add(cell)
        assert (streamed.count, streamed.digest()) == (len(set(cells)), cell_digest(cells))
    assert CellDigest().digest() == cell_digest([]) == hashlib.sha256(b"").hexdigest()
    assert CellDigest().count == 0


@pytest.mark.parametrize(
    ("first", "then"),
    [
        (("ab",), ("b",)),  # Python's order of texts, which the digest's is not
        (("é",), ("ab",)),  # by characters, not bytes
        (("ab", "c"), ("ab", "b")),
        (("b",), ("b",)),  # twice
        ((SECRET, "b"), (SECRET, "b")),
    ],
    ids=["texts' order", "characters' length", "second value", "twice", "a value twice"],
)
def test_cell_digest_streamed_refuses_a_cell_out_of_order_or_twice(
    first: tuple[str, ...], then: tuple[str, ...]
) -> None:
    streamed = CellDigest()
    streamed.add(first)
    before = (streamed.count, streamed.digest())
    with pytest.raises(ValueError, match="out of the digest's order or twice") as refused:
        streamed.add(then)
    assert SECRET not in str(refused.value)
    assert (streamed.count, streamed.digest()) == before == (1, cell_digest([first]))


def test_cell_digest_streamed_takes_the_digest_s_order() -> None:
    streamed = CellDigest()
    for cell in (("b",), ("ab",), ("é",), ("x" * 255,), ("x" * 256,)):
        streamed.add(cell)
    assert streamed.digest() == cell_digest([("ab",), ("b",), ("é",), ("x" * 256,), ("x" * 255,)])


@pytest.mark.parametrize(
    "cell",
    [
        SECRET,
        (SECRET, 1),
        (SECRET.encode(),),
        ("\ud800" + SECRET,),
        5,
        {SECRET: SECRET},
    ],
    ids=["one text", "a number", "bytes", "a surrogate", "not a sequence", "a mapping"],
)
def test_cell_digest_streamed_refuses_what_is_not_a_cell_of_texts(cell: Any) -> None:
    """A bare text is a sequence of texts, which would digest its characters; it is refused, as
    is what is not a sequence and a value that is not a text or that UTF-8 cannot encode, with
    a message naming none, neither as text nor as its ``repr``, and no context that would."""
    streamed = CellDigest()
    with pytest.raises(ValueError, match=r"\(D401\)") as refused:
        streamed.add(cell)
    said = str(refused.value)
    assert SECRET not in said
    assert repr(SECRET.encode()) not in said
    assert refused.value.__cause__ is None
    assert refused.value.__context__ is None
    assert (streamed.count, streamed.digest()) == (0, cell_digest([]))


def test_a_refused_cell_leaves_the_order_where_it_was() -> None:
    """A cell refused for its order moves nothing: after ``("c",)`` refuses ``("a",)``, the
    next cell is still held to ``("c",)``, so ``("b",)`` is refused, ``("d",)`` taken, and the
    digest is ``cell_digest``'s of the cells taken."""
    streamed = CellDigest()
    streamed.add(("c",))
    with pytest.raises(ValueError, match="out of the digest's order"):
        streamed.add(("a",))
    with pytest.raises(ValueError, match="out of the digest's order"):
        streamed.add(("b",))
    streamed.add(("d",))
    assert (streamed.count, streamed.digest()) == (2, cell_digest([("c",), ("d",)]))


class _Text(str):
    """A text of a subclass of ``str``, which a cell may hold but never be."""


DOMAIN: tuple[Any, ...] = (
    (),
    ("",),
    ("a",),
    (_Text("a"),),
    ("b",),
    ("ab",),
    ("é",),
    ("", ""),
    ("a", ""),
    ("a", "b"),
    ("ab", ""),
    (_Text("a"), "b"),
    ["b", "a"],
)
"""Cells of no value, one and two values, as a tuple or a list, with a value of a subclass of
``str``: their digest's order is neither their texts' nor their widths'."""


def _place(cell: Sequence[str]) -> tuple[tuple[int, bytes], ...]:
    """A cell's place in the digest's order as SPEC D401 states it: its values in turn, each by
    its UTF-8 length and then its UTF-8 bytes (a cell before any it is a prefix of)."""
    return tuple((len(value.encode()), value.encode()) for value in cell)


def test_cell_digest_streamed_takes_exactly_the_increasing_sequences() -> None:
    """Every sequence of one to three cells of ``DOMAIN``: the streamed digest takes each cell
    exactly while it comes strictly after the last one taken, the empty cell being a cell like
    any other (``cell_digest`` digests it, before every other), refuses it otherwise without
    moving, and its count and digest are always ``cell_digest``'s of the cells taken."""
    for size in (1, 2, 3):
        for cells in itertools.product(DOMAIN, repeat=size):
            streamed = CellDigest()
            taken: list[Sequence[str]] = []
            for cell in cells:
                after = not taken or _place(cell) > _place(taken[-1])
                if after:
                    streamed.add(cell)
                    taken.append(cell)
                else:
                    with pytest.raises(ValueError, match="out of the digest's order or twice"):
                        streamed.add(cell)
                assert streamed.count == len(taken)
                assert streamed.digest() == cell_digest(taken)
    assert cell_digest([()]) != cell_digest([])


@pytest.mark.parametrize("cell", [_Text("ab"), _Text("")], ids=["a text", "an empty text"])
def test_cell_digest_streamed_refuses_a_bare_text_of_a_subclass(cell: str) -> None:
    streamed = CellDigest()
    with pytest.raises(ValueError, match="not one text"):
        streamed.add(cell)
    assert (streamed.count, streamed.digest()) == (0, cell_digest([]))


def test_the_check_s_version_is_pinned() -> None:
    """Streaming the listing is a change of the check's logic (D401): its version is bumped, so
    a release an earlier version checked is checked again at its next change."""
    assert gate.CHECK_VERSION == "aibi.reshaped-coverage/2"


# --- Bounds: memory and time ---------------------------------------------------------------------


def _shape(units: int, plans: int, scopes: int, *, scope_first: bool, rows: int) -> Any:
    """``units`` units over ``plans`` plans (unit ``n`` follows plan ``n % plans``), each plan
    listing ``scopes`` scopes of its own, and ``rows`` rows held, the first unit's; every other
    listed cell declared dropped, under a digest that differs, so the whole listing is streamed
    and refused as other cells."""
    key = ("s0", "c0") if scope_first else ("c0", "s0")
    assigned = [(f"u{n}", f"g{n % plans}") for n in range(units)]
    slots = [(f"g{g}", f"{g}-{s}") for g in range(plans) for s in range(scopes)]
    held = [("u0", f"0-{s}") for s in range(rows)]
    if scope_first:
        held = [(scope, unit) for unit, scope in held]
    tables = {
        "plans": _table("plans", ["o0", "plan"], assigned),
        "slots": _table("slots", ["plan", "t0"], slots),
        "readings": _table("readings", [*key, "v"], [(*row, "1") for row in held]),
    }
    descriptors = _descriptors(1, 1, key, _grouped(1, [0]))
    listed = units * scopes
    declared = {"readings": Declared(column="v", dropped=listed - rows, digest="0" * 64)}
    return descriptors, tables, declared, listed


def _peak(case: Any) -> tuple[int, float]:
    """The peak of memory the check allocates (``tracemalloc``), and its seconds."""
    descriptors, tables, declared, listed = case
    checks = gate._Checks(descriptors, tables)
    tracemalloc.start()
    try:
        tracemalloc.reset_peak()
        base = tracemalloc.get_traced_memory()[0]
        began = time.perf_counter()
        checks.declarations(declared)
        took = time.perf_counter() - began
        peak = tracemalloc.get_traced_memory()[1] - base
    finally:
        tracemalloc.stop()
    [failure] = _found(checks)
    assert f"(as many, {declared['readings'].dropped}, but other cells)" in failure[3]
    assert declared["readings"].dropped < listed
    return peak, took


@pytest.mark.parametrize("scope_first", [False, True], ids=["parent first", "scope first"])
def test_the_check_s_memory_does_not_grow_with_the_listed_cells(scope_first: bool) -> None:
    """Few plans: 2,500 units over two plans, the plans' scopes from 10 to 40, so 25,000 to
    100,000 listed cells over the same rows: the check's peak stays that of the coverage tables'
    rows (about half a MiB), where main's built listing grew fourfold (about 250 bytes a cell:
    24 MiB at 100,000)."""
    small, _ = _peak(_shape(2500, 2, 10, scope_first=scope_first, rows=5))
    large, _ = _peak(_shape(2500, 2, 40, scope_first=scope_first, rows=5))
    assert large < 1.25 * small + 100_000, (small, large)
    assert large < 2 * 2**20, large


@pytest.mark.parametrize("scope_first", [False, True], ids=["parent first", "scope first"])
def test_the_check_s_memory_is_the_coverage_tables_with_a_plan_per_unit(scope_first: bool) -> None:
    """A plan per unit, each listing a scope of its own: 5,000 to 20,000 units, as many listed
    cells as the assignment's and the plans' rows. The peak grows with those rows, about fourfold,
    and within a bound per unit (measured: about 800 bytes parent first and 1,050 scope first,
    reading the coverage tables included; main took about 840) that a generator per plan (about
    4 KiB a plan, round 1 of the plan's review) or a listing kept beside the stream would pass."""
    small, _ = _peak(_shape(5000, 5000, 1, scope_first=scope_first, rows=1))
    large, _ = _peak(_shape(20_000, 20_000, 1, scope_first=scope_first, rows=1))
    assert large < 5 * small, (small, large)
    assert large < 20_000 * BYTES_PER_UNIT[scope_first], large


BYTES_PER_UNIT = {False: 1000, True: 1300}
"""A bound on the check's peak per unit with a plan per unit: what was measured and a quarter."""


def test_a_scope_first_stream_merges_rather_than_filters() -> None:
    """A plan per unit, each listing a scope of its own, keyed scope first: 20,000 scopes and
    20,000 units, which a filter of the units for each scope would take 400 million steps over;
    the merge takes about the listed cells' times the log of the plans."""
    descriptors, tables, declared, _ = _shape(20_000, 20_000, 1, scope_first=True, rows=1)
    checks = gate._Checks(descriptors, tables)
    began = time.perf_counter()
    checks.declarations(declared)
    assert time.perf_counter() - began < 5.0
    assert "other cells" in _found(checks)[0][3]


@pytest.mark.parametrize("scope_first", [False, True], ids=["parent first", "scope first"])
def test_each_parent_and_each_scope_is_sorted_once(
    scope_first: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each parent's and each listed scope's place in the digest's order is taken once: 500
    units over two plans of 40 scopes each, 20,000 listed cells, take at most 580 encodings,
    never one per listed cell (a group's sorted scopes are kept while a parent of it is to
    come)."""
    encoded: list[int] = []
    real = gate._encoded

    def counted(parts: Tuple) -> bytes:
        encoded.append(1)
        return real(parts)

    monkeypatch.setattr(gate, "_encoded", counted)
    descriptors, tables, declared, listed = _shape(500, 2, 40, scope_first=scope_first, rows=1)
    checks = gate._Checks(descriptors, tables)
    checks.declarations(declared)
    assert f"(as many, {listed - 1}, but other cells)" in _found(checks)[0][3]
    assert len(encoded) <= 500 + 2 * 40

"""Applicability by an index of the release (SPEC §9.4, D316, D353, D420).

``Analyses.applicable`` indexes the release's descriptors once per call (``registry._index``):
per requirement kind the number of descriptors that meet it and of those settled, per table;
per requirement shape, the units' counts sorted with the units below each distinct count, so
that a requirement of any ``min`` finds the units it misses and those where it is unconfirmed by
bisection (``_Index.signature``); each analysis judged at the first best unit
(``registry._choice``) and its roles found there alone (``registry._roles``). It must answer
exactly as the scan it replaced, kept verbatim in ``applicability_oracle`` as the brute force.
The tests are of two kinds, and only the first is exhaustive or repeatable whatever hypothesis
does (a plain loop over fixed seeds, or no draw at all):

- exhaustive and seeded (no hypothesis): the helpers at every width from 0 to 300 around each
  byte and word (``_prefix``, ``_below``, ``_bits``, ``_Index.every``, a real ``_Index`` of given
  counts with ``signature``, ``_choice`` and ``_roles``, a predicate that holds or fails on a
  requirement with a kind or none); the index's counts at every unit, for every kind, ``on``
  and datatype, of releases of 64 to 300 tables (``test_the_index_counts_every_unit...``);
  300 edge releases whose deciding unit is at each edge (``test_edge_releases_are_the_scan``,
  a unit named in one in seven, an unkeyed or coverage table or an absent one among them); 50
  graded releases (``test_wide_releases_are_the_scan``); every requirement shape there is, and
  every ordered pair of them with predicates, over 3, 65 and 130 keyed tables and a unit keyed,
  unkeyed, coverage or absent (``test_every_requirement_shape_is_the_scan``,
  ``test_every_pair_of_requirement_shapes...``); the examples the generated tests let through,
  one for each mutant they let through; the shapes of the generators and the guards of these
  tests' constants and seeds
  (``test_the_graded_releases_have_their_shape``); the cost (the work of a call held to its
  formula, the reads of each descriptor held under a constant); the state (a call changes
  nothing it is given, two threads sharing one registry);
- sampled by hypothesis, seeded (``@seed``; the same command draws the same examples, another
  selection or a mutated module others): end to end over releases and registries
  (``test_applicable_is_the_scan``, with the shares it drew held by
  ``test_the_end_to_end_examples_have_their_shares``; and with the registry as built,
  ``test_the_registry_as_built_is_the_scan``, whose entries are copied on each call), the
  entries a call leaves as they were (``test_a_call_leaves_the_entries_it_reads_as_they_were``),
  and a signature and a choice against the scan per unit.
"""

import inspect
import random
import sys
import threading
import types
from collections import Counter
from collections.abc import Callable, Iterator, Sequence
from functools import cache
from typing import Any, ClassVar, cast

import pytest
from hypothesis import HealthCheck, Phase, given, seed, settings
from hypothesis import strategies as st
from tests.core.analyses.applicability_oracle import (
    _CORE,
    _DATATYPES,
    _KINDS,
    _MINS,
    _ON,
    _REQUIREMENTS,
    _WIDE_MINS,
    BIG,
    DATATYPES,
    UNITS,
    USED,
    Quick,
    QuickScan,
    Scan,
    _categories_from,
    _cut,
    brute_count,
    brute_signature,
    counts_of,
    edge_registry,
    edge_release,
    entry,
    first_best,
    graded_release,
    is_the_scan,
    least_of,
    outcome,
    predicate,
    registries,
    registry_of,
    releases,
    requirement,
    scale_release,
    scan_choice,
    wide_registry,
    wide_release,
)

from aibi.core.analyses import registry
from aibi.core.analyses.registry import Analyses, Registered
from aibi.core.engine import build
from aibi.core.schema.descriptors import ColumnDescriptor, Descriptor
from aibi.core.schema.pack_api import PackRegistry
from aibi.core.schema.results import PackVersion

EXAMPLES = settings(
    max_examples=800,
    deadline=None,
    phases=[Phase.generate],
    suppress_health_check=cast(list[HealthCheck], list(HealthCheck)),
    database=None,
)
"""Generated only (none shrunk or replayed) and seeded (``@seed``, on each test): the same command
draws the same examples, but hypothesis also reads the constants of the modules loaded, so
another selection of tests, or a mutated module, draws others. What the claims rest on is the
exhaustive and the seeded tests below, which draw nothing from hypothesis."""
PARTS = settings(EXAMPLES, max_examples=1_000)
"""The tests of the index's parts, whose examples are cheaper to check but as cheap to draw."""


# --- The helpers, exhaustively (no draw from hypothesis: seeded and complete) -------------------

WIDTHS = (
    *range(13),
    15,
    16,
    17,
    31,
    32,
    33,
    63,
    64,
    65,
    66,
    127,
    128,
    129,
    130,
    199,
    200,
    201,
    256,
    300,
)
"""The numbers of units tried: every one to 12, and beside each width at which a bitset gains a
byte, fills a word or two, or a hundred more."""
POOLS = (range(7), range(60, 71), (0, 1, 2**53 - 1, 2**53 - 2, 64, 65))
"""The values a unit's count takes: few, near a word's width, and huge."""


def _set(flags: Sequence[object]) -> int:
    return sum(1 << position for position, flag in enumerate(flags) if flag)


def test_prefix_and_below_find_the_units_below_each_count_at_every_width() -> None:
    """``_prefix`` of the counts over ``n`` units, asked ``_below`` each ``min`` (beside each
    count, 0, 1, 63 to 65 and 2^53-1), is the set of the units whose count is below it, and its
    distinct counts and its set of every unit are as they are: at every width in ``WIDTHS``,
    for counts of each pool, so that no bit order, byte, word or cut passes."""
    rng = random.Random(7)
    for width in WIDTHS:
        for pool in POOLS:
            for _ in range(6):
                values = [rng.choice(list(pool)) for _ in range(width)]
                prefix = registry._prefix(values)
                assert prefix[0] == sorted(set(values)), width
                assert prefix[2] == (1 << width) - 1, width
                leasts = {0, 1, 2, 63, 64, 65, 2**53 - 1}
                leasts |= {one + step for one in values for step in (-1, 0, 1) if one + step >= 0}
                for least in leasts:
                    want = _set([one < least for one in values])
                    assert registry._below(prefix, least) == want, (width, least)


def test_bits_and_every_are_the_units_named_at_every_width() -> None:
    """``_bits`` of the units among some tables is their set, and ``_Index.every`` is that of
    every unit (the units counted as given, a table twice counted twice), at every width."""
    rng = random.Random(8)
    for width in WIDTHS:
        for twice in (False, True):
            units = [f"u{position}" for position in range(width)] + (
                ["u0"] if twice and width else []
            )
            tables = {one for one in units if rng.random() < 0.3}
            assert registry._bits(units, tables) == _set([one in tables for one in units]), width
            index = registry._Index(units, [0, 0], {}, {}, [0, 0], {}, set())
            assert index.every == (1 << len(units)) - 1, width


_LEASTS = (1, 2, 3, 63, 64, 65, 70, 2**53 - 1)


def _counts_of_units(rng: random.Random, width: int, least: int) -> list[tuple[int, int]]:
    """A (count, settled) for each of ``width`` units around ``least``: early units poor, the
    rest at it, beside it or far from it, settled below it or not."""
    cut = rng.randint(0, width)
    found: list[tuple[int, int]] = []
    for position in range(width):
        if position < cut and rng.random() < 0.9:
            count = rng.choice([0, 0, 1, max(least - 1, 0)])
        else:
            count = rng.choice([least, least, least + 1, max(least - 1, 0), 2 * least, 0])
        found.append((min(count, 2**53 - 1), 0))
    return [(count, max(count - rng.choice([0, 0, 0, 1, 3, count]), 0)) for count, _ in found]


def test_signatures_choices_and_roles_over_synthetic_counts_at_every_width() -> None:
    """A real ``_Index`` of ``n`` units whose counts of each requirement are given (no release,
    so nothing of a generator): each signature is the units whose count is below the ``min``
    and those met by fewer settled; ``_choice`` is the first unit met by settled counts, else
    the first met, else the first, whatever the widths and the predicates (a failing one misses
    every unit), and under *k* among the units with categories; and ``_roles`` at it names the
    roles as the counts say. A signature is asked twice, so that the cache is read too."""
    rng = random.Random(9)
    version = PackVersion(version="1.0.0", results_version=1)
    datatypes = ("integer", "string", "boolean")
    core = next(one for one in _CORE if one.id == "compare.columns")
    seen: set[tuple[str, str]] = set()
    used = USED["first_best"]
    for width in (one for one in WIDTHS if one):
        for trial in range(40):
            units = [f"u{position}" for position in range(width)]
            requires: list[dict[str, Any]] = []
            given: dict[str, list[tuple[int, int]]] = {}
            columns: dict[tuple[str | None, str], list[int]] = {}
            for number in range(rng.randint(1, 3)):
                least = rng.choice(_LEASTS)
                datatype = datatypes[number]
                given[datatype] = _counts_of_units(rng, width, least)
                requires.append(
                    {
                        "role": f"r{number}",
                        "kind": "column",
                        "on": "unit",
                        "datatype": datatype,
                        "min": least,
                    }
                )
                for unit, (count, settled) in zip(units, given[datatype], strict=True):
                    columns[(unit, datatype)] = [count, settled]
            held: dict[str, bool] = {}
            if trial % 5 == 0:
                requires.append({"role": "p", "predicate": "p.x"})
                held["p.x"] = rng.random() < 0.5
            elif trial % 5 in (1, 2):
                # a predicate on a requirement with a kind, the first or the last, which the
                # units may also miss
                requires[0 if trial % 5 == 1 else -1]["predicate"] = "p.x"
                held["p.x"] = rng.random() < 0.5
            analysis = Registered(entry("p", 0, requires), "p", None, version)
            categorised = {one for one in units if rng.random() < 0.4}
            index = registry._Index(units, [0, 0], {}, columns, [0, 0], {}, categorised)
            missed = [False] * width
            unconfirmed = [False] * width
            for one in (one for one in requires if "kind" in one):
                counts = given[one["datatype"]]
                least = one["min"]
                for _ in range(2):
                    got = index.signature("column", "unit", one["datatype"], least)
                    assert got == (
                        _set([count < least for count, _ in counts]),
                        _set([count >= least and settled < least for count, settled in counts]),
                    ), (width, least)
                for position, (count, settled) in enumerate(counts):
                    missed[position] |= count < least
                    unconfirmed[position] |= count >= least and settled < least
            if held.get("p.x") is False:
                missed = [True] * width
            want = first_best(missed, unconfirmed)
            seen.update(
                ("missed" if one else "met", "caveats" if two and not one else "settled")
                for one, two in zip(missed, unconfirmed, strict=True)
            )
            assert registry._choice(index, analysis, held, None, False) == want, (width, trial)
            _, missing, caveats = registry._roles(index, analysis, held, units[want])
            roles = {one["role"]: one for one in requires if "kind" in one}
            failing = held.get("p.x") is False
            for role, one in roles.items():
                count, settled = given[one["datatype"]][want]
                fails = failing and one.get("predicate") == "p.x"
                assert (role in missing) == (count < one["min"] or fails), (width, role)
                assert (role in caveats) == (
                    count >= one["min"] and settled < one["min"] and role not in missing
                ), (width, role)
            has_p = any(one["role"] == "p" for one in requires)
            assert ("p" in missing) == (has_p and failing), width
            # under k, a view that shows only numbers' units: among the units with categories
            total = rng.choice([0, 1, 5])
            index = registry._Index(
                units,
                [0, 0],
                {},
                {(None, registry._EVERY): [total, total]},
                [0, 0],
                {},
                categorised,
            )
            met = [
                position for position in range(width) if total and units[position] in categorised
            ]
            want = met[0] if met else 0
            assert registry._choice(index, core, {}, 3, False) == want, (width, trial)
    assert seen >= {("missed", "settled"), ("met", "settled"), ("met", "caveats")}, seen
    assert USED["first_best"] - used == 40 * len([one for one in WIDTHS if one])


# --- The index at scale: every count at every unit, releases of 64 to 300 tables ------------------

_COMBOS = (
    ("endpoint", None, None),
    ("endpoint", "unit", None),
    ("column", None, None),
    ("column", "unit", None),
    *(("column", on, datatype) for on in (None, "unit") for datatype in DATATYPES[:6]),
    ("table", None, None),
    ("table", "unit", None),
)


SCALES = (64, 70, 130, 300)


@pytest.mark.parametrize("tables", SCALES)
def test_the_index_counts_every_unit_as_the_scan_does_past_64_tables(tables: int) -> None:
    """``_Index.count`` of each requirement (every one of ``_COMBOS``: each kind, ``on`` and six
    datatypes or any) at every unit of a release of 64 to 300 keyed tables (columns of six
    datatypes, a third proposed, endpoints on two in three, tables of every role and none, a
    coverage table in twelve, a quarter of the tables proposed, two given twice) is the scan's
    count and settled count (``brute_count``, which runs the scan's own ``_matches``): the
    per-table endpoint and column counts are many (past 64 tables, past 300 keys)."""
    descriptors = scale_release(random.Random(tables), tables)
    index = registry._index(descriptors, None)
    assert len(index.units) == tables + 2
    used = USED["brute_count"]
    for unit in index.units:
        for kind, on, datatype in _COMBOS:
            assert index.count(kind, on, datatype, unit) == brute_count(
                descriptors, kind, on, datatype, unit
            ), (unit, kind, on, datatype)
    assert USED["brute_count"] - used == len(index.units) * len(_COMBOS)


def test_the_scans_count_is_the_scans_and_not_the_index() -> None:
    """``brute_count`` does not read the index, and counts as the scan does: two integer
    columns, one proposed, on a unit; a table other than it; an endpoint with no entry."""
    assert "_index" not in inspect.getsource(brute_count)
    descriptors = [
        build.dataset(),
        build.table("a", ["c0"]),
        build.table("b", ["c0"], role="coverage"),
        build.table("c", ["c0"]),
        build.column("a.c0", "integer"),
        build.column("a.c1", "integer", status="proposed"),
        build.descriptor(
            "endpoint",
            "ep:a",
            {
                "table": "a",
                "time_column": "c0",
                "status_column": "c1",
                "event_coding": {"event": ["a"], "censored": ["b"]},
            },
        ),
    ]
    assert brute_count(descriptors, "column", "unit", "integer", "a") == (2, 1)
    assert brute_count(descriptors, "column", "unit", "integer", "c") == (0, 0)
    assert brute_count(descriptors, "table", None, None, "a") == (1, 1)
    assert brute_count(descriptors, "endpoint", "unit", None, "a") == (1, 0)
    index = registry._index(descriptors, None)
    assert index.count("column", "unit", "integer", "a") == (2, 1)
    assert index.count("endpoint", "unit", None, "a") == (1, 0)


# --- Edge releases: the unit that decides at each width's edge (seeded, not drawn) ---------------

EDGE = 300
"""The seeds ``0`` to ``EDGE - 1`` of the edge releases: a plain loop, so that the same releases
are judged on every run, whatever hypothesis does."""


def test_edge_releases_are_the_scan() -> None:
    """Releases of up to 210 keyed tables whose deciding unit is at each edge of a width (the
    units before it with the column unconfirmed, absent or both; a table given twice; 62 to 66
    columns in a quarter; tables of every role and none; failing predicates, also on a
    requirement with a kind, a ``min`` of 2^53-1, endpoints, tables) against registries of the
    same kind: the scan's answer (outputs and predicate order) for each, under *k* or not, the
    units tried or, in one in seven, one named (a keyed table, the unkeyed ``u0``, the coverage
    table ``cv0`` or ``ghost``)."""
    big = late = 0
    shapes: dict[str, int] = {}
    used = USED["is_the_scan"]
    for number in range(EDGE):
        draw = random.Random(number)
        found, names, wide, at, mode, twice = edge_release(draw)
        packs = edge_registry(draw, wide)
        k = draw.choice([None, 3])
        unit = None if draw.random() < 0.85 else draw.choice([*names, "u0", "cv0", "ghost"])
        big += len(names) >= 127 and unit is None
        late += at >= 64
        for one in (mode, "wide" if wide else "narrow", "twice" if twice else "once"):
            shapes[one] = shapes.get(one, 0) + 1
        assert is_the_scan(packs, found, unit, k), number
    assert USED["is_the_scan"] - used == EDGE
    assert EDGE >= 300
    assert big >= EDGE // 20, big
    assert late >= EDGE // 10, late
    # the generator's shares: each mode, wide releases, a table given twice
    for one in ("unconfirmed", "missing", "both", "wide", "narrow", "twice", "once"):
        assert EDGE // 8 <= shapes[one] <= EDGE * 7 // 8, (one, shapes)


def test_the_comparison_with_the_scan_can_fail() -> None:
    """``is_the_scan`` tells a registry that answers otherwise (one that calls the first
    analysis unavailable) from the indexed one, so that the loops above compare with the scan."""

    class Wrong(Quick):
        def applicable(self, *given: Any, **named: Any) -> Any:
            found = super().applicable(*given, **named)
            found[0] = found[0].model_copy(update={"status": "unavailable", "missing": ["wrong"]})
            return found

    descriptors = [build.dataset(), build.table("a", ["c0"])]
    assert is_the_scan(None, descriptors, None, None)
    assert not is_the_scan(None, descriptors, None, None, Wrong)


def _units(
    tables: int, *, settled_from: int | None = None, column_from: int = 0
) -> list[Descriptor]:
    """``tables`` keyed tables, each with an integer column from ``column_from`` on, proposed
    before ``settled_from``."""
    found: list[Descriptor] = [build.dataset()]
    for number in range(tables):
        found.append(build.table(f"t{number:03d}", ["c0"]))
        if number >= column_from:
            late = settled_from is not None and number < settled_from
            found.append(
                build.column(
                    f"t{number:03d}.c0", "integer", status="proposed" if late else "asserted"
                )
            )
    return found


_INTEGER = {"role": "x", "kind": "column", "on": "unit", "datatype": "integer"}


def _same_as_the_scan(
    packs: PackRegistry, descriptors: list[Descriptor], k: int | None = None
) -> list[dict[str, Any]]:
    assert is_the_scan(packs, descriptors, None, k)
    found, _ = outcome(Quick(packs), descriptors, None, k)
    return [one for one in found if one["analysis"].startswith("p.")]


@pytest.mark.parametrize("edge", [8, 16, 32, 64, 128])
def test_the_first_unit_settled_is_found_past_each_width(edge: int) -> None:
    """Every table has the integer column, proposed before the ``edge``-th: the analysis is
    judged at it, available, not at the first with caveats (settled units preferred in a set of
    more than ``edge`` units)."""
    found = _same_as_the_scan(registry_of({"p": [[_INTEGER]]}), _units(edge + 3, settled_from=edge))
    assert found[0]["status"] == "available"


@pytest.mark.parametrize("edge", [8, 16, 32, 64, 128])
def test_a_failing_predicate_misses_every_unit_past_each_width(edge: int) -> None:
    """The column is only from the ``edge``-th table on, the predicate fails: unavailable at
    every unit, so judged at the first (missing both roles), not at one past the edge."""
    packs = registry_of(
        {"p": [[_INTEGER, {"role": "y", "predicate": "p.no"}]]},
        {"p": {"no": predicate("p.no", False)}},
    )
    found = _same_as_the_scan(packs, _units(edge + 3, column_from=edge))
    assert found[0]["missing"] == ["x", "y"]


@pytest.mark.parametrize("tables", [9, 33, 65, 129])
def test_a_table_given_twice_is_two_units_with_the_last_deciding(tables: int) -> None:
    """The first table is given twice and only the last has the column: its unit is the
    ``tables + 1``-th, so the set of every unit is as long as the units, not the tables."""
    descriptors = _units(tables, column_from=tables - 1)
    descriptors.insert(2, build.table("t000", ["c0"]))
    found = _same_as_the_scan(registry_of({"p": [[_INTEGER]]}), descriptors)
    assert found[0]["status"] == "available"


def test_a_min_of_two_to_the_53_minus_1_is_a_min_not_a_zero() -> None:
    """``min`` 2^53-1 for any column is met nowhere (the column is only from the second table)
    though the integer column is at the second: unavailable, naming both roles."""
    requires = [{"role": "y", "kind": "column", "min": 2**53 - 1}, _INTEGER]
    found = _same_as_the_scan(registry_of({"p": [requires]}), _units(3, column_from=1))
    assert found[0]["missing"] == ["x", "y"]


@pytest.mark.parametrize("tables", [65, 70, 129])
def test_endpoints_of_every_table_count_past_64_tables(tables: int) -> None:
    """Every table has a usable endpoint and the integer column is only on the last: the
    analysis is judged at the last (endpoints counted at every unit, not the first 64)."""
    descriptors = _units(tables, column_from=tables - 1)
    for number in range(tables):
        name = f"t{number:03d}"
        descriptors.append(build.column(f"{name}.c1", "string"))
        descriptors.append(build.column(f"{name}.c2", "string"))
        fields = {
            "table": name,
            "time_column": "c1",
            "status_column": "c2",
            "event_coding": {"event": ["a"], "censored": ["b"]},
            "entry": "at_origin",
        }
        descriptors.append(build.descriptor("endpoint", f"ep:{name}", fields))
    packs = registry_of({"p": [[{"role": "e", "kind": "endpoint", "on": "unit"}, _INTEGER]]})
    assert _same_as_the_scan(packs, descriptors)[0]["status"] == "available"


@pytest.mark.parametrize("tables", [101, 200])
def test_columns_of_every_table_count_past_300_keys(tables: int) -> None:
    """Every table has two columns of other datatypes and the integer column is only on the
    last: the analysis is judged at the last (per-table keys past 300, not the first 300)."""
    descriptors: list[Descriptor] = [build.dataset()]
    descriptors += [build.table(f"t{number:03d}", ["c0"]) for number in range(tables)]
    for number in range(tables):
        integer = "integer" if number == tables - 1 else "string"
        descriptors.append(build.column(f"t{number:03d}.c0", integer))
        descriptors.append(build.column(f"t{number:03d}.c1", "boolean"))
    assert (
        _same_as_the_scan(registry_of({"p": [[_INTEGER]]}), descriptors)[0]["status"] == "available"
    )


def test_unconfirmed_is_found_for_a_min_past_64() -> None:
    """Two units of 66 integer columns, three proposed at the first, ``min`` 64: the first is met
    but not by 64 settled (unconfirmed), the second is met by 66: available at the second, not
    ``available_with_caveats`` at the first."""
    descriptors: list[Descriptor] = [
        build.dataset(),
        build.table("a", ["c0"]),
        build.table("b", ["c0"]),
    ]
    status = ["proposed" if number < 3 else "asserted" for number in range(66)]
    descriptors += [
        build.column(f"a.c{number}", "integer", status=state) for number, state in enumerate(status)
    ]
    descriptors += [build.column(f"b.c{number}", "integer") for number in range(66)]
    packs = registry_of({"p": [[{**_INTEGER, "min": 64}]]})
    assert _same_as_the_scan(packs, descriptors)[0]["status"] == "available"


def test_a_release_with_no_unit_is_unavailable_with_what_refuses_it() -> None:
    """No keyed table (none at all, none keyed, or only an unkeyed one), a unit named that is
    not a table: every analysis is unavailable, naming ``unit`` (when none is tried), the
    settings that refuse it under *k* or without row ids, and a pack analysis's date column
    that no view is handed; as the scan says, for the core's registry and a pack's."""
    requires = [
        [{"role": "d", "kind": "column", "datatype": "date"}],
        [{"role": "x", "kind": "column", "datatype": "integer"}],
        [{"role": "p", "predicate": "p.yes"}],
    ]
    packs = registry_of({"p": requires}, {"p": {"yes": predicate("p.yes", True)}})
    cases: list[list[Descriptor]] = [
        [],
        [build.dataset()],
        [build.dataset(), build.table("a", None)],
        [
            build.dataset(disclosure={"min_cell_count": 3, "allow_row_ids": False}),
            build.table("a", None),
            build.column("a.c0", "integer"),
        ],
    ]
    for descriptors in cases:
        for registry_ in (None, packs):
            for unit in (None, "ghost"):
                for k in (None, 3):
                    assert outcome(Quick(registry_), descriptors, unit, k) == outcome(
                        QuickScan(registry_), descriptors, unit, k
                    ), (len(descriptors), unit, k)
    found, _ = outcome(Quick(packs), cases[1], None, 3)
    judged = next(one for one in found if one["analysis"] == "p.a000")
    assert judged["missing"] == ["d", "min_cell_count", "unit"]


def test_predicates_are_called_in_the_order_of_the_requirements_and_the_entries_kept() -> None:
    """An analysis whose requirements are in no order of their roles (``z`` then ``a``) with
    two predicates: the real registry calls them in the requirements' order, as the scan does,
    and leaves the entries it reads as they were (a sort of the requirements in place would
    call the other first)."""
    requires = [
        {"role": "z", "predicate": "p.yes"},
        {"role": "a", "predicate": "p.no"},
        {"role": "m", "kind": "column", "on": "unit", "datatype": "integer"},
    ]
    packs = registry_of(
        {"p": [requires]},
        {"p": {"yes": predicate("p.yes", True), "no": predicate("p.no", False)}},
    )
    descriptors = _units(3, column_from=1)
    before = _dumped(Analyses(packs))
    got = outcome(Analyses(packs), descriptors, None, None)
    assert got == outcome(Scan(packs), descriptors, None, None)
    assert got[1] == ["p.yes", "p.no"]
    assert _dumped(Analyses(packs)) == before
    quick = Quick(packs)
    before = _dumped(quick)
    assert outcome(quick, descriptors, None, None)[1] == ["p.yes", "p.no"]
    assert _dumped(quick) == before


def test_a_failing_predicate_of_a_requirement_with_a_kind_misses_it_beside_the_others() -> None:
    """Tables ``a`` and ``b`` (``b`` has an integer column): requirement ``x`` the integer column
    of the unit, ``y`` any column with a predicate that fails. Every unit misses ``y`` and ``a``
    misses ``x`` too, so the analysis is judged at the first, naming both."""
    descriptors = [
        build.dataset(),
        build.table("a", ["c0"]),
        build.table("b", ["c0"]),
        build.column("b.c0", "integer"),
    ]
    requires = [_INTEGER, {"role": "y", "kind": "column", "predicate": "p.no"}]
    packs = registry_of({"p": [requires]}, {"p": {"no": predicate("p.no", False)}})
    assert _same_as_the_scan(packs, descriptors)[0]["missing"] == ["x", "y"]


@pytest.mark.parametrize("unit", ["ghost", "u0", "cv0"])
def test_a_unit_named_that_is_no_keyed_table_is_tried_past_64_keyed_tables(unit: str) -> None:
    """70 keyed tables with integer columns, and the unit named is one that is no keyed table
    (absent, unkeyed, a coverage table): it alone is tried, whatever the number of keyed tables,
    and the analysis is unavailable there (it has no integer column), not available at another."""
    descriptors = _units(70)
    descriptors.append(build.table("u0", None))
    descriptors.append(build.table("cv0", None, role="coverage"))
    packs = registry_of({"p": [[_INTEGER]]})
    assert is_the_scan(packs, descriptors, unit, None)
    found, _ = outcome(Quick(packs), descriptors, unit, None)
    judged = next(one for one in found if one["analysis"] == "p.a000")
    assert (judged["status"], judged["missing"]) == ("unavailable", ["x"])


def test_an_analysis_no_view_can_run_keeps_no_unconfirmed_role() -> None:
    """A requirement met by a proposed integer column (unconfirmed) and one for a column of dates
    (which no view is handed): unavailable, missing the dates' role and with nothing unconfirmed."""
    descriptors = [
        build.dataset(),
        build.table("a", ["c0"]),
        build.column("a.c0", "integer", status="proposed"),
        build.column("a.c1", "date"),
    ]
    requires = [_INTEGER, {"role": "d", "kind": "column", "on": "unit", "datatype": "date"}]
    judged = _same_as_the_scan(registry_of({"p": [requires]}), descriptors)[0]
    assert (judged["status"], judged["missing"], judged["unconfirmed"]) == (
        "unavailable",
        ["d"],
        [],
    )


def test_the_tables_with_categories_are_all_of_them_not_the_first_eight() -> None:
    """Eight unkeyed tables with a column of categories come first, then the one keyed table
    with one: under *k* ``compare.columns`` is judged at it, with categories, so available."""
    descriptors: list[Descriptor] = [build.dataset()]
    for number in range(8):
        descriptors.append(build.table(f"u{number}", None))
        descriptors.append(_column(f"u{number}.c0", "category", "asserted"))
    descriptors.append(build.table("t8", ["c0"]))
    descriptors.append(_column("t8.c0", "category", "asserted"))
    assert is_the_scan(None, descriptors, None, 3)
    found, _ = outcome(Quick(None), descriptors, None, 3)
    judged = next(one for one in found if one["analysis"] == "compare.columns")
    assert judged["status"] == "available"


_PAIR_SHAPES = (
    ("column", "unit", "integer", None),
    ("column", "unit", "integer", 2),
    ("column", "unit", "string", None),
    ("column", "unit", None, 3),
    ("column", None, "integer", 1),
    ("column", None, None, 2**53 - 1),
    ("endpoint", "unit", None, None),
    ("endpoint", None, None, 2),
    ("table", None, None, 1),
    ("table", None, None, 3),
)
"""Requirement shapes of every kind: on the unit or not, a datatype or any, a ``min`` or none."""


@cache
def _pairs() -> PackRegistry:
    """Every ordered pair of the shapes, each with a predicate that holds, fails or none (900
    analyses of two requirements)."""
    variants = [
        {
            "kind": kind,
            **({"on": on} if on else {}),
            **({"datatype": datatype} if datatype else {}),
            **({"min": least} if least is not None else {}),
            **({"predicate": f"p.{name}"} if name else {}),
        }
        for kind, on, datatype, least in _PAIR_SHAPES
        for name in (None, "yes", "no")
    ]
    analyses = [
        [{"role": "x", **first}, {"role": "y", **second}]
        for first in variants
        for second in variants
    ]
    return registry_of(
        {"p": analyses}, {"p": {"yes": predicate("p.yes", True), "no": predicate("p.no", False)}}
    )


def _pair_release(tables: int) -> list[Descriptor]:
    """``tables`` keyed tables (integers on the odd ones, strings elsewhere, an endpoint on every
    third), an unkeyed table ``u0`` and a coverage table ``cv0`` with integer columns."""
    found: list[Descriptor] = [build.dataset()]
    for number in range(tables):
        name = f"t{number:03d}"
        found.append(build.table(name, ["c0"]))
        found.append(build.column(f"{name}.c0", "integer" if number % 2 else "string"))
        found.append(build.column(f"{name}.c1", "integer", status="proposed"))
        if number % 3 == 0:
            found.append(build.column(f"{name}.s1", "string"))
            found.append(build.column(f"{name}.s2", "string"))
            fields = {
                "table": name,
                "time_column": "s1",
                "status_column": "s2",
                "event_coding": {"event": ["a"], "censored": ["b"]},
                "entry": "at_origin",
            }
            found.append(build.descriptor("endpoint", f"ep:{name}", fields))
    found += [build.table("u0", None), build.table("cv0", None, role="coverage")]
    found += [build.column("u0.c0", "integer"), build.column("cv0.c0", "integer")]
    return found


@pytest.mark.parametrize("tables", [3, 65, 130])
def test_every_pair_of_requirement_shapes_with_predicates_is_the_scan(tables: int) -> None:
    """900 analyses, each two requirements of the shapes above with a predicate that holds, fails
    or is none, over a release of 3, 65 or 130 keyed tables: the scan's answer for the unit a
    keyed table, the unkeyed ``u0``, the coverage table ``cv0`` or ``ghost`` (and, at 3 tables,
    each keyed table tried), under *k* or not."""
    descriptors = _pair_release(tables)
    packs = _pairs()
    units = ["t001", f"t{tables - 1:03d}", "u0", "cv0", "ghost", *([None] if tables == 3 else [])]
    for unit in units:
        for k in (None, 3):
            assert is_the_scan(packs, descriptors, unit, k), (unit, k)


def test_the_tests_guard_their_constants_and_seeds() -> None:
    """What the exhaustive tests range over (the widths, the huge counts and ``min``) and that
    each sampled test is seeded, so that a loosened constant or a dropped ``@seed`` fails."""
    assert {127, 128, 129, 130, 199, 200, 201, 256, 300} <= set(WIDTHS)
    assert {15, 16, 17, 31, 32, 33, 63, 64, 65, 66} <= set(WIDTHS)
    assert {2**53 - 1, 2**53 - 2} <= set(POOLS[2])
    assert {1, 63, 64, 65, 70, 2**53 - 1} <= set(_LEASTS)
    for test in (
        test_applicable_is_the_scan,
        test_a_call_leaves_the_entries_it_reads_as_they_were,
        test_the_registry_as_built_is_the_scan,
        test_each_signature_is_the_scan_at_each_unit,
        test_each_analysis_is_judged_where_the_scan_judged_it,
    ):
        assert getattr(test, "_hypothesis_internal_use_seed", None) is not None, test.__name__


# --- End to end -------------------------------------------------------------------------------


def _drawn(
    release: tuple[list[Descriptor], list[str]], data: st.DataObject
) -> tuple[PackRegistry | None, str | None]:
    """A registry whose ``min`` are the release's counts, less one, as they are and plus one,
    and a unit: in seven examples of eight none (each keyed table is tried), in the other one
    named, or one that is not a table."""
    descriptors, names = release
    packs = data.draw(registries(counts_of(descriptors), len(names) > 20))
    if data.draw(st.integers(0, 7)) != 7:
        return packs, None
    return packs, data.draw(st.sampled_from([*names, "ghost"]))


class Draws:
    """What the end-to-end test drew, for ``test_the_end_to_end_examples_have_their_shares``."""

    examples = 0
    graded = 0
    core_only = 0
    named = 0
    tried: ClassVar[list[int]] = []


@seed(101)
@EXAMPLES
@given(releases(), st.sampled_from([None, 3]), st.data())
def test_applicable_is_the_scan(
    release: tuple[list[Descriptor], list[str]], k: int | None, data: st.DataObject
) -> None:
    """Every analysis's status, missing and unconfirmed roles, and the requirement predicates
    called and their order, are the scan's, for each keyed table or a unit named (keyed or
    not, a coverage table, or no table), under *k* or not, over releases of none to 20 tables
    and of 9 to 200 keyed units whose early tables are poor and late ones rich (so that the
    unit that decides is past a byte, a word, two), and registries whose ``min`` are their
    counts, less one, as they are and plus one."""
    descriptors, names = release
    packs, unit = _drawn(release, data)
    Draws.examples += 1
    Draws.graded += names[:1] == ["t000"]
    Draws.core_only += names[:1] == ["t000"] and packs is None
    Draws.named += unit is not None
    Draws.tried.append(len(registry._index(descriptors, unit).units))
    assert outcome(Quick(packs), descriptors, unit, k) == outcome(
        QuickScan(packs), descriptors, unit, k
    )


def test_the_end_to_end_examples_have_their_shares() -> None:
    """What the test above drew (it runs first; alone, this test has nothing to judge): its 800
    examples, a graded release in at least a tenth (measured 15.6%), a unit named in at most a
    fifth (10%: one example in eight), the graded ones against the core's registry alone in at most
    a quarter (14%), and units tried 9 or more in at least a twentieth, 64 or
    more in at least a fiftieth and 127 or more in at least a hundredth (measured 21%, 11% and
    5%): a generator that drops, shrinks or names away the large releases fails here, not by
    passing every test."""
    if not Draws.examples:
        pytest.skip("the end-to-end test did not run")
    total = Draws.examples
    assert EXAMPLES.max_examples >= 800
    assert total == EXAMPLES.max_examples, total
    assert Draws.graded >= total // 10, Draws.graded
    assert Draws.named <= total // 5, Draws.named
    assert Draws.core_only <= Draws.graded // 4, (Draws.core_only, Draws.graded)
    for least, share in ((9, 20), (64, 50), (127, 100)):
        tried = sum(one >= least for one in Draws.tried)
        assert tried >= total // share, (least, tried)


def _dumped(analyses: Analyses) -> list[Any]:
    """The registry's entries as data: each pack analysis's, whole, and each core analysis's
    requirements (the rest of a core entry is not read, and its dump is slow)."""
    return [
        one.entry.model_dump()
        if one.pack is not None
        else [requirement.model_dump() for requirement in one.entry.fields.requires]
        for one in analyses.all()
    ]


@seed(102)
@settings(PARTS, max_examples=150)
@given(releases(), st.sampled_from([None, 3]), st.data())
def test_a_call_leaves_the_entries_it_reads_as_they_were(
    release: tuple[list[Descriptor], list[str]], k: int | None, data: st.DataObject
) -> None:
    """``Quick`` and ``QuickScan`` share one list of entries, so that a call that sorts or
    changes them in place is not seen by the next: each entry, as data, is as it was after a
    call of either."""
    descriptors, _ = release
    packs, unit = _drawn(release, data)
    for registry_kind in (Quick, QuickScan):
        analyses = registry_kind(packs)
        before = _dumped(analyses)
        outcome(analyses, descriptors, unit, k)
        assert _dumped(analyses) == before


REAL = settings(EXAMPLES, max_examples=80)
"""The registry as it is built (its entries copied on each call, as they are not by ``Quick``)."""


@seed(103)
@REAL
@given(releases(), st.sampled_from([None, 3]), st.data())
def test_the_registry_as_built_is_the_scan(
    release: tuple[list[Descriptor], list[str]], k: int | None, data: st.DataObject
) -> None:
    """The same, with the real ``Analyses`` on both sides: a call that sorts or changes the
    entries it reads in place changes the order in which it calls the predicates, which the
    scan, reading its own copies, does not."""
    descriptors, _ = release
    packs, unit = _drawn(release, data)
    assert outcome(Analyses(packs), descriptors, unit, k) == outcome(
        Scan(packs), descriptors, unit, k
    )


WIDE = 50


def test_wide_releases_are_the_scan() -> None:
    """Graded releases of 9 to 200 keyed units (``wide_release``: poor tables, then rich ones
    from a cut, three of 60 to 70 columns) over registries whose ``min`` are the counts, less
    one, as they are and plus one, and up to 2^53-1: the units' bitsets are wider than a byte,
    a word and two, the deciding unit is past them, and a count is past 64. The scan's answer
    for each (the release's units, or one named), under *k* or not; seeds 0 to 49."""
    assert WIDE >= 50
    large = 0
    for number in range(WIDE):
        draw = random.Random(number)
        descriptors, names = wide_release(draw)
        packs = wide_registry(draw, counts_of(descriptors))
        k = draw.choice([None, 3, 3])
        unit = None if draw.random() < 0.85 else draw.choice([*names, "u0", "ghost"])
        large += len(names) >= 127 and unit is None
        assert is_the_scan(packs, descriptors, unit, k), number
    assert large >= WIDE // 10, large


def test_the_graded_releases_have_their_shape() -> None:
    """What ``graded_release`` draws, by seed: its sizes (``UNITS``, 9 to 200, 127 and 129 among
    them), and where a release of 129 or 200 units becomes rich (``_cut``): in the last eight
    units in 15 to 45%, within three of 8, 16, 32, 64 or 128 (and not there) in 35 to 80%, and
    elsewhere in at least 5%: a cut always at the end, always at 0, always at an edge, or
    one that never lands among the units past 128, fails."""
    assert min(UNITS) <= 9
    assert max(UNITS) >= 200
    assert {127, 129} <= set(UNITS)
    for units in (129, 200):
        cuts = [_cut(random.Random(number), units) for number in range(400)]
        last = sum(cut >= units - 8 for cut in cuts)
        edges = sum(
            cut < units - 8 and any(abs(cut - edge) <= 3 for edge in (8, 16, 32, 64, 128))
            for cut in cuts
        )
        assert 0.15 * len(cuts) <= last <= 0.45 * len(cuts), (units, last)
        assert 0.35 * len(cuts) <= edges <= 0.8 * len(cuts), (units, edges)
        assert len(cuts) - last - edges >= 0.05 * len(cuts), (units, last, edges)
    assert sum(_cut(random.Random(number), 200) > 128 for number in range(400)) >= 40
    late = [_categories_from(random.Random(number), 200, 50) for number in range(400)]
    assert 0.3 * len(late) <= sum(one >= 194 for one in late) <= 0.7 * len(late)
    assert sum(one == 50 for one in late) >= 0.1 * len(late)
    keyed = [len(graded_release(random.Random(number), 129)[1]) for number in range(5)]
    assert keyed == [129] * 5


def test_the_generators_draw_what_they_are_for() -> None:
    """The draws of the generators, by seed, so that one that stops drawing what a mutant of the
    index needs fails here: graded releases of 65 units with a table of 60 or more columns in at
    least half, poor and rich tables mixed in 15 to 60% (not cut) and the first column of
    categories in the last eight units in at least a fifth, and categories starting in the last
    six units in a third to seven tenths (``_categories_from``); a ``min`` drawn from the counts
    beside the greatest one (less one, as it is, plus one); a registry with the analyses of the
    greatest integer count and of the rich units; the sizes of the scale, and the tests' own
    numbers (the shares of ``releases`` and of the parts, the scales)."""
    wide = mixed = late = 0
    for number in range(100):
        descriptors, names = graded_release(random.Random(number), 65)
        columns = [one for one in descriptors if isinstance(one, ColumnDescriptor)]
        per_table = Counter(one.id.split(".", 1)[0] for one in columns)
        wide += max(per_table.values(), default=0) >= 60
        rich = [per_table[name] >= 3 for name in names]
        mixed += (any(rich[:57]) and not all(rich[57:])) or rich != sorted(rich)
        first = next(
            (
                names.index(one.id.split(".", 1)[0])
                for one in columns
                if one.fields.datatype in ("category", "list<category>")
            ),
            len(names),
        )
        late += first >= 57
    assert wide >= 50, wide
    assert 15 <= mixed <= 60, mixed
    assert late >= 20, late
    counts = counts_of(graded_release(random.Random(0), 65)[0])
    found = counts.of("column", "unit", "integer")
    assert {least_of(found, pick) for pick in range(60)} >= {
        found[-1] - 1,
        found[-1],
        found[-1] + 1,
    }
    drawn = changed = 0
    for number in range(0, _REQUIREMENTS, 11):
        with_counts = dict(requirement(number, "p", "r", 3 * number, counts))
        without = dict(requirement(number, "p", "r"))
        drawn += "kind" in with_counts
        changed += with_counts.get("min") != without.get("min")
    assert changed >= drawn // 10, (changed, drawn)
    derived = targets = 0
    for number in range(50):
        draw = random.Random(number)
        descriptors, _ = wide_release(draw)
        packs = wide_registry(draw, counts_of(descriptors))
        for one in packs.analyses():
            needs = one.entry.fields.requires
            target = len(needs) == 2 and needs[1].role == "r1" and needs[1].min == 3
            targets += target
            if not target:
                derived += sum(
                    need.min is not None and need.min not in _WIDE_MINS for need in needs
                )
    assert derived >= 40, derived
    assert targets >= 50, targets
    assert PARTS_BIG >= 2
    assert BIG >= 16
    assert min(SCALES) >= 64
    assert max(SCALES) >= 300


def _shapes() -> list[list[dict[str, Any]]]:
    """Every requirement there is but its predicate, each an analysis of its own."""
    return [
        [
            {
                "role": "r",
                **({} if kind is None else {"kind": kind}),
                **({} if on is None else {"on": on}),
                **({} if datatype is None else {"datatype": datatype}),
                **({} if least is None else {"min": least}),
            }
        ]
        for kind in _KINDS
        for on in _ON
        for datatype in _DATATYPES
        for least in _MINS
    ]


def _fixed(shift: int) -> list[Descriptor]:
    """A release whose tables differ in what they hold, so that every shape meets some and
    misses others: some keyed, one a keyed coverage table; columns of every datatype, settled
    and not, and undeclared; endpoints usable and not, settled and not; a relationship."""
    found: list[Descriptor] = [build.dataset()]
    for number in range(4):
        name = f"t{number}"
        role = "coverage" if number == 3 else "entity"
        found.append(build.table(name, ["c0"] if (number + shift) % 4 != 2 else None, role=role))
        for column in range(number + shift % 3):
            datatype = [None, *DATATYPES][(number * 3 + column + shift) % 10]
            status = "proposed" if (column + shift) % 3 == 0 else "asserted"
            found.append(_column(f"{name}.c{column}", datatype, status))
        fields: dict[str, Any] = {"table": name, "time_column": "c1", "status_column": "c2"}
        if (number + shift) % 2:
            fields["event_coding"] = {"event": ["a"], "censored": ["b"]}
        if (number + shift) % 3:
            fields["entry"] = "at_origin"
        found.append(build.descriptor("endpoint", f"ep:{name}", fields))
    found.append(build.relationship("t1", ["c0"], "t0", role="x"))
    return found


def _column(id: str, datatype: str | None, status: str) -> Descriptor:
    extra: dict[str, Any] = {}
    if datatype in ("category", "list<category>"):
        extra["permissible_values"] = {"values": [{"value": "a"}]}
    return build.column(id, datatype, status=status, **extra)


@pytest.mark.parametrize("shift", range(3))
def test_every_requirement_shape_is_the_scan(shift: int) -> None:
    """Each requirement of every kind (and none), ``on``, datatype (and any) and ``min`` (and
    the default), as an analysis of its own, is judged as the scan judges it: for each keyed
    table and for each table named, under *k* and not."""
    packs = registry_of({"p0": _shapes()})
    descriptors = _fixed(shift)
    for unit in (None, "t0", "t2", "t3", "ghost"):
        for k in (None, 3):
            assert outcome(Quick(packs), descriptors, unit, k) == outcome(
                QuickScan(packs), descriptors, unit, k
            )


# --- The parts --------------------------------------------------------------------------------

_LEAST = (0, 1, 2, 3, 4, 5, 63, 64, 65, 70, 72, 2**53 - 1)
"""The ``min`` drawn besides the release's counts, less one, as they are and plus one."""
PARTS_BIG = 2
"""The share (in a hundred) of the releases of the tests of the parts that are graded: the
scan they compare with, per unit, takes a time at 200 units."""


@seed(104)
@PARTS
@given(
    releases(PARTS_BIG),
    st.lists(st.integers(0, _REQUIREMENTS - 1), min_size=1, max_size=6),
    st.lists(st.integers(0, 2**20), min_size=1, max_size=5),
    st.data(),
)
def test_each_signature_is_the_scan_at_each_unit(
    release: tuple[list[Descriptor], list[str]],
    chosen: list[int],
    picks: list[int],
    data: st.DataObject,
) -> None:
    """For each requirement shape asked of one index at several ``min`` (the release's counts,
    less one, as they are and plus one, or one of ``_LEAST``), in turn (so that a cache that
    keeps a shape's answer for another ``min`` is caught), the units it misses and those where
    it is unconfirmed are the scan's at each unit, and so are its counts."""
    descriptors, names = release
    unit = data.draw(st.sampled_from([None, *names, "ghost"]))
    index = registry._index(descriptors, unit)
    units = index.units
    counts = counts_of(descriptors)
    for number in chosen:
        fields = dict(requirement(number, "p0", "r"))
        kind = fields.get("kind", "column")
        on, datatype = fields.get("on"), fields.get("datatype")
        found = counts.of(kind, on, datatype if kind == "column" else None)
        for least in (
            least_of(found, pick // 2) if pick % 2 else _LEAST[pick // 2 % len(_LEAST)]
            for pick in picks
        ):
            assert index.signature(kind, on, datatype, least) == brute_signature(
                descriptors, units, kind, on, datatype, least
            )
        for table in units:
            assert index.count(kind, on, datatype, table) == brute_count(
                descriptors, kind, on, datatype, table
            )


def _held(analyses: Sequence[Registered], draw: Any) -> dict[str, bool]:
    references = {
        one.predicate
        for analysis in analyses
        for one in analysis.entry.fields.requires
        if one.predicate is not None
    }
    return {reference: draw(st.booleans()) for reference in sorted(references)}


@seed(105)
@PARTS
@given(
    releases(PARTS_BIG),
    st.lists(
        st.lists(st.tuples(st.integers(0, _REQUIREMENTS - 1), st.integers(0, 2**20)), max_size=5),
        min_size=1,
        max_size=4,
    ),
    st.sampled_from([None, 3]),
    st.data(),
)
def test_each_analysis_is_judged_where_the_scan_judged_it(
    release: tuple[list[Descriptor], list[str]],
    chosen: list[list[tuple[int, int]]],
    k: int | None,
    data: st.DataObject,
) -> None:
    """Each analysis, the core's and a pack's, is judged at the unit the scan kept: the first
    of the best outcomes over the units, a refused or unrun one at the first, and under *k*
    one that shows only numbers' units among the tables with categories; the requirements'
    ``min`` are the release's counts, less one, as they are and plus one, in a third of them."""
    descriptors, names = release
    counts = counts_of(descriptors)
    unit = data.draw(st.sampled_from([None, None, *names, "ghost"]))
    version = PackVersion(version="1.0.0", results_version=1)
    analyses = [
        *_CORE,
        *(
            Registered(
                entry(
                    "p0",
                    number,
                    [
                        dict(requirement(one, "p0", f"r{role}", pick, counts))
                        for role, (one, pick) in enumerate(requires)
                    ],
                ),
                "p0",
                None,
                version,
            )
            for number, requires in enumerate(chosen)
        ),
    ]
    held = _held(analyses, data.draw)
    index = registry._index(descriptors, unit)
    if not index.units:
        return
    rowless = registry._rowless(descriptors)
    for analysis in analyses:
        at = registry._choice(index, analysis, held, k, rowless)
        assert at == scan_choice(None, descriptors, index.units, k, analysis, dict(held))


# --- Examples the end-to-end test let through --------------------------------------------------


def test_the_first_of_two_units_with_caveats_is_kept() -> None:
    """Two keyed units, each meeting both requirements, one unconfirmed on each: the scan
    keeps the first, whose unconfirmed role is ``x`` (taking the highest unit names ``y``)."""
    descriptors = [
        build.dataset(),
        build.table("a", ["c0"]),
        build.table("b", ["c0"]),
        build.column("a.c0", "integer", status="proposed"),
        build.column("a.c1", "string"),
        build.column("b.c0", "integer"),
        build.column("b.c1", "string", status="proposed"),
    ]
    requires = [
        {"role": "x", "kind": "column", "on": "unit", "datatype": "integer"},
        {"role": "y", "kind": "column", "on": "unit", "datatype": "string"},
    ]
    packs = registry_of({"p": [requires]})
    found, _ = outcome(Quick(packs), descriptors, None, None)
    assert outcome(Quick(packs), descriptors, None, None) == outcome(
        QuickScan(packs), descriptors, None, None
    )
    judged = next(one for one in found if one["analysis"] == "p.a000")
    assert judged["status"] == "available_with_caveats"
    assert judged["unconfirmed"] == ["x"]


def test_two_requirements_of_one_shape_and_two_mins_are_judged_apart() -> None:
    """``min`` 1 and ``min`` 2 of one shape: unit ``a`` has one integer column, ``b`` two; the
    second analysis is available at ``b`` (a cache of the shape's answer without its ``min``
    calls it unavailable)."""
    descriptors = [
        build.dataset(),
        build.table("a", ["c0"]),
        build.table("b", ["c0"]),
        build.column("a.c0", "integer"),
        build.column("b.c0", "integer"),
        build.column("b.c1", "integer"),
    ]

    def requires(least: int) -> list[dict[str, Any]]:
        return [{"role": "x", "kind": "column", "on": "unit", "datatype": "integer", "min": least}]

    packs = registry_of({"p": [requires(1), requires(2)]})
    found, _ = outcome(Quick(packs), descriptors, None, None)
    assert found == outcome(QuickScan(packs), descriptors, None, None)[0]
    statuses = {one["analysis"]: one["status"] for one in found}
    assert statuses["p.a000"] == statuses["p.a001"] == "available"


def test_an_analysis_that_no_view_can_run_is_judged_at_the_first_unit() -> None:
    """A pack's analysis that requires a column of dates, which no view is handed (D341), is
    unavailable at every unit, so judged at the first: ``a``, which misses both its roles, not
    ``b``, which meets both."""
    descriptors = [
        build.dataset(),
        build.table("a", ["c0"]),
        build.table("b", ["c0"]),
        build.column("b.c0", "integer"),
        build.column("b.c1", "date"),
    ]
    requires = [
        {"role": "x", "kind": "column", "on": "unit", "datatype": "date"},
        {"role": "y", "kind": "column", "on": "unit", "datatype": "integer"},
    ]
    packs = registry_of({"p": [requires]})
    found, _ = outcome(Quick(packs), descriptors, None, None)
    assert found == outcome(QuickScan(packs), descriptors, None, None)[0]
    judged = next(one for one in found if one["analysis"] == "p.a000")
    assert (judged["status"], judged["missing"]) == ("unavailable", ["x", "y"])


def test_a_requirement_of_no_descriptor_is_met_where_the_scan_met_it() -> None:
    """``min`` 0 is met at a unit with none (unit ``a``, whose strings are all settled), where
    unit ``b`` has the integer column but an unconfirmed string: the scan keeps ``a``, the first
    best (``min`` 0 read as 1 keeps ``b``, with caveats)."""
    descriptors = [
        build.dataset(),
        build.table("a", ["c0"]),
        build.table("b", ["c0"]),
        build.column("a.c0", "string"),
        build.column("b.c0", "string", status="proposed"),
        build.column("b.c1", "integer"),
    ]
    requires = [
        {"role": "x", "kind": "column", "on": "unit", "datatype": "integer", "min": 0},
        {"role": "y", "kind": "column", "on": "unit", "datatype": "string"},
    ]
    packs = registry_of({"p": [requires]})
    found, _ = outcome(Quick(packs), descriptors, None, None)
    assert found == outcome(QuickScan(packs), descriptors, None, None)[0]
    judged = next(one for one in found if one["analysis"] == "p.a000")
    assert (judged["status"], judged["unconfirmed"]) == ("available", [])


def test_a_predicate_that_fails_misses_every_unit_even_of_a_requirement_without_a_kind() -> None:
    """A requirement with only a predicate that does not hold makes the analysis unavailable
    at every unit, so judged at the first (``a``, which misses the column too), not at ``b``,
    which meets it (a predicate that is not counted as missing every unit keeps ``b``)."""
    descriptors = [
        build.dataset(),
        build.table("a", ["c0"]),
        build.table("b", ["c0"]),
        build.column("b.c0", "integer"),
    ]
    requires = [
        {"role": "x", "kind": "column", "on": "unit", "datatype": "integer"},
        {"role": "y", "predicate": "p.no"},
    ]
    packs = registry_of({"p": [requires]}, {"p": {"no": predicate("p.no", False)}})
    found, _ = outcome(Quick(packs), descriptors, None, None)
    assert found == outcome(QuickScan(packs), descriptors, None, None)[0]
    judged = next(one for one in found if one["analysis"] == "p.a000")
    assert (judged["status"], judged["missing"]) == ("unavailable", ["x", "y"])


def test_a_min_beside_each_count_around_64_is_the_scan() -> None:
    """Every ``min`` from 62 to 67 against units of 61 to 66 integer columns (each next to one
    with a column more), each as an analysis of its own with a second requirement that decides
    the unit: the first unit meeting a ``min`` of 63, 64 or 65 by one column less is not
    judged as one that does not (the scan's answer for each unit, named or not)."""
    requires = [
        [
            {"role": "x", "kind": "column", "on": "unit", "datatype": "integer", "min": least},
            {"role": "y", "kind": "column", "on": "unit", "datatype": "integer", "min": 3},
        ]
        for least in range(62, 68)
    ]
    packs = registry_of({"p": requires})
    for width in range(61, 67):
        descriptors: list[Descriptor] = [build.dataset()]
        for name, columns in (("a", width), ("b", width + 1)):
            descriptors.append(build.table(name, ["c0"]))
            descriptors += [
                build.column(f"{name}.c{number}", "integer") for number in range(columns)
            ]
        for unit in (None, "a", "b"):
            assert outcome(Quick(packs), descriptors, unit, None) == outcome(
                QuickScan(packs), descriptors, unit, None
            ), (width, unit)


def test_the_tables_with_categories_past_the_128th_unit_are_found_under_k() -> None:
    """Under *k* ``compare.columns`` is judged among the units with categories (D337): of 200
    keyed tables, one of the last has a column of categories, and the scan finds it there; the
    set of the units with categories (``registry._bits``) is not cut at a word's width, or two."""
    descriptors: list[Descriptor] = [build.dataset()]
    for number in range(200):
        name = f"t{number:03d}"
        descriptors.append(build.table(name, ["c0"]))
        descriptors.append(
            _column(f"{name}.c0", "category" if number == 150 else "integer", "asserted")
        )
    found, _ = outcome(Quick(None), descriptors, None, 3)
    assert found == outcome(QuickScan(None), descriptors, None, 3)[0]
    judged = next(one for one in found if one["analysis"] == "compare.columns")
    assert "columns" not in judged["missing"]


def test_a_min_past_64_is_judged_by_counts_past_64() -> None:
    """Units of 66, 70 and 64 integer columns and a requirement of 68: met only at the second
    (a ``min`` clamped to 64 meets it at the first), whose roles are none missing."""
    descriptors: list[Descriptor] = [build.dataset()]
    for name, width in (("a", 66), ("b", 70), ("c", 64)):
        descriptors.append(build.table(name, ["c0"]))
        descriptors += [build.column(f"{name}.c{number}", "integer") for number in range(width)]
    requires = [{"role": "x", "kind": "column", "on": "unit", "datatype": "integer", "min": 68}]
    packs = registry_of({"p": [requires]})
    found, _ = outcome(Quick(packs), descriptors, None, None)
    assert found == outcome(QuickScan(packs), descriptors, None, None)[0]
    judged = next(one for one in found if one["analysis"] == "p.a000")
    assert (judged["status"], judged["missing"]) == ("available", [])


# --- The cost ---------------------------------------------------------------------------------

READS = ("fields", "id", "curation")
"""The members of a descriptor that applicability reads."""


class _Reads:
    count = 0


_COUNTED: dict[type, type] = {}


def _counted(descriptor: Descriptor) -> Descriptor:
    """The same descriptor, of a subclass of its class that counts each read of ``READS``
    (``_Reads.count``): a copy of the descriptors made inside a call reads them too, so it
    cannot hide a scan of each one per unit, analysis or requirement."""
    kind = type(descriptor)
    if kind not in _COUNTED:

        def read(self: object, name: str) -> object:
            if name in READS:
                _Reads.count += 1
            return object.__getattribute__(self, name)

        _COUNTED[kind] = type(f"Counted{kind.__name__}", (kind,), {"__getattribute__": read})
    found = object.__new__(_COUNTED[kind])
    for name in (
        "__dict__",
        "__pydantic_fields_set__",
        "__pydantic_extra__",
        "__pydantic_private__",
    ):
        object.__setattr__(found, name, object.__getattribute__(descriptor, name))
    return found  # type: ignore[return-value]


def wide(tables: int, shift: int = 0) -> list[Descriptor]:
    """A release of ``tables`` keyed tables (every 17th a coverage table) with an integer
    column and one of another datatype each (one in four proposed), an endpoint on every third
    and a relationship from every fifth; ``shift`` moves which table has what."""
    found: list[Descriptor] = [build.dataset()]
    for number in range(tables):
        name = f"t{number:04d}"
        place = number + shift
        found.append(build.table(name, ["c0"], role="coverage" if place % 17 == 16 else "entity"))
        found.append(build.column(f"{name}.c0", "integer"))
        status = "proposed" if place % 4 == 0 else "asserted"
        found.append(_column(f"{name}.c1", DATATYPES[place % len(DATATYPES)], status))
        if place % 3 == 0:
            fields: dict[str, Any] = {
                "table": name,
                "time_column": "c0",
                "status_column": "c1",
                "event_coding": {"event": ["a"], "censored": ["b"]},
            }
            if place % 2:
                fields["entry"] = "at_origin"
            found.append(build.descriptor("endpoint", f"ep:{name}", fields))
        if number and place % 5 == 0:
            found.append(build.relationship(name, ["c0"], f"t{number - 1:04d}", role=f"x{name}"))
    return found


def _packs(analyses: int, requirements: int) -> PackRegistry:
    """One pack (the only one with predicates, whatever its size) of ``analyses`` analyses,
    each with ``requirements`` requirements of distinct ``min`` over every shape and one with
    a predicate that holds."""
    chosen = random.Random(3)
    shapes = [
        {"kind": kind, **({"on": on} if on else {}), **({"datatype": datatype} if datatype else {})}
        for kind in ("endpoint", "column", "table")
        for on in _ON
        for datatype in _DATATYPES
    ]
    given = [
        [
            *(
                {"role": f"r{role}", **chosen.choice(shapes), "min": chosen.randint(0, 40)}
                for role in range(requirements)
            ),
            {"role": "held", "predicate": "p.yes"},
        ]
        for _ in range(analyses)
    ]
    return registry_of({"p": given}, {"p": {"yes": predicate("p.yes", True)}})


def _reads(tables: int, packs: PackRegistry) -> float:
    """Reads of each descriptor, on average, by one call under *k* and one without."""
    descriptors = [_counted(one) for one in wide(tables)]
    _Reads.count = 0
    for k in (5, None):
        Analyses(packs).applicable(descriptors, dataset="d", manifest="m", k=k)
    return _Reads.count / len(descriptors)


def test_each_descriptor_is_read_a_constant_number_of_times() -> None:
    """Reads per descriptor do not grow with the units (3 to 120 tables), the analyses (2 to
    200) or their requirements (2 to 12 each, 24 to 2,400 in all): one pass indexes them, and
    each pack with a requirement predicate (one here, whatever the registry's size) copies them
    once for its view. A scan per unit, analysis or requirement, or of a copy, grows with them."""
    small, large = _packs(2, 2), _packs(200, 12)
    reads = {
        (tables, size): _reads(tables, packs)
        for tables in (3, 120)
        for size, packs in (("small", small), ("large", large))
    }
    least = min(reads.values())
    assert max(reads.values()) <= least + 1, reads
    assert max(reads.values()) <= 12, reads


def _work(
    tables: int, packs: PackRegistry, k: int | None, monkeypatch: pytest.MonkeyPatch
) -> dict[str, int]:
    """The calls of the index's parts that one call makes: ``_Index.count`` (a count of one
    requirement at one unit), ``_roles`` (an analysis's roles at one unit), ``_prefix`` (a
    shape's sorted counts) and ``_below`` (a bisection)."""
    calls: dict[str, int] = dict.fromkeys(("count", "roles", "prefix", "below"), 0)

    def counting(name: str, original: Callable[..., Any]) -> Callable[..., Any]:
        def counted(*given: Any, **named: Any) -> Any:
            calls[name] += 1
            return original(*given, **named)

        return counted

    for owner, member, name in (
        (registry._Index, "count", "count"),
        (registry, "_roles", "roles"),
        (registry, "_prefix", "prefix"),
        (registry, "_below", "below"),
    ):
        monkeypatch.setattr(owner, member, counting(name, getattr(owner, member)))
    Analyses(packs).applicable(wide(tables), dataset="d", manifest="m", k=k)
    return calls


def test_the_work_of_a_call_is_bound_by_its_formula(monkeypatch: pytest.MonkeyPatch) -> None:
    """The index's work, counted in its parts (D420: S·(U log U + V·U/w) + R·(log U + U/w) +
    A·(R + U/w)): per shape, one count at each unit and one sorting; per requirement, at most
    one bisection of two for each distinct shape and ``min``; per analysis, its roles at one
    unit. Over 3 to 120 units, 2 to 200 analyses and up to 12 requirements each, under *k* and
    not, the counts are at most S·U + (the requirements), the roles' calls at most A, the
    sortings at most 2·S and the bisections at most twice the distinct shapes and ``min``. A
    judgement of every unit for each analysis (A·R·U counts, A·U roles), counts redone for each
    ``min`` (R·U) or a signature found again for each requirement are over them. Its limit: it
    counts calls of those four, so a loop that inlines the lookups, or does the work in none of
    them, escapes it (the reads test below holds the descriptors, not the index)."""
    for packs in (_packs(2, 2), _packs(200, 12)):
        analyses = Analyses(packs).all()
        asked = [
            (requirement.kind, requirement.on, requirement.datatype, requirement.min)
            for analysis in analyses
            for requirement in analysis.entry.fields.requires
            if requirement.kind is not None
        ]
        shapes = {registry._shape(kind, on, datatype) for kind, on, datatype, _ in asked}
        signatures = {
            (registry._shape(kind, on, datatype), 1 if least is None else least)
            for kind, on, datatype, least in asked
        }
        requirements = sum(len(analysis.entry.fields.requires) for analysis in analyses)
        for tables in (3, 30, 120):
            for k in (5, None):
                with monkeypatch.context() as patched:
                    work = _work(tables, packs, k, patched)
                assert work["count"] <= len(shapes) * tables + requirements, (tables, k, work)
                assert work["roles"] <= len(analyses), (tables, k, work)
                assert work["prefix"] <= 2 * len(shapes), (tables, k, work)
                assert work["below"] <= 2 * len(signatures), (tables, k, work)


# --- Its state --------------------------------------------------------------------------------


def _own() -> list[object]:
    """Every function the registry's module names, and every class it defines: what a call
    might keep a cache on (a function's attributes, a class's)."""
    return [
        one
        for one in vars(registry).values()
        if isinstance(one, types.FunctionType)
        or (isinstance(one, type) and one.__module__ == registry.__name__)
    ]


def _copy(value: object) -> object | None:
    """A copy of a member whose contents may change in place, or ``None``."""
    if isinstance(value, type):
        return None
    if isinstance(value, set | frozenset | bytearray):
        return type(value)(value)
    if isinstance(value, dict | list) or hasattr(value, "keys"):
        return dict(value) if hasattr(value, "keys") else list(value)  # type: ignore[call-overload]
    return None


def _state(analyses: Analyses) -> list[tuple[object, dict[str, object]]]:
    """What the registry, its packs and their hooks hold, what their classes (along their method
    resolution order) hold, and what the module of the registry holds, with every function and
    class in it (their attributes): each object's members, and a copy of each member whose
    contents can change in place (a mapping, list, set or bytearray), so that a cache kept on
    any is seen, whether on an instance, on a class (which ``vars`` of an instance does not
    show), on a function or in the module, by whatever key."""
    objects: list[object] = [analyses]
    packs = analyses.packs
    if packs is not None:
        objects += [packs, *vars(packs)["_packs"].values(), *vars(packs)["_hooks"].values()]
    more: list[object] = []
    for one in objects:
        more += [kind for kind in type(one).__mro__ if kind is not object]
    found: list[tuple[object, dict[str, object]]] = []
    seen: list[object] = []
    for one in [*objects, *more, registry, *_own()]:
        if any(one is other for other in seen) or not hasattr(one, "__dict__"):
            continue
        seen.append(one)
        members = dict(vars(one))
        copies = {
            f"{name} (contents)": copied
            for name, value in members.items()
            if (copied := _copy(value)) is not None
        }
        found.append((one, {**members, **copies}))
    return found


def _same(
    before: list[tuple[object, dict[str, object]]], after: list[tuple[object, dict[str, object]]]
) -> None:
    assert [one for one, _ in before] == [one for one, _ in after]
    for (one, members), (_, now) in zip(before, after, strict=True):
        assert members.keys() == now.keys(), one
        for name, value in members.items():
            if name.endswith(" (contents)"):
                assert now[name] == value, (one, name)
            else:
                assert now[name] is value, (one, name)


def test_a_call_changes_nothing_it_is_given() -> None:
    """A call keeps nothing on the ``Analyses``, its registry, the packs or their hooks, on
    their classes, on a function or class of the registry's module or in it (a set, a
    bytearray or a dictionary, by any key): its index is its own (D420), so a call on another
    release, or a racing one, cannot read it. The calls differ in release, dataset, manifest,
    unit and *k*."""
    packs = _packs(20, 4)
    analyses = Analyses(packs)
    analyses.applicable(wide(3), dataset="d", manifest="m")
    before = _state(analyses)
    for number, descriptors in enumerate((wide(10), wide(12, 1), wide(10))):
        for k in (None, 5):
            for unit in (None, "t0001", "ghost"):
                analyses.applicable(
                    descriptors,
                    dataset=f"d{number}",
                    manifest=f"m{number}{k}{unit}",
                    unit=unit,
                    k=k,
                )
                _same(before, _state(analyses))
    core = Analyses()
    core.applicable(wide(3), dataset="d", manifest="m")
    before = _state(core)
    core.applicable(wide(5), dataset="e", manifest="n")
    _same(before, _state(core))


@pytest.fixture
def switching() -> Iterator[None]:
    interval = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    try:
        yield
    finally:
        sys.setswitchinterval(interval)


def test_two_threads_sharing_one_registry_get_the_scans_answers(switching: None) -> None:
    """One ``Analyses`` and one registry shared by two threads, each asking of its own release
    of 300 tables, under *k* and not, 10 times, the thread switching every microsecond: each
    answer is the scan's for its own release."""
    packs = _packs(6, 3)
    shared = Analyses(packs)
    first, second = wide(300), wide(300, 1)
    want = {
        (name, k): Scan(packs).applicable(descriptors, dataset="d", manifest="m", k=k)
        for name, descriptors in (("first", first), ("second", second))
        for k in (None, 5)
    }
    found: dict[tuple[str, int | None], list[object]] = {key: [] for key in want}
    failed: list[BaseException] = []

    def ask(name: str, descriptors: list[Descriptor]) -> None:
        try:
            for _ in range(10):
                for k in (None, 5):
                    found[(name, k)].append(
                        shared.applicable(descriptors, dataset="d", manifest="m", k=k)
                    )
        except BaseException as failure:
            failed.append(failure)

    threads = [
        threading.Thread(target=ask, args=("first", first)),
        threading.Thread(target=ask, args=("second", second)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not failed
    for key, answers in found.items():
        assert len(answers) == 10
        assert all(answer == want[key] for answer in answers), key


def test_two_releases_under_one_manifest_are_answered_apart() -> None:
    """Two releases given the same manifest, in turn on one ``Analyses``: each answer is its
    own release's (nothing is kept by manifest)."""
    packs = _packs(6, 3)
    shared = Analyses(packs)
    first, second = wide(30), wide(30, 1)
    for descriptors in (first, second, first):
        assert shared.applicable(descriptors, dataset="d", manifest="same", k=None) == Scan(
            packs
        ).applicable(descriptors, dataset="d", manifest="same", k=None)

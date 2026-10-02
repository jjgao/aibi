"""What ``compare.columns`` shows under *k* pinning no count it suppresses (SPEC §8.4; D337): every
way a cohort's units can hold a variable's values, beside another cohort, run through the analysis
whole, its tests, differences and bootstrap included."""

import itertools
import json
from collections import defaultdict
from collections.abc import Callable, Iterator, Sequence
from fractions import Fraction
from types import MappingProxyType
from typing import Any

import pytest

from aibi.core.analyses.columns import Outcome
from aibi.core.engine.variables import Materialised, Value
from aibi.core.schema.semantics import ExclusionReason

Columned = Callable[..., Any]
Contrasted = Callable[..., Outcome]
Vector = tuple[int, ...]
"""A count as a sum of a world's cells."""

CELLS = ("gold", "silver", "bronze", "x-rare", "I")
"""A categorical variable's world at the first position: its units of each declared category, of
a value the column does not declare, and those excluded as having no information."""
DECLARED = ("gold", "silver", "bronze")
NUMBER_CELLS = ("low", "middle", "high", "I")
"""A numeric variable's world at the first position: its units at each of three values, and those
excluded."""
OTHERS: tuple[dict[Value, int], ...] = (
    {"gold": 6, "silver": 1, "bronze": 5},
    {"gold": 3, "silver": 3, "bronze": 0, "x-rare": 2},
)
"""The second position's worlds: one that merges its rows, one whose rows hold 0 and an
undeclared value."""


def compositions(total: int, parts: int) -> Iterator[tuple[int, ...]]:
    for bars in itertools.combinations(range(total + parts - 1), parts - 1):
        edges = (-1, *bars, total + parts - 1)
        yield tuple(edges[i + 1] - edges[i] - 1 for i in range(parts))


def _rank(rows: list[list[Fraction]]) -> int:
    rows = [list(row) for row in rows]
    rank = 0
    for column in range(len(rows[0]) if rows else 0):
        pivot = next((r for r in range(rank, len(rows)) if rows[r][column] != 0), None)
        if pivot is None:
            continue
        rows[rank], rows[pivot] = rows[pivot], rows[rank]
        for r in range(len(rows)):
            if r != rank and rows[r][column] != 0:
                factor = rows[r][column] / rows[rank][column]
                rows[r] = [a - factor * b for a, b in zip(rows[r], rows[rank], strict=True)]
        rank += 1
    return rank


def given(vector: Vector, shown: Sequence[Vector]) -> bool:
    """Whether what is shown gives ``vector`` by a linear combination."""
    rows = [[Fraction(x) for x in row] for row in shown]
    if not rows:
        return not any(vector)
    return _rank([*rows, [Fraction(x) for x in vector]]) == _rank(rows)


def whole(outcome: Outcome) -> str:
    return json.dumps(
        [
            [p.model_dump(mode="json") for p in outcome.population],
            [a.model_dump(mode="json") for a in outcome.analysed],
            outcome.values.model_dump(mode="json"),
            [c.model_dump(mode="json") for c in outcome.caveats],
        ],
        sort_keys=True,
    )


def _of(cells: Sequence[str], names: Sequence[str]) -> Vector:
    return tuple(int(cell in names) for cell in cells)


def _made(values: dict[Value, int], excluded: int) -> Materialised:
    by_reason = dict.fromkeys(ExclusionReason, 0)
    by_reason[ExclusionReason.NO_INFORMATION] = excluded
    kept = {value: count for value, count in values.items() if count}
    return Materialised(MappingProxyType(kept), excluded, MappingProxyType(by_reason), frozenset())


def _category_world(world: Sequence[int]) -> Materialised:
    of = dict(zip(CELLS, world, strict=True))
    return _made({name: count for name, count in of.items() if name != "I"}, of["I"])


def _vectors(outcome: Outcome) -> list[Vector]:
    """What the first position's output shows, as sums of its world's cells."""
    found: list[Vector] = [_of(CELLS, CELLS)]
    analysed = outcome.analysed[0]
    if analysed.n is not None:
        found.append(_of(CELLS, CELLS[:4]))
    if analysed.excluded_units is not None:
        found.append(_of(CELLS, ("I",)))
    shown = outcome.values.positions[0].columns[0].model_dump(mode="json")
    for row in shown["categories"] or []:
        names = [value["data"] for value in row["values"]]
        found.append(_of(CELLS, [*names, *(("x-rare",) if row.get("other_values") else ())]))
    return found


def _pinned(
    outcomes: Callable[[Sequence[int]], Outcome],
    vectors: Callable[[Outcome], list[Vector]],
    cells: Sequence[str],
    secrets: Sequence[tuple[str, Vector]],
    size: int,
    k: int,
    *,
    small_only: Sequence[str] = (),
) -> tuple[list[tuple[Any, ...]], list[tuple[Any, ...]]]:
    """Every world of ``size`` units over ``cells`` at the first position run through the
    analysis: the counts shown from 1 to *k* − 1, and each secret the outputs that read the same
    pin to one value that what they show does not give (for ``small_only``, only to a value from
    1 to *k* − 1)."""
    seen: dict[str, list[tuple[int, ...]]] = defaultdict(list)
    shown: dict[str, list[Vector]] = {}
    small: list[tuple[Any, ...]] = []
    for world in compositions(size, len(cells)):
        outcome = outcomes(world)
        key = whole(outcome)
        seen[key].append(world)
        shown[key] = vectors(outcome)
        counts = [sum(x * y for x, y in zip(v, world, strict=True)) for v in shown[key]]
        small += [(size, k, world) for count in counts if 1 <= count < k]
    pinned: list[tuple[Any, ...]] = []
    for key, worlds in seen.items():
        for name, vector in secrets:
            values = {sum(x * y for x, y in zip(vector, w, strict=True)) for w in worlds}
            if len(values) > 1 or given(vector, shown[key]):
                continue
            if name not in small_only or 1 <= next(iter(values)) < k:
                pinned.append((size, k, worlds[0], name))
    return small, pinned


@pytest.mark.parametrize(("size", "k", "other"), [(8, 3, 0), (8, 3, 1), (10, 4, 0), (11, 5, 1)])
def test_a_categorical_comparison_pins_no_count_it_suppresses(
    columned: Columned, contrasted: Contrasted, size: int, k: int, other: int
) -> None:
    """Every way the first of two cohorts of up to 11 units can split over three declared
    categories, a value the column does not declare and units excluded, beside a second cohort,
    under *k* from 3 to 5: the whole output (population, analysed, both positions' rows, the
    table's rows, its test, every difference and its interval, the family and the caveats,
    messages included) names no undeclared value and shows no count from 1 to *k* − 1, and the
    outputs that read the same leave every category's count, the units with a value and those
    excluded two values or more unless what the first position shows gives it, and the
    undeclared value's count never pinned to one from 1 to *k* − 1 (D337): the view adds nothing
    that its positions' rows do not give."""
    view = columned([{"column": "customers.tier"}], k=k)
    second = OTHERS[other]
    beside = _made(dict(second), 0)
    secrets = [
        *((name, _of(CELLS, (name,))) for name in CELLS[:4]),
        ("n", _of(CELLS, CELLS[:4])),
        ("excluded_units", _of(CELLS, ("I",))),
    ]

    def outcome(world: Sequence[int]) -> Outcome:
        found = contrasted(
            view,
            [size, sum(second.values())],
            [([_category_world(world)], None), ([beside], None)],
            k=k,
        )
        assert "x-rare" not in whole(found), world
        return found

    small, pinned = _pinned(outcome, _vectors, CELLS, secrets, size, k, small_only=("x-rare",))
    assert small == []
    assert pinned == []


NUMBER_WORLDS = (
    (1.0, 2.0, 3.0),
    (-40.5, 0.0, 1e6),
    (7.0, 7.0, 7.0),
    (3.0, 2.0, 1.0),
)
"""The values a numeric world's units hold, by cell: distinct, far apart, all the same (whose
standard deviation and bootstrap interval would give every unit's value), and reversed."""


def _number_world(world: Sequence[int], values: Sequence[float]) -> Materialised:
    held: dict[Value, int] = {}
    for value, count in zip(values, world, strict=False):
        held[value] = held.get(value, 0) + count
    return _made(held, world[-1])


@pytest.mark.parametrize(("size", "k"), [(8, 3), (11, 5)])
def test_a_numeric_comparison_shows_nothing_of_its_values(
    columned: Columned, contrasted: Contrasted, size: int, k: int
) -> None:
    """Every way the first of two cohorts of up to 11 units can hold three values or be
    excluded, beside a second cohort, under *k* from 3 to 5, with the values distinct, far
    apart, all the same or reversed: the whole output (tests, differences in means and medians,
    their intervals and the bootstrap's among it) is the same whatever the values, so nothing it
    shows depends on them beyond the units with a value and those excluded; no count shown is
    from 1 to *k* − 1; and the outputs that read the same leave the units with a value and those
    excluded two values or more unless what they show gives them (D337)."""
    view = columned([{"column": "customers.age"}], k=k)
    beside = _made({4.0: 5, 9.0: 2, 11.5: 3}, 1)
    secrets = [("n", (1, 1, 1, 0)), ("excluded_units", (0, 0, 0, 1))]

    def outcome(world: Sequence[int]) -> Outcome:
        found = [
            contrasted(
                view,
                [size, 11],
                [([_number_world(world, values)], None), ([beside], None)],
                k=k,
            )
            for values in NUMBER_WORLDS
        ]
        assert len({whole(one) for one in found}) == 1, world
        return found[0]

    def vectors(found: Outcome) -> list[Vector]:
        shown: list[Vector] = [(1, 1, 1, 1)]
        if found.analysed[0].n is not None:
            shown.append((1, 1, 1, 0))
        if found.analysed[0].excluded_units is not None:
            shown.append((0, 0, 0, 1))
        return shown

    small, pinned = _pinned(outcome, vectors, NUMBER_CELLS, secrets, size, k)
    assert small == []
    assert pinned == []

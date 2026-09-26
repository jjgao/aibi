"""What ``summary.distribution`` shows under *k* pinning no count it suppresses (SPEC §8.4; D329):
every way a cohort's units can hold a variable's values, run through the analysis whole."""

import itertools
import json
from collections import defaultdict
from collections.abc import Callable, Iterator, Sequence
from fractions import Fraction
from types import MappingProxyType
from typing import Any

import pytest

from aibi.core.analyses.distribution import Outcome
from aibi.core.engine.variables import Materialised, Value
from aibi.core.schema.semantics import ExclusionReason

Distributed = Callable[..., Any]
Summarised = Callable[..., Outcome]
Vector = tuple[int, ...]
"""A count as a sum of a world's cells."""

TIERS = ("gold", "silver", "bronze")
CATEGORY_CELLS = ("gold", "silver", "bronze", "I", "A", "IA")
"""A categorical variable's world: its units of each category, and those excluded as having no
information, not assessed, or both."""
EDGES = [0, 10, 20, 30]
REPRESENTATIVE = (-5, 5, 15, 25, 35)
SECOND = (-7.5, 9, 10, 30, 1e6)
"""A second value in each bin, at its edges where it has one, so that a world's units may hold
two values in a bin (``_number_world``)."""
OPEN_CELLS = ("gold", "silver", "x-rare", "y-rare", "I")
"""A categorical variable's world with values the column does not declare: its units of two
declared categories, of two undeclared values, and those excluded as having no information."""
UNDECLARED = ("x-rare", "y-rare")
NUMBER_CELLS = ("below", "0-10", "10-20", "20-30", "above", "I")
"""A numeric variable's world: its units in the bin below the first edge, in each bin, above the
last, each at one value, and those excluded."""


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


def _excluded(cells: Sequence[str], world: Sequence[int]) -> tuple[dict[ExclusionReason, int], int]:
    of = dict(zip(cells, world, strict=True))
    by_reason = dict.fromkeys(ExclusionReason, 0)
    by_reason[ExclusionReason.NO_INFORMATION] = of.get("I", 0) + of.get("IA", 0)
    by_reason[ExclusionReason.NOT_ASSESSED] = of.get("A", 0) + of.get("IA", 0)
    return by_reason, of.get("I", 0) + of.get("A", 0) + of.get("IA", 0)


def _analysed_vectors(
    outcome: Outcome, cells: Sequence[str], values: Sequence[str]
) -> list[Vector]:
    [analysed] = outcome.analysed
    found: list[Vector] = [_of(cells, cells)]
    if analysed.n is not None:
        found.append(_of(cells, values))
    if analysed.excluded_units is not None:
        found.append(_of(cells, ("I", "A", "IA")))
    if analysed.excluded is not None:
        found += [_of(cells, ("I", "IA")), _of(cells, ("A", "IA"))]
    return found


def _category_world(world: Sequence[int]) -> Materialised:
    by_reason, units = _excluded(CATEGORY_CELLS, world)
    values: dict[Value, int] = {
        tier: count for tier, count in zip(TIERS, world, strict=False) if count
    }
    return Materialised(MappingProxyType(values), units, MappingProxyType(by_reason), frozenset())


def _category_vectors(outcome: Outcome) -> list[Vector]:
    found = _analysed_vectors(outcome, CATEGORY_CELLS, TIERS)
    shown = outcome.values.positions[0].columns[0].model_dump(mode="json")
    for row in shown["categories"] or []:
        found.append(_of(CATEGORY_CELLS, [value["data"] for value in row["values"]]))
    return found


def _number_world(world: Sequence[int], *, spread: bool = False) -> Materialised:
    """A numeric world: each bin's units at its representative value, or, ``spread``, half of
    them (rounded down) at its second value."""
    by_reason, units = _excluded(NUMBER_CELLS, world)
    values: dict[Value, int] = {}
    for first, second, count in zip(REPRESENTATIVE, SECOND, world, strict=False):
        moved = count // 2 if spread else 0
        for value, times in ((first, count - moved), (second, moved)):
            if times:
                values[value] = values.get(value, 0) + times
    return Materialised(MappingProxyType(values), units, MappingProxyType(by_reason), frozenset())


def _open_world(world: Sequence[int]) -> Materialised:
    of = dict(zip(OPEN_CELLS, world, strict=True))
    by_reason = dict.fromkeys(ExclusionReason, 0)
    by_reason[ExclusionReason.NO_INFORMATION] = of["I"]
    values: dict[Value, int] = {name: count for name, count in of.items() if name != "I" and count}
    return Materialised(MappingProxyType(values), of["I"], MappingProxyType(by_reason), frozenset())


def _open_vectors(outcome: Outcome) -> list[Vector]:
    [analysed] = outcome.analysed
    found: list[Vector] = [_of(OPEN_CELLS, OPEN_CELLS)]
    if analysed.n is not None:
        found.append(_of(OPEN_CELLS, OPEN_CELLS[:4]))
    if analysed.excluded_units is not None:
        found.append(_of(OPEN_CELLS, ("I",)))
    shown = outcome.values.positions[0].columns[0].model_dump(mode="json")
    for row in shown["categories"] or []:
        names = [value["data"] for value in row["values"]]
        found.append(_of(OPEN_CELLS, [*names, *(UNDECLARED if row.get("other_values") else ())]))
    return found


def _number_vectors(outcome: Outcome) -> list[Vector]:
    found = _analysed_vectors(outcome, NUMBER_CELLS, NUMBER_CELLS[:5])
    shown = outcome.values.positions[0].columns[0].model_dump(mode="json")
    if shown["histogram"] is not None:
        at = 0
        for part in shown["histogram"]["bins"]:
            span = [
                name
                for name, value in zip(NUMBER_CELLS[:5], REPRESENTATIVE, strict=True)
                if _inside(part, value)
            ]
            at += len(span)
            found.append(_of(NUMBER_CELLS, span))
        assert at == 5
    return found


def _inside(part: dict[str, Any], value: float) -> bool:
    low, high = part["low"], part["high"]
    above = low is None or value > low or (value == low and part["includes_low"])
    below = high is None or value < high or (value == high and part["includes_high"])
    return above and below


def _pinned(
    outcomes: Callable[[Sequence[int]], Outcome],
    vectors: Callable[[Outcome], list[Vector]],
    cells: Sequence[str],
    secrets: Sequence[tuple[str, Vector]],
    size: int,
    k: int,
    *,
    again: Callable[[Sequence[int]], Outcome] | None = None,
    small_only: Sequence[str] = (),
) -> tuple[list[tuple[Any, ...]], list[tuple[Any, ...]]]:
    """Every world of ``size`` units over ``cells`` run through the analysis: the counts shown
    from 1 to *k* − 1, and each secret the outputs that read the same pin to one value that
    what they show does not give (for ``small_only``, only to a value from 1 to *k* − 1). With
    ``again``, each world run another way must read the same."""
    seen: dict[str, list[tuple[int, ...]]] = defaultdict(list)
    shown: dict[str, list[Vector]] = {}
    small: list[tuple[Any, ...]] = []
    for world in compositions(size, len(cells)):
        outcome = outcomes(world)
        key = whole(outcome)
        if again is not None:
            assert whole(again(world)) == key, world
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


@pytest.mark.parametrize(("size", "k"), [(8, 3), (10, 3), (11, 4), (12, 5)])
def test_a_categorical_variable_s_output_pins_no_count_it_suppresses(
    distributed: Distributed, summarised: Summarised, size: int, k: int
) -> None:
    """Every way a cohort of up to 12 units can split over three categories and units excluded
    for either of two reasons or both, under *k* from 3 to 5: no count shown is from 1 to
    *k* − 1, and the outputs that read the same (population, analysed, values and caveats,
    messages included) leave every count of a category, the units with a value, those excluded
    and those excluded under each reason two values or more, unless what they show gives it
    (D329)."""
    view = distributed([{"column": "customers.tier"}], k=k)
    secrets = [
        *((tier, _of(CATEGORY_CELLS, (tier,))) for tier in TIERS),
        ("n", _of(CATEGORY_CELLS, TIERS)),
        ("excluded_units", _of(CATEGORY_CELLS, ("I", "A", "IA"))),
        ("NO_INFORMATION", _of(CATEGORY_CELLS, ("I", "IA"))),
        ("NOT_ASSESSED", _of(CATEGORY_CELLS, ("A", "IA"))),
    ]
    small, pinned = _pinned(
        lambda world: summarised(view, [size], [([_category_world(world)], None)], k=k),
        _category_vectors,
        CATEGORY_CELLS,
        secrets,
        size,
        k,
    )
    assert small == []
    assert pinned == []


@pytest.mark.parametrize(("size", "k"), [(8, 3), (10, 3), (11, 4), (12, 5)])
def test_a_numeric_variable_s_output_pins_no_count_it_suppresses(
    distributed: Distributed, summarised: Summarised, size: int, k: int
) -> None:
    """Every way a cohort of up to 12 units can fall into three bins, the open bins below and
    above them, or be excluded, under *k* from 3 to 5: the output is the same whether each bin's
    units hold one value or two, so nothing it shows depends on the values within a bin beyond
    the bins' counts; no count shown is from 1 to *k* − 1; and the outputs that read the same
    (the histogram and the quartiles' bins among them) leave every bin's count, the units with a
    value and those excluded two values or more, unless what they show gives it (D329)."""
    view = distributed([{"column": "customers.age", "bins": EDGES}], k=k)
    secrets = [
        *((name, _of(NUMBER_CELLS, (name,))) for name in NUMBER_CELLS[:5]),
        ("n", _of(NUMBER_CELLS, NUMBER_CELLS[:5])),
        ("excluded_units", _of(NUMBER_CELLS, ("I",))),
    ]
    small, pinned = _pinned(
        lambda world: summarised(view, [size], [([_number_world(world)], None)], k=k),
        _number_vectors,
        NUMBER_CELLS,
        secrets,
        size,
        k,
        again=lambda world: summarised(
            view, [size], [([_number_world(world, spread=True)], None)], k=k
        ),
    )
    assert small == []
    assert pinned == []


@pytest.mark.parametrize(("size", "k"), [(8, 3), (10, 3), (11, 4), (12, 5)])
def test_undeclared_values_are_never_named_and_their_counts_never_pinned(
    distributed: Distributed, summarised: Summarised, size: int, k: int
) -> None:
    """Every way a cohort of up to 12 units can split over two declared categories, two values
    the column does not declare and units with no information, under *k* from 3 to 5: no output
    names an undeclared value, no count shown is from 1 to *k* − 1, and the outputs that read
    the same leave each declared category's count, the units with a value and those excluded two
    values or more unless what they show gives it, and an undeclared value's count never pinned
    to one from 1 to *k* − 1 (D329)."""
    view = distributed([{"column": "customers.tier"}], k=k)
    secrets = [
        *((name, _of(OPEN_CELLS, (name,))) for name in OPEN_CELLS[:4]),
        ("n", _of(OPEN_CELLS, OPEN_CELLS[:4])),
        ("excluded_units", _of(OPEN_CELLS, ("I",))),
    ]

    def outcome(world: Sequence[int]) -> Outcome:
        found = summarised(view, [size], [([_open_world(world)], None)], k=k)
        assert not any(name in whole(found) for name in UNDECLARED), world
        return found

    small, pinned = _pinned(
        outcome, _open_vectors, OPEN_CELLS, secrets, size, k, small_only=UNDECLARED
    )
    assert small == []
    assert pinned == []

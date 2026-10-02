"""The recession cone of a Cox model's likelihood, in exact arithmetic (SPEC §9.5; D360).

A design is each unit's endpoint row (entry, time, event), its class (at first its stratum) and
its row of covariates. A pair (e, h) is an event e and a unit h at risk at e's time in e's class
(entry < t ≤ time, as ``coxph`` counts risk sets), and v = x_e − x_h. The likelihood's recession
cone is C = {d : v·d ≥ 0 on every pair}; its lineality is the orthogonal complement of the span
of the v's.

- ``integers``: each column scaled exactly to integers (each finite double is n·2^e, n odd; the
  column is multiplied by 2^−e for its least e where that is negative). A positive scaling of a
  column changes neither the cone's faces nor which columns its span holds, so every decision
  below is one of integers.
- ``Echelon``: an exact row echelon form of at most m rows, fraction-free, whose pivots are the
  reduced form's.
- ``span``: the span of the v's without forming the pairs. Every event at t joins its class's
  risk set at t, and a unit's risk sets are those of an interval of its class's event times, so
  the pairs' graph has the components one sweep per class finds, joining each event time's risk
  set to the one before while a member of it remains; the span of the v's is that of each
  unit's x less its component's first.
- ``gradient``: g = Σ v over every pair, Σ_e (n x_e − Σ_h x_h) over each event's risk set; on
  C, g·d > 0 exactly where d is not in the lineality.

Every loop spends its work through ``timetoevent.Watch`` (D350), an integer operation by the
limbs of its operands.
"""

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from fractions import Fraction
from heapq import heappop, heappush
from math import gcd, lcm
from operator import mul

from aibi.core.analyses.timetoevent import Watch

End = tuple[float, float, bool]
"""A unit's entry (``ORIGIN`` at the origin), its time and whether its follow-up ended in an
event."""

LIMB = 64
"""The bits an integer operation spends a unit of work on."""


def limbs(value: int) -> int:
    """The work of an operation on ``value``: one unit a ``LIMB`` of its bits."""
    return 1 + value.bit_length() // LIMB


def size(vectors: Sequence[Sequence[int]]) -> int:
    """The limbs of the largest value of ``vectors``: the work of an operation on any two of
    them, less their product's."""
    largest = 0
    for vector in vectors:
        if vector:
            largest = max(largest, max(map(abs, vector)))
    return limbs(largest)


def dot(a: Sequence[int], b: Sequence[int], watch: Watch, weight: int | None = None) -> int:
    """a·b, spending each product's limbs (``weight`` a product, where the caller bounds
    them)."""
    if weight is None:
        weight = size((a, b))
    watch.spend(len(a) * 2 * weight)
    return sum(map(mul, a, b))


def integers(rows: Sequence[Sequence[float]], watch: Watch) -> list[tuple[int, ...]]:
    """Each row's values, each column multiplied by the least power of 2 that makes all of its
    values integers (module docstring); every value must be finite."""
    m = len(rows[0]) if rows else 0
    ratios = [[value.as_integer_ratio() for value in row] for row in rows]
    watch.spend(len(rows) * max(m, 1))
    scales: list[int] = []
    for j in range(m):
        denominator = 1
        for row in ratios:
            denominator = max(denominator, row[j][1])
        scales.append(denominator)
    found: list[tuple[int, ...]] = []
    for row in ratios:
        values: list[int] = []
        for (numerator, denominator), scale in zip(row, scales, strict=True):
            value = numerator * (scale // denominator)
            watch.spend(limbs(value))
            values.append(value)
        found.append(tuple(values))
    return found


class Echelon:
    """The span of integer vectors of length ``m`` as a row echelon form: each row's first
    nonzero column (its pivot) right of the row before's, each row divided by the greatest
    common divisor of its entries. Its pivots are those of the span's reduced row echelon form
    in column order, the columns not among them the later of each dependency."""

    __slots__ = ("m", "null", "rows", "watch")

    def __init__(self, m: int, watch: Watch) -> None:
        self.m = m
        self.rows: list[tuple[int, ...]] = []
        self.null: list[tuple[int, ...]] = [
            tuple(1 if k == j else 0 for k in range(m)) for j in range(m)
        ]
        self.watch = watch

    def reduced_form(self) -> tuple[tuple[int, ...], list[tuple[int, ...]], int]:
        """The span's reduced row echelon form over integers: its pivots, its rows times the
        least common denominator of their entries, and that denominator."""
        rows = [[Fraction(value) for value in row] for row in self.rows]
        pivots = tuple(_pivot(row) for row in self.rows)
        for i, p in enumerate(pivots):
            head = rows[i][p]
            rows[i] = [value / head for value in rows[i]]
        for i in reversed(range(len(rows))):
            p = pivots[i]
            for r in range(i):
                factor = rows[r][p]
                if factor:
                    self.watch.spend(self.m)
                    rows[r] = [a - factor * b for a, b in zip(rows[r], rows[i], strict=True)]
        denominator = 1
        for row in rows:
            for value in row:
                denominator = lcm(denominator, value.denominator)
        scaled = [tuple(int(value * denominator) for value in row) for row in rows]
        return pivots, scaled, denominator

    @property
    def pivots(self) -> tuple[int, ...]:
        return tuple(_pivot(row) for row in self.rows)

    @property
    def full(self) -> bool:
        return len(self.rows) == self.m

    def reduced(self, vector: Sequence[int]) -> list[int]:
        """``vector`` less its components along the rows, as integers: 0 exactly where the span
        holds it."""
        found = list(vector)
        for row in self.rows:
            p = _pivot(row)
            if found[p] == 0:
                continue
            a, b = row[p], found[p]
            for k in range(self.m):
                self.watch.spend(limbs(a) + limbs(found[k]) + limbs(b) + limbs(row[k]))
                found[k] = a * found[k] - b * row[k]
            _divide(found, self.watch)
        return found

    def add(self, vector: Sequence[int]) -> bool:
        """Adds ``vector`` to the span; whether the span grew."""
        if self.full or self.contains(vector):
            return False
        found = self.reduced(vector)
        if found[_pivot(found)] < 0:
            found = [-value for value in found]
        p = _pivot(found)
        at = sum(1 for row in self.rows if _pivot(row) < p)
        self.rows.insert(at, tuple(found))
        pivots, scaled, denominator = self.reduced_form()
        null: list[tuple[int, ...]] = []
        for f in range(self.m):
            if f in pivots:
                continue
            basis = [0] * self.m
            basis[f] = denominator
            for i, q in enumerate(pivots):
                basis[q] = -scaled[i][f]
            null.append(tuple(basis))
        self.null = null
        return True

    def contains(self, vector: Sequence[int]) -> bool:
        """Whether the span holds ``vector``: whether it is orthogonal to the span's orthogonal
        complement, a basis of which ``null`` keeps."""
        return all(dot(vector, basis, self.watch) == 0 for basis in self.null)

    def holds(self, j: int) -> bool:
        """Whether the span holds the unit vector of column ``j``."""
        return self.contains([1 if k == j else 0 for k in range(self.m)])


def _pivot(row: Sequence[int]) -> int:
    return next(k for k, value in enumerate(row) if value)


def _divide(vector: list[int], watch: Watch) -> None:
    divisor = 0
    for value in vector:
        watch.spend(limbs(value))
        divisor = gcd(divisor, value)
    if divisor > 1:
        for k, value in enumerate(vector):
            vector[k] = value // divisor


@dataclass(frozen=True)
class Step:
    """One event time of a class's sweep: the units that joined its risk set there, those that
    left it since the time before (``left``, of those that had joined), and its events."""

    group: int
    time: float
    joined: tuple[int, ...]
    left: tuple[int, ...]
    events: tuple[int, ...]


def sweep(ends: Sequence[End], classes: Sequence[int], watch: Watch) -> Iterator[Step]:
    """Each class's event times, the classes in increasing order and each's times from the last
    to the first, with the risk set's changes: a unit joins at the first time of its class not
    after its time, unless its entry is not before it, and leaves once the sweep reaches its
    entry."""
    by: dict[int, list[int]] = {}
    for unit, group in enumerate(classes):
        watch.spend()
        by.setdefault(group, []).append(unit)
    for group in sorted(by):
        members = by[group]
        leaving = sorted(members, key=lambda u: -ends[u][1])
        entering = sorted(members, key=lambda u: -ends[u][0])
        times = sorted({ends[u][1] for u in members if ends[u][2]}, reverse=True)
        watch.spend(len(members))
        state = dict.fromkeys(members, 0)
        added = removed = 0
        for when in times:
            left: list[int] = []
            while removed < len(entering) and ends[entering[removed]][0] >= when:
                unit = entering[removed]
                removed += 1
                watch.spend()
                if state[unit] == 1:
                    left.append(unit)
                state[unit] = 2
            joined: list[int] = []
            while added < len(leaving) and ends[leaving[added]][1] >= when:
                unit = leaving[added]
                added += 1
                watch.spend()
                if state[unit] == 2:
                    continue
                state[unit] = 1
                joined.append(unit)
            events = tuple(u for u in joined if ends[u][2] and ends[u][1] == when)
            yield Step(group, when, tuple(joined), tuple(left), events)


def components(ends: Sequence[End], classes: Sequence[int], watch: Watch) -> dict[int, int]:
    """Each unit in some pair, and the first unit of its component of the pairs' graph (module
    docstring), in the order the sweep reaches them."""
    found: dict[int, int] = {}
    group = None
    root = None
    count = 0
    for step in sweep(ends, classes, watch):
        if step.group != group:
            group, root, count = step.group, None, 0
        count -= len(step.left)
        if count == 0:
            root = None
        for unit in step.joined:
            if root is None:
                root = unit
            found[unit] = root
        count += len(step.joined)
    return found


def span(
    ends: Sequence[End], classes: Sequence[int], x: Sequence[Sequence[int]], watch: Watch
) -> Echelon:
    """The span of the v's of every pair (module docstring), stopping once it is full."""
    m = len(x[0]) if x else 0
    found = Echelon(m, watch)
    for unit, root in components(ends, classes, watch).items():
        if found.full:
            break
        if unit != root:
            found.add([a - b for a, b in zip(x[unit], x[root], strict=True)])
    return found


def cosets(
    strata: Sequence[int], x: Sequence[Sequence[int]], final: Echelon, watch: Watch
) -> tuple[int, ...]:
    """Each unit's class: its stratum and its x less the span ``final`` (the reduced form's
    remainder, 0 at its pivots), the classes numbered by those in increasing order. Two units of
    a stratum share a class exactly when x_u − x_w lies in the span."""
    pivots, scaled, denominator = final.reduced_form()
    keys: list[tuple[int, tuple[int, ...]]] = []
    weight = (1 + len(pivots)) * len(x[0] if x else ()) * (size(x) + size(scaled))
    for stratum, row in zip(strata, x, strict=True):
        watch.spend(weight)
        key = [denominator * value for value in row]
        for p, reduced in zip(pivots, scaled, strict=True):
            if row[p]:
                factor = row[p]
                key = [a - factor * b for a, b in zip(key, reduced, strict=True)]
        keys.append((stratum, tuple(key)))
    number = {key: k for k, key in enumerate(sorted(set(keys)))}
    watch.spend(len(keys))
    return tuple(number[key] for key in keys)


def gradient(
    ends: Sequence[End], classes: Sequence[int], x: Sequence[Sequence[int]], watch: Watch
) -> tuple[int, ...]:
    """g = Σ v over every pair (module docstring)."""
    m = len(x[0]) if x else 0
    found: list[int] = [0] * m
    group = None
    count = 0
    sums: list[int] = [0] * m
    weight = m * (size(x) + limbs(len(x)))
    for step in sweep(ends, classes, watch):
        if step.group != group:
            group, count, sums = step.group, 0, [0] * m
        for unit in step.left:
            count -= 1
            watch.spend(weight)
            sums = [a - b for a, b in zip(sums, x[unit], strict=True)]
        for unit in step.joined:
            count += 1
            watch.spend(weight)
            sums = [a + b for a, b in zip(sums, x[unit], strict=True)]
        for unit in step.events:
            watch.spend(3 * weight)
            found = [f + count * a - b for f, a, b in zip(found, x[unit], sums, strict=True)]
    return tuple(found)


DEGENERATE = 20
"""The run of degenerate pivots after which ``Master`` takes Bland's rule, until a pivot that
lowers its objective: a cycle of bases needs a run of degenerate pivots, which Bland's rule
ends."""


class Master:
    """The linear programme max c·d over {d : v·d ≥ 0 on the cuts, −1 ≤ d ≤ 1}, as its dual:
    min Σ (μ⁺ + μ⁻) subject to −Σ λ_p v_p + μ⁺ − μ⁻ = c, λ, μ ≥ 0, whose simplex multipliers
    are d. A revised simplex over integers: the basis's determinant ``det`` and its adjugate
    ``adjugate`` (det · B⁻¹), a pivot on row r with w = adjugate·a making row r's unchanged and
    each other row i (w_r·row_i − w_i·row_r) / det, an exact division, det becoming w_r;
    Dantzig's rule, Bland's after ``DEGENERATE`` degenerate pivots. It starts from μ⁺_i where
    c_i ≥ 0 and μ⁻_i otherwise, a feasible basis."""

    def __init__(self, c: Sequence[int], watch: Watch) -> None:
        m = len(c)
        self.m = m
        self.c = list(c)
        self.watch = watch
        self.columns: list[tuple[int, ...]] = []
        self.costs: list[int] = []
        for i in range(m):
            for sign in (1, -1):
                self.columns.append(tuple(sign if k == i else 0 for k in range(m)))
                self.costs.append(1)
        self.basis = [2 * i + (0 if c[i] >= 0 else 1) for i in range(m)]
        det = 1
        for i in range(m):
            det *= self.columns[self.basis[i]][i]
        self.det = det
        self.adjugate = [
            [det * self.columns[self.basis[i]][i] if k == i else 0 for k in range(m)]
            for i in range(m)
        ]
        self.values = [sum(row[k] * c[k] for k in range(m)) for row in self.adjugate]
        self.pivots = 0

    def cut(self, v: Sequence[int]) -> None:
        """Adds the cut v·d ≥ 0: a column −v at cost 0."""
        self.columns.append(tuple(-value for value in v))
        self.costs.append(0)

    def multipliers(self) -> list[int]:
        """The simplex multipliers times |det|: d, up to a positive factor."""
        m = self.m
        found = [0] * m
        for i in range(m):
            cost = self.costs[self.basis[i]]
            if cost:
                row = self.adjugate[i]
                for k in range(m):
                    self.watch.spend(limbs(row[k]))
                    found[k] += cost * row[k]
        return found if self.det > 0 else [-value for value in found]

    def solve(self) -> None:
        """Pivots to an optimal basis of the columns it has."""
        degenerate = 0
        while True:
            y = self.multipliers()
            scale = abs(self.det)
            entering = None
            best = 0
            basic = set(self.basis)
            for j, column in enumerate(self.columns):
                if j in basic:
                    continue
                reduced = scale * self.costs[j]
                for k in range(self.m):
                    if column[k]:
                        self.watch.spend(limbs(y[k]) + limbs(column[k]))
                        reduced -= y[k] * column[k]
                if reduced < 0 and (entering is None or reduced < best):
                    entering, best = j, reduced
                    if degenerate >= DEGENERATE:
                        break
            if entering is None:
                return
            if self._pivot(entering):
                degenerate = 0
            else:
                degenerate += 1

    def _pivot(self, entering: int) -> bool:
        """Pivots ``entering`` into the basis by the ratio test, the least basis index among
        ties; whether the objective fell (the pivot was not degenerate)."""
        m = self.m
        column = self.columns[entering]
        alpha = [0] * m
        for i in range(m):
            row = self.adjugate[i]
            for k in range(m):
                if column[k]:
                    self.watch.spend(limbs(row[k]) + limbs(column[k]))
                    alpha[i] += row[k] * column[k]
        sign = 1 if self.det > 0 else -1
        leaving = None
        for i in range(m):
            if alpha[i] * sign <= 0:
                continue
            if leaving is None:
                leaving = i
                continue
            a, b = (
                abs(self.values[i]) * abs(alpha[leaving]),
                abs(self.values[leaving]) * abs(alpha[i]),
            )
            if a < b or (a == b and self.basis[i] < self.basis[leaving]):
                leaving = i
        if leaving is None:
            raise AssertionError("the dual of a bounded programme is bounded")
        r = leaving
        pivot = alpha[r]
        det = self.det
        rows = self.adjugate
        for i in range(m):
            if i == r:
                continue
            factor = alpha[i]
            row = rows[i]
            for k in range(m):
                self.watch.spend(2 * limbs(pivot) + limbs(row[k]) + limbs(rows[r][k]))
                row[k] = (pivot * row[k] - factor * rows[r][k]) // det
            self.values[i] = (pivot * self.values[i] - factor * self.values[r]) // det
        moved = self.values[r] != 0
        self.det = pivot
        self.basis[r] = entering
        self.pivots += 1
        return moved

    def certificate(self) -> dict[int, tuple[int, int]]:
        """The basis's cut columns and their weights as fractions (numerator, denominator):
        −c = Σ λ v over them where the optimum is 0."""
        return {
            j: (self.values[i], self.det)
            for i, j in enumerate(self.basis)
            if self.costs[j] == 0 and self.values[i]
        }


CUTS = 16
"""The most violated pairs a pricing pass adds to the master, one an event at most."""

Pool = dict[tuple[int, int], tuple[int, ...]]
"""Cuts found, by their pair (event, unit at risk), and their v: shared by every programme over
one design, each programme taking those whose two units share one of its classes (any pair of
the strata is a cut of the cone itself; one of a level's classes is also a cut of its level)."""


@dataclass(frozen=True)
class Optimum:
    """A programme's optimum: d up to a positive factor, whether c·d > 0 there, and the pivots
    and pricing passes it took."""

    direction: tuple[int, ...]
    positive: bool
    pivots: int
    passes: int


def violated(
    ends: Sequence[End],
    classes: Sequence[int],
    x: Sequence[Sequence[int]],
    direction: Sequence[int],
    watch: Watch,
) -> list[tuple[int, int, int]]:
    """Each event's most violated pair under ``direction`` (x_e·d below the largest x_h·d of
    its risk set, h the least index among the units that reach it): (violation, event, unit),
    the most violated first, ties in order of their units."""
    weight = size(x) + size((direction,))
    values = [dot(row, direction, watch, weight) for row in x]
    found: list[tuple[int, int, int]] = []
    group = None
    heap: list[tuple[int, int]] = []
    present: set[int] = set()
    for step in sweep(ends, classes, watch):
        if step.group != group:
            group, heap, present = step.group, [], set[int]()
        for unit in step.left:
            present.discard(unit)
        for unit in step.joined:
            present.add(unit)
            watch.spend(2 * weight)
            heappush(heap, (-values[unit], unit))
        while heap and heap[0][1] not in present:
            heappop(heap)
        if not heap:
            continue
        top, unit = -heap[0][0], heap[0][1]
        for event in step.events:
            if values[event] < top:
                found.append((top - values[event], event, unit))
    found.sort(key=lambda item: (-item[0], item[1], item[2]))
    return found


def optimum(
    ends: Sequence[End],
    classes: Sequence[int],
    x: Sequence[Sequence[int]],
    c: Sequence[int],
    pool: Pool,
    watch: Watch,
) -> Optimum:
    """max c·d over the cone of the design's pairs and the box (``Master``), by columns: the
    master over the pool's cuts valid here, then pricing (``violated``) adds the ``CUTS`` most
    violated pairs, until none is; its d is then exactly in the cone."""
    master = Master(c, watch)
    for (event, unit), v in pool.items():
        watch.spend()
        if classes[event] == classes[unit]:
            master.cut(v)
    passes = 0
    candidates: list[tuple[int, int]] = []
    while True:
        watch.look()
        master.solve()
        direction = master.multipliers()
        found = _violated_among(candidates, x, direction, watch)
        if not found:
            passes += 1
            found = violated(ends, classes, x, direction, watch)
            if not found:
                total = sum(a * b for a, b in zip(c, direction, strict=True))
                return Optimum(tuple(direction), total > 0, master.pivots, passes)
            candidates = [(event, unit) for _, event, unit in found]
        for _, event, unit in found[:CUTS]:
            v = tuple(a - b for a, b in zip(x[event], x[unit], strict=True))
            pool[(event, unit)] = v
            master.cut(v)


def _violated_among(
    candidates: Sequence[tuple[int, int]],
    x: Sequence[Sequence[int]],
    direction: Sequence[int],
    watch: Watch,
) -> list[tuple[int, int, int]]:
    """The pairs of ``candidates`` (the last pricing pass's) that ``direction`` violates, as
    ``violated`` gives them: pricing over them alone between full passes, a pair of the design
    being a cut wherever it is violated, and the full pass the only one that ends a
    programme."""
    values: dict[int, int] = {}
    found: list[tuple[int, int, int]] = []
    weight = size(x) + size((direction,))
    for event, unit in candidates:
        for one in (event, unit):
            if one not in values:
                values[one] = dot(x[one], direction, watch, weight)
        if values[event] < values[unit]:
            found.append((values[unit] - values[event], event, unit))
    found.sort(key=lambda item: (-item[0], item[1], item[2]))
    return found


@dataclass(frozen=True)
class Levels:
    """The cone's levels: each direction found (d_i, in C_i with g_i·d_i > 0), each unit's class
    at the last level, and the span of the pairs left there, whose orthogonal complement is the
    span of the cone (the pairs left are its implicit equalities). The classes depend on the
    directions the programmes found; ``cosets`` of the span do not."""

    directions: tuple[tuple[int, ...], ...]
    classes: tuple[int, ...]
    final: Echelon


def refined(classes: Sequence[int], values: Sequence[int], watch: Watch) -> tuple[int, ...]:
    """Each unit's class split by its value, the classes numbered by (class, value) in
    increasing order."""
    watch.spend(len(classes))
    keys = sorted(set(zip(classes, values, strict=True)))
    number = {key: k for k, key in enumerate(keys)}
    return tuple(number[key] for key in zip(classes, values, strict=True))


def levels(
    ends: Sequence[End],
    strata: Sequence[int],
    x: Sequence[Sequence[int]],
    pool: Pool,
    watch: Watch,
) -> Levels:
    """The levels of the cone of the design (module docstring): from the strata, while the
    programme max g·d over the cone and the box is positive, each class split by x·d."""
    classes = tuple(strata)
    directions: list[tuple[int, ...]] = []
    while True:
        watch.look()
        g = gradient(ends, classes, x, watch)
        if not any(g):
            break
        found = optimum(ends, classes, x, g, pool, watch)
        if not found.positive:
            break
        directions.append(found.direction)
        weight = size(x) + size((found.direction,))
        values = [dot(row, found.direction, watch, weight) for row in x]
        classes = refined(classes, values, watch)
    return Levels(tuple(directions), classes, span(ends, classes, x, watch))


def signs(
    ends: Sequence[End],
    strata: Sequence[int],
    x: Sequence[Sequence[int]],
    columns: Sequence[int],
    pool: Pool,
    directions: Sequence[Sequence[int]],
    watch: Watch,
) -> dict[int, tuple[bool, bool]]:
    """For each of ``columns``, whether the cone of the design holds a d with d_j > 0 and one
    with d_j < 0: the sign of the first of the levels' ``directions`` whose d_j is not 0, which
    the cone holds (their lexicographic combination, Σ N^(K−i) d_i for N large, lies in it), and
    otherwise the programmes max ±d_j over the cone and the box, over the strata, each optimum a
    witness for the columns after it."""
    m = len(x[0]) if x else 0
    known: list[tuple[int, ...]] = []
    for j in range(m):
        first = next((d[j] for d in directions if d[j]), 0)
        if first:
            known.append(tuple(first if k == j else 0 for k in range(m)))
    found: dict[int, tuple[bool, bool]] = {}
    for j in columns:
        sides: list[bool] = []
        for sign in (1, -1):
            if any(w[j] * sign > 0 for w in known):
                sides.append(True)
                continue
            c = [sign if k == j else 0 for k in range(m)]
            result = optimum(ends, strata, x, c, pool, watch)
            if result.positive:
                known.append(result.direction)
            sides.append(result.positive)
        found[j] = (sides[0], sides[1])
    return found

"""The Cox model of a design: each unit's row of covariates, its stratum and its endpoint row,
held to R's ``coxph`` and ``cox.zph`` (SPEC §9.5; D358, D359).

- ``fit``: the Cox model of any design, Efron's ties, by ``coxfit``'s search (``maximise``: R's
  iteration from 0 where it ends at the maximum, else from the point a safeguarded ascent
  reaches, D356) over a model of rows (``_Rows``). Identical rows (stratum, entry, time, event
  and covariates) are one row with a count, whose sums take the count times the weight where C sums
  per unit, so values agree with R to rounding. Its columns are R's (``coxph.fit`` and
  ``agreg.fit`` at survival 3.5-8): one whose values all lie in {−1, 0, 1} is neither centred
  nor scaled (``nocenter``); another is centred, by its mean without an entry column
  (``coxfit6``) and by the value of the first unit ``agfit4`` sorts with one (the latest to
  leave of the first stratum, the first given among ties, of the units whose follow-up spans an
  event time of their stratum: ``agfit4`` subtracts it inside its loop over units), and scaled
  by the units counted over the sum of their distances from that centre, the coefficients and
  their variance reported on the columns' own scale; a column whose spread or scale's square no
  double holds is left out (``unscalable``). The certificate and the ascent run
  on the scaled columns. A column that ``cholesky2`` finds dependent on those before it in the
  information at 0 is dropped (``dropped``), as R reports it ``NA``; R decides at every
  iteration, and in a narrow band of near dependence keeps one that this drops (D359). With an
  entry column every weight is taken as exp(η − c), c recentred as ``agfit4`` recentres it
  (``_Rows``); without one, as ``coxfit6``, it is not.
- ``proportional_hazards``: ``cox.zph(transform = "km")``'s global test of a fit with strata:
  the Kaplan–Meier transform of time over every unit pooled across strata, less its mean over
  their events, the covariates centred by their mean over every unit, risk sets per stratum, as
  ``zph1`` and ``zph2`` accumulate them (``coxfit.proportional_hazards`` for cohorts), its
  sums reset where the risk set empties, where ``zph2`` keeps the rounding of the units that
  left (D359).

Every sum of weights is C's arithmetic (``ieee``), a unit's η summed correctly rounded, and every
loop spends its work through ``timetoevent.Watch`` (D350): a row added to or removed from a risk
set, and a share of a tied time's events, the terms squared each.
"""

import math
from bisect import bisect_left, bisect_right
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType

from aibi.core.analyses import cone, ieee
from aibi.core.analyses.coxfit import (
    CoxFit,
    Evaluated,
    Test,
    factored_of,
    maximise,
    solved_quadratic,
)
from aibi.core.analyses.timetoevent import ORIGIN, Watch, chi_squared_p, cholesky

NOCENTER = frozenset({-1.0, 0.0, 1.0})
"""``coxph``'s ``nocenter``: a column of these values alone is neither centred nor scaled."""


@dataclass(frozen=True)
class Unit:
    """A unit of a design: its stratum, its entry (``ORIGIN`` without an entry column), its
    time, whether its follow-up ended in an event, and its covariates."""

    stratum: int
    entry: float
    time: float
    event: bool
    x: tuple[float, ...]


@dataclass(frozen=True)
class Fitted:
    """A design's fit: the columns fitted, those dropped as dependent and those no double can
    scale (``fit``), and the fit of the columns kept, on their own scale, none where no column
    is kept."""

    kept: tuple[int, ...]
    dropped: tuple[int, ...]
    fit: CoxFit | None
    unscalable: tuple[int, ...] = ()


@dataclass(frozen=True)
class _Row:
    stratum: int
    entry: float
    time: float
    event: bool
    count: int
    x: tuple[float, ...]


def _collapsed(units: Sequence[Unit], watch: Watch) -> list[_Row]:
    """The distinct units with their counts, in the order each first appears; each unit spends
    its covariates."""
    counts: dict[tuple[int, float, float, bool, tuple[float, ...]], int] = {}
    for unit in units:
        watch.spend(1 + len(unit.x))
        key = (unit.stratum, unit.entry, unit.time, unit.event, unit.x)
        counts[key] = counts.get(key, 0) + 1
    return [_Row(s, e, t, ev, count, x) for (s, e, t, ev, x), count in counts.items()]


def _used(units: Sequence[Unit], counting: bool, watch: Watch) -> list[Unit]:
    """The units R's fit counts: all without ``counting``, and with it those whose follow-up
    spans an event time of their stratum (``agreg.fit``'s units not ``ignore``d)."""
    if not counting:
        return list(units)
    times: dict[int, list[float]] = {}
    for unit in units:
        watch.spend()
        if unit.event:
            times.setdefault(unit.stratum, []).append(unit.time)
    for found in times.values():
        found.sort()
    used: list[Unit] = []
    for unit in units:
        watch.spend()
        found = times.get(unit.stratum, [])
        if bisect_right(found, unit.time) > bisect_right(found, unit.entry):
            used.append(unit)
    return used


def _centres(
    units: Sequence[Unit], used: Sequence[Unit], counting: bool, watch: Watch
) -> tuple[list[float], list[float]]:
    """Each column's centre and scale as ``coxfit6`` (without ``counting``) or ``agfit4`` (with
    it) finds them over the units it counts, ``used`` (module docstring): 0 and 1 for a column
    of values in ``NOCENTER``; each unit spends a unit a column and pass."""
    m = len(units[0].x) if units else 0
    centres, scales = [0.0] * m, [1.0] * m
    if not used:
        return centres, scales
    first = None
    if counting:
        watch.spend(3 * len(used))
        low = min(u.stratum for u in used)
        last = max(u.time for u in used if u.stratum == low)
        first = next(u for u in used if u.stratum == low and u.time == last)
    ordered = sorted(used, key=lambda u: (u.stratum, u.time)) if first is None else []
    for j in range(m):
        watch.spend(len(units))
        if all(u.x[j] in NOCENTER for u in units):
            continue
        if first is not None:
            centre = first.x[j]
        else:
            total = 0.0
            for u in ordered:
                watch.spend()
                total += u.x[j]
            centre = ieee.div(total, len(used))
        spread = 0.0
        for u in used:
            watch.spend()
            spread += abs(u.x[j] - centre)
        centres[j] = centre
        scales[j] = ieee.div(len(used), spread) if spread > 0 else 1.0
    return centres, scales


RECENTRE = 200.0
"""``agfit4``'s and ``zph2``'s bound: once the mean linear predictor of the units at risk moves
this far from the shift its sums are taken at, they are shifted to it (``_Rows``)."""
_PENDING, _IN, _GONE = 0, 1, 2
"""A row's place in its stratum's sweep: not yet reached, in the risk set, or past its entry."""


class _Rows:
    """A model of rows (``Model``): the Efron partial log-likelihood of the columns ``columns``
    of rows already scaled, its score and its information at a point, per stratum, event times
    from the last to the first, a row joining the risk set at its time and leaving it once the
    sweep reaches its entry, the sums reset to 0 when no row is at risk; with ``recentre`` (an
    entry column, as ``agfit4`` and ``zph2``; ``coxfit6`` and ``zph1`` do not) every weight
    taken as exp(η − c), c moved to the mean η of the units at risk whenever that is more than
    ``RECENTRE`` from it and the sums already made, where any are, rescaled with it (as
    ``agfit4`` and ``zph2`` do, but for the sums of a tied time's events, which they leave at
    the old c; ``zph2``'s second shift, of η at the end of a time, changes only rounding and is
    not taken). With time
    ``weights`` per stratum and event time, the model extended as ``zph1`` and ``zph2`` extend
    it; ``zph2`` counts a unit its sweep meets past its entry as at risk, so that where one is
    its sums are never reset and keep the rounding of the units that left, and this resets
    them as the fit does (D359)."""

    def __init__(
        self, rows: Sequence[_Row], columns: Sequence[int], watch: Watch, *, recentre: bool
    ) -> None:
        self.watch = watch
        self.recentre = recentre
        self.columns = list(columns)
        by: dict[int, list[_Row]] = {}
        for row in rows:
            watch.spend()
            by.setdefault(row.stratum, []).append(row)
        self.strata: list[tuple[list[_Row], list[int], list[int], list[float]]] = []
        for stratum in sorted(by):
            found = by[stratum]
            leaving = sorted(range(len(found)), key=lambda k: -found[k].time)
            entering = sorted(range(len(found)), key=lambda k: -found[k].entry)
            times = sorted({r.time for r in found if r.event}, reverse=True)
            self.strata.append((found, leaving, entering, times))

    def event_times(self) -> list[float]:
        """Each stratum's event times, in the order the model visits them."""
        return [t for _, _, _, times in self.strata for t in times]

    def __call__(
        self,
        beta: Sequence[float],
        weights: Sequence[float] | None = None,
        tested: Sequence[int] | None = None,
    ) -> Evaluated:
        self.watch.look()
        m = len(self.columns)
        cells = max(m * m, 1)
        slot: dict[int, int] = {}
        if weights is not None:
            chosen = range(m) if tested is None else sorted(set(tested))
            slot = {i: m + k for k, i in enumerate(chosen)}
        size = m + len(slot)
        loglik = 0.0
        score = [0.0] * size
        information = [[0.0] * size for _ in range(size)]
        at = 0
        recenter = 0.0
        for rows, leaving, entering, times in self.strata:
            state = [_PENDING] * len(rows)
            denom = 0.0
            a = [0.0] * m
            cmat = [[0.0] * m for _ in range(m)]
            added = removed = risk_count = 0
            etasum = 0.0
            for when in times:
                timewt = 1.0 if weights is None else weights[at]
                at += 1
                while removed < len(entering) and rows[entering[removed]].entry >= when:
                    index = entering[removed]
                    removed += 1
                    self.watch.spend()
                    was = state[index]
                    state[index] = _GONE
                    if was != _IN:
                        continue
                    row = rows[index]
                    self.watch.spend(cells)
                    risk_count -= row.count
                    if risk_count == 0:
                        etasum = denom = 0.0
                        a = [0.0] * m
                        cmat = [[0.0] * m for _ in range(m)]
                        continue
                    x = [row.x[c] for c in self.columns]
                    eta = ieee.fsum(b * v for b, v in zip(beta, x, strict=True))
                    etasum -= row.count * eta
                    weight = row.count * ieee.exp(eta - recenter)
                    denom -= weight
                    for i in range(m):
                        a[i] -= weight * x[i]
                        for j in range(i + 1):
                            cmat[i][j] -= weight * x[i] * x[j]
                ties = 0
                denom2 = 0.0
                a2 = [0.0] * m
                cmat2 = [[0.0] * m for _ in range(m)]
                while added < len(leaving) and rows[leaving[added]].time >= when:
                    index = leaving[added]
                    added += 1
                    self.watch.spend(cells)
                    row = rows[index]
                    x = [row.x[c] for c in self.columns]
                    eta = ieee.fsum(b * v for b, v in zip(beta, x, strict=True))
                    if state[index] == _GONE:
                        continue
                    state[index] = _IN
                    etasum += row.count * eta
                    risk_count += row.count
                    shift = ieee.div(etasum, risk_count) - recenter
                    if self.recentre and abs(shift) > RECENTRE:
                        recenter += shift
                        loglik -= ties * shift
                        if denom > 0 or denom2 > 0:
                            factor = ieee.exp(-shift)
                            denom *= factor
                            denom2 *= factor
                            for i in range(m):
                                a[i] *= factor
                                a2[i] *= factor
                                for j in range(i + 1):
                                    cmat[i][j] *= factor
                                    cmat2[i][j] *= factor
                    weight = row.count * ieee.exp(eta - recenter)
                    if row.event and row.time == when:
                        ties += row.count
                        denom2 += weight
                        loglik += row.count * (eta - recenter)
                        for i in range(m):
                            score[i] += row.count * x[i]
                            if i in slot:
                                score[slot[i]] += timewt * row.count * x[i]
                            a2[i] += weight * x[i]
                            for j in range(i + 1):
                                cmat2[i][j] += weight * x[i] * x[j]
                    else:
                        denom += weight
                        for i in range(m):
                            a[i] += weight * x[i]
                            for j in range(i + 1):
                                cmat[i][j] += weight * x[i] * x[j]
                for _ in range(ties):
                    self.watch.spend(cells)
                    denom += ieee.div(denom2, ties)
                    loglik -= ieee.log(denom)
                    for i in range(m):
                        a[i] += ieee.div(a2[i], ties)
                        mean = ieee.div(a[i], denom)
                        score[i] -= mean
                        if i in slot:
                            score[slot[i]] -= timewt * mean
                        for j in range(i + 1):
                            cmat[i][j] += ieee.div(cmat2[i][j], ties)
                            cell = ieee.div(cmat[i][j] - mean * a[j], denom)
                            information[j][i] += cell
                            if not slot:
                                continue
                            if i in slot:
                                information[j][slot[i]] += timewt * cell
                                if j in slot:
                                    information[slot[j]][slot[i]] += timewt * timewt * cell
                            if j != i and j in slot:
                                information[i][slot[j]] += timewt * cell
        for i in range(m):
            for j in range(i):
                information[i][j] = information[j][i]
        for k in range(m, size):
            for q in range(m, k):
                information[k][q] = information[q][k]
            for j in range(m):
                information[k][j] = information[j][k]
        return Evaluated(loglik, score, information)


def _shifted(
    units: Sequence[Unit], centres: Sequence[float], scales: Sequence[float], watch: Watch
) -> list[Unit]:
    """Each unit with its covariates less the centres, times the scales; each spends them."""
    found: list[Unit] = []
    for u in units:
        watch.spend(1 + len(u.x))
        x = tuple((v - c) * s for v, c, s in zip(u.x, centres, scales, strict=True))
        found.append(Unit(u.stratum, u.entry, u.time, u.event, x))
    return found


def _prepared(
    units: Sequence[Unit], watch: Watch, *, counting: bool, columns: Sequence[int] | None
) -> tuple[list[_Row], list[float], Fitted]:
    """The rows ``fit`` fits, scaled, the columns' scales, and its columns: of ``columns`` (every
    column where none are named), those whose scale or its square no double holds
    (``unscalable``), then those ``cholesky2`` drops from the information at 0, and the rest."""
    m = len(units[0].x) if units else 0
    if any((unit.entry != ORIGIN) != counting for unit in units):
        raise ValueError("counting says whether the units have entries")
    watch.look()
    named = list(range(m)) if columns is None else sorted(set(columns))
    used = _used(units, counting, watch)
    centres, scales = _centres(units, used, counting, watch)
    unscalable = tuple(
        j
        for j in named
        if not 0 < scales[j] * scales[j] < math.inf or not all(math.isfinite(u.x[j]) for u in units)
    )
    scalable = [j for j in named if j not in unscalable]
    rows = _collapsed(_shifted(used, centres, scales, watch), watch)
    watch.look()
    whole = _Rows(rows, scalable, watch, recentre=counting)
    matrix, _ = factored_of(whole([0.0] * len(scalable)))
    kept = tuple(j for k, j in enumerate(scalable) if matrix[k][k] != 0)
    dropped = tuple(j for k, j in enumerate(scalable) if matrix[k][k] == 0)
    return rows, scales, Fitted(kept, dropped, None, unscalable)


def columns_of(
    units: Sequence[Unit], watch: Watch, *, counting: bool, columns: Sequence[int] | None = None
) -> Fitted:
    """``fit``'s columns, without the fit (D359): of ``columns`` (every column where none are
    named), those it leaves out as ``unscalable``, those it drops as dependent and those it
    keeps."""
    return _prepared(units, watch, counting=counting, columns=columns)[2]


def fit(
    units: Sequence[Unit], watch: Watch, *, counting: bool, columns: Sequence[int] | None = None
) -> Fitted:
    """The Cox model of ``units`` (module docstring) over ``columns`` (every column where none
    are named), whose entries are all ``ORIGIN`` or none is, as ``counting`` says: R's columns,
    those whose scale or its square no double holds left out (``unscalable``), then those
    ``cholesky2`` drops from the information at 0, and ``coxfit.maximise`` over the rest; the
    coefficients and their variance on the columns' own scale, and not converged where one of
    them there is not finite."""
    rows, scales, found = _prepared(units, watch, counting=counting, columns=columns)
    return _maximised(rows, scales, found, watch, counting=counting)


def _maximised(
    rows: Sequence[_Row], scales: Sequence[float], found: Fitted, watch: Watch, *, counting: bool
) -> Fitted:
    """``fit`` of the rows ``_prepared`` gives, over the columns it keeps."""
    kept, dropped, unscalable = found.kept, found.dropped, found.unscalable
    if not kept:
        return Fitted(kept, dropped, None, unscalable)
    model = _Rows(rows, kept, watch, recentre=counting)
    best = maximise(model, len(kept), counting=counting)
    own = [scales[j] for j in kept]
    coefficients = tuple(b * s for b, s in zip(best.coefficients, own, strict=True))
    variance = tuple(
        tuple(best.variance[i][k] * own[i] * own[k] for k in range(len(kept)))
        for i in range(len(kept))
    )
    finite = all(math.isfinite(v) for v in coefficients) and all(
        math.isfinite(v) for row in variance for v in row
    )
    converged = best.converged and finite
    return Fitted(
        kept,
        dropped,
        CoxFit(coefficients, variance, converged, best.loglik, best.iterations, best.ascended),
        unscalable,
    )


@dataclass(frozen=True)
class Separated:
    """A design's columns by what its likelihood's recession cone says of them (D360), and the
    fit of its finite part (D361):

    - ``estimated``: the columns whose unit vectors the span of the cone is orthogonal to, their
      coefficients finite and the finite part's;
    - ``separation``: the others whose d_j keeps one sign on the cone, +1 where the coefficient
      runs to +∞ and −1 where it runs to −∞;
    - ``unidentified``: those in the lineality's support (an exact dependency), those
      ``cholesky2`` drops at 0, those whose d_j takes both signs, and those not separated that
      the finite part's fit drops or cannot scale (a separated column's value in it is never
      reported, and a rounding residue there does not undo the exact sign);
    - ``unscalable``: those no double can scale (``fit``);
    - ``classes``: each unit's class, its stratum and its covariates less the span of the pairs
      the cone's last level leaves (``cone.cosets``), the finite part's strata;
    - ``free``: the columns the finite part fits, the others of the model fixed at 0;
    - ``fit``: the finite part's fit over ``free``, none where nothing is free."""

    estimated: tuple[int, ...]
    separation: Mapping[int, int]
    unidentified: tuple[int, ...]
    unscalable: tuple[int, ...]
    classes: tuple[int, ...]
    free: tuple[int, ...]
    fit: CoxFit | None


def separated(units: Sequence[Unit], watch: Watch, *, counting: bool) -> Separated:
    """The columns of the design ``units`` by the recession cone of its likelihood, and the fit
    of its finite part (``Separated``; D360, D361): ``fit``'s columns (``unscalable`` left out,
    ``cholesky2``'s drop at 0, as R drops them); the pairs' span in exact integers
    (``cone.span``), the kept columns first, the non-pivots of its echelon form (the later
    columns of each exact dependency, the dropped among them) fixed at 0 and every column in
    its orthogonal complement's support not identified; the cone's levels over the kept pivots
    (``cone.levels``), those its last span holds estimated, the others' signs on the cone
    (``cone.signs``); and ``fit`` of the units stratified by their classes at the last level
    over the kept pivots but the last span's non-pivots."""
    rows, scales, numeric = _prepared(units, watch, counting=counting, columns=None)
    unscalable = numeric.unscalable
    order = list(numeric.kept) + list(numeric.dropped)
    ends = [(u.entry, u.time, u.event) for u in units]
    strata = [u.stratum for u in units]
    exact = cone.integers([[u.x[j] for j in order] for u in units], watch)
    lineal = cone.span(ends, strata, exact, watch)
    unidentified = {order[k] for k in range(len(order)) if not lineal.holds(k)}
    unidentified.update(numeric.dropped)
    pivots = set(lineal.pivots)
    local = [k for k in range(len(numeric.kept)) if k in pivots]
    kept = [order[k] for k in local]
    x = [tuple(row[k] for k in local) for row in exact]
    pool: cone.Pool = {}
    found = cone.levels(ends, strata, x, pool, watch)
    estimated = [j for k, j in enumerate(kept) if found.final.holds(k)]
    undecided = [k for k, j in enumerate(kept) if j not in unidentified and j not in estimated]
    sides = cone.signs(ends, strata, x, undecided, pool, found.directions, watch)
    separation: dict[int, int] = {}
    for k, (up, down) in sides.items():
        if not (up or down):
            raise AssertionError("a column the cone's span does not hold has a sign on it")
        if up and down:
            unidentified.add(kept[k])
        else:
            separation[kept[k]] = 1 if up else -1
    final = set(found.final.pivots)
    free = tuple(j for k, j in enumerate(kept) if k in final)
    whole = cone.span(ends, found.classes, exact, watch)
    if whole.full:
        number = {stratum: k for k, stratum in enumerate(sorted(set(strata)))}
        classes = tuple(number[stratum] for stratum in strata)
    else:
        classes = cone.cosets(strata, exact, whole, watch)
    if free == numeric.kept:
        finite = _maximised(rows, scales, numeric, watch, counting=counting)
    elif free:
        classified = [
            Unit(group, u.entry, u.time, u.event, u.x)
            for group, u in zip(classes, units, strict=True)
        ]
        finite = fit(classified, watch, counting=counting, columns=free)
    else:
        finite = None
    if finite is not None:
        lost = (set(finite.dropped) | set(finite.unscalable)) & set(free)
        unidentified.update(lost - set(separation))
    reported = tuple(j for j in estimated if j not in unidentified)
    if reported and (finite is None or not set(reported) <= set(finite.kept)):
        raise AssertionError("an estimated column the finite part does not fit")
    labels = [*reported, *separation, *unidentified, *unscalable]
    if sorted(labels) != list(range(len(units[0].x) if units else 0)):
        raise AssertionError("every column has exactly one of the cone's labels")
    return Separated(
        reported,
        MappingProxyType(separation),
        tuple(sorted(unidentified)),
        unscalable,
        classes,
        free if finite is None else finite.kept,
        None if finite is None else finite.fit,
    )


def _full_rank(information: Sequence[Sequence[float]]) -> bool:
    """Whether ``cholesky`` finds an information of full rank once each row and column is
    divided by the square root of its diagonal: a test that the covariates' scales do not
    decide, as R's ``solve`` is not decided by them."""
    diagonal = [information[i][i] for i in range(len(information))]
    if not all(value > 0 for value in diagonal):
        return False
    roots = [ieee.sqrt(value) for value in diagonal]
    matrix = [
        [ieee.div(cell, roots[i] * roots[j]) for j, cell in enumerate(row)]
        for i, row in enumerate(information)
    ]
    return cholesky(matrix) == len(matrix)


def proportional_hazards(
    units: Sequence[Unit], fitted: Fitted, watch: Watch, *, tested: Sequence[int] | None = None
) -> Test | None:
    """The global test of proportional hazards of ``fitted`` over ``units`` (module docstring),
    of its kept columns ``tested`` (all of them where none are named), the others of the fit's
    score taken as 0 as the terms' own are; none where the fit has no column or did not
    converge, or where the extended information has less than full rank (``_full_rank``)."""
    if fitted.fit is None or not fitted.fit.converged:
        return None
    kept = list(fitted.kept)
    m = len(kept)
    chosen = list(range(m)) if tested is None else sorted(kept.index(j) for j in set(tested))
    if not chosen:
        return None
    watch.look()
    watch.spend(len(units))
    events = sorted({u.time for u in units if u.event})
    change = [0] * (len(events) + 1)
    ended = [0] * len(events)
    for u in units:
        watch.spend()
        change[bisect_right(events, u.entry)] += 1
        change[bisect_right(events, u.time)] -= 1
        if u.event:
            ended[bisect_left(events, u.time)] += 1
    at_risk: list[int] = []
    running = 0
    for step in change[:-1]:
        running += step
        at_risk.append(running)
    transform: dict[float, float] = {}
    survival = 1.0
    for when, risk, count in zip(events, at_risk, ended, strict=True):
        watch.spend()
        transform[when] = 1 - survival
        survival *= ieee.div(risk - count, risk)
    marked: list[float] = []
    totals: list[list[float]] = [[] for _ in kept]
    columns: list[Unit] = []
    for u in units:
        watch.spend(1 + m)
        if u.event:
            marked.append(transform[u.time])
        for total, j in zip(totals, kept, strict=True):
            total.append(u.x[j])
        columns.append(Unit(u.stratum, u.entry, u.time, u.event, tuple(u.x[j] for j in kept)))
    centre = ieee.div(ieee.fsum(marked), len(marked))
    means = [ieee.div(ieee.fsum(total), len(units)) for total in totals]
    centred = _shifted(columns, means, [1.0] * m, watch)
    beta = list(fitted.fit.coefficients)
    counting = any(u.entry != ORIGIN for u in units)
    model = _Rows(_collapsed(centred, watch), range(m), watch, recentre=counting)
    weights = [transform[when] - centre for when in model.event_times()]
    evaluated = model(beta, weights, chosen)
    if not _full_rank(evaluated.information):
        return None
    score = [0.0] * m + evaluated.score[m:]
    statistic = solved_quadratic(evaluated.information, score)
    return Test(statistic, len(chosen), chi_squared_p(statistic, len(chosen)))


def separated_hazards(units: Sequence[Unit], found: Separated, watch: Watch) -> Test | None:
    """The global test of proportional hazards of a design's finite part (D361): of its
    estimated columns, in the finite part's model (its classes the strata, its free columns
    fitted, the others' score taken as 0 as the terms' own are), the Kaplan–Meier transform
    pooled over every unit, as R's ``cox.zph`` pools it; where nothing is separated, the
    classes are the strata and this is D359's test of the estimated columns. None where nothing
    is estimated or the finite fit did not converge."""
    if found.fit is None or not found.fit.converged or not found.estimated:
        return None
    classified = [
        Unit(group, u.entry, u.time, u.event, u.x)
        for group, u in zip(found.classes, units, strict=True)
    ]
    return proportional_hazards(
        classified, Fitted(found.free, (), found.fit), watch, tested=found.estimated
    )


__all__ = [
    "NOCENTER",
    "RECENTRE",
    "Fitted",
    "Separated",
    "Unit",
    "columns_of",
    "fit",
    "proportional_hazards",
    "separated",
    "separated_hazards",
]

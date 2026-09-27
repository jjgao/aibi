"""The methods of the core's survival analyses (SPEC §5.8, §9.3, §9.5; D349, D350).

Each is a plain function of the units' endpoint rows (``Subject``: an entry time, a time and
whether it ended in an event), deterministic as §9.3 requires: nothing reads DuckDB, the order of
every operation is fixed, and every method is the one R's ``survival`` package (3.5-8) computes
with the calls ``tests/core/analyses/reference/survival.R`` names, to 1e-10 relative:

- ``kaplan_meier``: the Kaplan–Meier curve over left-truncated risk sets, a unit at risk at t
  when entry < t ≤ time (an entry at the origin is just before 0, so that an event at 0 is at
  risk there, as R's ``Surv(time, status)`` has it), a step at every time at which a unit's
  follow-up ends, with Greenwood's variance of −log S and the pointwise log-log interval, as
  ``survfit(conf.type = "log-log")``; bounds where the curve is 1 are 1, and where it is 0 there
  are none (``zero_denominator``, §9.5).
- ``median``: the curve's median and its Brookmeyer–Crowley interval, the medians of the curve's
  pointwise bounds, as ``quantile.survfit`` finds them (``findq``, ported line by line, its
  tolerance √ε).
- ``survival_at``: the curve and its bounds at a time, as ``summary.survfit(times = …)`` reads
  them; ``None`` after the last follow-up (``beyond_follow_up``).
- ``log_rank``: the Mantel–Haenszel log-rank test over left-truncated risk sets, summed over
  strata, as ``survdiff`` computes it without delayed entry and as the score test of
  ``coxph(ties = "exact")`` with it, its equal: the groups with expected events above zero, the
  statistic by the Cholesky factorisation of the variance, a pivot below the tolerance of
  ``coxph.control`` dropped, its degrees of freedom the rank.
- ``cox``: the Cox model of the groups versus the reference, Efron's ties, by Newton–Raphson from
  0 with step halving as ``agreg.fit`` iterates it (``agfit4``: at most 20 iterations, converged
  when the partial log-likelihood changes by at most 1e-9 relatively), Cholesky with
  ``toler.chol`` = ε^0.75, a term with no information left at 0 and flagged ``singular``, and a
  term flagged infinite by ``coxph.fit``'s rule (|(U·I⁻¹)ⱼ| > 1e-9 and > √1e-9·|βⱼ|).
- ``proportional_hazards``: the Grambsch–Therneau global test of a fit, as ``cox.zph(transform =
  "km")`` computes it (``zph2``): the score of the terms times g(t), g the Kaplan–Meier
  transform of time over the fit's units less its mean over their events, against the
  information of the model extended by them, at the fit's coefficients; none where that
  information is singular, where R's ``solve`` fails.
- ``bootstrap_medians`` and ``median_interval``: the medians of resamples of a cohort, each unit
  drawn with replacement from a seeded stream, and the percentile interval of their differences,
  a median not reached being +∞ (D350).

Every group, stratum and event time is visited in sorted order, and sums are taken in that
order: every platform computes the same bits. Every floating operation is C's (``ieee``): a
division by 0, ``exp`` out of range, ``log`` or ``sqrt`` out of their domains and a sum that
overflows give ±∞ or NaN where Python would raise, and an iteration of the Cox fit that meets
one is a failed one, which it halves, as ``agfit4`` does (D349). Each method takes the call's
deadline ``ends`` and looks at it between its passes over the rows and every ``LOOK_EVERY``
times round its loops (D350).
"""

import math
import random
import time
from bisect import bisect_left, bisect_right
from collections.abc import Sequence
from dataclasses import dataclass
from fractions import Fraction
from itertools import repeat

from aibi.core.analyses import ieee
from aibi.core.analyses.stats import upper_gamma, z
from aibi.core.engine.worker import CallerDeadline

ORIGIN = -math.inf
"""The entry of a unit that enters at the origin: just before time 0 (§5.8)."""

HALF = 0.5
"""The level of 1 − S at which a curve's median is read."""
TOLERANCE = ieee.sqrt(2.220446049250313e-16)
"""``quantile.survfit``'s tolerance, √ε: a curve within it of ½ equals ½ (§9.5)."""

EPS = 1e-9
"""``coxph.control``'s ``eps``: the relative change of the log-likelihood at convergence."""
TOLER_CHOL = 2.220446049250313e-16**0.75
"""``coxph.control``'s ``toler.chol``: a Cholesky pivot below it, relative to the largest
diagonal, is 0."""
TOLER_INF = ieee.sqrt(EPS)
"""``coxph.control``'s ``toler.inf``, by which a coefficient is flagged infinite."""
ITERATIONS = 20
"""``coxph.control``'s ``iter.max``."""

Subject = tuple[float, float, bool]
"""A unit's endpoint row (§5.8): its entry (``ORIGIN`` at the origin), its time and whether its
follow-up ended in an event."""


# --- The Kaplan–Meier curve -----------------------------------------------------------------------


@dataclass(frozen=True)
class Step:
    """The curve at a time at which some unit's follow-up ends: the units at risk, those whose
    follow-up ends there in an event or censored, the curve after it, Greenwood's sum (the
    variance of −log S; infinite once the curve is 0) and the pointwise bounds (1 and 1 where the
    curve is 1, ``None`` where it is 0)."""

    time: float
    at_risk: int
    events: int
    censored: int
    survival: float
    greenwood: float
    low: float | None
    high: float | None


def _counts(
    subjects: Sequence[Subject], ends: float | None = None
) -> tuple[list[float], list[float]]:
    """The subjects' entries and times, each sorted, for counting those at risk; the call's
    deadline ``ends`` looked at between the sorts."""
    entries = sorted(entry for entry, _, _ in subjects)
    _look(ends)
    return entries, sorted(time for _, time, _ in subjects)


def at_risk(entries: Sequence[float], times: Sequence[float], at: float) -> int:
    """The units at risk at ``at``, entry < at ≤ time, from their sorted entries and times."""
    return bisect_left(entries, at) - bisect_left(times, at)


def log_log(survival: float, greenwood: float, level: float) -> tuple[float | None, float | None]:
    """The pointwise log-log interval of a curve's value, as ``survfit_confint`` writes it: 1 and
    1 where the curve is 1, none where it is 0 (§9.5)."""
    if survival == 1:
        return 1.0, 1.0
    if survival == 0:
        return None, None
    spread = ieee.div(z(level) * ieee.sqrt(greenwood), ieee.log(survival))
    centre = ieee.log(-ieee.log(survival))
    low = ieee.exp(-ieee.exp(centre - spread))
    high = ieee.exp(-ieee.exp(centre + spread))
    if not (math.isfinite(low) and math.isfinite(high)):
        return None, None
    return low, high


def chi_squared_p(statistic: float, df: int) -> float:
    """The upper tail of a chi-squared variable with ``df`` degrees of freedom at
    ``statistic``, a statistic below 0 read as 0; ``nan`` for a statistic that is not finite,
    which only a variance near 0 gives and the caller reports as ``zero_variance`` (D348)."""
    if not math.isfinite(statistic):
        return math.nan
    return upper_gamma(df / 2, max(statistic, 0.0) / 2)


LOOK_EVERY = 1 << 14
"""Distinct times, or event times, a method passes between looks at the call's deadline."""


def _look(ends: float | None, index: int = 0) -> None:
    """Raise ``CallerDeadline`` at every ``LOOK_EVERY``-th ``index`` once ``time.monotonic()``
    has passed ``ends``."""
    if ends is not None and not index % LOOK_EVERY and time.monotonic() >= ends:
        raise CallerDeadline


def kaplan_meier(
    subjects: Sequence[Subject], level: float, ends: float | None = None
) -> list[Step]:
    """The Kaplan–Meier curve of a cohort's endpoint rows (module docstring), a step at each
    distinct time at which some unit's follow-up ends, in increasing order; the call's deadline
    ``ends`` looked at every ``LOOK_EVERY`` times and between the passes over the rows."""
    entries, times = _counts(subjects, ends)
    _look(ends)
    happened: dict[float, int] = {}
    for _, when, event in subjects:
        if event:
            happened[when] = happened.get(when, 0) + 1
    _look(ends)
    steps: list[Step] = []
    survival, greenwood = 1.0, 0.0
    start, index = 0, 0
    while start < len(times):
        _look(ends, index)
        when = times[start]
        end = bisect_right(times, when, start)
        events = happened.get(when, 0)
        censored = end - start - events
        risk = bisect_left(entries, when) - start
        start, index = end, index + 1
        if events:
            survival *= ieee.div(risk - events, risk)
            greenwood = (
                greenwood + ieee.div(events, risk * (risk - events)) if risk > events else math.inf
            )
        low, high = log_log(survival, greenwood, level)
        steps.append(Step(when, risk, events, censored, survival, greenwood, low, high))
    return steps


def _raw_bound(step: Step, high: bool) -> float | None:
    """A step's bound as ``survfit`` keeps it: none where the curve is 0 or 1."""
    if step.survival in (0.0, 1.0):
        return None
    return step.high if high else step.low


def _right_constant(
    knots: Sequence[tuple[float, int]], p: float, ends: float | None = None
) -> int | None:
    """``approx(Y, index, p, method = "constant", f = 1)`` over the knots (Y, index), ``NA``
    ones removed: the index of the knot of least Y at or above ``p``, none above the knots'
    range. (``approx`` gives none below it too, which ``findq`` never asks: its curves start at 0
    and it asks for ½.) The call's deadline ``ends`` is looked at after the sort and every
    ``LOOK_EVERY`` knots."""
    ordered = sorted(knots)
    for position, (value, index) in enumerate(ordered):
        _look(ends, position)
        if value >= p:
            return index
    return None


def findq(x: Sequence[float], y: Sequence[float | None], ends: float | None = None) -> float | None:
    """The time at which 1 − S reaches ½, ``quantile.survfit``'s ``findq`` line by line: ``x`` the
    times from the curve's start, ``y`` 1 − S at each (``None`` for ``NA``), repeated values
    dropped; a level within ``TOLERANCE`` of ½ over an interval gives its midpoint, and one that
    ends there the midpoint of where it began and the last time; ``None`` when it is not
    reached. The call's deadline ``ends`` is looked at between the passes over the curve and
    every ``LOOK_EVERY`` times within them."""
    present = [value for value in y if value is not None]
    if not present or max(present) < HALF:
        return None
    _look(ends)
    last = x[-1]
    seen: set[float | None] = set()
    xs: list[float] = []
    ys: list[float | None] = []
    for position, (time_at, value) in enumerate(zip(x, y, strict=True)):
        _look(ends, position)
        if value in seen:
            continue
        seen.add(value)
        xs.append(time_at)
        ys.append(value)
    known = [(value, index) for index, value in enumerate(ys) if value is not None]
    _look(ends)
    first = _right_constant([(value + TOLERANCE, index) for value, index in known], HALF, ends)
    _look(ends)
    second = _right_constant([(value - TOLERANCE, index) for value, index in known], HALF, ends)
    assert first is not None, "a curve that reaches ½ reaches it within the tolerance"
    end = ys[-1]
    if end is not None and abs(HALF - end) < TOLERANCE:
        return (xs[first] + last) / 2
    return None if second is None else (xs[first] + xs[second]) / 2


def median(
    steps: Sequence[Step], ends: float | None = None
) -> tuple[float | None, float | None, float | None]:
    """The median of a curve and its Brookmeyer–Crowley interval: the medians of its lower and
    upper pointwise bounds (module docstring); ``None`` for each not reached. The call's
    deadline ``ends`` is looked at between the passes over the curve and within each ``findq``."""
    x = [0.0, *(step.time for step in steps)]
    _look(ends)
    estimate = findq(x, [0.0, *(1 - step.survival for step in steps)], ends)
    _look(ends)
    lower = [_raw_bound(step, high=False) for step in steps]
    _look(ends)
    upper = [_raw_bound(step, high=True) for step in steps]
    _look(ends)
    low = findq(x, [0.0, *(None if b is None else 1 - b for b in lower)], ends)
    _look(ends)
    high = findq(x, [0.0, *(None if b is None else 1 - b for b in upper)], ends)
    return estimate, low, high


def survival_at(
    steps: Sequence[Step], at: float, times: Sequence[float] | None = None
) -> tuple[float, float | None, float | None] | None:
    """The curve and its bounds at ``at``: the last step at or before it, 1 before the first;
    ``None`` after the curve's last time (module docstring). ``times`` are the steps' times,
    which a caller reading many times gives once."""
    if not steps or at > steps[-1].time:
        return None
    index = bisect_right([step.time for step in steps] if times is None else times, at) - 1
    if index < 0:
        return 1.0, 1.0, 1.0
    step = steps[index]
    return step.survival, step.low, step.high


@dataclass(frozen=True)
class Interval:
    """What happened in a grid interval (g₋, g], and the units at risk at g."""

    at_risk: int
    events: int
    censored: int


def grid_counts(
    subjects: Sequence[Subject], grid: Sequence[float], ends: float | None = None
) -> list[Interval]:
    """At each grid time g, in increasing order, the units at risk at g (entry < g ≤ time) and
    the follow-ups that ended in (g₋, g], the first interval open below; the call's deadline
    ``ends`` looked at between the passes over the rows and at each grid time."""
    entries, times = _counts(subjects, ends)
    _look(ends)
    ended = sorted((when, event) for _, when, event in subjects)
    found: list[Interval] = []
    start = 0
    for point in grid:
        _look(ends)
        end = bisect_right(ended, (point, True))
        events = sum(event for _, event in ended[start:end])
        found.append(Interval(at_risk(entries, times, point), events, end - start - events))
        start = end
    return found


# --- Risk sets across groups ------------------------------------------------------------------


@dataclass(frozen=True)
class EventTime:
    """One event time of a stratum: per group, the units at risk and the events."""

    time: float
    at_risk: tuple[int, ...]
    events: tuple[int, ...]


def risk_table(
    groups: Sequence[Sequence[Subject]],
    strata: Sequence[Sequence[int]] | None = None,
    ends: float | None = None,
) -> list[list[EventTime]]:
    """Each stratum's event times in increasing order, strata in increasing order of their
    number, with each group's units at risk and events there; the call's deadline ``ends``
    looked at every ``LOOK_EVERY`` event times and between the passes over the rows."""
    by_stratum: dict[int, list[list[Subject]]] = {}
    for index, subjects in enumerate(groups):
        for position, subject in enumerate(subjects):
            stratum = 0 if strata is None else strata[index][position]
            found = by_stratum.setdefault(stratum, [[] for _ in groups])
            found[index].append(subject)
    table: list[list[EventTime]] = []
    for stratum in sorted(by_stratum):
        members = by_stratum[stratum]
        counts: list[tuple[list[float], list[float]]] = []
        for subjects in members:
            _look(ends)
            counts.append(_counts(subjects, ends))
        events: list[dict[float, int]] = []
        for subjects in members:
            _look(ends)
            ended: dict[float, int] = {}
            for _, when, event in subjects:
                if event:
                    ended[when] = ended.get(when, 0) + 1
            events.append(ended)
        _look(ends)
        times = sorted({when for ended in events for when in ended})
        rows: list[EventTime] = []
        for index, when in enumerate(times):
            _look(ends, index)
            rows.append(
                EventTime(
                    when,
                    tuple(at_risk(entries, finished, when) for entries, finished in counts),
                    tuple(ended.get(when, 0) for ended in events),
                )
            )
        table.append(rows)
    return table


# --- Linear algebra, as survival's C routines do it ---------------------------------------------


def cholesky(matrix: list[list[float]], toler: float = TOLER_CHOL) -> int:
    """``cholesky2``: the LDLᵀ factorisation of a symmetric matrix in place, in its lower
    triangle with D on the diagonal; a pivot below ``toler`` times the largest diagonal (or not
    finite) is set to 0. The rank, negative when the matrix is not non-negative definite."""
    n = len(matrix)
    largest = 0.0
    for i in range(n):
        largest = max(largest, matrix[i][i])
        for j in range(i + 1, n):
            matrix[j][i] = matrix[i][j]
    eps = toler if largest == 0 else largest * toler
    rank, nonnegative = 0, 1
    for i in range(n):
        pivot = matrix[i][i]
        if not math.isfinite(pivot) or pivot < eps:
            matrix[i][i] = 0.0
            if pivot < -8 * eps:
                nonnegative = -1
            continue
        rank += 1
        for j in range(i + 1, n):
            temp = ieee.div(matrix[j][i], pivot)
            matrix[j][i] = temp
            matrix[j][j] -= temp * temp * pivot
            for k in range(j + 1, n):
                matrix[k][j] -= temp * matrix[k][i]
    return rank * nonnegative


def solve(factored: Sequence[Sequence[float]], y: Sequence[float]) -> list[float]:
    """``chsolve2``: the solution of A·b = y from A's ``cholesky``, 0 where a pivot is 0."""
    n = len(y)
    found = list(y)
    for i in range(n):
        temp = found[i]
        for j in range(i):
            temp -= found[j] * factored[i][j]
        found[i] = temp
    for i in range(n - 1, -1, -1):
        if factored[i][i] == 0:
            found[i] = 0.0
            continue
        temp = ieee.div(found[i], factored[i][i])
        for j in range(i + 1, n):
            temp -= found[j] * factored[j][i]
        found[i] = temp
    return found


def inverse(factored: list[list[float]]) -> list[list[float]]:
    """``chinv2``: the inverse of A from its ``cholesky``, a row and column of 0 where a pivot
    is 0, as a full symmetric matrix."""
    matrix = factored
    n = len(matrix)
    for i in range(n):
        if matrix[i][i] > 0:
            matrix[i][i] = ieee.div(1.0, matrix[i][i])
            for j in range(i + 1, n):
                matrix[j][i] = -matrix[j][i]
                for k in range(i):
                    matrix[j][k] += matrix[j][i] * matrix[i][k]
    for i in range(n):
        if matrix[i][i] == 0:
            for j in range(i):
                matrix[j][i] = 0.0
            for j in range(i, n):
                matrix[i][j] = 0.0
            continue
        for j in range(i + 1, n):
            temp = matrix[j][i] * matrix[j][j]
            matrix[i][j] = temp
            for k in range(i, j):
                matrix[i][k] += temp * matrix[j][k]
    return [[matrix[min(i, j)][max(i, j)] for j in range(n)] for i in range(n)]


def quadratic(matrix: Sequence[Sequence[float]], u: Sequence[float]) -> tuple[float, int]:
    """uᵀA⁻u over a symmetric non-negative definite A, by its ``cholesky`` (a pivot below the
    tolerance dropped), and A's rank."""
    factored = [list(row) for row in matrix]
    rank = cholesky(factored)
    solved = solve(factored, u)
    return ieee.fsum(a * b for a, b in zip(u, solved, strict=True)), max(rank, 0)


# --- The log-rank test ------------------------------------------------------------------------


@dataclass(frozen=True)
class LogRank:
    """A log-rank test: the groups it used (those with expected events), its statistic, degrees
    of freedom and p-value; no test (``statistic`` ``None``) when its variance is 0."""

    used: tuple[int, ...]
    statistic: float | None
    df: int
    p: float | None


def log_rank(
    table: Sequence[Sequence[EventTime]], groups: int, ends: float | None = None
) -> LogRank:
    """The Mantel–Haenszel log-rank test over a ``risk_table`` of ``groups`` groups (module
    docstring): observed less expected events and their hypergeometric covariance, summed over
    event times and strata, as ``survdiff2`` sums them; the call's deadline ``ends`` looked at
    every ``LOOK_EVERY`` event times."""
    observed = [0.0] * groups
    expected = [0.0] * groups
    variance = [[0.0] * groups for _ in range(groups)]
    for stratum in table:
        for index, event in enumerate(stratum):
            _look(ends, index)
            happened = sum(event.events)
            risk = sum(event.at_risk)
            for j in range(groups):
                observed[j] += event.events[j]
                expected[j] += ieee.div(happened * event.at_risk[j], risk)
            if risk == 1:
                continue
            for j in range(groups):
                temp = ieee.div(happened * event.at_risk[j] * (risk - happened), risk * (risk - 1))
                variance[j][j] += temp
                for k in range(groups):
                    variance[j][k] -= ieee.div(temp * event.at_risk[k], risk)
    used = tuple(j for j in range(groups) if expected[j] > 0)
    if len(used) < 2:
        return LogRank(used, None, 0, None)
    kept = used[1:]
    difference = [observed[j] - expected[j] for j in kept]
    statistic, rank = quadratic([[variance[j][k] for k in kept] for j in kept], difference)
    if rank == 0:
        return LogRank(used, None, 0, None)
    statistic = max(statistic, 0.0) if math.isfinite(statistic) else statistic
    return LogRank(used, statistic, rank, chi_squared_p(statistic, rank))


# --- The Cox model -----------------------------------------------------------------------------


@dataclass(frozen=True)
class CoxFit:
    """A Cox fit of the groups versus the reference: per term (each other group, in order) its
    coefficient, variance, whether it was flagged infinite or had no information (``singular``),
    and whether the fit converged."""

    coefficients: tuple[float, ...]
    variance: tuple[tuple[float, ...], ...]
    infinite: tuple[bool, ...]
    singular: tuple[bool, ...]
    converged: bool
    loglik: float
    iterations: int


@dataclass(frozen=True)
class _Evaluated:
    loglik: float
    score: list[float]
    information: list[list[float]]


def _width(table: Sequence[Sequence[EventTime]]) -> int:
    """The groups of a ``risk_table``: the length of any event time's counts, whichever stratum
    holds it (a stratum may hold none)."""
    return next((len(event.at_risk) for stratum in table for event in stratum), 0)


def _terms_of(reference: int, groups: int) -> list[int]:
    return [group for group in range(groups) if group != reference]


def _evaluate(
    table: Sequence[Sequence[EventTime]],
    terms: Sequence[int],
    beta: Sequence[float],
    ends: float | None = None,
) -> _Evaluated:
    """The Efron partial log-likelihood, its score and its information at ``beta`` over a
    ``risk_table``, the covariates each group's indicator (``terms``, the reference's none), as
    ``agfit4`` accumulates them per event time: its non-events' weight, then each of its events
    added a share at a time."""
    m = len(terms)
    groups = _width(table)
    weight = [1.0] * groups
    for index, group in enumerate(terms):
        weight[group] = ieee.exp(beta[index])
    loglik = 0.0
    score = [0.0] * m
    information = [[0.0] * m for _ in range(m)]
    for stratum in table:
        for index, event in enumerate(stratum):
            _look(ends, index)
            happened = sum(event.events)
            rest = ieee.fsum(
                (event.at_risk[j] - event.events[j]) * weight[j] for j in range(groups)
            )
            happening = ieee.fsum(event.events[j] * weight[j] for j in range(groups))
            for index, group in enumerate(terms):
                loglik += event.events[group] * beta[index]
                score[index] += event.events[group]
            for k in range(happened):
                share = ieee.div(k + 1, happened)
                denom = rest + share * happening
                loglik -= ieee.log(denom)
                means = [
                    ieee.div(
                        (event.at_risk[g] - event.events[g]) * weight[g]
                        + share * event.events[g] * weight[g],
                        denom,
                    )
                    for g in terms
                ]
                for i in range(m):
                    score[i] -= means[i]
                    information[i][i] += means[i] - means[i] * means[i]
                    for j in range(i):
                        information[i][j] -= means[i] * means[j]
                        information[j][i] = information[i][j]
    return _Evaluated(loglik, score, information)


def cox(table: Sequence[Sequence[EventTime]], reference: int, ends: float | None = None) -> CoxFit:
    """The Cox model of a ``risk_table``'s groups versus ``reference`` (module docstring), by
    ``agfit4``'s iteration: from 0 a Newton step, then at each iteration the log-likelihood
    compared with the best so far, converged when within ``EPS`` relatively (and not halving),
    else a Newton step from an improvement or the step halved back toward the last good
    coefficients from a worse one; at most ``ITERATIONS`` iterations, the call's deadline
    ``ends`` looked at every ``LOOK_EVERY`` event times of each."""
    groups = _width(table)
    terms = _terms_of(reference, groups)
    m = len(terms)
    beta = [0.0] * m
    first = _evaluate(table, terms, beta, ends)
    factored = [list(row) for row in first.information]
    rank = cholesky(factored)
    best = first.loglik
    step = solve(factored, first.score)
    old = list(beta)
    beta = [b + s for b, s in zip(beta, step, strict=True)]
    halving = 0
    converged = False
    iterations = 0
    current = first
    for iteration in range(1, ITERATIONS + 1):
        iterations = iteration
        current = _evaluate(table, terms, beta, ends)
        factored = [list(row) for row in current.information]
        fail = sum(not math.isfinite(current.information[i][i]) for i in range(m))
        again = cholesky(factored)
        fail += (not math.isfinite(current.loglik)) + abs(rank - again)
        if fail == 0 and halving == 0 and abs(1 - ieee.div(best, current.loglik)) <= EPS:
            converged = True
            break
        if iteration == ITERATIONS:
            if fail > 0 or not ieee.div(current.loglik - best, abs(best)) >= -EPS:
                beta = old
                current = _evaluate(table, terms, beta, ends)
                factored = [list(row) for row in current.information]
                cholesky(factored)
            break
        if fail > 0 or current.loglik < best:
            halving += 1
            beta = [ieee.div(o * halving + b, halving + 1) for o, b in zip(old, beta, strict=True)]
        else:
            halving = 0
            best = current.loglik
            step = solve(factored, current.score)
            old = list(beta)
            beta = [b + s for b, s in zip(beta, step, strict=True)]
    variance = inverse(factored)
    singular = tuple(variance[i][i] == 0 for i in range(m))
    infinite: list[bool] = []
    for j in range(m):
        found = abs(ieee.fsum(current.score[i] * variance[i][j] for i in range(m)))
        infinite.append(
            converged
            and not singular[j]
            and (not math.isfinite(found) or (found > EPS and found > TOLER_INF * abs(beta[j])))
        )
    return CoxFit(
        tuple(beta),
        tuple(tuple(row) for row in variance),
        tuple(infinite),
        singular,
        converged,
        current.loglik,
        iterations,
    )


# --- The test of proportional hazards ------------------------------------------------------------


@dataclass(frozen=True)
class Test:
    statistic: float
    df: int
    p: float


def proportional_hazards(
    groups: Sequence[Sequence[Subject]],
    table: Sequence[Sequence[EventTime]],
    fit: CoxFit,
    reference: int,
    ends: float | None = None,
) -> Test | None:
    """The Grambsch–Therneau global test of ``fit`` (module docstring), over the groups it was
    fitted to, its singular terms left out, as ``cox.zph`` drops a coefficient that is ``NA``;
    ``None`` when no term is left, or when the extended information is singular (a ``cholesky``
    pivot below ``TOLER_CHOL``), where R's ``solve`` fails or gives what rounding makes. The
    call's deadline ``ends`` is looked at every ``LOOK_EVERY`` event times."""
    terms = [
        group
        for index, group in enumerate(_terms_of(reference, len(groups)))
        if not fit.singular[index]
    ]
    beta = [c for c, singular in zip(fit.coefficients, fit.singular, strict=True) if not singular]
    m = len(terms)
    if m == 0:
        return None
    pooled = [subject for subjects in groups for subject in subjects]
    steps = kaplan_meier(pooled, 0.95, ends)
    _look(ends)
    step_times = [step.time for step in steps]
    before = [1.0, *(step.survival for step in steps)]

    def transformed(at: float) -> float:
        return 1 - before[bisect_left(step_times, at)]

    events = [when for _, when, event in pooled if event]
    centre = ieee.div(ieee.fsum(transformed(when) for when in events), len(events))
    _look(ends)
    count = len(groups)
    weight = [1.0] * count
    for index, group in enumerate(terms):
        weight[group] = ieee.exp(beta[index])
    size = 2 * m
    score = [0.0] * size
    information = [[0.0] * size for _ in range(size)]
    for stratum in table:
        for index, event in enumerate(stratum):
            _look(ends, index)
            g = transformed(event.time) - centre
            happened = sum(event.events)
            rest = ieee.fsum((event.at_risk[j] - event.events[j]) * weight[j] for j in range(count))
            happening = ieee.fsum(event.events[j] * weight[j] for j in range(count))
            for index, group in enumerate(terms):
                score[m + index] += event.events[group] * g
            for k in range(happened):
                share = ieee.div(k + 1, happened)
                denom = rest + share * happening
                means = [
                    ieee.div(
                        (event.at_risk[t] - event.events[t]) * weight[t]
                        + share * event.events[t] * weight[t],
                        denom,
                    )
                    for t in terms
                ]
                for i in range(m):
                    score[m + i] -= means[i] * g
                    for j in range(m):
                        cell = (means[i] if i == j else 0.0) - means[i] * means[j]
                        information[i][j] += cell
                        information[i][m + j] += cell * g
                        information[m + i][j] += cell * g
                        information[m + i][m + j] += cell * g * g
    factored = [list(row) for row in information]
    if cholesky(factored) < size:
        return None
    statistic = _solved_quadratic(information, score)
    return Test(statistic, m, chi_squared_p(statistic, m))


def _solved_quadratic(matrix: Sequence[Sequence[float]], u: Sequence[float]) -> float:
    """uᵀA⁻¹u by Gaussian elimination with partial pivoting, as R's ``solve`` (LAPACK's
    ``dgesv``) finds A⁻¹u."""
    n = len(u)
    rows = [[*row, value] for row, value in zip(matrix, u, strict=True)]
    for column in range(n):
        pivot = max(range(column, n), key=lambda row: abs(rows[row][column]))
        rows[column], rows[pivot] = rows[pivot], rows[column]
        head = rows[column][column]
        for row in range(column + 1, n):
            factor = ieee.div(rows[row][column], head)
            if factor:
                for at in range(column, n + 1):
                    rows[row][at] -= factor * rows[column][at]
    solved = [0.0] * n
    for row in range(n - 1, -1, -1):
        total = rows[row][n] - ieee.fsum(rows[row][at] * solved[at] for at in range(row + 1, n))
        solved[row] = ieee.div(total, rows[row][row])
    return ieee.fsum(a * b for a, b in zip(u, solved, strict=True))


# --- The bootstrap of medians (D350) -------------------------------------------------------

REPLICATES = 2000
"""The bootstrap's replicates (§9.5)."""


class _Resampled:
    """A cohort's endpoint rows arranged for resampling: sorted by time, events before
    censorings at a time; each distinct time's first row; and, with delayed entry, the order of
    their entries. A resample draws each of its n rows as the row at ⌊n·U⌋ of the sorted rows,
    for a uniform U of the stream; without delayed entry only each time's events and censorings
    matter, so the draws are counted by time, and with it by row. The call's deadline ``ends``
    is looked at between the passes over the rows."""

    def __init__(self, subjects: Sequence[Subject], ends: float | None = None) -> None:
        ordered = sorted(subjects, key=lambda subject: (subject[1], not subject[2], subject[0]))
        _look(ends)
        self.n = len(ordered)
        times = [when for _, when, _ in ordered]
        self.events = [event for _, _, event in ordered]
        starts = [0]
        for index in range(1, self.n):
            if times[index] != times[index - 1]:
                starts.append(index)
        self.starts = starts
        self.ends = [*starts[1:], self.n]
        self.times = [times[start] for start in starts]
        """Each distinct time, in increasing order."""
        self.delayed = any(entry != ORIGIN for entry, _, _ in ordered)
        _look(ends)
        group = [
            index
            for index, (start, end) in enumerate(zip(starts, self.ends, strict=True))
            for _ in range(start, end)
        ]
        self.code = [2 * group[row] + (0 if self.events[row] else 1) for row in range(self.n)]
        """Each row's time's index, twice, and 1 more for a censoring."""
        _look(ends)
        self.by_entry = sorted(range(self.n), key=lambda index: ordered[index][0])
        entries = [ordered[index][0] for index in self.by_entry]
        self.entered = [bisect_left(entries, when) for when in self.times]
        """How many rows enter before each distinct time."""

    def replicate(self, stream: random.Random) -> float:
        """The median of one resample drawn from ``stream``, as ``median`` finds a curve's; +∞
        when it is not reached."""
        n = self.n
        uniform = stream.random
        drawn = [uniform() for _ in repeat(None, n)]
        if not self.delayed:
            by_time = [0] * (2 * len(self.times))
            code = self.code
            for draw in drawn:
                by_time[code[int(draw * n)]] += 1
            return self._median(by_time, None)
        by_row = [0] * n
        for draw in drawn:
            by_row[int(draw * n)] += 1
        return self._median(None, by_row)

    def _median(self, by_time: Sequence[int] | None, by_row: Sequence[int] | None) -> float:
        """The resample's median, found as ``findq`` finds it on a curve that only falls: the
        first time at which 1 − S is within ``TOLERANCE`` of ½ or above, and the first at which
        it is above by more, their midpoint, or where the curve ends within the tolerance of ½,
        the midpoint of the first and the last time; each time a follow-up ends in the resample
        is one of the curve's."""
        n = self.n
        survival = 1.0
        ended = 0
        entered = 0
        pointer = 0
        first: float | None = None
        last = 0.0
        level = 0.0
        for group, when in enumerate(self.times):
            if by_time is not None:
                events, censored = by_time[2 * group], by_time[2 * group + 1]
                risk = n - ended
            else:
                assert by_row is not None
                events = censored = 0
                for row in range(self.starts[group], self.ends[group]):
                    if self.events[row]:
                        events += by_row[row]
                    else:
                        censored += by_row[row]
                limit = self.entered[group]
                while pointer < limit:
                    entered += by_row[self.by_entry[pointer]]
                    pointer += 1
                risk = entered - ended
            ended += events + censored
            if not events and not censored:
                continue
            if events:
                survival *= ieee.div(risk - events, risk)
            last = when
            level = 1 - survival
            if first is None and level + TOLERANCE >= HALF:
                first = when
            if level - TOLERANCE >= HALF:
                assert first is not None
                return (first + when) / 2
        if abs(HALF - level) < TOLERANCE:
            assert first is not None
            return (first + last) / 2
        return math.inf


def bootstrap_medians(
    subjects: Sequence[Subject],
    seed: int,
    replicates: int | None = None,
    ends: float | None = None,
) -> list[float]:
    """The medians of ``replicates`` resamples (``REPLICATES`` by default) of a cohort of at
    least one unit, each unit drawn with replacement (``_Resampled``) from a stream seeded with
    ``seed``, a median not reached +∞ (D350). Raises ``CallerDeadline`` once
    ``time.monotonic()`` passes ``ends``, looked at between the passes that arrange the rows and
    before each replicate."""
    if not subjects:
        raise ValueError("a bootstrap resamples at least one unit")
    arranged = _Resampled(subjects, ends)
    stream = random.Random(seed)
    found: list[float] = []
    for _ in range(REPLICATES if replicates is None else replicates):
        _look(ends)
        found.append(arranged.replicate(stream))
    return found


def median_interval(
    mine: Sequence[float], theirs: Sequence[float], level: float
) -> tuple[float | None, float | None]:
    """The percentile interval of the replicates' differences in medians (§9.5): the
    ⌈B·alpha/2⌉-th and ⌈B·(1 − alpha/2)⌉-th order statistics, alpha = 1 − ``level`` read as
    the decimal JSON writes it (D338), −∞ below every finite difference and +∞ above, and a
    difference of two medians not reached −∞ for the lower bound and +∞ for the upper; an
    infinite bound is ``None`` (``not_reached``)."""
    count = len(mine)
    if count == 0 or count != len(theirs) or not 0 < level < 1:
        raise ValueError("replicates in pairs, at a level between 0 and 1")
    alpha = 1 - Fraction(repr(level))
    low_rank = max(math.ceil(count * alpha / 2), 1)
    high_rank = min(math.ceil(count * (1 - alpha / 2)), count)
    lows: list[float] = []
    highs: list[float] = []
    for a, b in zip(mine, theirs, strict=True):
        if math.isinf(a) and math.isinf(b):
            lows.append(-math.inf)
            highs.append(math.inf)
        else:
            difference = a - b
            lows.append(difference)
            highs.append(difference)
    low = sorted(lows)[low_rank - 1]
    high = sorted(highs)[high_rank - 1]
    return (low if math.isfinite(low) else None), (high if math.isfinite(high) else None)


__all__ = [
    "ITERATIONS",
    "LOOK_EVERY",
    "ORIGIN",
    "REPLICATES",
    "TOLERANCE",
    "CoxFit",
    "EventTime",
    "Interval",
    "LogRank",
    "Step",
    "Subject",
    "Test",
    "at_risk",
    "bootstrap_medians",
    "chi_squared_p",
    "cholesky",
    "cox",
    "findq",
    "grid_counts",
    "inverse",
    "kaplan_meier",
    "log_log",
    "log_rank",
    "median",
    "median_interval",
    "proportional_hazards",
    "quadratic",
    "risk_table",
    "solve",
    "survival_at",
]

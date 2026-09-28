"""The unadjusted Cox fit of cohorts versus a reference, and its test of proportional hazards
(SPEC §9.5; D355–D357).

Each cohort is a group, and its covariate the indicator of the group, so every quantity is a
function of the risk table's counts per group (``timetoevent.risk_table``): a Newton step costs
the event times times the groups and the terms squared, and each event the terms squared (a share
of its time's ties), not a pass over the units.

- ``standing``: each group's place in the fit, decided exactly from the risk table (D356).
  Along a direction d of the coefficients (the reference's held at 0), an event's term of the
  partial log-likelihood never falls, with Breslow's, exact or Efron's ties, if and only if the
  event's group attains the largest d among the groups at risk at its time. So the directions
  along which the likelihood never falls are those with d_e ≥ d_h for every event of group e and
  every group h at risk at its time: a graph with an edge h → e for each. A group in the
  reference's strongly connected component is ``estimated``; one with a path to the reference,
  and none back, can fall without bound (``lower``: its hazard ratio tends to 0); one with a
  path from the reference, and none back, can rise without bound (``higher``); one with neither
  is not identified against the reference (``apart``). No rounding decides any of these.
- ``newton``: R's iteration for the Cox model of the estimated groups versus the reference,
  Efron's ties, ``coxfit6``'s without an entry column and ``agfit4``'s with one (survival
  3.5-8), from 0 or a given start as ``coxph``'s ``init``: the same sums in the same order,
  event times from the last to the first, each tied time's events added a share at a time
  (``denom += denom2 / d``), the covariates 0 and 1, neither centred nor scaled (as ``coxph``
  leaves such columns, ``nocenter``), Newton–Raphson with step halving, at most ``ITERATIONS``
  iterations, converged at a relative change of the log-likelihood of ``EPS``. Two of R's steps
  are not replicated, neither in a value shown: ``agfit4``'s recentring of the linear predictor,
  and the error it raises where that would overflow, so that where R stops here the iteration
  goes on; and ``coxfit6``'s inverse of an unfactored information when it runs out.
- ``fit``: the maximum of that model. No other group is at risk at an estimated group's event,
  so it is the limit of the fit of every group as the others' coefficients leave for ±∞ (D179's
  argument), and it exists; but Newton's steps from 0 need not reach it: one can land where the
  information vanishes to rounding, where ``cholesky`` drops a pivot and the iteration stops
  with the score far from 0, or the iteration can run out. So ``newton`` from 0 is the fit only
  where it ends ``_at_maximum``: the information of full rank and the gain that a Newton step
  predicts, half of uᵀI⁻¹u, at most ``EPS`` of max(1, |log-likelihood|). Else ``_ascent`` climbs to
  the maximum by capped, halved steps, and ``newton`` from there is the fit; where neither
  reaches it (the ascent finds no such point, or ``newton`` from its point does not end at one),
  the fit has not converged (D356).
- ``inverse``: ``chinv2``, the inverse of the information from its ``cholesky``.
- ``proportional_hazards``: the Grambsch–Therneau global test of a fit, ``cox.zph(transform =
  "km")`` (``zph1`` without an entry column, ``zph2`` with one): g the Kaplan–Meier transform of
  time over the fit's units less its mean over their events, the covariates centred over the
  fit's units, the score of the terms times g against the information of the model extended by
  them, at the fit's coefficients, the terms' own score taken as 0; none where that information
  is singular, where R's ``solve`` fails.

Every floating operation is C's (``ieee``), and every loop spends its work through
``timetoevent.Watch`` (D350): a tied time's shares cost the terms squared each.
"""

import dataclasses
import math
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from aibi.core.analyses import ieee
from aibi.core.analyses.timetoevent import EventTime, Watch, chi_squared_p, cholesky, solve

EPS = 1e-9
"""``coxph.control``'s ``eps``: the relative change of the log-likelihood at convergence."""
ITERATIONS = 20
"""``coxph.control``'s ``iter.max``."""
STEP = 1.0
"""The ascent's largest step in any coefficient (``fit``)."""
ARMIJO = 1e-4
"""The share of the rise its slope predicts that an ascent step must give (``fit``)."""
ASCENT = 200
"""The ascent's most steps (``fit``)."""
HALVINGS = 60
"""The most halvings of one ascent step (``fit``)."""


class Standing(StrEnum):
    """A group's place in the fit (module docstring)."""

    ESTIMATED = "estimated"
    LOWER = "lower"
    HIGHER = "higher"
    APART = "apart"


def standing(
    table: Sequence[EventTime], groups: int, reference: int, watch: Watch
) -> list[Standing]:
    """Each of ``groups`` groups' ``Standing`` against ``reference`` in one stratum of a
    ``risk_table`` (module docstring): the reachability of the graph with an edge h → e wherever
    group h is at risk at an event of group e, the reference's own ``ESTIMATED``. Each event time
    spends the groups squared."""
    reach = [[i == j for j in range(groups)] for i in range(groups)]
    watch.look()
    for event in table:
        watch.spend(groups * groups)
        for ending in range(groups):
            if not event.events[ending]:
                continue
            for present in range(groups):
                if event.at_risk[present]:
                    reach[present][ending] = True
    for via in range(groups):
        for start in range(groups):
            if reach[start][via]:
                for end in range(groups):
                    if reach[via][end]:
                        reach[start][end] = True
    found: list[Standing] = []
    for group in range(groups):
        to, back = reach[group][reference], reach[reference][group]
        if to and back:
            found.append(Standing.ESTIMATED)
        elif to:
            found.append(Standing.LOWER)
        elif back:
            found.append(Standing.HIGHER)
        else:
            found.append(Standing.APART)
    return found


@dataclass(frozen=True)
class CoxFit:
    """A Cox fit of groups versus the reference: per term (each fitted group but the reference,
    in order) its coefficient and the inverse of the information at it; whether it reached the
    maximum (``fit``); its log-likelihood where R's iteration started and where it ended, and
    that iteration's count; and whether it started from the ascent's point instead of 0."""

    coefficients: tuple[float, ...]
    variance: tuple[tuple[float, ...], ...]
    converged: bool
    loglik: tuple[float, float]
    iterations: int
    ascended: bool = False


@dataclass(frozen=True)
class _Evaluated:
    loglik: float
    score: list[float]
    information: list[list[float]]


def _evaluate(
    table: Sequence[EventTime],
    fitted: Sequence[int],
    covariates: Sequence[Sequence[float]],
    eta: Sequence[float],
    watch: Watch,
    weights: Sequence[float] | None = None,
) -> _Evaluated:
    """The Efron partial log-likelihood, its score and its information (module docstring) of the
    groups ``fitted``, each with its covariates and linear predictor, ``covariates[f]`` and
    ``eta[f]``; with ``weights`` (a time weight per event time, in the table's order), the score
    and information of the model extended by the covariates times that weight, as ``zph1`` and
    ``zph2`` accumulate them. Each event time spends the fitted groups times the terms squared,
    and each of its shares the terms squared."""
    m = len(covariates[0]) if covariates else 0
    cells = max(m * m, 1)
    risk = [ieee.exp(value) for value in eta]
    size = 2 * m if weights is not None else m
    loglik = 0.0
    score = [0.0] * size
    information = [[0.0] * size for _ in range(size)]
    for at in range(len(table) - 1, -1, -1):
        event = table[at]
        watch.spend(len(fitted) * cells)
        timewt = 1.0 if weights is None else weights[at]
        denom, denom2 = 0.0, 0.0
        a, a2 = [0.0] * m, [0.0] * m
        cmat = [[0.0] * m for _ in range(m)]
        cmat2 = [[0.0] * m for _ in range(m)]
        ties = 0
        for f, group in enumerate(fitted):
            ending = event.events[group]
            staying = event.at_risk[group] - ending
            x = covariates[f]
            if staying:
                weight = staying * risk[f]
                denom += weight
                for i in range(m):
                    a[i] += weight * x[i]
                    for j in range(i + 1):
                        cmat[i][j] += weight * x[i] * x[j]
            if ending:
                ties += ending
                weight = ending * risk[f]
                denom2 += weight
                loglik += ending * eta[f]
                for i in range(m):
                    score[i] += ending * x[i]
                    if weights is not None:
                        score[i + m] += timewt * ending * x[i]
                    a2[i] += weight * x[i]
                    for j in range(i + 1):
                        cmat2[i][j] += weight * x[i] * x[j]
        for _ in range(ties):
            watch.spend(cells)
            denom += ieee.div(denom2, ties)
            loglik -= ieee.log(denom)
            for i in range(m):
                a[i] += ieee.div(a2[i], ties)
                mean = ieee.div(a[i], denom)
                score[i] -= mean
                if weights is not None:
                    score[i + m] -= timewt * mean
                for j in range(i + 1):
                    cmat[i][j] += ieee.div(cmat2[i][j], ties)
                    cell = ieee.div(cmat[i][j] - mean * a[j], denom)
                    information[j][i] += cell
                    if weights is not None:
                        information[j][i + m] += timewt * cell
                        information[j + m][i + m] += timewt * timewt * cell
    for i in range(m):
        for j in range(i):
            information[i][j] = information[j][i]
            if weights is not None:
                information[i][j + m] = information[j][i + m]
                information[i + m][j + m] = information[j + m][i + m]
    if weights is not None:
        for i in range(m):
            for j in range(m):
                information[i + m][j] = information[j][i + m]
    return _Evaluated(loglik, score, information)


def _finite(evaluated: _Evaluated) -> bool:
    """Whether a log-likelihood, its score and its information are all finite."""
    return (
        math.isfinite(evaluated.loglik)
        and all(math.isfinite(value) for value in evaluated.score)
        and all(math.isfinite(value) for row in evaluated.information for value in row)
    )


def _factored(evaluated: _Evaluated) -> tuple[list[list[float]], int]:
    """A copy of the information, factored by ``cholesky``, and its rank."""
    matrix = [list(row) for row in evaluated.information]
    return matrix, cholesky(matrix)


def _changed(best: float, new: float) -> float:
    """``fabs(1 - best / new)``: the relative change of the log-likelihood."""
    return abs(1 - ieee.div(best, new))


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


class _Model:
    """The fitted groups' covariates, each term's indicator, and their evaluation at a β."""

    def __init__(
        self, table: Sequence[EventTime], fitted: Sequence[int], reference: int, watch: Watch
    ) -> None:
        self.table = table
        self.fitted = fitted
        self.watch = watch
        terms = [group for group in fitted if group != reference]
        self.covariates = [[1.0 if group == term else 0.0 for term in terms] for group in fitted]

    def __call__(self, beta: Sequence[float]) -> _Evaluated:
        self.watch.look()
        eta = [ieee.fsum(b * x for b, x in zip(beta, row, strict=True)) for row in self.covariates]
        return _evaluate(self.table, self.fitted, self.covariates, eta, self.watch)


def _decrement(evaluated: _Evaluated, factored: Sequence[Sequence[float]]) -> float:
    """uᵀI⁻¹u, twice the gain a Newton step predicts, from the information's ``cholesky``."""
    step = solve(factored, evaluated.score)
    return ieee.fsum(u * s for u, s in zip(evaluated.score, step, strict=True))


def _at_maximum(evaluated: _Evaluated, factored: Sequence[Sequence[float]], rank: int) -> bool:
    """Whether a point is the fit's maximum to R's tolerance: its information of full rank and
    the gain a Newton step predicts at most ``EPS`` of max(1, |log-likelihood|) (``fit``)."""
    if rank != len(evaluated.score) or not _finite(evaluated):
        return False
    return _decrement(evaluated, factored) <= 2 * EPS * max(1.0, abs(evaluated.loglik))


def _result(
    beta: Sequence[float],
    evaluated: _Evaluated,
    converged: bool,
    start: float,
    iterations: int,
) -> CoxFit:
    """A fit ended at ``beta``, which converged only where it is also ``_at_maximum``."""
    factored, rank = _factored(evaluated)
    reached = converged and _at_maximum(evaluated, factored, rank)
    return CoxFit(
        tuple(beta),
        tuple(tuple(row) for row in inverse(factored)),
        reached,
        (start, evaluated.loglik),
        iterations,
    )


def fit(
    table: Sequence[EventTime],
    fitted: Sequence[int],
    reference: int,
    watch: Watch,
    *,
    counting: bool,
) -> CoxFit:
    """The maximum of the Cox model of the groups ``fitted`` (the reference among them, all
    ``ESTIMATED``) in one stratum of a ``risk_table`` (module docstring): ``newton`` from 0 where
    that ends at it, else ``newton`` from the point ``_ascent`` climbs to (``ascended``); where
    the ascent finds none, ``newton``'s fit from 0, and where ``newton`` from its point ends
    short of the maximum, that fit, neither converged."""
    first = newton(table, fitted, reference, watch, counting=counting)
    if first.converged:
        return first
    model = _Model(table, fitted, reference, watch)
    found = _ascent(model, [0.0] * (len(fitted) - 1))
    if found is None:
        return first
    again = newton(table, fitted, reference, watch, counting=counting, start=found)
    return dataclasses.replace(again, ascended=True)


def newton(
    table: Sequence[EventTime],
    fitted: Sequence[int],
    reference: int,
    watch: Watch,
    *,
    counting: bool,
    start: Sequence[float] | None = None,
) -> CoxFit:
    """R's iteration of ``fit`` from ``start`` (0 by default), as ``coxph`` runs it with that
    ``init``: ``agfit4``'s where the endpoint has an entry column (``counting``), ``coxfit6``'s
    where it has none; converged only where it ends at the maximum (``_at_maximum``)."""
    if reference not in fitted or len(fitted) < 2:
        raise ValueError("a fit needs the reference and another group")
    model = _Model(table, fitted, reference, watch)
    first = [0.0] * (len(fitted) - 1) if start is None else list(start)
    return _counting(model, first) if counting else _right(model, first)


def _ascent(model: _Model, start: Sequence[float]) -> list[float] | None:
    """The maximum (``fit``), or none: from ``start``, each step Newton's where the information
    has full rank (its factors then positive, so the log-likelihood rises along it) and the
    score's where it has not, at most ``STEP`` in any coefficient, halved until the
    log-likelihood rises by ``ARMIJO`` of what the step's slope predicts; the first point
    ``_at_maximum``, within ``ASCENT`` steps and ``HALVINGS`` halvings of each, and none where
    the score is 0 short of it."""
    beta = list(start)
    evaluated = model(beta)
    for _ in range(ASCENT):
        if not _finite(evaluated):
            return None
        factored, rank = _factored(evaluated)
        if _at_maximum(evaluated, factored, rank):
            return beta
        newton = rank == len(beta)
        step = solve(factored, evaluated.score) if newton else list(evaluated.score)
        slope = ieee.fsum(u * s for u, s in zip(evaluated.score, step, strict=True))
        if not slope > 0:
            return None
        size = max(abs(value) for value in step)
        scale = 1.0 if size <= STEP else ieee.div(STEP, size)
        taken = scale
        for _ in range(HALVINGS):
            trial = [b + taken * s for b, s in zip(beta, step, strict=True)]
            tried = model(trial)
            if math.isfinite(tried.loglik) and tried.loglik >= (
                evaluated.loglik + ARMIJO * taken * slope
            ):
                break
            taken *= 0.5
        else:
            return None
        beta, evaluated = trial, tried
    return None


def _right(model: _Model, start: Sequence[float]) -> CoxFit:
    """``coxfit6``'s iteration: a Newton step from 0, then at most ``ITERATIONS`` more, a step
    that does not improve the log-likelihood halved back towards the last good β; run out, the
    last good β recomputed (which gives the values ``coxfit6`` gives without recomputing when
    ``ITERATIONS`` is 1), and its iterations counted as ``coxfit6`` counts them, one more."""
    beta = list(start)
    evaluated = model(beta)
    first = best = evaluated.loglik
    factored, _ = _factored(evaluated)
    step = solve(factored, evaluated.score)
    newbeta = [b + s for b, s in zip(beta, step, strict=True)]
    halving = 0
    for iteration in range(1, ITERATIONS + 1):
        evaluated = model(newbeta)
        factored, _ = _factored(evaluated)
        finite = _finite(evaluated)
        if finite and _changed(best, evaluated.loglik) <= EPS:
            return _result(newbeta, evaluated, True, first, iteration)
        if not finite or evaluated.loglik < best:
            halving += 1
            newbeta = [
                ieee.div(new + halving * old, halving + 1.0)
                for new, old in zip(newbeta, beta, strict=True)
            ]
        else:
            halving = 0
            best = evaluated.loglik
            step = solve(factored, evaluated.score)
            beta = newbeta
            newbeta = [b + s for b, s in zip(beta, step, strict=True)]
    evaluated = model(beta)
    return _result(beta, evaluated, False, first, ITERATIONS + 1)


def _counting(model: _Model, start: Sequence[float]) -> CoxFit:
    """``agfit4``'s iteration: iteration 0 a Newton step from 0; each later one fails on a
    non-finite diagonal or log-likelihood or a change of the information's rank, converges only
    when not halving, and halves back towards the last good β on a failure or a fall; run out
    after more than one iteration, a fall of more than ``EPS`` returns to the last good β."""
    m = len(start)
    beta = list(start)
    evaluated = model(beta)
    first = best = evaluated.loglik
    factored, rank = _factored(evaluated)
    step = solve(factored, evaluated.score)
    oldbeta = beta
    beta = [b + s for b, s in zip(beta, step, strict=True)]
    halving = 0
    for iteration in range(1, ITERATIONS + 1):
        evaluated = model(beta)
        fail = sum(1 for i in range(m) if not math.isfinite(evaluated.information[i][i]))
        factored, again = _factored(evaluated)
        fail += (not math.isfinite(evaluated.loglik)) + abs(rank - again)
        if fail == 0 and halving == 0 and _changed(best, evaluated.loglik) <= EPS:
            return _result(beta, evaluated, True, first, iteration)
        if iteration == ITERATIONS:
            if ITERATIONS > 1 and ieee.div(evaluated.loglik - best, abs(best)) < -EPS:
                beta = oldbeta
                evaluated = model(beta)
            return _result(beta, evaluated, False, first, iteration)
        if fail > 0 or evaluated.loglik < best:
            halving += 1
            beta = [
                ieee.div(old * halving + new, halving + 1.0)
                for old, new in zip(oldbeta, beta, strict=True)
            ]
        else:
            halving = 0
            best = evaluated.loglik
            step = solve(factored, evaluated.score)
            oldbeta = beta
            beta = [b + s for b, s in zip(beta, step, strict=True)]
    raise AssertionError("unreachable")


@dataclass(frozen=True)
class Test:
    """A chi-squared test: its statistic, degrees of freedom and p-value."""

    statistic: float
    df: int
    p: float


def proportional_hazards(
    table: Sequence[EventTime],
    fitted: Sequence[int],
    units: Sequence[int],
    reference: int,
    found: CoxFit,
    watch: Watch,
) -> Test | None:
    """The global test of proportional hazards of ``found``, the fit of the groups ``fitted``
    whose units number ``units`` (per group of the table), as ``cox.zph(transform = "km")``
    tests it (module docstring); none where the extended information's ``cholesky`` has less
    than full rank, where R's ``solve`` finds it singular."""
    terms = [group for group in fitted if group != reference]
    m = len(terms)
    if m == 0 or len(found.coefficients) != m:
        raise ValueError("a test of a fit's terms")
    watch.look()
    transform = [0.0] * len(table)
    ending = [0] * len(table)
    survival = 1.0
    for at, event in enumerate(table):
        watch.spend(len(fitted))
        risk = sum(event.at_risk[group] for group in fitted)
        ending[at] = sum(event.events[group] for group in fitted)
        if ending[at]:
            transform[at] = 1 - survival
            survival *= ieee.div(risk - ending[at], risk)
    centre = ieee.div(ieee.fsum(d * t for d, t in zip(ending, transform, strict=True)), sum(ending))
    weights = [value - centre for value in transform]
    pooled = sum(units[group] for group in fitted)
    means = [ieee.div(units[term], pooled) for term in terms]
    covariates = [
        [(1.0 if group == term else 0.0) - mean for term, mean in zip(terms, means, strict=True)]
        for group in fitted
    ]
    eta = [
        0.0 if group == reference else found.coefficients[terms.index(group)] for group in fitted
    ]
    evaluated = _evaluate(table, fitted, covariates, eta, watch, weights)
    matrix = [list(row) for row in evaluated.information]
    if cholesky(matrix) < 2 * m:
        return None
    score = [0.0] * m + evaluated.score[m:]
    statistic = _solved_quadratic(evaluated.information, score)
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

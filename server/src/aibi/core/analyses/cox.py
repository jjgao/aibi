"""``survival.cox``'s model of cohorts and covariates, as a function of each member's cells
(SPEC §9.5; D362–D365).

A view's positions each hand every member's cells (``Member``): its endpoint row or the reasons
it has none (``survival.endpoint_cells``), and per covariate, then the stratum, its value or the
reasons it has none (``engine.inputs``). ``model`` gives the analysis's values from them:

- **Complete cases** (D363): a member is analysed where it has an endpoint row and a value of
  every covariate and of the stratum; an excluded member counts under the union of its reasons.
  Each position's ``variables`` count, per covariate, then the stratum, then the endpoint, the
  members with a value, over every member.
- **Coding** (D363), over the complete cases of every position: a number as it is, a boolean
  1 for true against 0 for false, a category one column per level but its baseline, the most
  common level among those with an event (ties by the smallest canonical text, as UTF-16 code
  units), an ordered category's levels in its declared order and the others' in canonical
  order; a covariate whose column is constant over the complete cases (a category with one
  level) has one term, ``zero_variance``, and no column. At most ``MAX_PARAMETERS`` columns
  (``TooManyParameters``), and at most ``MAX_STRATA`` stratum levels (``TooManyStrata``).
- **The fit** (D364): with no covariate column and one stratum level, the cohorts' fit of
  D356 and D357 (``survival``'s), and otherwise ``coxph.separated`` and
  ``coxph.separated_hazards`` over the cohort indicators and the covariates' columns.
- **Labels** (D364), each member of the values taking the first that applies: overlapping
  cohorts; no complete case (``no_units``) or no event among them (``no_events``); a cohort term
  whose position, or whose reference, has no complete case or no event; a constant column's
  ``zero_variance``; the fit's ``separation`` (with its ``Direction``) or ``zero_variance``;
  ``not_converged``; a variance that is not positive; a value beyond 2^53 − 1 or that rounds to
  0 (``separation``, without a direction).

Every loop spends its work through ``timetoevent.Watch`` (D350).
"""

import math
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from aibi.core.analyses import coxfit, coxph, ieee, survival, timetoevent
from aibi.core.analyses.coxph import Unit
from aibi.core.analyses.distribution import category_label
from aibi.core.analyses.stats import z
from aibi.core.engine.variables import Value
from aibi.core.schema.analyses import (
    CovariateTest,
    CoxPosition,
    CoxTerm,
    CoxValues,
    CoxView,
    Direction,
    ModelTest,
)
from aibi.core.schema.digests import order_key
from aibi.core.schema.ids import MAX_SAFE_INTEGER
from aibi.core.schema.limits import MAX_VARIABLES
from aibi.core.schema.numbers import Interval, NotEstimableReason
from aibi.core.schema.output import Data
from aibi.core.schema.results import Analysed, AnalysedCounts
from aibi.core.schema.semantics import ExclusionReason

MAX_PARAMETERS = MAX_VARIABLES
"""The covariates' columns a model has at most, after coding (§9.5)."""

MAX_STRATA = 100
"""The stratum's levels a model has at most among its complete cases (D363)."""

TEST = "grambsch_therneau"
"""The method of the test of proportional hazards, as the entry names it."""

WALD = "wald"
"""The method of the terms' intervals and of the covariates' joint tests."""

_NO_UNITS = NotEstimableReason.NO_UNITS
_NO_EVENTS = NotEstimableReason.NO_EVENTS
_ZERO_VARIANCE = NotEstimableReason.ZERO_VARIANCE
_SEPARATION = NotEstimableReason.SEPARATION
_OVERLAP = NotEstimableReason.OVERLAPPING_COHORTS
_NOT_CONVERGED = NotEstimableReason.NOT_CONVERGED
_TERM = ("/estimate", "/ci/low", "/ci/high", "/p")
_TEST = ("/statistic", "/p")


class TooManyParameters(ValueError):  # noqa: N818 - raised like a limit's refusal
    """The covariates code to more than ``MAX_PARAMETERS`` columns: ``count`` of them."""

    def __init__(self, count: int) -> None:
        super().__init__(count)
        self.count = count


class TooManyStrata(ValueError):  # noqa: N818 - raised like a limit's refusal
    """The stratum has more than ``MAX_STRATA`` levels among the complete cases: ``count``."""

    def __init__(self, count: int) -> None:
        super().__init__(count)
        self.count = count


@dataclass(frozen=True)
class Covariate:
    """How a covariate codes (D363): a number, a boolean, or a category, whose ``order`` is its
    declared order where it is ordered."""

    kind: Literal["number", "boolean", "category"]
    order: tuple[Value, ...] | None = None


@dataclass(frozen=True)
class Member:
    """A member's cells: its endpoint row (``timetoevent.Subject``) or ``None`` with its
    reasons, and per covariate, then the stratum where the model has one, its value or
    ``None`` with its reasons. Every endpoint row of a model has an entry, or none has
    (``timetoevent.ORIGIN``), as an endpoint's entry column gives them; a boolean covariate's
    values are booleans."""

    endpoint: timetoevent.Subject | None
    endpoint_reasons: frozenset[ExclusionReason]
    values: tuple[Value | None, ...]
    reasons: tuple[frozenset[ExclusionReason], ...]


@dataclass(frozen=True)
class Outcome:
    """The model's ``analysed`` per position (complete cases) and its values."""

    analysed: list[Analysed]
    values: CoxValues


@dataclass(frozen=True)
class _Coded:
    """A covariate's coding: its columns' levels (none for a number or a constant column), its
    baseline, and whether its column is constant over the complete cases."""

    levels: tuple[Value, ...]
    baseline: Value | None
    constant: bool

    @property
    def width(self) -> int:
        return 0 if self.constant else max(1, len(self.levels))


def _counts(
    members: Sequence[Member], known: Sequence[bool], reasons: Sequence[frozenset[ExclusionReason]]
) -> AnalysedCounts:
    excluded = dict.fromkeys(ExclusionReason, 0)
    for given in reasons:
        for reason in given:
            excluded[reason] += 1
    n = sum(known)
    return AnalysedCounts(n=n, excluded=excluded, excluded_units=len(members) - n)


def _analysed(
    members: Sequence[Member], width: int, watch: timetoevent.Watch
) -> tuple[list[Member], Analysed, list[AnalysedCounts]]:
    """A position's complete cases, their ``analysed``, and each of its ``width`` variables' and
    the endpoint's counts over every member."""
    if any(len(member.values) != width or len(member.reasons) != width for member in members):
        raise ValueError("a member's cells are its covariates' and its stratum's")
    complete: list[Member] = []
    excluded = dict.fromkeys(ExclusionReason, 0)
    excluded_units = 0
    for member in members:
        watch.spend(1 + width)
        given = set(member.endpoint_reasons)
        for reasons in member.reasons:
            given |= reasons
        if member.endpoint is None or any(value is None for value in member.values):
            excluded_units += 1
            for reason in given:
                excluded[reason] += 1
            continue
        complete.append(member)
    analysed = Analysed(n=len(complete), excluded=excluded, excluded_units=excluded_units)
    variables = [
        _counts(
            members,
            [member.values[j] is not None for member in members],
            [member.reasons[j] for member in members],
        )
        for j in range(width)
    ]
    variables.append(
        _counts(
            members,
            [member.endpoint is not None for member in members],
            [member.endpoint_reasons for member in members],
        )
    )
    return complete, analysed, variables


def _known(values: Sequence[Value | None]) -> list[Value]:
    return [value for value in values if value is not None]


def _coded(
    covariate: Covariate, j: int, complete: Sequence[Member], watch: timetoevent.Watch
) -> _Coded:
    """A covariate's coding over the complete cases of every position (module docstring)."""
    watch.spend(len(complete))
    values = _known([member.values[j] for member in complete])
    if covariate.kind == "number":
        numbers = {ieee.real(value) for value in values if not isinstance(value, bool | str)}
        return _Coded((), None, len(numbers) <= 1)
    if covariate.kind == "boolean":
        if len(set(values)) <= 1:
            return _Coded((), None, True)
        return _Coded((True,), False, False)
    present = set(values)
    if not present:
        return _Coded((), None, True)
    if covariate.order is not None:
        ordered = [value for value in covariate.order if value in present]
        ordered += sorted(present - set(ordered), key=order_key)
    else:
        ordered = sorted(present, key=order_key)
    units = Counter(values)
    evented = set(
        _known([member.values[j] for member in complete if member.endpoint and member.endpoint[2]])
    )
    candidates = [value for value in ordered if value in evented] or ordered
    baseline = min(candidates, key=lambda value: (-units[value], order_key(value)))
    levels = tuple(value for value in ordered if value != baseline)
    return _Coded(levels, baseline, len(ordered) <= 1)


def _columns(covariate: Covariate, coded: _Coded, value: Value | None) -> tuple[float, ...]:
    """A complete case's columns of a covariate."""
    if coded.constant:
        return ()
    assert value is not None
    if covariate.kind == "number":
        assert not isinstance(value, bool | str)
        return (ieee.real(value),)
    if covariate.kind == "boolean":
        if not isinstance(value, bool):
            raise ValueError("a boolean covariate's values are booleans")
        return (1.0 if value else 0.0,)
    return tuple(1.0 if value == level else 0.0 for level in coded.levels)


def _data(value: Value) -> Data:
    return Data(data=category_label(value))


def _term(
    kind: Literal["cohort", "covariate"],
    index: int,
    level: Value | None,
    baseline: Value | None,
    confidence: float,
    reason: NotEstimableReason | None = None,
    estimate: tuple[float, float] | None = None,
    direction: Direction | None = None,
) -> CoxTerm:
    """A term: not estimable for ``reason``, or with ``estimate`` (β, its variance)."""
    position = index if kind == "cohort" else None
    covariate = index if kind == "covariate" else None
    shown_level = shown_baseline = None
    if kind == "covariate" and baseline is not None:
        assert level is not None
        shown_level, shown_baseline = _data(level), _data(baseline)
    interval = Interval(method=WALD, level=confidence, low=None, high=None)
    if estimate is None:
        assert reason is not None
        return CoxTerm(
            kind=kind,
            position=position,
            covariate=covariate,
            level=shown_level,
            baseline=shown_baseline,
            estimate=None,
            ci=interval,
            p=None,
            direction=direction if reason is _SEPARATION else None,
            not_estimable=dict.fromkeys(_TERM, reason),
        )
    beta, variance = estimate
    if not (variance > 0 and math.isfinite(variance)):
        return _term(kind, index, level, baseline, confidence, _ZERO_VARIANCE)
    spread = z(confidence) * ieee.sqrt(variance)
    found = [ieee.exp(value) for value in (beta, beta - spread, beta + spread)]
    shown = [value if 0 < value <= MAX_SAFE_INTEGER else None for value in found]
    statistic = ieee.div(beta * beta, variance)
    p = timetoevent.chi_squared_p(statistic, 1) if math.isfinite(statistic) else None
    reasons = {
        member: _SEPARATION for member, value in zip(_TERM[:3], shown, strict=True) if value is None
    }
    if p is None:
        reasons["/p"] = _ZERO_VARIANCE
    return CoxTerm(
        kind=kind,
        position=position,
        covariate=covariate,
        level=shown_level,
        baseline=shown_baseline,
        estimate=shown[0],
        ci=Interval(method=WALD, level=confidence, low=shown[1], high=shown[2]),
        p=p,
        not_estimable=reasons or None,
    )


@dataclass(frozen=True)
class _Plan:
    """The model's terms before the fit: per term its kind, index, level and baseline, its
    design column (``None`` where it has none) and a reason decided before the fit."""

    kind: Literal["cohort", "covariate"]
    index: int
    level: Value | None
    baseline: Value | None
    column: int | None
    reason: NotEstimableReason | None


def _not_computed(reason: NotEstimableReason) -> ModelTest:
    return ModelTest(
        method=TEST,
        terms=[],
        statistic=None,
        df=None,
        p=None,
        not_estimable=dict.fromkeys(_TEST, reason),
    )


def _covariate_test(
    covariate: int, df: int, reason: NotEstimableReason | None, found: tuple[float, float] | None
) -> CovariateTest:
    if found is None:
        assert reason is not None
        return CovariateTest(
            covariate=covariate,
            statistic=None,
            df=df,
            p=None,
            not_estimable=dict.fromkeys(_TEST, reason),
        )
    statistic, p = found
    return CovariateTest(covariate=covariate, statistic=statistic, df=df, p=p)


def model(
    positions: Sequence[Sequence[Member]],
    covariates: Sequence[Covariate],
    *,
    stratified: bool,
    reference: int,
    overlap: bool,
    level: float,
    ends: float | None = None,
) -> Outcome:
    """``survival.cox``'s values over the positions' members (module docstring): ``overlap``
    where cohorts share units a view allows, the positions' complete cases pooled for the
    coding, the reference ``reference``, intervals at ``level``, the call's deadline ``ends``
    watched."""
    if len(covariates) > MAX_VARIABLES:
        raise ValueError("a model has at most MAX_VARIABLES covariates")
    entries = {
        member.endpoint[0] == timetoevent.ORIGIN
        for members in positions
        for member in members
        if member.endpoint is not None
    }
    if len(entries) > 1:
        raise ValueError("every endpoint row has an entry, or none has")
    watch = timetoevent.Watch(ends)
    watch.look()
    cases: list[list[Member]] = []
    analysed: list[Analysed] = []
    counted: list[list[AnalysedCounts]] = []
    for members in positions:
        complete, whole, variables = _analysed(members, len(covariates) + stratified, watch)
        cases.append(complete)
        analysed.append(whole)
        counted.append(variables)
    pooled = [member for complete in cases for member in complete]
    events = [
        sum(1 for member in complete if member.endpoint and member.endpoint[2])
        for complete in cases
    ]
    coding = [_coded(covariate, j, pooled, watch) for j, covariate in enumerate(covariates)]
    parameters = sum(coded.width for coded in coding)
    if parameters > MAX_PARAMETERS:
        raise TooManyParameters(parameters)
    strata: dict[Value, int] = {}
    if stratified:
        levels = sorted(set(_known([member.values[-1] for member in pooled])), key=order_key)
        if len(levels) > MAX_STRATA:
            raise TooManyStrata(len(levels))
        strata = {value: k for k, value in enumerate(levels)}
    watch.look()
    plans: list[_Plan] = []
    others = [position for position in range(len(positions)) if position != reference]
    column = 0
    for position in others:
        reason = None
        if not cases[reference]:
            reason = _NO_UNITS
        elif not events[reference]:
            reason = _NO_EVENTS
        elif not cases[position]:
            reason = _NO_UNITS
        elif not events[position]:
            reason = _NO_EVENTS
        plans.append(_Plan("cohort", position, None, None, column, reason))
        column += 1
    for j, (covariate, coded) in enumerate(zip(covariates, coding, strict=True)):
        if coded.constant:
            plans.append(_Plan("covariate", j, None, None, None, _ZERO_VARIANCE))
            continue
        if covariate.kind == "number":
            plans.append(_Plan("covariate", j, None, None, column, None))
            column += 1
            continue
        for value in coded.levels:
            plans.append(_Plan("covariate", j, value, coded.baseline, column, None))
            column += 1
    whole_reason: NotEstimableReason | None = None
    if overlap:
        whole_reason = _OVERLAP
    elif not pooled:
        whole_reason = _NO_UNITS
    elif not sum(events):
        whole_reason = _NO_EVENTS
    positions_values = [
        CoxPosition(events=count, variables=variables)
        for count, variables in zip(events, counted, strict=True)
    ]
    if whole_reason is not None:
        terms = [
            _term(plan.kind, plan.index, plan.level, plan.baseline, level, whole_reason)
            for plan in plans
        ]
        tests = [
            _covariate_test(j, coded.width, whole_reason, None)
            for j, coded in enumerate(coding)
            if coded.width >= 2
        ]
        view = CoxView(
            terms=terms, covariate_tests=tests, proportional_hazards=_not_computed(whole_reason)
        )
        return Outcome(analysed, CoxValues(positions=positions_values, view=view))
    covariate_columns = sum(
        1 for plan in plans if plan.kind == "covariate" and plan.column is not None
    )
    if covariate_columns == 0 and len(strata) <= 1:
        view = _cohorts(cases, plans, reference, level, ends)
    else:
        view = _design(cases, plans, covariates, coding, strata, reference, level, watch)
    return Outcome(analysed, CoxValues(positions=positions_values, view=view))


def _rows(complete: Sequence[Member]) -> survival.Rows:
    subjects = tuple(member.endpoint for member in complete if member.endpoint is not None)
    return survival.Rows(subjects, dict.fromkeys(ExclusionReason, 0), 0, frozenset())


def _cohorts(
    cases: Sequence[Sequence[Member]],
    plans: Sequence[_Plan],
    reference: int,
    level: float,
    ends: float | None,
) -> CoxView:
    """The view by D356 and D357: ``survival.km``'s hazard ratios and test of the cohorts."""
    rows = [_rows(complete) for complete in cases]
    fitted = survival.cohort_fit(rows, reference, ends)
    terms: list[CoxTerm] = []
    for plan in plans:
        if plan.kind == "covariate":
            terms.append(
                _term("covariate", plan.index, plan.level, plan.baseline, level, plan.reason)
            )
            continue
        if plan.reason is not None:
            terms.append(_term("cohort", plan.index, None, None, level, plan.reason))
            continue
        effect = survival.hazard_ratio(rows, fitted, plan.index, reference, level, False)
        reasons = effect.reasons()
        if effect.estimate is None:
            reason = reasons["/estimate"]
            direction = None
            if not isinstance(fitted, NotEstimableReason) and reason is _SEPARATION:
                standing = fitted.standing.get(plan.index)
                if standing is coxfit.Standing.HIGHER:
                    direction = Direction.INFINITY
                elif standing is coxfit.Standing.LOWER:
                    direction = Direction.ZERO
            terms.append(
                _term("cohort", plan.index, None, None, level, reason, direction=direction)
            )
            continue
        assert not isinstance(fitted, NotEstimableReason)
        assert fitted.fit is not None
        k = fitted.terms[plan.index]
        terms.append(
            _term(
                "cohort",
                plan.index,
                None,
                None,
                level,
                estimate=(fitted.fit.coefficients[k], fitted.fit.variance[k][k]),
            )
        )
    found = survival.cohort_test(rows, fitted, reference, False, ends)
    if found.p is None:
        reason = found.reasons()["/p"]
        if reason in (_NO_UNITS, _NO_EVENTS):
            reason = _untested(terms)
        test = _not_computed(reason)
    else:
        assert not isinstance(fitted, NotEstimableReason)
        term_of = {plan.index: k for k, plan in enumerate(plans) if plan.kind == "cohort"}
        tested = sorted(term_of[position] for position in fitted.terms)
        test = ModelTest(
            method=TEST, terms=tested, statistic=found.statistic, df=int(found.df or 0), p=found.p
        )
    return CoxView(terms=terms, covariate_tests=[], proportional_hazards=test)


def _untested(terms: Sequence[CoxTerm]) -> NotEstimableReason:
    """Why a model with no estimated term has no test (D364): ``separation`` where a term is
    separated, else ``zero_variance``."""
    separated = any(term.reasons().get("/estimate") is _SEPARATION for term in terms)
    return _SEPARATION if separated else _ZERO_VARIANCE


def _design(
    cases: Sequence[Sequence[Member]],
    plans: Sequence[_Plan],
    covariates: Sequence[Covariate],
    coding: Sequence[_Coded],
    strata: dict[Value, int],
    reference: int,
    level: float,
    watch: timetoevent.Watch,
) -> CoxView:
    """The view by ``coxph.separated`` and ``coxph.separated_hazards`` (D364)."""
    others = [position for position in range(len(cases)) if position != reference]
    units: list[Unit] = []
    counting = False
    for position, complete in enumerate(cases):
        indicator = tuple(1.0 if position == other else 0.0 for other in others)
        for member in complete:
            watch.spend(1 + len(member.values))
            assert member.endpoint is not None
            entry, time, event = member.endpoint
            counting = counting or entry != timetoevent.ORIGIN
            columns: list[float] = list(indicator)
            for j, (covariate, coded) in enumerate(zip(covariates, coding, strict=True)):
                columns.extend(_columns(covariate, coded, member.values[j]))
            last = member.values[-1]
            stratum = strata[last] if strata and last is not None else 0
            units.append(Unit(stratum, entry, time, event, tuple(columns)))
    found = coxph.separated(units, watch, counting=counting)
    terms: list[CoxTerm] = []
    for plan in plans:
        if plan.reason is not None or plan.column is None:
            terms.append(
                _term(
                    plan.kind,
                    plan.index,
                    plan.level,
                    plan.baseline,
                    level,
                    plan.reason or _ZERO_VARIANCE,
                )
            )
            continue
        j = plan.column
        if j in found.separation:
            direction = Direction.INFINITY if found.separation[j] > 0 else Direction.ZERO
            terms.append(
                _term(
                    plan.kind,
                    plan.index,
                    plan.level,
                    plan.baseline,
                    level,
                    _SEPARATION,
                    direction=direction,
                )
            )
            continue
        if j in found.unidentified or j in found.unscalable or j not in found.estimated:
            terms.append(
                _term(plan.kind, plan.index, plan.level, plan.baseline, level, _ZERO_VARIANCE)
            )
            continue
        assert found.fit is not None
        if not found.fit.converged:
            terms.append(
                _term(plan.kind, plan.index, plan.level, plan.baseline, level, _NOT_CONVERGED)
            )
            continue
        k = found.free.index(j)
        terms.append(
            _term(
                plan.kind,
                plan.index,
                plan.level,
                plan.baseline,
                level,
                estimate=(found.fit.coefficients[k], found.fit.variance[k][k]),
            )
        )
    tests = _covariate_tests(plans, coding, terms, found)
    term_of = {plan.column: k for k, plan in enumerate(plans) if plan.column is not None}
    test = _model_test(terms, found, term_of, watch, units)
    return CoxView(terms=terms, covariate_tests=tests, proportional_hazards=test)


def _covariate_tests(
    plans: Sequence[_Plan],
    coding: Sequence[_Coded],
    terms: Sequence[CoxTerm],
    found: coxph.Separated,
) -> list[CovariateTest]:
    """Each covariate's joint Wald test of its two or more columns (D365)."""
    tests: list[CovariateTest] = []
    for j, coded in enumerate(coding):
        if coded.width < 2:
            continue
        mine = [k for k, plan in enumerate(plans) if plan.kind == "covariate" and plan.index == j]
        missing = next((terms[k] for k in mine if terms[k].estimate is None), None)
        if missing is not None:
            tests.append(_covariate_test(j, coded.width, missing.reasons()["/estimate"], None))
            continue
        assert found.fit is not None
        index = [found.free.index(plans[k].column) for k in mine]  # pyright: ignore[reportArgumentType]
        beta = [found.fit.coefficients[k] for k in index]
        variance = [[found.fit.variance[a][b] for b in index] for a in index]
        statistic = coxfit.solved_quadratic(variance, beta)
        if not (math.isfinite(statistic) and 0 <= statistic <= MAX_SAFE_INTEGER):
            tests.append(_covariate_test(j, coded.width, _ZERO_VARIANCE, None))
            continue
        p = timetoevent.chi_squared_p(statistic, coded.width)
        tests.append(_covariate_test(j, coded.width, None, (statistic, p)))
    return tests


def _model_test(
    terms: Sequence[CoxTerm],
    found: coxph.Separated,
    term_of: dict[int, int],
    watch: timetoevent.Watch,
    units: Sequence[Unit],
) -> ModelTest:
    """The test of proportional hazards of the columns the finite part estimates, named by
    their terms (D361, D365): ``not_converged`` where its fit did not converge, and D364's
    reason where it estimates none."""
    if not found.estimated:
        return _not_computed(_untested(terms))
    assert found.fit is not None
    if not found.fit.converged:
        return _not_computed(_NOT_CONVERGED)
    result = coxph.separated_hazards(units, found, watch)
    if result is None or not survival.writable(result.statistic, result.p):
        return _not_computed(_ZERO_VARIANCE)
    tested = sorted(term_of[column] for column in found.estimated)
    return ModelTest(
        method=TEST, terms=tested, statistic=result.statistic, df=result.df, p=result.p
    )


__all__ = [
    "MAX_PARAMETERS",
    "MAX_STRATA",
    "Covariate",
    "Member",
    "Outcome",
    "TooManyParameters",
    "TooManyStrata",
    "model",
]

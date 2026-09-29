"""``survival.cox``: one Cox model of cohorts and covariates (SPEC §8.4, §9.1, §9.5; D362–D369).

A view names cohorts (the reference one of them), an endpoint (``params.endpoint`` or the one
usable endpoint on the unit table, D347), covariates (``params.covariates``, variables of §9.2,
one value per unit) and optionally a stratum (``params.stratum``), and gets one model: each other
cohort's membership versus the reference and each covariate's columns, over left-truncated risk
sets, stratified by the stratum's levels (D366).

**Covariates** (D367). A covariate's coding is its variable's (``covariate_of``): a question
(``some``, ``every``) or a boolean column is a boolean; a number, integer or time offset column, a
``count`` or a ``mean``, and ``max`` or ``min`` of numbers, a number; a category column a category,
in its permissible values' order where it is ordered and in canonical order otherwise, a string
column a category in canonical order, and ``max`` or ``min`` of an ordered category a category in
its order. Anything else is refused in phase 2 (``views``), and so is the stratum's. A covariate
may be a predicate (D370), listed as a question of its clause and so a boolean, 1 where it is
TRUE; one that holds a lift is also listed flipped (``lifted``), and ``CoxPosition.lifts`` and
``LIFT_DIFFERS`` count the members whose truth the other lift rule changes (D323, D371).

**The rows** (D368). Each member's cells are listed as a pack analysis's inputs are
(``engine.inputs``, by SQL or the reference evaluator, in the order of §9.3): each covariate's
value, the stratum's, then its endpoint row (``survival.endpoint_cells``). A unit's time or entry
beyond ±(2^53 − 1) refuses the view, as ``survival.km``'s (``survival.TooLarge``), and so does a
number covariate's value there among the complete cases (``TooLarge``), a category's level longer
than ``MAX_TEXT`` (``LongLevel``) or not Unicode text (``NonTextLevel``; ``common.unwritable``),
and a number covariate whose column the design's fit cannot scale (``Unscalable``).

**The model** (D362–D365). A view's positions each hand every member's cells (``Member``): its
endpoint row or the reasons it has none, and per covariate, then the stratum, its value or the
reasons it has none. ``model`` gives the analysis's values from them:

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

**Caveats** (D369): the cohorts' (§8.3); ``UNKNOWN_EXCLUDED`` where a member is left out for a
reason other than ``NOT_APPLICABLE`` or ``INVALID_VALUE``; ``INVALID_EXCLUDED`` where an endpoint
row is invalid; the flags of the values read; ``COHORTS_OVERLAP``; ``SMALL_N`` where a cohort with
complete cases has fewer than ``MIN_GROUP_N`` or fewer than ``MIN_EVENTS`` events among them, and
where the complete cases' events are fewer than ``EVENTS_PER_TERM`` per term the fit estimated;
``PH_VIOLATED`` where the test of proportional hazards gives p < ``PH_LEVEL``; and the view's static
caveats.

**Disclosure** (§8.4, D351, D353). ``survival.cox`` is a refused analysis: under any disclosure
setting a view of it is refused (``WITHHELD_UNDER_K``, ``views.checked``), and applicability calls
it ``unavailable`` there (``registry``). Without one, every count is shown.

Every loop spends its work through ``timetoevent.Watch`` (D350).
"""

import math
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Literal

from aibi.core.analyses import coxfit, coxph, ieee, survival, timetoevent
from aibi.core.analyses.common import (
    CohortAt,
    caveat,
    cohort_caveats,
    flag_caveats,
    populations,
    unwritable,
)
from aibi.core.analyses.coxph import Unit
from aibi.core.analyses.distribution import category_label
from aibi.core.analyses.stats import z
from aibi.core.engine.canonical import CanonicalCohort, CanonicalVariable
from aibi.core.engine.inputs import Listed
from aibi.core.engine.readback import conditions, variable_readback
from aibi.core.engine.resolve import ResolvedEndpoint, ResolvedVariable
from aibi.core.engine.resolved import flipped
from aibi.core.engine.truth import Mark
from aibi.core.engine.variables import Value
from aibi.core.schema.analyses import (
    CovariateTest,
    CoxLift,
    CoxParams,
    CoxPosition,
    CoxTerm,
    CoxValues,
    CoxView,
    Direction,
    ModelTest,
)
from aibi.core.schema.caveats import Caveat, CaveatCode
from aibi.core.schema.descriptors import AnalysisDescriptor
from aibi.core.schema.digests import order_key
from aibi.core.schema.export import params_schema, values_schema
from aibi.core.schema.ids import MAX_SAFE_INTEGER
from aibi.core.schema.jsonio import number_text
from aibi.core.schema.limits import MAX_COHORTS, MAX_STRATA, MAX_VARIABLES
from aibi.core.schema.numbers import Interval, NotEstimableReason
from aibi.core.schema.output import Data, Segment, data, text
from aibi.core.schema.results import Analysed, AnalysedCounts, Population
from aibi.core.schema.semantics import ExclusionReason

MAX_PARAMETERS = MAX_VARIABLES
"""The covariates' columns a model has at most, after coding (§9.5)."""

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


class TooLarge(ValueError):  # noqa: N818 - raised like a limit's refusal
    """A number covariate, ``covariate`` its index, with a complete case's value beyond
    ±(2^53 − 1) (D368): an integer beyond it has no exact double, and rounding would merge
    values that coding and the columns tell apart."""

    def __init__(self, covariate: int) -> None:
        super().__init__(covariate)
        self.covariate = covariate


class LongLevel(ValueError):  # noqa: N818 - raised like a limit's refusal
    """A category covariate, ``covariate`` its index, a level or baseline of which, shown as
    data, is longer than ``MAX_TEXT`` characters (D368)."""

    def __init__(self, covariate: int) -> None:
        super().__init__(covariate)
        self.covariate = covariate


class NonTextLevel(ValueError):  # noqa: N818 - raised like a limit's refusal
    """A category covariate, ``covariate`` its index, a level or baseline of which, shown as
    data, is not Unicode text (a lone surrogate or a noncharacter), which no output's text holds
    (§8.2, D368)."""

    def __init__(self, covariate: int) -> None:
        super().__init__(covariate)
        self.covariate = covariate


class Unscalable(ValueError):  # noqa: N818 - raised like a limit's refusal
    """A number covariate, ``covariate`` its index, whose column the design's fit cannot scale:
    its spread's scale, or that scale's square, is no double (``coxph.Separated.unscalable``,
    D368). It is not constant, so no label would say what it is."""

    def __init__(self, covariate: int) -> None:
        super().__init__(covariate)
        self.covariate = covariate


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
class Model:
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


def _in_range(
    covariates: Sequence[Covariate], complete: Sequence[Member], watch: timetoevent.Watch
) -> None:
    """Raise ``TooLarge`` for a number covariate with a complete case's value beyond
    ±(2^53 − 1) (D368), each covariate's pass spending a unit a complete case."""
    for j, covariate in enumerate(covariates):
        if covariate.kind != "number":
            continue
        watch.spend(len(complete))
        if any(
            abs(value) > MAX_SAFE_INTEGER
            for member in complete
            if (value := member.values[j]) is not None and not isinstance(value, bool | str)
        ):
            raise TooLarge(j)


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
) -> Model:
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
    _in_range(covariates, pooled, watch)
    coding = [_coded(covariate, j, pooled, watch) for j, covariate in enumerate(covariates)]
    for j, coded in enumerate(coding):
        shown = () if coded.constant or coded.baseline is None else (*coded.levels, coded.baseline)
        if covariates[j].kind != "category":
            continue
        for value in shown:
            why = unwritable(category_label(value))
            if why == "long":
                raise LongLevel(j)
            if why == "not_text":
                raise NonTextLevel(j)
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
        return Model(analysed, CoxValues(positions=positions_values, view=view))
    covariate_columns = sum(
        1 for plan in plans if plan.kind == "covariate" and plan.column is not None
    )
    if covariate_columns == 0 and len(strata) <= 1:
        view = _cohorts(cases, plans, reference, level, ends)
    else:
        view = _design(cases, plans, covariates, coding, strata, reference, level, watch)
    return Model(analysed, CoxValues(positions=positions_values, view=view))


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
    for plan in plans:
        if plan.column is not None and plan.column in found.unscalable:
            assert plan.kind == "covariate", "a cohort's indicator is 0 or 1"
            assert covariates[plan.index].kind == "number", "a coded column is 0 or 1"
            raise Unscalable(plan.index)
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
        if j in found.unidentified or j not in found.estimated:
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


# --- The analysis (D366–D369) -------------------------------------------------------------------

ANALYSIS_ID = "survival.cox"
VERSION = "1.0.0"
"""Bumped whenever its outputs for the same inputs change (§7.6)."""
MIN_GROUP_N = survival.MIN_GROUP_N
MIN_EVENTS = survival.MIN_EVENTS
EVENTS_PER_TERM = survival.EVENTS_PER_TERM
"""Below this many events per term the fit estimated, the terms carry ``SMALL_N`` (§8.3)."""
PH_LEVEL = survival.PH_LEVEL
"""Below this p-value of the test of proportional hazards, the result carries ``PH_VIOLATED``."""
CONE = "recession_cone"
"""The method that decides separation and identification (D360)."""
METHODS: Mapping[str, str] = {
    WALD: 'The Cox model, coxph(ties = "efron"), of each other cohort\'s membership versus the '
    "reference and the covariates' columns over left-truncated risk sets, stratified by the "
    "stratum's levels, fitted as R fits it; each term's hazard ratio with its Wald interval and "
    "Wald p-value as summary.coxph gives them, and the joint Wald test of each covariate of two "
    "or more columns, implemented directly",
    CONE: "Separation and identification decided exactly from the risk sets, by the "
    "likelihood's recession cone in integers, not by R's flags after fitting; the finite part "
    "fitted with the separated columns fixed at 0",
    TEST: "The global test of proportional hazards of the terms the fit estimated, as R's "
    'cox.zph(transform = "km"), implemented directly',
}
CAVEATS = survival.CAVEATS
ENTRY = AnalysisDescriptor.model_validate(
    {
        "kind": "analysis",
        "id": ANALYSIS_ID,
        "version": VERSION,
        "label": "Cox proportional hazards model",
        "definition": (
            "One Cox model over left-truncated risk sets, Efron ties: each other cohort's "
            "membership versus the reference and each covariate (numbers as they are, booleans "
            "1 against 0, categories one term per level against the most common one among those "
            "with an event), optionally stratified; complete cases only; hazard ratios with "
            "Wald intervals and p-values, each covariate's joint Wald test, and the test of "
            "proportional hazards."
        ),
        "fields": {
            "requires": [
                {"role": "endpoint", "kind": "endpoint", "on": "unit"},
                {"role": "cohorts", "min": 1, "max": MAX_COHORTS},
            ],
            "params": params_schema(CoxParams),
            "returns": values_schema(CoxValues),
            "methods": dict(METHODS),
            "assumptions": [
                "proportional hazards",
                "log-linear effects of numbers",
                "independent censoring",
                "independent groups",
            ],
            "uses_reference": True,
            "assumes_independent_groups": True,
            "cross_dataset": None,
            "caveats": [code.value for code in CAVEATS],
            "min_group_n": MIN_GROUP_N,
            "min_events": MIN_EVENTS,
        },
    }
)
"""The registry entry (§9.1): implemented directly, with no library."""

_NUMBERS = frozenset({"number", "integer", "time_offset"})
TAKEN = ("category", "boolean", "string", "number", "integer", "time_offset")
"""The datatypes of the columns a covariate or the stratum reads as it is (D367)."""


def covariate_of(variable: ResolvedVariable) -> Covariate | None:
    """A covariate's coding by its variable (D367, module docstring); ``None`` for one that is
    none of them, which phase 2 refuses."""
    if variable.kind == "question":
        return Covariate("boolean")
    if variable.kind == "aggregate":
        if variable.function != "count" and variable.order is not None:
            return Covariate("category", tuple(variable.order))
        assert variable.function == "count" or variable.datatype in _NUMBERS, (
            "resolution takes a mean, max or min of numbers or of an ordered category alone"
        )
        return Covariate("number")
    if variable.datatype == "boolean":
        return Covariate("boolean")
    if variable.datatype in _NUMBERS:
        return Covariate("number")
    if variable.datatype == "string":
        return Covariate("category")
    if variable.datatype != "category":
        return None
    table, _, name = variable.column.partition(".")
    descriptor = variable.release.column(table, name)
    allowed = None if descriptor is None else descriptor.fields.permissible_values
    if allowed is None or not allowed.ordered:
        return Covariate("category")
    return Covariate("category", tuple(entry.value for entry in allowed.values))


def members_of(
    endpoint: ResolvedEndpoint, listed: Listed, width: int, ends: float | None = None
) -> list[Member]:
    """A position's members' cells from a listing of its ``width`` variables (the covariates,
    then the stratum) and then its endpoint's columns (D368); the call's deadline ``ends``
    watched, each member spending a unit a cell."""
    cells = survival.endpoint_cells(endpoint, listed, width, ends)
    watch = timetoevent.Watch(ends)
    found: list[Member] = []
    for member in range(listed.members):
        watch.spend(1 + width)
        found.append(
            Member(
                cells.subjects[member],
                cells.reasons[member],
                tuple(listed.values[j][member] for j in range(width)),
                tuple(listed.excluded[j][member] for j in range(width)),
            )
        )
    return found


def _within(positions: Sequence[Sequence[Member]], watch: timetoevent.Watch) -> None:
    """Raise ``survival.TooLarge`` for a unit's time or entry beyond ±(2^53 − 1), as
    ``survival.km`` does (D348, D368); an entry at the origin is none."""
    for members in positions:
        for member in members:
            watch.spend()
            if member.endpoint is None:
                continue
            entry, when, _ = member.endpoint
            if abs(when) > MAX_SAFE_INTEGER or (
                entry != timetoevent.ORIGIN and abs(entry) > MAX_SAFE_INTEGER
            ):
                raise survival.TooLarge


def lifted(variables: Sequence[ResolvedVariable]) -> list[tuple[int, ResolvedVariable]]:
    """Each predicate covariate that holds a lift, by its index, with its truth under every lift
    flipped (D323, D371), as a question the listing reads after the endpoint's columns."""
    found: list[tuple[int, ResolvedVariable]] = []
    for j, variable in enumerate(variables):
        if variable.kind != "question" or variable.aggregate is not None:
            continue
        assert variable.question is not None
        other = flipped(variable.question)
        if other != variable.question:
            found.append((j, replace(variable, key=f"{variable.key}/flipped", question=other)))
    return found


def listed_variables(
    variables: Sequence[ResolvedVariable], endpoint: ResolvedEndpoint
) -> list[ResolvedVariable]:
    """What a view's listing reads of each member (D368, D371): its covariates in order, its
    stratum, its endpoint's columns, then each predicate covariate that holds a lift, flipped
    (``lifted``)."""
    return [
        *variables,
        *endpoint.variables,
        *(variable for _, variable in lifted(variables)),
    ]


@dataclass(frozen=True)
class Outcome:
    """A view's digested parts, before its digest is taken, and its caveats."""

    population: list[Population]
    analysed: list[Analysed]
    values: CoxValues
    caveats: list[Caveat]


def analyse(
    positions: Sequence[CohortAt],
    listed: Sequence[Listed],
    endpoint: ResolvedEndpoint,
    variables: Sequence[ResolvedVariable],
    params: CoxParams,
    *,
    reference: int,
    overlap: bool,
    ends: float | None = None,
) -> Outcome:
    """``survival.cox`` over a view's cohorts (module docstring), from each position's listing
    of ``listed_variables(variables, endpoint)`` in the order of §9.3 (``members_of``);
    ``variables`` are the view's covariates in order, then its stratum, each coded by
    ``covariate_of``, and ``overlap`` says that the view allows overlap and its cohorts share
    units. The members whose truth the other lift rule changes are counted for each predicate
    covariate that holds a lift (``lifted``, D371). Raises ``survival.TooLarge``, ``TooLarge``,
    ``LongLevel``, ``NonTextLevel``, ``Unscalable``, ``TooManyParameters``, ``TooManyStrata``,
    and ``CallerDeadline`` once ``time.monotonic()`` has passed ``ends`` (``timetoevent.Watch``,
    D350)."""
    covariates: list[Covariate] = []
    for variable in variables[: len(params.covariates)]:
        coded = covariate_of(variable)
        assert coded is not None, "phase 2 refuses a covariate of no coding"
        covariates.append(coded)
    lifts = [j for j, _ in lifted(variables)]
    if len(listed) != len(positions):
        raise ValueError("a listing per position")
    width = len(covariates) + (params.stratum is not None)
    watch = timetoevent.Watch(ends)
    members = [members_of(endpoint, one, width, ends) for one in listed]
    _within(members, watch)
    watch.look()
    found = model(
        members,
        covariates,
        stratified=params.stratum is not None,
        reference=reference,
        overlap=overlap,
        level=params.level,
        ends=ends,
    )
    population = populations(positions, None)
    marks = [
        mark
        for one in listed
        for flags in one.marks[: width + len(endpoint.variables)]
        for mark in flags
    ]
    changed = [_lift_changes(one, lifts, width + len(endpoint.variables), watch) for one in listed]
    values = found.values
    if lifts:
        values = values.model_copy(
            update={
                "positions": [
                    at.model_copy(
                        update={
                            "lifts": [
                                CoxLift(covariate=j, lift_differs=count)
                                for j, count in counts.items()
                            ]
                        }
                    )
                    for at, counts in zip(values.positions, changed, strict=True)
                ]
            }
        )
    caveats = _caveats(positions, population, found, marks, overlap, changed)
    return Outcome(population, found.analysed, values, caveats)


def _lift_changes(
    listed: Listed, lifts: Sequence[int], offset: int, watch: timetoevent.Watch
) -> dict[int, int]:
    """Per predicate covariate that holds a lift, the members whose truth the other lift rule
    changes (D323): its listing from ``offset`` on is each such covariate flipped, in order."""
    found: dict[int, int] = {}
    for k, j in enumerate(lifts):
        watch.spend(listed.members)
        given, other = listed.values[j], listed.values[offset + k]
        found[j] = sum(1 for a, b in zip(given, other, strict=True) if a != b)
    return found


def _estimated(term: CoxTerm) -> bool:
    """Whether the fit estimated a term: its ratio is shown, or lies beyond range (D364)."""
    reason = term.reasons().get("/estimate")
    return term.estimate is not None or (reason is _SEPARATION and term.direction is None)


def _caveats(
    positions: Sequence[CohortAt],
    population: Sequence[Population],
    found: Model,
    marks: Sequence[Mark],
    overlap: bool,
    changed: Sequence[Mapping[int, int]],
) -> list[Caveat]:
    """The caveats that the view's data raise (module docstring); the static ones are the
    view's."""
    unknown = any(
        count
        for one in found.analysed
        for reason, count in (one.excluded or {}).items()
        if reason not in (ExclusionReason.NOT_APPLICABLE, ExclusionReason.INVALID_VALUE)
    )
    caveats = cohort_caveats(positions, population, None, values_unknown=unknown)
    caveats += flag_caveats(marks, "/values")
    if any(
        (position.variables[-1].excluded or {}).get(ExclusionReason.INVALID_VALUE)
        for position in found.values.positions
    ):
        caveats.append(
            caveat(
                CaveatCode.INVALID_EXCLUDED,
                ["/analysed"],
                text(
                    "Units whose status is not in the endpoint's event coding, whose time is "
                    "negative, or whose entry is at or after their time are left out as "
                    "INVALID_VALUE (§5.8)"
                ),
            )
        )
    differs = [
        f"covariate {j} for {count} units of the cohort at position {position}"
        for position, counts in enumerate(changed)
        for j, count in sorted(counts.items())
        if count
    ]
    if differs:
        caveats.append(
            caveat(
                CaveatCode.LIFT_DIFFERS,
                ["/values"],
                text(
                    "The other lift rule would change the truth of these predicate covariates: "
                    + "; ".join(differs)
                ),
            )
        )
    if overlap:
        caveats.append(
            caveat(
                CaveatCode.COHORTS_OVERLAP,
                ["/values"],
                text(
                    "Cohorts of the view share units, so no value of the model was computed; "
                    "compare a subset with the rest of its base instead (§7.4)"
                ),
            )
        )
    small = [
        f"/values/positions/{position}"
        for position, (one, at) in enumerate(
            zip(found.analysed, found.values.positions, strict=True)
        )
        if one.n and (one.n < MIN_GROUP_N or at.events < MIN_EVENTS)
    ]
    if small:
        caveats.append(
            caveat(
                CaveatCode.SMALL_N,
                small,
                text(
                    f"A cohort has fewer than {MIN_GROUP_N} complete cases or fewer than "
                    f"{MIN_EVENTS} events among them"
                ),
            )
        )
    view = found.values.view
    estimated = sum(_estimated(term) for term in view.terms)
    events = sum(position.events for position in found.values.positions)
    if estimated and events < EVENTS_PER_TERM * estimated:
        caveats.append(
            caveat(
                CaveatCode.SMALL_N,
                ["/values/view/terms"],
                text(f"The model has fewer than {EVENTS_PER_TERM} events per term it estimated"),
            )
        )
    tested = view.proportional_hazards
    if tested.p is not None and tested.p < PH_LEVEL:
        caveats.append(
            caveat(
                CaveatCode.PH_VIOLATED,
                ["/values/view/terms", "/values/view/proportional_hazards"],
                text(
                    f"The test of proportional hazards gives p < {number_text(PH_LEVEL)}: each "
                    "hazard ratio is an average over time"
                ),
            )
        )
    return caveats


# --- Readback ---------------------------------------------------------------------------------


def _coding_readback(variable: CanonicalVariable, covariate: Covariate) -> list[Segment]:
    """How a covariate enters the model (D367), as its readback says it."""
    if covariate.kind == "boolean":
        return [text("1 for true against 0 for false")]
    if covariate.kind == "category":
        found: list[Segment] = [
            text("one term per level against the most common level among those with an "),
            text("event"),
        ]
        if covariate.order is not None:
            found.append(text(", its levels in their declared order"))
        return found
    resolved = variable.resolved
    if resolved.function == "count":
        return [text("a number, its hazard ratio per row more")]
    table, _, name = resolved.column.partition(".")
    descriptor = resolved.release.column(table, name)
    units = None if descriptor is None else descriptor.fields.units
    if units is None:
        return [text("a number, its hazard ratio per 1 more")]
    return [text("a number, its hazard ratio per 1 more, in "), data(units)]


def view_readback(
    cohorts: int,
    reference: int,
    endpoint: ResolvedEndpoint,
    variables: Sequence[CanonicalVariable],
    params: CoxParams,
    predicates: Sequence[CanonicalCohort] = (),
) -> list[Segment]:
    """The view's readback (§7.7): a function of its canonical form and the release's
    descriptors, cohorts named by position; ``variables`` are the covariates, then the stratum
    where the view has one, and ``predicates`` the predicate covariates in order (D371)."""
    stratified = params.stratum is not None
    covariates = variables[: len(params.covariates)]
    asks = bool(predicates)
    found: list[Segment] = [
        text("A Cox proportional hazards model, Efron ties, of the endpoint "),
        *survival.endpoint_readback(endpoint),
    ]
    if cohorts > 1:
        found.append(
            text(
                f"; its terms: each other cohort's membership, of the {cohorts} in view order, "
                f"versus the reference, the cohort at position {reference}"
            )
        )
    asked = iter(predicates)
    for index, variable in enumerate(covariates):
        if variable.resolved.kind == "question" and variable.resolved.aggregate is None:
            found += [
                text(f"; covariate {index} (1 where the predicate holds, 0 where it does not): "),
                text("units for which "),
                *conditions(next(asked)),
            ]
            continue
        coded = covariate_of(variable.resolved)
        assert coded is not None, "phase 2 refuses a covariate of no coding"
        found += [text(f"; covariate {index} ("), *_coding_readback(variable, coded)]
        found += [text("): "), *variable_readback(variable)]
    if stratified:
        found += [text("; stratified by the levels of "), *variable_readback(variables[-1])]
    found += [
        text("; Wald intervals at "),
        data(number_text(params.level)),
        text(", each term's Wald p-value, the joint Wald test of each covariate of two or more "),
        text("columns, and the Grambsch–Therneau test of proportional hazards of the terms the "),
        text("fit estimated."),
        text(" Complete cases only: a unit without a valid endpoint row"),
        text(
            ", without a value of every covariate and of the stratum, or for which a predicate "
            "cannot be decided,"
            if asks
            else " or without a value of every covariate and of the stratum"
        ),
        text(
            " is left out and counted by reason; one whose status is not coded, whose time is "
            "negative or whose entry is at or after its time is left out as INVALID_VALUE."
        ),
    ]
    return found


__all__ = [
    "ANALYSIS_ID",
    "CAVEATS",
    "CONE",
    "ENTRY",
    "EVENTS_PER_TERM",
    "MAX_PARAMETERS",
    "METHODS",
    "MIN_EVENTS",
    "MIN_GROUP_N",
    "PH_LEVEL",
    "TAKEN",
    "TEST",
    "VERSION",
    "WALD",
    "Covariate",
    "LongLevel",
    "Member",
    "Model",
    "NonTextLevel",
    "Outcome",
    "TooLarge",
    "TooManyParameters",
    "TooManyStrata",
    "Unscalable",
    "analyse",
    "covariate_of",
    "lifted",
    "listed_variables",
    "members_of",
    "model",
    "view_readback",
]

"""``survival.km``: Kaplan–Meier curves compared across cohorts (SPEC §5.8, §8.4, §9.1, §9.3,
§9.5; D347–D351).

For the view's endpoint (§5.8, ``params.endpoint``, D347) and each cohort in view order, what
happened to its units, and between the cohorts how they differ from the reference:

- **Per cohort**: the Kaplan–Meier curve over left-truncated risk sets, a step at each time at
  which some unit's follow-up ends (or, with a ``grid``, at the grid's times, with the units at
  risk there and the follow-ups that ended since the grid time before), each with its pointwise
  log-log interval; its median with the Brookmeyer–Crowley interval; its value at each landmark
  time with its interval; its events and its last follow-up (``timetoevent``, held to R).
- **Across the cohorts**: the log-rank test over the cohorts with units; and of each other
  position versus the reference, the difference in medians with a bootstrap percentile interval
  (each cohort resampled within itself, ``timetoevent.REPLICATES`` times, seeded from the view's
  computation id, the position and nothing else, so that the same view gives the same replicates
  on every run, D350) and the hazard ratio of a Cox fit of the cohorts with events (Efron's ties,
  its Wald interval), whose proportional hazards the Grambsch–Therneau global test checks.

**Endpoint rows** (§5.8, D347). Each member's time, status and entry are its endpoint's columns,
listed as a pack analysis's inputs are (``engine.inputs``, by SQL or the reference evaluator,
ordered by key). A member whose time, status or entry is not PRESENT is excluded with each of
their reasons; one whose status is neither an event's nor a censoring's value (compared as the
gate compares them: numbers by value, strings and booleans apart), whose time is negative, or whose
entry is at or after its time, is excluded ``INVALID_VALUE``; ``analysed`` counts the rest, as the
units with valid endpoint data (§8.1), and the excluded by reason.

**Estimability** (§9.5), each value's first reason: with ``overlap: "allow"`` and units actually
shared, no between-cohort value (``overlapping_cohorts``); a cohort with no units analysed has no
curve value, median, landmark or last follow-up and no contrast (``no_units``), and the log-rank
test uses the others (``no_units`` with fewer than two); a cohort with no events has no difference
in medians and no hazard ratio (``no_events``), its units left out of the Cox fit but kept in its
curve and the log-rank test, and a reference with none leaves every value of the fit ``no_events``;
a log-rank test whose variance is 0 is ``zero_variance``; a Cox term flagged infinite has no
estimate and interval (``separation``), nor does the test of proportional hazards, and one the fit
cannot identify (no information) has none (``zero_variance``); a hazard ratio or Wald bound beyond
2^53 − 1, or that rounds to 0, is ``separation`` alone, the rest shown (D348); a fit that does not
converge has
no value (``not_converged``); a median, or a bound of it, whose curve does not reach ½ is
``not_reached``, and so is a difference that uses one, and a bootstrap bound whose order
statistic is infinite; a curve at 0 has no pointwise bounds from then on (``zero_denominator``);
and a landmark or grid time after the cohort's last follow-up has no value (``beyond_follow_up``).

**Caveats**: the cohorts' (§8.3), ``UNKNOWN_EXCLUDED`` where a member's endpoint cell is missing
for a reason other than ``NOT_APPLICABLE``, ``INVALID_EXCLUDED`` where one is invalid (§5.8), the
flags of the values read, ``COHORTS_OVERLAP``, ``SMALL_N`` where a cohort with units has fewer than
``MIN_GROUP_N`` units or ``MIN_EVENTS`` events, or the Cox fit fewer than 10 events per term,
``PH_VIOLATED`` where the test of proportional hazards gives p < 0.05, and the view's static
caveats, among them ``UNCONFIRMED_SEMANTICS`` for an undeclared entry (§5.8).

**Range** (§8.2, D348, D349). A unit's time or entry beyond ±(2^53 − 1) refuses the view
(``TooLarge``): every time a result shows, a median and a difference of medians included, is one
of them or lies between them. Every other number is found with C's arithmetic (``ieee``), and
one that is not finite or lies beyond that range is no value, with a reason: a hazard ratio or
bound ``separation``, a test ``zero_variance``, a pointwise bound ``zero_denominator``.

**Disclosure** (§8.4, D351). Under any disclosure setting a view of a survival analysis is refused
(``views.checked``), and applicability calls it ``unavailable`` there (``registry``): a curve's
values can give the censorings between its event times, counts §8.4's grid rule showed while it
protected every count it listed, and a rule that protects them is later work. Without one, every
count is shown.
"""

import hashlib
import math
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import cached_property

from aibi.core.analyses import ieee, timetoevent
from aibi.core.analyses.common import (
    CohortAt,
    caveat,
    cohort_caveats,
    flag_caveats,
    populations,
)
from aibi.core.analyses.stats import z
from aibi.core.engine.inputs import Listed
from aibi.core.engine.resolve import ResolvedEndpoint
from aibi.core.engine.truth import Mark
from aibi.core.engine.worker import CallerDeadline
from aibi.core.schema.analyses import (
    Curve,
    CurveStep,
    Landmark,
    SurvivalMedian,
    SurvivalParams,
    SurvivalPosition,
    SurvivalValues,
    SurvivalView,
)
from aibi.core.schema.caveats import Caveat, CaveatCode
from aibi.core.schema.descriptors import AnalysisDescriptor
from aibi.core.schema.export import params_schema, values_schema
from aibi.core.schema.ids import MAX_SAFE_INTEGER
from aibi.core.schema.jsonio import canonical, number_text
from aibi.core.schema.limits import MAX_COHORTS, MAX_CURVE_STEPS
from aibi.core.schema.numbers import (
    EffectMeasure,
    EffectSize,
    HypothesisTest,
    Interval,
    NotEstimableReason,
)
from aibi.core.schema.output import Segment, data, text
from aibi.core.schema.results import Analysed, Population
from aibi.core.schema.semantics import ExclusionReason

ANALYSIS_ID = "survival.km"
VERSION = "1.0.0"
"""Bumped whenever its outputs for the same inputs change (§7.6)."""
MIN_GROUP_N = 10
MIN_EVENTS = 5
EVENTS_PER_TERM = 10
"""Below this many events per term of the Cox fit, its hazard ratios carry ``SMALL_N`` (§8.3)."""
PH_LEVEL = 0.05
"""Below this p-value of the test of proportional hazards, the result carries ``PH_VIOLATED``."""
METHODS: Mapping[str, str] = {
    "kaplan_meier": "The Kaplan-Meier estimate over left-truncated risk sets (entry < t <= "
    "time), as R's survfit(Surv(entry, time, status)), implemented directly",
    "log_log": "The pointwise log-log interval of a curve's value from Greenwood's variance, as "
    'survfit(conf.type = "log-log"), not survfit\'s default "log"; 1 and 1 where the curve is 1',
    "brookmeyer_crowley": "The median of the curve and the medians of its pointwise bounds, as "
    "R's quantile.survfit finds them (its tolerance sqrt(.Machine$double.eps))",
    "log_rank": "The Mantel-Haenszel log-rank test over left-truncated risk sets, as R's "
    'survdiff, the score test of coxph(ties = "exact") with delayed entry',
    "bootstrap_percentile": "The percentile interval of the difference in medians over 2000 "
    "bootstrap replicates, each cohort resampled within itself, a median not reached counting "
    "as +Inf, seeded from the computation id",
    "wald": 'The hazard ratio of a Cox fit of the cohorts with events, coxph(ties = "efron") '
    "versus the reference, with its Wald interval, implemented directly",
    "grambsch_therneau": "The global test of proportional hazards, as R's cox.zph(transform = "
    '"km"), implemented directly',
}
CAVEATS = (
    CaveatCode.UNKNOWN_EXCLUDED,
    CaveatCode.INVALID_EXCLUDED,
    CaveatCode.SCOPE_PARTIAL,
    CaveatCode.COVERAGE_PROPOSED,
    CaveatCode.UNCONFIRMED_SEMANTICS,
    CaveatCode.COHORTS_OVERLAP,
    CaveatCode.SMALL_N,
    CaveatCode.PH_VIOLATED,
    CaveatCode.DRAFT_RELEASE,
    CaveatCode.NOT_ESTIMABLE,
    CaveatCode.LIFT_DIFFERS,
)
ENTRY = AnalysisDescriptor.model_validate(
    {
        "kind": "analysis",
        "id": ANALYSIS_ID,
        "version": VERSION,
        "label": "Kaplan–Meier survival",
        "definition": (
            "Kaplan–Meier estimate per cohort over left-truncated risk sets, with log-log "
            "pointwise intervals; median with its Brookmeyer–Crowley interval and landmark "
            "survival with intervals; the log-rank test; difference in medians (bootstrap "
            "interval, 2000 replicates) and unadjusted hazard ratio (Cox, Efron ties, Wald "
            "interval, tested for proportional hazards) versus the reference cohort."
        ),
        "fields": {
            "requires": [
                {"role": "endpoint", "kind": "endpoint", "on": "unit"},
                {"role": "cohorts", "min": 1, "max": MAX_COHORTS},
            ],
            "params": params_schema(SurvivalParams),
            "returns": values_schema(SurvivalValues),
            "methods": dict(METHODS),
            "assumptions": ["independent censoring", "independent groups"],
            "uses_reference": True,
            "assumes_independent_groups": True,
            "cross_dataset": None,
            "randomness": {"seeded": True, "replicates": timetoevent.REPLICATES},
            "caveats": [code.value for code in CAVEATS],
            "min_group_n": MIN_GROUP_N,
            "min_events": MIN_EVENTS,
        },
    }
)
"""The registry entry (§9.1): implemented directly, with no library."""

_OVERLAP = NotEstimableReason.OVERLAPPING_COHORTS
_NO_UNITS = NotEstimableReason.NO_UNITS
_NO_EVENTS = NotEstimableReason.NO_EVENTS
_NOT_REACHED = NotEstimableReason.NOT_REACHED
_BEYOND = NotEstimableReason.BEYOND_FOLLOW_UP
_ZERO_DENOMINATOR = NotEstimableReason.ZERO_DENOMINATOR
_ZERO_VARIANCE = NotEstimableReason.ZERO_VARIANCE
_BOUNDS = ("/estimate", "/ci/low", "/ci/high")


class TooManySteps(ValueError):  # noqa: N818 - raised like a limit's refusal
    """A cohort's curve with more than ``MAX_CURVE_STEPS`` steps, in a view without a grid."""

    def __init__(self, steps: int) -> None:
        super().__init__(f"{steps} steps")
        self.steps = steps


class TooLarge(ValueError):  # noqa: N818 - raised like a limit's refusal
    """A unit's time or entry beyond ±(2^53 − 1), which an output does not hold (§8.2): every
    time a result shows, a median and a difference of medians included, is one of them or lies
    between them."""


def seed(computation: str, position: int) -> int:
    """The seed of a position's bootstrap (module docstring): the SHA-256 of the RFC 8785 text of
    ``{"computation", "position"}``."""
    hashed = canonical({"computation": computation, "position": position})
    return int.from_bytes(hashlib.sha256(hashed).digest(), "big")


def _deadline(ends: float | None, index: int = 0) -> None:
    """Raise ``CallerDeadline`` at every ``timetoevent.LOOK_EVERY``-th ``index`` once
    ``time.monotonic()`` has passed ``ends``."""
    if ends is not None and not index % timetoevent.LOOK_EVERY and time.monotonic() >= ends:
        raise CallerDeadline


# --- Endpoint rows (D347) ---------------------------------------------------------------------


def _coded(value: object) -> tuple[str, object] | None:
    """A status value as event codes compare it: numbers by value, strings and booleans apart,
    as the gate compares them (§5.8)."""
    if isinstance(value, bool):
        return ("boolean", value)
    if isinstance(value, int | float):
        return ("number", ieee.real(value))
    if isinstance(value, str):
        return ("string", value)
    return None


@dataclass(frozen=True)
class Rows:
    """A position's endpoint rows (module docstring): the members analysed, each its entry
    (``timetoevent.ORIGIN`` at the origin), time and whether it ended in an event, and those
    excluded, by reason and in all."""

    subjects: tuple[timetoevent.Subject, ...]
    excluded: Mapping[ExclusionReason, int]
    excluded_units: int
    marks: frozenset[Mark]

    @cached_property
    def events(self) -> int:
        return sum(event for _, _, event in self.subjects)

    def analysed(self) -> Analysed:
        return Analysed(
            n=len(self.subjects), excluded=dict(self.excluded), excluded_units=self.excluded_units
        )


@dataclass(frozen=True)
class Cells:
    """Each member's endpoint row, in the listing's order (D347, D352): its subject, or ``None``
    where it is excluded, with its reasons (empty where it has a row), and the flags of the
    values read."""

    subjects: tuple[timetoevent.Subject | None, ...]
    reasons: tuple[frozenset[ExclusionReason], ...]
    marks: frozenset[Mark]


_INVALID = frozenset({ExclusionReason.INVALID_VALUE})


def endpoint_cells(
    endpoint: ResolvedEndpoint, listed: Listed, offset: int = 0, ends: float | None = None
) -> Cells:
    """Each member's endpoint row from a listing whose variables from ``offset`` on are the
    endpoint's (``ResolvedEndpoint.variables``: time, status, then entry if it has one): a member
    with a cell not PRESENT is excluded under each of their reasons, one whose status is not
    coded, whose time is negative, or whose entry is not before its time (which a time or an
    entry that is not a number never is) ``INVALID_VALUE`` (§5.8, module docstring); the call's
    deadline ``ends`` looked at every ``timetoevent.LOOK_EVERY`` members."""
    event = {_coded(value) for value in endpoint.event}
    censored = {_coded(value) for value in endpoint.censored}
    width = len(endpoint.variables)
    values = listed.values[offset : offset + width]
    reasons_of = listed.excluded[offset : offset + width]
    marks = frozenset(mark for found in listed.marks[offset : offset + width] for mark in found)
    subjects: list[timetoevent.Subject | None] = []
    reasons: list[frozenset[ExclusionReason]] = []
    for member in range(listed.members):
        _deadline(ends, member)
        given = frozenset(reason for found in reasons_of for reason in found[member])
        if given:
            subjects.append(None)
            reasons.append(given)
            continue
        ended = values[0][member]
        status = _coded(values[1][member])
        begun = values[2][member] if width == 3 else None
        assert isinstance(ended, int | float)
        assert not isinstance(ended, bool)
        when = ieee.real(ended)
        entry = timetoevent.ORIGIN
        if begun is not None:
            assert isinstance(begun, int | float)
            assert not isinstance(begun, bool)
            entry = ieee.real(begun)
        if (status not in event and status not in censored) or when < 0 or not entry < when:
            subjects.append(None)
            reasons.append(_INVALID)
            continue
        subjects.append((entry, when, status in event))
        reasons.append(frozenset())
    return Cells(tuple(subjects), tuple(reasons), marks)


def endpoint_rows(
    endpoint: ResolvedEndpoint, listed: Listed, offset: int = 0, ends: float | None = None
) -> Rows:
    """A position's endpoint rows (``endpoint_cells``): the members analysed, and those excluded
    by reason and in all; the call's deadline ``ends`` looked at every ``timetoevent.LOOK_EVERY``
    members."""
    cells = endpoint_cells(endpoint, listed, offset, ends)
    excluded = dict.fromkeys(ExclusionReason, 0)
    excluded_units = 0
    for member, given in enumerate(cells.reasons):
        _deadline(ends, member)
        if given:
            excluded_units += 1
            for reason in given:
                excluded[reason] += 1
    _deadline(ends)
    subjects = tuple(subject for subject in cells.subjects if subject is not None)
    return Rows(subjects, excluded, excluded_units, cells.marks)


# --- Per cohort ---------------------------------------------------------------------------------


def _nothing(reason: NotEstimableReason, *members: str) -> dict[str, NotEstimableReason]:
    return dict.fromkeys(members, reason)


def _within(rows: Sequence[Rows], ends: float | None = None) -> None:
    """Raise ``TooLarge`` for a unit's time or entry beyond ±(2^53 − 1) (§8.2, D348); an entry
    at the origin is none. The call's deadline ``ends`` is looked at every
    ``timetoevent.LOOK_EVERY`` units."""
    for one in rows:
        for index, (entry, when, _) in enumerate(one.subjects):
            _deadline(ends, index)
            if when > MAX_SAFE_INTEGER or MAX_SAFE_INTEGER < abs(entry) < math.inf:
                raise TooLarge


def _bounded(
    low: float | None, high: float | None, level: float, method: str
) -> tuple[Interval, dict[str, NotEstimableReason]]:
    reasons = {} if low is not None else _nothing(_ZERO_DENOMINATOR, "/ci/low", "/ci/high")
    return Interval(method=method, level=level, low=low, high=high), reasons


def _grid_steps(
    subjects: Sequence[timetoevent.Subject],
    steps: Sequence[timetoevent.Step],
    times: Sequence[float],
    grid: Sequence[float],
    level: float,
    ends: float | None,
) -> list[CurveStep]:
    found: list[CurveStep] = []
    counts = timetoevent.grid_counts(subjects, grid, ends)
    for point, counted in zip(grid, counts, strict=True):
        at = timetoevent.survival_at(steps, point, times)
        if at is None:
            reason = _NO_UNITS if not subjects else _BEYOND
            found.append(
                CurveStep(
                    time=point,
                    at_risk=counted.at_risk,
                    events=counted.events,
                    censored=counted.censored,
                    survival=None,
                    ci=Interval(method="log_log", level=level, low=None, high=None),
                    not_estimable=_nothing(reason, "/survival", "/ci/low", "/ci/high"),
                )
            )
            continue
        survival, low, high = at
        ci, reasons = _bounded(low, high, level, "log_log")
        found.append(
            CurveStep(
                time=point,
                at_risk=counted.at_risk,
                events=counted.events,
                censored=counted.censored,
                survival=survival,
                ci=ci,
                not_estimable=reasons or None,
            )
        )
    return found


def _curve(
    subjects: Sequence[timetoevent.Subject],
    steps: Sequence[timetoevent.Step],
    times: Sequence[float],
    params: SurvivalParams,
    ends: float | None,
) -> Curve:
    level = params.level
    if params.grid is not None:
        found = _grid_steps(subjects, steps, times, params.grid, level, ends)
        return Curve(times_from="grid", steps=found)
    if len(steps) > MAX_CURVE_STEPS:
        raise TooManySteps(len(steps))
    found: list[CurveStep] = []
    for step in steps:
        ci, reasons = _bounded(step.low, step.high, level, "log_log")
        found.append(
            CurveStep(
                time=step.time,
                at_risk=step.at_risk,
                events=step.events,
                censored=step.censored,
                survival=step.survival,
                ci=ci,
                not_estimable=reasons or None,
            )
        )
    return Curve(times_from="data", steps=found)


def _median(steps: Sequence[timetoevent.Step], level: float, ends: float | None) -> SurvivalMedian:
    interval = Interval(method="brookmeyer_crowley", level=level, low=None, high=None)
    if not steps:
        return SurvivalMedian(
            estimate=None, ci=interval, not_estimable=_nothing(_NO_UNITS, *_BOUNDS)
        )
    estimate, low, high = timetoevent.median(steps, ends)
    reasons = {
        member: _NOT_REACHED
        for member, value in zip(_BOUNDS, (estimate, low, high), strict=True)
        if value is None
    }
    return SurvivalMedian(
        estimate=estimate,
        ci=Interval(method="brookmeyer_crowley", level=level, low=low, high=high),
        not_estimable=reasons or None,
    )


def _landmarks(
    steps: Sequence[timetoevent.Step], times: Sequence[float], params: SurvivalParams
) -> list[Landmark]:
    found: list[Landmark] = []
    for point in params.landmarks or []:
        at = timetoevent.survival_at(steps, point, times)
        if at is None:
            found.append(
                Landmark(
                    time=point,
                    estimate=None,
                    ci=Interval(method="log_log", level=params.level, low=None, high=None),
                    not_estimable=_nothing(_NO_UNITS if not steps else _BEYOND, *_BOUNDS),
                )
            )
            continue
        survival, low, high = at
        ci, reasons = _bounded(low, high, params.level, "log_log")
        found.append(Landmark(time=point, estimate=survival, ci=ci, not_estimable=reasons or None))
    return found


def _position(
    rows: Rows, steps: Sequence[timetoevent.Step], params: SurvivalParams, ends: float | None
) -> SurvivalPosition:
    """A position's values from its curve's steps, the call's deadline ``ends`` looked at
    before each of them and within the methods that pass over the rows or the steps."""
    _deadline(ends)
    last = steps[-1].time if steps else None
    times = [step.time for step in steps]
    _deadline(ends)
    curve = _curve(rows.subjects, steps, times, params, ends)
    _deadline(ends)
    median = _median(steps, params.level, ends)
    _deadline(ends)
    return SurvivalPosition(
        events=rows.events,
        last_follow_up=last,
        curve=curve,
        median=median,
        landmarks=_landmarks(steps, times, params),
        not_estimable=None if last is not None else {"/last_follow_up": _NO_UNITS},
    )


# --- Across cohorts ---------------------------------------------------------------------------


def _not_computed(
    method: str, positions: Sequence[int], reason: NotEstimableReason
) -> HypothesisTest:
    return HypothesisTest(
        method=method,
        positions=list(positions),
        statistic=None,
        p=None,
        not_estimable=_nothing(reason, "/statistic", "/p"),
    )


def _writable(*numbers: float | None) -> bool:
    """Whether a test's statistic and p-value are numbers an output holds (§8.2): each finite
    and within ±(2^53 − 1). A statistic beyond that comes only of a variance too small for it,
    and the test is ``zero_variance``, as with a variance of 0 (D348)."""
    return all(
        number is not None and math.isfinite(number) and abs(number) <= MAX_SAFE_INTEGER
        for number in numbers
    )


def _log_rank(
    rows: Sequence[Rows], overlap: bool, ends: float | None
) -> tuple[HypothesisTest, list[int]]:
    """The log-rank test over the positions with units, and the positions it used."""
    if overlap:
        return _not_computed("log_rank", [], _OVERLAP), []
    with_units = [position for position, one in enumerate(rows) if one.subjects]
    if len(with_units) < 2:
        return _not_computed("log_rank", with_units, _NO_UNITS), with_units
    table = timetoevent.risk_table([rows[position].subjects for position in with_units], ends=ends)
    found = timetoevent.log_rank(table, len(with_units), ends)
    used = [with_units[group] for group in found.used]
    if not _writable(found.statistic, found.p):
        return _not_computed("log_rank", with_units, _ZERO_VARIANCE), with_units
    return (
        HypothesisTest(
            method="log_rank", positions=used, statistic=found.statistic, df=found.df, p=found.p
        ),
        used,
    )


@dataclass(frozen=True)
class _Fit:
    """The Cox fit of the positions with events, versus the reference: the positions it used,
    in view order, their risk table, the fit, and each other position's term."""

    positions: list[int]
    table: list[list[timetoevent.EventTime]]
    fit: timetoevent.CoxFit
    terms: dict[int, int]


def _cox(rows: Sequence[Rows], reference: int, ends: float | None) -> _Fit | NotEstimableReason:
    """The Cox fit, or why it has no value at all: a reference with no units or no events."""
    if not rows[reference].subjects:
        return _NO_UNITS
    if not rows[reference].events:
        return _NO_EVENTS
    used = [position for position, one in enumerate(rows) if one.events]
    if len(used) < 2:
        return _NO_EVENTS
    table = timetoevent.risk_table([rows[position].subjects for position in used], ends=ends)
    fitted = timetoevent.cox(table, used.index(reference), ends)
    others = [position for position in used if position != reference]
    return _Fit(used, table, fitted, {position: index for index, position in enumerate(others)})


def _hazard_ratio(
    rows: Sequence[Rows],
    fitted: _Fit | NotEstimableReason,
    position: int,
    reference: int,
    level: float,
    overlap: bool,
) -> EffectSize:
    interval = Interval(method="wald", level=level, low=None, high=None)

    def missing(reason: NotEstimableReason) -> EffectSize:
        return EffectSize(
            measure=EffectMeasure.HAZARD_RATIO,
            position=position,
            versus=reference,
            estimate=None,
            ci=interval,
            not_estimable=_nothing(reason, *_BOUNDS),
        )

    if overlap:
        return missing(_OVERLAP)
    if not rows[position].subjects:
        return missing(_NO_UNITS)
    if isinstance(fitted, NotEstimableReason):
        return missing(fitted)
    if not rows[position].events:
        return missing(_NO_EVENTS)
    fit = fitted.fit
    term = fitted.terms[position]
    if not fit.converged:
        return missing(NotEstimableReason.NOT_CONVERGED)
    if fit.singular[term]:
        return missing(_ZERO_VARIANCE)
    if fit.infinite[term]:
        return missing(NotEstimableReason.SEPARATION)
    coefficient = fit.coefficients[term]
    spread = z(level) * ieee.sqrt(fit.variance[term][term])
    found = [ieee.exp(value) for value in (coefficient, coefficient - spread, coefficient + spread)]
    shown = [value if 0 < value <= MAX_SAFE_INTEGER else None for value in found]
    ratio, low, high = shown
    reasons = {
        member: NotEstimableReason.SEPARATION
        for member, value in zip(_BOUNDS, shown, strict=True)
        if value is None
    }
    return EffectSize(
        measure=EffectMeasure.HAZARD_RATIO,
        position=position,
        versus=reference,
        estimate=ratio,
        ci=Interval(method="wald", level=level, low=low, high=high),
        not_estimable=reasons or None,
    )


def _proportional_hazards(
    rows: Sequence[Rows],
    fitted: _Fit | NotEstimableReason,
    reference: int,
    overlap: bool,
    ends: float | None,
) -> HypothesisTest:
    method = "grambsch_therneau"
    if overlap:
        return _not_computed(method, [], _OVERLAP)
    if isinstance(fitted, NotEstimableReason):
        return _not_computed(method, [], fitted)
    fit = fitted.fit
    if not fit.converged:
        return _not_computed(method, fitted.positions, NotEstimableReason.NOT_CONVERGED)
    if any(fit.infinite):
        return _not_computed(method, fitted.positions, NotEstimableReason.SEPARATION)
    found = timetoevent.proportional_hazards(
        [rows[position].subjects for position in fitted.positions],
        fitted.table,
        fit,
        fitted.positions.index(reference),
        ends,
    )
    if found is None or not _writable(found.statistic, found.p):
        return _not_computed(method, fitted.positions, _ZERO_VARIANCE)
    return HypothesisTest(
        method=method,
        positions=fitted.positions,
        statistic=found.statistic,
        df=found.df,
        p=found.p,
    )


def _median_difference(
    rows: Sequence[Rows],
    medians: Sequence[SurvivalMedian],
    position: int,
    reference: int,
    level: float,
    overlap: bool,
    replicates: "_Replicates",
) -> EffectSize:
    interval = Interval(method="bootstrap_percentile", level=level, low=None, high=None)
    reason: NotEstimableReason | None = None
    if overlap:
        reason = _OVERLAP
    elif not rows[position].subjects or not rows[reference].subjects:
        reason = _NO_UNITS
    elif not rows[position].events or not rows[reference].events:
        reason = _NO_EVENTS
    mine, theirs = medians[position].estimate, medians[reference].estimate
    if reason is None and (mine is None or theirs is None):
        reason = _NOT_REACHED
    if reason is not None:
        return EffectSize(
            measure=EffectMeasure.MEDIAN_DIFFERENCE,
            position=position,
            versus=reference,
            estimate=None,
            ci=interval,
            not_estimable=_nothing(reason, *_BOUNDS),
        )
    assert mine is not None
    assert theirs is not None
    low, high = timetoevent.median_interval(replicates(position), replicates(reference), level)
    estimate = mine - theirs
    reasons = {
        member: _NOT_REACHED
        for member, value in (("/ci/low", low), ("/ci/high", high))
        if value is None
    }
    return EffectSize(
        measure=EffectMeasure.MEDIAN_DIFFERENCE,
        position=position,
        versus=reference,
        estimate=estimate,
        ci=Interval(method="bootstrap_percentile", level=level, low=low, high=high),
        not_estimable=reasons or None,
    )


class _Replicates:
    """Each position's bootstrap medians, drawn once and shared by every effect that uses them."""

    def __init__(self, rows: Sequence[Rows], computation: str, ends: float | None) -> None:
        self.rows = rows
        self.computation = computation
        self.ends = ends
        self.found: dict[int, list[float]] = {}

    def __call__(self, position: int) -> list[float]:
        if position not in self.found:
            self.found[position] = timetoevent.bootstrap_medians(
                self.rows[position].subjects, seed(self.computation, position), ends=self.ends
            )
        return self.found[position]


# --- The analysis -------------------------------------------------------------------------------


@dataclass(frozen=True)
class Outcome:
    """A view's digested parts, before its digest is taken, and its caveats (D348)."""

    population: list[Population]
    analysed: list[Analysed]
    values: SurvivalValues
    caveats: list[Caveat]


def survive(
    positions: Sequence[CohortAt],
    rows: Sequence[Rows],
    params: SurvivalParams,
    *,
    reference: int,
    overlap: bool,
    computation: str,
    ends: float | None = None,
) -> Outcome:
    """``survival.km`` over a view's cohorts (module docstring), from each position's endpoint
    rows (``endpoint_rows``); ``overlap`` says that the view allows overlap and its cohorts share
    units, and ``computation`` is the view's computation id, which seeds the bootstrap. Raises
    ``TooManySteps``, ``TooLarge``, and ``CallerDeadline`` when ``time.monotonic()`` has passed
    ``ends``, looked at before each step of the analysis and each bootstrap replicate and within
    every method every ``timetoevent.LOOK_EVERY`` times (D350)."""
    if len(rows) != len(positions):
        raise ValueError("endpoint rows per position")
    _within(rows, ends)
    _deadline(ends)
    population = populations(positions, None)
    curves: list[list[timetoevent.Step]] = []
    at_positions: list[SurvivalPosition] = []
    for one in rows:
        _deadline(ends)
        steps = timetoevent.kaplan_meier(one.subjects, params.level, ends) if one.subjects else []
        curves.append(steps)
        at_positions.append(_position(one, steps, params, ends))
    view = SurvivalView(effects=[])
    small_terms = False
    violated = False
    if len(rows) > 1:
        test, _ = _log_rank(rows, overlap, ends)
        fitted = _NO_UNITS if overlap else _cox(rows, reference, ends)
        replicates = _Replicates(rows, computation, ends)
        medians = [position.median for position in at_positions]
        effects: list[EffectSize] = []
        for position in range(len(rows)):
            if position == reference:
                continue
            effects.append(
                _median_difference(
                    rows, medians, position, reference, params.level, overlap, replicates
                )
            )
            effects.append(_hazard_ratio(rows, fitted, position, reference, params.level, overlap))
        _deadline(ends)
        hazards = _proportional_hazards(rows, fitted, reference, overlap, ends)
        view = SurvivalView(test=test, effects=effects, proportional_hazards=hazards)
        violated = hazards.p is not None and hazards.p < PH_LEVEL
        if isinstance(fitted, _Fit) and fitted.terms:
            events = sum(rows[position].events for position in fitted.positions)
            small_terms = events < EVENTS_PER_TERM * len(fitted.terms)
    values = SurvivalValues(positions=at_positions, view=view)
    analysed = [one.analysed() for one in rows]
    caveats = _caveats(positions, population, rows, overlap, small_terms, violated)
    return Outcome(population, analysed, values, caveats)


def _caveats(
    positions: Sequence[CohortAt],
    population: Sequence[Population],
    rows: Sequence[Rows],
    overlap: bool,
    small_terms: bool,
    violated: bool,
) -> list[Caveat]:
    """The caveats that the view's data raise (module docstring); the static ones are the
    view's."""
    unknown = any(
        count
        for one in rows
        for reason, count in one.excluded.items()
        if reason not in (ExclusionReason.NOT_APPLICABLE, ExclusionReason.INVALID_VALUE)
    )
    found = cohort_caveats(positions, population, None, values_unknown=unknown)
    found += flag_caveats((mark for one in rows for mark in one.marks), "/values")
    if any(one.excluded[ExclusionReason.INVALID_VALUE] for one in rows):
        found.append(
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
    if overlap:
        found.append(
            caveat(
                CaveatCode.COHORTS_OVERLAP,
                ["/values"],
                text(
                    "Cohorts of the view share units, so no between-cohort value was computed; "
                    "compare a subset with the rest of its base instead (§7.4)"
                ),
            )
        )
    small = [
        f"/values/positions/{position}"
        for position, one in enumerate(rows)
        if one.subjects and (len(one.subjects) < MIN_GROUP_N or one.events < MIN_EVENTS)
    ]
    if small:
        found.append(
            caveat(
                CaveatCode.SMALL_N,
                small,
                text(
                    f"A cohort has fewer than {MIN_GROUP_N} units analysed or fewer than "
                    f"{MIN_EVENTS} events"
                ),
            )
        )
    if small_terms:
        found.append(
            caveat(
                CaveatCode.SMALL_N,
                ["/values/view/effects"],
                text(f"The Cox fit has fewer than {EVENTS_PER_TERM} events per term"),
            )
        )
    if violated:
        found.append(
            caveat(
                CaveatCode.PH_VIOLATED,
                ["/values/view/effects", "/values/view/proportional_hazards"],
                text(
                    f"The test of proportional hazards gives p < {number_text(PH_LEVEL)}: each "
                    "hazard ratio is an average over time"
                ),
            )
        )
    return found


# --- Readback ---------------------------------------------------------------------------------


def view_readback(
    cohorts: int, reference: int, endpoint: ResolvedEndpoint, params: SurvivalParams
) -> list[Segment]:
    """The view's readback (§7.7): a function of its canonical form and the release's
    descriptors, cohorts named by position."""
    level = data(number_text(params.level))
    units: list[Segment] = [] if endpoint.units is None else [text(" in "), data(endpoint.units)]
    found: list[Segment] = [
        text("Kaplan–Meier survival of the endpoint "),
        data(endpoint.endpoint),
        text(": the time "),
        data(endpoint.time.column),
        *units,
        text(", the status "),
        data(endpoint.status.column),
        text(" ("),
        *_coding(endpoint.event),
        text(" an event, "),
        *_coding(endpoint.censored),
        text(" censored)"),
    ]
    if endpoint.entry is None:
        found.append(text(", every unit entering at the origin"))
    else:
        found += [text(", each unit entering at "), data(endpoint.entry.column)]
    found.append(text(". For each cohort, the curve"))
    if params.grid is None:
        found.append(text(" at each time a unit's follow-up ends"))
    else:
        found += [text(" at the times "), *_times(params.grid)]
    found.append(text(", its median with the Brookmeyer–Crowley interval"))
    if params.landmarks:
        found += [text(" and its value at the times "), *_times(params.landmarks)]
    if cohorts > 1:
        found += [
            text(f"; across the {cohorts} cohorts in view order, the reference being the cohort "),
            text(f"at position {reference}: the log-rank test, and the difference in medians "),
            text(f"(bootstrap percentile interval of {timetoevent.REPLICATES} replicates) and "),
            text("the hazard ratio (Cox fit, Efron ties, Wald interval, tested for "),
            text("proportional hazards) of each other cohort versus the reference"),
        ]
    found += [text("; intervals at "), level, text(".")]
    found.append(
        text(
            " A unit whose time, status or entry is missing is left out and counted by reason; "
            "one whose status is not coded, whose time is negative or whose entry is at or after "
            "its time is left out as INVALID_VALUE."
        )
    )
    return found


def _coding(values: Sequence[str | bool | int | float]) -> list[Segment]:
    found: list[Segment] = []
    for index, value in enumerate(values):
        if index:
            found.append(text(" or "))
        found.append(data(canonical(value).decode()))
    return found or [text("no value")]


def _times(values: Sequence[float]) -> list[Segment]:
    found: list[Segment] = []
    for index, value in enumerate(values):
        if index:
            found.append(text(", "))
        found.append(data(number_text(value)))
    return found


__all__ = [
    "ANALYSIS_ID",
    "CAVEATS",
    "ENTRY",
    "METHODS",
    "MIN_EVENTS",
    "MIN_GROUP_N",
    "VERSION",
    "Cells",
    "Outcome",
    "Rows",
    "TooLarge",
    "TooManySteps",
    "endpoint_cells",
    "endpoint_rows",
    "seed",
    "survive",
    "view_readback",
]

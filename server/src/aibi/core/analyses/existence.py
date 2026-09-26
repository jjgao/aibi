"""``compare.existence``: how often a condition holds, across cohorts (SPEC §9.5; D319, D320).

For each predicate of the view (``params.predicates``, clauses asked of every unit of the unit
table) and each cohort in view order, the proportion of the cohort's units for which the
predicate is TRUE among those for which it is known, with its Wilson interval. A unit for which
it is UNKNOWN is left out of that proportion and counted in its ``excluded``, under each of its
reasons; NOT_APPLICABLE cells make value predicates FALSE (§6.4), so they are known. Between the
cohorts: per predicate, Fisher's exact test when the test uses two cohorts and the chi-squared
test when it uses more, over the cohorts that have units for which the predicate is known; the
risk difference (Newcombe's hybrid score interval) and the risk ratio (Katz log interval) of
every other position versus the reference; and Benjamini–Hochberg q-values over the view's
tests. Methods are ``stats``'.

**Accounting** (§8.1). ``analysed`` counts, per position, the cohort's units for which at least
one predicate is known (``n``) and those for which none is (``excluded_units``, and
``excluded`` under every reason any predicate gave them); with two or more predicates,
``variables`` gives each predicate's, which are its proportion's denominator and exclusions.
The counts come from the crossing of the view's cohorts with its predicates, integer counts
made by SQL (``engine.sql.compile_crossing``) or, for the reference evaluator, by ``cross``.
``lift_differs`` (in ``values``) counts, per position and predicate, the cohort's units whose
answer the other lift rule would change (§6.5, D323).

**Estimability** (§9.5), each value's reason the first that applies: with ``overlap: "allow"``
and units actually shared, no between-cohort value is computed (``overlapping_cohorts``,
``COHORTS_OVERLAP``); a cohort with no unit for which the predicate is known has no proportion
and no contrast (``no_units``), and the test uses the others, not estimable (``no_units``) with
fewer than two; a table whose TRUE or FALSE column is all zeros after that has no test
(``degenerate_table``); a risk ratio with a numerator of 0 on either side is not estimable
(``no_events``). A chi-squared test with an expected count below 5 carries ``SMALL_N``.

**Disclosure** (§8.4, D320). Under *k*, each position's population is disclosed as its cohort's
count is (``engine.suppression``), and each predicate's split of the cohort's units as a cohort
count over them is (``disclosure``): a proportion shows its numerator where the split's TRUE units
are shown, and its denominator and ``excluded_units`` where its UNKNOWN units are. With two
predicates or more, ``analysed``'s ``n``, ``excluded_units`` and ``excluded`` are never shown,
since they combine predicates; each predicate's entry of ``variables`` is shown as its split is.
An ``excluded`` map is ``null`` with its total or when any of its counts is small, and wherever
one map is ``null`` every map of the same units is; every ``lift_differs`` is
suppressed; a proportion's estimate and interval go with its counts, an effect with either
proportion's, and a test with any count of any position, leaving the family, which says how many
left it. Caveat messages quote no count but ``LIFT_DIFFERS``', which quotes none under *k*; and
under *k*, ``UNKNOWN_EXCLUDED`` is carried wherever an UNKNOWN count is suppressed, so that it says
nothing of one.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import cast

from aibi.core.analyses import stats
from aibi.core.analyses.disclosure import Hidden, Split, hidden, small
from aibi.core.engine.canonical import CanonicalCohort
from aibi.core.engine.counts import Tally, count_parts
from aibi.core.engine.ids import leaf_key
from aibi.core.engine.readback import conditions
from aibi.core.engine.sql import Crossed, Crossing
from aibi.core.engine.suppression import disclosed
from aibi.core.engine.truth import Mark
from aibi.core.schema.analyses import (
    ExistenceParams,
    ExistencePosition,
    ExistenceValues,
    ExistenceView,
    Family,
    PredicateContrast,
    PredicateShare,
)
from aibi.core.schema.caveats import CORE_SEVERITIES, Caveat, CaveatCode
from aibi.core.schema.descriptors import AnalysisDescriptor
from aibi.core.schema.export import params_schema, values_schema
from aibi.core.schema.jsonio import number_text
from aibi.core.schema.limits import MAX_COHORTS
from aibi.core.schema.numbers import (
    DenominatorDefinition,
    EffectMeasure,
    EffectSize,
    HypothesisTest,
    Interval,
    NotEstimableReason,
    Proportion,
)
from aibi.core.schema.output import Segment, data, text
from aibi.core.schema.results import Analysed, AnalysedVariable, Population
from aibi.core.schema.semantics import ExclusionReason, Flag, Reason

ANALYSIS_ID = "compare.existence"
VERSION = "1.0.0"
"""Bumped whenever its outputs for the same inputs change (§7.6)."""
METHODS: Mapping[str, str] = {
    "wilson": "Wilson score interval without continuity correction, as R's prop.test(correct "
    "= FALSE) computes it",
    "newcombe_hybrid_score": "Newcombe's hybrid score interval for a difference of "
    "proportions, method 10 of Newcombe (1998), from the two Wilson intervals",
    "katz_log": "Katz log interval for a ratio of proportions",
    "fisher_exact": "Fisher's exact test of a 2x2 table, two-sided as R's fisher.test, "
    "implemented directly",
    "chi_squared": "Pearson's chi-squared test of independence without continuity "
    "correction, as R's chisq.test(correct = FALSE)",
    "benjamini_hochberg": "Benjamini-Hochberg q-values over the view's tests, as R's "
    'p.adjust(method = "BH")',
}
CAVEATS = (
    CaveatCode.UNKNOWN_EXCLUDED,
    CaveatCode.SCOPE_PARTIAL,
    CaveatCode.COVERAGE_PROPOSED,
    CaveatCode.UNCONFIRMED_SEMANTICS,
    CaveatCode.COHORTS_OVERLAP,
    CaveatCode.SMALL_N,
    CaveatCode.DRAFT_RELEASE,
    CaveatCode.SUPPRESSED,
    CaveatCode.NOT_ESTIMABLE,
    CaveatCode.LIFT_DIFFERS,
)
ENTRY = AnalysisDescriptor.model_validate(
    {
        "kind": "analysis",
        "id": ANALYSIS_ID,
        "version": VERSION,
        "label": "Existence of a condition, compared across cohorts",
        "definition": (
            "For each predicate, the proportion of each cohort's units for which it holds "
            "among those for which it is known, with a Wilson interval; versus the reference "
            "cohort, the risk difference (Newcombe hybrid score interval) and the risk ratio "
            "(Katz log interval); Fisher's exact test for two cohorts, chi-squared for more, "
            "with Benjamini-Hochberg q-values over the predicates."
        ),
        "fields": {
            "requires": [{"role": "cohorts", "min": 1, "max": MAX_COHORTS}],
            "params": params_schema(ExistenceParams),
            "returns": values_schema(ExistenceValues),
            "methods": dict(METHODS),
            "assumptions": ["units independent of one another", "independent groups"],
            "uses_reference": True,
            "assumes_independent_groups": True,
            "cross_dataset": None,
            "caveats": [code.value for code in CAVEATS],
        },
    }
)
"""The registry entry (§9.1): implemented directly, with no library."""

_SUPPRESSED = NotEstimableReason.SUPPRESSED
_FLAGS = {
    Flag.SCOPE_PARTIAL: CaveatCode.SCOPE_PARTIAL,
    Flag.COVERAGE_PROPOSED: CaveatCode.COVERAGE_PROPOSED,
}
_FLAG_TEXT = {
    Flag.SCOPE_PARTIAL: "Some answers cover only the scope values listed as assessed, for ",
    Flag.COVERAGE_PROPOSED: "Some answers rely on coverage that is only proposed, for ",
}


# --- Inputs ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class CohortAt:
    """A cohort at its position: canonical, and its accounting."""

    cohort: CanonicalCohort
    accounting: Tally


def predicate_key(predicate: CanonicalCohort) -> str:
    """The leaf key of a predicate's canonical clause tree (§7.6)."""
    return leaf_key(predicate.form[predicate.release.manifest])


# --- Counts ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Share:
    """A predicate at a position: its split of the cohort's units, its UNKNOWN units by reason,
    the units whose answer the other lift rule changes, and the flags its truth values carry."""

    true: int
    false: int
    unknown: int
    excluded: Mapping[ExclusionReason, int]
    lift: int
    marks: frozenset[Mark]

    @property
    def known(self) -> int:
        return self.true + self.false

    @property
    def split(self) -> Split:
        return Split(self.true, self.false, self.unknown)


@dataclass(frozen=True)
class _Analysed:
    """The cohort's units for which some predicate is known, those for which none is, and those
    by every reason a predicate gave them."""

    n: int
    excluded_units: int
    excluded: Mapping[ExclusionReason, int]


def _excluded(by_reason: Mapping[Reason, int]) -> dict[ExclusionReason, int]:
    found = dict.fromkeys(ExclusionReason, 0)
    found.update((ExclusionReason(reason.value), count) for reason, count in by_reason.items())
    return found


def _counted(crossed: Crossed) -> tuple[list[_Share], _Analysed]:
    shares = [
        _Share(
            split.true,
            split.false,
            split.unknown,
            _excluded(split.unknown_by_reason),
            split.lift_differs,
            split.marks,
        )
        for split in crossed.splits
    ]
    whole = _Analysed(crossed.known, crossed.none_known, _excluded(crossed.none_known_by_reason))
    return shares, whole


# --- Values ----------------------------------------------------------------------------------


def _excluded_shown(
    excluded: Mapping[ExclusionReason, int], total_hidden: bool, k: int | None
) -> dict[ExclusionReason, int] | None:
    if total_hidden or (k is not None and any(small(k, count) for count in excluded.values())):
        return None
    return dict(excluded)


def _proportion(
    share: _Share,
    position: int,
    key: str,
    level: float,
    *,
    hidden_true: bool,
    hidden_known: bool,
    excluded: Mapping[ExclusionReason, int] | None,
) -> Proportion:
    reasons: dict[str, NotEstimableReason] = {}
    numerator = None if hidden_true else share.true
    denominator = None if hidden_known else share.known
    estimate: float | None = None
    low: float | None = None
    high: float | None = None
    if numerator is None or denominator is None:
        reasons.update(dict.fromkeys(("/estimate", "/ci/low", "/ci/high"), _SUPPRESSED))
    elif share.known == 0:
        reasons.update(
            dict.fromkeys(("/estimate", "/ci/low", "/ci/high"), NotEstimableReason.NO_UNITS)
        )
    else:
        estimate = share.true / share.known
        low, high = stats.wilson(share.true, share.known, level)
    for member, value in (("/numerator", numerator), ("/denominator", denominator)):
        if value is None:
            reasons[member] = _SUPPRESSED
    if excluded is None:
        reasons["/excluded"] = _SUPPRESSED
    return Proportion.model_validate(
        {
            "estimate": estimate,
            "numerator": numerator,
            "denominator": denominator,
            "denominator_definition": DenominatorDefinition(
                position=position, predicate=key, counts="known"
            ),
            "excluded": excluded,
            "ci": Interval(method="wilson", level=level, low=low, high=high),
            "not_estimable": reasons or None,
        }
    )


def _nothing(reason: NotEstimableReason, *members: str) -> dict[str, NotEstimableReason]:
    return dict.fromkeys(members, reason)


def _effects(
    shares: Sequence[_Share],
    position: int,
    reference: int,
    level: float,
    *,
    overlap: bool,
    suppressed: bool,
) -> list[EffectSize]:
    """The risk difference and the risk ratio of ``position`` versus ``reference``."""
    mine, theirs = shares[position], shares[reference]
    bounds = ("/estimate", "/ci/low", "/ci/high")
    found: list[EffectSize] = []
    for measure, method in (
        (EffectMeasure.RISK_DIFFERENCE, "newcombe_hybrid_score"),
        (EffectMeasure.RISK_RATIO, "katz_log"),
    ):
        reason: NotEstimableReason | None = None
        if overlap:
            reason = NotEstimableReason.OVERLAPPING_COHORTS
        elif suppressed:
            reason = _SUPPRESSED
        elif mine.known == 0 or theirs.known == 0:
            reason = NotEstimableReason.NO_UNITS
        elif measure == EffectMeasure.RISK_RATIO and (mine.true == 0 or theirs.true == 0):
            reason = NotEstimableReason.NO_EVENTS
        estimate = low = high = None
        if reason is None:
            versus = (theirs.true, theirs.known)
            compute = stats.newcombe if measure == EffectMeasure.RISK_DIFFERENCE else stats.katz
            estimate, low, high = compute(mine.true, mine.known, versus, level)
        found.append(
            EffectSize(
                measure=measure,
                position=position,
                versus=reference,
                estimate=estimate,
                ci=Interval(method=method, level=level, low=low, high=high),
                not_estimable=None if reason is None else _nothing(reason, *bounds),
            )
        )
    return found


@dataclass(frozen=True)
class _Tested:
    test: HypothesisTest
    small_expected: bool = False


def _test(shares: Sequence[_Share], cohorts: int, *, overlap: bool, suppressed: bool) -> _Tested:
    """The predicate's test across the positions that have units for which it is known. One
    that overlap or the disclosure settings leave uncomputed names no position, and its method
    is the one the view's number of cohorts gives: which positions it would use depends on counts
    that may not be shown."""
    used = [position for position, share in enumerate(shares) if share.known > 0]
    reason: NotEstimableReason | None = None
    if overlap or suppressed:
        reason = NotEstimableReason.OVERLAPPING_COHORTS if overlap else _SUPPRESSED
        used = []
    fisher = cohorts == 2 or len(used) == 2
    method = "fisher_exact" if fisher else "chi_squared"
    members = ("/p",) if fisher else ("/p", "/statistic")
    if reason is None and len(used) < 2:
        reason = NotEstimableReason.NO_UNITS
    elif reason is None and not (
        sum(shares[p].true for p in used) and sum(shares[p].false for p in used)
    ):
        reason = NotEstimableReason.DEGENERATE_TABLE
    if reason is not None:
        given: dict[str, object] = {"method": method, "positions": used, "p": None}
        if not fisher:
            given["statistic"] = None
        given["not_estimable"] = _nothing(reason, *members)
        return _Tested(HypothesisTest.model_validate(given))
    if fisher:
        first, second = (shares[p] for p in used)
        p_value = stats.fisher(((first.true, first.false), (second.true, second.false)))
        return _Tested(HypothesisTest(method=method, positions=used, p=p_value))
    tested = stats.chi_squared([[shares[p].true, shares[p].false] for p in used])
    return _Tested(
        HypothesisTest(
            method=method,
            positions=used,
            statistic=tested.statistic,
            df=tested.df,
            p=tested.p,
        ),
        small_expected=tested.least_expected < 5,
    )


# --- The analysis ----------------------------------------------------------------------------


@dataclass(frozen=True)
class Outcome:
    """A view's digested parts, before its digest is taken, and its caveats (D319)."""

    population: list[Population]
    analysed: list[Analysed]
    values: ExistenceValues
    caveats: list[Caveat]


def compare(
    positions: Sequence[CohortAt],
    predicates: Sequence[CanonicalCohort],
    crossing: Crossing,
    params: ExistenceParams,
    *,
    reference: int,
    overlap: bool,
    k: int | None,
) -> Outcome:
    """``compare.existence`` over a view's cohorts and predicates (module docstring), from the
    crossing of the ones with the others (``engine.sql``: counted by SQL, or by ``cross`` from
    the reference evaluator's truth values). ``overlap`` says that the view allows overlap and
    its cohorts share units."""
    level = params.level
    raw = [count_parts(position.cohort, position.accounting) for position in positions]
    if len(crossing.cohorts) != len(positions) or any(
        len(crossed.splits) != len(predicates) for crossed in crossing.cohorts
    ):
        raise ValueError("a crossing of the view's cohorts with its predicates")
    counted = [_counted(crossed) for crossed in crossing.cohorts]
    shares = [found[0] for found in counted]
    whole = [found[1] for found in counted]
    population = [disclosed(parts, k).population for parts in raw]
    hides = [
        _nothing_hidden(len(predicates))
        if k is None
        else hidden(population[position].n_true is not None, [s.split for s in shares[position]], k)
        for position in range(len(positions))
    ]
    analysed = [
        _analysed(whole[position], shares[position], hides[position], k)
        for position in range(len(positions))
    ]
    keys = [predicate_key(predicate) for predicate in predicates]
    at_positions: list[ExistencePosition] = []
    for position in range(len(positions)):
        found: list[PredicateShare] = []
        for predicate, share in enumerate(shares[position]):
            at = hides[position]
            proportion = _proportion(
                share,
                position,
                keys[predicate],
                level,
                hidden_true=at.true[predicate],
                hidden_known=at.unknown[predicate],
                excluded=_excluded_shown(share.excluded, at.unknown[predicate], k),
            )
            found.append(
                PredicateShare(
                    proportion=proportion,
                    lift_differs=None if k is not None else share.lift,
                    not_estimable=None if k is None else {"/lift_differs": _SUPPRESSED},
                )
            )
        at_positions.append(ExistencePosition(predicates=found))
    contrasts: list[PredicateContrast] = []
    tested: list[_Tested] = []
    for predicate in range(len(predicates)):
        column = [shares[position][predicate] for position in range(len(positions))]
        lost = [hides[position].proportion(predicate) for position in range(len(positions))]
        if len(positions) < 2:
            contrasts.append(PredicateContrast(effects=[]))
            continue
        test = _test(column, len(positions), overlap=overlap, suppressed=any(lost))
        tested.append(test)
        effects = [
            effect
            for position in range(len(positions))
            if position != reference
            for effect in _effects(
                column,
                position,
                reference,
                level,
                overlap=overlap,
                suppressed=lost[position] or lost[reference],
            )
        ]
        contrasts.append(PredicateContrast(test=test.test, effects=effects))
    family = _family(tested) if len(positions) >= 2 else None
    if family is not None:
        contrasts = _with_q(contrasts, family[1])
    view = ExistenceView(predicates=contrasts, family=None if family is None else family[0])
    values = ExistenceValues(positions=at_positions, view=view)
    caveats = _caveats(positions, population, analysed, shares, hides, tested, k, overlap)
    return Outcome(population, analysed, values, caveats)


def _nothing_hidden(predicates: int) -> Hidden:
    return Hidden((False,) * predicates, (False,) * predicates, False)


def _analysed(whole: _Analysed, shares: Sequence[_Share], hides: Hidden, k: int | None) -> Analysed:
    gone_whole = hides.analysed
    excluded = _excluded_shown(whole.excluded, gone_whole, k)
    reasons = {
        member: _SUPPRESSED
        for member, gone in (
            ("/n", gone_whole),
            ("/excluded_units", gone_whole),
            ("/excluded", excluded is None),
        )
        if gone
    }
    variables: list[AnalysedVariable] | None = None
    if len(shares) > 1:
        variables = []
        for predicate, share in enumerate(shares):
            unknown_hidden = hides.unknown[predicate]
            shown = _excluded_shown(share.excluded, unknown_hidden, k)
            gone = {
                member: _SUPPRESSED
                for member, lost in (
                    ("/n", unknown_hidden),
                    ("/excluded_units", unknown_hidden),
                    ("/excluded", shown is None),
                )
                if lost
            }
            variables.append(
                AnalysedVariable(
                    n=None if unknown_hidden else share.known,
                    excluded=shown,
                    excluded_units=None if unknown_hidden else share.unknown,
                    not_estimable=gone or None,
                )
            )
    return Analysed(
        n=None if gone_whole else whole.n,
        excluded=excluded,
        excluded_units=None if gone_whole else whole.excluded_units,
        not_estimable=reasons or None,
        variables=variables,
    )


def _family(tested: Sequence[_Tested]) -> tuple[Family, list[float | None]]:
    """The Benjamini–Hochberg family of the view's tests, and each test's q-value, ``None`` for
    one that left it (§9.5)."""
    computed = [index for index, test in enumerate(tested) if test.test.p is not None]
    q_values = stats.benjamini_hochberg([cast(float, tested[i].test.p) for i in computed])
    found: list[float | None] = [None] * len(tested)
    for index, q in zip(computed, q_values, strict=True):
        found[index] = q
    suppressed = sum(test.test.reasons().get("/p") == _SUPPRESSED for test in tested)
    return (
        Family(
            tests=len(computed),
            not_computed=len(tested) - len(computed) - suppressed,
            suppressed=suppressed,
        ),
        found,
    )


def _with_q(
    contrasts: Sequence[PredicateContrast], q_values: Sequence[float | None]
) -> list[PredicateContrast]:
    found: list[PredicateContrast] = []
    for contrast, q in zip(contrasts, q_values, strict=True):
        test = contrast.test
        if test is not None and q is not None:
            test = test.model_copy(update={"q": q})
        found.append(contrast.model_copy(update={"test": test}))
    return found


# --- Caveats ---------------------------------------------------------------------------------


def _caveat(code: CaveatCode, affects: Sequence[str], *message: Segment) -> Caveat:
    return Caveat(
        code=code, severity=CORE_SEVERITIES[code], message=list(message), affects=list(affects)
    )


def _caveats(
    positions: Sequence[CohortAt],
    population: Sequence[Population],
    analysed: Sequence[Analysed],
    shares: Sequence[Sequence[_Share]],
    hides: Sequence[Hidden],
    tested: Sequence[_Tested],
    k: int | None,
    overlap: bool,
) -> list[Caveat]:
    """The caveats that the view's data raise (module docstring); the static ones are the
    caller's."""
    found: list[Caveat] = []
    unknown = any(count.n_unknown is None or count.n_unknown for count in population) or any(
        at.unknown[predicate] or share.unknown
        for row, at in zip(shares, hides, strict=True)
        for predicate, share in enumerate(row)
    )
    if unknown:
        found.append(
            _caveat(
                CaveatCode.UNKNOWN_EXCLUDED,
                ["/analysed", "/population"],
                text(
                    "Units whose membership of a cohort, or whose answer to a predicate, could "
                    "not be decided, if any, are left out of the cohort or of that predicate's "
                    "proportion; population and analysed count them by reason, where the "
                    "disclosure settings show them"
                ),
            )
        )
    for flag, code in _FLAGS.items():
        cohorts = sorted(
            {m.relationship for p in positions for m in p.accounting.marks if m.flag is flag}
        )
        asked = sorted(
            {
                m.relationship
                for row in shares
                for share in row
                for m in share.marks
                if m.flag is flag
            }
        )
        if cohorts:
            found.append(_caveat(code, ["/population"], text(_FLAG_TEXT[flag]), *_listed(cohorts)))
        if asked:
            found.append(_caveat(code, ["/values"], text(_FLAG_TEXT[flag]), *_listed(asked)))
    lifts = [p.accounting.lift_differs for p in positions]
    asked_lifts = [[share.lift for share in row] for row in shares]
    if k is not None or any(lifts) or any(any(row) for row in asked_lifts):
        found.append(_lift(lifts, asked_lifts, k))
    if overlap:
        found.append(
            _caveat(
                CaveatCode.COHORTS_OVERLAP,
                ["/values"],
                text(
                    "Cohorts of the view share units, so no between-cohort value was computed; "
                    "compare a subset with the rest of its base instead (§7.4)"
                ),
            )
        )
    for predicate, test in enumerate(tested):
        if test.small_expected:
            found.append(
                _caveat(
                    CaveatCode.SMALL_N,
                    [f"/values/view/predicates/{predicate}/test"],
                    text("An expected count below 5 in the table this chi-squared test used"),
                )
            )
    if k is not None:
        affects = ["/population", "/values"]
        if any(
            entry.reasons() or any(v.reasons() for v in entry.variables or []) for entry in analysed
        ):
            affects.append("/analysed")
        found.append(
            _caveat(
                CaveatCode.SUPPRESSED,
                affects,
                text(
                    f"Counts from 1 to {k - 1}, what would reveal them, and the values computed "
                    "from them are suppressed (null) under the disclosure settings"
                ),
            )
        )
    return found


def _listed(names: Sequence[str]) -> list[Segment]:
    found: list[Segment] = []
    for index, name in enumerate(names):
        if index:
            found.append(text(", "))
        found.append(data(name))
    return found


def _lift(lifts: Sequence[int], asked: Sequence[Sequence[int]], k: int | None) -> Caveat:
    affects = ["/population", "/values"]
    if k is not None:
        return _caveat(
            CaveatCode.LIFT_DIFFERS,
            affects,
            text("Whether the other lift rule would change any cohort's or predicate's answer "),
            text("for some units, and for how many, is suppressed by the disclosure settings"),
        )
    message: list[Segment] = [text("The other lift rule would change the answer for")]
    parts: list[str] = []
    for position, count in enumerate(lifts):
        if count:
            parts.append(f" {count} units of the cohort at position {position}")
    for position, row in enumerate(asked):
        for predicate, count in enumerate(row):
            if count:
                parts.append(f" {count} units of position {position} for predicate {predicate}")
    message.append(text(";".join(parts)))
    return _caveat(CaveatCode.LIFT_DIFFERS, affects, *message)


# --- Readback --------------------------------------------------------------------------------


def view_readback(
    cohorts: int,
    reference: int,
    predicates: Sequence[CanonicalCohort],
    params: ExistenceParams,
) -> list[Segment]:
    """The view's readback (§7.7): a function of its canonical form and the release's
    descriptors, so cohorts are named by position, never by the names a document gives them."""
    level = data(number_text(params.level))
    found: list[Segment] = [text("For each predicate, the proportion of each cohort's units ")]
    found.append(text("for which it holds, over the units of the cohort for which it is known, "))
    found += [text("with a "), level, text(" Wilson interval")]
    if cohorts > 1:
        found += [
            text(f"; across the {cohorts} cohorts in view order, the reference being the cohort "),
            text(f"at position {reference}, the risk difference of each other cohort versus the "),
            text("reference with Newcombe's hybrid score interval and the risk ratio with the "),
            text("Katz log interval, at the same level; and Fisher's exact test when two "),
            text("cohorts have units for which the predicate is known, the chi-squared test "),
            text("when more do, with Benjamini-Hochberg q-values across the predicates"),
        ]
    found.append(text("."))
    for index, predicate in enumerate(predicates):
        found += [text(f" Predicate {index}: units for which "), *conditions(predicate)]
        found.append(text("."))
    found.append(
        text(
            " A unit for which a predicate cannot be decided is left out of that predicate's "
            "proportion and counted by reason."
        )
    )
    return found


__all__ = [
    "ANALYSIS_ID",
    "CAVEATS",
    "ENTRY",
    "METHODS",
    "VERSION",
    "CohortAt",
    "Outcome",
    "compare",
    "predicate_key",
    "view_readback",
]

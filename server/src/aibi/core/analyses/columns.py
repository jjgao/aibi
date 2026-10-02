"""``compare.columns``: each column compared across cohorts (SPEC §8.4, §9.2, §9.3, §9.5;
D336–D338).

For each variable of the view (``params.columns``, D325) and each cohort in view order, what the
cohort's units hold, and between the cohorts how they differ from the reference:

- **Categories** (a category or boolean column, ``max`` or ``min`` of an ordered category, and
  ``some`` or ``every``, whose categories are ``false`` and ``true``): at each position, the
  units of each category over the units the variable has a value for, listed as
  ``summary.distribution`` lists them (the column's permissible values in their order, zeros
  included, then, without a disclosure setting, the other values any cohort holds in canonical
  order); across the cohorts, the test of the table of cohorts by categories (Fisher's exact test
  for a table of two cohorts and two categories, else Pearson's chi-squared test), over the
  cohorts with a value and the categories some of them hold, and per category (per row of the
  table) the difference in its proportion of each other position versus the reference, with
  Newcombe's hybrid score interval.
- **Numbers** (a number, integer or time-offset column, ``count``, and ``mean``, ``max`` and
  ``min`` of numbers): at each position, n, mean, standard deviation and median; across the
  cohorts, the primary test, Welch's *t* for two cohorts and Welch's one-way test for more, the
  secondary test, Mann–Whitney for two and Kruskal–Wallis for more, reported unadjusted, and of
  each other position versus the reference the difference in means with the Welch–Satterthwaite
  interval and the difference in medians with a bootstrap percentile interval (``stats``).

Benjamini–Hochberg q-values cover the view's primary tests, one a column; tests that cannot be
computed leave the family and are counted (``Family``). A NOT_APPLICABLE cell, like every other
reason a unit has no value, leaves the unit out of that column's comparison, counted in
``analysed`` (§6.6, §9.5).

**Randomness** (§9.3). The bootstrap resamples each cohort within itself, ``stats.REPLICATES``
times, from a stream seeded by the SHA-256 of ``{"column", "computation", "position"}`` (RFC 8785)
— the view's computation id, which omits the disclosure setting (D144), the column's index and the
position — so that the same view gives the same replicates on every run, and no clock or state is
read. A reference cohort's replicates are shared by every effect versus it.

**Estimability** (§9.5), each value's reason the first that applies: with ``overlap: "allow"`` and
units actually shared, no between-cohort value is computed (``overlapping_cohorts``,
``COHORTS_OVERLAP``); then, under *k*, suppression (below); a cohort with no unit that has a value
has no estimate and no contrast (``no_units``), and the tests use the others, not estimable
(``no_units``) with fewer than two; a table that, after those cohorts and the categories none of
the rest holds are dropped, has fewer than two categories has no test (``degenerate_table``); a
group of fewer than two values, of values all the same, or whose standard deviation is below the
least double, has no standard deviation, and every Welch test that includes it and every interval
of a difference in means that includes it is not estimable (``zero_variance``), and when every
value is tied neither rank test is. A test not
computed for overlap or suppression names no positions and the method its view's cohorts' number
(and a table's rows') gives; one not estimable names the positions it would use. A chi-squared
test with an expected count below 5 carries ``SMALL_N``.

**Bounds and time** (D336). Without *k*, a value, statistic, difference or bound beyond
±(2^53 − 1), which an output does not hold (§8.2), refuses the call as ``summary.distribution``'s
does (``TooLarge``); Welch's tests are computed exactly enough that groups of any scales a
double holds are compared (``stats``), so only a result truly beyond that bound refuses. The
call's deadline is checked before each column and each of its steps (a cohort's values in order,
its moments, its median, the tests, a cohort's bootstrap; a cohort's categories counted), and
within each of them every 65,536 values (``stats``: sorts in runs merged two at a time, sums,
counts, the rank tests and their ties, the bootstrap's preparation). A column of categories
whose declared values are more than ``MAX_CATEGORIES`` refuses before any value is read, else at
the first undeclared value too many, before any is sorted; membership in the declared values is
a set's, and each cohort's units with a value are summed once. So the longest stretch without a
check, at the answer cap (64 MiB, about five million distinct values at 13 bytes a row), is
about 1.2 s, whatever the call's shape: a rank test's sums, or a merge of a cohort's sorted runs.

**Disclosure** (§8.4, D337). Under *k*, each position's population is disclosed as its cohort's
count is, and each variable's split of the cohort's units as ``summary.distribution``'s is
(``common.shown``): a position whose ``n_true`` is suppressed shows nothing of its variables, and
``analysed`` nothing that combines them. A position's categories are merged exactly as
``summary.distribution`` merges them, a category column's undeclared values being one row that
names none, so that the two analyses of one column in one call show the same rows. What the view
adds of categories is computed from the rows shown alone: the table's rows are the coarsest
grouping of the listed categories of which every shown position's merged rows are unions, each of
its counts the sum of rows shown, and a test or an effect that needs a position whose split is not
shown is suppressed. Of numbers nothing computed from the values is shown, only each position's
``n``: the mean, the standard deviation and the median, both tests (a rank test counts the pairs of
units ordered one way, which beside a histogram places units within a merged bin), both
differences and their intervals (a bootstrap interval's bounds are differences of medians of
resamples, values units hold) are ``suppressed``, as ``summary.distribution``'s statistics of
values are (D329); suppressed tests leave the family.
"""

import hashlib
import math
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from operator import itemgetter
from typing import Literal, cast

from aibi.core.analyses import stats
from aibi.core.analyses.common import (
    CohortAt,
    Shown,
    analysed_of,
    caveat,
    cohort_caveats,
    flag_caveats,
    populations,
    shown,
)
from aibi.core.analyses.disclosure import merged
from aibi.core.analyses.distribution import (
    TooLarge,
    TooManyCategories,
    categorical,
    category_label,
    declared_categories,
    open_categories,
    within_text,
)
from aibi.core.engine.canonical import CanonicalVariable
from aibi.core.engine.readback import variable_readback
from aibi.core.engine.variables import Joint, Materialised, Value
from aibi.core.engine.worker import CallerDeadline
from aibi.core.schema.analyses import (
    CategoryComparison,
    CategoryContrast,
    CategoryDistribution,
    CategoryShare,
    ColumnComparison,
    ColumnsParams,
    ColumnsPosition,
    ColumnSummary,
    ColumnsValues,
    ColumnsView,
    Family,
    NumberComparison,
    NumberSummary,
)
from aibi.core.schema.caveats import Caveat, CaveatCode
from aibi.core.schema.descriptors import AnalysisDescriptor
from aibi.core.schema.export import params_schema, values_schema
from aibi.core.schema.ids import MAX_SAFE_INTEGER
from aibi.core.schema.jsonio import canonical, number_text, utf16_key
from aibi.core.schema.limits import MAX_CATEGORIES, MAX_COHORTS
from aibi.core.schema.numbers import (
    DenominatorDefinition,
    EffectMeasure,
    EffectSize,
    HypothesisTest,
    Interval,
    NotEstimableReason,
    Proportion,
)
from aibi.core.schema.output import Data, Segment, data, text
from aibi.core.schema.results import Analysed, Population
from aibi.core.schema.semantics import ExclusionReason

ANALYSIS_ID = "compare.columns"
VERSION = "1.0.0"
"""Bumped whenever its outputs for the same inputs change (§7.6)."""
METHODS: Mapping[str, str] = {
    "chi_squared": "Pearson's chi-squared test of independence of the table of cohorts by "
    "categories, without continuity correction, as R's chisq.test(correct = FALSE)",
    "fisher_exact": "Fisher's exact test of a table of two cohorts by two categories, two-sided "
    "as R's fisher.test, implemented directly",
    "newcombe_hybrid_score": "Newcombe's hybrid score interval for a difference of proportions, "
    "method 10 of Newcombe (1998), from the two Wilson intervals",
    "welch_t": "Welch's two-sample t test, as R's t.test(var.equal = FALSE), implemented directly "
    "over the regularised incomplete beta function",
    "welch_anova": "Welch's one-way test, as R's oneway.test(var.equal = FALSE), implemented "
    "directly",
    "mann_whitney": "The Wilcoxon rank-sum test, as R's wilcox.test(correct = TRUE): exact when "
    "both groups hold fewer than 50 values and none are tied, else the normal approximation "
    "corrected for ties and continuity",
    "kruskal_wallis": "The Kruskal-Wallis test corrected for ties, as R's kruskal.test: ranks "
    "of the values as doubles, and ties in the correction of the values R's as.character prints "
    "alike",
    "welch_satterthwaite": "The interval of a difference in means with the Welch-Satterthwaite "
    "degrees of freedom, as t.test's conf.int",
    "bootstrap_percentile": "The percentile interval of the difference in medians (each R's "
    "quantile type 7) over 2000 bootstrap replicates, each cohort resampled within itself: the "
    "ceiling(B alpha / 2)-th and ceiling(B (1 - alpha / 2))-th replicates in order, not "
    "interpolated (49 below the low bound and 50 above the high at 0.95), seeded from the "
    "computation id",
    "benjamini_hochberg": "Benjamini-Hochberg q-values over the view's primary tests, as R's "
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
        "label": "Columns compared across cohorts",
        "definition": (
            "Per column: for categories, the units per category in each cohort, the chi-squared "
            "test of the table (Fisher's exact test for two cohorts and two categories) and the "
            "difference in each category's proportion versus the reference cohort (Newcombe "
            "hybrid score interval); for numbers, n, mean, standard deviation and median in each "
            "cohort, Welch's t test or Welch's one-way test, the Mann-Whitney or Kruskal-Wallis "
            "test, and the differences in means (Welch-Satterthwaite interval) and in medians "
            "(bootstrap interval, 2000 replicates) versus the reference; Benjamini-Hochberg "
            "q-values over the columns' primary tests."
        ),
        "fields": {
            "requires": [
                {"role": "cohorts", "min": 1, "max": MAX_COHORTS},
                {"role": "columns", "kind": "column", "min": 1},
            ],
            "params": params_schema(ColumnsParams),
            "returns": values_schema(ColumnsValues),
            "methods": dict(METHODS),
            "assumptions": ["units independent of one another", "independent groups"],
            "uses_reference": True,
            "assumes_independent_groups": True,
            "cross_dataset": None,
            "randomness": {"seeded": True, "replicates": stats.REPLICATES},
            "caveats": [code.value for code in CAVEATS],
        },
    }
)
"""The registry entry (§9.1): implemented directly, with no library."""

_SUPPRESSED = NotEstimableReason.SUPPRESSED
_NO_UNITS = NotEstimableReason.NO_UNITS
_OVERLAP = NotEstimableReason.OVERLAPPING_COHORTS
_ZERO_VARIANCE = NotEstimableReason.ZERO_VARIANCE
_BOUNDS = ("/estimate", "/ci/low", "/ci/high")


def seed(computation: str, column: int, position: int) -> int:
    """The seed of a position's bootstrap for a column (module docstring)."""
    hashed = canonical({"column": column, "computation": computation, "position": position})
    return int.from_bytes(hashlib.sha256(hashed).digest(), "big")


def _nothing(reason: NotEstimableReason, *members: str) -> dict[str, NotEstimableReason]:
    return dict.fromkeys(members, reason)


def _within(column: int, *numbers: float | None) -> None:
    """Raise ``TooLarge`` for a number beyond ±(2^53 − 1), which an output does not hold (§8.2),
    or not finite."""
    for number in numbers:
        if number is not None and not (math.isfinite(number) and abs(number) <= MAX_SAFE_INTEGER):
            raise TooLarge(column)


# --- Categories -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Row:
    """A listed category, or under *k* the row of a category column's undeclared values."""

    values: tuple[Value, ...]
    other: bool


def _rows(
    variable: CanonicalVariable,
    found: Sequence[Materialised],
    k: int | None,
    column: int,
    ends: float | None,
) -> list[_Row]:
    """A categorical variable's rows, the same at every position (module docstring); more than
    ``MAX_CATEGORIES`` raise ``TooManyCategories`` before any value is read where the declared
    ones are too many, else at the first undeclared value too many, before any is sorted, so a
    column of a million values is refused after reading about ``MAX_CATEGORIES`` of them, the
    deadline checked before each position's; a label longer than ``MAX_TEXT`` raises
    ``LongCategory``, and one that is not Unicode text ``NonTextCategory``
    (``distribution.within_text``, D382)."""
    declared = declared_categories(variable)
    if k is not None:
        rows = [_Row((value,), False) for value in declared]
        rows += [_Row((), True)] if open_categories(variable) else []
    else:
        if len(declared) > MAX_CATEGORIES:
            raise TooManyCategories(column)
        known = frozenset(declared)
        room = MAX_CATEGORIES - len(declared)
        undeclared: set[Value] = set()
        for one in found:
            _deadline(ends)
            for value in one.values:
                if value not in known and value not in undeclared:
                    undeclared.add(value)
                    if len(undeclared) > room:
                        raise TooManyCategories(column)
        others = sorted(undeclared, key=lambda value: utf16_key(category_label(value)))
        rows = [_Row((value,), False) for value in [*declared, *others]]
    if len(rows) > MAX_CATEGORIES:
        raise TooManyCategories(column)
    within_text((value for row in rows for value in row.values), column)
    return rows


def _row_counts(rows: Sequence[_Row], declared: frozenset[Value], one: Materialised) -> list[int]:
    return [
        sum(one.values.get(value, 0) for value in row.values)
        + (sum(n for value, n in one.values.items() if value not in declared) if row.other else 0)
        for row in rows
    ]


def _proportion(count: int, n: int, position: int) -> Proportion:
    return Proportion.model_validate(
        {
            "estimate": count / n if n else None,
            "numerator": count,
            "denominator": n,
            "denominator_definition": DenominatorDefinition(
                position=position, predicate=None, counts="known"
            ),
            "not_estimable": None if n else {"/estimate": _NO_UNITS},
        }
    )


def _values_of(rows: Sequence[_Row]) -> tuple[list[Data], Literal[True] | None]:
    names = [Data(data=category_label(value)) for row in rows for value in row.values]
    return names, True if any(row.other for row in rows) else None


def _joined(spans: Sequence[Sequence[tuple[int, int]]], width: int) -> list[tuple[int, int]]:
    """The coarsest grouping of ``width`` listed rows of which every position's spans are unions
    (module docstring): a row starts a group only where every position's spans start one."""
    starts = [
        at
        for at in range(width)
        if all(any(start == at for start, _ in position) for position in spans)
    ]
    return [(start, end - 1) for start, end in zip(starts, [*starts[1:], width], strict=True)]


def _category_test(
    table: Sequence[Sequence[int]],
    cohorts: int,
    *,
    overlap: bool,
    suppressed: bool,
) -> tuple[HypothesisTest, bool]:
    """The test of a table of positions by rows, and whether an expected count is below 5; a
    test not computed for overlap or suppression names no position, and its method is the one
    the view's cohorts and the table's rows give."""
    reason: NotEstimableReason | None = None
    used = [position for position, row in enumerate(table) if sum(row)]
    if overlap or suppressed:
        reason = _OVERLAP if overlap else _SUPPRESSED
        used = []
        columns = list(range(len(table[0]) if table else 0))
        fisher = cohorts == 2 and len(columns) == 2
    else:
        columns = [at for at in range(len(table[0])) if any(table[p][at] for p in used)]
        fisher = len(used) == 2 and len(columns) == 2
        if len(used) < 2:
            reason = _NO_UNITS
        elif len(columns) < 2:
            reason = NotEstimableReason.DEGENERATE_TABLE
    method = "fisher_exact" if fisher else "chi_squared"
    if reason is not None:
        given: dict[str, object] = {"method": method, "positions": used, "p": None}
        members = ("/p",) if fisher else ("/p", "/statistic")
        if not fisher:
            given["statistic"] = None
        given["not_estimable"] = _nothing(reason, *members)
        return HypothesisTest.model_validate(given), False
    counts = [[table[p][at] for at in columns] for p in used]
    if fisher:
        (a, b), (c, d) = counts
        return HypothesisTest(
            method=method, positions=used, p=stats.fisher(((a, b), (c, d)))
        ), False
    tested = stats.chi_squared(counts)
    return (
        HypothesisTest(
            method=method, positions=used, statistic=tested.statistic, df=tested.df, p=tested.p
        ),
        tested.least_expected < 5,
    )


@dataclass(frozen=True)
class _Compared:
    """A column at every position and across them, and whether its test carries ``SMALL_N``."""

    at_positions: list[ColumnSummary]
    view: ColumnComparison
    small_expected: bool = False


def _categories(
    variable: CanonicalVariable,
    found: Sequence[Materialised],
    visible: Sequence[Shown],
    column: int,
    *,
    reference: int,
    level: float,
    overlap: bool,
    k: int | None,
    ends: float | None,
) -> _Compared:
    """A column of categories at each position and across them (module docstring): each
    position's units with a value are summed once, and the deadline is checked before each
    position's values are counted."""
    rows = _rows(variable, found, k, column, ends)
    declared = frozenset(declared_categories(variable))
    counts: list[list[int]] = []
    for one in found:
        _deadline(ends)
        counts.append(_row_counts(rows, declared, one))
    sizes = [one.n for one in found]
    spans: list[list[tuple[int, int]] | None] = []
    at_positions: list[ColumnSummary] = []
    for position, (size, seen) in enumerate(zip(sizes, visible, strict=True)):
        if not seen.split:
            spans.append(None)
            at_positions.append(
                CategoryDistribution(
                    kind="categories", categories=None, not_estimable={"/categories": _SUPPRESSED}
                )
            )
            continue
        cut = [(at, at) for at in range(len(rows))] if k is None else merged(counts[position], k)
        spans.append(cut)
        shares: list[CategoryShare] = []
        for start, end in cut:
            names, other = _values_of(rows[start : end + 1])
            share = _proportion(sum(counts[position][start : end + 1]), size, position)
            shares.append(CategoryShare(values=names, other_values=other, proportion=share))
        at_positions.append(CategoryDistribution(kind="categories", categories=shares))
    known = [cut for cut in spans if cut is not None]
    groups = _joined(known, len(rows)) if known else []
    grouped = [
        None if cut is None else [sum(counts[position][s : e + 1]) for s, e in groups]
        for position, cut in enumerate(spans)
    ]
    cohorts = len(found)
    test: HypothesisTest | None = None
    small_expected = False
    if cohorts > 1:
        hidden = any(cut is None for cut in grouped)
        table = [cut if cut is not None else [0] * len(groups) for cut in grouped]
        test, small_expected = _category_test(table, cohorts, overlap=overlap, suppressed=hidden)
    contrasts: list[CategoryContrast] = []
    for index, (start, end) in enumerate(groups):
        effects: list[EffectSize] = []
        for position in range(cohorts):
            if position == reference or cohorts < 2:
                continue
            mine, theirs = grouped[position], grouped[reference]
            reason: NotEstimableReason | None = None
            if overlap:
                reason = _OVERLAP
            elif mine is None or theirs is None:
                reason = _SUPPRESSED
            elif not sizes[position] or not sizes[reference]:
                reason = _NO_UNITS
            estimate = low = high = None
            if reason is None:
                assert mine is not None
                assert theirs is not None
                estimate, low, high = stats.newcombe(
                    mine[index],
                    sizes[position],
                    (theirs[index], sizes[reference]),
                    level,
                )
            effects.append(
                EffectSize(
                    measure=EffectMeasure.PROPORTION_DIFFERENCE,
                    position=position,
                    versus=reference,
                    estimate=estimate,
                    ci=Interval(method="newcombe_hybrid_score", level=level, low=low, high=high),
                    not_estimable=None if reason is None else _nothing(reason, *_BOUNDS),
                )
            )
        names, other = _values_of(rows[start : end + 1])
        contrasts.append(CategoryContrast(values=names, other_values=other, effects=effects))
    view = CategoryComparison(kind="categories", test=test, categories=contrasts)
    return _Compared(at_positions, view, small_expected)


# --- Numbers --------------------------------------------------------------------------------------


def _weighted(one: Materialised, ends: float | None) -> stats.Weighted:
    """A cohort's values with their units, in increasing order (``stats.checked_sort``, the
    deadline checked between its runs and merges)."""
    items = [(cast(int | float, value), times) for value, times in one.values.items() if times]
    return stats.checked_sort(items, ends, key=itemgetter(0))


def _deadline(ends: float | None) -> None:
    """Raise ``CallerDeadline`` once ``time.monotonic()`` has passed ``ends``: checked between a
    column's steps, so that a call overruns its deadline by one step at most (D336)."""
    if ends is not None and time.monotonic() >= ends:
        raise CallerDeadline


@dataclass(frozen=True)
class _Group:
    """A position's values of a numeric variable, and, without *k*, their moments and median,
    computed once for the position's summary, the tests and the differences."""

    values: stats.Weighted
    moments: stats.Moments | None = None
    median: float | None = None

    @property
    def varied(self) -> bool:
        """Whether the group has a standard deviation, which Welch's tests need."""
        return self.moments is not None and self.moments.spread is not None


def _group(one: Materialised, column: int, k: int | None, ends: float | None) -> _Group:
    """A position's values in order, then without *k* their moments and median, each a step
    after which the deadline is checked (module docstring)."""
    values = _weighted(one, ends)
    if k is not None or not values:
        return _Group(values)
    _within(column, float(values[0][0]), float(values[-1][0]))
    _deadline(ends)
    moments = stats.moments(values, ends)
    _deadline(ends)
    found = _Group(values, moments, stats.median(values, ends))
    _within(column, moments.mean, moments.sd, found.median)
    return found


def _summary(group: _Group, one: Materialised, seen: Shown, k: int | None) -> NumberSummary:
    statistics = ("/mean", "/sd", "/median")
    if not seen.split:
        return NumberSummary.model_validate(
            {
                "kind": "numbers",
                **dict.fromkeys(("n", "mean", "sd", "median")),
                "not_estimable": _nothing(_SUPPRESSED, "/n", *statistics),
            }
        )
    if group.moments is None:
        reason = _SUPPRESSED if k is not None else _NO_UNITS
        return NumberSummary.model_validate(
            {
                "kind": "numbers",
                "n": one.n,
                **dict.fromkeys(("mean", "sd", "median")),
                "not_estimable": _nothing(reason, *statistics),
            }
        )
    spread = group.moments.sd
    return NumberSummary(
        kind="numbers",
        n=one.n,
        mean=group.moments.mean,
        sd=spread,
        median=group.median,
        not_estimable=None if spread is not None else {"/sd": _ZERO_VARIANCE},
    )


def _not_computed(
    method: str, positions: Sequence[int], reason: NotEstimableReason, statistic: bool = True
) -> HypothesisTest:
    given: dict[str, object] = {"method": method, "positions": list(positions), "p": None}
    members = ["/p"]
    if statistic:
        given["statistic"] = None
        members.append("/statistic")
    given["not_estimable"] = _nothing(reason, *members)
    return HypothesisTest.model_validate(given)


def _welch(first: _Group, second: _Group) -> stats.Welch:
    """Welch's test of two groups with a standard deviation."""
    return stats.welch(cast(stats.Moments, first.moments), cast(stats.Moments, second.moments))


def _number_tests(
    groups: Sequence[_Group],
    cohorts: int,
    column: int,
    *,
    blocked: NotEstimableReason | None,
    ends: float | None,
) -> tuple[HypothesisTest, HypothesisTest]:
    """The primary and the secondary test of a numeric variable across positions (module
    docstring); ``blocked`` is overlap's or suppression's reason, which comes first. The rank
    test checks ``ends`` as it ranks (``stats._ranks``)."""
    used = [] if blocked is not None else [p for p, group in enumerate(groups) if group.values]
    two = cohorts == 2 or len(used) == 2
    primary, secondary = ("welch_t", "mann_whitney") if two else ("welch_anova", "kruskal_wallis")
    if blocked is not None:
        return _not_computed(primary, [], blocked), _not_computed(secondary, [], blocked)
    if len(used) < 2:
        return _not_computed(primary, used, _NO_UNITS), _not_computed(secondary, used, _NO_UNITS)
    chosen = [groups[p] for p in used]
    if not all(group.varied for group in chosen):
        first = _not_computed(primary, used, _ZERO_VARIANCE)
    elif len(used) == 2:
        found = _welch(chosen[0], chosen[1])
        _within(column, found.statistic, found.df)
        first = HypothesisTest(
            method=primary, positions=used, statistic=found.statistic, df=found.df, p=found.p
        )
    else:
        anova = stats.welch_anova([cast(stats.Moments, group.moments) for group in chosen])
        _within(column, anova.statistic, anova.denominator)
        first = HypothesisTest(
            method=primary,
            positions=used,
            statistic=anova.statistic,
            df=anova.numerator,
            df_denominator=anova.denominator,
            p=anova.p,
        )
    values = [group.values for group in chosen]
    ranked = (
        stats.mann_whitney(values[0], values[1], ends)
        if len(used) == 2
        else stats.kruskal_wallis(values, ends)
    )
    if ranked is None:
        return first, _not_computed(secondary, used, _ZERO_VARIANCE)
    _within(column, ranked.statistic)
    return first, HypothesisTest(
        method=secondary, positions=used, statistic=ranked.statistic, df=ranked.df, p=ranked.p
    )


def _mean_difference(
    mine: _Group,
    theirs: _Group,
    position: int,
    reference: int,
    level: float,
    column: int,
    blocked: NotEstimableReason | None,
) -> EffectSize:
    reason = (
        blocked if blocked is not None else None if mine.values and theirs.values else _NO_UNITS
    )
    estimate = low = high = None
    reasons: dict[str, NotEstimableReason] = {}
    if reason is not None:
        reasons = _nothing(reason, *_BOUNDS)
    else:
        first, second = cast(stats.Moments, mine.moments), cast(stats.Moments, theirs.moments)
        estimate = first.mean - second.mean
        if mine.varied and theirs.varied:
            low, high = _welch(mine, theirs).interval(level)
        else:
            reasons = _nothing(_ZERO_VARIANCE, "/ci/low", "/ci/high")
        _within(column, estimate, low, high)
    return EffectSize(
        measure=EffectMeasure.MEAN_DIFFERENCE,
        position=position,
        versus=reference,
        estimate=estimate,
        ci=Interval(method="welch_satterthwaite", level=level, low=low, high=high),
        not_estimable=reasons or None,
    )


def _numbers(
    found: Sequence[Materialised],
    visible: Sequence[Shown],
    column: int,
    *,
    reference: int,
    level: float,
    overlap: bool,
    k: int | None,
    computation: str,
    ends: float | None,
) -> _Compared:
    groups: list[_Group] = []
    for one in found:
        _deadline(ends)
        groups.append(_group(one, column, k, ends))
    at_positions: list[ColumnSummary] = [
        _summary(group, one, seen, k)
        for group, one, seen in zip(groups, found, visible, strict=True)
    ]
    cohorts = len(found)
    if cohorts < 2:
        return _Compared(at_positions, NumberComparison(kind="numbers", effects=[]))
    blocked = _OVERLAP if overlap else _SUPPRESSED if k is not None else None
    _deadline(ends)
    primary, secondary = _number_tests(groups, cohorts, column, blocked=blocked, ends=ends)
    replicates: dict[int, list[float]] = {}

    def resampled(position: int) -> list[float]:
        if position not in replicates:
            _deadline(ends)
            replicates[position] = stats.bootstrap_medians(
                groups[position].values, seed(computation, column, position), ends=ends
            )
        return replicates[position]

    effects: list[EffectSize] = []
    for position in range(cohorts):
        if position == reference:
            continue
        mine, theirs = groups[position], groups[reference]
        effects.append(_mean_difference(mine, theirs, position, reference, level, column, blocked))
        reason = (
            blocked if blocked is not None else None if mine.values and theirs.values else _NO_UNITS
        )
        estimate = low = high = None
        if reason is None:
            estimate = cast(float, mine.median) - cast(float, theirs.median)
            differences = [
                a - b for a, b in zip(resampled(position), resampled(reference), strict=True)
            ]
            low, high = stats.percentile_interval(differences, level)
            _within(column, estimate, low, high)
        effects.append(
            EffectSize(
                measure=EffectMeasure.MEDIAN_DIFFERENCE,
                position=position,
                versus=reference,
                estimate=estimate,
                ci=Interval(method="bootstrap_percentile", level=level, low=low, high=high),
                not_estimable=None if reason is None else _nothing(reason, *_BOUNDS),
            )
        )
    view = NumberComparison(kind="numbers", test=primary, secondary=secondary, effects=effects)
    return _Compared(at_positions, view)


# --- The analysis ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class Outcome:
    """A view's digested parts, before its digest is taken, and its caveats (D336)."""

    population: list[Population]
    analysed: list[Analysed]
    values: ColumnsValues
    caveats: list[Caveat]


def compare_columns(
    positions: Sequence[CohortAt],
    variables: Sequence[CanonicalVariable],
    materialised: Sequence[tuple[Sequence[Materialised], Joint | None]],
    params: ColumnsParams,
    *,
    reference: int,
    overlap: bool,
    k: int | None,
    computation: str,
    ends: float | None = None,
) -> Outcome:
    """``compare.columns`` over a view's cohorts and variables (module docstring), from each
    variable materialised over each cohort's units (``engine.sql``: counted by SQL, or by
    ``engine.variables`` from the reference evaluator's values); ``overlap`` says that the view
    allows overlap and its cohorts share units, and ``computation`` is the view's computation id,
    which seeds the bootstrap. Raises ``TooManyCategories``, ``LongCategory``,
    ``NonTextCategory``, ``TooLarge``, and ``CallerDeadline`` when ``time.monotonic()`` has passed
    ``ends`` before a column or one of its steps (module docstring)."""
    if len(materialised) != len(positions) or any(
        len(found) != len(variables) for found, _ in materialised
    ):
        raise ValueError("each variable materialised over each of the view's cohorts")
    population = populations(positions, k)
    visible = [
        [shown(one, counts.n_true is not None, k) for one in found]
        for (found, _), counts in zip(materialised, population, strict=True)
    ]
    compared: list[_Compared] = []
    for index, variable in enumerate(variables):
        _deadline(ends)
        found = [row[index] for row, _ in materialised]
        seen = [row[index] for row in visible]
        if categorical(variable):
            compared.append(
                _categories(
                    variable,
                    found,
                    seen,
                    index,
                    reference=reference,
                    level=params.level,
                    overlap=overlap,
                    k=k,
                    ends=ends,
                )
            )
        else:
            compared.append(
                _numbers(
                    found,
                    seen,
                    index,
                    reference=reference,
                    level=params.level,
                    overlap=overlap,
                    k=k,
                    computation=computation,
                    ends=ends,
                )
            )
    at_positions = [
        ColumnsPosition(columns=[one.at_positions[position] for one in compared])
        for position in range(len(positions))
    ]
    views = [one.view for one in compared]
    family: Family | None = None
    if len(positions) > 1:
        family, views = _family(views)
    values = ColumnsValues(positions=at_positions, view=ColumnsView(columns=views, family=family))
    analysed = [
        analysed_of(found, together, split, k)
        for (found, together), split in zip(materialised, visible, strict=True)
    ]
    caveats = _caveats(positions, population, analysed, materialised, compared, k, overlap)
    return Outcome(population, analysed, values, caveats)


def _family(views: Sequence[ColumnComparison]) -> tuple[Family, list[ColumnComparison]]:
    """The Benjamini–Hochberg family of the view's primary tests, and the columns with each
    computed one's q-value (§9.5)."""
    tests = [cast(HypothesisTest, view.test) for view in views]
    computed = [index for index, test in enumerate(tests) if test.p is not None]
    q_values = stats.benjamini_hochberg([cast(float, tests[i].p) for i in computed])
    given = dict(zip(computed, q_values, strict=True))
    suppressed = sum(test.reasons().get("/p") == _SUPPRESSED for test in tests)
    family = Family(
        tests=len(computed),
        not_computed=len(tests) - len(computed) - suppressed,
        suppressed=suppressed,
    )
    found: list[ColumnComparison] = []
    for index, view in enumerate(views):
        if index in given:
            test = tests[index].model_copy(update={"q": given[index]})
            view = view.model_copy(update={"test": HypothesisTest.model_validate(test)})
        found.append(view)
    return family, found


def _caveats(
    positions: Sequence[CohortAt],
    population: Sequence[Population],
    analysed: Sequence[Analysed],
    materialised: Sequence[tuple[Sequence[Materialised], Joint | None]],
    compared: Sequence[_Compared],
    k: int | None,
    overlap: bool,
) -> list[Caveat]:
    """The caveats that the view's data raise (module docstring); the static ones are the
    view's. ``UNKNOWN_EXCLUDED`` names the values wherever a unit is excluded from one for a
    reason other than ``NOT_APPLICABLE``, and always under *k*, so that it says nothing of one."""
    found_unknown = k is not None or any(
        count
        for row, _ in materialised
        for one in row
        for reason, count in one.excluded.items()
        if reason is not ExclusionReason.NOT_APPLICABLE
    )
    found = cohort_caveats(positions, population, k, values_unknown=found_unknown)
    found += flag_caveats(
        (mark for row, _ in materialised for one in row for mark in one.marks), "/values"
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
    for index, one in enumerate(compared):
        if one.small_expected:
            found.append(
                caveat(
                    CaveatCode.SMALL_N,
                    [f"/values/view/columns/{index}/test"],
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
            caveat(
                CaveatCode.SUPPRESSED,
                affects,
                text(
                    f"Counts from 1 to {k - 1}, what would reveal them and the values computed "
                    "from them are suppressed (null), and categories with such counts merged with "
                    "their neighbours, the tests and differences of categories computed from the "
                    "rows shown; no statistic, test or difference of numbers' values is "
                    "reported, and a column's undeclared values are one row that names none, "
                    "under the disclosure settings"
                ),
            )
        )
    return found


# --- Readback -------------------------------------------------------------------------------------


def view_readback(
    cohorts: int, reference: int, variables: Sequence[CanonicalVariable], params: ColumnsParams
) -> list[Segment]:
    """The view's readback (§7.7): a function of its canonical form and the release's
    descriptors, so cohorts are named by position, never by the names a document gives them."""
    level = data(number_text(params.level))
    found: list[Segment] = [
        text("For each column, in each cohort, the units of each category over those with a "),
        text("value, or for numbers n, mean, standard deviation and median"),
    ]
    if cohorts > 1:
        found += [
            text(f"; across the {cohorts} cohorts in view order, the reference being the cohort "),
            text(f"at position {reference}: for categories, the chi-squared test of the table of "),
            text("cohorts by categories (Fisher's exact test for two cohorts and two "),
            text("categories) and the difference in each category's proportion of each other "),
            text(
                "cohort versus the reference with Newcombe's hybrid score interval; for numbers, "
            ),
            text("Welch's t test (Welch's one-way test for more than two cohorts), the "),
            text("Mann-Whitney test (Kruskal-Wallis), and the differences in means with the "),
            text("Welch-Satterthwaite interval and in medians with a bootstrap percentile "),
            text(f"interval of {stats.REPLICATES} replicates versus the reference; intervals at "),
            level,
            text(", and Benjamini-Hochberg q-values across the columns' primary tests"),
        ]
    found.append(text("."))
    for index, variable in enumerate(variables):
        found += [text(f" Column {index}: "), *variable_readback(variable)]
        found.append(text("."))
    found.append(
        text(
            " A unit whose value cannot be decided, or that has none, is left out of that "
            "column's comparison and counted by reason."
        )
    )
    return found


__all__ = [
    "ANALYSIS_ID",
    "CAVEATS",
    "ENTRY",
    "METHODS",
    "VERSION",
    "Outcome",
    "compare_columns",
    "seed",
    "view_readback",
]

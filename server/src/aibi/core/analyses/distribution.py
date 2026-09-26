"""``summary.distribution``: each column's values in each cohort (SPEC §8.4, §9.2, §9.5; D328,
D329).

For each variable of the view (``params.columns``, D325) and each cohort in view order, what the
cohort's units hold: for **categories** (a category or boolean column, ``max`` or ``min`` of an
ordered category, and ``some`` or ``every``, whose categories are ``false`` and ``true``), the
units of each category over the units the variable has a value for; for **numbers** (a number,
integer or time-offset column, and ``count``, and ``mean``, ``max`` and ``min`` of numbers), n,
mean, standard deviation, median and quartiles (``stats``), minimum and maximum, and a histogram.
Categories are listed as the column declares them, its permissible values in their order, zeros
included, then, without a disclosure setting, the other values found in canonical order (strings
compared as UTF-16 code units, §9.3), at most ``MAX_CATEGORIES`` rows (``LIMIT_EXCEEDED``
otherwise, ``TooManyCategories``). Numbers' values and standard deviation lie within ±(2^53 − 1),
as an output's numbers do (§8.2; ``TooLarge`` otherwise, without a disclosure setting, the only
case that computes them); sums of doubles are scaled so that none overflows (``stats``).
A histogram's edges are the view's ``bins``, else the column's declared ``range`` in
``BINS_OF_A_RANGE`` equal bins (``count`` has none), else, without a disclosure setting, the
same between the least and the greatest value; bins are [eᵢ, eᵢ₊₁), the last closed, with an
open bin below the first edge and above the last. The analysis is descriptive: no test and no
contrast, so ``values.view`` is empty.

**Accounting** (§8.1). ``analysed`` counts, per position, the units a variable has a value for
(``n``) and those it excludes (``excluded_units``, and ``excluded`` by every reason): a column's
cell NOT_APPLICABLE, NOT_ASSESSED or empty, a missing row, a question UNKNOWN, an aggregate
UNKNOWN or with no value to aggregate (``NO_ROWS``) (``engine.variables``). With two variables
or more, ``variables`` gives each one's, and ``n`` counts the units some variable has a value
for, ``excluded`` those it has none for by every reason any gave.

**Estimability** (§9.5): with no value, every statistic and the histogram of a range the data
set is not estimable (``no_units``); the standard deviation of fewer than two values, or of
values all the same, is not (``zero_variance``).

**Disclosure** (§8.4, D329; ``disclosure``). Under *k*, each position's population is
disclosed as its cohort's count is; a position whose ``n_true`` is suppressed shows nothing of
its variables. Each variable's split of the cohort's units, ``n`` and ``excluded_units``, is
shown whole or not at all, and ``excluded`` only with it and with no small count. A category
column's values that it does not declare are one row, ``other_values``, that names none of them,
after the declared ones, whatever the units hold (so the rows, and the category limit, depend on
the declaration alone); rows, in their listed order, and bins are merged by the count rule. No
statistic computed from the values themselves is shown (mean, standard deviation, median,
quartiles, least and greatest, all ``suppressed``): values are not counts, so the rule that keeps
counts from 1 to *k* − 1 hidden cannot protect them, and a mean or standard deviation over coarse
bins gives the counts finer bins hide, or every unit's value when they are all the same. Only the
merged bins' counts and each quartile as the index of the merged bin that holds it
(``q1_bin``, ``median_bin``, ``q3_bin``) are shown, the index read off the bins' counts:
``null`` where the two values the quartile lies between are in two bins, since which holds it
would depend on the values. With two variables or more, ``analysed``'s ``n``,
``excluded_units`` and ``excluded``, which combine them, are suppressed. Under *k*, a number's
histogram needs edges the data do not set: its ``bins`` or its column's declared range, checked
in phase 2 (``needs_edges``).
"""

import bisect
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, cast

from aibi.core.analyses import stats
from aibi.core.analyses.common import (
    CohortAt,
    caveat,
    cohort_caveats,
    flag_caveats,
    populations,
)
from aibi.core.analyses.disclosure import merged, small, split_hidden
from aibi.core.engine.canonical import CanonicalVariable
from aibi.core.engine.readback import variable_readback
from aibi.core.engine.variables import Joint, Materialised, Value
from aibi.core.engine.worker import CallerDeadline
from aibi.core.schema.analyses import (
    BINS_OF_A_RANGE,
    CategoryDistribution,
    CategoryShare,
    ColumnDistribution,
    DistributionParams,
    DistributionPosition,
    DistributionValues,
    Histogram,
    HistogramBin,
    NoViewValues,
    NumberDistribution,
)
from aibi.core.schema.caveats import Caveat, CaveatCode
from aibi.core.schema.descriptors import AnalysisDescriptor
from aibi.core.schema.export import params_schema, values_schema
from aibi.core.schema.ids import MAX_SAFE_INTEGER
from aibi.core.schema.jsonio import utf16_key
from aibi.core.schema.limits import MAX_CATEGORIES, MAX_COHORTS
from aibi.core.schema.numbers import DenominatorDefinition, NotEstimableReason, Proportion
from aibi.core.schema.output import Data, Segment, text
from aibi.core.schema.results import Analysed, AnalysedCounts, AnalysedVariable, Population
from aibi.core.schema.semantics import ExclusionReason

ANALYSIS_ID = "summary.distribution"
VERSION = "1.0.0"
"""Bumped whenever its outputs for the same inputs change (§7.6)."""
METHODS: Mapping[str, str] = {
    "mean": "The exact mean, correctly rounded: the exact sum of integers, or math.fsum of "
    "doubles, divided once (R's mean agrees but under catastrophic cancellation)",
    "standard_deviation": "The sample standard deviation, as R's sd: math.fsum of the squared "
    "deviations from the mean over n - 1",
    "quantile_type_7": "Quartiles and median as R's quantile(type = 7), implemented directly",
    "histogram": "Bins [e_i, e_i+1), the last closed, with open bins below and above, from the "
    "view's bins, the column's declared range in 10 equal bins, or without disclosure settings "
    "the data's",
}
CAVEATS = (
    CaveatCode.UNKNOWN_EXCLUDED,
    CaveatCode.SCOPE_PARTIAL,
    CaveatCode.COVERAGE_PROPOSED,
    CaveatCode.UNCONFIRMED_SEMANTICS,
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
        "label": "Distribution of columns in each cohort",
        "definition": (
            "Per column and cohort: for categories, the units per category over those whose "
            "value is known; for numbers, n, mean, standard deviation, median, quartiles, "
            "minimum, maximum and a histogram; each column's excluded units by reason. "
            "Descriptive only."
        ),
        "fields": {
            "requires": [
                {"role": "cohorts", "min": 1, "max": MAX_COHORTS},
                {"role": "columns", "kind": "column", "min": 1},
            ],
            "params": params_schema(DistributionParams),
            "returns": values_schema(DistributionValues),
            "methods": dict(METHODS),
            "assumptions": ["descriptive only"],
            "uses_reference": False,
            "assumes_independent_groups": False,
            "cross_dataset": None,
            "caveats": [code.value for code in CAVEATS],
        },
    }
)
"""The registry entry (§9.1): implemented directly, with no library."""

_SUPPRESSED = NotEstimableReason.SUPPRESSED
_NO_UNITS = NotEstimableReason.NO_UNITS
_CATEGORIES = ("category", "boolean")
_NUMBERS = ("number", "integer", "time_offset")
_STATISTICS = ("/mean", "/sd", "/median", "/q1", "/q3", "/min", "/max")


class TooLarge(Exception):  # noqa: N818 - a limit reached, as the refusal names it
    """A numeric variable without a disclosure setting whose values, or their standard
    deviation, lie beyond ±(2^53 − 1), which an output does not hold (§8.2): ``column`` is its
    index among the view's columns."""

    def __init__(self, column: int) -> None:
        super().__init__(f"column {column}")
        self.column = column


class TooManyCategories(Exception):  # noqa: N818 - a limit reached, as the refusal names it
    """A categorical variable whose values at some position are more than ``MAX_CATEGORIES``:
    ``column`` is its index among the view's columns."""

    def __init__(self, column: int) -> None:
        super().__init__(f"column {column}")
        self.column = column


# --- Variables ------------------------------------------------------------------------------------


def categorical(variable: CanonicalVariable) -> bool:
    """Whether a variable's values are categories (module docstring), else numbers."""
    resolved = variable.resolved
    if resolved.kind == "question":
        return True
    if resolved.kind == "aggregate":
        return resolved.order is not None
    return resolved.datatype in _CATEGORIES


def summarised(variable: CanonicalVariable) -> bool:
    """Whether the analysis summarises a variable's values: categories or numbers."""
    resolved = variable.resolved
    return resolved.kind != "column" or resolved.datatype in (*_CATEGORIES, *_NUMBERS)


def declared_range(variable: CanonicalVariable) -> tuple[float, float] | None:
    """The column's declared range, which a histogram of its values, or of their ``mean``,
    ``max`` or ``min``, divides; ``count``'s values have none."""
    resolved = variable.resolved
    if resolved.kind == "question" or resolved.function == "count":
        return None
    table, column = resolved.column.split(".", 1)
    descriptor = resolved.release.column(table, column)
    declared = None if descriptor is None else descriptor.fields.range
    if declared is None or isinstance(declared.min, str) or isinstance(declared.max, str):
        return None
    return float(declared.min), float(declared.max)


def needs_edges(variable: CanonicalVariable, bins: Sequence[float] | None) -> bool:
    """Whether a number's histogram would take its edges from the data, which a disclosure
    setting does not allow (§8.4)."""
    return not categorical(variable) and bins is None and declared_range(variable) is None


def _categories(variable: CanonicalVariable) -> list[Value]:
    """The categories a variable declares, in their order: a column's permissible values (an
    ordered category's, for ``max`` and ``min``), or ``false`` and ``true``."""
    resolved = variable.resolved
    if resolved.kind == "question" or resolved.datatype == "boolean":
        return [False, True]
    if resolved.order is not None:
        return list(resolved.order)
    table, column = resolved.column.split(".", 1)
    descriptor = resolved.release.column(table, column)
    allowed = None if descriptor is None else descriptor.fields.permissible_values
    return [] if allowed is None else [entry.value for entry in allowed.values]


def _label(value: Value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


# --- Values ---------------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Shown:
    """What the pass shows of a variable at a position: its split, and its breakdown."""

    split: bool
    excluded: bool


def _shown(found: Materialised, size_shown: bool, k: int | None) -> _Shown:
    if k is None:
        return _Shown(True, True)
    split = size_shown and not split_hidden(found.n, found.excluded_units, k)
    excluded = split and not any(small(k, count) for count in found.excluded.values())
    return _Shown(split, excluded)


def _proportion(count: int, n: int, position: int) -> Proportion:
    reasons = None if n else dict.fromkeys(("/estimate",), _NO_UNITS)
    return Proportion.model_validate(
        {
            "estimate": count / n if n else None,
            "numerator": count,
            "denominator": n,
            "denominator_definition": DenominatorDefinition(
                position=position, predicate=None, counts="known"
            ),
            "not_estimable": reasons,
        }
    )


def _category_distribution(
    variable: CanonicalVariable,
    found: Materialised,
    position: int,
    column: int,
    shown: _Shown,
    k: int | None,
) -> CategoryDistribution:
    declared = _categories(variable)
    undeclared = [value for value in found.values if value not in declared]
    open_list = _open(variable)
    if k is None:
        others = sorted(undeclared, key=lambda value: utf16_key(_label(value)))
        rows: list[tuple[list[Value], bool]] = [([value], False) for value in [*declared, *others]]
    else:
        rows = [([value], False) for value in declared] + ([([], True)] if open_list else [])
    if len(rows) > MAX_CATEGORIES:
        raise TooManyCategories(column)
    if not shown.split:
        return CategoryDistribution(
            kind="categories", categories=None, not_estimable={"/categories": _SUPPRESSED}
        )
    counts = [
        sum(found.values.get(value, 0) for value in listed)
        + (sum(found.values[value] for value in undeclared) if other else 0)
        for listed, other in rows
    ]
    spans = [(at, at) for at in range(len(rows))] if k is None else merged(counts, k)
    shares = [
        CategoryShare(
            values=[
                Data(data=_label(value)) for listed, _ in rows[start : end + 1] for value in listed
            ],
            other_values=True if any(other for _, other in rows[start : end + 1]) else None,
            proportion=_proportion(sum(counts[start : end + 1]), found.n, position),
        )
        for start, end in spans
    ]
    return CategoryDistribution(kind="categories", categories=shares)


def _open(variable: CanonicalVariable) -> bool:
    """Whether a variable's values may lie outside its declared categories: a category column's
    (an ordered category's ``max`` and ``min`` read none outside its list, and a boolean or a
    question has none)."""
    resolved = variable.resolved
    return resolved.kind == "column" and resolved.datatype == "category"


EdgesFrom = Literal["params", "range", "data"]


def _edges(
    variable: CanonicalVariable, bins: Sequence[float] | None, weighted: stats.Weighted
) -> tuple[list[float], EdgesFrom] | None:
    """A histogram's edges and where they came from (module docstring); ``None`` for data
    edges without data."""
    found = effective_edges(variable, bins)
    if found is not None:
        return found, "params" if bins is not None else "range"
    if not weighted:
        return None
    return _equal(float(weighted[0][0]), float(weighted[-1][0])), "data"


def _equal(low: float, high: float) -> list[float]:
    """``BINS_OF_A_RANGE`` equal bins from ``low`` to ``high``, one bin for a range of one
    value, as catalogue statistics divide one (D270); a range too narrow for that many doubles
    between its ends (a few ulps wide) has its repeated edges collapsed, so that no bin is
    empty by construction and no two are alike (D328)."""
    if low == high:
        return [low, high]
    edges = [low + (high - low) * i / BINS_OF_A_RANGE for i in range(BINS_OF_A_RANGE)] + [high]
    return list(dict.fromkeys(edges))


def effective_edges(
    variable: CanonicalVariable, bins: Sequence[float] | None
) -> list[float] | None:
    """The edges a number's histogram takes without data: its ``bins``, else its declared
    range's equal bins, else ``None`` (the data's, which only a view without *k* takes)."""
    if bins is not None:
        return list(bins)
    declared = declared_range(variable)
    return None if declared is None else _equal(*declared)


def histogram(edges: Sequence[float], weighted: stats.Weighted) -> list[HistogramBin]:
    """Values counted into bins [eᵢ, eᵢ₊₁), the last closed, with an open bin below the first
    edge and one above the last (§8.4)."""
    counts = [0] * (len(edges) + 1)
    last = len(edges) - 1
    for value, times in weighted:
        number = float(value)
        if number < edges[0]:
            counts[0] += times
        elif number > edges[last]:
            counts[-1] += times
        elif number == edges[last]:
            counts[last] += times
        else:
            counts[bisect.bisect_right(edges, number)] += times
    return [
        HistogramBin(
            low=None if index == 0 else edges[index - 1],
            high=None if index == last + 1 else edges[index],
            includes_low=0 < index <= last,
            includes_high=index == last,
            count=count,
        )
        for index, count in enumerate(counts)
    ]


def _joined(bins: Sequence[HistogramBin], spans: Sequence[tuple[int, int]]) -> list[HistogramBin]:
    return [
        HistogramBin(
            low=bins[start].low,
            high=bins[end].high,
            includes_low=bins[start].includes_low,
            includes_high=bins[end].includes_high,
            count=sum(one.count for one in bins[start : end + 1]),
        )
        for start, end in spans
    ]


def _rank_bin(bins: Sequence[HistogramBin], rank: int) -> int:
    """The index of the bin that holds the value of a rank, counted from 0 in increasing order:
    the bins are intervals in increasing order, so it is read off their counts."""
    seen = 0
    for index, one in enumerate(bins):
        seen += one.count
        if rank < seen:
            return index
    raise ValueError("a rank is less than the number of values")


def _quantile_bin(
    bins: Sequence[HistogramBin], n: int, numerator: int, denominator: int
) -> int | None:
    """The bin that holds the quantile at ``numerator / denominator`` (R's type 7) of ``n``
    values, read off the bins' counts alone: the bin of the order statistic it starts from,
    when the one it moves towards, if any, is in the same bin; ``None`` when they are in two,
    since the bin of a value between them would depend on the values, which the bins' edges
    bound (D329)."""
    low, remainder = divmod((n - 1) * numerator, denominator)
    found = _rank_bin(bins, low)
    if remainder and _rank_bin(bins, low + 1) != found:
        return None
    return found


_QUARTILES = (("q1", 1, 4), ("median", 1, 2), ("q3", 3, 4))


def _number_distribution(
    variable: CanonicalVariable,
    found: Materialised,
    column: int,
    bins: Sequence[float] | None,
    shown: _Shown,
    k: int | None,
) -> NumberDistribution:
    bins_of = ("q1_bin", "median_bin", "q3_bin") if k is not None else ()
    if not shown.split:
        return NumberDistribution.model_validate(
            {
                "kind": "numbers",
                "n": None,
                **dict.fromkeys(("mean", "sd", "median", "q1", "q3", "min", "max", *bins_of)),
                "histogram": None,
                "not_estimable": dict.fromkeys(
                    ("/n", *_STATISTICS, "/histogram", *(f"/{name}" for name in bins_of)),
                    _SUPPRESSED,
                ),
            }
        )
    weighted: stats.Weighted = sorted(
        (cast(int | float, value), times) for value, times in found.values.items() if times
    )
    n = found.n
    reasons: dict[str, NotEstimableReason] = {}
    members: dict[str, object] = {"kind": "numbers", "n": n}
    members.update(dict.fromkeys(("mean", "sd", "median", "q1", "q3", "min", "max")))
    if k is None and weighted:
        least, most = float(weighted[0][0]), float(weighted[-1][0])
        if max(abs(least), abs(most)) > MAX_SAFE_INTEGER:
            raise TooLarge(column)
    edges = _edges(variable, bins, weighted)
    counted: list[HistogramBin] | None = None
    members["histogram"] = None
    if edges is None:
        reasons["/histogram"] = _NO_UNITS
    else:
        counted = histogram(edges[0], weighted)
        if k is not None:
            counted = _joined(counted, merged([one.count for one in counted], k))
        members["histogram"] = Histogram(edges_from=edges[1], bins=counted)
    if k is not None:
        assert counted is not None, "under a disclosure setting a histogram has edges"
        reasons.update(dict.fromkeys(_STATISTICS, _SUPPRESSED))
        for name, numerator, denominator in _QUARTILES:
            at = _quantile_bin(counted, n, numerator, denominator) if n else None
            members[f"{name}_bin"] = at
            if at is None:
                reasons[f"/{name}_bin"] = _SUPPRESSED if n else _NO_UNITS
    elif not weighted:
        reasons.update(dict.fromkeys(_STATISTICS, _NO_UNITS))
    else:
        centre = stats.mean(weighted)
        spread = stats.sd(weighted, centre)
        members.update(
            mean=centre,
            sd=spread,
            q1=stats.quantile(weighted, 0.25),
            median=stats.quantile(weighted, 0.5),
            q3=stats.quantile(weighted, 0.75),
            min=float(weighted[0][0]),
            max=float(weighted[-1][0]),
        )
        if spread is None:
            reasons["/sd"] = NotEstimableReason.ZERO_VARIANCE
        elif spread > MAX_SAFE_INTEGER:
            raise TooLarge(column)
    members["not_estimable"] = reasons or None
    return NumberDistribution.model_validate(members)


# --- The analysis ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class Outcome:
    """A view's digested parts, before its digest is taken, and its caveats (D328)."""

    population: list[Population]
    analysed: list[Analysed]
    values: DistributionValues
    caveats: list[Caveat]


def summarise(
    positions: Sequence[CohortAt],
    variables: Sequence[CanonicalVariable],
    materialised: Sequence[tuple[Sequence[Materialised], Joint | None]],
    params: DistributionParams,
    *,
    k: int | None,
    ends: float | None = None,
) -> Outcome:
    """``summary.distribution`` over a view's cohorts and variables (module docstring), from
    each variable materialised over each cohort's units (``engine.sql``: counted by SQL, or by
    ``engine.variables`` from the reference evaluator's values). Raises
    ``TooManyCategories``, ``TooLarge``, and ``CallerDeadline`` when ``time.monotonic()`` has
    passed ``ends`` (the call's deadline) before a column is summarised, so that the call
    overruns it by one column's statistics at most (D327)."""
    if len(materialised) != len(positions) or any(
        len(found) != len(variables) for found, _ in materialised
    ):
        raise ValueError("each variable materialised over each of the view's cohorts")
    population = populations(positions, k)
    at_positions: list[DistributionPosition] = []
    analysed: list[Analysed] = []
    for position, ((found, together), counts) in enumerate(
        zip(materialised, population, strict=True)
    ):
        size_shown = counts.n_true is not None
        shown = [_shown(one, size_shown, k) for one in found]
        columns: list[ColumnDistribution] = []
        for index, (variable, one, visible) in enumerate(zip(variables, found, shown, strict=True)):
            if ends is not None and time.monotonic() >= ends:
                raise CallerDeadline
            if categorical(variable):
                columns.append(_category_distribution(variable, one, position, index, visible, k))
            else:
                bins = params.columns[index].bins
                columns.append(_number_distribution(variable, one, index, bins, visible, k))
        at_positions.append(DistributionPosition(columns=columns))
        analysed.append(_analysed(found, together, shown, k))
    values = DistributionValues(positions=at_positions, view=NoViewValues())
    caveats = _caveats(positions, population, analysed, materialised, k)
    return Outcome(population, analysed, values, caveats)


def _counts(
    model: type[AnalysedCounts],
    n: int,
    excluded: Mapping[ExclusionReason, int],
    units: int,
    shown: _Shown,
) -> AnalysedCounts:
    reasons = {
        member: _SUPPRESSED
        for member, lost in (
            ("/n", not shown.split),
            ("/excluded_units", not shown.split),
            ("/excluded", not shown.excluded),
        )
        if lost
    }
    return model(
        n=n if shown.split else None,
        excluded=dict(excluded) if shown.excluded else None,
        excluded_units=units if shown.split else None,
        not_estimable=reasons or None,
    )


def _analysed(
    found: Sequence[Materialised], together: Joint | None, shown: Sequence[_Shown], k: int | None
) -> Analysed:
    if len(found) == 1:
        [one], [visible] = found, shown
        counted = _counts(AnalysedCounts, one.n, one.excluded, one.excluded_units, visible)
        return Analysed.model_validate(counted.model_dump())
    assert together is not None, "several variables are counted together"
    variables = [
        cast(
            AnalysedVariable,
            _counts(AnalysedVariable, one.n, one.excluded, one.excluded_units, visible),
        )
        for one, visible in zip(found, shown, strict=True)
    ]
    whole = _Shown(k is None, k is None)
    counted = _counts(AnalysedCounts, together.known, together.none_by_reason, together.none, whole)
    return Analysed.model_validate({**counted.model_dump(), "variables": variables})


def _caveats(
    positions: Sequence[CohortAt],
    population: Sequence[Population],
    analysed: Sequence[Analysed],
    materialised: Sequence[tuple[Sequence[Materialised], Joint | None]],
    k: int | None,
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
                    "from them are suppressed (null), and categories and histogram bins with such "
                    "counts merged with their neighbours; no statistic of the values themselves "
                    "is reported, each quartile being given as the bin that holds it, and a "
                    "column's undeclared values are one row that names none, under the "
                    "disclosure settings"
                ),
            )
        )
    return found


# --- Readback -------------------------------------------------------------------------------------


def view_readback(cohorts: int, variables: Sequence[CanonicalVariable]) -> list[Segment]:
    """The view's readback (§7.7): a function of its canonical form and the release's
    descriptors, so cohorts are counted, never named."""
    found: list[Segment] = [
        text(f"For each of the {cohorts} cohorts in view order, " if cohorts > 1 else ""),
        text("the distribution of each column over the cohort's units: for categories, the "),
        text("units of each category over those with a value; for numbers, n, mean, standard "),
        text("deviation, median, quartiles, minimum, maximum and a histogram."),
    ]
    for index, variable in enumerate(variables):
        found += [text(f" Column {index}: "), *variable_readback(variable)]
        found.append(text("."))
    found.append(
        text(
            " A unit whose value cannot be decided, or that has none, is left out of that "
            "column's distribution and counted by reason."
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
    "TooLarge",
    "TooManyCategories",
    "categorical",
    "declared_range",
    "effective_edges",
    "histogram",
    "needs_edges",
    "summarise",
    "summarised",
    "view_readback",
]

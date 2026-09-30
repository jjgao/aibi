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
otherwise, ``TooManyCategories``), each label at most ``MAX_TEXT`` characters (``LongCategory``,
D382) and Unicode text (``NonTextCategory``, ``NOT_SUPPORTED``). Numbers' values and standard
deviation lie within ±(2^53 − 1), as an output's numbers do (§8.2; ``TooLarge`` otherwise,
without a disclosure setting, the only case that computes them); sums of doubles are scaled so
that none overflows (``stats``).
A histogram's edges are the view's ``bins``, else the column's declared ``range`` in
``BINS_OF_A_RANGE`` equal bins (``count`` has none), else, without a disclosure setting, the
same between the least and the greatest value; bins are [eᵢ, eᵢ₊₁), the last closed, with an
open bin below the first edge and above the last. The analysis is descriptive: no test and no
contrast, so ``values.view`` is empty.

**Rows** (§9.2, D378). A variable that counts rows (``count: "rows"``) summarises the rows a
numeric aggregate of its column would pool, each once for every path and unit that reaches it:
for categories (a category or boolean column) the rows of each category over the rows with a
value (``counts: "rows"``), listed as a column's categories are without a disclosure setting; for
numbers, n rows and the same statistics and histogram over their values (a declared range divides
it as it does the column's). A pooled row whose value is not PRESENT is left out of them and
counted by reason in ``excluded_rows``, and never excludes its unit; ``analysed`` counts the
units, those pooled (with no rows included) and those excluded, for their pooling's reasons, as
an aggregate's are. It is withheld under any disclosure setting (D379), which phase 2 refuses and
``summarise`` asserts.

**Memberships** (§9.2, D380, D382). A variable's memberships (``each: "category"``) give, for
each category listed over the cohort (``engine.memberships``: the declared values in their order,
zeros included, then the others in UTF-16 order, or a filtered column's allowed values), the
units for which some row (or item) has it over the units for which that is known, its UNKNOWN
units left out of that category alone and counted by reason in the proportion's ``excluded``, its
denominator definition naming the category's leaf key (``canonical.category_key``, as
``compare.existence`` names a predicate's); a unit may count in several categories, so no sum ties
them to ``n`` (``multi_membership``). ``analysed`` counts a unit where its answer for some listed
category is known, which depends on the categories the cohort lists, and excludes it otherwise
under the reasons of all its answers. More than ``MAX_CATEGORIES`` listed is ``TooManyCategories``.
Under a disclosure setting they list the column's declared categories alone, whatever the units
hold (``memberships.listing(…, declared=True)``, D384), each row disclosed as a question's split
(``disclosure.membership_shown``, D383), with no map of reasons, and ``analysed`` of the variable
suppressed; where the descriptors bound the rows each unit reaches (``withheld_under_k``) they
are withheld, which phase 2 refuses and ``summarise`` asserts.

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
in phase 2 (``needs_edges``). A variable's memberships list its declared categories, each shown
as ``some`` of that one category would be (its split whole or not at all, its TRUE units only
with it and where neither they nor its FALSE units are small, never its reasons), rows never
merged, and its ``analysed`` suppressed, alone or beside others (D383).
"""

import bisect
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
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
    unwritable,
)
from aibi.core.analyses.disclosure import CategoryShown, Split, membership_shown, merged
from aibi.core.engine.canonical import CanonicalVariable, category_key
from aibi.core.engine.memberships import bounded_rows, fixed
from aibi.core.engine.readback import variable_readback
from aibi.core.engine.resolve import ResolvedVariable, aggregates_of, aggregates_taken
from aibi.core.engine.variables import Joint, Materialised, Membership, RowCounts, Value
from aibi.core.engine.worker import CallerDeadline
from aibi.core.schema.analyses import (
    BINS_OF_A_RANGE,
    CategoryDistribution,
    CategoryRows,
    CategoryShare,
    ColumnDistribution,
    DistributionParams,
    DistributionPosition,
    DistributionValues,
    Histogram,
    HistogramBin,
    MembershipDistribution,
    NoViewValues,
    NumberDistribution,
    NumberRows,
    Variable,
)
from aibi.core.schema.caveats import Caveat, CaveatCode
from aibi.core.schema.descriptors import AnalysisDescriptor, ColumnDescriptor
from aibi.core.schema.export import params_schema, values_schema
from aibi.core.schema.ids import MAX_SAFE_INTEGER
from aibi.core.schema.jsonio import utf16_key
from aibi.core.schema.limits import MAX_CATEGORIES, MAX_COHORTS
from aibi.core.schema.numbers import DenominatorDefinition, NotEstimableReason, Proportion
from aibi.core.schema.output import Data, Segment, text
from aibi.core.schema.results import Analysed, Population
from aibi.core.schema.semantics import ExclusionReason

ANALYSIS_ID = "summary.distribution"
VERSION = "1.0.0"
"""Bumped whenever its outputs for the same inputs change (§7.6)."""
METHODS: Mapping[str, str] = {
    "mean": "The exact mean, correctly rounded: the values' exact sum, as integers over a power "
    "of two, divided once (R's mean agrees but under catastrophic cancellation)",
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
            "minimum, maximum and a histogram; each column's excluded units by reason. A "
            'column with count "rows" gives the same over the rows its units reach, with the '
            "rows excluded by reason, and is withheld under a disclosure setting. "
            'A column with each "category" gives its memberships: for each category, the units '
            "for which some row or item has it over those for which that is known, its excluded "
            "units by reason, a unit counting in each category it has (multi_membership); under "
            "a disclosure setting, its declared categories alone, each disclosed as a question's "
            "split, with no excluded units by reason, and withheld where the descriptors bound "
            "the rows each unit reaches. Descriptive only."
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
SUMMARISED = (*_CATEGORIES, *_NUMBERS)
"""The datatypes of the columns the analysis summarises as they are, as ``compare.columns``
compares them: a column of another is ``NOT_SUPPORTED`` (``summarised``)."""
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


class LongCategory(ValueError):  # noqa: N818 - raised like a limit's refusal
    """A categorical variable, ``column`` its index among the view's columns, a category of which,
    shown as data, is longer than ``MAX_TEXT`` characters, more than an output's text holds
    (``within_text``; D368, D382)."""

    def __init__(self, column: int) -> None:
        super().__init__(f"column {column}")
        self.column = column


class NonTextCategory(ValueError):  # noqa: N818 - raised like a limit's refusal
    """A categorical variable, ``column`` its index among the view's columns, a category of which,
    shown as data, is not Unicode text (a lone surrogate or a noncharacter), which no output's
    text holds (§8.2; ``within_text``, D382)."""

    def __init__(self, column: int) -> None:
        super().__init__(f"column {column}")
        self.column = column


# --- Variables ------------------------------------------------------------------------------------


def categorical(variable: CanonicalVariable) -> bool:
    """Whether a variable's values are categories (module docstring), else numbers: a
    variable's memberships are categories, a list's included, so that, taking no ``bins``, their
    canonical parameters hold none (``views``' canonical parameters, D382)."""
    resolved = variable.resolved
    if resolved.kind in ("question", "memberships"):
        return True
    if resolved.kind == "aggregate":
        return resolved.order is not None
    return resolved.datatype in _CATEGORIES


def counts_rows(variable: CanonicalVariable) -> bool:
    """Whether a variable counts rows (``count: "rows"``, D378)."""
    return variable.resolved.kind == "rows"


def memberships(variable: CanonicalVariable) -> bool:
    """Whether a variable gives its memberships of each category (``each``, D380)."""
    return variable.resolved.kind == "memberships"


def summarised(variable: CanonicalVariable) -> bool:
    """Whether the analysis summarises a variable's values (``summarises``)."""
    return summarises(variable.resolved)


def summarises(variable: ResolvedVariable) -> bool:
    """Whether the analysis summarises a resolved variable's values: categories or numbers, a
    column's or a count of rows' own; a form a refusal offers is checked so too (D380)."""
    return variable.kind not in ("column", "rows") or variable.datatype in SUMMARISED


def declared_range(variable: CanonicalVariable) -> tuple[float, float] | None:
    """The column's declared range, which a histogram of its values, or of their ``mean``,
    ``max`` or ``min``, divides; ``count``'s values have none."""
    resolved = variable.resolved
    if resolved.kind == "question" or resolved.function == "count":
        return None
    return _range_of(_descriptor(variable))


def _descriptor(variable: CanonicalVariable) -> ColumnDescriptor | None:
    resolved = variable.resolved
    table, column = resolved.column.split(".", 1)
    return resolved.release.column(table, column)


def _range_of(descriptor: ColumnDescriptor | None) -> tuple[float, float] | None:
    declared = None if descriptor is None else descriptor.fields.range
    if declared is None or isinstance(declared.min, str) or isinstance(declared.max, str):
        return None
    return float(declared.min), float(declared.max)


WITHHELD_MEMBERSHIPS = (
    "the descriptors bound how many rows, and so how many of the column's categories, each unit "
    "can hold, so the categories' units sum to at most that many times the cohort's and would "
    "pin a count the pass hides"
)
"""Why the analysis withholds memberships where the descriptors bound the rows each unit
reaches."""


def withheld_under_k(variable: CanonicalVariable) -> str | None:
    """Why the analysis withholds a variable under any disclosure setting whatever its cohorts'
    sizes, by what its descriptors say rather than by a member of its parameters
    (``registry.withheld_form``), or ``None``: memberships where the descriptors may bound the
    rows each unit reaches (``memberships.bounded_rows``: not a list, and its path not one down
    step from the unit whose child's every set of columns the gate keeps unique holds a free
    column, ``resolve.open_path``), whose TRUE counts then may sum to at most that bound times the
    units, so that shown ones bound a hidden one (10 units under *k* = 3, one row each: 5, 4 and
    a hidden one give it 1, since 0 would be shown; two rows each, four categories: 10, 5, 4 and
    a hidden one give it 1, D383). Phase 2 refuses them at ``…/each`` (``views._withheld_form``),
    and ``summarise`` and ``run_analysis`` assert none runs."""
    if memberships(variable) and bounded_rows(variable.resolved):
        return WITHHELD_MEMBERSHIPS
    return None


def forms_under_k(variable: CanonicalVariable, written: Variable | None = None) -> list[str]:
    """The forms of a variable the analysis withholds which it gives under a disclosure setting
    in its place, each of which runs there and the analysis reads (``summarises``: an aggregate
    or a question). For memberships where the descriptors bound the rows each unit reaches
    (``withheld_under_k``, D382, D383): ``some`` and ``every`` with ``values`` where the path
    allows them (``resolve.aggregates_of``, as resolution offers them: ``every`` of a scope column
    of the last step's child row is ``SCOPE_COLUMN_MENTION``, and ``some`` always runs), each an
    existence question whose split the pass protects as a question's (D329), one category at a
    time; the variable took no ``where`` or ``bins``. For a count of rows (D379):
    each aggregate its column takes over its path (``resolve.aggregates_of``; with a ``where``,
    whose conditions close every step for the count of rows as they do for an aggregate, each
    it takes, ``aggregates_taken``), with ``bins`` where its histogram would otherwise take edges
    from the data (``needs_edges``: always for ``count``, whose values have no range, and for a
    number's ``max``, ``min`` and ``mean`` where its column declares none), and ``some`` and
    ``every`` with ``values``. Of the variable as ``written``, its ``where`` and ``bins`` are
    kept: ``some`` and ``every``, which take neither, are not offered beside them, and an
    aggregate whose values are categories is offered without the ``bins``."""
    if memberships(variable):
        return [
            f'aggregate: "{name}" with values'
            for name in aggregates_of(variable.resolved)
            if name in ("some", "every")
        ]
    descriptor = _descriptor(variable)
    if descriptor is None:
        return []
    numeric = descriptor.fields.datatype in _NUMBERS
    ranged = _range_of(descriptor) is not None
    where = written is not None and written.where is not None
    binned = written is not None and written.bins is not None
    found: list[str] = []
    for name in aggregates_taken(descriptor) if where else aggregates_of(variable.resolved):
        given = f'aggregate: "{name}"'
        if name in ("some", "every"):
            if where or binned:
                continue
            given += " with values"
        elif name == "count" or numeric:
            if not binned and (name == "count" or not ranged):
                given += " with bins"
        elif binned:
            given += " without bins"
        found.append(given)
    return found


def needs_edges(variable: CanonicalVariable, bins: Sequence[float] | None) -> bool:
    """Whether a number's histogram would take its edges from the data, which a disclosure
    setting does not allow (§8.4)."""
    return not categorical(variable) and bins is None and declared_range(variable) is None


def declared_categories(variable: CanonicalVariable) -> list[Value]:
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


def category_label(value: Value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def within_text(values: Iterable[Value], column: int) -> None:
    """Checks that an output's text holds each label (``category_label``) of the categories a
    result lists of the view's column ``column`` (``common.unwritable``, D271's rule): one longer
    than ``MAX_TEXT`` characters raises ``LongCategory``, refused as ``LIMIT_EXCEEDED`` as
    ``cox.LongLevel`` is (D368), and one that is not Unicode text ``NonTextCategory``, refused as
    ``NOT_SUPPORTED`` (D382), the first such label in the listing's order deciding, for a
    column's categories, a count of rows' and memberships' alike, whatever the disclosure pass
    then shows of them."""
    for value in values:
        why = unwritable(category_label(value))
        if why == "long":
            raise LongCategory(column)
        if why == "not_text":
            raise NonTextCategory(column)


# --- Values ---------------------------------------------------------------------------------------


def _proportion(
    count: int, n: int, position: int, counts: Literal["known", "rows"] = "known"
) -> Proportion:
    reasons = None if n else dict.fromkeys(("/estimate",), _NO_UNITS)
    return Proportion.model_validate(
        {
            "estimate": count / n if n else None,
            "numerator": count,
            "denominator": n,
            "denominator_definition": DenominatorDefinition(
                position=position, predicate=None, counts=counts
            ),
            "not_estimable": reasons,
        }
    )


def _category_distribution(
    variable: CanonicalVariable,
    found: Materialised,
    position: int,
    column: int,
    shown: Shown,
    k: int | None,
) -> CategoryDistribution:
    declared = declared_categories(variable)
    undeclared = [value for value in found.values if value not in declared]
    open_list = open_categories(variable)
    if k is None:
        rows: list[tuple[list[Value], bool]] = [
            ([value], False) for value in _listed(declared, found.values)
        ]
    else:
        rows = [([value], False) for value in declared] + ([([], True)] if open_list else [])
    if len(rows) > MAX_CATEGORIES:
        raise TooManyCategories(column)
    within_text((value for listed, _ in rows for value in listed), column)
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
                Data(data=category_label(value))
                for listed, _ in rows[start : end + 1]
                for value in listed
            ],
            other_values=True if any(other for _, other in rows[start : end + 1]) else None,
            proportion=_proportion(sum(counts[start : end + 1]), found.n, position),
        )
        for start, end in spans
    ]
    return CategoryDistribution(kind="categories", categories=shares)


def _listed(declared: Sequence[Value], values: Iterable[Value]) -> list[Value]:
    """The categories a distribution lists without a disclosure setting (D329, D378): the
    declared ones in their order, zeros included, then the others ``values`` hold, in the UTF-16
    order of their labels."""
    others = [value for value in values if value not in declared]
    return [*declared, *sorted(others, key=lambda value: utf16_key(category_label(value)))]


def _category_rows(
    variable: CanonicalVariable, rows: RowCounts, position: int, column: int
) -> CategoryRows:
    """A count of rows' categories (D378), listed as a column's are without a disclosure setting
    (``_listed``), each the rows in it over the rows with a value."""
    listed = _listed(declared_categories(variable), rows.values)
    if len(listed) > MAX_CATEGORIES:
        raise TooManyCategories(column)
    within_text(listed, column)
    shares = [
        CategoryShare(
            values=[Data(data=category_label(value))],
            proportion=_proportion(rows.values.get(value, 0), rows.n, position, "rows"),
        )
        for value in listed
    ]
    return CategoryRows(kind="category_rows", categories=shares, excluded_rows=dict(rows.excluded))


def _memberships(
    variable: CanonicalVariable,
    found: Materialised,
    position: int,
    column: int,
    k: int | None,
    size_shown: bool,
) -> MembershipDistribution:
    """A variable's memberships at a position (module docstring, D382): each listed category's
    units over those for which it is known, its UNKNOWN units by reason, its key the category's
    (``canonical.category_key``); past ``MAX_CATEGORIES``, ``TooManyCategories``, and a label
    past ``MAX_TEXT``, ``LongCategory``, or not Unicode text, ``NonTextCategory``. Under *k*, the
    declared categories alone, which the engines list there (D384), each disclosed as
    ``disclosure.membership_shown`` has it, with no map of reasons (D383)."""
    listed = found.memberships
    assert listed is not None, "memberships are materialised with their categories"
    if listed.over or len(listed.categories) > MAX_CATEGORIES:
        raise TooManyCategories(column)
    if k is not None:
        declared = fixed(variable.resolved)
        assert tuple(one.category for one in listed.categories) == declared, (
            "under a disclosure setting memberships list their declared categories alone (D384)"
        )
    within_text((one.category for one in listed.categories), column)
    template = variable.resolved.question
    assert template is not None, "memberships have their template"
    return MembershipDistribution(
        kind="memberships",
        multi_membership=True,
        categories=[
            CategoryShare(
                values=[Data(data=category_label(one.category))],
                proportion=_membership(
                    one,
                    position,
                    category_key(template, one.category),
                    None
                    if k is None
                    else membership_shown(size_shown, Split(one.true, one.false, one.unknown), k),
                ),
            )
            for one in listed.categories
        ],
    )


def _membership(
    one: Membership, position: int, key: str, shown: CategoryShown | None = None
) -> Proportion:
    """A category's proportion (D382): TRUE over TRUE and FALSE, UNKNOWN in ``excluded`` by
    every reason, zeros included; no interval, the analysis being descriptive. Under *k*
    (``shown``), its counts as the pass shows them, each one hidden ``suppressed`` and the
    estimate with it, and ``excluded`` always suppressed (D383)."""
    known = one.true + one.false
    if shown is not None:
        numerator = one.true if shown.numerator else None
        denominator = known if shown.split else None
        reasons: dict[str, NotEstimableReason] = {"/excluded": _SUPPRESSED}
        estimate: float | None = None
        if numerator is None or denominator is None:
            reasons["/estimate"] = _SUPPRESSED
        elif denominator == 0:
            reasons["/estimate"] = _NO_UNITS
        else:
            estimate = numerator / denominator
        for member, value in (("/numerator", numerator), ("/denominator", denominator)):
            if value is None:
                reasons[member] = _SUPPRESSED
        return Proportion.model_validate(
            {
                "estimate": estimate,
                "numerator": numerator,
                "denominator": denominator,
                "denominator_definition": DenominatorDefinition(
                    position=position, predicate=key, counts="known"
                ),
                "excluded": None,
                "not_estimable": reasons,
            }
        )
    return Proportion.model_validate(
        {
            "estimate": one.true / known if known else None,
            "numerator": one.true,
            "denominator": known,
            "denominator_definition": DenominatorDefinition(
                position=position, predicate=key, counts="known"
            ),
            "excluded": {
                reason: one.unknown_by_reason.get(reason, 0) for reason in ExclusionReason
            },
            "not_estimable": None if known else {"/estimate": _NO_UNITS},
        }
    )


def open_categories(variable: CanonicalVariable) -> bool:
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
    shown: Shown,
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
    weighted = _weighted(found.values)
    n = found.n
    reasons: dict[str, NotEstimableReason] = {}
    members: dict[str, object] = {"kind": "numbers", "n": n}
    members.update(dict.fromkeys(("mean", "sd", "median", "q1", "q3", "min", "max")))
    if k is None:
        _bounded(weighted, column)
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
    else:
        _described(weighted, column, members, reasons)
    members["not_estimable"] = reasons or None
    return NumberDistribution.model_validate(members)


def _weighted(values: Mapping[Value, int]) -> stats.Weighted:
    return sorted((cast(int | float, value), times) for value, times in values.items() if times)


def _bounded(weighted: stats.Weighted, column: int) -> None:
    """Values beyond ±(2^53 − 1), which an output does not hold (§8.2): ``TooLarge``."""
    if weighted:
        least, most = float(weighted[0][0]), float(weighted[-1][0])
        if max(abs(least), abs(most)) > MAX_SAFE_INTEGER:
            raise TooLarge(column)


def _described(
    weighted: stats.Weighted,
    column: int,
    members: dict[str, object],
    reasons: dict[str, NotEstimableReason],
) -> None:
    """The statistics of the values themselves, without a disclosure setting, into ``members``
    and their reasons into ``reasons``: none with no value (``no_units``), no standard deviation
    of fewer than two or of values all the same (``zero_variance``), and ``TooLarge`` for one
    beyond ±(2^53 − 1)."""
    if not weighted:
        reasons.update(dict.fromkeys(_STATISTICS, _NO_UNITS))
        return
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


def _number_rows(
    variable: CanonicalVariable, rows: RowCounts, column: int, bins: Sequence[float] | None
) -> NumberRows:
    """A count of rows' numbers (D378): n rows with a value, their statistics and their
    histogram, as a number's without a disclosure setting."""
    weighted = _weighted(rows.values)
    _bounded(weighted, column)
    reasons: dict[str, NotEstimableReason] = {}
    members: dict[str, object] = {
        "kind": "number_rows",
        "n": rows.n,
        "excluded_rows": dict(rows.excluded),
        **dict.fromkeys(("mean", "sd", "median", "q1", "q3", "min", "max")),
        "histogram": None,
    }
    edges = _edges(variable, bins, weighted)
    if edges is None:
        reasons["/histogram"] = _NO_UNITS
    else:
        members["histogram"] = Histogram(edges_from=edges[1], bins=histogram(edges[0], weighted))
    _described(weighted, column, members, reasons)
    members["not_estimable"] = reasons or None
    return NumberRows.model_validate(members)


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
    ``TooManyCategories``, ``LongCategory``, ``NonTextCategory``, ``TooLarge``, and
    ``CallerDeadline`` when ``time.monotonic()`` has passed ``ends`` (the call's deadline) before
    a column is summarised, so that the call overruns it by one column's statistics at most
    (D327)."""
    if len(materialised) != len(positions) or any(
        len(found) != len(variables) for found, _ in materialised
    ):
        raise ValueError("each variable materialised over each of the view's cohorts")
    if k is not None and any(counts_rows(variable) for variable in variables):
        raise ValueError("a count of rows is never summarised under a disclosure setting (D379)")
    if k is not None and any(withheld_under_k(variable) for variable in variables):
        raise ValueError(
            "memberships whose rows per unit the descriptors bound are never summarised under a "
            "disclosure setting (D383)"
        )
    population = populations(positions, k)
    at_positions: list[DistributionPosition] = []
    analysed: list[Analysed] = []
    for position, ((found, together), counts) in enumerate(
        zip(materialised, population, strict=True)
    ):
        size_shown = counts.n_true is not None
        split = [
            Shown(False, False)
            if k is not None and memberships(variable)
            else shown(one, size_shown, k)
            for variable, one in zip(variables, found, strict=True)
        ]
        columns: list[ColumnDistribution] = []
        for index, (variable, one, visible) in enumerate(zip(variables, found, split, strict=True)):
            if ends is not None and time.monotonic() >= ends:
                raise CallerDeadline
            if counts_rows(variable):
                assert one.rows is not None, "a count of rows is materialised with its rows"
                bins = params.columns[index].bins
                columns.append(
                    _category_rows(variable, one.rows, position, index)
                    if categorical(variable)
                    else _number_rows(variable, one.rows, index, bins)
                )
            elif memberships(variable):
                columns.append(_memberships(variable, one, position, index, k, size_shown))
            elif categorical(variable):
                columns.append(_category_distribution(variable, one, position, index, visible, k))
            else:
                bins = params.columns[index].bins
                columns.append(_number_distribution(variable, one, index, bins, visible, k))
        at_positions.append(DistributionPosition(columns=columns))
        analysed.append(analysed_of(found, together, split, k))
    values = DistributionValues(positions=at_positions, view=NoViewValues())
    caveats = _caveats(positions, population, analysed, materialised, k)
    return Outcome(population, analysed, values, caveats)


def _caveats(
    positions: Sequence[CohortAt],
    population: Sequence[Population],
    analysed: Sequence[Analysed],
    materialised: Sequence[tuple[Sequence[Materialised], Joint | None]],
    k: int | None,
) -> list[Caveat]:
    """The caveats that the view's data raise (module docstring); the static ones are the
    view's. ``UNKNOWN_EXCLUDED`` names the values wherever a unit is excluded from one for a
    reason other than ``NOT_APPLICABLE``, and always under *k*, so that it says nothing of one;
    in a message of its own, wherever a count of rows leaves a row out for such a reason (D378),
    which no disclosure setting sees (D379); and in another, wherever a category of memberships
    leaves a unit out of its denominator, UNKNOWN for it (NOT_APPLICABLE is FALSE there, §6.4),
    and under *k* wherever a view has memberships, so that it says nothing of one, saying there
    that no count by reason is shown (D382, D383).
    Under *k*, ``SUPPRESSED`` says what the pass does of memberships only where a view has them,
    so that no other view's message changes (D383)."""
    listing = any(one.memberships is not None for row, _ in materialised for one in row)
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
    if any(
        count
        for row, _ in materialised
        for one in row
        if one.rows is not None
        for reason, count in one.rows.excluded.items()
        if reason is not ExclusionReason.NOT_APPLICABLE
    ):
        found.append(
            caveat(
                CaveatCode.UNKNOWN_EXCLUDED,
                ["/values"],
                text(
                    "Rows whose value of a column that counts rows could not be decided or has "
                    "none are left out of that column's rows; its excluded_rows counts them by "
                    "reason"
                ),
            )
        )
    if (k is not None and listing) or any(
        category.unknown
        for row, _ in materialised
        for one in row
        if one.memberships is not None
        for category in one.memberships.categories
    ):
        found.append(
            caveat(
                CaveatCode.UNKNOWN_EXCLUDED,
                ["/values"],
                text(
                    "Units for which a category of a column of memberships could not be decided "
                    "are left out of that category's denominator; "
                    + (
                        "its proportion's excluded counts them by reason"
                        if k is None
                        else "under the disclosure settings no count of them by reason is shown"
                    )
                ),
            )
        )
    if k is not None:
        affects = ["/population", "/values"]
        if any(
            entry.reasons() or any(v.reasons() for v in entry.variables or []) for entry in analysed
        ):
            affects.append("/analysed")
        message = (
            f"Counts from 1 to {k - 1}, what would reveal them and the values computed "
            "from them are suppressed (null), and categories and histogram bins with such "
            "counts merged with their neighbours; no statistic of the values themselves "
            "is reported, each quartile being given as the bin that holds it, and a "
            "column's undeclared values are one row that names none, under the "
            "disclosure settings"
        )
        if listing:
            message += (
                "; a column of memberships lists only its declared categories, each shown where "
                "its units known and unknown, and its units with and without the category, are "
                f"none from 1 to {k - 1}, with no excluded units by reason"
            )
        found.append(caveat(CaveatCode.SUPPRESSED, affects, text(message)))
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
    if any(counts_rows(variable) for variable in variables):
        found.append(
            text(
                " A column that counts rows gives the same over the rows its units reach, the "
                "rows of each category over those with a value, and a row whose value cannot "
                "be decided, or that has none, is left out of its column's rows and counted by "
                "reason."
            )
        )
    if any(memberships(variable) for variable in variables):
        found.append(
            text(
                " A column of memberships gives, for each of its categories, the units for "
                "which some row or item has it over those for which that is known; a unit for "
                "which it cannot be decided is left out of that category and counted by reason, "
                "and a unit may count in several categories, so the proportions need not sum "
                "to 1."
            )
        )
    return found


__all__ = [
    "ANALYSIS_ID",
    "CAVEATS",
    "ENTRY",
    "METHODS",
    "VERSION",
    "LongCategory",
    "NonTextCategory",
    "Outcome",
    "TooLarge",
    "TooManyCategories",
    "categorical",
    "counts_rows",
    "declared_range",
    "effective_edges",
    "histogram",
    "memberships",
    "needs_edges",
    "summarise",
    "summarised",
    "summarises",
    "view_readback",
    "within_text",
]

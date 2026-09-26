"""The parameters and values of the core's analyses (SPEC §8.1, §8.2, §9.1, §9.2, §9.5; D319,
D325, D328, D330).

An analysis's parameters are a view's ``params`` after substitution (§7.4): objects with fixed
keys, whose clauses are the document's own. Its values are the ``values`` of its result
envelope, per position in view order and for the view as a whole, made of the core's numbers
(``numbers``): proportions, effect sizes and tests, each with its ``not_estimable`` map. Both
are generated into the analysis's registry entry as JSON Schemas (``params``, ``returns``).

``compare.existence`` (D319) takes ``predicates``, from one to ``MAX_PREDICATES`` clauses asked
of every unit of the view's cohorts, and ``level``, the level of every interval. Its values:

- per position, per predicate in parameter order, the proportion of the cohort's units for
  which the predicate is TRUE among those for which it is known, with its Wilson interval, and
  ``lift_differs``, the cohort's units whose answer the other lift rule would change;
- for the view, per predicate, the test across the cohorts that have units for which it is
  known (Fisher's exact test for two, chi-squared for more), and the risk difference and the
  risk ratio of every other position versus the reference; and the Benjamini–Hochberg family of
  those tests, with how many left it.

A **variable** (``Variable``, D325) is a column a view reads one value per unit of: a column of the
unit table or of a row it looks up, or an aggregate of the rows below it (§9.2): ``count``,
``max``, ``min`` or ``mean`` of the rows reached at the last down step of its path (``where``
their conditions, ``lift`` the rule of every earlier step, ``empty`` the value of a unit with no
row to aggregate), or ``some`` or ``every`` with a ``values`` set, an existence question.

``summary.distribution`` (D328) takes ``columns``, one to ``MAX_VARIABLES`` variables. Its
values, per position and column in parameter order: for categories, the cohort's units per
category over those for which the value is known, categories merged under a disclosure setting
and a category column's undeclared values one ``other_values`` row there (D329); for numbers, n,
mean, standard deviation, median, quartiles, minimum, maximum and a histogram, and under a
disclosure setting only the histogram's merged bins and the quartiles' bins.

``summary.members`` (D331) takes ``offset`` and ``limit``, a page of the unit keys of the view's one
cohort. Its values, at its one position: the unit table's key columns, and at most ``limit`` of
its members' keys from the ``offset``-th, each its values in key order as they are stored, in the
canonical form's order (§7.6, step 8), and whether more follow. It lists no key under a disclosure
setting (D332).
"""

from itertools import pairwise
from typing import Annotated, Literal, Self, cast

from pydantic import (
    AfterValidator,
    Discriminator,
    Field,
    StrictBool,
    StrictInt,
    Tag,
    model_validator,
)
from pydantic_core import PydanticCustomError

from aibi.core.schema.document import (
    Clause,
    ClauseList,
    ColumnOrConcept,
    DocModel,
    Lift,
    Scalar,
    ValueList,
    Via,
)
from aibi.core.schema.ids import DECIMAL_INTEGER_RE, MAX_SAFE_INTEGER
from aibi.core.schema.limits import (
    BINS,
    MAX_BINS,
    MAX_CATEGORIES,
    MAX_COHORTS,
    MAX_COLUMNS,
    MAX_MEMBERS,
    MAX_PREDICATES,
    MAX_VARIABLES,
    PREDICATES,
    VARIABLES,
    LimitName,
)
from aibi.core.schema.numbers import (
    ComputedCount,
    EffectSize,
    Estimable,
    HypothesisTest,
    Level,
    Number,
    Proportion,
)
from aibi.core.schema.output import COMPUTED, Count, Data, Finite, Output

MAX_LEVEL = 1 - 1e-9
"""The highest level of an interval of the core's analyses: the normal quantile of a level within
2⁻⁵³ of 1 is infinite, since (1 + level) / 2 rounds to 1 (D319)."""


class ExistenceParams(DocModel):
    """``compare.existence``'s parameters (D319)."""

    predicates: Annotated[
        list[Clause],
        Field(min_length=1, max_length=MAX_PREDICATES),
        LimitName(PREDICATES),
    ]
    """The questions asked of each unit, in the order the values give them."""
    level: Annotated[Level, Field(le=MAX_LEVEL)] = 0.95
    """The level of every interval, at most ``MAX_LEVEL``."""


class PredicateShare(Estimable):
    """One predicate at one position (D319)."""

    proportion: Proportion
    """The units of the cohort for which the predicate is TRUE, over those for which it is known;
    ``excluded`` counts those for which it is UNKNOWN, by reason."""
    lift_differs: ComputedCount
    """The units of the cohort whose answer to the predicate the other lift rule would change: 0
    for a predicate without a lift; ``null`` only when suppressed (§8.4)."""


class ExistencePosition(Output):
    predicates: Annotated[list[PredicateShare], Field(min_length=1, max_length=MAX_PREDICATES)]


class PredicateContrast(Output):
    """One predicate across the view's cohorts (D319)."""

    test: HypothesisTest | None = None
    """Absent for a view of one cohort."""
    effects: Annotated[list[EffectSize], Field(max_length=2 * (MAX_COHORTS - 1))]
    """The risk difference and the risk ratio of each other position versus the reference, in
    view order."""


class Family(Output):
    """The Benjamini–Hochberg family of a view's primary tests (§9.5): those left out were not
    estimable or were suppressed (§8.4)."""

    tests: Count
    """The tests in the family, whose q-values it gives."""
    not_computed: Count
    """The tests that could not be computed (not estimable), which left it."""
    suppressed: Count
    """The tests the disclosure settings suppressed, which left it (§8.4)."""


class ExistenceView(Output):
    predicates: Annotated[list[PredicateContrast], Field(min_length=1, max_length=MAX_PREDICATES)]
    family: Family | None = None
    """Absent for a view of one cohort."""


class ExistenceValues(Output):
    """``compare.existence``'s ``values`` (§8.1): ``positions`` in view order, and ``view``."""

    positions: Annotated[list[ExistencePosition], Field(min_length=1, max_length=MAX_COHORTS)]
    view: ExistenceView


# --- Variables (§9.2; D325) --------------------------------------------------------------------

Aggregate = Literal["count", "max", "min", "mean", "some", "every"]
"""How a variable takes one value per unit of the rows below it (§9.2): ``count``, ``max``,
``min`` or ``mean`` of the rows its path reaches, or whether ``some`` or ``every`` row has a
value in ``values``."""
NUMERIC_AGGREGATES: tuple[Aggregate, ...] = ("count", "max", "min", "mean")
EXISTENCE_AGGREGATES: tuple[Aggregate, ...] = ("some", "every")
EXCLUDE = "exclude"
"""``empty``'s default: a unit with no row to aggregate is excluded (``NO_ROWS``, §9.2)."""


def _edges(value: list[float]) -> list[float]:
    if any(later <= earlier for earlier, later in pairwise(value)):
        raise PydanticCustomError("bins_order", "Histogram edges are strictly increasing")
    return value


Edges = Annotated[
    list[Finite],
    Field(min_length=2, max_length=MAX_BINS + 1),
    LimitName(BINS),
    AfterValidator(_edges),
]
"""A histogram's edges, e₀ < e₁ < … < e_B: bins [eᵢ, eᵢ₊₁), the last closed (§8.4)."""


class Variable(DocModel):
    """A column a view reads, one value per unit (§9.2; D325)."""

    column: ColumnOrConcept
    via: Via | None = None
    """The path from the unit table to the column's table (§6.1)."""
    aggregate: Aggregate | None = None
    """Required for a column below the unit or a list column (§9.2)."""
    values: ValueList | None = None
    """With ``some`` and ``every``, and only with them."""
    where: ClauseList | None = None
    """With ``count``, ``max``, ``min`` and ``mean``: the conditions a row reached at the last
    down step must meet to be aggregated, on the column's table as an ``exists`` leaf's
    ``where`` is (§6.5)."""
    lift: Lift | None = None
    """The rule of every intermediate step, ``strict`` by default (§6.5, §9.2)."""
    empty: Scalar | None = None
    """With ``max``, ``min`` and ``mean``: the value of a unit whose rows hold none, or
    ``"exclude"`` (the default), which leaves it out (``NO_ROWS``)."""
    bins: Edges | None = None
    """For numbers: a histogram's edges; without them, the column's declared ``range`` in
    ``BINS`` equal bins, or, without a disclosure setting, the data's."""
    count: Literal["rows"] | None = None
    """``"rows"`` counts rows rather than units (§9.2): refused until M3.2c (D324)."""

    @model_validator(mode="after")
    def _check_members(self) -> Self:
        aggregate = self.aggregate
        if self.values is not None and aggregate not in EXISTENCE_AGGREGATES:
            raise PydanticCustomError(
                "conflicting_members", 'values goes with aggregate "some" or "every"'
            )
        if aggregate in EXISTENCE_AGGREGATES and self.values is None:
            raise PydanticCustomError(
                "conflicting_members", 'aggregate "some" or "every" asks about values: give them'
            )
        if self.where is not None and aggregate not in NUMERIC_AGGREGATES:
            raise PydanticCustomError(
                "conflicting_members",
                'where goes with aggregate "count", "max", "min" or "mean"; for "some" and '
                '"every", ask the question as a compare.existence predicate',
            )
        if self.empty is not None and aggregate not in ("max", "min", "mean"):
            raise PydanticCustomError(
                "conflicting_members", 'empty goes with aggregate "max", "min" or "mean"'
            )
        return self


# --- summary.distribution (D328) ----------------------------------------------------------------

BINS_OF_A_RANGE = 10
"""*B*: the equal bins a declared ``range``, or without a disclosure setting the data, is
divided into when a variable gives no ``bins`` (§8.4)."""


class DistributionParams(DocModel):
    """``summary.distribution``'s parameters (D328)."""

    columns: Annotated[
        list[Variable], Field(min_length=1, max_length=MAX_VARIABLES), LimitName(VARIABLES)
    ]
    """The variables, in the order the values give them."""


class CategoryShare(Output):
    """One category at one position, or under a disclosure setting several merged into one row
    (§8.4, D329): its units, over the units for which the value is known."""

    values: Annotated[list[Data], Field(max_length=MAX_CATEGORIES)]
    """The category as data (a value of the column; ``"false"`` or ``"true"`` for a boolean one
    or a ``some`` or ``every`` aggregate), or the categories merged, in their listed order."""
    other_values: Literal[True] | None = None
    """Under a disclosure setting, a category column's values that it does not declare, one row
    that names none of them (D329), merged as the categories are; absent otherwise."""
    proportion: Proportion

    @model_validator(mode="after")
    def _check_named(self) -> Self:
        if not self.values and self.other_values is None:
            raise PydanticCustomError("empty_row", "A row names a category or holds other values")
        return self


class CategoryDistribution(Estimable):
    """A categorical variable at one position (D328, D329). ``categories`` lists the declared
    permissible values in their order, zeros included, then the other values in canonical order,
    or under a disclosure setting one row of them that names none (``other_values``), the rows
    the disclosure settings merged in one row each; ``null`` when suppressed."""

    kind: Literal["categories"]
    categories: Annotated[list[CategoryShare] | None, COMPUTED, Field(max_length=MAX_CATEGORIES)]


class HistogramBin(Output):
    """A bin [low, high), or [low, high] for the last; ``low`` is ``null`` for the open bin
    below the first edge and ``high`` for the one above the last."""

    low: Finite | None
    high: Finite | None
    includes_low: StrictBool
    includes_high: StrictBool
    count: Count


class Histogram(Output):
    edges_from: Literal["params", "range", "data"]
    """Where its edges came from: the view's ``bins``, the column's declared ``range``, or,
    without a disclosure setting, the data (§8.4)."""
    bins: Annotated[list[HistogramBin], Field(min_length=1, max_length=MAX_BINS + 2)]


BinIndex = Annotated[StrictInt, Field(ge=0, le=MAX_BINS + 1)]


class NumberDistribution(Estimable):
    """A numeric variable at one position (D328, D329). Under a disclosure setting the minimum
    and maximum are suppressed, the quartiles are given as the (merged) bin that holds each
    (``q1_bin``, ``median_bin``, ``q3_bin``), and no statistic of the values themselves (mean,
    standard deviation, median, quartiles) is shown."""

    kind: Literal["numbers"]
    n: ComputedCount
    mean: Number
    sd: Number
    median: Number
    q1: Number
    q3: Number
    min: Number
    max: Number
    histogram: Annotated[Histogram | None, COMPUTED]
    q1_bin: Annotated[BinIndex | None, COMPUTED] = None
    median_bin: Annotated[BinIndex | None, COMPUTED] = None
    q3_bin: Annotated[BinIndex | None, COMPUTED] = None
    """Given only under a disclosure setting: the index of the bin that holds each, ``null``
    (``suppressed``) where the two values the quantile lies between are in two bins (D329)."""


def _distribution_kind(value: object) -> str | None:
    kind: object = (
        cast(dict[str, object], value).get("kind")
        if isinstance(value, dict)
        else getattr(value, "kind", None)
    )
    return kind if isinstance(kind, str) and kind in ("categories", "numbers") else None


ColumnDistribution = Annotated[
    Annotated[CategoryDistribution, Tag("categories")]
    | Annotated[NumberDistribution, Tag("numbers")],
    Discriminator(
        _distribution_kind,
        custom_error_type="wrong_type",
        custom_error_message='A column\'s distribution is of kind "categories" or "numbers"',
    ),
]


class DistributionPosition(Output):
    columns: Annotated[list[ColumnDistribution], Field(min_length=1, max_length=MAX_VARIABLES)]


class NoViewValues(Output):
    """The values of a view as a whole, for an analysis that gives none: descriptive only."""


class DistributionValues(Output):
    """``summary.distribution``'s ``values`` (§8.1): ``positions`` in view order."""

    positions: Annotated[list[DistributionPosition], Field(min_length=1, max_length=MAX_COHORTS)]
    view: NoViewValues


# --- summary.members (D331) --------------------------------------------------------------------

MEMBERS_PAGE = 100
"""The keys a page of ``summary.members`` lists when its view gives no ``limit``."""


class MembersParams(DocModel):
    """``summary.members``'s parameters (D331): a page of the cohort's keys."""

    offset: Annotated[StrictInt, Field(ge=0, le=MAX_SAFE_INTEGER)] = 0
    """The members passed over, in the keys' order, before the page's first."""
    limit: Annotated[StrictInt, Field(ge=1, le=MAX_MEMBERS)] = MEMBERS_PAGE
    """The most keys the page lists, at most ``MAX_MEMBERS``."""


def _large_integer(value: str) -> str:
    if not DECIMAL_INTEGER_RE.fullmatch(value) or abs(int(value)) <= MAX_SAFE_INTEGER:
        raise PydanticCustomError(
            "large_integer", "A decimal string holds an integer beyond ±(2^53 - 1) alone"
        )
    return value


LargeInteger = Annotated[str, AfterValidator(_large_integer)]
"""An integer beyond ±(2^53 − 1), as a decimal string (§5.1, §8.2)."""

KeyPart = (
    StrictBool
    | Annotated[StrictInt, Field(ge=-MAX_SAFE_INTEGER, le=MAX_SAFE_INTEGER)]
    | (Finite | LargeInteger | Data)
)
"""One value of a unit key, as its column stores it (D331): a boolean, an integer (beyond
±(2^53 − 1) a decimal string), a double (an integral one written as an integer, as the canonical
form writes it), or text, a date or a datetime as data, as the canonical form writes them."""


class MembersPosition(Output):
    """``summary.members`` at its one position (D331)."""

    columns: Annotated[list[Data], Field(min_length=1, max_length=MAX_COLUMNS)]
    """The unit table's key columns, by descriptor id, in key order."""
    keys: Annotated[
        list[Annotated[list[KeyPart], Field(min_length=1, max_length=MAX_COLUMNS)]],
        Field(max_length=MAX_MEMBERS),
    ]
    """The page: members' keys, each its values in key order, in the canonical form's order."""
    offset: Count
    """The members passed over before the page's first key."""
    more: StrictBool
    """Whether members follow the page's last key."""

    @model_validator(mode="after")
    def _check_widths(self) -> Self:
        if any(len(key) != len(self.columns) for key in self.keys):
            raise PydanticCustomError("key_width", "Each key has a value per key column")
        return self


class MembersValues(Output):
    """``summary.members``'s ``values`` (§8.1): its one position, and nothing for the view."""

    positions: Annotated[list[MembersPosition], Field(min_length=1, max_length=1)]
    view: NoViewValues


__all__ = [
    "BINS_OF_A_RANGE",
    "EXCLUDE",
    "EXISTENCE_AGGREGATES",
    "MEMBERS_PAGE",
    "NUMERIC_AGGREGATES",
    "Aggregate",
    "CategoryDistribution",
    "CategoryShare",
    "ColumnDistribution",
    "DistributionParams",
    "DistributionPosition",
    "DistributionValues",
    "Edges",
    "ExistenceParams",
    "ExistencePosition",
    "ExistenceValues",
    "ExistenceView",
    "Family",
    "Histogram",
    "HistogramBin",
    "KeyPart",
    "LargeInteger",
    "MembersParams",
    "MembersPosition",
    "MembersValues",
    "NoViewValues",
    "NumberDistribution",
    "PredicateContrast",
    "PredicateShare",
    "Variable",
]

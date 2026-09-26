"""The parameters and values of the core's analyses (SPEC §8.1, §8.2, §9.1, §9.5; D319).

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
"""

from typing import Annotated

from pydantic import Field

from aibi.core.schema.document import Clause, DocModel
from aibi.core.schema.limits import MAX_COHORTS, MAX_PREDICATES, PREDICATES, LimitName
from aibi.core.schema.numbers import (
    ComputedCount,
    EffectSize,
    Estimable,
    HypothesisTest,
    Level,
    Proportion,
)
from aibi.core.schema.output import Count, Output

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


__all__ = [
    "ExistenceParams",
    "ExistencePosition",
    "ExistenceValues",
    "ExistenceView",
    "Family",
    "PredicateContrast",
    "PredicateShare",
]

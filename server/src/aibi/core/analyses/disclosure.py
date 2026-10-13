"""The disclosure pass over the counts an analysis adds to a result (SPEC §8.4; D320, D424).

A result's population is disclosed as a cohort count is (``engine.suppression``), each position
alone, so that a cohort's counts are the same in a result as in ``count_cohort``'s answer. What a
predicate adds at a position is its **split**: the units of the position's cohort for which it is
TRUE, FALSE and UNKNOWN.

**Scopes** (D424). Predicates of one view may depend on one another, whatever they read (one
column's categories share each unit's default, a predicate may read the rows a coverage or a
cohort of the view reads), and a count shown of one then bounds a count hidden of another. So
the pass discloses a view's predicates by **scope**, a set of positions whose units may be
shared: each position alone where the view refuses overlap, every position where it allows it
(``scopes``). Dependence is never decided: a scope is disclosed so that nothing in it is shown
beside anything hidden in it.

- **A predicate alone**, one predicate in a scope of one position, keeps D320's rule: its split
  is disclosed exactly as a cohort count is (``suppressed``), its total, the cohort's ``n_true``,
  as the size, so each count's complement is a member, one count suppressed alone takes another
  with it, and TRUE and FALSE are treated alike. What a result shows of a split is its TRUE
  units (a proportion's numerator) and its UNKNOWN units (``excluded_units``, and so the known
  ones, the denominator, which the size less them gives); the pass never leaves one of the three
  suppressed alone, so its FALSE units are shown only with both. Its map of UNKNOWN units by
  reason is shown with them, where none of its counts is small; ``analysed`` is its split's.
- **Any other scope**, two predicates or more, or two positions or more, is **gated**
  (``scope_shown``): it is shown only where every pass the output applies hides nothing in it,
  each predicate's split at each of its positions unsuppressed (``suppressed`` empty, a small
  table's rule included), each position's cohort ``n_true`` shown, and, where its positions may
  share units, nothing of each one's population hidden; else every predicate's counts at every
  position of the scope are withheld (``Pass.SCOPE``). Under a refused overlap only ``n_true``
  of the population is in the gate: the position's predicates count its own units, and its other
  population members count units outside it. A gated scope's maps of reasons and its
  ``analysed`` counts, joint counts of its predicates (none known of at least the sum of their
  UNKNOWN units less the size once for each predicate after the first, and of at most the
  fewest), are never shown under *k* (``Pass.NEVER``, ``existence.never_shown``): dependent
  predicates give them by difference.

A position whose cohort's ``n_true`` is suppressed shows none of its splits, whose counts would
add up to it. Whatever else a result carries (proportions' estimates and intervals, effects,
tests, charts) is computed only from counts shown. What this protects, for *k* of 3 or more, is
every count of a scope's gate and of its own positions' populations, relative to what the
populations alone give; §8.4's Limits name what it does not (D424).

**Variables** (D329). What a variable adds at a position is the split of the cohort's units into
those it has a value for (``n``) and those it excludes (``excluded_units``), whose total is the
cohort's ``n_true``, so the two are shown together or not at all (``split_hidden``); the units
excluded by reason are shown only with them and when none of their counts is small. Of two or
more variables, nothing that combines them is shown, as of predicates. Within ``n``:

- **Categories and histogram bins** are merged as §8.4 has them (``merged``), categories in the
  order they are listed: repeatedly, the leftmost with 1 to *k* − 1 units is merged with the
  nearest non-empty one on its right (on its left when none is), empty ones between them
  included, until none has 1 to *k* − 1 units or one non-empty one is left; the counts merged are
  never shown apart. The rule reads left to right, so a merged row's parts are those of every
  way of filling it that merges the same, and whatever it shows leaves each merged count two
  values or more. The rule that merges the smallest first, toward the neighbour with fewer units,
  gives the order it merged in away, and with it counts (3, 2, 2, 2 and 1 units in five bins
  under *k* = 3 are the only ones it shows as 3, 4 and 3), as pooling small categories into one
  row does whenever the row's count is read off: 1 each, or *k* − 1 each, or one category alone.

**Memberships** (D383). A variable's memberships (``each: "category"``) ask one question of each
category a column declares, and every unit's answer for a category none of its rows holds is its
default (D381), so the categories' splits depend on one another: a category no unit holds splits
the units as their defaults do, and bounds every other's FALSE and UNKNOWN units from above.
Each category's row is disclosed as ``some`` of that one category is as a variable of its own
(``membership_shown``): its split of the position's units into those it is known for (the
proportion's denominator) and those it is UNKNOWN for is shown whole or not at all
(``split_hidden``), and its TRUE units (the numerator) only with it and where neither its TRUE
nor its FALSE units are small, as a question's two categories merge. A numerator shown beside a
split that is hidden, as a predicate's split shows one, would tell that the UNKNOWN units are
few, and the default's bounds then pin them (10 units under *k* = 3: beside a category split 0,
3 and 7, one split 5, 3 and 2 shows 5 alone, and only its UNKNOWN 2 fits); maps of reasons of
categories that share defaults pin one another's, so no row shows its map, and nothing shows the
units known for some category, a joint count. Where the descriptors may bound the rows each
unit reaches, its path not one open down step from the unit (``resolve.bounded_rows``,
``resolve.open_path``: one row on a one-to-one path, two where a boolean completes a set of the
child's columns the gate keeps unique, as many as a record filter allows, one where a later
child is keyed by the unit's key alone), the TRUE counts sum to at most that bound times the
units, which pins one the rule hides, so the analysis withholds such
memberships instead (``distribution.withheld_under_k``). The rule assumes a unit may hold any
set of the declared categories, which an open step gives.

What is computed from a variable's values rather than counted is never shown: values are not
counts, so the rule that hides counts from 1 to *k* − 1 cannot protect them (a mean over coarse
bins gives the counts finer bins hide, and a standard deviation of zero every unit's value). Each
quartile is given as the bin that holds it, read off the merged bins' counts (``null`` where the
two values it lies between are in two bins); a category column's undeclared values are one row
that names none of them.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from aibi.core.engine.suppression import suppressed


@dataclass(frozen=True)
class Split:
    """A predicate's accounting over the units of a position's cohort."""

    true: int
    false: int
    unknown: int


class Pass(StrEnum):
    """The pass that nulls a member of what a result shows of its predicates (module
    docstring)."""

    COHORT = "cohort"
    """The position's cohort's ``n_true`` is suppressed (a predicate alone)."""
    SPLIT = "split"
    """The predicate's split suppresses the count (a predicate alone, D320)."""
    MAP = "map"
    """A count of the map is from 1 to *k* − 1 (a predicate alone)."""
    SCOPE = "scope"
    """The scope's gate holds a count a pass hides, so the scope is withheld whole (D424)."""
    NEVER = "never"
    """Never shown under *k*, for the scope's shape (D424, ``existence.never_shown``)."""


@dataclass(frozen=True)
class Cohort:
    """What the gate reads of a position: whether its cohort's ``n_true`` is shown, whether
    nothing of its population is hidden (``lift_differs`` aside, which always is), its
    predicates' splits of its units, and each one's UNKNOWN units by reason."""

    size_shown: bool
    whole_shown: bool
    splits: tuple[Split, ...]
    reasons: tuple[tuple[int, ...], ...]


@dataclass(frozen=True)
class Hidden:
    """What the pass hides at one position, per predicate, with the pass that hides it, or
    ``None``: its TRUE units (``true``), its UNKNOWN units and with them its known ones
    (``unknown``), and its map of UNKNOWN units by reason (``maps``)."""

    true: tuple[Pass | None, ...]
    unknown: tuple[Pass | None, ...]
    maps: tuple[Pass | None, ...]

    def proportion(self, predicate: int) -> bool:
        """Whether a count the predicate's proportion is computed from is suppressed."""
        return self.true[predicate] is not None or self.unknown[predicate] is not None


def small(k: int, count: int) -> bool:
    """Whether a count is one the pass suppresses by itself: from 1 to *k* − 1."""
    return 1 <= count <= k - 1


def scopes(positions: int, *, allow: bool) -> list[tuple[int, ...]]:
    """A view's scopes (module docstring): every position where it allows overlap, else each
    alone."""
    return [tuple(range(positions))] if allow else [(at,) for at in range(positions)]


def gated(positions: int, predicates: int) -> bool:
    """Whether a scope of this shape is gated: any but one predicate at one position."""
    return positions > 1 or predicates > 1


def scope_shown(scope: Sequence[Cohort], *, allow: bool, k: int) -> bool:
    """The gate of a gated scope (module docstring): whether every pass the output applies
    hides nothing in it."""
    for at in scope:
        if not at.size_shown or (allow and not at.whole_shown):
            return False
        if any(suppressed(s.true, s.false, s.unknown, k) for s in at.splits):
            return False
    return True


def _alone(at: Cohort, k: int) -> Hidden:
    """A predicate alone (D320)."""
    [split] = at.splits
    [reasons] = at.reasons
    if not at.size_shown:
        return Hidden((Pass.COHORT,), (Pass.COHORT,), (Pass.COHORT,))
    found = suppressed(split.true, split.false, split.unknown, k)
    true = Pass.SPLIT if "n_true" in found else None
    unknown = Pass.SPLIT if "n_unknown" in found else None
    map_pass = unknown or (Pass.MAP if any(small(k, count) for count in reasons) else None)
    return Hidden((true,), (unknown,), (map_pass,))


def hidden(scope: Sequence[Cohort], *, allow: bool, k: int) -> list[Hidden]:
    """What the pass hides at each position of a scope (module docstring); a gated scope's maps
    are ``Pass.NEVER``."""
    predicates = len(scope[0].splits)
    if not gated(len(scope), predicates):
        return [_alone(scope[0], k)]
    gone = None if scope_shown(scope, allow=allow, k=k) else Pass.SCOPE
    every = (gone,) * predicates
    return [Hidden(every, every, (Pass.NEVER,) * predicates) for _ in scope]


def split_hidden(n: int, excluded_units: int, k: int) -> bool:
    """Whether a variable's split of a cohort's shown ``n_true`` is suppressed: either part
    from 1 to *k* − 1, which the other, beside ``n_true``, would give away."""
    return small(k, n) or small(k, excluded_units)


@dataclass(frozen=True)
class CategoryShown:
    """What the pass shows of one category of a variable's memberships at a position: its split
    (the units it is known for, and so those it is UNKNOWN for), and its TRUE units."""

    split: bool
    numerator: bool


def membership_shown(size_shown: bool, split: Split, k: int) -> CategoryShown:
    """What the pass shows of a category of memberships whose units at a position whose
    cohort's ``n_true`` is shown or not (``size_shown``) split as ``split`` (module docstring,
    D383): the split whole where neither the units known nor those UNKNOWN are small, and the
    TRUE units with it where neither they nor the FALSE ones are."""
    if not size_shown:
        return CategoryShown(False, False)
    whole = not split_hidden(split.true + split.false, split.unknown, k)
    numerator = whole and not small(k, split.true) and not small(k, split.false)
    return CategoryShown(whole, numerator)


def merged(counts: Sequence[int], k: int) -> list[tuple[int, int]]:
    """Categories' or histogram bins' counts merged under *k* (module docstring): each row of the
    result as the span of those it merges, first and last included."""
    spans = [(index, index) for index in range(len(counts))]
    sizes = list(counts)
    while True:
        at = next((i for i, size in enumerate(sizes) if small(k, size)), None)
        filled = [i for i, size in enumerate(sizes) if size > 0]
        if at is None or len(filled) <= 1:
            return spans
        right = min((i for i in filled if i > at), default=None)
        if right is None:
            start, end = max(i for i in filled if i < at), at
        else:
            start, end = at, right
        spans[start : end + 1] = [(spans[start][0], spans[end][1])]
        sizes[start : end + 1] = [sum(sizes[start : end + 1])]


__all__ = [
    "CategoryShown",
    "Cohort",
    "Hidden",
    "Pass",
    "Split",
    "gated",
    "hidden",
    "membership_shown",
    "merged",
    "scope_shown",
    "scopes",
    "small",
    "split_hidden",
]

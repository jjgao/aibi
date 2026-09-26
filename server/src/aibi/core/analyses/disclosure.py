"""The disclosure pass over the counts an analysis adds to a result (SPEC §8.4; D320).

A result's population is disclosed as a cohort count is (``engine.suppression``), each position
alone, so that a cohort's counts are the same in a result as in ``count_cohort``'s answer. What a
predicate adds at a position is its **split**: the units of the position's cohort for which it is
TRUE, FALSE and UNKNOWN. A split is disclosed exactly as a cohort count is (``suppressed``), its
total, the cohort's ``n_true``, as the size: so each count's complement is a member, one count
suppressed alone takes another with it, and TRUE and FALSE are treated alike. A predicate and its
``not`` then split the same units with TRUE and FALSE swapped, and are disclosed alike, so a count
the view shows as FALSE of one and TRUE of the other is suppressed in both; ``known`` and
``unknown`` of a predicate split them as ``known`` and ``unknown`` of a cohort do, which the pass
over cohort counts leaves nothing to pin a suppressed count by (D297).

What a result shows of a split is its TRUE units (a proportion's numerator) and its UNKNOWN units
(``excluded_units``, and so the known ones, the denominator, which the size less them gives); the
pass never leaves one of the three suppressed alone, so its FALSE units are shown only with both.
A position whose cohort's ``n_true`` is suppressed shows none of its splits, whose counts would
add up to it. ``analysed`` of one predicate is its split's known and unknown units. Of two
predicates or more, nothing that combines them is shown: the units for which some predicate is
known, those for which none is and their reasons are joint counts, which the predicates' own
counts bound from both sides (none is known of at least the sum of their UNKNOWN units less the
size once for each predicate after the first, and of at most the fewest), so that a suppressed
one can be pinned, and a map of their reasons bounds a predicate's own map, suppressed or not;
each predicate's entry of ``variables`` carries its own. Whatever else a result carries
(proportions' estimates and intervals, effects, tests, charts) is computed only from counts
shown.

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

What is computed from a variable's values rather than counted is never shown: values are not
counts, so the rule that hides counts from 1 to *k* − 1 cannot protect them (a mean over coarse
bins gives the counts finer bins hide, and a standard deviation of zero every unit's value). Each
quartile is given as the bin that holds it, read off the merged bins' counts (``null`` where the
two values it lies between are in two bins); a category column's undeclared values are one row
that names none of them.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from aibi.core.engine.suppression import suppressed


@dataclass(frozen=True)
class Split:
    """A predicate's accounting over the units of a position's cohort."""

    true: int
    false: int
    unknown: int


@dataclass(frozen=True)
class Hidden:
    """What the pass hides at one position: per predicate, its TRUE units (``true``) and its
    UNKNOWN units (``unknown``, and with them its known ones); and ``analysed``, the units for
    which some predicate is known and those for which none is, with their reasons."""

    true: tuple[bool, ...]
    unknown: tuple[bool, ...]
    analysed: bool

    def proportion(self, predicate: int) -> bool:
        """Whether a count the predicate's proportion is computed from is suppressed."""
        return self.true[predicate] or self.unknown[predicate]


def small(k: int, count: int) -> bool:
    """Whether a count is one the pass suppresses by itself: from 1 to *k* − 1."""
    return 1 <= count <= k - 1


def hidden(size_shown: bool, splits: Sequence[Split], k: int) -> Hidden:
    """What the pass hides at a position whose cohort's ``n_true`` is shown or not
    (``size_shown``), and whose predicates split its units as ``splits`` (module docstring)."""
    if not size_shown:
        every = (True,) * len(splits)
        return Hidden(every, every, True)
    true: list[bool] = []
    unknown: list[bool] = []
    for split in splits:
        found = suppressed(split.true, split.false, split.unknown, k)
        unknown.append("n_unknown" in found)
        true.append("n_true" in found)
    return Hidden(tuple(true), tuple(unknown), unknown[0] if len(splits) == 1 else True)


def split_hidden(n: int, excluded_units: int, k: int) -> bool:
    """Whether a variable's split of a cohort's shown ``n_true`` is suppressed: either part
    from 1 to *k* − 1, which the other, beside ``n_true``, would give away."""
    return small(k, n) or small(k, excluded_units)


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


__all__ = ["Hidden", "Split", "hidden", "merged", "small", "split_hidden"]

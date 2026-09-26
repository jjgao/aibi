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


__all__ = ["Hidden", "Split", "hidden", "small"]

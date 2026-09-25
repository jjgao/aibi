"""The disclosure pass over a cohort's count (SPEC §8.4; D297).

``disclosed`` takes a count's digested parts (``counts.count_parts``) and the effective *k*,
the largest of the deployment's floor and the settings of the cohort's dataset, and returns them
as ``count_cohort`` serves them; the digest is taken after it (§7.6, D283). Without *k* the
parts are returned as they are. With *k* set:

- **The unit table's size** is the total of the linked set ``n_true``, ``n_false`` and
  ``n_unknown``, and a member of it: from 1 to *k* − 1 it is suppressed (``size.denominator``),
  as the catalogue suppresses such a table's ``n_rows`` (D271). A table of 1 to
  ``max(k, TINY) − 1`` units has all three counts suppressed, zeros included: shown beside
  such a size, or bounded by it, any one of them would be pinned to a single value.
- **Linked counts**, over a larger table, until nothing changes (``suppressed``): a count from 1
  to *k* − 1 is suppressed, and so is a non-zero count whose complement, the units outside it,
  is from 1 to *k* − 1. One suppressed alone takes another with it: ``n_true`` takes
  ``n_false``, and ``n_false`` ``n_true``, unless that is 0 (the complement rule has then
  taken a non-zero ``n_unknown``, whose complement is the suppressed count); and
  ``n_unknown`` takes the smaller non-zero of ``n_true`` and ``n_false``, both on a tie. Two
  suppressed beside a non-zero shown count take it with them when the rules above suppress
  those two, and show the third, for only one split of their sum (``_one_split``); the
  suppressed ones that would each be 1 are one such case. A count of 0 is shown, and a count
  suppressed in one place is suppressed wherever it appears (``n_true`` and
  ``size.numerator``).
- ``lift_differs`` is a linked set alone, and is suppressed, a 0 included, whenever one of
  the three counts is: it is bounded by the counts (a strict cohort's ``some`` steps turn only
  unknown units definite when the lift flips, so it is at most ``n_unknown``, and other
  cohorts bound it otherwise), so beside a suppressed count it could pin it.
- **Breakdowns.** ``unknown_by_reason`` and ``unknown_by_leaf`` are ``null`` as a whole when
  ``n_unknown`` is suppressed or any of their counts is from 1 to *k* − 1.
- **Derived values.** The size's estimate is suppressed with its numerator or its denominator.

The rules treat ``n_true`` and ``n_false`` alike, so that ``not X`` is disclosed as ``X`` with
the two swapped; and every count's complement is itself a count of a cohort the same document
can write (``known X`` counts ``n_true`` + ``n_false``, ``unknown X`` ``n_unknown``, over the
same table). So what the pass leaves pins no suppressed count to one value, from anything shown
(the other counts, the size, the breakdowns and the caveats), for a cohort alone and for ``X``,
``not X``, ``known X`` and ``unknown X`` counted together: ``test_suppression``'s exhaustive
checks, the second for *k* of 3 or more (under *k* = 2, where 1 to *k* − 1 is one value, a
``known X`` beside two counts of 1 shows them, and so the third). A suppressed member is
``null``, listed in the population's ``suppressed`` or in the size's ``not_estimable`` as
``suppressed``; the count then carries ``SUPPRESSED``. ``UNKNOWN_EXCLUDED`` and
``LIFT_DIFFERS`` are carried whenever ``n_unknown`` and ``lift_differs`` are suppressed, as the
model requires, with messages that hold whether or not a unit is unknown or changes; their
messages quote only the counts shown (§8.4).

Cohorts of one output are disclosed each alone, so that a count is the same whatever else its
call counts. So the closure covers ``not``, ``known`` and ``unknown`` of the cohort, and no other
cohort: two cohorts that differ by a few units difference to a small count, and a two-valued
collapse of the cohort, ``X and known X`` (``n_true`` beside ``n_false`` + ``n_unknown``) or
``X or unknown X`` (``n_true`` + ``n_unknown`` beside ``n_false``), is another cohort whose
counts, large enough to be shown, give what ``X``'s pass hid. No pass over one cohort's count
can see those, and §8.4's Limits disclaim them (D297).
"""

from dataclasses import dataclass, replace

from aibi.core.engine.counts import CountParts, lift_message, unknown_message
from aibi.core.schema.caveats import CORE_SEVERITIES, Caveat, CaveatCode, sort_caveats
from aibi.core.schema.numbers import NotEstimableReason, Proportion
from aibi.core.schema.output import Segment, text
from aibi.core.schema.results import Population

_LINKED = ("n_true", "n_false", "n_unknown")
"""The linked set of a cohort's accounting (§8.4)."""
_PARTNER = {"n_true": "n_false", "n_false": "n_true"}
_SIZE = "size"
TINY = 4
"""A unit table with fewer units than this, or than *k*, shows none of its three counts: with
zeros shown, three counts of 1, or two beside a 0, would be read off a size of 2 or 3."""
_SUPPRESSED = NotEstimableReason.SUPPRESSED


def _small(k: int, count: int) -> bool:
    return 1 <= count <= k - 1


@dataclass(frozen=True)
class _Counts:
    n_true: int
    n_false: int
    n_unknown: int

    def of(self, name: str) -> int:
        return int(getattr(self, name))

    @property
    def total(self) -> int:
        return self.n_true + self.n_false + self.n_unknown

    def split(self, pair: tuple[str, str], first: int) -> "_Counts":
        """These counts with the sum of the two ``pair`` names split as ``first`` and the
        rest."""
        given = {name: self.of(name) for name in _LINKED}
        given[pair[1]] = given[pair[0]] + given[pair[1]] - first
        given[pair[0]] = first
        return _Counts(**given)


def _with_one(counts: _Counts, one: str) -> set[str]:
    """What one suppressed count takes with it (module docstring)."""
    if one in _PARTNER:
        partner = _PARTNER[one]
        return {partner} if counts.of(partner) else set()
    others = [name for name in _PARTNER if counts.of(name)]
    if not others:
        return set()
    least = min(counts.of(name) for name in others)
    return {name for name in others if counts.of(name) == least}


def _one_split(counts: _Counts, pair: tuple[str, str], k: int) -> bool:
    """Whether the rules but this one suppress the two counts ``pair``, and show the third, for
    only one split of their sum into two non-zero counts (a 0 would be shown). A count the
    rules suppress is from 1 to *k* − 1, or has a complement that is, or is what such a count
    takes with it; so in every split the rules suppress, one of the two is below *k* (a count
    over the table's size less *k* leaves the other below *k*), and only those splits need be
    tried, each either way round."""
    total = counts.of(pair[0]) + counts.of(pair[1])
    tried = {first for part in range(1, min(k, total)) for first in (part, total - part)}
    found = 0
    for first in sorted(tried):
        if _linked(counts.split(pair, first), k, pairs=False) == frozenset(pair):
            found += 1
            if found > 1:
                return False
    return True


def _linked(counts: _Counts, k: int, *, pairs: bool = True) -> frozenset[str]:
    """The counts of the linked set the pass suppresses over a unit table of at least
    ``max(k, TINY)`` units (module docstring); ``pairs`` applies the rule of two suppressed
    beside a shown count."""
    hidden: set[str] = set()
    total = counts.total
    while True:
        before = set(hidden)
        hidden.update(
            name
            for name in _LINKED
            if _small(k, counts.of(name))
            or (counts.of(name) and _small(k, total - counts.of(name)))
        )
        suppressed = [name for name in _LINKED if name in hidden]
        if len(suppressed) == 1:
            hidden |= _with_one(counts, suppressed[0])
        elif pairs and len(suppressed) == 2:
            [third] = [name for name in _LINKED if name not in hidden]
            if counts.of(third) and _one_split(counts, (suppressed[0], suppressed[1]), k):
                hidden.add(third)
        if hidden == before:
            return frozenset(hidden)


def suppressed(n_true: int, n_false: int, n_unknown: int, k: int) -> frozenset[str]:
    """The counts of a cohort's linked set the pass suppresses under ``k``, by name, with
    ``size`` for the unit table's (module docstring)."""
    counts = _Counts(n_true, n_false, n_unknown)
    total = counts.total
    if 0 < total < max(k, TINY):
        return frozenset({*_LINKED, *((_SIZE,) if total < k else ())})
    return _linked(counts, k)


def disclosed(parts: CountParts, k: int | None) -> CountParts:
    """A cohort count's digested parts after the disclosure pass under ``k`` (module
    docstring)."""
    if k is None:
        return parts
    population = parts.population
    given = (population.n_true, population.n_false, population.n_unknown, population.lift_differs)
    if any(count is None for count in given):
        raise ValueError("the disclosure pass runs once, on counts as they were computed")
    counts = _Counts(*(int(count or 0) for count in given[:3]))
    lift_differs = int(given[3] or 0)
    hidden = set(suppressed(counts.n_true, counts.n_false, counts.n_unknown, k))
    if hidden or _small(k, lift_differs):
        hidden.add("lift_differs")
    by_reason = population.unknown_by_reason
    by_leaf = population.unknown_by_leaf
    if "n_unknown" in hidden or (
        by_reason is not None and any(_small(k, count) for count in by_reason.values())
    ):
        by_reason = None
    if "n_unknown" in hidden or (
        by_leaf is not None and any(_small(k, count) for count in by_leaf.values())
    ):
        by_leaf = None
    shown = Population(
        n_true=None if "n_true" in hidden else counts.n_true,
        n_false=None if "n_false" in hidden else counts.n_false,
        n_unknown=None if "n_unknown" in hidden else counts.n_unknown,
        unknown_by_reason=by_reason,
        unknown_by_leaf=by_leaf,
        lift_differs=None if "lift_differs" in hidden else lift_differs,
        suppressed=[
            "/" + name
            for name, value in (
                *((name, name in hidden) for name in (*_LINKED, "lift_differs")),
                ("unknown_by_reason", by_reason is None),
                ("unknown_by_leaf", by_leaf is None),
            )
            if value
        ],
    )
    size = parts.size
    if "n_true" in hidden:
        size = Proportion(
            estimate=None,
            numerator=None,
            denominator=None if _SIZE in hidden else size.denominator,
            denominator_definition=size.denominator_definition,
            not_estimable={
                "/estimate": _SUPPRESSED,
                "/numerator": _SUPPRESSED,
                **({"/denominator": _SUPPRESSED} if _SIZE in hidden else {}),
            },
        )
    caveats = [_rewritten(caveat, shown) for caveat in parts.caveats]
    carried = {caveat.code for caveat in caveats}
    if shown.n_unknown is None and CaveatCode.UNKNOWN_EXCLUDED not in carried:
        caveats.append(_carried(CaveatCode.UNKNOWN_EXCLUDED, unknown_message(None)))
    if shown.lift_differs is None and CaveatCode.LIFT_DIFFERS not in carried:
        caveats.append(_carried(CaveatCode.LIFT_DIFFERS, lift_message(None)))
    if shown.suppressed:
        caveats.append(
            Caveat(
                code=CaveatCode.SUPPRESSED,
                severity=CORE_SEVERITIES[CaveatCode.SUPPRESSED],
                message=[
                    text(
                        f"Counts from 1 to {k - 1}, what would reveal them, and every count of a "
                        f"unit table of fewer than {max(k, TINY)} units are suppressed (null) "
                        "under the disclosure settings"
                    )
                ],
                affects=["/population"],
            )
        )
    return replace(parts, population=shown, size=size, caveats=tuple(sort_caveats(caveats)))


def _carried(code: CaveatCode, message: list[Segment]) -> Caveat:
    """``UNKNOWN_EXCLUDED`` or ``LIFT_DIFFERS`` for a suppressed count that was 0: carried
    whatever it was, so that its presence shows nothing (D297)."""
    return Caveat(
        code=code, severity=CORE_SEVERITIES[code], message=message, affects=["/population"]
    )


def _rewritten(caveat: Caveat, shown: Population) -> Caveat:
    """A caveat whose message quotes a count, quoting it as the pass leaves it."""
    if caveat.code == CaveatCode.UNKNOWN_EXCLUDED:
        return caveat.model_copy(update={"message": unknown_message(shown.n_unknown)})
    if caveat.code == CaveatCode.LIFT_DIFFERS:
        return caveat.model_copy(update={"message": lift_message(shown.lift_differs)})
    return caveat


__all__ = ["TINY", "disclosed", "suppressed"]

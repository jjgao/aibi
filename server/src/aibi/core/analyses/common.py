"""What the core's descriptive analyses share (SPEC §8.1, §8.3, §8.4; D328, D330): their cohorts
at their positions, the population disclosed, and the caveats their cohorts raise.

- ``CohortAt``: a cohort at its position, canonical, with its accounting.
- ``populations``: each position's population, disclosed as its cohort's count is (D297, D320).
- ``cohort_caveats``: what the cohorts' counts raise in a result: ``UNKNOWN_EXCLUDED`` where a
  unit's membership is unknown (always under *k*, so that it says nothing of one), or a unit's
  value of a variable is excluded, one caveat naming both parts, the flags of
  their truth values (``SCOPE_PARTIAL``, ``COVERAGE_PROPOSED``) naming the relationships, and
  ``LIFT_DIFFERS`` where the other lift rule changes some unit's membership (always under *k*,
  whose message quotes no count, as ``lift_differs`` is always suppressed there).
- ``shown`` and ``analysed_of``: what the pass shows of a variable at a position, its split of
  the cohort's units and their reasons, and ``analysed`` from it, nothing that combines
  variables under *k* (D329).
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import cast

from aibi.core.analyses.disclosure import small, split_hidden
from aibi.core.analyses.existence import CohortAt
from aibi.core.engine.counts import count_parts
from aibi.core.engine.suppression import disclosed
from aibi.core.engine.truth import Mark
from aibi.core.engine.variables import Joint, Materialised
from aibi.core.schema.caveats import CORE_SEVERITIES, Caveat, CaveatCode
from aibi.core.schema.numbers import NotEstimableReason
from aibi.core.schema.output import Segment, data, text
from aibi.core.schema.results import Analysed, AnalysedCounts, AnalysedVariable, Population
from aibi.core.schema.semantics import ExclusionReason, Flag

FLAGS = {
    Flag.SCOPE_PARTIAL: CaveatCode.SCOPE_PARTIAL,
    Flag.COVERAGE_PROPOSED: CaveatCode.COVERAGE_PROPOSED,
}
_FLAG_TEXT = {
    Flag.SCOPE_PARTIAL: "Some answers cover only the scope values listed as assessed, for ",
    Flag.COVERAGE_PROPOSED: "Some answers rely on coverage that is only proposed, for ",
}


def caveat(code: CaveatCode, affects: Sequence[str], *message: Segment) -> Caveat:
    return Caveat(
        code=code, severity=CORE_SEVERITIES[code], message=list(message), affects=list(affects)
    )


def listed(names: Sequence[str]) -> list[Segment]:
    """Names from descriptors, each a data token, comma separated."""
    found: list[Segment] = []
    for index, name in enumerate(names):
        if index:
            found.append(text(", "))
        found.append(data(name))
    return found


def populations(positions: Sequence[CohortAt], k: int | None) -> list[Population]:
    """Each position's population, disclosed as its cohort's count is (D297, D320)."""
    return [
        disclosed(count_parts(position.cohort, position.accounting), k).population
        for position in positions
    ]


def flag_caveats(marks: Iterable[Mark], affects: str) -> list[Caveat]:
    """``SCOPE_PARTIAL`` and ``COVERAGE_PROPOSED`` for the flags of what a result read, each
    naming the relationships whose coverage raised it."""
    given = set(marks)
    found: list[Caveat] = []
    for flag, code in FLAGS.items():
        relationships = sorted({mark.relationship for mark in given if mark.flag is flag})
        if relationships:
            found.append(caveat(code, [affects], text(_FLAG_TEXT[flag]), *listed(relationships)))
    return found


def cohort_caveats(
    positions: Sequence[CohortAt],
    population: Sequence[Population],
    k: int | None,
    *,
    values_unknown: bool = False,
) -> list[Caveat]:
    """The caveats a result's cohorts raise (module docstring); ``values_unknown`` says that the
    result excludes units from its variables' values too, which the one ``UNKNOWN_EXCLUDED``
    then names beside the cohorts' (under *k*, always)."""
    found: list[Caveat] = []
    cohorts_unknown = k is not None or any(count.n_unknown for count in population)
    if values_unknown:
        found.append(
            caveat(
                CaveatCode.UNKNOWN_EXCLUDED,
                ["/analysed", "/population"],
                text(
                    "Units whose membership of a cohort, or whose value of a column, could not "
                    "be decided or has none, if any, are left out of the cohort or of that "
                    "column's values; population and analysed count them by reason, where the "
                    "disclosure settings show them"
                ),
            )
        )
    elif cohorts_unknown:
        found.append(
            caveat(
                CaveatCode.UNKNOWN_EXCLUDED,
                ["/population"],
                text(
                    "Units whose membership of a cohort could not be decided, if any, are left "
                    "out of it; population counts them by reason, where the disclosure "
                    "settings show them"
                ),
            )
        )
    found += flag_caveats(
        (mark for position in positions for mark in position.accounting.marks), "/population"
    )
    lifts = [position.accounting.lift_differs for position in positions]
    if k is not None:
        found.append(
            caveat(
                CaveatCode.LIFT_DIFFERS,
                ["/population"],
                text("Whether the other lift rule would change any cohort's units, and for "),
                text("how many, is suppressed by the disclosure settings"),
            )
        )
    elif any(lifts):
        parts = [
            f" {count} units of the cohort at position {position}"
            for position, count in enumerate(lifts)
            if count
        ]
        found.append(
            caveat(
                CaveatCode.LIFT_DIFFERS,
                ["/population"],
                text("The other lift rule would change the membership of" + ";".join(parts)),
            )
        )
    return found


@dataclass(frozen=True)
class Shown:
    """What the pass shows of a variable at a position: its split, and its breakdown."""

    split: bool
    excluded: bool


def shown(found: Materialised, size_shown: bool, k: int | None) -> Shown:
    """What the pass shows of a variable over a position whose cohort's ``n_true`` is shown or
    not (``size_shown``): its split whole or not at all, its reasons only with it and when none
    is small (D329)."""
    if k is None:
        return Shown(True, True)
    split = size_shown and not split_hidden(found.n, found.excluded_units, k)
    excluded = split and not any(small(k, count) for count in found.excluded.values())
    return Shown(split, excluded)


def _counts(
    model: type[AnalysedCounts],
    n: int,
    excluded: Mapping[ExclusionReason, int],
    units: int,
    visible: Shown,
) -> AnalysedCounts:
    suppressed = NotEstimableReason.SUPPRESSED
    reasons = {
        member: suppressed
        for member, lost in (
            ("/n", not visible.split),
            ("/excluded_units", not visible.split),
            ("/excluded", not visible.excluded),
        )
        if lost
    }
    return model(
        n=n if visible.split else None,
        excluded=dict(excluded) if visible.excluded else None,
        excluded_units=units if visible.split else None,
        not_estimable=reasons or None,
    )


def analysed_of(
    found: Sequence[Materialised], together: Joint | None, split: Sequence[Shown], k: int | None
) -> Analysed:
    """A position's ``analysed`` over its variables (§8.1): one variable's split, or with two or
    more each one's in ``variables`` and, but under *k*, the units some one has a value for."""
    if len(found) == 1:
        [one], [visible] = found, split
        counted = _counts(AnalysedCounts, one.n, one.excluded, one.excluded_units, visible)
        return Analysed.model_validate(counted.model_dump())
    assert together is not None, "several variables are counted together"
    variables = [
        cast(
            AnalysedVariable,
            _counts(AnalysedVariable, one.n, one.excluded, one.excluded_units, visible),
        )
        for one, visible in zip(found, split, strict=True)
    ]
    whole = Shown(k is None, k is None)
    counted = _counts(AnalysedCounts, together.known, together.none_by_reason, together.none, whole)
    return Analysed.model_validate({**counted.model_dump(), "variables": variables})


__all__ = [
    "FLAGS",
    "CohortAt",
    "Shown",
    "analysed_of",
    "caveat",
    "cohort_caveats",
    "flag_caveats",
    "listed",
    "populations",
    "shown",
]

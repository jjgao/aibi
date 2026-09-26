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
"""

from collections.abc import Iterable, Sequence

from aibi.core.analyses.existence import CohortAt
from aibi.core.engine.counts import count_parts
from aibi.core.engine.suppression import disclosed
from aibi.core.engine.truth import Mark
from aibi.core.schema.caveats import CORE_SEVERITIES, Caveat, CaveatCode
from aibi.core.schema.output import Segment, data, text
from aibi.core.schema.results import Population
from aibi.core.schema.semantics import Flag

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


__all__ = [
    "FLAGS",
    "CohortAt",
    "caveat",
    "cohort_caveats",
    "flag_caveats",
    "listed",
    "populations",
]

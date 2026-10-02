"""The digested part of a cohort's count, before the disclosure pass (SPEC §6.6, §8.1; D288).

A cohort count's digest hashes its ``population``, ``size`` and ``caveats`` (§7.6). They are made
here from a canonical cohort and its evaluation: the population with ``unknown_by_leaf`` keyed by
leaf keys, the size as the cohort's units over the unit table, and the caveats a cohort count can
carry, each code once, affecting ``/population``:

- ``UNKNOWN_EXCLUDED`` when some unit is UNKNOWN;
- ``SCOPE_PARTIAL`` and ``COVERAGE_PROPOSED`` when some unit's cohort-level truth value carries the
  flag, the message naming the relationships;
- ``UNCONFIRMED_SEMANTICS`` for the fields read whose status is not confirmed (§5.1), listed;
- ``LIFT_DIFFERS`` when ``lift_differs`` is above zero, with the count;
- ``NOT_ESTIMABLE`` when the unit table has no rows, and so the size no estimate (D297);
- ``DRAFT_RELEASE`` over a curation session's draft;
- the codes the packs' caveat rules raised, with their declared severities.

The disclosure pass (§8.4, ``suppression.disclosed``) applies to these before the count is
returned and its digest taken; messages are rendered text, outside the digest, and those that
quote counts are written again for the counts the pass shows. ``static_caveats`` are the
caveats that need no data, which ``validate_document`` reports (§11.1).
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from aibi.core.engine.canonical import CanonicalCohort
from aibi.core.engine.ids import count_digest
from aibi.core.engine.resolve import FieldRead
from aibi.core.engine.truth import Mark
from aibi.core.schema.caveats import CORE_SEVERITIES, Caveat, CaveatCode, sort_caveats
from aibi.core.schema.numbers import DenominatorDefinition, NotEstimableReason, Proportion
from aibi.core.schema.output import Segment, data, text
from aibi.core.schema.results import Population
from aibi.core.schema.semantics import Flag, Reason

_POPULATION = ["/population"]
_FLAGS = {
    Flag.SCOPE_PARTIAL: CaveatCode.SCOPE_PARTIAL,
    Flag.COVERAGE_PROPOSED: CaveatCode.COVERAGE_PROPOSED,
}
_FLAG_TEXT = {
    Flag.SCOPE_PARTIAL: "Some units' answers cover only the scope values listed as assessed, for ",
    Flag.COVERAGE_PROPOSED: "Some units' answers rely on coverage that is only proposed, for ",
}


class Tally(Protocol):
    """A cohort's accounting (§6.6): the reference evaluator's ``CohortResult``, or the SQL
    compiler's ``Accounting``, which the differential tests hold equal."""

    @property
    def n_true(self) -> int: ...
    @property
    def n_false(self) -> int: ...
    @property
    def n_unknown(self) -> int: ...
    @property
    def unknown_by_reason(self) -> Mapping[Reason, int]: ...
    @property
    def unknown_by_clause(self) -> tuple[int, ...]: ...
    @property
    def lift_differs(self) -> int: ...
    @property
    def marks(self) -> frozenset[Mark]: ...


@dataclass(frozen=True)
class CountParts:
    """A cohort count's digested members, before the disclosure pass."""

    population: Population
    size: Proportion
    caveats: tuple[Caveat, ...]

    @property
    def digest(self) -> str:
        return count_digest(self.population, self.size, self.caveats)


def count_parts(cohort: CanonicalCohort, result: Tally) -> CountParts:
    """A canonical cohort's population, size and caveats from its evaluation (D288)."""
    units = result.n_true + result.n_false + result.n_unknown
    population = Population(
        n_true=result.n_true,
        n_false=result.n_false,
        n_unknown=result.n_unknown,
        unknown_by_reason={reason: result.unknown_by_reason[reason] for reason in Reason},
        unknown_by_leaf=dict(zip(cohort.keys, result.unknown_by_clause, strict=True)),
        lift_differs=result.lift_differs,
    )
    size = Proportion(
        estimate=result.n_true / units if units else None,
        numerator=result.n_true,
        denominator=units,
        denominator_definition=DenominatorDefinition(
            position=None, predicate=None, counts="unit_table"
        ),
        not_estimable=None if units else {"/estimate": NotEstimableReason.NO_UNITS},
    )
    return CountParts(population, size, tuple(sort_caveats(_caveats(cohort, result))))


def _caveat(code: CaveatCode, *message: Segment) -> Caveat:
    return Caveat(
        code=code, severity=CORE_SEVERITIES[code], message=list(message), affects=_POPULATION
    )


def unknown_message(n_unknown: int | None) -> list[Segment]:
    """``UNKNOWN_EXCLUDED``'s message, with the count as the disclosure pass leaves it (§8.4); a
    suppressed one may be 0 in a small unit table, so the message does not say whether any unit is
    unknown (D297)."""
    if n_unknown is None:
        return [
            text("Units that could not be evaluated, if any, are not in the cohort; their "),
            text("number is suppressed by the disclosure settings"),
        ]
    return [text(f"{n_unknown} units could not be evaluated and are not in the cohort")]


def lift_message(lift_differs: int | None) -> list[Segment]:
    """``LIFT_DIFFERS``'s message, with the count as the disclosure pass leaves it (§8.4); a
    suppressed one may be 0 beside a suppressed count, so the message does not say whether the
    other lift rule changes any unit (D297)."""
    if lift_differs is None:
        return [
            text("Whether the other lift rule would change the answer for any units, and for "),
            text("how many, is suppressed by the disclosure settings"),
        ]
    return [
        text(f"The other lift rule would change the answer for {lift_differs} "),
        text("units"),
    ]


def _caveats(cohort: CanonicalCohort, result: Tally) -> list[Caveat]:
    found: list[Caveat] = []
    if result.n_unknown:
        found.append(_caveat(CaveatCode.UNKNOWN_EXCLUDED, *unknown_message(result.n_unknown)))
    for flag, code in _FLAGS.items():
        relationships = sorted({mark.relationship for mark in result.marks if mark.flag is flag})
        if relationships:
            found.append(
                _caveat(code, text(_FLAG_TEXT[flag]), *[data(rel) for rel in relationships])
            )
    if result.lift_differs:
        found.append(_caveat(CaveatCode.LIFT_DIFFERS, *lift_message(result.lift_differs)))
    if not result.n_true + result.n_false + result.n_unknown:
        found.append(
            _caveat(
                CaveatCode.NOT_ESTIMABLE,
                text("The unit table has no rows, so the cohort's size is not estimable"),
            )
        )
    return [*found, *static_caveats(cohort)]


def static_caveats(cohort: CanonicalCohort) -> list[Caveat]:
    """The caveats of a cohort's count that need no data (§11.1, ``validate_document``):
    ``UNCONFIRMED_SEMANTICS`` for the fields read, ``DRAFT_RELEASE`` over a draft, and the codes
    the packs' caveat rules raised, sorted."""
    found: list[Caveat] = []
    unconfirmed = cohort.resolved.unconfirmed
    if unconfirmed:
        found.append(
            _caveat(
                CaveatCode.UNCONFIRMED_SEMANTICS,
                text("These fields were read but are not confirmed: "),
                *_fields(unconfirmed),
            )
        )
    if cohort.release.status == "draft":
        found.append(
            _caveat(
                CaveatCode.DRAFT_RELEASE,
                text("Counted against the draft of a curation session of "),
                data(cohort.release.dataset),
            )
        )
    found.extend(
        Caveat(
            code=ruled.code,
            severity=ruled.severity,
            message=[text("Raised by the caveat rule of pack "), data(ruled.pack)],
            affects=_POPULATION,
        )
        for ruled in cohort.caveats
    )
    return sort_caveats(found)


def _fields(fields: tuple[FieldRead, ...]) -> list[Segment]:
    segments: list[Segment] = []
    for read in fields:
        if segments:
            segments.append(text(", "))
        segments += [data(read.descriptor + read.pointer), text(f" ({read.status})")]
    return segments


__all__ = [
    "CountParts",
    "Tally",
    "count_parts",
    "lift_message",
    "static_caveats",
    "unknown_message",
]

"""Truth values, their reasons and flags, and the combinators (SPEC §6.3).

Every criterion evaluates, per row it applies to, to TRUE, FALSE or UNKNOWN. An UNKNOWN carries a
non-empty set of reasons; every truth value carries a possibly empty set of flags. A flag here
also names the relationship whose coverage raised it, so that ``COVERAGE_PROPOSED`` can say
which (§8.3); ``TruthValue.flags`` gives the flags alone.

The combinators are Kleene's strong three-valued logic:

- ``all`` is FALSE if any operand is FALSE, otherwise UNKNOWN if any is UNKNOWN, otherwise TRUE;
  ``any`` is TRUE if any operand is TRUE, otherwise UNKNOWN if any is UNKNOWN, otherwise FALSE;
  ``not`` swaps TRUE and FALSE and leaves UNKNOWN.
- The reasons of an UNKNOWN result are the union of those of its UNKNOWN operands. The flags of a
  result are the union of those of the operands with the result's value; ``not`` keeps them.
- ``known(C)`` is TRUE iff ``C`` is not UNKNOWN, and ``unknown(C)`` iff it is; both keep ``C``'s
  flags.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

from aibi.core.schema.semantics import Flag, Reason


class Truth(StrEnum):
    TRUE = "TRUE"
    FALSE = "FALSE"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True, order=True)
class Mark:
    """A flag, with the relationship whose coverage raised it."""

    flag: Flag
    relationship: str


Marks = frozenset[Mark]
_NONE: Marks = frozenset()


@dataclass(frozen=True, slots=True)
class TruthValue:
    value: Truth
    reasons: frozenset[Reason] = frozenset()
    """Non-empty exactly when the value is UNKNOWN."""
    marks: Marks = _NONE

    def __post_init__(self) -> None:
        if (self.value is Truth.UNKNOWN) != bool(self.reasons):
            raise ValueError("An UNKNOWN has reasons, and only an UNKNOWN has them")

    @property
    def flags(self) -> frozenset[Flag]:
        return frozenset(mark.flag for mark in self.marks)

    @property
    def is_true(self) -> bool:
        return self.value is Truth.TRUE

    @property
    def is_false(self) -> bool:
        return self.value is Truth.FALSE

    @property
    def is_unknown(self) -> bool:
        return self.value is Truth.UNKNOWN

    def marked(self, *marks: Iterable[Mark]) -> "TruthValue":
        """The same value with more marks."""
        added = self.marks.union(*marks)
        if added == self.marks:
            return self
        return TruthValue(self.value, self.reasons, added)


TRUE = TruthValue(Truth.TRUE)
FALSE = TruthValue(Truth.FALSE)


def unknown(reasons: Iterable[Reason], marks: Iterable[Mark] = ()) -> TruthValue:
    return TruthValue(Truth.UNKNOWN, frozenset(reasons), frozenset(marks))


def truth(value: bool, marks: Iterable[Mark] = ()) -> TruthValue:
    """TRUE or FALSE, with marks."""
    return TruthValue(Truth.TRUE if value else Truth.FALSE, frozenset(), frozenset(marks))


def _union(values: Iterable[TruthValue]) -> tuple[frozenset[Reason], Marks]:
    reasons: set[Reason] = set()
    marks: set[Mark] = set()
    for value in values:
        reasons.update(value.reasons)
        marks.update(value.marks)
    return frozenset(reasons), frozenset(marks)


def _combine(operands: Iterable[TruthValue], dominant: Truth, neutral: Truth) -> TruthValue:
    """``all`` (FALSE dominant) or ``any`` (TRUE dominant)."""
    given = list(operands)
    for value in (dominant, Truth.UNKNOWN, neutral):
        chosen = [operand for operand in given if operand.value is value]
        if chosen or value is neutral:
            reasons, marks = _union(chosen)
            return TruthValue(value, reasons if value is Truth.UNKNOWN else frozenset(), marks)
    raise AssertionError("unreachable")  # pragma: no cover


def all_of(operands: Iterable[TruthValue]) -> TruthValue:
    """``all``: TRUE for no operands."""
    return _combine(operands, Truth.FALSE, Truth.TRUE)


def any_of(operands: Iterable[TruthValue]) -> TruthValue:
    """``any``: FALSE for no operands."""
    return _combine(operands, Truth.TRUE, Truth.FALSE)


def not_(operand: TruthValue) -> TruthValue:
    if operand.value is Truth.UNKNOWN:
        return operand
    swapped = Truth.FALSE if operand.value is Truth.TRUE else Truth.TRUE
    return TruthValue(swapped, frozenset(), operand.marks)


def known(operand: TruthValue) -> TruthValue:
    return truth(operand.value is not Truth.UNKNOWN, operand.marks)


def unknown_of(operand: TruthValue) -> TruthValue:
    """``unknown(C)``."""
    return truth(operand.value is Truth.UNKNOWN, operand.marks)


__all__ = [
    "FALSE",
    "TRUE",
    "Mark",
    "Marks",
    "Truth",
    "TruthValue",
    "all_of",
    "any_of",
    "known",
    "not_",
    "truth",
    "unknown",
    "unknown_of",
]

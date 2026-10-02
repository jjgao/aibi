"""Observation states, reasons, flags and exclusion reasons (SPEC §6.2, §6.3, §6.6).

These are the vocabulary of three-valued evaluation: missing is not negative, and every unknown
or excluded unit is counted by reason.
"""

from enum import StrEnum


class ObservationState(StrEnum):
    """The state of one (unit, variable) pair, or of one cell (SPEC §6.2)."""

    PRESENT = "PRESENT"
    """A value or a matching related row exists."""
    ABSENT = "ABSENT"
    """It was assessed, and there is none. Arises only from existence questions."""
    NOT_ASSESSED = "NOT_ASSESSED"
    """It is known that it was not assessed."""
    NOT_APPLICABLE = "NOT_APPLICABLE"
    """The question does not apply."""
    UNKNOWN = "UNKNOWN"
    """There is no information either way."""


class Reason(StrEnum):
    """Why a truth value is UNKNOWN (SPEC §6.3). An UNKNOWN always has at least one."""

    NOT_ASSESSED = "NOT_ASSESSED"
    """A cell says it was not assessed."""
    NOT_COVERED = "NOT_COVERED"
    """Coverage says the row was not assessed, or nothing below it could be considered."""
    NO_INFORMATION = "NO_INFORMATION"
    """No information either way: an empty cell, an undeclared code, undeclared coverage."""
    NO_PARENT = "NO_PARENT"
    """An up step met a null or dangling foreign key."""
    OUT_OF_SCOPE = "OUT_OF_SCOPE"
    """The row is outside a relationship's ``parent_scope``."""
    NO_ROWS = "NO_ROWS"
    """Nothing to evaluate: ``every`` over no rows, or ``match: "all"`` over an empty list."""


class Flag(StrEnum):
    """Set on a truth value that depended on partial or proposed coverage (SPEC §6.3, §6.5)."""

    SCOPE_PARTIAL = "SCOPE_PARTIAL"
    COVERAGE_PROPOSED = "COVERAGE_PROPOSED"


class ExclusionReason(StrEnum):
    """Why an analysis left out a unit or row (SPEC §6.6): the reasons, and two more."""

    NOT_ASSESSED = "NOT_ASSESSED"
    NOT_COVERED = "NOT_COVERED"
    NO_INFORMATION = "NO_INFORMATION"
    NO_PARENT = "NO_PARENT"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"
    NO_ROWS = "NO_ROWS"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    INVALID_VALUE = "INVALID_VALUE"


__all__ = ["ExclusionReason", "Flag", "ObservationState", "Reason"]

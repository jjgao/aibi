"""Refusals (SPEC §8.6).

A refusal names the problem, where it is and what is available instead (A3). Its messages are
segments (SPEC §8.1).
"""

from enum import StrEnum
from typing import Annotated

from pydantic import Field

from aibi.core.schema.ids import JsonPointer, PackCode
from aibi.core.schema.limits import MAX_REFUSALS, REFUSALS
from aibi.core.schema.output import DATA_MARK, LAX, Count, Output, Segment, text
from aibi.core.schema.results import CohortCount


class RefusalCode(StrEnum):
    """Stable refusal codes of the core. Pack codes are namespaced and not listed here."""

    INVALID_JSON = "INVALID_JSON"
    DUPLICATE_KEY = "DUPLICATE_KEY"
    NON_FINITE_NUMBER = "NON_FINITE_NUMBER"
    INTEGER_OUT_OF_RANGE = "INTEGER_OUT_OF_RANGE"
    NULL_NOT_ALLOWED = "NULL_NOT_ALLOWED"
    MISSING_MEMBER = "MISSING_MEMBER"
    UNKNOWN_MEMBER = "UNKNOWN_MEMBER"
    WRONG_TYPE = "WRONG_TYPE"
    INVALID_VALUE = "INVALID_VALUE"
    UNKNOWN_KIND = "UNKNOWN_KIND"
    CONFLICTING_MEMBERS = "CONFLICTING_MEMBERS"
    UNKNOWN_PARAMETER = "UNKNOWN_PARAMETER"
    INVALID_PARAMETER_REFERENCE = "INVALID_PARAMETER_REFERENCE"
    UNKNOWN_COHORT = "UNKNOWN_COHORT"
    COHORT_CYCLE = "COHORT_CYCLE"
    COHORT_MISMATCH = "COHORT_MISMATCH"
    LEAF_NOT_ALLOWED = "LEAF_NOT_ALLOWED"
    DATASET_MISSING = "DATASET_MISSING"
    DUPLICATE_ENTRY = "DUPLICATE_ENTRY"
    REFERENCE_NOT_IN_VIEW = "REFERENCE_NOT_IN_VIEW"
    CONCEPT_REQUIRED = "CONCEPT_REQUIRED"
    CROSS_DATASET_ONLY = "CROSS_DATASET_ONLY"
    UNKNOWN_DATASET = "UNKNOWN_DATASET"
    LIMIT_EXCEEDED = "LIMIT_EXCEEDED"
    UNKNOWN_DESCRIPTOR = "UNKNOWN_DESCRIPTOR"
    """A descriptor refers to one the release does not hold (§13.2)."""
    # Resolving a document against its releases (§6, §7.2, §7.6):
    UNKNOWN_TABLE = "UNKNOWN_TABLE"
    UNKNOWN_COLUMN = "UNKNOWN_COLUMN"
    UNKNOWN_RELATIONSHIP = "UNKNOWN_RELATIONSHIP"
    INVALID_UNIT = "INVALID_UNIT"
    """The unit is not a keyed table of the table graph (§5.3)."""
    INVALID_PATH = "INVALID_PATH"
    """A ``via`` whose steps do not connect, or a path of the wrong shape for its leaf."""
    NO_PATH = "NO_PATH"
    AMBIGUOUS_PATH = "AMBIGUOUS_PATH"
    QUANTIFIER_MISMATCH = "QUANTIFIER_MISMATCH"
    EXCLUDE_SELF_NOT_ALLOWED = "EXCLUDE_SELF_NOT_ALLOWED"
    UNDECLARED_DATATYPE = "UNDECLARED_DATATYPE"
    """A predicate on a column whose datatype nobody declared: its constants cannot be typed."""
    INVALID_CONSTANT = "INVALID_CONSTANT"
    NOT_PERMISSIBLE = "NOT_PERMISSIBLE"
    UNITS_UNCONVERTIBLE = "UNITS_UNCONVERTIBLE"
    RANGE_NOT_ALLOWED = "RANGE_NOT_ALLOWED"
    MEMBER_NOT_APPLICABLE = "MEMBER_NOT_APPLICABLE"
    """A member that does not apply to its column: ``units`` or ``match``."""
    SCOPE_COLUMN_MENTION = "SCOPE_COLUMN_MENTION"
    FILTER_COLUMN_MENTION = "FILTER_COLUMN_MENTION"
    UNKNOWN_SCOPE_COLUMN = "UNKNOWN_SCOPE_COLUMN"
    ROW_IDS_NOT_ALLOWED = "ROW_IDS_NOT_ALLOWED"
    INVALID_KEY = "INVALID_KEY"
    MIXED_RELEASES = "MIXED_RELEASES"
    NOT_SUPPORTED = "NOT_SUPPORTED"
    """Not supported until a later milestone, which the message names."""


class Limit(Output):
    """The limit a refusal hit: one of the names in ``aibi.core.schema.limits``, or a pack's."""

    name: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]*$")]
    max: Count


class Refusal(Output):
    """A structured error (SPEC §8.6).

    ``path`` is a JSON Pointer into the document as written, or ``None``.
    """

    code: Annotated[RefusalCode, LAX] | PackCode
    path: Annotated[JsonPointer, Field(json_schema_extra=DATA_MARK)] | None
    """Its tokens are the document's own keys, so it is data (A6)."""
    message: list[Segment]
    alternatives: list[Segment] = Field(default_factory=list[Segment])
    limit: Limit | None = None
    counts: list[CohortCount] | None = None
    """Cohort counts, with their ids, where a refusal reports numbers (e.g. an overlap, §7.4)."""


def sort_refusals(refusals: list[Refusal]) -> list[Refusal]:
    """Refusals in the order ``validate_document`` returns them: by path, then code (§8.6)."""
    return sorted(refusals, key=lambda refusal: (refusal.path or "", refusal.code))


def finish_refusals(refusals: list[Refusal]) -> list[Refusal]:
    """Refusals as they are returned (§8.6): the first of each (path, code), sorted, and at most
    ``MAX_REFUSALS``, then one that says how many more were found."""
    kept: dict[tuple[str | None, str], Refusal] = {}
    for refusal in refusals:
        kept.setdefault((refusal.path, str(refusal.code)), refusal)
    ordered = sort_refusals(list(kept.values()))
    if len(ordered) <= MAX_REFUSALS:
        return ordered
    return [
        *ordered[:MAX_REFUSALS],
        Refusal(
            code=RefusalCode.LIMIT_EXCEEDED,
            path=None,
            message=[text(f"{len(ordered) - MAX_REFUSALS} more refusals were left out")],
            limit=Limit(name=REFUSALS, max=MAX_REFUSALS),
        ),
    ]

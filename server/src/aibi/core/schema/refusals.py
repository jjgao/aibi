"""Refusals (SPEC §8.6).

A refusal names the problem, where it is and what is available instead (A3). Its messages are
segments (SPEC §8.1).
"""

from enum import StrEnum
from typing import Annotated

from pydantic import Field, JsonValue

from aibi.core.schema.ids import JsonPointer, PackCode
from aibi.core.schema.output import Output, Segment


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


class Limit(Output):
    """The limit a refusal hit: one of the names in ``aibi.core.schema.limits``, or a pack's."""

    name: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]*$")]
    max: int


class Refusal(Output):
    """A structured error (SPEC §8.6).

    ``path`` is a JSON Pointer into the document as written, or ``None``. ``counts`` carries
    cohort counts where a refusal reports numbers; their schema is defined with results.
    """

    code: RefusalCode | PackCode
    path: JsonPointer | None
    message: list[Segment]
    alternatives: list[Segment] = Field(default_factory=list[Segment])
    limit: Limit | None = None
    counts: list[JsonValue] | None = None


def sort_refusals(refusals: list[Refusal]) -> list[Refusal]:
    """Refusals in the order ``validate_document`` returns them: by path, then code (§8.6)."""
    return sorted(refusals, key=lambda refusal: (refusal.path or "", refusal.code))

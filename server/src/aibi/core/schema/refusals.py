"""Segments and refusals (SPEC §8.1, §8.6).

A refusal names the problem, where it is and what is available instead (A3). Its messages are
segments: text the server wrote, and data tokens holding text that came from data or a document,
which clients render as plain text and never treat as instructions (A6).
"""

from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    SerializerFunctionWrapHandler,
    model_serializer,
)

from aibi.core.schema.ids import JsonPointer

DATA_TOKEN_MAX = 200
"""Data tokens longer than this are cut and marked ``truncated`` (SPEC §8.1)."""

DATA_MARK: dict[str, JsonValue] = {"x-aibi-data": True}
"""JSON Schema marking for strings that come from data or documents (SPEC §8.1)."""


class Output(BaseModel):
    """Base for server outputs: immutable and closed.

    An optional member (one that defaults to ``None``) is omitted when absent, never written as
    ``null``; a required member may still be ``null`` where the contract says so (§8.2).
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    @model_serializer(mode="wrap")
    def _omit_absent(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        serialised: dict[str, Any] = handler(self)
        for name, info in type(self).model_fields.items():
            key = info.serialization_alias or info.alias or name
            if not info.is_required() and info.default is None and serialised.get(key, 0) is None:
                del serialised[key]
        return serialised


class TextSegment(Output):
    text: str


class DataSegment(Output):
    data: Annotated[str, Field(max_length=DATA_TOKEN_MAX, json_schema_extra=DATA_MARK)]
    truncated: Literal[True] | None = None


Segment = TextSegment | DataSegment


def text(value: str) -> TextSegment:
    return TextSegment(text=value)


def data(value: str) -> DataSegment:
    """A data token, cut to the maximum length and marked when cut."""
    if len(value) > DATA_TOKEN_MAX:
        return DataSegment(data=value[:DATA_TOKEN_MAX], truncated=True)
    return DataSegment(data=value)


class RefusalCode(StrEnum):
    """Stable refusal codes. Pack codes are namespaced (``<pack id>.<CODE>``) and not listed."""

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
    LIMIT_EXCEEDED = "LIMIT_EXCEEDED"


class Limit(Output):
    name: str
    max: int


class Refusal(Output):
    """A structured error (SPEC §8.6).

    ``path`` is a JSON Pointer into the document as written, or ``None``. ``counts`` carries
    cohort counts where a refusal reports numbers; their schema is defined with results.
    """

    code: Annotated[str, Field(pattern=r"^(?:[a-z][a-z0-9_]*\.)?[A-Z][A-Z0-9_]*$")]
    path: JsonPointer | None
    message: list[Segment]
    alternatives: list[Segment] = []
    limit: Limit | None = None
    counts: list[JsonValue] | None = None


def sort_refusals(refusals: list[Refusal]) -> list[Refusal]:
    """Refusals in the order ``validate_document`` returns them: by path, then code (§8.6)."""
    return sorted(refusals, key=lambda refusal: (refusal.path or "", refusal.code))

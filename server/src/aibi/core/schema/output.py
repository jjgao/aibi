"""The base of every server output, segments and the data mark (SPEC §8.1).

Messages and readbacks are segments: text the server wrote, and data tokens holding text that
came from data or a document, which clients render as plain text and never treat as
instructions (A6).
"""

from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    SerializerFunctionWrapHandler,
    model_serializer,
)

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

    # No return annotation: Pydantic would take it as the serialised type, and the output's
    # serialisation schema would be an untyped object instead of the model's.
    @model_serializer(mode="wrap")
    def _omit_absent(self, handler: SerializerFunctionWrapHandler):
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

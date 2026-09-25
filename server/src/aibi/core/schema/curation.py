"""What curation sessions, proposals and the curation queue exchange (SPEC §11.1, §11.2, §12.3,
D245, D248, D250).

- A change to a draft is a ``ChangeRequest``: 1 to ``MAX_CHANGE_EDITS`` edits, applied in order
  and at once (D245). Each ``Edit`` names whole curated fields by JSON Pointer (``/label``,
  ``/definition``, ``/fields/<name>``, ``/extensions/<pack>/<name>``); ``put`` gives a whole
  descriptor without ``version`` or ``curation``, which the server sets (§5.1, D243).
- A proposal is a ``ProposalInput``: a value for a field (or, at the pointer ``""``, a whole
  descriptor), or its removal, with evidence (D248).
- The curation queue (``CurationQueue``) is an output: the fields that are ``imported_default`` or
  ``proposed``, the fields nobody declared from a fixed list, the open proposals and the import
  report's notes, capped at ``MAX_QUEUE_ITEMS`` items and ``MAX_QUEUE_BYTES`` bytes (D250). It
  holds no cell values.

``by`` is never part of a request: the server sets it (§5.1, §11.1).
"""

from typing import Annotated, Literal, Self, cast

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Discriminator,
    Field,
    JsonValue,
    StrictBool,
    StringConstraints,
    Tag,
    WithJsonSchema,
    model_validator,
)
from pydantic_core import PydanticCustomError

from aibi.core.schema.descriptors import AnyJson, By, CurationPointer, PositiveInt, Text, Timestamp
from aibi.core.schema.ids import (
    IDENT,
    MAX_COLUMNS,
    MAX_IDENTIFIER,
    NO_DOUBLE_UNDERSCORE,
    DatasetId,
    Sha256,
)
from aibi.core.schema.jsonio import JsonError, json_value
from aibi.core.schema.limits import CHANGE_EDITS, MAX_CHANGE_EDITS, LimitName
from aibi.core.schema.output import DATA_MARK, OUTPUT_JSON_MARK, Count, Output, Segment
from aibi.core.schema.pack_api import NoteKind

_KEYED = rf"{IDENT}\.{IDENT}(?:\+{IDENT})*"
DESCRIPTOR_ID_RE = rf"^(?:{IDENT}(?:\.{IDENT})?|(?:rel|cov):{_KEYED}|ep:{IDENT})$"
DescriptorId = Annotated[
    str,
    StringConstraints(
        pattern=DESCRIPTOR_ID_RE,
        max_length=4 + MAX_IDENTIFIER + 1 + MAX_COLUMNS * (MAX_IDENTIFIER + 1),
    ),
    NO_DOUBLE_UNDERSCORE,
    Field(json_schema_extra={"not": {"pattern": "__"}}),
]
"""The id of a descriptor in a release: the dataset's, a table's, a column's, a relationship's,
a coverage's or an endpoint's (§5.1)."""


class _Request(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)


class SetField(_Request):
    """Set a curated field's value; its entry becomes ``asserted`` by the operator."""

    op: Literal["set"]
    descriptor: DescriptorId
    pointer: CurationPointer
    value: AnyJson
    evidence: Text | None = None


class RemoveField(_Request):
    """Remove a curated field; one with an inference leaves a tombstone (D240)."""

    op: Literal["remove"]
    descriptor: DescriptorId
    pointer: CurationPointer


class Confirm(_Request):
    """Assert the current values of the fields named, or of every field."""

    op: Literal["confirm"]
    descriptor: DescriptorId
    pointers: Annotated[list[CurationPointer], Field(min_length=1, max_length=256)] | None = None
    evidence: Text | None = None


class PutDescriptor(_Request):
    """Write a whole descriptor, without ``version`` or ``curation``."""

    op: Literal["put"]
    descriptor: AnyJson
    evidence: Text | None = None


class RemoveDescriptor(_Request):
    """Remove a relationship, coverage, endpoint or derived column (D245)."""

    op: Literal["remove_descriptor"]
    descriptor: DescriptorId


class Accept(_Request):
    """Apply an open proposal to the draft (D248)."""

    op: Literal["accept"]
    proposal: PositiveInt


_OPS = ("set", "remove", "confirm", "put", "remove_descriptor", "accept")


def _op(value: object) -> str | None:
    found: object = (
        cast(dict[str, object], value).get("op")
        if isinstance(value, dict)
        else getattr(value, "op", None)
    )
    return found if isinstance(found, str) and found in _OPS else None


Edit = Annotated[
    Annotated[SetField, Tag("set")]
    | Annotated[RemoveField, Tag("remove")]
    | Annotated[Confirm, Tag("confirm")]
    | Annotated[PutDescriptor, Tag("put")]
    | Annotated[RemoveDescriptor, Tag("remove_descriptor")]
    | Annotated[Accept, Tag("accept")],
    Discriminator(
        _op,
        custom_error_type="unknown_kind",
        custom_error_message="op is set, remove, confirm, put, remove_descriptor or accept",
    ),
]


class ChangeRequest(_Request):
    edits: Annotated[
        list[Edit], Field(min_length=1, max_length=MAX_CHANGE_EDITS), LimitName(CHANGE_EDITS)
    ]


class ProposalInput(_Request):
    """A proposal: ``value`` for the field at ``pointer`` (a whole descriptor at ``""``), or
    ``remove``; exactly one of them is given."""

    descriptor: DescriptorId
    pointer: CurationPointer
    value: AnyJson = None
    remove: StrictBool = False
    evidence: Text | None = None

    @model_validator(mode="after")
    def _one(self) -> Self:
        if ("value" in self.model_fields_set) == self.remove:
            raise PydanticCustomError(
                "conflicting_members", "Give a value, or remove: true, and not both"
            )
        return self


def _json(value: JsonValue) -> JsonValue:
    try:
        return json_value(value)
    except JsonError:
        raise PydanticCustomError(
            "output_json", "The value is not one JSON text carries unchanged (SPEC §8.2)"
        ) from None


QueueValue = Annotated[
    JsonValue, AfterValidator(_json), WithJsonSchema({OUTPUT_JSON_MARK: True, **DATA_MARK})
]
"""A descriptor's value, as the queue shows it: data (A6)."""
QueueText = Annotated[str, Field(json_schema_extra=DATA_MARK)]
ReleaseKind = Literal["dataset", "table", "column", "relationship", "coverage", "endpoint"]


class QueueField(Output):
    """A curated field whose status is ``imported_default`` or ``proposed``."""

    descriptor: DescriptorId
    kind: ReleaseKind
    pointer: CurationPointer
    status: Literal["imported_default", "proposed"]
    by: By
    at: Timestamp
    evidence: QueueText | None = None
    value: QueueValue


class QueueUndeclared(Output):
    """A field nobody declared: a table's ``role``, ``primary_key`` or ``grain``, a column's
    ``datatype``, the ``units`` of a number or time offset, a coverage's ``parents``, or a
    relationship without coverage (the coverage's id, at the pointer ``""``)."""

    descriptor: DescriptorId
    kind: ReleaseKind
    pointer: CurationPointer


class QueueProposal(Output):
    id: PositiveInt
    descriptor: DescriptorId
    pointer: CurationPointer
    value: QueueValue
    remove: bool
    proposer: By
    evidence: QueueText | None = None
    at: Timestamp
    release: Sha256
    """The manifest of the release it was made against."""
    stale: bool
    """Made against a release that is not the latest published one."""
    accepted_in_draft: bool
    """Accepted in the open session's draft, which still holds it and has not been published."""


class QueueNote(Output):
    """A note of the import report (D231): counts and row references, never cell values."""

    kind: NoteKind
    subject: QueueText | None = None
    message: list[Segment]
    count: Count | None = None
    rows: list[Count] = Field(default_factory=list[int])


class CurationQueue(Output):
    dataset: DatasetId
    release: Sha256
    label: PositiveInt
    fields: list[QueueField]
    undeclared: list[QueueUndeclared]
    proposals: list[QueueProposal]
    notes: list[QueueNote]
    truncated: Count
    """Items left out, in the order fields, undeclared, proposals, notes: the first that would
    have made more than ``MAX_QUEUE_ITEMS`` items or ``MAX_QUEUE_BYTES`` bytes of them in JSON,
    and every item after it."""


__all__ = [
    "DESCRIPTOR_ID_RE",
    "Accept",
    "ChangeRequest",
    "Confirm",
    "CurationQueue",
    "DescriptorId",
    "Edit",
    "ProposalInput",
    "PutDescriptor",
    "QueueField",
    "QueueNote",
    "QueueProposal",
    "QueueUndeclared",
    "ReleaseKind",
    "RemoveDescriptor",
    "RemoveField",
    "SetField",
]

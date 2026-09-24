"""The base of every server output, segments and the data mark (SPEC §8.1).

Messages and readbacks are segments: text the server wrote, and data tokens holding text that
came from data or a document, which clients render as plain text and never treat as
instructions (A6).

Outputs hold only what JSON text carries unchanged (§8.2): finite numbers within ±(2^53 − 1),
and Unicode text. Members are checked when an output is built, nested outputs included, and
again by ``model_copy`` with ``update``. ``model_construct`` bypasses validation, as Pydantic
documents; what it builds is still checked before it is dumped, where anything but a JSON value
(objects with string keys, arrays, strings, booleans, ``null`` and such numbers) is refused.
"""

import math
from collections.abc import Iterable, Iterator, Mapping
from contextvars import ContextVar
from enum import Enum
from functools import cache
from typing import Annotated, Any, Literal, Self, cast

from pydantic import (
    AfterValidator,
    AllowInfNan,
    BaseModel,
    ConfigDict,
    Field,
    GetJsonSchemaHandler,
    JsonValue,
    SerializerFunctionWrapHandler,
    Strict,
    StrictInt,
    WithJsonSchema,
    field_validator,
    model_serializer,
    model_validator,
)
from pydantic.json_schema import JsonSchemaValue
from pydantic_core import CoreSchema, PydanticCustomError

from aibi.core.schema.ids import MAX_SAFE_INTEGER
from aibi.core.schema.jsonio import JsonError, is_text, json_value
from aibi.core.schema.limits import MAX_TEXT

DATA_TOKEN_MAX = 200
"""Data tokens longer than this are cut and marked ``truncated`` (SPEC §8.1)."""

DATA_MARK: dict[str, JsonValue] = {"x-aibi-data": True}
"""JSON Schema marking for strings that come from data or documents (SPEC §8.1)."""

OUTPUT_JSON_MARK = "x-aibi-output-json"
"""Marks a JSON value in an output's schema; the export replaces it with a definition whose
numbers are bounded as outputs bound them."""


LAX = Strict(False)
"""Lets an enum in an output accept its value as well as its member, so an output dumped to
JSON-compatible data validates again; every other member stays strict."""


class OutputError(ValueError):
    """An output holds a value that JSON text cannot carry unchanged (SPEC §8.2)."""


def _enum_data(member: Enum) -> object:
    """What a dump writes for an enumeration member: the data of its ``str``, ``int`` or
    ``float`` mixin, which must be its value. A member of a plain ``Enum`` is refused: Pydantic
    writes it by value in one place and as itself in another, and two keys can become one."""
    written: object
    if isinstance(member, str):
        written = str.__str__(member)
    elif isinstance(member, int):
        written = int.__index__(member)
    elif isinstance(member, float):
        written = float.__float__(member)
    else:
        raise OutputError("Outputs hold only JSON values (SPEC §8.2)")
    value = cast(object, member.value)
    if type(value) is not type(written) or value != written:
        raise OutputError("An enumeration member dumps as its data, which is not its value")
    return written


def _check_scalar(value: object) -> None:
    """Raise ``OutputError`` unless a value that is not an array or object is one JSON text
    carries unchanged: Unicode text, a boolean, ``null`` or a finite number within ±(2^53 − 1),
    of Python's own types, or a member of a ``str``, ``int`` or ``float`` enumeration whose data
    is one. Any other subclass could dump otherwise than it looks, so it is refused."""
    kind = type(value)
    if kind is str:
        if not is_text(cast(str, value)):
            raise OutputError("Outputs hold only Unicode text (SPEC §8.2)")
    elif kind is float:
        number = cast(float, value)
        if not math.isfinite(number):
            raise OutputError(
                "Outputs contain no non-finite numbers; a number that cannot be computed is "
                "null, with its reason in not_estimable (SPEC §8.2)"
            )
        if abs(number) > MAX_SAFE_INTEGER:
            raise OutputError("Outputs hold no numbers beyond ±(2^53 - 1) (SPEC §8.2)")
    elif kind is int:
        if abs(cast(int, value)) > MAX_SAFE_INTEGER:
            raise OutputError("Outputs hold no numbers beyond ±(2^53 - 1) (SPEC §8.2)")
    elif isinstance(value, Enum):
        _check_scalar(_enum_data(value))
    elif value is not None and kind is not bool:
        raise OutputError("Outputs hold only JSON values (SPEC §8.2)")


def _check_keys(members: dict[object, object]) -> None:
    """Keys are Unicode text, or members of ``str`` enumerations whose data is, and no two keys
    are written as the same text."""
    written_keys: set[str] = set()
    for key in members:
        written = _enum_data(key) if isinstance(key, Enum) else key
        if type(written) is not str or not is_text(written):
            raise OutputError("Outputs have Unicode text keys only (SPEC §8.2)")
        if written in written_keys:
            raise OutputError("Outputs have no two keys written as the same text (SPEC §8.2)")
        written_keys.add(written)


_SCALARS = frozenset({str, int, float, bool, type(None)})
"""The types of scalars that need no look at their members."""


def _members(value: object) -> Iterable[object] | None:
    """What a container holds: an output's fields, or the items of a dict, list or tuple of
    Python's own types; ``None`` for a scalar. Another model, or a subclass of a container, could
    dump otherwise than its members say, so it is refused."""
    kind = type(value)
    if isinstance(value, Output):
        return [*value.__dict__.values(), *(value.__pydantic_extra__ or {}).values()]
    if isinstance(value, BaseModel):
        raise OutputError("Outputs hold only outputs and JSON values (SPEC §8.2)")
    if kind is dict:
        members = cast(dict[object, object], value)
        _check_keys(members)
        return members.values()
    if kind is list or kind is tuple:
        return cast(list[object] | tuple[object, ...], value)
    if isinstance(value, dict | list | tuple):
        raise OutputError("Outputs hold only JSON values (SPEC §8.2)")
    return None


def check_values(value: object) -> None:
    """Raise ``OutputError`` unless an output holds only outputs and JSON values, all the way
    down: objects with Unicode text keys, arrays, Unicode text, booleans, ``null`` and finite
    numbers within ±(2^53 − 1). A dump could otherwise turn a smuggled ``Decimal`` or set into a
    string or a list, so outputs are checked before they are dumped, in every mode. A value that
    holds itself is refused; one held in many places costs once."""
    done: set[int] = set()
    on_path: set[int] = set()
    first = _members(value)
    if first is None:
        _check_scalar(value)
        return
    stack: list[tuple[object, Iterator[object]]] = [(value, iter(first))]
    on_path.add(id(value))
    while stack:
        container, items = stack[-1]
        item = next(items, _END)
        if item is _END:
            stack.pop()
            on_path.discard(id(container))
            done.add(id(container))
            continue
        if type(item) in _SCALARS:
            _check_scalar(item)
            continue
        # A container is looked at once, wherever else it is held: done and on_path hold the
        # ids of containers the output holds, which stay alive while it is walked.
        if id(item) in done:
            continue
        if id(item) in on_path:
            raise OutputError("An output holds itself, which JSON cannot (SPEC §8.2)")
        inside = _members(item)
        if inside is None:
            _check_scalar(item)
        else:
            on_path.add(id(item))
            stack.append((item, iter(inside)))


_END = object()


@cache
def _absent_when_null(model: type[BaseModel]) -> frozenset[str]:
    """The names and aliases of a model's optional members that are not computed."""
    return frozenset(
        key
        for name, info in model.model_fields.items()
        if not info.is_required() and COMPUTED not in info.metadata
        for key in (name, info.alias)
        if key
    )


_DUMPING: ContextVar[bool] = ContextVar("aibi_output_dumping", default=False)
"""Set while an output is dumped, so that only the outermost output checks the whole."""


class Output(BaseModel):
    """Base for server outputs: immutable and closed.

    An optional member is omitted when absent, never written as ``null``, and given as ``None`` it
    is absent; a required member may still be ``null`` where the contract says so (§8.2), and so
    may an optional computed member that is given (a suppressed breakdown).
    """

    # Non-finite numbers stay numbers when an output is dumped, so that none can silently
    # become null, as Pydantic's default would make it; the check before the dump refuses them.
    # Nested outputs are validated again inside the output that holds them, so that none
    # bypasses its checks.
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        strict=True,
        ser_json_inf_nan="constants",
        revalidate_instances="always",
    )

    def model_copy(self, *, update: Mapping[str, Any] | None = None, deep: bool = False) -> Self:
        """A copy; with ``update``, a new output validated like any other, so its checks hold."""
        if not update:
            return super().model_copy(deep=deep)
        given = {name: getattr(self, name) for name in self.model_fields_set}
        return self.model_validate({**given, **update})

    @model_validator(mode="before")
    @classmethod
    def _null_is_absent(cls, data: object) -> object:
        """An optional member given as ``None`` is absent, as its dump writes it; computed
        members keep their ``null``, which means not estimable."""
        if not isinstance(data, dict):
            return data
        members = cast(dict[str, object], data)
        absent = _absent_when_null(cls)
        if not any(members.get(name, 0) is None for name in absent):
            return members
        return {
            key: value for key, value in members.items() if not (value is None and key in absent)
        }

    @field_validator("*", mode="after")
    @classmethod
    def _text(cls, value: object) -> object:
        if isinstance(value, str) and not is_text(value):
            raise PydanticCustomError(
                "invalid_text", "Text must be Unicode: no lone surrogates or noncharacters"
            )
        return value

    # No return annotation: Pydantic would take it as the serialised type, and the output's
    # serialisation schema would be an untyped object instead of the model's.
    @model_serializer(mode="wrap")
    def _omit_absent(self, handler: SerializerFunctionWrapHandler):
        outermost = not _DUMPING.get()
        token = _DUMPING.set(True) if outermost else None
        try:
            if outermost:
                check_values(self)
            serialised: dict[str, Any] = handler(self)
        finally:
            if token is not None:
                _DUMPING.reset(token)
        for name, info in type(self).model_fields.items():
            key = info.serialization_alias or info.alias or name
            if info.is_required() or serialised.get(key, 0) is not None:
                continue
            # An optional member's null is absent, unless it is a computed member given as null.
            if COMPUTED not in info.metadata or name not in self.model_fields_set:
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


class Data(Output):
    """Text from data or a document outside segments: labels, values, names, notes (SPEC §8.1)."""

    data: Annotated[str, Field(max_length=MAX_TEXT, json_schema_extra=DATA_MARK)]


def _safe_float(value: float) -> float:
    if abs(value) > MAX_SAFE_INTEGER:
        raise PydanticCustomError(
            "number_out_of_range",
            "Outputs hold no numbers beyond ±(2^53 - 1): such a value is refused, or carried in "
            "other units (SPEC §8.2)",
        )
    return value


Finite = Annotated[
    float,
    AllowInfNan(False),
    AfterValidator(_safe_float),
    Field(json_schema_extra={"minimum": -MAX_SAFE_INTEGER, "maximum": MAX_SAFE_INTEGER}),
]
"""A finite number within ±(2^53 - 1), as JSON text carries it unchanged (SPEC §8.2)."""

Count = Annotated[StrictInt, Field(ge=0, le=MAX_SAFE_INTEGER)]
"""A count of units or rows."""


def _finite_json(value: JsonValue) -> JsonValue:
    """The value as JSON reads it back: finite numbers, safe integers and Unicode text only."""
    try:
        return json_value(value)
    except JsonError as error:
        # The message names no key: keys come from data (A6).
        raise PydanticCustomError(
            "output_json",
            "The value holds a number that is not finite or lies beyond ±(2^53 - 1), text that "
            "is not Unicode, or nesting deeper than 64 (SPEC §8.2)",
            {"code": error.code},
        ) from None


_OUTPUT_JSON: dict[str, JsonValue] = {OUTPUT_JSON_MARK: True}

JsonObject = dict[str, JsonValue]
FiniteJsonObject = Annotated[
    JsonObject,
    AfterValidator(_finite_json),
    WithJsonSchema({"type": "object", "additionalProperties": _OUTPUT_JSON}),
]
"""A JSON object with only finite numbers, safe integers and Unicode text."""


class Computed:
    """Marks a member computed from data: ``null`` there means *not estimable* (SPEC §8.2).

    Used as ``Annotated[X | None, COMPUTED]``. A required computed member is always written, and
    an optional one is written whenever it is given, so a ``null`` is never mistaken for an
    absent member. The schema marks such members ``x-aibi-computed``.
    """

    def __repr__(self) -> str:
        return "COMPUTED"

    def __get_pydantic_json_schema__(
        self, schema: CoreSchema, handler: GetJsonSchemaHandler
    ) -> JsonSchemaValue:
        marked = handler(schema)
        marked[COMPUTED_MARK] = True
        return marked


COMPUTED_MARK = "x-aibi-computed"
"""Marks a computed member in a schema: its ``null`` is kept, with a reason (SPEC §8.2)."""


COMPUTED = Computed()

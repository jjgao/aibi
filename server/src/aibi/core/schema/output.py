"""The base of every server output, segments and the data mark (SPEC §8.1).

Messages and readbacks are segments: text the server wrote, and data tokens holding text that
came from data or a document, which clients render as plain text and never treat as
instructions (A6).

Outputs hold only what JSON text carries unchanged (§8.2): finite numbers within ±(2^53 − 1),
and Unicode text. Members are checked when an output is built, nested outputs included, and
again by ``model_copy`` with ``update``. ``model_construct`` bypasses validation, as Pydantic
documents; what it builds is still checked before it is dumped, where anything but a JSON value
(objects with string keys, arrays, strings, booleans, ``null`` and such numbers) is refused.

Server text is closed at its type (D399): a ``TextSegment`` is made by ``text()``, passes as an
exact instance, or is rebuilt at a listed ``Boundary``; it has no subclass and no unchecked
construction, and every other way in, a mapping included, is refused by its own validator, so
an output that holds one anywhere refuses it; its lists are mutable, so the outermost dump
validates the whole again. A static rule (``tests/core/closure_rule.py``) bans the reflective
routes that skip validators, against mistakes and data, not hostile code in the tree.
"""

import math
from collections.abc import Iterable, Iterator, Mapping
from contextvars import ContextVar
from enum import Enum
from functools import cache
from typing import Annotated, Any, Literal, Protocol, Self, cast, final

from pydantic import (
    AfterValidator,
    AllowInfNan,
    BaseModel,
    ConfigDict,
    Discriminator,
    Field,
    GetJsonSchemaHandler,
    JsonValue,
    ModelWrapValidatorHandler,
    SerializerFunctionWrapHandler,
    Strict,
    StrictInt,
    Tag,
    ValidationError,
    ValidationInfo,
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


def _is(value: object, kind: type) -> bool:
    """Whether ``value``'s type is ``kind`` or a subclass of it, read from the type's method
    resolution order: ``isinstance`` would read the value's ``__class__`` and call a metaclass's
    ``__instancecheck__``, both of which a value's own code may define (D400)."""
    return type.__subclasscheck__(kind, type(value))


class OutputError(ValueError):
    """An output holds a value that JSON text cannot carry unchanged (SPEC §8.2)."""


def _enum_data(member: Enum) -> object:
    """What a dump writes for an enumeration member: the data of its ``str``, ``int`` or
    ``float`` mixin, which must be its value. A member of a plain ``Enum`` is refused: Pydantic
    writes it by value in one place and as itself in another, and two keys can become one."""
    written: object
    if _is(member, str):
        written = str.__str__(cast(str, member))
    elif _is(member, int):
        written = int.__index__(cast(int, member))
    elif _is(member, float):
        written = float.__float__(cast(float, member))
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
    elif _is(value, Enum):
        _check_scalar(_enum_data(cast(Enum, value)))
    elif value is not None and kind is not bool:
        raise OutputError("Outputs hold only JSON values (SPEC §8.2)")


def _check_keys(members: dict[object, object]) -> None:
    """Keys are Unicode text, or members of ``str`` enumerations whose data is, and no two keys
    are written as the same text."""
    written_keys: set[str] = set()
    for key in members:
        written = _enum_data(cast(Enum, key)) if _is(key, Enum) else key
        if type(written) is not str or not is_text(written):
            raise OutputError("Outputs have Unicode text keys only (SPEC §8.2)")
        if written in written_keys:
            raise OutputError("Outputs have no two keys written as the same text (SPEC §8.2)")
        written_keys.add(written)


_SCALARS: tuple[type, ...] = (str, int, float, bool, type(None))
"""The types of scalars that need no look at their members."""


def _members(value: object) -> Iterable[object] | None:
    """What a container holds: an output's fields, or the items of a dict, list or tuple of
    Python's own types; ``None`` for a scalar. Another model, or a subclass of a container, could
    dump otherwise than its members say, so it is refused."""
    kind = type(value)
    if _is(value, Output):
        output = cast(Output, value)
        return [*output.__dict__.values(), *(output.__pydantic_extra__ or {}).values()]
    if _is(value, BaseModel):
        raise OutputError("Outputs hold only outputs and JSON values (SPEC §8.2)")
    if kind is dict:
        members = cast(dict[object, object], value)
        _check_keys(members)
        return members.values()
    if kind is list or kind is tuple:
        return cast(list[object] | tuple[object, ...], value)
    if _is(value, dict) or _is(value, list) or _is(value, tuple):
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
        kind = type(item)
        if any(kind is scalar for scalar in _SCALARS):
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
_VALIDATED: ContextVar[bool] = ContextVar("aibi_output_validated", default=False)
"""Set while a validator dumps the output it is validating (``dumped_in_validation``)."""


_V1 = "Pydantic 1's API is closed on an output: use model_validate or model_copy (D399)"


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
        # An error's string holds the input it refused, which is data and may be what a guard
        # refused: a log of a failure is not a place for it (A6, D399).
        hide_input_in_errors=True,
    )

    # Pydantic 1's API, which builds or copies without the checks of an output, or reaches its
    # internals, is closed at the type (D399); the validating entries, ``model_validate`` and
    # ``model_copy``, are the ways to make or copy one. Its dumps (``dict``, ``json``, ``schema``)
    # only read.
    def copy(self, *args: Any, **kwargs: Any) -> Self:  # pyright: ignore[reportIncompatibleMethodOverride]
        raise TypeError(_V1)

    def _copy_and_set_values(self, *args: Any, **kwargs: Any) -> Self:  # pyright: ignore[reportIncompatibleMethodOverride]
        raise TypeError(_V1)

    def _iter(self, *args: Any, **kwargs: Any) -> Any:  # pyright: ignore[reportIncompatibleMethodOverride]
        raise TypeError(_V1)

    def _calculate_keys(self, *args: Any, **kwargs: Any) -> Any:  # pyright: ignore[reportIncompatibleMethodOverride]
        raise TypeError(_V1)

    @classmethod
    def _get_value(cls, *args: Any, **kwargs: Any) -> Any:  # pyright: ignore[reportIncompatibleMethodOverride]
        raise TypeError(_V1)

    @classmethod
    def construct(cls, _fields_set: set[str] | None = None, **values: Any) -> Self:  # pyright: ignore[reportIncompatibleMethodOverride]
        raise TypeError(_V1)

    @classmethod
    def from_orm(cls, obj: Any) -> Self:  # pyright: ignore[reportIncompatibleMethodOverride]
        raise TypeError(_V1)

    @classmethod
    def parse_obj(cls, obj: Any) -> Self:  # pyright: ignore[reportIncompatibleMethodOverride]
        raise TypeError(_V1)

    @classmethod
    def parse_raw(cls, *args: Any, **kwargs: Any) -> Self:  # pyright: ignore[reportIncompatibleMethodOverride]
        raise TypeError(_V1)

    @classmethod
    def parse_file(cls, *args: Any, **kwargs: Any) -> Self:  # pyright: ignore[reportIncompatibleMethodOverride]
        raise TypeError(_V1)

    @classmethod
    def validate(cls, value: Any) -> Self:  # pyright: ignore[reportIncompatibleMethodOverride]
        raise TypeError(_V1)

    @classmethod
    def update_forward_refs(cls, **localns: Any) -> None:  # pyright: ignore[reportIncompatibleMethodOverride]
        raise TypeError(_V1)

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
                # What a dump writes was admitted by its schema, not only when it was built:
                # a list of segments is mutable, and a mapping put in one after construction
                # would be written as server text. The outermost dump validates the whole
                # again, as the instances they are (the guard admits only exact ones).
                if not _VALIDATED.get():
                    type(self).model_validate(self)
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


def dumped_in_validation(output: Output, **options: Any) -> Any:
    """``output.model_dump(**options)`` for a validator that reads the output it is validating:
    its members were just validated, so the dump does not validate them again (D399); the values
    are still checked. It is a capability: the static rule lets only the functions of
    ``DUMPS_IN_VALIDATION`` (``tests/core/closure_rule.py``) call it, and what it dumps is
    served nowhere."""
    token = _VALIDATED.set(True)
    try:
        return output.model_dump(**options)
    finally:
        _VALIDATED.reset(token)


class Boundary(Enum):
    """The listed places where server text is rebuilt from bytes the server's own code wrote
    (D399). Each is one validating call whose validation context is the place's capability
    (``admitted_at``), or the one unpickling of a reader's answer (``unpickled``); there, and only
    there, a segment written as ``{"text": ...}`` is server text again. The list is closed: a
    test holds each member's places, so a new place is a new member, with its reason."""

    REPORT_NOTES = "report_notes"
    """An import report's notes, read back from the blob a build wrote (D231): stored, so a
    report is served as written only under the classification it was written with
    (``REPORT_CLASSIFICATION``)."""
    RESULT_CACHE = "result_cache"
    """A result's digested content, read back from the cache row the server wrote (D374):
    stored, so a row is read back only under the wording it was written with
    (``CACHE_WORDING``)."""
    READER_ANSWER = "reader_answer"
    """A reader child's answer (D225): the child runs the server's own code under the same
    guard and is not a privilege boundary; it validates the refusals it sends, and the server
    validates them again, and the reasons of skipped relations, as it reads them."""
    CLI_CLIENT = "cli_client"
    """The operator CLI's reading of the answers of the server it is configured with, which it
    trusts as it trusts it with the curator token (D268); the server sends only validated
    outputs."""


STORED = frozenset({Boundary.REPORT_NOTES, Boundary.RESULT_CACHE})
"""The boundaries that read what was stored, which record what text meant when they wrote it."""

REPORT_CLASSIFICATION = 1
"""What counts as server text (D397, D399): an import report written under another
classification is read with each note's fixed text. Raised only when that changes (M4.0f-A2b's
conversions), never for a change of wording."""

CACHE_WORDING = 5
"""The server's wording: a cached result written under another wording is not read back, and
the call computes it again (D374, D399). Raised with any change to a ``text()`` template (a test
holds their digest) and with every change of ``REPORT_CLASSIFICATION``."""

GUARD_MESSAGE = (
    "Server text is made only by text(), as an exact TextSegment, or at a listed boundary (D399)"
)
"""The message of the guard's refusal, which names nothing it was given."""


@final
class _Admitted:
    """The capability of a listed boundary, or of ``text()``: the validation context of one
    call, which Pydantic gives that call's validators and nothing else (D399)."""

    __slots__ = ("member",)

    def __init__(self, member: Boundary | None) -> None:
        self.member = member

    def __repr__(self) -> str:
        return "<admitted>"


_BY_TEXT = _Admitted(None)
_ADMITTED = {member: _Admitted(member) for member in Boundary}
_UNPICKLING: ContextVar[Boundary | None] = ContextVar("aibi_unpickling", default=None)
"""Set while ``unpickled`` reads a reader's answer: a segment is restored only then."""


def admitted_at(member: Boundary) -> object:
    """The validation context of ``member``'s one call: ``Model.model_validate_json(payload,
    context=admitted_at(Boundary.X))``, written as a statement of its own at the place the member
    lists (D399)."""
    return _ADMITTED[member]


class _Loads(Protocol):
    def load(self) -> Any: ...


def unpickled(member: Boundary, unpickler: _Loads) -> object:
    """What ``unpickler`` loads, its segments restored as they were sent, unchecked (``member``
    is ``READER_ANSWER``, D225): whoever reads them validates them again, as the instances they
    are (``databases._reason``)."""
    token = _UNPICKLING.set(member)
    try:
        return unpickler.load()
    finally:
        _UNPICKLING.reset(token)


@final
class TextSegment(Output):
    # Server text: made only by text(), re-validated as an exact instance, or rebuilt at a listed
    # boundary (Boundary); a mapping, another model, or any object with a text member is refused
    # anywhere else, by every entry Pydantic has (D397, D399). No docstring: it would be the
    # schema's description.
    text: str

    def __init_subclass__(cls, **kwargs: Any) -> None:
        raise TypeError("TextSegment has no subclasses (D399)")

    @classmethod
    def model_construct(cls, _fields_set: set[str] | None = None, **values: Any) -> Self:
        raise TypeError("A TextSegment is never built unchecked (D399)")

    def __setstate__(self, state: dict[Any, Any]) -> None:
        if _UNPICKLING.get() is None:
            raise TypeError("A TextSegment is unpickled only at a listed boundary (D399)")
        super().__setstate__(state)

    @model_validator(mode="wrap")
    @classmethod
    def _made_here(
        cls, value: object, handler: ModelWrapValidatorHandler[Self], info: ValidationInfo
    ) -> Self:
        if type(value) is TextSegment or type(info.context) is _Admitted:
            return handler(value)
        raise PydanticCustomError("server_text", GUARD_MESSAGE)


class DataSegment(Output):
    data: Annotated[str, Field(max_length=DATA_TOKEN_MAX, json_schema_extra=DATA_MARK)]
    truncated: Literal[True] | None = None


def _segment_tag(value: object) -> str:
    """Which arm of ``Segment`` takes ``value``, by what it is, so that a data token is not
    tried against ``TextSegment``'s guard first: a data segment, a mapping with no ``text`` key
    and an object with no ``text`` attribute (``from_attributes``) are data; anything else is
    text, whose guard admits an exact instance and refuses the rest with its own message (D399)."""
    if isinstance(value, DataSegment):
        return "data"
    if isinstance(value, Mapping):
        return "text" if "text" in cast(Mapping[object, object], value) else "data"
    if isinstance(value, TextSegment):
        return "text"
    return "text" if hasattr(value, "text") else "data"


class _AnyOf:
    """Writes the union as ``anyOf``, as it was written before it was tagged: the schemas the
    clients read are the same."""

    def __get_pydantic_json_schema__(
        self, schema: CoreSchema, handler: GetJsonSchemaHandler
    ) -> JsonSchemaValue:
        written = handler(schema)
        if "oneOf" in written:
            written["anyOf"] = written.pop("oneOf")
        return written


Segment = Annotated[
    Annotated[TextSegment, Tag("text")] | Annotated[DataSegment, Tag("data")],
    Discriminator(_segment_tag),
    _AnyOf(),
]


def text(value: str) -> TextSegment:
    """Server text: ``value`` must be the server's own words (D397)."""
    return TextSegment.model_validate({"text": value}, context=_BY_TEXT)


def data(value: str) -> DataSegment:
    """A data token, cut to the maximum length and marked when cut. It is total over ``str``
    (D398): a lone surrogate or a noncharacter, which no output may hold, is written as its
    ``\\u``/``\\U`` escape, and a cut never ends inside an escape; on Unicode text it is the
    plain token, unchanged. The escapes are for display only and never parsed back: a backslash
    is not escaped, so the text ``a\\udcff`` and the lone surrogate show alike. Anything but a
    ``str`` is a ``TypeError``: a type confusion stays loud, whatever the model would coerce."""
    if not isinstance(value, str):  # pyright: ignore[reportUnnecessaryIsInstance]
        raise TypeError(f"a data token is made of a str, not {type(value).__name__}")
    try:
        if len(value) > DATA_TOKEN_MAX:
            return DataSegment(data=value[:DATA_TOKEN_MAX], truncated=True)
        return DataSegment(data=value)
    except ValidationError:
        return _shown(value)


def _escape(character: str) -> str:
    """A code point as its escape: ``\\u`` and four hex digits, or ``\\U`` and eight."""
    code = ord(character)
    return f"\\u{code:04x}" if code <= 0xFFFF else f"\\U{code:08x}"


def _shown(name: str) -> DataSegment:
    """``name`` as a data token with each lone surrogate and noncharacter written as its escape,
    cut only between escapes, never inside one: what ``data`` gives for a string that is not
    Unicode text (A6, D397, D398)."""
    kept: list[str] = []
    size = 0
    for character in name:
        piece = character if is_text(character) else _escape(character)
        if size + len(piece) > DATA_TOKEN_MAX:
            return DataSegment(data="".join(kept), truncated=True)
        kept.append(piece)
        size += len(piece)
    return DataSegment(data="".join(kept))


shown = data
"""``data``, by the name A1 gave it for a name, a type string or a time zone that a source gives
(D397); the two are one function since ``data`` is total (D398)."""

Message = tuple[Segment, ...]
"""What a carrier holds of a message on its way to a refusal or a note (a returned problem, an
exception, a record): the server's text and data tokens, never one string to be wrapped later
(D397)."""


def plain_text(message: Iterable[Segment]) -> str:
    """A message's text and data joined, for an exception's own message or a log; never made
    server text (D397)."""
    return "".join(
        segment.text if isinstance(segment, TextSegment) else segment.data for segment in message
    )


def listed(values: Iterable[str], separator: str = ", ") -> list[Segment]:
    """``values`` as data tokens, one for each, between server text ``separator`` (D296, D397)."""
    found: list[Segment] = []
    for value in values:
        if found:
            found.append(text(separator))
        found.append(data(value))
    return found


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

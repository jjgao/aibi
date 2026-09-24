"""Descriptors (SPEC §5, §9.1).

Every descriptor has the same envelope and kind-specific ``fields``. A member that is absent is
undeclared. ``null`` is a value only where the spec declares one (``primary_key``, a dataset's
``min_cell_count``, a mapping's ``transform``, an analysis's ``cross_dataset`` and a curation
entry's ``inferred``); the values the core leaves to JSON (``inferred``, extension members, an
analysis's parameter and return schemas) may hold ``null`` inside. Models serialise exactly the
members they were given, so a descriptor round-trips, through JSON text too: strings are Unicode
text, numbers are finite and within ±(2^53 - 1), and an integral float is kept as the integer
JSON reads back.

Descriptors in a release (dataset, table, column, relationship, coverage, endpoint) carry one
curation entry for each field that has a value (``/label``, ``/definition``, ``/fields/<name>``,
``/extensions/<pack>/<name>``) and no other. Concepts, analyses and model cards are defined by
code or configuration and carry none.

Checks report each problem at the member it concerns, and messages never repeat text from the
descriptor (A6): a value a message refers to travels in the error's ``shown`` context, and
refusals show it as a data token.
"""

import math
import re
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Annotated, Any, ClassVar, Literal, LiteralString, Self, cast

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Discriminator,
    Field,
    JsonValue,
    PlainValidator,
    SerializerFunctionWrapHandler,
    StrictBool,
    StrictInt,
    Tag,
    ValidationInfo,
    WithJsonSchema,
    field_validator,
    model_serializer,
    model_validator,
)
from pydantic.json_schema import SkipJsonSchema
from pydantic_core import PydanticCustomError

from aibi.core.schema.checks import walk
from aibi.core.schema.document import (
    Clause,
    ClauseModel,
    CohortLeaf,
    CoveredLeaf,
    ExistsLeaf,
    IdsLeaf,
    PackKey,
    PackLeaf,
    Units,
    ValueLeaf,
)
from aibi.core.schema.errors import Location, Problems, problem
from aibi.core.schema.ids import (
    DATASET_DESCRIPTOR_ID,
    IDENT,
    JSON_POINTER_RE,
    MAX_SAFE_INTEGER,
    AnalysisId,
    AnyCode,
    ColumnId,
    ColumnRef,
    ConceptId,
    CoverageId,
    EndpointId,
    Identifier,
    ModelCardId,
    PackName,
    RelationshipId,
    Sha256,
    TableId,
    identifier_parts,
    no_double_underscore,
)
from aibi.core.schema.jsonio import JsonError, escape_token, is_text, json_value
from aibi.core.schema.limits import (
    ENTRIES,
    KEY_COLUMNS,
    LIST_MEMBERS,
    MAX_COLUMNS,
    MAX_ENTRIES,
    MAX_LIST,
    MAX_NAME,
    MAX_PACKS,
    MAX_STRING,
    MAX_TEXT,
    NAME_CHARACTERS,
    PACKS,
    STRING_CHARACTERS,
    TEXT_CHARACTERS,
    LimitName,
    map_cap,
)
from aibi.core.schema.output import DATA_MARK

# --- Strings, numbers and JSON values --------------------------------------------------------


def _unicode(value: str) -> str:
    if not is_text(value):
        raise problem("invalid_text", "Text must be Unicode: no lone surrogates or noncharacters")
    return value


_UNICODE = AfterValidator(_unicode)

Text = Annotated[
    str,
    Field(min_length=1, max_length=MAX_TEXT, json_schema_extra=DATA_MARK),
    LimitName(TEXT_CHARACTERS),
    _UNICODE,
]
"""Plain text, never interpreted (A6); empty text is no value, so it is omitted instead."""
String = Annotated[
    str,
    Field(max_length=MAX_STRING, json_schema_extra=DATA_MARK),
    LimitName(STRING_CHARACTERS),
    _UNICODE,
]
"""A short string, from data or curation."""
Label = Annotated[
    str,
    Field(min_length=1, max_length=MAX_STRING, json_schema_extra=DATA_MARK),
    LimitName(STRING_CHARACTERS),
    _UNICODE,
]
"""A short string that is not empty."""
NameText = Annotated[
    str,
    Field(min_length=1, max_length=MAX_NAME, json_schema_extra=DATA_MARK),
    LimitName(NAME_CHARACTERS),
    _UNICODE,
]
"""A name: of an ontology system, a library, a licence, a pipeline, an encoding or a model."""


def _one_character(value: str) -> str:
    if len(value) != 1:
        raise problem("one_character", "Give exactly one character")
    return value


Character = Annotated[
    str,
    AfterValidator(_one_character),
    _UNICODE,
    WithJsonSchema({"type": "string", "minLength": 1, "maxLength": 1}),
]

PositiveInt = Annotated[StrictInt, Field(ge=1, le=MAX_SAFE_INTEGER)]
Count = Annotated[StrictInt, Field(ge=0, le=MAX_SAFE_INTEGER)]
Version = PositiveInt
"""A release descriptor's version: +1 whenever its fields change (§5.1)."""


def _number(value: object) -> int | float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise problem("number_type", "Expected a number")
    if isinstance(value, float) and not math.isfinite(value):
        raise problem("non_finite_number", "The number is not finite")
    if abs(value) > MAX_SAFE_INTEGER:
        raise problem("integer_out_of_range", "Numbers beyond ±(2^53 - 1) are refused")
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


Number = Annotated[
    int | float,
    PlainValidator(_number),
    WithJsonSchema({"type": "number", "minimum": -MAX_SAFE_INTEGER, "maximum": MAX_SAFE_INTEGER}),
]
"""A finite number within ±(2^53 - 1); an integral float is the integer JSON reads back."""

_JSON_PROBLEMS: dict[str, tuple[LiteralString, LiteralString]] = {
    "NON_FINITE_NUMBER": ("non_finite_number", "The number is not finite"),
    "INTEGER_OUT_OF_RANGE": ("integer_out_of_range", "Numbers beyond ±(2^53 - 1) are refused"),
    "INVALID_VALUE": ("invalid_text", "Strings and keys must be Unicode text"),
    "WRONG_TYPE": ("json_type", "Not a JSON value"),
    "LIMIT_EXCEEDED": ("recursion_loop", "Arrays and objects are nested too deep"),
}


def _tokens_of(pointer: str) -> Location:
    return tuple(token.replace("~1", "/").replace("~0", "~") for token in pointer.split("/")[1:])


def _json(value: JsonValue) -> JsonValue:
    try:
        return json_value(value)
    except JsonError as error:
        found = Problems("JsonValue")
        found.add(_tokens_of(error.pointer or ""), problem(*_JSON_PROBLEMS[error.code]))
        raise found.error() from None


DESCRIPTOR_JSON_MARK = "x-aibi-descriptor-json"
"""Marks a JSON value in the schema; the export replaces it with a definition whose numbers are
bounded as the models bound them."""

AnyJson = Annotated[
    JsonValue, AfterValidator(_json), WithJsonSchema({DESCRIPTOR_JSON_MARK: True, **DATA_MARK})
]
"""Any JSON value, ``null`` included, that JSON text carries unchanged."""


def _member_value(value: JsonValue) -> JsonValue:
    if value is None:
        raise problem(
            "null_not_allowed",
            "null is not allowed here: give a value, or omit the member if it is optional",
        )
    return _json(value)


ExtensionValue = Annotated[
    JsonValue,
    AfterValidator(_member_value),
    WithJsonSchema({DESCRIPTOR_JSON_MARK: True, **DATA_MARK, "not": {"type": "null"}}),
]
"""A member of a pack's extension object; its schema is the pack's (SPEC §10.1)."""


def _json_key(value: str | bool | int | float) -> tuple[str, str | bool | float]:
    """Values compared as JSON compares them: 1 and 1.0 are the same number, and not ``true``."""
    if isinstance(value, bool):
        return ("boolean", value)
    if isinstance(value, int | float):
        return ("number", float(value))
    return ("string", value)


def _repeats(
    values: Iterable[str | bool | int | float], at: Location, message: LiteralString
) -> list[tuple[Location, PydanticCustomError]]:
    """A problem at each value that repeats an earlier one, compared as JSON values."""
    seen: set[tuple[str, str | bool | float]] = set()
    found: list[tuple[Location, PydanticCustomError]] = []
    for index, value in enumerate(values):
        key = _json_key(value)
        if key in seen:
            found.append(((*at, index), problem("duplicate_entry", message, shown=value)))
        seen.add(key)
    return found


def _chosen(value: object, member: str) -> object:
    if isinstance(value, dict):
        return cast(dict[str, object], value).get(member)
    return getattr(value, member, None) if isinstance(value, BaseModel) else None


def _chooser(member: str, choices: Sequence[str]) -> Any:
    """A discriminator choosing a union member by the value of ``member``, or ``bad:<member>``."""

    def choose(value: object) -> str:
        chosen = _chosen(value, member)
        return chosen if isinstance(chosen, str) and chosen in choices else f"bad:{member}"

    return Discriminator(choose)


def _refuser(member: str, choices: Sequence[str]) -> Any:
    """The union member that refuses a value whose ``member`` chooses no other, at ``member``."""

    def refuse(value: object) -> object:
        if not isinstance(value, dict | BaseModel):
            raise problem("object_type", "Expected an object")
        found = Problems("Choice")
        chosen = _chosen(cast(object, value), member)
        if isinstance(value, dict) and member not in cast(dict[str, object], value):
            error = problem("required_member", "Missing member {member}", member=member)
        elif chosen is None:
            error = problem(
                "null_not_allowed",
                "null is not allowed here: give a value, or omit the member if it is optional",
            )
        elif not isinstance(chosen, str):
            error = problem("choice_type", "{member} is a string", member=member)
        else:
            error = problem(
                "unknown_kind" if member == "kind" else "unknown_choice",
                "Unknown {member} ",
                member=member,
                shown=chosen,
                alternatives=list(choices),
            )
        found.add((member,), error)
        raise found.error()

    return PlainValidator(refuse)


class DescModel(BaseModel):
    """Base for descriptor parts: strict, closed, immutable; dumps only the members given.

    ``_nullable`` lists the members for which ``null`` is a declared value; ``null`` is refused
    for every other member, at that member.
    """

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True, serialize_by_alias=True)
    _nullable: ClassVar[frozenset[str]] = frozenset()
    _reserved: ClassVar[Mapping[str, LiteralString]] = {}

    @classmethod
    def nullable(cls) -> frozenset[str]:
        """The members for which ``null`` is a declared value."""
        return cls._nullable

    @classmethod
    def reserved(cls) -> Mapping[str, LiteralString]:
        """Members reserved for later versions, refused as unknown with this explanation."""
        return cls._reserved

    @field_validator("*", mode="before")
    @classmethod
    def _refuse_null(cls, value: object, info: ValidationInfo) -> object:
        if value is None and info.field_name not in cls._nullable:
            raise problem(
                "null_not_allowed",
                "null is not allowed here: give a value, or omit the member if it is optional",
            )
        return value

    # No return annotation: Pydantic would take it as the serialised type, and the descriptor's
    # serialisation schema would be an untyped object instead of the model's.
    @model_serializer(mode="wrap")
    def _dump_given(self, handler: SerializerFunctionWrapHandler):
        dumped: dict[str, Any] = handler(self)
        given: set[str] = set()
        nullable: set[str] = set()
        for name, info in type(self).model_fields.items():
            keys = {key for key in (name, info.alias, info.serialization_alias) if key}
            if name in self.model_fields_set or info.is_required():
                given.update(keys)
            if name in type(self)._nullable:
                nullable.update(keys)
        return {
            key: value
            for key, value in dumped.items()
            if key in given and (value is not None or key in nullable)
        }


# --- Timestamps, dates and curation (§5.1) ---------------------------------------------------

_DATE = (
    r"(?:[0-9]{3}[1-9]|[0-9]{2}[1-9][0-9]|[0-9][1-9][0-9]{2}|[1-9][0-9]{3})"
    r"-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12][0-9]|3[01])"
)
_TIME = r"(?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9](?:\.[0-9]{1,9})?"
_OFFSET = r"(?:Z|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])"
_TIMESTAMP = rf"^{_DATE}T{_TIME}{_OFFSET}$"
_DAY = rf"^{_DATE}$"


def instant(value: str) -> datetime | None:
    """The instant an RFC 3339 timestamp with an explicit offset names, or ``None``."""
    if re.fullmatch(_TIMESTAMP, value) is None:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:  # a day the month does not have
        return None


_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def instant_ns(value: str) -> int | None:
    """The instant a timestamp names, in nanoseconds since 1970 in UTC, or ``None``.

    Exact to the nine digits of fraction a timestamp may have, which ``datetime`` cuts to six.
    """
    named = instant(value)
    if named is None:
        return None
    seconds = (named.replace(microsecond=0) - _EPOCH) // timedelta(seconds=1)
    _, dot, rest = value.partition(".")
    digits = rest[: len(rest) - len(rest.lstrip("0123456789"))] if dot else ""
    return seconds * 10**9 + int(digits.ljust(9, "0"))


def day(value: str) -> date | None:
    """The date a ``YYYY-MM-DD`` string names, or ``None``."""
    if re.fullmatch(_DAY, value) is None:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _timestamp(value: str) -> str:
    if instant(value) is None:
        raise problem(
            "timestamp",
            "Expected an RFC 3339 date and time with an explicit offset, such as "
            "2026-09-24T06:00:00Z; leap seconds are not accepted",
        )
    return value


Timestamp = Annotated[
    str, AfterValidator(_timestamp), WithJsonSchema({"type": "string", "pattern": _TIMESTAMP})
]
"""An RFC 3339 timestamp with an explicit offset; seconds run from 00 to 59."""
SemVer = Annotated[
    str,
    Field(pattern=r"^(?:0|[1-9][0-9]{0,15})\.(?:0|[1-9][0-9]{0,15})\.(?:0|[1-9][0-9]{0,15})$"),
]
"""``X.Y.Z``, each part at most 16 digits, without pre-release or build parts."""

_NAME_TEXT = rf"[^\x00-\x1f\x7f-\x9f\u2028\u2029]{{1,{MAX_NAME}}}"
_TOOL = rf"[A-Za-z0-9_.-]{{1,{MAX_NAME}}}@[A-Za-z0-9_.+-]{{1,{MAX_NAME}}}"
_BY = rf"^(?:(?:operator|agent):{_NAME_TEXT}|model:{IDENT}|importer:{_TOOL})$"
_BY_RE = re.compile(_BY)


def _by(value: str) -> str:
    kind, _, name = value.partition(":")
    if kind == "model":
        identifier_parts(name)
        no_double_underscore(name)
    parts = name.split("@", 1) if kind == "importer" else [name]
    if kind in ("operator", "agent", "importer") and any(len(part) > MAX_NAME for part in parts):
        raise problem(
            "string_too_long",
            "A name has at most {max_length} characters",
            max_length=MAX_NAME,
            limit=NAME_CHARACTERS,
        )
    if _BY_RE.fullmatch(value) is None:
        raise problem(
            "curation_by",
            'Expected "operator:<name>", "agent:<name>", "model:<id>" or '
            '"importer:<name>@<version>", without control characters or line breaks',
        )
    return _unicode(value)


By = Annotated[
    str, AfterValidator(_by), WithJsonSchema({"type": "string", "pattern": _BY, **DATA_MARK})
]
"""Who set a status: always the server, from the request's credentials (§11.1)."""

Status = Literal["asserted", "proposed", "imported", "imported_default", "undeclared"]
SET_BY: Mapping[str, tuple[str, ...]] = {
    "asserted": ("operator",),
    "imported": ("importer",),
    "imported_default": ("importer",),
    "proposed": ("model", "agent", "importer"),
    "undeclared": ("operator", "model", "agent", "importer"),
}
"""Who may set each status (§5.1): only an operator asserts; importers import."""


class CurationStatus(DescModel):
    model_config = ConfigDict(
        json_schema_extra={
            "allOf": [
                {
                    "if": {"properties": {"status": {"const": status}}},
                    "then": {"properties": {"by": {"pattern": f"^(?:{'|'.join(kinds)}):"}}},
                }
                for status, kinds in SET_BY.items()
                if len(kinds) < 4
            ]
        }
    )
    _nullable: ClassVar[frozenset[str]] = frozenset({"inferred"})

    status: Status
    by: By
    at: Timestamp
    evidence: Text | None = None
    inferred: AnyJson = None
    """The importer's own inference for this field, compared on re-import (§12.3)."""

    @model_validator(mode="after")
    def _check_by(self) -> Self:
        if self.by.partition(":")[0] not in SET_BY[self.status]:
            found = Problems("CurationStatus")
            found.add(
                ("by",),
                problem(
                    "conflicting_members",
                    "Only an operator asserts a status, only an importer imports one, and a "
                    "proposal comes from a model, an agent or an importer",
                ),
            )
            found.raise_any()
        return self


# --- Shared parts ----------------------------------------------------------------------------


class OntologyRef(DescModel):
    system: NameText
    code: Label
    label: Label
    relation: Literal["exact", "broader", "narrower", "related"]


OntologyRefs = Annotated[list[OntologyRef], Field(max_length=MAX_LIST), LimitName(LIST_MEMBERS)]


class PermissibleValue(DescModel):
    """A category value; categories are strings, as their constants are (§6.4)."""

    value: String
    label: Label | None = None
    concepts: OntologyRefs | None = None


class PermissibleValues(DescModel):
    values: Annotated[
        list[PermissibleValue], Field(min_length=1, max_length=MAX_LIST), LimitName(LIST_MEMBERS)
    ]
    ordered: StrictBool = False
    """Whether the listed order is meaningful; ``false`` when absent."""

    @model_validator(mode="after")
    def _unique(self) -> Self:
        found = Problems("PermissibleValues")
        found.extend(
            (),
            [
                ((*at, "value"), error)
                for at, error in _repeats(
                    (entry.value for entry in self.values),
                    ("values",),
                    "A permissible value is listed twice: ",
                )
            ],
        )
        found.raise_any()
        return self


ValueMapping = Annotated[
    dict[String, String],
    Field(min_length=1, max_length=MAX_LIST),
    LimitName(LIST_MEMBERS),
    map_cap(MAX_LIST),
]


class UnitTransform(DescModel):
    unit_from: Units
    unit_to: Units


class ValueMapTransform(DescModel):
    value_map: ValueMapping


def _transform_tag(value: object) -> str | None:
    if isinstance(value, dict):
        return "t:value_map" if "value_map" in value else "t:units"
    if isinstance(value, ValueMapTransform):
        return "t:value_map"
    if isinstance(value, UnitTransform):
        return "t:units"
    return None


Transform = Annotated[
    Annotated[UnitTransform, Tag("t:units")] | Annotated[ValueMapTransform, Tag("t:value_map")],
    Discriminator(
        _transform_tag,
        custom_error_type="transform_type",
        custom_error_message='A transform is {"unit_from": ..., "unit_to": ...} or '
        '{"value_map": {...}}',
    ),
]


class ConceptMapping(DescModel):
    """An exact mapping to a concept, with at most a unit conversion or a value map (§5.7)."""

    _nullable: ClassVar[frozenset[str]] = frozenset({"transform"})

    concept: ConceptId
    transform: Transform | None = None
    """``null`` declares that no transform is needed."""


# --- Derived columns (§5.7) ------------------------------------------------------------------


class DateDiff(DescModel):
    op: Literal["date_diff"]
    from_: ColumnId = Field(alias="from")
    to: ColumnId
    units: Units


def _operand_tag(value: object) -> str | None:
    if isinstance(value, str):
        return "o:column"
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return "o:number"
    if isinstance(value, dict | Arith):
        return "o:arith"
    return None


def _two(args: list[Any]) -> list[Any]:
    if len(args) != 2:
        raise problem("arith_args", "arith takes exactly two operands")
    return args


class Arith(DescModel):
    op: Literal["arith"]
    operator: Literal["+", "-", "*", "/"]
    args: Annotated[
        list["Operand"],
        AfterValidator(_two),
        Field(json_schema_extra={"minItems": 2, "maxItems": 2}),
    ]


Operand = Annotated[
    Annotated[ColumnId, Tag("o:column")]
    | Annotated[Number, Tag("o:number")]
    | Annotated[Arith, Tag("o:arith")],
    Discriminator(
        _operand_tag,
        custom_error_type="operand_type",
        custom_error_message="An operand is a column id, a number or an arith object",
    ),
]
Arith.model_rebuild()


class ValueMap(DescModel):
    op: Literal["value_map"]
    input: ColumnId
    map: ValueMapping


class UnitConvert(DescModel):
    op: Literal["unit_convert"]
    input: ColumnId
    units: Units


_OPS = ("date_diff", "arith", "value_map", "unit_convert")
_DerivedUnion = Annotated[
    Annotated[DateDiff, Tag("date_diff")]
    | Annotated[Arith, Tag("arith")]
    | Annotated[ValueMap, Tag("value_map")]
    | Annotated[UnitConvert, Tag("unit_convert")]
    | Annotated[SkipJsonSchema[object], _refuser("op", _OPS), Tag("bad:op")],
    _chooser("op", _OPS),
]
if TYPE_CHECKING:
    Derived = DateDiff | Arith | ValueMap | UnitConvert
else:
    Derived = _DerivedUnion


def derived_inputs(
    derived: DateDiff | Arith | ValueMap | UnitConvert,
) -> list[tuple[Location, str]]:
    """The columns a derivation reads, each with its location in the derivation."""
    if isinstance(derived, DateDiff):
        return [(("from",), derived.from_), (("to",), derived.to)]
    if isinstance(derived, ValueMap | UnitConvert):
        return [(("input",), derived.input)]
    found: list[tuple[Location, str]] = []
    pending: list[tuple[Location, Arith]] = [((), derived)]
    while pending:
        at, arith = pending.pop()
        for index, operand in enumerate(arith.args):
            where = (*at, "args", index)
            if isinstance(operand, str):
                found.append((where, operand))
            elif isinstance(operand, Arith):
                pending.append((where, operand))
    return found


# --- Envelope and curation (§5.1) ------------------------------------------------------------


class Pipeline(DescModel):
    name: NameText
    version: NameText


class Provenance(DescModel):
    source: Text | None = None
    pipeline: Pipeline | None = None
    citation: Annotated[list[Label], Field(max_length=MAX_LIST), LimitName(LIST_MEMBERS)] | None = (
        None
    )


Extensions = Annotated[
    dict[
        PackKey,
        Annotated[
            dict[Identifier, ExtensionValue],
            Field(max_length=MAX_ENTRIES),
            LimitName(ENTRIES),
            map_cap(MAX_ENTRIES),
        ],
    ],
    Field(max_length=MAX_PACKS),
    LimitName(PACKS),
    map_cap(MAX_PACKS),
]
"""Pack fields by pack id, checked against each pack's JSON Schema (SPEC §10.1)."""

CurationPointer = Annotated[
    str,
    Field(pattern=JSON_POINTER_RE.pattern, max_length=MAX_STRING),
    LimitName(STRING_CHARACTERS),
    _UNICODE,
]
Curation = Annotated[
    dict[CurationPointer, CurationStatus],
    Field(max_length=MAX_LIST),
    LimitName(LIST_MEMBERS),
    map_cap(MAX_LIST),
]


def _declared_only(schema: dict[str, Any]) -> None:
    """JSON Schema: a release descriptor's curation entries are never ``undeclared`` (§5.1)."""
    properties = schema["properties"]
    properties["curation"] = {
        "allOf": [
            properties["curation"],
            {"additionalProperties": {"properties": {"status": {"not": {"const": "undeclared"}}}}},
        ]
    }


class Envelope(DescModel):
    """Members every descriptor has (§5.1). Each kind adds ``kind``, ``id``, ``version`` and
    ``fields``."""

    model_config = ConfigDict(json_schema_extra=_declared_only)

    label: Label
    definition: Text | None = None
    provenance: Provenance | None = None
    extensions: Extensions = Field(default_factory=dict[str, dict[str, JsonValue]])
    curation: Curation = Field(default_factory=dict[str, CurationStatus])

    curated: ClassVar[bool] = False
    """Whether the kind lives in a release and so records curation statuses."""

    def curated_pointers(self) -> set[str]:
        """JSON Pointers of the fields that have values (§5.1)."""
        pointers = {"/label"}
        if self.definition is not None:
            pointers.add("/definition")
        fields = cast(DescModel, self.__dict__["fields"])
        for name, info in type(fields).model_fields.items():
            if name in fields.model_fields_set:
                pointers.add("/fields/" + escape_token(info.alias or name))
        for pack, members in self.extensions.items():
            for member in members:
                pointers.add(f"/extensions/{escape_token(pack)}/{escape_token(member)}")
        return pointers

    @model_validator(mode="after")
    def _check_curation(self) -> Self:
        found = Problems(type(self).__name__)
        if not self.curated:
            if self.curation:
                found.add(
                    ("curation",),
                    problem(
                        "curation_not_allowed",
                        "Concepts, analyses and model cards are defined by code or "
                        "configuration and carry no curation",
                    ),
                )
            found.raise_any()
            return self
        expected = self.curated_pointers()
        for pointer in sorted(expected - self.curation.keys()):
            found.add(
                ("curation", pointer),
                problem("curation_missing", "A field with a value needs a curation entry"),
            )
        for pointer, entry in self.curation.items():
            if pointer not in expected:
                found.add(
                    ("curation", pointer),
                    problem(
                        "curation_unexpected",
                        "Only fields with a value have curation entries; this one has none",
                    ),
                )
            elif entry.status == "undeclared":
                found.add(
                    ("curation", pointer, "status"),
                    problem("undeclared_with_value", "A field with a value is not undeclared"),
                )
        found.raise_any()
        return self


def _wrong_id(expected: str) -> Problems:
    found = Problems("Descriptor")
    found.add(("id",), problem("descriptor_id", "The id must be ", shown=expected))
    return found


# --- Dataset (§5.2) --------------------------------------------------------------------------


OriginalName = String
"""A name as the source wrote it, possibly empty: text from data (A6)."""


class DatasetSource(DescModel):
    kind: Literal["files", "database", "pack"]
    location: Label
    commit: NameText | None = None


class Disclosure(DescModel):
    """``min_cell_count`` is ``null`` (off) or at least 2; without row ids it is required (§8.4)."""

    model_config = ConfigDict(
        json_schema_extra={
            "if": {
                "properties": {"allow_row_ids": {"const": False}},
                "required": ["allow_row_ids"],
            },
            "then": {
                "required": ["min_cell_count"],
                "properties": {"min_cell_count": {"type": "integer"}},
            },
        }
    )
    _nullable: ClassVar[frozenset[str]] = frozenset({"min_cell_count"})

    min_cell_count: Annotated[StrictInt, Field(ge=2, le=MAX_SAFE_INTEGER)] | None = None
    allow_row_ids: StrictBool = True

    @model_validator(mode="after")
    def _check(self) -> Self:
        if not self.allow_row_ids and self.min_cell_count is None:
            found = Problems("Disclosure")
            if "min_cell_count" in self.model_fields_set:
                error = problem(
                    "conflicting_members", "Without row ids, min_cell_count is set, not null"
                )
            else:
                error = problem("required_member", "Without row ids, min_cell_count is required")
            found.add(("min_cell_count",), error)
            found.raise_any()
        return self


StringList = Annotated[list[Label], Field(max_length=MAX_LIST), LimitName(LIST_MEMBERS)]


class DatasetFields(DescModel):
    name: Label | None = None
    description: Text | None = None
    domain_tags: StringList | None = None
    citation: StringList | None = None
    references: StringList | None = None
    source: DatasetSource | None = None
    license: NameText | None = None
    data_use: OntologyRefs | None = None
    disclosure: Disclosure | None = None
    packs: (
        Annotated[
            list[PackKey],
            Field(max_length=MAX_PACKS, json_schema_extra={"uniqueItems": True}),
            LimitName(PACKS),
        ]
        | None
    ) = None

    @model_validator(mode="after")
    def _check(self) -> Self:
        found = Problems("DatasetFields")
        found.extend((), _repeats(self.packs or [], ("packs",), "A pack is listed twice: "))
        found.raise_any()
        return self


class DatasetDescriptor(Envelope):
    kind: Literal["dataset"]
    id: Literal["dataset"]
    version: Version
    fields: DatasetFields
    curated: ClassVar[bool] = True


# --- Table (§5.3) ----------------------------------------------------------------------------


class ParseSettings(DescModel):
    """How a text file is read; every member is given. ``header_row`` counts from 0, after the
    ``skip_rows`` skipped, and a ``tsv`` file is delimited by a tab."""

    model_config = ConfigDict(
        json_schema_extra={
            "if": {"properties": {"format": {"const": "tsv"}}},
            "then": {"properties": {"delimiter": {"const": "\t"}}},
        }
    )

    format: Literal["csv", "tsv"]
    delimiter: Character
    quote: Character
    header_row: Count
    skip_rows: Count
    encoding: NameText

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.format == "tsv" and self.delimiter != "\t":
            found = Problems("ParseSettings")
            found.add(
                ("delimiter",),
                problem("conflicting_members", "A tsv file is delimited by a tab"),
            )
            found.raise_any()
        return self


class TableSource(DescModel):
    model_config = ConfigDict(
        json_schema_extra={
            "if": {"required": ["parse"]},
            "then": {"properties": {"kind": {"const": "file"}}},
        }
    )

    kind: Literal["file", "sheet", "database", "pack"]
    name: Label
    original_name: OriginalName
    parse: ParseSettings | None = None
    """For text files only."""

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.parse is not None and self.kind != "file":
            found = Problems("TableSource")
            found.add(
                ("parse",),
                problem("conflicting_members", "Parse settings are for text files only"),
            )
            found.raise_any()
        return self


KeyColumns = Annotated[
    list[ColumnId],
    Field(min_length=1, max_length=MAX_COLUMNS, json_schema_extra={"uniqueItems": True}),
    LimitName(KEY_COLUMNS),
]


class TableFields(DescModel):
    """``observation_window`` is reserved for timeline queries (M7): until then it is refused
    as an unknown member."""

    _nullable: ClassVar[frozenset[str]] = frozenset({"primary_key"})
    _reserved: ClassVar[Mapping[str, LiteralString]] = {
        "observation_window": "is reserved for timeline queries (M7) and must be absent in v1"
    }

    grain: Text | None = None
    role: Literal["entity", "link", "measurement", "event", "coverage"] | None = None
    primary_key: KeyColumns | None = None
    """A list of columns; ``null`` declares that the table has no key; absent is undeclared."""
    maps_to: Annotated[
        ConceptMapping | None,
        Field(json_schema_extra={"properties": {"transform": {"type": "null"}}}),
    ] = None
    """What the rows are instances of: a table concept, without a transform."""
    time_origin: ConceptId | None = None
    source: TableSource | None = None

    @model_validator(mode="after")
    def _check(self) -> Self:
        found = Problems("TableFields")
        found.extend(
            (), _repeats(self.primary_key or [], ("primary_key",), "A key names a column twice: ")
        )
        if self.maps_to is not None and self.maps_to.transform is not None:
            found.add(
                ("maps_to", "transform"),
                problem("conflicting_members", "A table maps to a concept without a transform"),
            )
        found.raise_any()
        return self


class TableDescriptor(Envelope):
    kind: Literal["table"]
    id: Annotated[
        TableId,
        Field(json_schema_extra={"not": {"anyOf": [{"pattern": "__"}, {"const": "dataset"}]}}),
    ]
    version: Version
    fields: TableFields
    curated: ClassVar[bool] = True

    @model_validator(mode="after")
    def _check_id(self) -> Self:
        if self.id == DATASET_DESCRIPTOR_ID:
            found = Problems("TableDescriptor")
            found.add(
                ("id",), problem("descriptor_id", "The id dataset is the dataset descriptor's")
            )
            found.raise_any()
        return self


# --- Column (§5.4) ---------------------------------------------------------------------------

Datatype = Literal[
    "number",
    "integer",
    "string",
    "boolean",
    "category",
    "list<category>",
    "date",
    "datetime",
    "time_offset",
]
_WITH_UNITS = ("number", "integer", "time_offset")
_WITH_RANGE = ("number", "integer", "date", "datetime", "time_offset")
_WITH_VALUES = ("category", "list<category>")
_BOUND_TYPES: dict[str, LiteralString] = {
    "number": "A bound of a number column is a number",
    "integer": "A bound of an integer column is an integer",
    "time_offset": "A bound of a time_offset column is a number",
    "date": "A bound of a date column is a date, YYYY-MM-DD",
    "datetime": "A bound of a datetime column is an RFC 3339 date and time with an offset",
}


def _data_scalar(value: object) -> str | bool | int | float:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        if len(value) > MAX_STRING:
            raise problem(
                "string_too_long",
                "String should have at most {max_length} characters",
                max_length=MAX_STRING,
                limit=STRING_CHARACTERS,
            )
        return _unicode(value)
    if not isinstance(value, int | float):
        raise problem("scalar_type", "Expected a string, a boolean or a number")
    return _number(value)


def _data_bound(value: object) -> str | int | float:
    if isinstance(value, bool):
        raise problem("bound_type", "A range bound is a number or a string")
    checked = _data_scalar(value)
    assert not isinstance(checked, bool)
    return checked


_STRING_VALUE: dict[str, JsonValue] = {"type": "string", "maxLength": MAX_STRING}
_NUMBER_VALUE: dict[str, JsonValue] = {
    "type": "number",
    "minimum": -MAX_SAFE_INTEGER,
    "maximum": MAX_SAFE_INTEGER,
}
DataBound = Annotated[
    str | int | float,
    PlainValidator(_data_bound),
    WithJsonSchema({"anyOf": [_STRING_VALUE, _NUMBER_VALUE], **DATA_MARK}),
]
DataScalar = Annotated[
    str | bool | int | float,
    PlainValidator(_data_scalar),
    WithJsonSchema({"anyOf": [_STRING_VALUE, {"type": "boolean"}, _NUMBER_VALUE], **DATA_MARK}),
]
"""A value from data: a status code or a range bound (A6)."""


class DeclaredRange(DescModel):
    """The range a column's values lie in, in the column's type (checked with the column)."""

    min: DataBound
    max: DataBound


def _position(datatype: str, bound: str | int | float) -> int | float | None:
    """Where a bound lies, as a number compared exactly, if it has the column's type."""
    if datatype == "date":
        named = day(bound) if isinstance(bound, str) else None
        return None if named is None else named.toordinal()
    if datatype == "datetime":
        return instant_ns(bound) if isinstance(bound, str) else None
    if isinstance(bound, str) or (datatype == "integer" and not float(bound).is_integer()):
        return None
    return bound


def _range_problems(
    datatype: str | None, declared: DeclaredRange
) -> list[tuple[Location, PydanticCustomError]]:
    if datatype not in _WITH_RANGE:
        return [
            (
                (),
                problem(
                    "conflicting_members",
                    "A declared range applies to number, integer, date, datetime and "
                    "time_offset columns",
                ),
            )
        ]
    assert datatype is not None
    found: list[tuple[Location, PydanticCustomError]] = []
    low = _position(datatype, declared.min)
    high = _position(datatype, declared.max)
    for name, position in (("min", low), ("max", high)):
        if position is None:
            found.append(((name,), problem("range_type", _BOUND_TYPES[datatype])))
    if low is not None and high is not None and low > high:
        found.append((("max",), problem("range_order", "max is at least min")))
    return found


def _delimiter(value: str) -> str:
    if not 1 <= len(value) <= 8:
        raise problem("delimiter", "A delimiter has 1 to 8 characters")
    return _unicode(value)


class ListSyntax(DescModel):
    model_config = ConfigDict(
        json_schema_extra={
            "if": {"properties": {"format": {"const": "delimited"}}},
            "then": {"required": ["delimiter"]},
            "else": {"not": {"required": ["delimiter"]}},
        }
    )

    format: Literal["json", "python", "delimited"]
    delimiter: (
        Annotated[
            str,
            AfterValidator(_delimiter),
            WithJsonSchema({"type": "string", "minLength": 1, "maxLength": 8}),
        ]
        | None
    ) = None
    """Given exactly when the format is ``delimited``."""

    @model_validator(mode="after")
    def _check(self) -> Self:
        found = Problems("ListSyntax")
        if self.format == "delimited" and self.delimiter is None:
            found.add(
                ("delimiter",),
                problem("required_member", "A delimited list needs its delimiter"),
            )
        if self.format != "delimited" and self.delimiter is not None:
            found.add(
                ("delimiter",),
                problem("conflicting_members", "A delimiter is only for delimited lists"),
            )
        found.raise_any()
        return self


class ColumnSource(DescModel):
    original_name: OriginalName
    metadata: (
        Annotated[
            dict[String, Text],
            Field(max_length=MAX_ENTRIES),
            LimitName(ENTRIES),
            map_cap(MAX_ENTRIES),
        ]
        | None
    ) = None


MissingCodes = Annotated[
    dict[String, Literal["UNKNOWN", "NOT_APPLICABLE", "NOT_ASSESSED"]],
    Field(max_length=MAX_LIST),
    LimitName(LIST_MEMBERS),
    map_cap(MAX_LIST),
]


def _if_datatype(member: str, datatypes: Sequence[str]) -> dict[str, JsonValue]:
    """JSON Schema: ``member`` is given only with one of ``datatypes``."""
    return {
        "if": {"required": [member]},
        "then": {"required": ["datatype"], "properties": {"datatype": {"enum": [*datatypes]}}},
    }


def _bounds(datatypes: Sequence[str], bound: dict[str, JsonValue]) -> dict[str, JsonValue]:
    """JSON Schema: with one of ``datatypes``, both bounds of a declared range are ``bound``."""
    return {
        "if": {"properties": {"datatype": {"enum": [*datatypes]}}, "required": ["datatype"]},
        "then": {"properties": {"range": {"properties": {"min": bound, "max": bound}}}},
    }


class ColumnFields(DescModel):
    model_config = ConfigDict(
        json_schema_extra={
            "allOf": [
                _if_datatype("units", _WITH_UNITS),
                _if_datatype("range", _WITH_RANGE),
                _if_datatype("permissible_values", _WITH_VALUES),
                _if_datatype("list_syntax", ["list<category>"]),
                _bounds(["number", "time_offset"], {"type": "number"}),
                _bounds(["integer"], {"type": "integer"}),
                _bounds(["date"], {"type": "string", "pattern": _DAY}),
                _bounds(["datetime"], {"type": "string", "pattern": _TIMESTAMP}),
                {
                    "if": {
                        "properties": {"datatype": {"const": "list<category>"}},
                        "required": ["datatype"],
                    },
                    "then": {"required": ["list_syntax"]},
                },
            ]
        }
    )

    datatype: Datatype | None = None
    units: Units | None = None
    range: DeclaredRange | None = None
    permissible_values: PermissibleValues | None = None
    missing_codes: MissingCodes | None = None
    identifier: StrictBool | None = None
    concepts: OntologyRefs | None = None
    maps_to: ConceptMapping | None = None
    derived: Derived | None = None
    list_syntax: ListSyntax | None = None
    completeness: Literal["complete", "partial", "unknown"] | None = None
    source: ColumnSource | None = None

    @model_validator(mode="after")
    def _check(self) -> Self:
        found = Problems("ColumnFields")
        datatype = self.datatype
        if datatype == "list<category>" and self.list_syntax is None:
            found.add(
                ("list_syntax",),
                problem("required_member", "A list<category> column needs list_syntax"),
            )
        if self.list_syntax is not None and datatype != "list<category>":
            found.add(
                ("list_syntax",),
                problem("conflicting_members", "list_syntax is only for list<category> columns"),
            )
        if self.units is not None and datatype not in _WITH_UNITS:
            found.add(
                ("units",),
                problem(
                    "conflicting_members",
                    "units apply to number, integer and time_offset columns",
                ),
            )
        if self.range is not None:
            found.extend(("range",), _range_problems(datatype, self.range))
        if self.permissible_values is not None:
            if datatype not in _WITH_VALUES:
                found.add(
                    ("permissible_values",),
                    problem(
                        "conflicting_members",
                        "Permissible values apply to category and list<category> columns",
                    ),
                )
            for index, entry in enumerate(self.permissible_values.values):
                if entry.value in (self.missing_codes or {}):
                    found.add(
                        ("permissible_values", "values", index, "value"),
                        problem(
                            "conflicting_members",
                            "A permissible value is not also a missing code: ",
                            shown=entry.value,
                        ),
                    )
        found.raise_any()
        return self


class ColumnDescriptor(Envelope):
    kind: Literal["column"]
    id: ColumnRef
    version: Version
    fields: ColumnFields
    curated: ClassVar[bool] = True

    @model_validator(mode="after")
    def _check_inputs(self) -> Self:
        if self.fields.derived is not None:
            column = self.id.split(".", 1)[1]
            found = Problems("ColumnDescriptor")
            for at, name in derived_inputs(self.fields.derived):
                if name == column:
                    found.add(
                        ("fields", "derived", *at),
                        problem("conflicting_members", "A derived column does not read itself"),
                    )
            found.raise_any()
        return self


# --- Relationship (§5.5) and coverage (§5.6) --------------------------------------------------


class RelationshipFields(DescModel):
    child_table: TableId
    child_columns: KeyColumns
    parent_table: TableId
    parent_columns: KeyColumns
    cardinality: Literal["many-to-one", "one-to-one"]
    role: Identifier | None = None

    @model_validator(mode="after")
    def _check(self) -> Self:
        found = Problems("RelationshipFields")
        if len(self.child_columns) != len(self.parent_columns):
            found.add(
                ("parent_columns",),
                problem(
                    "conflicting_members", "parent_columns has one column for each child column"
                ),
            )
        found.extend((), _repeats(self.child_columns, ("child_columns",), "A column repeats: "))
        found.extend((), _repeats(self.parent_columns, ("parent_columns",), "A column repeats: "))
        if self.role is not None and self.role in self.child_columns:
            found.add(
                ("role",),
                problem("conflicting_members", "A role differs from the child table's column ids"),
            )
        found.raise_any()
        return self

    def descriptor_id(self) -> str:
        suffix = self.role if self.role is not None else "+".join(self.child_columns)
        return f"rel:{self.child_table}.{suffix}"


class RelationshipDescriptor(Envelope):
    kind: Literal["relationship"]
    id: RelationshipId
    version: Version
    fields: RelationshipFields
    curated: ClassVar[bool] = True

    @model_validator(mode="after")
    def _check_id(self) -> Self:
        if self.id != self.fields.descriptor_id():
            _wrong_id(self.fields.descriptor_id()).raise_any()
        return self


ColumnMap = Annotated[
    dict[ColumnId, ColumnId],
    Field(min_length=1, max_length=MAX_COLUMNS),
    LimitName(KEY_COLUMNS),
    map_cap(MAX_COLUMNS),
]
"""Coverage-table columns to the columns they stand for; no column is mapped to twice."""


def _map_problems(
    columns: Mapping[str, str] | None, at: Location
) -> list[tuple[Location, PydanticCustomError]]:
    if columns is None:
        return []
    found: list[tuple[Location, PydanticCustomError]] = []
    seen: set[str] = set()
    for key, target in columns.items():
        if target in seen:
            found.append(
                (
                    (*at, key),
                    problem("duplicate_entry", "Two columns are mapped to ", shown=target),
                )
            )
        seen.add(target)
    return found


class DirectCoverage(DescModel):
    table: TableId
    parent_columns: ColumnMap
    scope_columns: ColumnMap | None = None

    @model_validator(mode="after")
    def _check(self) -> Self:
        found = Problems("DirectCoverage")
        found.extend((), _map_problems(self.parent_columns, ("parent_columns",)))
        found.extend((), _map_problems(self.scope_columns, ("scope_columns",)))
        found.raise_any()
        return self


class Assignment(DescModel):
    table: TableId
    parent_columns: ColumnMap
    group_column: ColumnId

    @model_validator(mode="after")
    def _check(self) -> Self:
        found = Problems("Assignment")
        found.extend((), _map_problems(self.parent_columns, ("parent_columns",)))
        found.raise_any()
        return self


class Groups(DescModel):
    model_config = ConfigDict(
        json_schema_extra={
            "dependentRequired": {"covers_all_column": ["scope_columns"]},
        }
    )

    table: TableId
    group_column: ColumnId
    scope_columns: ColumnMap | None = None
    covers_all_column: ColumnId | None = None
    """A boolean column marking a group that covers every scope value; needs scope columns."""

    @model_validator(mode="after")
    def _check(self) -> Self:
        found = Problems("Groups")
        found.extend((), _map_problems(self.scope_columns, ("scope_columns",)))
        if self.covers_all_column is not None and self.scope_columns is None:
            found.add(
                ("covers_all_column",),
                problem("conflicting_members", "covers_all_column is only for groups with scope"),
            )
        found.raise_any()
        return self


class GroupedCoverage(DescModel):
    assignment: Assignment
    groups: Groups


def _parents_tag(value: object) -> str | None:
    if isinstance(value, str):
        return "p:word"
    if isinstance(value, dict):
        return "p:grouped" if "assignment" in value else "p:direct"
    if isinstance(value, GroupedCoverage):
        return "p:grouped"
    if isinstance(value, DirectCoverage):
        return "p:direct"
    return None


Parents = Annotated[
    Annotated[Literal["all"], Tag("p:word")]
    | Annotated[DirectCoverage, Tag("p:direct")]
    | Annotated[GroupedCoverage, Tag("p:grouped")],
    Discriminator(
        _parents_tag,
        custom_error_type="parents_type",
        custom_error_message='parents is "all", a direct form or a grouped form',
    ),
]
"""Absent, coverage is undeclared, as for a relationship without a coverage descriptor."""


PARENT_SCOPE_KINDS = ("value", "exists", "covered")
"""The leaf kinds a coverage's ``parent_scope`` may hold: it is a core clause (§5.6)."""


def parent_scope_kind() -> PydanticCustomError:
    """The problem with a pack, ids or cohort leaf in a parent scope, found at its ``kind``."""
    return problem(
        "parent_scope_clause",
        "parent_scope is a core clause: no pack, ids or cohort leaves",
        alternatives=list(PARENT_SCOPE_KINDS),
    )


def _scope_problems(clause: ClauseModel) -> list[tuple[Location, PydanticCustomError]]:
    """Leaves a parent scope may not hold, and steps given by dataset: a parent scope is on the
    one dataset of its release."""
    found: list[tuple[Location, PydanticCustomError]] = []
    for inner, path, _ in walk([clause], []):
        if isinstance(inner, PackLeaf | IdsLeaf | CohortLeaf):
            found.append(((*path[1:], "kind"), parent_scope_kind()))
        elif isinstance(inner, ValueLeaf | ExistsLeaf | CoveredLeaf) and isinstance(
            inner.via, dict
        ):
            found.append(
                (
                    (*path[1:], "via"),
                    problem(
                        "cross_dataset_only",
                        "A via by dataset is for cross-dataset cohorts; a parent scope is on "
                        "one dataset, so give the steps as a list",
                    ),
                )
            )
    return found


FilterValues = Annotated[
    list[String],
    Field(min_length=1, max_length=MAX_LIST, json_schema_extra={"uniqueItems": True}),
    LimitName(LIST_MEMBERS),
]
"""Allowed values of a category column: strings, as category values are (§6.4)."""


class CoverageFields(DescModel):
    relationship: RelationshipId
    parents: Parents | None = None
    record_filter: (
        Annotated[
            dict[ColumnId, FilterValues],
            Field(min_length=1, max_length=MAX_ENTRIES),
            LimitName(ENTRIES),
            map_cap(MAX_ENTRIES),
        ]
        | None
    ) = None
    parent_scope: Clause | None = None

    def tables(self) -> list[str]:
        """The coverage, assignment and group tables named, in the order written."""
        if isinstance(self.parents, DirectCoverage):
            return [self.parents.table]
        if isinstance(self.parents, GroupedCoverage):
            return [self.parents.assignment.table, self.parents.groups.table]
        return []

    @model_validator(mode="after")
    def _check(self) -> Self:
        found = Problems("CoverageFields")
        if self.parent_scope is not None:
            found.extend(("parent_scope",), _scope_problems(self.parent_scope))
        for column, values in (self.record_filter or {}).items():
            found.extend(
                (), _repeats(values, ("record_filter", column), "A value is listed twice: ")
            )
        found.raise_any()
        return self


class CoverageDescriptor(Envelope):
    kind: Literal["coverage"]
    id: CoverageId
    version: Version
    fields: CoverageFields
    curated: ClassVar[bool] = True

    @model_validator(mode="after")
    def _check_id(self) -> Self:
        expected = "cov:" + self.fields.relationship.removeprefix("rel:")
        if self.id != expected:
            _wrong_id(expected).raise_any()
        return self


# --- Endpoint (§5.8) -------------------------------------------------------------------------

EventValues = Annotated[
    list[DataScalar],
    Field(max_length=MAX_ENTRIES, json_schema_extra={"uniqueItems": True}),
    LimitName(ENTRIES),
]


class EventCoding(DescModel):
    """Status values for an event and for censoring; no value is both."""

    event: Annotated[EventValues, Field(min_length=1)]
    censored: EventValues

    @model_validator(mode="after")
    def _check(self) -> Self:
        found = Problems("EventCoding")
        found.extend((), _repeats(self.event, ("event",), "A value is listed twice: "))
        found.extend((), _repeats(self.censored, ("censored",), "A value is listed twice: "))
        events = {_json_key(value) for value in self.event}
        for index, value in enumerate(self.censored):
            if _json_key(value) in events:
                found.add(
                    ("censored", index),
                    problem(
                        "conflicting_members", "A value is an event or censored: ", shown=value
                    ),
                )
        found.raise_any()
        return self


class EntryColumn(DescModel):
    column: ColumnId


def _entry_tag(value: object) -> str | None:
    if isinstance(value, str):
        return "e:word"
    if isinstance(value, dict | EntryColumn):
        return "e:column"
    return None


Entry = Annotated[
    Annotated[Literal["at_origin"], Tag("e:word")] | Annotated[EntryColumn, Tag("e:column")],
    Discriminator(
        _entry_tag,
        custom_error_type="entry_type",
        custom_error_message='entry is "at_origin" or {"column": ...}',
    ),
]


class EndpointFields(DescModel):
    table: TableId | None = None
    time_column: ColumnId | None = None
    status_column: ColumnId | None = None
    event_coding: EventCoding | None = None
    time_origin: ConceptId | None = None
    entry: Entry | None = None
    """Delayed entry; absent, it is undeclared (§5.8)."""
    maps_to: ConceptMapping | None = None


class EndpointDescriptor(Envelope):
    kind: Literal["endpoint"]
    id: EndpointId
    version: Version
    fields: EndpointFields
    curated: ClassVar[bool] = True


# --- Concepts (§5.7), model cards (§5.9) and analysis entries (§9.1) ---------------------------


class ConceptFields(DescModel):
    model_config = ConfigDict(
        json_schema_extra={
            "if": {"properties": {"sort": {"not": {"const": "value"}}}},
            "then": {
                "not": {"anyOf": [{"required": ["units"]}, {"required": ["permissible_values"]}]}
            },
            "else": {"not": {"required": ["units", "permissible_values"]}},
        }
    )

    sort: Literal["value", "table", "endpoint", "time_origin"]
    units: Units | None = None
    permissible_values: PermissibleValues | None = None
    """Only value concepts have units or permissible values, and never both."""

    @model_validator(mode="after")
    def _check(self) -> Self:
        found = Problems("ConceptFields")
        if self.units is not None and self.permissible_values is not None:
            found.add(
                ("permissible_values",),
                problem(
                    "conflicting_members",
                    "A value concept has units or permissible values, not both",
                ),
            )
        if self.sort != "value":
            for member in ("units", "permissible_values"):
                if getattr(self, member) is not None:
                    found.add(
                        (member,),
                        problem(
                            "conflicting_members",
                            "Only value concepts have units or permissible values",
                        ),
                    )
        found.raise_any()
        return self


def _without_curation(schema: dict[str, Any]) -> None:
    """JSON Schema: a kind defined by code or configuration carries no curation entries."""
    schema["properties"]["curation"]["maxProperties"] = 0


class ConceptDescriptor(Envelope):
    model_config = ConfigDict(json_schema_extra=_without_curation)

    kind: Literal["concept"]
    id: ConceptId
    version: Version
    fields: ConceptFields


class ModelCardFields(DescModel):
    """Every member is required (§5.9)."""

    provider: NameText
    model: NameText
    model_version: NameText
    purpose: Annotated[list[Label], Field(min_length=1, max_length=MAX_ENTRIES), LimitName(ENTRIES)]
    limitations: Text
    configuration_digest: Sha256
    """``sha256:`` and the hash of the instructions and settings in use (SPEC §5.1)."""


class ModelCardDescriptor(Envelope):
    model_config = ConfigDict(json_schema_extra=_without_curation)

    kind: Literal["model"]
    id: ModelCardId
    version: SemVer
    fields: ModelCardFields


class Requirement(DescModel):
    """What an analysis needs (§9.1). Closed until M3 adds requirement predicates in use."""

    role: Identifier
    kind: Literal["endpoint", "column", "table"] | None = None
    on: Literal["unit"] | None = None
    datatype: Datatype | None = None
    min: Count | None = None
    max: PositiveInt | None = None
    predicate: PackName | None = None
    """A pack's requirement predicate, ``<pack id>.<name>``."""

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.min is not None and self.max is not None and self.min > self.max:
            found = Problems("Requirement")
            found.add(("max",), problem("conflicting_members", "max is at least min"))
            found.raise_any()
        return self


class Library(DescModel):
    name: NameText
    version: NameText


class CrossDataset(DescModel):
    method: Text


class Randomness(DescModel):
    seeded: StrictBool
    replicates: PositiveInt


JsonSchemaObject = Annotated[
    dict[String, AnyJson], Field(max_length=MAX_ENTRIES), LimitName(ENTRIES), map_cap(MAX_ENTRIES)
]
"""A JSON Schema, generated from a Pydantic model; ``null`` may appear inside it."""


class AnalysisFields(DescModel):
    """Required members are those without a default (§9.1)."""

    _nullable: ClassVar[frozenset[str]] = frozenset({"cross_dataset"})

    requires: Annotated[list[Requirement], Field(max_length=MAX_ENTRIES), LimitName(ENTRIES)]
    params: JsonSchemaObject
    """The JSON Schema of the parameters."""
    returns: JsonSchemaObject
    methods: Annotated[
        dict[String, Text], Field(max_length=MAX_ENTRIES), LimitName(ENTRIES), map_cap(MAX_ENTRIES)
    ]
    library: Library | None = None
    assumptions: Annotated[list[Label], Field(max_length=MAX_ENTRIES), LimitName(ENTRIES)]
    uses_reference: StrictBool
    assumes_independent_groups: StrictBool
    cross_dataset: CrossDataset | None
    """``null``: the analysis cannot run across datasets (§7.5)."""
    randomness: Randomness | None = None
    caveats: Annotated[
        list[AnyCode],
        Field(max_length=MAX_ENTRIES, json_schema_extra={"uniqueItems": True}),
        LimitName(ENTRIES),
    ]
    """Every code the analysis can raise, exhaustively."""
    min_group_n: PositiveInt | None = None
    min_events: PositiveInt | None = None

    @model_validator(mode="after")
    def _check(self) -> Self:
        found = Problems("AnalysisFields")
        roles = [requirement.role for requirement in self.requires]
        found.extend(
            (),
            [
                ((*at, "role"), error)
                for at, error in _repeats(roles, ("requires",), "A role is required twice: ")
            ],
        )
        found.extend((), _repeats(self.caveats, ("caveats",), "A code is listed twice: "))
        found.raise_any()
        return self


class AnalysisDescriptor(Envelope):
    model_config = ConfigDict(json_schema_extra=_without_curation)

    kind: Literal["analysis"]
    id: AnalysisId
    version: SemVer
    fields: AnalysisFields


_KINDS = (
    "dataset",
    "table",
    "column",
    "relationship",
    "coverage",
    "endpoint",
    "concept",
    "model",
    "analysis",
)
_DescriptorUnion = Annotated[
    Annotated[DatasetDescriptor, Tag("dataset")]
    | Annotated[TableDescriptor, Tag("table")]
    | Annotated[ColumnDescriptor, Tag("column")]
    | Annotated[RelationshipDescriptor, Tag("relationship")]
    | Annotated[CoverageDescriptor, Tag("coverage")]
    | Annotated[EndpointDescriptor, Tag("endpoint")]
    | Annotated[ConceptDescriptor, Tag("concept")]
    | Annotated[ModelCardDescriptor, Tag("model")]
    | Annotated[AnalysisDescriptor, Tag("analysis")]
    | Annotated[SkipJsonSchema[object], _refuser("kind", _KINDS), Tag("bad:kind")],
    _chooser("kind", _KINDS),
]
if TYPE_CHECKING:
    Descriptor = (
        DatasetDescriptor
        | TableDescriptor
        | ColumnDescriptor
        | RelationshipDescriptor
        | CoverageDescriptor
        | EndpointDescriptor
        | ConceptDescriptor
        | ModelCardDescriptor
        | AnalysisDescriptor
    )
else:
    Descriptor = _DescriptorUnion
"""Any descriptor, chosen by ``kind``."""

RELEASE_KINDS = ("dataset", "table", "column", "relationship", "coverage", "endpoint")

__all__ = [
    "PARENT_SCOPE_KINDS",
    "RELEASE_KINDS",
    "SET_BY",
    "AnalysisDescriptor",
    "ColumnDescriptor",
    "ConceptDescriptor",
    "CoverageDescriptor",
    "CurationStatus",
    "DatasetDescriptor",
    "DescModel",
    "Descriptor",
    "EndpointDescriptor",
    "ModelCardDescriptor",
    "RelationshipDescriptor",
    "TableDescriptor",
    "day",
    "derived_inputs",
    "instant",
]

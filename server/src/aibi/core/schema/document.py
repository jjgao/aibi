"""The analysis document after parameter substitution (SPEC §7.1–7.4).

These models check the document's shape only. Names, paths, types of constants, pack leaves and
everything else that needs a release's descriptors are checked when the document is resolved
against a release (M2); checks that need only the document are in ``aibi.core.schema.checks``.
"""

import math
import re
from typing import TYPE_CHECKING, Annotated, Any, Literal, Self, cast

from packaging.specifiers import InvalidSpecifier, SpecifierSet
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
    WithJsonSchema,
    model_serializer,
    model_validator,
)
from pydantic.json_schema import SkipJsonSchema
from pydantic_core import PydanticCustomError

from aibi.core.schema.ids import (
    CONCEPT_ID_RE,
    IDENT,
    MAX_SAFE_INTEGER,
    PACK_LEAF_KIND_RE,
    RESERVED_PACK_IDS,
    AnalysisId,
    ColumnId,
    DatasetId,
    DatasetRef,
    Name,
    PackId,
    RelationshipId,
)
from aibi.core.schema.limits import (
    CLAUSES,
    COHORTS,
    CONSTANT_CHARACTERS,
    DATASETS,
    IDENTIFIER_CHARACTERS,
    KEY_COLUMNS,
    LIST_MEMBERS,
    MAX_CLAUSES,
    MAX_COHORTS,
    MAX_COLUMNS,
    MAX_DATASETS,
    MAX_IDENTIFIER,
    MAX_LIST,
    MAX_NAME,
    MAX_PACKS,
    MAX_PARAMS,
    MAX_PATH_STEPS,
    MAX_STRING,
    MAX_TEXT,
    MAX_VIEWS,
    NAME_CHARACTERS,
    NOTE_CHARACTERS,
    PACKS,
    PARAMETERS,
    PATH_STEPS,
    SCOPE_COLUMNS,
    VIEWS,
    LimitName,
)
from aibi.core.schema.output import DATA_MARK

CORE_KINDS_TEXT = "value, exists, covered, ids or cohort"


class DocModel(BaseModel):
    """Base for document parts: strict (no coercion), closed and immutable.

    ``null`` is never a value in a document (SPEC §7.1): an absent member is omitted, and a dump
    writes only the members that were given, so a dumped document loads again unchanged.
    """

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True, serialize_by_alias=True)

    @model_validator(mode="before")
    @classmethod
    def _refuse_null(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        members = cast(dict[str, object], data)
        for key, value in members.items():
            if value is None:
                raise PydanticCustomError(
                    "null_not_allowed", "{member} is null; omit it instead", {"member": key}
                )
        return members

    @model_serializer(mode="wrap")
    def _dump_given(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        dumped: dict[str, Any] = handler(self)
        given = {
            info.serialization_alias or info.alias or name
            for name, info in type(self).model_fields.items()
            if name in self.model_fields_set or info.is_required()
        }
        return {key: value for key, value in dumped.items() if key in given}


# --- Scalars -------------------------------------------------------------------------------


def _scalar(value: object) -> str | bool | int | float:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        if len(value) > MAX_STRING:
            raise PydanticCustomError(
                "string_too_long",
                "String should have at most {max_length} characters",
                {"max_length": MAX_STRING, "limit": CONSTANT_CHARACTERS},
            )
        return value
    if isinstance(value, int):
        if abs(value) > MAX_SAFE_INTEGER:
            raise PydanticCustomError(
                "integer_out_of_range",
                "Integers beyond ±(2^53 - 1) must be written as decimal strings",
            )
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise PydanticCustomError("non_finite_number", "The number is not finite")
        return value
    raise PydanticCustomError("scalar_type", "Expected a string, a boolean or a number")


def _bound(value: object) -> str | int | float:
    if isinstance(value, bool):
        raise PydanticCustomError("bound_type", "A range bound is a number or a string")
    checked = _scalar(value)
    assert not isinstance(checked, bool)
    return checked


_STRING_CONSTANT: dict[str, JsonValue] = {"type": "string", "maxLength": MAX_STRING}

Scalar = Annotated[
    str | bool | int | float,
    PlainValidator(_scalar),
    WithJsonSchema({"anyOf": [_STRING_CONSTANT, {"type": ["boolean", "number"]}]}),
]
"""A constant: typed against its column when the document is resolved (SPEC §6.4)."""

Bound = Annotated[
    str | int | float,
    PlainValidator(_bound),
    WithJsonSchema({"anyOf": [_STRING_CONSTANT, {"type": "number"}]}),
]

Notes = Annotated[
    str, Field(max_length=MAX_TEXT, json_schema_extra=DATA_MARK), LimitName(NOTE_CHARACTERS)
]
"""Plain text, never compiled or interpreted (A6)."""

_REFERENCE_LENGTH = 4 * MAX_IDENTIFIER
ColumnOrConcept = Annotated[
    str,
    Field(
        pattern=rf"^(?:{IDENT}\.{IDENT}|{CONCEPT_ID_RE.pattern[1:-1]})$",
        max_length=_REFERENCE_LENGTH,
    ),
    LimitName(IDENTIFIER_CHARACTERS),
]
TableOrConcept = Annotated[
    str,
    Field(pattern=rf"^(?:{IDENT}|{CONCEPT_ID_RE.pattern[1:-1]})$", max_length=_REFERENCE_LENGTH),
    LimitName(IDENTIFIER_CHARACTERS),
]
Units = Annotated[str, Field(pattern=r"^[!-~]{1,64}$")]
"""A UCUM code, checked against the pinned UCUM tables on resolution (SPEC §6.4)."""
Lift = Literal["strict", "assessed"]

DOCUMENT_JSON_MARK = "x-aibi-document-json"
DocumentJson = Annotated[JsonValue, WithJsonSchema({DOCUMENT_JSON_MARK: True})]
"""Any JSON value but ``null``. The schema export replaces the mark with a definition."""
Count = Annotated[StrictInt, Field(ge=1, le=MAX_SAFE_INTEGER)]


def _pack_specifier(value: str) -> str:
    try:
        SpecifierSet(value)
    except InvalidSpecifier:
        raise PydanticCustomError(
            "invalid_specifier", "Not a PEP 440 version specifier, such as '>=1.2,<2'"
        ) from None
    return value


PackSpecifier = Annotated[
    str,
    Field(max_length=MAX_STRING),
    LimitName(CONSTANT_CHARACTERS),
    AfterValidator(_pack_specifier),
]

_DRAFTED_BY = rf"^(?:model:{IDENT}|(?:agent|operator):[^\x00-\x1f\x7f]{{1,{MAX_NAME}}})$"
_DRAFTED_BY_RE = re.compile(_DRAFTED_BY)


def _drafted_by(value: str) -> str:
    kind, _, name = value.partition(":")
    if kind in ("agent", "operator") and len(name) > MAX_NAME:
        raise PydanticCustomError(
            "string_too_long",
            "A self-declared name has at most {max_length} characters",
            {"max_length": MAX_NAME, "limit": NAME_CHARACTERS},
        )
    if _DRAFTED_BY_RE.fullmatch(value) is None:
        raise PydanticCustomError(
            "drafted_by",
            'Expected "model:<id>", "agent:<name>" or "operator:<name>", the name without '
            "control characters",
        )
    return value


DraftedBy = Annotated[
    str,
    AfterValidator(_drafted_by),
    WithJsonSchema({"type": "string", "pattern": _DRAFTED_BY, **DATA_MARK}),
]


def _pack_id(value: str) -> str:
    if value in RESERVED_PACK_IDS:
        raise PydanticCustomError(
            "reserved_pack_id", "'{pack}' is reserved for the core", {"pack": value}
        )
    return value


PackKey = Annotated[PackId, AfterValidator(_pack_id)]


# --- Paths and quantifiers (SPEC §6.1, §7.2) ---------------------------------------------------


class Step(DocModel):
    rel: RelationshipId
    dir: Literal["up", "down"]


Path = Annotated[list[Step], Field(max_length=MAX_PATH_STEPS), LimitName(PATH_STEPS)]


def _via_tag(value: object) -> str | None:
    if isinstance(value, list):
        return "via:path"
    if isinstance(value, dict):
        return "via:datasets"
    return None


Via = Annotated[
    Annotated[Path, Tag("via:path")]
    | Annotated[
        Annotated[dict[DatasetId, Path], Field(max_length=MAX_DATASETS), LimitName(DATASETS)],
        Tag("via:datasets"),
    ],
    Discriminator(
        _via_tag,
        custom_error_type="via_type",
        custom_error_message="Expected a list of steps, or a map from dataset id to steps",
    ),
]
"""An explicit path, or one per dataset in a cross-dataset query (SPEC §7.5)."""


class SomeAtLeast(DocModel):
    """``{"some": k}``: at least *k* children."""

    some: Count


def _quantifier_item_tag(value: object) -> str | None:
    if isinstance(value, str):
        return "q:word"
    if isinstance(value, dict | SomeAtLeast):
        return "q:some_k"
    return None


QuantifierItem = Annotated[
    Annotated[Literal["some", "every"], Tag("q:word")] | Annotated[SomeAtLeast, Tag("q:some_k")],
    Discriminator(
        _quantifier_item_tag,
        custom_error_type="quantifier_type",
        custom_error_message='Expected "some", "every" or {"some": k}',
    ),
]


def _quantifier_tag(value: object) -> str | None:
    return "q:list" if isinstance(value, list) else "q:single"


Quantifier = Annotated[
    Annotated[QuantifierItem, Tag("q:single")]
    | Annotated[
        Annotated[
            list[QuantifierItem],
            Field(min_length=1, max_length=MAX_PATH_STEPS),
            LimitName(PATH_STEPS),
        ],
        Tag("q:list"),
    ],
    Discriminator(_quantifier_tag),
]
"""One quantifier for every down step, or one per down step in path order (SPEC §7.2)."""


# --- Leaves (SPEC §7.2, §7.3) ----------------------------------------------------------------

ClauseList = Annotated[list["Clause"], Field(max_length=MAX_CLAUSES), LimitName(CLAUSES)]


class Range(DocModel):
    model_config = ConfigDict(
        json_schema_extra={
            "minProperties": 1,
            "not": {"anyOf": [{"required": ["gt", "gte"]}, {"required": ["lt", "lte"]}]},
        }
    )

    gt: Bound | None = None
    gte: Bound | None = None
    lt: Bound | None = None
    lte: Bound | None = None

    @model_validator(mode="after")
    def _check_bounds(self) -> Self:
        if self.gt is None and self.gte is None and self.lt is None and self.lte is None:
            raise PydanticCustomError("empty_range", "A range needs at least one bound")
        if self.gt is not None and self.gte is not None:
            raise PydanticCustomError("conflicting_members", "Give gt or gte, not both")
        if self.lt is not None and self.lte is not None:
            raise PydanticCustomError("conflicting_members", "Give lt or lte, not both")
        return self


ValueList = Annotated[
    list[Scalar], Field(min_length=1, max_length=MAX_LIST), LimitName(LIST_MEMBERS)
]


class ValueLeaf(DocModel):
    """A value predicate (SPEC §6.4): exactly one of ``values``, ``range`` or ``op`` + ``value``."""

    model_config = ConfigDict(
        json_schema_extra={
            "anyOf": [{"required": ["values"]}, {"required": ["range"]}, {"required": ["op"]}],
            "not": {
                "anyOf": [
                    {"required": ["values", "range"]},
                    {"required": ["values", "op"]},
                    {"required": ["range", "op"]},
                ]
            },
            "dependentRequired": {"op": ["value"], "value": ["op"]},
        }
    )

    kind: Literal["value"]
    column: ColumnOrConcept
    values: ValueList | None = None
    range: Range | None = None
    op: Literal["=", "!=", ">", ">=", "<", "<="] | None = None
    value: Scalar | None = None
    negate: StrictBool = False
    units: Units | None = None
    match: Literal["any", "all"] | None = None
    quantifier: Quantifier | None = None
    lift: Lift | None = None
    via: Via | None = None

    @model_validator(mode="after")
    def _check_predicate(self) -> Self:
        given = [name for name in ("values", "range", "op") if getattr(self, name) is not None]
        if len(given) != 1:
            raise PydanticCustomError(
                "conflicting_members",
                "Give exactly one of values, range, or op with value",
            )
        if (self.op is None) != (self.value is None):
            raise PydanticCustomError("conflicting_members", "op and value go together")
        return self


def _check_min_count(quantifier: object, min_count: int | None) -> None:
    if min_count is not None and quantifier not in (None, "some"):
        raise PydanticCustomError(
            "conflicting_members",
            'min_count is allowed only when quantifier is absent or "some"',
        )


class ExistsLeaf(DocModel):
    """An existence question (SPEC §6.5); ``where`` clauses are ANDed per row of ``table``."""

    model_config = ConfigDict(
        json_schema_extra={
            "if": {"required": ["min_count"]},
            "then": {"properties": {"quantifier": {"const": "some"}}},
        }
    )

    kind: Literal["exists"]
    table: TableOrConcept
    where: ClauseList | None = None
    quantifier: Quantifier | None = None
    min_count: Count | None = None
    lift: Lift | None = None
    via: Via | None = None
    exclude_self: StrictBool = False

    @model_validator(mode="after")
    def _check(self) -> Self:
        _check_min_count(self.quantifier, self.min_count)
        return self


class CoveredLeaf(DocModel):
    """Coverage as a predicate (SPEC §6.5)."""

    kind: Literal["covered"]
    table: TableOrConcept
    scope: (
        Annotated[
            dict[ColumnId, ValueList],
            Field(min_length=1, max_length=MAX_COLUMNS),
            LimitName(SCOPE_COLUMNS),
        ]
        | None
    ) = None
    lift: Lift | None = None
    via: Via | None = None


class UnitKey(DocModel):
    dataset: DatasetId
    key: Annotated[
        list[Scalar], Field(min_length=1, max_length=MAX_COLUMNS), LimitName(KEY_COLUMNS)
    ]


def _ids_member_tag(value: object) -> str | None:
    if isinstance(value, str):
        return "ids:text"
    if isinstance(value, dict | UnitKey):
        return "ids:key"
    return None


IdsMember = Annotated[
    Annotated[
        Annotated[
            str,
            Field(pattern=rf"^{IDENT}:[^\x00-\x1f\x7f\u2028\u2029]+$", max_length=MAX_STRING),
            LimitName(CONSTANT_CHARACTERS),
        ],
        Tag("ids:text"),
    ]
    | Annotated[UnitKey, Tag("ids:key")],
    Discriminator(
        _ids_member_tag,
        custom_error_type="ids_member_type",
        custom_error_message='Expected "<dataset>:<key>" or {"dataset": ..., "key": [...]}',
    ),
]


class IdsLeaf(DocModel):
    """An explicit list of unit keys; not allowed inside any ``where`` (SPEC §7.2)."""

    kind: Literal["ids"]
    ids: Annotated[
        list[IdsMember], Field(min_length=1, max_length=MAX_LIST), LimitName(LIST_MEMBERS)
    ]


class CohortLeaf(DocModel):
    """Another cohort of the same document (SPEC §7.2)."""

    kind: Literal["cohort"]
    cohort: Name


_RESERVED_NAMESPACE: dict[str, JsonValue] = {
    "pattern": "^(?:" + "|".join(sorted(RESERVED_PACK_IDS)) + r")\."
}


class PackLeaf(BaseModel):
    """A pack leaf, ``<pack id>.<name>``: other members are checked by the pack's schema."""

    model_config = ConfigDict(strict=True, extra="allow", frozen=True)

    kind: Annotated[
        str,
        Field(
            pattern=PACK_LEAF_KIND_RE.pattern,
            max_length=2 * MAX_IDENTIFIER + 1,
            json_schema_extra={"not": _RESERVED_NAMESPACE},
        ),
        LimitName(IDENTIFIER_CHARACTERS),
    ]

    @model_validator(mode="after")
    def _check_pack(self) -> Self:
        _pack_id(self.kind.split(".", 1)[0])
        return self


# --- Combinators and the clause union (SPEC §7.1) ----------------------------------------------


class AllClause(DocModel):
    all: ClauseList


class AnyClause(DocModel):
    any: ClauseList


class NotClause(DocModel):
    not_: "Clause" = Field(alias="not")


class KnownClause(DocModel):
    known: "Clause"


class UnknownClause(DocModel):
    unknown: "Clause"


def _refuse_unknown_kind(value: object) -> object:
    raise PydanticCustomError(
        "unknown_kind",
        "Unknown leaf kind: core kinds are " + CORE_KINDS_TEXT + "; pack kinds are <pack>.<name>",
    )


def _refuse_shape(value: object) -> object:
    raise PydanticCustomError(
        "not_a_clause",
        'Expected a leaf (an object with "kind") or one of all, any, not, known, unknown',
    )


_LEAF_TAGS = {
    "value": "leaf:value",
    "exists": "leaf:exists",
    "covered": "leaf:covered",
    "ids": "leaf:ids",
    "cohort": "leaf:cohort",
}
_COMBINATORS = ("all", "any", "not", "known", "unknown")
_PACK_KIND = re.compile(PACK_LEAF_KIND_RE.pattern)


def _clause_tag(value: object) -> str:
    if isinstance(value, dict):
        value = cast(dict[str, object], value)
        if "kind" in value:
            kind = value["kind"]
            if not isinstance(kind, str):
                return "bad:kind"
            if kind in _LEAF_TAGS:
                return _LEAF_TAGS[kind]
            return "leaf:pack" if _PACK_KIND.fullmatch(kind) else "bad:kind"
        combinators = [key for key in value if key in _COMBINATORS]
        if len(combinators) == 1:
            # Other members are then reported as unknown members of that combinator.
            return f"clause:{combinators[0]}"
        return "bad:shape"
    for tag, model in _TAG_MODELS.items():
        if isinstance(value, model):
            return tag
    return "bad:shape"


_ClauseUnion = Annotated[
    Annotated[ValueLeaf, Tag("leaf:value")]
    | Annotated[ExistsLeaf, Tag("leaf:exists")]
    | Annotated[CoveredLeaf, Tag("leaf:covered")]
    | Annotated[IdsLeaf, Tag("leaf:ids")]
    | Annotated[CohortLeaf, Tag("leaf:cohort")]
    | Annotated[PackLeaf, Tag("leaf:pack")]
    | Annotated[AllClause, Tag("clause:all")]
    | Annotated[AnyClause, Tag("clause:any")]
    | Annotated[NotClause, Tag("clause:not")]
    | Annotated[KnownClause, Tag("clause:known")]
    | Annotated[UnknownClause, Tag("clause:unknown")]
    | Annotated[SkipJsonSchema[object], PlainValidator(_refuse_unknown_kind), Tag("bad:kind")]
    | Annotated[SkipJsonSchema[object], PlainValidator(_refuse_shape), Tag("bad:shape")],
    Discriminator(_clause_tag),
]
"""The runtime union. Invalid inputs are routed to the two ``bad:`` members, which only raise."""

_TAG_MODELS: dict[str, type[BaseModel]] = {
    "leaf:value": ValueLeaf,
    "leaf:exists": ExistsLeaf,
    "leaf:covered": CoveredLeaf,
    "leaf:ids": IdsLeaf,
    "leaf:cohort": CohortLeaf,
    "leaf:pack": PackLeaf,
    "clause:all": AllClause,
    "clause:any": AnyClause,
    "clause:not": NotClause,
    "clause:known": KnownClause,
    "clause:unknown": UnknownClause,
}
"""The model behind each tag; tags contain ``:`` so they never collide with member names."""

Leaf = ValueLeaf | ExistsLeaf | CoveredLeaf | IdsLeaf | CohortLeaf | PackLeaf
Combinator = AllClause | AnyClause | NotClause | KnownClause | UnknownClause
ClauseModel = Leaf | Combinator

if TYPE_CHECKING:
    Clause = ClauseModel
else:
    Clause = _ClauseUnion


# --- Cohorts, views and the document (SPEC §7.1, §7.4) ------------------------------------------


class Cohort(DocModel):
    """A cohort. Duplicate datasets are refused by the document checks, with their paths."""

    model_config = ConfigDict(json_schema_extra={"not": {"required": ["dataset", "datasets"]}})

    all: ClauseList
    """``[]`` selects every row of the unit table."""
    dataset: DatasetRef | None = None
    datasets: (
        Annotated[
            list[DatasetRef],
            Field(min_length=2, max_length=MAX_DATASETS, json_schema_extra={"uniqueItems": True}),
            LimitName(DATASETS),
        ]
        | None
    ) = None
    unmapped: Literal["allow"] | None = None
    notes: Notes | None = None

    @model_validator(mode="after")
    def _check_datasets(self) -> Self:
        if self.dataset is not None and self.datasets is not None:
            raise PydanticCustomError("conflicting_members", "Give dataset or datasets, not both")
        return self


class View(DocModel):
    """A view. Its cohorts and reference are checked by the document checks, with their paths."""

    analysis: AnalysisId
    cohorts: (
        Annotated[
            list[Name],
            Field(min_length=1, max_length=MAX_COHORTS, json_schema_extra={"uniqueItems": True}),
            LimitName(COHORTS),
        ]
        | None
    ) = None
    reference: Name | None = None
    overlap: Literal["allow"] | None = None
    unmapped: Literal["allow"] | None = None
    params: dict[str, DocumentJson] | None = None
    """Checked against the analysis's parameter schema once the registry exists (M3)."""
    note: Notes | None = None


_PACK_NAMES: dict[str, JsonValue] = {
    "propertyNames": {"not": {"enum": [*sorted(RESERVED_PACK_IDS)]}}
}


class Document(DocModel):
    """An analysis document after ``params`` substitution (SPEC §7.1)."""

    aibi: Literal["1"]
    packs: (
        Annotated[
            dict[PackKey, PackSpecifier],
            Field(max_length=MAX_PACKS, json_schema_extra=_PACK_NAMES),
            LimitName(PACKS),
        ]
        | None
    ) = None
    params: (
        Annotated[dict[Name, DocumentJson], Field(max_length=MAX_PARAMS), LimitName(PARAMETERS)]
        | None
    ) = None
    dataset: DatasetRef | None = None
    unit: TableOrConcept
    cohorts: Annotated[
        dict[Name, Cohort], Field(min_length=1, max_length=MAX_COHORTS), LimitName(COHORTS)
    ]
    views: Annotated[list[View], Field(max_length=MAX_VIEWS), LimitName(VIEWS)] | None = None
    notes: Notes | None = None
    drafted_by: DraftedBy | None = None
    """The client's claim, recorded and never hashed."""


for _model in (ExistsLeaf, AllClause, AnyClause, NotClause, KnownClause, UnknownClause, Cohort):
    _model.model_rebuild()

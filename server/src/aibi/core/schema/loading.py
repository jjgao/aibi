"""Loading a document as written: parse, substitute ``params``, validate, check (SPEC §7.1).

Every problem becomes a refusal whose path points into the document as written (§8.6). Where a
problem lies inside a value that a parameter supplied, the path points at the ``"$name"`` string
and the message names the parameter.
"""

import json
import types
from dataclasses import dataclass, field
from typing import Annotated, Any, Literal, Union, cast, get_args, get_origin

from pydantic import BaseModel, JsonValue, ValidationError
from pydantic_core import ErrorDetails

from aibi.core.schema.checks import check_document
from aibi.core.schema.document import (
    _TAG_MODELS,  # pyright: ignore[reportPrivateUsage]
    Document,
    Path,
    QuantifierItem,
    SomeAtLeast,
    UnitKey,
)
from aibi.core.schema.ids import DatasetId
from aibi.core.schema.jsonio import JsonError, parse_json, pointer
from aibi.core.schema.limits import MAX_DOCUMENT_BYTES
from aibi.core.schema.params import substitute
from aibi.core.schema.refusals import (
    Limit,
    Refusal,
    RefusalCode,
    Segment,
    data,
    sort_refusals,
    text,
)

_TAG_TYPES: dict[str, object] = {
    **_TAG_MODELS,
    "q:some_k": SomeAtLeast,
    "q:word": Literal["some", "every"],
    "q:single": QuantifierItem,
    "q:list": list[QuantifierItem],
    "ids:key": UnitKey,
    "ids:text": str,
    "via:path": Path,
    "via:datasets": dict[DatasetId, Path],
    "bad:kind": object,
    "bad:shape": object,
}
_LABELS = frozenset({"[key]"})


@dataclass(frozen=True)
class DocumentResult:
    """A validated document, or the refusals that stop it (all of them, sorted, §8.6)."""

    document: Document | None
    refusals: list[Refusal]
    written: JsonValue = None
    params_used: dict[str, JsonValue] = field(default_factory=dict[str, JsonValue])
    params_unused: list[str] = field(default_factory=list[str])


def load_document(source: str | bytes) -> DocumentResult:
    size = len(source.encode() if isinstance(source, str) else source)
    if size > MAX_DOCUMENT_BYTES:
        return DocumentResult(
            None,
            [
                Refusal(
                    code=RefusalCode.LIMIT_EXCEEDED,
                    path=None,
                    message=[text("The document is too large")],
                    limit=Limit(name="document_bytes", max=MAX_DOCUMENT_BYTES),
                )
            ],
        )
    try:
        written = parse_json(source)
    except JsonError as problem:
        return DocumentResult(
            None,
            [Refusal(code=problem.code, path=problem.pointer, message=[text(problem.message)])],
        )
    if not isinstance(written, dict):
        return DocumentResult(
            None,
            [
                Refusal(
                    code=RefusalCode.WRONG_TYPE, path="", message=[text("A document is an object")]
                )
            ],
            written,
        )
    nulls = [
        Refusal(
            code=RefusalCode.NULL_NOT_ALLOWED,
            path=path,
            message=[text("null is not allowed in a document; omit the member instead")],
        )
        for path in _nulls(written, [])
    ]
    if nulls:
        return DocumentResult(None, sort_refusals(nulls), written)
    substitution = substitute(written)
    if substitution.refusals:
        return DocumentResult(None, sort_refusals(substitution.refusals), written)
    substituted = substitution.document
    try:
        document = Document.model_validate(substituted)
    except ValidationError as error:
        refusals = [_from_error(details) for details in error.errors()]
        document = None
    else:
        refusals = check_document(document)
    refusals = [_as_written(refusal, substitution.positions) for refusal in refusals]
    return DocumentResult(
        document if not refusals else None,
        sort_refusals(refusals),
        written,
        substitution.used,
        substitution.unused,
    )


def _nulls(value: JsonValue, path: list[str | int]) -> list[str]:
    if value is None:
        return [pointer(path)]
    if isinstance(value, dict):
        return [found for key, member in value.items() for found in _nulls(member, [*path, key])]
    if isinstance(value, list):
        return [found for index, item in enumerate(value) for found in _nulls(item, [*path, index])]
    return []


def _as_written(refusal: Refusal, positions: dict[str, str]) -> Refusal:
    """Point a refusal inside a substituted value at the ``"$name"`` that supplied it."""
    if refusal.path is None:
        return refusal
    for position in sorted(positions, key=len, reverse=True):
        if refusal.path == position or refusal.path.startswith(position + "/"):
            message: list[Segment] = [
                *refusal.message,
                text(" (in the value of parameter "),
                data(positions[position]),
                text(")"),
            ]
            return refusal.model_copy(update={"path": position, "message": message})
    return refusal


# --- Pydantic errors to refusals -------------------------------------------------------------

_CODES: dict[str, RefusalCode] = {
    "missing": RefusalCode.MISSING_MEMBER,
    "extra_forbidden": RefusalCode.UNKNOWN_MEMBER,
    "unknown_kind": RefusalCode.UNKNOWN_KIND,
    "conflicting_members": RefusalCode.CONFLICTING_MEMBERS,
    "integer_out_of_range": RefusalCode.INTEGER_OUT_OF_RANGE,
    "non_finite_number": RefusalCode.NON_FINITE_NUMBER,
    "too_long": RefusalCode.LIMIT_EXCEEDED,
    "string_too_long": RefusalCode.LIMIT_EXCEEDED,
}


def _code(error_type: str) -> RefusalCode:
    if error_type in _CODES:
        return _CODES[error_type]
    if error_type.endswith("_type") or error_type in ("not_a_clause", "model_attributes_type"):
        return RefusalCode.WRONG_TYPE
    return RefusalCode.INVALID_VALUE


def _tokens(loc: tuple[int | str, ...]) -> list[str | int]:
    """The JSON path of an error: its location without Pydantic's union tags and labels.

    Tags contain ``:`` and labels are bracketed, so neither is ever a member name of a document.
    """
    return [
        element
        for element in loc
        if not (isinstance(element, str) and (element in _TAG_TYPES or element in _LABELS))
    ]


def _strip(annotation: object) -> object:
    """Remove ``Annotated`` and ``| None`` wrappers."""
    while True:
        origin = get_origin(annotation)
        if origin is Annotated:
            annotation = cast(object, get_args(annotation)[0])
        elif origin in (Union, types.UnionType):
            members: list[object] = [arg for arg in get_args(annotation) if arg is not type(None)]
            if len(members) != 1:
                return annotation
            annotation = members[0]
        else:
            return annotation


def _type_at(loc: tuple[int | str, ...]) -> object:
    """The annotation at a location, following tags; ``None`` where it cannot be told."""
    current: object = Document
    for element in loc:
        if isinstance(element, str) and element in _TAG_TYPES:
            current = _TAG_TYPES[element]
            continue
        if element in _LABELS:
            continue
        current = _strip(current)
        if isinstance(current, type) and issubclass(current, BaseModel):
            fields = {info.alias or name: info for name, info in current.model_fields.items()}
            if not isinstance(element, str) or element not in fields:
                return None
            current = cast(object, fields[element].annotation)
        elif get_origin(current) is list and isinstance(element, int):
            current = cast(object, get_args(current)[0])
        elif get_origin(current) is dict and isinstance(element, str):
            current = cast(object, get_args(current)[1])
        else:
            return None
    return current


def _members(model: object) -> list[str]:
    model = _strip(model)
    if isinstance(model, type) and issubclass(model, BaseModel):
        return sorted(info.alias or name for name, info in model.model_fields.items())
    return []


def _from_error(details: ErrorDetails) -> Refusal:
    loc = details["loc"]
    tokens = _tokens(loc)
    error_type = details["type"]
    code = _code(error_type)
    message: list[Segment] = [text(details["msg"])]
    alternatives: list[Segment] = []
    limit: Limit | None = None
    ctx: dict[str, Any] = dict(details.get("ctx") or {})
    if code is RefusalCode.UNKNOWN_MEMBER and tokens:
        message = [text("Unknown member "), data(str(tokens[-1]))]
        alternatives = [text(member) for member in _members(_type_at(loc[:-1]))]
    elif code is RefusalCode.MISSING_MEMBER and tokens:
        message = [text(f"Missing member {tokens[-1]}")]
    elif code is RefusalCode.UNKNOWN_KIND:
        alternatives = [text(kind) for kind in ("value", "exists", "covered", "ids", "cohort")]
        alternatives.append(text("<pack id>.<name> for a pack leaf"))
    elif error_type == "literal_error":
        annotation = _strip(_type_at(loc))
        if get_origin(annotation) is Literal:
            alternatives = [text(json.dumps(value)) for value in get_args(annotation)]
    elif code is RefusalCode.LIMIT_EXCEEDED and "max_length" in ctx:
        limit = Limit(name="length", max=int(ctx["max_length"]))
    return Refusal(
        code=code,
        path=pointer(tokens),
        message=message,
        alternatives=alternatives,
        limit=limit,
    )

"""Loading a document as written: parse, substitute ``params``, validate, check (SPEC §7.1).

Every problem becomes a refusal whose path points into the document as written (§8.6). Where a
problem lies inside a value that a parameter supplied, the path points at the ``"$name"`` string
and the message names the parameter.

Loading goes on past ``null`` members and refused parameter references, so that independent
problems are all reported, but nothing is reported under a position already refused. Refusals
are de-duplicated by (path, code), sorted, and capped at ``MAX_REFUSALS``; a last refusal says
how many were left out.
"""

import json
import types
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Annotated, Any, Literal, Union, cast, get_args, get_origin

from pydantic import BaseModel, Discriminator, JsonValue, Tag, ValidationError
from pydantic_core import ErrorDetails

from aibi.core.schema.checks import check_document
from aibi.core.schema.document import CORE_KINDS_TEXT, Document
from aibi.core.schema.jsonio import JsonError, escape_token, parse_json, pointer
from aibi.core.schema.limits import (
    CONSTANT_CHARACTERS,
    DOCUMENT_BYTES,
    LIST_MEMBERS,
    MAX_DEPTH,
    MAX_DOCUMENT_BYTES,
    MAX_REFUSALS,
    NESTING_DEPTH,
    REFUSALS,
    LimitName,
)
from aibi.core.schema.output import Segment, data, text
from aibi.core.schema.params import substitute
from aibi.core.schema.refusals import Limit, Refusal, RefusalCode

_KEY_LABEL = "[key]"
"""Pydantic's location element for a problem with a dict key rather than its value."""


@dataclass(frozen=True)
class DocumentResult:
    """A validated document, or the refusals that stop it."""

    document: Document | None
    refusals: list[Refusal]
    written: JsonValue = None
    params_used: dict[str, JsonValue] = field(default_factory=dict[str, JsonValue])
    params_unused: list[str] = field(default_factory=list[str])
    positions: dict[str, str] = field(default_factory=dict[str, str])
    """JSON Pointer of each position that a parameter filled -> the parameter's name."""


@dataclass(frozen=True)
class _Found:
    """A refusal found, built only if it is among those returned."""

    path: str | None
    code: str
    build: Callable[[], Refusal]


def load_document(source: str | bytes) -> DocumentResult:
    size = len(source.encode("utf-8", "surrogatepass") if isinstance(source, str) else source)
    if size > MAX_DOCUMENT_BYTES:
        return DocumentResult(
            None,
            [
                Refusal(
                    code=RefusalCode.LIMIT_EXCEEDED,
                    path=None,
                    message=[text("The document is too large")],
                    limit=Limit(name=DOCUMENT_BYTES, max=MAX_DOCUMENT_BYTES),
                )
            ],
        )
    try:
        written = parse_json(source)
    except JsonError as problem:
        limit = Limit(name=problem.limit[0], max=problem.limit[1]) if problem.limit else None
        return DocumentResult(
            None,
            [
                Refusal(
                    code=RefusalCode(problem.code),
                    path=problem.pointer,
                    message=[text(problem.message)],
                    limit=limit,
                )
            ],
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

    found: list[_Found] = []
    refused: set[str] = set()
    nulls = list(_nulls(written))
    for path in nulls:
        refused.add(path)
        found.append(_Found(path, RefusalCode.NULL_NOT_ALLOWED, _null_refusal(path)))
    # A null member is reported above; leaving it out lets its siblings be checked.
    cleaned = cast(dict[str, JsonValue], _without_null_members(written)) if nulls else written
    substitution = substitute(cleaned, size)
    for refusal in substitution.refusals:
        found.append(_Found(refusal.path, refusal.code, _given(refusal)))
    refused.update(substitution.failed)
    refused.update(_knock_ons(nulls, substitution.failed, substitution.positions))

    document: Document | None = None
    try:
        document = Document.model_validate(substitution.document)
    except ValidationError as error:
        for details in error.errors(include_url=False, include_input=False):
            if details["type"] == "null_not_allowed" and nulls:
                continue  # every null is already reported, at its own path
            path = pointer(_tokens(details["loc"]))
            if not _under(path, refused):
                found.append(_Found(path, _code(details["type"]), _error_refusal(details)))
    else:
        for refusal in check_document(document):
            if refusal.path is None or not _under(refusal.path, refused):
                found.append(_Found(refusal.path, refusal.code, _given(refusal)))

    refusals = _finish(found, substitution.positions)
    return DocumentResult(
        document if not refusals else None,
        refusals,
        written,
        substitution.used,
        substitution.unused,
        substitution.positions,
    )


def _null_refusal(path: str) -> Callable[[], Refusal]:
    return lambda: Refusal(
        code=RefusalCode.NULL_NOT_ALLOWED,
        path=path,
        message=[text("null is not allowed in a document; omit the member instead")],
    )


def _given(refusal: Refusal) -> Callable[[], Refusal]:
    return lambda: refusal


def _error_refusal(details: ErrorDetails) -> Callable[[], Refusal]:
    return lambda: refusal_from_error(details, Document)


_MAPS = frozenset({"packs", "params", "cohorts", "scope", "via"})
"""Members whose values are maps: a null entry there is kept, so it is refused where it is."""


def _without_null_members(value: JsonValue, parent: str | int | None = None) -> JsonValue:
    """The document without the null members of its objects, which are reported already.

    Leaving a null member out lets its siblings be checked. Entries of maps are kept, since
    leaving one out could make the map look empty or a cohort look unknown, and ``params``
    values are verbatim.
    """
    if isinstance(value, list):
        return [_without_null_members(item, index) for index, item in enumerate(value)]
    if not isinstance(value, dict):
        return value
    result: dict[str, JsonValue] = {}
    for key, member in value.items():
        if member is None and parent not in _MAPS:
            continue
        verbatim = parent is None and key == "params"
        result[key] = member if verbatim else _without_null_members(member, key)
    return result


def _knock_ons(nulls: list[str], failed: list[str], positions: dict[str, str]) -> set[str]:
    """Positions whose problems follow from others already reported.

    A null inside a parameter's value recurs wherever the value was substituted, and a leaf
    whose ``kind`` is a refused reference has no shape to check.
    """
    refused: set[str] = set()
    by_parameter: dict[str, list[str]] = {}
    for position, name in positions.items():
        by_parameter.setdefault(name, []).append(position)
    for null in nulls:
        if not null.startswith("/params/"):
            continue
        token, _, rest = null.removeprefix("/params/").partition("/")
        name = token.replace("~1", "/").replace("~0", "~")
        refused.update(f"{at}/{rest}" if rest else at for at in by_parameter.get(name, []))
    refused.update(path.removesuffix("/kind") for path in failed if path.endswith("/kind"))
    return refused


def _nulls(value: JsonValue) -> Iterator[str]:
    """Pointers to every ``null``; the document is at most ``MAX_DEPTH`` deep."""
    pending: list[tuple[JsonValue, str]] = [(value, "")]
    while pending:
        current, path = pending.pop()
        if current is None:
            yield path
        elif isinstance(current, dict):
            pending.extend(
                (member, f"{path}/{escape_token(key)}") for key, member in current.items()
            )
        elif isinstance(current, list):
            pending.extend((item, f"{path}/{index}") for index, item in enumerate(current))


def _prefixes(path: str) -> Iterator[str]:
    """The pointer and each pointer above it, longest first."""
    while True:
        yield path
        if not path:
            return
        path = path[: path.rfind("/")]


def _under(path: str, positions: set[str]) -> bool:
    return any(prefix in positions for prefix in _prefixes(path))


def as_written(path: str | None, positions: dict[str, str]) -> tuple[str | None, str | None]:
    """The position in the document as written, and the parameter that filled it, if any.

    A pointer inside a substituted value becomes the pointer of the ``"$name"`` string.
    """
    if path is None:
        return None, None
    for prefix in _prefixes(path):
        if prefix in positions:
            return prefix, positions[prefix]
    return path, None


def _finish(found: list[_Found], positions: dict[str, str]) -> list[Refusal]:
    kept: dict[tuple[str | None, str], tuple[_Found, str | None]] = {}
    for item in found:
        path, parameter = as_written(item.path, positions)
        kept.setdefault((path, str(item.code)), (item, parameter))
    order = sorted(kept, key=lambda key: (key[0] or "", key[1]))
    refusals: list[Refusal] = []
    for key in order[:MAX_REFUSALS]:
        item, parameter = kept[key]
        refusal = item.build()
        update: dict[str, Any] = {"path": key[0]}
        if parameter is not None:
            update["message"] = [
                *refusal.message,
                text(" (in the value of parameter "),
                data(parameter),
                text(")"),
            ]
        refusals.append(refusal.model_copy(update=update))
    if len(order) > MAX_REFUSALS:
        refusals.append(
            Refusal(
                code=RefusalCode.LIMIT_EXCEEDED,
                path=None,
                message=[text(f"{len(order) - MAX_REFUSALS} more refusals were left out")],
                limit=Limit(name=REFUSALS, max=MAX_REFUSALS),
            )
        )
    return refusals


# --- Pydantic errors to refusals -------------------------------------------------------------

_CODES: dict[str, RefusalCode] = {
    "missing": RefusalCode.MISSING_MEMBER,
    "extra_forbidden": RefusalCode.UNKNOWN_MEMBER,
    "unknown_kind": RefusalCode.UNKNOWN_KIND,
    "conflicting_members": RefusalCode.CONFLICTING_MEMBERS,
    "integer_out_of_range": RefusalCode.INTEGER_OUT_OF_RANGE,
    "non_finite_number": RefusalCode.NON_FINITE_NUMBER,
    "null_not_allowed": RefusalCode.NULL_NOT_ALLOWED,
    "too_long": RefusalCode.LIMIT_EXCEEDED,
    "string_too_long": RefusalCode.LIMIT_EXCEEDED,
    "recursion_loop": RefusalCode.LIMIT_EXCEEDED,
}


def _code(error_type: str) -> RefusalCode:
    if error_type in _CODES:
        return _CODES[error_type]
    if error_type.endswith("_type") or error_type in ("not_a_clause", "model_attributes_type"):
        return RefusalCode.WRONG_TYPE
    return RefusalCode.INVALID_VALUE


@dataclass(frozen=True)
class Resolved:
    """Where an error location leads: the JSON path it names, and the annotation there."""

    tokens: list[str | int]
    positions: list[int]
    """The index in the location of each token."""
    annotation: object
    metadata: list[object]
    """The ``Annotated`` metadata at the position, such as a ``LimitName``."""


def _unwrap(annotation: object) -> tuple[object, list[object]]:
    """Remove ``Annotated`` layers and ``| None``, keeping the metadata."""
    metadata: list[object] = []
    while True:
        origin = get_origin(annotation)
        if origin is Annotated:
            args = get_args(annotation)
            annotation = cast(object, args[0])
            metadata.extend(cast(tuple[object, ...], args[1:]))
        elif origin in (Union, types.UnionType):
            members: list[object] = [arg for arg in get_args(annotation) if arg is not type(None)]
            if len(members) != 1:
                return annotation, metadata
            annotation = members[0]
        else:
            return annotation, metadata


def _tags(union: object) -> dict[str, object]:
    """The members of a tagged union, by tag."""
    tags: dict[str, object] = {}
    for member in get_args(union):
        if get_origin(member) is Annotated:
            for extra in get_args(member)[1:]:
                if isinstance(extra, Tag):
                    tags[extra.tag] = member
    return tags


def resolve(loc: tuple[int | str, ...], root: type[BaseModel]) -> Resolved:
    """Follow a Pydantic error location through the model types from ``root``.

    Union tags and Pydantic's labels are left out of the path by their position, so a member
    name that happens to look like a tag is kept. Where the types cannot be followed, the rest
    of the location is kept as it is, without labels.
    """
    positions: list[int] = []
    current: object = root
    carried: list[object] = []
    index = 0
    while index < len(loc):
        element = loc[index]
        current, metadata = _unwrap(current)
        metadata = carried + metadata
        carried = []
        if any(isinstance(extra, Discriminator) for extra in metadata):
            tags = _tags(current)
            if isinstance(element, str) and element in tags:
                current = tags[element]
                index += 1
                continue
        if isinstance(current, type) and issubclass(current, BaseModel):
            fields = {info.alias or name: info for name, info in current.model_fields.items()}
            positions.append(index)
            if not isinstance(element, str) or element not in fields:
                current = None
            else:
                current = fields[element].annotation
                carried = list(fields[element].metadata)
        elif get_origin(current) is list and isinstance(element, int):
            positions.append(index)
            current = get_args(current)[0]
        elif get_origin(current) is dict and isinstance(element, str):
            positions.append(index)
            key_type, value_type = get_args(current)
            if index + 1 < len(loc) and loc[index + 1] == _KEY_LABEL:
                current = key_type
                index += 1
            else:
                current = value_type
        else:
            positions.extend(at for at in range(index, len(loc)) if loc[at] != _KEY_LABEL)
            return Resolved([loc[at] for at in positions], positions, None, [])
        index += 1
    current, metadata = _unwrap(current)
    return Resolved([loc[at] for at in positions], positions, current, carried + metadata)


_OTHER = "\x00"
"""Stands for any string in a location's shape that is not a member name, tag or label."""


@lru_cache(maxsize=1)
def _structural_names() -> frozenset[str]:
    """The member names, aliases and union tags of the document models, and Pydantic's label."""
    names = {_KEY_LABEL}
    seen: set[int] = set()
    pending: list[object] = [Document]
    while pending:
        annotation = pending.pop()
        if id(annotation) in seen:
            continue
        seen.add(id(annotation))
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            for name, info in annotation.model_fields.items():
                names.update(key for key in (name, info.alias) if key)
                pending.append(info.annotation)
                pending.extend(info.metadata)
        elif isinstance(annotation, Tag):
            names.add(annotation.tag)
        else:
            pending.extend(get_args(annotation))
    return frozenset(names)


@lru_cache(maxsize=4096)
def _kept(shape: tuple[str | None, ...]) -> tuple[int, ...]:
    """Which elements of a document error location are path tokens.

    The answer depends only on the location's structure, so it is cached by its shape: list
    indices become ``None`` and strings that are not member names, tags or labels (dict keys,
    unknown members) become one placeholder. The cache therefore holds no document text, and
    many similar errors cost little.
    """
    loc = tuple(0 if element is None else element for element in shape)
    return tuple(resolve(loc, Document).positions)


def _tokens(loc: tuple[int | str, ...]) -> list[str | int]:
    names = _structural_names()
    shape = tuple(
        None if isinstance(element, int) else element if element in names else _OTHER
        for element in loc
    )
    return [loc[index] for index in _kept(shape)]


def _members(model: object) -> list[str]:
    model, _ = _unwrap(model)
    if isinstance(model, type) and issubclass(model, BaseModel):
        return sorted(info.alias or name for name, info in model.model_fields.items())
    return []


def refusal_from_error(details: ErrorDetails, root: type[BaseModel]) -> Refusal:
    """The refusal for one Pydantic error in a model validated from ``root``."""
    loc = details["loc"]
    resolved = resolve(loc, root)
    tokens = resolved.tokens
    error_type = details["type"]
    code = _code(error_type)
    message: list[Segment] = [text(details["msg"])]
    alternatives: list[Segment] = []
    limit: Limit | None = None
    ctx: dict[str, Any] = dict(details.get("ctx") or {})
    if code is RefusalCode.UNKNOWN_MEMBER and tokens:
        message = [text("Unknown member "), data(str(tokens[-1]))]
        alternatives = [text(member) for member in _members(resolve(loc[:-1], root).annotation)]
    elif code is RefusalCode.MISSING_MEMBER and tokens:
        message = [text("Missing member "), text(str(tokens[-1]))]
    elif code is RefusalCode.UNKNOWN_KIND:
        alternatives = [text(kind) for kind in CORE_KINDS_TEXT.replace(" or", ",").split(", ")]
        alternatives.append(text("<pack id>.<name> for a pack leaf"))
    elif error_type == "literal_error":
        if get_origin(resolved.annotation) is Literal:
            alternatives = [text(json.dumps(value)) for value in get_args(resolved.annotation)]
    elif error_type == "recursion_loop":
        limit = Limit(name=NESTING_DEPTH, max=MAX_DEPTH)
    elif code is RefusalCode.LIMIT_EXCEEDED and "max_length" in ctx:
        named = [extra.name for extra in resolved.metadata if isinstance(extra, LimitName)]
        fallback = CONSTANT_CHARACTERS if error_type == "string_too_long" else LIST_MEMBERS
        name = str(ctx.get("limit") or (named[0] if named else fallback))
        limit = Limit(name=name, max=int(ctx["max_length"]))
    return Refusal(
        code=code,
        path=pointer(tokens),
        message=message,
        alternatives=alternatives,
        limit=limit,
    )

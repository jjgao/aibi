"""Loading a document as written: parse, substitute ``params``, validate, check (SPEC §7.1).

Every problem becomes a refusal whose path points into the document as written (§8.6). Where a
problem lies inside a value that a parameter supplied, the path points at the ``"$name"`` string
and the message names the parameter.

Loading goes on past ``null`` members and refused parameter references, so that independent
problems are all reported, but nothing is reported under a position already refused, nor what
follows only from it: a clause whose ``kind`` was refused is not checked; an object that lost a
null member is not refused for lacking that member; and the checks that need a cohort's
datasets, its ``unmapped`` or a view's cohorts skip what a null left unknown. Refusals are
de-duplicated by (path, code), sorted, and capped at ``MAX_REFUSALS``; a last refusal says how
many were left out.

Positions are tuples of JSON Pointer tokens, which share the document's strings: a pointer is
written only for a refusal returned, so that long keys above many values cost little.
"""

import json
import types
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Annotated, Any, Literal, Union, cast, get_args, get_origin

from pydantic import BaseModel, Discriminator, JsonValue, Tag, ValidationError
from pydantic_core import ErrorDetails

from aibi.core.schema.checks import Unknown, check_document
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
from aibi.core.schema.params import Position, Substitution, substitute
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
    positions: dict[Position, str] = field(default_factory=dict[Position, str])
    """The pointer tokens of each position that a parameter filled -> the parameter's name."""


@dataclass(frozen=True, slots=True)
class _Found:
    """A refusal found, built only if it is among those returned, at the pointer it is given."""

    position: Position | None
    code: str
    build: Callable[[str | None], Refusal]


class _Positions:
    """Positions, with the one at or above a given position, found in its depth's steps."""

    _END = object()

    def __init__(self) -> None:
        self._root: dict[object, Any] = {}

    def add(self, position: Position, value: object = True) -> None:
        node = self._root
        for token in position:
            node = node.setdefault(str(token), {})
        node.setdefault(self._END, value)

    def above(self, position: Position) -> tuple[int, object] | None:
        """How many tokens the position at or above ``position`` has, and its value; or None."""
        node = self._root
        for depth, token in enumerate((*position, None)):
            if self._END in node:
                return depth, node[self._END]
            if token is None:
                return None
            child = node.get(str(token))
            if child is None:
                return None
            node = child
        return None  # pragma: no cover - the loop returns at the end of the position

    def covers(self, position: Position) -> bool:
        return self.above(position) is not None


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
    refused = _Positions()
    nulls = _nulls(written)
    for position in nulls:
        refused.add(position)
        found.append(_Found(position, RefusalCode.NULL_NOT_ALLOWED, _null_refusal))
    # A null member is reported above; leaving it out lets its siblings be checked.
    dropped: dict[Position, set[str]] = {}
    cleaned = _without_null_members(written, dropped) if nulls else written
    substitution = substitute(cleaned, size)
    for problem in substitution.problems:
        found.append(_Found(problem.position, problem.code, problem.build))
    for position in substitution.failed:
        refused.add(position)
    knock_ons = _knock_ons(written, nulls, dropped, substitution)
    for position in knock_ons.under:
        refused.add(position)

    document: Document | None = None
    try:
        document = Document.model_validate(substitution.document)
    except ValidationError as error:
        for details in error.errors(include_url=False, include_input=False):
            if details["type"] == "null_not_allowed" and nulls:
                continue  # every null is already reported, at its own path
            position = _tokens(details["loc"])
            if _CAUSES.get(details["type"], frozenset()) & knock_ons.lost.get(position, set()):
                continue  # the object lacks a member only because a null was left out
            if not refused.covers(position):
                found.append(
                    _Found(position, _code(details["type"]), _error_refusal(details, position))
                )
    else:
        for refusal in check_document(document, knock_ons.unknown):
            position = None if refusal.path is None else _position(refusal.path)
            if position is None or not refused.covers(position):
                found.append(_Found(position, refusal.code, _given(refusal)))

    positions = _Positions()
    for position, name in substitution.positions.items():
        positions.add(position, name)
    refusals = _finish(found, positions)
    return DocumentResult(
        document if not refusals else None,
        refusals,
        written,
        substitution.used,
        substitution.unused,
        substitution.positions,
    )


def _null_refusal(at: str | None) -> Refusal:
    return Refusal(
        code=RefusalCode.NULL_NOT_ALLOWED,
        path=at,
        message=[text("null is not allowed in a document; omit the member instead")],
    )


def _given(refusal: Refusal) -> Callable[[str | None], Refusal]:
    return lambda at: refusal.model_copy(update={"path": at})


def _error_refusal(details: ErrorDetails, position: Position) -> Callable[[str | None], Refusal]:
    return lambda at: _refusal(details, Document, position, at)


def _position(path: str) -> Position:
    """The tokens of a JSON Pointer."""
    return tuple(token.replace("~1", "/").replace("~0", "~") for token in path.split("/")[1:])


# --- Nulls and what follows from them ------------------------------------------------------


def _nulls(value: JsonValue) -> list[Position]:
    """The position of every ``null``. A position is built only for a null found: the walk
    keeps each value's parent and key, so a long key is not copied for every value below it."""
    found: list[Position] = []
    pending: list[tuple[JsonValue, tuple[Any, str | int] | None]] = [(value, None)]
    while pending:
        current, chain = pending.pop()
        if current is None:
            tokens: list[str | int] = []
            while chain is not None:
                chain, token = chain
                tokens.append(token)
            found.append(tuple(reversed(tokens)))
        elif isinstance(current, dict):
            pending.extend((member, (chain, key)) for key, member in current.items())
        elif isinstance(current, list):
            pending.extend((item, (chain, index)) for index, item in enumerate(current))
    return found


_MAPS_AT_ROOT = ("packs", "cohorts")
"""Root members whose values are maps; ``params`` is kept whole, a null included."""


def _without_null_members(
    document: dict[str, JsonValue], dropped: dict[Position, set[str]]
) -> dict[str, JsonValue]:
    """The document without the null members of its objects, which are reported already.

    Leaving a null member out lets its siblings be checked; ``dropped`` collects, for each
    object, the members left out. Entries of maps are kept, since leaving one out could make the
    map look empty or a cohort look unknown: the maps are ``packs``, ``cohorts``, a leaf's
    ``scope`` and ``via``, and a view's ``params``. ``params`` itself is kept whole, a null
    included, so that references are marked as failed rather than unknown.
    """
    path: list[str | int] = []

    def clean(value: JsonValue, is_map: bool) -> JsonValue:
        if isinstance(value, list):
            items: list[JsonValue] = []
            for index, item in enumerate(value):
                path.append(index)
                items.append(clean(item, False))
                path.pop()
            return items
        if not isinstance(value, dict):
            return value
        leaf = "kind" in value
        result: dict[str, JsonValue] = {}
        for key, member in value.items():
            if member is None and not is_map:
                dropped.setdefault(tuple(path), set()).add(key)
                continue
            path.append(key)
            result[key] = clean(member, _is_map(path, leaf))
            path.pop()
        return result

    result: dict[str, JsonValue] = {}
    for key, member in document.items():
        if key == "params":
            result[key] = member
        elif member is None:
            dropped.setdefault((), set()).add(key)
        else:
            path.append(key)
            result[key] = clean(member, key in _MAPS_AT_ROOT)
            path.pop()
    return result


def _is_map(path: list[str | int], in_leaf: bool) -> bool:
    """Whether the member at ``path`` holds a map: a leaf's scope or via, or a view's params."""
    if in_leaf and path[-1] in ("scope", "via"):
        return True
    return len(path) == 3 and path[0] == "views" and path[2] == "params"


_CAUSES: dict[str, frozenset[str]] = {
    "not_a_clause": frozenset({"kind", "all", "any", "not", "known", "unknown"}),
    "unknown_kind": frozenset({"kind"}),
    "conflicting_members": frozenset({"values", "range", "op", "value"}),
    "empty_range": frozenset({"gt", "gte", "lt", "lte"}),
}
"""Errors about an object as a whole, and the members whose absence can cause each: such an
error is not reported for an object that lost one of them to a null."""


@dataclass(frozen=True)
class _KnockOns:
    under: list[Position]
    """Positions under which nothing is reported."""
    lost: dict[Position, set[str]]
    """Objects that lost members to nulls, left out or supplied by a parameter, with them."""
    unknown: Unknown
    """What the document checks cannot know because of a null."""


def _at(document: JsonValue, position: Position) -> JsonValue:
    """The value at ``position``, or ``None`` if there is none."""
    current = document
    for token in position:
        if isinstance(current, dict) and isinstance(token, str):
            current = current.get(token)
        elif isinstance(current, list) and isinstance(token, int) and 0 <= token < len(current):
            current = current[token]
        else:
            return None
    return current


def _is_clause(document: JsonValue, position: Position) -> bool:
    """Whether ``position`` holds a clause: an item of a cohort's ``all``, or a clause inside one.

    Inside a clause, the items of ``all`` and ``any`` and the values of ``not``, ``known`` and
    ``unknown`` of a clause without ``kind`` are clauses, and so are the items of an ``exists``
    leaf's ``where``.
    """
    if len(position) < 4 or position[0] != "cohorts" or position[2] != "all":
        return False
    node = _at(document, position[:4])
    rest = position[4:]
    while rest:
        if not isinstance(node, dict):
            return False
        step = rest[0]
        if "kind" not in node and step in ("not", "known", "unknown"):
            node, rest = node.get(step), rest[1:]
        elif len(rest) > 1 and (
            ("kind" not in node and step in ("all", "any"))
            or (node.get("kind") == "exists" and step == "where")
        ):
            node, rest = _at(node, rest[:2]), rest[2:]
        else:
            return False
    return isinstance(node, dict)


def _knock_ons(
    written: dict[str, JsonValue],
    nulls: list[Position],
    dropped: dict[Position, set[str]],
    substitution: Substitution,
) -> _KnockOns:
    """Problems that follow only from others already reported.

    A null inside a parameter's value recurs wherever the value was substituted; a clause whose
    ``kind`` is a refused reference has no shape to check; an object that lost a null member
    may look like no clause or lack a member it needs; and what a null leaves unknown is not
    checked.
    """
    under: list[Position] = []
    lost: dict[Position, set[str]] = {parent: set(names) for parent, names in dropped.items()}
    by_parameter: dict[str, list[Position]] = {}
    for position, name in substitution.positions.items():
        by_parameter.setdefault(name, []).append(position)
    for null in nulls:
        if len(null) < 2 or null[0] != "params":
            continue
        name, rest = null[1], null[2:]
        for at in by_parameter.get(str(name), []):
            substituted = (*at, *rest)
            under.append(substituted)
            lost.setdefault(substituted[:-1], set()).add(str(substituted[-1]))
    under.extend(
        position[:-1]
        for position in substitution.failed
        if position and position[-1] == "kind" and _is_clause(written, position[:-1])
    )
    return _KnockOns(under, lost, _unknown(written, dropped))


def _unknown(written: dict[str, JsonValue], dropped: dict[Position, set[str]]) -> Unknown:
    """Cohorts whose datasets or ``unmapped`` a null left unknown, and views whose cohorts."""
    cohorts = written.get("cohorts")
    datasets: set[str] = set()
    unmapped: set[str] = set()
    if isinstance(cohorts, dict):
        root_null = "dataset" in dropped.get((), set())
        for name, cohort in cohorts.items():
            if not isinstance(cohort, dict):
                continue
            lost = dropped.get(("cohorts", name), set())
            own = any(cohort.get(member) is not None for member in ("dataset", "datasets"))
            if not own and (root_null or lost & {"dataset", "datasets"}):
                datasets.add(name)
            if "unmapped" in lost:
                unmapped.add(name)
    views = written.get("views")
    view_cohorts: set[int] = set()
    if isinstance(views, list):
        view_cohorts = {
            index
            for index in range(len(views))
            if "cohorts" in dropped.get(("views", index), set())
        }
    return Unknown(frozenset(datasets), frozenset(unmapped), frozenset(view_cohorts))


def as_written(
    position: Position, positions: Mapping[Position, str]
) -> tuple[Position, str | None]:
    """The position in the document as written, and the parameter that filled it, if any.

    A position inside a substituted value becomes the position of the ``"$name"`` string.
    """
    index = _Positions()
    for filled, name in positions.items():
        index.add(filled, name)
    return _as_written(position, index)


def _as_written(position: Position, positions: _Positions) -> tuple[Position, str | None]:
    above = positions.above(position)
    if above is None:
        return position, None
    depth, name = above
    return position[:depth], str(name)


class _SortKeys:
    """Keys that order positions as their JSON Pointers do, without writing the pointers.

    A pointer is ``/t1/t2/…/tn`` with each token escaped. Keyed as ``(t1/, t2/, …, tn)`` —
    every token but the last followed by its separator — tuples compare as the pointers do: a
    key with a separator is never a proper prefix of another key at the same place, since
    escaped tokens hold no ``/``. Keys are made once per token, so they share their strings.
    """

    def __init__(self) -> None:
        self._inner: dict[str | int, str] = {}
        self._last: dict[str | int, str] = {}

    def of(self, position: Position) -> tuple[str, ...]:
        if not position:
            return ()
        keys: list[str] = []
        for token in position[:-1]:
            key = self._inner.get(token)
            if key is None:
                key = self._inner[token] = escape_token(token) + "/"
            keys.append(key)
        last = self._last.get(position[-1])
        if last is None:
            last = self._last[position[-1]] = escape_token(position[-1])
        keys.append(last)
        return tuple(keys)


def _finish(found: list[_Found], positions: _Positions) -> list[Refusal]:
    keys = _SortKeys()
    kept: dict[tuple[tuple[str, ...], str], tuple[_Found, Position | None, str | None]] = {}
    for item in found:
        if item.position is None:
            written_at, parameter, key = None, None, ()
        else:
            written_at, parameter = _as_written(item.position, positions)
            key = keys.of(written_at)
        kept.setdefault((key, str(item.code)), (item, written_at, parameter))
    order = sorted(kept)
    refusals: list[Refusal] = []
    for key in order[:MAX_REFUSALS]:
        item, written_at, parameter = kept[key]
        refusal = item.build(None if written_at is None else pointer(written_at))
        if parameter is not None:
            message = [
                *refusal.message,
                text(" (in the value of parameter "),
                data(parameter),
                text(")"),
            ]
            refusal = refusal.model_copy(update={"message": message})
        refusals.append(refusal)
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


def _tokens(loc: tuple[int | str, ...]) -> Position:
    names = _structural_names()
    shape = tuple(
        None if isinstance(element, int) else element if element in names else _OTHER
        for element in loc
    )
    return tuple(loc[index] for index in _kept(shape))


def _members(model: object) -> list[str]:
    model, _ = _unwrap(model)
    if isinstance(model, type) and issubclass(model, BaseModel):
        return sorted(info.alias or name for name, info in model.model_fields.items())
    return []


def refusal_from_error(details: ErrorDetails, root: type[BaseModel]) -> Refusal:
    """The refusal for one Pydantic error in a model validated from ``root``."""
    tokens = resolve(details["loc"], root).tokens
    return _refusal(details, root, tokens, pointer(tokens))


def _refusal(
    details: ErrorDetails, root: type[BaseModel], tokens: Sequence[str | int], at: str | None
) -> Refusal:
    """The refusal for an error whose location names ``tokens``, reported at ``at``."""
    loc = details["loc"]
    resolved = resolve(loc, root)
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
        path=at,
        message=message,
        alternatives=alternatives,
        limit=limit,
    )

"""``params`` substitution on the document as written (SPEC §7.1).

A string that is exactly ``"$name"`` is replaced by that parameter's value, whatever its type;
``"$$…"`` stands for the literal string with one ``$`` removed; any other string starting with
``$`` is refused, so that a mistyped reference is never taken literally. Object keys, the
``params`` member itself, and ``notes``, ``note`` and ``drafted_by`` (plain text, A6) are never
substituted, and substituted values are not scanned again.

Substitution can multiply a document: a large value used in many places. The substituted
document, ``params`` included, may be no larger than a document as written, in bytes and in JSON
values, nor nest deeper, nor have longer paths to its values, one by one or together; a
reference that would cross a limit is refused. A document declaring more parameters than it may
have is refused before any is substituted, so that nothing costs more with their number.

Each position a parameter fills gets its own copy of the value, so that no two members of the
substituted document are one object.

Positions are tuples of JSON Pointer tokens, which share the document's strings; pointers are
written only for the refusals returned, so that long keys above many references cost little.
"""

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field

from pydantic import JsonValue

from aibi.core.schema.ids import NAME
from aibi.core.schema.jsonio import escaped_length, pointer
from aibi.core.schema.limits import (
    ALL_POINTER_CHARACTERS,
    JSON_VALUES,
    MAX_DEPTH,
    MAX_DOCUMENT_BYTES,
    MAX_PARAMS,
    MAX_POINTER,
    MAX_POINTERS,
    MAX_VALUES,
    NESTING_DEPTH,
    PARAMETERS,
    POINTER_CHARACTERS,
    SUBSTITUTED_BYTES,
)
from aibi.core.schema.output import data, text
from aibi.core.schema.refusals import Limit, Refusal, RefusalCode

Position = tuple[str | int, ...]
"""The JSON Pointer tokens of a position in the document."""

_REFERENCE = re.compile(rf"\$({NAME})")


def _is_text_member(path: list[str | int]) -> bool:
    """Whether ``path`` is a plain-text member: ``notes`` or ``drafted_by``, a cohort's
    ``notes`` or a view's ``note``. It runs for every value, so it tests lengths first."""
    if len(path) == 1:
        return path[0] in ("notes", "drafted_by")
    if len(path) == 3:
        return (path[0], path[2]) in (("cohorts", "notes"), ("views", "note"))
    return False


def _size(value: JsonValue) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode())


@dataclass(frozen=True, slots=True)
class _Shape:
    values: int
    """JSON values, the value itself included."""
    depth: int
    """Levels of arrays and objects: 0 for a scalar."""
    pointers: int
    """Characters in the pointers from the value to each value in it, together."""
    longest: int
    """Characters in the longest of those pointers."""


def _shape(value: JsonValue) -> _Shape:
    """What a value adds to a document, counted as ``parse_json`` counts it."""
    values = depth = pointers = longest = 0
    pending: list[tuple[JsonValue, int, int]] = [(value, 0, 0)]
    while pending:
        current, level, length = pending.pop()
        values += 1
        pointers += length
        longest = max(longest, length)
        if isinstance(current, dict):
            depth = max(depth, level + 1)
            pending.extend(
                (member, level + 1, length + 1 + escaped_length(key))
                for key, member in current.items()
            )
        elif isinstance(current, list):
            depth = max(depth, level + 1)
            pending.extend(
                (item, level + 1, length + 1 + len(str(index)))
                for index, item in enumerate(current)
            )
    return _Shape(values, depth, pointers, longest)


@dataclass(frozen=True)
class Problem:
    """A refusal found at ``position``, built only if it is among those returned."""

    position: Position
    code: RefusalCode
    build: Callable[[str | None], Refusal]
    """Builds the refusal, given the pointer it is reported at."""


@dataclass
class Substitution:
    """The substituted document, the parameters used and the positions they filled."""

    document: JsonValue
    used: dict[str, JsonValue] = field(default_factory=dict[str, JsonValue])
    unused: list[str] = field(default_factory=list[str])
    positions: dict[Position, str] = field(default_factory=dict[Position, str])
    """Each substituted position -> the parameter's name."""
    failed: list[Position] = field(default_factory=list[Position])
    """The references that were refused and left in place."""
    problems: list[Problem] = field(default_factory=list[Problem])

    @property
    def refusals(self) -> list[Refusal]:
        """Every refusal, with its pointer. The loader builds only those it returns."""
        return [found.build(pointer(found.position)) for found in self.problems]


def _unknown(name: str, declared: list[str]) -> Callable[[str | None], Refusal]:
    return lambda at: Refusal(
        code=RefusalCode.UNKNOWN_PARAMETER,
        path=at,
        message=[text("Unknown parameter "), data(name)],
        alternatives=[data(known) for known in declared],
    )


def _not_a_reference(value: str) -> Callable[[str | None], Refusal]:
    return lambda at: Refusal(
        code=RefusalCode.INVALID_PARAMETER_REFERENCE,
        path=at,
        message=[
            text("Not a parameter reference: "),
            data(value),
            text('. Write "$name" for a parameter or "$$…" for a literal $'),
        ],
    )


def _too_large(name: str, limit: Limit) -> Callable[[str | None], Refusal]:
    return lambda at: Refusal(
        code=RefusalCode.LIMIT_EXCEEDED,
        path=at,
        message=[
            text("Substituting parameter "),
            data(name),
            text(
                " here makes the document larger or deeper, or the paths to its values longer, "
                "than a document may have them"
            ),
        ],
        limit=limit,
    )


def _not_an_object(at: str | None) -> Refusal:
    return Refusal(
        code=RefusalCode.WRONG_TYPE,
        path=at,
        message=[text("params must be an object mapping names to values")],
    )


def _too_many(at: str | None) -> Refusal:
    return Refusal(
        code=RefusalCode.LIMIT_EXCEEDED,
        path=at,
        message=[text(f"A document may declare at most {MAX_PARAMS} parameters")],
        limit=Limit(name=PARAMETERS, max=MAX_PARAMS),
    )


def _copied(value: JsonValue) -> JsonValue:
    """A copy of a JSON value, down to its scalars; values nest at most ``MAX_DEPTH`` deep."""
    if isinstance(value, dict):
        return {key: _copied(member) for key, member in value.items()}
    if isinstance(value, list):
        return [_copied(item) for item in value]
    return value


def substitute(document: dict[str, JsonValue], size: int | None = None) -> Substitution:
    """Substitute parameters. ``size`` is the document's size in bytes as written, if known."""
    params = document.get("params", {})
    result = Substitution(document=None)
    usable = isinstance(params, dict) and len(params) <= MAX_PARAMS
    if not usable:
        # Every reference fails with it; they are marked, but only params itself is refused (a
        # null params by the loader, which refuses every null).
        result.failed.append(("params",))
        if isinstance(params, dict):
            result.problems.append(Problem(("params",), RefusalCode.LIMIT_EXCEEDED, _too_many))
        elif params is not None:
            result.problems.append(Problem(("params",), RefusalCode.WRONG_TYPE, _not_an_object))
    walker = _Walker(
        params if isinstance(params, dict) and usable else {},
        usable,
        result,
        _size(document) if size is None else size,
        _shape(document),
    )
    substituted: dict[str, JsonValue] = {}
    for key, member in document.items():
        substituted[key] = (
            member if key == "params" else walker.walk(member, [key], 1 + escaped_length(key))
        )
    result.document = substituted
    result.unused = sorted(set(walker.params) - set(result.used))
    return result


class _Walker:
    """One substitution, with the budgets it has left. A class rather than nested functions, which
    would refer to themselves and so keep the document in a reference cycle after a load."""

    def __init__(
        self,
        params: dict[str, JsonValue],
        usable: bool,
        result: Substitution,
        total: int,
        written: _Shape,
    ) -> None:
        self.params = params
        self.usable = usable
        self.result = result
        self.declared = sorted(params)
        self.shapes: dict[str, tuple[int, _Shape]] = {}
        self.total = total
        """Bytes, as written so far and with each reference substituted."""
        self.values = written.values
        self.pointers = written.pointers

    def refuse(
        self, where: Position, code: RefusalCode, build: Callable[[str | None], Refusal]
    ) -> None:
        """Record a refusal; the reference stays in place, and nothing under it is reported."""
        self.result.failed.append(where)
        self.result.problems.append(Problem(where, code, build))

    def reference(self, value: str, path: list[str | int], length: int) -> JsonValue:
        """``length`` is that of the pointer to the reference."""
        where = tuple(path)
        if not self.usable:
            self.result.failed.append(where)
            return value
        match = _REFERENCE.fullmatch(value)
        if match is None:
            self.refuse(where, RefusalCode.INVALID_PARAMETER_REFERENCE, _not_a_reference(value))
            return value
        name = match.group(1)
        if name not in self.params:
            self.refuse(where, RefusalCode.UNKNOWN_PARAMETER, _unknown(name, self.declared))
            return value
        if name not in self.shapes:
            self.shapes[name] = (_size(self.params[name]), _shape(self.params[name]))
        value_bytes, shape = self.shapes[name]
        grown = self.total + value_bytes - _size(value)
        more = self.values + shape.values - 1
        # Each value substituted is reached through the reference's pointer; the string it
        # replaces had that pointer too.
        longer = self.pointers + shape.values * length + shape.pointers - length
        limit: Limit | None = None
        if grown > MAX_DOCUMENT_BYTES:
            limit = Limit(name=SUBSTITUTED_BYTES, max=MAX_DOCUMENT_BYTES)
        elif more > MAX_VALUES:
            limit = Limit(name=JSON_VALUES, max=MAX_VALUES)
        elif len(path) + shape.depth > MAX_DEPTH:
            limit = Limit(name=NESTING_DEPTH, max=MAX_DEPTH)
        elif length + shape.longest > MAX_POINTER:
            limit = Limit(name=POINTER_CHARACTERS, max=MAX_POINTER)
        elif longer > MAX_POINTERS:
            limit = Limit(name=ALL_POINTER_CHARACTERS, max=MAX_POINTERS)
        if limit is not None:
            self.refuse(where, RefusalCode.LIMIT_EXCEEDED, _too_large(name, limit))
            return value
        self.total, self.values, self.pointers = grown, more, longer
        self.result.used[name] = self.params[name]
        self.result.positions[where] = name
        return _copied(self.params[name])

    def walk(self, value: JsonValue, path: list[str | int], length: int) -> JsonValue:
        """``path`` is shared and restored on return: tokens are copied only at references.
        ``length`` is that of the pointer to ``value``."""
        if _is_text_member(path):
            return value
        if isinstance(value, dict):
            members: dict[str, JsonValue] = {}
            for key, member in value.items():
                path.append(key)
                members[key] = self.walk(member, path, length + 1 + escaped_length(key))
                path.pop()
            return members
        if isinstance(value, list):
            items: list[JsonValue] = []
            for index, item in enumerate(value):
                path.append(index)
                items.append(self.walk(item, path, length + 1 + len(str(index))))
                path.pop()
            return items
        if isinstance(value, str) and value.startswith("$"):
            if value.startswith("$$"):
                return value[1:]
            return self.reference(value, path, length)
        return value


__all__ = ["Position", "Problem", "Substitution", "substitute"]

"""``params`` substitution on the document as written (SPEC §7.1).

A string that is exactly ``"$name"`` is replaced by that parameter's value, whatever its type;
``"$$…"`` stands for the literal string with one ``$`` removed; any other string starting with
``$`` is refused, so that a mistyped reference is never taken literally. Object keys, the
``params`` member itself, and ``notes``, ``note`` and ``drafted_by`` (plain text, A6) are never
substituted, and substituted values are not scanned again.

Substitution can multiply a document: a large value used in many places. The substituted
document may be no larger than a document as written, in bytes and in JSON values, nor nest
deeper; a reference that would cross a limit is refused.
"""

import json
import re
from dataclasses import dataclass, field

from pydantic import JsonValue

from aibi.core.schema.ids import NAME
from aibi.core.schema.jsonio import pointer
from aibi.core.schema.limits import (
    JSON_VALUES,
    MAX_DEPTH,
    MAX_DOCUMENT_BYTES,
    MAX_VALUES,
    NESTING_DEPTH,
    SUBSTITUTED_BYTES,
)
from aibi.core.schema.output import data, text
from aibi.core.schema.refusals import Limit, Refusal, RefusalCode

_REFERENCE = re.compile(rf"\$({NAME})")

_TEXT_MEMBERS: tuple[tuple[str | None, ...], ...] = (
    ("notes",),
    ("drafted_by",),
    ("cohorts", None, "notes"),
    ("views", None, "note"),
)
"""Paths of plain-text members, where ``None`` stands for any cohort name or view index."""


def _is_text_member(path: list[str | int]) -> bool:
    return any(
        len(path) == len(pattern)
        and all(want is None or want == got for want, got in zip(pattern, path, strict=True))
        for pattern in _TEXT_MEMBERS
    )


def _size(value: JsonValue) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode())


def _shape(value: JsonValue) -> tuple[int, int]:
    """A value's number of JSON values and its nesting depth."""
    count = 0
    depth = 0
    pending: list[tuple[JsonValue, int]] = [(value, 0)]
    while pending:
        current, level = pending.pop()
        count += 1
        if isinstance(current, dict | list):
            depth = max(depth, level + 1)
            members = current.values() if isinstance(current, dict) else current
            pending.extend((member, level + 1) for member in members)
    return count, depth


@dataclass
class Substitution:
    """The substituted document, the parameters used and the positions they filled."""

    document: JsonValue
    used: dict[str, JsonValue] = field(default_factory=dict[str, JsonValue])
    unused: list[str] = field(default_factory=list[str])
    positions: dict[str, str] = field(default_factory=dict[str, str])
    """JSON Pointer of each substituted position -> parameter name."""
    failed: list[str] = field(default_factory=list[str])
    """JSON Pointers of the references that were refused and left in place."""
    refusals: list[Refusal] = field(default_factory=list[Refusal])


def substitute(document: dict[str, JsonValue], size: int | None = None) -> Substitution:
    """Substitute parameters. ``size`` is the document's size in bytes as written, if known."""
    params_value = document.get("params", {})
    result = Substitution(document=None)
    usable = isinstance(params_value, dict)
    if not usable:
        # Every reference fails with it; they are marked, but only params itself is refused (a
        # null params by the loader, which refuses every null).
        result.failed.append("/params")
        if params_value is not None:
            result.refusals.append(
                Refusal(
                    code=RefusalCode.WRONG_TYPE,
                    path="/params",
                    message=[text("params must be an object mapping names to values")],
                )
            )
    params: dict[str, JsonValue] = params_value if isinstance(params_value, dict) else {}
    sizes: dict[str, tuple[int, int, int]] = {}
    pointer_value: dict[str, JsonValue] = {}
    total = _size(document) if size is None else size
    values = _shape(document)[0]

    def refuse(refusal: Refusal) -> JsonValue:
        """Record a refusal; the reference stays in place, and nothing under it is reported."""
        assert refusal.path is not None
        result.failed.append(refusal.path)
        result.refusals.append(refusal)
        return pointer_value[refusal.path]

    def reference(value: str, where: str, level: int) -> JsonValue:
        nonlocal total, values
        pointer_value[where] = value
        if not usable:
            result.failed.append(where)
            return value
        match = _REFERENCE.fullmatch(value)
        if match is None:
            return refuse(
                Refusal(
                    code=RefusalCode.INVALID_PARAMETER_REFERENCE,
                    path=where,
                    message=[
                        text("Not a parameter reference: "),
                        data(value),
                        text('. Write "$name" for a parameter or "$$…" for a literal $'),
                    ],
                )
            )
        name = match.group(1)
        if name not in params:
            return refuse(
                Refusal(
                    code=RefusalCode.UNKNOWN_PARAMETER,
                    path=where,
                    message=[text("Unknown parameter "), data(name)],
                    alternatives=[data(declared) for declared in sorted(params)],
                )
            )
        if name not in sizes:
            sizes[name] = (_size(params[name]), *_shape(params[name]))
        value_bytes, value_count, value_depth = sizes[name]
        grown = total + value_bytes - _size(value)
        more = values + value_count - 1
        limit: Limit | None = None
        if grown > MAX_DOCUMENT_BYTES:
            limit = Limit(name=SUBSTITUTED_BYTES, max=MAX_DOCUMENT_BYTES)
        elif more > MAX_VALUES:
            limit = Limit(name=JSON_VALUES, max=MAX_VALUES)
        elif level + value_depth > MAX_DEPTH:
            limit = Limit(name=NESTING_DEPTH, max=MAX_DEPTH)
        if limit is not None:
            return refuse(
                Refusal(
                    code=RefusalCode.LIMIT_EXCEEDED,
                    path=where,
                    message=[
                        text("Substituting parameter "),
                        data(name),
                        text(" here makes the document larger or deeper than a document may be"),
                    ],
                    limit=limit,
                )
            )
        total, values = grown, more
        result.used[name] = params[name]
        result.positions[where] = name
        return params[name]

    def walk(value: JsonValue, path: list[str | int]) -> JsonValue:
        if _is_text_member(path):
            return value
        if isinstance(value, dict):
            return {key: walk(member, [*path, key]) for key, member in value.items()}
        if isinstance(value, list):
            return [walk(item, [*path, index]) for index, item in enumerate(value)]
        if isinstance(value, str) and value.startswith("$"):
            if value.startswith("$$"):
                return value[1:]
            return reference(value, pointer(path), len(path))
        return value

    substituted: dict[str, JsonValue] = {}
    for key, member in document.items():
        substituted[key] = member if key == "params" else walk(member, [key])
    result.document = substituted
    result.unused = sorted(set(params) - set(result.used))
    return result

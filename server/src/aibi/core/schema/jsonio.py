"""Strict JSON parsing and JSON Pointers.

Documents are parsed before they are validated, so that problems the JSON data model hides are
refused with a path: duplicate keys (SPEC §7.1), non-finite numbers (§8.2) and integers beyond
±(2^53 - 1), which must be written as decimal strings (§5.1).
"""

import json
from collections.abc import Sequence

from pydantic import JsonValue

from aibi.core.schema.ids import MAX_SAFE_INTEGER


class JsonError(Exception):
    """A problem found while parsing, with the JSON Pointer of the offending value."""

    def __init__(self, code: str, pointer: str | None, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.pointer = pointer
        self.message = message


def escape_token(token: str | int) -> str:
    return str(token).replace("~", "~0").replace("/", "~1")


def pointer(tokens: Sequence[str | int]) -> str:
    """The RFC 6901 JSON Pointer for a sequence of object keys and array indices."""
    return "".join("/" + escape_token(token) for token in tokens)


class _Pairs(list[tuple[str, JsonValue]]):
    """Object members as parsed, kept in order so duplicates can be found with their path."""


def _keep_pairs(pairs: list[tuple[str, JsonValue]]) -> _Pairs:
    return _Pairs(pairs)


def _refuse_constant(name: str) -> JsonValue:
    raise JsonError("NON_FINITE_NUMBER", None, f"{name} is not a JSON number")


def _build(value: object, path: list[str | int]) -> JsonValue:
    if isinstance(value, _Pairs):
        result: dict[str, JsonValue] = {}
        for key, member in value:
            if key in result:
                raise JsonError(
                    "DUPLICATE_KEY", pointer([*path, key]), "Duplicate key in a JSON object"
                )
            result[key] = _build(member, [*path, key])
        return result
    if isinstance(value, list):
        items: list[object] = value  # pyright: ignore[reportUnknownVariableType]
        return [_build(item, [*path, index]) for index, item in enumerate(items)]
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return value
    if isinstance(value, int):
        if abs(value) > MAX_SAFE_INTEGER:
            raise JsonError(
                "INTEGER_OUT_OF_RANGE",
                pointer(path),
                "Integers beyond ±(2^53 - 1) must be written as decimal strings",
            )
        return value
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise JsonError("NON_FINITE_NUMBER", pointer(path), "The number is not finite")
        return value
    raise TypeError(f"unexpected JSON value {value!r}")  # pragma: no cover


def parse_json(text: str | bytes) -> JsonValue:
    """Parse JSON, refusing duplicate keys, non-finite numbers and unsafe integers.

    Raises ``JsonError``; its ``pointer`` is ``None`` when the text is not JSON at all.
    """
    try:
        raw: object = json.loads(
            text, object_pairs_hook=_keep_pairs, parse_constant=_refuse_constant
        )
    except JsonError:
        raise
    except (ValueError, UnicodeDecodeError) as error:
        raise JsonError("INVALID_JSON", None, f"Not valid JSON: {error}") from error
    return _build(raw, [])

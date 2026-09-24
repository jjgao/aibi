"""Strict JSON parsing and JSON Pointers (SPEC §7.1).

Documents are parsed before they are validated, so that problems the JSON data model hides are
refused with a path:

- text that is not UTF-8, or starts with a byte order mark;
- duplicate keys;
- strings and keys that are not Unicode text: lone surrogate escapes and noncharacters, as I-JSON
  (RFC 7493 §2.1) requires;
- non-finite numbers;
- numbers beyond ±(2^53 - 1), however they are written: every such double is an integer, and
  integers beyond that range are written as decimal strings instead (§5.1);
- arrays and objects nested more than ``MAX_DEPTH`` deep, more than ``MAX_VALUES`` values, and
  keys that make the JSON Pointer to a value longer than ``MAX_POINTER`` characters, or those to
  all values longer than ``MAX_POINTERS`` together.

A number is a value, not a spelling: an integral number such as ``2.0`` is read as the integer
2, which is how RFC 8785 writes it.
"""

import json
import math
from collections.abc import Sequence
from typing import cast

from pydantic import JsonValue

from aibi.core.schema.ids import MAX_SAFE_INTEGER
from aibi.core.schema.limits import (
    ALL_POINTER_CHARACTERS,
    JSON_VALUES,
    MAX_DEPTH,
    MAX_POINTER,
    MAX_POINTERS,
    MAX_VALUES,
    NESTING_DEPTH,
    POINTER_CHARACTERS,
)

_MAX_INTEGER_DIGITS = len(str(MAX_SAFE_INTEGER))


class JsonError(Exception):
    """A problem found while parsing, with the JSON Pointer of the offending value.

    ``pointer`` is ``None`` when no position can be given. ``limit`` names the limit hit, if any.
    """

    def __init__(
        self,
        code: str,
        pointer: str | None,
        message: str,
        limit: tuple[str, int] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.pointer = pointer
        self.message = message
        self.limit = limit


def escape_token(token: str | int) -> str:
    return str(token).replace("~", "~0").replace("/", "~1")


def pointer(tokens: Sequence[str | int]) -> str:
    """The RFC 6901 JSON Pointer for a sequence of object keys and array indices."""
    return "".join("/" + escape_token(token) for token in tokens)


class _Pairs(list[tuple[str, JsonValue]]):
    """Object members as parsed, kept in order so duplicates can be found with their path."""


class _Constant:
    """``NaN``, ``Infinity`` or ``-Infinity``, kept so the parser can refuse it with its path."""

    def __init__(self, name: str) -> None:
        self.name = name


class _LongInteger:
    """An integer with too many digits to be safe, kept so it can be refused with its path."""


def _keep_pairs(pairs: list[tuple[str, JsonValue]]) -> _Pairs:
    return _Pairs(pairs)


def _keep_constant(name: str) -> _Constant:
    return _Constant(name)


def _parse_int(digits: str) -> int | _LongInteger:
    # Checking the length first also keeps int() away from Python's limit on digit strings.
    if len(digits.lstrip("-")) > _MAX_INTEGER_DIGITS:
        return _LongInteger()
    return int(digits)


def _is_noncharacter(character: str) -> bool:
    point = ord(character)
    return 0xFDD0 <= point <= 0xFDEF or point & 0xFFFE == 0xFFFE


def is_text(value: str) -> bool:
    """Unicode text: no lone surrogates and no noncharacters (RFC 7493 §2.1)."""
    if value.isascii():
        return True
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return not any(_is_noncharacter(character) for character in value)


def _out_of_range(path: list[str | int]) -> JsonError:
    return JsonError(
        "INTEGER_OUT_OF_RANGE",
        pointer(path),
        "Numbers beyond ±(2^53 - 1) are refused; write such an integer as a decimal string",
    )


def escaped_length(key: str) -> int:
    """The length of a key as a JSON Pointer token: ``~`` and ``/`` take two characters."""
    return len(key) + key.count("~") + key.count("/")


def _build(value: object, path: list[str | int], count: list[int], length: int = 0) -> JsonValue:
    """The value as JSON reads it; ``length`` is that of the pointer to it, ``pointer(path)``.

    ``count`` holds the values read so far and the length of their pointers together.
    """
    count[0] += 1
    count[1] += length
    if count[1] > MAX_POINTERS:
        raise JsonError(
            "LIMIT_EXCEEDED",
            pointer(path),
            f"The paths to all the values may have at most {MAX_POINTERS} characters "
            "together; shorten the long keys above many values",
            (ALL_POINTER_CHARACTERS, MAX_POINTERS),
        )
    if count[0] > MAX_VALUES:
        raise JsonError(
            "LIMIT_EXCEEDED",
            pointer(path),
            f"The text may hold at most {MAX_VALUES} JSON values",
            (JSON_VALUES, MAX_VALUES),
        )
    if isinstance(value, _Pairs | list) and len(path) >= MAX_DEPTH:
        raise JsonError(
            "LIMIT_EXCEEDED",
            pointer(path),
            f"Arrays and objects may be nested at most {MAX_DEPTH} deep",
            (NESTING_DEPTH, MAX_DEPTH),
        )
    if isinstance(value, _Pairs):
        result: dict[str, JsonValue] = {}
        for key, member in value:
            if not is_text(key):
                raise JsonError(
                    "INVALID_JSON",
                    pointer(path),
                    "A key in this object is not Unicode text (a lone surrogate or a noncharacter)",
                )
            member_length = length + 1 + escaped_length(key)
            if member_length > MAX_POINTER:
                raise JsonError(
                    "LIMIT_EXCEEDED",
                    pointer(path),
                    f"A key in this object makes the path to a value longer than {MAX_POINTER} "
                    "characters",
                    (POINTER_CHARACTERS, MAX_POINTER),
                )
            if key in result:
                raise JsonError(
                    "DUPLICATE_KEY", pointer([*path, key]), "Duplicate key in a JSON object"
                )
            result[key] = _build(member, [*path, key], count, member_length)
        return result
    if isinstance(value, list):
        items: list[object] = value  # pyright: ignore[reportUnknownVariableType]
        if items and length + 1 + len(str(len(items) - 1)) > MAX_POINTER:
            raise JsonError(
                "LIMIT_EXCEEDED",
                pointer(path),
                f"The path to a value in this array is longer than {MAX_POINTER} characters",
                (POINTER_CHARACTERS, MAX_POINTER),
            )
        return [
            _build(item, [*path, index], count, length + 1 + len(str(index)))
            for index, item in enumerate(items)
        ]
    if isinstance(value, str):
        if not is_text(value):
            raise JsonError(
                "INVALID_JSON",
                pointer(path),
                "The string is not Unicode text (a lone surrogate or a noncharacter)",
            )
        return value
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, int):
        if abs(value) > MAX_SAFE_INTEGER:
            raise _out_of_range(path)
        return value
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise _out_of_range(path)  # a finite literal too large for a double
        if value.is_integer():
            if abs(value) > MAX_SAFE_INTEGER:
                raise _out_of_range(path)
            return int(value)
        return value
    if isinstance(value, _LongInteger):
        raise _out_of_range(path)
    if isinstance(value, _Constant):
        raise JsonError("NON_FINITE_NUMBER", pointer(path), f"{value.name} is not a JSON number")
    raise TypeError(f"unexpected JSON value {value!r}")  # pragma: no cover


def json_value(value: object) -> JsonValue:
    """A Python value as JSON reads it back: integral floats become integers (SPEC §5.1).

    Raises ``JsonError``, with a pointer into the value, for what JSON cannot carry unchanged:
    a non-finite number, a number beyond ±(2^53 - 1), a string or key that is not Unicode text,
    nesting deeper than ``MAX_DEPTH``, or a value of another type. Values from ``parse_json``
    pass unchanged.
    """
    return _checked(value, [])


def _checked(value: object, path: list[str | int]) -> JsonValue:
    if isinstance(value, dict | list) and len(path) >= MAX_DEPTH:
        raise JsonError(
            "LIMIT_EXCEEDED",
            pointer(path),
            f"Arrays and objects may be nested at most {MAX_DEPTH} deep",
            (NESTING_DEPTH, MAX_DEPTH),
        )
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, str):
        if not is_text(value):
            raise JsonError("INVALID_VALUE", pointer(path), "Strings must be Unicode text")
        return value
    if isinstance(value, int):
        if abs(value) > MAX_SAFE_INTEGER:
            raise _out_of_range(path)
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise JsonError("NON_FINITE_NUMBER", pointer(path), "The number is not finite")
        if abs(value) > MAX_SAFE_INTEGER:
            raise _out_of_range(path)
        return int(value) if value.is_integer() else value
    if isinstance(value, list):
        items = cast(list[object], value)
        return [_checked(item, [*path, index]) for index, item in enumerate(items)]
    if isinstance(value, dict):
        members = cast(dict[object, object], value)
        result: dict[str, JsonValue] = {}
        for key, member in members.items():
            if not isinstance(key, str) or not is_text(key):
                raise JsonError("INVALID_VALUE", pointer(path), "Keys must be Unicode text")
            result[key] = _checked(member, [*path, key])
        return result
    raise JsonError("WRONG_TYPE", pointer(path), "Not a JSON value")


def parse_json(source: str | bytes) -> JsonValue:
    """Parse a JSON text under the rules above. Raises ``JsonError``."""
    if isinstance(source, bytes):
        try:
            text = source.decode("utf-8")
        except UnicodeDecodeError as error:
            raise JsonError("INVALID_JSON", None, f"The text is not UTF-8: {error}") from error
    else:
        text = source
    if text.startswith("﻿"):
        raise JsonError("INVALID_JSON", None, "The text must not start with a byte order mark")
    try:
        raw: object = json.loads(
            text,
            object_pairs_hook=_keep_pairs,
            parse_constant=_keep_constant,
            parse_int=_parse_int,
        )
    except RecursionError:
        raise JsonError(
            "LIMIT_EXCEEDED",
            None,
            f"Arrays and objects may be nested at most {MAX_DEPTH} deep",
            (NESTING_DEPTH, MAX_DEPTH),
        ) from None
    except ValueError as error:
        raise JsonError("INVALID_JSON", None, f"Not valid JSON: {error}") from error
    return _build(raw, [], [0, 0])

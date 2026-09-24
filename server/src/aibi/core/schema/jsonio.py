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

``canonical`` writes a value as RFC 8785 (the JSON Canonicalization Scheme) does: members sorted
by their keys' UTF-16 code units, no whitespace, strings escaped as JSON requires and no more,
and numbers as ECMAScript writes doubles (``number_text``). Manifests are hashed over it, and so
are the canonical forms of M2.
"""

import json
import math
import re
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


def utf16_key(value: str) -> bytes:
    """A sort key that orders strings by UTF-16 code units, as RFC 8785 orders keys."""
    return value.encode("utf-16-be", "surrogatepass")


def escape_token(token: str | int) -> str:
    return str(token).replace("~", "~0").replace("/", "~1")


def pointer(tokens: Sequence[str | int]) -> str:
    """The RFC 6901 JSON Pointer for a sequence of object keys and array indices."""
    return "".join("/" + escape_token(token) for token in tokens)


_MISSING = object()


_ESCAPE = re.compile(r"~(?![01])")


def lookup(value: JsonValue, path: str) -> object:
    """The value a JSON Pointer names, or ``MISSING`` when it names nothing, as for a string
    that is no pointer (one not starting with ``/``, or with a ``~`` not followed by 0 or 1)."""
    if path == "":
        return value
    if not path.startswith("/") or _ESCAPE.search(path):
        return MISSING
    current: object = value
    for raw in path[1:].split("/"):
        token = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict) and token in current:
            current = cast(dict[str, object], current)[token]
        elif (
            isinstance(current, list)
            and token.isascii()
            and token.isdigit()
            and (token == "0" or token[0] != "0")
            and len(token) <= _MAX_INTEGER_DIGITS  # longer is no index, and int() has a limit
        ):
            items = cast(list[object], current)
            if int(token) >= len(items):
                return MISSING
            current = items[int(token)]
        else:
            return MISSING
    return current


MISSING = _MISSING
"""Returned by ``lookup`` for a pointer that names nothing."""


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


def number_text(value: int | float) -> str:
    """A number as RFC 8785 writes it: ECMAScript's shortest round-trip form of the double.

    Integers are written in decimal; ``-0.0`` is ``0``. Raises ``ValueError`` for a non-finite
    number, which JSON cannot carry.
    """
    if isinstance(value, int):
        return str(value)
    if not math.isfinite(value):
        raise ValueError(f"{value!r} is not a JSON number")
    if value == 0.0:
        return "0"
    sign = "-" if value < 0 else ""
    # repr gives the shortest digits that read back as the same double, as ECMAScript requires.
    mantissa, _, exponent = repr(abs(value)).partition("e")
    whole, _, fraction = mantissa.partition(".")
    digits = whole + fraction
    point = len(whole) + (int(exponent) if exponent else 0)
    significant = digits.lstrip("0")
    point -= len(digits) - len(significant)
    significant = significant.rstrip("0")
    count = len(significant)
    if count <= point <= 21:
        return sign + significant + "0" * (point - count)
    if 0 < point <= 21:
        return sign + significant[:point] + "." + significant[point:]
    if -6 < point <= 0:
        return sign + "0." + "0" * -point + significant
    power = point - 1
    written = ("+" if power >= 0 else "-") + str(abs(power))
    if count == 1:
        return f"{sign}{significant}e{written}"
    return f"{sign}{significant[0]}.{significant[1:]}e{written}"


def canonical(value: JsonValue) -> bytes:
    """``value`` in RFC 8785 form, as UTF-8.

    Raises ``JsonError``, with a pointer, for what JSON cannot carry unchanged: a non-finite
    number, an integer beyond ±(2^53 - 1) (write it as a decimal string), a string that is not
    UTF-8 (a lone surrogate), a key that is not a string, nesting deeper than ``MAX_DEPTH``, or
    a value of another type. A ``float`` of any finite magnitude is written, as RFC 8785 writes
    every double (``1e30`` is its own example), though ``json_value`` and ``parse_json`` refuse
    those beyond ±(2^53 - 1) when they read a document: an ``int`` beyond that range is refused
    here because no double holds it, while such a ``float`` is already a double. It does not
    refuse noncharacters, which JSON carries; values that must be I-JSON (RFC 7493), as the
    canonical forms of documents are, are checked where they are made.
    """
    parts: list[str] = []
    _canonical(value, parts, [])
    try:
        return "".join(parts).encode("utf-8")
    except UnicodeEncodeError as error:  # a lone surrogate, found where it is
        raise _surrogate(value, []) or error from None


def _canonical(value: object, parts: list[str], path: list[str | int]) -> None:
    if value is None:
        parts.append("null")
    elif value is True:
        parts.append("true")
    elif value is False:
        parts.append("false")
    elif isinstance(value, str):
        parts.append(json.dumps(value, ensure_ascii=False))
    elif isinstance(value, int):
        if abs(value) > MAX_SAFE_INTEGER:
            raise _out_of_range(path)
        parts.append(str(value))
    elif isinstance(value, float):
        if not math.isfinite(value):
            raise JsonError("NON_FINITE_NUMBER", pointer(path), "The number is not finite")
        parts.append(number_text(value))
    elif isinstance(value, list | dict):
        if len(path) >= MAX_DEPTH:
            raise JsonError(
                "LIMIT_EXCEEDED",
                pointer(path),
                f"Arrays and objects may be nested at most {MAX_DEPTH} deep",
                (NESTING_DEPTH, MAX_DEPTH),
            )
        if isinstance(value, list):
            parts.append("[")
            for index, item in enumerate(cast(list[object], value)):
                if index:
                    parts.append(",")
                _canonical(item, parts, [*path, index])
            parts.append("]")
        else:
            members = cast(dict[object, object], value)
            for key in members:
                if not isinstance(key, str):
                    raise JsonError("INVALID_VALUE", pointer(path), "Keys must be strings")
            parts.append("{")
            for index, key in enumerate(sorted(cast(dict[str, object], members), key=utf16_key)):
                if index:
                    parts.append(",")
                parts.append(json.dumps(key, ensure_ascii=False))
                parts.append(":")
                _canonical(members[key], parts, [*path, key])
            parts.append("}")
    else:
        raise JsonError("WRONG_TYPE", pointer(path), "Not a JSON value")


def _surrogate(value: object, path: list[str | int]) -> JsonError | None:
    """The first string or key holding a lone surrogate, as a refusal with its pointer."""
    if isinstance(value, str):
        return None if _encodes(value) else _not_utf8(path)
    if isinstance(value, list):
        for index, item in enumerate(cast(list[object], value)):
            found = _surrogate(item, [*path, index])
            if found is not None:
                return found
    if isinstance(value, dict):
        for key, member in cast(dict[str, object], value).items():
            if not _encodes(key):
                return _not_utf8(path)
            found = _surrogate(member, [*path, key])
            if found is not None:
                return found
    return None


def _encodes(value: str) -> bool:
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _not_utf8(path: list[str | int]) -> JsonError:
    return JsonError("INVALID_VALUE", pointer(path), "A string holds a lone surrogate")

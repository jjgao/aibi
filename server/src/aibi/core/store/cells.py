"""Typed cells from source values: missing codes, states and the column's datatype (SPEC §5.4,
§6.2, §12.2).

A cell's state comes first from its canonical string form: a declared missing code gives that
code's state; a null or empty cell without one is UNKNOWN; a non-finite number or an error cell
is never PRESENT, so it is UNKNOWN too. Any other cell is PRESENT with a value of the column's
datatype, or UNKNOWN when it has none: a token that does not parse is an undeclared missing
code (§5.1), and the column counts it (``unparsed``) for the curation queue.

A value already of the column's type is taken as it is (a number for a number column, a
``date`` for a date column); any other value is read from its canonical string:

- numbers: ``[+-]?(digits[.digits] | .digits)([eE][+-]?digits)?`` in ASCII digits, finite;
- integers: numbers whose decimal value is exactly integral (``1.0000000000000001`` is not),
  within the 64-bit range, and within ±(2^53 − 1) when written with a fraction or an exponent
  or given as a double, as a double holds integers exactly only in that range; an exponent of
  more digits than a decimal holds (``0e99999999999999999999``) is no integer;
- booleans: ``true``, ``t``, ``yes``, ``y``, ``1`` and ``false``, ``f``, ``no``, ``n``, ``0``, in
  any ASCII case;
- dates: ``YYYY-MM-DD``; a datetime at midnight, without an offset, is its date;
- datetimes: RFC 3339, with ``T``, ``t`` or a space between date and time, fractions of a
  second truncated to microseconds, and ``Z`` or ``±HH:MM``; without an offset the time is
  UTC (§12.2); a full date is its midnight, UTC. Datetimes are held in UTC;
- strings and categories: the canonical string exactly, and so for a column whose datatype is
  undeclared;
- ``list<category>``: the cell's string parsed by the column's ``list_syntax``, each item then
  taking its state as a cell does (``""`` and null items are UNKNOWN); a list that does not
  parse, or holds anything but scalars (a string with a lone surrogate escape among them),
  leaves the cell UNKNOWN.

Numbers, booleans, dates and datetimes may be surrounded by ASCII whitespace; strings,
categories and list items are taken exactly.
"""

import ast
import json
import math
import re
from collections import Counter
from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import cast

from aibi.core.engine.data import EMPTY, PRESENT, Cell, Value
from aibi.core.schema.descriptors import ListSyntax
from aibi.core.schema.ids import MAX_SAFE_INTEGER
from aibi.core.schema.semantics import ObservationState
from aibi.core.store.sources import ErrorCell, SourceValue, canonical_string

MAX_LIST_TEXT = 1_000_000
"""Characters of a JSON or Python list cell that are parsed at most; a longer one is UNKNOWN."""

_INT64 = 2**63
_SPACE = " \t\r\n\f\v"
_DIGITS = re.compile(r"[+-]?[0-9]+")
_NUMBER = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?")
_DAY = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_DATETIME = re.compile(
    r"([0-9]{4})-([0-9]{2})-([0-9]{2})[Tt ]([0-9]{2}):([0-9]{2}):([0-9]{2})(?:\.([0-9]{1,9}))?"
    r"(?:([Zz])|([+-])([0-9]{2}):([0-9]{2}))?"
)
_TRUE = frozenset({"true", "t", "yes", "y", "1"})
_FALSE = frozenset({"false", "f", "no", "n", "0"})
_MIDNIGHT = time(0)


def _integer(value: SourceValue, token: str) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if -_INT64 <= value < _INT64 else None
    if isinstance(value, float):
        return int(value) if value.is_integer() and abs(value) <= MAX_SAFE_INTEGER else None
    if not isinstance(value, str):
        return None
    text = token.strip(_SPACE)
    if _DIGITS.fullmatch(text):
        try:
            whole = int(text)
        except ValueError:  # more digits than Python converts
            return None
        return whole if -_INT64 <= whole < _INT64 else None
    if _NUMBER.fullmatch(text):
        try:
            number = Decimal(text)
            if number.copy_abs() <= MAX_SAFE_INTEGER and number == number.to_integral_value():
                return int(number)
        except InvalidOperation:  # an exponent beyond what a decimal holds
            return None
    return None


def _number(value: SourceValue, token: str) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        try:
            return float(value)
        except OverflowError:
            return None
    if isinstance(value, float):
        return value
    if not isinstance(value, str):
        return None
    text = token.strip(_SPACE)
    if not _NUMBER.fullmatch(text):
        return None
    number = float(text)
    return number if math.isfinite(number) else None


def _boolean(value: SourceValue, token: str) -> bool | None:
    if isinstance(value, bool):
        return value
    text = token.strip(_SPACE)
    if not text.isascii():
        return None
    text = text.lower()
    if text in _TRUE:
        return True
    if text in _FALSE:
        return False
    return None


def _date(value: SourceValue, token: str) -> date | None:
    if isinstance(value, datetime):
        if value.utcoffset() is None and value.time() == _MIDNIGHT:
            return value.date()
        return None
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        return None
    return _day(token.strip(_SPACE))


def _day(text: str) -> date | None:
    if not _DAY.fullmatch(text):
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _datetime(value: SourceValue, token: str) -> datetime | None:
    try:
        if isinstance(value, datetime):
            if value.utcoffset() is None:
                return value.replace(tzinfo=UTC)
            return value.astimezone(UTC)
        if isinstance(value, date):
            return datetime(value.year, value.month, value.day, tzinfo=UTC)
        if not isinstance(value, str):
            return None
        text = token.strip(_SPACE)
        day = _day(text)
        if day is not None:
            return datetime(day.year, day.month, day.day, tzinfo=UTC)
        found = _DATETIME.fullmatch(text)
        if found is None:
            return None
        year, month, mday, hour, minute, second = (int(found.group(i)) for i in range(1, 7))
        micro = int((found.group(7) or "").ljust(6, "0")[:6])
        offset = timedelta(0)
        if found.group(9):
            hours, minutes = int(found.group(10)), int(found.group(11))
            if hours > 23 or minutes > 59:
                return None
            offset = timedelta(hours=hours, minutes=minutes)
            if found.group(9) == "-":
                offset = -offset
        written = datetime(year, month, mday, hour, minute, second, micro, timezone(offset))
        return written.astimezone(UTC)
    except (ValueError, OverflowError):  # no such day or time, or beyond the years Python holds
        return None


def _text(value: SourceValue, token: str) -> str:
    return token


Converter = Callable[[SourceValue, str], Value | None]

_CONVERTERS: dict[str | None, Converter] = {
    "integer": _integer,
    "number": _number,
    "time_offset": _number,
    "boolean": _boolean,
    "date": _date,
    "datetime": _datetime,
    "string": _text,
    "category": _text,
    None: _text,
}


def _scalar_item(item: object) -> str | bool | None:
    """A list item as a token: text, a scalar's canonical string, ``None`` for null, or
    ``False`` for anything else (a nested list or object, or a string with a lone surrogate,
    which an escape can write but UTF-8 cannot carry)."""
    if item is None:
        return None
    if isinstance(item, str):
        return item if _encodes(item) else False
    if isinstance(item, bool | int):
        return canonical_string(item)
    if isinstance(item, float) and math.isfinite(item):
        return canonical_string(item)
    return False


def _items(token: str, syntax: ListSyntax | None) -> list[str | None] | None:
    """A list cell's items, or ``None`` when it does not parse as a list of scalars."""
    if syntax is None:
        return None
    if syntax.format == "delimited":
        return cast(list[str | None], token.split(cast(str, syntax.delimiter)))
    if len(token) > MAX_LIST_TEXT:
        return None
    parsed: object
    try:
        if syntax.format == "json":
            parsed = json.loads(token, parse_constant=_no_constant)
        else:
            parsed = ast.literal_eval(token)
    except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
        return None
    if not isinstance(parsed, list | tuple):
        return None
    found: list[str | None] = []
    for item in cast(list[object] | tuple[object, ...], parsed):
        scalar = _scalar_item(item)
        if scalar is False:
            return None
        found.append(cast(str | None, scalar))
    return found


def _encodes(text: str) -> bool:
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _no_constant(name: str) -> object:
    raise ValueError(f"{name} is not a JSON number")


class ColumnCells:
    """Turns one column's source values into cells, counting states and unparsed tokens."""

    def __init__(
        self,
        datatype: str | None,
        missing_codes: Mapping[str, str] | None,
        list_syntax: ListSyntax | None = None,
    ) -> None:
        self.datatype = datatype
        self.codes = {
            token: ObservationState(state) for token, state in (missing_codes or {}).items()
        }
        self.list_syntax = list_syntax
        self.is_list = datatype == "list<category>"
        self._convert = _CONVERTERS.get(datatype, _text)
        self.states: Counter[ObservationState] = Counter()
        """Cells by state."""
        self.item_states: Counter[ObservationState] = Counter()
        """For a list column, items by state."""
        self.unparsed: Counter[str] = Counter()
        """Tokens that are no value of the datatype, nor a declared code: UNKNOWN (§5.1)."""

    def cell(self, value: SourceValue) -> Cell:
        cell = self._cell(value)
        self.states[cell.state] += 1
        return cell

    def _cell(self, value: SourceValue) -> Cell:
        token = canonical_string(value)
        if token is None:
            return EMPTY
        coded = self.codes.get(token)
        if coded is not None:
            return Cell(coded)
        if token == "":
            return EMPTY
        if isinstance(value, ErrorCell) or (isinstance(value, float) and not math.isfinite(value)):
            self.unparsed[token] += 1
            return EMPTY
        if self.is_list:
            return self._list(token)
        converted = self._convert(value, token)
        if converted is None:
            self.unparsed[token] += 1
            return EMPTY
        return Cell(PRESENT, converted)

    def _list(self, token: str) -> Cell:
        items = _items(token, self.list_syntax)
        if items is None:
            self.unparsed[token] += 1
            return EMPTY
        cells: list[Cell] = []
        for item in items:
            coded = None if item is None else self.codes.get(item)
            if coded is not None:
                cells.append(Cell(coded))
            elif item is None or item == "":
                cells.append(EMPTY)
            else:
                cells.append(Cell(PRESENT, item))
        for cell in cells:
            self.item_states[cell.state] += 1
        return Cell(PRESENT, tuple(cells))


__all__ = ["MAX_LIST_TEXT", "ColumnCells"]

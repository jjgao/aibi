"""Raw snapshots: source tables as imported, before parsing (SPEC §12.2).

A text file's raw snapshot is its bytes; the parse settings that read it are fields of its table
descriptor (§5.3). A typed source's (a spreadsheet, a Parquet file, a database table) is its
column names and its rows of source-typed values: ``None``, ``bool``, ``int``, ``float``
(non-finite included), ``str``, ``date``, ``datetime`` (with an offset or without one) and
spreadsheet error cells. An importer turns every other source type into one of these.

A typed snapshot is stored as JSON Lines (``encode``): a first line
``{"columns": [...], "format": "aibi.rows/1"}``, then one array per row, each line written as RFC
8785 writes it. Plain JSON carries nulls, booleans, strings, integers within ±(2^53 − 1) and
finite numbers; a tagged object carries each other value: ``{"int": "<decimal>"}``,
``{"float": "NaN" | "Infinity" | "-Infinity"}``, ``{"date": "YYYY-MM-DD"}``,
``{"datetime": "<ISO 8601>"}`` and ``{"error": "<text>"}``. A number is a value, not a spelling,
as everywhere (§5.1): ``-99.0`` is written, and read back, as ``-99``.

Missing codes are matched against each cell's canonical string form (``canonical_string``).
``parse`` reads a raw snapshot into its header names and rows: a text file's values are the
strings it holds, a typed source's are its own.
"""

import csv
import io
import json
import math
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Literal, cast

from aibi.core.schema.descriptors import ParseSettings
from aibi.core.schema.ids import MAX_SAFE_INTEGER
from aibi.core.schema.jsonio import JsonError, canonical, number_text


@dataclass(frozen=True, slots=True)
class ErrorCell:
    """A spreadsheet error cell, by its error text (e.g. ``#N/A``); never PRESENT (§12.2)."""

    text: str


SourceValue = None | bool | int | float | str | date | datetime | ErrorCell
"""A value as a typed source holds it; a ``datetime`` may have an offset or not."""

Kind = Literal["text", "rows"]
ROWS_FORMAT = "aibi.rows/1"
FIELD_CHARACTERS = 131_072
"""The most characters a cell holds: a field of a text file (the ``csv`` module's field limit,
which ``parse_text`` reads with) and a typed source's string (``long_cell``)."""


class SourceError(ValueError):
    """A raw snapshot that cannot be read: an unparseable file (§13.2) or an invalid value."""


class TooManyCells(SourceError):  # noqa: N818 - a SourceError, named as what it says
    """A text file with more cells than it may have, found while it is parsed."""

    def __init__(self, limit: int) -> None:
        super().__init__(f"more than {limit} cells")
        self.limit = limit


@dataclass(frozen=True)
class TextSource:
    """A text file as uploaded."""

    data: bytes

    @property
    def kind(self) -> Kind:
        return "text"


@dataclass(frozen=True)
class TypedSource:
    """A typed source: its column names, in source order, and its rows."""

    columns: tuple[str, ...]
    rows: tuple[tuple[SourceValue, ...], ...]

    @property
    def kind(self) -> Kind:
        return "rows"

    def __post_init__(self) -> None:
        width = len(self.columns)
        for index, row in enumerate(self.rows):
            if len(row) != width:
                raise SourceError(f"row {index} has {len(row)} values; there are {width} columns")
            for value in row:
                _check_value(value, index)


RawSource = TextSource | TypedSource


@dataclass(frozen=True)
class Parsed:
    """A source table as read: its header names and its rows of values, in source order."""

    names: tuple[str, ...]
    rows: Sequence[Sequence[SourceValue]]


def long_cell(rows: Sequence[Sequence[object]]) -> tuple[int, int] | None:
    """The row and column, from 1, of the first string of ``rows`` over ``FIELD_CHARACTERS``
    characters, if there is one: a typed source has no cell a text file could not hold."""
    for index, row in enumerate(rows):
        for column, value in enumerate(row):
            if isinstance(value, str) and len(value) > FIELD_CHARACTERS:
                return index + 1, column + 1
    return None


def _check_value(value: object, row: int) -> None:
    if value is None or isinstance(value, bool | int | float | str | ErrorCell):
        return
    if isinstance(value, datetime):
        offset = value.utcoffset()
        if offset is not None and offset % timedelta(minutes=1):
            raise SourceError(f"row {row}: a datetime's offset is whole minutes, not {offset}")
        return
    if isinstance(value, date):
        return
    raise SourceError(f"row {row}: not a source value: {type(value).__name__}")


# --- Canonical string form (§12.2) ------------------------------------------------------------


def canonical_string(value: SourceValue) -> str | None:
    """The string a cell's missing codes are matched against; ``None`` for a null cell.

    Text as it is; integers in decimal; other finite numbers as RFC 8785 writes them (so -99.0
    is ``-99``); non-finite numbers as ``NaN``, ``Infinity`` and ``-Infinity``; booleans as
    ``true`` and ``false``; dates as RFC 3339 full dates; datetimes in RFC 3339 with their
    offset or, without one, in ISO 8601 without one; error cells as their error text.
    """
    if value is None or isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if math.isnan(value):
            return "NaN"
        if math.isinf(value):
            return "Infinity" if value > 0 else "-Infinity"
        return number_text(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    return value.text


# --- Typed snapshots as JSON Lines -----------------------------------------------------------


def encode(source: RawSource) -> bytes:
    """The bytes stored for a raw snapshot."""
    if isinstance(source, TextSource):
        return source.data
    try:
        header = canonical({"columns": list(source.columns), "format": ROWS_FORMAT}).decode()
    except JsonError:
        raise SourceError(
            "a column name holds a lone surrogate, which UTF-8 cannot carry"
        ) from None
    # Each line is encoded as it is written, so that the rows' text is held once more in UTF-8
    # and never as one Python string, whose every character is as wide as its widest.
    lines = [header.encode("utf-8")]
    try:
        for row in source.rows:
            lines.append(("[" + ",".join(_written(value) for value in row) + "]").encode("utf-8"))
    except UnicodeEncodeError:
        raise SourceError("a string holds a lone surrogate, which UTF-8 cannot carry") from None
    lines.append(b"")
    return b"\n".join(lines)


_NON_FINITE = {"NaN": math.nan, "Infinity": math.inf, "-Infinity": -math.inf}
_INTEGER = re.compile(r"-?[0-9]+")


def _written(value: SourceValue) -> str:
    if value is None:
        return "null"
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value) if abs(value) <= MAX_SAFE_INTEGER else f'{{"int":"{value}"}}'
    if isinstance(value, float):
        if math.isfinite(value):
            return number_text(value)
        return f'{{"float":"{canonical_string(value)}"}}'
    if isinstance(value, datetime):
        return f'{{"datetime":"{value.isoformat()}"}}'
    if isinstance(value, date):
        return f'{{"date":"{value.isoformat()}"}}'
    return '{"error":' + json.dumps(value.text, ensure_ascii=False) + "}"


def decode(kind: Kind, data: bytes) -> RawSource:
    """A raw snapshot from its stored bytes; ``SourceError`` for bytes ``encode`` does not write.

    The blob's hash is checked when it is read, so this guards against damage and against bytes
    stored by another version, not against a forger who can also rename the blob.
    """
    if kind == "text":
        return TextSource(data)
    try:
        lines = data.decode("utf-8").split("\n")
        if len(lines) < 2 or lines[-1] != "":
            raise SourceError("a typed snapshot is JSON Lines with a header line")
        header = json.loads(lines[0])
        columns = (
            cast(dict[str, object], header).get("columns") if isinstance(header, dict) else None
        )
        if (
            not isinstance(header, dict)
            or cast(dict[str, object], header).get("format") != ROWS_FORMAT
            or not isinstance(columns, list)
            or not all(isinstance(name, str) for name in cast(list[object], columns))
        ):
            raise SourceError(f"a typed snapshot starts with its {ROWS_FORMAT} header")
        names = tuple(cast(list[str], columns))
        rows = tuple(tuple(_read(value) for value in _row(line)) for line in lines[1:-1])
    except (ValueError, KeyError, TypeError, AttributeError) as error:
        if isinstance(error, SourceError):
            raise
        raise SourceError(f"a damaged typed snapshot: {error!r}"[:200]) from None
    return TypedSource(names, rows)


def _row(line: str) -> list[object]:
    row = json.loads(line)
    if not isinstance(row, list):
        raise SourceError("each row of a typed snapshot is an array")
    return cast(list[object], row)


def _read(value: object) -> SourceValue:
    if not isinstance(value, dict):
        return cast(SourceValue, value)
    [(tag, text)] = cast(dict[str, object], value).items()
    if not isinstance(text, str):
        raise SourceError(f"a tag in a typed snapshot holds a string: {tag}")
    if tag == "int":
        if not _INTEGER.fullmatch(text):
            raise SourceError("an int tag holds an integer in ASCII decimal")
        return int(text)
    if tag == "float":
        return _NON_FINITE[text]
    if tag == "date":
        return date.fromisoformat(text)
    if tag == "datetime":
        return datetime.fromisoformat(text)
    if tag == "error":
        return ErrorCell(text)
    raise SourceError(f"unknown tag in a typed snapshot: {tag}")


# --- Reading a raw snapshot --------------------------------------------------------------------

_LINE_END = re.compile(r"\r\n|\n|\r")


def parse(source: RawSource, settings: ParseSettings | None) -> Parsed:
    """The header names and rows of a raw snapshot; a text file is read with ``settings``.

    Raises ``SourceError`` for a file that cannot be read with them (§13.2): see ``parse_text``.
    """
    if isinstance(source, TypedSource):
        if settings is not None:
            raise SourceError("Parse settings are for text files only")
        return Parsed(source.columns, source.rows)
    if settings is None:
        raise SourceError("A text file is read with parse settings")
    return parse_text(source.data, settings)


def parse_text(data: bytes, settings: ParseSettings, *, max_cells: int | None = None) -> Parsed:
    """A delimited text file read with its parse settings (§5.3).

    The bytes are decoded strictly in the named encoding. ``skip_rows`` lines (ended by CRLF, LF
    or CR) are skipped; ``header_row`` then counts records, and the records before the header are
    left out. Blank lines are no records, so in a file of one column an empty value on a line of
    its own is no row either. Fields are quoted with ``quote``, which a quoted field doubles to
    hold it; a quote inside an unquoted field (``x"y``) is part of it. A record with more or fewer
    fields than the header, a quoted field followed by anything but a delimiter or the end of
    the record, a field over 131,072 characters, or a file with no header row cannot be read,
    and the error names the line. With ``utf-8`` a byte order mark is the start of the first
    field, as the bytes say; ``utf-8-sig`` reads past one. Given ``max_cells``, a file whose
    header and rows have more cells (columns times rows, at least one row) raises
    ``TooManyCells`` as soon as the parse passes it.
    """
    delimiter, quote = settings.delimiter, settings.quote
    if delimiter == quote:
        raise SourceError("The delimiter and the quote are different characters")
    if {delimiter, quote} & {"\r", "\n"}:
        raise SourceError("Neither the delimiter nor the quote is a line break")
    try:
        text = data.decode(settings.encoding)
    except LookupError:
        raise SourceError(f"Not a text encoding: {settings.encoding}") from None
    except UnicodeDecodeError as error:
        line = data.count(b"\n", 0, error.start) + 1
        raise SourceError(
            f"line {line}: the bytes are not {settings.encoding} ({error.reason})"
        ) from None
    start, skipped = 0, 0
    while skipped < settings.skip_rows:
        found = _LINE_END.search(text, start)
        if found is None:
            start = len(text)
            break
        start, skipped = found.end(), skipped + 1
    reader = csv.reader(
        io.StringIO(text[start:], newline=""),
        delimiter=delimiter,
        quotechar=quote,
        doublequote=True,
        strict=True,
    )
    header: tuple[str, ...] | None = None
    before = 0
    rows: list[tuple[str, ...]] = []
    try:
        for record in reader:
            if not record:
                continue
            if header is None:
                if before < settings.header_row:
                    before += 1
                    continue
                header = tuple(record)
            elif len(record) != len(header):
                raise SourceError(
                    f"line {skipped + reader.line_num}: {len(record)} fields, and the header "
                    f"has {len(header)}"
                )
            else:
                rows.append(tuple(record))
                if max_cells is not None and len(header) * len(rows) > max_cells:
                    raise TooManyCells(max_cells)
    except csv.Error as error:
        raise SourceError(f"line {skipped + reader.line_num}: {error}") from None
    if header is None:
        raise SourceError(
            f"No header row: {before} records after the {skipped} lines skipped, and the header "
            f"is record {settings.header_row}"
        )
    return Parsed(header, rows)


__all__ = [
    "FIELD_CHARACTERS",
    "ROWS_FORMAT",
    "ErrorCell",
    "Kind",
    "Parsed",
    "RawSource",
    "SourceError",
    "SourceValue",
    "TextSource",
    "TooManyCells",
    "TypedSource",
    "canonical_string",
    "decode",
    "encode",
    "long_cell",
    "parse",
    "parse_text",
]

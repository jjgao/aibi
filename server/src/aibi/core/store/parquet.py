# pyarrow's stubs leave some overloads partly unknown (they name numpy and pandas types, which
# are not installed); the types this module exposes are its own.
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
# pyright: reportUnknownArgumentType=false
"""Parquet files: the only module that calls pyarrow (SPEC §12.2, D218, D225).

A file of typed columns is written with fixed settings, so the same columns give the same bytes
under one version of pyarrow: format 2.6, data pages of version 1, zstd at level 3, dictionaries
and statistics on, and row groups of ``ROW_GROUP`` rows. Values are Python's, ``None`` for null.
Files are read on the calling thread: pyarrow's thread pool can abort the interpreter at exit.

``read_source`` reads a Parquet file an operator imports, from its bytes, as source values
(D225): integers of every width as ``int``; floats of 32 and 64 bits as ``float``; booleans;
strings, large strings and dictionaries of strings as ``str``; dates as ``date``; timestamps with
a time zone as datetimes with the offset in force (in UTC when it is not whole minutes), and
without one as naive datetimes, both truncated to microseconds; decimals as their exact decimal
text; times of day as their ISO text; lists of strings as the text of a JSON array; a column of
nulls as nulls. Any other type is refused, and the cells are counted from the file's metadata
before any is read. What the values decode to is not bounded by the file's bytes (one dictionary
string in every row, say), so an import calls it in its worker process (D225).
"""

import io
import json
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal, cast
from zoneinfo import ZoneInfoNotFoundError

import pyarrow as pa
import pyarrow.parquet as pq

PhysicalType = Literal["int64", "float64", "string", "bool", "date32", "timestamp", "strings"]
"""``timestamp`` is in microseconds, UTC; ``strings`` is a list of strings."""

ROW_GROUP = 1 << 20

_ARROW: dict[PhysicalType, pa.DataType] = {
    "int64": pa.int64(),
    "float64": pa.float64(),
    "string": pa.string(),
    "bool": pa.bool_(),
    "date32": pa.date32(),
    "timestamp": pa.timestamp("us", tz="UTC"),
    "strings": pa.list_(pa.string()),
}


@dataclass(frozen=True)
class Column:
    name: str
    type: PhysicalType
    values: Sequence[object]


def write(columns: Sequence[Column]) -> bytes:
    """The Parquet bytes of ``columns``, in order; each holds one value per row."""
    schema = pa.schema([pa.field(column.name, _ARROW[column.type]) for column in columns])
    arrays = [pa.array(list(column.values), type=_ARROW[column.type]) for column in columns]
    table = pa.Table.from_arrays(arrays, schema=schema)
    sink = io.BytesIO()
    pq.write_table(
        table,
        sink,
        version="2.6",
        data_page_version="1.0",
        compression="zstd",
        compression_level=3,
        use_dictionary=True,
        write_statistics=True,
        row_group_size=ROW_GROUP,
    )
    return sink.getvalue()


def read(source: Path | bytes) -> list[Column]:
    """The columns of a file ``write`` wrote, in order."""
    if isinstance(source, bytes):
        table = pq.read_table(io.BytesIO(source), use_threads=False)
    else:
        with open(source, "rb") as file:  # a file, never a dataset directory to discover
            table = pq.read_table(file, use_threads=False)
    found: list[Column] = []
    for field in table.schema:
        kind = _physical(field.type)
        values = cast(list[object], table.column(field.name).to_pylist())
        found.append(Column(field.name, kind, values))
    return found


def names(path: Path) -> tuple[str, ...]:
    """The columns a file stores, in order, from its footer alone."""
    with open(path, "rb") as file:  # a file, never a dataset directory to discover
        return tuple(pq.read_schema(file).names)


def read_columns(source: bytes, names: Collection[str]) -> tuple[int, list[Column]]:
    """The file's row count, and those of ``names`` it has, in the file's order; only they are
    read and decompressed."""
    file = pq.ParquetFile(pa.BufferReader(source))
    wanted = [field.name for field in file.schema_arrow if field.name in names]
    rows = file.metadata.num_rows
    if not wanted:
        return rows, []
    table = file.read(columns=wanted, use_threads=False)
    found: list[Column] = []
    for field in table.schema:
        values = cast(list[object], table.column(field.name).to_pylist())
        found.append(Column(field.name, _physical(field.type), values))
    return rows, found


def _physical(given: pa.DataType) -> PhysicalType:
    for kind, arrow in _ARROW.items():
        if given.equals(arrow):
            return kind
    raise ValueError(f"not a column type the store writes: {given}")


# --- Reading a source file (D225) ---------------------------------------------------------------

ArrowKind = Literal[
    "integer",
    "number",
    "boolean",
    "string",
    "date",
    "datetime",
    "naive_datetime",
    "decimal",
    "time",
    "strings",
    "null",
]
"""What a source column holds, as its Parquet type says."""

SUPPORTED_TYPES = (
    "int8–int64 and uint8–uint64",
    "float and double",
    "bool",
    "string, large_string and dictionary<string>",
    "date32 and date64",
    "timestamp, with or without a time zone",
    "decimal128 and decimal256",
    "time32 and time64",
    "list<string> and large_list<string>",
    "null",
)
"""The Parquet column types ``read_source`` reads, as a refusal lists them."""


class UnsupportedTypeError(ValueError):
    """A column of a type ``read_source`` does not read."""

    def __init__(self, column: int, arrow_type: str) -> None:
        super().__init__(f"column {column + 1} has the Parquet type {arrow_type}")
        self.column = column
        self.arrow_type = arrow_type


class CellLimitError(ValueError):
    def __init__(self, cells: int, limit: int) -> None:
        super().__init__(f"the file has {cells} cells; at most {limit} are imported")
        self.cells = cells
        self.limit = limit


class UnreadableParquetError(ValueError):
    """Bytes that are not a Parquet file pyarrow can read."""


@dataclass(frozen=True)
class ParquetSource:
    names: tuple[str, ...]
    kinds: tuple[ArrowKind, ...]
    rows: tuple[tuple[object, ...], ...]


def _text(given: pa.DataType) -> bool:
    return pa.types.is_string(given) or pa.types.is_large_string(given)


def _kind(given: pa.DataType) -> ArrowKind | None:
    types = pa.types
    if types.is_integer(given):
        return "integer"
    if types.is_float32(given) or types.is_float64(given):
        return "number"
    if types.is_boolean(given):
        return "boolean"
    if types.is_string(given) or types.is_large_string(given):
        return "string"
    if types.is_dictionary(given):
        return "string" if _text(given.value_type) else None
    if types.is_date(given):
        return "date"
    if types.is_timestamp(given):
        return "naive_datetime" if cast(pa.TimestampType, given).tz is None else "datetime"
    if types.is_decimal(given):
        return "decimal"
    if types.is_time(given):
        return "time"
    if types.is_list(given) or types.is_large_list(given):
        return "strings" if _text(cast("pa.ListType[Any]", given).value_type) else None
    if types.is_null(given):
        return "null"
    return None


def read_source(data: bytes, max_cells: int) -> ParquetSource:
    """A Parquet file's column names, kinds and rows of source values.

    Raises ``UnsupportedTypeError``, ``CellLimitError`` or ``UnreadableParquetError``: whatever
    else pyarrow or the conversion to Python raises on hostile bytes (a thrift header that does
    not deserialize, a timestamp beyond year 9999, an unknown time zone, invalid UTF-8) is an
    unreadable file, and so is one with two columns of the same name. ``MemoryError`` (pyarrow's
    ``ArrowMemoryError`` included) is raised as it is: the file is bounded by the memory of the
    process that reads it (D225)."""
    try:
        return _read_source(data, max_cells)
    except (UnsupportedTypeError, CellLimitError, MemoryError):
        raise
    except (pa.ArrowException, OSError, ValueError, OverflowError, ZoneInfoNotFoundError) as error:
        name = type(error).__name__
        raise UnreadableParquetError(f"reading it raised {name}") from None


def _read_source(data: bytes, max_cells: int) -> ParquetSource:
    file = pq.ParquetFile(pa.BufferReader(data))
    schema = file.schema_arrow
    names = tuple(field.name for field in schema)
    if len(set(names)) != len(names):
        raise UnreadableParquetError("two columns have the same name")
    cells = file.metadata.num_rows * len(schema)
    kinds: list[ArrowKind] = []
    for index, field in enumerate(schema):
        kind = _kind(field.type)
        if kind is None:
            raise UnsupportedTypeError(index, str(field.type))
        kinds.append(kind)
    if cells > max_cells:
        raise CellLimitError(cells, max_cells)
    table = file.read()
    columns: list[list[object]] = []
    for index, (field, kind) in enumerate(zip(table.schema, kinds, strict=True)):
        column = table.column(index)
        if kind in ("datetime", "naive_datetime"):
            tz = cast(pa.TimestampType, field.type).tz
            column = column.cast(pa.timestamp("us", tz=tz), safe=False)
        elif kind == "time":
            column = column.cast(pa.time64("us"), safe=False)
        values = cast(list[object], column.to_pylist())
        columns.append([_value(kind, value) for value in values])
    rows = tuple(zip(*columns, strict=True)) if columns else ()
    return ParquetSource(names, tuple(kinds), rows)


def _value(kind: ArrowKind, value: object) -> object:
    if value is None:
        return None
    if kind == "datetime" and isinstance(value, datetime):
        offset = value.utcoffset() or timedelta(0)
        if offset % timedelta(minutes=1):
            return value.astimezone(UTC)
        return value.astimezone(timezone(offset))
    if kind == "decimal" and isinstance(value, Decimal):
        return format(value, "f")
    if kind == "time" and isinstance(value, time):
        return value.isoformat()
    if kind == "strings" and isinstance(value, list):
        return json.dumps(cast(list[object], value), ensure_ascii=False)
    return value


__all__ = [
    "ROW_GROUP",
    "SUPPORTED_TYPES",
    "ArrowKind",
    "CellLimitError",
    "Column",
    "ParquetSource",
    "PhysicalType",
    "UnreadableParquetError",
    "UnsupportedTypeError",
    "names",
    "read",
    "read_columns",
    "read_source",
    "write",
]

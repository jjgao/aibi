# pyarrow's stubs leave some overloads partly unknown (they name numpy and pandas types, which
# are not installed); the types this module exposes are its own.
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
# pyright: reportUnknownArgumentType=false
"""Parquet files of typed columns: the only module that calls pyarrow (SPEC §12.2, D218).

A file is written with fixed settings, so the same columns give the same bytes under one
version of pyarrow: format 2.6, data pages of version 1, zstd at level 3, dictionaries and
statistics on, and row groups of ``ROW_GROUP`` rows. Values are Python's, ``None`` for null.
Files are read on the calling thread: pyarrow's thread pool can abort the interpreter at exit.
"""

import io
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

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


def _physical(given: pa.DataType) -> PhysicalType:
    for kind, arrow in _ARROW.items():
        if given.equals(arrow):
            return kind
    raise ValueError(f"not a column type the store writes: {given}")


__all__ = ["ROW_GROUP", "Column", "PhysicalType", "read", "write"]

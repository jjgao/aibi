"""Typed tables: built from a raw snapshot and the descriptors, stored as Parquet (SPEC §12.2).

A table's columns are its source columns, in source order, then its derived columns, by id.
Every column with a declared missing code or a cell that is not PRESENT has a companion
``<column>__state`` holding each cell's state, and its value column holds a value only where
the state is PRESENT. A list column's items have their states in ``<column>__item_state``, a list
beside each PRESENT list, under the same rule for items. Names with ``__`` are the system's
(§5.1), so a companion never meets a column id.

Physical types: ``integer`` int64; ``number`` and ``time_offset`` float64; ``boolean`` bool;
``date`` date32; ``datetime`` timestamp in microseconds, UTC; ``list<category>`` a list of
strings; ``string``, ``category`` and an undeclared datatype string. A derived column of
undeclared datatype is a number unless it maps values. Rows keep the source's order.
"""

from collections import Counter
from collections.abc import Collection, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from aibi.core.engine.data import PRESENT, Cell, Row, Table, Value
from aibi.core.schema.descriptors import ColumnFields
from aibi.core.schema.refusals import RefusalCode
from aibi.core.schema.semantics import ObservationState
from aibi.core.store import derive, parquet
from aibi.core.store.cells import ColumnCells
from aibi.core.store.sources import Parsed

STATE = "__state"
ITEM_STATE = "__item_state"
MAX_UNPARSED = 64
"""Distinct unparsed tokens a column's report keeps: the most frequent, then by token."""

_PHYSICAL: dict[str | None, parquet.PhysicalType] = {
    "integer": "int64",
    "number": "float64",
    "time_offset": "float64",
    "boolean": "bool",
    "date": "date32",
    "datetime": "timestamp",
    "list<category>": "strings",
    "string": "string",
    "category": "string",
    None: "string",
}


Problem = tuple[RefusalCode, str | None, tuple[str | int, ...], str]
"""A problem's code, its column (``None`` for the table), where it is in that column's
``fields``, and what it is."""


class TableError(ValueError):
    """What stops a table from being built."""

    def __init__(self, problems: Sequence[Problem]) -> None:
        super().__init__("; ".join(message for *_, message in problems))
        self.problems = tuple(problems)


@dataclass(frozen=True)
class ColumnReport:
    states: Mapping[ObservationState, int]
    item_states: Mapping[ObservationState, int]
    unparsed: tuple[tuple[str, int], ...]
    """Tokens that did not parse and were UNKNOWN, with their counts (at most ``MAX_UNPARSED``)."""
    unparsed_cells: int = 0
    """How many cells did not parse, whatever their tokens."""
    unparsed_rows: tuple[int, ...] = ()
    """The rows (from 0) of the first cells whose token did not parse (at most 5)."""


@dataclass(frozen=True)
class TypedTable:
    id: str
    columns: tuple[str, ...]
    """Value columns, in the stored order."""
    cells: Mapping[str, tuple[Cell, ...]]
    rows: int
    report: Mapping[str, ColumnReport]


def build_table(
    table: str,
    parsed: Parsed,
    layout: Sequence[str],
    columns: Mapping[str, ColumnFields],
) -> TypedTable:
    """The typed table ``table`` from its parsed source, whose position ``i`` holds column
    ``layout[i]``, and its columns' fields by column id. Raises ``TableError``."""
    changed = RefusalCode.COLUMNS_CHANGED
    if len(layout) != len(parsed.names):
        message = f"The source has {len(parsed.names)} columns, and {len(layout)} are laid out"
        raise TableError([(changed, None, (), message)])
    problems: list[Problem] = []
    for name in layout:
        fields = columns.get(name)
        if fields is None:
            problems.append((changed, None, (), f"The source column {name} has no descriptor"))
        elif fields.derived is not None:
            message = f"A source column is not derived: {name}"
            problems.append((RefusalCode.INVALID_VALUE, name, ("derived",), message))
    for name, fields in columns.items():
        if fields.derived is None and name not in layout:
            message = f"The column {name} is neither in the source nor derived"
            problems.append((changed, name, (), message))
        for code, where, message in derive.problems(fields, columns):
            problems.append((code, name, where, message))
    if problems:
        raise TableError(problems)
    cells: dict[str, tuple[Cell, ...]] = {}
    report: dict[str, ColumnReport] = {}
    for position, name in enumerate(layout):
        fields = columns[name]
        typer = ColumnCells(fields.datatype, fields.missing_codes, fields.list_syntax)
        cells[name] = tuple(typer.cell(row[position]) for row in parsed.rows)
        report[name] = ColumnReport(
            dict(typer.states),
            dict(typer.item_states),
            _most_frequent(typer.unparsed),
            typer.unparsed_cells,
            tuple(typer.unparsed_rows),
        )
    for name in derive.order(columns):
        computed = tuple(derive.compute(columns[name], cells, columns))
        cells[name] = computed
        report[name] = ColumnReport(dict(Counter(cell.state for cell in computed)), {}, ())
    derived = sorted(name for name, fields in columns.items() if fields.derived is not None)
    return TypedTable(table, (*layout, *derived), cells, len(parsed.rows), report)


def _most_frequent(counted: Counter[str]) -> tuple[tuple[str, int], ...]:
    ranked = sorted(counted.items(), key=lambda item: (-item[1], item[0]))
    return tuple(ranked[:MAX_UNPARSED])


def physical(fields: ColumnFields) -> parquet.PhysicalType:
    """The stored type of a column's values."""
    return _PHYSICAL[derive.stored_datatype(fields)]


def encode(table: TypedTable, columns: Mapping[str, ColumnFields]) -> bytes:
    """The table's Parquet bytes."""
    stored: list[parquet.Column] = []
    for name in table.columns:
        fields = columns[name]
        cells = table.cells[name]
        kind = physical(fields)
        coded = bool(fields.missing_codes)
        if kind == "strings":
            stored.append(parquet.Column(name, kind, [_items(cell) for cell in cells]))
        else:
            stored.append(parquet.Column(name, kind, [_value(cell) for cell in cells]))
        if coded or any(cell.state is not PRESENT for cell in cells):
            stored.append(
                parquet.Column(name + STATE, "string", [cell.state.value for cell in cells])
            )
        if kind == "strings" and (coded or _any_item_missing(cells)):
            stored.append(
                parquet.Column(name + ITEM_STATE, "strings", [_item_states(cell) for cell in cells])
            )
    return parquet.write(stored)


def _value(cell: Cell) -> object:
    return cell.value if cell.state is PRESENT else None


def _items(cell: Cell) -> list[object] | None:
    if cell.state is not PRESENT or not isinstance(cell.value, tuple):
        return None
    return [item.value if item.state is PRESENT else None for item in cell.value]


def _item_states(cell: Cell) -> list[object] | None:
    if cell.state is not PRESENT or not isinstance(cell.value, tuple):
        return None
    return [item.state.value for item in cell.value]


def _any_item_missing(cells: Sequence[Cell]) -> bool:
    return any(
        isinstance(cell.value, tuple) and any(item.state is not PRESENT for item in cell.value)
        for cell in cells
    )


class CorruptTableError(ValueError):
    """A table blob that is not what ``encode`` writes."""


def decode(table: str, source: Path | bytes) -> Table:
    """A table blob read back as the reference evaluator's table (§13.3)."""
    stored = {column.name: column for column in parquet.read(source)}
    names = [name for name in stored if "__" not in name]
    rows = len(stored[names[0]].values) if names else 0
    built: list[dict[str, Cell]] = [{} for _ in range(rows)]
    for name in names:
        for row, cell in enumerate(_cells(table, name, stored, rows)):
            built[row][name] = cell
    rows_built: tuple[Row, ...] = tuple(built)
    return Table(table, rows_built)


def decode_cells(table: str, data: bytes, columns: Collection[str]) -> TypedTable:
    """The cells of some columns of a table blob, by name, as the validation gate reads them
    (§13.2): only those columns and their companions are read. A name the table does not have
    is left out."""
    wanted = {name for column in columns for name in (column, column + STATE, column + ITEM_STATE)}
    rows, found = parquet.read_columns(data, wanted)
    stored = {column.name: column for column in found}
    names = tuple(name for name in stored if "__" not in name)
    cells = {name: tuple(_cells(table, name, stored, rows)) for name in names}
    return TypedTable(table, names, cells, rows, {})


def _cells(
    table: str, name: str, stored: Mapping[str, parquet.Column], rows: int
) -> Iterator[Cell]:
    values = stored[name].values
    states = stored.get(name + STATE)
    items = stored.get(name + ITEM_STATE)
    for row in range(rows):
        state = ObservationState(states.values[row]) if states is not None else PRESENT
        value = values[row]
        if state is not PRESENT:
            if value is not None:
                raise CorruptTableError(f"{table}.{name}[{row}] has a value but is {state}")
            yield Cell(state)
            continue
        if value is None:
            raise CorruptTableError(f"{table}.{name}[{row}] is PRESENT without a value")
        yield _cell(value, None if items is None else items.values[row])


def _cell(value: object, item_states: object) -> Cell:
    if isinstance(value, list):
        listed = cast(list[str | None], value)
        states = cast(list[str], item_states) if isinstance(item_states, list) else None
        if states is not None and len(states) != len(listed):
            raise CorruptTableError("a list and its item states differ in length")
        cells: list[Cell] = []
        for index, item in enumerate(listed):
            state = PRESENT if states is None else ObservationState(states[index])
            cells.append(Cell(state, item if state is PRESENT else None))
        return Cell(PRESENT, tuple(cells))
    if isinstance(value, datetime):
        return Cell(PRESENT, value.astimezone(UTC))
    return Cell(PRESENT, cast(Value, value))


__all__ = [
    "ITEM_STATE",
    "MAX_UNPARSED",
    "STATE",
    "ColumnReport",
    "CorruptTableError",
    "TableError",
    "TypedTable",
    "build_table",
    "decode",
    "decode_cells",
    "encode",
    "physical",
]

"""Releases in memory: descriptors, and tables of typed cells and their states (SPEC §6.2,
§12.2).

A cell has a state and, when PRESENT, a value of its column's type; a PRESENT list cell holds its
items, each a cell with its own state. The typed values are Python's: ``int`` and ``float`` for
numbers and time offsets, ``str`` for strings and categories, ``bool``, ``datetime.date``, and
timezone-aware ``datetime.datetime`` for datetimes, compared in UTC. A row may leave a column
out, which reads as an empty cell (UNKNOWN).

``Release`` checks what the evaluator relies on, so that a fixture cannot be wrong silently:
cells of their column's type, and parent keys that identify one row each. The validation gate's
other structural checks (§13.2) belong to the importers: a dangling foreign key, say, is
evaluated as ``NO_PARENT``, as §5.5 has it.
"""

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from functools import cached_property
from typing import Literal

from aibi.core.schema.descriptors import (
    ColumnDescriptor,
    CoverageDescriptor,
    DatasetDescriptor,
    Descriptor,
    DirectCoverage,
    GroupedCoverage,
    RelationshipDescriptor,
    TableDescriptor,
)
from aibi.core.schema.semantics import ObservationState

Value = int | float | str | bool | date | datetime
"""A typed value of a cell that is PRESENT."""

State = ObservationState
PRESENT = ObservationState.PRESENT


@dataclass(frozen=True, slots=True)
class Cell:
    state: ObservationState
    value: "Value | tuple[Cell, ...] | None" = None
    """The value when PRESENT (the items, for a list); ``None`` otherwise."""


EMPTY = Cell(ObservationState.UNKNOWN)
"""An empty cell with no declared missing code (§6.2)."""


def present(value: Value) -> Cell:
    """A PRESENT cell."""
    return Cell(PRESENT, value)


def items(*values: "Cell | str") -> Cell:
    """A PRESENT list cell; an item given as a string is PRESENT."""
    return Cell(PRESENT, tuple(v if isinstance(v, Cell) else Cell(PRESENT, v) for v in values))


def missing(state: ObservationState) -> Cell:
    return Cell(state)


def state_of(token: str | None, missing_codes: Mapping[str, str] | None) -> ObservationState:
    """The state a raw token takes (§6.2): its declared missing code, UNKNOWN for a null or
    empty cell without one, and otherwise PRESENT. ``token`` is the cell's canonical string
    form (§12.2); a non-finite number or an error cell is given as its text."""
    codes = missing_codes or {}
    if token is not None and token in codes:
        return ObservationState(codes[token])
    if token is None or token == "":
        return ObservationState.UNKNOWN
    if token in ("NaN", "Infinity", "-Infinity"):
        return ObservationState.UNKNOWN
    return PRESENT


Row = Mapping[str, Cell]


@dataclass(frozen=True)
class Table:
    id: str
    rows: tuple[Row, ...]

    def cell(self, row: int, column: str) -> Cell:
        return self.rows[row].get(column, EMPTY)

    def __len__(self) -> int:
        return len(self.rows)


class ReleaseError(ValueError):
    """A release the evaluator cannot read: a cell of the wrong type, or a parent key that
    identifies more than one row."""


def _typed(datatype: str | None, value: object) -> bool:
    if datatype is None:
        return isinstance(value, int | float | str | bool | date)
    if datatype in ("number", "time_offset"):
        return (
            isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)
        )
    if datatype == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if datatype in ("string", "category"):
        return isinstance(value, str)
    if datatype == "boolean":
        return isinstance(value, bool)
    if datatype == "date":
        return isinstance(value, date) and not isinstance(value, datetime)
    if datatype == "datetime":
        return isinstance(value, datetime) and value.utcoffset() is not None
    return False


KeyPart = tuple[bool, Value]
"""A key value, with booleans told apart from the numbers Python equates with them."""


def key_part(value: Value) -> KeyPart:
    return (isinstance(value, bool), value)


@dataclass(frozen=True)
class _Links:
    """One relationship's rows: each child's parent, and each parent's children."""

    parent: tuple[int | None, ...]
    children: Mapping[int, tuple[int, ...]]


@dataclass(frozen=True)
class Release:
    """A release in memory: its dataset, manifest hash, descriptors and tables."""

    dataset: str
    manifest: str
    descriptors: tuple[Descriptor, ...]
    tables: Mapping[str, Table]
    _links: dict[str, _Links] = field(
        init=False, default_factory=dict[str, _Links], compare=False, repr=False
    )

    def __post_init__(self) -> None:
        self._check()

    # --- Descriptors -------------------------------------------------------------------------

    @cached_property
    def by_id(self) -> Mapping[str, Descriptor]:
        found: dict[str, Descriptor] = {}
        for descriptor in self.descriptors:
            found.setdefault(descriptor.id, descriptor)
        return found

    @cached_property
    def table_ids(self) -> tuple[str, ...]:
        return tuple(sorted(d.id for d in self.descriptors if isinstance(d, TableDescriptor)))

    @cached_property
    def _columns(self) -> Mapping[str, tuple[str, ...]]:
        columns: dict[str, list[str]] = {table: [] for table in self.table_ids}
        for descriptor in self.descriptors:
            if isinstance(descriptor, ColumnDescriptor):
                table, column = descriptor.id.split(".", 1)
                columns.setdefault(table, []).append(column)
        return {table: tuple(sorted(names)) for table, names in columns.items()}

    def columns(self, table: str) -> tuple[str, ...]:
        return self._columns.get(table, ())

    def table(self, table: str) -> TableDescriptor | None:
        found = self.by_id.get(table)
        return found if isinstance(found, TableDescriptor) else None

    def column(self, table: str, column: str) -> ColumnDescriptor | None:
        found = self.by_id.get(f"{table}.{column}")
        return found if isinstance(found, ColumnDescriptor) else None

    def relationship(self, relationship: str) -> RelationshipDescriptor | None:
        found = self.by_id.get(relationship)
        return found if isinstance(found, RelationshipDescriptor) else None

    @cached_property
    def relationships(self) -> tuple[RelationshipDescriptor, ...]:
        return tuple(
            sorted(
                (d for d in self.descriptors if isinstance(d, RelationshipDescriptor)),
                key=lambda d: d.id,
            )
        )

    def coverage(self, relationship: str) -> CoverageDescriptor | None:
        found = self.by_id.get("cov:" + relationship.removeprefix("rel:"))
        return found if isinstance(found, CoverageDescriptor) else None

    @cached_property
    def dataset_descriptor(self) -> DatasetDescriptor | None:
        found = self.by_id.get("dataset")
        return found if isinstance(found, DatasetDescriptor) else None

    @cached_property
    def coverage_tables(self) -> frozenset[str]:
        """Tables with role ``coverage``, and those a coverage descriptor names (§5.6)."""
        tables = {
            table.id
            for table in self.descriptors
            if isinstance(table, TableDescriptor) and table.fields.role == "coverage"
        }
        for descriptor in self.descriptors:
            if isinstance(descriptor, CoverageDescriptor):
                parents = descriptor.fields.parents
                if isinstance(parents, DirectCoverage):
                    tables.add(parents.table)
                elif isinstance(parents, GroupedCoverage):
                    tables.update((parents.assignment.table, parents.groups.table))
        return frozenset(tables)

    def primary_key(self, table: str) -> tuple[str, ...] | None:
        """The declared key's columns; ``None`` when there is none or it is undeclared."""
        descriptor = self.table(table)
        if descriptor is None or descriptor.fields.primary_key is None:
            return None
        return tuple(descriptor.fields.primary_key)

    def datatype(self, table: str, column: str) -> str | None:
        descriptor = self.column(table, column)
        return None if descriptor is None else descriptor.fields.datatype

    # --- Rows --------------------------------------------------------------------------------

    def rows(self, table: str) -> Table:
        return self.tables.get(table) or Table(table, ())

    def key(self, table: str, row: int, columns: Sequence[str]) -> tuple[KeyPart, ...] | None:
        """The row's values in ``columns``, or ``None`` if one of them is not PRESENT."""
        parts: list[KeyPart] = []
        for column in columns:
            cell = self.rows(table).cell(row, column)
            if cell.state is not PRESENT or cell.value is None or isinstance(cell.value, tuple):
                return None
            parts.append(key_part(cell.value))
        return tuple(parts)

    def _linked(self, relationship: str) -> _Links:
        links = self._links.get(relationship)
        if links is None:
            links = self._link(relationship)
            self._links[relationship] = links
        return links

    def _link(self, relationship: str) -> _Links:
        descriptor = self.relationship(relationship)
        if descriptor is None:
            raise KeyError(relationship)
        fields = descriptor.fields
        parents: dict[tuple[KeyPart, ...], int] = {}
        for row in range(len(self.rows(fields.parent_table))):
            key = self.key(fields.parent_table, row, fields.parent_columns)
            if key is not None:
                parents.setdefault(key, row)
        found: list[int | None] = []
        children: dict[int, list[int]] = {}
        for row in range(len(self.rows(fields.child_table))):
            key = self.key(fields.child_table, row, fields.child_columns)
            parent = None if key is None else parents.get(key)
            found.append(parent)
            if parent is not None:
                children.setdefault(parent, []).append(row)
        return _Links(tuple(found), {p: tuple(rows) for p, rows in children.items()})

    def parent(self, relationship: str, row: int) -> int | None:
        """The parent row of a child row: ``None`` for a null or dangling key (``NO_PARENT``)."""
        return self._linked(relationship).parent[row]

    def children(self, relationship: str, row: int) -> tuple[int, ...]:
        return self._linked(relationship).children.get(row, ())

    # --- Checks ------------------------------------------------------------------------------

    def _check(self) -> None:
        problems: list[str] = []
        for name, table in self.tables.items():
            if table.id != name:
                problems.append(f"table {name} is filed under another id, {table.id}")
            if self.table(name) is None:
                problems.append(f"table {name} has no table descriptor")
                continue
            columns = set(self.columns(name))
            for index, row in enumerate(table.rows):
                for column, cell in row.items():
                    if column not in columns:
                        problems.append(f"{name}[{index}] has a cell in no column: {column}")
                        continue
                    problem = _cell_problem(self.datatype(name, column), cell)
                    if problem:
                        problems.append(f"{name}[{index}].{column}: {problem}")
        for relationship in self.relationships:
            fields = relationship.fields
            seen: set[tuple[KeyPart, ...]] = set()
            for row in range(len(self.rows(fields.parent_table))):
                key = self.key(fields.parent_table, row, fields.parent_columns)
                if key is None:
                    continue
                if key in seen:
                    problems.append(
                        f"{relationship.id}: the parent key of {fields.parent_table}[{row}] is "
                        "not unique"
                    )
                seen.add(key)
        if problems:
            raise ReleaseError("; ".join(problems))


def _cell_problem(datatype: str | None, cell: Cell) -> str | None:
    if cell.state is ObservationState.ABSENT:
        return "a cell is never ABSENT; that state comes from existence questions (§6.2)"
    if cell.state is not PRESENT:
        return None if cell.value is None else "only a PRESENT cell has a value"
    if datatype == "list<category>":
        if not isinstance(cell.value, tuple):
            return "a PRESENT list cell holds its items"
        for item in cell.value:
            if item.state is ObservationState.ABSENT:
                return "an item is never ABSENT"
            if item.state is PRESENT and not isinstance(item.value, str):
                return "a PRESENT item of a list<category> column is a string"
            if item.state is not PRESENT and item.value is not None:
                return "only a PRESENT item has a value"
        return None
    if isinstance(cell.value, tuple) or not _typed(datatype, cell.value):
        return f"the value is not of type {datatype}"
    return None


Direction = Literal["up", "down"]

__all__ = [
    "EMPTY",
    "PRESENT",
    "Cell",
    "Direction",
    "KeyPart",
    "Release",
    "ReleaseError",
    "Row",
    "Table",
    "Value",
    "items",
    "key_part",
    "missing",
    "present",
    "state_of",
]

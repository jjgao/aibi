"""Catalogue statistics, computed when a release is built (SPEC §5.2, §8.1, §8.4, §12.2, D270).

Every build (an import, a re-import, a draft change) writes the release's ``statistics`` blob:
``{"format": "aibi.statistics/1", "tables": {<table>: {"n_rows": n, "columns": {<column>:
{"states": {...}, "distribution": {...}}}}}}`` in RFC 8785 form. They are counted before any
disclosure setting applies, which the catalogue applies when it serves them (§8.4, D271), since
the deployment's floor is configuration and not part of the release.

- ``states`` counts each cell's observation state: ``PRESENT``, ``NOT_APPLICABLE``,
  ``NOT_ASSESSED`` and ``UNKNOWN``, every one listed.
- ``distribution`` is one of:

  - ``{"kind": "categories", "categories": [[value, count], …], "multi_membership": bool}`` for
    ``category``, ``boolean`` (``"false"`` and ``"true"``) and ``list<category>`` columns, whose
    PRESENT rows are counted once under each PRESENT item they hold (``multi_membership``). The
    declared permissible values come first, in their listed order, zero counts included, then
    the other values in canonical order (strings compared as UTF-16 code units, §9.3);
  - ``{"kind": "histogram", "scale": "number" | "day" | "instant", "from": "range" | "data",
    "edges": [e₀, …, e_B], "counts": [below, b₀, …, b_(B-1), above], "min": v, "max": v}`` for
    ``number``, ``integer``, ``time_offset``, ``date`` and ``datetime`` columns: *B* = 10
    equal-width bins over the declared ``range`` or, without one, between the smallest and the
    largest PRESENT value (used only without a disclosure setting, §8.4); bins are [eᵢ, eᵢ₊₁), the
    last closed on the right, and values outside the edges fall into *below* and *above*. Dates
    are placed by their day and datetimes by their microsecond in UTC, whose edges are whole
    days or microseconds (floor division), with at most as many bins as the span has units;
    edges, ``min`` and ``max`` are written in the column's type (a number, ``YYYY-MM-DD``, or
    RFC 3339 in UTC with ``Z``); ``min`` and ``max`` are absent when no value is PRESENT;
  - ``{"kind": "none", "reason": …}``: ``identifier`` for identifier columns (a declared
    ``identifier``, and implicitly every primary-key, foreign-key and coverage parent column,
    §5.4, §8.4), which never have value distributions; ``text`` for ``string`` columns and
    ``undeclared`` for columns without a datatype, whose values are not categories;
    ``categories`` for more than ``MAX_CATEGORIES`` distinct values (the cap on categorical levels,
    §14); ``out_of_range`` for values or a range beyond ±(2^53 − 1); and ``empty`` for a histogram
    with neither a declared range nor a PRESENT value.

The statistics are a deterministic function of the typed tables and of the fields they read (a
column's stored datatype, ``range``, ``permissible_values`` and whether it is an identifier), so
a table reused by a draft change keeps its statistics while those fields are unchanged
(``inputs``). They hold no value of an identifier column.
"""

import bisect
import json
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Literal, cast

from pydantic import JsonValue

from aibi.core.engine.data import PRESENT, Cell
from aibi.core.schema.descriptors import (
    ColumnDescriptor,
    ColumnFields,
    CoverageDescriptor,
    DeclaredRange,
    Descriptor,
    DirectCoverage,
    GroupedCoverage,
    RelationshipDescriptor,
    TableDescriptor,
    day,
    instant,
)
from aibi.core.schema.ids import MAX_SAFE_INTEGER
from aibi.core.schema.jsonio import canonical, utf16_key
from aibi.core.schema.semantics import ObservationState
from aibi.core.store import derive

FORMAT = "aibi.statistics/1"
BINS = 10
"""*B*, the bins of a histogram in catalogue statistics (SPEC §8.4)."""
MAX_CATEGORIES = 150
"""Distinct values a column's categories may have, the cap on categorical levels (§14)."""
STATES = (
    ObservationState.PRESENT,
    ObservationState.NOT_APPLICABLE,
    ObservationState.NOT_ASSESSED,
    ObservationState.UNKNOWN,
)
"""The states a cell can have, in the order statistics list them."""
Scale = Literal["number", "day", "instant"]
NoneReason = Literal["identifier", "text", "undeclared", "categories", "out_of_range", "empty"]

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_CATEGORICAL = frozenset({"category", "boolean", "list<category>"})
_SCALES: Mapping[str, Scale] = {
    "number": "number",
    "integer": "number",
    "time_offset": "number",
    "date": "day",
    "datetime": "instant",
}

TableStatistics = dict[str, JsonValue]
"""``{"n_rows": n, "columns": {<column>: {"states": …, "distribution": …}}}``."""


def identifiers(descriptors: Iterable[Descriptor]) -> dict[str, set[str]]:
    """The identifier columns of each table (§5.4): those declared ``identifier``, and the
    primary-key, foreign-key and coverage parent columns, which are identifiers implicitly."""
    found: dict[str, set[str]] = {}

    def add(table: str, columns: Iterable[str]) -> None:
        found.setdefault(table, set()).update(columns)

    for descriptor in descriptors:
        if isinstance(descriptor, ColumnDescriptor) and descriptor.fields.identifier:
            table, column = descriptor.id.split(".", 1)
            add(table, [column])
        elif isinstance(descriptor, TableDescriptor) and descriptor.fields.primary_key:
            add(descriptor.id, descriptor.fields.primary_key)
        elif isinstance(descriptor, RelationshipDescriptor):
            fields = descriptor.fields
            add(fields.child_table, fields.child_columns)
            add(fields.parent_table, fields.parent_columns)
        elif isinstance(descriptor, CoverageDescriptor):
            parents = descriptor.fields.parents
            if isinstance(parents, DirectCoverage):
                add(parents.table, parents.parent_columns)
            elif isinstance(parents, GroupedCoverage):
                add(parents.assignment.table, parents.assignment.parent_columns)
    return found


def inputs(
    columns: Mapping[str, ColumnFields], identifying: Iterable[str], table_hash: str
) -> bytes:
    """What a table's statistics are computed from, besides its cells: its blob, and each
    column's stored datatype, range, permissible values and whether it is an identifier."""
    marked = set(identifying)
    read: dict[str, JsonValue] = {}
    for name, fields in sorted(columns.items()):
        dumped = cast(dict[str, JsonValue], fields.model_dump(mode="json"))
        read[name] = {
            "datatype": derive.stored_datatype(fields),
            "range": dumped.get("range"),
            "permissible_values": dumped.get("permissible_values"),
            "identifier": name in marked,
        }
    return canonical({"table": table_hash, "columns": read})


def table_statistics(
    rows: int,
    cells: Mapping[str, Sequence[Cell]],
    columns: Mapping[str, ColumnFields],
    identifying: Iterable[str],
) -> TableStatistics:
    """A table's statistics, from its cells by column and its columns' fields."""
    marked = set(identifying)
    found: dict[str, JsonValue] = {}
    for name in sorted(cells):
        fields = columns[name]
        found[name] = column_statistics(cells[name], fields, identifier=name in marked)
    return {"n_rows": rows, "columns": found}


def without_distributions(found: TableStatistics, identifying: Iterable[str]) -> TableStatistics:
    """A table's statistics with the distributions of its identifier columns left out."""
    marked = set(identifying)
    columns = cast(dict[str, dict[str, JsonValue]], found["columns"])
    kept: dict[str, JsonValue] = {
        name: {**column, "distribution": _none("identifier")} if name in marked else column
        for name, column in columns.items()
    }
    return {**found, "columns": kept}


def column_statistics(
    cells: Sequence[Cell], fields: ColumnFields, *, identifier: bool
) -> dict[str, JsonValue]:
    counted = Counter(cell.state for cell in cells)
    states: dict[str, JsonValue] = {state.value: counted.get(state, 0) for state in STATES}
    return {"states": states, "distribution": _distribution(cells, fields, identifier)}


def _none(reason: NoneReason) -> dict[str, JsonValue]:
    return {"kind": "none", "reason": reason}


def _distribution(
    cells: Sequence[Cell], fields: ColumnFields, identifier: bool
) -> dict[str, JsonValue]:
    datatype = derive.stored_datatype(fields)
    if identifier:
        return _none("identifier")
    if datatype in _CATEGORICAL:
        return _categories(cells, fields, datatype == "list<category>")
    scale = _SCALES.get(datatype or "")
    if scale is not None:
        return _histogram(cells, fields.range, scale, datatype == "integer")
    return _none("text" if datatype == "string" else "undeclared")


def _category(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _categories(cells: Sequence[Cell], fields: ColumnFields, listed: bool) -> dict[str, JsonValue]:
    counted: Counter[str] = Counter()
    for cell in cells:
        if cell.state is not PRESENT:
            continue
        if listed:
            items = cast(tuple[Cell, ...], cell.value)
            counted.update({_category(item.value) for item in items if item.state is PRESENT})
        else:
            counted[_category(cell.value)] += 1
    declared = (
        []
        if fields.permissible_values is None
        else [v.value for v in fields.permissible_values.values]
    )
    others = sorted((value for value in counted if value not in set(declared)), key=utf16_key)
    if len(declared) + len(others) > MAX_CATEGORIES:
        return _none("categories")
    ordered: list[JsonValue] = [[value, counted.get(value, 0)] for value in [*declared, *others]]
    return {"kind": "categories", "categories": ordered, "multi_membership": listed}


def _position(value: object, scale: Scale) -> int | float:
    """Where a value lies on its scale: a number, a day's ordinal, or microseconds since 1970."""
    if scale == "day":
        return cast(date, value).toordinal()
    if scale == "instant":
        return (cast(datetime, value) - _EPOCH) // timedelta(microseconds=1)
    return cast(int | float, value)


def _bound(value: str | int | float, scale: Scale) -> int | float | None:
    if scale == "day":
        named = day(value) if isinstance(value, str) else None
        return None if named is None else named.toordinal()
    if scale == "instant":
        named_instant = instant(value) if isinstance(value, str) else None
        return None if named_instant is None else _position(named_instant, "instant")
    return None if isinstance(value, str) else value


def _written(position: int | float, scale: Scale, integral: bool) -> JsonValue:
    """A position written in the column's type."""
    if scale == "day":
        return date.fromordinal(int(position)).isoformat()
    if scale == "instant":
        moment = _EPOCH + timedelta(microseconds=int(position))
        fraction = f".{moment.microsecond:06d}" if moment.microsecond else ""
        return moment.strftime("%Y-%m-%dT%H:%M:%S") + fraction + "Z"
    if integral and float(position).is_integer():
        return int(position)
    return float(position)


def _edges(low: int | float, high: int | float, scale: Scale) -> list[int | float]:
    if scale == "number":
        if low == high:
            return [low, high]
        return [low + (high - low) * i / BINS for i in range(BINS)] + [high]
    span = int(high) - int(low)
    bins = max(1, min(BINS, span))
    whole: list[int | float] = [int(low) + span * i // bins for i in range(bins)]
    return [*whole, int(high)]


def _histogram(
    cells: Sequence[Cell], declared: DeclaredRange | None, scale: Scale, integral: bool
) -> dict[str, JsonValue]:
    values = [_position(cell.value, scale) for cell in cells if cell.state is PRESENT]
    if declared is not None:
        low, high = _bound(declared.min, scale), _bound(declared.max, scale)
        assert low is not None, "a declared range has its column's type"
        assert high is not None, "a declared range has its column's type"
        source = "range"
    elif values:
        low, high, source = min(values), max(values), "data"
    else:
        return _none("empty")
    if scale == "number" and any(
        abs(bound) > MAX_SAFE_INTEGER for bound in (low, high, *_extremes(values))
    ):
        return _none("out_of_range")
    edges = _edges(low, high, scale)
    counts = [0] * (len(edges) + 1)
    last = len(edges) - 1
    for value in values:
        if value < edges[0]:
            counts[0] += 1
        elif value > edges[last]:
            counts[-1] += 1
        elif value == edges[last]:
            counts[last] += 1
        else:
            counts[bisect.bisect_right(edges, value)] += 1
    found: dict[str, JsonValue] = {
        "kind": "histogram",
        "scale": scale,
        "from": source,
        "edges": [_written(edge, scale, integral) for edge in edges],
        "counts": list(counts),
    }
    if values:
        found["min"] = _written(min(values), scale, integral)
        found["max"] = _written(max(values), scale, integral)
    return found


def _extremes(values: Sequence[int | float]) -> tuple[int | float, ...]:
    return (min(values), max(values)) if values else ()


def encode(tables: Mapping[str, TableStatistics]) -> bytes:
    """The statistics blob: every table's statistics, in RFC 8785 form."""
    written: dict[str, JsonValue] = {name: tables[name] for name in sorted(tables)}
    return canonical({"format": FORMAT, "tables": written})


def decode(data: bytes) -> dict[str, TableStatistics]:
    """The tables of a statistics blob."""
    parsed = cast(object, json.loads(data))
    if not isinstance(parsed, dict):
        raise ValueError(f"statistics are {FORMAT}")
    found = cast(dict[str, JsonValue], parsed)
    if found.get("format") != FORMAT or not isinstance(found.get("tables"), dict):
        raise ValueError(f"statistics are {FORMAT}")
    return cast(dict[str, TableStatistics], found["tables"])


__all__ = [
    "BINS",
    "FORMAT",
    "MAX_CATEGORIES",
    "STATES",
    "TableStatistics",
    "column_statistics",
    "decode",
    "encode",
    "identifiers",
    "inputs",
    "table_statistics",
    "without_distributions",
]

"""Derived columns (SPEC §5.7), computed when a table is built.

A derived cell is PRESENT when all its inputs are PRESENT and the operation is defined; an
undefined operation (division by zero, a result that is not finite or does not fit the column's
datatype, a value missing from a value map) gives UNKNOWN; otherwise the cell takes the first of
UNKNOWN, NOT_ASSESSED and NOT_APPLICABLE among its inputs. The operations:

- ``date_diff``: ``to`` minus ``from``, both date or datetime columns (a date is its midnight,
  UTC), in the given time units, computed exactly and rounded once to a double;
- ``arith``: the operands, left to right as nested. For an ``integer`` column the arithmetic is
  exact: each operand is the number its canonical string writes (the double ``0.1`` is 1/10),
  and a result that is not an integer, or not within 64 bits, is UNKNOWN. For any other column,
  integers stay integers under ``+``, ``-`` and ``*``, ``/`` is true division, and the result is
  a double;
- ``value_map``: the input's canonical string looked up in the map, and the value found read as
  a source token of the column's datatype;
- ``unit_convert``: the input, a number with declared units, multiplied by the exact factor
  rounded to a double, as a numeric predicate's constants are (D203).

What is wrong whatever the data holds is refused before any cell is computed (``problems``):
inputs of the wrong datatype, units that are missing or do not convert, and a derived column
whose datatype or units contradict its derivation. A derived input's datatype is the one it is
stored as (``stored_datatype``): a derivation of undeclared datatype gives numbers, unless it
maps values, which gives strings.
"""

import math
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, date, datetime
from fractions import Fraction

from aibi.core.engine.data import EMPTY, PRESENT, Cell, Value
from aibi.core.engine.units import factor
from aibi.core.schema.descriptors import (
    Arith,
    ColumnFields,
    DateDiff,
    Derived,
    UnitConvert,
    ValueMap,
    derived_inputs,
)
from aibi.core.schema.jsonio import number_text
from aibi.core.schema.refusals import RefusalCode
from aibi.core.schema.semantics import ObservationState
from aibi.core.store.cells import ColumnCells
from aibi.core.store.sources import canonical_string

_NUMERIC = ("number", "integer", "time_offset")
_TIMES = ("date", "datetime")
_FIRST = (
    ObservationState.UNKNOWN,
    ObservationState.NOT_ASSESSED,
    ObservationState.NOT_APPLICABLE,
)
_INT64 = 2**63
_MICROSECONDS = 10**6

Location = tuple[str | int, ...]
Problem = tuple[RefusalCode, Location, str]
"""A problem's code, where it is in the derived column's ``fields``, and what it is."""
_INVALID = RefusalCode.INVALID_VALUE
_UNITS = RefusalCode.UNITS_UNCONVERTIBLE


def problems(fields: ColumnFields, columns: Mapping[str, ColumnFields]) -> list[Problem]:
    """What is wrong with a derived column whatever the data: ``columns`` are the table's."""
    derived = fields.derived
    if derived is None:
        return []
    found: list[Problem] = []
    at: Location = ("derived",)
    for where, name in derived_inputs(derived):
        input_fields = columns.get(name)
        if input_fields is not None and input_fields.datatype == "list<category>":
            found.append((_INVALID, (*at, *where), f"A derivation reads no list column: {name}"))
    datatype = fields.datatype
    if isinstance(derived, DateDiff):
        for where, name in derived_inputs(derived):
            if _datatype(columns, name) not in _TIMES:
                found.append(
                    (_INVALID, (*at, *where), f"date_diff reads date or datetime columns: {name}")
                )
        if factor("s", derived.units) is None:
            found.append((_UNITS, (*at, "units"), f"Not a unit of time: {derived.units}"))
        found.extend(_units_agree(fields, derived.units))
        if datatype not in (None, "number", "time_offset"):
            found.append((_INVALID, ("datatype",), "date_diff gives a number or a time offset"))
    elif isinstance(derived, Arith):
        for where, name in derived_inputs(derived):
            if _datatype(columns, name) not in _NUMERIC:
                found.append((_INVALID, (*at, *where), f"arith reads numeric columns: {name}"))
        if datatype not in (None, *_NUMERIC):
            found.append((_INVALID, ("datatype",), "arith gives a number"))
    elif isinstance(derived, UnitConvert):
        given = columns.get(derived.input)
        source_units = None if given is None else given.units
        if _datatype(columns, derived.input) not in _NUMERIC:
            found.append(
                (_INVALID, (*at, "input"), f"unit_convert reads a numeric column: {derived.input}")
            )
        elif source_units is None:
            found.append(
                (_UNITS, (*at, "input"), f"The input's units are undeclared: {derived.input}")
            )
        elif factor(source_units, derived.units) is None:
            found.append(
                (_UNITS, (*at, "units"), f"{source_units} does not convert to {derived.units}")
            )
        found.extend(_units_agree(fields, derived.units))
        if datatype not in (None, "number", "time_offset"):
            found.append((_INVALID, ("datatype",), "unit_convert gives a number or a time offset"))
    elif datatype == "list<category>":
        found.append((_INVALID, ("datatype",), "value_map gives one value, not a list"))
    return found


def stored_datatype(fields: ColumnFields) -> str | None:
    """The datatype a column's values have once built: its own, or for a derived column that
    declares none, ``number`` unless it maps values (which gives strings, as ``None`` does)."""
    if fields.datatype is None and fields.derived is not None:
        return None if isinstance(fields.derived, ValueMap) else "number"
    return fields.datatype


def _datatype(columns: Mapping[str, ColumnFields], name: str) -> str | None:
    found = columns.get(name)
    return None if found is None else stored_datatype(found)


def _units_agree(fields: ColumnFields, units: str) -> list[Problem]:
    if fields.units is not None and fields.units != units:
        message = f"The derivation gives {units}, and the column declares {fields.units}"
        return [(_INVALID, ("units",), message)]
    return []


def compute(
    fields: ColumnFields, cells: Mapping[str, Sequence[Cell]], columns: Mapping[str, ColumnFields]
) -> list[Cell]:
    """The derived column's cells, row by row, from the cells of the columns it reads.

    ``problems(fields, columns)`` must be empty.
    """
    derived = fields.derived
    assert derived is not None
    names = [name for _, name in derived_inputs(derived)]
    rows = len(next(iter(cells.values()))) if cells else 0
    operation = _operation(derived, fields, columns)
    result: list[Cell] = []
    for row in range(rows):
        inputs = {name: cells[name][row] for name in names}
        missing = [cell.state for cell in inputs.values() if cell.state is not PRESENT]
        if missing:
            result.append(Cell(next(state for state in _FIRST if state in missing)))
            continue
        value = operation({name: cell.value for name, cell in inputs.items()})
        result.append(EMPTY if value is None else Cell(PRESENT, value))
    return result


Operation = Callable[[Mapping[str, object]], Value | None]


def _operation(
    derived: Derived, fields: ColumnFields, columns: Mapping[str, ColumnFields]
) -> Operation:
    datatype = fields.datatype
    if isinstance(derived, DateDiff):
        per_second = factor("s", derived.units)
        assert per_second is not None

        def date_diff(values: Mapping[str, object]) -> Value | None:
            start, end = _instant(values[derived.from_]), _instant(values[derived.to])
            span = end - start
            microseconds = (span.days * 86_400 + span.seconds) * _MICROSECONDS + span.microseconds
            return _finite(float(Fraction(microseconds, _MICROSECONDS) * per_second))

        return date_diff
    if isinstance(derived, Arith):
        if datatype == "integer":

            def integral(values: Mapping[str, object]) -> Value | None:
                try:
                    number = _exact(derived, values)
                except ZeroDivisionError:
                    return None
                if number.denominator != 1 or not -_INT64 <= number.numerator < _INT64:
                    return None
                return number.numerator

            return integral

        def arith(values: Mapping[str, object]) -> Value | None:
            try:
                number = _arith(derived, values)
                return _finite(float(number))
            except (ZeroDivisionError, OverflowError):
                return None

        return arith
    if isinstance(derived, UnitConvert):
        given = columns[derived.input].units
        assert given is not None
        exact = factor(given, derived.units)
        assert exact is not None
        multiplier = float(exact)

        def unit_convert(values: Mapping[str, object]) -> Value | None:
            number = values[derived.input]
            assert isinstance(number, int | float)
            try:
                return _finite(float(number) * multiplier)
            except OverflowError:
                return None

        return unit_convert
    assert isinstance(derived, ValueMap)
    reader = ColumnCells(datatype, None)

    def value_map(values: Mapping[str, object]) -> Value | None:
        key = _key(values[derived.input])
        mapped = derived.map.get(key)
        if mapped is None:
            return None
        cell = reader.cell(mapped)
        return None if cell.state is not PRESENT else _value(cell)

    return value_map


def _value(cell: Cell) -> Value:
    value = cell.value
    assert value is not None
    assert not isinstance(value, tuple)
    return value


def _instant(value: object) -> datetime:
    if isinstance(value, datetime):
        return value
    assert isinstance(value, date)
    return datetime(value.year, value.month, value.day, tzinfo=UTC)


def _key(value: object) -> str:
    """A typed value's canonical string, as a value map is keyed (§12.2)."""
    assert isinstance(value, bool | int | float | str | date)
    written = canonical_string(value)
    assert written is not None
    return written


def _arith(arith: Arith, values: Mapping[str, object]) -> int | float:
    operands: list[int | float] = []
    for operand in arith.args:
        if isinstance(operand, str):
            given = values[operand]
            assert isinstance(given, int | float)
            operands.append(given)
        elif isinstance(operand, Arith):
            operands.append(_arith(operand, values))
        else:
            operands.append(operand)
    left, right = operands
    if arith.operator == "+":
        return left + right
    if arith.operator == "-":
        return left - right
    if arith.operator == "*":
        return left * right
    return left / right


def _exact(arith: Arith, values: Mapping[str, object]) -> Fraction:
    """``arith`` computed exactly, each operand read as its canonical string writes it."""
    operands: list[Fraction] = []
    for operand in arith.args:
        if isinstance(operand, Arith):
            operands.append(_exact(operand, values))
            continue
        given = values[operand] if isinstance(operand, str) else operand
        assert isinstance(given, int | float)
        operands.append(Fraction(given) if isinstance(given, int) else Fraction(number_text(given)))
    left, right = operands
    if arith.operator == "+":
        return left + right
    if arith.operator == "-":
        return left - right
    if arith.operator == "*":
        return left * right
    return left / right


def _finite(number: float) -> float | None:
    return number if math.isfinite(number) else None


def order(columns: Mapping[str, ColumnFields]) -> list[str]:
    """The derived columns in an order that computes each after the derived columns it reads:
    by id among those ready. The release's check refuses cycles (§13.2); a column on one is left
    out here."""
    derived = {name: fields.derived for name, fields in columns.items() if fields.derived}
    reads = {
        name: {read for _, read in derived_inputs(derivation) if read in derived}
        for name, derivation in derived.items()
    }
    done: list[str] = []
    ready = sorted(name for name, needs in reads.items() if not needs)
    remaining = {name: set(needs) for name, needs in reads.items() if needs}
    while ready:
        name = ready.pop(0)
        done.append(name)
        freed: list[str] = []
        for other, needs in remaining.items():
            needs.discard(name)
            if not needs:
                freed.append(other)
        for other in freed:
            del remaining[other]
        ready = sorted([*ready, *freed])
    return done


__all__ = ["compute", "order", "problems"]

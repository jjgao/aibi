"""Derived columns (SPEC §5.7): computed when the table is built, PRESENT only from PRESENT inputs
and a defined operation, and refused when wrong whatever the data holds."""

from datetime import UTC, date, datetime
from typing import Any

import pytest

from aibi.core.engine import build
from aibi.core.engine.data import EMPTY, PRESENT, Cell
from aibi.core.schema.descriptors import ColumnDescriptor, ColumnFields
from aibi.core.schema.semantics import ObservationState
from aibi.core.store.sources import Parsed, SourceValue
from aibi.core.store.tables import TableError, build_table

UNKNOWN = ObservationState.UNKNOWN
NOT_ASSESSED = ObservationState.NOT_ASSESSED
NOT_APPLICABLE = ObservationState.NOT_APPLICABLE
CODES = {"NA": "UNKNOWN", "nd": "NOT_ASSESSED", "no": "NOT_APPLICABLE"}


def fields(datatype: str | None, **given: Any) -> ColumnFields:
    descriptor = build.column(f"t.c{id(given)}", datatype, **given)
    assert isinstance(descriptor, ColumnDescriptor)
    return descriptor.fields


def derive(
    sources: dict[str, tuple[ColumnFields, list[SourceValue]]], derived: dict[str, ColumnFields]
) -> dict[str, tuple[Cell, ...]]:
    names = list(sources)
    rows = list(zip(*(values for _, values in sources.values()), strict=True))
    columns = {name: given for name, (given, _) in sources.items()} | derived
    typed = build_table("t", Parsed(tuple(names), rows), names, columns)
    return {name: typed.cells[name] for name in derived}


def present(value: object) -> Cell:
    return Cell(PRESENT, value)  # type: ignore[arg-type]


def test_date_diff_in_days_years_and_months_of_dates_and_datetimes() -> None:
    days = fields("date")
    moments = fields("datetime")
    found = derive(
        {
            "start": (days, ["2020-01-01", "2020-03-01", "2021-01-01"]),
            "end": (days, ["2021-01-01", "2020-02-01", "2021-01-01"]),
            "at": (moments, ["2021-01-01T12:00:00Z", "2020-02-01T06:00:00+06:00", None]),
        },
        {
            "in_days": fields(
                "time_offset",
                derived={"op": "date_diff", "from": "start", "to": "end", "units": "d"},
            ),
            "in_years": fields(
                "number", derived={"op": "date_diff", "from": "start", "to": "end", "units": "a"}
            ),
            "in_months": fields(
                None, derived={"op": "date_diff", "from": "start", "to": "end", "units": "mo"}
            ),
            "in_hours": fields(
                "number", derived={"op": "date_diff", "from": "start", "to": "at", "units": "h"}
            ),
        },
    )
    assert found["in_days"] == (present(366.0), present(-29.0), present(0.0))
    assert found["in_years"] == (present(366 / 365.25), present(-29 / 365.25), present(0.0))
    assert found["in_months"][0] == present(366 / (365.25 / 12))
    # A date is its midnight, UTC; the datetime is in UTC.
    assert found["in_hours"] == (present(366 * 24 + 12.0), present(-29 * 24.0), EMPTY)


def test_date_diff_is_exact_until_it_is_rounded_once() -> None:
    moments = fields("datetime")
    found = derive(
        {
            "start": (moments, ["2024-01-01T00:00:00.000001Z"]),
            "end": (moments, ["2024-01-01T00:00:00.000004Z"]),
        },
        {
            "gap": fields(
                "number", derived={"op": "date_diff", "from": "start", "to": "end", "units": "us"}
            )
        },
    )
    assert found["gap"] == (present(3.0),)


def test_arith_keeps_integers_and_divides_truly() -> None:
    whole, real = fields("integer", missing_codes=CODES), fields("number")
    add = {"op": "arith", "operator": "+", "args": ["a", "b"]}
    nested = {
        "op": "arith",
        "operator": "*",
        "args": [{"op": "arith", "operator": "-", "args": ["a", 1]}, 2.5],
    }
    found = derive(
        {
            "a": (whole, ["3", str(2**62), "7"]),
            "b": (whole, ["4", "1", "2"]),
            "x": (real, [1.5, 2, 3]),
        },
        {
            "sum": fields("integer", derived=add),
            "ratio": fields("number", derived={"op": "arith", "operator": "/", "args": ["a", "b"]}),
            "nested": fields(None, derived=nested),
            "mixed": fields("number", derived={"op": "arith", "operator": "+", "args": ["a", "x"]}),
            "halves": fields(
                "integer", derived={"op": "arith", "operator": "/", "args": ["a", "b"]}
            ),
        },
    )
    assert found["sum"] == (present(7), present(2**62 + 1), present(9))
    assert found["ratio"] == (present(0.75), present(float(2**62)), present(3.5))
    assert found["nested"] == (present(5.0), present((2**62 - 1) * 2.5), present(15.0))
    assert found["mixed"] == (present(4.5), present(2**62 + 2.0), present(10.0))
    assert found["halves"] == (EMPTY, present(2**62), EMPTY)  # 3/4 and 7/2 are no integers


def test_arith_into_an_integer_column_is_exact() -> None:
    """Each operand is the number its canonical string writes, and only an integer within 64
    bits is kept; a double would round 2^63 - 1 halved, or 2^53 + 1, to an integer."""
    whole = fields("integer")

    def into_integers(operator: str, right: object) -> dict[str, Any]:
        return {"op": "arith", "operator": operator, "args": ["x", right]}

    found = derive(
        {"x": (whole, [str(2**63 - 1), str(2**53 + 1), "10", "-6"])},
        {
            "half": fields("integer", derived=into_integers("/", 2)),
            "same": fields("integer", derived=into_integers("/", 1)),
            "plus": fields("integer", derived=into_integers("+", 0.5)),
            "tenth": fields("integer", derived=into_integers("*", 0.1)),
        },
    )
    assert found["half"] == (EMPTY, EMPTY, present(5), present(-3))
    assert found["same"] == (present(2**63 - 1), present(2**53 + 1), present(10), present(-6))
    assert found["plus"] == (EMPTY, EMPTY, EMPTY, EMPTY)
    assert found["tenth"] == (EMPTY, EMPTY, present(1), EMPTY)  # 0.1 is 1/10
    assert all(type(cell.value) is int for cell in found["same"])


def test_a_derivation_of_undeclared_datatype_feeds_arithmetic_as_a_number() -> None:
    found = derive(
        {"x": (fields("integer"), ["3"])},
        {
            "doubled": fields(None, derived={"op": "arith", "operator": "*", "args": ["x", 2]}),
            "next": fields(
                "number", derived={"op": "arith", "operator": "+", "args": ["doubled", 1]}
            ),
        },
    )
    assert found == {"doubled": (present(6.0),), "next": (present(7.0),)}


def test_undefined_operations_are_unknown() -> None:
    real, whole = fields("number"), fields("integer")
    found = derive(
        {
            "a": (real, ["1", "1e308", "5"]),
            "b": (real, ["0", "10", "2"]),
            "n": (whole, ["1", str(2**62), "3"]),
        },
        {
            "divided": fields(
                "number", derived={"op": "arith", "operator": "/", "args": ["a", "b"]}
            ),
            "times": fields("number", derived={"op": "arith", "operator": "*", "args": ["a", "b"]}),
            "wide": fields("integer", derived={"op": "arith", "operator": "*", "args": ["n", 4]}),
        },
    )
    assert found["divided"] == (EMPTY, present(1e307), present(2.5))
    assert found["times"] == (present(0.0), EMPTY, present(10.0))  # 1e309 is not finite
    assert found["wide"] == (present(4), EMPTY, present(12))  # 2^64 is beyond 64 bits


@pytest.mark.parametrize(
    ("left", "right", "state"),
    [
        ("NA", "nd", UNKNOWN),
        ("nd", "NA", UNKNOWN),
        ("no", "nd", NOT_ASSESSED),
        ("no", "1", NOT_APPLICABLE),
        ("1", "no", NOT_APPLICABLE),
        ("", "nd", UNKNOWN),
    ],
)
def test_a_missing_input_gives_the_first_of_unknown_not_assessed_and_not_applicable(
    left: str, right: str, state: ObservationState
) -> None:
    column = fields("integer", missing_codes=CODES)
    found = derive(
        {"a": (column, [left]), "b": (column, [right])},
        {"sum": fields("integer", derived={"op": "arith", "operator": "+", "args": ["a", "b"]})},
    )
    assert found["sum"] == (Cell(state),)


def test_value_map_keys_by_canonical_string_and_types_the_value_found() -> None:
    found = derive(
        {
            "sex": (fields("category"), ["F", "M", "X"]),
            "grade": (fields("integer"), ["1", "2", "3"]),
        },
        {
            "label": fields(
                "category",
                derived={"op": "value_map", "input": "sex", "map": {"F": "female", "M": "male"}},
            ),
            "pass": fields(
                "boolean",
                derived={
                    "op": "value_map",
                    "input": "grade",
                    "map": {"1": "yes", "2": "no", "3": "?"},
                },
            ),
        },
    )
    assert found["label"] == (present("female"), present("male"), EMPTY)
    assert found["pass"] == (present(True), present(False), EMPTY)


def test_unit_convert_multiplies_by_the_exact_factor_rounded_to_a_double() -> None:
    found = derive(
        {"length": (fields("number", units="[ft_i]"), ["0.1", "3"])},
        {
            "metres": fields(
                "number",
                units="m",
                derived={"op": "unit_convert", "input": "length", "units": "m"},
            )
        },
    )
    assert found["metres"] == (present(0.1 * 0.3048), present(3 * 0.3048))
    assert found["metres"][0] == present(0.030480000000000004)  # as SQL computes it (D203)


def test_derived_columns_read_derived_columns_in_order() -> None:
    found = derive(
        {"a": (fields("integer"), ["2"])},
        {
            "c": fields("integer", derived={"op": "arith", "operator": "*", "args": ["b", 10]}),
            "b": fields("integer", derived={"op": "arith", "operator": "+", "args": ["a", 1]}),
            "d": fields("integer", derived={"op": "arith", "operator": "+", "args": ["c", "b"]}),
        },
    )
    assert found == {"c": (present(30),), "b": (present(3),), "d": (present(33),)}


def _problems(
    sources: dict[str, ColumnFields],
    derived: dict[str, ColumnFields],
    layout: list[str] | None = None,
) -> list[tuple[str, str | None, tuple[str | int, ...]]]:
    names = list(sources) if layout is None else layout
    columns = sources | derived
    with pytest.raises(TableError) as raised:
        build_table("t", Parsed(tuple(names), []), names, columns)
    return [(code, column, where) for code, column, where, _ in raised.value.problems]


def test_what_is_wrong_whatever_the_data_holds_is_refused() -> None:
    sources = {
        "text": fields("string"),
        "day": fields("date"),
        "kg": fields("number", units="kg"),
        "bare": fields("number"),
        "tags": fields("list<category>"),
    }
    derived = {
        "diff": fields(
            None, derived={"op": "date_diff", "from": "text", "to": "day", "units": "kg"}
        ),
        "sum": fields("string", derived={"op": "arith", "operator": "+", "args": ["text", 1]}),
        "grams": fields(
            "number", units="mg", derived={"op": "unit_convert", "input": "kg", "units": "g"}
        ),
        "metres": fields(None, derived={"op": "unit_convert", "input": "kg", "units": "m"}),
        "undeclared": fields(None, derived={"op": "unit_convert", "input": "bare", "units": "g"}),
        "listed": fields(
            "list<category>", derived={"op": "value_map", "input": "tags", "map": {"a": "b"}}
        ),
        "hours": fields(
            "number",
            units="h",
            derived={"op": "date_diff", "from": "day", "to": "day", "units": "d"},
        ),
        "mapped": fields(None, derived={"op": "value_map", "input": "text", "map": {"a": "1"}}),
        "counted": fields(
            "number", derived={"op": "arith", "operator": "+", "args": ["mapped", 1]}
        ),
    }
    assert sorted(_problems(sources, derived)) == [
        ("INVALID_VALUE", "counted", ("derived", "args", 0)),  # a value map gives strings
        ("INVALID_VALUE", "diff", ("derived", "from")),
        ("INVALID_VALUE", "grams", ("units",)),
        ("INVALID_VALUE", "hours", ("units",)),
        ("INVALID_VALUE", "listed", ("datatype",)),
        ("INVALID_VALUE", "listed", ("derived", "input")),
        ("INVALID_VALUE", "sum", ("datatype",)),
        ("INVALID_VALUE", "sum", ("derived", "args", 0)),
        ("UNITS_UNCONVERTIBLE", "diff", ("derived", "units")),
        ("UNITS_UNCONVERTIBLE", "metres", ("derived", "units")),
        ("UNITS_UNCONVERTIBLE", "undeclared", ("derived", "input")),
    ]


def test_a_source_column_is_not_derived_and_every_other_column_is() -> None:
    derived = fields("integer", derived={"op": "arith", "operator": "+", "args": ["a", 1]})
    assert _problems({"a": fields("integer"), "b": derived}, {}, ["a", "b"]) == [
        ("INVALID_VALUE", "b", ("derived",))
    ]
    assert _problems({"a": fields("integer"), "b": fields("integer")}, {}, ["a"]) == [
        ("COLUMNS_CHANGED", "b", ())
    ]
    assert _problems({"a": fields("integer")}, {}, ["a", "z"]) == [("COLUMNS_CHANGED", None, ())]


def test_dates_and_datetimes_read_as_utc_instants() -> None:
    found = derive(
        {
            "day": (fields("date"), [date(2024, 1, 1)]),
            "at": (fields("datetime"), [datetime(2024, 1, 1, 6, tzinfo=UTC)]),
        },
        {
            "hours": fields(
                "number", derived={"op": "date_diff", "from": "day", "to": "at", "units": "h"}
            )
        },
    )
    assert found["hours"] == (present(6.0),)

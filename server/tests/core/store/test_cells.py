"""Typed cells: missing codes first, then empty and non-finite cells, then the datatype's grammar
(SPEC §5.4, §6.2, §12.2)."""

import math
from collections import Counter
from datetime import UTC, date, datetime, timedelta, timezone
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from aibi.core.engine.data import EMPTY, PRESENT, Cell
from aibi.core.schema.descriptors import ListSyntax
from aibi.core.schema.semantics import ObservationState
from aibi.core.store.cells import MAX_LIST_TEXT, ColumnCells
from aibi.core.store.sources import ErrorCell, SourceValue, canonical_string

UNKNOWN = ObservationState.UNKNOWN
NOT_ASSESSED = ObservationState.NOT_ASSESSED
NOT_APPLICABLE = ObservationState.NOT_APPLICABLE


def cell(datatype: str | None, value: SourceValue, **given: Any) -> Cell:
    return ColumnCells(datatype, given.get("codes"), given.get("syntax")).cell(value)


def present(value: object) -> Cell:
    return Cell(PRESENT, value)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("42", 42),
        ("+7", 7),
        ("-0", 0),
        ("007", 7),
        (" 5\t", 5),
        ("3.0", 3),
        ("1e3", 1000),
        ("-2.5e1", -25),
        (str(2**63 - 1), 2**63 - 1),
        (str(-(2**63)), -(2**63)),
        ("9007199254740991.0", 2**53 - 1),
        ("1" + "0" * 40 + "e-40", 1),
        (3.0, 3),
        (-(2**63), -(2**63)),
    ],
)
def test_integers(value: SourceValue, expected: int) -> None:
    found = cell("integer", value)
    assert found == present(expected)
    assert type(found.value) is int


@pytest.mark.parametrize(
    "value",
    [
        "3.5",
        "4503599627370496.5",  # a double would round it to an integer
        "1.0000000000000001",
        "9007199254740992.0",
        "1e30",
        "1e999999999",
        "1e-999999999",
        "0e99999999999999999999",  # beyond a decimal's exponents, zero or not
        "1e99999999999999999999",
        "0.0e-99999999999999999999",
        str(2**63),
        "9" * 5000,
        "\u0661\u0662",
        "1_000",
        "0x10",
        "",
        "twelve",
        2**63,
        2.5,
        2.0**60,  # a double beyond 2^53 may be the rounding of another integer
        True,
    ],
)
def test_what_is_no_integer_is_unknown(value: SourceValue) -> None:
    assert cell("integer", value) == EMPTY


def test_an_exponent_beyond_a_decimal_s_is_an_unparsed_token() -> None:
    cells = ColumnCells("integer", None)
    assert cells.cell("0e99999999999999999999") == EMPTY
    assert cells.unparsed == Counter({"0e99999999999999999999": 1})


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("1.5", 1.5),
        (".5", 0.5),
        ("5.", 5.0),
        ("-1e-3", -0.001),
        ("1E3", 1000.0),
        (" 2 ", 2.0),
        (7, 7.0),
        (2**70, float(2**70)),
        (-99.0, -99.0),
    ],
)
def test_numbers(value: SourceValue, expected: float) -> None:
    found = cell("number", value)
    assert found == present(expected)
    assert type(found.value) is float
    assert cell("time_offset", value) == found


@pytest.mark.parametrize(
    "value",
    ["inf", "nan", "Infinity", "-Infinity", "1e999", "1,5", "e3", ".", "", math.nan, math.inf],
)
def test_what_is_no_finite_number_is_unknown(value: SourceValue) -> None:
    assert cell("number", value) == EMPTY


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("true", True),
        ("TRUE", True),
        (" t ", True),
        ("Yes", True),
        ("y", True),
        ("1", True),
        ("false", False),
        ("F", False),
        ("no", False),
        ("N", False),
        ("0", False),
        (True, True),
        (False, False),
        (1, True),
        (0.0, False),
    ],
)
def test_booleans(value: SourceValue, expected: bool) -> None:
    assert cell("boolean", value) == present(expected)


@pytest.mark.parametrize("value", ["2", "truth", "\uff54\uff52\uff55\uff45", "oui", 1.5, 2])
def test_what_is_no_boolean_is_unknown(value: SourceValue) -> None:
    assert cell("boolean", value) == EMPTY


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2024-02-29", date(2024, 2, 29)),
        (" 0001-01-01 ", date(1, 1, 1)),
        (date(2024, 1, 2), date(2024, 1, 2)),
        (datetime(2024, 1, 2), date(2024, 1, 2)),
    ],
)
def test_dates(value: SourceValue, expected: date) -> None:
    assert cell("date", value) == present(expected)


@pytest.mark.parametrize(
    "value",
    [
        "2023-02-29",
        "2024-1-2",
        "20240102",
        "2024-W01-1",
        "2024-01-02T00:00:00",
        "0000-01-01",
        datetime(2024, 1, 2, 0, 0, 1),
        datetime(2024, 1, 2, tzinfo=UTC),
        19000,
    ],
)
def test_what_is_no_date_is_unknown(value: SourceValue) -> None:
    assert cell("date", value) == EMPTY


_PLUS_TWO = timezone(timedelta(hours=2))


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2024-01-02T03:04:05Z", datetime(2024, 1, 2, 3, 4, 5, tzinfo=UTC)),
        ("2024-01-02t03:04:05z", datetime(2024, 1, 2, 3, 4, 5, tzinfo=UTC)),
        ("2024-01-02 03:04:05", datetime(2024, 1, 2, 3, 4, 5, tzinfo=UTC)),
        ("2024-01-02T03:04:05+02:00", datetime(2024, 1, 2, 1, 4, 5, tzinfo=UTC)),
        ("2024-01-02T03:04:05-00:00", datetime(2024, 1, 2, 3, 4, 5, tzinfo=UTC)),
        ("2024-01-02T03:04:05.123456789Z", datetime(2024, 1, 2, 3, 4, 5, 123456, tzinfo=UTC)),
        ("2024-01-02T03:04:05.5Z", datetime(2024, 1, 2, 3, 4, 5, 500000, tzinfo=UTC)),
        ("2024-01-02", datetime(2024, 1, 2, tzinfo=UTC)),
        (date(2024, 1, 2), datetime(2024, 1, 2, tzinfo=UTC)),
        (datetime(2024, 1, 2, 3), datetime(2024, 1, 2, 3, tzinfo=UTC)),
        (datetime(2024, 1, 2, 3, tzinfo=_PLUS_TWO), datetime(2024, 1, 2, 1, tzinfo=UTC)),
    ],
)
def test_datetimes_are_held_in_utc(value: SourceValue, expected: datetime) -> None:
    found = cell("datetime", value)
    assert found == present(expected)
    assert isinstance(found.value, datetime)
    assert found.value.utcoffset() == timedelta(0)


@pytest.mark.parametrize(
    "value",
    [
        "2024-01-02T24:00:00Z",
        "2024-01-02T23:59:60Z",
        "2024-01-02T03:04Z",
        "2024-01-02T03:04:05+24:00",
        "2024-01-02T03:04:05+05:60",
        "2024-01-02T03:04:05.Z",
        "0001-01-01T00:00:00+01:00",
        "9999-12-31T23:59:59-01:00",
        "yesterday",
    ],
)
def test_what_is_no_datetime_is_unknown(value: SourceValue) -> None:
    assert cell("datetime", value) == EMPTY


@pytest.mark.parametrize("datatype", ["string", "category", None])
def test_text_is_taken_exactly_and_typed_values_by_their_canonical_string(
    datatype: str | None,
) -> None:
    assert cell(datatype, "  Mixed Case  ") == present("  Mixed Case  ")
    assert cell(datatype, 3.0) == present("3")
    assert cell(datatype, date(2024, 1, 2)) == present("2024-01-02")
    assert cell(datatype, "nan") == present("nan")
    assert cell(datatype, math.nan) == EMPTY  # a non-finite number is never PRESENT


# --- Missing codes ----------------------------------------------------------------------------


def test_a_missing_code_comes_first_and_matches_the_canonical_string_exactly() -> None:
    codes = {"-99": "NOT_ASSESSED", "0": "NOT_APPLICABLE", "NA": "UNKNOWN", "": "NOT_ASSESSED"}
    assert cell("integer", "-99", codes=codes) == Cell(NOT_ASSESSED)
    assert cell("integer", -99.0, codes=codes) == Cell(NOT_ASSESSED)
    assert cell("integer", "0", codes=codes) == Cell(NOT_APPLICABLE)
    assert cell("integer", "00", codes=codes) == present(0)
    assert cell("integer", " NA", codes=codes) == EMPTY
    assert cell("integer", "", codes=codes) == Cell(NOT_ASSESSED)
    assert cell("integer", None, codes=codes) == EMPTY


def test_non_finite_numbers_and_error_cells_are_never_present() -> None:
    codes = {"#N/A": "NOT_APPLICABLE", "NaN": "NOT_ASSESSED"}
    assert cell("number", ErrorCell("#N/A"), codes=codes) == Cell(NOT_APPLICABLE)
    assert cell("number", math.nan, codes=codes) == Cell(NOT_ASSESSED)
    assert cell("string", ErrorCell("#REF!"), codes=codes) == EMPTY
    assert cell("string", -math.inf, codes=codes) == EMPTY


def test_tokens_that_do_not_parse_are_counted() -> None:
    column = ColumnCells("integer", {"NA": "UNKNOWN"})
    for value in ("1", "x", "x", "NA", "", ErrorCell("#N/A"), math.nan, "2.5"):
        column.cell(value)
    assert column.unparsed == {"x": 2, "#N/A": 1, "NaN": 1, "2.5": 1}
    assert column.states == {PRESENT: 1, UNKNOWN: 7}


# --- Lists --------------------------------------------------------------------------------------

_DELIMITED = ListSyntax(format="delimited", delimiter="; ")
_JSON = ListSyntax(format="json")
_PYTHON = ListSyntax(format="python")


def items(*cells: Cell) -> Cell:
    return Cell(PRESENT, tuple(cells))


def test_a_delimited_list_splits_exactly_and_its_items_take_states() -> None:
    codes = {"NA": "UNKNOWN", "n/a": "NOT_APPLICABLE"}
    found = cell("list<category>", "a; b;c; NA; ; n/a", codes=codes, syntax=_DELIMITED)
    assert found == items(present("a"), present("b;c"), EMPTY, EMPTY, Cell(NOT_APPLICABLE))


def test_a_list_cell_takes_its_missing_code_before_it_is_split() -> None:
    codes = {"NA": "NOT_ASSESSED"}
    assert cell("list<category>", "NA", codes=codes, syntax=_JSON) == Cell(NOT_ASSESSED)
    assert cell("list<category>", "", syntax=_JSON) == EMPTY


def test_json_and_python_lists_hold_scalars() -> None:
    codes = {"?": "NOT_ASSESSED"}
    expected = items(
        present("a"), EMPTY, present("1"), present("2.5"), present("true"), Cell(NOT_ASSESSED)
    )
    assert (
        cell("list<category>", '["a", null, 1, 2.5, true, "?"]', codes=codes, syntax=_JSON)
        == expected
    )
    assert (
        cell("list<category>", "['a', None, 1, 2.5, True, '?']", codes=codes, syntax=_PYTHON)
        == expected
    )
    assert cell("list<category>", "('a',)", syntax=_PYTHON) == items(present("a"))
    assert cell("list<category>", "[]", syntax=_JSON) == items()


@pytest.mark.parametrize(
    ("token", "syntax"),
    [
        ('["a", ["b"]]', _JSON),
        ('{"a": 1}', _JSON),
        ('"a"', _JSON),
        ("[NaN]", _JSON),
        ("[1e999]", _JSON),
        ("[a]", _JSON),
        ("['a', b'x']", _PYTHON),
        ("{'a'}", _PYTHON),
        ("['a', 1j]", _PYTHON),
        ("[" * 300 + "]" * 300, _PYTHON),
        ("__import__('os')", _PYTHON),
        ('["a", "\\ud800"]', _JSON),  # an escape UTF-8 cannot carry
        ("['\\udfff']", _PYTHON),
        ("[" + "'a', " * (MAX_LIST_TEXT // 5) + "]", _PYTHON),
    ],
)
def test_a_list_that_does_not_parse_as_scalars_is_unknown(token: str, syntax: ListSyntax) -> None:
    column = ColumnCells("list<category>", None, syntax)
    assert column.cell(token) == EMPTY
    assert column.unparsed == {token: 1}


def test_item_states_are_counted() -> None:
    column = ColumnCells("list<category>", {"NA": "UNKNOWN"}, _DELIMITED)
    column.cell("a; NA; b")
    assert column.item_states == {PRESENT: 2, UNKNOWN: 1}


@given(st.floats(allow_nan=False, allow_infinity=False))
def test_a_number_s_canonical_string_reads_back_as_the_number(value: float) -> None:
    written = canonical_string(value)
    assert written is not None
    assert cell("number", written) == present(value)

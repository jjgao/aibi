"""A cell that is not PRESENT carries no value, and the engine reads none (SPEC §6.2, §6.4).

This is the property the web column screen's gloss of the UNKNOWN state, "no usable value" (D443),
is true by: whatever made a cell UNKNOWN (an empty cell, a token that does not parse, a non-finite
number, a spreadsheet error cell, a list that does not parse, a derived cell that is undefined or
inherits UNKNOWN, a code a curator declared), the cell holds no value, and the engine reads none of
it (a derived cell takes the first non-PRESENT state of its inputs whatever they hold, and the
statistics read PRESENT cells alone). The property is checked over the whole input domain, not a
list of the producers known: every datatype, with declared missing codes and without, a corpus of
tokens and Python values, lists and derived cells; the named producers are labelled rows of it, each
asserted UNKNOWN. A pack's spellings reach the core as declared codes (D437), so they are the
"declared code" rows; the pack's own are tested in ``tests/packs/onco/test_unknown_states.py``.

The data is synthetic and domain-neutral.
"""

from datetime import UTC, date, datetime
from itertools import product
from typing import Any

import pytest

from aibi.core.engine import build
from aibi.core.engine.data import EMPTY, PRESENT, Cell
from aibi.core.schema.descriptors import ColumnDescriptor, ColumnFields, ListSyntax
from aibi.core.schema.semantics import ObservationState
from aibi.core.store import derive
from aibi.core.store.cells import MAX_LIST_TEXT, ColumnCells
from aibi.core.store.sources import ErrorCell, Parsed, SourceValue
from aibi.core.store.statistics import column_statistics
from aibi.core.store.tables import build_table

UNKNOWN = ObservationState.UNKNOWN
NOT_ASSESSED = ObservationState.NOT_ASSESSED
NOT_APPLICABLE = ObservationState.NOT_APPLICABLE
NON_PRESENT = (UNKNOWN, NOT_ASSESSED, NOT_APPLICABLE)

DATATYPES: tuple[str | None, ...] = (
    None,
    "string",
    "category",
    "integer",
    "number",
    "boolean",
    "date",
    "datetime",
    "time_offset",
)
"""Every scalar datatype and the undeclared column; ``list<category>`` has its own tests below."""

CODES = {"NA": "UNKNOWN", "Unknown": "NOT_ASSESSED", " ": "NOT_APPLICABLE"}
"""Declared missing codes, one for each state, one of them a spelling of a value (``Unknown``)."""

TOKENS: tuple[SourceValue, ...] = (
    "",
    " ",
    "NA",
    "Unknown",
    "   ",
    ">89",
    "12 mg",
    "1,5",
    "1",
    "1.5",
    "3.5",
    "99999999999999999999",
    "1e400",
    "NaN",
    "Infinity",
    "-Infinity",
    "true",
    "2020-01-01",
    "2020-13-45",
    "Jan 2020",
    "maybe",
    "2020-01-01T00:00:00Z",
    None,
    1,
    1.5,
    float("nan"),
    float("inf"),
    True,
    ErrorCell("#DIV/0!"),
    date(2020, 1, 1),
    datetime(2020, 1, 1, tzinfo=UTC),
)


def fields(datatype: str | None, **given: Any) -> ColumnFields:
    descriptor = build.column("t.c", datatype, **given)
    assert isinstance(descriptor, ColumnDescriptor)
    return descriptor.fields


def carries_no_value(cell: Cell) -> None:
    """The property, for a cell and, for a list's PRESENT cell, each of its items."""
    if cell.state is not PRESENT:
        assert cell.value is None, cell
        return
    assert cell.value is not None, cell
    if isinstance(cell.value, tuple):
        for item in cell.value:
            carries_no_value(item)


@pytest.mark.parametrize(
    ("datatype", "codes", "token"), list(product(DATATYPES, (None, CODES), TOKENS))
)
def test_a_cell_that_is_not_present_carries_no_value(
    datatype: str | None, codes: dict[str, str] | None, token: SourceValue
) -> None:
    carries_no_value(ColumnCells(datatype, codes).cell(token))


def test_the_corpus_reaches_every_state_under_every_datatype() -> None:
    reached: set[tuple[str | None, ObservationState]] = set()
    for datatype, codes, token in product(DATATYPES, (None, CODES), TOKENS):
        reached.add((datatype, ColumnCells(datatype, codes).cell(token).state))
    for datatype in DATATYPES:
        # Every datatype has a value (PRESENT) and its cells in each other state: a corpus that
        # made no cell PRESENT or no cell UNKNOWN would hold the property of nothing.
        assert (datatype, PRESENT) in reached, datatype
        for state in NON_PRESENT:
            assert (datatype, state) in reached, (datatype, state)


LIST_SYNTAXES = (
    ListSyntax(format="json"),
    ListSyntax(format="python"),
    ListSyntax(format="delimited", delimiter=";"),
)
LIST_TOKENS = (
    '["a"]',
    '["a", null, ""]',
    "a;;b",
    "a;x",
    '["a"',
    '"a"',
    "{}",
    "[1, [2]]",
    '[1, {"x": 1}]',
    "NaN",
)


@pytest.mark.parametrize("syntax", LIST_SYNTAXES, ids=lambda one: one.format)
def test_a_list_cell_and_its_items_carry_no_value_unless_present(syntax: ListSyntax) -> None:
    for codes, token in product((None, {"a": "NOT_ASSESSED", "x": "UNKNOWN"}), LIST_TOKENS):
        carries_no_value(ColumnCells("list<category>", codes, syntax).cell(token))


@pytest.mark.parametrize("syntax", LIST_SYNTAXES, ids=lambda one: one.format)
def test_list_cells_that_do_not_parse_are_unknown_and_carry_no_value(syntax: ListSyntax) -> None:
    unparsed: list[SourceValue] = ["", None, float("nan"), ErrorCell("#N/A")]
    if syntax.format != "delimited":
        # A list that is not a list, one whose items are not scalars, text that does not parse,
        # text over MAX_LIST_TEXT, and a lone surrogate among the items (which no text encodes).
        unparsed += [
            '["a"',
            "not a list",
            "[[1]]",
            '[{"k": 1}]',
            "[" + "1," * MAX_LIST_TEXT + "1]",
            '["\\ud800"]',
            "['\\ud800']",
        ]
    for token in unparsed:
        cell = ColumnCells("list<category>", None, syntax).cell(token)
        assert cell.state is UNKNOWN, (syntax.format, str(token)[:20])
        assert cell.value is None
    assert ColumnCells("list<category>", None, None).cell("a;b").state is UNKNOWN


# The named producers of UNKNOWN, each asserted UNKNOWN: their tokens are in the corpus above (the
# last three declare a code of their own, which the corpus does with ``CODES``). The property above
# holds them and every other; these are the ones known, so that the words "no usable value" can be
# read against a list.
PRODUCERS: tuple[tuple[str, str | None, dict[str, str] | None, SourceValue], ...] = (
    ("an empty cell", "number", None, ""),
    ("a null cell", "number", None, None),
    ("a cell of spaces under a number", "number", None, "   "),
    ("a top-coded number", "number", None, ">89"),
    ("a number with units written in", "number", None, "12 mg"),
    ("a decimal comma", "number", None, "1,5"),
    ("a fraction under an integer", "integer", None, "3.5"),
    ("an integer beyond 64 bits", "integer", None, "99999999999999999999"),
    ("a number beyond a double", "number", None, "1e400"),
    ("a month written in words", "date", None, "Jan 2020"),
    ("a date that is none", "date", None, "2020-13-45"),
    ("a boolean that is neither", "boolean", None, "maybe"),
    ("NaN text", "number", None, "NaN"),
    ("a double NaN", "number", None, float("nan")),
    ("a double infinity", "number", None, float("inf")),
    ("a spreadsheet error cell", "string", None, ErrorCell("#DIV/0!")),
    ("a declared code of UNKNOWN", "category", {"Pending": "UNKNOWN"}, "Pending"),
    ("a declared code that is a real answer elsewhere", "category", {"None": "UNKNOWN"}, "None"),
    ("a declared code under a number", "number", {"-": "UNKNOWN"}, "-"),
)


@pytest.mark.parametrize(
    ("label", "datatype", "codes", "token"), PRODUCERS, ids=[row[0] for row in PRODUCERS]
)
def test_the_named_producers_of_unknown_give_unknown_and_no_value(
    label: str, datatype: str | None, codes: dict[str, str] | None, token: SourceValue
) -> None:
    cells = ColumnCells(datatype, codes)
    cell = cells.cell(token)
    assert cell.state is UNKNOWN, label
    assert cell.value is None, label


def test_a_value_of_the_datatype_is_not_unknown_the_control() -> None:
    assert ColumnCells("number", None).cell("61") == Cell(PRESENT, 61)
    assert ColumnCells("category", {"NA": "UNKNOWN"}).cell("Yes") == Cell(PRESENT, "Yes")


# Derived cells.


def computed(
    sources: dict[str, tuple[ColumnFields, list[SourceValue]]], derived_fields: ColumnFields
) -> list[Cell]:
    """The derived column's cells through ``build_table``, as the engine builds a table."""
    names = list(sources)
    rows = list(zip(*(values for _, values in sources.values()), strict=True))
    columns = {name: given for name, (given, _) in sources.items()} | {"out": derived_fields}
    typed = build_table("t", Parsed(tuple(names), rows), names, columns)
    return list(typed.cells["out"])


def arith(operator: str, datatype: str = "number") -> ColumnFields:
    return fields(datatype, derived={"op": "arith", "operator": operator, "args": ["a", "b"]})


def test_a_derived_cell_inherits_the_first_non_present_state_of_its_inputs() -> None:
    number = fields("number", missing_codes={"na": "NOT_APPLICABLE", "nd": "NOT_ASSESSED"})
    found = computed(
        {
            "a": (number, ["1", "", "nd", "na", "na", "1"]),
            "b": (number, ["1", "1", "1", "nd", "", "na"]),
        },
        arith("+"),
    )
    assert [cell.state for cell in found] == [
        PRESENT,
        UNKNOWN,
        NOT_ASSESSED,
        NOT_ASSESSED,
        UNKNOWN,
        NOT_APPLICABLE,
    ]
    for cell in found:
        carries_no_value(cell)


def test_a_derived_cell_that_is_undefined_is_unknown_and_carries_no_value() -> None:
    number = fields("number")
    integer = fields("integer")
    int64 = str(2**63 - 1)
    cases: dict[str, list[Cell]] = {
        "division by zero": computed({"a": (number, ["1"]), "b": (number, ["0"])}, arith("/")),
        "a result beyond a double": computed(
            {"a": (number, ["1e308"]), "b": (number, ["10"])}, arith("*")
        ),
        "an integer quotient that is not an integer": computed(
            {"a": (integer, ["1"]), "b": (integer, ["2"])}, arith("/", "integer")
        ),
        "an integer sum beyond 64 bits": computed(
            {"a": (integer, [int64]), "b": (integer, ["1"])}, arith("+", "integer")
        ),
        "an integer difference beyond 64 bits": computed(
            {"a": (integer, [str(-(2**63))]), "b": (integer, ["1"])}, arith("-", "integer")
        ),
        "a value missing from a value map": computed(
            {"a": (fields("category"), ["X"])},
            fields("category", derived={"op": "value_map", "input": "a", "map": {"F": "female"}}),
        ),
        "a mapped value that does not parse as the datatype": computed(
            {"a": (fields("category"), ["M", "F"])},
            fields(
                "integer", derived={"op": "value_map", "input": "a", "map": {"M": "one", "F": "1"}}
            ),
        ),
        "a mapped value that is not a date": computed(
            {"a": (fields("category"), ["M"])},
            fields("date", derived={"op": "value_map", "input": "a", "map": {"M": "2020-13-45"}}),
        ),
        "a unit conversion beyond a double": computed(
            {"a": (fields("number", units="m"), ["1e308"])},
            fields("number", derived={"op": "unit_convert", "input": "a", "units": "mm"}),
        ),
    }
    for label, cells in cases.items():
        assert cells[0].state is UNKNOWN, label
        assert cells[0].value is None, label
        for cell in cells:
            carries_no_value(cell)


def test_the_value_map_keeps_the_values_that_do_parse_the_control() -> None:
    found = computed(
        {"a": (fields("category"), ["M", "F"])},
        fields("integer", derived={"op": "value_map", "input": "a", "map": {"M": "one", "F": "1"}}),
    )
    assert found == [EMPTY, Cell(PRESENT, 1)]


# The engine reads no value of a cell that is not PRESENT: a cell given a stray value anyway (a
# construction the typed builders never make, here to show the engine would not read it) changes no
# result.


def stray(state: ObservationState, value: object) -> Cell:
    return Cell(state, value)  # type: ignore[arg-type]


STRAY_OPERATIONS: tuple[tuple[str, ColumnFields, dict[str, ColumnFields], object], ...] = (
    ("arith", arith("+"), {"a": fields("number"), "b": fields("number")}, 1.0),
    ("integer arith", arith("/", "integer"), {"a": fields("integer"), "b": fields("integer")}, 1),
    (
        "unit_convert",
        fields("number", derived={"op": "unit_convert", "input": "a", "units": "mm"}),
        {"a": fields("number", units="m")},
        1.0,
    ),
    (
        "value_map",
        fields("category", derived={"op": "value_map", "input": "a", "map": {"M": "male"}}),
        {"a": fields("category")},
        "M",
    ),
    (
        "date_diff",
        fields("number", derived={"op": "date_diff", "from": "a", "to": "b", "units": "d"}),
        {"a": fields("date"), "b": fields("date")},
        date(2020, 1, 1),
    ),
)


@pytest.mark.parametrize("state", NON_PRESENT)
@pytest.mark.parametrize(
    ("label", "derived_fields", "inputs", "value"),
    STRAY_OPERATIONS,
    ids=[row[0] for row in STRAY_OPERATIONS],
)
def test_a_derived_cell_reads_no_value_of_a_cell_that_is_not_present(
    label: str,
    derived_fields: ColumnFields,
    inputs: dict[str, ColumnFields],
    value: object,
    state: ObservationState,
) -> None:
    columns = {**inputs, "out": derived_fields}
    for name in inputs:
        cells = {other: [Cell(PRESENT, value)] for other in inputs}  # type: ignore[arg-type]
        cells[name] = [stray(state, value)]
        assert derive.compute(derived_fields, cells, columns) == [Cell(state)], (label, name)


@pytest.mark.parametrize(
    ("datatype", "present", "other"),
    [
        ("category", "a", "zzz"),
        ("number", 1.5, 99.0),
        ("integer", 3, 99),
        ("date", date(2020, 1, 1), date(1999, 1, 1)),
        ("boolean", True, False),
        ("string", "a", "zzz"),
    ],
)
def test_the_statistics_read_present_cells_alone(
    datatype: str, present: object, other: object
) -> None:
    given = fields(
        datatype, **({"range": {"min": 0, "max": 100}} if datatype in ("number", "integer") else {})
    )
    clean = [
        stray(PRESENT, present),
        stray(PRESENT, present),
        EMPTY,
        Cell(NOT_ASSESSED),
        Cell(NOT_APPLICABLE),
    ]
    stray_cells = [*clean[:2], *(stray(cell.state, other) for cell in clean[2:])]
    with_stray = column_statistics(stray_cells, given, identifier=False)
    assert with_stray == column_statistics(clean, given, identifier=False)
    assert with_stray["states"] == {
        "PRESENT": 2,
        "NOT_APPLICABLE": 1,
        "NOT_ASSESSED": 1,
        "UNKNOWN": 1,
    }


def test_the_statistics_read_the_items_of_a_list_by_their_state() -> None:
    given = fields("list<category>", list_syntax={"format": "delimited", "delimiter": ";"})
    clean_items = (Cell(PRESENT, "a"), EMPTY, Cell(NOT_ASSESSED), Cell(NOT_APPLICABLE))
    stray_items = (
        Cell(PRESENT, "a"),
        *(stray(item.state, "zzz") for item in clean_items[1:]),
    )
    clean = [Cell(PRESENT, clean_items), Cell(NOT_ASSESSED)]
    with_stray = [Cell(PRESENT, stray_items), stray(NOT_ASSESSED, (Cell(PRESENT, "zzz"),))]
    found = column_statistics(with_stray, given, identifier=False)
    assert found == column_statistics(clean, given, identifier=False)
    assert found["distribution"] == {
        "kind": "categories",
        "categories": [["a", 1]],
        "multi_membership": True,
    }


def test_a_number_that_is_not_finite_is_never_a_value() -> None:
    for datatype in DATATYPES:
        for token in (float("nan"), float("inf"), float("-inf")):
            cell = ColumnCells(datatype, None).cell(token)
            assert cell.state is UNKNOWN, (datatype, token)
            assert cell.value is None
    for datatype in ("number", "integer", "time_offset"):
        for token in ("NaN", "Infinity", "-Infinity", "1e400"):
            cell = ColumnCells(datatype, None).cell(token)
            assert cell.state is UNKNOWN, (datatype, token)
            assert cell.value is None

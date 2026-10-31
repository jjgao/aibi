"""The oncology pack's spellings of a missing cell, read by the core as UNKNOWN cells that carry no
value (SPEC D437, D443, §6.2, §6.4).

The core's own property is ``tests/core/store/test_state_carries_no_value.py``: a cell that is not
PRESENT carries no value, and the engine reads none, so the web column screen's gloss of UNKNOWN,
"no usable value", is true of any cell in that state. A pack's spellings reach the core as the
``missing_codes`` its importer declares (D437), the same path as a curated code; these cases are the
pack's own: cBioPortal's ``Discrepancy`` (the sources recorded conflicting values, none used),
``Completed`` and ``Pending`` (no value yet), the spellings of ``NOT_AVAILABLE``, ``NULL``,
``MISSING`` and ``NA``, and a cell of spaces alone, each UNKNOWN with no value, through the cells a
column of the pack's declaration builds and through the statistics the column screen shows.

The pack is imported inside the test bodies alone (``conftest.py``).
"""

import importlib
from types import ModuleType

import pytest

from aibi.core.engine import build
from aibi.core.engine.data import PRESENT
from aibi.core.schema.descriptors import ColumnDescriptor
from aibi.core.schema.semantics import ObservationState
from aibi.core.store.cells import ColumnCells
from aibi.core.store.sources import Parsed
from aibi.core.store.statistics import column_statistics
from aibi.core.store.tables import build_table

UNKNOWN = ObservationState.UNKNOWN
NOT_APPLICABLE = ObservationState.NOT_APPLICABLE

CASES = (
    ("Discrepancy", UNKNOWN),
    ("DISCREPANCY", UNKNOWN),
    ("Completed", UNKNOWN),
    ("Pending", UNKNOWN),
    ("PENDING", UNKNOWN),
    ("Not Available", UNKNOWN),
    ("NOT_AVAILABLE", UNKNOWN),
    ("Null", UNKNOWN),
    ("Missing", UNKNOWN),
    ("[Not Available]", UNKNOWN),
    ("NA", UNKNOWN),
    (" ", UNKNOWN),
    ("Not applicable", NOT_APPLICABLE),
    ("NOT_APPLICABLE", NOT_APPLICABLE),
)
"""A cell as written and the state D437 gives it; the last two are NOT_APPLICABLE, apart."""


def codes_of(missing: ModuleType, cells: list[str]) -> dict[str, str]:
    """The ``missing_codes`` the importer declares for a column holding these cells (D437)."""
    return {**missing.FIXED, **missing.additions(cells)}


@pytest.fixture
def missing() -> ModuleType:
    return importlib.import_module("aibi.packs.onco.missing")


@pytest.mark.parametrize(("cell", "state"), CASES)
@pytest.mark.parametrize("datatype", ["string", "category", "number", "integer", "date", "boolean"])
def test_the_packs_spellings_are_cells_of_their_state_with_no_value(
    missing: ModuleType, cell: str, state: ObservationState, datatype: str
) -> None:
    assert missing.classify(cell) == state.value
    # A column's codes are FIXED with its additions (a spelling outside FIXED, as written).
    found = ColumnCells(datatype, codes_of(missing, [cell])).cell(cell)
    assert found.state is state
    assert found.value is None


def test_the_columns_unknown_count_counts_them_and_the_screen_reads_no_value(
    missing: ModuleType,
) -> None:
    cells = [cell for cell, _ in CASES] + ["Yes", "No", ""]
    given = build.column("t.c", "category", missing_codes=codes_of(missing, cells))
    assert isinstance(given, ColumnDescriptor)
    typed = build_table(
        "t", Parsed(("c",), [(cell,) for cell in cells]), ["c"], {"c": given.fields}
    )
    column = typed.cells["c"]
    assert [one.state for one in column] == [
        *[state for _, state in CASES],
        PRESENT,
        PRESENT,
        UNKNOWN,
    ]
    assert all(one.value is None for one in column if one.state is not PRESENT)
    statistics = column_statistics(column, given.fields, identifier=False)
    unknown = sum(1 for _, state in CASES if state is UNKNOWN) + 1
    assert statistics["states"] == {
        "PRESENT": 2,
        "NOT_APPLICABLE": 2,
        "NOT_ASSESSED": 0,
        "UNKNOWN": unknown,
    }
    # The categories are the two values; no spelling of a missing cell is one of them.
    distribution = statistics["distribution"]
    assert isinstance(distribution, dict)
    categories = distribution["categories"]
    assert isinstance(categories, list)
    assert categories == [["No", 1], ["Yes", 1]]

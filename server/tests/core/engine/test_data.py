"""Cells, tables and releases in memory (§6.2, §12.2), and the fixture builder."""

from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta, timezone
from typing import Any

import pytest

from aibi.core.engine import build
from aibi.core.engine.data import (
    EMPTY,
    PRESENT,
    Cell,
    Release,
    ReleaseError,
    Table,
    items,
    key_part,
    missing,
    present,
    state_of,
)
from aibi.core.schema.semantics import ObservationState

CODES = {"N/A": "NOT_APPLICABLE", "not done": "NOT_ASSESSED", "?": "UNKNOWN"}


@pytest.mark.parametrize(
    ("token", "state"),
    [
        ("N/A", ObservationState.NOT_APPLICABLE),
        ("not done", ObservationState.NOT_ASSESSED),
        ("?", ObservationState.UNKNOWN),
        (None, ObservationState.UNKNOWN),
        ("", ObservationState.UNKNOWN),
        ("NaN", ObservationState.UNKNOWN),
        ("-Infinity", ObservationState.UNKNOWN),
        ("No", PRESENT),
        ("0", PRESENT),
        ("n/a", PRESENT),
    ],
)
def test_a_token_takes_its_state_from_the_missing_codes(
    token: str | None, state: ObservationState
) -> None:
    assert state_of(token, CODES) == state


def test_a_declared_code_wins_over_the_empty_cell_rule() -> None:
    assert state_of("", {"": "NOT_APPLICABLE"}) == ObservationState.NOT_APPLICABLE
    assert state_of("", None) == ObservationState.UNKNOWN


def _one_column(datatype: str, value: Cell) -> Release:
    descriptors = [
        build.dataset(),
        build.table("t", ["k"]),
        build.column("t.k", "string"),
        build.column("t.c", datatype),
    ]
    tables = {"t": Table("t", ({"c": value},))}
    return Release("d", "sha256:" + "0" * 64, tuple(descriptors), tables)


@pytest.mark.parametrize(
    ("datatype", "value"),
    [
        ("integer", present("1")),
        ("integer", present(1.0)),
        ("integer", present(True)),
        ("number", present(False)),
        ("number", present(float("nan"))),
        ("number", present(float("inf"))),
        ("string", present(1)),
        ("category", present(True)),
        ("boolean", present(1)),
        ("date", present(datetime(2026, 1, 1, tzinfo=UTC))),
        ("date", present("2026-01-01")),
        ("datetime", present(datetime(2026, 1, 1))),  # naive
        ("datetime", present(date(2026, 1, 1))),
        ("list<category>", present("a")),
        ("list<category>", items(Cell(PRESENT, 1))),  # pyright: ignore[reportArgumentType]
        ("list<category>", items(missing(ObservationState.ABSENT))),
        ("string", missing(ObservationState.ABSENT)),
        ("string", Cell(ObservationState.UNKNOWN, "x")),
    ],
)
def test_cells_of_the_wrong_type_or_state_are_refused(datatype: str, value: Cell) -> None:
    with pytest.raises(ReleaseError):
        _one_column(datatype, value)


@pytest.mark.parametrize(
    ("datatype", "value"),
    [
        ("integer", present(2**60)),
        ("number", present(1)),
        ("number", present(1.5)),
        ("time_offset", present(-3)),
        ("date", present(date(2026, 1, 1))),
        ("datetime", present(datetime(2026, 1, 1, tzinfo=timezone(timedelta(hours=-5))))),
        ("boolean", present(False)),
        ("list<category>", items("a", missing(ObservationState.NOT_ASSESSED), EMPTY)),
        ("list<category>", items()),
        ("string", missing(ObservationState.NOT_APPLICABLE)),
    ],
)
def test_cells_of_their_columns_type_are_kept(datatype: str, value: Cell) -> None:
    assert _one_column(datatype, value).rows("t").cell(0, "c") == value


def test_a_release_names_what_it_cannot_read() -> None:
    descriptors = (build.dataset(), build.table("t", ["k"]), build.column("t.k", "string"))
    with pytest.raises(ReleaseError, match="no table descriptor"):
        Release("d", "m", descriptors, {"u": Table("u", ())})
    with pytest.raises(ReleaseError, match="another id"):
        Release("d", "m", descriptors, {"t": Table("u", ())})
    with pytest.raises(ReleaseError, match="in no column"):
        Release("d", "m", descriptors, {"t": Table("t", ({"x": present("a")},))})


def test_parent_keys_are_unique(city: Callable[..., Release]) -> None:
    with pytest.raises(ReleaseError, match="not unique"):
        city({"establishments": [{"establishment_id": "e1"}, {"establishment_id": "e1"}]})


def test_a_row_may_leave_a_column_out_as_an_empty_cell(city: Callable[..., Release]) -> None:
    release = city({"establishments": [{"establishment_id": "e1"}]})
    assert release.rows("establishments").cell(0, "cuisine") == EMPTY
    assert release.rows("nowhere").rows == ()


def test_children_and_parents_follow_keys(city: Callable[..., Release]) -> None:
    release = city(
        {
            "establishments": [{"establishment_id": "e1"}, {"establishment_id": "e2"}],
            "inspections": [
                {"inspection_id": "i1", "establishment_id": "e2"},
                {"inspection_id": "i2", "establishment_id": None},
                {"inspection_id": "i3", "establishment_id": "gone"},
                {"inspection_id": "i4", "establishment_id": "e2"},
            ],
        }
    )
    rel = "rel:inspections.establishment"
    assert [release.parent(rel, row) for row in range(4)] == [1, None, None, 1]
    assert release.children(rel, 1) == (0, 3)
    assert release.children(rel, 0) == ()


def test_booleans_are_not_the_numbers_python_equates_with_them() -> None:
    assert key_part(True) != key_part(1)
    assert key_part(False) != key_part(0)
    assert key_part(1) == key_part(1.0)
    descriptors = [
        build.dataset(),
        build.table("p", ["k"]),
        build.column("p.k", "boolean"),
        build.table("c", ["id"]),
        build.column("c.id", "string"),
        build.column("c.k", "integer"),
        build.relationship("c", ["k"], "p"),
    ]
    release = build.release(
        descriptors, {"p": [{"k": True}], "c": [{"id": "x", "k": 1}]}, check=False
    )
    assert release.parent("rel:c.k", 0) is None


def test_coverage_tables_are_those_with_the_role_and_those_a_coverage_names(
    city: Callable[..., Release],
) -> None:
    assert city().coverage_tables == {
        "inspection_checklists",
        "checklist_items",
        "checked_appliances",
    }


@pytest.mark.parametrize(
    ("value", "datatype", "expected"),
    [
        (None, "string", EMPTY),
        ("N/A", "string", missing(ObservationState.NOT_APPLICABLE)),
        ("x", "string", present("x")),
        ("2026-02-03", "date", present(date(2026, 2, 3))),
        (
            "2026-02-03T04:05:06+02:00",
            "datetime",
            present(datetime(2026, 2, 3, 4, 5, 6, tzinfo=timezone(timedelta(hours=2)))),
        ),
        (
            ["a", "N/A", None],
            "list<category>",
            items("a", missing(ObservationState.NOT_APPLICABLE), EMPTY),
        ),
        (present(3), "integer", present(3)),
    ],
)
def test_the_builder_types_plain_values(value: Any, datatype: str, expected: Cell) -> None:
    assert build.cell(value, datatype, CODES) == expected


def test_the_builder_refuses_an_invalid_release() -> None:
    descriptors = [build.dataset(), build.relationship("c", ["k"], "p")]
    with pytest.raises(ReleaseError, match="UNKNOWN_DESCRIPTOR at /1/fields/child_table"):
        build.release(descriptors, {})

"""A cohort's members' unit keys (§7.6, §9.3, §13.3; D331, D333): listed by the reference
evaluator and by the SQL compiler, which must agree over keys of every stored type, and ordered
as the canonical form orders an ``ids`` leaf's members."""

import json
import time
from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime, timedelta, timezone
from typing import Any

import pytest
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

from aibi.core.engine import build
from aibi.core.engine import members as members_module
from aibi.core.engine.data import Release
from aibi.core.engine.members import keys, ordered, select, written
from aibi.core.engine.resolve import ResolvedCohort
from aibi.core.engine.sql import CompiledMembers, compile_members
from aibi.core.engine.worker import CallerDeadline, QueryError, Rows
from aibi.core.schema.ids import MAX_SAFE_INTEGER
from aibi.core.schema.limits import MAX_LISTED

Listed = Callable[..., tuple[tuple[Any, ...], ...]]
Canon = Callable[..., Any]

DATATYPES: tuple[str, ...] = ("integer", "number", "boolean", "string", "date", "datetime")
FLAG = {"kind": "value", "column": "things.flag", "values": ["yes"]}


def _things(datatypes: Sequence[str | None], rows: Sequence[dict[str, object]]) -> Release:
    names = [f"k{index}" for index in range(len(datatypes))]
    return build.release(
        [
            build.dataset(),
            build.table("things", names),
            *(
                build.column(f"things.{name}", datatype)
                for name, datatype in zip(names, datatypes, strict=True)
            ),
            build.column(
                "things.flag",
                "category",
                permissible_values={"values": [{"value": "yes"}, {"value": "no"}]},
            ),
        ],
        {"things": list(rows)},
    )


def _cohort(canon: Canon, release: Release, clauses: Sequence[Any] = (FLAG,)) -> ResolvedCohort:
    written_document = {
        "aibi": "1",
        "dataset": "d",
        "unit": "things",
        "cohorts": {"c": {"all": list(clauses)}},
    }
    found = canon(written_document, release)
    assert found.refusals == [], found.refusals
    return found.cohorts["c"].resolved


_HUGE = st.integers(MAX_SAFE_INTEGER + 1, 2**63 - 1) | st.integers(-(2**63), -MAX_SAFE_INTEGER - 1)
_PARTS: dict[str, st.SearchStrategy[Any]] = {
    "integer": st.integers(-(2**63), 2**63 - 1) | _HUGE,
    "number": st.floats(allow_nan=False, allow_infinity=False).map(lambda v: v + 0.0),
    "boolean": st.booleans(),
    "string": st.text(
        st.characters(codec="utf-8", exclude_categories=("Cs",)), min_size=0, max_size=6
    ),
    "date": st.dates(min_value=date(1, 1, 1), max_value=date(9999, 12, 31)),
    "datetime": st.datetimes(
        min_value=datetime(1, 1, 2), max_value=datetime(9999, 12, 30), timezones=st.just(UTC)
    ).map(lambda moment: moment.astimezone(timezone(timedelta(hours=5, minutes=30)))),
}


@st.composite
def _tables(draw: st.DrawFn) -> tuple[list[str], list[dict[str, object]]]:
    """A key of one column or two, of any stored types, and rows with distinct keys, each
    ``yes``, ``no`` or empty (UNKNOWN) in ``flag``."""
    datatypes = draw(st.lists(st.sampled_from(DATATYPES), min_size=1, max_size=2))
    parts = st.tuples(*(_PARTS[datatype] for datatype in datatypes))
    found = draw(st.lists(parts, max_size=12, unique_by=lambda key: json.dumps(written(key))))
    flags = draw(st.lists(st.sampled_from(["yes", "no", None]), min_size=len(found)))
    rows = [
        {**{f"k{at}": value for at, value in enumerate(key)}, "flag": flag}
        for key, flag in zip(found, flags, strict=False)
    ]
    return datatypes, rows


@settings(max_examples=150, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(table=_tables())
def test_the_compiler_lists_the_evaluator_s_keys_of_every_stored_type(
    canon: Canon, listed: Listed, table: tuple[list[str], list[dict[str, object]]]
) -> None:
    datatypes, rows = table
    cohort = _cohort(canon, _things(datatypes, rows))
    by_sql = listed(cohort)
    by_evaluator = keys(cohort)
    assert by_sql == tuple(by_evaluator)
    assert [written(key) for key in ordered(by_sql)] == [
        written(key) for key in ordered(by_evaluator)
    ]
    for key in by_sql:
        for part, datatype in zip(key, datatypes, strict=True):
            assert type(part) is {"integer": int, "number": float, "boolean": bool}.get(
                datatype, type(part)
            )


def test_keys_are_ordered_by_their_serialisation_as_utf_16_code_units(
    canon: Canon, listed: Listed
) -> None:
    names = ["b", "a", "\U0001f600", "｡", "ab", "A"]
    release = _things(["string"], [{"k0": name, "flag": "yes"} for name in names])
    found = ordered(listed(_cohort(canon, release)))
    independent = sorted(
        names, key=lambda name: json.dumps(name, ensure_ascii=False).encode("utf-16-be")
    )
    assert [key[0] for key in found] == independent
    assert [key[0] for key in found] == ["A", "a", "ab", "b", "\U0001f600", "｡"]


def test_integers_are_ordered_as_the_canonical_form_writes_them_not_by_value(
    canon: Canon, listed: Listed
) -> None:
    given = [9, 10, -3, 2**60, -(2**60), 0]
    release = _things(["integer"], [{"k0": value, "flag": "yes"} for value in given])
    found = ordered(listed(_cohort(canon, release)))
    assert [written(key) for key in found] == [
        [str(-(2**60))],
        [str(2**60)],
        [-3],
        [0],
        [10],
        [9],
    ]


def test_a_composite_key_is_its_values_in_key_order_as_the_canonical_form_writes_them(
    canon: Canon, listed: Listed
) -> None:
    release = _things(
        ["date", "datetime", "number"],
        [
            {"k0": "2024-02-29", "k1": "2024-03-01T01:30:00+02:00", "k2": 2.0, "flag": "yes"},
            {"k0": "0005-01-01", "k1": "1969-12-31T23:59:59.5Z", "k2": -0.25, "flag": "yes"},
            {"k0": "2000-01-01", "k1": "2000-01-01T00:00:00Z", "k2": 1.5, "flag": "no"},
        ],
    )
    cohort = _cohort(canon, release)
    assert [written(key) for key in ordered(listed(cohort))] == [
        ["0005-01-01", "1969-12-31T23:59:59.5Z", -0.25],
        ["2024-02-29", "2024-02-29T23:30:00Z", 2],
    ]


def test_a_page_is_the_keys_from_its_offset_in_order_and_says_whether_more_follow() -> None:
    given = [(index,) for index in (4, 2, 0, 3, 1)]
    assert select(given, 0, 2) == ([(0,), (1,)], True)
    assert select(given, 3, 2) == ([(3,), (4,)], False)
    assert select(given, 4, 10) == ([(4,)], False)
    assert select(given, 7, 1) == ([], False)
    assert select([], 0, 1) == ([], False)


def test_a_page_across_many_sorted_runs_is_the_whole_order_s_slice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(members_module, "DEADLINE_KEYS", 3)
    given = [(f"k{(index * 7) % 20}",) for index in range(20)]
    whole = sorted(given, key=lambda key: json.dumps(key, ensure_ascii=False).encode("utf-16-be"))
    assert ordered(given) == whole
    for offset in range(0, 22, 3):
        for limit in (1, 4, 20):
            assert select(given, offset, limit) == (
                whole[offset : offset + limit],
                offset + limit < 20,
            )


def test_the_server_stops_keying_sorting_and_merging_keys_at_the_call_s_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    given = [(index,) for index in range(10)]
    with pytest.raises(CallerDeadline):
        select(given, 0, 1, ends=time.monotonic() - 1)
    monkeypatch.setattr(members_module, "DEADLINE_KEYS", 2)
    moments = iter([0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 2.0])
    monkeypatch.setattr(members_module.time, "monotonic", lambda: next(moments))
    with pytest.raises(CallerDeadline):
        select(given, 8, 1, ends=1.0)
    monkeypatch.undo()
    assert select(given, 0, 1, ends=time.monotonic() + 60) == ([(0,)], True)


def test_the_members_query_reads_the_key_columns_of_the_unit_s_members_alone(
    canon: Canon, blobs: Callable[[Release], dict[str, Any]]
) -> None:
    release = _things(["date", "datetime", "string"], [])
    compiled = compile_members(_cohort(canon, release), blobs(release))
    assert compiled.kinds == ("date32", "timestamp", "string")
    assert compiled.values == frozenset({2})
    assert "DATE_DIFF(" in compiled.statement
    assert "EPOCH_US(" in compiled.statement
    assert "ORDER BY" in compiled.statement
    assert compiled.statement.endswith(f"LIMIT {MAX_LISTED + 1}")
    assert "1970-01-01" in compiled.parameters_json().values()


@pytest.mark.parametrize(
    ("kind", "raw"),
    [
        ("int64", 1.5),
        ("int64", True),
        ("bool", 2),
        ("string", 3),
        ("float64", "x"),
        ("float64", float("inf")),
        ("date32", 10**12),
    ],
)
def test_a_members_answer_it_cannot_give_is_a_fault(kind: str, raw: object) -> None:
    compiled = CompiledMembers("SELECT 1", {}, (kind,))  # pyright: ignore[reportArgumentType]
    column: Any = [raw]
    with pytest.raises(QueryError):
        compiled.read(Rows([column], 1))[0]


@pytest.mark.parametrize("width", [0, 2])
def test_a_members_answer_of_another_width_is_a_fault(width: int) -> None:
    compiled = CompiledMembers("SELECT 1", {}, ("int64",))
    column: Any = [1]
    with pytest.raises(QueryError):
        compiled.read(Rows([column] * width, 1))


def test_listed_keys_are_made_as_they_are_read() -> None:
    compiled = CompiledMembers("SELECT 1", {}, ("int64", "string"))
    numbers: Any = [3, 1]
    names: Any = ["b", "a"]
    found = compiled.read(Rows([numbers, names], 2))
    assert (len(found), found[0], found[-1], list(found[0:2])) == (
        2,
        (3, "b"),
        (1, "a"),
        [(3, "b"), (1, "a")],
    )
    with pytest.raises(IndexError):
        found[2]


def _noncharacter(value: object) -> bool:
    """Whether text holds a noncharacter, which no document holds (§7.1)."""
    return isinstance(value, str) and any(
        0xFDD0 <= ord(c) <= 0xFDEF or ord(c) & 0xFFFE == 0xFFFE for c in value
    )


def _as_written(part: Any) -> Any:
    """A key's value as a document writes it: a string starting with ``$`` as ``$$…`` (§7.1)."""
    return "$" + part if isinstance(part, str) and part.startswith("$") else part


@settings(max_examples=150, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(table=_tables())
def test_a_page_s_keys_written_into_an_ids_leaf_give_the_same_members(
    canon: Canon, table: tuple[list[str], list[dict[str, object]]]
) -> None:
    datatypes, rows = table
    assume(not any(_noncharacter(value) for row in rows for value in row.values()))
    release = _things(datatypes, rows)
    page = [written(key) for key in ordered(keys(_cohort(canon, release)))]
    ids = {
        "kind": "ids",
        "ids": [{"dataset": "d", "key": [_as_written(part) for part in key]} for key in page],
    }
    if not page:
        return
    again = _cohort(canon, release, [ids])
    assert [written(key) for key in ordered(keys(again))] == page


def test_text_keys_starting_with_a_dollar_are_written_back_escaped(canon: Canon) -> None:
    names = ["$a", "$$b", "c$"]
    release = _things(["string"], [{"k0": name, "flag": "yes"} for name in names])
    page = [written(key) for key in ordered(keys(_cohort(canon, release)))]
    ids = {
        "kind": "ids",
        "ids": [{"dataset": "d", "key": [_as_written(part) for part in key]} for key in page],
    }
    assert [written(key) for key in ordered(keys(_cohort(canon, release, [ids])))] == page


def test_datetimes_before_the_year_1000_are_written_with_four_digits_and_read_back(
    canon: Canon, listed: Listed
) -> None:
    moments = ["0001-01-01T00:00:00Z", "0999-06-01T12:00:00Z", "2020-01-01T00:00:00Z"]
    release = _things(["datetime"], [{"k0": moment, "flag": "yes"} for moment in moments])
    found = [written(key) for key in ordered(listed(_cohort(canon, release)))]
    assert found == [[moment] for moment in moments]
    ids = {"kind": "ids", "ids": [{"dataset": "d", "key": key} for key in found]}
    assert [written(key) for key in ordered(keys(_cohort(canon, release, [ids])))] == found


def test_the_deadline_is_looked_at_once_a_run_and_every_65536_keys_merged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    looks: list[float] = []
    clock = time.monotonic

    def monotonic() -> float:
        looks.append(0.0)
        return clock()

    monkeypatch.setattr(members_module.time, "monotonic", monotonic)
    given = [(index,) for index in range(2**16 + 1)]
    found, more = select(given, 2**16, 1, ends=clock() + 600)
    assert (found, more, len(looks)) == ([(9,)], False, 5)

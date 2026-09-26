"""A cohort's members' unit keys (§7.6, §9.3, §13.3; D331, D333): listed by the reference
evaluator and by the SQL compiler, which must agree over keys of every stored type, and ordered
as the canonical form orders an ``ids`` leaf's members."""

import json
import time
from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime, timedelta, timezone
from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from aibi.core.engine import build
from aibi.core.engine.data import Release
from aibi.core.engine.members import keys, ordered, page, written
from aibi.core.engine.resolve import ResolvedCohort
from aibi.core.engine.sql import CompiledMembers, compile_members
from aibi.core.engine.worker import CallerDeadline, QueryError, Rows
from aibi.core.schema.ids import MAX_SAFE_INTEGER

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


def test_a_page_is_the_keys_from_its_offset_and_says_whether_more_follow() -> None:
    given = [(index,) for index in range(5)]
    assert page(given, 0, 2) == ([(0,), (1,)], True)
    assert page(given, 3, 2) == ([(3,), (4,)], False)
    assert page(given, 4, 10) == ([(4,)], False)
    assert page(given, 7, 1) == ([], False)


def test_the_server_stops_reading_and_ordering_keys_at_the_call_s_deadline(
    canon: Canon, listed: Listed
) -> None:
    release = _things(["integer"], [{"k0": 1, "flag": "yes"}])
    cohort = _cohort(canon, release)
    with pytest.raises(CallerDeadline):
        listed(cohort, ends=time.monotonic() - 1)
    with pytest.raises(CallerDeadline):
        ordered([(1,)], ends=time.monotonic() - 1)
    assert listed(cohort, ends=time.monotonic() + 60) == ((1,),)


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
    assert "1970-01-01" in compiled.parameters_json().values()


@pytest.mark.parametrize(
    ("kind", "raw"),
    [
        ("int64", 1.5),
        ("bool", 2),
        ("string", 3),
        ("float64", "x"),
        ("float64", float("inf")),
        ("date32", 10**12),
    ],
)
def test_a_members_answer_it_cannot_give_is_a_fault(kind: str, raw: object) -> None:
    compiled = CompiledMembers("SELECT 1", {}, (kind,))  # type: ignore[arg-type]
    column: Any = [raw]
    with pytest.raises(QueryError):
        compiled.read(Rows([column], 1))

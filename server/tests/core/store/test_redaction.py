"""Matching the terms of an erasure (SPEC §12.2; D223, D290): every spelling the engine accepts
of a value shares a reading with it, and a constant is matched as its place says, by every
reading where it may name the person and by its text alone where it names no one."""

import re
import time
from datetime import UTC, date, datetime, timedelta, timezone

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import JsonValue

from aibi.core.engine.resolve import typed_constant
from aibi.core.schema.ids import MAX_SAFE_INTEGER
from aibi.core.store.redaction import MARK, Place, Terms, Where
from aibi.core.store.sources import canonical_string

INSTANT = "2024-02-03T11:30:00+00:00"
"""A datetime cell's canonical string: loan 2's instant."""


@st.composite
def _instants(draw: st.DrawFn) -> str:
    """A datetime as the engine accepts one (RFC 3339 with an offset, any fraction whose digits
    beyond the microsecond are zeros), at any offset, with ``T`` or ``t`` and ``Z`` or ``z``."""
    moment = draw(
        st.datetimes(min_value=datetime(1, 1, 2), max_value=datetime(9999, 12, 30))
    ).replace(tzinfo=UTC)
    minutes = draw(st.integers(-(23 * 60 + 59), 23 * 60 + 59))
    local = moment.astimezone(timezone(timedelta(minutes=minutes)))
    micro = f"{local.microsecond:06d}"
    fraction = draw(
        st.sampled_from(
            ["", micro, micro.rstrip("0") or "0", micro + "0" * draw(st.integers(1, 30))]
        )
    )
    if local.microsecond and fraction == "":
        fraction = micro
    sign = "-" if minutes < 0 else "+"
    offset = f"{sign}{abs(minutes) // 60:02d}:{abs(minutes) % 60:02d}"
    if minutes == 0:
        offset = draw(st.sampled_from(["Z", "z", "+00:00", "-00:00"]))
    separator = draw(st.sampled_from("Tt"))
    written = (
        f"{local.year:04d}-{local.month:02d}-{local.day:02d}{separator}"
        f"{local.hour:02d}:{local.minute:02d}:{local.second:02d}"
    )
    return written + (f".{fraction}" if fraction else "") + offset


def _spellings() -> st.SearchStrategy[tuple[str, str]]:
    """A datatype and a string the engine accepts as a constant of it."""
    huge = st.integers(MAX_SAFE_INTEGER + 1, 2**63 - 1) | st.integers(
        -(2**63), -MAX_SAFE_INTEGER - 1
    )
    return st.one_of(
        st.tuples(st.just("datetime"), _instants()),
        st.tuples(st.just("date"), st.dates(min_value=date(1, 1, 1)).map(date.isoformat)),
        st.tuples(st.just("integer"), huge.map(str)),
        st.tuples(st.just("number"), huge.map(str)),
        st.tuples(st.sampled_from(["string", "category", "list<category>"]), st.text(min_size=1)),
    )


@given(_spellings())
def test_every_string_the_engine_accepts_as_a_constant_shares_a_reading_with_its_value(
    spelling: tuple[str, str],
) -> None:
    datatype, written = spelling
    value = typed_constant(written, datatype)
    assert value is not None
    term = canonical_string(value)
    assert term is not None
    assert Terms([term]).whole(written, Place.NAMING)
    assert Terms([term]).whole(written, Place.UNKNOWN)


@given(
    st.one_of(
        st.integers(-(2**63), 2**63 - 1),
        st.floats(allow_nan=False, allow_infinity=False),
    )
)
def test_every_json_number_the_engine_accepts_is_its_value_where_the_column_names_the_person(
    number: float,
) -> None:
    term = canonical_string(number)
    assert term is not None
    assert Terms([term]).whole(number, Place.NAMING)
    assert not Terms([term]).whole(number, Place.UNKNOWN)
    assert not Terms([term]).whole(number, Place.OTHER)


def test_a_json_integer_beyond_two_to_the_53_is_read_as_the_integer_it_is() -> None:
    assert Terms(["9007199254740993"]).whole(9007199254740993, Place.NAMING)
    assert Terms(["9007199254740993"]).whole("9007199254740993", Place.NAMING)
    assert Terms(["9007199254740993"]).json(9007199254740993) == MARK


def test_a_json_boolean_is_a_term_that_reads_as_it_where_the_column_names_the_person() -> None:
    assert Terms(["true"]).whole(True, Place.NAMING)
    assert not Terms(["true"]).whole(False, Place.NAMING)
    assert not Terms(["true"]).whole(True, Place.OTHER)


@pytest.mark.parametrize(
    ("term", "constant", "held"),
    [
        ("7", "7", True),
        ("7", "07", False),
        ("7", "7.0", False),
        ("7", "+7", False),
        ("7", " 7", True),
        ("7", "7e0", False),
        ("7", "no. 7", True),
        ("7", "7.0 kg", False),
        ("7", "-7", False),
        ("7", "10:7", False),
        ("m-17", "for m-17 only", True),
        ("2020-01-02", "2020-01-02", True),
        ("2020-01-02", "2020-01-02T00:00:00Z", False),
        ("2020-01-02", "2020-01-02T01:00:00+01:00", False),
        (INSTANT, "2024-02-03T11:30:00Z", False),
        (INSTANT, "2024-02-03T12:30:00+01:00", False),
        (INSTANT, INSTANT, True),
    ],
)
def test_a_string_on_a_column_that_names_no_one_is_matched_by_its_text_alone(
    term: str, constant: str, held: bool
) -> None:
    assert Terms([term]).in_constant(constant, Place.OTHER) is held


def test_a_json_number_is_never_a_term_on_a_column_that_names_no_one() -> None:
    assert not Terms(["7"], numbers=["7"]).in_constant(7, Place.OTHER)
    assert not Terms(["m-1"], ["7"], numbers=["7"]).in_constant(7.0, Place.UNKNOWN)


def test_a_number_below_the_person_is_held_only_where_its_column_names_their_rows() -> None:
    terms = Terms(["m-17"], ["2"], numbers=["2"])
    assert terms.in_constant("2", Place.NAMING)
    assert terms.in_constant(2.0, Place.NAMING)
    assert not terms.in_constant("2", Place.UNKNOWN)
    assert not terms.in_constant("2", Place.OTHER)
    assert terms.text("page 2, 2.0, 02 and +2") == "page 2, 2.0, 02 and +2"


def test_a_text_term_below_the_person_is_never_held_as_a_number_where_nothing_says_its_place() -> (
    None
):
    terms = Terms(["m-17"], ["02"])
    assert terms.constant("02", Place.UNKNOWN) == MARK
    assert terms.constant("2", Place.UNKNOWN) == "2"
    assert terms.constant("2", Place.NAMING) == MARK
    assert terms.text("page 2") == "page 2"


@pytest.mark.parametrize("given_as", [" 0017 ", "017", "1.7e1", "17.0", "+17"])
def test_a_string_that_is_the_person_s_number_in_another_spelling_is_erased_whole(
    given_as: str,
) -> None:
    terms = Terms(["17"], numbers=["17"])
    assert terms.constant(given_as, Place.UNKNOWN) == MARK
    assert terms.constant(given_as, Place.NAMING) == MARK


@pytest.mark.parametrize(
    "spelling",
    [
        "2024-02-03T11:30:00Z",
        "2024-02-03t11:30:00z",
        "2024-02-03 11:30:00",
        "2024-02-03T12:30:00+01:00",
        "2024-02-03T12:30:00+0100",
        "2024-02-03T12:30:00+01",
        "2024-02-03 12:30:00 +01:00",
        "2024-02-03T12:30:00.0000000000+01:00",
        "2024-02-03T11:30:00.0000000Z",
        "2024-02-03T11:30:00.000000000Z",
        "2024-02-03T11:30:00,0Z",
        "2024-02-03T11:30:00.0000009Z",
        "2024-02-03T11:30Z",
        "2024-02-03T12:30+01:00",
        "2024-02-03 11:30 UTC",
        "2024-02-03T11:30:00 GMT",
        "2024-02-03T06:30:00-05:00",
    ],
)
def test_free_text_loses_an_instant_that_is_a_term_s_in_every_listed_spelling(
    spelling: str,
) -> None:
    assert Terms([INSTANT]).text(f"at {spelling}.") == f"at {MARK}."


@pytest.mark.parametrize(
    "spelling",
    [
        "2024-02-03T11:31:00Z",
        "2024-02-03T11:30:00.5Z",
        "2024-02-03T12:30:00Z",
        "2024-02-03",
    ],
)
def test_free_text_keeps_a_date_and_time_that_is_no_term_s_instant(spelling: str) -> None:
    assert Terms([INSTANT]).text(f"at {spelling}.") == f"at {spelling}."


def test_free_text_loses_the_person_s_numbers_in_every_spelling_but_not_their_neighbours() -> None:
    terms = Terms(["17"], numbers=["17"])
    written = "member 0017, +17, 17.0, 1.7e1 and 17. Not 170, 1017, 1.75, 2024-02-03 or v17.5.3"
    assert terms.text(written) == (
        f"member {MARK}, +{MARK}, {MARK}.0, {MARK} and {MARK}. "
        "Not 170, 1017, 1.75, 2024-02-03 or v17.5.3"
    )


def test_a_text_key_of_the_person_that_reads_as_a_number_is_erased_from_free_text_by_value() -> (
    None
):
    assert Terms(["0017"]).text("member 17 and 17.0") == f"member {MARK} and {MARK}"


def test_the_audit_trail_s_whole_values_are_erased_in_any_spelling_of_a_term() -> None:
    terms = Terms(["m-17"], ["2", "2020-01-02"], numbers=["2"])
    given: list[object] = ["2.0", "02", " 2", "+2", "7.0", "2020-01-02T00:00:00Z", 2.0, 7]
    assert terms.json(given) == [MARK, MARK, MARK, MARK, "7.0", MARK, MARK, 7]  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("written", "left"),
    [
        ("member 017: Grace", f"member {MARK}: Grace"),
        ("member:017, then", f"member:{MARK}, then"),
        ("items 017/ and 017/b", f"items {MARK}/ and {MARK}/b"),
        ("at 10:17", "at 10:17"),
        ("at 017:30", "at 017:30"),
        ("on 3/17 and 17/3", "on 3/17 and 17/3"),
        ("017,5 and 1,017 and 17,000", "017,5 and 1,017 and 17,000"),
    ],
)
def test_free_text_joins_a_number_to_another_only_by_a_separator_between_digits(
    written: str, left: str
) -> None:
    assert Terms(["0017"]).text(written) == left


@pytest.mark.parametrize(
    ("term", "written", "left"),
    [
        ("2020-01-02", "on 2020-01-02.", f"on {MARK}."),
        ("2020-01-02", "at 2020-01-02 10:00", "at 2020-01-02 10:00"),
        ("2020-01-02", "at 2020-01-02 10", f"at {MARK} 10"),
        ("7", "7 and 7-8 and 6-7", f"{MARK} and 7-8 and 6-7"),
        ("7", "7/8, 6/7, 7:8, 6:7", "7/8, 6/7, 7:8, 6:7"),
        ("7", "7 - 8", f"{MARK} - 8"),
    ],
)
def test_a_term_matched_by_its_text_alone_is_no_part_of_a_longer_date_time_or_range(
    term: str, written: str, left: str
) -> None:
    assert Terms([term]).text(written, Place.OTHER) == left


def test_free_text_reads_an_offset_of_up_to_twenty_three_hours() -> None:
    terms = Terms([INSTANT])
    assert terms.text("at 2024-02-04T10:30+23:00") == f"at {MARK}"
    assert terms.text("at 2024-02-04T10:59+23:29") == f"at {MARK}"
    assert terms.text("at 2024-02-04T11:30+24:00") == "at 2024-02-04T11:30+24:00"


def test_a_constant_where_its_column_names_the_person_holds_a_term_in_any_reading() -> None:
    terms = Terms(["7"])
    assert terms.constant("no. 07", Place.NAMING) == f"no. {MARK}"
    assert terms.constant("no. 07", Place.UNKNOWN) == f"no. {MARK}"
    assert terms.constant("no. 07", Place.OTHER) == "no. 07"


def test_three_keys_redacted_alike_are_all_kept_apart() -> None:
    terms = Terms(["m-17"], ["2", "3"], numbers=["2", "3"])
    given: dict[str, JsonValue] = {"m-17": 10, "2": 20, "3.0": 30, "4": 40}
    assert terms.json(given) == {MARK: 10, f"{MARK} (2)": 20, f"{MARK} (3)": 30, "4": 40}
    assert terms.renamed(["[erased] (2)", "m-17", "2"]) == {
        "[erased] (2)": "[erased] (2)",
        "m-17": MARK,
        "2": f"{MARK} (3)",
    }


def test_a_naming_place_holds_only_the_terms_of_the_tables_whose_rows_it_names() -> None:
    terms = Terms(
        ["17", "m-17"],
        ["3", "l-9"],
        numbers=["17", "3"],
        origins={"17": ["members"], "m-17": ["members"], "3": ["loans"], "l-9": ["loans"]},
    )
    members, loans = Where(frozenset({"members"})), Where(frozenset({"loans"}))
    assert [terms.whole(value, members) for value in (17, "17.0", "m-17", 3, "3", "l-9")] == [
        True,
        True,
        True,
        False,
        False,
        False,
    ]
    assert [terms.whole(value, loans) for value in (3, "3.0", "l-9", 17, "17", "m-17")] == [
        True,
        True,
        True,
        False,
        False,
        False,
    ]
    assert terms.text("no. 0017, l-9", members) == f"no. {MARK}, l-9"
    assert terms.text("no. 0017, l-9", loans) == f"no. 0017, {MARK}"
    assert terms.whole(3, Place.NAMING)
    assert terms.whole("l-9", Place.OTHER)


def test_a_place_joined_from_several_holds_what_each_of_them_holds() -> None:
    terms = Terms(["m-17"], ["3"], numbers=["3"], origins={"m-17": ["members"], "3": ["loans"]})
    members = Where(frozenset({"members"}))
    joined = members | Where(frozenset({"loans"}))
    assert joined == Where(frozenset({"members", "loans"}))
    assert terms.whole(3, joined)
    assert not terms.whole(3, members | Where.of(Place.UNKNOWN))
    assert terms.whole("m-17", Where(frozenset({"loans"})) | Where.of(Place.OTHER))
    assert (members | Where.of(Place.NAMING)).naming is None
    with pytest.raises(ValueError, match="naming"):
        Where(frozenset(), Place.NAMING)


def test_a_term_with_no_table_given_is_held_on_every_naming_place() -> None:
    terms = Terms(["m-17"], ["3"], numbers=["3"], origins={"m-17": ["members"]})
    assert terms.whole(3, Where(frozenset({"members"})))
    assert not terms.whole("m-17", Where(frozenset({"loans"})))
    assert not terms.whole("m-17", Where())


def test_many_terms_are_found_in_long_text_without_a_pattern_of_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compiled: list[str] = []
    compile_pattern = re.compile

    def recording(pattern: str, flags: int = 0) -> re.Pattern[str]:
        compiled.append(pattern)
        return compile_pattern(pattern, flags)

    monkeypatch.setattr(re, "compile", recording)
    person = [f"k{index:07d}" for index in range(50_000)]
    below = [str(10_000_000 + index) for index in range(50_000)]
    started = time.perf_counter()
    terms = Terms(person, below, numbers=below)
    line = "see k0012345 and k00123456, not 10000002 but 17; "
    note = line * (1_000_000 // len(line))
    redacted = terms.text(note)
    elapsed = time.perf_counter() - started
    assert redacted == note.replace("k0012345 ", f"{MARK} ")
    assert [pattern for pattern in compiled if "k0012345" in pattern] == []
    assert elapsed < 20, elapsed


@pytest.mark.parametrize(
    ("term", "written", "left"),
    [
        ("a.", "a.b, a. c", f"a.b, {MARK} c"),
        (".a", "b.a, .a", f"b.a, {MARK}"),
        ("-7-", "x-7-y, -7- ", f"x-7-y, {MARK} "),
    ],
)
def test_a_term_that_starts_or_ends_with_no_letter_is_still_a_whole_token(
    term: str, written: str, left: str
) -> None:
    assert Terms([term]).text(written) == left
    assert Terms([term]).text(written, Place.OTHER) == left

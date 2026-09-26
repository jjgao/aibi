"""Matching the terms of an erasure (SPEC §12.2; D223, D290): every spelling the engine accepts
of a value shares a reading with it, and a constant is matched as its place says, by every
reading where it may name the person and by its text alone where it names no one."""

import re
import time
from datetime import UTC, date, datetime, timedelta, timezone
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import JsonValue

from aibi.core.engine.resolve import typed_constant
from aibi.core.schema.ids import MAX_SAFE_INTEGER
from aibi.core.store import redaction
from aibi.core.store.redaction import MARK, Naming, Place, Terms, Where
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


def test_a_naming_place_holds_only_its_own_terms() -> None:
    terms = Terms(["17", "m-17"], ["3", "l-9"], numbers=["17", "3"])
    members, loans = Where(frozenset({"17", "m-17"})), Where(frozenset({"3", "l-9"}))
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
    terms = Terms(["m-17", "17"], ["3"], numbers=["3", "17"])
    members = Where(frozenset({"m-17"}))
    joined = members | Where(frozenset({"3"}), scales=frozenset({0.5}))
    assert joined == Where(frozenset({"m-17", "3"}), scales=frozenset({1.0, 0.5}))
    assert terms.whole(3, joined)
    assert terms.whole(6, joined)
    assert not terms.whole(3, Where(frozenset({"3"}), scales=frozenset({0.5})))
    assert not terms.whole(3, members | Where.of(Place.UNKNOWN))
    assert terms.whole("m-17", Where(frozenset({"3"})) | Where.of(Place.OTHER))
    assert (members | Where.of(Place.NAMING)).naming is None
    for either in (Place.OTHER, Place.UNKNOWN):
        other = Place.UNKNOWN if either is Place.OTHER else Place.OTHER
        assert (Where.of(either) | Where.of(other)).rest is Place.UNKNOWN
        assert terms.constant("17.0", Where.of(either) | Where.of(other)) == MARK
    assert terms.constant("17.0", Place.OTHER) == "17.0"
    with pytest.raises(ValueError, match="naming"):
        Where(frozenset(), Place.NAMING)


NAMING = Naming.of(
    keys={
        "members": [["m-17"]],
        "loans": [["2"], ["3"]],
        "visits": [["m-17", "1"]],
        "lends": [["north", "7"]],
    },
    key_columns={
        "lends": [["branch", "number"]],
        "members": [["member_id"]],
        "loans": [["loan_id"]],
        "visits": [["member_id", "seq"]],
        "books": [["book_id"]],
    },
    names={
        "members.member_id": ["members"],
        "loans.loan_id": ["loans"],
        "loans.member_id": ["members"],
        "loans.book_id": ["books"],
        "visits.member_id": ["members"],
        "visits.seq": [],
        "books.book_id": ["books"],
        "lends.branch": [],
        "lends.number": [],
    },
    identifiers={"members.name": ["Grace"], "loans.days": ["3"], "visits.room": ["r1"]},
    identifying=["m-17", "Grace", "r1"],
    units={"loans.days": "d"},
)
"""The rule table's places, as a person's releases give them: ``m-17``, named Grace, with loans
2 and 3 (of 3 days), a visit of seq 1 in room r1 and a lending keyed by its branch and number,
and books, which hold none of their rows. The texts that identify them are their key's and their
identifier values that are not numbers."""


@pytest.mark.parametrize(
    ("column", "units", "held", "kept"),
    [
        ("members.member_id", None, ["m-17", "Grace", "r1", "with Grace"], [2, "2", "m-1"]),
        ("loans.loan_id", None, [2, "3.0", "m-17", "Grace"], [17, "17", "Graces", "1"]),
        ("loans.member_id", None, ["m-17", "Grace"], [2, "2", "3.0"]),
        ("loans.book_id", None, ["m-17", "Grace", "for m-17"], [2, "2", "m-170"]),
        ("books.book_id", None, ["m-17", "Grace"], [2, 3, "3"]),
        ("visits.member_id", None, ["m-17", "Grace"], [1, "1"]),
        ("visits.seq", None, ["m-17", "r1"], [1, "1", 2, "2"]),
        ("visits.room", None, ["Grace", "with Grace", "r1", "m-17"], [3, "3", 2]),
        ("members.name", None, ["r1", "Grace", "m-17"], [3, 2]),
        ("loans.days", None, [3, 3.0, "Grace", "m-17"], [2, 72]),
        ("loans.days", "d", [3], [72]),
        ("loans.days", "h", [72, 72.0], [3, 2, 48]),
        ("loans.days", "min", [4320], [3, 3 * 60]),
        ("loans.days", "[lb_av]", [3], [72]),
        ("books.title", None, ["m-17", "Grace", "for m-17 only"], [2, 3, "3", "2.0"]),
    ],
)
def test_a_column_s_place_holds_what_its_values_may_name_as_the_rule_table_says(
    column: str, units: str | None, held: list[JsonValue], kept: list[JsonValue]
) -> None:
    terms = Terms(["m-17", "Grace"], ["2", "3", "1", "r1"], numbers=["2", "3", "1"], naming=NAMING)
    place = terms.place(column, units)
    assert [value for value in held if not terms.in_constant(value, place)] == []
    assert [value for value in kept if terms.in_constant(value, place)] == []


@pytest.mark.parametrize(
    ("unit", "key", "held"),
    [
        ("visits", ["m-17", 1], True),
        ("visits", ["m-17", 9], True),
        ("visits", ["m-1", 1], False),
        ("visits", ["m-1", 9], False),
        ("lends", ["north", 7], True),
        ("lends", ["north", 8], False),
        ("lends", ["south", 7], False),
        ("members", ["m-17"], True),
        ("loans", [3], True),
        ("loans", [17], False),
        ("loans", ["m-17"], True),
        ("books", ["m-17"], True),
        ("books", [2], False),
        ("books", ["2"], False),
        ("core:person", [2], True),
        (None, ["m-17"], True),
    ],
)
def test_a_unit_key_names_a_row_as_a_whole_and_its_parts_only_on_their_own_columns(
    unit: str | None, key: list[JsonValue], held: bool
) -> None:
    terms = Terms(["m-17", "Grace"], ["2", "3", "1", "r1"], numbers=["2", "3", "1"], naming=NAMING)
    ids = {"kind": "ids", "ids": [{"dataset": "sha256:" + "0" * 64, "key": key}]}
    assert redaction._clause_holds(ids, terms, unit, True) is held  # pyright: ignore[reportPrivateUsage]


def test_a_waiting_redaction_keeps_what_names_the_person_s_rows() -> None:
    terms = Terms(["m-17"], ["2"], numbers=["2"], naming=NAMING)
    again = Terms.loads(terms.dumps())
    assert (again.person, again.below, again.numeric, again.naming) == (
        terms.person,
        terms.below,
        terms.numeric,
        NAMING,
    )
    assert Terms.loads(Terms(["m-17"]).dumps()).naming is None


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


def test_an_instant_in_text_on_a_naming_place_is_erased_only_if_the_place_holds_its_term() -> None:
    terms = Terms(["m-17"], ["2024-02-03T11:30:00Z"])
    written = "m-1 since 2024-02-03 11:30 UTC"
    assert terms.text(written, Where(frozenset({"m-17"}))) == written
    assert terms.text(written, Where(frozenset({"2024-02-03T11:30:00Z"}))) == (f"m-1 since {MARK}")


def test_a_number_in_text_on_a_naming_place_is_erased_only_as_one_of_the_person_s_terms() -> None:
    terms = Terms(["17"], ["017"])
    assert terms.text("see 17", Where(frozenset({"017"}))) == "see 17"
    assert terms.text("see 017", Where(frozenset({"017"}))) == f"see {MARK}"
    assert terms.text("see 0017", Where(frozenset({"17"}))) == f"see {MARK}"


def test_a_note_of_many_numbers_parses_only_those_whose_digits_could_be_a_term_s(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    terms = Terms(["17", "m-17"], ["2"], numbers=["17", "2"])
    parsed: list[str] = []
    reading = redaction.readings

    def recording(value: str) -> frozenset[tuple[str, str]]:
        parsed.append(value)
        return reading(value)

    monkeypatch.setattr(redaction, "readings", recording)
    numbers = " ".join(str(number) for number in range(100_000, 240_000))
    note = f"{numbers} 0017 1.7e1 017.00 1.8e1"
    started = time.perf_counter()
    redacted = terms.text(note)
    elapsed = time.perf_counter() - started
    assert len(note) > 950_000
    assert redacted == f"{numbers} {MARK} {MARK} {MARK} 1.8e1"
    assert sorted(parsed) == sorted(["170000", "0017", "1.7e1", "017.00"])
    assert elapsed < 20, elapsed


@pytest.mark.parametrize(
    ("term", "written"),
    [
        ("José", "for José only"),
        ("José", "for José only"),
        ("m-17", "for m-1​7 only"),
        ("m-17", "for m­-17 only"),
        ("Grace", "for Gr‍ace only"),
        ("Grace", "for ⁦Grace⁩ only"),
    ],
)
def test_text_is_compared_in_normal_form_c_without_format_characters(
    term: str, written: str
) -> None:
    terms = Terms([term])
    assert terms.text(written) == f"for {MARK} only"
    assert terms.text(written, Place.OTHER) == f"for {MARK} only"
    assert terms.whole(written.removeprefix("for ").removesuffix(" only"), Place.OTHER)
    assert terms.string(written.removeprefix("for ").removesuffix(" only")) == MARK


def test_text_that_holds_no_term_is_kept_as_it_is_written_whatever_its_form() -> None:
    written = "for Joséph and m-1​70"
    assert Terms(["José", "m-17"]).text(written) == written


def test_a_place_that_quotes_a_term_holds_its_text_alone() -> None:
    terms = Terms(["m-17", "17"], numbers=["17"])
    place = Where(quoted=frozenset({"m-17"}))
    assert terms.whole("m-17", place)
    assert terms.text("for m-17 only", place) == f"for {MARK} only"
    kept: list[JsonValue] = ["m-170", "am-17", "17", 17, "17.0"]
    assert [value for value in kept if terms.in_constant(value, place)] == []
    assert (place | Where()).quoted == frozenset({"m-17"})


def test_a_number_written_with_more_digits_than_a_double_holds_is_read_as_the_double_it_is() -> (
    None
):
    assert Terms(["17"]).text("x 1.70000000000000001e1 y") == f"x {MARK} y"
    assert Terms(["17"]).text("x 1.7000000000001e1 y") == "x 1.7000000000001e1 y"


def test_a_unit_key_s_part_is_where_the_key_column_of_every_release_puts_it() -> None:
    naming = Naming.of(
        keys={"lends": [["x"]], "branches": [["y"]]},
        key_columns={"lends": [["number"], ["branch"], ["branch", "number"]]},
        names={"lends.number": ["lends"], "lends.branch": ["branches"]},
        identifiers={},
        identifying=[],
        units={},
    )
    terms = Terms(["x"], ["y"], naming=naming)
    [place] = terms.part_places("lends", 1)
    assert [terms.in_constant(value, place) for value in ("x", "y", "z")] == [True, True, False]
    assert terms.part_places("lends", 2) == [
        terms.place("lends.branch"),
        terms.place("lends.number"),
    ]


def test_the_reprs_of_places_and_of_what_names_the_person_rows_name_none_of_their_values() -> None:
    secret = "k-4711"
    naming = Naming.of(
        keys={"members": [[secret]]},
        key_columns={"members": [["member_id"]]},
        names={"members.member_id": ["members"]},
        identifiers={"members.name": [f"{secret} Hopper"]},
        identifying=[secret, f"{secret} Hopper"],
        units={},
    )
    terms = Terms([secret, f"{secret} Hopper"], naming=naming)
    shown = [
        repr(naming),
        repr(terms.place("members.member_id")),
        repr(terms.place("members.name")),
        repr(Where(frozenset({secret}), Place.OTHER, quoted=frozenset({secret}))),
        repr(terms),
    ]
    assert [text for text in shown if "4711" in text] == []


def test_a_variable_s_values_and_empty_are_erased_as_constants_on_its_column() -> None:
    terms = Terms(["m-17", "Grace"], ["2", "3", "1", "r1"], numbers=["2", "3", "1"], naming=NAMING)
    written: JsonValue = {
        "aibi": "1",
        "dataset": "d",
        "unit": "members",
        "cohorts": {"all": {"all": []}},
        "views": [
            {
                "analysis": "summary.distribution",
                "params": {
                    "columns": [
                        {"column": "loans.loan_id", "aggregate": "some", "values": [2, 17]},
                        {"column": "loans.days", "aggregate": "max", "empty": 3, "bins": [0, 3]},
                        {"column": "loans.days", "aggregate": "mean", "empty": "exclude"},
                    ]
                },
            }
        ],
    }
    found: Any = redaction._Written(terms, "members", "d").document(written)  # pyright: ignore[reportPrivateUsage]
    some, most, mean = found["views"][0]["params"]["columns"]
    assert some["values"] == [MARK, 17]
    assert (most["empty"], most["bins"]) == (MARK, [0, MARK])
    assert mean["empty"] == "exclude"


def _distribution(columns: list[JsonValue]) -> JsonValue:
    return {
        "aibi": "1",
        "dataset": "d",
        "unit": "members",
        "cohorts": {"all": {"all": []}},
        "views": [{"analysis": "summary.distribution", "params": {"columns": columns}}],
    }


def test_a_bin_that_isolates_the_person_s_identifier_value_is_erased() -> None:
    terms = Terms(["m-17", "Grace"], ["2", "3", "1", "r1"], numbers=["2", "3", "1"], naming=NAMING)
    written = _distribution([{"column": "loans.days", "aggregate": "max", "bins": [3, 3.0000001]}])
    found: Any = redaction._Written(terms, "members", "d").document(written)  # pyright: ignore[reportPrivateUsage]
    assert found["views"][0]["params"]["columns"][0]["bins"] == [MARK, 3.0000001]


def test_a_bin_that_isolates_the_person_s_loan_key_is_erased() -> None:
    terms = Terms(["m-17", "Grace"], ["2", "3", "1", "r1"], numbers=["2", "3", "1"], naming=NAMING)
    written = _distribution([{"column": "loans.loan_id", "aggregate": "max", "bins": [2, 2.5]}])
    found: Any = redaction._Written(terms, "members", "d").document(written)  # pyright: ignore[reportPrivateUsage]
    assert found["views"][0]["params"]["columns"][0]["bins"] == [MARK, 2.5]


def test_a_variable_s_bin_that_names_the_person_on_another_column_only_is_kept() -> None:
    terms = Terms(["m-17", "Grace"], ["2", "3", "1", "r1"], numbers=["2", "3", "1"], naming=NAMING)
    written = _distribution(
        [
            {"column": "loans.days", "aggregate": "max", "bins": [1.0, 5]},
            {"column": "loans.loan_id", "aggregate": "max", "via": [], "bins": [1, 3]},
        ]
    )
    found: Any = redaction._Written(terms, "members", "d").document(written)  # pyright: ignore[reportPrivateUsage]
    days, loans = found["views"][0]["params"]["columns"]
    assert (days["bins"], loans["bins"]) == ([1.0, 5], [1, MARK])


def test_the_bins_of_a_count_bound_numbers_of_rows_and_are_kept() -> None:
    terms = Terms(["m-17", "Grace"], ["2", "3", "1", "r1"], numbers=["2", "3", "1"], naming=NAMING)
    written = _distribution([{"column": "loans.loan_id", "aggregate": "count", "bins": [2, 3]}])
    found: Any = redaction._Written(terms, "members", "d").document(written)  # pyright: ignore[reportPrivateUsage]
    assert found["views"][0]["params"]["columns"][0]["bins"] == [2, 3]


def test_a_result_s_canonical_variable_holds_its_empty_constant() -> None:
    terms = Terms(["m-17", "Grace"], ["2", "3", "1", "r1"], numbers=["2", "3", "1"], naming=NAMING)
    rows: JsonValue = {"kind": "exists", "table": "loans", "via": [], "where": []}
    held: JsonValue = {
        "columns": [{"aggregate": "max", "column": "loans.days", "rows": rows, "empty": 3}]
    }
    kept: JsonValue = {
        "columns": [{"aggregate": "max", "column": "loans.days", "rows": rows, "empty": 5}]
    }
    assert redaction._params_hold(held, terms)  # pyright: ignore[reportPrivateUsage]
    assert not redaction._params_hold(kept, terms)  # pyright: ignore[reportPrivateUsage]


def test_a_result_s_canonical_variable_holds_a_bin_edge_on_its_column() -> None:
    terms = Terms(["m-17", "Grace"], ["2", "3", "1", "r1"], numbers=["2", "3", "1"], naming=NAMING)
    rows: JsonValue = {"kind": "exists", "table": "loans", "via": [], "where": []}
    variable: dict[str, JsonValue] = {"aggregate": "max", "column": "loans.days", "rows": rows}
    held: JsonValue = {"columns": [{**variable, "empty": "exclude", "bins": [3, 4]}]}
    kept: JsonValue = {"columns": [{**variable, "empty": "exclude", "bins": [4, 5]}]}
    counted: JsonValue = {
        "columns": [{"aggregate": "count", "column": "loans.days", "rows": rows, "bins": [3, 4]}]
    }
    assert redaction._params_hold(held, terms)  # pyright: ignore[reportPrivateUsage]
    assert not redaction._params_hold(kept, terms)  # pyright: ignore[reportPrivateUsage]
    assert not redaction._params_hold(counted, terms)  # pyright: ignore[reportPrivateUsage]
    elsewhere: JsonValue = {"columns": [{**variable, "empty": "exclude", "bins": [1, 5]}]}
    assert not redaction._params_hold(elsewhere, terms)  # pyright: ignore[reportPrivateUsage]

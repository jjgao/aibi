"""Raw snapshots: canonical strings, typed snapshots as JSON Lines, and text files read with
their parse settings (SPEC §5.3, §12.2)."""

import math
from datetime import UTC, date, datetime, timedelta, timezone
from typing import Any

import pytest

from aibi.core.schema.descriptors import ParseSettings
from aibi.core.store.sources import (
    ErrorCell,
    SourceError,
    SourceValue,
    TextSource,
    TypedSource,
    canonical_string,
    decode,
    encode,
    parse,
    parse_text,
)


def settings(**given: Any) -> ParseSettings:
    fields: dict[str, Any] = {
        "format": "csv",
        "delimiter": ",",
        "quote": '"',
        "header_row": 0,
        "skip_rows": 0,
        "encoding": "utf-8",
    }
    fields.update(given)
    return ParseSettings.model_validate(fields)


# --- Canonical strings ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "written"),
    [
        (None, None),
        ("", ""),
        (" NA ", " NA "),
        (True, "true"),
        (False, "false"),
        (0, "0"),
        (-99, "-99"),
        (2**70, str(2**70)),
        (-99.0, "-99"),
        (0.1, "0.1"),
        (1e21, "1e+21"),
        (1.5e-7, "1.5e-7"),
        (-0.0, "0"),
        (math.nan, "NaN"),
        (math.inf, "Infinity"),
        (-math.inf, "-Infinity"),
        (date(2024, 1, 2), "2024-01-02"),
        (datetime(2024, 1, 2, 3, 4, 5), "2024-01-02T03:04:05"),
        (datetime(2024, 1, 2, 3, 4, 5, 6), "2024-01-02T03:04:05.000006"),
        (datetime(2024, 1, 2, 3, 4, 5, tzinfo=UTC), "2024-01-02T03:04:05+00:00"),
        (
            datetime(2024, 1, 2, 3, 4, 5, tzinfo=timezone(timedelta(hours=-5, minutes=-30))),
            "2024-01-02T03:04:05-05:30",
        ),
        (ErrorCell("#N/A"), "#N/A"),
    ],
)
def test_the_canonical_string_form(value: SourceValue, written: str | None) -> None:
    """§12.2: integers in decimal, other numbers as RFC 8785 writes them (-99.0 is -99),
    non-finite numbers by name, datetimes with their offset or without one, error text."""
    assert canonical_string(value) == written


# --- Typed snapshots ----------------------------------------------------------------------------

_EVERY_KIND: tuple[SourceValue, ...] = (
    None,
    True,
    False,
    0,
    -(2**53) + 1,
    2**53,
    -(2**70),
    1.5,
    -99.0,
    1e300,
    5e-324,
    math.nan,
    math.inf,
    -math.inf,
    "",
    'text "quoted"\n\u2028\U0001f600\ufdd0',
    date(1, 1, 1),
    date(9999, 12, 31),
    datetime(2024, 1, 2, 3, 4, 5),
    datetime(2024, 1, 2, 3, 4, 5, 123456, tzinfo=timezone(timedelta(hours=5, minutes=45))),
    ErrorCell("#DIV/0!"),
)


def _same(left: SourceValue, right: SourceValue) -> bool:
    if isinstance(left, float) and math.isnan(left):
        return isinstance(right, float) and math.isnan(right)
    if isinstance(left, float) and left.is_integer():
        return right == int(left)  # an integral number is written, and read back, as an integer
    return type(left) is type(right) and left == right


def test_a_typed_snapshot_round_trips_every_kind_of_value() -> None:
    source = TypedSource(("a", "b"), tuple((value, value) for value in _EVERY_KIND))
    data = encode(source)
    back = decode("rows", data)
    assert isinstance(back, TypedSource)
    assert back.columns == ("a", "b")
    for written, read in zip(source.rows, back.rows, strict=True):
        assert _same(written[0], read[0]), (written, read)
        assert canonical_string(written[0]) == canonical_string(read[0])
    assert encode(back) == data


def test_a_typed_snapshot_is_json_lines_with_tagged_values() -> None:
    source = TypedSource(
        ("n", "x"),
        (
            (2**60, math.nan),
            (-99.0, date(2024, 1, 2)),
            (None, datetime(2024, 1, 2, 3, 4, 5)),
            ("é", ErrorCell("#N/A")),
        ),
    )
    assert encode(source).decode() == (
        '{"columns":["n","x"],"format":"aibi.rows/1"}\n'
        '[{"int":"1152921504606846976"},{"float":"NaN"}]\n'
        '[-99,{"date":"2024-01-02"}]\n'
        '[null,{"datetime":"2024-01-02T03:04:05"}]\n'
        '["é",{"error":"#N/A"}]\n'
    )


@pytest.mark.parametrize(
    ("columns", "rows", "message"),
    [
        (("a",), ((1, 2),), "row 0 has 2 values"),
        (("a",), ((object(),),), "not a source value: object"),
        (("a",), ((b"bytes",),), "not a source value: bytes"),
        (
            ("a",),
            ((datetime(2024, 1, 1, tzinfo=timezone(timedelta(seconds=30))),),),
            "whole minutes",
        ),
    ],
)
def test_a_typed_snapshot_refuses_what_it_cannot_carry(
    columns: tuple[str, ...], rows: Any, message: str
) -> None:
    with pytest.raises(SourceError, match=message):
        TypedSource(columns, rows)


def test_a_lone_surrogate_cannot_be_stored() -> None:
    with pytest.raises(SourceError, match="lone surrogate"):
        encode(TypedSource(("a",), (("\ud800",),)))
    with pytest.raises(SourceError, match="lone surrogate"):
        encode(TypedSource(("\ud800",), ()))


def test_a_text_snapshot_is_its_bytes() -> None:
    raw = b"a,b\r\n1,2\r\n"
    assert encode(TextSource(raw)) == raw
    assert decode("text", raw) == TextSource(raw)


@pytest.mark.parametrize(
    "data",
    [
        b'{"columns":["a"],"format":"aibi.rows/1"}',
        b'{"columns":["a"],"format":"other"}\n',
        b"[1]\n[2]\n",
        b'{"columns":["a"],"format":"aibi.rows/1"}\n{"a":1}\n',
        b'{"columns":["a"],"format":"aibi.rows/1"}\n[{"when":"x"}]\n',
        b'{"columns":["a"],"format":"aibi.rows/1"}\n[{"float":"nan"}]\n',
        b'{"columns":["a"],"format":"aibi.rows/1"}\n[{"date":5}]\n',
        b'{"columns":["a"],"format":"aibi.rows/1"}\n[{"int":"\xd9\xa3"}]\n',
        b'{"columns":["a"],"format":"aibi.rows/1"}\n[{}]\n',
        b'{"columns":["a"],"format":"aibi.rows/1"}\n[{"int":"1","date":"2024-01-01"}]\n',
        b'{"columns":["a"],"format":"aibi.rows/1"}\n[[1]]\n',
    ],
)
def test_a_damaged_typed_snapshot_is_refused(data: bytes) -> None:
    with pytest.raises(SourceError):
        decode("rows", data)


# --- Reading text files -----------------------------------------------------------------------


def test_a_csv_file_is_read_with_its_header() -> None:
    parsed = parse_text(b'id,name\n1,Ada\n2,"Lovelace, A."\n', settings())
    assert parsed.names == ("id", "name")
    assert parsed.rows == [("1", "Ada"), ("2", "Lovelace, A.")]


def test_quotes_doubled_and_line_breaks_inside_quotes_are_kept() -> None:
    raw = b'a,b\r\n"say ""hi""","two\r\nlines"\r\n"x\ny",z\r\n'
    parsed = parse_text(raw, settings())
    assert parsed.rows == [('say "hi"', "two\r\nlines"), ("x\ny", "z")]
    assert parse_text(b'a\nx"y\n', settings()).rows == [('x"y',)]  # unquoted, it is text


@pytest.mark.parametrize("ending", [b"\n", b"\r\n", b"\r"])
def test_every_line_ending_is_read(ending: bytes) -> None:
    raw = ending.join([b"a,b", b"1,2", b"3,4", b""])
    assert parse_text(raw, settings()).rows == [("1", "2"), ("3", "4")]


def test_blank_lines_are_no_records() -> None:
    raw = b"\n\na,b\n\n1,2\n\n\n3,4"
    parsed = parse_text(raw, settings())
    assert parsed.names == ("a", "b")
    assert parsed.rows == [("1", "2"), ("3", "4")]
    # So a file of one column has no row where a line is empty (D215).
    assert parse_text(b"a\n1\n\n2\n", settings()).rows == [("1",), ("2",)]


def test_skipped_lines_are_not_parsed_and_the_header_row_counts_records_after_them() -> None:
    raw = b'# a preamble with an unbalanced " quote\n#another\nnotes,x\nid,v\n1,2\n'
    parsed = parse_text(raw, settings(skip_rows=2, header_row=1))
    assert parsed.names == ("id", "v")
    assert parsed.rows == [("1", "2")]


def test_a_tab_separated_file() -> None:
    parsed = parse_text(b"a\tb\n1\t\n", settings(format="tsv", delimiter="\t"))
    assert parsed.rows == [("1", "")]


@pytest.mark.parametrize(
    ("raw", "given", "message"),
    [
        (b"a,b\n1,2,3\n", {}, "line 2: 3 fields, and the header has 2"),
        (b"a,b\n1\n", {}, "line 2: 1 fields, and the header has 2"),
        (b"x\ny\na,b\n1,2\n\n1\n", {"skip_rows": 2}, "line 6: 1 fields"),
        (b'a,b\n"multi\nline",2\n3\n', {}, "line 4: 1 fields"),
        (b'a,b\n"x"y,2\n', {}, "line 2: ',' expected after '\"'"),
        (b"a,b\n" + b"x" * 131_073 + b",1\n", {}, "field larger than field limit"),
        (b"", {}, "No header row"),
        (b"a,b\n", {"header_row": 1}, "No header row: 1 records after the 0 lines skipped"),
        (b"a,b\n", {"quote": ","}, "different characters"),
        (b"a,b\n", {"delimiter": "\n"}, "line break"),
        (b"a,b\n", {"encoding": "no-such-encoding"}, "Not a text encoding"),
        (b"a,b\n1,\xff\n", {}, "line 2: the bytes are not utf-8"),
    ],
)
def test_a_file_its_settings_cannot_read_is_refused_with_its_line(
    raw: bytes, given: dict[str, Any], message: str
) -> None:
    with pytest.raises(SourceError, match=message.replace("(", r"\(")):
        parse_text(raw, settings(**given))


def test_the_encoding_is_the_one_named() -> None:
    assert parse_text("a\nné\n".encode("latin-1"), settings(encoding="latin-1")).rows == [("né",)]
    with_bom = "\ufeffa\n1\n".encode()
    assert parse_text(with_bom, settings()).names == ("\ufeffa",)
    assert parse_text(with_bom, settings(encoding="utf-8-sig")).names == ("a",)
    assert parse_text("a\n1\n".encode("utf-16"), settings(encoding="utf-16")).rows == [("1",)]


def test_parse_reads_text_with_settings_and_typed_sources_without() -> None:
    typed = TypedSource(("a",), ((1,),))
    assert parse(typed, None).rows == ((1,),)
    with pytest.raises(SourceError, match="text files only"):
        parse(typed, settings())
    with pytest.raises(SourceError, match="with parse settings"):
        parse(TextSource(b"a\n1\n"), None)


@pytest.mark.parametrize(
    "data",
    [
        b'{"columns":["a"],"format":"aibi.rows/1"}\n[1,\n',
        b'{"columns":[1],"format":"aibi.rows/1"}\n',
        b'{"columns":["a"],"format":"aibi.rows/1"}\n["\xff"]\n',
        b'{"columns":["a"],"format":"aibi.rows/1"}\n[{"int":"x"}]\n',
    ],
)
def test_a_typed_snapshot_that_is_not_json_is_refused(data: bytes) -> None:
    with pytest.raises(SourceError):
        decode("rows", data)

"""RFC 8785: numbers as ECMAScript writes doubles, keys by UTF-16 code units, minimal escapes."""

import json
import math
import re
import struct
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import JsonValue

from aibi.core.schema.jsonio import JsonError, canonical, number_text

# RFC 8785, Appendix B: IEEE 754 bit patterns and how the scheme writes them.
_APPENDIX_B = [
    ("0000000000000000", "0"),
    ("8000000000000000", "0"),
    ("0000000000000001", "5e-324"),
    ("8000000000000001", "-5e-324"),
    ("7fefffffffffffff", "1.7976931348623157e+308"),
    ("ffefffffffffffff", "-1.7976931348623157e+308"),
    ("4340000000000000", "9007199254740992"),
    ("c340000000000000", "-9007199254740992"),
    ("4430000000000000", "295147905179352830000"),
    ("44b52d02c7e14af5", "9.999999999999997e+22"),
    ("44b52d02c7e14af6", "1e+23"),
    ("44b52d02c7e14af7", "1.0000000000000001e+23"),
    ("444b1ae4d6e2ef4e", "999999999999999700000"),
    ("444b1ae4d6e2ef4f", "999999999999999900000"),
    ("444b1ae4d6e2ef50", "1e+21"),
    ("3eb0c6f7a0b5ed8c", "9.999999999999997e-7"),
    ("3eb0c6f7a0b5ed8d", "0.000001"),
    ("41b3de4355555553", "333333333.3333332"),
    ("41b3de4355555554", "333333333.33333325"),
    ("41b3de4355555555", "333333333.3333333"),
    ("41b3de4355555556", "333333333.3333334"),
    ("41b3de4355555557", "333333333.33333343"),
    ("becbf647612f3696", "-0.0000033333333333333333"),
    ("43143ff3c1cb0959", "1424953923781206.2"),
]


@pytest.mark.parametrize(("bits", "written"), _APPENDIX_B)
def test_numbers_are_written_as_the_rfc_s_appendix_writes_them(bits: str, written: str) -> None:
    [value] = struct.unpack(">d", bytes.fromhex(bits))
    assert number_text(value) == written


_ES_NUMBER = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]*[1-9])?(?:e[+-][1-9][0-9]*)?")


@given(st.floats(allow_nan=False, allow_infinity=False))
def test_every_double_reads_back_as_itself_in_the_shortest_form(value: float) -> None:
    written = number_text(value)
    assert _ES_NUMBER.fullmatch(written)
    assert float(written) == value
    if value.is_integer() and abs(value) <= 2**53:
        assert written == str(int(value))
    elif value.is_integer() and abs(value) < 1e21:
        # Shortest digits, then zeros: 47828722253173072.0 is written 47828722253173070.
        assert "." not in written
        assert "e" not in written


def test_integers_are_written_in_decimal_and_non_finite_numbers_are_refused() -> None:
    assert number_text(-12) == "-12"
    for value in (math.nan, math.inf, -math.inf):
        with pytest.raises(ValueError, match="not a JSON number"):
            number_text(value)


def test_the_rfc_s_example_object() -> None:
    """RFC 8785, section 3.2.3."""
    value = json.loads(
        '{"numbers": [333333333.33333329, 1E30, 4.50, 2e-3, 0.000000000000000000000000001],'
        ' "string": "\\u20ac$\\u000F\\u000aA\'\\u0042\\u0022\\u005c\\\\\\"\\/",'
        ' "literals": [null, true, false]}'
    )
    assert (
        canonical(value)
        == (
            '{"literals":[null,true,false],"numbers":[333333333.3333333,1e+30,4.5,0.002,1e-27],'
            '"string":"€$\\u000f\\nA\'B\\"\\\\\\\\\\"/"}'
        ).encode()
    )


def test_keys_are_sorted_by_utf16_code_units() -> None:
    """RFC 8785, section 3.2.3: U+FB33 sorts after the surrogates of U+1F600."""
    members: dict[str, JsonValue] = {
        "€": 1,
        "\r": 2,
        "דּ": 3,
        "1": 4,
        "\U0001f600": 5,
        "\x80": 6,
        "ö": 7,
    }
    written = canonical(members).decode()
    keys = [key for key, _ in json.loads(written, object_pairs_hook=list)]
    assert keys == ["\r", "1", "\x80", "ö", "€", "\U0001f600", "דּ"]


def test_strings_escape_what_json_requires_and_nothing_else() -> None:
    value = '"\\\b\f\n\r\t\x00\x1f\x7f\u2028é\U0001f600/'
    assert canonical(value) == (
        '"\\"\\\\\\b\\f\\n\\r\\t\\u0000\\u001f\x7f\u2028é\U0001f600/"'.encode()
    )


def test_integral_doubles_are_integers_and_negative_zero_is_zero() -> None:
    assert canonical([2.0, -0.0, 1e20, 0.5]) == b"[2,0,100000000000000000000,0.5]"


@pytest.mark.parametrize(
    ("value", "code", "pointer"),
    [
        ({"a": [1, math.nan]}, "NON_FINITE_NUMBER", "/a/1"),
        ({"a": math.inf}, "NON_FINITE_NUMBER", "/a"),
        ([2**53], "INTEGER_OUT_OF_RANGE", "/0"),
        ({"x": {"y": "\ud800"}}, "INVALID_VALUE", "/x/y"),
        ({"x": {"\udfff": 1}}, "INVALID_VALUE", "/x"),
        ({1: "a"}, "INVALID_VALUE", ""),
        ([{1, 2}], "WRONG_TYPE", "/0"),
    ],
)
def test_what_json_cannot_carry_is_refused_with_its_pointer(
    value: Any, code: str, pointer: str
) -> None:
    with pytest.raises(JsonError) as raised:
        canonical(value)
    assert (raised.value.code, raised.value.pointer) == (code, pointer)


def test_nesting_beyond_the_cap_is_refused() -> None:
    """64 nested arrays are allowed, as JSON text may nest them; 65 are not."""
    deep: Any = 1
    for _ in range(65):
        deep = [deep]
    with pytest.raises(JsonError) as raised:
        canonical(deep)
    assert raised.value.code == "LIMIT_EXCEEDED"
    canonical(deep[0])

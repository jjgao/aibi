import pytest

from aibi.core.schema.jsonio import JsonError, parse_json, pointer
from aibi.core.schema.limits import MAX_DEPTH, MAX_VALUES


def test_parses_json() -> None:
    assert parse_json('{"a": [1, 2.5, {"b": "c"}], "d": true}') == {
        "a": [1, 2.5, {"b": "c"}],
        "d": True,
    }


@pytest.mark.parametrize(
    ("source", "code", "path"),
    [
        ('{"a": 1, "a": 2}', "DUPLICATE_KEY", "/a"),
        ('{"x": [{"y": 1, "y": 2}]}', "DUPLICATE_KEY", "/x/0/y"),
        ('{"a/b": {"c": 1, "c": 1}}', "DUPLICATE_KEY", "/a~1b/c"),
        ("[NaN]", "NON_FINITE_NUMBER", "/0"),
        ('{"v": -Infinity}', "NON_FINITE_NUMBER", "/v"),
        ('{"f": 1e400}', "INTEGER_OUT_OF_RANGE", "/f"),
        ('{"f": 6.022e23}', "INTEGER_OUT_OF_RANGE", "/f"),
        ('{"n": 9007199254740992}', "INTEGER_OUT_OF_RANGE", "/n"),
        ('{"n": [-9007199254740992]}', "INTEGER_OUT_OF_RANGE", "/n/0"),
        ('{"n": 9007199254740993.0}', "INTEGER_OUT_OF_RANGE", "/n"),
        ('{"n": 1e20}', "INTEGER_OUT_OF_RANGE", "/n"),
        ('{"n": -1.5e300}', "INTEGER_OUT_OF_RANGE", "/n"),
        ('{"n": ' + "1" * 5000 + "}", "INTEGER_OUT_OF_RANGE", "/n"),
        ('{"s": "\\ud800"}', "INVALID_JSON", "/s"),
        ('{"s": ["ok", "\\udc00x"]}', "INVALID_JSON", "/s/1"),
        ('{"o": {"\\ud800": 1}}', "INVALID_JSON", "/o"),
        ('{"s": "x\\uffff"}', "INVALID_JSON", "/s"),
        ('{"s": "\\ufdd0"}', "INVALID_JSON", "/s"),
        ('{"s": "\\ud83f\\udffe"}', "INVALID_JSON", "/s"),
        ("{bad", "INVALID_JSON", None),
        (b"\xff", "INVALID_JSON", None),
        ('﻿{"a": 1}', "INVALID_JSON", None),
        (b'\xef\xbb\xbf{"a": 1}', "INVALID_JSON", None),
        ('{"a": 1}'.encode("utf-16"), "INVALID_JSON", None),
    ],
)
def test_refuses(source: str | bytes, code: str, path: str | None) -> None:
    with pytest.raises(JsonError) as raised:
        parse_json(source)
    assert raised.value.code == code
    assert raised.value.pointer == path


def test_safe_integers_pass() -> None:
    assert parse_json("[9007199254740991, -9007199254740991]") == [
        9007199254740991,
        -9007199254740991,
    ]


def test_integral_numbers_are_integers() -> None:
    parsed = parse_json("[2.0, -0.0, 1e3, 2.5, 1e-400]")
    assert parsed == [2, 0, 1000, 2.5, 0]
    assert isinstance(parsed, list)
    assert [type(value) for value in parsed] == [int, int, int, float, int]


def test_nesting_is_capped_with_a_pointer_where_there_is_one() -> None:
    parse_json("[" * MAX_DEPTH + "]" * MAX_DEPTH)
    with pytest.raises(JsonError) as raised:
        parse_json("[" * (MAX_DEPTH + 1) + "]" * (MAX_DEPTH + 1))
    assert (raised.value.code, raised.value.pointer) == ("LIMIT_EXCEEDED", "/0" * MAX_DEPTH)
    assert raised.value.limit == ("nesting_depth", MAX_DEPTH)
    with pytest.raises(JsonError) as raised:
        parse_json("[" * 100_000 + "]" * 100_000)
    assert (raised.value.code, raised.value.pointer) == ("LIMIT_EXCEEDED", None)


def test_the_number_of_values_is_capped() -> None:
    parse_json("[" + ",".join(["0"] * (MAX_VALUES - 1)) + "]")
    with pytest.raises(JsonError) as raised:
        parse_json("[" + ",".join(["0"] * MAX_VALUES) + "]")
    assert (raised.value.code, raised.value.pointer) == ("LIMIT_EXCEEDED", f"/{MAX_VALUES - 1}")
    assert raised.value.limit == ("json_values", MAX_VALUES)


def test_pointer_escapes() -> None:
    assert pointer([]) == ""
    assert pointer(["a/b", "c~d", 0]) == "/a~1b/c~0d/0"

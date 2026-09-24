import pytest

from aibi.core.schema.jsonio import JsonError, parse_json, pointer


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
        ("[NaN]", "NON_FINITE_NUMBER", None),
        ('{"v": -Infinity}', "NON_FINITE_NUMBER", None),
        ('{"f": 1e400}', "NON_FINITE_NUMBER", "/f"),
        ('{"n": 9007199254740992}', "INTEGER_OUT_OF_RANGE", "/n"),
        ('{"n": [-9007199254740992]}', "INTEGER_OUT_OF_RANGE", "/n/0"),
        ("{bad", "INVALID_JSON", None),
        (b"\xff", "INVALID_JSON", None),
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


def test_pointer_escapes() -> None:
    assert pointer([]) == ""
    assert pointer(["a/b", "c~d", 0]) == "/a~1b/c~0d/0"

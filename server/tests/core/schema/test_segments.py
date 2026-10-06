"""Segments that hold what is not the server's (A6, D397): ``shown`` escapes a name that is not
Unicode text and cuts it only between escapes; ``listed`` gives each value a data token of its
own; ``plain_text`` joins a message for a log, never for server text."""

import pytest

from aibi.core.schema.output import (
    DATA_TOKEN_MAX,
    DataSegment,
    data,
    listed,
    plain_text,
    shown,
    text,
)

ESCAPES = {"￾": "\\ufffe", "\ud800": "\\ud800", "\U0001ffff": "\\U0001ffff"}


def _pieces(name: str) -> list[str]:
    return [ESCAPES.get(character, character) for character in name]


@pytest.mark.parametrize("character", sorted(ESCAPES))
@pytest.mark.parametrize("before", range(185, 201))
def test_shown_cuts_a_name_only_between_escapes(character: str, before: int) -> None:
    name = "a" * before + character + "b" * 10
    found = shown(name)
    pieces = _pieces(name)
    whole = "".join(pieces)
    assert len(found.data) <= DATA_TOKEN_MAX
    cuts = {"".join(pieces[:end]) for end in range(len(pieces) + 1)}
    assert found.data in cuts
    assert (found.truncated is True) == (found.data != whole)
    # The longest whole prefix that fits is kept: nothing is cut that need not be.
    assert found.data == max((cut for cut in cuts if len(cut) <= DATA_TOKEN_MAX), key=len)


def test_shown_of_unicode_text_is_data() -> None:
    assert shown("struct<a: int64>") == DataSegment(data="struct<a: int64>")
    assert shown("x" * 201) == data("x" * 201)
    assert shown("x" * 201).truncated is True


def test_listed_gives_each_value_a_token_of_its_own() -> None:
    assert listed(["a, b", "c"]) == [data("a, b"), text(", "), data("c")]
    assert listed(["a"]) == [data("a")]
    assert listed([]) == []
    assert listed(["a", "b"], " and ") == [data("a"), text(" and "), data("b")]


def test_plain_text_joins_text_and_data() -> None:
    message = (text("arith reads numeric columns: "), DataSegment(data="t.c"))
    assert plain_text(message) == "arith reads numeric columns: t.c"

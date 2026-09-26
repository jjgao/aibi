"""HTML that text cannot turn into markup (SPEC §3.2 A6, §14; D312): whatever a text holds, it
parses back as the text it was written as, its unsafe characters visible and marked, and no
element or attribute outside the allow-list can be written."""

import re
import unicodedata
from html.parser import HTMLParser

import pytest
from hypothesis import given
from hypothesis import strategies as st

from aibi.core.api.markup import (
    ATTRIBUTES,
    ELEMENTS,
    Markup,
    data,
    document,
    element,
    escaped,
    joined,
    segments,
)
from aibi.core.schema.output import DataSegment, text
from aibi.core.schema.refusals import BIDI_FORMATTING

PIECES = ["<", ">", "&", '"', "'", "</bdi>", "<!--", "]]>", "\r", "\r\n", "\x00", "&lt;", "\\x1b"]
FORMAT = [0x2028, 0x2029, 0x85, 0x1B, 0x200B, 0xFEFF, 0xAD, 0x2060, 0xE0001, 0x200C, 0x200D]
HOSTILE = st.lists(
    st.characters(codec="utf-8")
    | st.sampled_from(PIECES)
    | st.sampled_from(sorted(BIDI_FORMATTING))
    | st.sampled_from([chr(point) for point in FORMAT])
).map("".join)
ESCAPE = re.compile(r"^(?:\\x[0-9a-f]{2}|\\u[0-9a-f]{4}|\\U[0-9a-f]{8})+$")


def unsafe(character: str) -> bool:
    point = ord(character)
    if character in "\t\n" or point in (0x200C, 0x200D):
        return False
    return (
        point < 0x20
        or 0x7F <= point <= 0x9F
        or point in (0x2028, 0x2029)
        or unicodedata.category(character) in ("Cf", "Cs")
    )


def visible(value: str) -> str:
    def written(character: str) -> str:
        point = ord(character)
        if not unsafe(character):
            return character
        if point < 0x100:
            return f"\\x{point:02x}"
        return f"\\u{point:04x}" if point < 0x10000 else f"\\U{point:08x}"

    return "".join(written(character) for character in value.replace("\r\n", "\n"))


class Nodes(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.nodes: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.nodes.append(("start", tag))
        self.nodes.extend(("attribute", f"{name}={value}") for name, value in attrs)

    def handle_endtag(self, tag: str) -> None:
        self.nodes.append(("end", tag))

    def handle_data(self, data: str) -> None:
        self.nodes.append(("text", data))

    def handle_comment(self, data: str) -> None:
        self.nodes.append(("comment", data))


def nodes(markup: Markup) -> list[tuple[str, str]]:
    found = Nodes()
    found.feed(markup.html)
    found.close()
    return found.nodes


@given(HOSTILE)
def test_any_text_written_as_data_parses_back_as_its_text_with_each_escape_marked(
    value: str,
) -> None:
    found = nodes(element("p", data(value)))
    assert found[:3] == [("start", "p"), ("start", "bdi"), ("attribute", "class=data")]
    assert found[-2:] == [("end", "bdi"), ("end", "p")]
    inner = found[3:-2]
    written: list[str] = []
    position = 0
    while position < len(inner):
        kind, content = inner[position]
        if kind == "text":
            written.append(content)
            position += 1
            continue
        assert inner[position : position + 4] == [
            ("start", "span"),
            ("attribute", "class=escape"),
            inner[position + 2],
            ("end", "span"),
        ]
        escape_kind, escape = inner[position + 2]
        assert escape_kind == "text"
        assert ESCAPE.fullmatch(escape)
        written.append(escape)
        position += 4
    assert "".join(written) == visible(value)
    escapes = sum(1 for kind, content in inner if (kind, content) == ("start", "span"))
    given = value.replace("\r\n", "\n")
    runs = sum(
        1
        for index, character in enumerate(given)
        if unsafe(character) and (index == 0 or not unsafe(given[index - 1]))
    )
    assert escapes == runs


@given(HOSTILE)
def test_any_text_in_an_attribute_stays_that_attributes_value(value: str) -> None:
    found = nodes(element("a", "x", attributes={"title": value}))
    assert found == [
        ("start", "a"),
        ("attribute", f"title={visible(value)}"),
        ("text", "x"),
        ("end", "a"),
    ]


def test_unsafe_characters_are_written_visible() -> None:
    assert escaped("a\tb\nc") == "a\tb\nc"
    assert escaped("a\rb\r\nc") == "a\\x0db\nc"
    assert escaped("\x1b[31m\x7f\x85") == "\\x1b[31m\\x7f\\x85"
    assert escaped("a\x7fb") == "a\\x7fb"
    assert element("p", "a\x7fb").html == '<p>a<span class="escape">\\x7f</span>b</p>'
    formatting = chr(0x202E) + "evil" + chr(0x2066) + chr(0x2028) + chr(0x200B) + chr(0xE0041)
    assert escaped(formatting) == r"\u202eevil\u2066\u2028\u200b\U000e0041"
    joiners = "a" + chr(0x200C) + "b" + chr(0x200D) + "c"
    assert escaped(joiners) == joiners
    assert escaped("<b a='1'>&amp;</b>") == "&lt;b a=&#x27;1&#x27;&gt;&amp;amp;&lt;/b&gt;"


def test_an_escape_is_marked_and_the_same_characters_in_data_are_not() -> None:
    written = element("p", "\\x1b and \x1b")
    assert written.html == '<p>\\x1b and <span class="escape">\\x1b</span></p>'
    run = element("p", "a" + chr(0x200B) * 3 + "b")
    assert run.html == '<p>a<span class="escape">\\u200b\\u200b\\u200b</span>b</p>'


def test_a_lone_surrogate_is_written_as_an_escape() -> None:
    written = document("t", "", element("p", "a" + chr(0xD800) + "b"))
    assert b'a<span class="escape">\\ud800</span>b' in written


def test_a_messages_data_tokens_are_isolated_and_a_cut_one_is_marked() -> None:
    written = segments([text("Unknown "), DataSegment(data="<i>", truncated=True), text(" here")])
    assert written.html == (
        'Unknown <bdi class="data">&lt;i&gt;</bdi><span class="cut" title="cut">…</span> here'
    )


def test_only_the_allowed_elements_attributes_and_links_can_be_written() -> None:
    assert "script" not in ELEMENTS
    assert not {name for name in ATTRIBUTES if name.startswith("on")} | (
        {"style", "src"} & ATTRIBUTES
    )
    for tag in ("script", "img", "iframe", "object", "form", "link", "base", "svg"):
        with pytest.raises(ValueError, match="element's name"):
            element(tag)  # pyright: ignore[reportArgumentType]
    for name in ("onclick", "style", "src", "on click", "formaction"):
        with pytest.raises(ValueError, match="attribute's name"):
            element("a", attributes={name: "x"})  # pyright: ignore[reportArgumentType]
    for href in (
        "//elsewhere",
        "javascript:alert(1)",
        "https://elsewhere",
        "data:x",
        "x",
        "",
        "/\t/elsewhere",
        "/\n/elsewhere",
        "/\\elsewhere",
        "/\x00/x",
        "/ /x",
        "/a//b",
        "#",
        "/a?b=<",
        "/?a=<",
        "/? x",
        "/?next=//elsewhere",
        "/?u=http://x",
        "/%zz",
    ):
        with pytest.raises(ValueError, match="path on the server or a fragment"):
            element("a", attributes={"href": href})
    for href in (
        "/",
        "/?offset=2",
        "/datasets/x?columns_offset=2",
        "/a%20b/",
        "/a%20b/?offset=1",
        "#table-x",
    ):
        assert element("a", attributes={"href": href}).html
    with pytest.raises(ValueError, match="void element"):
        element("br", "x")


def test_a_document_holds_its_title_as_text_and_its_one_stylesheet() -> None:
    title = "<title> & more" + chr(0x202E)
    written = document(title, "p { color: red; }", joined(["a", element("br"), "b"]))
    parser = Nodes()
    parser.feed(written.decode("utf-8"))
    parser.close()
    assert ("text", "<title> & more\\u202e") in parser.nodes
    assert [tag for kind, tag in parser.nodes if kind == "start"] == [
        "html",
        "head",
        "meta",
        "meta",
        "title",
        "style",
        "body",
        "br",
    ]

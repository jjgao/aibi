"""HTML that text cannot turn into markup (SPEC §3.2 A6, §14; D312).

``Markup`` is HTML this module wrote; every ``str`` handed to ``element`` beside it is text,
escaped where it is written, so no text reaches the page as markup whatever it holds. A text's
*unsafe* characters are its control characters (C0 but tab and line feed, DEL and C1), its
format characters (Unicode's category ``Cf``, the bidi formatting among them, but the zero-width
non-joiner and joiner that scripts and emoji need), lone surrogates (``Cs``, which UTF-8 cannot
carry) and the line and paragraph separators; each is written as a visible ``\\xNN``,
``\\uNNNN`` or ``\\UNNNNNNNN``, as the operator CLI writes control and bidi characters
(``operator.cli.escaped``), and a carriage return before a line feed is dropped. ``escaped``
gives a text as an attribute's value; in an element's text each run of consecutive unsafe
characters is also a ``<span class="escape">`` of its own, so that its escapes are told apart
from the same characters written in the data. ``&``, ``<``, ``>``, ``"`` and ``'`` become character
references. Text from data is written by ``data``, each in a ``<bdi>`` of its own, so that its
direction cannot reorder the page around it.

Element and attribute names come from an allow-list (``ELEMENTS``, ``ATTRIBUTES``) that holds no
script, style attribute or event handler; an ``href`` is a path on the server, ``/`` or
segments of letters, digits, ``.``, ``_``, ``~``, ``-`` and percent-escapes each after a ``/``,
with an optional trailing ``/`` and a query of those characters, ``=`` and ``&``, or a fragment
(``#`` and such a segment); and a page's stylesheet is a literal of the server's code.
"""

import html
import re
import unicodedata
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from functools import cache
from typing import LiteralString

from aibi.core.schema.output import DataSegment, Segment

ELEMENTS = frozenset(
    {
        "a",
        "article",
        "bdi",
        "body",
        "br",
        "code",
        "div",
        "h1",
        "h2",
        "h3",
        "h4",
        "head",
        "header",
        "html",
        "li",
        "meta",
        "nav",
        "p",
        "section",
        "span",
        "strong",
        "style",
        "table",
        "tbody",
        "td",
        "th",
        "thead",
        "title",
        "tr",
        "ul",
    }
)
"""The elements a page may hold."""
ATTRIBUTES = frozenset({"charset", "class", "content", "href", "id", "lang", "name", "title"})
"""The attributes an element may have."""
_VOID = frozenset({"meta", "br"})
_KEPT = frozenset({"\t", "\n", chr(0x200C), chr(0x200D)})
_ASCII_UNSAFE = re.compile("[\x00-\x08\x0b-\x1f\x7f]")
_SEGMENT = r"(?:[A-Za-z0-9._~-]|%[0-9A-Fa-f]{2})+"
_QUERY = r"(?:[A-Za-z0-9._~=&-]|%[0-9A-Fa-f]{2})*"
_HREF = re.compile(rf"^(?:#{_SEGMENT}|/(?:\?{_QUERY})?|(?:/{_SEGMENT})+/?(?:\?{_QUERY})?)$")


@dataclass(frozen=True, slots=True)
class Markup:
    """HTML this module wrote (module docstring)."""

    html: str


Child = Markup | str
"""What an element holds: markup, or text to escape."""


@cache
def _unsafe(character: str) -> bool:
    if character in _KEPT:
        return False
    point = ord(character)
    return (
        point < 0x20
        or 0x7F <= point <= 0x9F
        or point in (0x2028, 0x2029)
        or unicodedata.category(character) in ("Cf", "Cs")
    )


def _escape(character: str) -> str:
    point = ord(character)
    if point < 0x100:
        return f"\\x{point:02x}"
    return f"\\u{point:04x}" if point < 0x10000 else f"\\U{point:08x}"


def _visible(run: str) -> str:
    return "".join(_escape(character) for character in run)


def _runs(text: str) -> Iterator[tuple[bool, str]]:
    """``text``, a carriage return before a line feed dropped, as runs of safe characters and of
    unsafe ones, each marked whether it is unsafe."""
    given = text.replace("\r\n", "\n")
    if given.isascii() and _ASCII_UNSAFE.search(given) is None:
        if given:
            yield False, given
        return
    start = 0
    for index in range(1, len(given) + 1):
        if index == len(given) or _unsafe(given[index]) != _unsafe(given[start]):
            yield _unsafe(given[start]), given[start:index]
            start = index


def escaped(text: str) -> str:
    """``text`` as an attribute's value (module docstring)."""
    return html.escape(
        "".join(_visible(part) if unsafe else part for unsafe, part in _runs(text)), quote=True
    )


def _text(text: str) -> str:
    """``text`` as an element's text, each visible escape in its own ``span``."""
    return "".join(
        f'<span class="escape">{html.escape(_visible(part))}</span>'
        if unsafe
        else html.escape(part, quote=True)
        for unsafe, part in _runs(text)
    )


def _written(child: Child) -> str:
    return child.html if isinstance(child, Markup) else _text(child)


def _href(value: str) -> None:
    if _HREF.fullmatch(value) is None:
        raise ValueError("a link is a path on the server or a fragment, of plain characters")


def element(
    tag: LiteralString, *children: Child, attributes: Mapping[LiteralString, str] | None = None
) -> Markup:
    """``<tag attributes>children</tag>``; a void element (``meta``, ``br``) holds nothing."""
    if tag not in ELEMENTS:
        raise ValueError("an element's name is one of ELEMENTS")
    given = attributes or {}
    if any(name not in ATTRIBUTES for name in given):
        raise ValueError("an attribute's name is one of ATTRIBUTES")
    if "href" in given:
        _href(given["href"])
    opened = "".join(f' {name}="{escaped(value)}"' for name, value in given.items())
    if tag in _VOID:
        if children:
            raise ValueError("a void element holds nothing")
        return Markup(f"<{tag}{opened}>")
    return Markup(f"<{tag}{opened}>{''.join(_written(child) for child in children)}</{tag}>")


def joined(children: Iterable[Child], separator: Child = "") -> Markup:
    """The children one after another, ``separator`` between them."""
    return Markup(_written(separator).join(_written(child) for child in children))


def data(value: str, *, truncated: bool = False) -> Markup:
    """Text from data (A6), isolated in a ``<bdi>``; ``truncated`` marks text that was cut."""
    shown = element("bdi", value, attributes={"class": "data"})
    if not truncated:
        return shown
    return joined([shown, element("span", "…", attributes={"class": "cut", "title": "cut"})])


def segments(given: Sequence[Segment]) -> Markup:
    """A message or a readback: the server's text as text, and each data token as data."""
    return joined(
        data(segment.data, truncated=bool(segment.truncated))
        if isinstance(segment, DataSegment)
        else segment.text
        for segment in given
    )


def document(title: str, style: LiteralString, *body: Child) -> bytes:
    """A whole page, UTF-8: its title, its one stylesheet and its body."""
    head = element(
        "head",
        element("meta", attributes={"charset": "utf-8"}),
        element(
            "meta",
            attributes={"name": "viewport", "content": "width=device-width, initial-scale=1"},
        ),
        element("title", Markup(escaped(title))),
        element("style", Markup(style)),
    )
    page = element("html", head, element("body", *body), attributes={"lang": "en"})
    return ("<!doctype html>\n" + page.html + "\n").encode("utf-8")


__all__ = [
    "ATTRIBUTES",
    "ELEMENTS",
    "Child",
    "Markup",
    "data",
    "document",
    "element",
    "escaped",
    "joined",
    "segments",
]

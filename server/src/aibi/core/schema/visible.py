"""The visible form of a text from data (D312, D428): which code points are written as escapes.

A text's *unsafe* code points are its control characters (C0 but tab and line feed, DEL and C1),
its format characters (Unicode's category ``Cf``, the bidi formatting among them, but the
zero-width non-joiner and joiner that scripts and emoji need), lone surrogates (``Cs``) and the
line and paragraph separators; each is written as a visible ``\\xNN``, ``\\uNNNN`` or
``\\UNNNNNNNN`` (lower-case hex, one escape per code point). A text from data is cut at ``CUT``
code points before anything else, then a carriage return before a line feed is dropped, then each
run of consecutive unsafe code points is written as one escaped run.

The server's page (``aibi.core.api.markup``, ``page``) writes text by these classes, and the web
bundle by the same ones: ``document()`` is written to ``schemas/visible-form.json`` by
``aibi.core.schema.export``, with the Unicode version of the interpreter that wrote it, and the
web generates its ranges from that file (``npm run generate``), so that the two sides cannot
drift apart unseen. The classes depend on the interpreter's Unicode data (``unicodedata``): the
file records the version, and a test compares it only under that version.

D312 leaves some code points raw that a renderer may draw as nothing: the default-ignorable code
points that are neither ``Cf`` nor a control (variation selectors, the combining grapheme joiner,
the Hangul fillers, and the unassigned ones a renderer is told to draw invisibly), the space
separators but U+0020, and U+2800. The file names them (``raw_by_d312``), for the owner (D428's
O-2 record), so that a change to D312 is a change here that both sides see.

This module is domain-neutral and imports no other layer; ``markup`` imports it.
"""

import unicodedata
from collections.abc import Iterator

from pydantic import JsonValue

VISIBLE_FILE = "visible-form.json"
"""The file ``aibi.core.schema.export`` writes ``document()`` to."""

CUT = 2_000
"""The code points of a text from data that are shown; the rest is cut and marked (D312)."""

KEPT = frozenset({0x09, 0x0A, 0x200C, 0x200D})
"""The code points of the unsafe categories that stay raw: tab, line feed, and the zero-width
non-joiner and joiner that scripts and emoji need."""

LAST = 0x10FFFF
"""The last code point."""

SEPARATORS = frozenset({0x2028, 0x2029})
"""The line and paragraph separators, unsafe though they are no format character."""

DEFAULT_IGNORABLE = (
    (0x00AD, 0x00AD),
    (0x034F, 0x034F),
    (0x061C, 0x061C),
    (0x115F, 0x1160),
    (0x17B4, 0x17B5),
    (0x180B, 0x180F),
    (0x200B, 0x200F),
    (0x202A, 0x202E),
    (0x2060, 0x206F),
    (0x3164, 0x3164),
    (0xFE00, 0xFE0F),
    (0xFEFF, 0xFEFF),
    (0xFFA0, 0xFFA0),
    (0xFFF0, 0xFFF8),
    (0x1BCA0, 0x1BCA3),
    (0x1D173, 0x1D17A),
    (0xE0000, 0xE0FFF),
)
"""``Default_Ignorable_Code_Point`` (Unicode 15.0's ``DerivedCoreProperties.txt``, which
``unicodedata`` does not expose; the web's test holds it to the engine's
``\\p{Default_Ignorable_Code_Point}``)."""

BRAILLE_BLANK = 0x2800
"""U+2800 BRAILLE PATTERN BLANK: a symbol (``So``) that is drawn as nothing."""


def unsafe_point(point: int) -> bool:
    """Whether a code point is written as an escape (module docstring)."""
    if point in KEPT:
        return False
    return (
        point < 0x20
        or 0x7F <= point <= 0x9F
        or point in SEPARATORS
        or unicodedata.category(chr(point)) in ("Cf", "Cs")
    )


def unsafe(character: str) -> bool:
    """Whether a character, one code point, is written as an escape."""
    return unsafe_point(ord(character))


def escape(character: str) -> str:
    """One code point as its visible escape: ``\\xNN``, ``\\uNNNN`` or ``\\UNNNNNNNN``."""
    point = ord(character)
    if point < 0x100:
        return f"\\x{point:02x}"
    return f"\\u{point:04x}" if point < 0x10000 else f"\\U{point:08x}"


def _ranges(points: Iterator[int]) -> list[tuple[int, int]]:
    """Ascending code points as inclusive ``(first, last)`` ranges."""
    found: list[tuple[int, int]] = []
    for point in points:
        if found and found[-1][1] == point - 1:
            found[-1] = (found[-1][0], point)
        else:
            found.append((point, point))
    return found


def _json(ranges: list[tuple[int, int]]) -> list[JsonValue]:
    """Ranges as JSON: a list of ``[first, last]``."""
    return [[first, last] for first, last in ranges]


def unsafe_ranges() -> list[tuple[int, int]]:
    """Every unsafe code point, over all 1,114,112, as inclusive ranges."""
    return _ranges(point for point in range(LAST + 1) if unsafe_point(point))


def _ignorable() -> Iterator[int]:
    for first, last in DEFAULT_IGNORABLE:
        yield from range(first, last + 1)


def raw_ignorable(*, assigned: bool) -> list[tuple[int, int]]:
    """The default-ignorable code points D312 leaves raw (neither unsafe nor kept on purpose),
    assigned or unassigned (``Cn``) under this interpreter's Unicode."""
    return _ranges(
        point
        for point in _ignorable()
        if not unsafe_point(point)
        and point not in KEPT
        and (unicodedata.category(chr(point)) != "Cn") == assigned
    )


def raw_spaces() -> list[tuple[int, int]]:
    """The space separators (``Zs``) but U+0020: raw, and some drawn as nothing or alike."""
    return _ranges(
        point
        for point in range(LAST + 1)
        if point != 0x20 and unicodedata.category(chr(point)) == "Zs"
    )


def _kept() -> list[JsonValue]:
    found: list[JsonValue] = []
    found.extend(sorted(KEPT))
    return found


def _count(ranges: list[tuple[int, int]]) -> int:
    return sum(last - first + 1 for first, last in ranges)


def document() -> dict[str, JsonValue]:
    """``schemas/visible-form.json``: the visible form's rules and classes, by this interpreter's
    Unicode data."""
    assigned = raw_ignorable(assigned=True)
    unassigned = raw_ignorable(assigned=False)
    spaces = raw_spaces()
    return {
        "description": (
            "The visible form of a text from data (SPEC D312, D428), written by "
            "aibi.core.schema.export from aibi.core.schema.visible: the server's page and the web "
            "bundle escape the same code points. Do not edit; regenerate under the pinned Python."
        ),
        "unidata_version": unicodedata.unidata_version,
        "cut": CUT,
        "order": ["cut", "fold", "escape"],
        "rules": {
            "cut": (
                "a text from data is cut at `cut` code points (never splitting a surrogate pair), "
                "before it is folded or escaped, and the cut is marked; pointers, statistic "
                "references and data tokens are not cut"
            ),
            "fold": "a carriage return immediately before a line feed is dropped (after the cut)",
            "escape": (
                "each unsafe code point is written \\xNN below U+0100, \\uNNNN below U+10000, "
                "\\UNNNNNNNN above, in lower-case hex, one per code point (a lone surrogate is one)"
            ),
            "runs": "each maximal run of unsafe code points is one escaped run (one span)",
        },
        "classes": [
            "C0 controls but tab and line feed",
            "DEL and the C1 controls",
            "Unicode's Cf but the zero-width non-joiner and joiner",
            "Unicode's Cs (lone surrogates)",
            "U+2028 and U+2029",
        ],
        "kept": _kept(),
        "unsafe": _json(unsafe_ranges()),
        "raw_by_d312": {
            "note": (
                "Code points D312 leaves raw although a renderer may draw them as nothing or "
                "alike (D428, O-2: recorded for the owner, not changed): the default-ignorable "
                "code points that are neither Cf nor a control (variation selectors, the "
                "combining grapheme joiner, the Hangul fillers) and the unassigned ones, the "
                "space separators but U+0020, and U+2800. The tag characters U+E0001 and "
                "U+E0020..U+E007F are Cf, so a subdivision flag's tag sequence is escaped."
            ),
            "default_ignorable_assigned": _json(assigned),
            "default_ignorable_assigned_count": _count(assigned),
            "default_ignorable_unassigned": _json(unassigned),
            "default_ignorable_unassigned_count": _count(unassigned),
            "space_separators": _json(spaces),
            "space_separators_count": _count(spaces),
            "braille_blank": BRAILLE_BLANK,
        },
    }

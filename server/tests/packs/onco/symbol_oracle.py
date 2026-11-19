"""The test's own model of a padded ``Hugo_Symbol`` (D444's M27, M4.2b-2b-i): which code points pad
a symbol at its edge, written from Unicode's definitions and ``unicodedata``, never from the pack
or from ``aibi.core.schema.visible`` (which ``test_mutation.py`` holds against it).

This is not a test: pytest does not collect it, and it imports nothing but ``unicodedata``, so that
the model cannot be the pack's code read back (``test_the_symbol_model_imports_nothing_of_aibi``).

A code point **pads** a symbol at its first or last place when it is

- whitespace by ``str.strip()`` (what the reference strips, V:1806);
- U+2800 BRAILLE PATTERN BLANK, a symbol drawn as nothing;
- of D312's unsafe class: the C0 controls but tab and line feed, DEL and the C1 controls, U+2028
  and U+2029, and Unicode's ``Cf`` and ``Cs`` but the zero-width non-joiner and joiner;
- ``Default_Ignorable_Code_Point``, derived as ``DerivedCoreProperties.txt`` derives it:
  ``Other_Default_Ignorable_Code_Point`` + ``Cf`` + ``Variation_Selector`` - ``White_Space`` -
  U+FFF9..U+FFFB - U+13430..U+1343F - ``Prepended_Concatenation_Mark``. ``unicodedata`` gives the
  categories and names; the two properties it does not give are the literals below (Unicode 15.0's
  ``PropList.txt``), the variation selectors and the Hangul fillers found by their names.

The model is Unicode 15.0's (``UNIDATA``), the pinned Python's: under another version the tests that
read it skip, as the test of ``visible-form.json`` does.
"""

import unicodedata

UNIDATA = "15.0.0"
"""The Unicode version the literals below are from: the pinned Python's ``unidata_version``."""

LAST = 0x10FFFF

OTHER_DEFAULT_IGNORABLE = (
    (0x034F, 0x034F),
    (0x115F, 0x1160),
    (0x17B4, 0x17B5),
    (0x2065, 0x2065),
    (0x3164, 0x3164),
    (0xFFA0, 0xFFA0),
    (0xFFF0, 0xFFF8),
    (0xE0000, 0xE0000),
    (0xE0002, 0xE001F),
    (0xE0080, 0xE00FF),
    (0xE01F0, 0xE0FFF),
)
"""``Other_Default_Ignorable_Code_Point`` (``PropList.txt``, Unicode 15.0)."""

PREPENDED_CONCATENATION_MARK = (
    (0x0600, 0x0605),
    (0x06DD, 0x06DD),
    (0x070F, 0x070F),
    (0x0890, 0x0891),
    (0x08E2, 0x08E2),
    (0x110BD, 0x110BD),
    (0x110CD, 0x110CD),
)
"""``Prepended_Concatenation_Mark`` (``PropList.txt``, Unicode 15.0): format characters that are
drawn, and so not ignorable."""

VISIBLE_FORMAT = ((0xFFF9, 0xFFFB), (0x13430, 0x1343F))
"""The interlinear annotation characters and the Egyptian hieroglyph format controls, which the
derivation takes out of ``Cf``."""

KEPT = (0x200C, 0x200D)
"""The zero-width non-joiner and joiner, which D312 leaves raw (they are default-ignorable)."""

BRAILLE_BLANK = 0x2800


def _within(point: int, ranges: tuple[tuple[int, int], ...]) -> bool:
    return any(first <= point <= last for first, last in ranges)


def whitespace(point: int) -> bool:
    """Whether ``str.strip()`` removes the code point."""
    return chr(point).strip() == ""


def variation_selector(point: int) -> bool:
    """``Variation_Selector``: the code points Unicode names variation selectors (the Mongolian
    free variation selectors among them)."""
    return "VARIATION SELECTOR" in unicodedata.name(chr(point), "")


def hangul_filler(point: int) -> bool:
    """The Hangul fillers, by their names (each also in ``OTHER_DEFAULT_IGNORABLE``)."""
    name = unicodedata.name(chr(point), "")
    return "HANGUL" in name and name.endswith("FILLER")


def default_ignorable(point: int) -> bool:
    """``Default_Ignorable_Code_Point``, by the derivation of the module's docstring."""
    if whitespace(point) or _within(point, VISIBLE_FORMAT):
        return False
    if _within(point, PREPENDED_CONCATENATION_MARK):
        return False
    return (
        unicodedata.category(chr(point)) == "Cf"
        or variation_selector(point)
        or hangul_filler(point)
        or _within(point, OTHER_DEFAULT_IGNORABLE)
    )


def unsafe(point: int) -> bool:
    """D312's unsafe class, from its definition (SPEC D312, D428)."""
    if point in KEPT or point in (0x09, 0x0A):
        return False
    return (
        point < 0x20
        or 0x7F <= point <= 0x9F
        or point in (0x2028, 0x2029)
        or unicodedata.category(chr(point)) in ("Cf", "Cs")
    )


def pads(point: int) -> bool:
    """Whether the code point pads a symbol at its edge (the module's docstring)."""
    return whitespace(point) or point == BRAILLE_BLANK or unsafe(point) or default_ignorable(point)


def edged(symbol: str) -> bool:
    """Whether a symbol's first or last code point pads it; a code point inside is text."""
    return symbol != "" and (pads(ord(symbol[0])) or pads(ord(symbol[-1])))


CLASSES = ("whitespace", "braille", "unsafe", "ignorable", "text")


def class_of(point: int) -> str:
    """The first class of ``CLASSES`` the code point is of: each padding class, else text."""
    if whitespace(point):
        return "whitespace"
    if point == BRAILLE_BLANK:
        return "braille"
    if unsafe(point):
        return "unsafe"
    if default_ignorable(point):
        return "ignorable"
    return "text"

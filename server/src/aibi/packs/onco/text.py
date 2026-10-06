"""Text a study declares, checked before the pack keeps it (SPEC D409).

A study's ``meta_study.txt`` values are declared text (D271): the pack keeps them as written, or
refuses them; it never strips, cuts or rewrites one. ``written`` is the one check every such value
passes first, and ``Checked`` is the type of what passed it, so that pyright refuses a value that
reaches ``meta.StudyFields`` (and, from M4.2a-1b, a descriptor) without the check.
"""

from typing import NewType

from aibi.core.schema.jsonio import is_text

Checked = NewType("Checked", str)
"""A value ``written`` accepted."""

CONTROLS: frozenset[str] = frozenset(
    character for character in map(chr, (*range(0x20), 0x7F)) if character not in "\t\n\r"
)
"""The 30 control characters a declared value may not hold: U+0000 to U+001F but tab, line feed
and carriage return, and U+007F."""


def written(value: str, most: int, least: int = 1) -> Checked | None:
    """``value`` as ``Checked`` if it is Unicode text (no lone surrogate, no noncharacter) of
    ``least`` to ``most`` characters, none of them in ``CONTROLS``; otherwise ``None``. The
    length is compared before a character is read."""
    if not least <= len(value) <= most:
        return None
    if not is_text(value) or any(character in CONTROLS for character in value):
        return None
    return Checked(value)


__all__ = ["CONTROLS", "Checked", "written"]

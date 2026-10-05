"""What the tests of names that are not Unicode text share (SPEC §8.1, §14, D398): the classes of
code points a name can hold, by the medium that carries it, and the outcome of an import, with
every segment it shows checked."""

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from aibi.core.importers.errors import ImportRefused
from aibi.core.schema.jsonio import is_text
from aibi.core.schema.output import DataSegment, Segment, TextSegment

INTERNAL = (
    "ValidationError",
    "OutputError",
    "UnicodeEncodeError",
    "UnicodeDecodeError",
    "RuntimeError",
    "ReaderError",
)
"""Classes a refusal must never name for a name: the server's own faults (D398)."""

_PARTIAL = re.compile(r"\\(?!u[0-9a-f]{4}|U[0-9a-f]{8})")
"""A backslash that does not start a whole escape: a cut inside one. The names the tests make
hold no backslash of their own."""


@dataclass(frozen=True)
class Bad:
    """A class of code points: as Python holds it in a name, and as a file system's bytes."""

    label: str
    text: str
    disk: bytes | None
    """The bytes a file system holds for it; ``None`` when no file name decodes to it."""

    def __repr__(self) -> str:
        return self.label


SURROGATE_FF = Bad("U+DCFF", "\udcff", b"\xff")
SURROGATE_80 = Bad("U+DC80", "\udc80", b"\x80")
SURROGATE_HIGH = Bad("U+D800", "\ud800", None)
SURROGATE_HIGH_LAST = Bad("U+DBFF", "\udbff", None)
SURROGATE_LOW = Bad("U+DC00", "\udc00", None)
NONCHARACTER_FDD0 = Bad("U+FDD0", "\ufdd0", "\ufdd0".encode())
NONCHARACTER_FFFE = Bad("U+FFFE", "\ufffe", "\ufffe".encode())
NONCHARACTER_FFFF = Bad("U+FFFF", "\uffff", "\uffff".encode())
NONCHARACTER_ASTRAL = Bad("U+1FFFE", "\U0001fffe", "\U0001fffe".encode())
TEXT = Bad("text", "z", b"z")
"""The twin: a name that is text, in the same place."""

DISK = (SURROGATE_FF, SURROGATE_80, NONCHARACTER_FDD0, NONCHARACTER_FFFF, NONCHARACTER_ASTRAL)
"""A file system's name: a byte that is not UTF-8 (read as U+DC80-DCFF), or a noncharacter."""
BYTES = (SURROGATE_FF, SURROGATE_80)
"""Bytes that are not UTF-8 where a format holds UTF-8 (a Parquet file's names)."""
UTF8 = (NONCHARACTER_FDD0, NONCHARACTER_FFFE, NONCHARACTER_FFFF, NONCHARACTER_ASTRAL)
"""UTF-8 text with a noncharacter: a CSV header (bytes that are not UTF-8 decode as another
encoding), a Parquet file's names, and those of a DuckDB database, which validates its names as
UTF-8. A SQLite file's names are these and ``BYTES``: it stores what its client wrote."""
XML = (NONCHARACTER_FDD0, NONCHARACTER_ASTRAL)
"""What XML 1.0 (a workbook) holds: neither a surrogate nor U+FFFE or U+FFFF."""
LIBRARY = (
    SURROGATE_FF,
    SURROGATE_80,
    SURROGATE_HIGH,
    SURROGATE_HIGH_LAST,
    SURROGATE_LOW,
    NONCHARACTER_FDD0,
    NONCHARACTER_FFFE,
    NONCHARACTER_FFFF,
    NONCHARACTER_ASTRAL,
)
"""Every class: a library caller gives any ``str``."""


def check_shown(segments: Iterable[Segment]) -> None:
    """Every segment is valid output: Unicode text, at most 200 characters, never cut inside an
    escape, naming no internal class."""
    for segment in segments:
        token = segment.data if isinstance(segment, DataSegment) else segment.text
        assert is_text(token), ascii(token)
        assert len(token) <= 200, ascii(token)
        assert not _PARTIAL.search(token), ascii(token)
        assert not any(name in token for name in INTERNAL), ascii(token)


Outcome = tuple[str, str] | None
"""``(code, the server's text of the first refusal)``, or ``None`` for an import."""


def said(segments: Iterable[Segment]) -> str:
    """The server's text of a message, without the ``In <file>: `` a file's refusal starts with."""
    written = "".join(s.text for s in segments if isinstance(s, TextSegment))
    return written.removeprefix("In : ")


def escape_of(text: str) -> str:
    """How the server writes ``text`` as data (§8.1): each lone surrogate and noncharacter as
    ``\\uXXXX`` (``\\UXXXXXXXX`` above U+FFFF), and any other character as itself."""
    return "".join(
        character
        if is_text(character)
        else f"\\u{ord(character):04x}"
        if ord(character) <= 0xFFFF
        else f"\\U{ord(character):08x}"
        for character in text
    )


def outcome(run: Callable[[], object], noted: Iterable[str] = ()) -> Outcome:
    """What ``run`` gives, every refusal and note it shows checked (``check_shown``). Each of
    ``noted`` is a name that is only shown, so that an import must note it: some note holds it,
    escaped (``escape_of``), in a data token."""
    try:
        result = run()
    except ImportRefused as error:
        for refusal in error.refusals:
            check_shown([*refusal.message, *refusal.alternatives])
            refusal.model_dump_json()
        first = error.refusals[0]
        return str(first.code), said(first.message)
    notes: list[Any] = list(getattr(result, "notes", ()))
    for note in notes:
        check_shown(note.message)
    for name in noted:
        shown = escape_of(name)
        assert any(
            isinstance(segment, DataSegment) and shown in segment.data
            for note in notes
            for segment in note.message
        ), f"no note names {shown!r}"
    return None


def matches(got: Outcome, expected: tuple[str, str] | None) -> bool:
    if expected is None:
        return got is None
    return got is not None and got[0] == expected[0] and got[1].startswith(expected[1])

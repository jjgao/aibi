"""The import's notes, within the core's bounds (SPEC D400, D409's clinical files).

The core takes at most ``MAX_QUEUE_ITEMS`` notes from an importer, their text at most
``MAX_QUEUE_BYTES`` in UTF-8 (its message's segments and its subject, as ``importers.checks``
counts them), and refuses a result past either as the pack's failure (``PACK_FAILED``). So every
note the import writes, discovery's skip notes included, goes through one ``Budget``, which keeps
the notes in the order they come while both bounds hold with room for one more, and past that
lists none and counts them all in one last note (``LEFT_OUT``), which the room was kept for: no
legal study's notes make the import fail.
"""

from collections.abc import Iterable

from aibi.core.schema.limits import MAX_QUEUE_BYTES, MAX_QUEUE_ITEMS
from aibi.core.schema.output import TextSegment, text
from aibi.core.schema.pack_api import ImportNote

LEFT_OUT = "further notes of the import, not listed"
"""The text of the note that counts the notes left out."""


def size(note: ImportNote) -> int:
    """A note's text in UTF-8 bytes, as the core counts it: its segments and its subject."""
    parts = [
        segment.text if isinstance(segment, TextSegment) else segment.data
        for segment in note.message
    ]
    if note.subject is not None:
        parts.append(note.subject)
    return sum(len(part.encode("utf-8", "surrogatepass")) for part in parts)


def left_out(count: int) -> ImportNote:
    return ImportNote("not_proposed", None, [text(LEFT_OUT)], count=count)


class Budget:
    """The notes kept, within the core's bounds and the room for ``left_out``'s note."""

    def __init__(self, items: int = MAX_QUEUE_ITEMS, bytes_: int = MAX_QUEUE_BYTES) -> None:
        self._items = items - 1
        self._bytes = bytes_ - size(left_out(0))
        self._kept: list[ImportNote] = []
        self._size = 0
        self._left = 0

    def add(self, notes: Iterable[ImportNote]) -> None:
        for note in notes:
            more = size(note)
            if self._left or len(self._kept) >= self._items or self._size + more > self._bytes:
                self._left += 1
                continue
            self._kept.append(note)
            self._size += more

    @property
    def notes(self) -> tuple[ImportNote, ...]:
        """The notes kept, then the one that counts the rest, if any were left out."""
        if not self._left:
            return tuple(self._kept)
        return (*self._kept, left_out(self._left))


__all__ = ["LEFT_OUT", "Budget", "left_out", "size"]

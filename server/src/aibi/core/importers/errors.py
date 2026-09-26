"""The refusal that stops an import (SPEC §8.6, §13.2)."""

from collections.abc import Sequence

from aibi.core.schema.jsonio import is_text
from aibi.core.schema.output import Segment, data, text
from aibi.core.schema.refusals import Limit, Refusal, RefusalCode, finish_refusals
from aibi.core.store.sources import FIELD_CHARACTERS


class ImportRefused(Exception):  # noqa: N818 - the spec's word
    def __init__(self, refusals: Sequence[Refusal]) -> None:
        super().__init__("; ".join(f"{refusal.code} at {refusal.path}" for refusal in refusals))
        self.refusals = tuple(finish_refusals(list(refusals)))


def refused(
    code: RefusalCode | str,
    *message: Segment | str,
    alternatives: Sequence[Segment | str] = (),
    limit: tuple[str, int] | None = None,
) -> ImportRefused:
    """An import refused with one refusal: text given as a string is the server's own, and a
    segment is kept as it is (a name from the source is ``data``, A6)."""
    segments = [text(part) if isinstance(part, str) else part for part in message]
    return ImportRefused(
        [
            Refusal(
                code=code,
                path=None,
                message=segments,
                alternatives=[
                    text(alternative) if isinstance(alternative, str) else alternative
                    for alternative in alternatives
                ],
                limit=None if limit is None else Limit(name=limit[0], max=limit[1]),
            )
        ]
    )


def escaped(name: str) -> str:
    """``name`` with each lone surrogate and noncharacter written as its ``\\u`` escape, so that a
    segment can hold a name a source gives that is not Unicode text (D309)."""
    if is_text(name):
        return name
    return "".join(
        character
        if is_text(character)
        else (
            f"\\u{ord(character):04x}" if ord(character) <= 0xFFFF else f"\\U{ord(character):08x}"
        )
        for character in name
    )


def long_cell_refused(
    found: tuple[int, int], columns: Sequence[str], *table: Segment | str
) -> ImportRefused:
    """A typed source's cell over ``FIELD_CHARACTERS`` characters, at ``found`` (its row and
    column from 1, as ``long_cell`` gives them) in ``table`` (``The sheet …`` or ``The file``),
    whose columns are named ``columns``: ``UNPARSEABLE_SOURCE``, as a text file's field over it
    is. The column is named by its header, and the row by its place among the table's rows (the
    header and blank rows are none of them), not by the sheet's coordinates."""
    row, column = found
    name = columns[column - 1]
    named = ("the column ", data(name)) if name else (f"column {column}, which has no name",)
    return refused(
        RefusalCode.UNPARSEABLE_SOURCE,
        *table,
        f" has a cell of more than {FIELD_CHARACTERS} characters, the most a cell holds: ",
        *named,
        f", row {row} of the table",
    )


def out_of_memory(name: str, maximum: int) -> ImportRefused:
    """The server's own memory ran out (a ``MemoryError``) holding what the source reads to:
    ``LIMIT_EXCEEDED``, naming the limit that bounds it."""
    return refused(
        RefusalCode.LIMIT_EXCEEDED,
        "The server has not the memory to hold what the source reads to",
        limit=(name, maximum),
    )


def within(error: ImportRefused, name: str) -> ImportRefused:
    """The refusals of ``error``, each message saying which file it is about."""
    return ImportRefused(
        [
            refusal.model_copy(
                update={"message": [text("In "), data(name), text(": "), *refusal.message]}
            )
            for refusal in error.refusals
        ]
    )


__all__ = ["ImportRefused", "escaped", "long_cell_refused", "out_of_memory", "refused", "within"]

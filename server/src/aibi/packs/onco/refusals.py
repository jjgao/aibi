"""The refusals of the oncology pack's importer and validator (SPEC §8.6, §10.1, D400, D409).

Each is ``onco.<CODE>``, one of ``CODES`` (pinned by the pack's tests), raised as the pack API's
``Refused``. A refusal points at no path and names no limit and no count (D400): its ``path``,
``limit`` and ``counts`` are empty, and what is refused is named in its message. The message is the
pack's own fixed text, plus, where an entry of the study's directory is meant, that entry's name as
a ``data`` segment (A6), and only when it is Unicode text of at most ``SHOWN_MOST`` characters; any
other name is "an entry whose name cannot be shown". A clinical file's refusal says where, as
numbers: its line, counted from 1 as the pack's reader splits the file, and its column, the
position of a tab-separated field counted from 1. Those are positions, not counts: no message
counts rows or cells. No value read from a file (a meta file's value, a header field, a cell) is
ever part of a message: a value is named by its key or its position, and an attribute by its
position, or by the pack's own constant where the pack's rule names it (a predefined or banned
attribute).
"""

from typing import Literal, get_args

from aibi.core.schema.jsonio import is_text
from aibi.core.schema.output import Segment, data, text
from aibi.core.schema.pack_api import Refused
from aibi.core.schema.refusals import Refusal

Code = Literal[
    "META_FILE",
    "META_FIELD",
    "META_VALUE",
    "TOO_MANY_FILES",
    "NO_STUDY_META",
    "DUPLICATE_STUDY_META",
    "NO_SAMPLE_META",
    "DUPLICATE_CLINICAL_META",
    "STUDY_MISMATCH",
    "DATA_FILE",
    "DUPLICATE_STABLE_ID",
    "NOT_UTF8",
    "CONTROL_CHARACTER",
    "HEADER_ROWS",
    "HEADER_ROW_WIDTH",
    "TOO_MANY_COLUMNS",
    "BLANK_LINE",
    "COMMENT_LINE",
    "REPEATED_HEADER",
    "ROW_WIDTH",
    "LONG_FIELD",
    "TOO_MANY_CELLS",
    "UNREADABLE_ROWS",
    "ATTRIBUTE_NAME",
    "DUPLICATE_ATTRIBUTE",
    "TEXT_VALUE",
    "DATATYPE_ROW",
    "PRIORITY_ROW",
    "PREDEFINED_ATTRIBUTE",
    "BANNED_ATTRIBUTE",
    "ATTRIBUTE_IN_BOTH_FILES",
    "NO_KEY_COLUMN",
    "SAMPLE_ID_IN_PATIENT_FILE",
    "PADDED_KEY",
    "KEY_MISSING",
    "KEY_CHARACTERS",
    "SAMPLE_ID_LENGTH",
    "EXCEL_DATE",
    "SURVIVAL_STATUS",
    "BOOLEAN_VALUE",
    "MISSING_FORM",
    "TYPE_OF_CANCER",
]
CODES: tuple[Code, ...] = get_args(Code)
"""Every code the pack refuses with, without its ``onco.`` prefix: discovery's eleven (M4.2a-1a),
then the clinical files' thirty (``clinical``) and the validator's ``TYPE_OF_CANCER``
(``validators``), M4.2a-1b's. ``UNREADABLE_ROWS`` is the settled list's code for a clinical file
the core's parse would read otherwise than the pack's reader; no input reaches it (the parse
differential of the pack's tests holds the two readers to one reading), so nothing raises it."""

SHOWN_MOST = 200
"""The longest entry name a message or note shows, in characters."""
UNSHOWN = "an entry whose name cannot be shown"


def shown(name: str) -> list[Segment]:
    """An entry's name as a message shows it: the name as ``data``, or ``UNSHOWN``."""
    if len(name) <= SHOWN_MOST and is_text(name):
        return [data(name)]
    return [text(UNSHOWN)]


def refused(code: Code, *message: Segment) -> Refused:
    """The study refused with one refusal, ``onco.<code>``."""
    return Refused([Refusal(code=f"onco.{code}", path=None, message=list(message))])


def entry_refused(code: Code, name: str, reason: str) -> Refused:
    """The study refused for the entry ``name``: its name, then ``reason``, the pack's text."""
    return refused(code, *shown(name), text(" " + reason))


def placed(code: Code, name: str, line: int | None, column: int | None, reason: str) -> Refused:
    """A clinical file ``name`` refused at a place: its line and its column where given, then
    ``reason``, the pack's text."""
    where = "" if line is None else f" line {line}"
    if column is not None:
        where += f"{',' if where else ''} column {column}"
    return refused(code, *shown(name), text(f"{where}: {reason}" if where else f" {reason}"))


__all__ = [
    "CODES",
    "SHOWN_MOST",
    "UNSHOWN",
    "Code",
    "entry_refused",
    "placed",
    "refused",
    "shown",
]

"""The refusals of the oncology pack's study discovery (SPEC §8.6, §10.1, D400, D409).

Each is ``onco.<CODE>``, one of ``CODES`` (pinned by the pack's tests), raised as the pack API's
``Refused``. A refusal points at no path and names no limit and no count (D400): what is refused is
named in its message. The message is the pack's own fixed text, plus, where an entry of the study's
directory is meant, that entry's name as a ``data`` segment (A6), and only when it is Unicode text
of at most ``SHOWN_MOST`` characters; any other name is "an entry whose name cannot be shown". No
value read from a file (a meta file's value, a cell) is ever part of a message: a value is named by
its key, which is the pack's constant.
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
]
CODES: tuple[Code, ...] = get_args(Code)
"""Every code discovery refuses with, without the pack's ``onco.`` prefix."""

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


__all__ = ["CODES", "SHOWN_MOST", "UNSHOWN", "Code", "entry_refused", "refused", "shown"]

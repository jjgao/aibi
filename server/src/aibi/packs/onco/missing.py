"""cBioPortal's missing values in a clinical file's cells (SPEC D409's clinical files, D437).

**The classifier** is cBioPortal's Java importer's (its ``MissingAttributeValues.has`` after the
importer's trim, J:81-110 and J:337 as the plan cites them; re-expressed here, no code copied),
for a cell the reader has already let through, so that it holds no tab, line end or any of the 30
control characters, and Java's trim (every character up to U+0020) removes spaces alone:

1. ``t`` is the cell with its leading and trailing spaces removed (``strip(" ")``);
2. a cell that is not empty while ``t`` is empty (spaces alone) is missing, ``UNKNOWN``;
3. ``t`` with every ``[``, ``]`` and ``|`` removed, each space turned into ``_``, and upper-cased
   by Python's ``str.upper()``, is one of the eight ``NAMES``: missing, ``NOT_APPLICABLE`` for
   ``NOT_APPLICABLE`` and ``UNKNOWN`` for the other seven;
4. anything else is a value (``None``).

So ``[Missing]``, ``N|A``, ``Mi\u00dfing``, ``Pend\u0131ng`` and ``Di\u017fcrepancy`` are
missing, and ``[]``, ``|``, ``N/A``, ``[Unknown]``, ``N A`` and ``NA`` after a no-break space
are values, as in Java under every locale but ``tr``, ``az`` and ``lt`` (D409 lists the three).
An empty cell is no spelling: the core reads it as ``UNKNOWN`` itself.

**``FIXED``** is the spellings declared on every column but a key, whatever its rows hold: each
name in upper, lower and title case, and in sentence case for the two names of two words, written
with a space between the words, each bare and in brackets, and one space: 53 spellings.
**Additions** are the distinct cells of a column that the classifier calls missing and that are
not in ``FIXED``, as written, sorted; each of at most ``ADDITION_MOST`` characters and at most
``ADDITIONS_MOST`` of them in a column, else the column is refused (``MISSING_FORM``, stricter,
D409). A column's ``missing_codes`` is ``FIXED`` with its additions.
"""

from collections.abc import Iterable, Mapping
from types import MappingProxyType
from typing import Literal

State = Literal["UNKNOWN", "NOT_APPLICABLE"]

NAMES: frozenset[str] = frozenset(
    {
        "NOT_APPLICABLE",
        "NOT_AVAILABLE",
        "PENDING",
        "DISCREPANCY",
        "COMPLETED",
        "NULL",
        "MISSING",
        "NA",
    }
)
"""The eight names of cBioPortal's ``MissingAttributeValues``."""
NOT_APPLICABLE = "NOT_APPLICABLE"
ADDITION_MOST = 64
"""The most characters an addition may have."""
ADDITIONS_MOST = 64
"""The most additions a column may have."""
SPACE = " "


def classify(cell: str) -> State | None:
    """The missing state Java's ``has`` gives the cell, or ``None`` for a value (the module's
    docstring). ``cell`` holds no tab, line end or control character."""
    trimmed = cell.strip(SPACE)
    if trimmed == "":
        return "UNKNOWN" if cell != "" else None
    folded = trimmed.replace("[", "").replace("]", "").replace("|", "").replace(SPACE, "_")
    folded = folded.upper()
    if folded not in NAMES:
        return None
    return "NOT_APPLICABLE" if folded == NOT_APPLICABLE else "UNKNOWN"


def _spellings(name: str) -> list[str]:
    words = name.split("_")
    upper = " ".join(word.upper() for word in words)
    lower = " ".join(word.lower() for word in words)
    title = " ".join(word.capitalize() for word in words)
    found = [upper, lower, title]
    if len(words) == 2:
        found.append(f"{words[0].capitalize()} {words[1].lower()}")
    return found


def _fixed() -> Mapping[str, State]:
    found: dict[str, State] = {}
    for name in sorted(NAMES):
        state: State = "NOT_APPLICABLE" if name == NOT_APPLICABLE else "UNKNOWN"
        for spelling in _spellings(name):
            found[spelling] = state
            found[f"[{spelling}]"] = state
    found[SPACE] = "UNKNOWN"
    return MappingProxyType(dict(sorted(found.items())))


FIXED: Mapping[str, State] = _fixed()
"""The 53 spellings every non-key column declares, by spelling, sorted."""


def additions(cells: Iterable[str]) -> dict[str, State] | None:
    """The additions of a column whose distinct missing cells outside ``FIXED`` are ``cells``,
    sorted, or ``None`` when one is over ``ADDITION_MOST`` characters or there are more than
    ``ADDITIONS_MOST``."""
    found: dict[str, State] = {}
    for cell in sorted(set(cells)):
        state = classify(cell)
        if state is None or cell in FIXED:
            continue
        if len(cell) > ADDITION_MOST or len(found) == ADDITIONS_MOST:
            return None
        found[cell] = state
    return found


__all__ = [
    "ADDITIONS_MOST",
    "ADDITION_MOST",
    "FIXED",
    "NAMES",
    "State",
    "additions",
    "classify",
]

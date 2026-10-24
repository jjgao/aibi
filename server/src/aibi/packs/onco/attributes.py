"""cBioPortal's rules on a clinical file's attributes and cells (SPEC D409's clinical files).

The facts are cBioPortal's validator's at cbioportal-core 47890bb, re-expressed in the pack's own
words and data and cited by line (``V:`` is ``scripts/importer/validateData.py``); no code and no
message text is taken from it:

- ``PREDEFINED``: the attributes cBioPortal predefines (V:2451-2550), each with the level it must
  have (a patient's or a sample's) and the datatype its third header row must declare, where the
  validator states one; an attribute it names without either constrains nothing here;
- ``BANNED``: the attributes cBioPortal computes, which a study may not hold (V:2551, V:2731);
- ``NULL_WORDS``: the validator's null words (V:2442), which a header field, a key and the
  validator's exemptions compare a value with after Python's ``str.strip()`` and ``str.lower()``;
- ``ID_CHARACTERS``: the characters an id may hold (V:95, the complement of its class);
- ``excel_date``: the shape a spreadsheet gives a value it turned into a date (V:2795-2806);
- ``OS_VALUES``, ``DFS_VALUES`` and ``survival_prefix``: the survival status columns' values
  (V:2955, V:2971, V:2980-2992), checked in the patient file alone, as the validator does.
"""

from collections.abc import Mapping
from types import MappingProxyType
from typing import Literal, NamedTuple

Level = Literal["patient", "sample"]
Declared = Literal["STRING", "NUMBER", "BOOLEAN"]


class Predefined(NamedTuple):
    """What cBioPortal requires of a predefined attribute: its level and its datatype row, each
    ``None`` where it states none."""

    level: Level | None
    datatype: Declared | None


_PATIENT_STRING = Predefined("patient", "STRING")
_PATIENT_NUMBER = Predefined("patient", "NUMBER")
_SAMPLE_STRING = Predefined("sample", "STRING")
_ANY_STRING = Predefined(None, "STRING")
_FREE = Predefined(None, None)

PREDEFINED: Mapping[str, Predefined] = MappingProxyType(
    {
        "AGE": _PATIENT_NUMBER,
        "CANCER_TYPE": _ANY_STRING,
        "CANCER_TYPE_DETAILED": _ANY_STRING,
        "DFS_STATUS": _PATIENT_STRING,
        "DFS_MONTHS": _PATIENT_NUMBER,
        "DRIVER_MUTATIONS": _FREE,
        "ERG_FUSION_ACGH": _FREE,
        "ETS_RAF_SPINK1_STATUS": _FREE,
        "GENDER": _PATIENT_STRING,
        "GLEASON_SCORE": _FREE,
        "HISTOLOGY": _ANY_STRING,
        "KNOWN_MOLECULAR_CLASSIFIER": _ANY_STRING,
        "METASTATIC_SITE": _SAMPLE_STRING,
        "MOUSE_STRAIN": _FREE,
        "OS_STATUS": _PATIENT_STRING,
        "OS_MONTHS": _PATIENT_NUMBER,
        "OTHER_SAMPLE_ID": _SAMPLE_STRING,
        "PATIENT_DISPLAY_NAME": _PATIENT_STRING,
        "PRIMARY_SITE": _SAMPLE_STRING,
        "SAMPLE_CLASS": _SAMPLE_STRING,
        "SAMPLE_DISPLAY_NAME": _SAMPLE_STRING,
        "SAMPLE_TYPE": _SAMPLE_STRING,
        "SERUM_PSA": _FREE,
        "SEX": _PATIENT_STRING,
        "TMPRSS2_ERG_FUSION_STATUS": _FREE,
        "TUMOR_GRADE": _FREE,
        "TUMOR_SITE": _PATIENT_STRING,
        "TUMOR_STAGE_2009": _FREE,
        "TUMOR_TISSUE_SITE": _SAMPLE_STRING,
        "TUMOR_TYPE": _SAMPLE_STRING,
    }
)
"""cBioPortal's predefined clinical attributes (V:2451-2550), by name as the header writes it."""

BANNED: frozenset[str] = frozenset({"MUTATION_COUNT", "FRACTION_GENOME_ALTERED"})
"""The attributes cBioPortal computes itself (V:2551)."""

PATIENT_ID = "PATIENT_ID"
SAMPLE_ID = "SAMPLE_ID"

NULL_WORDS: frozenset[str] = frozenset(
    {
        "[not applicable]",
        "[not available]",
        "[pending]",
        "[discrepancy]",
        "[completed]",
        "[null]",
        "",
        "na",
    }
)
"""The validator's null words (V:2442), compared with ``value.strip().lower()``."""


def null_word(value: str) -> bool:
    """Whether the validator reads ``value`` as a null word (V:2442, V:2608, V:2780)."""
    return value.strip().lower() in NULL_WORDS


ID_CHARACTERS: frozenset[str] = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._()[]',+-:;"
)
"""The characters a ``PATIENT_ID`` or ``SAMPLE_ID`` may hold (V:95, V:2788)."""
SAMPLE_ID_MOST = 63
"""The most characters a ``SAMPLE_ID`` may have (V:92, V:2844)."""

MONTHS: frozenset[str] = frozenset(
    {"jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"}
)
"""The three-letter English month abbreviations, lower case."""
DATE = "date"
EXCEL_PUNCTUATION = (",", ".", ":", ";")


def dated_column(name: str) -> bool:
    """Whether a column may hold a date: its name, lower-cased, holds ``date`` (V:2797)."""
    return DATE in name.lower()


def excel_date(cell: str) -> bool:
    """Whether a cell has the shape of a value a spreadsheet turned into a date (V:2795-2806),
    in the pack's words: the cell, stripped by Python's ``str.strip()``, splits at ``-`` into
    exactly two parts, holds none of ``,``, ``.``, ``:`` and ``;``, and one of the two parts,
    lower-cased, is a three-letter English month abbreviation. A column whose name holds ``date``
    is exempt (``dated_column``)."""
    if "-" not in cell:
        return False
    value = cell.strip()
    parts = value.split("-")
    if len(parts) != 2 or any(mark in value for mark in EXCEL_PUNCTUATION):
        return False
    return any(part.lower() in MONTHS for part in parts)


OS_STATUS = "OS_STATUS"
DFS_STATUS = "DFS_STATUS"
STATUS = "_STATUS"
MONTHS_SUFFIX = "_MONTHS"
OS_VALUES: frozenset[str] = frozenset({"LIVING", "DECEASED", "0:LIVING", "1:DECEASED"})
"""The values of ``OS_STATUS`` (V:2955)."""
DFS_VALUES: frozenset[str] = frozenset(
    {
        "0:DiseaseFree",
        "1:Recurred/Progressed",
        "1:Recurred",
        "1:Progressed",
        "DiseaseFree",
        "Recurred/Progressed",
        "Recurred",
        "Progressed",
    }
)
"""The values of ``DFS_STATUS`` (V:2971)."""


def survival_status(name: str, names: frozenset[str]) -> Literal["os", "dfs", "prefix"] | None:
    """Which survival status rule a patient file's column follows (V:2950-2992), in the
    validator's order: ``OS_STATUS``, ``DFS_STATUS``, then any other ``<X>_STATUS`` whose file
    holds ``<X>_MONTHS`` (a name ending in ``_MONTHS`` never ends in ``_STATUS``, so no time is
    read as a status); ``None`` for any other column."""
    if name == OS_STATUS:
        return "os"
    if name == DFS_STATUS:
        return "dfs"
    if name.endswith(STATUS) and name[: -len(STATUS)] + MONTHS_SUFFIX in names:
        return "prefix"
    return None


def survival_prefix(value: str) -> bool:
    """Whether a status value of the ``prefix`` rule is written ``0:<label>`` or ``1:<label>``
    (V:2985-2992): split at ``:`` after lower-casing, at least two parts, the first ``0`` or
    ``1``."""
    parts = value.lower().split(":")
    return len(parts) >= 2 and parts[0] in ("0", "1")


TIME_OFFSETS: Mapping[str, Literal["imported_default", "proposed"]] = MappingProxyType(
    {
        "OS_MONTHS": "imported_default",
        "DFS_MONTHS": "imported_default",
        "PFS_MONTHS": "proposed",
        "DSS_MONTHS": "proposed",
    }
)
"""The four survival times, read as a ``time_offset`` in months when their datatype row says
``NUMBER``, with the status of that reading: cBioPortal's file formats document what overall and
disease-free survival count from, and name the other two only."""
MONTHS_UNITS = "mo"


__all__ = [
    "BANNED",
    "DFS_VALUES",
    "ID_CHARACTERS",
    "MONTHS_UNITS",
    "NULL_WORDS",
    "OS_VALUES",
    "PATIENT_ID",
    "PREDEFINED",
    "SAMPLE_ID",
    "SAMPLE_ID_MOST",
    "TIME_OFFSETS",
    "Declared",
    "Level",
    "Predefined",
    "dated_column",
    "excel_date",
    "null_word",
    "survival_prefix",
    "survival_status",
]

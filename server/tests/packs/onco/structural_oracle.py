"""The structural variant map's predicates (M4.2b-4, D445): for each item of D445, the condition
over a cell under which the pack must refuse with it, written here from the reference
(cbioportal-core 47890bb, V:3137-3333 and its bases) and D445, and importing nothing of the pack.

This is not a test: pytest does not collect it. ``expected(cell)`` is the outcome the items give
a whole cell, in the pack's refusal order (D445): the first item whose predicate holds, ``ok``
when none does; ``row_item(...)`` the same for one row of the SV file, given its header, which the
brute forces call directly. The judge (``test_structural_corpus.py``) holds the pack's outcome of
every cell to it, so that a check narrowed in the reader (and its outcomes re-recorded) still
fails: the predicate, not the recorded outcome, says what must be refused.

Where the core's reading matters (an Entrez id the published column would read as a number), the
predicate asks the core itself (``aibi.core.store.cells``), less the declared missing codes.
"""

import re
import unicodedata
from collections.abc import Mapping, Sequence
from typing import Any

SAMPLE = "Sample_Id"
STATUS = "SV_Status"
BUILD = "NCBI_Build"
HUGO = ("Site1_Hugo_Symbol", "Site2_Hugo_Symbol")
ENTREZ = ("Site1_Entrez_Gene_Id", "Site2_Entrez_Gene_Id")
NULL = ("", "NA", "Unknown", "0")
"""What ``checkPresenceValue`` reads as no value, after ``str.strip()`` (V:3220)."""
CONTROL = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
"""The 30 control characters the pack refuses anywhere (D409)."""
LINE_END = re.compile(rb"\r\n|\n|\r")
FIELD_MOST = 131_072
COLUMNS_MOST = 4_096
NAME_MOST = 4_096
SNIFFED = 5
STABLE_ID = "structural_variants"
UNIQUE_KEY = (
    "Sample_Id",
    "Site1_Hugo_Symbol",
    "Site1_Entrez_Gene_Id",
    "Site1_Chromosome",
    "Site1_Position",
    "Site1_Region_Number",
    "Site1_Ensembl_Transcript_Id",
    "Site2_Hugo_Symbol",
    "Site2_Entrez_Gene_Id",
    "Site2_Chromosome",
    "Site2_Position",
    "Site2_Region_Number",
    "Site2_Ensembl_Transcript_Id",
    "Event_Info",
)
"""V:3153-3167, the duplicate check's columns, written again here."""
PROFILE_KEYS = (
    "cancer_study_identifier",
    "genetic_alteration_type",
    "datatype",
    "stable_id",
    "show_profile_in_analysis_tab",
    "profile_name",
    "profile_description",
    "data_filename",
)
"""C:339-350, the SV meta's mandatory keys in order."""

# --- The pack's reasons, as the items name them --------------------------------------------------

SV = "onco."
READS = (
    "has a data_filename that names a meta file or a clinical data file, exactly or under case "
    "and normalisation folding"
)
REASONS: Mapping[str, str] = {
    "not_utf8": "NOT_UTF8|the line is not UTF-8",
    "control": "CONTROL_CHARACTER|the field holds a control character",
    "no_header": "NO_HEADER|the file has no line of column names",
    "too_many_columns": "TOO_MANY_COLUMNS|the file has more columns than a table may",
    "names_empty": "COLUMN_NAME|the line of column names is empty",
    "name_long": "COLUMN_NAME|the column name is empty or longer than a descriptor's string",
    "name_text": "COLUMN_NAME|the column name is not Unicode text",
    "duplicate_column": "DUPLICATE_COLUMN|the column name repeats an earlier's",
    "no_tab": "MISSING_COLUMN|the line of column names and the five lines after it hold no tab",
    "required": "MISSING_COLUMN|the file has no {} column",
    "site": "MISSING_COLUMN|the file names a site's gene by neither {} nor {}",
    "namespace": "MISSING_COLUMN|the file has no column of a namespace its meta file declares",
    "blank": "BLANK_LINE|the line is blank",
    "comment": "COMMENT_LINE|a data line starts with '#'",
    "comment_spaced": "COMMENT_LINE|a data line starts with '#' after spaces or tabs",
    "repeated_header": "REPEATED_HEADER|the line repeats the column names",
    "row_width": ("ROW_WIDTH|the line does not have as many fields as there are column names"),
    "long_field": "LONG_FIELD|the field is longer than a cell may be",
    "null_sample": "UNKNOWN_SAMPLE|the sample id is a spelling of no value",
    "undefined_sample": "UNKNOWN_SAMPLE|the sample id is no sample id of the sample file",
    "status": "SV_STATUS|the SV status is neither SOMATIC nor GERMLINE",
    "build": "NCBI_BUILD|the NCBI build is neither GRCh37 nor GRCh38",
    "second_build": "NCBI_BUILD|the NCBI build is not the file's first one",
    "nonpositive": "ENTREZ_VALUE|the Entrez gene id is not positive",
    "no_gene": "NO_GENE|the row names a gene at neither site, by symbol or id",
    "padded_sample": (
        "PADDED_VALUE|the sample id is a sample's with whitespace around it, which cBioPortal "
        "strips and the published column would keep"
    ),
    "padded_null": (
        "PADDED_VALUE|the value is a spelling of no value with whitespace around it, or "
        "whitespace alone, which cBioPortal reads as no value and the published column would "
        "read as a value"
    ),
    "padded_zero": (
        "PADDED_VALUE|the Entrez gene id is 0 with spaces around it, which cBioPortal reads as "
        "no value and the published column would read as the value 0"
    ),
    "not_integer": "ENTREZ_VALUE|the Entrez gene id is a number but no integer",
    "second_meta": "DUPLICATE_PROFILE_META|is a second structural variant meta file",
    "no_file": (
        "DATA_FILE|is a structural variant meta file whose data_filename names no regular file "
        "in the study's directory"
    ),
    "stable_id": "META_VALUE|holds a stable_id that a structural variant meta may not",
    "name_not_text": (
        "DATA_FILE|has a data_filename that names a structural variant file whose name is not "
        "Unicode text"
    ),
    "p0": "DATA_FILE|has a data_filename that is not a file name M4.2a accepts",
    "symlink": (
        "DATA_FILE|has a data_filename that names a symlink, exactly or under case and "
        "normalisation folding"
    ),
    "reads": f"DATA_FILE|{READS}",
    "meta_type": "META_FILE|has no meta file type cBioPortal defines",
    "meta_key": "META_FIELD|lacks the key {}",
    "duplicate_stable_id": "DUPLICATE_STABLE_ID|repeats another meta file's stable_id",
    "study_mismatch": "STUDY_MISMATCH|names another study than the study meta",
}
"""Each item's refusal, ``<CODE>|<reason>`` (``{}`` a field), as the pack writes it."""

ROW_ORDER = (
    "null_sample",
    "undefined_sample",
    "status",
    "build",
    "second_build",
    "nonpositive",
    "no_gene",
    "padded_sample",
    "padded_null",
    "padded_zero",
    "not_integer",
)
"""The cell items in the pack's refusal order within a row (D445): the same-outcome items, then
the stricter. Every item that can come before a stricter one has a predicate here."""


def said(item: str, *fields: str) -> str:
    """An item's outcome as ``outcome`` writes it: ``onco.<CODE>|<reason>``, fields filled."""
    text = REASONS[item]
    for field in fields:
        text = text.replace("{}", field, 1)
    return SV + text


# --- The cell predicates (one row) ----------------------------------------------------------------


def stripped_null(cell: str) -> bool:
    """``checkPresenceValue`` reads ``cell`` as no value (V:3218-3221)."""
    return cell.strip() in NULL


def as_int(text: str) -> int | None:
    try:
        return int(text)
    except ValueError:
        return None


def core_reads(cell: str) -> bool:
    """The published integer column reads ``cell`` as a number: the core's parse reads it and it
    is no declared missing code (``''``, ``NA``, ``Unknown``, ``0``; D445)."""
    from aibi.core.store.cells import _integer  # pyright: ignore[reportPrivateUsage]

    return cell not in NULL and _integer(cell, cell) is not None


def padded_null(cell: str) -> bool:
    """A spelling of no value once stripped, written otherwise: whitespace around it, or alone."""
    return stripped_null(cell) and cell not in NULL


def row_items(
    names: Sequence[str],
    fields: Sequence[str],
    samples: frozenset[str],
    first_build: str | None,
) -> list[str]:
    """Every cell item whose predicate holds on the row ``fields`` under the header ``names``,
    in ``ROW_ORDER``, given the sample ids and the file's first build so far."""
    at = {name: index for index, name in enumerate(names)}

    def get(column: str) -> str | None:
        return fields[at[column]] if column in at else None

    holds: dict[str, bool] = dict.fromkeys(ROW_ORDER, False)
    raw = get(SAMPLE) or ""
    holds["null_sample"] = stripped_null(raw)
    holds["undefined_sample"] = (
        not stripped_null(raw) and raw not in samples and raw.strip() not in samples
    )
    holds["padded_sample"] = (
        not stripped_null(raw) and raw not in samples and raw.strip() in samples
    )
    status = get(STATUS) or ""
    holds["status"] = not stripped_null(status) and status.strip().lower() not in (
        "somatic",
        "germline",
    )
    build = get(BUILD)
    if build is not None and not stripped_null(build):
        holds["build"] = build.strip().lower() not in ("grch37", "grch38")
        holds["second_build"] = first_build is not None and build.strip() != first_build
    named = False
    for hugo, entrez in zip(HUGO, ENTREZ, strict=True):
        symbol, number = get(hugo), get(entrez)
        named = named or (symbol is not None and not stripped_null(symbol))
        named = named or (number is not None and not stripped_null(number))
        if number is not None and not stripped_null(number):
            value = as_int(number.strip())
            holds["nonpositive"] = holds["nonpositive"] or (value is not None and value <= 0)
            holds["not_integer"] = holds["not_integer"] or (value is None and core_reads(number))
        if number is not None:
            zero = number.strip() == "0" and number != "0" and core_reads(number)
            holds["padded_zero"] = holds["padded_zero"] or zero
    holds["no_gene"] = not named
    texts = [get(column) for column in (STATUS, BUILD, *HUGO)]
    holds["padded_null"] = any(text is not None and padded_null(text) for text in texts)
    return [item for item in ROW_ORDER if holds[item]]


def row_item(
    names: Sequence[str], fields: Sequence[str], samples: frozenset[str], first: str | None = None
) -> str | None:
    """The row's first item, or ``None``."""
    found = row_items(names, fields, samples, first)
    return found[0] if found else None


# --- The file -------------------------------------------------------------------------------------


def _lines(data: bytes) -> list[bytes]:
    out: list[bytes] = []
    start = 0
    for found in LINE_END.finditer(data):
        out.append(data[start : found.start()])
        start = found.end()
    if start < len(data):
        out.append(data[start:])
    return out


def _is_text(name: str) -> bool:
    """Unicode text as a descriptor's string is: no lone surrogate and no noncharacter."""
    for character in name:
        point = ord(character)
        if 0xD800 <= point <= 0xDFFF:
            return False
        if 0xFDD0 <= point <= 0xFDEF or (point & 0xFFFE) == 0xFFFE:
            return False
    return True


def read_item(
    data: bytes, samples: frozenset[str], namespaces: Sequence[str]
) -> tuple[str, tuple[str, ...]] | None:
    """The SV file's first item and its fields, by class: a line not UTF-8, a control character,
    the structure, then the first row's first cell item; ``None`` when none holds."""
    lines = _lines(data)
    decoded: list[str] = []
    for line in lines:
        try:
            decoded.append(line.decode("utf-8"))
        except UnicodeDecodeError:
            return "not_utf8", ()
    if any(CONTROL.search(line) for line in decoded):
        return "control", ()
    header = None
    for number, line in enumerate(decoded):
        text = line[1:] if number == 0 and line.startswith("\ufeff") else line
        if text.startswith("#"):
            continue
        header = number, text
        break
    if header is None:
        return "no_header", ()
    at, text = header
    names = text.split("\t")
    if len(names) > COLUMNS_MOST:
        return "too_many_columns", ()
    if names == [""]:
        return "names_empty", ()
    for name in names:
        if not 0 < len(name) <= NAME_MOST:
            return "name_long", ()
        if not _is_text(name):
            return "name_text", ()
    if len(set(names)) < len(names):
        return "duplicate_column", ()
    sniffed = decoded[at : at + 1 + SNIFFED]
    if len(names) == 1 and not any("\t" in line for line in sniffed):
        return "no_tab", ()
    for required in (SAMPLE, STATUS):
        if required not in names:
            return "required", (required,)
    for hugo, entrez in zip(HUGO, ENTREZ, strict=True):
        if hugo not in names and entrez not in names:
            return "site", (hugo, entrez)
    for space in namespaces:
        if not any(name.lower().startswith(space + ".") for name in names):
            return "namespace", ()
    rows: list[list[str]] = []
    for line in decoded[at + 1 :]:
        if line.strip() == "":
            return "blank", ()
        if line.startswith("#"):
            return "comment", ()
        if line.lstrip(" \t").startswith("#"):
            return "comment_spaced", ()
        fields = line.split("\t")
        if fields[: len(names)] == names:
            return "repeated_header", ()
        if len(fields) != len(names):
            return "row_width", ()
        if any(len(field) > FIELD_MOST for field in fields):
            return "long_field", ()
        rows.append(fields)
    first: str | None = None
    for fields in rows:
        found = row_item(names, fields, samples, first)
        if found is not None:
            return found, ()
        if BUILD in names:
            build = fields[names.index(BUILD)]
            if first is None and not stripped_null(build):
                first = build.strip()
    return None


def reference_checks_nothing(data: bytes) -> bool:
    """The reference's sniffer gives up and it checks nothing (V:485-496): its header is the first
    line, after universal-newline splitting, that does not start with ``#`` (a byte order mark is
    part of the line), and neither it nor the five lines after it holds a tab. With no such line
    it reports that there is no header instead."""
    lines = [line.decode("utf-8", "replace") for line in _lines(data)]
    for at, line in enumerate(lines):
        if not line.startswith("#"):
            return not any("\t" in sniffed for sniffed in lines[at : at + 6])
    return False


def duplicate_rows(data: bytes) -> bool:
    """Two data rows the reference keys alike (V:3318-3328): the fields, as split after universal
    newline splitting, of the ``UNIQUE_KEY`` columns the header holds."""
    lines = [line.decode() for line in _lines(data) if not line.startswith(b"#")]
    names = lines[0].split("\t")  # the reference keys rows by its own names, a mark kept
    held = [names.index(column) for column in UNIQUE_KEY if column in names]
    keys = [tuple(row.split("\t")[index] for index in held) for row in lines[1:]]
    return len(set(keys)) < len(keys)


# --- The study ------------------------------------------------------------------------------------

META_NAME = re.compile(r"(\b|_)meta(\b|[_0-9])", re.IGNORECASE)


def fold(name: str) -> str:
    return unicodedata.normalize("NFD", unicodedata.normalize("NFD", name).casefold())


def parse_meta(content: bytes) -> dict[str, str]:
    found: dict[str, str] = {}
    for line in content.decode().splitlines():
        if line.strip():
            key, _, value = line.partition(":")
            found[key] = value.strip()
    return found


def _samples(content: bytes) -> frozenset[str]:
    lines = [line for line in content.decode().splitlines() if not line.startswith("#")]
    at = lines[0].split("\t").index("SAMPLE_ID")
    return frozenset(line.split("\t")[at] for line in lines[1:])


def one_name(value: str) -> bool:
    if len(value.encode("utf-8", "surrogatepass")) > 255 or value in ("", ".", ".."):
        return False
    return "\0" not in value and "/" not in value


def expected(cell: Mapping[str, Any]) -> str:
    """The outcome D445's items give the cell: ``ok``, or ``onco.<CODE>|<reason>``."""
    metas = {
        name: parse_meta(content)
        for name, content in sorted(cell.items())
        if "/" not in name and isinstance(content, bytes) and META_NAME.search(name)
    }
    svs = [n for n, m in metas.items() if m.get("genetic_alteration_type") == "STRUCTURAL_VARIANT"]
    for name in svs:
        if metas[name].get("datatype") != "SV":
            return said("meta_type")
        for key in PROFILE_KEYS:
            if key not in metas[name]:
                return said("meta_key", key)
    seen: set[str] = set()
    for found in metas.values():
        if "stable_id" in found:
            if found["stable_id"].strip() in seen:
                return said("duplicate_stable_id")
            seen.add(found["stable_id"].strip())
    study = metas.get("meta_study.txt", {}).get("cancer_study_identifier")
    for name in svs:
        if metas[name].get("cancer_study_identifier", study) != study:
            return said("study_mismatch")
    if not svs:
        return "ok"
    if len(svs) > 1:
        return said("second_meta")
    sv_meta = metas[svs[0]]
    value = sv_meta["data_filename"]
    imported = ("CLINICAL", "MUTATION_EXTENDED")
    reads = set(metas)
    reads |= {
        m["data_filename"]
        for m in metas.values()
        if m.get("genetic_alteration_type") in imported and m.get("datatype") != "TIMELINE"
    }
    if not one_name(value):
        return said("p0")
    listed = {name.split("/")[0]: content for name, content in cell.items()}
    alike = [name for name in listed if fold(name) == fold(value)]
    if any(isinstance(listed[name], tuple) for name in alike):
        return said("symlink")
    if any(name in reads for name in alike):
        return said("reads")
    content = listed.get(value)
    if not isinstance(content, bytes):
        return said("no_file")
    if sv_meta["stable_id"].strip() != STABLE_ID:
        return said("stable_id")
    for found in metas.values():
        if found.get("datatype") == "TIMELINE" and fold(found["data_filename"]) == fold(value):
            return said("reads")
    if not _is_text(value):
        return said("name_not_text")
    spaces = sv_meta.get("namespaces")
    namespaces = [] if spaces is None else [s.strip().lower() for s in spaces.split(",")]
    samples = _samples(cell["data_clinical_sample.txt"])
    found_item = read_item(content, samples, sorted(set(namespaces)))
    if found_item is None:
        return "ok"
    item, fields = found_item
    return said(item, *fields)

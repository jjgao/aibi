"""The discrete copy-number map's predicates (M4.2b-3, D446): for each item of D446, the condition
over a cell under which the pack must refuse with it, written here from the reference
(cbioportal-core 47890bb: ``CNADiscreteValidator``, V:1232-1257, and its bases, V:361-720 and
V:1026-1230, the gene identification of V:760-853 offline, the ``_cna`` case list of V:5042-5048)
and D446, and importing nothing of the pack.

This is not a test: pytest does not collect it. ``expected(cell)`` is the outcome the items give a
whole cell, in the pack's refusal order (D446): the first item whose predicate holds, ``ok`` when
none does; ``row_item(...)`` the same for one row of the matrix, given its header and the rows
before it, which the brute forces call directly; ``collision(first, second)`` the one predicate
over both gene keys of a pair of rows (review r2 MJ1). The judge (``test_cna_corpus.py``) holds the
pack's outcome of every cell to it, so that a check narrowed in the reader (and its outcomes
re-recorded) still fails: the predicate, not the recorded outcome, says what must be refused.

**Unicode.** The reference key uses ``str.strip()`` and ``str.upper()``, whose tables follow the
Python's Unicode version: the pinned reference ran under Python 3.12.3, Unicode 15.0.0, and so does
this oracle (``UNICODE``, held by ``test_cna.py``).
"""

import re
import unicodedata
from collections.abc import Collection, Mapping, Sequence
from typing import Any

UNICODE = "15.0.0"
"""The Unicode version of the reference's Python (3.12.3) when its cells were recorded."""
CYTOBAND = "Cytoband"
HUGO = "Hugo_Symbol"
ENTREZ = "Entrez_Gene_Id"
PREFIX = (CYTOBAND, HUGO, ENTREZ)
"""V:1056-1063 with V:1220: the gene columns, read as a prefix of the header."""
VALUES = ("-2", "-1.5", "-1", "0", "1", "2", "NA")
"""V:1235: the cells the reference reads, once stripped."""
STABLE_IDS = ("cna", "cna_rae", "cna_consensus", "gistic")
"""C:739-744: the discrete CNA ``stable_id``s."""
CONTROL = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
"""The 30 control characters the pack refuses anywhere (D409)."""
LINE_END = re.compile(rb"\r\n|\n|\r")
FIELD_MOST = 131_072
SNIFFED = 5
BOM = "\ufeff"
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
"""C:124-135, the discrete CNA meta's mandatory keys in order."""

READS = (
    "{} a {} that names a meta file or a clinical data file, exactly or under case and "
    "normalisation folding"
)
REASONS: Mapping[str, str] = {
    "not_utf8": "NOT_UTF8|the line is not UTF-8",
    "control": "CONTROL_CHARACTER|the field holds a control character",
    "no_header": "NO_HEADER|the file has no line of column names",
    "bom": (
        "BYTE_ORDER_MARK|the file starts with a byte order mark, which cBioPortal reads into the "
        "first column's name"
    ),
    "no_tab": "MISSING_COLUMN|the line of column names and the five lines after it hold no tab",
    "too_many_columns": (
        "TOO_MANY_COLUMNS|the file has more columns than its gene columns and the sample file's "
        "samples"
    ),
    "duplicate_column": "DUPLICATE_COLUMN|the column name repeats an earlier's",
    "placement": "COLUMN_NAME|a gene column comes after a sample column",
    "no_gene_column": (
        "MISSING_COLUMN|the file has neither a Hugo_Symbol nor an Entrez_Gene_Id column before its "
        "samples"
    ),
    "no_samples": "MISSING_COLUMN|the file has no sample column",
    "unknown_sample": "UNKNOWN_SAMPLE|the sample id is no sample id of the sample file",
    "no_hugo": "MISSING_COLUMN|the file has no Hugo_Symbol column, which the pack keys a gene by",
    "blank": "BLANK_LINE|the line is blank",
    "comment": "COMMENT_LINE|a data line starts with '#'",
    "repeated_header": "REPEATED_HEADER|the line repeats the column names",
    "row_width": "ROW_WIDTH|the line does not have as many fields as there are column names",
    "long_field": "LONG_FIELD|the field is longer than a cell may be",
    "nonpositive": "ENTREZ_VALUE|the Entrez gene id is not positive",
    "no_gene": "NO_GENE|the row names its gene by neither symbol nor id",
    "not_integer": (
        "ENTREZ_VALUE|the Entrez gene id is not an integer, so cBioPortal would not load the row"
    ),
    "symbol_repeat": "DUPLICATE_GENE|the gene symbol is an earlier row's",
    "key_repeat": (
        "DUPLICATE_GENE|cBioPortal keys the gene as an earlier row's and keeps that row alone"
    ),
    "value": "CNA_VALUE|the value is none cBioPortal reads as a discrete copy number",
    "entrez_alone": (
        "NO_GENE|the row names its gene by its Entrez gene id alone, and the pack keys a gene by "
        "its symbol"
    ),
    "padded_symbol": (
        "PADDED_VALUE|the gene symbol has whitespace around it, which cBioPortal strips and the "
        "published column would keep"
    ),
    "padded_entrez": (
        "PADDED_VALUE|the Entrez gene id has whitespace around it, which cBioPortal strips and "
        "the pack does not read"
    ),
    "padded_value": (
        "PADDED_VALUE|the value has whitespace around it, which cBioPortal strips and the "
        "published column would keep"
    ),
    "line_ends": "LINE_ENDS|the file's line ends are not all of one kind, or none",
    "second_meta": "DUPLICATE_PROFILE_META|is a second discrete copy-number meta file",
    "no_file": (
        "DATA_FILE|is a discrete copy-number meta file whose data_filename names no regular file "
        "in the study's directory"
    ),
    "stable_id": "META_VALUE|holds a stable_id that a discrete copy-number meta may not",
    "no_list": (
        "NO_CNA_LIST|is a discrete copy-number meta file, and no case list's stable_id is the "
        "study id followed by _cna"
    ),
    "name_not_text": (
        "DATA_FILE|has a data_filename that names a discrete copy-number file whose name is not "
        "Unicode text"
    ),
    "p0": "DATA_FILE|has a {} that is not a file name M4.2a accepts",
    "symlink": (
        "DATA_FILE|has a {} that names a symlink, exactly or under case and normalisation folding"
    ),
    "reads": "DATA_FILE|has a {} that " + READS.split("that ", 1)[1],
    "meta_type": "META_FILE|has no meta file type cBioPortal defines",
    "meta_key": "META_FIELD|lacks the key {}",
    "duplicate_stable_id": "DUPLICATE_STABLE_ID|repeats another meta file's stable_id",
    "study_mismatch": "STUDY_MISMATCH|names another study than the study meta",
    "list_id": "UNKNOWN_SAMPLE|lists an id that is no sample id of the sample file",
    # the shared modules' refusals of a meta or a case list, out of the CNA map (OUT_OF_MAP)
    "meta_not_utf8": "META_FILE|is not UTF-8",
    "meta_colon": "META_FILE|has a line without ':'",
    "stable_charset": "META_VALUE|holds a stable_id that M4.2a does not accept",
    "list_not_utf8": "NOT_UTF8|the case list's line is not UTF-8",
    "list_control": "CONTROL_CHARACTER|the line holds a control character",
    "list_colon": "CASE_LIST|the line has no ':'",
    "list_key": "CASE_LIST|lacks the key {}",
    "list_study": "CASE_LIST|names another study than the study meta",
    "list_prefix": "CASE_LIST|holds a stable_id that does not start with the study id and '_'",
    "list_repeat": "CASE_LIST|repeats another case list's stable_id",
    "list_category": "CASE_LIST|holds a case_list_category that cBioPortal does not define",
}
"""Each item's refusal, ``<CODE>|<reason>`` (``{}`` a field), as the pack writes it."""

ROW_ORDER = (
    "nonpositive",
    "no_gene",
    "not_integer",
    "symbol_repeat",
    "key_repeat",
    "value",
    "entrez_alone",
    "padded_symbol",
    "padded_entrez",
    "padded_value",
)
"""The row items in the pack's refusal order (D446): the same-outcome gene items; the rows the
reference neither loads nor checks; the same-outcome value; then the stricter."""


def said(item: str, *fields: str) -> str:
    """An item's outcome as ``outcome`` writes it: ``onco.<CODE>|<reason>``, fields filled."""
    text = REASONS[item]
    for field in fields:
        text = text.replace("{}", field, 1)
    return "onco." + text


# --- The row predicates ---------------------------------------------------------------------------


def as_int(text: str) -> int | None:
    """``int()`` as V:815-821 calls it."""
    try:
        return int(text)
    except ValueError:
        return None


def entrez_of(entrez: str | None) -> str | None:
    """V:1192-1197: the Entrez id stripped, ``None`` for none or an empty one."""
    if entrez is None:
        return None
    stripped = entrez.strip()
    return stripped if stripped != "" else None


def reference_key(symbol: str, entrez: str | None) -> str | None:
    """The key the reference gives a row it loads (V:1094-1106 with V:1178-1198, V:1225-1230,
    V:766-771, V:853): the Entrez id stripped where there is one, else the symbol stripped, cut at
    its first ``|`` (nothing stripped after the cut) and upper-cased; ``None`` for a row it skips
    (an Entrez id ``int()`` does not read, or one of 0 or less) or does not identify (neither)."""
    number = entrez_of(entrez)
    if number is not None:
        value = as_int(number)
        return None if value is None or value <= 0 else number
    stripped = symbol.strip()
    if stripped == "":
        return None
    return stripped.split("|", 1)[0].upper()


def collision(first: tuple[str, str | None], second: tuple[str, str | None]) -> str | None:
    """Review r2 MJ1: the one predicate over both keys of a later row ``second`` after a row
    ``first`` the file kept: ``symbol_repeat`` when aibi's keys (the symbols as written) collide,
    else ``key_repeat`` when the reference's do, else ``None``."""
    if second[0] == first[0]:
        return "symbol_repeat"
    one, two = reference_key(*first), reference_key(*second)
    if one is not None and one == two:
        return "key_repeat"
    return None


def row_items(
    names: Sequence[str],
    fields: Sequence[str],
    kept: Sequence[tuple[str, str | None]] = (),
) -> list[str]:
    """Every row item whose predicate holds on the row ``fields`` under the header ``names`` (whose
    gene columns are its prefix), in ``ROW_ORDER``, given the rows ``kept`` before it (each as its
    symbol and Entrez id)."""
    prefix = 0
    while prefix < len(names) and names[prefix] in PREFIX:
        prefix += 1
    genes = names[:prefix]
    symbol = fields[genes.index(HUGO)]
    entrez = fields[genes.index(ENTREZ)] if ENTREZ in genes else None
    number = entrez_of(entrez)
    value = None if number is None else as_int(number)
    cells = fields[prefix:]
    holds = dict.fromkeys(ROW_ORDER, False)
    holds["nonpositive"] = value is not None and value <= 0
    holds["no_gene"] = symbol.strip() == "" and number is None
    holds["not_integer"] = number is not None and value is None
    found = [collision(row, (symbol, entrez)) for row in kept]
    holds["symbol_repeat"] = "symbol_repeat" in found
    holds["key_repeat"] = "key_repeat" in found
    holds["value"] = any(cell.strip() not in VALUES for cell in cells)
    holds["entrez_alone"] = symbol.strip() == "" and number is not None
    holds["padded_symbol"] = symbol != symbol.strip()
    holds["padded_entrez"] = entrez is not None and entrez != entrez.strip()
    holds["padded_value"] = any(cell != cell.strip() for cell in cells)
    return [item for item in ROW_ORDER if holds[item]]


def row_item(
    names: Sequence[str], fields: Sequence[str], kept: Sequence[tuple[str, str | None]] = ()
) -> str | None:
    """The row's first item, or ``None``."""
    found = row_items(names, fields, kept)
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
    for character in name:
        point = ord(character)
        if 0xD800 <= point <= 0xDFFF:
            return False
        if 0xFDD0 <= point <= 0xFDEF or (point & 0xFFFE) == 0xFFFE:
            return False
    return True


def line_ends(data: bytes) -> bool:
    """V:708-713: the file's newlines, as Python's universal newlines record them, are not one kind
    (mixed, or none at all)."""
    kinds = {found.group() for found in LINE_END.finditer(data)}
    return len(kinds) != 1


def reference_checks_nothing(data: bytes) -> bool:
    """The reference's sniffer gives up and it checks nothing (V:485-496): its header is the first
    line, after universal-newline splitting, that does not start with ``#`` (a byte order mark is
    part of the line), and neither it nor the five lines after it holds a tab."""
    lines = [line.decode("utf-8", "replace") for line in _lines(data)]
    for at, line in enumerate(lines):
        if not line.startswith("#"):
            return not any("\t" in sniffed for sniffed in lines[at : at + 1 + SNIFFED])
    return False


def read_item(data: bytes, samples: Collection[str]) -> tuple[str, tuple[str, ...]] | None:
    """The matrix's first item and its fields, by class: a line not UTF-8, a control character,
    the structure (the header, then every data line), the first row's first item, the line ends;
    ``None`` when none holds."""
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
        if number == 0 and line.startswith(BOM):
            return "bom", ()
        if line.startswith("#"):
            continue
        header = number, line
        break
    if header is None:
        return "no_header", ()
    at, text = header
    names = text.split("\t")
    sniffed = decoded[at : at + 1 + SNIFFED]
    if len(names) == 1 and not any("\t" in line for line in sniffed):
        return "no_tab", ()
    if len(names) > len(PREFIX) + len(samples):
        return "too_many_columns", ()
    if len(set(names)) < len(names):
        return "duplicate_column", ()
    prefix = 0
    while prefix < len(names) and names[prefix] in PREFIX:
        prefix += 1
    if HUGO in names[prefix:] or ENTREZ in names[prefix:]:
        return "placement", ()
    if HUGO not in names[:prefix] and ENTREZ not in names[:prefix]:
        return "no_gene_column", ()
    if prefix == len(names):
        return "no_samples", ()
    if any(name not in samples for name in names[prefix:]):
        return "unknown_sample", ()
    if HUGO not in names[:prefix]:
        return "no_hugo", ()
    rows: list[list[str]] = []
    for line in decoded[at + 1 :]:
        if line.strip() == "":
            return "blank", ()
        if line.startswith("#"):
            return "comment", ()
        fields = line.split("\t")
        if fields[: len(names)] == names:
            return "repeated_header", ()
        if len(fields) != len(names):
            return "row_width", ()
        if any(len(field) > FIELD_MOST for field in fields):
            return "long_field", ()
        rows.append(fields)
    genes = names[:prefix]
    kept: list[tuple[str, str | None]] = []
    for fields in rows:
        found = row_item(names, fields, kept)
        if found is not None:
            return found, ()
        entrez = fields[genes.index(ENTREZ)] if ENTREZ in genes else None
        kept.append((fields[genes.index(HUGO)], entrez))
    if line_ends(data):
        return "line_ends", ()
    return None


# --- The study ------------------------------------------------------------------------------------

META_NAME = re.compile(r"(\b|_)meta(\b|[_0-9])", re.IGNORECASE)
DISCRETE = ("COPY_NUMBER_ALTERATION", "DISCRETE")


def fold(name: str) -> str:
    return unicodedata.normalize("NFD", unicodedata.normalize("NFD", name).casefold())


LINES = re.compile(r"\r\n|\n|\r")
"""The line ends the shared parsers split at (``meta.LINES``), never ``str.splitlines()``."""
OUT_OF_MAP = frozenset(
    {
        "meta_not_utf8",
        "meta_colon",
        "stable_charset",
        "list_not_utf8",
        "list_control",
        "list_colon",
        "list_key",
        "list_study",
        "list_prefix",
        "list_repeat",
        "list_category",
    }
)
"""The predicates of the shared modules' refusals a CNA cell can reach on a meta or a case list
(``meta``, ``study``, ``caselists``; D409, D444), outside the CNA map: the oracle predicts them
exactly, and the judge holds them to ``test_cna_corpus.OUT_OF_MAP`` instead of the inventory."""
SEG_KEYS = (
    "cancer_study_identifier",
    "genetic_alteration_type",
    "datatype",
    "reference_genome_id",
    "data_filename",
    "description",
)
"""C:170: a SEG meta's mandatory keys in order."""
CLINICAL_KEYS = ("cancer_study_identifier", "genetic_alteration_type", "datatype", "data_filename")
STABLE_ID_CHARACTERS = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._()[]',+-:;"
)
"""V:95: the characters a ``stable_id`` may hold."""
LIST_KEYS = (
    "cancer_study_identifier",
    "stable_id",
    "case_list_name",
    "case_list_description",
    "case_list_ids",
)
CATEGORIES = frozenset(
    {
        "all_cases_in_study",
        "all_cases_with_mutation_data",
        "all_cases_with_cna_data",
        "all_cases_with_log2_cna_data",
        "all_cases_with_methylation_data",
        "all_cases_with_mrna_array_data",
        "all_cases_with_mrna_rnaseq_data",
        "all_cases_with_rppa_data",
        "all_cases_with_microrna_data",
        "all_cases_with_mutation_and_cna_data",
        "all_cases_with_mutation_and_cna_and_mrna_data",
        "all_cases_with_gsva_data",
        "all_cases_with_sv_data",
        "other",
    }
)
"""V:4935-4949."""


def meta_fault(content: bytes) -> str | None:
    """The shared parser's refusal of a meta file (C:852-870 as ``meta.parse`` reads it): not
    UTF-8, or a line, past one leading byte order mark and blank lines, without ``:``."""
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return "meta_not_utf8"
    text = text.removeprefix(BOM)
    if any(line.strip() and ":" not in line for line in LINES.split(text)):
        return "meta_colon"
    return None


def parse_meta(content: bytes) -> dict[str, str]:
    """A meta file's keys and values as the shared parser reads them: past one byte order mark,
    split at ``\r\n``, ``\n`` and ``\r`` alone, each value stripped, the last of a repeated
    key kept; total (a file the parser refuses reads as what it holds)."""
    text = content.decode("utf-8", "replace").removeprefix(BOM)
    found: dict[str, str] = {}
    for line in LINES.split(text):
        if line.strip():
            key, _, value = line.partition(":")
            found[key] = value.strip()
    return found


PAIR_TYPES: Mapping[str, str] = {
    "CANCER_TYPE:CANCER_TYPE": "CANCER_TYPE",
    "CLINICAL:PATIENT_ATTRIBUTES": "PATIENT_ATTRIBUTES",
    "CLINICAL:SAMPLE_ATTRIBUTES": "SAMPLE_ATTRIBUTES",
    "CLINICAL:TIMELINE": "TIMELINE",
    "PROTEIN_LEVEL:LOG2-VALUE": "PROTEIN",
    "PROTEIN_LEVEL:Z-SCORE": "PROTEIN",
    "PROTEIN_LEVEL:CONTINUOUS": "PROTEIN",
    "COPY_NUMBER_ALTERATION:DISCRETE": "CNA_DISCRETE",
    "COPY_NUMBER_ALTERATION:DISCRETE_LONG": "CNA_DISCRETE_LONG",
    "COPY_NUMBER_ALTERATION:CONTINUOUS": "CNA_CONTINUOUS",
    "COPY_NUMBER_ALTERATION:LOG2-VALUE": "CNA_LOG2",
    "COPY_NUMBER_ALTERATION:SEG": "SEG",
    "MRNA_EXPRESSION:CONTINUOUS": "EXPRESSION",
    "MRNA_EXPRESSION:Z-SCORE": "EXPRESSION",
    "MRNA_EXPRESSION:DISCRETE": "EXPRESSION",
    "MUTATION_EXTENDED:MAF": "MUTATION",
    "MUTATION_UNCALLED:MAF": "MUTATION_UNCALLED",
    "METHYLATION:CONTINUOUS": "METHYLATION",
    "GENE_PANEL_MATRIX:GENE_PANEL_MATRIX": "GENE_PANEL_MATRIX",
    "STRUCTURAL_VARIANT:SV": "STRUCTURAL_VARIANT",
    "GISTIC_GENES_AMP:Q-VALUE": "GISTIC_GENES",
    "GISTIC_GENES_DEL:Q-VALUE": "GISTIC_GENES",
    "MUTSIG:Q-VALUE": "MUTATION_SIGNIFICANCE",
    "GENESET_SCORE:GSVA-SCORE": "GSVA_SCORES",
    "GENESET_SCORE:P-VALUE": "GSVA_PVALUES",
    "GENERIC_ASSAY:LIMIT-VALUE": "GENERIC_ASSAY_CONTINUOUS",
    "GENERIC_ASSAY:BINARY": "GENERIC_ASSAY_BINARY",
    "GENERIC_ASSAY:CATEGORICAL": "GENERIC_ASSAY_CATEGORICAL",
}
"""C:651-689: a meta's type by its ``<genetic_alteration_type>:<datatype>`` (written here from the
C table; ``test_cna_corpus`` holds it equal to the pack's)."""
RESOURCE_TYPES: Mapping[str, str] = {
    "PATIENT": "PATIENT_RESOURCES",
    "SAMPLE": "SAMPLE_RESOURCES",
    "STUDY": "STUDY_RESOURCES",
    "DEFINITION": "RESOURCES_DEFINITION",
}
_PROFILE_TYPE = PROFILE_KEYS
_CLINICAL_TYPE = CLINICAL_KEYS
_ASSAY = (
    "cancer_study_identifier",
    "genetic_alteration_type",
    "generic_assay_type",
    "datatype",
    "stable_id",
    "profile_name",
    "profile_description",
    "data_filename",
    "show_profile_in_analysis_tab",
)
_RESOURCE = ("cancer_study_identifier", "resource_type", "data_filename")
_GSVA = (
    "cancer_study_identifier",
    "genetic_alteration_type",
    "datatype",
    "stable_id",
    "source_stable_id",
    "profile_name",
    "profile_description",
    "data_filename",
)
MANDATORY: Mapping[str, tuple[str, ...]] = {
    "CANCER_TYPE": ("genetic_alteration_type", "datatype", "data_filename"),
    "STUDY": ("cancer_study_identifier", "type_of_cancer", "name", "description"),
    "SAMPLE_ATTRIBUTES": _CLINICAL_TYPE,
    "PATIENT_ATTRIBUTES": _CLINICAL_TYPE,
    "CNA_DISCRETE": _PROFILE_TYPE,
    "CNA_DISCRETE_LONG": _PROFILE_TYPE,
    "CNA_LOG2": _PROFILE_TYPE,
    "CNA_CONTINUOUS": _PROFILE_TYPE,
    "SEG": SEG_KEYS,
    "MUTATION": _PROFILE_TYPE,
    "MUTATION_UNCALLED": (
        "cancer_study_identifier",
        "genetic_alteration_type",
        "datatype",
        "stable_id",
        "profile_name",
        "profile_description",
        "data_filename",
    ),
    "EXPRESSION": _PROFILE_TYPE,
    "METHYLATION": _PROFILE_TYPE,
    "PROTEIN": _PROFILE_TYPE,
    "GISTIC_GENES": (
        "cancer_study_identifier",
        "genetic_alteration_type",
        "datatype",
        "reference_genome_id",
        "data_filename",
    ),
    "TIMELINE": _CLINICAL_TYPE,
    "MUTATION_SIGNIFICANCE": _CLINICAL_TYPE,
    "GENE_PANEL_MATRIX": _CLINICAL_TYPE,
    "GSVA_PVALUES": (*_GSVA, "geneset_def_version"),
    "GSVA_SCORES": (*_GSVA, "show_profile_in_analysis_tab", "geneset_def_version"),
    "GENERIC_ASSAY_CONTINUOUS": _ASSAY,
    "GENERIC_ASSAY_BINARY": _ASSAY,
    "GENERIC_ASSAY_CATEGORICAL": _ASSAY,
    "STRUCTURAL_VARIANT": _PROFILE_TYPE,
    "SAMPLE_RESOURCES": _RESOURCE,
    "PATIENT_RESOURCES": _RESOURCE,
    "STUDY_RESOURCES": _RESOURCE,
    "RESOURCES_DEFINITION": _RESOURCE,
}
"""C:94-371: each type's mandatory keys in order (``test_cna_corpus`` holds it equal to the
pack's)."""
IMPORTED_TYPES = frozenset(
    {"SAMPLE_ATTRIBUTES", "PATIENT_ATTRIBUTES", "MUTATION", "STRUCTURAL_VARIANT", "CNA_DISCRETE"}
)
"""The types whose data files the pack imports (``meta.IMPORTED``)."""


def classify(found: Mapping[str, str]) -> str | None:
    """A meta's type (C:691-729): a pair of ``PAIR_TYPES``; else the study meta; else a cancer
    type meta; else a resource meta; ``None`` for none (``META_FILE``)."""
    if "genetic_alteration_type" in found and "datatype" in found:
        return PAIR_TYPES.get(f"{found['genetic_alteration_type']}:{found['datatype']}")
    if "cancer_study_identifier" in found and "type_of_cancer" in found:
        return "STUDY"
    if "type_of_cancer" in found:
        return "CANCER_TYPE"
    if "cancer_study_identifier" in found and "resource_type" in found:
        return RESOURCE_TYPES.get(found["resource_type"])
    return None


def list_fault(
    content: bytes, study: str, seen: set[str], samples: frozenset[str]
) -> tuple[str | None, tuple[str, ...], str]:
    """A case list as ``caselists`` reads it, line by line: its refusal (an item and its fields)
    or ``None``, and its ``stable_id``."""
    found: dict[str, str] = {}
    for number, line in enumerate(LINES.split(content.decode("latin-1")), start=1):
        try:
            text = line.encode("latin-1").decode("utf-8")
        except UnicodeDecodeError:
            return "list_not_utf8", (), ""
        if number == 1:
            text = text.removeprefix(BOM)
        if CONTROL.search(text):
            return "list_control", (), ""
        if text.strip() == "":
            continue
        key, colon, value = text.partition(":")
        if not colon:
            return "list_colon", (), ""
        found[key] = value.strip()
    for key in LIST_KEYS:
        if key not in found:
            return "list_key", (key,), ""
    stable = found["stable_id"]
    if found["cancer_study_identifier"] != study:
        return "list_study", (), stable
    if not stable.startswith(study + "_"):
        return "list_prefix", (), stable
    if stable in seen:
        return "list_repeat", (), stable
    if "case_list_category" in found and found["case_list_category"] not in CATEGORIES:
        return "list_category", (), stable
    if any(item.strip() not in samples for item in found["case_list_ids"].split("\t")):
        return "list_id", (), stable
    return None, (), stable


def _samples(content: bytes) -> frozenset[str]:
    lines = [line for line in LINES.split(content.decode()) if line and not line.startswith("#")]
    at = lines[0].split("\t").index("SAMPLE_ID")
    return frozenset(line.split("\t")[at] for line in lines[1:])


def one_name(value: str) -> bool:
    if len(value.encode("utf-8", "surrogatepass")) > 255 or value in ("", ".", ".."):
        return False
    return "\0" not in value and "/" not in value


def _path(value: str, listed: Mapping[str, Any], reads: set[str], key: str) -> str | None:
    """Phase 9's rule (D409) on the value of ``key``, any path key alike (review r2 MJ-A 3): P0,
    Kind, Reads; the refusal names the key; ``None`` when the value passes."""
    if not one_name(value):
        return said("p0", key)
    alike = [name for name in listed if fold(name) == fold(value)]
    if any(isinstance(listed[name], tuple) for name in alike):
        return said("symlink", key)
    if any(name in reads for name in alike):
        return said("reads", key)
    return None


def expected(cell: Mapping[str, Any]) -> str:
    """The outcome D446's items give the cell: ``ok``, or ``onco.<CODE>|<reason>``."""
    named = {
        name: content
        for name, content in sorted(cell.items())
        if "/" not in name
        and isinstance(content, bytes)
        and META_NAME.search(name)
        and not name.startswith(".")
        and not name.endswith("~")
        and not name.startswith("ONCOKB_IMPORT_BACKUP")
    }
    for content in named.values():
        fault = meta_fault(content)
        if fault is not None:
            return said(fault)
    metas = {name: parse_meta(content) for name, content in named.items()}
    for found in metas.values():
        kind = classify(found)
        if kind is None:
            return said("meta_type")
        for key in MANDATORY[kind]:
            if key not in found:
                return said("meta_key", key)
    seen: set[str] = set()
    for found in metas.values():
        if "stable_id" in found:
            stripped = found["stable_id"].strip()
            if stripped == "" or any(c not in STABLE_ID_CHARACTERS for c in stripped):
                return said("stable_charset")
            if stripped in seen:
                return said("duplicate_stable_id")
            seen.add(stripped)
    study = metas.get("meta_study.txt", {}).get("cancer_study_identifier")
    for found in metas.values():
        if found.get("cancer_study_identifier", study) != study:
            return said("study_mismatch")
    listed: dict[str, Any] = {
        name.split("/")[0]: "directory" if "/" in name else content
        for name, content in cell.items()
    }
    reads = set(metas)
    for found in metas.values():
        if found.get("genetic_alteration_type") in ("CLINICAL", "MUTATION_EXTENDED") and (
            found.get("datatype") != "TIMELINE"
        ):
            reads.add(found["data_filename"])
    for found in metas.values():
        if found.get("genetic_alteration_type") == "STRUCTURAL_VARIANT":
            reads.add(found["data_filename"])
    cnas = [
        name
        for name, found in metas.items()
        if (found.get("genetic_alteration_type"), found.get("datatype")) == DISCRETE
    ]
    copies = None
    if len(cnas) > 1:
        return said("second_meta")
    if cnas:
        copies = cnas[0]
        value = metas[copies]["data_filename"]
        failed = _path(value, listed, reads, "data_filename")
        if failed is not None:
            return failed
        if not isinstance(listed.get(value), bytes):
            return said("no_file")
        if metas[copies]["stable_id"].strip() not in STABLE_IDS:
            return said("stable_id")
        reads.add(value)
    for found in metas.values():
        pair = (found.get("genetic_alteration_type"), found.get("datatype"))
        if classify(found) not in (*IMPORTED_TYPES, "STUDY") and "data_filename" in found:
            failed = _path(found["data_filename"], listed, reads, "data_filename")
            if failed is not None:
                return failed
        if pair == DISCRETE and "pd_annotations_filename" in found:
            failed = _path(
                found["pd_annotations_filename"], listed, reads, "pd_annotations_filename"
            )
            if failed is not None:
                return failed
    samples = _samples(cell["data_clinical_sample.txt"])
    lists = {
        name: content
        for name, content in sorted(cell.items())
        if name.startswith("case_lists/")
        and isinstance(content, bytes)
        and not name.removeprefix("case_lists/").startswith(".")
        and not name.endswith("~")
    }
    stable_ids: list[str] = []
    study_meta = metas.get("meta_study.txt", {})
    if study_meta.get("add_global_case_list", "").lower() == "true":
        stable_ids.append(f"{study}_all")
    for content in lists.values():
        fault, fields, stable = list_fault(content, study or "", set(stable_ids), samples)
        if fault is not None:
            return said(fault, *fields)
        stable_ids.append(stable)
    if copies is None:
        return "ok"
    if metas["meta_study.txt"]["cancer_study_identifier"] + "_cna" not in stable_ids:
        return said("no_list")
    value = metas[copies]["data_filename"]
    if not _is_text(value):
        return said("name_not_text")
    content = listed[value]
    assert isinstance(content, bytes)
    found_item = read_item(content, samples)
    if found_item is None:
        return "ok"
    item, fields = found_item
    return said(item, *fields)

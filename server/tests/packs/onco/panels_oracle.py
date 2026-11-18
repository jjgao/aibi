"""The gene panel matrix map's predicates (M4.2b-2a, D447): for each item of D447, the condition
over a cell under which the pack must refuse with it, written here from the reference
(cbioportal-core 47890bb: ``GenePanelMatrixValidator``, V:3343-3433, its base ``Validator``,
V:361-720, the dictionary of meta files of V:4740-4778 read with C:826-1000, and the order of
V:5570-5586) and D447, importing nothing of the pack.

This is not a test: pytest does not collect it. It loads ``cna_oracle.py`` by its path (slice 3's
predicates of the shared parsers, a stable helper that imports nothing of the pack either).
``expected(cell)`` is the outcome the items give a whole cell, in the pack's refusal order (D447):
the first item whose predicate holds, ``ok`` when none does; ``row_items`` the same for one row
of the matrix, given the header and the samples before it, ``header_item`` for a header, and
``profiles`` the model of the reference's dictionary and of its header check's crash
(V:3363-3373), which the brute forces call directly. The judge (``test_panels_corpus.py``) holds
the pack's outcome of every cell to it, so that a check narrowed in the reader (and its outcomes
re-recorded) still fails: the predicate, not the recorded outcome, says what must be refused.
"""

import importlib.util
import re
import unicodedata
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any


def _load(name: str) -> ModuleType:
    path = Path(__file__).resolve().parent / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"{name}_for_panels_oracle", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SHARED = _load("cna_oracle")
"""Slice 3's oracle: the shared parsers' predicates (meta files, case lists, phase 9's rule) and
the discrete copy-number matrix's (``read_item``)."""

UNICODE = SHARED.UNICODE
SAMPLE_ID = "SAMPLE_ID"
MAF_COLUMN = "mutations"
NA = "NA"
FIELD_MOST = 131_072
"""V:511, V:540: the csv reader's field limit, in characters."""
SNIFFED = 5
BOM = "\ufeff"
CONTROL = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\x85\u2028\u2029\ufeff]")
"""The 30 control characters, the three line separators and a byte order mark the pack
refuses (D447; plan r3 MJ-B), a mark at the file's very start being the ``bom`` item's."""
LINE_END = re.compile(rb"\r\n|\n|\r")
LINES = re.compile(r"\r\n|\n|\r")
DESCRIPTOR_STRING = 4_096
"""A descriptor string's most characters (an original name, a label)."""
IDENTIFIER_MOST = 64
"""A column id's most characters (SPEC 5.1)."""

CRASH = (
    "a meta file holds a stable_id without the keys cBioPortal's check of the matrix reads "
    "(genetic_alteration_type, and datatype beside MUTATION_EXTENDED), which crashes it"
)
REASONS: Mapping[str, str] = {
    "not_utf8": "NOT_UTF8|the line is not UTF-8",
    "control": (
        "CONTROL_CHARACTER|the field holds a control character, a line separator or a byte "
        "order mark"
    ),
    "no_header": "NO_HEADER|the file has no line of column names",
    "bom": (
        "BYTE_ORDER_MARK|the file starts with a byte order mark, which cBioPortal reads into the "
        "first column's name"
    ),
    "no_tab": "MISSING_COLUMN|the line of column names and the five lines after it hold no tab",
    "crash": "STABLE_ID_KEYS|" + CRASH,
    "too_many_columns": (
        "TOO_MANY_COLUMNS|the file has more columns than the meta files have stable_ids and "
        "SAMPLE_ID"
    ),
    "duplicate_column": "DUPLICATE_COLUMN|the column name repeats an earlier's",
    "first_not_sample": "COLUMN_NAME|the first column is not SAMPLE_ID",
    "unknown_profile": "COLUMN_NAME|the column name is no meta file's stable_id",
    "panel_property": "COLUMN_NAME|the column is a profile's whose meta file holds gene_panel",
    "long_name": "COLUMN_NAME|the column name is longer than a descriptor's string",
    "collision": "COLUMN_NAME|the column name's id is another column's",
    "blank": "BLANK_LINE|the line is blank",
    "comment": "COMMENT_LINE|a data line starts with '#'",
    "repeated_header": "REPEATED_HEADER|the line repeats the column names",
    "row_width": "ROW_WIDTH|the line does not have as many fields as there are column names",
    "long_field": "LONG_FIELD|the field is longer than a cell may be",
    "blank_cell": "REQUIRED_VALUE|the cell is blank",
    "duplicate_sample": "DUPLICATE_SAMPLE|the sample id is an earlier row's",
    "unknown_sample": "UNKNOWN_SAMPLE|the row's sample id is no sample id of the sample file",
    "not_sequenced": (
        "NOT_SEQUENCED|the sample has a mutation panel and is not in the study's _sequenced case "
        "list"
    ),
    "padded": (
        "PADDED_VALUE|the value has whitespace around it, which cBioPortal reads as written and "
        "the published column would keep"
    ),
    "line_ends": "LINE_ENDS|the file's line ends are not all of one kind, or none",
    "second_meta": "DUPLICATE_PROFILE_META|is a second gene panel matrix meta file",
    "no_file": (
        "DATA_FILE|is a gene panel matrix meta file whose data_filename names no regular file in "
        "the study's directory"
    ),
    "name_not_text": (
        "DATA_FILE|has a data_filename that names a gene panel matrix whose name is not Unicode "
        "text"
    ),
    # the other modules' refusals a matrix cell reaches, outside the matrix's map
    "no_sequenced": (
        "NO_SEQUENCED_LIST|is a mutation meta file, and no case list's stable_id is the study id "
        "followed by _sequenced"
    ),
    "maf_barcode": "UNKNOWN_SAMPLE|the barcode is no sample id of the sample file",
    "sv_sample": "UNKNOWN_SAMPLE|the sample id is no sample id of the sample file",
}
"""Each matrix item's refusal, ``<CODE>|<reason>``, as the pack writes it, and the refusals of
other readers a cell of the corpus reaches."""

HEADER_ORDER = (
    "crash",
    "too_many_columns",
    "duplicate_column",
    "first_not_sample",
    "unknown_profile",
    "panel_property",
    "long_name",
    "collision",
)
"""The header items after the gate, in the pack's refusal order (D447)."""
ROW_ORDER = ("blank_cell", "duplicate_sample", "unknown_sample", "not_sequenced", "padded")
"""The row items in the pack's refusal order (D447): the same-outcome items, then the stricter."""


def said(item: str, *fields: str) -> str:
    """An item's outcome: ``onco.<CODE>|<reason>``, fields filled; an item of slice 3's oracle
    where this one has none."""
    if item not in REASONS:
        return SHARED.said(item, *fields)
    found = REASONS[item]
    for field in fields:
        found = found.replace("{}", field, 1)
    return "onco." + found


# --- The reference's dictionary of meta files (V:4740-4778, C:826-1000) ---------------------------

TYPES_AND_IDS = frozenset(
    {
        ("COPY_NUMBER_ALTERATION", "CONTINUOUS", "linear_CNA"),
        ("COPY_NUMBER_ALTERATION", "DISCRETE", "cna"),
        ("COPY_NUMBER_ALTERATION", "DISCRETE", "cna_consensus"),
        ("COPY_NUMBER_ALTERATION", "DISCRETE", "cna_rae"),
        ("COPY_NUMBER_ALTERATION", "DISCRETE", "gistic"),
        ("COPY_NUMBER_ALTERATION", "DISCRETE_LONG", "cna"),
        ("COPY_NUMBER_ALTERATION", "DISCRETE_LONG", "gistic"),
        ("COPY_NUMBER_ALTERATION", "LOG2-VALUE", "log2CNA"),
        ("GENERIC_ASSAY", "BINARY", "*"),
        ("GENERIC_ASSAY", "CATEGORICAL", "*"),
        ("GENERIC_ASSAY", "LIMIT-VALUE", "*"),
        ("GENESET_SCORE", "GSVA-SCORE", "gsva_scores"),
        ("GENESET_SCORE", "P-VALUE", "gsva_pvalues"),
        ("METHYLATION", "CONTINUOUS", "methylation_epic"),
        ("METHYLATION", "CONTINUOUS", "methylation_hm27"),
        ("METHYLATION", "CONTINUOUS", "methylation_hm450"),
        ("METHYLATION", "CONTINUOUS", "methylation_promoters_rrbs"),
        ("MRNA_EXPRESSION", "CONTINUOUS", "mirna"),
        ("MRNA_EXPRESSION", "CONTINUOUS", "mrna"),
        ("MRNA_EXPRESSION", "CONTINUOUS", "mrna_U133"),
        ("MRNA_EXPRESSION", "CONTINUOUS", "mrna_seq_cpm"),
        ("MRNA_EXPRESSION", "CONTINUOUS", "mrna_seq_fpkm"),
        ("MRNA_EXPRESSION", "CONTINUOUS", "mrna_seq_fpkm_capture"),
        ("MRNA_EXPRESSION", "CONTINUOUS", "mrna_seq_fpkm_polya"),
        ("MRNA_EXPRESSION", "CONTINUOUS", "mrna_seq_tpm"),
        ("MRNA_EXPRESSION", "CONTINUOUS", "rna_seq_mrna"),
        ("MRNA_EXPRESSION", "CONTINUOUS", "rna_seq_mrna_capture"),
        ("MRNA_EXPRESSION", "CONTINUOUS", "rna_seq_v2_mrna"),
        ("MRNA_EXPRESSION", "CONTINUOUS", "rna_seq_v2_mrna_median_normals"),
        ("MRNA_EXPRESSION", "DISCRETE", "mrna_outliers"),
        ("MRNA_EXPRESSION", "Z-SCORE", "mirna_median_Zscores"),
        ("MRNA_EXPRESSION", "Z-SCORE", "mrna_U133_Zscores"),
        ("MRNA_EXPRESSION", "Z-SCORE", "mrna_U133_all_sample_Zscores"),
        ("MRNA_EXPRESSION", "Z-SCORE", "mrna_all_sample_Zscores"),
        ("MRNA_EXPRESSION", "Z-SCORE", "mrna_median_Zscores"),
        ("MRNA_EXPRESSION", "Z-SCORE", "mrna_median_all_sample_Zscores"),
        ("MRNA_EXPRESSION", "Z-SCORE", "mrna_merged_median_Zscores"),
        ("MRNA_EXPRESSION", "Z-SCORE", "mrna_seq_cpm_Zscores"),
        ("MRNA_EXPRESSION", "Z-SCORE", "mrna_seq_cpm_all_sample_Zscores"),
        ("MRNA_EXPRESSION", "Z-SCORE", "mrna_seq_fpkm_Zscores"),
        ("MRNA_EXPRESSION", "Z-SCORE", "mrna_seq_fpkm_all_sample_Zscores"),
        ("MRNA_EXPRESSION", "Z-SCORE", "mrna_seq_fpkm_capture_Zscores"),
        ("MRNA_EXPRESSION", "Z-SCORE", "mrna_seq_fpkm_capture_all_sample_Zscores"),
        ("MRNA_EXPRESSION", "Z-SCORE", "mrna_seq_fpkm_polya_Zscores"),
        ("MRNA_EXPRESSION", "Z-SCORE", "mrna_seq_fpkm_polya_all_sample_Zscores"),
        ("MRNA_EXPRESSION", "Z-SCORE", "mrna_seq_read_counts_Zscores"),
        ("MRNA_EXPRESSION", "Z-SCORE", "mrna_seq_tpm_Zscores"),
        ("MRNA_EXPRESSION", "Z-SCORE", "mrna_seq_tpm_all_sample_Zscores"),
        ("MRNA_EXPRESSION", "Z-SCORE", "mrna_zbynorm"),
        ("MRNA_EXPRESSION", "Z-SCORE", "rna_seq_mrna_capture_Zscores"),
        ("MRNA_EXPRESSION", "Z-SCORE", "rna_seq_mrna_capture_all_sample_Zscores"),
        ("MRNA_EXPRESSION", "Z-SCORE", "rna_seq_mrna_median_Zscores"),
        ("MRNA_EXPRESSION", "Z-SCORE", "rna_seq_mrna_median_all_sample_Zscores"),
        ("MRNA_EXPRESSION", "Z-SCORE", "rna_seq_v2_mrna_median_Zscores"),
        ("MRNA_EXPRESSION", "Z-SCORE", "rna_seq_v2_mrna_median_all_sample_Zscores"),
        ("MRNA_EXPRESSION", "Z-SCORE", "rna_seq_v2_mrna_median_all_sample_ref_normal_Zscores"),
        ("MRNA_EXPRESSION", "Z-SCORE", "rna_seq_v2_mrna_median_normals_Zscores"),
        ("MUTATION_EXTENDED", "MAF", "mutations"),
        ("MUTATION_UNCALLED", "MAF", "mutations_uncalled"),
        ("PROTEIN_LEVEL", "CONTINUOUS", "protein_quantification"),
        ("PROTEIN_LEVEL", "LOG2-VALUE", "protein_quantification"),
        ("PROTEIN_LEVEL", "LOG2-VALUE", "rppa"),
        ("PROTEIN_LEVEL", "Z-SCORE", "protein_quantification_zscores"),
        ("PROTEIN_LEVEL", "Z-SCORE", "rppa_Zscores"),
        ("STRUCTURAL_VARIANT", "SV", "structural_variants"),
    }
)
"""C:738-791: the combinations the reference's ``validate_types_and_id`` allows (``*``: any id)."""


def reference_meta(content: bytes) -> dict[str, str] | None:
    """C:852-870, as the reference reads a meta file: lines at ``\\r\\n``, ``\\n``, ``\\r``; a
    line blank by ``str.strip()`` skipped; a line without ``:`` makes it untyped (``None``); else
    the key as written (a leading byte order mark kept in it), the value stripped, the last of a
    repeated key kept."""
    found: dict[str, str] = {}
    for line in LINES.split(content.decode("utf-8", "replace")):
        if line.strip() == "":
            continue
        if ":" not in line:
            return None
        key, value = line.split(":", 1)
        found[key] = value.strip()
    return found


def reference_type(found: Mapping[str, str], study: str | None) -> str | None:
    """C:643-729 and C:878-989: the type the reference gives the meta ``found`` read, or ``None``
    (which leaves it out of every later step)."""
    if "genetic_alteration_type" in found and "datatype" in found:
        kind = SHARED.PAIR_TYPES.get(f"{found['genetic_alteration_type']}:{found['datatype']}")
    elif "cancer_study_identifier" in found and "type_of_cancer" in found:
        kind = "STUDY"
    elif "type_of_cancer" in found:
        kind = "CANCER_TYPE"
    elif "cancer_study_identifier" in found and "resource_type" in found:
        kind = SHARED.RESOURCE_TYPES.get(found["resource_type"])
    else:
        kind = None
    if kind is None:
        return None
    mandatory = SHARED.MANDATORY[kind]
    if any(key not in found for key in mandatory):
        return None
    if "stable_id" in mandatory:
        triple = (found["genetic_alteration_type"], found["datatype"], found["stable_id"])
        if triple not in TYPES_AND_IDS and (*triple[:2], "*") not in TYPES_AND_IDS:
            return None
    if (
        study is not None
        and "cancer_study_identifier" in found
        and found["cancer_study_identifier"] != study
    ):
        return None
    if kind in ("SEG", "GISTIC_GENES") and found["reference_genome_id"] not in ("hg19", "hg38"):
        return None
    if kind in ("MUTATION", "MUTATION_UNCALLED") and found.get("swissprot_identifier") not in (
        None,
        "name",
        "accession",
    ):
        return None
    return kind


@dataclass(frozen=True)
class Profiles:
    keys: frozenset[str]
    panels: frozenset[str]
    crashes: bool


def profiles(metas: Mapping[str, bytes]) -> Profiles:
    """V:4740-4778 then V:3363-3373, as one model: each meta file (in the order of its name) read
    and typed as the reference does, an entry by its ``stable_id`` stripped for each typed one
    holding the key, the last of an id winning; the header check's loop evaluates, for every entry,
    ``entry['genetic_alteration_type'] == 'MUTATION_EXTENDED' and entry['datatype'] == 'MAF' and
    ...``, so it raises where an entry lacks ``genetic_alteration_type`` or holds
    ``MUTATION_EXTENDED`` without ``datatype``."""
    entries: dict[str, dict[str, str]] = {}
    study: str | None = None
    for name in sorted(metas):
        found = reference_meta(metas[name])
        if found is None or reference_type(found, study) is None:
            continue
        if "stable_id" in found:
            entries[found["stable_id"].strip()] = found
        if study is None and "cancer_study_identifier" in found:
            study = found["cancer_study_identifier"]

    def raises(entry: Mapping[str, str]) -> bool:
        try:
            _ = (
                entry["genetic_alteration_type"] == "MUTATION_EXTENDED"
                and entry["datatype"] == "MAF"
                and entry["stable_id"] == ""
            )
        except KeyError:
            return True
        return False

    return Profiles(
        frozenset(entries),
        frozenset(key for key, entry in entries.items() if "gene_panel" in entry),
        any(raises(entry) for entry in entries.values()),
    )


# --- The matrix -----------------------------------------------------------------------------------


def _lines(data: bytes) -> list[bytes]:
    out: list[bytes] = []
    start = 0
    for found in LINE_END.finditer(data):
        out.append(data[start : found.start()])
        start = found.end()
    if start < len(data):
        out.append(data[start:])
    return out


def column_id_base(name: str, position: int) -> str:
    """SPEC 5.1, written here: NFKC, lower case, runs of other characters an ``_``, stripped of
    ``_``, cut at 64 without a trailing ``_``; empty, ``c_<position>``; a leading digit, ``c_``
    before it, cut again."""
    base = re.sub(r"[^a-z0-9]+", "_", unicodedata.normalize("NFKC", name).lower()).strip("_")
    base = base[:IDENTIFIER_MOST].rstrip("_")
    if not base:
        return f"c_{position}"
    if base[0].isdigit():
        return ("c_" + base)[:IDENTIFIER_MOST].rstrip("_")
    return base


def assigned_ids(
    names: Sequence[str], previous: Sequence[tuple[str, str]] | None = None
) -> list[str]:
    """SPEC 5.1 with D238, written here: the k-th occurrence of a name keeps the id of its k-th
    occurrence in ``previous``; every other name takes its base, or, where that is taken (by a
    kept id or an earlier name's), the base cut to leave room for the smallest ``_<n>``, n >= 2,
    that gives a free id."""
    earlier: dict[str, list[str]] = {}
    for name, given in previous or ():
        earlier.setdefault(name, []).append(given)
    seen: dict[str, int] = {}
    kept: list[str | None] = []
    for name in names:
        at = seen.get(name, 0)
        seen[name] = at + 1
        ids = earlier.get(name, [])
        kept.append(ids[at] if at < len(ids) else None)
    taken = {given for given in kept if given is not None}
    out: list[str] = []
    for position, (name, given) in enumerate(zip(names, kept, strict=True), start=1):
        if given is not None:
            out.append(given)
            continue
        base = column_id_base(name, position)
        candidate, n = base, 2
        while candidate in taken:
            suffix = f"_{n}"
            candidate = base[: IDENTIFIER_MOST - len(suffix)].rstrip("_") + suffix
            n += 1
        taken.add(candidate)
        out.append(candidate)
    return out


def collides(names: Sequence[str], previous: Sequence[tuple[str, str]] | None = None) -> bool:
    """Plan r4 m8, review r1 m2: a name's column id would be another than its own (its base, or
    on a re-import the id the previous release gave it), ``SAMPLE_ID``'s included."""
    earlier: dict[str, list[str]] = {}
    for name, given in previous or ():
        earlier.setdefault(name, []).append(given)
    seen: dict[str, int] = {}
    ids = assigned_ids(names, previous)
    for position, (name, given) in enumerate(zip(names, ids, strict=True), start=1):
        at = seen.get(name, 0)
        seen[name] = at + 1
        own = earlier.get(name, [])
        if given != column_id_base(name, position) and given not in own[at : at + 1]:
            return True
    return False


def gated(lines: Sequence[str], at: int) -> bool:
    """V:485-496: the header (line ``at``) and the five lines after it hold no tab, so the
    reference's sniffer checks nothing."""
    return not any("\t" in line for line in lines[at : at + 1 + SNIFFED])


def reference_checks_nothing(data: bytes) -> bool:
    """The reference's view (the mark is part of the first line): slice 3's predicate."""
    return bool(SHARED.reference_checks_nothing(data))


def header_item(names: Sequence[str], found: Profiles, maf: bool) -> str | None:
    """The header's first item after the gate, in ``HEADER_ORDER``, or ``None``."""
    holds = dict.fromkeys(HEADER_ORDER, False)
    holds["crash"] = found.crashes
    holds["too_many_columns"] = len(names) > len(found.keys) + 1
    holds["duplicate_column"] = len(set(names)) < len(names)
    holds["first_not_sample"] = names[0] != SAMPLE_ID
    holds["unknown_profile"] = any(name not in found.keys for name in names[1:])
    own = MAF_COLUMN if maf else None
    holds["panel_property"] = any(name != own and name in found.panels for name in names[1:])
    holds["long_name"] = any(len(name) > DESCRIPTOR_STRING for name in names)
    holds["collision"] = collides(names)
    for item in HEADER_ORDER:
        if holds[item]:
            return item
    return None


def row_items(
    names: Sequence[str],
    fields: Sequence[str],
    samples: Collection[str],
    seen: Collection[str],
    sequenced: Collection[str] | None,
) -> list[str]:
    """Every row item whose predicate holds on the row ``fields`` under the header ``names``,
    given the sample ids of the rows before it (``seen``), the sample file's ``samples`` and, for
    a study with a mutation meta, its ``_sequenced`` list's ids (``sequenced``), in
    ``ROW_ORDER``."""
    holds = dict.fromkeys(ROW_ORDER, False)
    holds["blank_cell"] = any(field.strip() == "" for field in fields)
    holds["duplicate_sample"] = fields[0] in seen
    holds["unknown_sample"] = fields[0] not in samples
    if sequenced is not None and MAF_COLUMN in names:
        cell = fields[list(names).index(MAF_COLUMN)]
        holds["not_sequenced"] = cell != NA and fields[0] not in sequenced
    holds["padded"] = any(field != field.strip() for field in fields[1:])
    return [item for item in ROW_ORDER if holds[item]]


def read_item(
    data: bytes,
    samples: Collection[str],
    found: Profiles,
    sequenced: Collection[str] | None,
) -> str | None:
    """The matrix's first item, by class: a line not UTF-8, a control character or separator, the
    structure (the header, then every data line), the first row's first item, the line ends;
    ``None`` when none holds."""
    decoded: list[str] = []
    for line in _lines(data):
        try:
            decoded.append(line.decode("utf-8"))
        except UnicodeDecodeError:
            return "not_utf8"
    if any(
        CONTROL.search(line[1:] if at == 0 and line.startswith(BOM) else line)
        for at, line in enumerate(decoded)
    ):
        return "control"
    header = None
    for number, line in enumerate(decoded):
        if number == 0 and line.startswith(BOM):
            return "bom"
        if line.startswith("#"):
            continue
        header = number
        break
    if header is None:
        return "no_header"
    names = decoded[header].split("\t")
    if len(names) == 1 and gated(decoded, header):
        return "no_tab"
    item = header_item(names, found, sequenced is not None)
    if item is not None:
        return item
    rows: list[list[str]] = []
    for line in decoded[header + 1 :]:
        fields = line.split("\t")
        if all(field.strip() == "" for field in fields):
            return "blank"
        if line.startswith("#"):
            return "comment"
        if fields[: len(names)] == names:
            return "repeated_header"
        if len(fields) != len(names):
            return "row_width"
        if any(len(field) > FIELD_MOST for field in fields):
            return "long_field"
        rows.append(fields)
    seen: list[str] = []
    for fields in rows:
        items = row_items(names, fields, samples, seen, sequenced)
        if items:
            return items[0]
        seen.append(fields[0])
    if SHARED.line_ends(data):
        return "line_ends"
    return None


# --- The study ------------------------------------------------------------------------------------


def _metas(cell: Mapping[str, Any]) -> dict[str, bytes]:
    return {
        name: content
        for name, content in sorted(cell.items())
        if "/" not in name
        and isinstance(content, bytes)
        and SHARED.META_NAME.search(name)
        and not name.startswith(".")
        and not name.endswith("~")
        and not name.startswith("ONCOKB_IMPORT_BACKUP")
    }


def _list_ids(content: bytes) -> list[str]:
    parsed = SHARED.parse_meta(content)
    return [item.strip() for item in parsed.get("case_list_ids", "").split("\t")]


def _pair(found: Mapping[str, str]) -> tuple[str | None, str | None]:
    return found.get("genetic_alteration_type"), found.get("datatype")


def expected(cell: Mapping[str, Any]) -> str:
    """The outcome D447's items, and the shared parsers' predicates before them, give the cell:
    ``ok``, or ``onco.<CODE>|<reason>``."""
    named = _metas(cell)
    metas: dict[str, dict[str, str]] = {}
    for name, content in named.items():
        fault = SHARED.meta_fault(content)
        if fault is not None:
            return said(fault)
        found = SHARED.parse_meta(content)
        kind = SHARED.classify(found)
        if kind is None:
            return said("meta_type")
        for key in SHARED.MANDATORY[kind]:
            if key not in found:
                return said("meta_key", key)
        metas[name] = found
    seen: set[str] = set()
    for found in metas.values():
        if "stable_id" in found:
            stripped = found["stable_id"].strip()
            if stripped == "" or any(c not in SHARED.STABLE_ID_CHARACTERS for c in stripped):
                return said("stable_charset")
            if stripped in seen:
                return said("duplicate_stable_id")
            seen.add(stripped)
    study = metas["meta_study.txt"]["cancer_study_identifier"]
    for found in metas.values():
        if found.get("cancer_study_identifier", study) != study:
            return said("study_mismatch")
    kinds = {name: SHARED.classify(found) for name, found in metas.items()}
    listed: dict[str, Any] = {
        name.split("/")[0]: "directory" if "/" in name else content
        for name, content in cell.items()
    }
    reads = set(metas)
    for name, kind in kinds.items():
        if kind in ("SAMPLE_ATTRIBUTES", "PATIENT_ATTRIBUTES", "MUTATION"):
            reads.add(metas[name]["data_filename"])
    imported_steps = (
        ("STRUCTURAL_VARIANT", None),
        ("CNA_DISCRETE", None),
        ("GENE_PANEL_MATRIX", "matrix"),
    )
    matrix: str | None = None
    for kind, role in imported_steps:
        names = [name for name, found in kinds.items() if found == kind]
        if not names:
            continue
        if len(names) > 1 and role == "matrix":
            return said("second_meta")
        value = metas[names[0]]["data_filename"]
        failed = SHARED._path(value, listed, reads, "data_filename")
        if failed is not None:
            return failed
        if not isinstance(listed.get(value), bytes):
            if role == "matrix":
                return said("no_file")
            raise AssertionError(f"the corpus holds no faulty {kind} data value")
        reads.add(value)
        if role == "matrix":
            matrix = value
    imported = {"SAMPLE_ATTRIBUTES", "PATIENT_ATTRIBUTES", "MUTATION", "STRUCTURAL_VARIANT"}
    imported |= {"CNA_DISCRETE", "GENE_PANEL_MATRIX", "STUDY"}
    for name, kind in kinds.items():
        if kind not in imported and "data_filename" in metas[name]:
            failed = SHARED._path(metas[name]["data_filename"], listed, reads, "data_filename")
            if failed is not None:
                return failed
    samples = SHARED._samples(cell["data_clinical_sample.txt"])
    lists = {
        name: content
        for name, content in sorted(cell.items())
        if name.startswith("case_lists/")
        and isinstance(content, bytes)
        and not name.removeprefix("case_lists/").startswith(".")
        and not name.endswith("~")
    }
    stable_ids: list[str] = []
    if metas["meta_study.txt"].get("add_global_case_list", "").lower() == "true":
        stable_ids.append(f"{study}_all")
    ids_of: dict[str, list[str]] = {}
    for content in lists.values():
        fault, fields, stable = SHARED.list_fault(content, study, set(stable_ids), samples)
        if fault is not None:
            return said(fault, *fields)
        stable_ids.append(stable)
        ids_of[stable] = _list_ids(content)
    mutation = any(kind == "MUTATION" for kind in kinds.values())
    if mutation and f"{study}_sequenced" not in stable_ids:
        return said("no_sequenced")
    if matrix is not None:
        if not SHARED._is_text(matrix):
            return said("name_not_text")
        content = listed[matrix]
        sequenced = ids_of.get(f"{study}_sequenced") if mutation else None
        item = read_item(content, samples, profiles(named), sequenced)
        if item is not None:
            return said(item)
    for name, kind in kinds.items():
        if kind == "MUTATION":
            maf = cell[metas[name]["data_filename"]]
            assert isinstance(maf, bytes)
            rows = [line.split("\t") for line in LINES.split(maf.decode())[1:] if line]
            header = rows[0]
            barcode = header.index("Tumor_Sample_Barcode")
            if any(row[barcode] not in samples for row in rows[1:]):
                return said("maf_barcode")
    for name, kind in kinds.items():
        if kind == "STRUCTURAL_VARIANT":
            sv = cell[metas[name]["data_filename"]]
            assert isinstance(sv, bytes)
            rows = [line.split("\t") for line in LINES.split(sv.decode()) if line]
            if any(row[0] not in samples for row in rows[1:]):
                return said("sv_sample")
    for name, kind in kinds.items():
        if kind == "CNA_DISCRETE":
            if f"{study}_cna" not in stable_ids:
                return said("no_list")
            content = cell[metas[name]["data_filename"]]
            assert isinstance(content, bytes)
            found_item = SHARED.read_item(content, samples)
            if found_item is not None:
                return SHARED.said(found_item[0], *found_item[1])
    return "ok"


__all__ = [
    "BOM",
    "CRASH",
    "HEADER_ORDER",
    "REASONS",
    "ROW_ORDER",
    "UNICODE",
    "Profiles",
    "collides",
    "column_id_base",
    "expected",
    "header_item",
    "profiles",
    "read_item",
    "reference_checks_nothing",
    "reference_meta",
    "reference_type",
    "row_items",
    "said",
]

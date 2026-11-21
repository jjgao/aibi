"""The MAF and case-list corpus against the reference's recorded outcomes (M4.2b-1b, D444).

Every cell of ``mutation_corpus.py`` (whole studies made by generators: the MAF's bytes, its
header, a token of each column class on each class of row, the NCBI build under each genome, the
normal samples, the mutation meta, the case lists) is imported through ``PACK.importer`` and the
core's checks of a pack's result (``run_importer``), and its outcome is held three ways:

- **per cell, to its recorded outcome**: aibi's refusal code, or that it accepts, is exactly what
  ``mutation_outcomes.json`` records (``test_aibi_s_outcome_is_the_recorded_one``), the onco code
  asserted, so that a check removed from the pack is caught though the gate would refuse the
  study anyway (review r2 m5);
- **per cell, to the reference** (the leniency map, D409's scheme): ``mutation_reference.json`` is
  each cell's recorded outcome on cbioportal-core 47890bb, offline, not relaxed (whether it
  crashed, and the identity of each error it reported; no message of the reference). A refused
  cell's kind is D409's one definition: ``stricter`` where the reference reports no error and does
  not crash, ``same outcome`` where it errs or crashes; ``(code, kind)`` must be an item of D444
  (``ITEMS``), every item named by a cell. An accepted cell's errors must each be an exit of
  ``EXITS``, every exit named by a cell. Neither table is a list of cells: each is keyed by what
  the cell's outcome is, and the cells come from generators;
- **through the store**, for the accepted cells of the ``byte``, ``header``, ``meta``, ``list`` and
  ``normal`` generators and one of each token of the ``row`` generator: published, so that the gate
  accepts the relationships and tables (``test_an_accepted_cell_publishes``).

The recorded outcomes are data: the runs and their logs stay outside the repository. To record a
new cell's outcome on the reference, run the scratch harness the D444 row names; to record aibi's,
run this module with ``AIBI_RECORD_MUTATION_OUTCOMES=1`` and review the difference.
"""

import ast
import importlib.util
import itertools
import json
import os
import re
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from functools import cache
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from aibi.core.importers.checks import run_importer
from aibi.core.importers.confine import Confinement
from aibi.core.importers.errors import ImportRefused
from aibi.core.schema.limits import ImportLimits
from aibi.core.schema.pack_api import ImportOptions, PackRegistry

HERE = Path(__file__).resolve().parent
SPEC = HERE.parents[3] / "SPEC.md"
REFERENCE: Mapping[str, Any] = json.loads((HERE / "mutation_reference.json").read_text())
OUTCOMES_FILE = HERE / "mutation_outcomes.json"
OUTCOMES: Mapping[str, str] = (
    json.loads(OUTCOMES_FILE.read_text()) if OUTCOMES_FILE.exists() else {}
)
AT = "2026-01-01T00:00:00Z"

SAME = "same outcome"
STRICTER = "stricter"
CRASH = "crash"
SAME_AS: Mapping[str, frozenset[str]] = {
    "NOT_UTF8": frozenset({"not-utf8", CRASH}),
    "CONTROL_CHARACTER": frozenset(),
    "NO_HEADER": frozenset({"no-header"}),
    "COLUMN_NAME": frozenset({"missing-column"}),
    "DUPLICATE_COLUMN": frozenset({"duplicate-column"}),
    "MISSING_COLUMN": frozenset(
        {"missing-column", "missing-amino-column", "driver-pair", "namespace", "ascn"}
    ),
    "BLANK_LINE": frozenset({"blank-line"}),
    "COMMENT_LINE": frozenset({"comment-line"}),
    "REPEATED_HEADER": frozenset({"repeated-header", CRASH}),
    "ROW_WIDTH": frozenset({"row-width", CRASH}),
    "LONG_FIELD": frozenset({CRASH}),
    "UNKNOWN_SAMPLE": frozenset({"sample-undefined"}),
    "DRIVER_VALUE": frozenset({"driver-value", "driver-length", "driver-tier-missing"}),
    "REQUIRED_VALUE": frozenset({"blank-required"}),
    "ENTREZ_VALUE": frozenset({"entrez-nonpositive"}),
    "NO_GENE": frozenset({"no-gene"}),
    "COUNT_VALUE": frozenset({"count"}),
    "POSITION_VALUE": frozenset({"position", CRASH}),
    "AMINO_ACID_CHANGE": frozenset({"amino-acid", "amino-acid-length"}),
    "NCBI_BUILD": frozenset({"ncbi-build"}),
    "NORMAL_SAMPLE": frozenset({"normal-sample"}),
    "PADDED_VALUE": frozenset(),
    "MISSING_SPELLING": frozenset(),
    "META_VALUE": frozenset({"type-meta"}),
    "META_FILE": frozenset({"meta-type"}),
    "DUPLICATE_STABLE_ID": frozenset({"duplicate-stable-id"}),
    "DUPLICATE_PROFILE_META": frozenset({"type-meta", "duplicate-stable-id"}),
    "DATA_FILE": frozenset({"open-fail", "missing-column", "sv-content"}),
    "CASE_LISTS": frozenset(),
    "CASE_LIST": frozenset(
        {
            "list-key",
            "list-prefix",
            "list-duplicate",
            "list-category",
            "list-colon",
            "list-space",
            "study-mismatch",
            "type-meta",
            CRASH,
        }
    ),
    "NO_SEQUENCED_LIST": frozenset({"no-sequenced-list"}),
    "TOO_MANY_COLUMNS": frozenset(),
}
"""Each code's class among the reference's error identities (``crash`` for a crash): a refusal is
``same outcome`` only when the cell's recorded errors hold one of its code's, never merely some
error (review r1 MJ2), and ``stricter`` otherwise. A code mapped to nothing is always stricter:
the reference reads a control character, a padded spelling of no value, a symlinked
``case_lists`` as content."""
_ITEMS = (
    ("NOT_UTF8", SAME),
    ("CONTROL_CHARACTER", STRICTER),
    ("NO_HEADER", SAME),
    ("COLUMN_NAME", SAME),
    ("COLUMN_NAME", STRICTER),
    ("DUPLICATE_COLUMN", SAME),
    ("MISSING_COLUMN", SAME),
    ("MISSING_COLUMN", STRICTER),
    ("BLANK_LINE", SAME),
    ("COMMENT_LINE", SAME),
    ("COMMENT_LINE", STRICTER),
    ("REPEATED_HEADER", SAME),
    ("ROW_WIDTH", SAME),
    ("LONG_FIELD", SAME),
    ("UNKNOWN_SAMPLE", SAME),
    ("UNKNOWN_SAMPLE", STRICTER),
    ("DRIVER_VALUE", SAME),
    ("REQUIRED_VALUE", SAME),
    ("ENTREZ_VALUE", SAME),
    ("ENTREZ_VALUE", STRICTER),
    ("NO_GENE", SAME),
    ("COUNT_VALUE", SAME),
    ("POSITION_VALUE", SAME),
    ("AMINO_ACID_CHANGE", SAME),
    ("NCBI_BUILD", SAME),
    ("NORMAL_SAMPLE", SAME),
    ("PADDED_VALUE", STRICTER),
    ("META_VALUE", SAME),
    ("META_FILE", SAME),
    ("DUPLICATE_STABLE_ID", SAME),
    ("DUPLICATE_PROFILE_META", SAME),
    ("DATA_FILE", SAME),
    ("DATA_FILE", STRICTER),
    ("CASE_LISTS", STRICTER),
    ("CASE_LIST", SAME),
    ("CASE_LIST", STRICTER),
    ("NO_SEQUENCED_LIST", SAME),
    ("TOO_MANY_COLUMNS", STRICTER),
    ("MISSING_SPELLING", STRICTER),
)
ITEMS: Mapping[tuple[str, str], str] = {
    (f"onco.{code}", kind): f"(M{number}) {kind}: [{code}]"
    for number, (code, kind) in enumerate(_ITEMS, start=1)
}
"""Each refusal's code and kind to the head of its D444 item, numbered ``M1`` on: the items a
corpus cell names. The stricter bounds no corpus cell reaches at the default limits (more columns,
cells, case-list bytes or entries than the pack reads) are D444's too, held by
``test_mutation.py``."""

BOM_BYTES = b"\xef\xbb\xbf"


def _imported(cell: Mapping[str, Any]) -> list[bytes]:
    """The cell's MAF and case lists, as written (a symlink's target is not followed)."""
    return [
        content
        for name, content in cell.items()
        if isinstance(content, bytes)
        and (name == "data_mutations.txt" or name.startswith("case_lists/"))
    ]


def _bom(cell: Mapping[str, Any], file: str) -> bool:
    """E1: the file the error names starts with a byte order mark, or, for an error that names
    no file (a list it cascades to), some MAF or case list of the cell does."""
    content = cell.get(file)
    if isinstance(content, bytes):
        return content.startswith(BOM_BYTES)
    return file == "-" and any(found.startswith(BOM_BYTES) for found in _imported(cell))


def _duplicate(cell: Mapping[str, Any], file: str) -> bool:
    """E2: the named MAF has two rows the reference would key alike (V:1773-1790)."""
    content = cell.get(file)
    if not isinstance(content, bytes):
        return False
    lines = [line for line in content.decode().splitlines() if not line.startswith("#")]
    names = lines[0].split("\t")
    keys = (
        "Entrez_Gene_Id",
        "Chromosome",
        "Start_Position",
        "End_Position",
        "Variant_Classification",
        "Tumor_Seq_Allele2",
        "HGVSp_Short",
        "Tumor_Sample_Barcode",
    )
    if not all(key in names for key in keys):
        return False
    found = [tuple(row.split("\t")[names.index(key)].strip() for key in keys) for row in lines[1:]]
    return len(set(found)) < len(found)


def _mixed(cell: Mapping[str, Any], file: str) -> bool:
    """E3: the named file's line ends are of more than one kind."""
    content = cell.get(file)
    if not isinstance(content, bytes):
        return False
    crlf = content.count(b"\r\n")
    kinds = [crlf > 0, content.count(b"\n") > crlf, content.count(b"\r") > crlf]
    return sum(kinds) > 1


def _no_line_end(cell: Mapping[str, Any], file: str) -> bool:
    """E4: the named file holds no line end at all (one line, its header)."""
    content = cell.get(file)
    return isinstance(content, bytes) and b"\n" not in content and b"\r" not in content


def _uncalled(cell: Mapping[str, Any], file: str) -> bool:
    """E5: the named file is the data file of a ``MUTATION_UNCALLED`` meta of the cell."""
    named = f"data_filename: {file}\n".encode()
    return any(
        isinstance(content, bytes)
        and b"genetic_alteration_type: MUTATION_UNCALLED" in content
        and named in content
        for content in cell.values()
    )


def _no_all_list(cell: Mapping[str, Any], file: str) -> bool:
    """E6, D409's (14): the error names no file and the study meta's ``add_global_case_list``,
    lower-cased, is not ``true``. It does not look at the case lists: the reference reports the
    error only when no list holds ``<study>_all`` either (V:5028), which the cell's recorded error
    already says."""
    study = cell.get("meta_study.txt", b"")
    assert isinstance(study, bytes)
    lines = [line.partition(":") for line in study.decode().splitlines()]
    flag = {key: value.strip() for key, _, value in lines}.get("add_global_case_list", "")
    return file == "-" and flag.lower() != "true"


Exit = tuple[str, Callable[[Mapping[str, Any], str], bool]]
BOM_EXIT: Exit = ("(E1) exit 1: [byte order mark]", _bom)
EXITS: Mapping[str, tuple[Exit, ...]] = {
    "missing-column": (BOM_EXIT,),
    "missing-amino-column": (BOM_EXIT,),
    "header-cascade": (BOM_EXIT,),
    "list-key": (BOM_EXIT,),
    "no-sequenced-list": (BOM_EXIT,),
    "duplicate-key": (("(E2) exit 1: [duplicate key]", _duplicate),),
    "line-ends": (
        ("(E3) exit 1: [mixed line ends]", _mixed),
        ("(E4) exit 1: [no line end]", _no_line_end),
    ),
    "count": (("(E5) exit 3: [uncalled mutations]", _uncalled),),
    "no-all-list": (("(E6) exit 1: [no all list]", _no_all_list),),
}
"""Each error identity the reference reports on a cell aibi accepts, to the exits that may
explain it, each with the predicate over the cell and the file the error names that must hold for
it (review r1 MJ3: never the identity alone): a byte order mark on that file, and every error it
causes there or cascades to (D409's (13)); a repeated mutation key in that MAF, every row kept;
mixed line ends in that file, or none at all, read without loss; that file being an uncalled
meta's MAF, a kind never imported (D409's (20)); no ``_all`` list (D409's (14)), when
the study meta does not define it.

E2's and E6's predicates never evaluate ``False`` over the corpus (review r2): each restates the
condition under which the reference reports its error (two rows keyed alike; no ``_all`` list),
and aibi has no check behind either, accepting the cell by design (every row kept; the absent
list read without loss). They are kept so that the identity alone never explains an error: an
identity such as ``duplicate-key`` reported for another reason would find no exit."""


@cache
def corpus() -> ModuleType:
    spec = importlib.util.spec_from_file_location("mutation_corpus", HERE / "mutation_corpus.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def build(root: Path, files: Mapping[str, Any]) -> Path:
    """The cell's study, written below ``root / "imports" / "study"``."""
    directory = root / "imports" / "study"
    directory.mkdir(parents=True)
    for name, content in files.items():
        path = directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if content == corpus().DIRECTORY:
            path.mkdir(exist_ok=True)
        elif isinstance(content, tuple):
            os.symlink(content[1], path)
        else:
            path.write_bytes(content)
    return directory


def outcome(onco: ModuleType, directory: Path) -> str:
    """``ok``; or the pack's one refusal, ``onco.<CODE>|<reason>``, its code and the pack's reason
    (``mutation_checks.reason_of``), which names the check that refused; or the core's codes."""
    confinement = Confinement.of(directory.parent)
    options = ImportOptions(dataset="study", reader=confinement, limits=ImportLimits(), at=AT)
    try:
        run_importer(onco.PACK.importer, "onco", confinement.confine(directory), options)
    except ImportRefused as error:
        refusals = error.refusals
        if len(refusals) == 1 and str(refusals[0].code).startswith("onco."):
            return f"{refusals[0].code}|{checks().reason_of(refusals[0].message)}"
        return "+".join(sorted({str(refusal.code) for refusal in refusals}))
    return "ok"


@cache
def checks() -> ModuleType:
    spec = importlib.util.spec_from_file_location("mutation_checks", HERE / "mutation_checks.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CELL_IDS = sorted(corpus().CELLS)
READER_CHECKS: tuple[Any, ...] = tuple(checks().every_check(checks().READERS))
OTHER_CHECKS: tuple[Any, ...] = tuple(checks().every_check(checks().OTHERS))
CHECKS = READER_CHECKS
READER_TEMPLATES: frozenset[str] = frozenset(
    template for module in checks().READERS for template in checks().templates(module)
)
OTHER_TEMPLATES: frozenset[str] = frozenset(
    template for module in checks().OTHERS for template in checks().templates(module)
)

INVENTORY: frozenset[tuple[str, str, str]] = frozenset(
    {
        ("onco.AMINO_ACID_CHANGE", "the amino-acid change holds ','", "same outcome"),
        ("onco.AMINO_ACID_CHANGE", "the amino-acid change holds ';'", "same outcome"),
        ("onco.AMINO_ACID_CHANGE", "the amino-acid change holds a second 'p.'", "same outcome"),
        (
            "onco.AMINO_ACID_CHANGE",
            "the amino-acid change is longer than 255 characters",
            "same outcome",
        ),
        ("onco.BLANK_LINE", "the line is blank", "same outcome"),
        (
            "onco.CASE_LIST",
            "holds a case_list_category that cBioPortal does not define",
            "same outcome",
        ),
        ("onco.CASE_LIST", "holds a case_list_description longer than a text may be", "stricter"),
        ("onco.CASE_LIST", "holds a case_list_description that is not Unicode text", "stricter"),
        ("onco.CASE_LIST", "holds a case_list_name longer than a label may be", "stricter"),
        ("onco.CASE_LIST", "holds a case_list_name that is not Unicode text", "stricter"),
        (
            "onco.CASE_LIST",
            (
                "holds a genetic_alteration_type and datatype that make no combination "
                "cBioPortal accepts with its stable_id"
            ),
            "same outcome",
        ),
        ("onco.CASE_LIST", "holds a stable_id longer than 200 characters", "stricter"),
        (
            "onco.CASE_LIST",
            "holds a stable_id that does not start with the study id and '_'",
            "same outcome",
        ),
        ("onco.CASE_LIST", "holds a stable_id that is not Unicode text", "stricter"),
        ("onco.CASE_LIST", "holds more entries than the pack reads as case lists", "stricter"),
        ("onco.CASE_LIST", "is a directory", "same outcome"),
        ("onco.CASE_LIST", "is a symlink, which the pack never follows", "stricter"),
        ("onco.CASE_LIST", "is larger than a case list may be", "stricter"),
        ("onco.CASE_LIST", "lacks the key {}", "same outcome"),
        ("onco.CASE_LIST", "names another study than the study meta", "same outcome"),
        ("onco.CASE_LIST", "repeats another case list's stable_id", "same outcome"),
        ("onco.CASE_LIST", "the line has no ':'", "same outcome"),
        (
            "onco.COLUMN_NAME",
            "the column name is empty or longer than a descriptor's string",
            "stricter",
        ),
        ("onco.COLUMN_NAME", "the column name is not Unicode text", "stricter"),
        ("onco.COLUMN_NAME", "the line of column names is empty", "same outcome"),
        ("onco.COMMENT_LINE", "a data line starts with '#'", "same outcome"),
        ("onco.COMMENT_LINE", "a data line starts with '#' after spaces or tabs", "stricter"),
        ("onco.CONTROL_CHARACTER", "the field holds a control character", "stricter"),
        ("onco.CONTROL_CHARACTER", "the line holds a control character", "stricter"),
        ("onco.COUNT_VALUE", "the count is not an integer", "same outcome"),
        ("onco.DRIVER_VALUE", "a tier annotation has no tier", "same outcome"),
        ("onco.DRIVER_VALUE", "the annotation is longer than 80 characters", "same outcome"),
        ("onco.DRIVER_VALUE", "the driver tier is longer than 50 characters", "same outcome"),
        ("onco.DRIVER_VALUE", "the driver value is not one cBioPortal takes", "same outcome"),
        ("onco.DUPLICATE_COLUMN", "the column name repeats an earlier's", "same outcome"),
        ("onco.ENTREZ_VALUE", "the Entrez gene id is a number but no integer", "stricter"),
        ("onco.ENTREZ_VALUE", "the Entrez gene id is not positive", "same outcome"),
        ("onco.LONG_FIELD", "the field is longer than a cell may be", "same outcome"),
        ("onco.MISSING_COLUMN", "the file has neither a {} nor an {} column", "same outcome"),
        (
            "onco.MISSING_COLUMN",
            "the file has no column of a namespace its meta file declares",
            "same outcome",
        ),
        ("onco.MISSING_COLUMN", "the file has no {} column", "same outcome"),
        ("onco.MISSING_COLUMN", "the file has one of {} and {} without the other", "same outcome"),
        (
            "onco.MISSING_COLUMN",
            "the file lacks a column the ascn namespace requires",
            "same outcome",
        ),
        (
            "onco.MISSING_COLUMN",
            "the line of column names and the five lines after it hold no tab",
            "stricter",
        ),
        ("onco.NCBI_BUILD", "the NCBI build is not the study's genome's", "same outcome"),
        ("onco.NORMAL_SAMPLE", "the normal barcode is not in the meta's list", "same outcome"),
        ("onco.NOT_UTF8", "the case list's line is not UTF-8", "same outcome"),
        ("onco.NOT_UTF8", "the line is not UTF-8", "same outcome"),
        ("onco.NO_GENE", "the row names neither a gene symbol nor an id", "same outcome"),
        ("onco.NO_HEADER", "the file has no line of column names", "same outcome"),
        (
            "onco.PADDED_VALUE",
            (
                "the Entrez gene id is 0 with spaces around it, which cBioPortal reads as no "
                "value and the published column would read as the value 0"
            ),
            "stricter",
        ),
        (
            "onco.PADDED_VALUE",
            (
                "the gene symbol is a spelling of no value with spaces around it, which "
                "cBioPortal reads as no value and the published column would read as a value"
            ),
            "stricter",
        ),
        (
            "onco.PADDED_VALUE",
            (
                "the gene symbol starts or ends with whitespace, a control or format character, "
                "or a code point drawn as nothing, which cBioPortal accepts and the published "
                "column would keep as written"
            ),
            "stricter",
        ),
        ("onco.POSITION_VALUE", "the position is not a number", "same outcome"),
        ("onco.POSITION_VALUE", "the position is not an integer", "same outcome"),
        ("onco.REPEATED_HEADER", "the line repeats the column names", "same outcome"),
        ("onco.REQUIRED_VALUE", "the required value is blank", "same outcome"),
        (
            "onco.MISSING_SPELLING",
            (
                "the value looks blank, or reads as a spelling of no value once case, width, "
                "spaces and invisible code points are set aside, but is not one of the column's "
                "own spellings of no value, which the published column would keep as a value"
            ),
            "stricter",
        ),
        (
            "onco.ROW_WIDTH",
            "the line does not have as many fields as there are column names",
            "same outcome",
        ),
        ("onco.TOO_MANY_COLUMNS", "the file has more columns than a table may", "stricter"),
        (
            "onco.UNKNOWN_SAMPLE",
            "lists an id that is no sample id of the sample file",
            "same outcome",
        ),
        ("onco.UNKNOWN_SAMPLE", "the barcode is no sample id of the sample file", "same outcome"),
        (
            "onco.UNKNOWN_SAMPLE",
            "the barcode is no sample id of the sample file, on a row cBioPortal skips",
            "stricter",
        ),
    }
)
"""The inventory of the two readers' checks (review r3 MJ-1): every check, by its code, its
reason as written (``{}`` for a field) and its kind, written here by hand. The walker's templates
(``mutation_checks``), this set and ``HELD``, the triples the corpus names, and D444's list are
held equal, so that a check deleted, added or changed in kind changes this set and D444 too: a
visible edit, never a silent re-recording."""
HELD: Mapping[str, tuple[str, str, str]] = {
    "cannot be confined": ("onco.CASE_LIST", "stricter", "test_a_case_list_entry_of_no_file_kind"),
    "is not a regular file": (
        "onco.CASE_LIST",
        "stricter",
        "test_a_case_list_entry_of_no_file_kind",
    ),
    "takes the study's files past the cells an import may": (
        "onco.TOO_MANY_CELLS",
        "stricter",
        "test_the_import_counts_every_table_s_cells",
    ),
    "the study's files have more cells than an import may": (
        "onco.TOO_MANY_CELLS",
        "stricter",
        "test_the_cell_bound_counts_the_import_s_cells",
    ),
}
"""The readers' checks no corpus cell reaches, by template: their code, their kind and the test
of ``test_mutation.py`` that holds each (the cell bound a small study cannot pass; a case-list
entry that is a FIFO or cannot be confined, which the reference's run would block or fail on)."""
OTHER_INVENTORY: frozenset[tuple[str, str, str]] = frozenset(
    {
        ("onco.CASE_LISTS", "is a symlink, which the pack never follows", "stricter"),
        ("onco.DATA_FILE", "has a data_filename that is a symlink", "stricter"),
        (
            "onco.DATA_FILE",
            "has a data_filename that names a clinical meta file's data file",
            "same outcome",
        ),
        (
            "onco.DATA_FILE",
            "has a data_filename that names a MAF whose name is not Unicode text",
            "stricter",
        ),
        (
            "onco.DATA_FILE",
            "has a data_filename that names a file whose name is not Unicode text",
            "stricter",
        ),
        (
            "onco.DATA_FILE",
            "has a data_filename that names no regular file in the study's directory",
            "same outcome",
        ),
        (
            "onco.DATA_FILE",
            (
                "{} a {} that names a meta file or a clinical data file, exactly or under case "
                "and normalisation folding"
            ),
            "same outcome",
        ),
        ("onco.DUPLICATE_PROFILE_META", "is a second mutation meta file", "same outcome"),
        ("onco.DUPLICATE_STABLE_ID", "repeats another meta file's stable_id", "same outcome"),
        ("onco.META_FILE", "has no meta file type cBioPortal defines", "same outcome"),
        ("onco.META_VALUE", "holds a stable_id that a mutation meta may not", "same outcome"),
        (
            "onco.META_VALUE",
            "holds a swissprot_identifier that is neither name nor accession",
            "same outcome",
        ),
        (
            "onco.NO_SEQUENCED_LIST",
            (
                "is a mutation meta file, and no case list's stable_id is the study id followed "
                "by _sequenced"
            ),
            "same outcome",
        ),
    }
)
"""The refusals of discovery, the meta files and the importer that the corpus reaches, by code,
template and kind: D444's own (the mutation meta, the MAF's role and name, ``case_lists``) and
D409's that a MAF study meets. The triples the corpus names must be exactly these."""
D409_HELD: frozenset[str] = frozenset(
    {
        "No meta file in the study's directory is a sample's",
        "No meta file in the study's directory is a study's",
        "The study's directory holds more meta-named entries than M4.2a reads",
        "has a data_filename that is not a file name M4.2a accepts",
        "has a data_filename that names a meta file",
        "has a data_filename that names the other clinical meta file's data file",
        "has a line without ':'",
        "holds a cancer_study_identifier that M4.2a does not accept",
        "holds a pmid that M4.2a does not accept",
        "holds a reference_genome that M4.2a does not accept",
        "holds a stable_id that M4.2a does not accept",
        "holds a type_of_cancer that M4.2a does not accept",
        "holds a {} that M4.2a does not accept",
        "is a second clinical meta file of its kind",
        "is a second study meta file",
        "is larger than a meta file may be",
        "is not UTF-8",
        "lacks the key {}",
        "names another study than the study meta",
        "{} a {} that is not a file name M4.2a accepts",
        "{} a {} that names a symlink, exactly or under case and normalisation folding",
        "{} a {} that names no regular file in the study's directory",
    }
)
"""The other templates of discovery, the meta files and the importer, which D409's corpora hold
(``test_study.py``, ``test_meta.py``, ``test_paths.py``, ``test_clinical.py``): no MAF cell is
needed for them, and a new one is a visible edit here."""


def code_of(found: str) -> str:
    return found.partition("|")[0]


def reader(found: str) -> bool:
    return "|" in found and code_of(found).removeprefix("onco.") in READER_CODES


def check_of(found: str) -> list[Any]:
    """The checks whose reasons match a refusal's: the readers' for a reader's code, the other
    modules' for another pack code, none for the core's."""
    _, bar, reason = found.partition("|")
    if not bar:
        return []
    pool = READER_CHECKS if reader(found) else OTHER_CHECKS
    return [check for check in pool if check.matches(reason)]


def template_of(found: str) -> str | None:
    """The one template a pack refusal's reason is written from, or ``None``: a template with no
    field that is the reason itself before one with fields that the reason also fills."""
    _, bar, reason = found.partition("|")
    if not bar:
        return None
    pool = READER_TEMPLATES if reader(found) else OTHER_TEMPLATES
    if reason in pool:
        return reason
    matched = [
        template for template in pool if re.fullmatch(checks().pattern(template), reason, re.DOTALL)
    ]
    return matched[0] if len(matched) == 1 else None


def kind(cell: str, code: str) -> str:
    """D409's one definition, per class: ``same outcome`` when the cell's recorded errors (a crash
    counted as ``crash``) hold an identity of the refusal's own class (``SAME_AS``)."""
    recorded = REFERENCE[cell]
    found = {identity for identity, _ in recorded["errors"]} | (
        {CRASH} if recorded["crash"] else set()
    )
    reason = code.partition("|")[2]
    same_as = SAME_AS_BY_REASON.get(
        reason, SAME_AS.get(code_of(code).removeprefix("onco."), frozenset())
    )
    return SAME if found & same_as else STRICTER


SAME_AS_BY_REASON: Mapping[str, frozenset[str]] = {
    "is a symlink, which the pack never follows": frozenset(),
}
"""A check whose class is not its code's: a symlinked case list is refused for being a symlink,
and the reference's error on such a cell, when it reports one, is about the list the link reaches
(a repeated ``stable_id``), so the check is always stricter (review r1 MJ2)."""


EDGED = (
    "the gene symbol starts or ends with whitespace, a control or format character, or a code "
    "point drawn as nothing, which cBioPortal accepts and the published column would keep as "
    "written"
)
"""The edged symbol's reason (M4.2b-2b-i): the one stricter item of the MAF reader held to a
predicate written here (``edged_cell``), not to its code's class alone."""


MODEL: dict[str, ModuleType] = {}
"""The test's model of a padded symbol (``symbol_oracle``), as the conftest's ``symbol_model``
loads it (its one loader), set for the module's tests by ``_symbol_model``."""


@pytest.fixture(autouse=True)
def _symbol_model(symbol_model: ModuleType) -> None:
    MODEL["model"] = symbol_model


def model() -> ModuleType:
    """The model ``_symbol_model`` set."""
    return MODEL["model"]


LINES = re.compile(r"\r\n|\n|\r")


def symbols(cell: Mapping[str, Any], column: str = "Hugo_Symbol") -> list[str]:
    """Every ``Hugo_Symbol`` cell (or ``column``'s) of every data row of the cell's MAFs, as
    written: a file of the cell, no meta file or case list, whose first line not starting with
    ``#`` (past a byte order mark) holds a column of that name (its first); a row too short for
    that column has none."""
    found: list[str] = []
    for name, content in cell.items():
        if not isinstance(content, bytes) or name.startswith(("meta_", "case_lists/")):
            continue
        lines = LINES.split(content.decode("utf-8", "replace").removeprefix("\ufeff"))
        rows = [line for line in lines if not line.startswith("#")]
        if not rows or column not in rows[0].split("\t"):
            continue
        at = rows[0].split("\t").index(column)
        found += [fields[at] for line in rows[1:] if len(fields := line.split("\t")) > at]
    return found


@cache
def edged_cell(cell: str) -> bool:
    """The predicate of the edged symbol (M27, M4.2b-2b-i), written from D444 and the test's
    model, never from ``mutation.py``: some data row of the cell's MAF, validated or skipped, has a
    ``Hugo_Symbol`` whose first or last code point pads it (``symbol_oracle.edged``)."""
    return any(model().edged(symbol) for symbol in symbols(corpus().CELLS[cell]))


@cache
def accepted_by_reference(cell: str) -> bool:
    """The live outcome the stricter kind reads: the reference did not crash on the cell and
    every error it reported has an exit whose predicate holds (it accepts the cell)."""
    recorded = REFERENCE[cell]
    return not recorded["crash"] and all(
        any(holds(corpus().CELLS[cell], file) for _, holds in EXITS.get(identity, ()))
        for identity, file in recorded["errors"]
    )


def symbol_problems(cell: str, found: str) -> list[str]:
    """The edged symbol's item against its predicate and the live outcome: a refusal of it where
    the predicate does not hold or the reference does not accept the cell, an accepted cell the
    predicate holds for; and a cell of the ``symbol`` generator (one symbol of one class at one
    place, the rest valid) refused other than by the item where the predicate holds, or refused
    where it does not."""
    problems: list[str] = []
    edged = edged_cell(cell)
    if found.partition("|")[2] == EDGED:
        if not edged:
            problems.append(f"{cell}: refused as an edged symbol, and no symbol of it is edged")
        if not accepted_by_reference(cell):
            problems.append(f"{cell}: refused as an edged symbol, and the reference refuses it")
    elif found == "ok" and edged:
        problems.append(f"{cell}: accepted, and a symbol of it is edged")
    if cell in SKIPPED_SYMBOLS:
        refused = SKIPPED_SYMBOLS[cell]
    elif cell.startswith("symbol:"):
        # review r1 m1: from the cell's own class and place, a literal of the test, never from the
        # predicate: a code point of a padding class at an edge is refused, text or inside is not
        kind, place = cell.split(":")[1].split("_")[0], cell.split("_")[-2]
        refused = kind in PADDING_CLASSES and place != "inside"
    else:
        return problems
    wanted = f"onco.PADDED_VALUE|{EDGED}" if refused else "ok"
    if found != wanted:
        problems.append(f"{cell}: {found!r} is not the class's {wanted!r}")
    if refused != edged:
        problems.append(f"{cell}: the predicate says {edged}, the cell's class {refused}")
    return problems


PADDING_CLASSES = frozenset({"whitespace", "braille", "unsafe", "ignorable"})
"""The classes of D444's edged symbol, written here (the corpus names a cell by its class)."""
SKIPPED_SYMBOLS: Mapping[str, bool] = {
    "symbol:skipped_space_silent": True,
    "symbol:skipped_ideographic_silent": True,
    "symbol:skipped_empty_silent": False,
    "symbol:skipped_padded-na_silent": True,
    "symbol:skipped_unknown-padded_silent": True,
    "symbol:skipped_zwsp_silent": True,
}
"""Review r1 m2: D444's sub-cases on a row the reference skips (``Silent``), each refused by the
edged symbol or not, written here: a symbol blank by ``str.strip()`` (a space, U+3000), padded
``NA`` and ``Unknown``, and U+200B alone are refused; an empty symbol is not edged."""


SPELLING = (
    "the value looks blank, or reads as a spelling of no value once case, width, spaces and "
    "invisible code points are set aside, but is not one of the column's own spellings of no "
    "value, which the published column would keep as a value"
)
"""The look-alike's reason (M4.2b-2b-ii): held, as the edged symbol is, to a predicate written
here (``spelled_cell``)."""
SPELLED_FAMILY = frozenset(
    "".join(spelled)
    for word in ("unknown", "na", "n/a", "null")
    for spelled in itertools.product(*({c.lower(), c.upper()} for c in word))
)
"""D444's 152 spellings of no value of the class and the status: every ASCII case of the four
words, by ``itertools.product`` (written here, never read from the pack)."""
SPELLED_COLUMNS: Mapping[str, tuple[frozenset[str], tuple[str, ...]]] = {
    "Hugo_Symbol": (frozenset({"NA", "Unknown"}), ("unknown", "na")),
    "Variant_Classification": (SPELLED_FAMILY, ("unknown", "na", "n/a", "null")),
    "Mutation_Status": (SPELLED_FAMILY, ("unknown", "na", "n/a", "null")),
}
"""D444's three columns of the look-alike, each with its exact spellings and its words."""


@cache
def spelled_cell(cell: str) -> bool:
    """The predicate of the look-alike (M4.2b-2b-ii), written from D444 and the test's model, never
    from ``mutation.py``: some data row of the cell's MAF, validated or skipped, has in one of
    ``SPELLED_COLUMNS`` a cell the model reads as a look-alike (``symbol_oracle.lookalike``)."""
    files = corpus().CELLS[cell]
    return any(
        model().lookalike(value, exact, words)
        for column, (exact, words) in SPELLED_COLUMNS.items()
        for value in symbols(files, column)
    )


SPELLING_REFUSED = frozenset(
    {
        "fullwidth-na",
        "kelvin-unknown",
        "square-na",
        "zwj-na",
        "padded-upper",
        "zwsp",
        "space",
        "braille",
        "fullwidth-null",
    }
)
"""The ``spelling`` generator's values refused as look-alikes in the class and the status, written
here from each value's class (review m1's lesson): a fullwidth, Kelvin, squared or joined spelling,
a padded one, and a cell that looks blank."""
SYMBOL_SPELLING_REFUSED = frozenset(
    {"fullwidth-na", "kelvin-unknown", "square-na", "zwj-na", "lower-na", "mixed-unknown"}
)
"""Those refused as look-alikes in ``Hugo_Symbol``: its words are ``na`` and ``unknown`` alone,
and its exact spellings ``NA`` and ``Unknown``, so ``na`` and ``uNkNoWn`` are look-alikes there."""
SYMBOL_EDGED = frozenset({"padded-upper", "zwsp", "braille", "padded-somatic", "space"})
"""Those the edged symbol refuses first in ``Hugo_Symbol`` (but a space on a validated row, which
is blank: ``REQUIRED_VALUE``)."""


def spelling_wanted(case: str) -> str:
    """A ``spelling`` cell's outcome, from its column, value and row by the literals above."""
    column, value, row = case.split("_")
    if value == "space" and row == "valid" and column != "status":
        return "onco.REQUIRED_VALUE|the required value is blank"
    if column == "symbol":
        if value in SYMBOL_EDGED:
            return f"onco.PADDED_VALUE|{EDGED}"
        refused = value in SYMBOL_SPELLING_REFUSED
    else:
        refused = value in SPELLING_REFUSED
    return f"onco.MISSING_SPELLING|{SPELLING}" if refused else "ok"


def spelling_problems(cell: str, found: str) -> list[str]:
    """The look-alike's item against its predicate and the live outcome, as ``symbol_problems``
    holds the edged symbol's: a refusal of it where the predicate does not hold or the reference
    does not accept the cell, an accepted cell the predicate holds for; and a cell of the
    ``spelling`` generator whose outcome is not its literal (``spelling_wanted``), or whose
    predicate disagrees with a literal of a look-alike refused or a value accepted."""
    problems: list[str] = []
    spelled = spelled_cell(cell)
    if found.partition("|")[2] == SPELLING:
        if not spelled:
            problems.append(f"{cell}: refused as a look-alike, and no cell of it is one")
        if not accepted_by_reference(cell):
            problems.append(f"{cell}: refused as a look-alike, and the reference refuses it")
    elif found == "ok" and spelled:
        problems.append(f"{cell}: accepted, and a cell of it is a look-alike")
    if not cell.startswith("spelling:"):
        return problems
    wanted = spelling_wanted(cell.split(":", 1)[1])
    if found != wanted:
        problems.append(f"{cell}: {found!r} is not the class's {wanted!r}")
    if (wanted.endswith(SPELLING) and not spelled) or (wanted == "ok" and spelled):
        problems.append(f"{cell}: the predicate says {spelled}, the cell's class {wanted!r}")
    return problems


def explain(outcomes: Mapping[str, str], cells: Sequence[str]) -> list[str]:
    """Why the leniency map does not explain ``outcomes`` over ``cells``, one line a problem: a
    refusal whose ``(code, kind)`` is no item, a pack refusal whose reason matches no template or
    several, an accepted cell that crashed the reference, an accepted cell's error no exit's
    predicate holds for (an identity with no exit among them), and the edged symbol's item held to
    its predicate (``symbol_problems``)."""
    problems: list[str] = []
    for cell in cells:
        found = outcomes[cell]
        recorded = REFERENCE[cell]
        problems += symbol_problems(cell, found)
        problems += spelling_problems(cell, found)
        if found == "ok":
            if recorded["crash"]:
                problems.append(f"{cell}: accepted, and the reference crashed")
            for identity, file in recorded["errors"]:
                exits = EXITS.get(identity, ())
                if not any(holds(corpus().CELLS[cell], file) for _, holds in exits):
                    problems.append(f"{cell}: accepted, {identity} in {file} has no exit")
            continue
        key = (code_of(found), kind(cell, found))
        if key not in ITEMS:
            problems.append(f"{cell}: {key} is no item")
        if "|" in found and template_of(found) is None:
            problems.append(f"{cell}: {found!r} matches no template, or several")
    return problems


Named = tuple[
    set[tuple[str, str]],
    set[str],
    dict[str, set[tuple[str, str]]],
    set[tuple[str, str, str]],
    set[tuple[str, str, str]],
]


def named(outcomes: Mapping[str, str]) -> Named:
    """What the cells name: the items, the exits, for each reader check the items its refusals
    are, and the ``(code, template, kind)`` triples of the readers and of the other modules. An
    error with no exit names none (``explain`` reports it)."""
    items: set[tuple[str, str]] = set()
    exits: set[str] = set()
    by_check: dict[str, set[tuple[str, str]]] = {check.id: set() for check in READER_CHECKS}
    readers: set[tuple[str, str, str]] = set()
    others: set[tuple[str, str, str]] = set()
    for cell in CELL_IDS:
        found = outcomes[cell]
        if found != "ok":
            key = (code_of(found), kind(cell, found))
            items.add(key)
            template = template_of(found)
            if template is not None:
                triple = (code_of(found), template, key[1])
                (readers if reader(found) else others).add(triple)
            if reader(found):
                for check in check_of(found):
                    by_check[check.id].add(key)
            continue
        for identity, file in REFERENCE[cell]["errors"]:
            exits.update(
                head for head, holds in EXITS.get(identity, ()) if holds(corpus().CELLS[cell], file)
            )
    return items, exits, by_check, readers, others


def judged(outcomes: Mapping[str, str]) -> list[str]:
    """The map's whole judgement of ``outcomes``, the map alone: ``explain``; every item and exit
    named; each reader check named by exactly one item, but those ``HELD``; the readers' triples
    the cells name exactly ``INVENTORY``, the other modules' exactly ``OTHER_INVENTORY``."""
    problems = explain(outcomes, CELL_IDS)
    items, exits, by_check, readers, others = named(outcomes)
    if items != set(ITEMS):
        problems.append(f"items named {sorted(items ^ set(ITEMS))} differ")
    heads = {head for found in EXITS.values() for head, _ in found}
    if exits != heads:
        problems.append(f"exits named {sorted(exits ^ heads)} differ")
    for check in READER_CHECKS:
        if set(check.templates) <= set(HELD):
            continue
        if len(by_check[check.id]) != 1:
            problems.append(f"{check.id}: named by {sorted(by_check[check.id])}")
    if readers != INVENTORY:
        problems.append(f"the readers' checks named {sorted(readers ^ INVENTORY)} differ")
    if others != OTHER_INVENTORY:
        problems.append(f"the other refusals named {sorted(others ^ OTHER_INVENTORY)} differ")
    return problems


READER_CODES = frozenset(
    {
        "NOT_UTF8",
        "CONTROL_CHARACTER",
        "NO_HEADER",
        "TOO_MANY_COLUMNS",
        "COLUMN_NAME",
        "DUPLICATE_COLUMN",
        "MISSING_COLUMN",
        "BLANK_LINE",
        "COMMENT_LINE",
        "REPEATED_HEADER",
        "ROW_WIDTH",
        "LONG_FIELD",
        "TOO_MANY_CELLS",
        "UNKNOWN_SAMPLE",
        "DRIVER_VALUE",
        "REQUIRED_VALUE",
        "ENTREZ_VALUE",
        "NO_GENE",
        "COUNT_VALUE",
        "POSITION_VALUE",
        "AMINO_ACID_CHANGE",
        "NCBI_BUILD",
        "NORMAL_SAMPLE",
        "PADDED_VALUE",
        "MISSING_SPELLING",
        "CASE_LIST",
    }
)
"""The codes the two readers refuse with: a refusal with one of them is a reader check's."""


@pytest.mark.parametrize("cell", CELL_IDS)
def test_aibi_s_outcome_is_the_recorded_one(cell: str, onco: ModuleType, tmp_path: Path) -> None:
    found = outcome(onco, build(tmp_path, corpus().CELLS[cell]))
    if os.environ.get("AIBI_RECORD_MUTATION_OUTCOMES"):
        recorded = json.loads(OUTCOMES_FILE.read_text()) if OUTCOMES_FILE.exists() else {}
        recorded[cell] = found
        OUTCOMES_FILE.write_text(json.dumps(dict(sorted(recorded.items())), indent=0) + "\n")
        return
    assert found == OUTCOMES[cell]


def test_every_cell_has_a_recorded_reference_outcome_and_aibi_outcome() -> None:
    assert set(REFERENCE) == set(CELL_IDS)
    assert set(OUTCOMES) == set(CELL_IDS)


def test_the_leniency_map_explains_every_cell() -> None:
    """``judged``: the map explains every cell, names every item and exit, every reader check by
    exactly one item, and the inventories exactly."""
    assert judged(OUTCOMES) == []


def test_the_inventory_is_the_code_s() -> None:
    """Review r3 MJ-1: the templates the walker finds in the readers are exactly the inventory's
    and ``HELD``'s, and those of discovery, the meta files and the importer exactly
    ``OTHER_INVENTORY``'s and ``D409_HELD``'s: a check deleted from the code, or added, fails
    here, whatever outcomes are recorded, until the inventory and D444 are edited."""
    listed = {template for _, template, _ in INVENTORY}
    assert listed | set(HELD) == READER_TEMPLATES
    assert not listed & set(HELD)
    other = {template for _, template, _ in OTHER_INVENTORY}
    assert other | D409_HELD == OTHER_TEMPLATES
    assert not other & D409_HELD
    # every template is some check's, so that the drop-each-check property covers them all
    assert {t for check in READER_CHECKS for t in check.templates} == READER_TEMPLATES
    assert other <= {t for check in OTHER_CHECKS for t in check.templates}


def test_every_held_check_names_the_test_that_holds_it() -> None:
    """Each ``HELD`` template names a test of ``test_mutation.py`` whose code (its statements,
    never its docstring) holds that very reason and code as string constants (review r4)."""
    tree = ast.parse((HERE / "test_mutation.py").read_text(encoding="utf-8"))
    functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
    for template, (code, _, test) in HELD.items():
        body = functions[test].body
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
            body = body[1:]
        constants = {
            node.value
            for statement in body
            for node in ast.walk(statement)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        }
        assert template in constants, template
        assert code in constants, template


def in_d444(template: str) -> str:
    """A template as D444 lists it: each field ``…``."""
    return template.replace("{}", "…")


def test_d444_lists_the_inventory_and_the_corpus_size() -> None:
    """D444 lists every check of the inventory, ``HELD`` and ``OTHER_INVENTORY`` by its reason, and
    states the corpus's true size (review r3 m1)."""
    text = SPEC.read_text(encoding="utf-8")
    row = next(line for line in text.splitlines() if line.startswith("| D444 |"))
    templates = [template for _, template, _ in INVENTORY | OTHER_INVENTORY] + list(HELD)
    for template in templates:
        assert f"`{in_d444(template)}`" in row, template
    assert f"a corpus of {len(CELL_IDS)} cells" in row
    (changelog,) = [line for line in text.splitlines() if line.startswith("- **v0.8.110**")]
    assert f"held by {len(CELL_IDS)} generated cells" in changelog


DROPPED_OTHERS = tuple(
    check
    for check in OTHER_CHECKS
    if set(check.templates) & {template for _, template, _ in OTHER_INVENTORY}
)


@pytest.mark.parametrize("check", [check.id for check in (*READER_CHECKS, *DROPPED_OTHERS)])
def test_dropping_any_check_fails_the_map_alone(
    check: str, onco: ModuleType, tmp_path: Path
) -> None:
    """Review r2 MJ-A, r3 MJ-1, the drop-each-check property, over every check of the readers and
    every check of the other modules that ``OTHER_INVENTORY`` holds: with the check's test made
    ``False``, the cells it refused are imported again, and the map alone (``judged``, given the
    fresh outcomes) reports a problem. A check no cell reaches fails here, but those ``HELD``."""
    (found,) = [
        candidate for candidate in (*READER_CHECKS, *DROPPED_OTHERS) if candidate.id == check
    ]
    if set(found.templates) <= set(HELD):
        pytest.skip(f"held by {HELD[found.templates[0]][2]}")
    cells = [cell for cell in CELL_IDS if found in check_of(OUTCOMES[cell])]
    assert cells, f"{check}: no corpus cell reaches it"
    names = ("mutation", "caselists", "study", "meta", "importer")
    pack = {name: importlib.import_module(f"aibi.packs.onco.{name}") for name in names}
    with checks().dropped(found, pack):
        fresh = {
            cell: outcome(onco, build(tmp_path / str(index), corpus().CELLS[cell]))
            for index, cell in enumerate(cells)
        }
    assert judged({**OUTCOMES, **fresh}) != [], check


def test_the_map_fails_on_its_own_when_a_check_is_dropped(
    onco: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review r1 MJ3 (its probe p8): with ``COUNT_VALUE`` dropped from the reader, the cells whose
    reference errors hold ``count`` are re-imported and accepted, and the map alone, given those
    fresh outcomes and no recorded one, reports them: ``count`` is an exit only in an uncalled
    meta's MAF."""
    mutation = importlib.import_module("aibi.packs.onco.mutation")
    validated = mutation._Reading.validated

    def without_counts(self: Any, fields: list[str]) -> list[Any]:
        return [found for found in validated(self, fields) if found[1] != "COUNT_VALUE"]

    monkeypatch.setattr(mutation._Reading, "validated", without_counts)
    cells = [
        cell
        for cell in CELL_IDS
        if any(identity == "count" for identity, _ in REFERENCE[cell]["errors"])
    ]
    assert len(cells) >= 10
    fresh = {
        cell: outcome(onco, build(tmp_path / str(index), corpus().CELLS[cell]))
        for index, cell in enumerate(cells)
    }
    problems = explain(fresh, cells)
    assert len(problems) >= 10
    assert all("count in data_mutations.txt has no exit" in problem for problem in problems)


def test_the_map_reports_a_check_no_cell_names() -> None:
    """Review r3 MJ-1, the map's own arms: for each reader check not wholly ``HELD``, with every
    cell it refused accepted instead, ``judged`` reports the check named by no item and the
    readers' triples differing from ``INVENTORY``, each in its own words."""
    for check in READER_CHECKS:
        if set(check.templates) <= set(HELD):
            continue
        cells = [cell for cell in CELL_IDS if check in check_of(OUTCOMES[cell])]
        problems = judged({**OUTCOMES, **dict.fromkeys(cells, "ok")})
        assert f"{check.id}: named by []" in problems, check.id
        assert any(p.startswith("the readers' checks named") for p in problems), check.id


def test_a_reason_two_templates_write_is_no_one_s(monkeypatch: pytest.MonkeyPatch) -> None:
    """Review r3 MJ-1: a reason that two templates with fields both write is no template's
    (``None``), never the first found; a template with no field equal to the reason is its own."""
    import sys

    module = sys.modules[__name__]
    monkeypatch.setattr(module, "READER_TEMPLATES", frozenset({"the {} is bad", "the line {}"}))
    assert template_of("onco.NOT_UTF8|the line is bad") is None
    monkeypatch.setattr(
        module, "READER_TEMPLATES", frozenset({"the line is bad", "the {} is bad", "the line {}"})
    )
    assert template_of("onco.NOT_UTF8|the line is bad") == "the line is bad"


def test_an_error_with_no_exit_is_a_problem_never_an_exception() -> None:
    """Review r2 m6: an accepted cell whose recorded error has no exit at all is reported by
    ``explain``, and ``named`` passes it, never a ``KeyError``."""
    cell = next(cell for cell in CELL_IDS if REFERENCE[cell]["errors"] and OUTCOMES[cell] != "ok")
    outcomes = {**OUTCOMES, cell: "ok"}
    assert any(cell in problem for problem in explain(outcomes, [cell]))
    named(outcomes)


def test_every_item_and_exit_is_in_d444() -> None:
    text = SPEC.read_text(encoding="utf-8")
    row = next(line for line in text.splitlines() if line.startswith("| D444 |"))
    heads = {head for found in EXITS.values() for head, _ in found}
    for item in {*ITEMS.values(), *heads}:
        assert item in row, item


PUBLISHED_GENERATORS = ("byte", "header", "meta", "list", "normal")


def published_cells() -> list[str]:
    chosen = []
    tokens: set[str] = set()
    for cell in CELL_IDS:
        if OUTCOMES.get(cell) != "ok":
            continue
        generator, case = cell.split(":", 1)
        if generator in PUBLISHED_GENERATORS:
            chosen.append(cell)
        elif generator == "row":
            token = case.rsplit("_", 1)[0]
            if token not in tokens:
                tokens.add(token)
                chosen.append(cell)
    return chosen


@pytest.mark.parametrize("cell", published_cells())
def test_an_accepted_cell_publishes(cell: str, world: Any, registry: PackRegistry) -> None:
    directory = build(world.root / "cell", corpus().CELLS[cell])
    world.publish("study", directory, registry, pack="onco")


SYMBOL_CELLS = [cell for cell in CELL_IDS if cell.startswith("symbol:")]


def test_the_reference_accepts_every_padded_symbol() -> None:
    """M4.2b-2b-i, the live outcome the stricter item reads: on the pinned reference, offline
    (``-n``), every cell of the ``symbol`` generator, a symbol of each class at each place on a
    validated and on a skipped row, exits 3 with no error and no crash: it strips the whitespace
    (V:1806) and reads the rest as a symbol. Every class of the model has a code point there, on
    every place and row."""
    for cell in SYMBOL_CELLS:
        assert REFERENCE[cell] == {"crash": False, "errors": [], "rc": 3}, cell
    assert set(SKIPPED_SYMBOLS) <= set(SYMBOL_CELLS)
    kinds = {
        cell.split(":")[1].split("_")[0] for cell in SYMBOL_CELLS if cell not in SKIPPED_SYMBOLS
    }
    assert kinds == set(model().CLASSES)
    shapes = {tuple(cell.split("_")[-2:]) for cell in set(SYMBOL_CELLS) - set(SKIPPED_SYMBOLS)}
    assert shapes == {
        (place, row)
        for place in ("leading", "trailing", "both", "inside")
        for row in ("valid", "silent")
    }


@pytest.mark.skipif(
    unicodedata.unidata_version != "15.0.0",
    reason="the symbol model's literals are Unicode 15.0's, the pinned Python's",
)
def test_each_symbol_cell_s_code_point_is_of_its_class() -> None:
    """The corpus names each code point's class; the model says it is that class."""
    points = corpus().SYMBOL_POINTS
    assert set(points) == set(model().CLASSES)
    for kind, found in points.items():
        for point in found:
            assert model().class_of(point) == kind, (kind, hex(point))


@pytest.mark.parametrize(
    "narrowed",
    ["deleted", "strip", "validated", "unsafe", "ignorable", "leading", "stripped"],
)
def test_narrowing_the_edged_symbol_fails_the_map(
    narrowed: str, onco: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """M4.2b-2b-i: the check narrowed or deleted, and the cells it refused re-imported (as a
    re-recording would), the map alone reports a problem: the check deleted; padding read by
    ``str.strip()`` alone; checked on validated rows alone; without D312's unsafe class; without
    the default-ignorable table; at the first code point alone; on the symbol after
    ``str.strip()``."""
    mutation = importlib.import_module("aibi.packs.onco.mutation")
    edge_point = mutation.edge_point
    edged = mutation.edged
    checked = mutation._Reading.checked
    visible = importlib.import_module("aibi.core.schema.visible")

    def narrow_point(character: str) -> bool:
        point = ord(character)
        if narrowed == "strip":
            return character.isspace()
        if narrowed == "unsafe" and visible.unsafe_point(point):
            return character.isspace() or point == visible.BRAILLE_BLANK
        if narrowed == "ignorable" and not visible.unsafe_point(point):
            return character.isspace() or point == visible.BRAILLE_BLANK
        return edge_point(character)

    def narrow(symbol: str) -> bool:
        if narrowed == "deleted":
            return False
        if narrowed == "leading":
            return symbol != "" and narrow_point(symbol[0])
        if narrowed == "stripped":
            return edged(symbol.strip())
        return symbol != "" and (narrow_point(symbol[0]) or narrow_point(symbol[-1]))

    def validated_only(self: Any, fields: list[str]) -> Any:
        if narrowed == "validated" and self.skipped(fields):
            # a row skipped by its class (an edged symbol is never exactly ``Unknown``) keeps
            # being skipped with a plain symbol, which the narrowed check never refuses
            fields = [*fields]
            fields[self.hugo] = "TP53"
        return checked(self, fields)

    monkeypatch.setattr(mutation, "edge_point", narrow_point)
    monkeypatch.setattr(mutation, "edged", narrow)
    monkeypatch.setattr(mutation._Reading, "checked", validated_only)
    cells = [cell for cell in CELL_IDS if OUTCOMES[cell].endswith(EDGED)]
    fresh = {
        cell: outcome(onco, build(tmp_path / str(index), corpus().CELLS[cell]))
        for index, cell in enumerate(cells)
    }
    assert fresh != {cell: OUTCOMES[cell] for cell in cells}, narrowed
    assert judged({**OUTCOMES, **fresh}) != [], narrowed


SPELLING_CELLS = [cell for cell in CELL_IDS if cell.startswith("spelling:")]


def test_the_reference_accepts_every_look_alike() -> None:
    """M4.2b-2b-ii, the live outcome the stricter item reads: on the pinned reference, offline,
    every cell of the ``spelling`` generator exits 3 with no error and no crash, but a lone space
    on a validated row in the class and the symbol, which it reads as blank (``blank-required``);
    it warns of a class outside the format's list and of a status outside its own, and loads the
    row. The generator covers the three columns on a validated and a skipped row."""
    blank = {"spelling:class_space_valid", "spelling:symbol_space_valid"}
    for cell in SPELLING_CELLS:
        if cell in blank:
            wanted = {"crash": False, "errors": [["blank-required", "data_mutations.txt"]], "rc": 1}
        else:
            wanted = {"crash": False, "errors": [], "rc": 3}
        assert REFERENCE[cell] == wanted, cell
    shapes = {(cell.split(":")[1].split("_")[0], cell.split("_")[-1]) for cell in SPELLING_CELLS}
    assert shapes == {
        (column, row) for column in ("class", "status", "symbol") for row in ("valid", "skipped")
    }
    assert len(SPELLING_CELLS) == 90


@pytest.mark.parametrize(
    "narrowed",
    [
        "deleted",
        "validated",
        "class",
        "status",
        "symbol",
        "casefold",
        "nfkc",
        "removal",
        "strip",
        "exact",
    ],
)
def test_narrowing_the_look_alike_fails_the_map(
    narrowed: str, onco: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """M4.2b-2b-ii: the look-alike's check narrowed or deleted, and the cells it refused
    re-imported (as a re-recording would), the map alone reports a problem: the check deleted;
    checked on validated rows alone; with the class, the status or the symbol column left out;
    N without casefold; without NFKC; without deleting the ignorable and unsafe code points; N as
    ``str.strip()`` and ``lower()``; and the column's exact spellings refused too."""
    mutation = importlib.import_module("aibi.packs.onco.mutation")
    normal_form = mutation.normal_form
    lookalike = mutation.lookalike
    setup = mutation._Reading.setup
    checked = mutation._Reading.checked
    columns = {
        "class": "Variant_Classification",
        "status": "Mutation_Status",
        "symbol": "Hugo_Symbol",
    }

    def removed(cell: str) -> str:
        return "".join(c for c in cell if not mutation._removable(ord(c)))

    def narrow_normal(cell: str) -> str | None:
        if narrowed == "strip":
            return cell.strip().lower()
        if narrowed == "removal":
            folded = unicodedata.normalize("NFKC", cell.strip()).casefold()
            return unicodedata.normalize("NFKC", folded)
        if normal_form(cell) is None:
            return None
        if narrowed == "casefold":
            return unicodedata.normalize("NFKC", removed(cell).strip())
        if narrowed == "nfkc":
            return removed(cell).strip().casefold()
        return normal_form(cell)

    def narrow(cell: str, exact: frozenset[str], words: tuple[str, ...]) -> bool:
        if narrowed == "deleted":
            return False
        if narrowed == "exact":
            return cell != "" and (lookalike(cell, exact, words) or cell in exact)
        if cell == "" or cell in exact:
            return False
        found = narrow_normal(cell)
        return found is not None and (found == "" or found in words)

    def narrow_setup(self: Any, names: list[str]) -> None:
        setup(self, names)
        if narrowed in columns:
            left = self.at.get(columns[narrowed])
            self.spellings = [item for item in self.spellings if item[0] != left]

    def validated_only(self: Any, fields: list[str]) -> Any:
        if narrowed == "validated" and self.skipped(fields):
            spellings, self.spellings = self.spellings, []
            try:
                return checked(self, fields)
            finally:
                self.spellings = spellings
        return checked(self, fields)

    monkeypatch.setattr(mutation, "lookalike", narrow)
    monkeypatch.setattr(mutation._Reading, "setup", narrow_setup)
    monkeypatch.setattr(mutation._Reading, "checked", validated_only)
    cells = [cell for cell in CELL_IDS if OUTCOMES[cell].endswith(SPELLING)]
    if narrowed == "exact":
        cells = SPELLING_CELLS
    fresh = {
        cell: outcome(onco, build(tmp_path / str(index), corpus().CELLS[cell]))
        for index, cell in enumerate(cells)
    }
    assert fresh != {cell: OUTCOMES[cell] for cell in cells}, narrowed
    assert judged({**OUTCOMES, **fresh}) != [], narrowed


def test_the_judges_report_a_refused_cell_accepted() -> None:
    """Review r2 minor 2: each predicate's own arm, outside the ``spelling`` and ``symbol``
    generators' literals: every cell refused as a look-alike, or as an edged symbol, given
    ``ok`` instead (as a re-recording without the check would), is reported by its judge as
    accepted while a cell of it is one, in those words."""
    spelled = [cell for cell in CELL_IDS if OUTCOMES[cell].endswith(SPELLING)]
    edged = [cell for cell in CELL_IDS if OUTCOMES[cell].endswith(EDGED)]
    assert spelled
    assert edged
    for cell in spelled:
        wanted = f"{cell}: accepted, and a cell of it is a look-alike"
        assert wanted in spelling_problems(cell, "ok"), cell
    for cell in edged:
        wanted = f"{cell}: accepted, and a symbol of it is edged"
        assert wanted in symbol_problems(cell, "ok"), cell

"""The discrete copy-number corpus against the reference's recorded outcomes and D446's predicates
(M4.2b-3, D446).

Every cell of ``cna_corpus.py`` is imported through ``PACK.importer`` and the core's checks of a
pack's result (``run_importer``), and its outcome is held four ways:

- **per cell, to its recorded outcome** (``cna_outcomes.json``), the onco code asserted;
- **per cell, to the predicates** (``cna_oracle.expected``): the outcome is the first item, in the
  pack's refusal order, whose predicate over the cell holds, ``ok`` when none does. The predicates
  are written in the test from the reference and D446, never read from ``cna.py``, so a check
  narrowed in the reader and its outcomes re-recorded still fails here;
- **per cell, to the reference** (``cna_reference.json``, each cell's outcome on cbioportal-core
  47890bb, offline, not relaxed, by error identity and the file it names): a refusal's kind is
  D409's one definition, ``same outcome`` where the reference reports an error of the refusal's own
  class (``SAME_AS``) in a file the refusal is about, ``stricter`` otherwise, and ``(module, code,
  reason, kind)`` must be an item of the literal ``INVENTORY``; an accepted cell's every error and
  crash must be explained by an exit whose predicate holds over the file it names (``EXITS``, by
  file: a file the pack never reads, a meta it never imports), and no error in a file the pack
  never reads decides a refusal's kind (``_about``);
- **through the store**, for the accepted cells of every generator: published.

To record a new cell's outcome on the reference, run the scratch harness D446 names; to record
aibi's, run this module with ``AIBI_RECORD_CNA_OUTCOMES=1`` and review the difference.
"""

import importlib
import importlib.util
import json
import os
import posixpath
import re
import sys
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
REFERENCE: Mapping[str, Any] = json.loads((HERE / "cna_reference.json").read_text())
OUTCOMES_FILE = HERE / "cna_outcomes.json"
OUTCOMES: Mapping[str, str] = (
    json.loads(OUTCOMES_FILE.read_text()) if OUTCOMES_FILE.exists() else {}
)
AT = "2026-01-01T00:00:00Z"
SAME = "same outcome"
STRICTER = "stricter"
CRASH = "crash"


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"{name}_cna_test", HERE / f"{name}.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@cache
def corpus() -> ModuleType:
    return _load("cna_corpus")


@cache
def checks() -> ModuleType:
    return _load("cna_checks")


@cache
def oracle() -> ModuleType:
    return _load("cna_oracle")


CELL_IDS = sorted(corpus().CELLS)
CHECKS: tuple[Any, ...] = tuple(checks().every_check())
TEMPLATES: frozenset[str] = frozenset(
    template for module in checks().MODULES for template in checks().templates(module)
)
SHARED_TEMPLATES: frozenset[str] = frozenset(
    template for module in checks().SHARED for template in checks().templates(module)
)

HEADER = frozenset(
    {
        "duplicate-column",
        "gene-placement",
        "no-gene-column",
        "no-samples",
        "sample-undefined",
        "sample-space",
        "header-cascade",
        CRASH,
    }
)
"""Every error the reference reports on a CNA header (V:934-946, V:1107-1172) and its cascade, and
its csv reader's crash on a name over the field limit: one class, so that a header refusal is the
same outcome wherever the reference errs on the header, whichever error it names first."""
SAME_AS: Mapping[str, frozenset[str]] = {
    "NOT_UTF8": frozenset({"not-utf8"}),
    "CONTROL_CHARACTER": frozenset(),
    "NO_HEADER": frozenset({"no-header"}),
    "BYTE_ORDER_MARK": HEADER,
    "MISSING_COLUMN": HEADER,
    "TOO_MANY_COLUMNS": HEADER,
    "DUPLICATE_COLUMN": HEADER,
    "COLUMN_NAME": HEADER,
    "UNKNOWN_SAMPLE": HEADER | {"sample-undefined"},
    "BLANK_LINE": frozenset({"blank-line", CRASH}),
    "COMMENT_LINE": frozenset({"comment-line", CRASH}),
    "REPEATED_HEADER": frozenset({"repeated-header", CRASH}),
    "ROW_WIDTH": frozenset({"row-width", CRASH}),
    "LONG_FIELD": frozenset({CRASH}),
    "ENTREZ_VALUE": frozenset({"entrez-nonpositive"}),
    "NO_GENE": frozenset({"no-gene"}),
    "DUPLICATE_GENE": frozenset(),
    "CNA_VALUE": frozenset({"cna-value"}),
    "PADDED_VALUE": frozenset(),
    "LINE_ENDS": frozenset({"line-ends"}),
    "NO_CNA_LIST": frozenset({"cna"}),
    "DUPLICATE_PROFILE_META": frozenset({"type-meta", "duplicate-stable-id"}),
    "DUPLICATE_STABLE_ID": frozenset({"duplicate-stable-id"}),
    "META_VALUE": frozenset({"type-meta"}),
    "META_FILE": frozenset({"meta-type"}),
    "META_FIELD": frozenset({"meta-key"}),
    "STUDY_MISMATCH": frozenset({"study-mismatch"}),
    "DATA_FILE": frozenset(
        {"open-fail", "missing-column", "missing-amino-column", "sv-content", "header-order"}
        | {"no-gene-column", "sample-undefined", "header-cascade"}
    ),
}
"""Each code's class among the reference's error identities (``crash`` for a crash): a refusal is
``same outcome`` only when the cell's recorded errors hold one of its code's, in a file the refusal
is about, and ``stricter`` otherwise. ``DATA_FILE``'s class is the reference failing to open the
named file or reading a file in the wrong role (a clinical file, a MAF, an SV file or a PD file as
a CNA matrix, a CNA matrix as a MAF, an SV or a timeline); it holds no PD identity, a PD value's
refusal being its own item (``PD_SITE``), judged by file. A control character is always
stricter (the reference reads it as content), as slice 4's is. Every data line's structure code
(blank, ``#``, repeated header, width, field length) holds the crash: the reference's csv reader
fails on a field over its limit on any line, before it looks at the line."""
SAME_AS_BY_REASON: Mapping[str, frozenset[str]] = {
    "the line of column names and the five lines after it hold no tab": frozenset(),
    "the file has no Hugo_Symbol column, which the pack keys a gene by": frozenset(),
    "the Entrez gene id is not an integer, so cBioPortal would not load the row": frozenset(),
    (
        "the row names its gene by its Entrez gene id alone, and the pack keys a gene by its symbol"
    ): frozenset(),
}
"""Checks whose class is not their code's: the reference checks nothing behind its sniffer's tab,
accepts a file with no ``Hugo_Symbol`` column, skips a row whose Entrez id ``int()`` does not read
and loads a row named by its Entrez id alone, so these are always stricter."""
META_CODES = frozenset(
    {
        "DUPLICATE_PROFILE_META",
        "DUPLICATE_STABLE_ID",
        "META_VALUE",
        "META_FILE",
        "META_FIELD",
        "STUDY_MISMATCH",
        "DATA_FILE",
    }
)
META_CLASS = frozenset(
    {"type-meta", "duplicate-stable-id", "meta-type", "meta-key", "study-mismatch"}
)
"""A refusal of a CNA meta is the same outcome as any error the reference reports about that meta
file: which of its checks the reference names first is its own order."""
LIST_FILES = "case_lists/"
TWO_KINDS: Mapping[str, str] = {
    (
        "the file starts with a byte order mark, which cBioPortal reads into the first column's "
        "name"
    ): "(C4) or (C5)",
}
"""The check whose kind is the file's, not the check's: a byte order mark joins the line the
reference takes for its header, and where neither it nor the five lines after it holds a tab the
reference's sniffer gives up and checks nothing (``cna_oracle.reference_checks_nothing``), so the
refusal is stricter there and the same outcome otherwise. ``explain`` holds its kind to that
predicate."""


PD_KEY = "pd_annotations_filename"
PD_SITE = f" {PD_KEY}"
"""A PD value's refusal (phase 9's rule on ``pd_annotations_filename``) is its own item: its code
is labelled ``DATA_FILE pd_annotations_filename`` (``label_of``), and its kind is judged by file,
``same outcome`` where the reference reports any error, or crashes, in the file the value names
(review r2 MJ-A 3)."""


def _spellings(value: str) -> set[str]:
    """A path value as the reference may name it: as written, normalised, without a trailing
    slash."""
    return {value, posixpath.normpath(value), value.rstrip("/")} if value else set()


def _metas(files: Mapping[str, Any]) -> dict[str, dict[str, str]]:
    return {
        name: oracle().parse_meta(content)
        for name, content in files.items()
        if "/" not in name
        and isinstance(content, bytes)
        and oracle().META_NAME.search(name)
        and not name.startswith(".")
        and not name.endswith("~")
        and not name.startswith("ONCOKB_IMPORT_BACKUP")
    }


def _discrete(parsed: Mapping[str, str]) -> bool:
    return (parsed.get("genetic_alteration_type"), parsed.get("datatype")) == (
        "COPY_NUMBER_ALTERATION",
        "DISCRETE",
    )


def _kind(parsed: Mapping[str, str]) -> str | None:
    return oracle().classify(parsed)


def never_metas(files: Mapping[str, Any]) -> set[str]:
    """The metas of a C-table type the pack never imports: every type outside ``meta.IMPORTED``
    but the study meta (review r4 m1; D409 (25))."""
    imported = oracle().IMPORTED_TYPES | {"STUDY", None}
    return {name for name, parsed in _metas(files).items() if _kind(parsed) not in imported}


def never_copy_number(files: Mapping[str, Any]) -> set[str]:
    """The never-imported copy-number metas (CE7)."""
    metas = _metas(files)
    return {
        name
        for name in never_metas(files)
        if metas[name].get("genetic_alteration_type") == "COPY_NUMBER_ALTERATION"
    }


def unread(files: Mapping[str, Any]) -> set[str]:
    """The files the pack never reads, as the reference may name them: each discrete CNA meta's PD
    value and the data file of each meta of a type the pack never imports (``never_metas``), less
    every file an imported meta names (the matrix, the clinical files, the MAF, the SV file),
    which the pack reads."""
    metas = _metas(files)
    never = never_metas(files)
    found: set[str] = set()
    read: set[str] = set()
    for name, parsed in metas.items():
        if _discrete(parsed):
            found |= _spellings(parsed.get(PD_KEY, ""))
        if name in never:
            found |= _spellings(parsed.get("data_filename", ""))
        elif _kind(parsed) in oracle().IMPORTED_TYPES:
            read |= _spellings(parsed.get("data_filename", ""))
    return found - read


def label_of(found: str) -> str:
    """A refusal's code as its item names it: a PD value's ``DATA_FILE`` is its own."""
    code = found.partition("|")[0]
    reason = found.partition("|")[2]
    return code + PD_SITE if f" a {PD_KEY} that " in reason else code


def _named(file: str, names: set[str]) -> bool:
    return file in names or posixpath.basename(file) in names


def _about(cell: str, found: str) -> set[str]:
    """The files a refusal on the cell is about: for a case list's refusal, the case lists; for a
    PD value's, the file it names; else the CNA metas, each file they name (as written and
    normalised), the CNA file and ``-``, and the meta file the reference names for a meta error,
    less every file the pack never reads (``unread``): an error there decides no refusal's kind."""
    files = corpus().CELLS[cell]
    if found.partition("|")[2].startswith("lists an id"):
        return {name for name in files if name.startswith(LIST_FILES)}
    if label_of(found).endswith(PD_SITE):
        return {
            name
            for parsed in _metas(files).values()
            if _discrete(parsed)
            for spelled in _spellings(parsed.get(PD_KEY, ""))
            for name in (spelled, posixpath.basename(spelled))
            if name
        }
    about = {"-", corpus().CNA_FILE}
    for name, parsed in _metas(files).items():
        if _discrete(parsed):
            about |= {name, *_spellings(parsed.get("data_filename", ""))}
    return about - unread(files)


def reported(record: Mapping[str, Any]) -> list[tuple[str, str]]:
    """The reference's errors and its crash, each with the file it names (the file whose
    validation was under way, for a crash)."""
    found = [(identity, file) for identity, file in record["errors"]]
    if record["crash"]:
        found.append((CRASH, record.get("crash_file") or "-"))
    return found


@cache
def kind(cell: str, found: str) -> str:
    """D409's one definition, per class (``SAME_AS``), over the errors and the crash in the files
    the refusal is about; a PD value's refusal by file alone (``PD_SITE``)."""
    about = _about(cell, found)
    errors = {identity for identity, file in reported(REFERENCE[cell]) if _named(file, about)}
    if label_of(found).endswith(PD_SITE):
        return SAME if errors else STRICTER
    code = found.partition("|")[0].removeprefix("onco.")
    reason = found.partition("|")[2]
    same_as = SAME_AS_BY_REASON.get(reason, SAME_AS.get(code, frozenset()))
    if code in META_CODES and _errs_in_a_meta(cell):
        return SAME
    return SAME if errors & same_as else STRICTER


def _errs_in_a_meta(cell: str) -> bool:
    """Review r4 m2: the reference reports an error, or crashes, in a meta file of the cell: a
    meta refusal is then the same outcome, whatever the error (a rule, not a list of
    identities)."""
    metas = set(_metas(corpus().CELLS[cell]))
    return any(_named(file, metas) for _, file in reported(REFERENCE[cell]))


INVENTORY: frozenset[tuple[str, str, str, str]] = frozenset(
    {
        ("cna", "onco.BLANK_LINE", "the line is blank", SAME),
        (
            "cna",
            "onco.BYTE_ORDER_MARK",
            (
                "the file starts with a byte order mark, which cBioPortal reads into the first "
                "column's name"
            ),
            SAME,
        ),
        (
            "cna",
            "onco.BYTE_ORDER_MARK",
            (
                "the file starts with a byte order mark, which cBioPortal reads into the first "
                "column's name"
            ),
            STRICTER,
        ),
        (
            "cna",
            "onco.CNA_VALUE",
            "the value is none cBioPortal reads as a discrete copy number",
            SAME,
        ),
        ("cna", "onco.COLUMN_NAME", "a gene column comes after a sample column", SAME),
        ("cna", "onco.COMMENT_LINE", "a data line starts with '#'", SAME),
        ("cna", "onco.CONTROL_CHARACTER", "the field holds a control character", STRICTER),
        (
            "cna",
            "onco.DATA_FILE",
            (
                "is a discrete copy-number meta file whose data_filename names no regular file "
                "in the study's directory"
            ),
            SAME,
        ),
        (
            "cna",
            "onco.DATA_FILE",
            (
                "has a data_filename that names a discrete copy-number file whose name is not "
                "Unicode text"
            ),
            STRICTER,
        ),
        ("cna", "onco.DUPLICATE_COLUMN", "the column name repeats an earlier's", SAME),
        (
            "cna",
            "onco.DUPLICATE_GENE",
            "cBioPortal keys the gene as an earlier row's and keeps that row alone",
            STRICTER,
        ),
        ("cna", "onco.DUPLICATE_GENE", "the gene symbol is an earlier row's", STRICTER),
        (
            "cna",
            "onco.DUPLICATE_PROFILE_META",
            "is a second discrete copy-number meta file",
            STRICTER,
        ),
        ("cna", "onco.ENTREZ_VALUE", "the Entrez gene id is not positive", SAME),
        (
            "cna",
            "onco.ENTREZ_VALUE",
            "the Entrez gene id is not an integer, so cBioPortal would not load the row",
            STRICTER,
        ),
        ("cna", "onco.LINE_ENDS", "the file's line ends are not all of one kind, or none", SAME),
        ("cna", "onco.LONG_FIELD", "the field is longer than a cell may be", SAME),
        (
            "cna",
            "onco.META_VALUE",
            "holds a stable_id that a discrete copy-number meta may not",
            SAME,
        ),
        (
            "cna",
            "onco.MISSING_COLUMN",
            "the file has neither a Hugo_Symbol nor an Entrez_Gene_Id column before its samples",
            SAME,
        ),
        ("cna", "onco.MISSING_COLUMN", "the file has no sample column", SAME),
        (
            "cna",
            "onco.MISSING_COLUMN",
            "the file has no Hugo_Symbol column, which the pack keys a gene by",
            STRICTER,
        ),
        (
            "cna",
            "onco.MISSING_COLUMN",
            "the line of column names and the five lines after it hold no tab",
            STRICTER,
        ),
        ("cna", "onco.NOT_UTF8", "the line is not UTF-8", SAME),
        (
            "cna",
            "onco.NO_CNA_LIST",
            (
                "is a discrete copy-number meta file, and no case list's stable_id is the study "
                "id followed by _cna"
            ),
            SAME,
        ),
        ("cna", "onco.NO_GENE", "the row names its gene by neither symbol nor id", SAME),
        (
            "cna",
            "onco.NO_GENE",
            (
                "the row names its gene by its Entrez gene id alone, and the pack keys a gene "
                "by its symbol"
            ),
            STRICTER,
        ),
        ("cna", "onco.NO_HEADER", "the file has no line of column names", SAME),
        (
            "cna",
            "onco.PADDED_VALUE",
            (
                "the Entrez gene id has whitespace around it, which cBioPortal strips and the "
                "pack does not read"
            ),
            STRICTER,
        ),
        (
            "cna",
            "onco.PADDED_VALUE",
            (
                "the gene symbol has whitespace around it, which cBioPortal strips and the "
                "published column would keep"
            ),
            STRICTER,
        ),
        (
            "cna",
            "onco.PADDED_VALUE",
            (
                "the value has whitespace around it, which cBioPortal strips and the published "
                "column would keep"
            ),
            STRICTER,
        ),
        ("cna", "onco.REPEATED_HEADER", "the line repeats the column names", SAME),
        (
            "cna",
            "onco.ROW_WIDTH",
            "the line does not have as many fields as there are column names",
            SAME,
        ),
        (
            "cna",
            "onco.TOO_MANY_COLUMNS",
            "the file has more columns than its gene columns and the sample file's samples",
            SAME,
        ),
        ("cna", "onco.UNKNOWN_SAMPLE", "the sample id is no sample id of the sample file", SAME),
        (
            "study",
            "onco.DATA_FILE",
            "has a data_filename that is not a file name M4.2a accepts",
            SAME,
        ),
        (
            "study",
            "onco.DATA_FILE",
            (
                "{} a {} that names a meta file or a clinical data file, exactly or under case "
                "and normalisation folding"
            ),
            SAME,
        ),
        (
            "study",
            "onco.DATA_FILE",
            "has a data_filename that is not a file name M4.2a accepts",
            STRICTER,
        ),
        (
            "study",
            "onco.DATA_FILE",
            (
                "{} a {} that names a meta file or a clinical data file, exactly or under case "
                "and normalisation folding"
            ),
            STRICTER,
        ),
        (
            "study",
            "onco.DATA_FILE",
            "{} a {} that names a symlink, exactly or under case and normalisation folding",
            STRICTER,
        ),
        *(
            ("study", "onco.DATA_FILE" + PD_SITE, template, kind_)
            for template in (
                "{} a {} that is not a file name M4.2a accepts",
                "{} a {} that names a symlink, exactly or under case and normalisation folding",
                (
                    "{} a {} that names a meta file or a clinical data file, exactly or under case "
                    "and normalisation folding"
                ),
            )
            for kind_ in (SAME, STRICTER)
        ),
        ("study", "onco.DUPLICATE_STABLE_ID", "repeats another meta file's stable_id", SAME),
        ("meta", "onco.META_FIELD", "lacks the key {}", SAME),
        ("meta", "onco.META_FILE", "has no meta file type cBioPortal defines", SAME),
        ("study", "onco.STUDY_MISMATCH", "names another study than the study meta", SAME),
        (
            "caselists",
            "onco.UNKNOWN_SAMPLE",
            "lists an id that is no sample id of the sample file",
            SAME,
        ),
    }
)
"""The inventory of the discrete copy-number checks and of the other refusals the corpus reaches
(literal, keyed by module, code, reason and kind), written by hand. The ``cna`` module's templates
are exactly the walker's, less ``HELD`` (``test_the_inventory_is_the_code_s``); the other modules'
are D409's and D444's sites a CNA cell reaches, which slice 3 does not change
(``test_cna.test_the_shared_modules_are_main_s``). A check deleted, added or changed in kind
changes this set and D446 too: a visible edit."""
HELD: Mapping[str, tuple[str, str, str]] = {
    "the study's files have more cells than an import may": (
        "onco.TOO_MANY_CELLS",
        STRICTER,
        "test_the_running_count_is_the_core_s_and_refuses_at_its_first_overflow",
    ),
}
"""The checks no corpus cell reaches, by template: the cell bound, which a small study cannot pass,
held by the named test of ``test_cna.py``."""

_ITEMS: tuple[tuple[str, str], ...] = (
    ("NOT_UTF8", SAME),
    ("CONTROL_CHARACTER", STRICTER),
    ("NO_HEADER", SAME),
    ("BYTE_ORDER_MARK", SAME),
    ("BYTE_ORDER_MARK", STRICTER),
    ("MISSING_COLUMN", STRICTER),
    ("TOO_MANY_COLUMNS", SAME),
    ("DUPLICATE_COLUMN", SAME),
    ("COLUMN_NAME", SAME),
    ("MISSING_COLUMN", SAME),
    ("UNKNOWN_SAMPLE", SAME),
    ("BLANK_LINE", SAME),
    ("COMMENT_LINE", SAME),
    ("REPEATED_HEADER", SAME),
    ("ROW_WIDTH", SAME),
    ("LONG_FIELD", SAME),
    ("ENTREZ_VALUE", SAME),
    ("NO_GENE", SAME),
    ("ENTREZ_VALUE", STRICTER),
    ("DUPLICATE_GENE", STRICTER),
    ("CNA_VALUE", SAME),
    ("NO_GENE", STRICTER),
    ("PADDED_VALUE", STRICTER),
    ("LINE_ENDS", SAME),
    ("DUPLICATE_PROFILE_META", STRICTER),
    ("DUPLICATE_STABLE_ID", SAME),
    ("META_VALUE", SAME),
    ("META_FILE", SAME),
    ("META_FIELD", SAME),
    ("STUDY_MISMATCH", SAME),
    ("DATA_FILE", SAME),
    ("DATA_FILE", STRICTER),
    ("DATA_FILE" + PD_SITE, SAME),
    ("DATA_FILE" + PD_SITE, STRICTER),
    ("NO_CNA_LIST", SAME),
)
ITEMS: Mapping[tuple[str, str], str] = {
    (f"onco.{code}", kind_): f"(C{number}) {kind_}: [{code}]"
    for number, (code, kind_) in enumerate(_ITEMS, start=1)
}
"""Each refusal's code and kind to the head of its D446 item, numbered ``C1`` on, in the order of
the pack's refusals: the inventory's ``(code, kind)`` pairs, exactly."""


def _unopened(cell: Mapping[str, Any], file: str) -> bool:
    """CE1: the file is one the pack never reads (``unread``: a PD value, a never-imported
    copy-number meta's data file), and no regular file the reference can open (D409's (20))."""
    return file in unread(cell) and not isinstance(cell.get(file), bytes)


def _content(cell: Mapping[str, Any], file: str) -> bool:
    """CE2: the file is one the pack never reads, a regular file the reference reads (D409's (20):
    the content of a data file never imported), whatever it reports there or however it fails."""
    return file in unread(cell) and isinstance(cell.get(file), bytes)


def _never_meta(cell: Mapping[str, Any], file: str) -> bool:
    """CE3: the file is a meta of a type the pack never imports (D409's (25): a type-specific
    meta check)."""
    return file in never_metas(cell)


MARK = "\ufeff".encode()
NOWHERE = "-"
"""The file the reference names for an error it attributes to no file."""


def marked(cell: Mapping[str, Any]) -> set[str]:
    """The cell's metas and case lists a byte order mark leads (D409 (13))."""
    return {
        name
        for name, content in cell.items()
        if isinstance(content, bytes)
        and content.startswith(MARK)
        and (name.startswith(LIST_FILES) or ("/" not in name and oracle().META_NAME.search(name)))
    }


def _study_id(cell: Mapping[str, Any]) -> str:
    for parsed in _metas(cell).values():
        if "type_of_cancer" in parsed and "cancer_study_identifier" in parsed:
            return parsed["cancer_study_identifier"]
    return ""


def _held_by(cell: Mapping[str, Any], stable: str) -> tuple[set[str], set[str]]:
    """The case lists holding the ``stable_id`` ``stable``: those the reference reads, and those a
    mark leads (which it cannot)."""
    lists = {
        name: content
        for name, content in cell.items()
        if name.startswith(LIST_FILES)
        and isinstance(content, bytes)
        and not name.removeprefix(LIST_FILES).startswith(".")
        and not name.endswith("~")
    }
    holding = {
        name
        for name, content in lists.items()
        if oracle().parse_meta(content).get("stable_id") == stable
    }
    led = holding & marked(cell)
    return holding - led, led


def _needed(cell: Mapping[str, Any]) -> set[str]:
    """The files without which the reference reads neither the study nor the matrix: the study
    meta, the sample meta and the discrete CNA meta."""
    needed = set()
    for name, parsed in _metas(cell).items():
        pair = (parsed.get("genetic_alteration_type"), parsed.get("datatype"))
        if "type_of_cancer" in parsed or pair == ("CLINICAL", "SAMPLE_ATTRIBUTES"):
            needed.add(name)
        if _discrete(parsed):
            needed.add(name)
    return needed


def _mark_led(cell: Mapping[str, Any], file: str) -> bool:
    """CE4: the file is a meta or a case list a byte order mark leads, which the reference reads
    as invalid and the pack reads past (D409 (13))."""
    return file in marked(cell)


def _mark_lost(cell: Mapping[str, Any], file: str) -> bool:
    """CE5: an error the reference attributes to no file, in a cell where a mark leads a file it
    needs: the study meta, the sample meta, or the only case lists holding the ``_cna`` list's
    ``stable_id`` (D409 (13), its cascade)."""
    if file != NOWHERE:
        return False
    read, led = _held_by(cell, _study_id(cell) + "_cna")
    return bool(marked(cell) & _needed(cell)) or (bool(led) and not read)


def _no_all(cell: Mapping[str, Any], file: str) -> bool:
    """CE6: an error the reference attributes to no file, in a cell with no ``_all`` list it reads:
    the study meta does not add the global list and no case list it reads holds
    ``<study>_all`` (D409 (14))."""
    if file != NOWHERE:
        return False
    study = next((p for p in _metas(cell).values() if "type_of_cancer" in p), {})
    read, _ = _held_by(cell, _study_id(cell) + "_all")
    return study.get("add_global_case_list", "").lower() != "true" and not read


def _never_repeated(cell: Mapping[str, Any], file: str) -> bool:
    """CE7: an error the reference attributes to no file, in a cell holding two never-imported
    copy-number metas of one datatype (D409 (25))."""
    if file != NOWHERE:
        return False
    metas = _metas(cell)
    datatypes = [metas[name].get("datatype") for name in never_copy_number(cell)]
    return len(datatypes) != len(set(datatypes))


def _sample_resources(cell: Mapping[str, Any], file: str) -> bool:
    """CE8: an error the reference attributes to no file, in a cell holding a sample-resource
    meta, whose data file the reference reads with the sample file and the pack never reads
    (D409 (20))."""
    if file != NOWHERE:
        return False
    return any(_kind(_metas(cell)[name]) == "SAMPLE_RESOURCES" for name in never_metas(cell))


Exit = tuple[str, Callable[[Mapping[str, Any], str], bool]]
EXITS: tuple[Exit, ...] = (
    ("(CE1) exit 3: [a file the pack never reads, which the reference cannot open]", _unopened),
    ("(CE2) exit 3: [the content of a file the pack never reads]", _content),
    ("(CE3) exit 3: [a meta of a type the pack never imports]", _never_meta),
    ("(CE4) exit 3: [a meta or case list a byte order mark leads]", _mark_led),
    ("(CE5) exit 3: [what the reference loses past a mark on a file it needs]", _mark_lost),
    ("(CE6) exit 3: [no _all list]", _no_all),
    ("(CE7) exit 3: [a never-imported copy-number datatype twice]", _never_repeated),
    ("(CE8) exit 3: [a sample-resource meta's file read with the samples]", _sample_resources),
)
"""The exits of an accepted cell's errors and crash, by the file each names and a rule about the
cell, never by its identity (review r2 MJ-A 1, r3 MJ-1): any error or crash in a file the pack
never reads is D409's (20), any error about a never-imported copy-number meta its (25); a meta or
case list a mark leads, and what the reference loses with it, is its (13); a study with no
``_all`` list its (14); two never-imported copy-number metas of one datatype its (25)."""


def masked(cell: Mapping[str, Any]) -> bool:
    """Review r3 MJ-1: a mark leads a file the reference needs to read the matrix (the study meta,
    the sample meta or the discrete CNA meta): the reference never validates the matrix, so a
    refusal of it has no kind the reference can give; it must be an item of either kind."""
    return bool(marked(cell) & _needed(cell))


def out_of_map(written: tuple[str, str], found: str) -> bool:
    """Review r4 m4: a refusal written from a template of a shared module (``cna_checks.SHARED``:
    ``study``, ``meta``, ``importer``, ``caselists``) that no inventory item names is out of the
    CNA map, by module: the predicates give it exactly, and the judge judges no kind of it. The
    shared templates the inventory names (D409's and D444's sites a CNA cell reaches) stay in
    it."""
    module, template = written
    if module not in checks().SHARED:
        return False
    code = label_of(found)
    return not any(item[:3] == (module, code, template) for item in INVENTORY)


def outcome(onco: ModuleType, directory: Path, limits: ImportLimits | None = None) -> str:
    """``ok``; or the pack's one refusal, ``onco.<CODE>|<reason>``; or the core's codes."""
    confinement = Confinement.of(directory.parent)
    options = ImportOptions(
        dataset="study", reader=confinement, limits=limits or ImportLimits(), at=AT
    )
    try:
        run_importer(onco.PACK.importer, "onco", confinement.confine(directory), options)
    except ImportRefused as error:
        refusals = error.refusals
        if len(refusals) == 1 and str(refusals[0].code).startswith("onco."):
            return f"{refusals[0].code}|{checks().reason_of(refusals[0].message)}"
        return "+".join(sorted({str(refusal.code) for refusal in refusals}))
    return "ok"


def build(root: Path, files: Mapping[str, Any]) -> Path:
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


@cache
def template_of(found: str) -> tuple[str, str] | None:
    """The one ``(module, template)`` a pack refusal's reason is written from, or ``None``: a
    template with no field that is the reason itself before one with fields."""
    _, bar, reason = found.partition("|")
    if not bar:
        return None
    pool = [("cna", t) for t in TEMPLATES] + [("shared", t) for t in SHARED_TEMPLATES]
    exact = [(module, t) for module, t in pool if t == reason]
    if exact:
        return _module_of(exact[0])
    matched = [
        (module, t) for module, t in pool if re.fullmatch(checks().pattern(t), reason, re.DOTALL)
    ]
    return _module_of(matched[0]) if len(matched) == 1 else None


def _module_of(found: tuple[str, str]) -> tuple[str, str]:
    module, template = found
    if module == "cna":
        return found
    for name in checks().SHARED:
        if template in checks().templates(name):
            return name, template
    return found


@cache
def check_of(found: str) -> tuple[Any, ...]:
    """The CNA checks whose reasons match a refusal's."""
    _, bar, reason = found.partition("|")
    if not bar:
        return ()
    return tuple(check for check in CHECKS if check.matches(reason))


@cache
def wanted_of(cell: str) -> str:
    """The predicates' outcome of the corpus cell ``cell`` (``cna_oracle.expected``)."""
    return oracle().expected(corpus().CELLS[cell])


def explain(outcomes: Mapping[str, str], cells: Sequence[str]) -> list[str]:
    """Why the map does not explain ``outcomes`` over ``cells``, one line a problem: an outcome
    that is not the predicates' (``cna_oracle.expected``), a refusal whose ``(module, code,
    template, kind)`` is no item of the inventory, an accepted cell that crashed the reference, an
    accepted cell's error no exit's predicate holds for, a two-kind refusal whose kind is not its
    predicate's."""
    problems: list[str] = []
    for cell in cells:
        found = outcomes[cell]
        files = corpus().CELLS[cell]
        wanted = wanted_of(cell)
        if found != wanted:
            problems.append(f"{cell}: {found!r} is not the predicates' {wanted!r}")
        recorded = REFERENCE[cell]
        if found == "ok":
            for identity, file in reported(recorded):
                if not any(holds(files, file) for _, holds in EXITS):
                    if identity == CRASH:
                        problems.append(f"{cell}: accepted, and the reference crashed in {file}")
                    else:
                        problems.append(f"{cell}: accepted, {identity} in {file} has no exit")
            continue
        written = template_of(found)
        if written is None:
            problems.append(f"{cell}: {found!r} matches no template, or several")
            continue
        if out_of_map(written, found):
            continue
        item = (written[0], label_of(found), written[1], kind(cell, found))
        if masked(files):
            either = {(*item[:3], SAME), (*item[:3], STRICTER)}
            if not either & INVENTORY:
                problems.append(f"{cell}: {item[:3]} is no item of either kind")
        elif item not in INVENTORY:
            problems.append(f"{cell}: {item} is no item")
        content = files.get(corpus().CNA_FILE)
        if written[1] in TWO_KINDS and isinstance(content, bytes):
            nothing = oracle().reference_checks_nothing(content)
            if (item[3] == STRICTER) != nothing:
                problems.append(
                    f"{cell}: {item[3]} where the reference checks nothing is {nothing}"
                )
    return problems


def named(
    outcomes: Mapping[str, str],
) -> tuple[set[tuple[str, str, str, str]], set[str], dict[str, set[str]]]:
    """What the cells name: the inventory's items, the exits, and for each CNA check the items its
    refusals are."""
    items: set[tuple[str, str, str, str]] = set()
    exits: set[str] = set()
    by_check: dict[str, set[str]] = {check.id: set() for check in CHECKS}
    for cell in CELL_IDS:
        found = outcomes[cell]
        if found != "ok":
            written = template_of(found)
            files = corpus().CELLS[cell]
            if written is None or masked(files) or out_of_map(written, found):
                continue
            item = (written[0], label_of(found), written[1], kind(cell, found))
            items.add(item)
            label = f"{item[1]} {item[3]}"
            if written[1] in TWO_KINDS:
                label = f"{item[1]} {TWO_KINDS[written[1]]}"
            for check in check_of(found):
                by_check[check.id].add(label)
            continue
        for _, file in reported(REFERENCE[cell]):
            exits.update(head for head, holds in EXITS if holds(corpus().CELLS[cell], file))
    return items, exits, by_check


def judged(outcomes: Mapping[str, str]) -> list[str]:
    """The map's whole judgement: ``explain``; the inventory named exactly; every exit named; each
    CNA check named by exactly one item, but those ``HELD``."""
    problems = explain(outcomes, CELL_IDS)
    items, exits, by_check = named(outcomes)
    if items != INVENTORY:
        problems.append(f"the items named {sorted(items ^ INVENTORY)} differ")
    heads = {head for head, _ in EXITS}
    if exits != heads:
        problems.append(f"exits named {sorted(exits ^ heads)} differ")
    for check in CHECKS:
        if set(check.templates) <= set(HELD):
            continue
        if len(by_check[check.id]) != 1:
            problems.append(f"{check.id}: named by {sorted(by_check[check.id])}")
    return problems


@pytest.mark.parametrize("cell", CELL_IDS)
def test_aibi_s_outcome_is_the_recorded_one(cell: str, onco: ModuleType, tmp_path: Path) -> None:
    found = outcome(onco, build(tmp_path, corpus().CELLS[cell]))
    if os.environ.get("AIBI_RECORD_CNA_OUTCOMES"):
        recorded = json.loads(OUTCOMES_FILE.read_text()) if OUTCOMES_FILE.exists() else {}
        recorded[cell] = found
        OUTCOMES_FILE.write_text(json.dumps(dict(sorted(recorded.items())), indent=0) + "\n")
        return
    assert found == OUTCOMES[cell]


def test_every_cell_has_a_recorded_reference_outcome_and_aibi_outcome() -> None:
    assert set(REFERENCE) == set(CELL_IDS)
    assert set(OUTCOMES) == set(CELL_IDS)


def test_the_map_explains_every_cell() -> None:
    """``judged``: every outcome is the predicates', every refusal an item of its kind, every
    accepted cell's error an exit; the inventory and the exits named exactly; every check named by
    exactly one item."""
    assert judged(OUTCOMES) == []


def test_the_items_are_the_inventory_s() -> None:
    assert set(ITEMS) == {(code, kind_) for _, code, _, kind_ in INVENTORY}


def test_the_inventory_is_the_code_s() -> None:
    """The templates the walker finds in ``cna`` are exactly the inventory's and ``HELD``'s, every
    template is some check's, and the other modules' templates the inventory names are among the
    shared modules' (D409's and D444's sites, unchanged by slice 3)."""
    listed = {template for module, _, template, _ in INVENTORY if module == "cna"}
    assert listed | set(HELD) == TEMPLATES
    assert not listed & set(HELD)
    assert {t for check in CHECKS for t in check.templates} == TEMPLATES
    shared = {template for module, _, template, _ in INVENTORY if module != "cna"}
    assert shared <= SHARED_TEMPLATES


def test_every_held_check_names_the_test_that_holds_it() -> None:
    import ast

    tree = ast.parse((HERE / "test_cna.py").read_text(encoding="utf-8"))
    functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
    for template, (code, _, test) in HELD.items():
        body = functions[test].body[1:]
        constants = {
            node.value
            for statement in body
            for node in ast.walk(statement)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        }
        assert template in constants, template
        assert code in constants, template


def in_d446(template: str) -> str:
    return template.replace("{}", "\u2026")


def d446() -> str:
    text = SPEC.read_text(encoding="utf-8")
    return next(line for line in text.splitlines() if line.startswith("| D446 |"))


def test_d446_lists_the_inventory_the_items_the_exits_and_the_corpus_size() -> None:
    row = d446()
    for _, _, template, _ in INVENTORY:
        assert f"`{in_d446(template)}`" in row, template
    for template in HELD:
        assert f"`{in_d446(template)}`" in row, template
    for head in ITEMS.values():
        assert head in row, head
    for head, _ in EXITS:
        assert head in row, head
    assert f"a corpus of {len(CELL_IDS)} cells" in row
    listing = row.split("The inventory, by item:", 1)[1].split("Held outside the corpus", 1)[0]
    found = re.findall(r"\((C\d+)\) (.*?)(?= \(C\d+\) |$)", listing)
    segments = dict(found)
    for _, code, template, kind_ in INVENTORY:
        head = ITEMS[(code, kind_)].split(")")[0].lstrip("(")
        assert f"`{in_d446(template)}`" in segments[head], (head, template)


@pytest.mark.parametrize("check", [check.id for check in CHECKS])
def test_dropping_any_check_fails_the_map_alone(
    check: str, onco: ModuleType, tmp_path: Path
) -> None:
    """The drop-each-check property, over every check of ``cna``: with the check's test made
    ``False``, the cells it refused are imported again, and the map alone (``judged``, given the
    fresh outcomes) reports a problem. A check no cell reaches fails here, but those ``HELD``."""
    (found,) = [candidate for candidate in CHECKS if candidate.id == check]
    if set(found.templates) <= set(HELD):
        pytest.skip(f"held by {HELD[found.templates[0]][2]}")
    cells = [cell for cell in CELL_IDS if found in check_of(OUTCOMES[cell])]
    assert cells, f"{check}: no corpus cell reaches it"
    names = ("cna", "study", "importer")
    pack = {name: importlib.import_module(f"aibi.packs.onco.{name}") for name in names}
    with checks().dropped(found, pack):
        fresh = {
            cell: outcome(onco, build(tmp_path / str(index), corpus().CELLS[cell]))
            for index, cell in enumerate(cells)
        }
    assert judged({**OUTCOMES, **fresh}) != [], check


def test_a_narrowed_check_re_recorded_still_fails_the_map() -> None:
    """The laundering probe: the outcomes a reader narrowed to spaces alone would give (every cell
    whose value is padded by another whitespace accepted), recorded as if they were the reader's,
    still fail the map: the predicates say they must be refused."""
    narrowed = {
        cell: "ok"
        for cell, found in OUTCOMES.items()
        if found.startswith("onco.PADDED_VALUE|the value has")
        and any(
            character in json.dumps(corpus().CELLS[cell].get("data_cna.txt", b"").decode())
            for character in ("\\u00a0", "\\u3000", "\\u2029")
        )
    }
    assert narrowed
    problems = judged({**OUTCOMES, **narrowed})
    assert any("is not the predicates'" in problem for problem in problems)


def test_the_byte_order_mark_s_kind_is_held_to_its_predicate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A mark-led file refused where the reference's sniffer finds a tab (so it errs on the header)
    yet recorded with no error is reported, never taken as the stricter kind the predicate does not
    give; and one where its sniffer finds none is stricter."""
    module = sys.modules[__name__]
    found = "onco.BYTE_ORDER_MARK|" + next(iter(TWO_KINDS))
    for content, nothing in ((b"\xef\xbb\xbf" + corpus().VALID, False), (b"\xef\xbb\xbfx\n", True)):
        cells = {**corpus().CELLS, "fake:led": corpus().study(data_cna_DOT_txt=content)}
        reference = {**REFERENCE, "fake:led": {"crash": False, "errors": [], "rc": 3}}
        monkeypatch.setattr(corpus(), "CELLS", cells)
        monkeypatch.setattr(module, "REFERENCE", reference)
        kind.cache_clear()
        problems = explain({"fake:led": found}, ["fake:led"])
        assert any("where the reference checks nothing is False" in p for p in problems) is (
            not nothing
        ), content
    kind.cache_clear()


def test_every_cna_list_id_follows_its_predicate(onco: ModuleType, tmp_path: Path) -> None:
    """Review r3 MJ-2: the ``_cna`` list's ``stable_id``, its suffix spelt near
    (``cna_corpus.spellings``: padded, NFC and NFD, full-width, other cases), imported whole and
    held to the predicates: a list the pack takes for the ``_cna`` list after a normalisation, a
    strip it does not do or a case fold fails here."""
    c = corpus()
    checked = 0
    for index, (label, suffix) in enumerate(c.spellings("cna")):
        files = c.study(case_lists__cases_cna_DOT_txt=c.cna_list(suffix=suffix))
        found = outcome(onco, build(tmp_path / str(index), files))
        assert found == oracle().expected(files), (label, suffix)
        checked += 1
    assert checked > 40


def test_the_predicates_import_nothing_of_the_pack() -> None:
    """The predicates are the test's own, never ``cna``'s: ``cna_oracle.py`` imports no module of
    the pack (by AST) and names none (by text)."""
    import ast

    source = (HERE / "cna_oracle.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    names = [
        node.module if isinstance(node, ast.ImportFrom) else alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import | ast.ImportFrom)
        for alias in node.names
    ]
    assert not any(str(name).startswith("aibi") for name in names), names
    assert "aibi.packs" not in source
    assert "import_module" not in source


def test_the_earlier_walker_lists_are_unchanged() -> None:
    """``cna`` joins neither slice 1b's module lists nor slice 4's."""
    walker = checks().WALKER
    assert walker.READERS == ("mutation", "caselists")
    assert walker.OTHERS == ("study", "meta", "importer")
    assert checks().MODULES == ("cna",)
    assert _load("structural_checks").MODULES == ("structural",)


def test_the_map_reports_a_check_no_cell_names() -> None:
    for check in CHECKS:
        if set(check.templates) <= set(HELD):
            continue
        cells = [cell for cell in CELL_IDS if check in check_of(OUTCOMES[cell])]
        problems = judged({**OUTCOMES, **dict.fromkeys(cells, "ok")})
        assert f"{check.id}: named by []" in problems, check.id


def test_an_error_with_no_exit_is_a_problem_never_an_exception() -> None:
    cell = next(cell for cell in CELL_IDS if REFERENCE[cell]["errors"] and OUTCOMES[cell] != "ok")
    outcomes = {**OUTCOMES, cell: "ok"}
    assert any(cell in problem for problem in explain(outcomes, [cell]))
    named(outcomes)


def test_the_exits_are_held_to_their_predicates_by_file() -> None:
    """CE1 holds on a file the pack never reads that is no regular file, CE2 on one that is, CE3 on
    a never-imported copy-number meta; none on the matrix, a meta the pack imports, a file no meta
    names, or a PD value that names the matrix (which the pack reads)."""
    c = corpus()
    missing = c.study(meta_cna_DOT_txt=c.pd_meta("data_pd.txt"))
    present = {**missing, "data_pd.txt": c.PD}
    dotless = c.study(meta_cna_DOT_txt=c.pd_meta("pdfile"), pdfile=c.PD)
    never = c.study(meta_other_DOT_txt=c.never_meta("CONTINUOUS", "linear_CNA"))
    on_matrix = c.study(meta_cna_DOT_txt=c.pd_meta(c.CNA_FILE))
    unnamed = {**c.study(), "data_pd.txt": c.PD}
    ce1, ce2, ce3 = (holds for _, holds in EXITS[:3])
    assert ce1(missing, "data_pd.txt")
    assert not ce2(missing, "data_pd.txt")
    assert ce2(present, "data_pd.txt")
    assert not ce1(present, "data_pd.txt")
    assert ce2(dotless, "pdfile")
    assert ce1(never, "d.txt")
    assert ce3(never, "meta_other.txt")
    assert ce2({**never, "d.txt": b"x"}, "d.txt")
    for cell, file in (
        (present, c.CNA_FILE),
        (present, c.CNA_META),
        (unnamed, "data_pd.txt"),
        (on_matrix, c.CNA_FILE),
        (never, "data_clinical_sample.txt"),
        (missing, "-"),
        (missing, "nothing_named.txt"),
        (never, "case_lists"),
    ):
        assert not any(holds(cell, file) for _, holds in EXITS), file


def test_an_accepted_crash_needs_an_exit_by_its_file(monkeypatch: pytest.MonkeyPatch) -> None:
    """Review r2 T10: an accepted cell whose reference crashed is a problem unless the crash is in
    a file the pack never reads: in the matrix it is reported, in the PD file it is CE2's."""
    module = sys.modules[__name__]
    c = corpus()
    cells = {
        **c.CELLS,
        "fake:crash": {**c.study(meta_cna_DOT_txt=c.pd_meta("data_pd.txt")), "data_pd.txt": c.PD},
    }
    monkeypatch.setattr(c, "CELLS", cells)
    for file, problem in ((c.CNA_FILE, True), ("data_pd.txt", False)):
        record = {"crash": True, "crash_file": file, "errors": [], "rc": 1}
        monkeypatch.setattr(module, "REFERENCE", {**REFERENCE, "fake:crash": record})
        monkeypatch.setattr(module, "wanted_of", lambda cell: "ok")
        found = explain({"fake:crash": "ok"}, ["fake:crash"])
        assert any("the reference crashed in" in p for p in found) is problem, file


def test_an_error_in_a_file_the_pack_never_reads_decides_no_kind() -> None:
    """Review r2 MJ-A 2 (the reviewer's ``plaunder``): a matrix refused by the Reads rule beside a
    PD file the reference errs on is of the kind it is beside a valid PD file; and every corpus
    refusal's kind is the same with the errors in the files the pack never reads removed."""
    found = OUTCOMES["unread:fold_reads_pd_bad"]
    assert found == OUTCOMES["unread:fold_reads_pd_ok"]
    assert REFERENCE["unread:fold_reads_pd_bad"]["errors"]
    assert kind("unread:fold_reads_pd_bad", found) == kind("unread:fold_reads_pd_ok", found)
    for cell in CELL_IDS:
        refused = OUTCOMES[cell]
        if refused == "ok" or label_of(refused).endswith(PD_SITE):
            continue
        assert not _about(cell, refused) & unread(corpus().CELLS[cell]), cell


def test_about_leaves_out_a_never_read_file_named_like_the_matrix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Review r4 (U1): where the matrix has another name and a meta of a type the pack never
    imports names ``data_cna.txt``, that file is no file a refusal is about."""
    c = corpus()
    files = c.study(
        meta_cna_DOT_txt=c.meta(data="m.txt"),
        m_DOT_txt=c.VALID + b"\n",
        meta_other_DOT_txt=c.never_meta("CONTINUOUS", "linear_CNA", data_value=c.CNA_FILE),
    )
    monkeypatch.setattr(c, "CELLS", {**c.CELLS, "fake:named": files})
    record = {"crash": False, "errors": [["blank-line", c.CNA_FILE]], "rc": 1}
    monkeypatch.setattr(sys.modules[__name__], "REFERENCE", {**REFERENCE, "fake:named": record})
    found = "onco.BLANK_LINE|the line is blank"
    assert c.CNA_FILE in unread(files)
    assert c.CNA_FILE not in _about("fake:named", found)
    kind.cache_clear()
    assert kind("fake:named", found) == STRICTER
    kind.cache_clear()


def test_a_refusal_is_judged_on_the_files_it_is_about(monkeypatch: pytest.MonkeyPatch) -> None:
    """``kind`` counts only the errors in the files a refusal is about: a refusal of the CNA file
    whose class the reference reports in another file alone is stricter, and the same outcome once
    that error is in the CNA file (review r1 T03)."""
    module = sys.modules[__name__]
    found = "onco.BLANK_LINE|the line is blank"
    cells = {**corpus().CELLS, "fake:about": corpus().study()}
    monkeypatch.setattr(corpus(), "CELLS", cells)
    for file, wanted in (("data_other.txt", STRICTER), (corpus().CNA_FILE, SAME)):
        record = {"crash": False, "errors": [["blank-line", file]], "rc": 1}
        monkeypatch.setattr(module, "REFERENCE", {**REFERENCE, "fake:about": record})
        kind.cache_clear()
        assert kind("fake:about", found) == wanted, file
    kind.cache_clear()


def test_the_mark_exits_are_rules_about_the_cell() -> None:
    """Review r3 MJ-1: CE4 holds on a meta or case list a mark leads, never on an unmarked one or a
    marked data file; CE5 on ``-`` when a mark leads the study, sample or CNA meta or the only
    ``_cna`` list, never when another list holds that id unmarked; CE6 on ``-`` with no ``_all``
    list read; CE7 on ``-`` with two never-imported metas of one datatype."""
    c = corpus()
    mark = MARK
    _, ce4, ce5, ce6, ce7 = (holds for _, holds in EXITS[2:7])
    led = c.study(meta_cna_DOT_txt=mark + c.meta())
    assert ce4(led, c.CNA_META)
    assert not ce4(c.study(), c.CNA_META)
    assert not ce4({**c.study(), c.CNA_FILE: mark + c.VALID}, c.CNA_FILE)
    assert ce5(led, NOWHERE)
    assert not ce5(led, c.CNA_META)
    assert not ce5(c.study(), NOWHERE)
    listed = c.study(case_lists__cases_cna_DOT_txt=mark + c.cna_list())
    assert ce5(listed, NOWHERE)
    both = {**listed, "case_lists/cases_cna2.txt": c.cna_list()}
    assert not ce5(both, NOWHERE)
    patient = c.study(meta_clinical_patient_DOT_txt=mark + c.BASE.PATIENT_META)
    assert not ce5(patient, NOWHERE)
    no_all = c.BASE.STUDY.replace(b"add_global_case_list: true\n", b"")
    assert ce6(c.study(meta_study_DOT_txt=no_all), NOWHERE)
    assert not ce6(c.study(), NOWHERE)
    assert not ce6(c.study(meta_study_DOT_txt=no_all), c.CNA_META)
    all_list = c.BASE.case_list("all", ["S1"], category="all_cases_in_study")
    assert not ce6(
        c.study(meta_study_DOT_txt=no_all, case_lists__cases_all_DOT_txt=all_list), NOWHERE
    )
    twice = c.CELLS["mark:twice_seg"]
    assert ce7(twice, NOWHERE)
    assert not ce7(c.CELLS["mark:never_seg_no_all"], NOWHERE)
    assert masked(c.CELLS["mark:r3_bom_meta_badmatrix"])
    assert not masked(c.CELLS["mark:badmatrix_bom_meta_clinical_patient_txt"])
    assert not masked(listed)


def test_the_exit_rules_skip_what_the_reference_skips() -> None:
    """Review r4 m5 (J03, J06): a hidden or backup ``_all`` list is no ``_all`` list the reference
    reads, so CE6 holds without the global list; two imported or clinical metas sharing a
    datatype are no never-imported datatype twice, so CE7 does not hold."""
    c = corpus()
    no_global = c.BASE.STUDY.replace(b"add_global_case_list: true\n", b"")
    all_list = c.BASE.case_list("all", ["S1"], category="all_cases_in_study")
    _, _, _, _, _, ce6, ce7, ce8 = (holds for _, holds in EXITS)
    for name in ("case_lists/.cases_all.txt", "case_lists/cases_all.txt~"):
        hidden = {**c.study(meta_study_DOT_txt=no_global), name: all_list}
        assert ce6(hidden, NOWHERE), name
    shown = {**c.study(meta_study_DOT_txt=no_global), "case_lists/cases_all.txt": all_list}
    assert not ce6(shown, NOWHERE)
    two_samples = c.study(meta_clinical_sample2_DOT_txt=c.BASE.SAMPLE_META)
    assert not ce7(two_samples, NOWHERE)
    two_discrete = c.study(meta_cna2_DOT_txt=c.meta(stable="gistic", data="data_cna2.txt"))
    assert not ce7(two_discrete, NOWHERE)
    assert ce8(c.CELLS["type:resource_sample_valid"], NOWHERE)
    assert not ce8(c.CELLS["type:resource_patient_valid"], NOWHERE)


def test_the_oracle_s_type_tables_are_the_pack_s() -> None:
    """Review r4 m1, m3: the oracle's C tables (written from cBioPortal's) are the pack's, and its
    imported types are ``meta.IMPORTED``."""
    meta = importlib.import_module("aibi.packs.onco.meta")
    o = oracle()
    assert dict(o.PAIR_TYPES) == dict(meta.PAIR_TYPES)
    assert dict(o.RESOURCE_TYPES) == dict(meta.RESOURCE_TYPES)
    assert {k: tuple(v) for k, v in o.MANDATORY.items()} == {
        k: tuple(v) for k, v in meta.MANDATORY.items()
    }
    names = {
        meta.SAMPLE: "SAMPLE_ATTRIBUTES",
        meta.PATIENT: "PATIENT_ATTRIBUTES",
        meta.MUTATION: "MUTATION",
        meta.STRUCTURAL_VARIANT: "STRUCTURAL_VARIANT",
        meta.CNA_DISCRETE: "CNA_DISCRETE",
        meta.GENE_PANEL_MATRIX: "GENE_PANEL_MATRIX",
    }
    assert {names[kind] for kind in meta.IMPORTED} == set(o.IMPORTED_TYPES)


def test_an_unexited_error_after_an_exited_one_is_a_problem(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Review r3 minor 1 (J08): every error of an accepted cell needs an exit, not its first."""
    module = sys.modules[__name__]
    c = corpus()
    files = {**c.study(meta_cna_DOT_txt=c.pd_meta("data_pd.txt")), "data_pd.txt": c.PD}
    monkeypatch.setattr(c, "CELLS", {**c.CELLS, "fake:two": files})
    record = {
        "crash": False,
        "errors": [["pd-content", "data_pd.txt"], ["blank-line", c.CNA_FILE]],
        "rc": 1,
    }
    monkeypatch.setattr(module, "REFERENCE", {**REFERENCE, "fake:two": record})
    monkeypatch.setattr(module, "wanted_of", lambda cell: "ok")
    found = explain({"fake:two": "ok"}, ["fake:two"])
    assert found == [f"fake:two: accepted, blank-line in {c.CNA_FILE} has no exit"]


def test_the_judge_s_names_are_exact() -> None:
    """Review r3 (J04, J05): a path value's spellings are as written, normalised and without a
    trailing slash, never another case; a file is named by its name or its basename, never by a
    fold of either."""
    assert _spellings("A/./B.TXT/") == {"A/./B.TXT/", "A/B.TXT", "A/./B.TXT"}
    assert _spellings("") == set()
    assert _named("x/data_pd.txt", {"data_pd.txt"})
    assert not _named("DATA_PD.TXT", {"data_pd.txt"})
    assert not _named("data_pd.TXT", {"data_pd.txt"})


def test_the_meta_class_counts_for_a_meta_refusal_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    """Review r3 (J12): an error of the meta class in a file a matrix refusal is about makes it no
    same outcome; it does for a meta refusal."""
    module = sys.modules[__name__]
    c = corpus()
    monkeypatch.setattr(c, "CELLS", {**c.CELLS, "fake:meta": c.study()})
    record = {"crash": False, "errors": [["type-meta", c.CNA_META]], "rc": 1}
    monkeypatch.setattr(module, "REFERENCE", {**REFERENCE, "fake:meta": record})
    kind.cache_clear()
    assert kind("fake:meta", "onco.BLANK_LINE|the line is blank") == STRICTER
    found = "onco.META_VALUE|holds a stable_id that a discrete copy-number meta may not"
    assert kind("fake:meta", found) == SAME
    kind.cache_clear()


def test_the_shared_refusals_are_out_of_the_map_by_module() -> None:
    """Review r3 minor 4, r4 m4: a refusal is out of the map when its template is a shared
    module's and no inventory item names it, never a ``cna`` one; each the corpus reaches is the
    predicates' (``cna_oracle.OUT_OF_MAP``); the two the review named are pinned."""
    out = set()
    for cell in CELL_IDS:
        found = OUTCOMES[cell]
        written = template_of(found) if found != "ok" else None
        if written and out_of_map(written, found):
            out.add((written[0], found.partition("|")[0], written[1]))
    assert out
    assert all(module in checks().SHARED for module, _, _ in out)
    assert not out_of_map(("cna", "the line is blank"), "onco.BLANK_LINE|the line is blank")
    assert not out_of_map(
        ("study", "repeats another meta file's stable_id"),
        "onco.DUPLICATE_STABLE_ID|repeats another meta file's stable_id",
    )
    assert OUTCOMES["shared:meta_value_vt_mid"] == (
        "onco.META_VALUE|holds a stable_id that M4.2a does not accept"
    )
    assert OUTCOMES["shared:list_key_bom_mid"] == "onco.CASE_LIST|lacks the key stable_id"
    assert len(oracle().OUT_OF_MAP) == len(out)


def test_a_pd_value_s_refusal_is_judged_by_its_file(monkeypatch: pytest.MonkeyPatch) -> None:
    """Review r2 MJ-A 3: a PD value's refusal is its own item (``label_of``), the same outcome where
    the reference reports any error in the file the value names and stricter otherwise, never by
    an error in the matrix; and ``DATA_FILE``'s class holds no PD identity."""
    module = sys.modules[__name__]
    c = corpus()
    found = "onco.DATA_FILE|has a pd_annotations_filename that is not a file name M4.2a accepts"
    assert label_of(found) == "onco.DATA_FILE" + PD_SITE
    assert label_of(found.replace(PD_KEY, "data_filename")) == "onco.DATA_FILE"
    assert "pd-content" not in SAME_AS["DATA_FILE"]
    cells = {**c.CELLS, "fake:pd": c.study(meta_cna_DOT_txt=c.pd_meta("./data_pd.txt"))}
    monkeypatch.setattr(c, "CELLS", cells)
    for file, wanted in (("data_pd.txt", SAME), (c.CNA_FILE, STRICTER), ("x.txt", STRICTER)):
        record = {"crash": False, "errors": [["no-gene", file]], "rc": 1}
        monkeypatch.setattr(module, "REFERENCE", {**REFERENCE, "fake:pd": record})
        kind.cache_clear()
        assert kind("fake:pd", found) == wanted, file
    kind.cache_clear()


def published_cells() -> list[str]:
    return [cell for cell in CELL_IDS if OUTCOMES.get(cell) == "ok"]


@pytest.mark.parametrize("cell", published_cells())
def test_an_accepted_cell_publishes(cell: str, world: Any, registry: PackRegistry) -> None:
    directory = build(world.root / "cell", corpus().CELLS[cell])
    world.publish("study", directory, registry, pack="onco")

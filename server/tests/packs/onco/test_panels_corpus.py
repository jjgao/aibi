"""The gene panel matrix corpus against the reference's recorded outcomes and D447's predicates
(M4.2b-2a, D447).

Every cell of ``panels_corpus.py`` is imported through ``PACK.importer`` and the core's checks of a
pack's result (``run_importer``), and its outcome is held four ways:

- **per cell, to its recorded outcome** (``panels_outcomes.json``), the onco code asserted;
- **per cell, to the predicates** (``panels_oracle.expected``): the outcome is the first item, in
  the pack's refusal order, whose predicate over the cell holds, ``ok`` when none does. The
  predicates are written in the test from the reference and D447, never read from ``panels.py``,
  so a check narrowed in the reader and its outcomes re-recorded still fails here;
- **per cell, to the reference** (``panels_reference.json``, each cell's outcome on
  cbioportal-core 47890bb, offline, not relaxed, by error identity and the file it names, and the
  exception of a crash): a refusal's kind is D409's one definition, ``same outcome`` where the
  reference reports an error of the refusal's own class (``SAME_AS``) in a file the refusal is
  about, ``stricter`` otherwise, and ``(module, code, reason, kind)`` must be an item of the literal
  ``INVENTORY``; an accepted cell's every error and crash must be explained by an exit whose
  predicate holds over the file it names (``EXITS``, by file and by rule, never by identity);
- **through the store**, for the accepted cells: published.

To record a new cell's outcome on the reference, run the scratch harness D447 names; to record
aibi's, run this module with ``AIBI_RECORD_PANELS_OUTCOMES=1`` and review the difference.
"""

import ast
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
REFERENCE: Mapping[str, Any] = json.loads((HERE / "panels_reference.json").read_text())
OUTCOMES_FILE = HERE / "panels_outcomes.json"
OUTCOMES: Mapping[str, str] = (
    json.loads(OUTCOMES_FILE.read_text()) if OUTCOMES_FILE.exists() else {}
)
AT = "2026-01-01T00:00:00Z"
SAME = "same outcome"
STRICTER = "stricter"
CRASH = "crash"


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"{name}_panels_test", HERE / f"{name}.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@cache
def corpus() -> ModuleType:
    return _load("panels_corpus")


@cache
def checks() -> ModuleType:
    return _load("panels_checks")


@cache
def oracle() -> ModuleType:
    return _load("panels_oracle")


CELL_IDS = sorted(corpus().CELLS)
CHECKS: tuple[Any, ...] = tuple(checks().every_check())
TEMPLATES: frozenset[str] = frozenset(
    template for module in checks().MODULES for template in checks().templates(module)
)
OTHER_MODULES = (*checks().SHARED, *checks().READERS)
OTHER_TEMPLATES: Mapping[str, frozenset[str]] = {
    module: frozenset(checks().templates(module)) for module in OTHER_MODULES
}
MATRIX = "data_gene_panel_matrix.txt"

HEADER = frozenset(
    {
        "duplicate-column",
        "header-order",
        "stable-id-unknown",
        "gene-panel-column",
        "header-cascade",
        CRASH,
    }
)
"""Every error the reference reports on the matrix's header (V:612-615, V:3376-3389, V:934-946)
and its cascade, and its crashes in the header's parse and check (V:511, V:3363-3373): one class,
so that a header refusal is the same outcome wherever the reference errs on the header, whichever
error it names first."""
SAME_AS: Mapping[str, frozenset[str]] = {
    "NOT_UTF8": frozenset({"not-utf8"}),
    "CONTROL_CHARACTER": frozenset(),
    "NO_HEADER": frozenset({"no-header"}),
    "BYTE_ORDER_MARK": HEADER,
    "MISSING_COLUMN": HEADER,
    "STABLE_ID_KEYS": frozenset({CRASH}),
    "TOO_MANY_COLUMNS": HEADER,
    "DUPLICATE_COLUMN": HEADER,
    "COLUMN_NAME": HEADER,
    "BLANK_LINE": frozenset({"blank-line", CRASH}),
    "COMMENT_LINE": frozenset({"comment-line", CRASH}),
    "REPEATED_HEADER": frozenset({"repeated-header", CRASH}),
    "ROW_WIDTH": frozenset({"row-width", CRASH}),
    "LONG_FIELD": frozenset({CRASH}),
    "REQUIRED_VALUE": frozenset({"blank-cell"}),
    "DUPLICATE_SAMPLE": frozenset({"duplicate-sample"}),
    "UNKNOWN_SAMPLE": frozenset({"sample-undefined"}),
    "NOT_SEQUENCED": frozenset({"not-sequenced"}),
    "PADDED_VALUE": frozenset(),
    "LINE_ENDS": frozenset({"line-ends"}),
    "DUPLICATE_PROFILE_META": frozenset({"second-matrix"}),
    "DATA_FILE": frozenset(
        {"open-fail", "header-order", "header-cascade", "stable-id-unknown", "no-gene-column"}
        | {"sample-undefined", "missing-column"}
    ),
    "DUPLICATE_STABLE_ID": frozenset({"duplicate-stable-id"}),
    "META_FILE": frozenset({"meta-type"}),
    "META_FIELD": frozenset({"meta-key"}),
    "NO_SEQUENCED_LIST": frozenset({"no-sequenced-list"}),
}
"""Each code's class among the reference's error identities (``crash`` for a crash): a refusal is
``same outcome`` only when the cell's recorded errors hold one of its code's, in a file the refusal
is about, and ``stricter`` otherwise. ``DATA_FILE``'s class is the reference failing to open the
named file or reading a file in the wrong role (a MAF, a clinical file, an SV or CNA file as a
matrix, the matrix as a timeline or a CNA file). A control character is always stricter (the
reference reads it as content). Every data line's structure code holds the crash: the reference's
csv reader fails on a field over its limit on any line, and a short row crashes its check of the
MAF's column (V:3418). The dictionary's crash item (``STABLE_ID_KEYS``) is the crash alone."""
SAME_AS_BY_REASON: Mapping[str, frozenset[str]] = {
    "the line of column names and the five lines after it hold no tab": frozenset(),
    "the column name is longer than a descriptor's string": frozenset(),
    "the column name's id is another column's": frozenset(),
}
"""Checks whose class is not their code's: the reference checks nothing behind its sniffer's tab
and reads a long name or two names whose column ids collide, so these are always stricter."""
META_CODES = frozenset(
    {"DUPLICATE_STABLE_ID", "META_FILE", "META_FIELD", "DUPLICATE_PROFILE_META", "DATA_FILE"}
)
LIST_FILES = "case_lists/"
TWO_KINDS: Mapping[str, str] = {
    (
        "the file starts with a byte order mark, which cBioPortal reads into the first column's "
        "name"
    ): "(P11) or (P12)",
}
"""The check whose kind is the file's: a byte order mark joins the line the reference takes for
its header, and where neither it nor the five lines after it holds a tab the reference's sniffer
checks nothing (``panels_oracle.reference_checks_nothing``), so the refusal is stricter there and
the same outcome otherwise. ``explain`` holds its kind to that predicate."""


def _spellings(value: str) -> set[str]:
    return {value, posixpath.normpath(value), value.rstrip("/")} if value else set()


def _metas(files: Mapping[str, Any]) -> dict[str, dict[str, str]]:
    shared = oracle().SHARED
    return {
        name: shared.parse_meta(content)
        for name, content in files.items()
        if "/" not in name
        and isinstance(content, bytes)
        and shared.META_NAME.search(name)
        and not name.startswith(".")
        and not name.endswith("~")
        and not name.startswith("ONCOKB_IMPORT_BACKUP")
    }


def _kind(parsed: Mapping[str, str]) -> str | None:
    return oracle().SHARED.classify(parsed)


IMPORTED_TYPES = frozenset(
    {
        "SAMPLE_ATTRIBUTES",
        "PATIENT_ATTRIBUTES",
        "MUTATION",
        "STRUCTURAL_VARIANT",
        "CNA_DISCRETE",
        "GENE_PANEL_MATRIX",
    }
)
"""The types whose data files the pack imports (``meta.IMPORTED``, D447)."""


def _matrix(parsed: Mapping[str, str]) -> bool:
    return _kind(parsed) == "GENE_PANEL_MATRIX"


def never_metas(files: Mapping[str, Any]) -> set[str]:
    """The metas of a C-table type the pack never imports: every type outside ``meta.IMPORTED``
    but the study meta (D409 (25))."""
    imported = IMPORTED_TYPES | {"STUDY", None}
    return {name for name, parsed in _metas(files).items() if _kind(parsed) not in imported}


def unread(files: Mapping[str, Any]) -> set[str]:
    """The files the pack never reads, as the reference may name them: the data file of each meta
    of a type the pack never imports, less every file an imported meta names."""
    metas = _metas(files)
    never = never_metas(files)
    found: set[str] = set()
    read: set[str] = set()
    for name, parsed in metas.items():
        if name in never:
            found |= _spellings(parsed.get("data_filename", ""))
        elif _kind(parsed) in IMPORTED_TYPES:
            read |= _spellings(parsed.get("data_filename", ""))
    return found - read


def _named(file: str, names: set[str]) -> bool:
    return file in names or posixpath.basename(file) in names


def _about(cell: str, found: str) -> set[str]:
    """The files a refusal on the cell is about: for a case list's refusal, the case lists; else
    the matrix metas, each file they name (as written and normalised), the matrix and ``-``, less
    every file the pack never reads (``unread``): an error there decides no refusal's kind."""
    files = corpus().CELLS[cell]
    if found.partition("|")[2].startswith("lists an id"):
        return {name for name in files if name.startswith(LIST_FILES)}
    about = {"-", MATRIX}
    for name, parsed in _metas(files).items():
        if _matrix(parsed):
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
    the refusal is about; a meta refusal the same outcome where the reference errs in a meta."""
    about = _about(cell, found)
    errors = {identity for identity, file in reported(REFERENCE[cell]) if _named(file, about)}
    code = found.partition("|")[0].removeprefix("onco.")
    reason = found.partition("|")[2]
    same_as = SAME_AS_BY_REASON.get(reason, SAME_AS.get(code, frozenset()))
    if code in META_CODES and _errs_in_a_meta(cell):
        return SAME
    return SAME if errors & same_as else STRICTER


def _errs_in_a_meta(cell: str) -> bool:
    """The reference reports an error, or crashes, in a meta file of the cell: a meta refusal is
    then the same outcome, whatever the error (a rule, not a list of identities)."""
    metas = set(_metas(corpus().CELLS[cell]))
    return any(_named(file, metas) for _, file in reported(REFERENCE[cell]))


READS = (
    "{} a {} that names a meta file or a clinical data file, exactly or under case and "
    "normalisation folding"
)
INVENTORY: frozenset[tuple[str, str, str, str]] = frozenset(
    {
        ("meta", "onco.META_FILE", "has no meta file type cBioPortal defines", SAME),
        ("meta", "onco.META_FIELD", "lacks the key {}", SAME),
        ("study", "onco.DUPLICATE_STABLE_ID", "repeats another meta file's stable_id", SAME),
        (
            "panels",
            "onco.DUPLICATE_PROFILE_META",
            "is a second gene panel matrix meta file",
            SAME,
        ),
        (
            "panels",
            "onco.DATA_FILE",
            (
                "is a gene panel matrix meta file whose data_filename names no regular file in "
                "the study's directory"
            ),
            SAME,
        ),
        ("study", "onco.DATA_FILE", READS, SAME),
        (
            "study",
            "onco.DATA_FILE",
            "{} a {} that names a symlink, exactly or under case and normalisation folding",
            SAME,
        ),
        ("study", "onco.DATA_FILE", READS, STRICTER),
        (
            "study",
            "onco.DATA_FILE",
            "has a data_filename that is not a file name M4.2a accepts",
            STRICTER,
        ),
        (
            "panels",
            "onco.DATA_FILE",
            "has a data_filename that names a gene panel matrix whose name is not Unicode text",
            STRICTER,
        ),
        (
            "importer",
            "onco.NO_SEQUENCED_LIST",
            (
                "is a mutation meta file, and no case list's stable_id is the study id followed "
                "by _sequenced"
            ),
            SAME,
        ),
        ("panels", "onco.NOT_UTF8", "the line is not UTF-8", SAME),
        (
            "panels",
            "onco.CONTROL_CHARACTER",
            "the field holds a control character, a line separator or a byte order mark",
            STRICTER,
        ),
        ("panels", "onco.NO_HEADER", "the file has no line of column names", SAME),
        (
            "panels",
            "onco.BYTE_ORDER_MARK",
            (
                "the file starts with a byte order mark, which cBioPortal reads into the first "
                "column's name"
            ),
            SAME,
        ),
        (
            "panels",
            "onco.BYTE_ORDER_MARK",
            (
                "the file starts with a byte order mark, which cBioPortal reads into the first "
                "column's name"
            ),
            STRICTER,
        ),
        (
            "panels",
            "onco.MISSING_COLUMN",
            "the line of column names and the five lines after it hold no tab",
            STRICTER,
        ),
        (
            "panels",
            "onco.STABLE_ID_KEYS",
            (
                "a meta file holds a stable_id without the keys cBioPortal's check of the matrix "
                "reads (genetic_alteration_type, and datatype beside MUTATION_EXTENDED), which "
                "crashes it"
            ),
            SAME,
        ),
        (
            "panels",
            "onco.TOO_MANY_COLUMNS",
            "the file has more columns than the meta files have stable_ids and SAMPLE_ID",
            SAME,
        ),
        ("panels", "onco.DUPLICATE_COLUMN", "the column name repeats an earlier's", SAME),
        ("panels", "onco.COLUMN_NAME", "the first column is not SAMPLE_ID", SAME),
        ("panels", "onco.COLUMN_NAME", "the column name is no meta file's stable_id", SAME),
        (
            "panels",
            "onco.COLUMN_NAME",
            "the column is a profile's whose meta file holds gene_panel",
            SAME,
        ),
        (
            "panels",
            "onco.COLUMN_NAME",
            "the column name is longer than a descriptor's string",
            STRICTER,
        ),
        ("panels", "onco.COLUMN_NAME", "the column name's id is another column's", STRICTER),
        ("panels", "onco.BLANK_LINE", "the line is blank", SAME),
        ("panels", "onco.COMMENT_LINE", "a data line starts with '#'", SAME),
        ("panels", "onco.REPEATED_HEADER", "the line repeats the column names", SAME),
        (
            "panels",
            "onco.ROW_WIDTH",
            "the line does not have as many fields as there are column names",
            SAME,
        ),
        ("panels", "onco.LONG_FIELD", "the field is longer than a cell may be", SAME),
        ("panels", "onco.REQUIRED_VALUE", "the cell is blank", SAME),
        ("panels", "onco.DUPLICATE_SAMPLE", "the sample id is an earlier row's", SAME),
        (
            "panels",
            "onco.UNKNOWN_SAMPLE",
            "the row's sample id is no sample id of the sample file",
            SAME,
        ),
        (
            "panels",
            "onco.NOT_SEQUENCED",
            "the sample has a mutation panel and is not in the study's _sequenced case list",
            SAME,
        ),
        (
            "panels",
            "onco.PADDED_VALUE",
            (
                "the value has whitespace around it, which cBioPortal reads as written and the "
                "published column would keep"
            ),
            STRICTER,
        ),
        (
            "panels",
            "onco.LINE_ENDS",
            "the file's line ends are not all of one kind, or none",
            SAME,
        ),
    }
)
"""The inventory of the gene panel matrix's checks and of the other refusals of the matrix and its
meta the corpus reaches (literal, keyed by module, code, reason and kind), written by hand. The
``panels`` module's templates are exactly the walker's, less ``HELD``
(``test_the_inventory_is_the_code_s``); the other modules' are D409's and D444's sites a matrix
cell reaches about the matrix or its meta, which slice 2a does not change (the census and the
sites pin of ``test_structural.py``). A check deleted, added or changed in kind changes this set
and D447 too: a visible edit."""
HELD: Mapping[str, tuple[str, str, str]] = {
    "the study's files have more cells than an import may": (
        "onco.TOO_MANY_CELLS",
        STRICTER,
        "test_the_running_count_is_the_core_s_and_refuses_at_its_first_overflow",
    ),
}
"""The checks no corpus cell reaches, by template: the cell bound, which a small study cannot pass,
held by the named test of ``test_panels.py``."""

_ITEMS: tuple[tuple[str, str], ...] = (
    ("META_FILE", SAME),
    ("META_FIELD", SAME),
    ("DUPLICATE_STABLE_ID", SAME),
    ("DUPLICATE_PROFILE_META", SAME),
    ("DATA_FILE", SAME),
    ("DATA_FILE", STRICTER),
    ("NO_SEQUENCED_LIST", SAME),
    ("NOT_UTF8", SAME),
    ("CONTROL_CHARACTER", STRICTER),
    ("NO_HEADER", SAME),
    ("BYTE_ORDER_MARK", SAME),
    ("BYTE_ORDER_MARK", STRICTER),
    ("MISSING_COLUMN", STRICTER),
    ("STABLE_ID_KEYS", SAME),
    ("TOO_MANY_COLUMNS", SAME),
    ("DUPLICATE_COLUMN", SAME),
    ("COLUMN_NAME", SAME),
    ("COLUMN_NAME", STRICTER),
    ("BLANK_LINE", SAME),
    ("COMMENT_LINE", SAME),
    ("REPEATED_HEADER", SAME),
    ("ROW_WIDTH", SAME),
    ("LONG_FIELD", SAME),
    ("REQUIRED_VALUE", SAME),
    ("DUPLICATE_SAMPLE", SAME),
    ("UNKNOWN_SAMPLE", SAME),
    ("NOT_SEQUENCED", SAME),
    ("PADDED_VALUE", STRICTER),
    ("LINE_ENDS", SAME),
)
ITEMS: Mapping[tuple[str, str], str] = {
    (f"onco.{code}", kind_): f"(P{number}) {kind_}: [{code}]"
    for number, (code, kind_) in enumerate(_ITEMS, start=1)
}
"""Each refusal's code and kind to the head of its D447 item, numbered ``P1`` on, in the order of
the pack's refusals: the inventory's ``(code, kind)`` pairs, exactly."""


MARK = "\ufeff".encode()
NOWHERE = "-"


def marked(cell: Mapping[str, Any]) -> set[str]:
    """The cell's metas and case lists a byte order mark leads (D409 (13))."""
    return {
        name
        for name, content in cell.items()
        if isinstance(content, bytes)
        and content.startswith(MARK)
        and (
            name.startswith(LIST_FILES)
            or ("/" not in name and oracle().SHARED.META_NAME.search(name))
        )
    }


def _needed(cell: Mapping[str, Any]) -> set[str]:
    """The metas without which the reference reads neither the study nor the matrix: the study
    meta, the sample meta and the matrix meta."""
    needed = set()
    for name, parsed in _metas(cell).items():
        pair = (parsed.get("genetic_alteration_type"), parsed.get("datatype"))
        if "type_of_cancer" in parsed or pair == ("CLINICAL", "SAMPLE_ATTRIBUTES"):
            needed.add(name)
        if _matrix(parsed):
            needed.add(name)
    return needed


def _mark_led(cell: Mapping[str, Any], file: str) -> bool:
    """PE1: the file is a meta or a case list a byte order mark leads, which the reference reads
    as invalid and the pack reads past (D409 (13))."""
    return file in marked(cell)


def _mark_lost(cell: Mapping[str, Any], file: str) -> bool:
    """PE2: an error the reference attributes to no file, in a cell where a mark leads a meta it
    needs (D409 (13), its cascade)."""
    return file == NOWHERE and bool(marked(cell) & _needed(cell))


def _never_meta(cell: Mapping[str, Any], file: str) -> bool:
    """PE3: the file is a meta of a type the pack never imports, or the data file one names, which
    the pack never reads (D409 (20), (25))."""
    return file in never_metas(cell) or file in unread(cell)


def _relations(cell: Mapping[str, Any], file: str) -> bool:
    """PE4: an error the reference attributes to no file, in a cell holding a GSVA meta, whose
    relations to the expression and GSVA metas it checks study-wide (D409 (23)); the pack never
    imports either."""
    if file != NOWHERE:
        return False
    metas = _metas(cell)
    return any(_kind(metas[name]) in ("GSVA_SCORES", "GSVA_PVALUES") for name in never_metas(cell))


Exit = tuple[str, Callable[[Mapping[str, Any], str], bool]]
EXITS: tuple[Exit, ...] = (
    ("(PE1) exit 3: [a meta or case list a byte order mark leads]", _mark_led),
    ("(PE2) exit 3: [what the reference loses past a mark on a meta it needs]", _mark_lost),
    ("(PE3) exit 3: [a meta of a type the pack never imports, or its data file]", _never_meta),
    ("(PE4) exit 3: [a GSVA meta's study-wide relations]", _relations),
)
"""The exits of an accepted cell's errors and crash, by the file each names and a rule about the
cell, never by its identity: a meta or case list a mark leads, and what the reference loses with
it, is D409's (13); a meta of a type the pack never imports, or its data file, its (20) and
(25)."""


def masked(cell: str) -> bool:
    """The reference never validated the matrix (no error and no crash in it, and the cell not
    accepted) because a mark leads a meta it needs, or because it crashed in another file first:
    a refusal of the matrix has no kind the reference can give, so it must be an item of either
    kind. Never where the reference accepts the cell or crashes in the matrix (plan r5)."""
    record = REFERENCE[cell]
    files = corpus().CELLS[cell]
    if record["rc"] == 3 or any(_named(file, {MATRIX}) for _, file in reported(record)):
        return False
    crashed = record["crash"] and record.get("crash_file") not in (None, MATRIX)
    return bool(marked(files) & _needed(files)) or bool(crashed)


def out_of_map(written: tuple[str, str], found: str) -> bool:
    """A refusal written from a template of another module (``panels_checks.SHARED`` and
    ``READERS``) that no inventory item names is out of the matrix's map, by module: the
    predicates give it exactly, and the judge judges no kind of it."""
    module, template = written
    if module not in OTHER_MODULES:
        return False
    code = found.partition("|")[0]
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
    """The one ``(module, template)`` a pack refusal's reason is written from, or ``None``: the
    matrix reader's first, then another module's (the first of ``OTHER_MODULES`` holding it, where
    two write one template), a template with no field before one with."""
    _, bar, reason = found.partition("|")
    if not bar:
        return None
    pools = [[("panels", t) for t in sorted(TEMPLATES)]]
    pools.append([(module, t) for module in OTHER_MODULES for t in sorted(OTHER_TEMPLATES[module])])
    for pool in pools:
        exact = [(module, t) for module, t in pool if t == reason]
        if exact:
            return exact[0]
        matched = [
            (module, t)
            for module, t in pool
            if re.fullmatch(checks().pattern(t), reason, re.DOTALL)
        ]
        if len({t for _, t in matched}) == 1:
            return matched[0]
        if matched:
            return None
    return None


@cache
def check_of(found: str) -> tuple[Any, ...]:
    """The matrix checks whose reasons match a refusal's."""
    _, bar, reason = found.partition("|")
    if not bar:
        return ()
    return tuple(check for check in CHECKS if check.matches(reason))


@cache
def wanted_of(cell: str) -> str:
    """The predicates' outcome of the corpus cell ``cell`` (``panels_oracle.expected``)."""
    return oracle().expected(corpus().CELLS[cell])


def explain(outcomes: Mapping[str, str], cells: Sequence[str]) -> list[str]:
    """Why the map does not explain ``outcomes`` over ``cells``, one line a problem: an outcome
    that is not the predicates', a refusal whose ``(module, code, template, kind)`` is no item of
    the inventory, an accepted cell that crashed the reference, an accepted cell's error no exit's
    predicate holds for, a two-kind refusal whose kind is not its predicate's."""
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
        item = (written[0], found.partition("|")[0], written[1], kind(cell, found))
        if masked(cell):
            either = {(*item[:3], SAME), (*item[:3], STRICTER)}
            if not either & INVENTORY:
                problems.append(f"{cell}: {item[:3]} is no item of either kind")
        elif item not in INVENTORY:
            problems.append(f"{cell}: {item} is no item")
        content = files.get(MATRIX)
        if written[1] in TWO_KINDS and isinstance(content, bytes) and not masked(cell):
            nothing = oracle().reference_checks_nothing(content)
            if (item[3] == STRICTER) != nothing:
                problems.append(
                    f"{cell}: {item[3]} where the reference checks nothing is {nothing}"
                )
    return problems


def named(
    outcomes: Mapping[str, str],
) -> tuple[set[tuple[str, str, str, str]], set[str], dict[str, set[str]]]:
    """What the cells name: the inventory's items, the exits, and for each matrix check the items
    its refusals are."""
    items: set[tuple[str, str, str, str]] = set()
    exits: set[str] = set()
    by_check: dict[str, set[str]] = {check.id: set() for check in CHECKS}
    for cell in CELL_IDS:
        found = outcomes[cell]
        if found != "ok":
            written = template_of(found)
            if written is None or masked(cell) or out_of_map(written, found):
                continue
            item = (written[0], found.partition("|")[0], written[1], kind(cell, found))
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
    matrix check named by exactly one item, but those ``HELD``."""
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
    if os.environ.get("AIBI_RECORD_PANELS_OUTCOMES"):
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
    """The templates the walker finds in ``panels`` are exactly the inventory's and ``HELD``'s,
    every template is some check's, and the other modules' templates the inventory names are
    theirs."""
    listed = {template for module, _, template, _ in INVENTORY if module == "panels"}
    assert listed | set(HELD) == TEMPLATES
    assert not listed & set(HELD)
    assert {t for check in CHECKS for t in check.templates} == TEMPLATES
    for module, _, template, _ in INVENTORY:
        if module != "panels":
            assert template in OTHER_TEMPLATES[module], (module, template)


def test_every_held_check_names_the_test_that_holds_it() -> None:
    tree = ast.parse((HERE / "test_panels.py").read_text(encoding="utf-8"))
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


def in_d447(template: str) -> str:
    return template.replace("{}", "\u2026")


def d447() -> str:
    text = SPEC.read_text(encoding="utf-8")
    return next(line for line in text.splitlines() if line.startswith("| D447 |"))


def test_d447_lists_the_inventory_the_items_the_exits_and_the_corpus_size() -> None:
    row = d447()
    for _, _, template, _ in INVENTORY:
        assert f"`{in_d447(template)}`" in row, template
    for template in HELD:
        assert f"`{in_d447(template)}`" in row, template
    for head in ITEMS.values():
        assert head in row, head
    for head, _ in EXITS:
        assert head in row, head
    assert f"a corpus of {len(CELL_IDS)} cells" in row
    listing = row.split("The inventory, by item:", 1)[1].split("Held outside the corpus", 1)[0]
    found = re.findall(r"\((P\d+)\) (.*?)(?= \(P\d+\) |$)", listing)
    segments = dict(found)
    for _, code, template, kind_ in INVENTORY:
        head = ITEMS[(code, kind_)].split(")")[0].lstrip("(")
        assert f"`{in_d447(template)}`" in segments[head], (head, template)


@pytest.mark.parametrize("check", [check.id for check in CHECKS])
def test_dropping_any_check_fails_the_map_alone(
    check: str, onco: ModuleType, tmp_path: Path
) -> None:
    """The drop-each-check property, over every check of ``panels``: with the check's test made
    ``False``, the cells it refused are imported again, and the map alone (``judged``, given the
    fresh outcomes) reports a problem. A check no cell reaches fails here, but those ``HELD``."""
    (found,) = [candidate for candidate in CHECKS if candidate.id == check]
    if set(found.templates) <= set(HELD):
        pytest.skip(f"held by {HELD[found.templates[0]][2]}")
    cells = [cell for cell in CELL_IDS if found in check_of(OUTCOMES[cell])]
    assert cells, f"{check}: no corpus cell reaches it"
    names = ("panels", "study", "importer")
    pack = {name: importlib.import_module(f"aibi.packs.onco.{name}") for name in names}
    with checks().dropped(found, pack):
        fresh = {
            cell: outcome(onco, build(tmp_path / str(index), corpus().CELLS[cell]))
            for index, cell in enumerate(cells[:40])
        }
    assert judged({**OUTCOMES, **fresh}) != [], check


def test_a_narrowed_check_re_recorded_still_fails_the_map() -> None:
    """The laundering probe: the outcomes a reader narrowed to spaces alone would give (every cell
    whose cell is padded by another whitespace accepted), recorded as if they were the reader's,
    still fail the map: the predicates say they must be refused."""
    narrowed = {
        cell: "ok"
        for cell, found in OUTCOMES.items()
        if found.startswith("onco.PADDED_VALUE|")
        and any(
            character in corpus().CELLS[cell][MATRIX].decode("utf-8")  # type: ignore[union-attr]
            for character in ("\xa0", "\u3000", "\u2000")
        )
    }
    assert narrowed
    problems = judged({**OUTCOMES, **narrowed})
    assert any("is not the predicates'" in problem for problem in problems)


def test_the_byte_order_mark_s_kind_is_held_to_its_predicate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Plan r4 m5, r5 n3: a mark-led matrix refused where the reference's sniffer finds a tab (so
    it errs on the header) yet recorded with no error is reported, never taken as the stricter
    kind the predicate does not give; one where its sniffer finds none is stricter; and the two
    live cells of a mark before ``#`` hold the predicate's reading (the marked line is the
    header)."""
    module = sys.modules[__name__]
    c = corpus()
    found = "onco.BYTE_ORDER_MARK|" + next(iter(TWO_KINDS))
    for content, nothing in ((MARK + c.VALID, False), (MARK + b"x\n", True)):
        cells = {**c.CELLS, "fake:led": c.data(content)}
        reference = {**REFERENCE, "fake:led": {"crash": False, "errors": [], "rc": 1}}
        monkeypatch.setattr(c, "CELLS", cells)
        monkeypatch.setattr(module, "REFERENCE", reference)
        kind.cache_clear()
        problems = explain({"fake:led": found}, ["fake:led"])
        assert any("where the reference checks nothing is False" in p for p in problems) is (
            not nothing
        ), content
    kind.cache_clear()
    monkeypatch.undo()
    shift = c.CELLS["byte:bom_hash_shift"][MATRIX]
    assert isinstance(shift, bytes)
    assert oracle().reference_checks_nothing(shift)
    assert REFERENCE["byte:bom_hash_shift"]["rc"] == 3
    assert REFERENCE["byte:nobom_hash_shift"]["rc"] == 1


def test_the_predicates_import_nothing_of_the_pack() -> None:
    """The predicates are the test's own, never ``panels``': ``panels_oracle.py`` and the oracle
    it loads (``cna_oracle.py``) import no module of aibi (by AST) and name none (by text)."""
    for name in ("panels_oracle", "cna_oracle", "panels_corpus"):
        source = (HERE / f"{name}.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        names = [
            node.module if isinstance(node, ast.ImportFrom) else alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import | ast.ImportFrom)
            for alias in node.names
        ]
        assert not any(str(found).startswith("aibi") for found in names), (name, names)
        assert "aibi.packs" not in source, name
        assert "import_module" not in source, name


def test_the_earlier_walker_lists_are_unchanged() -> None:
    """``panels`` joins neither slice 1b's module lists nor slice 4's nor slice 3's."""
    walker = checks().WALKER
    assert walker.READERS == ("mutation", "caselists")
    assert walker.OTHERS == ("study", "meta", "importer")
    assert checks().MODULES == ("panels",)
    assert _load("structural_checks").MODULES == ("structural",)
    assert _load("cna_checks").MODULES == ("cna",)


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


def test_a_same_item_on_a_cell_the_reference_accepts_is_a_problem(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Plan r4 MJ-1: an item labelled ``same outcome`` fired on a cell the reference accepts is
    judged stricter, so it is no item: a mislabelled item fails the suite (the crash item, the
    header items and a row item)."""
    module = sys.modules[__name__]
    c = corpus()
    cells = {**c.CELLS, "fake:accepted": c.study()}
    monkeypatch.setattr(c, "CELLS", cells)
    record = {"crash": False, "crash_file": None, "errors": [], "rc": 3}
    monkeypatch.setattr(module, "REFERENCE", {**REFERENCE, "fake:accepted": record})
    for found in (
        oracle().said("crash"),
        oracle().said("unknown_profile"),
        oracle().said("not_sequenced"),
        oracle().said("duplicate_sample"),
    ):
        monkeypatch.setattr(module, "wanted_of", lambda cell, found=found: found)
        kind.cache_clear()
        problems = explain({"fake:accepted": found}, ["fake:accepted"])
        assert any("is no item" in problem for problem in problems), found
    kind.cache_clear()


def test_the_exits_are_held_to_their_predicates_by_file() -> None:
    """PE1 holds on a meta or list a mark leads, never on an unmarked one or the marked matrix;
    PE2 on ``-`` when a mark leads the study, sample or matrix meta; PE3 on a never-imported meta
    and its data file, never on the matrix, its meta or a file no meta names."""
    c = corpus()
    pe1, pe2, pe3, pe4 = (holds for _, holds in EXITS)
    gsva = c.CELLS["typing:geneset_score_gsva_score_kept"]
    assert pe4(gsva, NOWHERE)
    assert not pe4(gsva, MATRIX)
    assert not pe4(c.CELLS["typing:mrna_expression_z_score_kept"], NOWHERE)
    led = c.study(meta_gene_panel_matrix_DOT_txt=MARK + c.meta())
    assert pe1(led, c.MATRIX_META)
    assert not pe1(c.study(), c.MATRIX_META)
    assert not pe1(c.data(MARK + c.VALID), MATRIX)
    assert pe2(led, NOWHERE)
    assert not pe2(led, c.MATRIX_META)
    assert not pe2(c.study(), NOWHERE)
    maf_led = c.study(meta_mutations_DOT_txt=MARK + c.BASE.META.encode())
    assert not pe2(maf_led, NOWHERE)
    typed = c.CELLS["type:clinical_timeline_beside"]
    assert pe3(typed, "meta_type.txt")
    assert pe3(typed, "d_type.txt")
    for cell, file in (
        (typed, MATRIX),
        (typed, c.MATRIX_META),
        (typed, "data_clinical_sample.txt"),
        (typed, NOWHERE),
        (c.study(), "nothing_named.txt"),
    ):
        assert not any(holds(cell, file) for _, holds in EXITS), file


def test_an_accepted_crash_needs_an_exit_by_its_file(monkeypatch: pytest.MonkeyPatch) -> None:
    """An accepted cell whose reference crashed is a problem unless the crash is in a file the
    pack never reads: in the matrix it is reported, in a never-imported meta it is PE3's."""
    module = sys.modules[__name__]
    c = corpus()
    files = c.CELLS["type:clinical_timeline_beside"]
    monkeypatch.setattr(c, "CELLS", {**c.CELLS, "fake:crash": files})
    for file, problem in ((MATRIX, True), ("meta_type.txt", False)):
        record = {"crash": True, "crash_file": file, "errors": [], "rc": 1}
        monkeypatch.setattr(module, "REFERENCE", {**REFERENCE, "fake:crash": record})
        monkeypatch.setattr(module, "wanted_of", lambda cell: "ok")
        found = explain({"fake:crash": "ok"}, ["fake:crash"])
        assert any("the reference crashed in" in p for p in found) is problem, file


def test_a_refusal_is_judged_on_the_files_it_is_about(monkeypatch: pytest.MonkeyPatch) -> None:
    """``kind`` counts only the errors in the files a refusal is about: a matrix refusal whose
    class the reference reports in another file alone is stricter, and the same outcome once that
    error is in the matrix."""
    module = sys.modules[__name__]
    found = "onco.BLANK_LINE|the line is blank"
    monkeypatch.setattr(corpus(), "CELLS", {**corpus().CELLS, "fake:about": corpus().study()})
    for file, wanted in (("data_other.txt", STRICTER), (MATRIX, SAME)):
        record = {"crash": False, "errors": [["blank-line", file]], "rc": 1}
        monkeypatch.setattr(module, "REFERENCE", {**REFERENCE, "fake:about": record})
        kind.cache_clear()
        assert kind("fake:about", found) == wanted, file
    kind.cache_clear()


def test_masked_never_covers_an_accepted_cell_or_a_matrix_crash() -> None:
    """Plan r5: the judge never exempts a cell by a mark when the live outcome is rc 3 or a crash
    in the matrix; it does where the reference lost a needed meta and never read the matrix."""
    for cell in CELL_IDS:
        record = REFERENCE[cell]
        if record["rc"] == 3 or (record["crash"] and record.get("crash_file") == MATRIX):
            assert not masked(cell), cell
    assert masked("dict:study_bom_blank_onecol")
    assert not masked("dict:study_bom_gat_tabbed")
    assert not masked("dict:study_bom_sid_onecol")


def test_the_dictionary_model_is_the_reference_s_on_every_cell() -> None:
    """Plan r5 MJ: the executable model of V:3363-3373 over the dictionary of V:4740-4778, on the
    reference's reading of the metas, holds every live cell whose matrix reaches the reference's
    header check: the reference crashed in the matrix with a ``KeyError`` exactly where the model
    says the loop raises, the byte order mark's two directions among them."""
    o = oracle()
    checked = 0
    for cell in CELL_IDS:
        files = corpus().CELLS[cell]
        content = files.get(MATRIX)
        record = REFERENCE[cell]
        if not isinstance(content, bytes) or not _reaches_header_check(content, record):
            continue
        metas = {
            name: found
            for name, found in files.items()
            if isinstance(found, bytes) and name in _metas(files)
        }
        crashed = (record.get("crash_error") or "").startswith("KeyError") and record.get(
            "crash_file"
        ) == MATRIX
        assert o.profiles(metas).crashes is crashed, cell
        checked += 1
    assert checked > 300
    for cell, crashes in (
        ("dict:study_bom_sid_tabbed", False),
        ("dict:study_bom_gat_tabbed", True),
        ("dict:study_sid_gat_mutext_tabbed", True),
        ("dict:study_sid_gat_other_tabbed", False),
        ("dict:study_sid_key_space_tabbed", False),
        ("dict:study_gat_key_space_tabbed", True),
        ("dict:study_sid_onecol", False),
    ):
        record = REFERENCE[cell]
        assert (record["crash"] and record.get("crash_file") == MATRIX) is crashes, cell


def _reaches_header_check(content: bytes, record: Mapping[str, Any]) -> bool:
    """Whether the reference's validation of the matrix reaches its header check (V:525): it was
    validated (not masked by a second matrix meta, a lost study, a crash elsewhere), the file is
    UTF-8, has a header, is not gated by the sniffer, and no header field is over the limit."""
    o = oracle()
    lost = ("second-matrix", "no-study-file", "open-fail")
    if any(identity in lost for identity, _ in record["errors"]):
        return False
    if record["crash"] and record.get("crash_file") != MATRIX:
        return False
    try:
        content.decode("utf-8")
    except UnicodeDecodeError:
        return False
    lines = [line.decode("utf-8") for line in o._lines(content)]
    header = next((at for at, line in enumerate(lines) if not line.startswith("#")), None)
    if header is None or o.reference_checks_nothing(content):
        return False
    return all(len(name) <= o.FIELD_MOST for name in lines[header].split("\t"))


RAISE_SITES: Mapping[str, str] = {
    "V:437 the file cannot be opened": "DATA_FILE",
    "V:446 the file is not UTF-8": "NOT_UTF8",
    "V:469 no line but '#' lines": "NO_HEADER",
    "V:474 processTopLines is False": "unreachable: the base validator's hook returns True",
    "V:494-496 the sniffer finds no tab": "MISSING_COLUMN",
    "V:505 not a tab-delimited file": "unreachable: the sniffer is given the tab alone",
    "V:511 a header field over the csv limit (crash)": "COLUMN_NAME",
    "V:612 a repeated column name": "DUPLICATE_COLUMN",
    "V:615 SAMPLE_ID not first": "COLUMN_NAME",
    "V:3365 KeyError genetic_alteration_type (crash)": "STABLE_ID_KEYS",
    "V:3366 KeyError datatype (crash)": "STABLE_ID_KEYS",
    "V:3367 KeyError stable_id": "unreachable: an entry exists only for a meta holding the key",
    "V:3373 KeyError stable_id": "unreachable: an entry exists only for a meta holding the key",
    "V:3379 a name no meta's stable_id": "COLUMN_NAME",
    "V:3385 a profile whose meta holds gene_panel": "COLUMN_NAME",
    "V:540 a data field over the csv limit (crash)": "LONG_FIELD",
    "V:543 a blank line": "BLANK_LINE",
    "V:547 a data line starting with '#'": "COMMENT_LINE",
    "V:547 fields[0] of an empty row": "unreachable: an empty row is blank first (V:543)",
    "V:627 a repeated header": "REPEATED_HEADER",
    "V:636 a row of another width": "ROW_WIDTH",
    "V:646 a blank cell": "REQUIRED_VALUE",
    "V:3402 the pop of SAMPLE_ID (ValueError)": "unreachable: a header without it stops (V:615)",
    "V:3405 a sample not in the sample file": "UNKNOWN_SAMPLE",
    "V:3414 a sample twice": "DUPLICATE_SAMPLE",
    "V:3418 IndexError on a short row (crash)": "ROW_WIDTH",
    "V:3422 a panel on a sample not in _sequenced": "NOT_SEQUENCED",
    "V:3427 a panel id not in the portal": "exit (15): portal only, never offline",
    "V:712 line ends of more than one kind, or none": "LINE_ENDS",
    "V:5582 two matrix metas": "DUPLICATE_PROFILE_META",
    "V:5586 validate() on None": "unreachable: data_filename is mandatory (C:269)",
}
"""Every raise site of the reference's validation of the matrix (V:430-712, V:934-946,
V:3343-3433, V:5579-5586) and its crash paths, each named by exactly one item's code of D447, one
recorded exit, or as unreachable with its reason (plan r3 MJ-A, r4 m6, r5)."""
CRASHES: Mapping[str, str] = {
    "KeyError:'genetic_alteration_type'": "V:3365 KeyError genetic_alteration_type (crash)",
    "KeyError:'datatype'": "V:3366 KeyError datatype (crash)",
    "IndexError": "V:3418 IndexError on a short row (crash)",
    "_csv.Error": "V:540 a data field over the csv limit (crash)",
}


def test_every_raise_site_is_an_item_an_exit_or_unreachable() -> None:
    """Plan r3 MJ-A, r4 m6: each raise site of the reference's validation of the matrix is named
    by one item of the inventory, one exit or ``unreachable`` with its reason; every crash the
    corpus recorded in the matrix is one of the crash sites, and each crash site is witnessed."""
    codes = {code.removeprefix("onco.") for _, code, _, _ in INVENTORY}
    for site, named_by in RAISE_SITES.items():
        if named_by.startswith(("unreachable: ", "exit ")):
            assert named_by.split(": ", 1)[1], site
            continue
        assert named_by in codes, (site, named_by)
    seen = set()
    for cell in CELL_IDS:
        record = REFERENCE[cell]
        if record["crash"] and record.get("crash_file") == MATRIX:
            assert record["crash_error"] in CRASHES, (cell, record["crash_error"])
            seen.add(record["crash_error"])
    assert seen == set(CRASHES)
    unreachable = [site for site, by in RAISE_SITES.items() if by.startswith("unreachable")]
    assert {site.split(" ", 1)[0] for site in unreachable} == {
        "V:474",
        "V:505",
        "V:3367",
        "V:3373",
        "V:547",
        "V:3402",
        "V:5586",
    }


def published_cells() -> list[str]:
    return [cell for cell in CELL_IDS if OUTCOMES.get(cell) == "ok"]


@pytest.mark.parametrize("cell", published_cells())
def test_an_accepted_cell_publishes(cell: str, world: Any, registry: PackRegistry) -> None:
    directory = build(world.root / "cell", corpus().CELLS[cell])
    world.publish("study", directory, registry, pack="onco")

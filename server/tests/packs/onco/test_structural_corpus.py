"""The structural variant corpus against the reference's recorded outcomes and D445's predicates
(M4.2b-4, D445).

Every cell of ``structural_corpus.py`` is imported through ``PACK.importer`` and the core's checks
of a pack's result (``run_importer``), and its outcome is held four ways:

- **per cell, to its recorded outcome** (``structural_outcomes.json``), the onco code asserted;
- **per cell, to the predicates** (``structural_oracle.expected``): the outcome is the first item,
  in the pack's refusal order, whose predicate over the cell holds, ``ok`` when none does. The
  predicates are written in the test from the reference and D445, never read from
  ``structural.py``, so a check narrowed in the reader and its outcomes re-recorded still fails
  here (review r2 MJ4: the map is held by predicates, not by the recorded outcome file);
- **per cell, to the reference** (``structural_reference.json``, each cell's outcome on
  cbioportal-core 47890bb, offline, not relaxed, by error identity and the file it names): a
  refusal's kind is D409's one definition, ``same outcome`` where the reference reports an error of
  the refusal's own class (``SAME_AS``) in a file the refusal is about, ``stricter`` otherwise, and
  ``(module, code, reason, kind)`` must be an item of the literal ``INVENTORY``; an accepted cell's
  every error must be explained by an exit whose predicate holds (``EXITS``);
- **through the store**, for the accepted cells of the ``byte``, ``header``, ``namespace``,
  ``build``, ``dup`` and ``meta`` generators and one cell of each token class: published.

To record a new cell's outcome on the reference, run the scratch harness D445 names; to record
aibi's, run this module with ``AIBI_RECORD_STRUCTURAL_OUTCOMES=1`` and review the difference.
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
REFERENCE: Mapping[str, Any] = json.loads((HERE / "structural_reference.json").read_text())
OUTCOMES_FILE = HERE / "structural_outcomes.json"
OUTCOMES: Mapping[str, str] = (
    json.loads(OUTCOMES_FILE.read_text()) if OUTCOMES_FILE.exists() else {}
)
AT = "2026-01-01T00:00:00Z"
SAME = "same outcome"
STRICTER = "stricter"
MARKED = "stricter under a leading mark"
"""(S33)'s kind: a check of a column whose name a leading mark changes for the reference, or of a
header behind a mark-led ``#`` line (review r3 MJ-3)."""
CRASH = "crash"


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"{name}_sv_test", HERE / f"{name}.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@cache
def corpus() -> ModuleType:
    return _load("structural_corpus")


@cache
def checks() -> ModuleType:
    return _load("structural_checks")


@cache
def oracle() -> ModuleType:
    return _load("structural_oracle")


CELL_IDS = sorted(corpus().CELLS)
CHECKS: tuple[Any, ...] = tuple(checks().every_check())
TEMPLATES: frozenset[str] = frozenset(
    template for module in checks().MODULES for template in checks().templates(module)
)
SHARED_TEMPLATES: frozenset[str] = frozenset(
    template for module in checks().SHARED for template in checks().templates(module)
)

SAME_AS: Mapping[str, frozenset[str]] = {
    "NOT_UTF8": frozenset({"not-utf8"}),
    "CONTROL_CHARACTER": frozenset(),
    "NO_HEADER": frozenset({"no-header", "missing-column", "sv-content", "header-cascade"}),
    "TOO_MANY_COLUMNS": frozenset(),
    "COLUMN_NAME": frozenset({"missing-column", "sv-content"}),
    "DUPLICATE_COLUMN": frozenset({"duplicate-column"}),
    "MISSING_COLUMN": frozenset({"missing-column", "sv-content", "namespace"}),
    "BLANK_LINE": frozenset({"blank-line"}),
    "COMMENT_LINE": frozenset({"comment-line"}),
    "REPEATED_HEADER": frozenset({"repeated-header"}),
    "ROW_WIDTH": frozenset({"row-width", CRASH}),
    "LONG_FIELD": frozenset({CRASH}),
    "UNKNOWN_SAMPLE": frozenset({"sample-undefined"}),
    "SV_STATUS": frozenset({"sv-status"}),
    "NCBI_BUILD": frozenset({"sv-build", "sv-build-multi"}),
    "ENTREZ_VALUE": frozenset({"entrez-nonpositive"}),
    "NO_GENE": frozenset({"sv-no-gene"}),
    "PADDED_VALUE": frozenset(),
    "DUPLICATE_PROFILE_META": frozenset({"type-meta", "duplicate-stable-id"}),
    "DUPLICATE_STABLE_ID": frozenset({"duplicate-stable-id"}),
    "META_VALUE": frozenset({"type-meta"}),
    "META_FILE": frozenset({"meta-type"}),
    "META_FIELD": frozenset({"meta-key"}),
    "STUDY_MISMATCH": frozenset({"study-mismatch"}),
    "DATA_FILE": frozenset(
        {"open-fail", "missing-column", "missing-amino-column", "sv-content", "top-lines"}
        | {"header-order"}
    ),
}
"""Each code's class among the reference's error identities (``crash`` for a crash): a refusal is
``same outcome`` only when the cell's recorded errors hold one of its code's, in a file the refusal
is about (the SV file, an SV meta, the file the SV meta names, or none: ``-``), and ``stricter``
otherwise. ``DATA_FILE``'s class is the reference failing to open the named file or reading a file
in the wrong role (an SV file as a MAF, a sample or a timeline file, or a read file as an SV
file)."""


def _about(cell: str) -> set[str]:
    """The files a refusal on the cell is about: the SV metas, the file each names (as written
    and normalised), the SV file, ``-``."""
    files = corpus().CELLS[cell]
    about = {"-", corpus().SV_FILE}
    for name, content in files.items():
        if isinstance(content, bytes) and b"genetic_alteration_type: STRUCTURAL_VARIANT" in content:
            value = oracle().parse_meta(content).get("data_filename", "")
            about |= {name, value, posixpath.normpath(value)}
    # the meta file the reference names for a meta error (another meta repeating the SV meta's
    # stable_id, review r3 m1)
    about |= {file for identity, file in REFERENCE[cell]["errors"] if identity in META_CLASS}
    return about


BOM_CLASS = frozenset({"missing-column", "header-cascade"})
"""What the reference reports on a file a byte order mark leads when its first column is a required
one: it reads the mark into that column's name, its header fails, and it stops (review r1 m1)."""
BOM_HEADER = BOM_CLASS | {"sv-content", "namespace", "duplicate-column"}
"""Every error the reference can report on a header (its missing, site, namespace and repeated
columns), and nothing past it: on a mark-led first line it stops there (review r3, seed 4403)."""
ROW_ITEMS = frozenset(
    {
        "UNKNOWN_SAMPLE",
        "SV_STATUS",
        "NCBI_BUILD",
        "ENTREZ_VALUE",
        "NO_GENE",
        "PADDED_VALUE",
        "BLANK_LINE",
        "REPEATED_HEADER",
        "ROW_WIDTH",
        "LONG_FIELD",
    }
)
"""The refusals of a data row's cells or structure, which the reference, stopped at a header a byte
order mark broke, never reached (review r2 MJ-2 (b)); a data line starting with ``#`` is one too
(``COMMENT_LINE`` with its own reason, not the stricter one after spaces). Every other refusal of
a byte-order-mark-led file (a control character, a column name, a ``#`` after spaces) keeps its
own item."""
ROW_REASONS = frozenset({"a data line starts with '#'"})
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
"""A refusal of the SV meta is the same outcome as any error the reference reports about that meta
file: which of its checks the reference names first is its own order (review r2 MJ-2 (d))."""


def bom_rule(cell: str, found: str) -> bool:
    """Whether the byte order mark's rule decides the cell's kind: the SV file starts with one
    mark, the reference's errors on it are its header's alone (it stopped there), and the pack's
    refusal is of a data row, which the reference never reached."""
    content = corpus().CELLS[cell].get(corpus().SV_FILE)
    if not isinstance(content, bytes) or not content.startswith(b"\xef\xbb\xbf"):
        return False
    mine = {identity for identity, file in REFERENCE[cell]["errors"] if file == corpus().SV_FILE}
    code, _, reason = found.partition("|")
    row = code.removeprefix("onco.") in ROW_ITEMS or reason in ROW_REASONS
    return bool(mine & BOM_CLASS) and mine <= BOM_HEADER and row


def one_mark(cell: str) -> bool:
    content = corpus().CELLS[cell].get(corpus().SV_FILE)
    mark = b"\xef\xbb\xbf"
    return (
        isinstance(content, bytes) and content.startswith(mark) and not content.startswith(2 * mark)
    )


def marked(cell: str, found: str) -> bool:
    """(S33)'s predicate: one leading mark, and the refusal is the same outcome on the cell's twin
    (the file without the mark, recorded live): what the reference would report there, it does
    not report here only because the mark renamed its first column or made a ``#`` line its
    header."""
    twin = f"twin:{cell}"
    return one_mark(cell) and twin in REFERENCE and _class_kind(twin, found) == SAME


@cache
def kind(cell: str, found: str, rules: bool = True) -> str:
    """D409's one definition, per class (``SAME_AS``), over the errors in the files the refusal is
    about; then the leading mark's two rules (``rules``; each check's own kind, for ``named``,
    leaves them out): a data row refused behind a header the mark broke is ``same outcome``
    (``bom_rule``), and a check the mark hid from the reference is (S33), ``MARKED``."""
    if rules and bom_rule(cell, found):
        return SAME
    own = _class_kind(cell, found)
    header_less = found.partition("|")[2] in TWO_KINDS  # its kind is the predicate's alone
    if rules and own == STRICTER and not header_less and marked(cell, found):
        return MARKED
    return own


@cache
def _class_kind(cell: str, found: str) -> str:
    recorded = REFERENCE[cell]
    about = _about(cell)
    errors = {
        identity
        for identity, file in recorded["errors"]
        if file in about or posixpath.basename(file) in about
    }
    if recorded["crash"]:
        errors.add(CRASH)
    code = found.partition("|")[0].removeprefix("onco.")
    reason = found.partition("|")[2]
    same_as = SAME_AS_BY_REASON.get(reason, SAME_AS.get(code, frozenset()))
    if code in META_CODES:
        same_as = same_as | META_CLASS
    return SAME if errors & same_as else STRICTER


SAME_AS_BY_REASON: Mapping[str, frozenset[str]] = {
    "the column name is empty or longer than a descriptor's string": frozenset(),
    "the column name is not Unicode text": frozenset(),
}
"""Checks whose class is not their code's: the reference never checks a column name's length or
text, so a missing column it reports beside such a name is another fault's, and the check is
always stricter."""


INVENTORY: frozenset[tuple[str, str, str, str]] = frozenset(
    {
        ("structural", "onco.NOT_UTF8", "the line is not UTF-8", SAME),
        ("structural", "onco.CONTROL_CHARACTER", "the field holds a control character", STRICTER),
        ("structural", "onco.NO_HEADER", "the file has no line of column names", SAME),
        (
            "structural",
            "onco.TOO_MANY_COLUMNS",
            "the file has more columns than a table may",
            STRICTER,
        ),
        ("structural", "onco.COLUMN_NAME", "the line of column names is empty", SAME),
        (
            "structural",
            "onco.COLUMN_NAME",
            "the column name is empty or longer than a descriptor's string",
            STRICTER,
        ),
        ("structural", "onco.COLUMN_NAME", "the column name is not Unicode text", STRICTER),
        ("structural", "onco.DUPLICATE_COLUMN", "the column name repeats an earlier's", SAME),
        (
            "structural",
            "onco.MISSING_COLUMN",
            "the line of column names and the five lines after it hold no tab",
            STRICTER,
        ),
        ("structural", "onco.MISSING_COLUMN", "the file has no {} column", SAME),
        (
            "structural",
            "onco.MISSING_COLUMN",
            "the file names a site's gene by neither {} nor {}",
            SAME,
        ),
        (
            "structural",
            "onco.MISSING_COLUMN",
            "the file has no column of a namespace its meta file declares",
            SAME,
        ),
        ("structural", "onco.BLANK_LINE", "the line is blank", SAME),
        ("structural", "onco.COMMENT_LINE", "a data line starts with '#'", SAME),
        (
            "structural",
            "onco.COMMENT_LINE",
            "a data line starts with '#' after spaces or tabs",
            STRICTER,
        ),
        ("structural", "onco.REPEATED_HEADER", "the line repeats the column names", SAME),
        (
            "structural",
            "onco.ROW_WIDTH",
            "the line does not have as many fields as there are column names",
            SAME,
        ),
        ("structural", "onco.LONG_FIELD", "the field is longer than a cell may be", SAME),
        ("structural", "onco.UNKNOWN_SAMPLE", "the sample id is a spelling of no value", SAME),
        (
            "structural",
            "onco.UNKNOWN_SAMPLE",
            "the sample id is no sample id of the sample file",
            SAME,
        ),
        ("structural", "onco.SV_STATUS", "the SV status is neither SOMATIC nor GERMLINE", SAME),
        ("structural", "onco.NCBI_BUILD", "the NCBI build is neither GRCh37 nor GRCh38", SAME),
        ("structural", "onco.NCBI_BUILD", "the NCBI build is not the file's first one", SAME),
        ("structural", "onco.ENTREZ_VALUE", "the Entrez gene id is not positive", SAME),
        (
            "structural",
            "onco.NO_GENE",
            "the row names a gene at neither site, by symbol or id",
            SAME,
        ),
        (
            "structural",
            "onco.PADDED_VALUE",
            (
                "the sample id is a sample's with whitespace around it, which cBioPortal strips "
                "and the published column would keep"
            ),
            STRICTER,
        ),
        (
            "structural",
            "onco.PADDED_VALUE",
            (
                "the value is a spelling of no value with whitespace around it, or whitespace "
                "alone, which cBioPortal reads as no value and the published column would read "
                "as a value"
            ),
            STRICTER,
        ),
        (
            "structural",
            "onco.PADDED_VALUE",
            (
                "the Entrez gene id is 0 with spaces around it, which cBioPortal reads as no value "
                "and the published column would read as the value 0"
            ),
            STRICTER,
        ),
        (
            "structural",
            "onco.ENTREZ_VALUE",
            "the Entrez gene id is a number but no integer",
            STRICTER,
        ),
        (
            "structural",
            "onco.DUPLICATE_PROFILE_META",
            "is a second structural variant meta file",
            SAME,
        ),
        (
            "structural",
            "onco.DATA_FILE",
            (
                "is a structural variant meta file whose data_filename names no regular file in "
                "the study's directory"
            ),
            SAME,
        ),
        (
            "structural",
            "onco.META_VALUE",
            "holds a stable_id that a structural variant meta may not",
            SAME,
        ),
        (
            "structural",
            "onco.DATA_FILE",
            (
                "has a data_filename that names a structural variant file whose name is not "
                "Unicode text"
            ),
            STRICTER,
        ),
        (
            "study",
            "onco.DATA_FILE",
            "has a data_filename that is not a file name M4.2a accepts",
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
            "{} a {} that names a symlink, exactly or under case and normalisation folding",
            STRICTER,
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
            (
                "{} a {} that names a meta file or a clinical data file, exactly or under case "
                "and normalisation folding"
            ),
            STRICTER,
        ),
        ("study", "onco.DUPLICATE_STABLE_ID", "repeats another meta file's stable_id", SAME),
        ("meta", "onco.META_FILE", "has no meta file type cBioPortal defines", SAME),
        ("meta", "onco.META_FIELD", "lacks the key {}", SAME),
        ("study", "onco.STUDY_MISMATCH", "names another study than the study meta", SAME),
        ("structural", "onco.COLUMN_NAME", "the line of column names is empty", STRICTER),
        ("structural", "onco.NO_HEADER", "the file has no line of column names", STRICTER),
        ("structural", "onco.DUPLICATE_COLUMN", "the column name repeats an earlier's", MARKED),
        ("structural", "onco.NCBI_BUILD", "the NCBI build is neither GRCh37 nor GRCh38", MARKED),
        ("structural", "onco.NCBI_BUILD", "the NCBI build is not the file's first one", MARKED),
        ("structural", "onco.ENTREZ_VALUE", "the Entrez gene id is not positive", MARKED),
        (
            "structural",
            "onco.PADDED_VALUE",
            (
                "the sample id is a sample's with whitespace around it, which cBioPortal strips "
                "and the published column would keep"
            ),
            SAME,
        ),
        (
            "structural",
            "onco.PADDED_VALUE",
            (
                "the value is a spelling of no value with whitespace around it, or whitespace "
                "alone, which cBioPortal reads as no value and the published column would read "
                "as a value"
            ),
            SAME,
        ),
        (
            "structural",
            "onco.PADDED_VALUE",
            (
                "the Entrez gene id is 0 with spaces around it, which cBioPortal reads as no value "
                "and the published column would read as the value 0"
            ),
            SAME,
        ),
        (
            "structural",
            "onco.ENTREZ_VALUE",
            "the Entrez gene id is a number but no integer",
            SAME,
        ),
    }
)
"""The inventory of the structural variant checks and of the other refusals the corpus reaches
(review r3 MJ-1, the brief: literal, keyed by module, code, reason and kind), written by hand.
The ``structural`` module's templates are exactly the walker's, less ``HELD``
(``test_the_inventory_is_the_code_s``); the other modules' are D409's sites an SV cell reaches,
which slice 4 does not change (``test_the_shared_modules_are_main_s``). A check deleted, added or
changed in kind changes this set and D445 too: a visible edit."""
HELD: Mapping[str, tuple[str, str, str]] = {
    "the study's files have more cells than an import may": (
        "onco.TOO_MANY_CELLS",
        STRICTER,
        "test_the_cell_bound_counts_the_sv_file_s_cells",
    ),
}
"""The structural variant checks no corpus cell reaches, by template: the cell bound, which a
small study cannot pass, held by the named test of ``test_structural.py``."""

_ITEMS = (
    ("NOT_UTF8", SAME),
    ("CONTROL_CHARACTER", STRICTER),
    ("NO_HEADER", SAME),
    ("TOO_MANY_COLUMNS", STRICTER),
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
    ("SV_STATUS", SAME),
    ("NCBI_BUILD", SAME),
    ("ENTREZ_VALUE", SAME),
    ("NO_GENE", SAME),
    ("PADDED_VALUE", STRICTER),
    ("ENTREZ_VALUE", STRICTER),
    ("PADDED_VALUE", SAME),
    ("DUPLICATE_PROFILE_META", SAME),
    ("DUPLICATE_STABLE_ID", SAME),
    ("META_VALUE", SAME),
    ("META_FILE", SAME),
    ("META_FIELD", SAME),
    ("STUDY_MISMATCH", SAME),
    ("DATA_FILE", SAME),
    ("DATA_FILE", STRICTER),
    ("NO_HEADER", STRICTER),
)
ITEMS: Mapping[tuple[str, str], str] = {
    (f"onco.{code}", kind): f"(S{number}) {kind}: [{code}]"
    for number, (code, kind) in enumerate(_ITEMS, start=1)
}
MARKED_CODES = ("DUPLICATE_COLUMN", "NCBI_BUILD", "ENTREZ_VALUE")
ITEMS = {
    **ITEMS,
    **{(f"onco.{code}", MARKED): f"(S33) {MARKED}: [{code}]" for code in MARKED_CODES},
}
"""Each refusal's code and kind to the head of its D445 item, numbered ``S1`` on, in the order of
the pack's refusals, and (S33) last, one item over the codes a leading mark hides from the
reference: the inventory's ``(code, kind)`` pairs, exactly."""


def _bom(cell: Mapping[str, Any], file: str) -> bool:
    """E1: the named file starts with a byte order mark (the reference reads it into the first
    column's name, and every error that causes)."""
    content = cell.get(file)
    mark = b"\xef\xbb\xbf"
    return (
        isinstance(content, bytes) and content.startswith(mark) and not content.startswith(2 * mark)
    )


def _duplicate(cell: Mapping[str, Any], file: str) -> bool:
    """E2: the named SV file has two rows the reference keys alike (V:3318-3328)."""
    content = cell.get(file)
    return isinstance(content, bytes) and oracle().duplicate_rows(content)


def _mixed(cell: Mapping[str, Any], file: str) -> bool:
    """E3: the named file's line ends are of more than one kind."""
    content = cell.get(file)
    if not isinstance(content, bytes):
        return False
    crlf = content.count(b"\r\n")
    kinds = [crlf > 0, content.count(b"\n") > crlf, content.count(b"\r") > crlf]
    return sum(kinds) > 1


def _no_line_end(cell: Mapping[str, Any], file: str) -> bool:
    """E4: the named file holds no line end at all."""
    content = cell.get(file)
    return isinstance(content, bytes) and b"\n" not in content and b"\r" not in content


Exit = tuple[str, Callable[[Mapping[str, Any], str], bool]]
SE1 = "(SE1) exit 1: [byte order mark]"


def se1(cell: str, identity: str, file: str) -> bool:
    """SE1, every error a leading mark causes (review r3 MJ-3): exactly one mark leads the SV file
    the error names, and the error is absent from the twin's recorded errors (the same file without
    the mark), whatever its identity."""
    if file != corpus().SV_FILE or not one_mark(cell):
        return False
    twin = REFERENCE.get(f"twin:{cell}")
    if twin is None:
        return False
    return identity not in {found for found, named in twin["errors"] if named == file}


EXITS: Mapping[str, tuple[Exit, ...]] = {
    "sv-duplicate": (("(SE2) exit 1: [duplicate row]", _duplicate),),
    "line-ends": (
        ("(SE3) exit 1: [mixed line ends]", _mixed),
        ("(SE4) exit 1: [no line end]", _no_line_end),
    ),
}
"""Each error identity the reference reports on a cell the pack accepts, to the exits that may
explain it, each with its predicate over the cell and the file the error names (never the
identity alone): a byte order mark on that file and the errors it causes; two rows of that SV file
keyed alike, every row kept; mixed line ends in that file, or none at all, read without loss."""


def outcome(onco: ModuleType, directory: Path) -> str:
    """``ok``; or the pack's one refusal, ``onco.<CODE>|<reason>``; or the core's codes."""
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
    pool = [("structural", t) for t in TEMPLATES] + [("shared", t) for t in SHARED_TEMPLATES]
    exact = [(module, t) for module, t in pool if t == reason]
    if exact:
        return _module_of(exact[0])
    matched = [
        (module, t) for module, t in pool if re.fullmatch(checks().pattern(t), reason, re.DOTALL)
    ]
    return _module_of(matched[0]) if len(matched) == 1 else None


def _module_of(found: tuple[str, str]) -> tuple[str, str]:
    module, template = found
    if module == "structural":
        return found
    for name in checks().SHARED:
        if template in checks().templates(name):
            return name, template
    return found


@cache
def check_of(found: str) -> tuple[Any, ...]:
    """The structural checks whose reasons match a refusal's."""
    _, bar, reason = found.partition("|")
    if not bar:
        return ()
    return tuple(check for check in CHECKS if check.matches(reason))


@cache
def wanted_of(cell: str) -> str:
    """The predicates' outcome of the corpus cell ``cell`` (``structural_oracle.expected``)."""
    return oracle().expected(corpus().CELLS[cell])


TWO_KINDS: Mapping[str, str] = {
    "the line of column names is empty": "(S5) or (S6)",
    "the file has no line of column names": "(S3) or (S32)",
}
"""The two checks whose kind is the file's, not the check's (review r2 MJ-2 (c)): with no tab in
the line the reference takes for its header and the five after it, its sniffer gives up and it
checks nothing (``structural_oracle.reference_checks_nothing``), so an empty header line or a lone
top line it reads as a header (a byte order mark before the ``#``) is stricter there, and the same
outcome otherwise. ``explain`` holds each such refusal's kind to that predicate."""


def explain(outcomes: Mapping[str, str], cells: Sequence[str]) -> list[str]:
    """Why the map does not explain ``outcomes`` over ``cells``, one line a problem: an outcome
    that is not the predicates' (``structural_oracle.expected``), a refusal whose
    ``(module, code, template, kind)`` is no item of the inventory, an accepted cell that crashed
    the reference, an accepted cell's error no exit's predicate holds for."""
    problems: list[str] = []
    for cell in cells:
        found = outcomes[cell]
        files = corpus().CELLS[cell]
        wanted = wanted_of(cell)
        if found != wanted:
            problems.append(f"{cell}: {found!r} is not the predicates' {wanted!r}")
        recorded = REFERENCE[cell]
        if found == "ok":
            if recorded["crash"]:
                problems.append(f"{cell}: accepted, and the reference crashed")
            for identity, file in recorded["errors"]:
                exits = EXITS.get(identity, ())
                if not any(holds(files, file) for _, holds in exits) and not se1(
                    cell, identity, file
                ):
                    problems.append(f"{cell}: accepted, {identity} in {file} has no exit")
            continue
        written = template_of(found)
        if written is None:
            problems.append(f"{cell}: {found!r} matches no template, or several")
            continue
        item = (written[0], found.partition("|")[0], written[1], kind(cell, found))
        if item not in INVENTORY:
            problems.append(f"{cell}: {item} is no item")
        content = files.get(corpus().SV_FILE)
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
    """What the cells name: the inventory's items, the exits, and for each structural check the
    items its refusals are."""
    items: set[tuple[str, str, str, str]] = set()
    exits: set[str] = set()
    by_check: dict[str, set[str]] = {check.id: set() for check in CHECKS}
    for cell in CELL_IDS:
        found = outcomes[cell]
        if found != "ok":
            written = template_of(found)
            if written is None:
                continue
            item = (written[0], found.partition("|")[0], written[1], kind(cell, found))
            items.add(item)
            if kind(cell, found, rules=False) != item[3]:
                continue  # named by the byte order mark's rule, not by the check's own kind
            label = f"{item[1]} {item[3]}"
            if written[1] in TWO_KINDS:
                label = f"{item[1]} {TWO_KINDS[written[1]]}"
            for check in check_of(found):
                by_check[check.id].add(label)
            continue
        for identity, file in REFERENCE[cell]["errors"]:
            if se1(cell, identity, file):
                exits.add(SE1)
            exits.update(
                head for head, holds in EXITS.get(identity, ()) if holds(corpus().CELLS[cell], file)
            )
    return items, exits, by_check


def judged(outcomes: Mapping[str, str]) -> list[str]:
    """The map's whole judgement: ``explain``; the inventory named exactly; every exit named; each
    structural check named by exactly one item, but those ``HELD``."""
    problems = explain(outcomes, CELL_IDS)
    items, exits, by_check = named(outcomes)
    if items != INVENTORY:
        problems.append(f"the items named {sorted(items ^ INVENTORY)} differ")
    heads = {head for found in EXITS.values() for head, _ in found} | {SE1}
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
    if os.environ.get("AIBI_RECORD_STRUCTURAL_OUTCOMES"):
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
    assert set(ITEMS) == {(code, kind) for _, code, _, kind in INVENTORY}


def test_the_inventory_is_the_code_s() -> None:
    """The templates the walker finds in ``structural`` are exactly the inventory's and ``HELD``'s,
    every template is some check's, and the other modules' templates the inventory names are
    among the shared modules' (D409's sites, unchanged by slice 4)."""
    listed = {template for module, _, template, _ in INVENTORY if module == "structural"}
    assert listed | set(HELD) == TEMPLATES
    assert not listed & set(HELD)
    assert {t for check in CHECKS for t in check.templates} == TEMPLATES
    shared = {template for module, _, template, _ in INVENTORY if module != "structural"}
    assert shared <= SHARED_TEMPLATES


def test_every_held_check_names_the_test_that_holds_it() -> None:
    import ast

    tree = ast.parse((HERE / "test_structural.py").read_text(encoding="utf-8"))
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


def in_d445(template: str) -> str:
    return template.replace("{}", "\u2026")


def d445() -> str:
    text = SPEC.read_text(encoding="utf-8")
    return next(line for line in text.splitlines() if line.startswith("| D445 |"))


def test_d445_lists_the_inventory_the_items_the_exits_and_the_corpus_size() -> None:
    row = d445()
    for _, _, template, _ in INVENTORY:
        assert f"`{in_d445(template)}`" in row, template
    for template in HELD:
        assert f"`{in_d445(template)}`" in row, template
    for head in ITEMS.values():
        assert head in row, head
    for head in {head for found in EXITS.values() for head, _ in found} | {SE1}:
        assert head in row, head
    assert f"a corpus of {len(CELL_IDS)} cells" in row
    listing = row.split("The inventory, by item:", 1)[1].split("Held outside the corpus", 1)[0]
    found = re.findall(r"\((S\d+)\) (.*?)(?= \(S\d+\) |$)", listing)
    segments = dict(found)
    for _, code, template, kind_ in INVENTORY:
        head = ITEMS[(code, kind_)].split(")")[0].lstrip("(")
        assert f"`{in_d445(template)}`" in segments[head], (head, template)


@pytest.mark.parametrize("check", [check.id for check in CHECKS])
def test_dropping_any_check_fails_the_map_alone(
    check: str, onco: ModuleType, tmp_path: Path
) -> None:
    """The drop-each-check property, over every check of ``structural``: with the check's test made
    ``False``, the cells it refused are imported again, and the map alone (``judged``, given the
    fresh outcomes) reports a problem. A check no cell reaches fails here, but those ``HELD``."""
    (found,) = [candidate for candidate in CHECKS if candidate.id == check]
    if set(found.templates) <= set(HELD):
        pytest.skip(f"held by {HELD[found.templates[0]][2]}")
    cells = [cell for cell in CELL_IDS if found in check_of(OUTCOMES[cell])]
    assert cells, f"{check}: no corpus cell reaches it"
    names = ("structural", "study", "importer")
    pack = {name: importlib.import_module(f"aibi.packs.onco.{name}") for name in names}
    with checks().dropped(found, pack):
        fresh = {
            cell: outcome(onco, build(tmp_path / str(index), corpus().CELLS[cell]))
            for index, cell in enumerate(cells)
        }
    assert judged({**OUTCOMES, **fresh}) != [], check


def test_a_narrowed_check_re_recorded_still_fails_the_map() -> None:
    """Review r2 MJ4's probe: the outcomes a reader narrowed to spaces alone would give (every
    cell whose padded spelling of no value is padded by another whitespace accepted), recorded as
    if they were the reader's, still fail the map: the predicates say they must be refused."""
    narrowed = {
        cell: "ok"
        for cell, found in OUTCOMES.items()
        if found.startswith("onco.PADDED_VALUE|the value is a spelling")
        and any(
            character in json.dumps(corpus().CELLS[cell].get("data_sv.txt", b"").decode())
            for character in ("\\u00a0", "\\u3000", "\\u2029")
        )
    }
    assert narrowed
    problems = judged({**OUTCOMES, **narrowed})
    assert any("is not the predicates'" in problem for problem in problems)


def test_the_byte_order_mark_s_rule_holds_where_it_is_true_alone() -> None:
    """Review r2 MJ-2 (b): the rule decides a cell's kind exactly where one leading mark broke a
    required first column (the reference reports its header's errors and nothing past them) and
    the pack refuses a data row; a mark before a non-required first column (``first_free``:
    the reference checks the file, with no error), on line 2, inside a field, or before a control
    character, a column name or a ``#`` after spaces leaves each refusal its own kind."""
    assert bom_rule("bom:padded_null", OUTCOMES["bom:padded_null"])
    assert kind("bom:padded_null", OUTCOMES["bom:padded_null"]) == SAME
    found = OUTCOMES["boms:first_free_padded"]
    assert found.startswith("onco.PADDED_VALUE|")
    assert not bom_rule("boms:first_free_padded", found)
    assert kind("boms:first_free_padded", found) == STRICTER
    for cell in (
        "boms:n1_start_header_control",
        "boms:n1_line2_data_cell",
        "boms:n1_field_data_cell",
        "role:header_after_top_bom_start",
    ):
        assert not bom_rule(cell, OUTCOMES[cell]), cell
    ruled = [cell for cell in CELL_IDS if OUTCOMES[cell] != "ok" and bom_rule(cell, OUTCOMES[cell])]
    assert ruled
    for cell in ruled:
        content = corpus().CELLS[cell][corpus().SV_FILE]
        assert content.startswith(b"\xef\xbb\xbf"), cell
        # the reference's header is the first line with the mark read into it: it lacks a
        # required column (the mark before a required first column, or before a top line)
        names = content.decode("utf-8").splitlines()[0].split("\t")
        assert not {"Sample_Id", "SV_Status"} <= set(names), cell


def test_each_condition_of_the_byte_order_mark_s_rule_is_needed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Review r2 MJ-2 (b), each condition alone (the corpus cannot violate them one at a time,
    since the reference never reports a header's error past a mark that does not lead the file,
    nor anything past a failing header): a recorded outcome made up to break one condition at a
    time is not ruled."""
    module = sys.modules[__name__]
    row = "onco.UNKNOWN_SAMPLE|the sample id is no sample id of the sample file"
    header = [["missing-column", corpus().SV_FILE], ["header-cascade", corpus().SV_FILE]]
    led = b"\xef\xbb\xbf" + corpus().VALID
    cases = {
        "leading": (led, header, row, True),
        "not_leading": (b"#v\n\xef\xbb\xbf" + corpus().VALID, header, row, False),
        "past_the_header": (led, [*header, ["sample-undefined", corpus().SV_FILE]], row, False),
        "no_header_error": (led, [], row, False),
        "not_a_row": (
            led,
            header,
            "onco.CONTROL_CHARACTER|the field holds a control character",
            False,
        ),
        "spaced_comment": (
            led,
            header,
            "onco.COMMENT_LINE|a data line starts with '#' after spaces or tabs",
            False,
        ),
    }
    cells = dict(corpus().CELLS)
    reference = dict(REFERENCE)
    for name, (content, errors, _, _) in cases.items():
        cells[f"fake:{name}"] = corpus().study(data_sv_DOT_txt=content)
        reference[f"fake:{name}"] = {"crash": False, "errors": errors, "rc": 1}
    monkeypatch.setattr(corpus(), "CELLS", cells)
    monkeypatch.setattr(module, "REFERENCE", reference)
    for name, (_, _, found, ruled) in cases.items():
        assert bom_rule(f"fake:{name}", found) is ruled, name


def test_the_header_less_kind_is_held_to_its_predicate(monkeypatch: pytest.MonkeyPatch) -> None:
    """Review r2 MJ-2 (c): an empty header line refused where the reference's sniffer finds a tab
    (so it checks the header and errs) yet recorded with no error is reported, never taken as the
    stricter kind the predicate does not give."""
    module = sys.modules[__name__]
    found = "onco.COLUMN_NAME|the line of column names is empty"
    # without a mark, and with one leading mark (review r3: the check holds under a mark too)
    for prefix in (b"", b"\xef\xbb\xbf"):
        content = prefix + b"\n" + corpus().VALID
        cells = {**corpus().CELLS, "fake:sniffed": corpus().study(data_sv_DOT_txt=content)}
        reference = {**REFERENCE, "fake:sniffed": {"crash": False, "errors": [], "rc": 3}}
        monkeypatch.setattr(corpus(), "CELLS", cells)
        monkeypatch.setattr(module, "REFERENCE", reference)
        kind.cache_clear()
        _class_kind.cache_clear()
        problems = explain({"fake:sniffed": found}, ["fake:sniffed"])
        assert any("where the reference checks nothing is False" in p for p in problems), prefix
    kind.cache_clear()
    _class_kind.cache_clear()


def test_se1_and_s33_are_held_to_the_twin(monkeypatch: pytest.MonkeyPatch) -> None:
    """Review r3 MJ-3: SE1 explains an error on a file one mark leads exactly when the twin (the
    file without the mark) lacks it, whatever its identity; (S33) names a refusal exactly when the
    twin's reference errs in its class, and never on two marks or a file with no twin."""
    module = sys.modules[__name__]
    led = b"\xef\xbb\xbf" + corpus().VALID
    sv = corpus().SV_FILE
    cells = dict(corpus().CELLS)
    reference = dict(REFERENCE)
    for name, content, errors, twin_errors in (
        ("absent", led, [["sv-no-gene", sv]], []),
        ("present", led, [["sv-no-gene", sv]], [["sv-no-gene", sv]]),
        ("two", b"\xef\xbb\xbf" + led, [["sv-no-gene", sv]], []),
        ("hidden", led, [], [["duplicate-column", sv]]),
        ("not_hidden", led, [], []),
        ("line2", b"#v\n" + led, [["sv-no-gene", sv]], []),
    ):
        cells[f"fake:{name}"] = corpus().study(data_sv_DOT_txt=content)
        cells[f"twin:fake:{name}"] = corpus().study(data_sv_DOT_txt=content[3:])
        reference[f"fake:{name}"] = {"crash": False, "errors": errors, "rc": 1}
        reference[f"twin:fake:{name}"] = {"crash": False, "errors": twin_errors, "rc": 1}
    monkeypatch.setattr(corpus(), "CELLS", cells)
    monkeypatch.setattr(module, "REFERENCE", reference)
    assert se1("fake:absent", "sv-no-gene", sv)
    assert not se1("fake:present", "sv-no-gene", sv)
    assert not se1("fake:two", "sv-no-gene", sv)
    assert not se1("fake:absent", "sv-no-gene", "other.txt")
    assert not se1("fake:line2", "sv-no-gene", sv)
    assert not marked("fake:line2", "onco.DUPLICATE_COLUMN|the column name repeats an earlier's")
    found = "onco.DUPLICATE_COLUMN|the column name repeats an earlier's"
    assert marked("fake:hidden", found)
    assert not marked("fake:not_hidden", found)
    assert not marked("fake:two", found)


def test_the_byte_order_mark_s_exit_is_one_leading_mark() -> None:
    """Review r2 MJ-2 (a): SE1 holds on a file one mark leads, never on two marks or a mark
    elsewhere."""
    for cell in CELL_IDS:
        content = corpus().CELLS[cell].get(corpus().SV_FILE)
        if not isinstance(content, bytes):
            continue
        one = content.startswith(b"\xef\xbb\xbf") and not content.startswith(b"\xef\xbb\xbf" * 2)
        assert _bom(corpus().CELLS[cell], corpus().SV_FILE) == one, cell
    assert OUTCOMES["boms:n2_start_header_none"] != "ok"


def test_the_duplicate_predicate_is_the_reference_s_own() -> None:
    """Review r2 MJ1, r3 m9: SE2's predicate (``structural_oracle.duplicate_rows``: the fields, as
    split after universal-newline splitting, of the ``UNIQUE_KEY`` columns the header holds)
    holds on exactly the accepted cells whose reference run reports a duplicate, so that it is
    neither looser (stripped cells, every column) nor tighter (fields keeping ``\\r``)."""
    checked = 0
    for cell in CELL_IDS:
        recorded = REFERENCE[cell]
        identities = {identity for identity, _ in recorded["errors"]}
        if OUTCOMES[cell] != "ok" or "header-cascade" in identities:
            continue
        content = corpus().CELLS[cell].get(corpus().SV_FILE)
        if not isinstance(content, bytes):
            continue
        assert oracle().duplicate_rows(content) == ("sv-duplicate" in identities), cell
        checked += 1
    assert checked > 100


def test_the_predicates_import_nothing_of_the_pack() -> None:
    """Review r3 m7: the predicates are the test's own, never ``structural``'s."""
    import ast

    tree = ast.parse((HERE / "structural_oracle.py").read_text(encoding="utf-8"))
    names = [
        node.module if isinstance(node, ast.ImportFrom) else alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import | ast.ImportFrom)
        for alias in node.names
    ]
    assert not any(str(name).startswith("aibi.packs") for name in names), names


def test_slice_1b_s_walker_lists_are_unchanged() -> None:
    """Review r3 MJ-B: ``structural`` joins neither of slice 1b's module lists."""
    walker = checks().WALKER
    assert walker.READERS == ("mutation", "caselists")
    assert walker.OTHERS == ("study", "meta", "importer")
    assert checks().MODULES == ("structural",)


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


PUBLISHED_GENERATORS = ("byte", "header", "namespace", "build", "dup", "meta")


def published_cells() -> list[str]:
    chosen = []
    kinds: set[str] = set()
    for cell in CELL_IDS:
        if OUTCOMES.get(cell) != "ok":
            continue
        generator, case = cell.split(":", 1)
        if generator in PUBLISHED_GENERATORS:
            chosen.append(cell)
        elif generator == "cell":
            token_class = case.split("_", 1)[0]
            if token_class not in kinds:
                kinds.add(token_class)
                chosen.append(cell)
    return chosen


@pytest.mark.parametrize("cell", published_cells())
def test_an_accepted_cell_publishes(cell: str, world: Any, registry: PackRegistry) -> None:
    directory = build(world.root / "cell", corpus().CELLS[cell])
    world.publish("study", directory, registry, pack="onco")

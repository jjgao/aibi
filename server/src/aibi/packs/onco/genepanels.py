"""A study's gene panel definitions and the MAF's coverage (SPEC D448, M4.2b-2b-ii slice B).

Discovery and the importer reach this module through calls that hold no check of their own
(``entry`` from ``study.discover``; ``definitions``, ``genes``, ``coverage`` and ``covered`` from
the importer): every check of the ``gene_panels`` directory, of its definitions and of the
coverage is here, so that ``study``, ``meta`` and ``importer`` gain no check site, and D447's
matrix reader (``panels``) keeps its own corpus and inventory.

**The entry** (``entry``, discovery's phase 12): ``gene_panels`` by its exact name; a directory is
the study's definitions; a file, a symlink or any other kind is ``GENE_PANELS``.

**The definitions** (``definitions``, after the gene panel matrix and before the MAF): the study
must have a mutation meta, a matrix and its column ``mutations`` (``GENE_PANELS``); the directory
is listed once, names starting ``.`` or ending ``~`` skipped, in the order of the names, at most
``DEFINITION_FILES_MOST`` entries and at least one (``GENE_PANELS``), each a regular file whose
name is Unicode text of at most 200 characters (``GENE_PANEL``), all checked before a file is
read; each file is read at most ``DEFINITION_MOST`` bytes and the ``DEFINITIONS_MOST`` the files
before it leave (``GENE_PANEL``, ``GENE_PANELS``), or the operator's ``import_bytes``. A file is
read (``parse``) in this order, each item refused with its code:

1. the file: empty (``GENE_PANEL``); ``NOT_UTF8`` (each line strict UTF-8); ``BYTE_ORDER_MARK``;
   ``LINE_ENDS`` (``\r\n``, ``\n``, ``\r``: one kind; a last line without one is read);
   ``CONTROL_CHARACTER`` (the 30 controls, U+0085, U+2028, U+2029, U+FEFF in any line);
2. each line, a line blank by ``str.strip()`` skipped: no ``:`` (a wrapped line), a key with
   padding (whitespace, a control or format character or a code point drawn as nothing at an
   edge, ``mutation.edged``), ``cancer_study_identifier`` (named), a key not of ``KNOWN_KEYS``,
   a repeated key, a value with padding past the one optional space after ``:`` (``GENE_PANEL``);
3. ``stable_id`` and ``gene_list`` present (``GENE_PANEL``);
4. ``stable_id``: empty, a character outside ``PANEL_ID``, over 200 characters, a word of
   ``NO_PANEL_WORDS`` in any case (``PANEL_ID``);
5. ``description``: over a text's length or not Unicode text; ``number_of_profiled_coding_
   base_pairs``: not 1 to 18 ASCII digits (``GENE_PANEL``; both read and never used or served);
6. ``gene_list``: empty (``GENE_LIST``); its genes counted from its tabs before it is split,
   over ``PANEL_GENES_MOST`` or over what the definitions before it leave of ``GENES_MOST``
   (``TOO_MANY_GENES``); then each gene in turn: empty, padded (``GENE_LIST``), holding ``,``,
   ``;`` or ``|`` (``COMBINED_TOKEN``), all digits, an Entrez id (``GENE_LIST``), outside
   ``GENE`` (``TOKEN_CLASS``), over ``GENE_MOST`` characters, a word of no gene in any case, a
   repeat (``GENE_LIST``).

Across the files, as each is read: a ``stable_id`` equal to an earlier one, or equal in lower
case (``PANEL_ID``); a gene equal in lower case to a gene of the definitions but not as written
(``GENE_SPELLING``). Last, some sample's matrix ``mutations`` cell must be a defined
``stable_id`` as written (``GENE_PANELS``). A cell that is not one (``NA``, ``WGS``, ``WXS``,
``WXS/WGS``, one that differs from a defined id only in case, an undefined id) leaves its sample
out of the assignment, counted by reason (``LEFT_OUT``; the owner's D7).

**The coverage** (``coverage``, after the MAF): the row-kind filter is the MAF's
``Variant_Classification`` and, where the file has it, ``Mutation_Status`` values as written
(``mutation.MafFilter``); it is withheld, and the study imports without a coverage, where a
filtered column is no category by D228's rule, a value is not a ``FILTER_VALUE`` of at most 64
characters, or two values are equal in lower case (``_withheld``). Where it is built, a MAF row
whose symbol is a defined gene's but for case, width or invisible code points
(``Maf.misspelled``) is ``GENE_SPELLING`` (a refusal of the whole import, where a filter's
problem only withholds the coverage: D448's stated asymmetry), and the two tables' cells, counted
as the core counts them after the MAF's, past ``import_cells`` are ``TOO_MANY_CELLS``.

No message holds a value read from a file: a file is named as data (``refusals.shown``), a
place by its line and column. The module keeps no cache past a read (D438): the definitions
are held while the import runs, bounded as above.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from aibi.core.importers.errors import ImportRefused
from aibi.core.importers.infer import CATEGORY_VALUES
from aibi.core.schema.jsonio import is_text
from aibi.core.schema.limits import MAX_TEXT, ImportLimits
from aibi.core.schema.pack_api import ConfinedPath, DirectoryEntry, SourceReader
from aibi.core.schema.refusals import RefusalCode
from aibi.packs.onco.clinical import spans
from aibi.packs.onco.mutation import HUGO, Defined, Maf, edged
from aibi.packs.onco.panels import BOM, NA, REFUSED, TAB, Matrix
from aibi.packs.onco.refusals import SHOWN_MOST, Code, entry_refused, placed
from aibi.packs.onco.text import written

GENE_PANELS = "gene_panels"
"""The entry of a study's directory that holds its gene panel definitions, by its exact name (D448;
the owner's convention: cBioPortal's importer reads definitions it is given apart)."""
DEFINITION_FILES_MOST = 256
"""The most entries of ``gene_panels/`` the pack reads (stricter, D448)."""
DEFINITION_MOST = 2**20
"""The most bytes of a definition file (stricter; the largest of datahub's 63 is 25.7 KB)."""
DEFINITIONS_MOST = 16 * 2**20
"""The most bytes of all the definitions together, held by a cap each read shrinks."""
GENES_MOST = 1_000_000
"""The most genes all the definitions may list together, counted from each list's tabs before it
is split (datahub's 63 list 22,745)."""
PANEL_GENES_MOST = 50_000
"""The most genes one definition may list (datahub's largest lists 4,047)."""
GENE_MOST = 100
"""The most characters of a gene (datahub's longest is 15)."""
PANEL_ID_MOST = 200
"""The most characters of a definition's ``stable_id``."""
STABLE_ID = "stable_id"
GENE_LIST = "gene_list"
DESCRIPTION = "description"
BASE_PAIRS = "number_of_profiled_coding_base_pairs"
STUDY_KEY = "cancer_study_identifier"
KNOWN_KEYS = (STABLE_ID, DESCRIPTION, GENE_LIST, BASE_PAIRS)
"""The keys a definition may hold: ``stable_id`` and ``gene_list`` required, the other two read,
checked and never used nor served (D448)."""
PANEL_ID = re.compile(r"[A-Za-z0-9_.-]+")
"""A definition's ``stable_id``: ASCII letters, digits, ``_``, ``.`` and ``-``."""
GENE = re.compile(r"[A-Za-z][A-Za-z0-9._-]*")
"""A listed gene: an ASCII letter, then ASCII letters, digits, ``.``, ``_`` and ``-`` (every one
of datahub's 22,745 tokens is one)."""
DIGITS = re.compile(r"[0-9]+")
"""An Entrez id, which the pack does not read as a gene (D448: symbols as written)."""
COMBINED = re.compile(r"[,;|]")
"""A character that joins genes in other formats; a definition separates them by tabs alone."""
BASE_PAIRS_VALUE = re.compile(r"[0-9]{1,18}")
NO_PANEL_WORDS = ("na", "wgs", "wxs")
"""The matrix's words of no panel or every gene, in lower case: no ``stable_id`` may be one."""
NO_GENE_WORDS = ("na", "unknown")
"""The MAF's spellings of no gene (``Hugo_Symbol``'s ``NA`` and ``Unknown``), in lower case: no
listed gene may be one (``0``, its Entrez id's, is all digits)."""
WHOLE = ("WGS", "WXS", "WXS/WGS")
"""The matrix cells cBioPortal reads as every gene; the pack has no definition of them (D7)."""
HIDDEN = "."
BACKUP = "~"
MUTATION_ASSIGNMENT = "mutation_assignment"
MUTATION_GROUPS = "mutation_groups"
ASSIGNMENT_CELLS = 2
"""The assignment table's columns: ``sample_id`` and ``panel``."""
GROUP_CELLS = 2
"""The groups table's columns: ``panel`` and ``gene``."""
FILTER_VALUE = re.compile(r"[A-Za-z0-9_'.-]+")
"""A value a row-kind filter may hold: one token of ASCII letters, digits, ``_``, ``'``, ``.``
and ``-`` (``3'UTR``); a value with an inner space is held only if it is one of ``MAF_KINDS``
(``Post-transcriptional modification``), so no phrase is served from a row (D448)."""
FILTER_VALUE_MOST = 64
MAF_KINDS = frozenset(
    {
        "Frame_Shift_Del",
        "Frame_Shift_Ins",
        "In_Frame_Del",
        "In_Frame_Ins",
        "Missense_Mutation",
        "Nonsense_Mutation",
        "Silent",
        "Splice_Site",
        "Splice_Region",
        "Translation_Start_Site",
        "Nonstop_Mutation",
        "3'UTR",
        "3'Flank",
        "5'UTR",
        "5'Flank",
        "IGR",
        "Intron",
        "RNA",
        "Targeted_Region",
        "De_novo_Start_InFrame",
        "De_novo_Start_OutOfFrame",
        "None",
        "Germline",
        "Somatic",
        "LOH",
        "Post-transcriptional modification",
        "Wildtype",
        "UNCALLED",
    }
)
"""The MAF format's variant classifications and mutation statuses, as its documentation (the
format's and cBioPortal's) names them: a filter value outside them is counted in a note, never
refused (D448: two spellings of one kind are a stated residual)."""
_DIRECTORY_KIND: Mapping[str, str] = MappingProxyType(
    {
        "file": "is a file, not the directory of the study's gene panel definitions",
        "symlink": "is a symlink, which the pack never follows",
        "other": "is not a directory",
        "unconfined": "cannot be confined",
    }
)
_ENTRY_KIND: Mapping[str, str] = MappingProxyType(
    {
        "directory": "is a directory, not a gene panel definition",
        "symlink": "is a symlink, which the pack never follows",
        "other": "is not a regular file",
        "unconfined": "cannot be confined",
        "file": "cannot be confined",
    }
)
EDGED_TEXT = (
    "has whitespace, a control or format character, or a code point drawn as nothing at its "
    "start or end, beside the one space a definition's line may hold after ':'"
)
NO_DEFINITION = (
    "has no definition, so its samples are left out of the mutations' coverage and their "
    "mutations are UNKNOWN where no row says otherwise (D448)"
)
LEFT_OUT: Mapping[str, str] = MappingProxyType(
    {
        "na": "Samples whose gene panel matrix mutations cell is NA, which gives no panel",
        "whole": (
            "Samples whose gene panel matrix mutations cell is WGS, WXS or WXS/WGS, which "
            "cBioPortal reads as every gene and the pack has no definition of"
        ),
        "near": (
            "Samples whose gene panel matrix mutations cell differs from a defined panel's "
            "stable_id only in case, which the pack reads as no definition"
        ),
        "undefined": "Samples whose gene panel matrix mutations cell names no defined panel",
    }
)
"""The reasons a sample's ``mutations`` cell leaves it out of the assignment (the owner's D7), each
a note's text whose count D277 suppresses under *k*; the order is the notes'."""


@dataclass(frozen=True)
class Definition:
    """A gene panel definition, read: its file's name, its ``stable_id`` and its genes as listed."""

    file: str
    stable_id: str
    genes: tuple[str, ...]


@dataclass(frozen=True)
class Definitions:
    """A study's gene panel definitions, read (D448): each in the order of their files' names, the
    samples assigned a defined panel (``assignment``, in the matrix's order), the samples left out
    by reason (``left``, keys of ``LEFT_OUT``, counts above 0), the definitions no sample is
    assigned (``unused``), and their genes as the MAF's reader compares symbols with them."""

    panels: tuple[Definition, ...]
    assignment: tuple[tuple[str, str], ...]
    left: tuple[tuple[str, int], ...]
    unused: int
    defined: Defined

    @property
    def groups(self) -> tuple[tuple[str, str], ...]:
        """The groups table's rows: each definition's ``stable_id`` and each of its genes."""
        return tuple((panel.stable_id, gene) for panel in self.panels for gene in panel.genes)


@dataclass(frozen=True)
class Coverage:
    """The MAF's coverage (D448): the definitions, and either the row-kind filter, by the MAF's
    column name (``record_filter``), with the filtered columns' cells empty or a spelling of no
    value (``unset``) and the filter's values outside ``MAF_KINDS`` (``outside``), or the reason
    it is withheld (``withheld``), when its tables and descriptor are not built."""

    definitions: Definitions
    record_filter: tuple[tuple[str, tuple[str, ...]], ...]
    unset: tuple[tuple[str, int], ...]
    outside: int
    withheld: str | None

    @property
    def cells(self) -> int:
        """The cells the core counts for the coverage's two tables, or none where withheld."""
        if self.withheld is not None:
            return 0
        rows = self.definitions
        return ASSIGNMENT_CELLS * max(1, len(rows.assignment)) + GROUP_CELLS * max(
            1, len(rows.groups)
        )


def entry(listed: Mapping[str, DirectoryEntry]) -> DirectoryEntry | None:
    """The study's ``gene_panels`` entry, from the listing of its directory by name, or ``None``:
    a directory, by its exact name (no case or normalisation sibling is looked up); a file, a
    symlink or any other kind in its place is ``GENE_PANELS`` (D448)."""
    found = listed.get(GENE_PANELS)
    if found is None:
        return None
    if found.kind != "directory":
        raise entry_refused("GENE_PANELS", found.name, _DIRECTORY_KIND[found.kind])
    return found


def _content(reader: SourceReader, found: DirectoryEntry, limits: ImportLimits, left: int) -> bytes:
    """A definition's bytes, read at most ``DEFINITION_MOST`` and the ``left`` bytes of
    ``DEFINITIONS_MOST`` the files before it leave, or the operator's ``import_bytes`` where that
    is smaller (whose refusal then passes out)."""
    assert found.path is not None
    cap = min(DEFINITION_MOST, left)
    try:
        return reader.read(found.path, min(cap, limits.import_bytes))
    except ImportRefused as error:
        mine = limits.import_bytes > cap and bool(error.refusals)
        if not (mine and all(r.code == RefusalCode.LIMIT_EXCEEDED for r in error.refusals)):
            raise
        if cap == DEFINITION_MOST:
            raise entry_refused(
                "GENE_PANEL", found.name, "is larger than a gene panel definition may be"
            ) from None
        raise entry_refused(
            "GENE_PANELS",
            GENE_PANELS,
            "holds definitions of more bytes together than the pack reads",
        ) from None


def _lines(name: str, data: bytes) -> list[tuple[int, str]]:
    """A definition's lines (D448), checked as a whole file first: not empty, strict UTF-8 line by
    line (``NOT_UTF8``), no byte order mark (``BYTE_ORDER_MARK``), line ends of one kind, a last
    line without one accepted (``LINE_ENDS``), and no control character, line separator or byte
    order mark in any line (``CONTROL_CHARACTER``)."""
    if not data:
        raise entry_refused("GENE_PANEL", name, "is empty")
    view = memoryview(data)
    for number, start, end in spans(data):
        try:
            str(view[start:end], "utf-8")
        except UnicodeDecodeError:
            raise placed("NOT_UTF8", name, number, None, "the line is not UTF-8") from None
    if data.startswith(BOM.encode()):
        raise entry_refused("BYTE_ORDER_MARK", name, "starts with a byte order mark")
    crlf = data.count(b"\r\n")
    kinds = (crlf > 0) + (data.count(b"\n") > crlf) + (data.count(b"\r") > crlf)
    if kinds > 1:
        raise entry_refused("LINE_ENDS", name, "has line ends of more than one kind")
    found: list[tuple[int, str]] = []
    for number, start, end in spans(data):
        line = str(view[start:end], "utf-8")
        if REFUSED.search(line) is not None:
            raise placed(
                "CONTROL_CHARACTER",
                name,
                number,
                None,
                "the line holds a control character, a line separator or a byte order mark",
            )
        found.append((number, line))
    return found


def _pairs(name: str, data: bytes) -> dict[str, tuple[int, str]]:
    """A definition's keys, each to its line and value (D448): a line blank by ``str.strip()`` is
    skipped; any other is split at its first ``:``, the key as written (no padding), one of
    ``KNOWN_KEYS`` (``cancer_study_identifier`` refused by name), never repeated, then one
    optional space, and the value with no padding (``GENE_PANEL``)."""
    found: dict[str, tuple[int, str]] = {}
    for number, line in _lines(name, data):
        if line.strip() == "":
            continue
        key, colon, rest = line.partition(":")
        if not colon:
            raise placed("GENE_PANEL", name, number, None, "the line has no ':'")
        if edged(key):
            raise placed("GENE_PANEL", name, number, None, "the key " + EDGED_TEXT)
        if key == STUDY_KEY:
            raise placed(
                "GENE_PANEL",
                name,
                number,
                None,
                "the key cancer_study_identifier is not one a gene panel definition the pack "
                "reads may hold",
            )
        if key not in KNOWN_KEYS:
            raise placed(
                "GENE_PANEL",
                name,
                number,
                None,
                "the key is not one the pack reads; it reads " + ", ".join(KNOWN_KEYS),
            )
        if key in found:
            raise placed("GENE_PANEL", name, number, None, "the key repeats an earlier line's")
        value = rest[1:] if rest.startswith(" ") else rest
        if edged(value):
            raise placed("GENE_PANEL", name, number, None, "the value " + EDGED_TEXT)
        found[key] = (number, value)
    for key in (STABLE_ID, GENE_LIST):
        if key not in found:
            raise entry_refused("GENE_PANEL", name, f"lacks the key {key}")
    return found


def _gene_failure(gene: str) -> tuple[Code, str] | None:
    """A listed gene's first failure (D448), in this order, or ``None``."""
    if gene == "":
        return "GENE_LIST", "the list holds an empty gene (two tabs together)"
    if edged(gene):
        return "GENE_LIST", "the gene " + EDGED_TEXT
    if COMBINED.search(gene) is not None:
        return (
            "COMBINED_TOKEN",
            "the gene holds ',', ';' or '|'; a definition separates its genes by tabs alone",
        )
    if DIGITS.fullmatch(gene) is not None:
        return "GENE_LIST", "the gene is all digits; the pack reads gene symbols, not Entrez ids"
    if GENE.fullmatch(gene) is None:
        return (
            "TOKEN_CLASS",
            "the gene is not an ASCII letter followed by ASCII letters, digits, '.', '_' and '-'",
        )
    if len(gene) > GENE_MOST:
        return "GENE_LIST", "the gene is longer than 100 characters"
    if gene.lower() in NO_GENE_WORDS:
        return "GENE_LIST", "the gene is a spelling the MAF reads as no gene"
    return None


def parse(name: str, data: bytes, genes_left: int = GENES_MOST) -> Definition:
    """The gene panel definition ``name``, read from its bytes ``data`` (D448), when it may list
    ``genes_left`` genes more; raises ``Refused`` in the order the module's docstring gives."""
    found = _pairs(name, data)
    line, stable = found[STABLE_ID]
    if stable == "":
        raise placed("PANEL_ID", name, line, None, "the stable_id is empty")
    if PANEL_ID.fullmatch(stable) is None:
        raise placed(
            "PANEL_ID",
            name,
            line,
            None,
            "the stable_id holds a character other than ASCII letters, digits, '_', '.' and '-'",
        )
    if len(stable) > PANEL_ID_MOST:
        raise placed("PANEL_ID", name, line, None, "the stable_id is longer than 200 characters")
    if stable.lower() in NO_PANEL_WORDS:
        raise placed(
            "PANEL_ID",
            name,
            line,
            None,
            "the stable_id is a word the gene panel matrix reads as no panel or every gene",
        )
    if DESCRIPTION in found:
        line, value = found[DESCRIPTION]
        if len(value) > MAX_TEXT or written(value, MAX_TEXT, 0) is None:
            raise placed(
                "GENE_PANEL",
                name,
                line,
                None,
                "the description is longer than a text may be, or not Unicode text",
            )
    if BASE_PAIRS in found:
        line, value = found[BASE_PAIRS]
        if BASE_PAIRS_VALUE.fullmatch(value) is None:
            raise placed(
                "GENE_PANEL",
                name,
                line,
                None,
                "the number_of_profiled_coding_base_pairs is not 1 to 18 ASCII digits",
            )
    line, listed = found[GENE_LIST]
    if listed == "":
        raise placed("GENE_LIST", name, line, None, "the gene_list is empty")
    count = listed.count(TAB) + 1
    if count > PANEL_GENES_MOST:
        raise placed(
            "TOO_MANY_GENES", name, line, None, "the list holds more genes than a panel may"
        )
    if count > genes_left:
        raise placed(
            "TOO_MANY_GENES",
            name,
            line,
            None,
            "the definitions list more genes together than the pack reads",
        )
    genes = listed.split(TAB)
    seen: set[str] = set()
    for column, gene in enumerate(genes, start=1):
        failure = _gene_failure(gene)
        if failure is not None:
            raise placed(failure[0], name, line, column, failure[1])
        if gene in seen:
            raise placed("GENE_LIST", name, line, column, "the gene repeats an earlier gene")
        seen.add(gene)
    return Definition(name, stable, tuple(genes))


def _listed(reader: SourceReader, source: ConfinedPath) -> list[DirectoryEntry]:
    """The definitions' entries: one listing of ``gene_panels/``, its names starting ``.`` or
    ending ``~`` skipped, in the order of the names; each a regular file whose name is Unicode
    text of at most 200 characters."""
    directory = ConfinedPath(Path(source) / GENE_PANELS)
    entries = sorted(
        (
            found
            for found in reader.files(directory)
            if not (found.name.startswith(HIDDEN) or found.name.endswith(BACKUP))
        ),
        key=lambda found: found.name,
    )
    if len(entries) > DEFINITION_FILES_MOST:
        raise entry_refused(
            "GENE_PANELS", GENE_PANELS, "holds more entries than the pack reads as definitions"
        )
    if not entries:
        raise entry_refused("GENE_PANELS", GENE_PANELS, "holds no gene panel definition")
    for found in entries:
        if found.kind != "file" or found.path is None:
            raise entry_refused("GENE_PANEL", found.name, _ENTRY_KIND[found.kind])
        if len(found.name) > SHOWN_MOST or not is_text(found.name):
            raise entry_refused(
                "GENE_PANEL",
                found.name,
                "has a name that is not Unicode text of at most 200 characters",
            )
    return entries


def definitions(
    reader: SourceReader,
    source: ConfinedPath,
    found: DirectoryEntry | None,
    mutation: object | None,
    matrix: Matrix | None,
    limits: ImportLimits,
) -> Definitions | None:
    """The study's gene panel definitions (D448), or ``None`` where it has no ``gene_panels``
    entry (``found``, ``entry``'s): read after the gene panel matrix and before the MAF. The study
    must have a mutation meta (``mutation``), a matrix and its column ``mutations``
    (``GENE_PANELS``); each definition is read in the order of the names (``parse``); no two
    ``stable_id``s may be equal, or equal in lower case (``PANEL_ID``), nor two genes of the
    definitions differ only in case (``GENE_SPELLING``); and some sample's ``mutations`` cell must
    be a defined ``stable_id`` as written (``GENE_PANELS``), checked last."""
    if found is None:
        return None
    if mutation is None:
        raise entry_refused(
            "GENE_PANELS",
            found.name,
            "holds gene panel definitions, and the study has no mutation meta file for them",
        )
    if matrix is None:
        raise entry_refused(
            "GENE_PANELS",
            found.name,
            "holds gene panel definitions, and the study has no gene panel matrix to assign them",
        )
    if matrix.assigned is None:
        raise entry_refused(
            "GENE_PANELS",
            found.name,
            "holds gene panel definitions, and the gene panel matrix has no mutations column",
        )
    panels: list[Definition] = []
    ids: dict[str, str] = {}
    genes: dict[str, str] = {}
    left = DEFINITIONS_MOST
    listed = 0
    for item in _listed(reader, source):
        data = _content(reader, item, limits, left)
        left -= len(data)
        panel = parse(item.name, data, GENES_MOST - listed)
        listed += len(panel.genes)
        earlier = ids.get(panel.stable_id.lower())
        if earlier is not None:
            same = earlier == panel.stable_id
            raise entry_refused(
                "PANEL_ID",
                item.name,
                "repeats another definition's stable_id"
                if same
                else "has a stable_id that differs from another definition's only in case",
            )
        ids[panel.stable_id.lower()] = panel.stable_id
        for gene in panel.genes:
            spelled = genes.setdefault(gene.lower(), gene)
            if spelled != gene:
                raise entry_refused(
                    "GENE_SPELLING",
                    item.name,
                    "lists a gene that differs from a gene of the definitions only in case",
                )
        panels.append(panel)
    defined = {panel.stable_id for panel in panels}
    assignment: list[tuple[str, str]] = []
    counts = dict.fromkeys(LEFT_OUT, 0)
    for sample, cell in matrix.assigned:
        if cell in defined:
            assignment.append((sample, cell))
        elif cell == NA:
            counts["na"] += 1
        elif cell in WHOLE:
            counts["whole"] += 1
        elif cell.lower() in ids:
            counts["near"] += 1
        else:
            counts["undefined"] += 1
    if not assignment:
        raise entry_refused(
            "GENE_PANELS",
            found.name,
            "holds gene panel definitions, and no sample's mutations cell in the gene panel "
            "matrix is a defined stable_id",
        )
    used_ids = {panel for _, panel in assignment}
    longest = max(len(gene) for gene in genes.values())
    return Definitions(
        tuple(panels),
        tuple(assignment),
        tuple((reason, count) for reason, count in counts.items() if count),
        sum(1 for panel in panels if panel.stable_id not in used_ids),
        Defined(MappingProxyType(genes), frozenset(genes.values()), longest),
    )


def genes(found: Definitions | None) -> Defined | None:
    """The definitions' genes, as the MAF's reader compares symbols with them, or ``None``."""
    return None if found is None else found.defined


def _withheld(maf: Maf) -> str | None:
    """Why the MAF's row-kind filter cannot be built (D448), or ``None``: each filtered column
    must be a category by D228's rule (a column with no present cell, or a MAF with no row, is
    not), each of its values a ``FILTER_VALUE`` of at most ``FILTER_VALUE_MOST`` characters, and
    no two of them equal in lower case."""
    categories = {column.name: column.category for column in maf.columns}
    for kind in maf.filters:
        if not categories[kind.name]:
            return f"the column {kind.name} is not a category by D228's rule"
        if len(kind.values) > CATEGORY_VALUES or any(
            len(value) > FILTER_VALUE_MOST
            or (value not in MAF_KINDS and FILTER_VALUE.fullmatch(value) is None)
            for value in kind.values
        ):
            return (
                f"a value of the column {kind.name} is not an identifier of at most 64 "
                "characters (letters, digits, '_', \"'\", '.', '-': one token, or a known kind)"
            )
        if len({value.lower() for value in kind.values}) != len(kind.values):
            return f"two values of the column {kind.name} differ only in case"
    return None


def coverage(
    found: Definitions | None, maf: Maf | None, limits: ImportLimits, cells_before: int
) -> Coverage | None:
    """The MAF's coverage from the definitions ``found`` (D448), after the MAF's read and before
    the SV file's and the CNA matrix's, or ``None`` where the study has no definitions. Where the
    filter cannot be built the coverage is withheld (``Coverage.withheld``) and the study imports
    without it; where it is built, a MAF row whose symbol is a defined gene's but for case, width
    or invisible code points is ``GENE_SPELLING``, and the coverage's cells counted after
    ``cells_before`` and the MAF's past ``import_cells`` are ``TOO_MANY_CELLS``, naming the
    table that passes them."""
    if found is None or maf is None:
        return None
    withheld = _withheld(maf)
    if withheld is not None:
        return Coverage(found, (), (), 0, withheld)
    if maf.misspelled:
        hugo = [column.name for column in maf.columns].index(HUGO) + 1
        raise placed(
            "GENE_SPELLING",
            maf.name,
            maf.misspelled_line,
            hugo,
            "the gene symbol is a defined gene's but for case, width or invisible code points, "
            "and not as the definition writes it",
        )
    left = limits.import_cells - cells_before - maf.cells
    assignment = ASSIGNMENT_CELLS * max(1, len(found.assignment))
    for table, cells in (
        (MUTATION_ASSIGNMENT, assignment),
        (MUTATION_GROUPS, assignment + GROUP_CELLS * max(1, len(found.groups))),
    ):
        if cells > left:
            raise entry_refused(
                "TOO_MANY_CELLS", table, "takes the study's files past the cells an import may"
            )
    record_filter = tuple((kind.name, kind.values) for kind in maf.filters)
    unset = tuple((kind.name, kind.unset) for kind in maf.filters if kind.unset)
    outside = sum(1 for _, values in record_filter for value in values if value not in MAF_KINDS)
    return Coverage(found, record_filter, unset, outside, None)


def covered(found: Coverage | None) -> int:
    """The cells the core counts for the coverage's tables, or none."""
    return 0 if found is None else found.cells


__all__ = [
    "DEFINITIONS_MOST",
    "DEFINITION_FILES_MOST",
    "DEFINITION_MOST",
    "GENES_MOST",
    "GENE_PANELS",
    "MUTATION_ASSIGNMENT",
    "MUTATION_GROUPS",
    "PANEL_GENES_MOST",
    "Coverage",
    "Definition",
    "Definitions",
    "coverage",
    "covered",
    "definitions",
    "entry",
    "genes",
    "parse",
]

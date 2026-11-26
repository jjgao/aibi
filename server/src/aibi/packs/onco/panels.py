"""A cBioPortal study's gene panel matrix: the ``GENE_PANEL_MATRIX`` meta and its data file (SPEC
D447, M4.2b-2a; the reference's ``GenePanelMatrixValidator``, V:3343-3433, with its base
``Validator``, V:361-720, the study-wide dictionary of meta files it reads, V:4740-4778, the
meta reading of C:826-1000 and its place among the files, V:5579-5586).

Discovery and the importer reach this module through calls that hold no check of their own
(``discovered``, ``used``, ``noted``, ``imported``): every check of the matrix meta and of its data
file is here. The matrix is a table and nothing more (D447): it declares no coverage, reads no
gene panel definition and claims nothing about what a panel assays. From D448 it hands its rows'
``mutations`` cells to ``genepanels`` (``Matrix.assigned``), which reads the study's definitions
and builds the MAF's coverage from them; ``used`` takes the ``gene_panels`` entry too.

**The meta** (``discovered``, after the discrete copy-number meta's): a second matrix meta is
``DUPLICATE_PROFILE_META`` (the reference validates neither, V:5582); the data file is resolved
by phase 9's rule over Reads(S) (``study``'s ``_path_value``, given as ``check``) and must be an
exact listed regular file, which then joins Reads(S). A ``gene_panel`` key on any meta is
accepted, noted (``noted``) and never read.

**The dictionary** (``Profiles``): the reference keeps every meta file it types and that holds the
key ``stable_id`` in one dictionary, by its ``stable_id`` stripped, the last of a repeated id
winning (V:4746-4778), and reads it in the matrix's header check (V:3363-3373). ``profiles``
builds it as the reference does, on the reference's reading of each meta (``reference_meta``: a
byte order mark stays in the first key, which the pack's ``meta.parse`` reads past; the keys as
written, the values stripped) and its typing (``reference_kind``, C:643-729, C:826-1000). The
header check's loop raises where an entry lacks ``genetic_alteration_type``, or holds
``MUTATION_EXTENDED`` without ``datatype``: ``Profiles.crashes``, a model of V:3363-3373, so that
the item below fires exactly where the reference crashes.

**Lines** are read as ``cna`` reads them (``\\r\\n``, ``\\n`` and ``\\r`` alone, each line strict
UTF-8, the top lines those that start with ``#``), and a byte order mark is refused
(``BYTE_ORDER_MARK``): the reference reads it into the header's first name.

**The order of the checks.** A refusal is the first failure of the earliest of these classes, and
within the cells, the first row's first item in the order below:

1. ``NOT_UTF8`` (V:444-450); 2. ``CONTROL_CHARACTER``, one of the 30 control characters, a line
   separator (U+0085, U+2028, U+2029) or a byte order mark (U+FEFF, but at the file's first
   character) in any name or cell (stricter: the reference reads them);
3. the structure: ``NO_HEADER`` (V:458-471); ``BYTE_ORDER_MARK``; ``MISSING_COLUMN`` for a
   one-column header when neither it nor the five lines after it hold a tab (stricter: the
   reference's sniffer then checks nothing, V:491-496); ``STABLE_ID_KEYS`` where
   ``Profiles.crashes`` (the reference's header check crashes, V:3363-3373); ``TOO_MANY_COLUMNS``,
   more names than the dictionary's ids and ``SAMPLE_ID``, counted before the line is split (every
   such header repeats a name or holds one no meta's); ``DUPLICATE_COLUMN`` (V:934-946);
   ``COLUMN_NAME`` for a first name other than ``SAMPLE_ID`` (V:615), for a name no dictionary
   id (V:3376-3380, compared as written), for a column of a profile whose meta holds
   ``gene_panel`` other than the MAF's (V:3382-3389), then the stricter ones: a name longer than
   a descriptor's string, and two names whose column ids collide (``normalise_names`` gives an id
   other than the name's own or, on a re-import, the id the previous release gave it,
   ``SAMPLE_ID``'s included); then each data line: ``BLANK_LINE``
   (V:543, Unicode ``str.strip()``), ``COMMENT_LINE`` (V:547), ``REPEATED_HEADER`` (V:627),
   ``ROW_WIDTH`` (V:636; a short row also crashes the reference where the MAF's column is past
   it, V:3418), ``LONG_FIELD`` (the reference's csv reader fails over ``FIELD_CHARACTERS``
   characters) and ``TOO_MANY_CELLS`` (stricter, below);
4. the rows' cells, each row's items in this order: ``REQUIRED_VALUE``, a cell blank by
   ``str.strip()`` (V:642-650); ``DUPLICATE_SAMPLE``, a sample id an earlier row holds as written
   (V:3403-3410); ``UNKNOWN_SAMPLE``, one the sample file does not hold as written
   (V:3412-3413, V:731-743); ``NOT_SEQUENCED``, where the study has a mutation meta, a cell of
   the column ``mutations`` other than ``NA`` as written whose sample is not in the case list
   ``<study>_sequenced`` (V:3415-3424; the reference takes the last list whose id ends in
   ``_sequenced``); then the stricter ``PADDED_VALUE``, a cell with whitespace around it (the
   reference reads it as written, and so would the published column);
5. ``LINE_ENDS``, the file's line ends of more than one kind, or none (V:708-713).

**The table** (D447): ``gene_panel_matrix``, one row a line, a column a name of the header (its id
from ``normalise_names``), every column text, ``SAMPLE_ID`` the key, ``NA`` exactly a declared
missing code of every other column (no panel is given: UNKNOWN, D437), a column a ``category``
where D228's rule makes one. The cells are counted as the core counts them (``Matrix.cells``: the
columns times the rows, a table with no rows counted as one row) after the cells before the matrix,
and the first row past ``import_cells`` is ``TOO_MANY_CELLS``, before the core's
``LIMIT_EXCEEDED`` (stricter).

**Memory.** Nothing of a cell outlives its row but each column's distinct values, at most
``CATEGORY_VALUES + 1``, and the sample ids, one a row. The module keeps no cache past a read
(D438).
"""

import re
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from aibi.core.importers.infer import CATEGORY_VALUES
from aibi.core.schema.descriptors import ParseSettings
from aibi.core.schema.ids import normalise_names
from aibi.core.schema.jsonio import is_text
from aibi.core.schema.limits import MAX_IDENTIFIER, MAX_STRING, ImportLimits
from aibi.core.schema.output import text
from aibi.core.schema.pack_api import (
    DirectoryEntry,
    ImportNote,
    Previous,
    Refused,
    SourceReader,
)
from aibi.core.store.sources import FIELD_CHARACTERS
from aibi.packs.onco.caselists import SEQUENCED, CaseList
from aibi.packs.onco.clinical import CONTROLS, LINE_END, identity, spans
from aibi.packs.onco.meta import (
    GENE_PANEL_MATRIX,
    LINES,
    MANDATORY,
    PAIR_TYPES,
    RESOURCE_TYPES,
    types_and_id,
)
from aibi.packs.onco.mutation import settings
from aibi.packs.onco.refusals import Code, entry_refused, placed, shown

BOM = "\ufeff"
HASH = "#"
TAB = "\t"

GENE_PANEL_TABLE = "gene_panel_matrix"
"""The table's id, whose previous release's column ids a re-import keeps (D238)."""
SAMPLE_ID = "SAMPLE_ID"
"""The matrix's first column (V:3347, V:615)."""
MAF_COLUMN = "mutations"
"""The column the reference reads as the MAF's: the ``stable_id`` a ``MUTATION_EXTENDED:MAF`` meta
must hold (C:747), where the study has one (V:3363-3369)."""
NA = "NA"
"""The cell that gives no panel, exactly as written (V:3420): a declared missing code, UNKNOWN."""
SNIFFED_LINES = 5
"""The lines after the header the reference's sniffer reads with it (V:485-490)."""
SEPARATORS = "\x85\u2028\u2029"
"""The line separators the pack refuses in a name or a cell beside the control characters."""
REFUSED = re.compile(
    "[" + re.escape("".join(sorted(CONTROLS)) + SEPARATORS + BOM) + "]",
)
"""What no name or cell may hold: the 30 control characters (``text.CONTROLS``),
``SEPARATORS`` and a byte order mark (U+FEFF) anywhere but at the file's start, which
``BYTE_ORDER_MARK`` refuses (plan r3 MJ-B: a cell such as ``\ufeffNA`` would read like ``NA`` and
be published as a value). Other invisible format characters (U+200B, U+2060) are kept as written,
values (D447)."""
GENOMES = ("hg19", "hg38")
"""The ``reference_genome_id`` a segment or GISTIC meta must hold for the reference to type it
(C:968-978)."""
SWISSPROT = ("name", "accession")
"""The ``swissprot_identifier`` a mutation meta may hold for the reference to type it
(C:980-989)."""

GENE_PANEL = "gene_panel"
NOT_READ = " holds a gene_panel key, which the pack does not read (D447)"
CRASH = (
    "a meta file holds a stable_id without the keys cBioPortal's check of the matrix reads "
    "(genetic_alteration_type, and datatype beside MUTATION_EXTENDED), which crashes it"
)
UNKNOWN_PROFILE = "the column name is no meta file's stable_id"
PANEL_PROPERTY = "the column is a profile's whose meta file holds gene_panel"
COLLISION = "the column name's id is another column's"
PADDED_CELL = (
    "the value has whitespace around it, which cBioPortal reads as written and the published "
    "column would keep"
)


# --- The reference's dictionary of meta files (V:4740-4778) ---------------------------------------


def reference_meta(raw: bytes) -> dict[str, str] | None:
    """A meta file's keys and values as the reference reads them (C:852-870): split at
    ``\\r\\n``, ``\\n`` and ``\\r`` alone, a line blank by ``str.strip()`` skipped, any other split
    at its first ``:``, the key as written (a leading byte order mark kept in it) and the value
    stripped, the last of a repeated key kept; ``None`` for a line without ``:`` (the reference
    types it ``None``). The pack has refused a meta that is not UTF-8 before."""
    found: dict[str, str] = {}
    for line in LINES.split(raw.decode("utf-8", "replace")):
        if line.strip() == "":
            continue
        key, colon, value = line.partition(":")
        if not colon:
            return None
        found[key] = value.strip()
    return found


def reference_kind(found: Mapping[str, str], study: str | None) -> str | None:
    """The type the reference gives a meta it read as ``found`` (C:643-729, C:878-989), or
    ``None``: its type by its keys, then its mandatory keys, the type, datatype and ``stable_id``
    combination where ``stable_id`` is mandatory, the study id against ``study`` (the first typed
    meta's), a segment or GISTIC meta's genome and a mutation meta's ``swissprot_identifier``."""
    kind: str | None = None
    if "genetic_alteration_type" in found and "datatype" in found:
        kind = PAIR_TYPES.get(f"{found['genetic_alteration_type']}:{found['datatype']}")
    elif "cancer_study_identifier" in found and "type_of_cancer" in found:
        kind = "STUDY"
    elif "type_of_cancer" in found:
        kind = "CANCER_TYPE"
    elif "cancer_study_identifier" in found and "resource_type" in found:
        kind = RESOURCE_TYPES.get(found["resource_type"])
    if kind is None:
        return None
    if any(key not in found for key in MANDATORY[kind]):
        return None
    if "stable_id" in MANDATORY[kind] and not types_and_id(found):
        return None
    identifier = found.get("cancer_study_identifier")
    if study is not None and identifier is not None and identifier != study:
        return None
    if kind in ("SEG", "GISTIC_GENES") and found["reference_genome_id"] not in GENOMES:
        return None
    swissprot = found.get("swissprot_identifier")
    if kind in ("MUTATION", "MUTATION_UNCALLED") and swissprot not in (None, *SWISSPROT):
        return None
    return kind


@dataclass(frozen=True)
class Profiles:
    """The reference's dictionary as the matrix's header check reads it: its ids (``keys``), the
    ids of the entries holding ``gene_panel`` (``panels``), and whether the check's loop raises
    (``crashes``, V:3363-3373)."""

    keys: frozenset[str]
    panels: frozenset[str]
    crashes: bool


def profiles(raws: Mapping[str, bytes]) -> Profiles:
    """The dictionary the reference builds from the meta files ``raws`` (each meta file's name to
    its bytes, in the order of the names), V:4740-4778: each meta read and typed as the reference
    does, the study id the first typed meta's, an entry for each typed meta holding
    ``stable_id``, by the value stripped, the last winning."""
    entries: dict[str, dict[str, str]] = {}
    study: str | None = None
    for raw in raws.values():
        found = reference_meta(raw)
        if found is None:
            continue
        if reference_kind(found, study) is None:
            continue
        if "stable_id" in found:
            entries[found["stable_id"].strip()] = found
        if study is None and "cancer_study_identifier" in found:
            study = found["cancer_study_identifier"]
    crashes = any(
        "genetic_alteration_type" not in entry
        or (entry["genetic_alteration_type"] == "MUTATION_EXTENDED" and "datatype" not in entry)
        for entry in entries.values()
    )
    panels = frozenset(key for key, entry in entries.items() if GENE_PANEL in entry)
    return Profiles(frozenset(entries), panels, crashes)


# --- The meta (after the discrete copy-number meta's) --------------------------------------------


@dataclass(frozen=True)
class PanelMeta:
    """The gene panel matrix meta file, by its entry's name, its data file, a listed regular file,
    and the reference's dictionary of the study's meta files."""

    meta: str
    data: DirectoryEntry
    profiles: Profiles


@dataclass(frozen=True)
class MatrixColumn:
    name: str
    category: bool
    """For a column other than ``SAMPLE_ID``: whether D228 makes it a category."""


@dataclass(frozen=True)
class Matrix:
    """A gene panel matrix, read: its entry's name, its bytes, how the core parses them, its
    columns in source order and its rows."""

    name: str
    data: bytes
    settings: ParseSettings
    columns: tuple[MatrixColumn, ...]
    rows: int
    assigned: tuple[tuple[str, str], ...] | None = None
    """Each row's sample id and its ``mutations`` cell, in the file's order, where the study has a
    mutation meta and the matrix the column ``mutations``; ``None`` otherwise (D448)."""

    @property
    def cells(self) -> int:
        """The cells the core counts for the table (``_limits``)."""
        return len(self.columns) * max(1, self.rows)


def discovered(
    metas: Mapping[str, tuple[str, Mapping[str, str]]],
    raws: Mapping[str, bytes],
    check: Callable[[str, str], DirectoryEntry | None],
) -> PanelMeta | None:
    """The study's gene panel matrix meta, from ``metas`` (each meta file's name to its type and
    values, in the order of the names) and ``raws`` (their bytes), or ``None``. ``check(name,
    value)`` is phase 9's rule for a data value, which raises ``Refused`` or returns the exact
    listed regular file it names (or ``None``)."""
    names = [name for name, (kind, _) in metas.items() if kind == GENE_PANEL_MATRIX]
    if len(names) > 1:
        raise entry_refused(
            "DUPLICATE_PROFILE_META", names[1], "is a second gene panel matrix meta file"
        )
    if not names:
        return None
    name = names[0]
    entry = check(name, metas[name][1]["data_filename"])
    if entry is None:
        raise entry_refused(
            "DATA_FILE",
            name,
            "is a gene panel matrix meta file whose data_filename names no regular file in the "
            "study's directory",
        )
    return PanelMeta(name, entry, profiles(raws))


def used(found: PanelMeta | None, definitions: DirectoryEntry | None = None) -> tuple[str, ...]:
    """The entries ``found`` names, the meta file and its data file, or none, and the
    ``gene_panels`` directory's entry where the study has one (``entry``, D448)."""
    named = () if found is None else (found.meta, found.data.name)
    return named + (() if definitions is None else (definitions.name,))


def noted(metas: Mapping[str, tuple[str, Mapping[str, str]]]) -> list[ImportNote]:
    """One note for each meta file holding ``gene_panel``, which the pack never reads (D447)."""
    return [
        ImportNote("not_proposed", None, [*shown(name), text(NOT_READ)])
        for name, (_, found) in metas.items()
        if GENE_PANEL in found
    ]


# --- The data file --------------------------------------------------------------------------------


def _tabbed(data: bytes, end: int) -> bool:
    """Whether one of the ``SNIFFED_LINES`` lines after the header line, which ends at ``end``,
    holds a tab, the lines found as ``clinical.spans`` finds them."""
    start = end
    for lines, found in enumerate(LINE_END.finditer(data, end)):
        if lines > 0 and b"\t" in data[start : found.start()]:
            return True
        if lines >= SNIFFED_LINES:
            return False
        start = found.end()
    return b"\t" in data[start:]


def column_base(name: str, position: int) -> str:
    """A column name's id before collisions (SPEC 5.1, ``ids.normalise_names``): its
    normalised form, ``c_`` before one that starts with a digit, ``c_<position>`` for an empty
    one."""
    base = unicodedata.normalize("NFKC", name).lower()
    base = re.sub(r"[^a-z0-9]+", "_", base).strip("_")[:MAX_IDENTIFIER].rstrip("_")
    if not base:
        return f"c_{position}"
    if base[0].isdigit():
        return ("c_" + base)[:MAX_IDENTIFIER].rstrip("_")
    return base


def _padded(cell: str) -> bool:
    return cell != cell.strip()


Failure = tuple[int, Code, str]


class _Reading:
    def __init__(
        self,
        name: str,
        samples: frozenset[str],
        found: Profiles,
        sequenced: frozenset[str] | None,
        limits: ImportLimits,
        cells_before: int,
        previous: Sequence[tuple[str, str]] | None = None,
    ) -> None:
        self.name = name
        self.previous = previous
        self.samples = samples
        self.profiles = found
        self.sequenced = sequenced
        self.left = limits.import_cells - cells_before
        self.control: Refused | None = None
        self.structure: Refused | None = None
        self.cell: Refused | None = None
        self.names: list[str] | None = None
        self.top = 0
        self.width = 0
        self.rows = 0
        self.joined = ""
        self.maf: int | None = None
        self.seen: set[str] = set()
        self.assigned: list[tuple[str, str]] = []
        self.distinct: list[set[str | bytes]] = []
        self.present: list[int] = []

    def refuse(self, code: Code, line: int | None, column: int | None, reason: str) -> Refused:
        return placed(code, self.name, line, column, reason)

    # --- The header -----------------------------------------------------------------------------

    def header(self, number: int, line: str, tabbed: bool) -> Refused | None:
        self.width = line.count(TAB) + 1
        if self.width == 1 and not tabbed:
            return self.refuse(
                "MISSING_COLUMN",
                number,
                None,
                "the line of column names and the five lines after it hold no tab",
            )
        if self.profiles.crashes:
            return self.refuse("STABLE_ID_KEYS", number, None, CRASH)
        if self.width > len(self.profiles.keys) + 1:
            return self.refuse(
                "TOO_MANY_COLUMNS",
                number,
                None,
                "the file has more columns than the meta files have stable_ids and SAMPLE_ID",
            )
        names = line.split(TAB)
        seen: set[str] = set()
        for index, found in enumerate(names):
            if found in seen:
                return self.refuse(
                    "DUPLICATE_COLUMN", number, index + 1, "the column name repeats an earlier's"
                )
            seen.add(found)
        if names[0] != SAMPLE_ID:
            return self.refuse("COLUMN_NAME", number, 1, "the first column is not SAMPLE_ID")
        for index, found in enumerate(names[1:], start=2):
            if found not in self.profiles.keys:
                return self.refuse("COLUMN_NAME", number, index, UNKNOWN_PROFILE)
        maf = MAF_COLUMN if self.sequenced is not None else None
        for index, found in enumerate(names[1:], start=2):
            if found != maf and found in self.profiles.panels:
                return self.refuse("COLUMN_NAME", number, index, PANEL_PROPERTY)
        for index, found in enumerate(names, start=1):
            if len(found) > MAX_STRING:
                return self.refuse(
                    "COLUMN_NAME",
                    number,
                    index,
                    "the column name is longer than a descriptor's string",
                )
        ids = normalise_names(names, "column", self.previous)
        kept: dict[str, list[str]] = {}
        for name, assigned in self.previous or ():
            kept.setdefault(name, []).append(assigned)
        seen_names: dict[str, int] = {}
        for index, (found, assigned) in enumerate(zip(names, ids, strict=True), start=1):
            occurrence = seen_names.get(found, 0)
            seen_names[found] = occurrence + 1
            earlier = kept.get(found, [])
            own = earlier[occurrence] if occurrence < len(earlier) else None
            if assigned not in (column_base(found, index), own):
                return self.refuse("COLUMN_NAME", number, index, COLLISION)
        self.names = names
        self.joined = line
        self.maf = names.index(maf) if maf in names else None
        self.distinct = [set() for _ in names]
        self.present = [0 for _ in names]
        return None

    # --- The data lines -------------------------------------------------------------------------

    def data_line(self, number: int, line: str) -> None:
        if line == "" or line.isspace():
            self.structure = self.refuse("BLANK_LINE", number, None, "the line is blank")
            return
        if line.startswith(HASH):
            self.structure = self.refuse(
                "COMMENT_LINE", number, None, "a data line starts with '#'"
            )
            return
        end = len(self.joined)
        if line.startswith(self.joined) and line[end : end + 1] in ("", TAB):
            self.structure = self.refuse(
                "REPEATED_HEADER", number, None, "the line repeats the column names"
            )
            return
        if line.count(TAB) + 1 != self.width:
            self.structure = self.refuse(
                "ROW_WIDTH",
                number,
                None,
                "the line does not have as many fields as there are column names",
            )
            return
        if len(line) > FIELD_CHARACTERS:
            begin = 0
            for index in range(self.width):
                stop = line.find(TAB, begin)
                stop = len(line) if stop < 0 else stop
                if stop - begin > FIELD_CHARACTERS:
                    self.structure = self.refuse(
                        "LONG_FIELD", number, index + 1, "the field is longer than a cell may be"
                    )
                    return
                begin = stop + 1
        self.rows += 1
        if self.width * self.rows > self.left:
            self.structure = self.too_many(number)
            return
        if self.cell is not None:
            return
        fields = line.split(TAB)
        failure = self.checked(fields)
        if failure is not None:
            column, code, reason = failure
            self.cell = self.refuse(code, number, column + 1, reason)
            return
        self.seen.add(fields[0])
        if self.maf is not None:
            self.assigned.append((fields[0], fields[self.maf]))
        for index in range(1, self.width):
            cell = fields[index]
            if cell == NA:
                continue
            self.present[index] += 1
            kept = self.distinct[index]
            if len(kept) <= CATEGORY_VALUES:
                kept.add(identity(cell))

    def too_many(self, number: int | None) -> Refused:
        return self.refuse(
            "TOO_MANY_CELLS", number, None, "the study's files have more cells than an import may"
        )

    def checked(self, fields: Sequence[str]) -> Failure | None:
        """The row's first failure in the order of the module's docstring, or ``None``; a row
        that passes is the file's (its sample id is an earlier row's for the rows after it).
        Every item is tested, and the first that holds is the row's."""
        found: list[Failure] = []
        blank = [index for index, cell in enumerate(fields) if cell.strip() == ""]
        if blank:
            found.append((blank[0], "REQUIRED_VALUE", "the cell is blank"))
        sample = fields[0]
        if sample in self.seen:
            found.append((0, "DUPLICATE_SAMPLE", "the sample id is an earlier row's"))
        if sample not in self.samples:
            found.append(
                (0, "UNKNOWN_SAMPLE", "the row's sample id is no sample id of the sample file")
            )
        if (
            self.maf is not None
            and self.sequenced is not None
            and fields[self.maf] != NA
            and sample not in self.sequenced
        ):
            found.append(
                (
                    self.maf,
                    "NOT_SEQUENCED",
                    "the sample has a mutation panel and is not in the study's _sequenced case "
                    "list",
                )
            )
        padded = [index for index, cell in enumerate(fields) if index > 0 and _padded(cell)]
        if padded:
            found.append((padded[0], "PADDED_VALUE", PADDED_CELL))
        return found[0] if found else None

    # --- The end --------------------------------------------------------------------------------

    def finished(self, data: bytes) -> Matrix:
        if self.structure is None and self.names is None:
            self.structure = self.refuse(
                "NO_HEADER", None, None, "the file has no line of column names"
            )
        if self.structure is None and self.width * max(1, self.rows) > self.left:
            self.structure = self.too_many(None)
        for found in (self.control, self.structure, self.cell):
            if found is not None:
                raise found
        crlf = data.count(b"\r\n")
        kinds = (crlf > 0) + (data.count(b"\n") > crlf) + (data.count(b"\r") > crlf)
        if kinds != 1:
            raise self.refuse(
                "LINE_ENDS", None, None, "the file's line ends are not all of one kind, or none"
            )
        assert self.names is not None
        columns = [MatrixColumn(self.names[0], False)]
        for index, name in enumerate(self.names[1:], start=1):
            distinct = len(self.distinct[index])
            category = 0 < distinct <= CATEGORY_VALUES and 2 * distinct <= self.present[index]
            columns.append(MatrixColumn(name, category))
        assigned = None if self.maf is None else tuple(self.assigned)
        return Matrix(
            self.name, data, settings(self.top, False), tuple(columns), self.rows, assigned
        )


def read(
    data: bytes,
    name: str,
    samples: frozenset[str],
    limits: ImportLimits,
    *,
    found: Profiles,
    sequenced: frozenset[str] | None = None,
    cells_before: int = 0,
    previous: Sequence[tuple[str, str]] | None = None,
) -> Matrix:
    """The gene panel matrix ``name``, read from its bytes ``data`` against the sample ids
    ``samples``, the reference's dictionary ``found`` and, where the study has a mutation meta,
    the ids of its ``<study>_sequenced`` list (``sequenced``), with ``cells_before`` cells of the
    import counted before it (the module's docstring), and the previous release's ``(name, id)``
    pairs of the table's columns (``previous``), which a name's id may keep. Raises
    ``Refused``."""
    reading = _Reading(name, samples, found, sequenced, limits, cells_before, previous)
    view = memoryview(data)
    for number, start, end in spans(data):
        try:
            line = str(view[start:end], "utf-8")
        except UnicodeDecodeError:
            line = None
        if line is None:
            raise reading.refuse("NOT_UTF8", number, None, "the line is not UTF-8")
        if reading.control is not None:
            continue
        refused = REFUSED.search(line, 1 if number == 1 and line.startswith(BOM) else 0)
        if refused is not None:
            column = line.count(TAB, 0, refused.start()) + 1
            reading.control = reading.refuse(
                "CONTROL_CHARACTER",
                number,
                column,
                "the field holds a control character, a line separator or a byte order mark",
            )
            continue
        if reading.structure is not None:
            continue
        if reading.names is not None:
            reading.data_line(number, line)
            continue
        if number == 1 and line.startswith(BOM):
            reading.structure = reading.refuse(
                "BYTE_ORDER_MARK",
                number,
                None,
                "the file starts with a byte order mark, which cBioPortal reads into the first "
                "column's name",
            )
            continue
        if line.startswith(HASH):
            reading.top += 1
            continue
        tabbed = TAB in line or _tabbed(data, end)
        reading.structure = reading.header(number, line, tabbed)
    return reading.finished(data)


# --- The importer's call --------------------------------------------------------------------------


def imported(
    reader: SourceReader,
    found: PanelMeta | None,
    samples: frozenset[str],
    limits: ImportLimits,
    cells_before: int,
    lists: Sequence[CaseList],
    identifier: str,
    mutation: object | None,
    previous: Previous | None = None,
) -> Matrix | None:
    """The study's gene panel matrix, read through ``reader`` at the operator's ``import_bytes``
    after the import's ``cells_before`` cells, or ``None`` for a study with no matrix meta. Where
    the study has a mutation meta (``mutation``), the column ``mutations`` is the MAF's and its
    samples are held to the case list ``<identifier>_sequenced`` among ``lists``, which the
    importer has required. The data file's name becomes the table's ``original_name``: one that is
    not Unicode text is ``DATA_FILE`` (stricter, as the MAF's)."""
    if found is None:
        return None
    wanted = identifier + SEQUENCED
    listed = [frozenset(case.ids) for case in lists if case.stable_id == wanted]
    sequenced = None if mutation is None else frozenset().union(*listed)
    entry = found.data
    if entry.path is None or len(entry.name) > MAX_STRING or not is_text(entry.name):
        raise entry_refused(
            "DATA_FILE",
            found.meta,
            "has a data_filename that names a gene panel matrix whose name is not Unicode text",
        )
    data = reader.read(entry.path, limits.import_bytes)
    return read(
        data,
        entry.name,
        samples,
        limits,
        found=found.profiles,
        sequenced=sequenced,
        cells_before=cells_before,
        previous=None if previous is None else previous.columns.get(GENE_PANEL_TABLE),
    )


def counted(found: Matrix | None) -> int:
    """The cells the core counts for the matrix's table, or none without a matrix."""
    return 0 if found is None else found.cells


__all__ = [
    "MAF_COLUMN",
    "NA",
    "SAMPLE_ID",
    "Matrix",
    "MatrixColumn",
    "PanelMeta",
    "Profiles",
    "column_base",
    "counted",
    "discovered",
    "imported",
    "noted",
    "profiles",
    "read",
    "reference_kind",
    "reference_meta",
    "used",
]

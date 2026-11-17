"""A cBioPortal study's discrete copy-number matrix: the ``COPY_NUMBER_ALTERATION:DISCRETE`` meta
and its data file (SPEC D446, M4.2b-3; the reference's ``CNADiscreteValidator``, V:1232-1257, with
its bases ``CNAValidator``, V:1217-1230, ``GenewiseFileValidator``, V:1143-1198,
``FeaturewiseFileValidator``, V:1026-1141, and ``Validator``, V:361-720, the gene identification
of V:760-853 offline, the ``_cna`` case list of V:5042-5048 and the meta's checks, C:124-135 and
C:731-818).

Discovery and the importer reach this module through calls that hold no check of their own
(``discovered``, ``used``, ``imported``): every check of the CNA meta, of its case list and of its
data file is here.

**The meta** (``discovered``, after the structural variants'): a second discrete CNA meta is
``DUPLICATE_PROFILE_META`` (stricter: the reference reads ``gistic`` beside ``cna``); the data file
is resolved by phase 9's rule over Reads(S) (``study``'s ``_path_value``, given as ``check``) and
must be an exact listed regular file; its ``stable_id``, stripped, is one of ``STABLE_IDS``
(C:739-744). After it, the data file is a member of Reads(S), so no other meta file may name it,
and its ``pd_annotations_filename`` stays a ``PATH_SITES`` row: named, checked, never read.

**The case list** (``imported``): a study with the meta must hold a case list whose ``stable_id``
is the study id followed by ``_cna`` (V:5042-5048), else ``NO_CNA_LIST``. Its ids are members like
any list's; nothing compares them with the matrix's samples, in either direction (the reference
compares nothing).

**Lines** are read as ``structural`` reads them (``\\r\\n``, ``\\n`` and ``\\r`` alone, each line
strict UTF-8, the top lines those that start with ``#``), but a byte order mark is refused
(``BYTE_ORDER_MARK``): the reference reads it into the header's first name, which then names no
gene column and no sample, so it errs on the header (D446).

**The order of the checks.** A refusal is the first failure of the earliest of these classes, and
within the cells, the first row's first item in the order below:

1. ``NOT_UTF8`` (V:444-450); 2. ``CONTROL_CHARACTER`` (the reference reads them: a cell holding one
   is its invalid value, a symbol or a ``Cytoband`` holding one it accepts);
3. the structure: ``NO_HEADER`` (V:469); ``BYTE_ORDER_MARK``; ``MISSING_COLUMN`` for a one-column
   header when neither it nor the five lines after it hold a tab (stricter: the reference's sniffer
   then checks nothing, V:494-496); ``TOO_MANY_COLUMNS``, more names than the gene columns and the
   sample file's samples, counted before the line is split (each name past the gene columns must be
   a sample id, once); ``DUPLICATE_COLUMN`` (V:934-946); the gene columns, the header's prefix of
   names among ``Cytoband``, ``Hugo_Symbol`` and ``Entrez_Gene_Id`` (V:1056-1063):
   ``COLUMN_NAME`` for a gene column among the samples (V:1163-1167), ``MISSING_COLUMN`` for no gene
   column (V:1168-1172) and for no sample column (V:1112); ``UNKNOWN_SAMPLE`` for a sample id the
   sample file does not hold, as written (V:731-743, V:1126-1141; a padded id is one); then
   ``MISSING_COLUMN`` for a file with no ``Hugo_Symbol`` column (stricter: the pack keys a gene by
   its symbol, D446); then each data line: ``BLANK_LINE`` (V:543), ``COMMENT_LINE`` (V:547),
   ``REPEATED_HEADER`` (V:627), ``ROW_WIDTH`` (V:636), ``LONG_FIELD`` (the reference's csv reader
   fails over ``FIELD_CHARACTERS``) and ``TOO_MANY_CELLS`` (stricter, below);
4. the rows' cells. A row's same-outcome gene items first: ``ENTREZ_VALUE`` for an Entrez id
   ``int()`` reads, stripped, as 0 or less (V:822-827), ``NO_GENE`` for a row with neither a
   symbol nor an Entrez id once stripped (V:830-835); then the rows the reference does not load or
   check (stricter): ``ENTREZ_VALUE`` for an Entrez id ``int()`` does not read (V:815-821: "will not
   be loaded", its cells never checked), ``DUPLICATE_GENE`` for a symbol an earlier row holds as
   written (the table's key) and for a gene the reference keys as an earlier row's (``entrez``
   stripped, else the symbol stripped, cut at its first ``|`` and upper-cased: V:1094-1106, V:1225,
   V:766, V:853; it keeps the earlier row alone); then ``CNA_VALUE``, a cell whose ``str.strip()``
   is not one of ``VALUES`` (V:1237-1245; ``NA`` as written alone, a blank cell one); then the
   stricter items: ``NO_GENE`` for a row named by its Entrez id alone (the pack keys a gene by its
   symbol), ``PADDED_VALUE`` for a symbol, an Entrez id or a cell with whitespace around it (the
   reference strips each; the published column would keep it);
5. ``LINE_ENDS``, the file's line ends of more than one kind, or none (V:708-713).

**The table** (D401, D446): ``calls``, one row per cell that is neither ``0`` (assessed, neutral:
the absent value, dropped, counted and digested by ``CellDigest`` in its order) nor ``NA`` (not
assessed: counted as empty), ``(sample, gene, value)``, the value as written; the coverage, one
group per distinct ``NA`` pattern of a sample's column (``assignment``: the sample and its group;
``groups``: the group and each gene it assessed), a group's id the digest of its genes, so that it
derives from the pattern alone; a sample whose every cell is ``NA`` has no group, so it is not
covered. The cells are counted as the core counts them (``Cna.cells``): three a call, one a ``0`` or
``NA``, two an assignment or group row, a table with no rows counted as one; the count runs over the
rows with the cells before the matrix, and the first row past ``import_cells`` is
``TOO_MANY_CELLS``, before the core's ``LIMIT_EXCEEDED`` (stricter), and so is a total past it once
the coverage is counted.

**Memory.** One byte of code a cell (a call, ``0`` or ``NA``), the calls' rows and the header's
names; a sample's column is sliced from the codes when its group and its dropped cells are found,
and a group is kept by the SHA-256 of its pattern and one sample of it. The module keeps no cache
past a read (D438).
"""

import hashlib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType

from aibi.core.schema.jsonio import is_text
from aibi.core.schema.limits import MAX_STRING, ImportLimits
from aibi.core.schema.pack_api import CellDigest, DirectoryEntry, Refused, SourceReader, cell_digest
from aibi.core.store.sources import FIELD_CHARACTERS
from aibi.packs.onco.caselists import CaseList
from aibi.packs.onco.clinical import CONTROL, LINE_END, spans
from aibi.packs.onco.meta import CNA_DISCRETE, stable_id
from aibi.packs.onco.mutation import Maf
from aibi.packs.onco.refusals import Code, entry_refused, placed
from aibi.packs.onco.structural import Structural

BOM = "\ufeff"
HASH = "#"
TAB = "\t"

CYTOBAND = "Cytoband"
HUGO = "Hugo_Symbol"
ENTREZ = "Entrez_Gene_Id"
PREFIX = (CYTOBAND, HUGO, ENTREZ)
"""The names the reference reads as gene columns, as a prefix of the header (V:1056-1063)."""
UNREAD = (CYTOBAND, ENTREZ)
"""The gene columns the pack does not import: one note names those a file holds."""
STABLE_IDS = ("cna", "cna_rae", "cna_consensus", "gistic")
"""The ``stable_id``s a discrete CNA meta may hold (C:739-744)."""
CNA_LIST = "_cna"
"""What follows the study id in the ``stable_id`` of the case list a CNA meta needs (V:5043)."""
NA = "NA"
ABSENT = "0"
VALUES = ("-2", "-1.5", "-1", ABSENT, "1", "2", NA)
"""The cells the reference reads, once ``str.strip()``\\ ed (V:1235)."""
LEVELS = ("-2", "-1.5", "-1", "1", "2")
"""The calls: the ``cna`` column's permissible values, in order; ``0`` is the absent value and
``NA`` an empty cell (D401, D407)."""
VALUE_MAP: Mapping[str, str] = MappingProxyType(
    {"-2": "-2", "-1.5": "-2", "-1": "-1", "1": "1", "2": "2"}
)
"""The ``cna`` column's value map onto ``onco:cna_level``: every level as itself, and ``-1.5`` as
``-2``, which cBioPortal's loader is said to do (not in the pinned reference: D446)."""
HALF = "-1.5"
SNIFFED_LINES = 5
"""The lines after the header the reference's sniffer reads with it (V:485-490)."""

_CALL = 99
_ZERO = 48
_EMPTY = 78
_CODES: Mapping[str, int] = MappingProxyType(
    {ABSENT: _ZERO, NA: _EMPTY, **dict.fromkeys(LEVELS, _CALL)}
)
"""Each cell written as the reference reads it unchanged, to its code; any other cell is ``0``,
a cell the slow path checks."""
_LEVELS: Mapping[str, str] = MappingProxyType({level: level for level in LEVELS})
_NA_MASK = bytes(1 if code == _EMPTY else 0 for code in range(256))
"""A column's codes to its ``NA`` pattern: 1 where not assessed, else 0."""

NOT_LOADED = "the Entrez gene id is not an integer, so cBioPortal would not load the row"
KEYED_ALIKE = "cBioPortal keys the gene as an earlier row's and keeps that row alone"
INVALID = "the value is none cBioPortal reads as a discrete copy number"
ENTREZ_ALONE = (
    "the row names its gene by its Entrez gene id alone, and the pack keys a gene by its symbol"
)
PADDED_SYMBOL = (
    "the gene symbol has whitespace around it, which cBioPortal strips and the published column "
    "would keep"
)
PADDED_ENTREZ = (
    "the Entrez gene id has whitespace around it, which cBioPortal strips and the pack does not "
    "read"
)
PADDED_CELL = (
    "the value has whitespace around it, which cBioPortal strips and the published column would "
    "keep"
)


@dataclass(frozen=True)
class CnaMeta:
    """The discrete CNA meta file, by its entry's name, and its data file, a listed regular
    file."""

    meta: str
    data: DirectoryEntry


@dataclass(frozen=True)
class Cna:
    """A discrete CNA file, read: its entry's name, the calls, the coverage's two tables, the
    dropped (``0``) cells' count and digest, the empty (``NA``) cells' count, the cells holding
    ``-1.5`` and the gene columns of ``UNREAD`` it holds."""

    name: str
    calls: tuple[tuple[str, str, str], ...]
    assignment: tuple[tuple[str, str], ...]
    groups: tuple[tuple[str, str], ...]
    dropped: int
    digest: str
    empty: int
    halves: int
    unread: tuple[str, ...]

    @property
    def cells(self) -> int:
        """The cells the core counts for the three tables and the declaration (``_limits``)."""
        return (
            3 * max(1, len(self.calls))
            + 2 * max(1, len(self.assignment))
            + 2 * max(1, len(self.groups))
            + self.dropped
            + self.empty
        )


# --- The meta (after the structural variants') ----------------------------------------------------


def discovered(
    metas: Mapping[str, tuple[str, Mapping[str, str]]],
    check: Callable[[str, str], DirectoryEntry | None],
) -> CnaMeta | None:
    """The study's discrete CNA meta, from ``metas`` (each meta file's name to its type and values,
    in the order of the names), or ``None``. ``check(name, value)`` is phase 9's rule for a data
    value, which raises ``Refused`` or returns the exact listed regular file it names (or
    ``None``)."""
    names = [name for name, (kind, _) in metas.items() if kind == CNA_DISCRETE]
    if len(names) > 1:
        raise entry_refused(
            "DUPLICATE_PROFILE_META", names[1], "is a second discrete copy-number meta file"
        )
    if not names:
        return None
    name = names[0]
    found = metas[name][1]
    entry = check(name, found["data_filename"])
    if entry is None:
        raise entry_refused(
            "DATA_FILE",
            name,
            "is a discrete copy-number meta file whose data_filename names no regular file in the "
            "study's directory",
        )
    if stable_id(found["stable_id"]) not in STABLE_IDS:
        raise entry_refused(
            "META_VALUE", name, "holds a stable_id that a discrete copy-number meta may not"
        )
    return CnaMeta(name, entry)


def used(found: CnaMeta | None) -> tuple[str, ...]:
    """The entries ``found`` names, the meta file and its data file, or none."""
    return () if found is None else (found.meta, found.data.name)


# --- The data file --------------------------------------------------------------------------------


def _int(value: str) -> int | None:
    try:
        return int(value)
    except ValueError:
        return None


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


def reference_key(symbol: str, entrez: str | None) -> str:
    """The reference's key of a row it loads (V:1178-1198, V:1225, V:766, V:853): the Entrez id,
    stripped, where there is one, else the symbol stripped, cut at its first ``|`` and
    upper-cased."""
    if entrez is not None and entrez.strip() != "":
        return entrez.strip()
    return symbol.strip().split("|", 1)[0].upper()


def _padded(cell: str) -> bool:
    return cell != cell.strip()


def _encoded(value: str) -> bytes:
    raw = value.encode()
    return len(raw).to_bytes(8, "big") + raw


Failure = tuple[int, Code, str]


class _Reading:
    def __init__(
        self, name: str, samples: frozenset[str], limits: ImportLimits, cells_before: int
    ) -> None:
        self.name = name
        self.samples = samples
        self.left = limits.import_cells - cells_before
        self.control: Refused | None = None
        self.structure: Refused | None = None
        self.cell: Refused | None = None
        self.names: list[str] | None = None
        self.width = 0
        self.joined = ""
        self.counted = 0
        self.codes = bytearray()
        self.genes: list[str] = []
        self.calls: list[tuple[str, str, str]] = []
        self.halves = 0
        self.symbols: set[str] = set()
        self.keys: set[str] = set()

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
        if self.width > len(PREFIX) + len(self.samples):
            return self.refuse(
                "TOO_MANY_COLUMNS",
                number,
                None,
                "the file has more columns than its gene columns and the sample file's samples",
            )
        names = line.split(TAB)
        seen: set[str] = set()
        for index, found in enumerate(names):
            if found in seen:
                return self.refuse(
                    "DUPLICATE_COLUMN", number, index + 1, "the column name repeats an earlier's"
                )
            seen.add(found)
        prefix = 0
        while prefix < len(names) and names[prefix] in PREFIX:
            prefix += 1
        genes = names[:prefix]
        for index, found in enumerate(names[prefix:], start=prefix):
            if found in (HUGO, ENTREZ):
                return self.refuse(
                    "COLUMN_NAME", number, index + 1, "a gene column comes after a sample column"
                )
        if HUGO not in genes and ENTREZ not in genes:
            return self.refuse(
                "MISSING_COLUMN",
                number,
                None,
                "the file has neither a Hugo_Symbol nor an Entrez_Gene_Id column before its "
                "samples",
            )
        if prefix == len(names):
            return self.refuse("MISSING_COLUMN", number, None, "the file has no sample column")
        for index, found in enumerate(names[prefix:], start=prefix):
            if found not in self.samples:
                return self.refuse(
                    "UNKNOWN_SAMPLE",
                    number,
                    index + 1,
                    "the sample id is no sample id of the sample file",
                )
        if HUGO not in genes:
            return self.refuse(
                "MISSING_COLUMN",
                number,
                None,
                "the file has no Hugo_Symbol column, which the pack keys a gene by",
            )
        self.names = names
        self.prefix = prefix
        self.hugo = genes.index(HUGO)
        self.entrez = genes.index(ENTREZ) if ENTREZ in genes else None
        self.unread = tuple(column for column in UNREAD if column in genes)
        self.sample_ids = names[prefix:]
        self.joined = line
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
        if self.cell is not None:
            return
        fields = line.split(TAB)
        failure, codes = self.row(fields)
        if failure is not None:
            column, code, reason = failure
            self.cell = self.refuse(code, number, column + 1, reason)
            return
        calls = codes.count(_CALL)
        self.counted += 3 * calls + codes.count(_ZERO) + codes.count(_EMPTY)
        if self.counted > self.left:
            self.structure = self.too_many(number)
            return
        gene = fields[self.hugo]
        self.genes.append(gene)
        self.codes += codes
        cells = fields[self.prefix :]
        at = codes.find(_CALL)
        while at >= 0:
            self.calls.append((self.sample_ids[at], gene, _LEVELS[cells[at]]))
            at = codes.find(_CALL, at + 1)
        self.halves += cells.count(HALF)

    def too_many(self, number: int | None) -> Refused:
        return self.refuse(
            "TOO_MANY_CELLS", number, None, "the study's files have more cells than an import may"
        )

    def checked(self, fields: Sequence[str]) -> Failure | None:
        """The row's first failure in the order of the module's docstring, or ``None``; a row that
        passes is the file's (its symbol and key are an earlier row's for the rows after it)."""
        return self.row(fields)[0]

    def row(self, fields: Sequence[str]) -> tuple[Failure | None, bytes]:
        """The row's first failure, or ``None``, and its cells' codes. Every item is tested, in
        the order of the module's docstring, and the first that holds is the row's."""
        found: list[Failure] = []
        symbol = fields[self.hugo]
        entrez = None if self.entrez is None else fields[self.entrez]
        number = None if entrez is None or entrez.strip() == "" else entrez.strip()
        value = None if number is None else _int(number)
        if self.entrez is not None and value is not None and value <= 0:
            found.append((self.entrez, "ENTREZ_VALUE", "the Entrez gene id is not positive"))
        if symbol.strip() == "" and number is None:
            found.append((self.hugo, "NO_GENE", "the row names its gene by neither symbol nor id"))
        if self.entrez is not None and number is not None and value is None:
            found.append((self.entrez, "ENTREZ_VALUE", NOT_LOADED))
        if symbol in self.symbols:
            found.append((self.hugo, "DUPLICATE_GENE", "the gene symbol is an earlier row's"))
        key = reference_key(symbol, entrez)
        if key in self.keys:
            found.append((self.hugo, "DUPLICATE_GENE", KEYED_ALIKE))
        cells = fields[self.prefix :]
        codes = bytes([_CODES.get(cell, 0) for cell in cells])
        unread = [index for index, code in enumerate(codes) if code == 0]
        invalid = [index for index in unread if cells[index].strip() not in VALUES]
        if invalid:
            found.append((self.prefix + invalid[0], "CNA_VALUE", INVALID))
        if symbol.strip() == "" and number is not None:
            found.append((self.hugo, "NO_GENE", ENTREZ_ALONE))
        if _padded(symbol):
            found.append((self.hugo, "PADDED_VALUE", PADDED_SYMBOL))
        if entrez is not None and _padded(entrez):
            found.append((self.entrez or 0, "PADDED_VALUE", PADDED_ENTREZ))
        if unread:
            found.append((self.prefix + unread[0], "PADDED_VALUE", PADDED_CELL))
        if found:
            return found[0], codes
        self.symbols.add(symbol)
        self.keys.add(key)
        return None, codes

    # --- The end --------------------------------------------------------------------------------

    def finished(self, data: bytes) -> Cna:
        if self.structure is None and self.names is None:
            self.structure = self.refuse(
                "NO_HEADER", None, None, "the file has no line of column names"
            )
        coverage = None
        if self.control is None and self.structure is None and self.cell is None:
            coverage = self.coverage()
            unrowed = 0 if self.calls else 3
            if self.counted + unrowed + coverage.cells > self.left:
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
        assert coverage is not None
        return coverage.done(self)

    def coverage(self) -> "_Coverage":
        found = _Coverage(len(self.genes), len(self.sample_ids))
        for sample in range(found.samples):
            found.add(sample, self.column(sample))
        return found

    def column(self, sample: int) -> bytes:
        """The codes of the sample's column, in the file's gene order."""
        return bytes(self.codes[sample :: len(self.sample_ids)])


class _Coverage:
    """The ``NA`` patterns of the samples' columns: each distinct non-empty one, by its SHA-256,
    with one sample of it, and each sample's pattern."""

    def __init__(self, genes: int, samples: int) -> None:
        self.genes = genes
        self.samples = samples
        self.first: dict[bytes, int] = {}
        self.of: list[bytes | None] = []
        self.rows = 0
        self.assigned = 0

    def add(self, sample: int, column: bytes) -> None:
        empty = column.count(_EMPTY)
        if empty == self.genes:
            self.of.append(None)
            return
        key = hashlib.sha256(column.translate(_NA_MASK)).digest()
        self.of.append(key)
        self.assigned += 1
        if key not in self.first:
            self.first[key] = sample
            self.rows += self.genes - empty

    @property
    def cells(self) -> int:
        """The cells the core counts for the coverage's two tables, each counted as one row where
        it has none."""
        return 2 * max(1, self.assigned) + 2 * max(1, self.rows)

    def done(self, reading: _Reading) -> Cna:
        genes = reading.genes
        ids = reading.sample_ids
        named: dict[bytes, str] = {}
        rows: list[tuple[str, str]] = []
        for key, sample in self.first.items():
            column = reading.column(sample)
            assessed = [gene for gene, code in zip(genes, column, strict=True) if code != _EMPTY]
            named[key] = cell_digest((gene,) for gene in assessed)
        for key in sorted(self.first, key=lambda found: named[found]):
            column = reading.column(self.first[key])
            rows.extend(
                (named[key], gene)
                for gene, code in zip(genes, column, strict=True)
                if code != _EMPTY
            )
        assignment = tuple(
            (ids[sample], named[key]) for sample, key in enumerate(self.of) if key is not None
        )
        digest = CellDigest()
        order = sorted(range(len(genes)), key=lambda gene: _encoded(genes[gene]))
        ordered = [genes[gene] for gene in order]
        for sample in sorted(range(self.samples), key=lambda found: _encoded(ids[found])):
            column = reading.column(sample)
            sorted_codes = bytes(map(column.__getitem__, order))
            at = sorted_codes.find(_ZERO)
            while at >= 0:
                digest.add((ids[sample], ordered[at]))
                at = sorted_codes.find(_ZERO, at + 1)
        codes = reading.codes
        return Cna(
            reading.name,
            tuple(reading.calls),
            assignment,
            tuple(rows),
            codes.count(_ZERO),
            digest.digest(),
            codes.count(_EMPTY),
            reading.halves,
            reading.unread,
        )


def read(
    data: bytes,
    name: str,
    samples: frozenset[str],
    limits: ImportLimits,
    *,
    cells_before: int = 0,
) -> Cna:
    """The discrete CNA file ``name``, read from its bytes ``data`` against the sample ids
    ``samples`` (the module's docstring), with ``cells_before`` cells of the import counted before
    it. Raises ``Refused``."""
    reading = _Reading(name, samples, limits, cells_before)
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
        found = CONTROL.search(line)
        if found is not None:
            column = line.count(TAB, 0, found.start()) + 1
            reading.control = reading.refuse(
                "CONTROL_CHARACTER", number, column, "the field holds a control character"
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
            continue
        tabbed = TAB in line or _tabbed(data, end)
        reading.structure = reading.header(number, line, tabbed)
    return reading.finished(data)


# --- The importer's call --------------------------------------------------------------------------


def imported(
    reader: SourceReader,
    found: CnaMeta | None,
    samples: frozenset[str],
    limits: ImportLimits,
    cells_before: int,
    counted: Sequence[Maf | Structural | None],
    lists: Sequence[CaseList],
    identifier: str,
) -> Cna | None:
    """The study's discrete CNA file, read through ``reader`` at the operator's ``import_bytes``
    after the import's ``cells_before`` cells and those of the files ``counted`` (the MAF's and
    the SV file's, where read), or ``None`` for a study with no discrete CNA meta. ``lists`` are
    the study's case lists, one of which must be ``<identifier>_cna``. The data file's name becomes
    the tables' ``original_name``: one that is not Unicode text is ``DATA_FILE`` (stricter, as the
    MAF's)."""
    if found is None:
        return None
    wanted = identifier + CNA_LIST
    if not any(listed.stable_id == wanted for listed in lists):
        raise entry_refused(
            "NO_CNA_LIST",
            found.meta,
            "is a discrete copy-number meta file, and no case list's stable_id is the study id "
            "followed by _cna",
        )
    before = cells_before + sum(file.cells for file in counted if file is not None)
    entry = found.data
    if entry.path is None or len(entry.name) > MAX_STRING or not is_text(entry.name):
        raise entry_refused(
            "DATA_FILE",
            found.meta,
            "has a data_filename that names a discrete copy-number file whose name is not "
            "Unicode text",
        )
    data = reader.read(entry.path, limits.import_bytes)
    return read(data, entry.name, samples, limits, cells_before=before)


__all__ = [
    "ABSENT",
    "CNA_LIST",
    "ENTREZ",
    "HUGO",
    "LEVELS",
    "NA",
    "PREFIX",
    "STABLE_IDS",
    "UNREAD",
    "VALUES",
    "VALUE_MAP",
    "Cna",
    "CnaMeta",
    "discovered",
    "imported",
    "read",
    "reference_key",
    "used",
]

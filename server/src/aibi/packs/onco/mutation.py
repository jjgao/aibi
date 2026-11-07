"""Reading a cBioPortal study's MAF, the data file of its ``MUTATION_EXTENDED:MAF`` meta (SPEC
D444, M4.2b-1b; the reference's ``MutationsExtendedValidator``, V:1613-2429, with its bases
V:1259-1440 and V:420-640).

``read`` reads the MAF's bytes once, as a stream of lines, and returns what the builder needs, or
raises ``Refused``. It is header-driven by the reference's rules alone: the columns the reference
requires, and the reference's checks of the columns it names; a column no rule names is read as
text, never typed by a guess (no real file's shape is assumed: D444's unknowns). The file's bytes
are the ``mutations`` table's raw snapshot, which the core parses with ``settings``.

**Lines.** As ``clinical`` reads them (``clinical.spans``): ``\\r\\n``, ``\\n`` and ``\\r`` alone;
each line decoded on its own, strict UTF-8; one byte order mark at the start of line 1 is read
past by an offset (D409's exit 1 (13); the reference reads it into the first line, cells b01,
b01b). The top lines are the lines that start with ``#`` before the first that does not (V:452),
which is the header; nothing of them is kept but their count.

**The order of the checks.** A refusal is the first failure of the earliest of these classes, and
within a class the first by line, then by column. Each check has a reason of its own, which the
refusal gives after the place, and which the corpus holds check by check (D444):

1. ``NOT_UTF8``: a line that is not strict UTF-8 (V:440);
2. ``CONTROL_CHARACTER``: one of the 30 control characters anywhere (stricter: the reference
   reads NUL and U+0001 as content, cells b18, w08);
3. the structure, in the order the file is read: ``NO_HEADER`` (no line but top lines, V:457);
   ``TOO_MANY_COLUMNS`` (the header's tabs counted before it is split; stricter);
   ``COLUMN_NAME``: the line of names empty (a blank first line, or a BOM alone, which the
   reference reads as a header lacking its columns), then a name empty among others, over a
   descriptor string's characters or not Unicode text, found by its tabs' positions and refused
   uncut (stricter: the reference reads an empty name, x09); ``DUPLICATE_COLUMN`` (a name repeated
   exactly, V:934-946; b10); ``MISSING_COLUMN``: a header of one column when neither it nor the
   five lines after it hold a tab (stricter: the reference's sniffer then checks nothing,
   V:470-495), a required column (V:1617-1621; h01, b19, x05), neither ``HGVSp_Short`` nor
   ``Amino_Acid_Change`` (V:1712; h04), a driver column without its pair (V:1275-1295; h07), a
   namespace of the meta with no column ``<namespace>.<x>`` (V:1414-1420; lower-cased by
   ``str.lower``, found by a binary search of the sorted, lower-cased names), and the ASCN columns
   of an ``ascn`` namespace (V:1721-1735; h06); then each data line: ``BLANK_LINE`` (every field
   blank by ``str.strip()``, V:607; b03), ``COMMENT_LINE`` (a line that starts with ``#``, V:611;
   and, stricter, one whose ``#`` comes after spaces or tabs, which the reference reads as data),
   ``REPEATED_HEADER`` (V:627; b05, where the reference crashes), ``ROW_WIDTH`` (V:633; b06, b07),
   ``LONG_FIELD`` (over the core's ``FIELD_CHARACTERS``, where the reference's ``csv`` raises; w01)
   and ``TOO_MANY_CELLS`` (the import's cells so far and the table's columns times its rows, over
   ``import_cells``; stricter);
4. the cells of each row, by column. On **every** row: ``UNKNOWN_SAMPLE``, a
   ``Tumor_Sample_Barcode`` that is no sample id of the sample file, as written (V:1797 on the
   rows the reference validates: r01, r04, r04b; on the rows it skips, a check of its own and
   **stricter**, D444 B2: r04c, r04d, r20, r21, w02-w06), and ``DRIVER_VALUE``, the driver columns'
   checks (V:1300-1380, which the reference runs before it skips a row). On the rows the reference
   validates alone (``skipped`` is false): ``REQUIRED_VALUE`` (``Hugo_Symbol`` or
   ``Variant_Classification`` blank, V:2296; r02, r03); ``PADDED_VALUE`` (stricter): a
   ``Hugo_Symbol`` that ``str.strip()`` makes ``NA`` or ``Unknown`` but is not written so, on a
   row whose Entrez id names the gene, which the reference reads as no symbol (V:1806-1810) and
   the core would keep as a value (with no Entrez id either, the row is ``NO_GENE``), and an
   ``Entrez_Gene_Id`` that ``str.strip()`` makes ``0`` but is not written so and that the core's
   integer parse reads (``core_integer``: `` 0``, ``0 ``), which the reference reads as no id and
   the core would read as 0 (a padded ``NA``, or ``0`` padded by a space the core does not strip,
   is a counted ``unparsed`` cell instead, not refused); ``ENTREZ_VALUE``: an ``Entrez_Gene_Id``
   that ``int()`` reads, after ``strip()``, as 0 or less and that is not ``0`` (V:829-833; r17),
   and, stricter, one ``int()`` does not read that the core's integer parse reads as an integer
   (``1e3``, ``7157.0``: the reference warns that the record will not be loaded; ``nan``,
   ``inf``, ``1.5`` and ``1e400`` the core counts as ``unparsed``, and they are not refused);
   ``NO_GENE`` (neither a symbol nor an Entrez id, V:836; r02b, r14b); ``COUNT_VALUE`` (a count
   ``int()`` does not read, not empty and not ``.``, V:2216-2234; r07, t02); ``POSITION_VALUE``
   (a position neither blank, ``NA`` nor ``str.isdigit()``, V:2378-2412, or one ``float()``
   cannot read where both positions are given and neither is blank or ``NA``, V:1876-1881, where
   the reference crashes; r08); ``AMINO_ACID_CHANGE`` (V:2236-2265 and V:1840-1846; r10, r11);
   ``NCBI_BUILD`` (V:2162-2177 against the study's build, ``meta.GENOME_BUILDS``, GRCh37 by
   default; r12) and ``NORMAL_SAMPLE`` (V:2179-2188; r13).

**The skip predicate** (V:2267-2291): a row is skipped when its ``Hugo_Symbol`` is exactly
``Unknown`` and its ``Entrez_Gene_Id`` exactly ``0`` (``0`` when the file has no such column:
w05), or when its ``Variant_Classification`` is in the skip list: the meta's
``variant_classification_filter`` where it has one (its items stripped), which *replaces* the
default eight (``SKIP_DEFAULT``; v01-v03), each compared as written (``silent`` and ``Silent ``
are validated: w07). The list is a set built once per read, so a long filter costs a row no more
than a short one.

**What the reference accepts and the core reads otherwise** (D444): a count or position cell the
reference accepts that the core's integer parse does not read (``1_0``, a non-ASCII digit) is the
core's counted ``unparsed``. A cell of a skipped row is never checked by the reference: one the
core cannot read is counted ``unparsed``, and one it can read becomes a value (a count ``1e3`` on a
Silent row is 1000, an Entrez `` 0`` there is 0), which the reference never loads. A repeated
mutation key (V:1786; r05) is exit 1: every row is kept, read without loss.

**Memory.** Nothing of a cell outlives its row but per-column state for the columns
``CATEGORICAL`` names: their distinct values, at most ``CATEGORY_VALUES + 1``, each as
``clinical.identity`` keeps it. The sample ids are the sample file's, given. The module keeps no
cache past a read (D438).
"""

import bisect
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from aibi.core.importers.infer import CATEGORY_VALUES
from aibi.core.schema.descriptors import ParseSettings
from aibi.core.schema.jsonio import is_text
from aibi.core.schema.limits import MAX_STRING, ImportLimits
from aibi.core.schema.pack_api import Refused
from aibi.core.store.sources import FIELD_CHARACTERS
from aibi.packs.onco.clinical import COMMENT, CONTROL, LINE_END, identity, spans
from aibi.packs.onco.meta import MutationFields
from aibi.packs.onco.refusals import Code, placed

BOM = "\ufeff"
HASH = "#"
TAB = "\t"

BARCODE = "Tumor_Sample_Barcode"
NORMAL = "Matched_Norm_Sample_Barcode"
HUGO = "Hugo_Symbol"
ENTREZ = "Entrez_Gene_Id"
CLASSIFICATION = "Variant_Classification"
HGVSP_SHORT = "HGVSp_Short"
AMINO_ACID_CHANGE = "Amino_Acid_Change"
BUILD = "NCBI_Build"
START = "Start_Position"
END = "End_Position"
REQUIRED = (BARCODE, HUGO, CLASSIFICATION)
"""The columns the reference requires (V:1617-1621)."""
AMINO_ACID = (HGVSP_SHORT, AMINO_ACID_CHANGE)
"""One of them is required (V:1712)."""
COUNTS = ("t_alt_count", "t_ref_count", "n_alt_count", "n_ref_count")
"""The count columns the reference checks with ``int()`` (V:2242-2260)."""
POSITIONS = (START, END)
INTEGERS = (ENTREZ, *POSITIONS, *COUNTS)
"""The columns the pack declares ``integer``: those the reference reads as integers."""
CATEGORICAL = (
    CLASSIFICATION,
    "Variant_Type",
    "Mutation_Status",
    BUILD,
    "Chromosome",
    "Strand",
    "Verification_Status",
    "Validation_Status",
)
"""The columns whose values the MAF format enumerates (or the reference checks against a list,
V:2222-2240, V:2299, V:2420): a ``category`` by D228's rule where their cells allow, else a
``string``. Every other column is a ``string``; no value of any of them is mapped to a concept's
value (``Variant_Classification`` keeps its own values: the public probe of D444 found values
outside the format's list)."""
IDENTIFIERS = (BARCODE, NORMAL)
"""The columns that hold sample ids (a normal barcode embeds the patient's: ``identifier``)."""
SKIP_DEFAULT = ("Silent", "Intron", "3'UTR", "3'Flank", "5'UTR", "5'Flank", "IGR", "RNA")
"""The reference's default skip list (V:1626-1635)."""
UNKNOWN_GENE = "Unknown"
NO_ENTREZ = "0"
NULL_GENE = ("NA", "", UNKNOWN_GENE)
NULL_ENTREZ = ("NA", "", NO_ENTREZ)
NULL_AMINO_ACID = ("", "NULL", "NA")
PADDED_NULL_GENE = ("NA", UNKNOWN_GENE)
PADDED_SYMBOL = (
    "the gene symbol is a spelling of no value with spaces around it, which cBioPortal reads as "
    "no value and the published column would read as a value"
)
PADDED_ENTREZ = (
    "the Entrez gene id is 0 with spaces around it, which cBioPortal reads as no value and the "
    "published column would read as the value 0"
)
SKIPPED_BARCODE = "the barcode is no sample id of the sample file, on a row cBioPortal skips"
CORE_SPACE = " \t\r\n\f\v"
CORE_DIGITS = re.compile(r"[+-]?[0-9]+")
CORE_NUMBER = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?")
CORE_INT64 = 2**63
CORE_SAFE = 2**53 - 1


def core_integer(token: str) -> bool:
    """Whether the core's integer parse reads ``token`` as a present integer: ASCII digits, or a
    decimal or exponent that is integral and within 2^53 - 1, after ASCII whitespace is stripped
    (re-expressed from ``aibi.core.store.cells``, which a pack may not import; D444). A token it
    does not read is a counted ``unparsed`` cell, never a value."""
    text = token.strip(CORE_SPACE)
    if CORE_DIGITS.fullmatch(text):
        try:
            return -CORE_INT64 <= int(text) < CORE_INT64
        except ValueError:
            return False
    if CORE_NUMBER.fullmatch(text):
        try:
            number = Decimal(text)
            return number.copy_abs() <= CORE_SAFE and number == number.to_integral_value()
        except InvalidOperation:
            return False
    return False


AMINO_ACID_MOST = 255
NULL_COUNT = ("", ".")
DRIVER = "cbp_driver"
DRIVER_ANNOTATION = "cbp_driver_annotation"
TIERS = "cbp_driver_tiers"
TIERS_ANNOTATION = "cbp_driver_tiers_annotation"
DRIVER_PAIRS = ((DRIVER_ANNOTATION, DRIVER), (TIERS_ANNOTATION, TIERS))
NULL_DRIVER = ("Putative_Passenger", "Putative_Driver", "NA", "Unknown", "")
TIERS_MOST = 50
ANNOTATION_MOST = 80
ASCN = "ascn"
ASCN_COLUMNS = (
    "ASCN.ASCN_METHOD",
    "ASCN.ASCN_INTEGER_COPY_NUMBER",
    "ASCN.TOTAL_COPY_NUMBER",
    "ASCN.MINOR_COPY_NUMBER",
    "ASCN.CCF_EXPECTED_COPIES",
    "ASCN.CCF_EXPECTED_COPIES_UPPER",
    "ASCN.CLONAL",
    "ASCN.EXPECTED_ALT_COPIES",
)
"""The columns an ``ascn`` namespace requires (V:1657-1666)."""
SHORT_BUILDS = ("37", "38")


def settings(top: int, bom: bool) -> ParseSettings:
    """How the core parses a MAF's snapshot: its ``top`` lines skipped, the names on the next,
    fields at tabs, ``\\x01`` (refused in any accepted file) the quote, so that no field is ever
    quoted; ``utf-8-sig`` when a byte order mark starts the header line itself."""
    encoding = "utf-8-sig" if bom and top == 0 else "utf-8"
    return ParseSettings(
        format="tsv", delimiter=TAB, quote="\x01", header_row=0, skip_rows=top, encoding=encoding
    )


@dataclass(frozen=True)
class MafColumn:
    name: str
    category: bool
    """For a ``CATEGORICAL`` column: whether D228 makes it a category."""


@dataclass(frozen=True)
class Maf:
    """A MAF, read: its entry's name, its bytes, how the core parses them, its columns in source
    order and its rows."""

    name: str
    data: bytes
    settings: ParseSettings
    columns: tuple[MafColumn, ...]
    rows: int

    @property
    def cells(self) -> int:
        return len(self.columns) * max(1, self.rows)


def _float(value: str) -> bool:
    try:
        float(value)
    except ValueError:
        return False
    return True


def _int(value: str) -> int | None:
    try:
        return int(value)
    except ValueError:
        return None


class _Reading:
    def __init__(
        self,
        name: str,
        samples: frozenset[str],
        fields: MutationFields,
        limits: ImportLimits,
        cells_before: int,
    ) -> None:
        self.name = name
        self.samples = samples
        self.fields = fields
        self.limits = limits
        self.left = limits.import_cells - cells_before
        self.skip = frozenset(SKIP_DEFAULT if fields.filter is None else fields.filter)
        self.control: Refused | None = None
        self.structure: Refused | None = None
        self.cell: Refused | None = None
        self.top = 0
        self.tabbed = True
        """Whether the header line or one of the five lines after it holds a tab: the sample the
        reference sniffs a dialect from (V:470-495), which, with no tab, it does not check."""
        self.bom = False
        self.names: list[str] | None = None
        self.width = 0
        self.rows = 0
        self.joined = ""

    def refuse(self, code: Code, line: int | None, column: int | None, reason: str) -> Refused:
        return placed(code, self.name, line, column, reason)

    # --- The header -----------------------------------------------------------------------------

    def header(self, number: int, line: str, start: int) -> Refused | None:
        self.width = line.count(TAB, start) + 1
        if self.width > self.limits.table_columns:
            return self.refuse(
                "TOO_MANY_COLUMNS", number, None, "the file has more columns than a table may"
            )
        names: list[str] = []
        begin = start
        for index in range(self.width):
            end = line.find(TAB, begin)
            if end < 0:
                end = len(line)
            if self.width == 1 and end == begin:
                return self.refuse("COLUMN_NAME", number, None, "the line of column names is empty")
            if not 0 < end - begin <= MAX_STRING:
                return self.refuse(
                    "COLUMN_NAME",
                    number,
                    index + 1,
                    "the column name is empty or longer than a descriptor's string",
                )
            found = line[begin:end]
            if not is_text(found):
                return self.refuse(
                    "COLUMN_NAME", number, index + 1, "the column name is not Unicode text"
                )
            names.append(found)
            begin = end + 1
        seen: set[str] = set()
        for index, found in enumerate(names):
            if found in seen:
                return self.refuse(
                    "DUPLICATE_COLUMN", number, index + 1, "the column name repeats an earlier's"
                )
            seen.add(found)
        missing = self.missing(names, seen)
        if missing is not None:
            return self.refuse("MISSING_COLUMN", number, None, missing)
        self.names = names
        self.setup(names)
        return None

    def missing(self, names: list[str], seen: set[str]) -> str | None:
        if len(names) == 1 and not self.tabbed:
            return "the line of column names and the five lines after it hold no tab"
        for required in REQUIRED:
            if required not in seen:
                return f"the file has no {required} column"
        if not any(column in seen for column in AMINO_ACID):
            return f"the file has neither a {HGVSP_SHORT} nor an {AMINO_ACID_CHANGE} column"
        for annotation, value in DRIVER_PAIRS:
            if (annotation in seen) != (value in seen):
                return f"the file has one of {value} and {annotation} without the other"
        lowered = sorted(found.lower() for found in names)
        for space in sorted(set(self.fields.namespaces)):
            prefix = space + "."
            at = bisect.bisect_left(lowered, prefix)
            if at == len(lowered) or not lowered[at].startswith(prefix):
                return "the file has no column of a namespace its meta file declares"
        if ASCN in self.fields.namespaces and not all(c in seen for c in ASCN_COLUMNS):
            return "the file lacks a column the ascn namespace requires"
        return None

    def setup(self, names: list[str]) -> None:
        at = {found: index for index, found in enumerate(names)}
        self.at = at
        self.barcode = at[BARCODE]
        self.hugo = at[HUGO]
        self.classification = at[CLASSIFICATION]
        self.entrez = at.get(ENTREZ)
        self.normal = at.get(NORMAL)
        self.build = at.get(BUILD)
        self.start = at.get(START)
        self.end = at.get(END)
        self.amino = [(at[c], c == HGVSP_SHORT) for c in AMINO_ACID if c in at]
        self.counts = [at[c] for c in COUNTS if c in at]
        self.drivers = {
            c: at[c] for c in (DRIVER, DRIVER_ANNOTATION, TIERS, TIERS_ANNOTATION) if c in at
        }
        self.categorical = [at[c] for c in CATEGORICAL if c in at]
        self.distinct: dict[int, set[str | bytes]] = {index: set() for index in self.categorical}
        self.present: dict[int, int] = dict.fromkeys(self.categorical, 0)
        self.joined = TAB.join(names)

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
        if COMMENT.match(line) is not None:
            self.structure = self.refuse(
                "COMMENT_LINE", number, None, "a data line starts with '#' after spaces or tabs"
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
        fields = line.split(TAB)
        if self.cell is None:
            failure = self.checked(fields)
            if failure is not None:
                code, column, reason = failure
                self.cell = self.refuse(code, number, column + 1, reason)
        for index in self.categorical:
            cell = fields[index]
            if cell == "":
                continue
            self.present[index] += 1
            kept = self.distinct[index]
            if len(kept) <= CATEGORY_VALUES:
                kept.add(identity(cell))

    def too_many(self, number: int | None) -> Refused:
        return self.refuse(
            "TOO_MANY_CELLS", number, None, "the study's files have more cells than an import may"
        )

    def skipped(self, fields: list[str]) -> bool:
        """The reference's skip predicate (V:2269-2291)."""
        entrez = NO_ENTREZ if self.entrez is None else fields[self.entrez]
        if fields[self.hugo] == UNKNOWN_GENE and entrez == NO_ENTREZ:
            return True
        return fields[self.classification] in self.skip

    def checked(self, fields: list[str]) -> tuple[Code, int, str] | None:
        """The row's first failure by column, or ``None``."""
        found: list[tuple[int, Code, str]] = []
        skipped = self.skipped(fields)
        if fields[self.barcode] not in self.samples and not skipped:
            found.append(
                (self.barcode, "UNKNOWN_SAMPLE", "the barcode is no sample id of the sample file")
            )
        if fields[self.barcode] not in self.samples and skipped:
            found.append((self.barcode, "UNKNOWN_SAMPLE", SKIPPED_BARCODE))
        found.extend(self.driver(fields))
        if not skipped:
            found.extend(self.validated(fields))
        if not found:
            return None
        column, code, reason = min(found, key=lambda item: item[0])
        return code, column, reason

    def driver(self, fields: list[str]) -> list[tuple[int, Code, str]]:
        found: list[tuple[int, Code, str]] = []
        at = self.drivers
        if DRIVER in at and fields[at[DRIVER]] not in NULL_DRIVER:
            found.append(
                (at[DRIVER], "DRIVER_VALUE", "the driver value is not one cBioPortal takes")
            )
        if TIERS in at and len(fields[at[TIERS]]) > TIERS_MOST:
            found.append(
                (at[TIERS], "DRIVER_VALUE", "the driver tier is longer than 50 characters")
            )
        for column in (DRIVER_ANNOTATION, TIERS_ANNOTATION):
            if column in at and len(fields[at[column]]) > ANNOTATION_MOST:
                found.append(
                    (at[column], "DRIVER_VALUE", "the annotation is longer than 80 characters")
                )
        if TIERS_ANNOTATION in at and fields[at[TIERS_ANNOTATION]].strip() != "":
            tier = fields[at[TIERS]].strip() if TIERS in at else ""
            if tier == "":
                found.append(
                    (at[TIERS_ANNOTATION], "DRIVER_VALUE", "a tier annotation has no tier")
                )
        return found

    def validated(self, fields: list[str]) -> list[tuple[int, Code, str]]:
        found: list[tuple[int, Code, str]] = []
        for column in (self.hugo, self.classification):
            if fields[column].strip() == "":
                found.append((column, "REQUIRED_VALUE", "the required value is blank"))
        hugo = fields[self.hugo].strip()
        entrez = None if self.entrez is None else fields[self.entrez].strip()
        raw = None if self.entrez is None else fields[self.entrez]
        identified = entrez is not None and entrez not in NULL_ENTREZ
        if hugo in PADDED_NULL_GENE and hugo != fields[self.hugo] and identified:
            found.append((self.hugo, "PADDED_VALUE", PADDED_SYMBOL))
        if raw is not None and entrez == NO_ENTREZ and raw != entrez and core_integer(raw):
            assert self.entrez is not None
            found.append((self.entrez, "PADDED_VALUE", PADDED_ENTREZ))
        if entrez is not None and entrez not in NULL_ENTREZ:
            assert self.entrez is not None
            number = _int(entrez)
            if number is not None and number <= 0:
                found.append((self.entrez, "ENTREZ_VALUE", "the Entrez gene id is not positive"))
            if number is None and raw is not None and core_integer(raw):
                found.append(
                    (self.entrez, "ENTREZ_VALUE", "the Entrez gene id is a number but no integer")
                )
        elif hugo in NULL_GENE:
            found.append((self.hugo, "NO_GENE", "the row names neither a gene symbol nor an id"))
        for column in self.counts:
            cell = fields[column]
            if cell not in NULL_COUNT and _int(cell) is None:
                found.append((column, "COUNT_VALUE", "the count is not an integer"))
        found.extend(self.positions(fields))
        for column, short in self.amino:
            reason = _amino_acid(fields[column], short)
            if reason is not None:
                found.append((column, "AMINO_ACID_CHANGE", reason))
        if self.build is not None:
            cell = fields[self.build]
            build = self.fields.build
            if cell:
                written = build[:4] + cell if cell in SHORT_BUILDS else cell
                if written != build:
                    found.append(
                        (self.build, "NCBI_BUILD", "the NCBI build is not the study's genome's")
                    )
        if self.normal is not None and self.fields.normals is not None:
            cell = fields[self.normal]
            if cell != "" and cell not in self.fields.normals:
                found.append(
                    (self.normal, "NORMAL_SAMPLE", "the normal barcode is not in the meta's list")
                )
        return found

    def positions(self, fields: list[str]) -> list[tuple[int, Code, str]]:
        found: list[tuple[int, Code, str]] = []
        given = [index for index in (self.start, self.end) if index is not None]
        for index in given:
            cell = fields[index]
            if not (cell.strip() == "" or cell == "NA" or cell.isdigit()):
                found.append((index, "POSITION_VALUE", "the position is not an integer"))
        if self.start is not None and self.end is not None:
            cells = (fields[self.start], fields[self.end])
            if not any(cell.strip() == "" or cell == "NA" for cell in cells):
                for index, cell in zip((self.start, self.end), cells, strict=True):
                    if not _float(cell.strip("'").strip('"')):
                        found.append((index, "POSITION_VALUE", "the position is not a number"))
        return found

    # --- The end --------------------------------------------------------------------------------

    def finished(self, data: bytes) -> Maf:
        if self.structure is None and self.names is None:
            self.structure = self.refuse(
                "NO_HEADER", None, None, "the file has no line of column names"
            )
        if self.structure is None and self.width * max(1, self.rows) > self.left:
            self.structure = self.too_many(None)
        for found in (self.control, self.structure, self.cell):
            if found is not None:
                raise found
        assert self.names is not None
        columns = []
        for index, found in enumerate(self.names):
            distinct = len(self.distinct.get(index, ()))
            category = (
                index in self.distinct
                and 0 < distinct <= CATEGORY_VALUES
                and 2 * distinct <= self.present[index]
            )
            columns.append(MafColumn(found, category))
        return Maf(self.name, data, settings(self.top, self.bom), tuple(columns), self.rows)


SNIFFED_LINES = 5
"""The lines after the header the reference's sniffer reads with it (V:470-475)."""


def _tabbed(data: bytes, end: int) -> bool:
    """Whether one of the ``SNIFFED_LINES`` lines after the header line, which ends at ``end``,
    holds a tab: the lines are found as ``clinical.spans`` finds them, and nothing past them is
    read."""
    start = end
    for lines, found in enumerate(LINE_END.finditer(data, end)):
        if lines > 0 and b"\t" in data[start : found.start()]:
            return True
        if lines >= SNIFFED_LINES:
            return False
        start = found.end()
    return b"\t" in data[start:]


def _amino_acid(cell: str, short: bool) -> str | None:
    """The reference's error on an amino-acid change, as the pack says it (V:2262-2281; for
    ``HGVSp_Short``, V:1840-1846), or ``None``."""
    if short and len(cell) > AMINO_ACID_MOST:
        return "the amino-acid change is longer than 255 characters"
    if cell in NULL_AMINO_ACID:
        return None
    value = cell.strip()
    if len(value) > 1 and "p." in value[1:]:
        return "the amino-acid change holds a second 'p.'"
    if ";" in value:
        return "the amino-acid change holds ';'"
    if "," in value:
        return "the amino-acid change holds ','"
    return None


def read(
    data: bytes,
    name: str,
    samples: frozenset[str],
    fields: MutationFields,
    limits: ImportLimits,
    *,
    cells_before: int = 0,
) -> Maf:
    """The MAF ``name``, read from its bytes ``data`` against the sample ids ``samples`` and the
    mutation meta's ``fields`` (the module's docstring), with ``cells_before`` cells of the import
    counted before it. Raises ``Refused``."""
    reading = _Reading(name, samples, fields, limits, cells_before)
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
        offset = 0
        if number == 1 and line.startswith(BOM):
            offset = len(BOM)
            reading.bom = True
        if line.startswith(HASH, offset):
            reading.top += 1
            continue
        reading.tabbed = TAB in line or _tabbed(data, end)
        reading.structure = reading.header(number, line, offset)
    return reading.finished(data)


__all__ = [
    "CATEGORICAL",
    "COUNTS",
    "IDENTIFIERS",
    "INTEGERS",
    "POSITIONS",
    "REQUIRED",
    "SKIP_DEFAULT",
    "Maf",
    "MafColumn",
    "read",
    "settings",
]

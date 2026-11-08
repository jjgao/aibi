"""A cBioPortal study's structural variants: the ``STRUCTURAL_VARIANT:SV`` meta and its data file
(SPEC D445, M4.2b-4; the reference's ``StructuralVariantValidator``, V:3137-3333, with its bases
``CustomNamespacesValidator``, V:1405-1426, and ``Validator``, V:361-720, and the meta's checks,
C:339-350 and C:731-818).

Discovery and the importer reach this module through calls that hold no check of their own
(``discovered``, ``used``, ``imported``): every check of the SV meta and of its data file is here.

**The meta** (``discovered``, after discovery's phase 8): a second SV meta is
``DUPLICATE_PROFILE_META`` (the reference reports the second's ``stable_id``, cells
``meta:second_*``); the data file is resolved by phase 9's rule over Reads(S) (``study``'s
``_path_value``, given as ``check``: P0 on the value as written, no symlink and no member of
Reads(S) matching it, exactly or under case and normalisation folding), and must be an exact
listed regular file; its ``stable_id``, stripped, must be ``structural_variants`` (C:774); its
``namespaces`` are kept, each stripped and lower-cased (V:1416-1419). After it, the data file is a
member of Reads(S), so no other meta file may name it (D445).

**Lines** are read as ``mutation`` reads a MAF's: ``\\r\\n``, ``\\n`` and ``\\r`` alone, each line
strict UTF-8, one byte order mark at the start of line 1 read past (an exit 1: the reference reads
it into the first column's name), the top lines those that start with ``#``.

**The order of the checks.** A refusal is the first failure of the earliest of these classes, and
within the cells, the first row's first item in the order below:

1. ``NOT_UTF8`` (V:444-450); 2. ``CONTROL_CHARACTER`` (stricter: the reference reads them as
   content);
3. the structure: ``NO_HEADER`` (V:469); ``TOO_MANY_COLUMNS`` (stricter); ``COLUMN_NAME`` (the
   line of names empty; stricter, a name empty among others, too long or not Unicode text: the
   reference reads them); ``DUPLICATE_COLUMN`` (V:934-946); ``MISSING_COLUMN``: a one-column
   header when neither it nor the five lines after it hold a tab (stricter: the reference's
   sniffer then checks nothing, V:494-496), ``Sample_Id`` or ``SV_Status`` absent (V:3146-3149),
   neither of a site's ``Hugo_Symbol`` and ``Entrez_Gene_Id`` (V:3182-3192), a namespace of the
   meta with no column ``<namespace>.<x>`` (V:1415-1424, lower-cased by ``str.lower``); then each
   data line: ``BLANK_LINE`` (V:543), ``COMMENT_LINE`` (V:547; stricter, a ``#`` after spaces or
   tabs), ``REPEATED_HEADER`` (V:627), ``ROW_WIDTH`` (V:636), ``LONG_FIELD`` and
   ``TOO_MANY_CELLS`` (stricter);
4. the cells of each row. The reference reads seven columns, each through one oracle
   (``checkPresenceValue``, V:3215-3222): the cell ``str.strip()``\\ ed, and ``''``, ``NA``,
   ``Unknown`` and ``0`` read as no value (``NULL``). In order, the same-outcome items first:
   ``UNKNOWN_SAMPLE`` for a ``Sample_Id`` that is a spelling of no value, then for one that
   neither as written nor stripped is a sample id of the sample file (V:3271-3272, V:731-743);
   ``SV_STATUS``, a status neither ``somatic`` nor ``germline`` by ``str.lower`` (V:3243-3256);
   ``NCBI_BUILD``, a build neither ``grch37`` nor ``grch38`` by ``str.lower``, then one that is
   not, as stripped, the file's first build (V:3224-3241; never compared with the study's genome);
   ``ENTREZ_VALUE``, an Entrez id ``int()`` reads, stripped, as 0 or less (V:807-824), site 1
   before site 2; ``NO_GENE``, no site naming a gene (V:3285-3306); then the stricter items:
   ``PADDED_VALUE`` for a ``Sample_Id`` that is a sample id only once stripped, for a spelling of
   no value written with whitespace around it (or whitespace alone) in ``SV_Status``,
   ``NCBI_Build`` or a site's ``Hugo_Symbol``, and for an Entrez id ``str.strip()`` makes ``0``
   that the core's integer parse reads (`` 0``); and ``ENTREZ_VALUE`` for an Entrez id ``int()``
   does not read, stripped, that the core's integer parse reads (``1e3``, ``238.0``). Positions are
   never checked (the reference reads none of them).

**What the reference reads and the core reads otherwise** (D445): a status, a build or a symbol
written with whitespace around a value (`` SOMATIC``) is the reference's value and the core's
padded category; an Entrez id the core cannot read (an Arabic-Indic digit, ``1_0``,
``238\\xa0``) is the core's counted ``unparsed``; a repeated row (V:3318-3328; its key is the
fields of the fourteen columns of ``UNIQUE_KEY`` the header holds, as split) is exit 1: every row
is kept.

**Memory.** Nothing of a cell outlives its row but the categorical columns' distinct values, at
most ``CATEGORY_VALUES + 1`` each, and the file's first build. The module keeps no cache past a
read (D438).
"""

import bisect
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from aibi.core.importers.infer import CATEGORY_VALUES
from aibi.core.schema.descriptors import ParseSettings
from aibi.core.schema.jsonio import is_text
from aibi.core.schema.limits import MAX_STRING, ImportLimits
from aibi.core.schema.pack_api import DirectoryEntry, Refused, SourceReader
from aibi.core.store.sources import FIELD_CHARACTERS
from aibi.packs.onco.clinical import COMMENT, CONTROL, LINE_END, identity, spans
from aibi.packs.onco.meta import STRUCTURAL_VARIANT, stable_id
from aibi.packs.onco.mutation import Maf, core_integer, settings
from aibi.packs.onco.refusals import Code, entry_refused, placed

BOM = "\ufeff"
HASH = "#"
TAB = "\t"

SAMPLE = "Sample_Id"
STATUS = "SV_Status"
BUILD = "NCBI_Build"
HUGO = ("Site1_Hugo_Symbol", "Site2_Hugo_Symbol")
ENTREZ = ("Site1_Entrez_Gene_Id", "Site2_Entrez_Gene_Id")
POSITIONS = ("Site1_Position", "Site2_Position")
REQUIRED = (SAMPLE, STATUS)
"""The columns the reference requires (V:3146-3149)."""
INTEGERS = (*ENTREZ, *POSITIONS)
"""The columns the pack declares ``integer``: the Entrez ids, which the reference reads with
``int()``, and the positions, which the format writes as integers (the reference reads none)."""
CATEGORICAL = (STATUS, BUILD)
"""The columns the reference checks against a list: a ``category`` by D228's rule where their
cells allow, else a ``string``. Every other column is a ``string``."""
NULL = frozenset({"", "NA", "Unknown", "0"})
"""What the reference reads as no value in its seven columns, after ``str.strip()`` (V:3220)."""
NULL_COLUMNS = (STATUS, BUILD, *HUGO, *ENTREZ)
"""The columns whose spellings of ``NULL`` are declared missing codes (D445)."""
STATUSES = ("somatic", "germline")
BUILDS = ("grch37", "grch38")
UNIQUE_KEY = (
    SAMPLE,
    "Site1_Hugo_Symbol",
    "Site1_Entrez_Gene_Id",
    "Site1_Chromosome",
    "Site1_Position",
    "Site1_Region_Number",
    "Site1_Ensembl_Transcript_Id",
    "Site2_Hugo_Symbol",
    "Site2_Entrez_Gene_Id",
    "Site2_Chromosome",
    "Site2_Position",
    "Site2_Region_Number",
    "Site2_Ensembl_Transcript_Id",
    "Event_Info",
)
"""The columns whose fields, as split, key a row for the reference's duplicate check
(V:3153-3167, V:3319): a repeated key is exit 1, every row kept."""
STABLE_ID = "structural_variants"
"""The one ``stable_id`` an SV meta may hold (C:774)."""
PADDED_SAMPLE = (
    "the sample id is a sample's with whitespace around it, which cBioPortal strips and the "
    "published column would keep"
)
PADDED_NULL = (
    "the value is a spelling of no value with whitespace around it, or whitespace alone, which "
    "cBioPortal reads as no value and the published column would read as a value"
)
PADDED_ZERO = (
    "the Entrez gene id is 0 with spaces around it, which cBioPortal reads as no value and the "
    "published column would read as the value 0"
)
SNIFFED_LINES = 5
"""The lines after the header the reference's sniffer reads with it (V:485-490)."""


@dataclass(frozen=True)
class StructuralFields:
    """What the reader needs of the SV meta: its namespaces, each stripped and lower-cased."""

    namespaces: tuple[str, ...]


@dataclass(frozen=True)
class StructuralMeta:
    """The SV meta file, by its entry's name, its data file, a listed regular file, and the
    meta's values the reader needs."""

    meta: str
    data: DirectoryEntry
    fields: StructuralFields


@dataclass(frozen=True)
class SvColumn:
    name: str
    category: bool
    """For a ``CATEGORICAL`` column: whether D228 makes it a category."""


@dataclass(frozen=True)
class Structural:
    """An SV file, read: its entry's name, its bytes, how the core parses them, its columns in
    source order and its rows."""

    name: str
    data: bytes
    settings: ParseSettings
    columns: tuple[SvColumn, ...]
    rows: int

    @property
    def cells(self) -> int:
        return len(self.columns) * max(1, self.rows)


# --- The meta (after discovery's phase 8) ---------------------------------------------------------


def discovered(
    metas: Mapping[str, tuple[str, Mapping[str, str]]],
    check: Callable[[str, str], DirectoryEntry | None],
) -> StructuralMeta | None:
    """The study's SV meta, from ``metas`` (each meta file's name to its type and values, in the
    order of the names), or ``None``. ``check(name, value)`` is phase 9's rule for a data value,
    which raises ``Refused`` or returns the exact listed regular file it names (or ``None``)."""
    names = [name for name, (kind, _) in metas.items() if kind == STRUCTURAL_VARIANT]
    if len(names) > 1:
        raise entry_refused(
            "DUPLICATE_PROFILE_META", names[1], "is a second structural variant meta file"
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
            "is a structural variant meta file whose data_filename names no regular file in the "
            "study's directory",
        )
    if stable_id(found["stable_id"]) != STABLE_ID:
        raise entry_refused(
            "META_VALUE", name, "holds a stable_id that a structural variant meta may not"
        )
    spaces = found.get("namespaces")
    namespaces = () if spaces is None else tuple(s.strip().lower() for s in spaces.split(","))
    return StructuralMeta(name, entry, StructuralFields(namespaces))


def used(found: StructuralMeta | None) -> tuple[str, ...]:
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


Failure = tuple[int, Code, str]


class _Reading:
    def __init__(
        self,
        name: str,
        samples: frozenset[str],
        fields: StructuralFields,
        limits: ImportLimits,
        cells_before: int,
    ) -> None:
        self.name = name
        self.samples = samples
        self.fields = fields
        self.limits = limits
        self.left = limits.import_cells - cells_before
        self.control: Refused | None = None
        self.structure: Refused | None = None
        self.cell: Refused | None = None
        self.top = 0
        self.tabbed = True
        self.bom = False
        self.names: list[str] | None = None
        self.width = 0
        self.rows = 0
        self.joined = ""
        self.first_build: str | None = None

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
        if len(names) == 1 and not self.tabbed:
            return self.refuse(
                "MISSING_COLUMN",
                number,
                None,
                "the line of column names and the five lines after it hold no tab",
            )
        for required in REQUIRED:
            if required not in seen:
                return self.refuse(
                    "MISSING_COLUMN", number, None, f"the file has no {required} column"
                )
        for hugo, entrez in zip(HUGO, ENTREZ, strict=True):
            if hugo not in seen and entrez not in seen:
                return self.refuse(
                    "MISSING_COLUMN",
                    number,
                    None,
                    f"the file names a site's gene by neither {hugo} nor {entrez}",
                )
        lowered = sorted(found.lower() for found in names)
        for space in sorted(set(self.fields.namespaces)):
            prefix = space + "."
            at = bisect.bisect_left(lowered, prefix)
            if at == len(lowered) or not lowered[at].startswith(prefix):
                return self.refuse(
                    "MISSING_COLUMN",
                    number,
                    None,
                    "the file has no column of a namespace its meta file declares",
                )
        self.names = names
        self.setup(names)
        return None

    def setup(self, names: list[str]) -> None:
        at = {found: index for index, found in enumerate(names)}
        self.sample = at[SAMPLE]
        self.status = at[STATUS]
        self.build = at.get(BUILD)
        self.hugo = [at.get(column) for column in HUGO]
        self.entrez = [at.get(column) for column in ENTREZ]
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
                column, code, reason = failure
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

    def checked(self, fields: list[str]) -> Failure | None:
        """The row's first failure in the order of the module's docstring, or ``None``: the
        same-outcome items (``same``), then the stricter (``stricter``)."""
        found = self.same(fields)
        found.extend(self.stricter(fields))
        return found[0] if found else None

    def same(self, fields: list[str]) -> list[Failure]:
        found: list[Failure] = []
        raw = fields[self.sample]
        sample = raw.strip()
        if sample in NULL:
            found.append((self.sample, "UNKNOWN_SAMPLE", "the sample id is a spelling of no value"))
        if sample not in NULL and raw not in self.samples and sample not in self.samples:
            found.append(
                (self.sample, "UNKNOWN_SAMPLE", "the sample id is no sample id of the sample file")
            )
        status = fields[self.status].strip()
        if status not in NULL and status.lower() not in STATUSES:
            found.append(
                (self.status, "SV_STATUS", "the SV status is neither SOMATIC nor GERMLINE")
            )
        if self.build is not None:
            build = fields[self.build].strip()
            if build not in NULL and build.lower() not in BUILDS:
                found.append(
                    (self.build, "NCBI_BUILD", "the NCBI build is neither GRCh37 nor GRCh38")
                )
            if build not in NULL and self.first_build is not None and build != self.first_build:
                found.append(
                    (self.build, "NCBI_BUILD", "the NCBI build is not the file's first one")
                )
            if build not in NULL and self.first_build is None:
                self.first_build = build
        named = False
        for hugo, entrez in zip(self.hugo, self.entrez, strict=True):
            symbol = "" if hugo is None else fields[hugo].strip()
            number = "" if entrez is None else fields[entrez].strip()
            named = named or symbol not in NULL or number not in NULL
            value = None if number in NULL else _int(number)
            if entrez is not None and value is not None and value <= 0:
                found.append((entrez, "ENTREZ_VALUE", "the Entrez gene id is not positive"))
        if not named:
            found.append(
                (self.sample, "NO_GENE", "the row names a gene at neither site, by symbol or id")
            )
        return found

    def stricter(self, fields: list[str]) -> list[Failure]:
        found: list[Failure] = []
        raw = fields[self.sample]
        if raw not in self.samples and raw.strip() not in NULL and raw.strip() in self.samples:
            found.append((self.sample, "PADDED_VALUE", PADDED_SAMPLE))
        texts = [self.status, self.build, *self.hugo]
        for column in texts:
            if column is not None and _padded_null(fields[column]):
                found.append((column, "PADDED_VALUE", PADDED_NULL))
        for column in self.entrez:
            cell = "" if column is None else fields[column]
            if column is not None and cell.strip() == "0" and cell != "0" and core_integer(cell):
                found.append((column, "PADDED_VALUE", PADDED_ZERO))
        for column in self.entrez:
            cell = "" if column is None else fields[column]
            number = cell.strip()
            if (
                column is not None
                and number not in NULL
                and _int(number) is None
                and core_integer(cell)
            ):
                found.append(
                    (column, "ENTREZ_VALUE", "the Entrez gene id is a number but no integer")
                )
        return found

    # --- The end --------------------------------------------------------------------------------

    def finished(self, data: bytes) -> Structural:
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
            columns.append(SvColumn(found, category))
        return Structural(self.name, data, settings(self.top, self.bom), tuple(columns), self.rows)


def _padded_null(cell: str) -> bool:
    """A spelling of ``NULL`` once stripped that is not written as one: whitespace around it, or
    whitespace alone."""
    return cell.strip() in NULL and cell not in NULL


def read(
    data: bytes,
    name: str,
    samples: frozenset[str],
    fields: StructuralFields,
    limits: ImportLimits,
    *,
    cells_before: int = 0,
) -> Structural:
    """The SV file ``name``, read from its bytes ``data`` against the sample ids ``samples`` and
    the SV meta's ``fields`` (the module's docstring), with ``cells_before`` cells of the import
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


# --- The importer's call --------------------------------------------------------------------------


def imported(
    reader: SourceReader,
    found: StructuralMeta | None,
    samples: frozenset[str],
    limits: ImportLimits,
    cells_before: int,
    maf: Maf | None,
) -> Structural | None:
    """The study's SV file, read through ``reader`` at the operator's ``import_bytes`` after the
    import's ``cells_before`` cells and the MAF's, or ``None`` for a study with no SV meta. The data
    file's name becomes the table's ``original_name``: one that is not Unicode text is
    ``DATA_FILE`` (stricter, as the MAF's)."""
    if found is None:
        return None
    if maf is not None:
        cells_before += maf.cells
    entry = found.data
    if entry.path is None or len(entry.name) > MAX_STRING or not is_text(entry.name):
        raise entry_refused(
            "DATA_FILE",
            found.meta,
            "has a data_filename that names a structural variant file whose name is not Unicode "
            "text",
        )
    data = reader.read(entry.path, limits.import_bytes)
    return read(data, entry.name, samples, found.fields, limits, cells_before=cells_before)


__all__ = [
    "CATEGORICAL",
    "ENTREZ",
    "HUGO",
    "INTEGERS",
    "NULL",
    "NULL_COLUMNS",
    "POSITIONS",
    "REQUIRED",
    "SAMPLE",
    "STABLE_ID",
    "UNIQUE_KEY",
    "Structural",
    "StructuralFields",
    "StructuralMeta",
    "SvColumn",
    "discovered",
    "imported",
    "read",
    "used",
]

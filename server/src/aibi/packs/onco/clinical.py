"""Reading a cBioPortal clinical data file (SPEC D409's clinical files, M4.2a-1b).

``read`` reads a patient or a sample file's bytes once, as a stream of lines, and returns what the
pack's builder needs, or raises ``Refused`` with one of the codes below. It holds no decoded copy
of the whole file and no row: each line is decoded and checked on its own, and what is kept of
the rows is per column, in a bounded amount (whether it is a category, its missing spellings, its
padded cells) and the patient file's ``PATIENT_ID`` values, which the relationship needs. The
file's bytes stay as they are: they are the table's raw snapshot (§12.2), which the core parses
with ``SETTINGS``.

**Memory.** Nothing of a cell outlives its row but through the per-column state, and that is
bounded: a column's distinct values, which its category rule needs, are kept for at most
``CATEGORY_VALUES + 1`` of them, each as written when it is at most ``IDENTITY_CHARACTERS``
characters and otherwise as a 16-byte digest; a column's missing spellings outside ``FIXED``, which
its additions need, for at most ``ADDITIONS_MOST + 1`` of them, each of at most ``ADDITION_MOST``
characters, a longer one setting a flag (it is refused as ``MISSING_FORM`` whatever else the
column holds). The module keeps no cache past a read (a process-level one would hold the cells of an
erased dataset for the server's life; ``test_retention`` holds every module of the pack to it). A
line's width is counted on the line as it is, from its tabs, before the line is split, so that a
line of 10^8 tabs is refused for its width and never made into 10^8 fields; a header row's fields
are found by their tabs' positions, one at a time, and one over its limit is refused without being
cut out (``_Reading.header_row``), so that a header row costs no more than its line, whatever its
fields; a line is decoded from the file's bytes where they are (a ``memoryview``) and a byte order
mark is read past by an offset, so that neither is copied first; the id characters are checked by
one compiled pattern, not a loop of Python.

**Lines.** The bytes are split at ``\\r\\n``, ``\\n`` and ``\\r`` alone (``LINE_END``: the line
ends the core's parse reads, never ``str.splitlines``, which also splits at U+2028 or U+0085,
nor a binary file's iteration, which reads a file of lone carriage returns as one line); a
line end at the very end of the file ends the last line, and opens none. Lines are counted from
1; a byte order mark at the start of line 1 is read past (one, and only there).

**The order of the checks.** A refusal is the first failure of the earliest of these classes, and
within a class the first by line, then by column:

1. ``NOT_UTF8``: a line that is not strict UTF-8;
2. ``CONTROL_CHARACTER``: one of the 30 control characters (``text.CONTROLS``: U+0000 to U+001F
   but tab, line feed and carriage return, and U+007F) anywhere (stricter, policy);
3. the structure, in the order the file is read:

   - ``HEADER_ROWS``: lines 1 to 4 start with ``#``, and line 5, the attribute names, is not
     empty and does not;
   - ``HEADER_ROW_WIDTH``: each header row, past exactly one ``#``, split at tabs, has as many
     fields as line 5;
   - ``TOO_MANY_COLUMNS``: line 5 has at most the import's ``table_columns``;
   - ``ATTRIBUTE_NAME``: each name is not empty, of at most a descriptor string's characters,
     Unicode text and upper case by ``str.isupper()``; ``DUPLICATE_ATTRIBUTE``: no name repeats;
   - the header rows, by row then column: rows 1 and 2 (label and definition): ``TEXT_VALUE``
     for a null word (``attributes.null_word``), a field over a label's or a text's characters,
     counted as written, or one that is not Unicode text; row 3: ``DATATYPE_ROW`` unless exactly
     ``STRING``, ``NUMBER`` or ``BOOLEAN``; row 4: ``PRIORITY_ROW`` unless ``PRIORITY`` matches
     it whole and its value is from -1 to 2^53 - 1;
   - the columns: ``NO_KEY_COLUMN``, ``SAMPLE_ID_IN_PATIENT_FILE``, then each column in turn,
     ``PREDEFINED_ATTRIBUTE`` (its level or its datatype row is not cBioPortal's) and
     ``BANNED_ATTRIBUTE``, then ``ATTRIBUTE_IN_BOTH_FILES`` (the sample file's attribute, not a
     key, is the patient file's too);
   - each data line, from line 6: ``BLANK_LINE`` (every field blank by ``str.strip()``, an empty
     line included), ``COMMENT_LINE`` (the line, past spaces and tabs, starts with ``#``),
     ``REPEATED_HEADER`` (its first fields are line 5's names), ``ROW_WIDTH``, ``LONG_FIELD`` (a
     field over the core's ``FIELD_CHARACTERS``), and ``TOO_MANY_CELLS`` (the table's columns
     times its rows, at least one, as the core counts a table's cells, over what is left of the
     import's ``import_cells``);
4. the cells, each in turn: ``EXCEL_DATE`` (``attributes.excel_date``, in every column but one
   whose name holds ``date``), then by the column: a key's ``KEY_MISSING`` (a null word),
   ``PADDED_KEY`` (spaces around it), ``KEY_CHARACTERS`` and the ``SAMPLE_ID``'s
   ``SAMPLE_ID_LENGTH``; a ``BOOLEAN`` column's ``BOOLEAN_VALUE``; a patient file's survival
   status column's ``SURVIVAL_STATUS``. A cell ``missing.classify`` calls missing, or an empty
   one, passes the last two; their values are compared after spaces are trimmed (Java's trim on
   a cell that holds no control character), so that a cell padded with another space character
   is refused where the validator's ``str.strip()`` would read it (stricter, D409);
5. ``MISSING_FORM``: a column's additions (``missing.additions``), by column.

**What is kept** per column: its name, label, definition, datatype row and priority as written
(each checked, ``text.Checked``), whether a ``STRING`` column is a category (D228: some present
cell, at most ``CATEGORY_VALUES`` distinct values among its present cells, and at most half of
them; a present cell is neither empty nor missing), its additions, and how many present cells
of a ``STRING`` column have spaces around them, with the first ``ROWS_SHOWN`` row positions (a
row is line minus 5, as the core counts rows).
"""

import hashlib
import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Literal

from aibi.core.importers.infer import CATEGORY_VALUES
from aibi.core.schema.descriptors import ParseSettings
from aibi.core.schema.jsonio import is_text
from aibi.core.schema.limits import MAX_STRING, MAX_TEXT, ImportLimits
from aibi.core.schema.pack_api import Refused
from aibi.core.store.sources import FIELD_CHARACTERS
from aibi.packs.onco.attributes import (
    BANNED,
    DFS_VALUES,
    ID_CHARACTERS,
    OS_VALUES,
    PATIENT_ID,
    PREDEFINED,
    SAMPLE_ID,
    SAMPLE_ID_MOST,
    Declared,
    dated_column,
    excel_date,
    null_word,
    survival_prefix,
    survival_status,
)
from aibi.packs.onco.missing import ADDITION_MOST, ADDITIONS_MOST, FIXED, State, additions, classify
from aibi.packs.onco.refusals import Code, placed
from aibi.packs.onco.text import CONTROLS, Checked, written

Role = Literal["patient", "sample"]

LINE_END = re.compile(rb"\r\n|\n|\r")
"""Where a line ends: ``\\r\\n``, ``\\n`` or ``\\r`` alone, as the core's parse reads them."""
CONTROL = re.compile("[" + "".join(sorted(CONTROLS)).replace("\\", "\\\\") + "]")
ID_CELL = re.compile("[" + re.escape("".join(sorted(ID_CHARACTERS))) + "]*")
"""A key cell whose every character an id may hold (``attributes.ID_CHARACTERS``), checked in C,
not by a loop of Python over its characters."""
COMMENT = re.compile("[ \t]*#")
"""A line that, past spaces and tabs, starts with ``#`` (matched without copying the line)."""
BOM = "\ufeff"
HASH = "#"
TAB = "\t"
SPACE = " "
HEADER_ROWS = 4
"""The header rows above the attribute names: label, definition, datatype and priority."""
NAMES_LINE = HEADER_ROWS + 1
DATATYPES: frozenset[str] = frozenset({"STRING", "NUMBER", "BOOLEAN"})
PRIORITY = re.compile(r"-?[0-9]{1,17}")
"""A priority as written: an optional ``-`` and 1 to 17 ASCII digits."""
PRIORITY_CHARACTERS = 18
"""The most characters of a priority as written (``PRIORITY``): the ``-`` and 17 digits."""
DATATYPE_MOST = max(map(len, DATATYPES))
"""The most characters of a datatype (``BOOLEAN``): a longer field is none of them."""
PRIORITY_LEAST = -1
PRIORITY_MOST = 2**53 - 1
ROW_MOST = (MAX_STRING, MAX_TEXT)
"""The most characters of a header field of row 1 (a label) and of row 2 (a definition)."""
TRUE_FALSE: frozenset[str] = frozenset({"TRUE", "FALSE"})
ROWS_SHOWN = 5
"""The most row positions a note gives."""
IDENTITY_CHARACTERS = 64
"""The most characters of a cell a column's distinct set keeps as written; a longer cell is kept as
its digest."""
DIGEST_BYTES = 16

SETTINGS = ParseSettings(
    format="tsv", delimiter=TAB, quote="\x01", header_row=0, skip_rows=HEADER_ROWS, encoding="utf-8"
)
"""How the core parses a clinical file's snapshot: the four header rows skipped, the names on
line 5, fields at tabs. ``\\x01``, a control character no accepted file holds, is the quote, so
that no field is ever quoted, as cBioPortal reads none."""


@dataclass(frozen=True)
class Column:
    """A column of a clinical file, as the builder describes it."""

    name: str
    label: Checked
    definition: Checked
    declared: Declared
    priority: int
    key: bool
    category: bool
    """For a ``STRING`` column that is not a key: whether D228 makes it a category."""
    additions: Mapping[str, State]
    padded: int
    """For a ``STRING`` column that is not a key: its present cells with spaces around them."""
    padded_rows: tuple[int, ...]


@dataclass(frozen=True)
class Clinical:
    """A clinical file, read: its entry's name, its bytes, its columns in source order, its rows,
    the patient file's ``PATIENT_ID`` values, and, for a sample file read with a patient file,
    the samples whose patient has no row there (their count and first row positions)."""

    name: str
    role: Role
    data: bytes
    columns: tuple[Column, ...]
    rows: int
    keys: frozenset[str] = frozenset()
    dangling: int = 0
    dangling_rows: tuple[int, ...] = ()

    @property
    def cells(self) -> int:
        """The cells the core counts for the table: its columns times its rows, at least one."""
        return len(self.columns) * max(1, self.rows)


def spans(data: bytes) -> Iterator[tuple[int, int, int]]:
    """The file's lines, numbered from 1, as where each begins and ends in ``data`` (``LINE_END``;
    the end excludes the line end), so that a line is decoded from the bytes where they are and
    never copied out as bytes first."""
    start = 0
    number = 0
    for found in LINE_END.finditer(data):
        number += 1
        yield number, start, found.start()
        start = found.end()
    if start < len(data):
        yield number + 1, start, len(data)


def lines(data: bytes) -> Iterator[tuple[int, bytes]]:
    """The file's lines, numbered from 1, without their ends (``LINE_END``)."""
    for number, start, end in spans(data):
        yield number, data[start:end]


def identity(cell: str) -> str | bytes:
    """What a column's distinct set keeps of a present cell: the cell as written when it is short,
    else a 16-byte BLAKE2 digest of its UTF-8 (a ``bytes``, never equal to a ``str``), so that a
    column's set is bounded in bytes however long its cells."""
    if len(cell) <= IDENTITY_CHARACTERS:
        return cell
    return hashlib.blake2b(cell.encode("utf-8"), digest_size=DIGEST_BYTES).digest()


def _key_failure(cell: str, sample_id: bool) -> tuple[Code, str] | None:
    if null_word(cell):
        return "KEY_MISSING", "is an id that is empty or a null word"
    if cell != cell.strip(SPACE):
        return "PADDED_KEY", "is an id with spaces around it"
    if ID_CELL.fullmatch(cell) is None:
        return (
            "KEY_CHARACTERS",
            "is an id holding a character an id may not hold: letters, digits and . _ ( ) [ ] "
            "' , + - : ; alone",
        )
    if sample_id and len(cell) > SAMPLE_ID_MOST:
        return "SAMPLE_ID_LENGTH", "is a sample id longer than cBioPortal's"
    return None


_SURVIVAL = "is not a value cBioPortal takes in this survival status column, nor a missing value"


class _Reading:
    """One file's read: the state of the stream, and the first failure of each class."""

    def __init__(
        self,
        name: str,
        role: Role,
        limits: ImportLimits,
        cells_before: int,
        patient: "Clinical | None",
    ) -> None:
        self.name = name
        self.role: Role = role
        self.limits = limits
        self.left = limits.import_cells - cells_before
        self.patient = patient
        self.control: Refused | None = None
        self.structure: Refused | None = None
        self.cell: Refused | None = None
        self.header: list[str] = []
        self.start = 0
        """Where line 1's content begins: past the byte order mark, when it has one."""
        self.names: list[str] = []
        self.width = 0
        self.rows = 0
        self.columns: list[Column] = []
        self.keys: set[str] = set()
        self.dangling = 0
        self.dangling_rows: list[int] = []

    def refuse(self, code: Code, line: int | None, column: int | None, reason: str) -> Refused:
        return placed(code, self.name, line, column, reason)

    # --- The header -----------------------------------------------------------------------------

    def header_line(self, number: int, line: str) -> None:
        if number == 1 and line.startswith(BOM):
            self.start = len(BOM)  # read past, never sliced off: a slice copies a long line
        self.header.append(line)
        if number == NAMES_LINE:
            self.structure = self.checked_header()

    def checked_header(self) -> Refused | None:
        """The header's first failure (lines 1 to 5), or ``None``, setting up the columns."""
        for index, line in enumerate(self.header):
            begins = line.startswith(HASH, self.start if index == 0 else 0)
            if (index < HEADER_ROWS) != begins or line == "":
                return self.refuse(
                    "HEADER_ROWS",
                    index + 1,
                    None,
                    "the file does not start with four header rows that begin with '#' and a "
                    "line of attribute names",
                )
        self.width = self.header[HEADER_ROWS].count(TAB) + 1
        for index, line in enumerate(self.header[:HEADER_ROWS]):
            if line.count(TAB) + 1 != self.width:
                return self.refuse(
                    "HEADER_ROW_WIDTH",
                    index + 1,
                    None,
                    "a header row does not have as many fields as there are attribute names",
                )
        if self.width > self.limits.table_columns:
            return self.refuse(
                "TOO_MANY_COLUMNS", NAMES_LINE, None, "the file has more columns than a table may"
            )
        self.names = self.header[HEADER_ROWS].split(TAB)
        seen: set[str] = set()
        for index, name in enumerate(self.names):
            if not (len(name) <= MAX_STRING and name.isupper() and is_text(name)):
                return self.refuse(
                    "ATTRIBUTE_NAME",
                    NAMES_LINE,
                    index + 1,
                    "the attribute name is empty, not upper case, not Unicode text, or longer than "
                    "a descriptor's string",
                )
            if name in seen:
                return self.refuse(
                    "DUPLICATE_ATTRIBUTE",
                    NAMES_LINE,
                    index + 1,
                    "the attribute name repeats an earlier column's",
                )
            seen.add(name)
        checked = self.header_rows()
        if isinstance(checked, Refused):
            return checked
        return self.checked_columns(*checked)

    def header_row(self, row: int, most: int) -> Iterator[str | None]:
        """The fields of header row ``row`` (0 to 3), past its ``#``: each as written, or ``None``
        for one of more than ``most`` characters. Fields are found by the tabs' positions, one at
        a time, so that a field over ``most`` is never made into a string: a refused header row
        costs no more than the line itself, whatever its fields."""
        line = self.header[row]
        begin = len(HASH) + (self.start if row == 0 else 0)
        for _ in range(self.width):
            end = line.find(TAB, begin)
            if end < 0:
                end = len(line)
            yield line[begin:end] if end - begin <= most else None
            begin = end + 1

    def header_rows(
        self,
    ) -> Refused | tuple[list[Checked], list[Checked], list[Declared], list[int]]:
        labels: list[Checked] = []
        definitions: list[Checked] = []
        for row, kept in ((0, labels), (1, definitions)):
            for index, field in enumerate(self.header_row(row, ROW_MOST[row])):
                found = None if field is None else written(field, ROW_MOST[row])
                if found is None or null_word(found):
                    return self.refuse(
                        "TEXT_VALUE",
                        row + 1,
                        index + 1,
                        "the header field is empty, a null word, not Unicode text, or longer than "
                        + ("a label" if row == 0 else "a definition")
                        + " may be",
                    )
                kept.append(found)
        declared: list[Declared] = []
        for index, field in enumerate(self.header_row(2, DATATYPE_MOST)):
            if field is None or field not in DATATYPES:
                return self.refuse(
                    "DATATYPE_ROW",
                    3,
                    index + 1,
                    "the datatype is not exactly STRING, NUMBER or BOOLEAN",
                )
            declared.append(field)  # pyright: ignore[reportArgumentType]
        priorities: list[int] = []
        for index, field in enumerate(self.header_row(3, PRIORITY_CHARACTERS)):
            value = int(field) if field and PRIORITY.fullmatch(field) else None
            if value is None or not PRIORITY_LEAST <= value <= PRIORITY_MOST:
                return self.refuse(
                    "PRIORITY_ROW",
                    4,
                    index + 1,
                    "the priority is not an integer from -1, written with at most 17 ASCII digits "
                    "and an optional '-'",
                )
            priorities.append(value)
        return labels, definitions, declared, priorities

    def checked_columns(
        self,
        labels: list[Checked],
        definitions: list[Checked],
        declared: list[Declared],
        priorities: list[int],
    ) -> Refused | None:
        keys = (PATIENT_ID,) if self.role == "patient" else (PATIENT_ID, SAMPLE_ID)
        for key in keys:
            if key not in self.names:
                return self.refuse(
                    "NO_KEY_COLUMN", NAMES_LINE, None, f"the file has no {key} column"
                )
        if self.role == "patient" and SAMPLE_ID in self.names:
            return self.refuse(
                "SAMPLE_ID_IN_PATIENT_FILE",
                NAMES_LINE,
                self.names.index(SAMPLE_ID) + 1,
                f"a patient file holds a {SAMPLE_ID} column",
            )
        for index, name in enumerate(self.names):
            if name in keys:
                continue
            rule = PREDEFINED.get(name)
            if rule is not None and rule.level is not None and rule.level != self.role:
                return self.refuse(
                    "PREDEFINED_ATTRIBUTE",
                    NAMES_LINE,
                    index + 1,
                    f"cBioPortal's predefined attribute {name} is a {rule.level}'s attribute",
                )
            if rule is not None and rule.datatype is not None and rule.datatype != declared[index]:
                return self.refuse(
                    "PREDEFINED_ATTRIBUTE",
                    NAMES_LINE,
                    index + 1,
                    f"cBioPortal's predefined attribute {name} is of the datatype {rule.datatype}",
                )
            if name in BANNED:
                return self.refuse(
                    "BANNED_ATTRIBUTE",
                    NAMES_LINE,
                    index + 1,
                    f"{name} is an attribute cBioPortal computes, which a study may not hold",
                )
        if self.patient is not None:
            other = {column.name for column in self.patient.columns if not column.key}
            for index, name in enumerate(self.names):
                if name not in keys and name in other:
                    return self.refuse(
                        "ATTRIBUTE_IN_BOTH_FILES",
                        NAMES_LINE,
                        index + 1,
                        "the attribute is in the patient file too",
                    )
        self.setup(labels, definitions, declared, priorities, frozenset(keys))
        return None

    def setup(
        self,
        labels: list[Checked],
        definitions: list[Checked],
        declared: list[Declared],
        priorities: list[int],
        keys: frozenset[str],
    ) -> None:
        names = frozenset(self.names)
        self.labels, self.definitions = labels, definitions
        self.declared, self.priorities = declared, priorities
        self.is_key = [name in keys for name in self.names]
        self.sample_id = [name == SAMPLE_ID for name in self.names]
        self.patient_id = self.names.index(PATIENT_ID)
        self.excel = [not dated_column(name) for name in self.names]
        self.boolean = [
            kind == "BOOLEAN" and not key for kind, key in zip(declared, self.is_key, strict=True)
        ]
        self.status = [
            survival_status(name, names) if self.role == "patient" and not key else None
            for name, key in zip(self.names, self.is_key, strict=True)
        ]
        self.string = [
            kind == "STRING" and not key for kind, key in zip(declared, self.is_key, strict=True)
        ]
        self.joined = TAB.join(self.names)
        self.distinct: list[set[str | bytes]] = [set() for _ in self.names]
        self.present = [0] * self.width
        self.missing: list[set[str]] = [set() for _ in self.names]
        self.long_missing = [False] * self.width
        self.padded = [0] * self.width
        self.padded_rows: list[list[int]] = [[] for _ in self.names]

    # --- The data lines -------------------------------------------------------------------------

    def data_line(self, number: int, line: str) -> None:
        if line == "" or line.isspace():
            self.structure = self.refuse("BLANK_LINE", number, None, "the line is blank")
            return
        if COMMENT.match(line) is not None:
            self.structure = self.refuse(
                "COMMENT_LINE", number, None, "a data line starts with '#'"
            )
            return
        if self.repeats(line):
            self.structure = self.refuse(
                "REPEATED_HEADER", number, None, "the line repeats the attribute names"
            )
            return
        if line.count(TAB) + 1 != self.width:
            self.structure = self.refuse(
                "ROW_WIDTH",
                number,
                None,
                "the line does not have as many fields as there are attribute names",
            )
            return
        fields = line.split(TAB)
        if len(line) > FIELD_CHARACTERS:
            for index, field in enumerate(fields):
                if len(field) > FIELD_CHARACTERS:
                    self.structure = self.refuse(
                        "LONG_FIELD", number, index + 1, "the field is longer than a cell may be"
                    )
                    return
        self.rows += 1
        if self.width * self.rows > self.left:
            self.structure = self.too_many(number)
            return
        if self.cell is None:
            self.cell = self.checked_cells(number, fields)
        if self.cell is None:
            self.counted(number - NAMES_LINE, fields)

    def repeats(self, line: str) -> bool:
        """Whether the line's first fields are the attribute names (and it may have more)."""
        end = len(self.joined)
        return line.startswith(self.joined) and line[end : end + 1] in ("", TAB)

    def too_many(self, number: int | None) -> Refused:
        return self.refuse(
            "TOO_MANY_CELLS",
            number,
            None,
            "the study's clinical files have more cells than an import may",
        )

    def checked_cells(self, number: int, fields: list[str]) -> Refused | None:
        for index, cell in enumerate(fields):
            failure = self.cell_failure(index, cell)
            if failure is not None:
                code, reason = failure
                return self.refuse(code, number, index + 1, reason)
        return None

    def cell_failure(self, index: int, cell: str) -> tuple[Code, str] | None:
        if self.excel[index] and excel_date(cell):
            return (
                "EXCEL_DATE",
                "the value has the shape of a date a spreadsheet made (a month and a number "
                "joined by '-'), in a column whose name does not hold 'date'",
            )
        if self.is_key[index]:
            return _key_failure(cell, self.sample_id[index])
        if cell == "" or classify(cell) is not None:
            return None
        trimmed = cell.strip(SPACE)
        if self.boolean[index] and trimmed not in TRUE_FALSE:
            return "BOOLEAN_VALUE", "the value is not TRUE, FALSE or a missing value"
        rule = self.status[index]
        if rule == "os" and trimmed not in OS_VALUES:
            return "SURVIVAL_STATUS", _SURVIVAL
        if rule == "dfs" and trimmed not in DFS_VALUES:
            return "SURVIVAL_STATUS", _SURVIVAL
        if rule == "prefix" and not survival_prefix(trimmed):
            return "SURVIVAL_STATUS", _SURVIVAL
        return None

    def counted(self, row: int, fields: list[str]) -> None:
        """What a row adds to the columns' aggregates and the keys."""
        identifier = fields[self.patient_id]
        if self.role == "patient":
            self.keys.add(identifier)
        elif self.patient is not None and identifier not in self.patient.keys:
            self.dangling += 1
            if len(self.dangling_rows) < ROWS_SHOWN:
                self.dangling_rows.append(row)
        for index, cell in enumerate(fields):
            if cell == "" or self.is_key[index]:
                continue
            if classify(cell) is not None:
                kept = self.missing[index]
                if cell in FIXED:
                    pass
                elif len(cell) > ADDITION_MOST:
                    self.long_missing[index] = True
                elif len(kept) <= ADDITIONS_MOST:
                    kept.add(cell)
                continue
            if not self.string[index]:
                continue
            self.present[index] += 1
            distinct = self.distinct[index]
            if len(distinct) <= CATEGORY_VALUES:
                distinct.add(identity(cell))
            if cell[0] == SPACE or cell[-1] == SPACE:
                self.padded[index] += 1
                if len(self.padded_rows[index]) < ROWS_SHOWN:
                    self.padded_rows[index].append(row)

    # --- The end --------------------------------------------------------------------------------

    def finished(self, data: bytes) -> Clinical:
        if len(self.header) < NAMES_LINE:
            self.structure = self.refuse(
                "HEADER_ROWS",
                None,
                None,
                "the file does not start with four header rows that begin with '#' and a line "
                "of attribute names",
            )
        if self.structure is None and self.width * max(1, self.rows) > self.left:
            self.structure = self.too_many(None)
        for found in (self.control, self.structure, self.cell):
            if found is not None:
                raise found
        columns: list[Column] = []
        for index, name in enumerate(self.names):
            added = (
                {}
                if self.is_key[index]
                else None
                if self.long_missing[index]
                else additions(self.missing[index])
            )
            if added is None:
                raise self.refuse(
                    "MISSING_FORM",
                    None,
                    index + 1,
                    "the column holds more spellings of a missing value than the pack declares, "
                    "or a longer one",
                )
            distinct = len(self.distinct[index])
            columns.append(
                Column(
                    name=name,
                    label=self.labels[index],
                    definition=self.definitions[index],
                    declared=self.declared[index],
                    priority=self.priorities[index],
                    key=self.is_key[index],
                    category=self.string[index]
                    and 0 < distinct <= CATEGORY_VALUES
                    and 2 * distinct <= self.present[index],
                    additions=added,
                    padded=self.padded[index],
                    padded_rows=tuple(self.padded_rows[index]),
                )
            )
        return Clinical(
            name=self.name,
            role=self.role,
            data=data,
            columns=tuple(columns),
            rows=self.rows,
            keys=frozenset(self.keys),
            dangling=self.dangling,
            dangling_rows=tuple(self.dangling_rows),
        )


def read(
    data: bytes,
    name: str,
    role: Role,
    limits: ImportLimits,
    *,
    cells_before: int = 0,
    patient: Clinical | None = None,
) -> Clinical:
    """The clinical file ``name`` (a patient's or a sample's), read from its bytes ``data``
    (the module's docstring), with ``cells_before`` cells of the import counted before it and,
    for a sample file, the study's patient file as read. Raises ``Refused``."""
    reading = _Reading(name, role, limits, cells_before, patient)
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
        if number <= NAMES_LINE:
            reading.header_line(number, line)
        else:
            reading.data_line(number, line)
    return reading.finished(data)


__all__ = [
    "LINE_END",
    "SETTINGS",
    "Clinical",
    "Column",
    "Role",
    "identity",
    "lines",
    "read",
]

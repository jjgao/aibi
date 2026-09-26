"""The core's importer of files (SPEC §13.1, D225–D227): ``aibi.files``.

A source is a file, a directory of files, or a ``.zip`` archive of files at any depth. A
directory is read flat: its regular files, by their own names; subdirectories, hidden entries
(a name starting with ``.``), symlinks (never followed), entries that are not regular files, a
file that cannot be confined, archives (a ``.zip`` inside the source is never read) and files
of other kinds are skipped and noted, as are an archive's hidden members and members of other
kinds. By extension, ``.csv``, ``.tsv`` and ``.txt`` files are delimited text, their parse
settings detected (``detect``); ``.xlsx``, ``.xlsm`` and ``.ods`` files are workbooks, one table
per worksheet with cells (``sheets``); ``.parquet`` files are Parquet. A single file of any
other kind is refused, with the kinds read as alternatives, and so is an ``.xls`` workbook
(D225).

A table's original name is the file's name without its extension (an upload's original name, which
its stored path does not keep, D234); a worksheet's is the sheet's name when the source is that one
workbook, and the file's then the sheet's, joined by a space, when there are other files. Table and
column ids are normalised from original names (§5.1), the table id ``dataset`` avoided; each one
that differs from its original name is noted. On a re-import, the *k*-th occurrence of a name keeps
the id of its *k*-th occurrence in the previous release (``ImportOptions.previous``, D238). Each
table is laid out on a raw snapshot named by its id: a text file's bytes, or a sheet's or Parquet
file's typed rows (§12.2). Workbooks and Parquet files are read in the import's one worker process
(``worker``), under ``reader_memory``, ``reader_seconds`` and ``decoded_bytes``, never in the
server's. The limits on tables, columns and cells apply as the files are read: a text file's cells
are counted while it is parsed, a sheet's extent before its cells become values, and a Parquet
file's cells from its metadata. Every name that becomes a label or an original name (a header, a
Parquet column's name, a sheet's name, a file's or an archive member's, the dataset's name) is a
descriptor's string, so one over ``MAX_STRING`` characters is refused (``LIMIT_EXCEEDED``) before
anything is described. What stays in memory until the release is built is bounded by
``import_bytes``, the archive limits, ``import_cells`` and ``decoded_bytes``: a text file's bytes,
which are its raw snapshot, and every table's rows.
"""

import os
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Literal, cast

import aibi
from aibi.core.importers import archives
from aibi.core.importers.describe import DatasetOrigin, TableOrigin, by_importer, describe
from aibi.core.importers.detect import detect
from aibi.core.importers.errors import ImportRefused, long_cell_refused, refused, within
from aibi.core.importers.infer import Datatype, SourceTable, Status, infer
from aibi.core.importers.sheets import read_workbook
from aibi.core.importers.worker import Reader
from aibi.core.schema.descriptors import ParseSettings
from aibi.core.schema.ids import normalise_names
from aibi.core.schema.limits import (
    IMPORT_CELLS,
    IMPORT_TABLES,
    MAX_STRING,
    STRING_CHARACTERS,
    TABLE_COLUMNS,
    ImportLimits,
)
from aibi.core.schema.output import data, text
from aibi.core.schema.pack_api import ConfinedPath, ImportNote, ImportOptions, ImportResult
from aibi.core.schema.refusals import RefusalCode
from aibi.core.store import parquet
from aibi.core.store.build import Layout
from aibi.core.store.sources import (
    RawSource,
    SourceValue,
    TextSource,
    TooManyCells,
    TypedSource,
    long_cell,
)

NAME = "aibi.files"
TEXT = frozenset({"csv", "tsv", "txt"})
WORKBOOKS = frozenset({"xlsx", "xlsm", "ods"})
PARQUET = frozenset({"parquet"})
READ = (*sorted(TEXT), *sorted(WORKBOOKS), *sorted(PARQUET))
"""The extensions read, and ``zip`` for a single archive."""
REFUSED = {"xls": ("xlsx", "ods", "csv")}
"""Extensions refused with the kinds to convert to (D225)."""
_SKIPPED_KINDS = {
    "directory": "a directory, and directories are flat",
    "symlink": "a symbolic link, which is never followed",
    "other": "neither a regular file nor a directory",
    "unconfined": "it could not be confined as it was listed",
}

DECLARED: dict[parquet.ArrowKind, tuple[Datatype, Status]] = {
    "integer": ("integer", "imported"),
    "number": ("number", "imported"),
    "boolean": ("boolean", "imported"),
    "date": ("date", "imported"),
    "datetime": ("datetime", "imported"),
    "naive_datetime": ("datetime", "imported_default"),
}
"""The datatypes a typed source declares, by the Arrow kind a Parquet file or a database's column
has, with their status (D227)."""


@dataclass(frozen=True)
class _Unit:
    """A table as read, before it has an id."""

    kind: Literal["file", "sheet"]
    original_name: str
    label: str
    raw: RawSource
    names: tuple[str, ...]
    rows: Sequence[Sequence[SourceValue]]
    parse: ParseSettings | None = None
    parse_evidence: str | None = None
    declared: dict[int, tuple[Datatype, Status]] = field(
        default_factory=dict[int, tuple[Datatype, Status]]
    )


def _extension(name: str) -> str:
    return PurePosixPath(name).suffix.lower().removeprefix(".")


def _stem(name: str) -> str:
    return PurePosixPath(name).stem


def _skipped(name: str, why: str) -> ImportNote:
    return ImportNote("skipped_source", None, [text("Skipped "), data(name), text(f": {why}")])


class _Reading:
    def __init__(self, options: ImportOptions) -> None:
        self.options = options
        self.limits: ImportLimits = options.limits
        self.notes: list[ImportNote] = []
        self.units: list[_Unit] = []
        self.cells = 0
        self.worker = Reader(self.limits)

    def files(self, source: ConfinedPath) -> list[tuple[str, bytes]]:
        """The files of the source, by name, with their bytes."""
        reader = self.options.reader
        name = os.path.basename(source)
        original = self.options.original_name
        if original is not None:
            bounded(original, "The upload's original name")
        if original and not os.path.isdir(source):
            name = f"{_stem(original)}.{_extension(name)}"
        if os.path.isdir(source):
            found: list[tuple[str, bytes]] = []
            for entry in reader.files(source):
                if entry.name.startswith("."):
                    self.notes.append(_skipped(entry.name, "a hidden file"))
                elif entry.kind != "file" or entry.path is None:
                    kind = "unconfined" if entry.kind == "file" else entry.kind
                    self.notes.append(_skipped(entry.name, _SKIPPED_KINDS[kind]))
                elif (why := self._skip(entry.name)) is not None:
                    self.notes.append(_skipped(entry.name, why))
                else:
                    found.append((entry.name, reader.read(entry.path, self.limits.import_bytes)))
            return found
        extension = _extension(name)
        if extension == "zip":
            data = reader.read(source, self.limits.import_bytes)
            archived = archives.members(data, self.limits, keep=lambda m: self._skip(m) is None)
            kept: list[tuple[str, bytes]] = []
            for member, content in sorted(archived, key=lambda found: found[0]):
                if content is None:
                    self.notes.append(_skipped(member, cast(str, self._skip(member))))
                else:
                    kept.append((PurePosixPath(member).name, content))
            return kept
        if extension in REFUSED:
            raise _refused_kind(name, REFUSED[extension])
        if extension not in READ:
            raise _refused_kind(name, (*READ, "zip"))
        return [(name, reader.read(source, self.limits.import_bytes))]

    @staticmethod
    def _skip(name: str) -> str | None:
        """Why a directory entry or an archive member (by its path in the archive) is skipped,
        by its name alone."""
        parts = PurePosixPath(name).parts
        if any(part.startswith(".") for part in parts):
            return "a hidden file"
        extension = _extension(name)
        if extension == "zip":
            return "an archive inside the source, which is not read (D225)"
        if extension in REFUSED:
            kinds = ", ".join(f".{kind}" for kind in REFUSED[extension])
            return f"a kind of file that is not read; convert it to {kinds} (D225)"
        if extension not in READ:
            return "not a kind of file that is read"
        return None

    def add(self, unit: _Unit) -> None:
        bounded(unit.original_name, "A table's original name")
        bounded(unit.label, "A table's original name")
        for name in unit.names:
            bounded(name, "A column's name")
        if len(self.units) >= self.limits.import_tables:
            raise refused(
                RefusalCode.LIMIT_EXCEEDED,
                f"The source has more than {self.limits.import_tables} tables",
                limit=(IMPORT_TABLES, self.limits.import_tables),
            )
        if len(unit.names) > self.limits.table_columns:
            raise refused(
                RefusalCode.LIMIT_EXCEEDED,
                f"A table has more than {self.limits.table_columns} columns: ",
                data(unit.label),
                limit=(TABLE_COLUMNS, self.limits.table_columns),
            )
        self.cells += len(unit.names) * max(1, len(unit.rows))
        if self.cells > self.limits.import_cells:
            raise self._too_many_cells()
        self.units.append(unit)

    def _too_many_cells(self) -> ImportRefused:
        return refused(
            RefusalCode.LIMIT_EXCEEDED,
            f"The source has more than {self.limits.import_cells} cells",
            limit=(IMPORT_CELLS, self.limits.import_cells),
        )

    def read(self, name: str, content: bytes, several: bool) -> None:
        extension = _extension(name)
        stem = _stem(name)
        if extension in TEXT:
            try:
                detected = detect(
                    content, extension=extension, max_cells=self.limits.import_cells - self.cells
                )
            except TooManyCells:
                raise self._too_many_cells() from None
            parsed = detected.parsed
            self.add(
                _Unit(
                    "file",
                    name,
                    stem,
                    TextSource(content),
                    parsed.names,
                    parsed.rows,
                    detected.settings,
                    detected.evidence,
                )
            )
        elif extension in WORKBOOKS:
            read = self.worker.run(
                read_workbook, content, self.limits, self.limits.import_cells - self.cells
            )
            for sheet in read:
                label = f"{stem} {sheet.name}" if several else sheet.name
                if sheet.source is None:
                    self.notes.append(_skipped(label, cast(str, sheet.skipped)))
                    continue
                typed = sheet.source
                self.add(_Unit("sheet", sheet.name, label, typed, typed.columns, typed.rows))
        else:
            self.parquet(name, stem, content)

    def parquet(self, name: str, stem: str, content: bytes) -> None:
        read = self.worker.run(
            read_parquet, content, self.limits, self.limits.import_cells - self.cells
        )
        rows = cast(tuple[tuple[SourceValue, ...], ...], read.rows)
        typed = TypedSource(read.names, rows)
        declared = {i: DECLARED[kind] for i, kind in enumerate(read.kinds) if kind in DECLARED}
        self.add(_Unit("file", name, stem, typed, read.names, rows, declared=declared))


class FileImporter:
    """The core's importer, of files (``aibi.files``); an ``Importer`` like a pack's (D235)."""

    name = NAME

    @property
    def version(self) -> str:
        return aibi.__version__

    def import_source(self, source: ConfinedPath, options: ImportOptions) -> ImportResult:
        named = (
            options.name or _stem(options.original_name or os.path.basename(source)) or "dataset"
        )
        bounded(named, "The dataset's name")
        reading = _Reading(options)
        files = reading.files(source)
        several = not (len(files) == 1 and _extension(files[0][0]) in WORKBOOKS)
        with reading.worker:
            if any(_extension(name) in WORKBOOKS | PARQUET for name, _ in files):
                reading.worker.start()
            for name, content in files:
                try:
                    reading.read(name, content, several)
                except ImportRefused as error:
                    raise within(error, name) from None
        if not reading.units:
            raise refused(RefusalCode.EMPTY_SOURCE, "The source holds no table")
        units = reading.units
        notes = reading.notes
        previous = options.previous
        tables_before = previous.tables if previous is not None else None
        table_ids = normalise_names([unit.label for unit in units], "table", tables_before)
        sources: dict[str, RawSource] = {}
        layouts: dict[str, Layout] = {}
        origins: dict[str, TableOrigin] = {}
        tables: list[SourceTable] = []
        for table, unit in zip(table_ids, units, strict=True):
            if table != unit.label:
                notes.append(renamed(table, unit.label))
            columns_before = previous.columns.get(table) if previous is not None else None
            columns = normalise_names(unit.names, "column", columns_before)
            for column, original in zip(columns, unit.names, strict=True):
                if column != original:
                    notes.append(renamed(f"{table}.{column}", original))
            sources[table] = unit.raw
            layouts[table] = Layout(table, tuple(zip(columns, unit.names, strict=True)))
            origins[table] = TableOrigin(
                unit.kind,
                unit.original_name,
                unit.label,
                unit.names,
                unit.parse,
                unit.parse_evidence,
            )
            declared = {columns[i]: found for i, found in unit.declared.items()}
            tables.append(SourceTable(table, tuple(columns), unit.rows, declared))
        inferred = infer(tables)
        location = options.reader.location(source)
        dataset = DatasetOrigin(named, location if location != "." else os.path.basename(source))
        bounded(dataset.location, "The source's location")
        descriptors = describe(
            dataset, origins, inferred, by=by_importer(NAME, self.version), at=options.at
        )
        return ImportResult(sources, layouts, descriptors, [*notes, *inferred.notes])


def read_parquet(content: bytes, limits: ImportLimits, max_cells: int) -> parquet.ParquetSource:
    """A Parquet file read as a source (``parquet.read_source``), with at most ``max_cells``
    cells, those left under ``import_cells``, and no string over ``FIELD_CHARACTERS``
    characters (``UNPARSEABLE_SOURCE``, as a text file's field over it is). Called in the
    import's worker process. Raises ``ImportRefused``."""
    try:
        read = parquet.read_source(content, max_cells)
    except parquet.UnsupportedTypeError as error:
        raise refused(
            RefusalCode.UNSUPPORTED_FORMAT,
            f"A Parquet column's type is not read: {error}",
            alternatives=list(parquet.SUPPORTED_TYPES),
        ) from None
    except parquet.CellLimitError:
        raise refused(
            RefusalCode.LIMIT_EXCEEDED,
            f"The source has more than {limits.import_cells} cells",
            limit=(IMPORT_CELLS, limits.import_cells),
        ) from None
    except parquet.UnreadableParquetError as error:
        raise refused(
            RefusalCode.UNPARSEABLE_SOURCE, f"Not a Parquet file that can be read: {error}"
        ) from None
    if (found := long_cell(read.rows)) is not None:
        raise long_cell_refused(found, read.names, "The file")
    return read


def _refused_kind(name: str, kinds: Sequence[str]) -> ImportRefused:
    return refused(
        RefusalCode.UNSUPPORTED_FORMAT,
        "Files of this kind are not imported: ",
        data(name),
        alternatives=[f".{known}" for known in kinds],
    )


def bounded(name: str, what: str) -> None:
    """Refuse a name longer than a descriptor's string (§14)."""
    if len(name) > MAX_STRING:
        raise refused(
            RefusalCode.LIMIT_EXCEEDED,
            f"{what} has more than {MAX_STRING} characters",
            limit=(STRING_CHARACTERS, MAX_STRING),
        )


def renamed(subject: str, original: str) -> ImportNote:
    return ImportNote("renamed", subject, [text("The original name was "), data(original)])


__all__ = ["DECLARED", "NAME", "READ", "FileImporter", "bounded", "renamed"]

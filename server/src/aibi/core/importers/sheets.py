"""Workbooks (XLSX, XLSM, ODS): the only module that calls python-calamine (SPEC §12.2, §13.1,
D225).

Each worksheet is a typed source. Its header is the first row with a non-empty cell; the rows
after it whose every cell is empty are dropped, as blank records are (D215), and columns empty
at either edge, header included, are trimmed. A sheet with no cell is skipped, and so is a sheet
that is not a worksheet (a chart or a macro sheet); a sheet with only a header is a table of no
rows. Header names are the canonical strings of the header cells (§12.2), an empty cell giving
an empty name.

Values are kept as calamine reads them: numbers as floats (integral ones are integers to a
column of integers, §12.2), booleans, strings, dates and datetimes, which spreadsheets hold
without an offset. A time of day becomes its ISO 8601 text and a duration an ISO 8601 duration
(``P1DT6H``). An empty cell is null. python-calamine reads an error cell (``#N/A``) as an empty
string, so an error cell is an empty cell here: UNKNOWN, as §12.2 has an error cell without a
missing code, but never matched by its error text.

A workbook is checked as an archive (``archives.check_container``) before calamine reads it, and
one that holds ``xl/workbook.bin`` is an XLSB workbook, which is not read (``UNSUPPORTED_FORMAT``)
whatever its extension says, since calamine would read it as one. Calamine allocates a sheet's
whole range, from A1 to its last cell, before anything can be counted, and what it decodes (a
shared string every cell names, an ODS cell repeated) is not bounded by the file's bytes, so
``read_workbook`` runs in the import's worker process (``worker``), under its memory, time and
decoded-text limits (D225). There each sheet's extent, its last row times its last column from
A1 as calamine reads it, is counted against the cells left under ``import_cells``, for one sheet
and for the sheets together, before its cells become Python values, and a sheet with a string
over ``FIELD_CHARACTERS`` characters, which no field of a text file can hold, is
``UNPARSEABLE_SOURCE``. XLS workbooks are not read in v1 (``files``, D225).
"""

import io
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from python_calamine import CalamineError, CalamineSheet, CalamineWorkbook, SheetTypeEnum

from aibi.core.importers.archives import check_container
from aibi.core.importers.errors import ImportRefused, long_cell_refused, refused
from aibi.core.schema import output
from aibi.core.schema.limits import IMPORT_CELLS, ImportLimits
from aibi.core.schema.refusals import RefusalCode
from aibi.core.store.sources import SourceValue, TypedSource, canonical_string, long_cell

Calamine = int | float | str | bool | time | date | datetime | timedelta
XLSB = "xl/workbook.bin"


@dataclass(frozen=True)
class Sheet:
    name: str
    source: TypedSource | None
    """``None`` for a sheet skipped: one with no cell, or not a worksheet."""
    skipped: str | None = None
    """Why it was skipped."""


def duration(value: timedelta) -> str:
    """An ISO 8601 duration: days, hours, minutes and seconds, zero parts left out."""
    micro = value // timedelta(microseconds=1)
    sign = "-" if micro < 0 else ""
    days, rest = divmod(abs(micro), 86_400_000_000)
    hours, rest = divmod(rest, 3_600_000_000)
    minutes, rest = divmod(rest, 60_000_000)
    seconds, fraction = divmod(rest, 1_000_000)
    clock = f"{hours}H" if hours else ""
    clock += f"{minutes}M" if minutes else ""
    if fraction:
        clock += f"{seconds}.{fraction:06d}".rstrip("0") + "S"
    elif seconds:
        clock += f"{seconds}S"
    written = (f"{days}D" if days else "") + (f"T{clock}" if clock else "")
    return sign + "P" + (written or "T0S")


def _value(value: Calamine) -> SourceValue:
    if isinstance(value, str):
        return value or None
    if isinstance(value, timedelta):
        return duration(value)
    if isinstance(value, time):
        return value.isoformat()
    return value


def _source(rows: list[list[Calamine]]) -> TypedSource | None:
    values = [[_value(value) for value in row] for row in rows]
    start = next((i for i, row in enumerate(values) if any(v is not None for v in row)), None)
    if start is None:
        return None
    kept = [values[start]] + [row for row in values[start + 1 :] if any(v is not None for v in row)]
    width = max(len(row) for row in kept)
    used = [i for i in range(width) if any(i < len(row) and row[i] is not None for row in kept)]
    first, last = used[0], used[-1] + 1

    def cut(row: list[SourceValue]) -> tuple[SourceValue, ...]:
        padded = row + [None] * (width - len(row))
        return tuple(padded[first:last])

    header = tuple(canonical_string(value) or "" for value in cut(kept[0]))
    return TypedSource(header, tuple(cut(row) for row in kept[1:]))


def _over(limit: int, together: bool = False) -> ImportRefused:
    what = "The sheets together reach" if together else "A sheet reaches"
    return refused(
        RefusalCode.LIMIT_EXCEEDED,
        f"{what} more than {limit} cells, counted from A1",
        limit=(IMPORT_CELLS, limit),
    )


def _extent(sheet: CalamineSheet) -> int:
    end = sheet.end
    return 0 if end is None else (end[0] + 1) * (end[1] + 1)


def read_workbook(data: bytes, limits: ImportLimits, max_cells: int) -> list[Sheet]:
    """The sheets of a workbook, in the workbook's order, their extents together at most
    ``max_cells``, the cells left under ``import_cells``. Called in the import's worker process.
    Raises ``ImportRefused``."""
    if any(name.lower() == XLSB for name in check_container(data, limits)):
        raise refused(
            RefusalCode.UNSUPPORTED_FORMAT,
            "An XLSB workbook is not read",
            alternatives=[".xlsx", ".ods", ".csv"],
        )
    try:
        workbook = CalamineWorkbook.from_filelike(io.BytesIO(data))
        with workbook:
            sheets: list[Sheet] = []
            cells = 0
            for metadata in workbook.sheets_metadata:
                if metadata.typ != SheetTypeEnum.WorkSheet:
                    sheets.append(Sheet(metadata.name, None, "it is not a worksheet"))
                    continue
                sheet = workbook.get_sheet_by_name(metadata.name)
                extent = _extent(sheet)
                cells += extent
                if extent > max_cells or cells > max_cells:
                    raise _over(limits.import_cells, together=extent <= max_cells)
                source = _source(sheet.to_python(skip_empty_area=False))
                if source is not None and (found := long_cell(source.rows)) is not None:
                    named = output.data(metadata.name)
                    raise long_cell_refused(found, source.columns, "The sheet ", named)
                skipped = "it has no cell" if source is None else None
                sheets.append(Sheet(metadata.name, source, skipped))
    except (CalamineError, OSError, ValueError, OverflowError) as error:
        name = type(error).__name__
        raise refused(
            RefusalCode.UNPARSEABLE_SOURCE, f"The workbook cannot be read ({name})"
        ) from None
    return sheets


__all__ = ["Sheet", "duration", "read_workbook"]

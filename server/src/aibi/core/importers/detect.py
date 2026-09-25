"""Detecting how a delimited text file is read (SPEC §5.3, §13.1, D224).

- **Encoding:** a UTF-8 byte-order mark gives ``utf-8-sig`` and a UTF-16 one ``utf-16``; bytes
  that decode strictly as UTF-8 are ``utf-8``, else ``cp1252`` if they decode as that, else
  ``latin-1``, which decodes any bytes.
- **Delimiter:** comma, semicolon, bar and tab (tab first for a ``.tsv`` file) are tried in turn
  on the first 1 MiB of text, at most 1,000 records. Each gives its records' modal field count
  (the smaller on a tie, which splits less); the winner has the most records with that count
  when it is above 1, the first candidate on a tie. With no candidate above 1 the file has one
  column, and the delimiter is a comma. ``tsv`` is the format exactly when the delimiter is a
  tab.
- **Quote:** always ``"``.
- **Skipped lines:** the leading lines that are blank, are comments, or have another field
  count than the modal one; they end at the first line with the modal count, the header. A
  comment starts with ``#`` followed by a space, another ``#`` or nothing (``# exported``,
  ``##meta``), so a header whose first name starts with ``#`` (``#id,name``, ``#CHROM``) is a
  header. ``header_row`` is always 0: files without a header row are not detected.

What is detected is checked by reading the whole file with it, its cells counted as it is read,
and ``TooManyCells`` raised past ``max_cells``. A file with no record, or one the
settings detected cannot read, is ``UNPARSEABLE_SOURCE``. The evidence names the rules that
applied and counts, never a cell value (A6).
"""

import codecs
import csv
import io
import re
from collections import Counter
from dataclasses import dataclass

from aibi.core.importers.errors import refused
from aibi.core.schema.descriptors import ParseSettings
from aibi.core.schema.refusals import RefusalCode
from aibi.core.store.sources import Parsed, SourceError, TooManyCells, parse_text

SAMPLE_CHARACTERS = 1 << 20
SAMPLE_RECORDS = 1_000
QUOTE = '"'
_DELIMITERS = (",", ";", "|")
_LINE_END = re.compile(r"\r\n|\n|\r")
"""Line ends as ``skip_rows`` counts them (D215)."""
_NAMES = {"\t": "tab", ",": "comma", ";": "semicolon", "|": "bar"}
_COMMENT = re.compile(r"#(?:[#\s]|$)")


@dataclass(frozen=True)
class Detected:
    settings: ParseSettings
    evidence: str
    parsed: Parsed
    """The file read with the settings detected."""


def _encoding(data: bytes) -> tuple[str, str]:
    if data.startswith(codecs.BOM_UTF8):
        return "utf-8-sig", "a UTF-8 byte-order mark"
    if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return "utf-16", "a UTF-16 byte-order mark"
    for encoding in ("utf-8", "cp1252"):
        try:
            data.decode(encoding)
        except UnicodeDecodeError:
            continue
        return encoding, f"the bytes decode strictly as {encoding}"
    return "latin-1", "the bytes decode neither as utf-8 nor as cp1252"


def _counts(sample: str, delimiter: str) -> list[int]:
    """The field counts of the sample's first records, blank lines left out."""
    reader = csv.reader(io.StringIO(sample, newline=""), delimiter=delimiter, quotechar=QUOTE)
    found: list[int] = []
    try:
        for record in reader:
            if record:
                found.append(len(record))
            if len(found) >= SAMPLE_RECORDS:
                break
    except csv.Error:
        pass
    return found


def _modal(counts: list[int]) -> tuple[int, int]:
    """The modal count, the smaller on a tie, and how many records have it."""
    if not counts:
        return 0, 0
    tallied = Counter(counts)
    modal = max(tallied, key=lambda count: (tallied[count], -count))
    return modal, tallied[modal]


def _line_count(line: str, delimiter: str) -> int:
    reader = csv.reader(io.StringIO(line, newline=""), delimiter=delimiter, quotechar=QUOTE)
    try:
        record = next(reader, None)
    except csv.Error:
        return -1
    return 0 if record is None else len(record)


def _skipped(sample: str, delimiter: str, modal: int) -> int:
    lines: list[str] = _LINE_END.split(sample)
    for index, line in enumerate(lines[:SAMPLE_RECORDS]):
        if not line.strip() or _COMMENT.match(line):
            continue
        if _line_count(line, delimiter) == modal:
            return index
    return 0


def detect(data: bytes, *, extension: str, max_cells: int | None = None) -> Detected:
    """The parse settings of a text file, and the file read with them. Raises
    ``ImportRefused`` (``UNPARSEABLE_SOURCE``), or ``TooManyCells`` past ``max_cells``."""
    encoding, why = _encoding(data)
    text = data.decode(encoding, errors="replace")
    sample = text[:SAMPLE_CHARACTERS]
    candidates = ("\t", *_DELIMITERS) if extension == "tsv" else (*_DELIMITERS, "\t")
    best: tuple[str, int, int, int] | None = None
    for delimiter in candidates:
        counts = _counts(sample, delimiter)
        modal, records = _modal(counts)
        if modal > 1 and (best is None or records > best[2]):
            best = (delimiter, modal, records, len(counts))
    if best is None:
        counts = _counts(sample, ",")
        if not counts:
            raise refused(RefusalCode.UNPARSEABLE_SOURCE, "The file holds no record")
        best = (",", 1, len(counts), len(counts))
        how = "no delimiter gave more than one field, so the file has one column"
    else:
        how = (
            f"the {_NAMES[best[0]]} gave {best[2]} of {best[3]} sampled records with "
            f"{best[1]} fields, the most of the delimiters tried"
        )
    delimiter, modal = best[0], best[1]
    skip = _skipped(sample, delimiter, modal)
    settings = ParseSettings(
        format="tsv" if delimiter == "\t" else "csv",
        delimiter=delimiter,
        quote=QUOTE,
        header_row=0,
        skip_rows=skip,
        encoding=encoding,
    )
    try:
        parsed = parse_text(data, settings, max_cells=max_cells)
    except TooManyCells:
        raise
    except SourceError as error:
        raise refused(
            RefusalCode.UNPARSEABLE_SOURCE,
            f"The file cannot be read with the settings detected ({error})",
        ) from None
    evidence = f"Detected (D224): encoding {encoding}, since {why}; {how}"
    if skip:
        evidence += f"; {skip} leading line{'s' if skip != 1 else ''} before the header skipped"
    return Detected(settings, evidence, parsed)


__all__ = ["SAMPLE_CHARACTERS", "SAMPLE_RECORDS", "Detected", "detect"]

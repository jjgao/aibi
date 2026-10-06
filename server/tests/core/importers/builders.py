"""The importer tests' builders of workbooks and archives: a minimal SpreadsheetML workbook with
inline strings (``build_xlsx``), a minimal OpenDocument one (``build_ods``) and a zip archive of
``Entry`` values, which can be symbolic links or encrypted (``build_zip``), and one that lies
about its size (``build_lie``), so that edge cases and bombs need no binary fixture. The
fixtures in ``conftest.py`` give them to the tests that take fixtures; a module that imports
them by name takes them from here (a conftest is pytest's, not a module to import)."""

import io
import stat
import struct
import zipfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from xml.sax.saxutils import escape, quoteattr

# --- Workbooks ---------------------------------------------------------------------------------

_EPOCH = datetime(1899, 12, 30)
_STYLES = {"date": 1, "datetime": 2, "time": 3, "duration": 4}


@dataclass(frozen=True)
class Error:
    """A spreadsheet error cell."""

    text: str


def _column_name(index: int) -> str:
    name = ""
    index += 1
    while index:
        index, rest = divmod(index - 1, 26)
        name = chr(65 + rest) + name
    return name


def _serial(value: date | datetime | time | timedelta) -> tuple[str, int]:
    if isinstance(value, datetime):
        return repr((value - _EPOCH) / timedelta(days=1)), _STYLES["datetime"]
    if isinstance(value, date):
        return str((value - _EPOCH.date()).days), _STYLES["date"]
    if isinstance(value, time):
        seconds = value.hour * 3600 + value.minute * 60 + value.second
        return repr(seconds / 86400), _STYLES["time"]
    return repr(value / timedelta(days=1)), _STYLES["duration"]


def _xlsx_cell(reference: str, value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, Error):
        return f'<c r="{reference}" t="e"><v>{escape(value.text)}</v></c>'
    if isinstance(value, bool):
        return f'<c r="{reference}" t="b"><v>{int(value)}</v></c>'
    if isinstance(value, int | float):
        return f'<c r="{reference}"><v>{value!r}</v></c>'
    if isinstance(value, date | time | timedelta):
        serial, style = _serial(value)
        return f'<c r="{reference}" s="{style}"><v>{serial}</v></c>'
    return f'<c r="{reference}" t="inlineStr"><is><t>{escape(str(value))}</t></is></c>'


_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_RELS = "http://schemas.openxmlformats.org/package/2006/relationships"
_DOC = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_STYLESHEET = (
    f'<?xml version="1.0"?><styleSheet xmlns="{_MAIN}"><numFmts count="1">'
    '<numFmt numFmtId="164" formatCode="[h]:mm:ss"/></numFmts><fonts count="1"><font/></fonts>'
    '<fills count="1"><fill/></fills><borders count="1"><border/></borders>'
    '<cellStyleXfs count="1"><xf/></cellStyleXfs><cellXfs count="5"><xf numFmtId="0"/>'
    '<xf numFmtId="14" applyNumberFormat="1"/><xf numFmtId="22" applyNumberFormat="1"/>'
    '<xf numFmtId="21" applyNumberFormat="1"/><xf numFmtId="164" applyNumberFormat="1"/>'
    "</cellXfs></styleSheet>"
)

Sheets = Sequence[tuple[str, Sequence[Sequence[object]]]]


def _zip(parts: Mapping[str, str | bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in parts.items():
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_STORED if name == "mimetype" else zipfile.ZIP_DEFLATED
            archive.writestr(info, content)
    return buffer.getvalue()


def build_xlsx(sheets: Sheets) -> bytes:
    count = len(sheets)
    overrides = "".join(
        f'<Override PartName="/xl/worksheets/sheet{i + 1}.xml" ContentType="application/'
        'vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        for i in range(count)
    )
    parts: dict[str, str | bytes] = {
        "[Content_Types].xml": (
            '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/'
            'content-types"><Default Extension="rels" ContentType="application/'
            'vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" '
            'ContentType="application/xml"/><Override PartName="/xl/workbook.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.'
            f'main+xml"/>{overrides}<Override PartName="/xl/styles.xml" ContentType="application/'
            'vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/></Types>'
        ),
        "_rels/.rels": (
            f'<?xml version="1.0"?><Relationships xmlns="{_RELS}"><Relationship Id="rId1" '
            f'Type="{_DOC}/officeDocument" Target="xl/workbook.xml"/></Relationships>'
        ),
        "xl/workbook.xml": (
            f'<?xml version="1.0"?><workbook xmlns="{_MAIN}" xmlns:r="{_DOC}"><sheets>'
            + "".join(
                f'<sheet name={quoteattr(name)} sheetId="{i + 1}" r:id="rId{i + 1}"/>'
                for i, (name, _) in enumerate(sheets)
            )
            + "</sheets></workbook>"
        ),
        "xl/_rels/workbook.xml.rels": (
            f'<?xml version="1.0"?><Relationships xmlns="{_RELS}">'
            + "".join(
                f'<Relationship Id="rId{i + 1}" Type="{_DOC}/worksheet" '
                f'Target="worksheets/sheet{i + 1}.xml"/>'
                for i in range(count)
            )
            + f'<Relationship Id="rId{count + 1}" Type="{_DOC}/styles" Target="styles.xml"/>'
            + "</Relationships>"
        ),
        "xl/styles.xml": _STYLESHEET,
    }
    for i, (_, rows) in enumerate(sheets):
        body = "".join(
            f'<row r="{r + 1}">'
            + "".join(_xlsx_cell(f"{_column_name(c)}{r + 1}", value) for c, value in enumerate(row))
            + "</row>"
            for r, row in enumerate(rows)
        )
        parts[f"xl/worksheets/sheet{i + 1}.xml"] = (
            f'<?xml version="1.0"?><worksheet xmlns="{_MAIN}"><sheetData>{body}</sheetData>'
            "</worksheet>"
        )
    return _zip(parts)


_ODS_NS = (
    'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
    'xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" '
    'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"'
)


def _ods_cell(value: object) -> str:
    if value is None:
        return "<table:table-cell/>"
    if isinstance(value, bool):
        word = "true" if value else "false"
        return (
            f'<table:table-cell office:value-type="boolean" office:boolean-value="{word}">'
            f"<text:p>{word.upper()}</text:p></table:table-cell>"
        )
    if isinstance(value, int | float):
        return (
            f'<table:table-cell office:value-type="float" office:value="{value!r}">'
            f"<text:p>{value!r}</text:p></table:table-cell>"
        )
    if isinstance(value, date):
        return (
            f'<table:table-cell office:value-type="date" office:date-value="{value.isoformat()}">'
            f"<text:p>{value.isoformat()}</text:p></table:table-cell>"
        )
    return (
        '<table:table-cell office:value-type="string">'
        f"<text:p>{escape(str(value))}</text:p></table:table-cell>"
    )


def build_ods(sheets: Sheets) -> bytes:
    tables = "".join(
        f"<table:table table:name={quoteattr(name)}>"
        + (
            "".join(
                "<table:table-row>" + "".join(_ods_cell(v) for v in row) + "</table:table-row>"
                for row in rows
            )
            or "<table:table-row><table:table-cell/></table:table-row>"
        )
        + "</table:table>"
        for name, rows in sheets
    )
    return _zip(
        {
            "mimetype": "application/vnd.oasis.opendocument.spreadsheet",
            "META-INF/manifest.xml": (
                '<?xml version="1.0" encoding="UTF-8"?><manifest:manifest xmlns:manifest="urn:'
                'oasis:names:tc:opendocument:xmlns:manifest:1.0" manifest:version="1.2">'
                '<manifest:file-entry manifest:full-path="/" manifest:media-type="application/'
                'vnd.oasis.opendocument.spreadsheet"/><manifest:file-entry manifest:full-path='
                '"content.xml" manifest:media-type="text/xml"/></manifest:manifest>'
            ),
            "content.xml": (
                f'<?xml version="1.0" encoding="UTF-8"?><office:document-content {_ODS_NS} '
                f'office:version="1.2"><office:body><office:spreadsheet>{tables}'
                "</office:spreadsheet></office:body></office:document-content>"
            ),
        }
    )


# --- Archives ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Entry:
    name: str
    content: bytes = b""
    symlink: bool = False
    encrypted: bool = False
    stored: bool = False


def build_zip(entries: Sequence[Entry]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for entry in entries:
            info = zipfile.ZipInfo(entry.name, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_STORED if entry.stored else zipfile.ZIP_DEFLATED
            if entry.symlink:
                info.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(info, entry.content)
    data = buffer.getvalue()
    for entry in entries:
        if entry.encrypted:
            data = _set_flag(data, entry.name, 0x1)
    return data


def _central(data: bytes, name: str) -> int:
    """The offset of the central directory record of ``name``."""
    at = data.find(b"PK\x01\x02")
    while at != -1:
        length = struct.unpack_from("<H", data, at + 28)[0]
        if data[at + 46 : at + 46 + length] == name.encode():
            return at
        at = data.find(b"PK\x01\x02", at + 4)
    raise KeyError(name)


def _set_flag(data: bytes, name: str, flag: int) -> bytes:
    patched = bytearray(data)
    at = _central(data, name)
    flags = struct.unpack_from("<H", patched, at + 8)[0]
    struct.pack_into("<H", patched, at + 8, flags | flag)
    local = struct.unpack_from("<I", patched, at + 42)[0]
    struct.pack_into("<H", patched, local + 6, flags | flag)
    return bytes(patched)


def build_lie(data: bytes, name: str, size: int) -> bytes:
    """The archive with ``name``'s uncompressed size declared as ``size``, in both headers."""
    patched = bytearray(data)
    at = _central(data, name)
    struct.pack_into("<I", patched, at + 24, size)
    local = struct.unpack_from("<I", patched, at + 42)[0]
    struct.pack_into("<I", patched, local + 22, size)
    return bytes(patched)

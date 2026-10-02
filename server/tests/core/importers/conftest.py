"""The importer tests' builders and a test-only pack.

- ``make_xlsx`` writes a minimal SpreadsheetML workbook with inline strings, and ``make_ods`` a
  minimal OpenDocument one, so that edge cases and bombs need no binary fixture; ``error_cell``
  is an error cell in one.
- ``make_zip`` writes a zip archive of ``entry`` values, which can be symbolic links or
  encrypted; ``declare_size`` makes one lie about its size.
- ``roots`` gives an import directory and a directory outside it; ``importing`` builds a release
  of a path from them in a fresh store and publishes it with the store's own step, and
  ``lifecycle`` imports and re-imports through the operator's functions, which publish (§12.3).
- ``library_variant`` copies the lending library of ``fixtures/library`` (or its next export,
  ``fixtures/library_next``) into the import directory, changed as a test needs.
- ``birds`` is a pack of bird surveys (SPEC §10.1: extension points are tested with a
  non-biomedical pack): a survey file gives sites, checklists and the counts of species on each
  checklist, with a grouped coverage (a checklist follows a protocol, which lists the species it
  counts, or counts them all). Its dataset extension names the survey's protocol, from a fixed
  list; its validator refuses a release without one; its proposer proposes a definition for each
  table that has none.

Test modules can't import one another (``--import-mode=importlib``), so the helpers are given as
fixtures, as in the engine and store tests.
"""

import io
import itertools
import json
import shutil
import stat
import struct
import zipfile
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape, quoteattr

import pytest
from pydantic import JsonValue, TypeAdapter

import aibi
from aibi.core.importers.confine import Confinement
from aibi.core.importers.run import (
    Imported,
    Published,
    build_import,
    import_dataset,
    reimport_dataset,
)
from aibi.core.schema.descriptors import Descriptor, TableDescriptor
from aibi.core.schema.limits import ImportLimits
from aibi.core.schema.output import Segment, text
from aibi.core.schema.pack_api import (
    ConfinedPath,
    ImportOptions,
    ImportResult,
    Pack,
    PackManifest,
    PackRegistry,
    Proposal,
    ReleaseView,
)
from aibi.core.schema.refusals import Refusal
from aibi.core.store.build import Layout
from aibi.core.store.sources import SourceValue, TypedSource
from aibi.core.store.store import Store

AT = "2026-01-01T00:00:00Z"
FIXTURES = Path(__file__).resolve().parents[4] / "fixtures"

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


@pytest.fixture(name="make_xlsx")
def make_xlsx_fixture() -> Callable[[Sheets], bytes]:
    return build_xlsx


@pytest.fixture(name="make_ods")
def make_ods_fixture() -> Callable[[Sheets], bytes]:
    return build_ods


@pytest.fixture(name="make_zip")
def make_zip_fixture() -> Callable[[Sequence[Entry]], bytes]:
    return build_zip


@pytest.fixture(name="declare_size")
def declare_size_fixture() -> Callable[[bytes, str, int], bytes]:
    return build_lie


@pytest.fixture(name="entry")
def entry_fixture() -> type[Entry]:
    return Entry


@pytest.fixture(name="error_cell")
def error_cell_fixture() -> type[Error]:
    return Error


# --- Importing ---------------------------------------------------------------------------------


@dataclass
class Roots:
    inside: Path
    outside: Path
    confinement: Confinement
    stores: Path

    def write(self, name: str, content: bytes) -> Path:
        path = self.inside / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def options(self, dataset: str = "d", **given: Any) -> ImportOptions:
        return ImportOptions(
            dataset=dataset,
            reader=given.pop("reader", self.confinement),
            limits=given.pop("limits", ImportLimits()),
            at=AT,
            **given,
        )


@pytest.fixture
def roots(tmp_path: Path) -> Roots:
    inside, outside, stores = tmp_path / "imports", tmp_path / "elsewhere", tmp_path / "stores"
    for directory in (inside, outside, stores):
        directory.mkdir()
    return Roots(inside, outside, Confinement.of(inside), stores)


def _clock() -> Callable[[], datetime]:
    ticks = itertools.count()
    start = datetime(2026, 1, 1, tzinfo=UTC)
    return lambda: start + timedelta(seconds=next(ticks))


@pytest.fixture
def store(roots: Roots) -> Iterator[Store]:
    opened = Store(roots.stores / "data", clock=_clock())
    yield opened
    opened.close()


Importing = Callable[..., Imported]


@pytest.fixture
def importing(roots: Roots, store: Store) -> Importing:
    """Import a path of the import directory into the store, published as ``@1``."""

    def run(
        path: Path,
        *,
        dataset: str = "d",
        registry: PackRegistry | None = None,
        pack: str | None = None,
        publish: bool = True,
        **options: Any,
    ) -> Imported:
        with store.pin() as pin:
            imported = build_import(
                store,
                pin,
                roots.confinement.confine(path),
                roots.options(dataset, **options),
                registry=registry,
                pack=pack,
            )
            if publish:
                store.publish(dataset, imported.built.manifest.hash, "operator:ada")
        return imported

    return run


@dataclass
class Lifecycle:
    roots: Roots
    store: Store

    def run(
        self,
        operation: Callable[..., Published],
        path: Path,
        dataset: str,
        by: str,
        registry: PackRegistry | None,
        pack: str | None,
        options: dict[str, Any],
    ) -> Published:
        return operation(
            self.store,
            self.roots.confinement.confine(path),
            self.roots.options(dataset, **options),
            by,
            registry=registry,
            pack=pack,
        )

    def import_(
        self,
        path: Path,
        *,
        dataset: str = "d",
        by: str = "operator:ada",
        registry: PackRegistry | None = None,
        pack: str | None = None,
        **options: Any,
    ) -> Published:
        return self.run(import_dataset, path, dataset, by, registry, pack, options)

    def reimport(
        self,
        path: Path,
        *,
        dataset: str = "d",
        by: str = "operator:ada",
        registry: PackRegistry | None = None,
        pack: str | None = None,
        **options: Any,
    ) -> Published:
        return self.run(reimport_dataset, path, dataset, by, registry, pack, options)


@pytest.fixture
def lifecycle(roots: Roots, store: Store) -> Lifecycle:
    return Lifecycle(roots, store)


# --- The lending library -------------------------------------------------------------------------


def copy_library(
    roots: Roots,
    name: str,
    *,
    source: str = "library",
    without: Sequence[str] = (),
    files: Mapping[str, bytes] | None = None,
) -> Path:
    """The library of ``fixtures/<source>`` in the import directory as ``name``, without the
    files ``without`` and with ``files`` written over its own; ``formats/`` left out."""
    target = roots.inside / name
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(FIXTURES / source, target, ignore=shutil.ignore_patterns("formats", "*.md"))
    for gone in without:
        (target / gone).unlink()
    for file, content in (files or {}).items():
        (target / file).write_bytes(content)
    return target


@pytest.fixture
def library_variant(roots: Roots) -> Callable[..., Path]:
    def make(name: str = "library", **given: Any) -> Path:
        return copy_library(roots, name, **given)

    return make


# --- The birds pack ----------------------------------------------------------------------------

_DESCRIPTORS: TypeAdapter[Descriptor] = TypeAdapter(Descriptor)
BY = "importer:birds@1.0.0"


def _declared(
    kind: str,
    id: str,
    fields: Mapping[str, JsonValue],
    extensions: Mapping[str, Any] | None = None,
    *,
    proposed: Sequence[str] = (),
) -> Descriptor:
    """A descriptor whose fields the importer declares as ``imported``, but for those named in
    ``proposed``."""
    entry: dict[str, JsonValue] = {"status": "imported", "by": BY, "at": AT}
    guess: dict[str, JsonValue] = {"status": "proposed", "by": BY, "at": AT}
    curation: dict[str, JsonValue] = {"/label": entry}
    curation.update({f"/fields/{name}": guess if name in proposed else entry for name in fields})
    for pack, members in (extensions or {}).items():
        curation.update({f"/extensions/{pack}/{member}": entry for member in members})
    written: dict[str, Any] = {
        "kind": kind,
        "id": id,
        "version": 1,
        "label": id,
        "fields": dict(fields),
        "curation": curation,
    }
    if extensions:
        written["extensions"] = dict(extensions)
    return _DESCRIPTORS.validate_python(written)


_TABLES: dict[str, tuple[list[str], list[str] | None, str, dict[str, str]]] = {
    "sites": (["site_id", "habitat"], ["site_id"], "entity", {"habitat": "category"}),
    "checklists": (
        ["checklist_id", "site_id", "protocol", "observed"],
        ["checklist_id"],
        "event",
        {"protocol": "category", "observed": "date"},
    ),
    "counts": (
        ["checklist_id", "species", "count"],
        ["checklist_id", "species"],
        "measurement",
        {"species": "category", "count": "integer"},
    ),
    "assignments": (["checklist_id", "protocol"], None, "coverage", {"protocol": "category"}),
    "protocol_species": (
        ["protocol", "species", "all_species"],
        None,
        "coverage",
        {"protocol": "category", "species": "category", "all_species": "boolean"},
    ),
}


def _survey_rows(survey: Mapping[str, Any]) -> dict[str, list[tuple[SourceValue, ...]]]:
    return {
        "sites": [(s["site_id"], s["habitat"]) for s in survey["sites"]],
        "checklists": [
            (c["checklist_id"], c["site_id"], c["protocol"], date.fromisoformat(c["observed"]))
            for c in survey["checklists"]
        ],
        "counts": [(n["checklist_id"], n["species"], n["count"]) for n in survey["counts"]],
        "assignments": [(c["checklist_id"], c["protocol"]) for c in survey["checklists"]],
        "protocol_species": [
            (p["protocol"], p["species"], p.get("all_species", False)) for p in survey["protocols"]
        ],
    }


@dataclass
class BirdImporter:
    """Reads a survey file, only through ``options.reader`` (SPEC §14). Its keys are declared,
    or proposed when the survey says ``keys_proposed``."""

    reads: list[str] = field(default_factory=list[str])

    def import_source(self, source: ConfinedPath, options: ImportOptions) -> ImportResult:
        survey = json.loads(options.reader.read(source, options.limits.import_bytes))
        self.reads.append(str(source))
        extensions = {"birds": {"protocol": survey["protocol"]}} if "protocol" in survey else None
        descriptors: list[Descriptor] = [
            _declared(
                "dataset",
                "dataset",
                {"name": "Bird survey", "packs": survey.get("packs", ["birds"])},
                extensions,
            )
        ]
        sources: dict[str, TypedSource] = {}
        layouts: dict[str, Layout] = {}
        rows = _survey_rows(survey)
        for table, (columns, key, role, datatypes) in _TABLES.items():
            fields: dict[str, JsonValue] = {"role": role}
            if key is not None:
                fields["primary_key"] = list(key)
            proposed = ["primary_key"] if survey.get("keys_proposed") else []
            descriptors.append(_declared("table", table, fields, proposed=proposed))
            for column in columns:
                datatype = datatypes.get(column, "string")
                descriptors.append(_declared("column", f"{table}.{column}", {"datatype": datatype}))
            sources[table] = TypedSource(tuple(columns), tuple(rows[table]))
            layouts[table] = Layout(table, tuple((c, c) for c in columns))
        for child, column, parent in (
            ("checklists", "site_id", "sites"),
            ("counts", "checklist_id", "checklists"),
        ):
            descriptors.append(
                _declared(
                    "relationship",
                    f"rel:{child}.{column}",
                    {
                        "child_table": child,
                        "child_columns": [column],
                        "parent_table": parent,
                        "parent_columns": [column],
                        "cardinality": "many-to-one",
                    },
                )
            )
        descriptors.append(
            _declared(
                "coverage",
                "cov:counts.checklist_id",
                {
                    "relationship": "rel:counts.checklist_id",
                    "parents": {
                        "assignment": {
                            "table": "assignments",
                            "parent_columns": {"checklist_id": "checklist_id"},
                            "group_column": "protocol",
                        },
                        "groups": {
                            "table": "protocol_species",
                            "group_column": "protocol",
                            "scope_columns": {"species": "species"},
                            "covers_all_column": "all_species",
                        },
                    },
                },
            )
        )
        return ImportResult(sources, layouts, descriptors)


class BirdValidator:
    def validate_source(self, source: ConfinedPath, result: ImportResult) -> Sequence[Refusal]:
        counts = result.sources["counts"]
        assert isinstance(counts, TypedSource)
        negative = sum(1 for row in counts.rows if isinstance(row[2], int) and row[2] < 0)
        if not negative:
            return []
        message: list[Segment] = [text(f"{negative} counts are negative")]
        return [Refusal(code="birds.NEGATIVE_COUNT", path=None, message=message)]

    def validate_descriptors(self, release: ReleaseView) -> Sequence[Refusal]:
        dataset = release.descriptors["dataset"]
        if "birds" in dataset.extensions:
            return []
        message: list[Segment] = [text(f"Release @{release.label} has no survey protocol")]
        return [Refusal(code="birds.NO_PROTOCOL", path="/dataset/extensions", message=message)]


def propose_definitions(release: ReleaseView) -> Sequence[Proposal]:
    """A definition for each table that has none."""
    return [
        Proposal(
            descriptor.id,
            "/definition",
            f"The survey's {descriptor.id.replace('_', ' ')}",
            evidence="The birds pack defines every table it knows",
        )
        for descriptor in release.descriptors.values()
        if isinstance(descriptor, TableDescriptor) and descriptor.definition is None
    ]


@dataclass
class Birds:
    importer: BirdImporter
    registry: PackRegistry
    survey: Callable[..., dict[str, Any]]
    pack: Pack


def survey(**changes: Any) -> dict[str, Any]:
    written: dict[str, Any] = {
        "protocol": "stationary",
        "sites": [{"site_id": "s1", "habitat": "wood"}, {"site_id": "s2", "habitat": "marsh"}],
        "checklists": [
            {"checklist_id": "c1", "site_id": "s1", "protocol": "all", "observed": "2026-05-01"},
            {"checklist_id": "c2", "site_id": "s2", "protocol": "waders", "observed": "2026-05-02"},
        ],
        "counts": [
            {"checklist_id": "c1", "species": "wren", "count": 3},
            {"checklist_id": "c2", "species": "curlew", "count": 2},
            {"checklist_id": "c2", "species": "wren", "count": 1},
        ],
        "protocols": [
            {"protocol": "all", "species": "wren", "all_species": True},
            {"protocol": "waders", "species": "curlew"},
            {"protocol": "waders", "species": "redshank"},
        ],
    }
    written.update(changes)
    return written


@pytest.fixture
def birds() -> Birds:
    importer = BirdImporter()
    pack = Pack(
        manifest=PackManifest(
            id="birds", version="1.0.0", results_version=1, requires_core=">=0.0.1"
        ),
        extension_schemas={
            "dataset": {
                "type": "object",
                "properties": {"protocol": {"enum": ["stationary", "travelling"]}},
                "additionalProperties": False,
            }
        },
        importer=importer,
        validator=BirdValidator(),
        proposer=propose_definitions,
    )
    return Birds(importer, PackRegistry([pack], core_version=aibi.__version__), survey, pack)

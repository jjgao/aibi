"""Building releases: raw snapshots and descriptors to typed tables and a manifest (SPEC §12.2).

``import_release`` builds every table from new raw snapshots, laid out by the importer: for each
table, its source and the id and source name of each source column. ``change_release`` builds a
release from a base one and new descriptors (a draft change): it reuses the raw snapshots and
layouts, rebuilds a table only when a field that affects its parsing changed (its parse
settings, or a column's ``datatype``, ``missing_codes``, ``list_syntax``, ``derived`` or
``units``, or the set of its derived columns), and reuses every other table blob. A change that
would give a table other source columns (a header read differently, a column descriptor
removed or added without a derivation) is a re-import, and is refused.

Typed values and states are a deterministic function of the raw snapshots and the descriptors,
so a table rebuilt without need gets the blob it had. Descriptors are checked as a release
(``check_release``) first. A raw snapshot that cannot be stored or read (a typed string with a
lone surrogate, a header name that is not Unicode text or is too long) is refused. Every
refusal's path points into the descriptors given.

An import runs the validation gate (``gate``, §13.2) on the tables it built, before anything
reads them as a release: a structural error refuses the import, and a proposal that fails is
dropped from the descriptors written, with its evidence. The import report (D231) holds the
importer's notes, what the gate dropped, its gaps, and the cells of each column that did not
parse, as counts and row references without values. A change carries its base's report, and
runs the gate too, in change mode, where every failure refuses (§12.3, D246): on the tables it
rebuilt, and on the columns the gate reads of each table it reused, read from its blob. A table a
pack importer reshaped (``source.kind`` ``pack``) is not rebuilt in a change until packs can
rebuild (M4, #19): a change that would rebuild it is refused (``NOT_SUPPORTED``).

Every build writes the release's catalogue statistics (``statistics``, D270): counted on each
table it built, carried from the base for a table a change reused while what they are computed
from is unchanged, and otherwise counted again from the table's blob.

Each blob is pinned through ``holder`` before it is written, so that a sweep leaves it alone
until the operation commits or fails (§12.2, Deletion).
"""

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import cast

from pydantic import JsonValue, TypeAdapter

from aibi.core.schema.descriptors import (
    ColumnDescriptor,
    ColumnFields,
    Descriptor,
    ParseSettings,
    TableDescriptor,
)
from aibi.core.schema.jsonio import canonical, is_text, pointer
from aibi.core.schema.limits import MAX_STRING, STRING_CHARACTERS
from aibi.core.schema.output import Segment, data, text
from aibi.core.schema.pack_api import ImportNote
from aibi.core.schema.refusals import Limit, Refusal, RefusalCode, finish_refusals
from aibi.core.schema.release import check_release
from aibi.core.store import gate, statistics, tables
from aibi.core.store.blobs import BlobStore, Holder
from aibi.core.store.gate import GateResult, Mode
from aibi.core.store.manifest import Manifest, SourceColumn, SourceEntry, TableEntry
from aibi.core.store.sources import RawSource, SourceError, decode, encode, parse
from aibi.core.store.tables import ColumnReport, TableError, TypedTable

REPORT_FORMAT = "aibi.import-report/1"
_PARSE_FIELDS = ("datatype", "missing_codes", "list_syntax", "derived", "units")
_DESCRIPTORS: TypeAdapter[list[Descriptor]] = TypeAdapter(list[Descriptor])


@dataclass(frozen=True)
class Layout:
    """A table's source and its source columns, in source order: (column id, source name)."""

    source: str
    columns: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class Built:
    manifest: Manifest
    reports: Mapping[str, Mapping[str, ColumnReport]]
    """For each table built, by column: its states and the tokens that did not parse."""
    rebuilt: frozenset[str]
    """The tables built; the others reused their blobs."""
    descriptors: tuple[Descriptor, ...] = ()
    """The descriptors written: those given, less what the gate dropped."""
    gate: GateResult | None = None
    notes: tuple[ImportNote, ...] = ()
    """The import report's notes, in the report's order."""


class BuildRefused(Exception):  # noqa: N818 - the spec's word
    def __init__(self, refusals: Sequence[Refusal]) -> None:
        super().__init__("; ".join(f"{refusal.code} at {refusal.path}" for refusal in refusals))
        self.refusals = tuple(refusals)


def import_release(
    blobs: BlobStore,
    dataset: str,
    descriptors: Sequence[Descriptor],
    sources: Mapping[str, RawSource],
    layouts: Mapping[str, Layout],
    *,
    holder: Holder | None = None,
    tombstones: str | None = None,
    notes: Sequence[ImportNote] = (),
    revise: Callable[[tuple[Descriptor, ...]], Sequence[Descriptor]] | None = None,
) -> Built:
    """A release built from new raw snapshots, through the validation gate, with its import
    report, which holds ``notes``. ``revise`` is given the descriptors the gate left and returns
    those written, changed in nothing the gate or the build reads (a re-import's versions,
    D243). Raises ``BuildRefused``."""
    index = _Index(descriptors)
    _refuse_if(check_release(descriptors))
    refusals: list[Refusal] = []
    for table in index.tables:
        if table not in layouts:
            message = "No source lays out the table"
            refusals.append(index.refusal(RefusalCode.COLUMNS_CHANGED, table, (), message))
    for table, layout in layouts.items():
        if table not in index.tables:
            message = f"No descriptor for the table {table}"
            refusals.append(_refusal(RefusalCode.UNKNOWN_DESCRIPTOR, None, message))
        elif len({column for column, _ in layout.columns}) != len(layout.columns):
            message = "The layout gives a column id to two source columns"
            refusals.append(index.refusal(RefusalCode.DUPLICATE_ENTRY, table, (), message))
        if layout.source not in sources:
            raise ValueError(f"table {table} is laid out on no source: {layout.source}")
    _refuse_if(refusals)
    entries: list[SourceEntry] = []
    for name, source in sorted(sources.items()):
        try:
            data = encode(source)
        except SourceError as error:
            readers = sorted(table for table, layout in layouts.items() if layout.source == name)
            path = [index.tables[readers[0]]] if readers and readers[0] in index.tables else None
            raise BuildRefused(
                [_refusal(RefusalCode.UNPARSEABLE_SOURCE, path, str(error))]
            ) from None
        entries.append(SourceEntry(name=name, kind=source.kind, hash=blobs.put(data, holder)))
    builder = _Builder(blobs, index, holder, gate.needed(descriptors))
    for table, layout in sorted(layouts.items()):
        builder.table(table, sources[layout.source], layout.source, layout.columns)
    result = gate.check(descriptors, builder.typed, mode="import")
    _refuse_if(result.refusals)
    kept = result.descriptors if revise is None else tuple(revise(result.descriptors))
    builder.index = _Index(kept)
    found = sorted_notes([*notes, *_gate_notes(result), *_unparsed_notes(builder.reports)])
    report = blobs.put(report_bytes(found), holder)
    return builder.finish(dataset, tuple(entries), tombstones, report, result, found)


def change_release(
    blobs: BlobStore,
    base: Manifest,
    base_descriptors: Sequence[Descriptor],
    descriptors: Sequence[Descriptor],
    *,
    holder: Holder | None = None,
    tombstones: str | None = None,
    keep_tombstones: bool = True,
    gate_mode: Mode | None = "change",
) -> Built:
    """A release made from ``base`` with new descriptors, through the gate in ``gate_mode``
    unless it is ``None``. Raises ``BuildRefused``.

    ``tombstones`` are the base's unless given (or dropped with ``keep_tombstones=False``).
    """
    index, before = _Index(descriptors), _Index(base_descriptors)
    _refuse_if(check_release(descriptors))
    refusals: list[Refusal] = []
    changed = RefusalCode.COLUMNS_CHANGED
    for table in index.tables:
        if base.table(table) is None:
            message = "The table has no source; adding a table is a re-import"
            refusals.append(index.refusal(changed, table, (), message))
    for entry in base.tables:
        if entry.id not in index.tables:
            message = f"Removing the table {entry.id} is a re-import"
            refusals.append(_refusal(changed, None, message))
    _refuse_if(refusals)
    needed = gate.needed(descriptors) if gate_mode is not None else None
    builder = _Builder(blobs, index, holder, needed)
    reused: list[TableEntry] = []
    for entry in base.tables:
        if index.fingerprint(entry.id) == before.fingerprint(entry.id):
            builder.reuse(entry)
            reused.append(entry)
            continue
        source_kind = index.table_fields(entry.id).fields.source
        if source_kind is not None and source_kind.kind == "pack":
            message = (
                "A pack importer reshaped this table, and rebuilding it comes with packs' "
                "rebuild (M4); a re-import changes its parsing until then"
            )
            raise BuildRefused([index.refusal(RefusalCode.NOT_SUPPORTED, entry.id, (), message)])
        source = base.source(entry.source)
        assert source is not None
        raw = decode(source.kind, blobs.read(source.hash))
        columns = tuple((column.id, column.name) for column in entry.columns)
        builder.table(entry.id, raw, entry.source, columns)
    result: GateResult | None = None
    if gate_mode is not None and needed is not None:
        typed: dict[str, gate.Cells] = dict(builder.typed)
        for entry in reused:
            if needed.get(entry.id):
                data = blobs.read(entry.hash)
                typed[entry.id] = tables.decode_cells(entry.id, data, needed[entry.id])
        result = gate.check(descriptors, typed, mode=gate_mode)
        _refuse_if(result.refusals)
        builder.index = _Index(result.descriptors)
    if base.statistics is not None:
        carried = statistics.decode(blobs.read(base.statistics))
        marked = statistics.identifiers(base_descriptors)
        for entry in reused:
            if entry.id in carried:
                columns = before.column_fields(entry.id)
                read = statistics.inputs(columns, marked.get(entry.id, ()), entry.hash)
                builder.carried[entry.id] = (read, carried[entry.id])
    kept = base.tombstones if keep_tombstones and tombstones is None else tombstones
    return builder.finish(base.dataset, base.sources, kept, base.report, result)


def descriptors_bytes(descriptors: Sequence[Descriptor]) -> bytes:
    """The descriptors blob: every descriptor, sorted by id, in RFC 8785 form."""
    ordered = sorted(descriptors, key=lambda descriptor: descriptor.id)
    dumped: list[JsonValue] = [descriptor.model_dump(mode="json") for descriptor in ordered]
    return canonical(dumped)


def read_descriptors(data: bytes) -> tuple[Descriptor, ...]:
    return tuple(_DESCRIPTORS.validate_json(data))


def _note_json(note: ImportNote) -> JsonValue:
    written: dict[str, JsonValue] = {
        "kind": note.kind,
        "message": [segment.model_dump(mode="json") for segment in note.message],
    }
    if note.subject is not None:
        written["subject"] = note.subject
    if note.count is not None:
        written["count"] = note.count
    if note.rows:
        written["rows"] = list(note.rows)
    return written


def sorted_notes(notes: Sequence[ImportNote]) -> list[ImportNote]:
    """Notes in the report's order: by kind, subject and content."""
    keys = [(note.kind, note.subject or "", canonical(_note_json(note))) for note in notes]
    return [notes[i] for i in sorted(range(len(notes)), key=lambda i: keys[i])]


def report_bytes(notes: Sequence[ImportNote]) -> bytes:
    """The import report blob (D231): ``{"format": "aibi.import-report/1", "notes": [...]}`` in
    RFC 8785 form, the notes in ``sorted_notes`` order, without timestamps, so that the same
    inputs give the same report."""
    ordered: list[JsonValue] = [_note_json(note) for note in sorted_notes(notes)]
    return canonical({"format": REPORT_FORMAT, "notes": ordered})


def read_report(data: bytes) -> list[dict[str, JsonValue]]:
    """The notes of an import report blob, as JSON values."""
    parsed: object = json.loads(data)
    if not isinstance(parsed, dict):
        raise ValueError(f"an import report is {REPORT_FORMAT}")
    report = cast(dict[str, JsonValue], parsed)
    if report.get("format") != REPORT_FORMAT:
        raise ValueError(f"an import report is {REPORT_FORMAT}")
    return cast(list[dict[str, JsonValue]], report["notes"])


def _gate_notes(result: GateResult) -> list[ImportNote]:
    found: list[ImportNote] = []
    for dropped in result.dropped:
        what = "field " + dropped.pointer if dropped.pointer else "descriptor"
        message: list[Segment] = [text(f"The proposed {what} was dropped ({dropped.code}). ")]
        message.append(text(dropped.message))
        if dropped.evidence:
            message.extend((text(". Its evidence: "), data(dropped.evidence)))
        count = dropped.count or None
        found.append(ImportNote("dropped", dropped.descriptor, message, count, dropped.rows))
    for gap in result.gaps:
        said = {
            "outside_coverage": "Child rows whose parent the coverage does not list; they are "
            "kept, as evidence",
            "invalid_endpoint_rows": "Rows with a status outside the event coding, a negative "
            "time or an entry at or after the time; analyses exclude them as INVALID_VALUE",
        }[gap.kind]
        found.append(ImportNote("gap", gap.subject, [text(said)], gap.count, gap.rows))
    return found


def _unparsed_notes(reports: Mapping[str, Mapping[str, ColumnReport]]) -> list[ImportNote]:
    found: list[ImportNote] = []
    for table, columns in sorted(reports.items()):
        for column, report in sorted(columns.items()):
            count = report.unparsed_cells
            if not count:
                continue
            message = "Cells whose value does not parse as the column's datatype, which are UNKNOWN"
            rows = tuple(row + 1 for row in report.unparsed_rows)
            found.append(ImportNote("unparsed", f"{table}.{column}", [text(message)], count, rows))
    return found


class _Index:
    """The descriptors of a release, by table: positions for refusal paths, and fields."""

    def __init__(self, descriptors: Sequence[Descriptor]) -> None:
        self.descriptors = descriptors
        self.tables: dict[str, int] = {}
        self.columns: dict[str, dict[str, int]] = {}
        for position, descriptor in enumerate(descriptors):
            if isinstance(descriptor, TableDescriptor):
                self.tables.setdefault(descriptor.id, position)
            elif isinstance(descriptor, ColumnDescriptor):
                table, column = descriptor.id.split(".", 1)
                self.columns.setdefault(table, {}).setdefault(column, position)

    def table_fields(self, table: str) -> TableDescriptor:
        found = self.descriptors[self.tables[table]]
        assert isinstance(found, TableDescriptor)
        return found

    def column_fields(self, table: str) -> dict[str, ColumnFields]:
        found: dict[str, ColumnFields] = {}
        for column, position in self.columns.get(table, {}).items():
            descriptor = self.descriptors[position]
            assert isinstance(descriptor, ColumnDescriptor)
            found[column] = descriptor.fields
        return found

    def parse_settings(self, table: str) -> ParseSettings | None:
        source = self.table_fields(table).fields.source
        return None if source is None else source.parse

    def fingerprint(self, table: str) -> bytes:
        """What of the table's descriptors affects how it is built (D220)."""
        if table not in self.tables:
            return b""
        settings = self.parse_settings(table)
        columns: dict[str, JsonValue] = {}
        for column, fields in sorted(self.column_fields(table).items()):
            dumped = fields.model_dump(mode="json")
            columns[column] = {name: dumped[name] for name in _PARSE_FIELDS if name in dumped}
        parse_dump: JsonValue = None if settings is None else settings.model_dump(mode="json")
        return canonical({"columns": columns, "parse": parse_dump})

    def refusal(
        self,
        code: RefusalCode,
        table: str,
        where: tuple[str | int, ...],
        message: str,
        column: str | None = None,
    ) -> Refusal:
        """A refusal at ``where`` in the column's ``fields``, or at the table's descriptor."""
        path: list[str | int] = []
        if column is not None and column in self.columns.get(table, {}):
            path = [self.columns[table][column]]
            if where:
                path.extend(("fields", *where))
        elif table in self.tables:
            path = [self.tables[table]]
        return _refusal(code, path, message)


def _refusal(code: RefusalCode, path: Sequence[str | int] | None, message: str) -> Refusal:
    return Refusal(code=code, path=None if path is None else pointer(path), message=[text(message)])


def _header_problems(names: Sequence[str], path: Sequence[str | int]) -> list[Refusal]:
    """A header name is kept in the manifest, so it is a string of a descriptor (§14): Unicode
    text of at most ``MAX_STRING`` characters."""
    found: list[Refusal] = []
    for position, name in enumerate(names, start=1):
        if len(name) > MAX_STRING:
            message = f"Header name {position} is longer than {MAX_STRING} characters"
            found.append(
                Refusal(
                    code=RefusalCode.LIMIT_EXCEEDED,
                    path=pointer(path),
                    message=[text(message)],
                    limit=Limit(name=STRING_CHARACTERS, max=MAX_STRING),
                )
            )
        elif not is_text(name):
            message = f"Header name {position} is not Unicode text (a noncharacter or surrogate)"
            found.append(_refusal(RefusalCode.UNPARSEABLE_SOURCE, path, message))
    return found


def _refuse_if(refusals: Sequence[Refusal]) -> None:
    if refusals:
        raise BuildRefused(finish_refusals(list(refusals)))


class _Builder:
    def __init__(
        self,
        blobs: BlobStore,
        index: _Index,
        holder: Holder | None,
        keep: Mapping[str, set[str]] | None = None,
    ) -> None:
        self.blobs = blobs
        self.index = index
        self.holder = holder
        self.keep = keep
        """The columns of each table the gate reads, kept in memory once it is built."""
        self.entries: list[TableEntry] = []
        self.reports: dict[str, Mapping[str, ColumnReport]] = {}
        self.rebuilt: set[str] = set()
        self.typed: dict[str, TypedTable] = {}
        self.counted: dict[str, statistics.TableStatistics] = {}
        """The statistics of each table built, before identifier columns lose their
        distributions, which the descriptors written decide (D270)."""
        self.carried: dict[str, tuple[bytes, statistics.TableStatistics]] = {}
        """A reused table's statistics in the base, by what they were computed from."""

    def reuse(self, entry: TableEntry) -> None:
        self.entries.append(entry)

    def table(
        self, table: str, raw: RawSource, source: str, columns: tuple[tuple[str, str], ...]
    ) -> None:
        settings = self.index.parse_settings(table)
        at = self.index.tables[table]
        settings_path = (at, "fields", "source", "parse") if settings is not None else (at,)
        try:
            parsed = parse(raw, settings)
        except SourceError as error:
            code = RefusalCode.UNPARSEABLE_SOURCE
            raise BuildRefused([_refusal(code, settings_path, str(error))]) from None
        _refuse_if(_header_problems(parsed.names, settings_path))
        names = tuple(name for _, name in columns)
        if parsed.names != names:
            message = "The parse settings give other source columns; changing them is a re-import"
            raise BuildRefused([_refusal(RefusalCode.COLUMNS_CHANGED, settings_path, message)])
        fields = self.index.column_fields(table)
        try:
            typed = tables.build_table(table, parsed, [column for column, _ in columns], fields)
        except TableError as error:
            raise BuildRefused(
                finish_refusals(
                    [
                        self.index.refusal(code, table, where, message, column)
                        for code, column, where, message in error.problems
                    ]
                )
            ) from None
        digest = self.blobs.put(tables.encode(typed, fields), self.holder)
        laid_out = tuple(SourceColumn(id=column, name=name) for column, name in columns)
        self.entries.append(TableEntry(id=table, hash=digest, source=source, columns=laid_out))
        self.reports[table] = typed.report
        self.rebuilt.add(table)
        self.counted[table] = statistics.table_statistics(typed.rows, typed.cells, fields, ())
        if self.keep is not None:
            kept = self.keep.get(table, set())
            cells = {name: found for name, found in typed.cells.items() if name in kept}
            self.typed[table] = TypedTable(table, typed.columns, cells, typed.rows, {})

    def _statistics(self) -> str:
        """The statistics blob (D270): each table's, identifier columns without distributions;
        a reused table's carried from the base when what they are computed from is unchanged,
        and otherwise counted again from its blob."""
        marked = statistics.identifiers(self.index.descriptors)
        found: dict[str, statistics.TableStatistics] = {}
        for entry in self.entries:
            columns = self.index.column_fields(entry.id)
            identifying = marked.get(entry.id, set())
            built = self.counted.get(entry.id)
            if built is not None:
                found[entry.id] = statistics.without_distributions(built, identifying)
                continue
            carried = self.carried.get(entry.id)
            if carried is not None and carried[0] == statistics.inputs(
                columns, identifying, entry.hash
            ):
                found[entry.id] = carried[1]
                continue
            typed = tables.decode_cells(entry.id, self.blobs.read(entry.hash), list(columns))
            found[entry.id] = statistics.table_statistics(
                typed.rows, typed.cells, columns, identifying
            )
        return self.blobs.put(statistics.encode(found), self.holder)

    def finish(
        self,
        dataset: str,
        sources: tuple[SourceEntry, ...],
        tombstones: str | None,
        report: str | None = None,
        result: GateResult | None = None,
        notes: Sequence[ImportNote] = (),
    ) -> Built:
        described = self.blobs.put(descriptors_bytes(self.index.descriptors), self.holder)
        manifest = Manifest(
            dataset=dataset,
            descriptors=described,
            sources=sources,
            tables=tuple(sorted(self.entries, key=lambda entry: entry.id)),
            statistics=self._statistics(),
            tombstones=tombstones,
            report=report,
        )
        self.blobs.put(manifest.canonical_bytes(), self.holder)
        return Built(
            manifest,
            self.reports,
            frozenset(self.rebuilt),
            tuple(self.index.descriptors),
            result,
            tuple(notes),
        )


__all__ = [
    "REPORT_FORMAT",
    "BuildRefused",
    "Built",
    "Layout",
    "change_release",
    "descriptors_bytes",
    "import_release",
    "read_descriptors",
    "read_report",
    "report_bytes",
    "sorted_notes",
]

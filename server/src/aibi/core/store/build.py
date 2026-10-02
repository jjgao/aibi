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

Each blob is pinned through ``holder`` before it is written, so that a sweep leaves it alone
until the operation commits or fails (§12.2, Deletion).
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

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
from aibi.core.schema.output import text
from aibi.core.schema.refusals import Limit, Refusal, RefusalCode, finish_refusals
from aibi.core.schema.release import check_release
from aibi.core.store import tables
from aibi.core.store.blobs import BlobStore, Holder
from aibi.core.store.manifest import Manifest, SourceColumn, SourceEntry, TableEntry
from aibi.core.store.sources import RawSource, SourceError, decode, encode, parse
from aibi.core.store.tables import ColumnReport, TableError

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
    statistics: str | None = None,
    tombstones: str | None = None,
) -> Built:
    """A release built from new raw snapshots. Raises ``BuildRefused``."""
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
    builder = _Builder(blobs, index, holder)
    for table, layout in sorted(layouts.items()):
        builder.table(table, sources[layout.source], layout.source, layout.columns)
    return builder.finish(dataset, tuple(entries), statistics, tombstones)


def change_release(
    blobs: BlobStore,
    base: Manifest,
    base_descriptors: Sequence[Descriptor],
    descriptors: Sequence[Descriptor],
    *,
    holder: Holder | None = None,
    statistics: str | None = None,
    tombstones: str | None = None,
    keep_tombstones: bool = True,
) -> Built:
    """A release made from ``base`` with new descriptors. Raises ``BuildRefused``.

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
    builder = _Builder(blobs, index, holder)
    for entry in base.tables:
        if index.fingerprint(entry.id) == before.fingerprint(entry.id):
            builder.reuse(entry)
            continue
        source = base.source(entry.source)
        assert source is not None
        raw = decode(source.kind, blobs.read(source.hash))
        columns = tuple((column.id, column.name) for column in entry.columns)
        builder.table(entry.id, raw, entry.source, columns)
    kept = base.tombstones if keep_tombstones and tombstones is None else tombstones
    return builder.finish(base.dataset, base.sources, statistics, kept)


def descriptors_bytes(descriptors: Sequence[Descriptor]) -> bytes:
    """The descriptors blob: every descriptor, sorted by id, in RFC 8785 form."""
    ordered = sorted(descriptors, key=lambda descriptor: descriptor.id)
    dumped: list[JsonValue] = [descriptor.model_dump(mode="json") for descriptor in ordered]
    return canonical(dumped)


def read_descriptors(data: bytes) -> tuple[Descriptor, ...]:
    return tuple(_DESCRIPTORS.validate_json(data))


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
    def __init__(self, blobs: BlobStore, index: _Index, holder: Holder | None) -> None:
        self.blobs = blobs
        self.index = index
        self.holder = holder
        self.entries: list[TableEntry] = []
        self.reports: dict[str, Mapping[str, ColumnReport]] = {}
        self.rebuilt: set[str] = set()

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

    def finish(
        self,
        dataset: str,
        sources: tuple[SourceEntry, ...],
        statistics: str | None,
        tombstones: str | None,
    ) -> Built:
        described = self.blobs.put(descriptors_bytes(self.index.descriptors), self.holder)
        manifest = Manifest(
            dataset=dataset,
            descriptors=described,
            sources=sources,
            tables=tuple(sorted(self.entries, key=lambda entry: entry.id)),
            statistics=statistics,
            tombstones=tombstones,
        )
        self.blobs.put(manifest.canonical_bytes(), self.holder)
        return Built(manifest, self.reports, frozenset(self.rebuilt))


__all__ = [
    "BuildRefused",
    "Built",
    "Layout",
    "change_release",
    "descriptors_bytes",
    "import_release",
    "read_descriptors",
]

"""The core's importer of database snapshots (SPEC §13.1, §14, D305–D307): ``aibi.databases``.

A source is a named connection of the server's configuration (``[databases.<name>]``, D253): a
SQLite or DuckDB file inside an import directory, or a Postgres or MySQL database whose URL an
environment variable holds. A request names the connection, never a host, a path or a
credential (§14). ``resolve`` turns a connection into what one import reads (``Resolved``): a
file is confined as any import's is (D232), and must be a regular file that the server's user or
root owns and that neither its group nor others can write, in a directory that it or root owns
and that neither its group nor others can write, unless it has the sticky bit (D253, D310); a URL
is read from the environment when the import runs, so that a rotated credential needs no
restart, by the server's own grammar (``urls``, D310), and the driver is given the connection
string built from what it names, never the URL. A refusal about a file names its location in the
import directory, not its absolute path. Its provenance, the dataset's ``source.location`` and
the ``DatabaseSource`` a pack's validator sees, is ``<host>/<database>/<schema>`` for a server
(the schema being MySQL's database), the file's location for SQLite and ``<location>/<schema>``
for DuckDB: never a port, a user, a password or a query string. A server's database is recorded
as the server names the one it opened, which the snapshot checks is the URL's (D310). A refusal
names the connection and the variable, never the URL.

``DatabaseImporter.import_database`` reads the snapshot in the import's worker process
(``snapshot.read_snapshot``, under ``reader_memory``, ``reader_seconds`` and ``decoded_bytes``)
and describes it as the file importer describes its tables: ids normalised from the tables' and
columns' names, the *k*-th occurrence of a name keeping its id on a re-import (D238), each table
a typed raw snapshot named by its id, and the proposals of ``infer`` (D227–D229). What the
database declares is ``imported`` (D307): a column type DuckDB reads as Arrow (as a Parquet file's
is, D227; SQLite declares none a cell keeps), a table's and a column's comment as its
``definition``, the primary key, and each foreign key to a table of the snapshot, its parent
columns being the parent's primary key where SQLite leaves them out. Names are matched as the
database wrote them, or else ignoring the case of ASCII letters, as SQLite does, when exactly one
matches. A foreign key whose parent is not read (in another schema or database, a view, two
tables that match it ignoring case), whose columns do not match or repeat one, whose parent
columns are not the parent's declared primary key (as a relationship's must be, §5.6), or whose
child columns another declared foreign key of the table already has, in any order, is not kept,
and a note says why. The relations that are not base tables are skipped and noted, at most
``import_tables`` of them, as the worker keeps them, each in a note of its own and the rest
counted in one, their names escaped where they are not Unicode text (``errors.escaped``, D309).
Names over ``MAX_STRING`` characters and comments over ``MAX_TEXT`` are ``LIMIT_EXCEEDED``, and
either one that is not Unicode text (a noncharacter, say) is ``UNPARSEABLE_SOURCE``, before
anything is described; so is a comment that holds a control character but tab, line feed and
carriage return, since it would be a definition, whose text an agent and a terminal read (A6).
"""

import os
import re
import stat
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

import aibi
from aibi.core.importers import urls
from aibi.core.importers.confine import Confinement
from aibi.core.importers.describe import DatasetOrigin, TableOrigin, by_importer, describe
from aibi.core.importers.errors import ImportRefused, escaped, refused
from aibi.core.importers.files import DECLARED, bounded, renamed
from aibi.core.importers.infer import Datatype, ForeignKey, SourceTable, Status, infer
from aibi.core.importers.snapshot import (
    SERVERS,
    Kind,
    Snapshot,
    SnapshotTable,
    Target,
    ascii_folded,
    read_snapshot,
)
from aibi.core.importers.worker import Reader
from aibi.core.schema.ids import normalise_names
from aibi.core.schema.jsonio import is_text
from aibi.core.schema.limits import MAX_TEXT, TEXT_CHARACTERS
from aibi.core.schema.output import Segment, data, text
from aibi.core.schema.pack_api import (
    ConfinedPath,
    DatabaseSource,
    ImportNote,
    ImportOptions,
    ImportResult,
)
from aibi.core.schema.refusals import RefusalCode
from aibi.core.store.build import Layout
from aibi.core.store.sources import RawSource, SourceError, SourceValue, TypedSource

NAME = "aibi.databases"
_OTHERS_WRITE = stat.S_IWGRP | stat.S_IWOTH


@dataclass(frozen=True)
class Connection:
    """A named connection as the server's configuration declares it (D253, D305): never a
    credential."""

    name: str
    kind: Kind
    path: Path | None = None
    """A SQLite or DuckDB file."""
    url_env: str | None = None
    """For Postgres and MySQL, the environment variable that holds the URL."""
    schema: str | None = None
    """For Postgres (``public`` when absent) and DuckDB (``main``), the schema read."""


@dataclass(frozen=True)
class Resolved:
    """A connection resolved for one import: what the worker reads, and the provenance."""

    source: DatabaseSource
    target: Target = field(repr=False)
    host: str | None = None
    """A server's host, as the provenance records it."""


def _variable_refused(connection: Connection, why: str) -> ImportRefused:
    return refused(
        RefusalCode.INVALID_VALUE,
        "The environment variable ",
        data(cast(str, connection.url_env)),
        " of the connection ",
        data(connection.name),
        f" {why}",
    )


def _server(connection: Connection, environ: Mapping[str, str]) -> Resolved:
    """What a server's connection reads: its URL read by ``urls.parse``, and the connection
    string built from what it names, from which the provenance comes too (D310)."""
    url = environ.get(cast(str, connection.url_env), "")
    if not url:
        raise _variable_refused(connection, "is not set")
    try:
        read = urls.parse(cast(urls.ServerKind, connection.kind), url)
    except urls.UrlError as refusal:
        raise _variable_refused(connection, str(refusal)) from None
    target = Target(
        connection.kind, schema=connection.schema, database=read.database, dsn=read.connection()
    )
    location = _server_location(read.host, read.database, target)
    return Resolved(DatabaseSource(connection.name, connection.kind, location), target, read.host)


def _server_location(host: str, database: str, target: Target) -> str:
    """A server's provenance: ``<host>/<database>/<schema>``, MySQL's schema being its
    database."""
    schema = database if target.kind == "mysql" else target.schema or "public"
    return f"{host}/{database}/{schema}"


def _owned(status: os.stat_result) -> bool:
    """Whether the server's user or root owns what ``status`` describes: another owner could
    replace a connection's file, or swap it for a link, while it is read (D310)."""
    return status.st_uid in (os.geteuid(), 0)


def _file(connection: Connection, confinement: Confinement) -> Resolved:
    path = confinement.confine(os.fspath(cast(Path, connection.path)))
    if not os.path.isfile(path):
        raise refused(
            RefusalCode.PATH_NOT_CONFINED,
            "The connection's path is not a regular file: ",
            data(confinement.location(path)),
        )
    folder = os.path.dirname(path)
    directory = os.stat(folder)
    if not _owned(directory) or (
        directory.st_mode & _OTHERS_WRITE and not directory.st_mode & stat.S_ISVTX
    ):
        raise refused(
            RefusalCode.PATH_NOT_CONFINED,
            "The directory of the connection's file is another user's, or one its group or "
            "others can write, who could replace the file, or swap it for a link, while it is "
            "read: ",
            data(confinement.location(ConfinedPath(Path(folder)))),
        )
    status = os.stat(path)
    if not _owned(status) or status.st_mode & _OTHERS_WRITE:
        raise refused(
            RefusalCode.PATH_NOT_CONFINED,
            "The connection's file is another user's, or one its group or others can write, who "
            "could rewrite or replace it, or swap it for a link, while it is read: ",
            data(confinement.location(path)),
        )
    location = confinement.location(path)
    if connection.kind == "duckdb":
        location = f"{location}/{connection.schema or 'main'}"
    target = Target(
        connection.kind,
        schema=connection.schema,
        path=str(path),
        identity=confinement.identity(path),
        location=confinement.location(path),
    )
    return Resolved(DatabaseSource(connection.name, connection.kind, location), target)


def resolve(
    connection: Connection, confinement: Confinement, environ: Mapping[str, str] | None = None
) -> Resolved:
    """What an import of ``connection`` reads, with its provenance (D305); ``environ`` is the
    server's environment unless given. Raises ``ImportRefused``."""
    if connection.kind in SERVERS:
        return _server(connection, os.environ if environ is None else environ)
    return _file(connection, confinement)


def _named(name: str, what: str) -> None:
    bounded(name, what)
    if not is_text(name):
        raise refused(
            RefusalCode.UNPARSEABLE_SOURCE,
            f"{what} is not Unicode text (a lone surrogate or a noncharacter): ",
            data(escaped(name)),
        )


_CONTROL = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
"""The control characters a comment may not hold: C0 but tab, line feed and carriage return,
DEL and C1."""


def _comment(comment: str | None, *subject: Segment | str) -> None:
    if comment is None:
        return
    if len(comment) > MAX_TEXT:
        raise refused(
            RefusalCode.LIMIT_EXCEEDED,
            "The comment of ",
            *subject,
            f" has more than {MAX_TEXT} characters",
            limit=(TEXT_CHARACTERS, MAX_TEXT),
        )
    if not is_text(comment):
        raise refused(
            RefusalCode.UNPARSEABLE_SOURCE,
            "The comment of ",
            *subject,
            " is not Unicode text (a lone surrogate or a noncharacter)",
        )
    if _CONTROL.search(comment):
        raise refused(
            RefusalCode.UNPARSEABLE_SOURCE,
            "The comment of ",
            *subject,
            " holds a control character, which a definition does not (but tab and line breaks)",
        )


def _checked(snapshot: Snapshot) -> None:
    for table in snapshot.tables:
        _named(table.name, "A table's name")
        _comment(table.comment, "the table ", data(table.name))
        for column, comment in zip(
            table.columns, table.column_comments or (None,) * len(table.columns), strict=True
        ):
            _named(column, "A column's name")
            _comment(comment, "the column ", data(column), " of the table ", data(table.name))


def _match(name: str, names: Sequence[str]) -> str | None:
    """``name`` as one of ``names`` writes it, or else the one name it equals ignoring the case
    of ASCII letters, as SQLite matches names (D307)."""
    if name in names:
        return name
    folded = [found for found in names if ascii_folded(found) == ascii_folded(name)]
    return folded[0] if len(folded) == 1 else None


def _skipped(snapshot: Snapshot) -> list[ImportNote]:
    """A note for each relation the snapshot names as skipped, at most ``import_tables`` of them
    as the worker kept them, and one that counts the rest (D309)."""
    notes = [
        ImportNote(
            "skipped_source",
            None,
            [text("Skipped "), data(escaped(skipped.name)), text(f": {skipped.reason}")],
        )
        for skipped in snapshot.skipped
    ]
    rest = snapshot.more_skipped
    if rest > 0:
        notes.append(
            ImportNote(
                "skipped_source",
                None,
                [text(f"Skipped {rest} more relations that are not read, not noted one by one")],
                count=rest,
            )
        )
    return notes


def _not_kept(subject: str, why: str) -> ImportNote:
    return ImportNote(
        "not_proposed", subject, [text(f"A foreign key the database declares is not kept: {why}")]
    )


@dataclass(frozen=True)
class _Table:
    read: SnapshotTable
    id: str
    columns: tuple[str, ...]
    """Column ids, in source order."""

    def ids(self, names: Sequence[str]) -> tuple[str, ...] | None:
        """The ids of ``names``, each matched to one of the table's columns."""
        found: list[str] = []
        for name in names:
            matched = _match(name, self.read.columns)
            if matched is None:
                return None
            found.append(self.columns[self.read.columns.index(matched)])
        return tuple(found)


def _foreign_keys(
    table: _Table, tables: Mapping[str, _Table], notes: list[ImportNote]
) -> tuple[ForeignKey, ...]:
    kept: list[ForeignKey] = []
    held: set[frozenset[str]] = set()
    for key in table.read.foreign_keys:
        columns = table.ids(key.columns)
        subject = f"{table.id}.{'+'.join(columns)}" if columns else table.id
        parent_name = None if key.parent is None else _match(key.parent, list(tables))
        if parent_name is None:
            notes.append(_not_kept(subject, "its parent table is not read"))
            continue
        parent = tables[parent_name]
        parent_columns = parent.ids(key.parent_columns or parent.read.primary_key or ())
        if not columns or not parent_columns or len(columns) != len(parent_columns):
            notes.append(_not_kept(subject, "its columns do not match its parent's columns"))
            continue
        if len(set(columns)) != len(columns) or len(set(parent_columns)) != len(parent_columns):
            notes.append(_not_kept(subject, "a column repeats in it"))
            continue
        if set(parent_columns) != set(parent.ids(parent.read.primary_key or ()) or ()):
            notes.append(_not_kept(subject, "its parent columns are not its parent's primary key"))
            continue
        if frozenset(columns) in held:
            notes.append(
                _not_kept(subject, "another foreign key of the table has the same columns")
            )
            continue
        held.add(frozenset(columns))
        kept.append(ForeignKey(columns, parent.id, parent_columns))
    return tuple(kept)


class DatabaseImporter:
    """The core's importer of database snapshots (``aibi.databases``, D305)."""

    name = NAME

    @property
    def version(self) -> str:
        return aibi.__version__

    def import_database(self, source: Resolved, options: ImportOptions) -> ImportResult:
        named = options.name or source.source.connection
        bounded(named, "The dataset's name")
        with Reader(options.limits) as reader:
            snapshot = reader.run(read_snapshot, source.target, options.limits)
        _checked(snapshot)
        if not snapshot.tables:
            raise refused(RefusalCode.EMPTY_SOURCE, "The database holds no base table")
        notes = _skipped(snapshot)
        previous = options.previous
        table_ids = normalise_names(
            [table.name for table in snapshot.tables],
            "table",
            previous.tables if previous is not None else None,
        )
        tables: dict[str, _Table] = {}
        for table_id, read in zip(table_ids, snapshot.tables, strict=True):
            if table_id != read.name:
                notes.append(renamed(table_id, read.name))
            before = previous.columns.get(table_id) if previous is not None else None
            columns = tuple(normalise_names(read.columns, "column", before))
            for column, original in zip(columns, read.columns, strict=True):
                if column != original:
                    notes.append(renamed(f"{table_id}.{column}", original))
            tables[read.name] = _Table(read, table_id, columns)
        sources: dict[str, RawSource] = {}
        layouts: dict[str, Layout] = {}
        origins: dict[str, TableOrigin] = {}
        described: list[SourceTable] = []
        for table in tables.values():
            read = table.read
            rows = cast(tuple[tuple[SourceValue, ...], ...], read.rows)
            try:
                sources[table.id] = TypedSource(read.columns, rows)
            except SourceError as error:
                raise refused(
                    RefusalCode.UNPARSEABLE_SOURCE,
                    "The table ",
                    data(read.name),
                    f" holds a value that is not read: {error}",
                ) from None
            layouts[table.id] = Layout(
                table.id, tuple(zip(table.columns, read.columns, strict=True))
            )
            origins[table.id] = TableOrigin(
                "database",
                read.name,
                read.name,
                read.columns,
                comment=read.comment,
                column_comments=read.column_comments,
            )
            declared: dict[str, tuple[Datatype, Status]] = {}
            if read.kinds is not None:
                declared = {
                    table.columns[i]: DECLARED[kind]
                    for i, kind in enumerate(read.kinds)
                    if kind in DECLARED
                }
            primary = None if read.primary_key is None else table.ids(read.primary_key)
            described.append(
                SourceTable(
                    table.id,
                    table.columns,
                    rows,
                    declared,
                    primary,
                    _foreign_keys(table, tables, notes),
                )
            )
        inferred = infer(described)
        location = source.source.location
        if snapshot.database is not None:
            location = _server_location(cast(str, source.host), snapshot.database, source.target)
        dataset = DatasetOrigin(named, location, kind="database")
        bounded(dataset.location, "The source's location")
        descriptors = describe(
            dataset, origins, inferred, by=by_importer(NAME, self.version), at=options.at
        )
        return ImportResult(sources, layouts, descriptors, [*notes, *inferred.notes])


__all__ = ["NAME", "Connection", "DatabaseImporter", "Resolved", "resolve"]

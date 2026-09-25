"""Reading a database snapshot, in the import's worker process (SPEC §13.1, §14, D305–D307).

``read_snapshot`` is called in the import's worker (``worker.Reader``), never in the server's
process, which imports this module only to name the function and its classes, and so never
loads DuckDB: this module imports it inside the functions the worker calls. It reads every base
table of one database, with the keys and comments it declares, in one read transaction, so that
keys stay consistent:

- **SQLite** files are read with Python's own ``sqlite3``, opened read-only by a ``file:`` URI
  (``mode=ro``) and made defensive before anything is read (``SQLITE_DBCONFIG_DEFENSIVE``), with
  views, triggers and extension loading disabled by SQLite itself, an untrusted schema
  (``trusted_schema`` off), no memory map, cell checks on and temporary data in memory, so that
  a sort writes no file and ``reader_memory`` bounds it. The tables are ``pragma_table_list``'s
  of type ``table``; one whose name starts ``sqlite_`` is not read, and is noted unless it is
  one SQLite keeps of its own (``sqlite_sequence``, ``sqlite_stat1``…); views, virtual tables
  and their shadow tables are skipped and noted. A cell is what SQLite stores: an integer, a
  real, a text or null; a BLOB is refused (D306).
- **DuckDB** files are attached read-only to a session with external access disabled but for the
  file and its write-ahead log, extensions neither installed nor loaded, persistent secrets
  off, no temporary directory, UTC, one thread, half ``reader_memory`` as its memory limit, and
  its configuration locked before the file is attached. The tables are ``duckdb_tables()``'s in
  the connection's schema (``main`` by default); views are skipped and noted, and a generated
  column, which DuckDB would compute by the file's expression as it reads it, is refused.
- **Postgres** and **MySQL** databases are attached read-only, by the URL the connection's
  environment variable holds, to a session that loads only the scanner extension of their kind,
  bundled with the server and verified by DuckDB's signature (``allow_unsigned_extensions`` off),
  and that then disables external access and locks its configuration, so that nothing else it
  runs can reach a file or the network (D306). The tables are the database's own catalogue's
  base tables (Postgres: ordinary and partitioned tables, not partitions, in the connection's
  schema, ``public`` by default; MySQL: ``BASE TABLE`` in the URL's database); views,
  materialized views and foreign tables are skipped and noted. Their rows are read on the
  server by the scanner's query function (``postgres_query``, ``mysql_query``), in the
  snapshot's transaction, the columns named by the catalogue in its order, so that Postgres's
  names that differ only in case stay apart; a Postgres ordinary table is read ``ONLY``,
  without the rows of the tables that inherit from it, and a decimal of more than
  ``EXACT_DIGITS`` digits, or of any (Postgres's unconstrained ``numeric``, through its
  domains too), is cast to text on the server, as the scanners would read it as a
  floating-point number. Postgres reads in one ``REPEATABLE READ`` transaction, as DuckDB's
  scanner opens it; a MySQL session reads ``TINYINT(1)`` and ``BIT(1)`` columns as the types
  they declare, not as booleans, pushes no ordering to the server, and has one pooled
  connection, the transaction's, taken without waiting (MySQL snapshots are untested here).

For every kind, every statement is one the snapshot generates: the catalogue queries are
constants, with their filters bound as parameters or applied to their rows, and each table's
reads are SQLGlot trees whose identifiers are quoted names from the catalogue (§14), a server's
bound as the query function's parameter. Each table is counted first, and the cells of every
table together are refused over what ``import_cells`` leaves, before any row is read; a table is
then read in DuckDB's order of its declared primary key, or of all its columns when it declares
none, so that the same data gives the same raw snapshot whatever the server's storage order
(D306). DuckDB's values are read as Arrow, whose types the Parquet reader reads
(``parquet.from_arrow``): a column of another type is refused, naming it. A string over
``FIELD_CHARACTERS`` characters is ``UNPARSEABLE_SOURCE``, as a text file's field over it is,
its row named by its place in that order, from 1 (a snapshot has no header row).

The file of a SQLite or DuckDB connection must be the file confined (its device and inode) when
it is opened and when the snapshot ends; each file the database keeps beside it (SQLite's
``-wal``, ``-shm`` and ``-journal``, DuckDB's ``.wal``) must be a regular file where it exists;
and, where ``/proc/self/fd`` lists them, the regular files the reader opened must be those,
since neither SQLite nor DuckDB can be made to open a path without following a link that
replaced it after the check. Whatever the database or its driver raises, but a refusal and
running out of memory, is ``UNPARSEABLE_SOURCE`` naming the exception's class alone: DuckDB's
and the drivers' messages can hold the URL, and so a credential (§14).
"""

import os
import sqlite3
import stat
import urllib.parse
from collections.abc import Collection, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from importlib.util import find_spec
from pathlib import Path
from typing import TYPE_CHECKING, Literal, cast

from sqlglot import exp

from aibi.core.importers.errors import ImportRefused, long_cell_refused, refused
from aibi.core.schema.limits import IMPORT_CELLS, IMPORT_TABLES, TABLE_COLUMNS, ImportLimits
from aibi.core.schema.output import Segment, data
from aibi.core.schema.refusals import RefusalCode
from aibi.core.store import parquet
from aibi.core.store.sources import long_cell

if TYPE_CHECKING:
    import duckdb

Kind = Literal["postgres", "mysql", "sqlite", "duckdb"]
KINDS: tuple[Kind, ...] = ("postgres", "mysql", "sqlite", "duckdb")
SERVERS: frozenset[Kind] = frozenset({"postgres", "mysql"})
"""The kinds read over the network, by a URL; the others are files."""
EXTENSIONS: Mapping[Kind, str] = {"postgres": "postgres_scanner", "mysql": "mysql_scanner"}
"""The DuckDB extension each server's kind loads, from its bundled wheel (D306)."""
SQLITE_VALUES = ("integer", "real", "text", "null")
"""What a SQLite cell may hold to be read."""
DATABASE_TYPES = (
    "integers of every width",
    "floating-point numbers",
    "decimals",
    "booleans",
    "text, UUIDs, JSON and enumerations",
    "dates",
    "timestamps, with or without a time zone",
    "times of day",
    "lists of text",
)
"""The column types a DuckDB, Postgres or MySQL snapshot reads, as a refusal lists them."""
SOURCE = "src"
"""The name the attached database has in the snapshot's session."""
BESIDE: Mapping[Kind, tuple[str, ...]] = {
    "sqlite": ("-wal", "-shm", "-journal"),
    "duckdb": (".wal",),
}
"""The suffixes of the files a SQLite or DuckDB database keeps beside its own."""
EXACT_DIGITS = 38
"""The most digits a DuckDB decimal holds: the scanners read a wider or unconstrained decimal as a
floating-point number, so the server casts such a column to text (D306)."""
_BATCH = 4096
_NUMERIC = 1700
"""Postgres's ``numeric``, whose OID is fixed."""
_SQLITE_OWN = frozenset(
    {
        "sqlite_schema",
        "sqlite_master",
        "sqlite_temp_schema",
        "sqlite_temp_master",
        "sqlite_sequence",
        "sqlite_stat1",
        "sqlite_stat2",
        "sqlite_stat3",
        "sqlite_stat4",
    }
)
"""The tables SQLite keeps of its own, which are neither read nor noted."""


@dataclass(frozen=True)
class Target:
    """What the worker reads: a connection resolved by the server (``databases.resolve``)."""

    kind: Kind
    schema: str | None = None
    """The schema read: Postgres's (``public`` by default) or a DuckDB file's (``main``)."""
    path: str | None = None
    """A SQLite or DuckDB file's confined path."""
    identity: tuple[int, int] | None = None
    """The confined file's device and inode."""
    database: str | None = None
    """A MySQL URL's database, the schema its tables are read from."""
    url: str | None = field(default=None, repr=False)
    """A Postgres or MySQL URL, which holds a credential: never in a repr, log or message."""


@dataclass(frozen=True)
class ForeignKey:
    """A foreign key the database declares, by the names it gives."""

    columns: tuple[str, ...]
    parent: str | None
    """``None`` for a parent in another schema or database, which is not read."""
    parent_columns: tuple[str, ...]
    """Empty when SQLite leaves them out: the parent's primary key."""


@dataclass(frozen=True)
class SnapshotTable:
    """A base table as read: its columns in the database's order and its rows."""

    name: str
    columns: tuple[str, ...]
    kinds: tuple[parquet.ArrowKind, ...] | None
    """The type of each column, as Arrow gave it; ``None`` for SQLite, whose cells have types of
    their own, whatever a column declares."""
    rows: tuple[tuple[object, ...], ...]
    comment: str | None = None
    column_comments: tuple[str | None, ...] = ()
    primary_key: tuple[str, ...] | None = None
    """The declared primary key; ``None`` when the table declares none."""
    foreign_keys: tuple[ForeignKey, ...] = ()


@dataclass(frozen=True)
class Skipped:
    """A relation that is not a base table, which is not read."""

    name: str
    kind: str
    """What it is: ``a view``, ``a virtual table``…"""


@dataclass(frozen=True)
class Snapshot:
    tables: tuple[SnapshotTable, ...]
    """In the order of their names."""
    skipped: tuple[Skipped, ...] = ()


@dataclass(frozen=True)
class _Relation:
    name: str
    comment: str | None
    columns: tuple[str, ...]
    """As the catalogue names them, in the database's order."""
    column_comments: Mapping[str, str | None]
    primary_key: tuple[str, ...] | None
    foreign_keys: tuple[ForeignKey, ...]
    as_text: frozenset[str] = frozenset()
    """The columns the server casts to text: decimals of more than ``EXACT_DIGITS`` digits."""
    only: bool = False
    """A Postgres ordinary table, read without the rows of the tables that inherit from it."""


class _Cells:
    """The tables and cells counted so far, against the import's limits."""

    def __init__(self, limits: ImportLimits) -> None:
        self.limits = limits
        self.cells = 0

    def tables(self, count: int) -> None:
        if count > self.limits.import_tables:
            raise refused(
                RefusalCode.LIMIT_EXCEEDED,
                f"The database has more than {self.limits.import_tables} tables",
                limit=(IMPORT_TABLES, self.limits.import_tables),
            )

    def table(self, name: str, columns: int, rows: int) -> None:
        if columns > self.limits.table_columns:
            raise refused(
                RefusalCode.LIMIT_EXCEEDED,
                f"A table has more than {self.limits.table_columns} columns: ",
                data(name),
                limit=(TABLE_COLUMNS, self.limits.table_columns),
            )
        self.cells += columns * max(1, rows)
        if self.cells > self.limits.import_cells:
            raise refused(
                RefusalCode.LIMIT_EXCEEDED,
                f"The database has more than {self.limits.import_cells} cells",
                limit=(IMPORT_CELLS, self.limits.import_cells),
            )


def read_snapshot(target: Target, limits: ImportLimits) -> Snapshot:
    """Every base table of ``target``, in one read transaction (see the module's docstring).
    Called in the import's worker process. Raises ``ImportRefused``."""
    try:
        if target.kind == "sqlite":
            return _sqlite(target, limits)
        return _duckdb(target, limits)
    except (ImportRefused, MemoryError):
        raise
    except Exception as error:
        raise refused(
            RefusalCode.UNPARSEABLE_SOURCE,
            f"The database cannot be read ({type(error).__name__})",
        ) from None


def _same_file(target: Target) -> None:
    path = cast(str, target.path)
    try:
        status = os.stat(path, follow_symlinks=False)
    except OSError:
        status = None
    if status is None or (status.st_dev, status.st_ino) != target.identity:
        raise refused(
            RefusalCode.PATH_NOT_CONFINED,
            "The database file changed after it was confined: ",
            data(path),
        )


def _beside(target: Target) -> set[tuple[int, int]]:
    """The device and inode of each file the database keeps beside its own, which must be a
    regular file where it exists: a link there could have the reader open a file outside the
    import directories."""
    found: set[tuple[int, int]] = set()
    for suffix in BESIDE[target.kind]:
        path = f"{target.path}{suffix}"
        try:
            status = os.stat(path, follow_symlinks=False)
        except FileNotFoundError:
            continue
        except OSError:
            status = None
        if status is None or not stat.S_ISREG(status.st_mode):
            raise refused(
                RefusalCode.PATH_NOT_CONFINED,
                "A file beside the database is not a regular file: ",
                data(path),
            )
        found.add((status.st_dev, status.st_ino))
    return found


def _open_files() -> dict[int, tuple[int, int]]:
    """The regular files this process holds open, by descriptor: their device and inode, as
    Linux's ``/proc/self/fd`` gives them (none where there is no such directory)."""
    try:
        descriptors = os.listdir("/proc/self/fd")
    except OSError:
        return {}
    found: dict[int, tuple[int, int]] = {}
    for descriptor in descriptors:
        try:
            status = os.stat(f"/proc/self/fd/{descriptor}")
        except OSError:
            continue
        if stat.S_ISREG(status.st_mode):
            found[int(descriptor)] = (status.st_dev, status.st_ino)
    return found


def _opened_confined(target: Target, before: Mapping[int, tuple[int, int]]) -> None:
    """Refuse the snapshot if the reader opened a regular file other than the confined one and
    those beside it: its path could have been swapped for a link between the check and the
    open, which neither SQLite nor DuckDB opens without following."""
    allowed = {cast(tuple[int, int], target.identity), *_beside(target)}
    for descriptor, identity in _open_files().items():
        if before.get(descriptor) != identity and identity not in allowed:
            raise refused(
                RefusalCode.PATH_NOT_CONFINED,
                "The database file opened is not the one confined: ",
                data(cast(str, target.path)),
            )


def _identifier(name: str) -> exp.Identifier:
    return exp.to_identifier(name, quoted=True)


def _table(name: str, schema: str | None = None, catalog: str | None = None) -> exp.Table:
    return exp.Table(
        this=_identifier(name),
        db=None if schema is None else _identifier(schema),
        catalog=None if catalog is None else _identifier(catalog),
    )


def _count(table: exp.Table, dialect: str) -> str:
    return exp.select(exp.Count(this=exp.Star())).from_(table.copy()).sql(dialect=dialect)


def _rows(table: exp.Table, columns: Sequence[str], order: Sequence[str], dialect: str) -> str:
    """The table's columns, in the order of ``order``."""
    select = exp.select(*(exp.column(_identifier(name)) for name in columns))
    ordered = [exp.Ordered(this=exp.column(_identifier(name))) for name in order]
    return select.from_(table.copy()).order_by(*ordered).sql(dialect=dialect)


def _long(
    name: str, columns: Sequence[str], rows: Sequence[Sequence[object]], before: int = 0
) -> None:
    """Refuse a cell over ``FIELD_CHARACTERS`` characters in ``rows``, which follow ``before``
    rows of the table: a row is named by its place in the snapshot's order, from 1, a snapshot
    having no header row."""
    if (found := long_cell(rows)) is not None:
        row, column = found
        raise long_cell_refused((before + row, column), columns, "The table ", data(name))


def _key(given: Sequence[str], columns: Sequence[str]) -> tuple[str, ...] | None:
    return tuple(given) if given and all(name in columns for name in given) else None


# --- SQLite ------------------------------------------------------------------------------------


def _defensive(connection: sqlite3.Connection) -> None:
    for option, value in (
        (sqlite3.SQLITE_DBCONFIG_DEFENSIVE, True),
        (sqlite3.SQLITE_DBCONFIG_ENABLE_VIEW, False),
        (sqlite3.SQLITE_DBCONFIG_ENABLE_TRIGGER, False),
        (sqlite3.SQLITE_DBCONFIG_TRUSTED_SCHEMA, False),
        (sqlite3.SQLITE_DBCONFIG_ENABLE_LOAD_EXTENSION, False),
    ):
        connection.setconfig(option, value)
    connection.execute("PRAGMA query_only = ON")
    connection.execute("PRAGMA mmap_size = 0")
    connection.execute("PRAGMA cell_size_check = ON")
    connection.execute("PRAGMA temp_store = MEMORY")


_SQLITE_SKIPPED = {"view": "a view", "virtual": "a virtual table", "shadow": "a shadow table"}


def _sqlite(target: Target, limits: ImportLimits) -> Snapshot:
    path = cast(str, target.path)
    _same_file(target)
    _beside(target)
    before = _open_files()
    uri = f"file:{urllib.parse.quote(path)}?mode=ro"
    connection = sqlite3.connect(uri, uri=True, isolation_level=None, cached_statements=0)
    try:
        _defensive(connection)
        connection.execute("BEGIN")
        snapshot = _sqlite_tables(connection, limits)
        _opened_confined(target, before)
        connection.execute("COMMIT")
    finally:
        connection.close()
    _same_file(target)
    return snapshot


def _sqlite_tables(connection: sqlite3.Connection, limits: ImportLimits) -> Snapshot:
    listed = connection.execute(
        "SELECT name, type FROM pragma_table_list() WHERE schema = 'main' ORDER BY name"
    ).fetchall()
    names: list[str] = []
    skipped: list[Skipped] = []
    for name, kind in cast(list[tuple[str, str]], listed):
        if name.lower().startswith("sqlite_"):
            if name.lower() not in _SQLITE_OWN:
                skipped.append(Skipped(name, "a table named as SQLite's own tables are"))
            continue
        if kind == "table":
            names.append(name)
        else:
            skipped.append(Skipped(name, _SQLITE_SKIPPED.get(kind, f"a {kind}")))
    cells = _Cells(limits)
    cells.tables(len(names))
    relations: dict[str, _Relation] = {}
    counts: dict[str, int] = {}
    for name in names:
        info = cast(
            list[tuple[str, int, int]],
            connection.execute(
                "SELECT name, pk, hidden FROM pragma_table_xinfo(?) ORDER BY cid", (name,)
            ).fetchall(),
        )
        columns = tuple(column for column, _, hidden in info if hidden != 1)
        ranked = sorted((pk, column) for column, pk, _ in info if pk > 0)
        primary = tuple(column for _, column in ranked) or None
        keys: dict[int, list[tuple[int, str, str, str | None]]] = {}
        for number, sequence, parent, child, parent_column in cast(
            list[tuple[int, int, str, str, str | None]],
            connection.execute(
                'SELECT id, seq, "table", "from", "to" FROM pragma_foreign_key_list(?)', (name,)
            ).fetchall(),
        ):
            keys.setdefault(number, []).append((sequence, parent, child, parent_column))
        foreign: list[ForeignKey] = []
        for number in sorted(keys):
            parts = sorted(keys[number])
            given = [part[3] for part in parts]
            parent_columns = tuple(c for c in given if c is not None) if None not in given else ()
            foreign.append(
                ForeignKey(tuple(part[2] for part in parts), parts[0][1], parent_columns)
            )
        relations[name] = _Relation(name, None, columns, {}, primary, tuple(foreign))
        counted = connection.execute(_count(_table(name, "main"), "sqlite")).fetchone()
        counts[name] = int(cast(tuple[int], counted)[0])
        cells.table(name, len(columns), counts[name])
    tables: list[SnapshotTable] = []
    for name in names:
        relation = relations[name]
        columns = relation.columns
        if not columns:
            skipped.append(Skipped(name, "a table without columns"))
            continue
        rows = _sqlite_rows(connection, name, columns, relation.primary_key or columns)
        tables.append(
            SnapshotTable(
                name,
                columns,
                None,
                rows,
                primary_key=relation.primary_key,
                foreign_keys=relation.foreign_keys,
            )
        )
    return Snapshot(tuple(tables), tuple(sorted(skipped, key=lambda found: found.name)))


def _sqlite_rows(
    connection: sqlite3.Connection, name: str, columns: tuple[str, ...], order: Sequence[str]
) -> tuple[tuple[object, ...], ...]:
    cursor = connection.execute(_rows(_table(name, "main"), columns, order, "sqlite"))
    rows: list[tuple[object, ...]] = []
    while batch := cast(list[tuple[object, ...]], cursor.fetchmany(_BATCH)):
        for row in batch:
            for index, value in enumerate(row):
                if isinstance(value, bytes):
                    raise refused(
                        RefusalCode.UNSUPPORTED_FORMAT,
                        "A cell of the table ",
                        data(name),
                        ", column ",
                        data(columns[index]),
                        ", holds a BLOB, which is not read",
                        alternatives=SQLITE_VALUES,
                    )
        _long(name, columns, batch, len(rows))
        rows.extend(batch)
    return tuple(rows)


# --- DuckDB, Postgres and MySQL ----------------------------------------------------------------


def extension_path(kind: Kind) -> Path | None:
    """The bundled extension a server's kind loads, for this DuckDB, if it is installed."""
    import duckdb

    name = EXTENSIONS[kind]
    spec = find_spec(f"duckdb_extension_{name}")
    if spec is None or not spec.submodule_search_locations:
        return None
    version = duckdb.__version__
    for location in spec.submodule_search_locations:
        found = Path(location) / "extensions" / f"v{version}" / f"{name}.duckdb_extension"
        if found.is_file():
            return found
    return None


def _session(limits: ImportLimits) -> "duckdb.DuckDBPyConnection":
    import duckdb

    return duckdb.connect(
        ":memory:",
        config={
            "autoinstall_known_extensions": False,
            "autoload_known_extensions": False,
            "allow_unsigned_extensions": False,
            "allow_community_extensions": False,
            "threads": 1,
            "memory_limit": f"{limits.reader_memory // 2}B",
            "temp_directory": "",
        },
    )


def _attach(url_or_path: str, kind: Kind) -> str:
    options = [exp.AttachOption(this=exp.var("READ_ONLY"))]
    if kind in SERVERS:
        options.insert(0, exp.AttachOption(this=exp.var("TYPE"), expression=exp.var(kind)))
    attach = exp.Attach(
        this=exp.Alias(this=exp.Literal.string(url_or_path), alias=_identifier(SOURCE)),
        exists=False,
        expressions=options,
    )
    return attach.sql(dialect="duckdb")


_MYSQL_SETTINGS = (
    "SET mysql_tinyint1_as_boolean = false",
    "SET mysql_bit1_as_boolean = false",
    "SET mysql_order_pushdown_enabled = false",
    "SET mysql_pool_size = 1",
    "SET mysql_pool_acquire_mode = 'try'",
)
"""A MySQL session's: a column is read as the type it declares, the rows are ordered by DuckDB,
and one connection, the transaction's, reads everything; a read that would need another fails
rather than read outside the transaction."""


def _lock(connection: "duckdb.DuckDBPyConnection") -> None:
    connection.execute("SET TimeZone = 'UTC'")
    connection.execute("SET enable_external_access = false")
    connection.execute("SET lock_configuration = true")


def _opened(target: Target, limits: ImportLimits) -> "duckdb.DuckDBPyConnection":
    """A session with the target attached as ``src``, read-only, and its configuration
    locked."""
    connection = _session(limits)
    try:
        if target.kind == "duckdb":
            path = cast(str, target.path)
            connection.execute("SET allowed_paths = $paths", {"paths": [path, f"{path}.wal"]})
            connection.execute("SET allow_persistent_secrets = false")
            _lock(connection)
            connection.execute(_attach(path, "duckdb"))
            return connection
        extension = extension_path(target.kind)
        if extension is None:
            raise refused(
                RefusalCode.NOT_SUPPORTED,
                f"The server has no {EXTENSIONS[target.kind]} extension installed to read the "
                "database with",
            )
        connection.load_extension(str(extension))
        if target.kind == "mysql":
            for setting in _MYSQL_SETTINGS:
                connection.execute(setting)
        connection.execute(_attach(cast(str, target.url), target.kind))
        _lock(connection)
    except BaseException:
        connection.close()
        raise
    return connection


def _duckdb(target: Target, limits: ImportLimits) -> Snapshot:
    import duckdb

    before: dict[int, tuple[int, int]] = {}
    if target.path is not None:
        _same_file(target)
        _beside(target)
        before = _open_files()
    try:
        connection = _opened(target, limits)
    except duckdb.Error as error:
        raise refused(
            RefusalCode.UNPARSEABLE_SOURCE,
            f"The database cannot be opened ({type(error).__name__})",
        ) from None
    try:
        connection.execute("BEGIN TRANSACTION")
        snapshot = _read(connection, target, limits)
        if target.path is not None:
            _opened_confined(target, before)
        connection.execute("COMMIT")
    except duckdb.OutOfMemoryException:
        raise MemoryError from None
    finally:
        connection.close()
    if target.path is not None:
        _same_file(target)
    return snapshot


def _schema(target: Target) -> str:
    if target.kind == "mysql":
        return cast(str, target.database)
    return target.schema or ("public" if target.kind == "postgres" else "main")


@dataclass(frozen=True)
class _Tables:
    """The statements that count and read a relation's rows in the snapshot's session: a
    DuckDB file's through the attached database, a server's run on the server by its query
    function (``postgres_query``, ``mysql_query``), in the snapshot's transaction, with each
    column named ``c0``, ``c1``… after its place, and ordered by DuckDB."""

    connection: "duckdb.DuckDBPyConnection"
    kind: Kind
    schema: str

    def _table(self, relation: _Relation) -> exp.Table:
        if self.kind == "duckdb":
            return _table(relation.name, self.schema, SOURCE)
        table = _table(relation.name, self.schema)
        if relation.only:
            table.set("only", True)
        return table

    def _on_server(
        self, query: exp.Select, order: Sequence[int] = ()
    ) -> "duckdb.DuckDBPyConnection":
        function = exp.Anonymous(
            this=f"{self.kind}_query",
            expressions=[exp.Placeholder(this="source"), exp.Placeholder(this="query")],
        )
        select = exp.select(exp.Star()).from_(exp.Table(this=function))
        if order:
            select = select.order_by(
                *(exp.Ordered(this=exp.column(_identifier(f"c{place}"))) for place in order)
            )
        parameters = {"source": SOURCE, "query": query.sql(dialect=self.kind)}
        return self.connection.execute(select.sql(dialect="duckdb"), parameters)

    def count(self, relation: _Relation) -> int:
        if self.kind == "duckdb":
            counted = self.connection.execute(_count(self._table(relation), "duckdb")).fetchone()
        else:
            query = exp.select(exp.Count(this=exp.Star())).from_(self._table(relation))
            counted = self._on_server(query).fetchone()
        return int(cast(tuple[int], counted)[0])

    def rows(self, relation: _Relation, order: Sequence[str]) -> "duckdb.DuckDBPyConnection":
        if self.kind == "duckdb":
            sql = _rows(self._table(relation), relation.columns, order, "duckdb")
            return self.connection.execute(sql)
        named: list[exp.Expr] = []
        for place, name in enumerate(relation.columns):
            column: exp.Expr = exp.column(_identifier(name))
            if name in relation.as_text:
                column = exp.Cast(this=column, to=exp.DataType.build("text"))
            named.append(exp.alias_(column, _identifier(f"c{place}")))
        query = exp.select(*named).from_(self._table(relation))
        return self._on_server(query, [relation.columns.index(name) for name in order])


def _read(
    connection: "duckdb.DuckDBPyConnection", target: Target, limits: ImportLimits
) -> Snapshot:
    schema = _schema(target)
    if target.kind == "duckdb":
        relations, skipped = _duckdb_catalog(connection, schema)
    elif target.kind == "postgres":
        relations, skipped = _postgres_catalog(connection, schema)
    else:
        relations, skipped = _mysql_catalog(connection)
    cells = _Cells(limits)
    cells.tables(len(relations))
    reader = _Tables(connection, target.kind, schema)
    for relation in relations:
        cells.table(relation.name, len(relation.columns), reader.count(relation))
    tables: list[SnapshotTable] = []
    extra: list[Skipped] = []
    for relation in relations:
        columns = relation.columns
        if not columns:
            extra.append(Skipped(relation.name, "a table without columns"))
            continue
        primary = _key(relation.primary_key or (), columns)
        read = reader.rows(relation, primary or columns)
        try:
            found = parquet.from_arrow(read.to_arrow_table())
        except parquet.UnsupportedTypeError as error:
            raise refused(
                RefusalCode.UNSUPPORTED_FORMAT,
                "A column's type is not read: the table ",
                data(relation.name),
                ", column ",
                data(columns[error.column]),
                f", of the type {error.arrow_type}",
                alternatives=DATABASE_TYPES,
            ) from None
        except parquet.UnreadableParquetError as error:
            raise refused(
                RefusalCode.UNPARSEABLE_SOURCE,
                "The table ",
                data(relation.name),
                f" cannot be read: {error}",
            ) from None
        _long(relation.name, columns, found.rows)
        tables.append(
            SnapshotTable(
                relation.name,
                columns,
                found.kinds,
                found.rows,
                relation.comment,
                tuple(relation.column_comments.get(column) for column in columns),
                primary,
                relation.foreign_keys,
            )
        )
    every = sorted([*skipped, *extra], key=lambda found: found.name)
    return Snapshot(tuple(tables), tuple(every))


def _fetched(
    connection: "duckdb.DuckDBPyConnection", sql: str, parameters: Mapping[str, object]
) -> list[tuple[object, ...]]:
    return cast(list[tuple[object, ...]], connection.execute(sql, dict(parameters)).fetchall())


def _comment(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _relations(
    tables: Sequence[tuple[str, str | None]],
    columns: Iterable[tuple[str, str, str | None, bool]],
    keys: Iterable[tuple[str, str, str, Sequence[str], str | None, Sequence[str]]],
    only: Collection[str] = (),
) -> list[_Relation]:
    """Relations from rows of (table, comment); (table, column, comment, read as text), in the
    database's order of each table's columns; and (table, name, ``p`` or ``f``, columns, parent
    or ``None`` for one not read, parent columns). ``only`` names Postgres's ordinary tables."""
    named: dict[str, list[str]] = {name: [] for name, _ in tables}
    comments: dict[str, dict[str, str | None]] = {name: {} for name, _ in tables}
    as_text: dict[str, set[str]] = {name: set() for name, _ in tables}
    for table, column, comment, text in columns:
        if table in named:
            named[table].append(column)
            comments[table][column] = _comment(comment)
            if text:
                as_text[table].add(column)
    primary: dict[str, tuple[str, ...]] = {}
    foreign: dict[str, list[ForeignKey]] = {}
    for table, _, kind, key_columns, parent, parent_columns in keys:
        if table not in named:
            continue
        if kind == "p":
            primary[table] = tuple(key_columns)
        else:
            foreign.setdefault(table, []).append(
                ForeignKey(tuple(key_columns), parent, tuple(parent_columns))
            )
    return [
        _Relation(
            name,
            _comment(comment),
            tuple(named[name]),
            comments[name],
            primary.get(name),
            tuple(foreign.get(name, ())),
            frozenset(as_text[name]),
            name in only,
        )
        for name, comment in sorted(tables)
    ]


_DUCKDB_TABLES = (
    "SELECT table_name, comment FROM duckdb_tables() "
    "WHERE database_name = $source AND schema_name = $schema"
)
_DUCKDB_VIEWS = (
    "SELECT view_name FROM duckdb_views() WHERE database_name = $source AND schema_name = $schema"
)
_DUCKDB_SCHEMAS = "SELECT schema_name FROM duckdb_schemas() WHERE database_name = $source"
_DUCKDB_COLUMNS = (
    "SELECT table_name, column_name, comment, column_default IS NOT NULL FROM duckdb_columns() "
    "WHERE database_name = $source AND schema_name = $schema ORDER BY table_name, column_index"
)
_DUCKDB_KEYS = (
    "SELECT table_name, constraint_name, "
    "CASE constraint_type WHEN 'PRIMARY KEY' THEN 'p' ELSE 'f' END, "
    "constraint_column_names, referenced_table, referenced_column_names FROM duckdb_constraints() "
    "WHERE database_name = $source AND schema_name = $schema "
    "AND constraint_type IN ('PRIMARY KEY', 'FOREIGN KEY') ORDER BY table_name, constraint_index"
)


def _unknown_schema(schema: str, known: Iterable[str]) -> ImportRefused:
    alternatives: list[Segment] = [data(name) for name in sorted(known)]
    return refused(
        RefusalCode.INVALID_VALUE,
        "The database has no schema ",
        data(schema),
        alternatives=alternatives,
    )


def _generated(
    connection: "duckdb.DuckDBPyConnection", schema: str, table: str, defaulted: Collection[str]
) -> None:
    """Refuse a generated column of a DuckDB file's table: DuckDB computes its values when it is
    read, by an expression the file gives, which could read the session's settings. Its
    catalogue gives a generated column's expression as the column's default, and ``DESCRIBE``
    gives none."""
    described = connection.execute(
        exp.Describe(this=_table(table, schema, SOURCE)).sql(dialect="duckdb")
    ).fetchall()
    for row in cast(list[tuple[object, ...]], described):
        if str(row[0]) in defaulted and row[4] is None:
            raise refused(
                RefusalCode.UNSUPPORTED_FORMAT,
                "A generated column is not read: the table ",
                data(table),
                ", column ",
                data(str(row[0])),
                alternatives=("columns that store their values",),
            )


def _duckdb_catalog(
    connection: "duckdb.DuckDBPyConnection", schema: str
) -> tuple[list[_Relation], list[Skipped]]:
    given = {"source": SOURCE, "schema": schema}
    schemas = [str(row[0]) for row in _fetched(connection, _DUCKDB_SCHEMAS, {"source": SOURCE})]
    if schema not in schemas:
        raise _unknown_schema(schema, schemas)
    tables = [
        (str(name), cast(str | None, comment))
        for name, comment in _fetched(connection, _DUCKDB_TABLES, given)
    ]
    views = [Skipped(str(row[0]), "a view") for row in _fetched(connection, _DUCKDB_VIEWS, given)]
    columns: list[tuple[str, str, str | None, bool]] = []
    defaulted: dict[str, set[str]] = {}
    for table, column, comment, has_default in _fetched(connection, _DUCKDB_COLUMNS, given):
        columns.append((str(table), str(column), cast(str | None, comment), False))
        if has_default:
            defaulted.setdefault(str(table), set()).add(str(column))
    for table, names in sorted(defaulted.items()):
        _generated(connection, schema, table, names)
    keys = (
        (
            str(table),
            str(name),
            str(kind),
            cast(list[str], child),
            cast(str | None, parent),
            cast(list[str], parent_columns),
        )
        for table, name, kind, child, parent, parent_columns in _fetched(
            connection, _DUCKDB_KEYS, given
        )
    )
    return _relations(tables, columns, keys), views


_POSTGRES_SCHEMAS = (
    "SELECT nspname::text FROM pg_catalog.pg_namespace "
    "WHERE nspname <> 'pg_toast' AND nspname !~ '^pg_(toast_)?temp_'"
)
_POSTGRES_RELATIONS = (
    "SELECT n.nspname, c.relname, c.relkind::text, obj_description(c.oid, 'pg_class') "
    "FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
    "WHERE c.relkind IN ('r', 'p', 'v', 'm', 'f') AND NOT c.relispartition"
)
_POSTGRES_COLUMNS = (
    "SELECT n.nspname, c.relname, a.attname::text, col_description(c.oid, a.attnum), "
    "a.atttypid::int8, a.atttypmod "
    "FROM pg_catalog.pg_attribute a JOIN pg_catalog.pg_class c ON c.oid = a.attrelid "
    "JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
    "WHERE a.attnum > 0 AND NOT a.attisdropped AND c.relkind IN ('r', 'p') "
    "AND NOT c.relispartition ORDER BY c.relname, a.attnum"
)
_POSTGRES_DOMAINS = (
    "SELECT oid::int8, typbasetype::int8, typtypmod FROM pg_catalog.pg_type WHERE typtype = 'd'"
)
_POSTGRES_KEYS = (
    "SELECT n.nspname AS table_schema, c.relname AS table_name, k.conname::text AS name, "
    "k.contype::text AS kind, "
    "ARRAY(SELECT a.attname::text FROM unnest(k.conkey) WITH ORDINALITY AS u(attnum, i) "
    "JOIN pg_catalog.pg_attribute a ON a.attrelid = k.conrelid AND a.attnum = u.attnum "
    "ORDER BY u.i) AS columns, "
    "pn.nspname AS parent_schema, pc.relname AS parent, "
    "ARRAY(SELECT a.attname::text FROM unnest(k.confkey) WITH ORDINALITY AS u(attnum, i) "
    "JOIN pg_catalog.pg_attribute a ON a.attrelid = k.confrelid AND a.attnum = u.attnum "
    "ORDER BY u.i) AS parent_columns "
    "FROM pg_catalog.pg_constraint k JOIN pg_catalog.pg_class c ON c.oid = k.conrelid "
    "JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
    "LEFT JOIN pg_catalog.pg_class pc ON pc.oid = k.confrelid "
    "LEFT JOIN pg_catalog.pg_namespace pn ON pn.oid = pc.relnamespace "
    "WHERE k.contype IN ('p', 'f') AND NOT c.relispartition ORDER BY c.relname, k.conname"
)
_POSTGRES_QUERY = "SELECT * FROM postgres_query($source, $query)"
_POSTGRES_SKIPPED = {"v": "a view", "m": "a materialized view", "f": "a foreign table"}


def _wide_numeric(type_id: int, modifier: int, domains: Mapping[int, tuple[int, int]]) -> bool:
    """Whether a Postgres column of the type ``type_id`` with the modifier ``modifier`` is a
    ``numeric`` of more than ``EXACT_DIGITS`` digits, or of any: through the domains it is
    declared by, each ``(base type, modifier)`` by its OID, the first modifier given holding."""
    seen: set[int] = set()
    while type_id in domains and type_id not in seen:
        seen.add(type_id)
        base, given = domains[type_id]
        modifier = given if modifier < 0 else modifier
        type_id = base
    return type_id == _NUMERIC and (modifier < 4 or (modifier - 4) >> 16 > EXACT_DIGITS)


def _postgres_catalog(
    connection: "duckdb.DuckDBPyConnection", schema: str
) -> tuple[list[_Relation], list[Skipped]]:
    def query(sql: str) -> list[tuple[object, ...]]:
        return _fetched(connection, _POSTGRES_QUERY, {"source": SOURCE, "query": sql})

    schemas = [str(row[0]) for row in query(_POSTGRES_SCHEMAS)]
    if schema not in schemas:
        raise _unknown_schema(schema, schemas)
    tables: list[tuple[str, str | None]] = []
    ordinary: set[str] = set()
    skipped: list[Skipped] = []
    for space, name, kind, comment in query(_POSTGRES_RELATIONS):
        if space != schema:
            continue
        if kind in ("r", "p"):
            tables.append((str(name), cast(str | None, comment)))
            if kind == "r":
                ordinary.add(str(name))
        else:
            skipped.append(Skipped(str(name), _POSTGRES_SKIPPED[str(kind)]))
    domains = {
        int(cast(int, domain)): (int(cast(int, base)), int(cast(int, modifier)))
        for domain, base, modifier in query(_POSTGRES_DOMAINS)
    }
    columns = (
        (
            str(table),
            str(column),
            cast(str | None, comment),
            _wide_numeric(int(cast(int, type_id)), int(cast(int, modifier)), domains),
        )
        for space, table, column, comment, type_id, modifier in query(_POSTGRES_COLUMNS)
        if space == schema
    )
    keys = (
        (
            str(table),
            str(name),
            str(kind),
            cast(list[str], child),
            None if parent_space != schema else cast(str | None, parent),
            cast(list[str], parent_columns),
        )
        for space, table, name, kind, child, parent_space, parent, parent_columns in query(
            _POSTGRES_KEYS
        )
        if space == schema
    )
    return _relations(tables, columns, keys, ordinary), skipped


_MYSQL_TABLES = (
    "SELECT TABLE_NAME, TABLE_TYPE, TABLE_COMMENT FROM information_schema.TABLES "
    "WHERE TABLE_SCHEMA = DATABASE()"
)
_MYSQL_COLUMNS = (
    "SELECT TABLE_NAME, COLUMN_NAME, COLUMN_COMMENT, "
    f"IF(DATA_TYPE = 'decimal' AND NUMERIC_PRECISION > {EXACT_DIGITS}, 'text', '') "
    "FROM information_schema.COLUMNS "
    "WHERE TABLE_SCHEMA = DATABASE() ORDER BY TABLE_NAME, ORDINAL_POSITION"
)
_MYSQL_KEYS = (
    "SELECT TABLE_NAME, CONSTRAINT_NAME, COLUMN_NAME, REFERENCED_TABLE_SCHEMA, "
    "REFERENCED_TABLE_NAME, REFERENCED_COLUMN_NAME FROM information_schema.KEY_COLUMN_USAGE "
    "WHERE TABLE_SCHEMA = DATABASE() "
    "AND (CONSTRAINT_NAME = 'PRIMARY' OR REFERENCED_TABLE_NAME IS NOT NULL) "
    "ORDER BY TABLE_NAME, CONSTRAINT_NAME, ORDINAL_POSITION"
)
_MYSQL_QUERY = "SELECT * FROM mysql_query($source, $query)"


def _mysql_catalog(
    connection: "duckdb.DuckDBPyConnection",
) -> tuple[list[_Relation], list[Skipped]]:
    def query(sql: str) -> list[tuple[object, ...]]:
        return _fetched(connection, _MYSQL_QUERY, {"source": SOURCE, "query": sql})

    tables: list[tuple[str, str | None]] = []
    skipped: list[Skipped] = []
    for name, kind, comment in query(_MYSQL_TABLES):
        if kind == "BASE TABLE":
            tables.append((str(name), cast(str | None, comment)))
        else:
            skipped.append(Skipped(str(name), "a view"))
    columns = (
        (str(table), str(column), cast(str | None, comment), text == "text")
        for table, column, comment, text in query(_MYSQL_COLUMNS)
    )
    grouped: dict[tuple[str, str], list[tuple[object, ...]]] = {}
    for row in query(_MYSQL_KEYS):
        grouped.setdefault((str(row[0]), str(row[1])), []).append(row)
    database = _database(connection)

    def keys() -> Iterator[tuple[str, str, str, Sequence[str], str | None, Sequence[str]]]:
        for (table, name), rows in grouped.items():
            child = [str(row[2]) for row in rows]
            if name == "PRIMARY":
                yield table, name, "p", child, None, ()
                continue
            same = all(row[3] == database for row in rows)
            parent = str(rows[0][4]) if same else None
            yield table, name, "f", child, parent, [str(row[5]) for row in rows]

    return _relations(tables, columns, keys()), skipped


def _database(connection: "duckdb.DuckDBPyConnection") -> str:
    found = _fetched(connection, _MYSQL_QUERY, {"source": SOURCE, "query": "SELECT DATABASE()"})
    return str(found[0][0])


__all__ = [
    "DATABASE_TYPES",
    "EXTENSIONS",
    "KINDS",
    "SERVERS",
    "SQLITE_VALUES",
    "ForeignKey",
    "Kind",
    "Skipped",
    "Snapshot",
    "SnapshotTable",
    "Target",
    "extension_path",
    "read_snapshot",
]

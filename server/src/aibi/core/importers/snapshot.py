"""Reading a database snapshot, in the import's worker process (SPEC §13.1, §14, D305–D307).

``read_snapshot`` is called in the import's worker (``worker.Reader``), never in the server's
process, which imports this module only to name the function and its classes, and so never loads
DuckDB: this module imports it inside the functions the worker calls. It reads every base table of
one database, with the keys and comments it declares, in one read transaction, so that keys stay
consistent:

- **SQLite** files are read with Python's own ``sqlite3``, opened read-only by a ``file:`` URI
  (``mode=ro``) and made defensive before anything is read (``SQLITE_DBCONFIG_DEFENSIVE``), with
  views, triggers and extension loading disabled by SQLite itself, an untrusted schema
  (``trusted_schema`` off), no memory map, cell checks on and temporary data in memory, so that a
  sort writes no file and ``reader_memory`` bounds it. The tables are ``sqlite_schema``'s of type
  ``table`` (``pragma_table_list``'s when a virtual table may be there, one without a root page or
  whose ``sql`` holds ``virtual``, whose shadow tables only it tells apart); one whose name starts
  ``sqlite_`` is not read, and is noted unless it is one SQLite keeps of its own
  (``sqlite_sequence``, ``sqlite_stat1``…); views, virtual tables and their shadow tables are
  skipped and noted. A cell is what SQLite stores: an integer, a real, a text or null; a BLOB is
  refused, and so is a ``VIRTUAL`` generated column, which SQLite would compute by the file's
  expression as it reads it (a ``STORED`` one is read, D306, D309).
- **DuckDB** files are attached read-only to a session with external access disabled but for the
  file and its write-ahead log, extensions neither installed nor loaded, persistent secrets off, no
  temporary directory, UTC, one thread, half ``reader_memory`` as its memory limit, and its
  configuration locked before the file is attached. The tables are ``duckdb_tables()``'s in the
  connection's schema (``main`` by default); views are skipped and noted, and a generated column,
  which DuckDB would compute by the file's expression as it reads it, is refused.
- **Postgres** and **MySQL** databases are attached read-only, by the connection string built from
  the URL the connection's environment variable holds (``urls``, D310), with the environment
  variables the driver would read as defaults removed from the worker's, to a session that loads
  only the scanner extension of their kind, bundled with the server and verified by DuckDB's
  signature (``allow_unsigned_extensions`` off), whose ``secret_directory`` cannot exist, so that no
  stored secret completes the connection string, and that then disables external access and locks
  its configuration, so that nothing else it runs can reach a file or the network (D306). A Postgres
  session resolves names in ``pg_catalog`` alone and has ``row_security`` off, set at connect time
  over whatever the role or the database sets (``urls.POSTGRES_OPTIONS``). It also fixes at connect
  time every setting that shapes a value's text or binary form (``DateStyle``, ``TimeZone``,
  ``IntervalStyle``, ``client_encoding`` and the rest), so that a role or database cannot steer the
  recorded bytes. The server must name the database its session opened as the URL's (Postgres's
  ``current_database()``, in the snapshot's transaction; MySQL's ``DATABASE()``, before it), which
  the provenance records (D310). The tables are the database's own catalogue's base tables
  (Postgres: ordinary and partitioned tables, not partitions, in the connection's schema, ``public``
  by default; MySQL: ``BASE TABLE`` and MariaDB's ``SYSTEM VERSIONED`` ones in the URL's database);
  views, materialized views, sequences, foreign tables, the partitions of another schema's tables,
  partitions pending detach, partitioned tables with a foreign partition, and Postgres tables with
  row security or a virtual generated column, whose owner's code reading them would run, or a
  column of a base type whose output or send function is neither ``pg_catalog``'s nor an installed
  extension's (D309), are skipped and noted; a ``time with time zone`` is read as its exact text
  (Postgres) or refused (a DuckDB file), since Arrow drops its offset; each base table read is
  locked ``ACCESS SHARE`` (an ordinary one ``ONLY``, a partitioned one with its partitions) before
  its columns are read, so a ``RENAME`` cannot mislabel them, and a lock waited for past half of
  ``reader_seconds`` is refused naming its table (``lock_timeout``). Postgres reads in one
  ``REPEATABLE READ`` read-only transaction, as DuckDB's scanner opens it: its catalogue and its
  rows are read on the server by ``postgres_query``, in that transaction, the catalogue filtered
  there by the schema's name, and each table's columns named by the catalogue in its order, so that
  names that differ only in case stay apart, each column of a type outside ``pg_catalog`` read
  through its type's output function (``pg_catalog.format``) so that no cast, whose lookup ignores
  the transaction's snapshot and which the schema owner could add mid-snapshot, is resolved on a
  read; an ordinary table is read ``ONLY``, without the rows of
  the tables that inherit from it, and a ``numeric`` (through its domains too), which can hold
  ``NaN`` and which the scanner would read as a floating-point number or a zero, is cast to text
  there, and a ``money`` to ``numeric`` and then text, rather than the server's locale's format, an
  array of either to an array of text (D309). MySQL reads through the attached catalogue alone, its
  ``information_schema`` too, in the one read-only transaction the scanner starts on the one pooled
  connection DuckDB's transaction holds (D308): its query function would run each statement in a
  transaction of its own. The session's transactions must read a snapshot (``REPEATABLE READ`` or
  ``SERIALIZABLE``), which ``mysql_query`` reads before the transaction starts; its time zone is
  UTC, so that a ``TIMESTAMP`` is read in UTC; a table of an engine without transactions (MyISAM,
  say), a decimal of more than ``EXACT_DIGITS`` digits, which the scanner reads as a floating-point
  number, and two databases of the server, or two relations of the database, whose names the
  server's collation or the catalogue's ASCII folding compares as equal, which it cannot tell apart
  (their weights read by ``mysql_query`` before the transaction), are refused, and so is a
  relation's or a column's name that the scanner cannot quote safely (``urls.mysql_quotable``: it
  escapes a backtick in an identifier with a backslash, which MySQL does not read, so that the rest
  of the name would be SQL), and a zero date, which it reads as null: its column's present values,
  which MySQL counts, are more than those read. A MySQL session reads ``TINYINT(1)`` and ``BIT(1)``
  columns as the types they declare, not as booleans, and pushes no ordering to the server.

For every kind, every statement is one the snapshot generates: the catalogue queries are constants,
with their filters bound as parameters, a Postgres schema's name given as the hexadecimal digits of
its UTF-8, or applied to their rows, and each table's reads are SQLGlot trees whose identifiers are
quoted names from the catalogue (§14), Postgres's bound as the query function's parameter. Each
table is counted first, and the cells of every table together are refused over what ``import_cells``
leaves, before any row is read; a table is then read in DuckDB's or SQLite's order of its declared
primary key, or of all its columns when it declares none, text compared by its bytes whatever the
column's collation (SQLite's ``BINARY``, then each cell's type and a zero real's sign; DuckDB's
``C``), so that the same data gives the same raw snapshot whatever the server's storage order (D306,
D309). DuckDB's values are read as Arrow, whose types the Parquet reader reads
(``parquet.from_arrow``): a column of another type is refused, naming it. A string over
``FIELD_CHARACTERS`` characters is ``UNPARSEABLE_SOURCE``, as a text file's field over it is, its
row named by its place in that order, from 1 (a snapshot has no header row).

The file of a SQLite or DuckDB connection must be the file confined (its device and inode) when it
is opened and when the snapshot ends; each file the database keeps beside it (SQLite's ``-wal``,
``-shm`` and ``-journal``, DuckDB's ``.wal``) must be a regular file where it exists; and, where
``/proc/self/fd`` lists them, the regular files the reader opened must be those, since neither
SQLite nor DuckDB can be made to open a path without following a link that replaced it after the
check. Whatever the database or its driver raises, but a refusal and running out of memory, is
``UNPARSEABLE_SOURCE`` naming the exception's class alone, and the table being counted or read when
there is one: DuckDB's and the drivers' messages can hold the URL, and so a credential (§14). A name
from the catalogue that a refusal gives has each lone surrogate and noncharacter escaped
(``errors.escaped``), and a file is named by its location in its import directory. At most
``import_tables`` skipped relations are named, and the rest counted (D309).
"""

import math
import os
import sqlite3
import stat
import string
import urllib.parse
from collections.abc import Collection, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field, replace
from importlib.util import find_spec
from pathlib import Path
from typing import TYPE_CHECKING, Literal, cast

from sqlglot import exp

from aibi.core.importers.errors import ImportRefused, escaped, long_cell_refused, refused
from aibi.core.importers.urls import mysql_quotable
from aibi.core.schema.limits import (
    IMPORT_CELLS,
    IMPORT_TABLES,
    READER_SECONDS,
    TABLE_COLUMNS,
    ImportLimits,
)
from aibi.core.schema.output import DataSegment, Segment, data
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
"""The most digits a DuckDB decimal holds: the MySQL scanner reads a wider decimal as a
floating-point number, so a MySQL column of one is refused (D308)."""
MYSQL_ISOLATION = ("REPEATABLE READ", "SERIALIZABLE")
"""The isolation levels of a MySQL session whose transactions read a snapshot (D308)."""
_BATCH = 4096
_NUMERIC = 1700
"""Postgres's ``numeric``, whose OID is fixed."""
_MONEY = 790
"""Postgres's ``money``, whose OID is fixed."""
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
_ASCII_LOWER = str.maketrans(string.ascii_uppercase, string.ascii_lowercase)


def ascii_folded(name: str) -> str:
    """``name`` with its ASCII letters in lower case: SQLite, and DuckDB's catalogue of a MySQL
    database, match names ignoring the case of ASCII letters alone (D307, D308)."""
    return name.translate(_ASCII_LOWER)


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
    location: str | None = None
    """A SQLite or DuckDB file's location in its import directory, which a refusal names rather
    than its absolute path (D310)."""
    database: str | None = None
    """A server URL's database, which the snapshot checks the server opened (D310): a MySQL
    database is the schema its tables are read from."""
    dsn: str | None = field(default=None, repr=False)
    """A Postgres or MySQL connection string, built from the connection's URL (``urls``, D310),
    which holds a credential: never in a repr, log or message."""


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
    """A relation that is not read, and why, as its note says it: ``a view, not a base
    table``…"""

    name: str
    reason: str


@dataclass(frozen=True)
class Snapshot:
    """What one snapshot read (D306): its base tables, and the relations it skipped."""

    tables: tuple[SnapshotTable, ...]
    """In the order of their names."""
    skipped: tuple[Skipped, ...] = ()
    """In the order of their names, at most ``import_tables`` of them (D309)."""
    more_skipped: int = 0
    """How many more relations were skipped, which are not named."""
    database: str | None = None
    """A server's database, as the server named the one it opened (D310)."""


@dataclass(frozen=True)
class _Column:
    """A column as a catalogue gives it, in its table's order."""

    table: str
    name: str
    comment: object
    casts: tuple[str, ...] = ()
    """The types the server casts it to, in turn (D309)."""
    text: bool = False
    """A DuckDB file's text column, ordered by its bytes whatever its collation (D309)."""
    output: bool = False
    """A Postgres column of a type outside ``pg_catalog``, read through its type's output
    function so that no cast is resolved on a read (D309)."""


@dataclass(frozen=True)
class _Relation:
    name: str
    comment: str | None
    columns: tuple[str, ...]
    """As the catalogue names them, in the database's order."""
    column_comments: Mapping[str, str | None]
    primary_key: tuple[str, ...] | None
    foreign_keys: tuple[ForeignKey, ...]
    casts: Mapping[str, tuple[str, ...]] = field(default_factory=dict[str, tuple[str, ...]])
    """The types a Postgres server casts a column to, in turn: a ``numeric`` to text, a
    ``money`` to ``numeric`` and then text (D309)."""
    only: bool = False
    """A Postgres ordinary table, read without the rows of the tables that inherit from it."""
    text: frozenset[str] = frozenset()
    """A DuckDB file's text columns, ordered by their bytes whatever their collation (D309)."""
    output: frozenset[str] = frozenset()
    """A Postgres relation's columns of a type outside ``pg_catalog``, read through the type's
    output function (D309)."""


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
                _name(name),
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


def _name(name: str) -> DataSegment:
    """A name from the catalogue, as a refusal gives it (``errors.escaped``, D309)."""
    return data(escaped(name))


def _unreadable(table: str, error: BaseException) -> ImportRefused:
    """What the database or its driver raised counting or reading ``table``, named by its class
    alone, since its message can hold the URL (§14, D309)."""
    return refused(
        RefusalCode.UNPARSEABLE_SOURCE,
        "The table ",
        _name(table),
        f" cannot be read ({type(error).__name__})",
    )


def _generated_refused(table: str, column: str) -> ImportRefused:
    return refused(
        RefusalCode.UNSUPPORTED_FORMAT,
        "A generated column is not read: the table ",
        _name(table),
        ", column ",
        _name(column),
        alternatives=("columns that store their values",),
    )


def _kept(skipped: Iterable[Skipped], limits: ImportLimits) -> tuple[tuple[Skipped, ...], int]:
    """The first ``import_tables`` of ``skipped`` by name, and how many more there are: the
    worker sends no more names than that (D309)."""
    ordered = sorted(skipped, key=lambda found: found.name)
    return tuple(ordered[: limits.import_tables]), max(0, len(ordered) - limits.import_tables)


def _not_base(what: str) -> str:
    return f"{what}, not a base table"


_NO_COLUMNS = "a base table without columns, which is not read"


def _shown(target: Target, suffix: str = "") -> DataSegment:
    """The file a refusal names: its location in its import directory (D310)."""
    return data(f"{target.location or os.path.basename(cast(str, target.path))}{suffix}")


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
            _shown(target),
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
                _shown(target, suffix),
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
                _shown(target),
            )


def _identifier(name: str) -> exp.Identifier:
    return exp.to_identifier(name, quoted=True)


def _column(name: str) -> exp.Column:
    return exp.column(_identifier(name))


def _table(name: str, schema: str | None = None, catalog: str | None = None) -> exp.Table:
    return exp.Table(
        this=_identifier(name),
        db=None if schema is None else _identifier(schema),
        catalog=None if catalog is None else _identifier(catalog),
    )


def _count(table: exp.Table, dialect: str) -> str:
    return exp.select(exp.Count(this=exp.Star())).from_(table.copy()).sql(dialect=dialect)


def _rows(table: exp.Table, columns: Sequence[str], order: Sequence[exp.Expr], dialect: str) -> str:
    """The table's columns, in the order of ``order``."""
    select = exp.select(*(_column(name) for name in columns))
    ordered = [exp.Ordered(this=expression) for expression in order]
    return select.from_(table.copy()).order_by(*ordered).sql(dialect=dialect)


def _long(
    name: str, columns: Sequence[str], rows: Sequence[Sequence[object]], before: int = 0
) -> None:
    """Refuse a cell over ``FIELD_CHARACTERS`` characters in ``rows``, which follow ``before``
    rows of the table: a row is named by its place in the snapshot's order, from 1, a snapshot
    having no header row."""
    if (found := long_cell(rows)) is not None:
        row, column = found
        shown = [escaped(column) for column in columns]
        raise long_cell_refused((before + row, column), shown, "The table ", _name(name))


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
_SQLITE_VIRTUAL = 2
"""``pragma_table_xinfo``'s ``hidden`` for a ``VIRTUAL`` generated column (``3`` is a
``STORED`` one, and ``1`` a virtual table's hidden column)."""


def _sqlite(target: Target, limits: ImportLimits) -> Snapshot:
    path = cast(str, target.path)
    _same_file(target)
    _beside(target)
    before = _open_files()
    uri = f"file:{urllib.parse.quote(path)}?mode=ro"
    connection = sqlite3.connect(uri, uri=True, isolation_level=None, cached_statements=0)
    try:
        _defensive(connection)
        connection.create_function(_SIGNED, 1, _signed, deterministic=True)
        connection.execute("BEGIN")
        snapshot = _sqlite_tables(connection, limits)
        _opened_confined(target, before)
        connection.execute("COMMIT")
    finally:
        connection.close()
    _same_file(target)
    return snapshot


def _sqlite_relation(connection: sqlite3.Connection, name: str) -> _Relation:
    info = cast(
        list[tuple[str, int, int]],
        connection.execute(
            "SELECT name, pk, hidden FROM pragma_table_xinfo(?) ORDER BY cid", (name,)
        ).fetchall(),
    )
    for column, _, hidden in info:
        if hidden == _SQLITE_VIRTUAL:
            raise _generated_refused(name, column)
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
        foreign.append(ForeignKey(tuple(part[2] for part in parts), parts[0][1], parent_columns))
    return _Relation(name, None, columns, {}, primary, tuple(foreign))


def _sqlite_listed(connection: sqlite3.Connection) -> list[tuple[str, str]]:
    """The file's tables and views by name, each with ``pragma_table_list``'s type: that pragma
    computes every view's columns, in time that grows faster than their number, so it runs only
    when a virtual table, whose shadow tables only it tells apart, may be there: a table without
    a root page (``rootpage`` 0), or one whose ``sql`` holds ``virtual`` in any case, since
    SQLite reads a table as virtual by that keyword, whatever its ``rootpage`` says (D309)."""
    listed = cast(
        list[tuple[str, str, int | None, str | None]],
        connection.execute(
            "SELECT name, type, rootpage, sql FROM sqlite_schema WHERE type IN ('table', 'view')"
        ).fetchall(),
    )
    if any(
        kind == "table" and (not root or "virtual" in ascii_folded(sql or ""))
        for _, kind, root, sql in listed
    ):
        return sorted(
            cast(
                list[tuple[str, str]],
                connection.execute(
                    "SELECT name, type FROM pragma_table_list() WHERE schema = 'main'"
                ).fetchall(),
            )
        )
    return sorted((name, kind) for name, kind, _, _ in listed)


def _sqlite_tables(connection: sqlite3.Connection, limits: ImportLimits) -> Snapshot:
    names: list[str] = []
    skipped: list[Skipped] = []
    for name, kind in _sqlite_listed(connection):
        if ascii_folded(name).startswith("sqlite_"):
            if ascii_folded(name) not in _SQLITE_OWN:
                skipped.append(Skipped(name, "a table named as SQLite's own tables are"))
            continue
        if kind == "table":
            names.append(name)
        else:
            skipped.append(Skipped(name, _not_base(_SQLITE_SKIPPED.get(kind, f"a {kind}"))))
    cells = _Cells(limits)
    cells.tables(len(names))
    relations: dict[str, _Relation] = {}
    for name in names:
        try:
            relations[name] = _sqlite_relation(connection, name)
            counted = connection.execute(_count(_table(name, "main"), "sqlite")).fetchone()
        except sqlite3.Error as error:
            raise _unreadable(name, error) from None
        cells.table(name, len(relations[name].columns), int(cast(tuple[int], counted)[0]))
    tables: list[SnapshotTable] = []
    for name in names:
        relation = relations[name]
        columns = relation.columns
        if not columns:
            skipped.append(Skipped(name, _NO_COLUMNS))
            continue
        try:
            rows = _sqlite_rows(connection, name, columns, relation.primary_key or columns)
        except sqlite3.Error as error:
            raise _unreadable(name, error) from None
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
    return Snapshot(tuple(tables), *_kept(skipped, limits))


_SIGNED = "aibi_signed"
"""The function a SQLite snapshot's session defines: whether a real is negative, its sign bit
set, as ``-0.0``'s is."""


def _signed(value: object) -> int:
    return int(isinstance(value, float) and math.copysign(1.0, value) < 0)


def _sqlite_order(order: Sequence[str]) -> list[exp.Expr]:
    """Each column of ``order`` compared by its bytes (``BINARY``) whatever the collation it
    declares, then by its cell's type, since SQLite's order ties an integer and the real that
    equals it, and then a zero real by its sign, since it ties ``-0.0`` and ``0.0`` (D309)."""
    ordered: list[exp.Expr] = []
    for name in order:
        typed = exp.Anonymous(this="typeof", expressions=[_column(name)])
        zero = exp.and_(
            exp.EQ(this=_column(name), expression=exp.Literal.number(0)),
            exp.EQ(this=typed.copy(), expression=exp.Literal.string("real")),
        )
        ordered.append(exp.Collate(this=_column(name), expression=exp.var("BINARY")))
        ordered.append(typed)
        ordered.append(
            exp.Case(
                ifs=[
                    exp.If(this=zero, true=exp.Anonymous(this=_SIGNED, expressions=[_column(name)]))
                ]
            )
        )
    return ordered


def _sqlite_rows(
    connection: sqlite3.Connection, name: str, columns: tuple[str, ...], order: Sequence[str]
) -> tuple[tuple[object, ...], ...]:
    sql = _rows(_table(name, "main"), columns, _sqlite_order(order), "sqlite")
    cursor = connection.execute(sql)
    rows: list[tuple[object, ...]] = []
    while batch := cast(list[tuple[object, ...]], cursor.fetchmany(_BATCH)):
        for row in batch:
            for index, value in enumerate(row):
                if isinstance(value, bytes):
                    raise refused(
                        RefusalCode.UNSUPPORTED_FORMAT,
                        "A cell of the table ",
                        _name(name),
                        ", column ",
                        _name(columns[index]),
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


_NO_SECRETS = "/dev/null/secrets"
"""The session's ``secret_directory``: a path that cannot exist, ``/dev/null`` being no
directory, so that no persistent secret (DuckDB's default is ``.duckdb/stored_secrets``, beneath
the worker's working directory when it has no ``HOME``) gives a server's connection a port, a
password or anything its URL leaves out (D310)."""


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
            "secret_directory": _NO_SECRETS,
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
    "SET mysql_enable_transactions = true",
    "SET mysql_tinyint1_as_boolean = false",
    "SET mysql_bit1_as_boolean = false",
    "SET mysql_time_as_time = false",
    "SET mysql_incomplete_dates_as_nulls = false",
    "SET mysql_order_pushdown_enabled = false",
    "SET mysql_aggregate_pushdown_enabled = true",
    "SET mysql_session_time_zone = '+00:00'",
    "SET mysql_pool_size = 1",
    "SET mysql_pool_acquire_mode = 'try'",
)
"""A MySQL session's (D308): every read through the catalogue runs in one read-only transaction
of the server's, which the one pooled connection holds, and a read that would need another
connection fails rather than read outside it; a column is read as the type it declares, a
``TIME`` as its text (it can be a duration) and a date with a zero month or day refused rather
than read as null; counts are pushed to the server, which counts a zero date as present, and the
rows are ordered by DuckDB; and a ``TIMESTAMP`` is read in UTC."""


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
        connection.execute(_attach(cast(str, target.dsn), target.kind))
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
    except duckdb.OutOfMemoryException:
        raise MemoryError from None
    except duckdb.Error as error:
        raise refused(
            RefusalCode.UNPARSEABLE_SOURCE,
            f"The database cannot be opened ({type(error).__name__})",
        ) from None
    try:
        database: str | None = None
        if target.kind == "mysql":
            database = _opened_database(connection, target)
            _mysql_isolation(connection)
            _mysql_collisions(connection, cast(str, target.database))
        connection.execute("BEGIN TRANSACTION")
        if target.kind == "postgres":
            database = _opened_database(connection, target)
        snapshot = replace(_read(connection, target, limits), database=database)
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


_OPENED = {"postgres": "SELECT pg_catalog.current_database()", "mysql": "SELECT DATABASE()"}
"""How a server names the database its session opened: Postgres's ``current_database()``, in
the snapshot's transaction, and MySQL's ``DATABASE()``, on the one pooled connection that then
holds it."""


def _opened_database(connection: "duckdb.DuckDBPyConnection", target: Target) -> str:
    """The database the server opened, as it names it, refused unless it is the URL's, which
    the provenance records (D310): the URL's grammar bounds a name to what the server keeps of
    one, and this checks that it did."""
    query = _POSTGRES_QUERY if target.kind == "postgres" else _MYSQL_QUERY
    [(found,)] = _fetched(connection, query, {"source": SOURCE, "query": _OPENED[target.kind]})
    if found != target.database:
        raise refused(
            RefusalCode.INVALID_VALUE,
            "The server opened a database other than the one the connection's URL names: ",
            data(escaped(str(found))),
        )
    return cast(str, found)


def _schema(target: Target) -> str:
    if target.kind == "mysql":
        return cast(str, target.database)
    return target.schema or ("public" if target.kind == "postgres" else "main")


def _postgres_read(relation: _Relation, name: str) -> exp.Expr:
    """How a Postgres column is read on the server (D309). A column of a type outside
    ``pg_catalog`` (an enum, a domain, a composite, an array of such, a range or a multirange) is
    read through its type's output function, called by ``pg_catalog.format('%s', …)`` with its
    null kept, so that no cast, whose lookup ignores the transaction's snapshot and which a schema
    owner could add or change mid-snapshot to run a function as the import's role, is resolved on
    a read; the output functions ``format`` reaches (``enum_out``, ``record_out``, ``array_out``,
    ``range_out`` and a domain's base type's) are ``pg_catalog``'s or an installed extension's, and
    a type whose own output or send function is neither is skipped before the read
    (``_POSTGRES_OWNER_CODE``). A ``numeric``,
    ``money`` or ``timetz``, all ``pg_catalog``'s, is cast to text, whose cast a non-superuser
    cannot redefine."""
    column: exp.Expr = _column(name)
    if name in relation.output:
        formatted = exp.Anonymous(
            this="pg_catalog.format", expressions=[exp.Literal.string("%s"), column]
        )
        return exp.Case(
            ifs=[exp.If(this=exp.Is(this=column, expression=exp.Null()), true=exp.Null())],
            default=formatted,
        )
    for to in relation.casts.get(name, ()):
        column = exp.Cast(this=column, to=exp.DataType.build(to, dialect="postgres"))
    return column


@dataclass(frozen=True)
class _Tables:
    """The statements that count and read a relation's rows in the snapshot's session: a
    DuckDB file's and a MySQL database's through the attached catalogue, a Postgres database's
    run on the server by ``postgres_query``, in the snapshot's transaction, with each column
    named ``c0``, ``c1``… after its place, and ordered by DuckDB."""

    connection: "duckdb.DuckDBPyConnection"
    kind: Kind
    schema: str

    def _table(self, relation: _Relation) -> exp.Table:
        if self.kind != "postgres":
            return _table(relation.name, self.schema, SOURCE)
        table = _table(relation.name, self.schema)
        if relation.only:
            table.set("only", True)
        return table

    def _on_server(
        self, query: exp.Select, order: Sequence[int] = ()
    ) -> "duckdb.DuckDBPyConnection":
        function = exp.Anonymous(
            this="postgres_query",
            expressions=[exp.Placeholder(this="source"), exp.Placeholder(this="query")],
        )
        select = exp.select(exp.Star()).from_(exp.Table(this=function))
        if order:
            select = select.order_by(*(exp.Ordered(this=_column(f"c{place}")) for place in order))
        parameters = {"source": SOURCE, "query": query.sql(dialect="postgres")}
        return self.connection.execute(select.sql(dialect="duckdb"), parameters)

    def count(self, relation: _Relation) -> int:
        if self.kind != "postgres":
            counted = self.connection.execute(_count(self._table(relation), "duckdb")).fetchone()
        else:
            query = exp.select(exp.Count(this=exp.Star())).from_(self._table(relation))
            counted = self._on_server(query).fetchone()
        return int(cast(tuple[int], counted)[0])

    def present(self, relation: _Relation, columns: Sequence[str]) -> tuple[int, ...]:
        """How many present values each of ``columns`` holds, as a MySQL server counts them: the
        count is pushed to it (``mysql_aggregate_pushdown_enabled``)."""
        counts = (exp.Count(this=_column(name)) for name in columns)
        select = exp.select(*counts).from_(self._table(relation))
        found = self.connection.execute(select.sql(dialect="duckdb")).fetchone()
        return tuple(int(value) for value in cast(tuple[int, ...], found))

    def rows(self, relation: _Relation, order: Sequence[str]) -> "duckdb.DuckDBPyConnection":
        if self.kind != "postgres":
            ordered: list[exp.Expr] = [
                exp.Collate(this=_column(name), expression=exp.var("C"))
                if name in relation.text
                else _column(name)
                for name in order
            ]
            sql = _rows(self._table(relation), relation.columns, ordered, "duckdb")
            return self.connection.execute(sql)
        named = [
            exp.alias_(_postgres_read(relation, name), _identifier(f"c{place}"))
            for place, name in enumerate(relation.columns)
        ]
        query = exp.select(*named).from_(self._table(relation))
        return self._on_server(query, [relation.columns.index(name) for name in order])


_TEMPORAL: frozenset[parquet.ArrowKind] = frozenset({"date", "datetime", "naive_datetime"})


def _zero_dates(reader: _Tables, relation: _Relation, found: parquet.ParquetSource) -> None:
    """Refuse a MySQL zero date (``0000-00-00``), which the scanner reads as null: MySQL counts
    it as a present value, so its column's count on the server is more than the present values
    read (D308)."""
    temporal = [place for place, kind in enumerate(found.kinds) if kind in _TEMPORAL]
    if not temporal:
        return
    counted = reader.present(relation, [relation.columns[place] for place in temporal])
    for place, count in zip(temporal, counted, strict=True):
        if count != sum(1 for row in found.rows if row[place] is not None):
            raise refused(
                RefusalCode.UNPARSEABLE_SOURCE,
                "The table ",
                _name(relation.name),
                ", column ",
                _name(relation.columns[place]),
                ", holds a zero date, which is no date and which MySQL does not hold as missing",
            )


def _read(
    connection: "duckdb.DuckDBPyConnection", target: Target, limits: ImportLimits
) -> Snapshot:
    import duckdb

    schema = _schema(target)
    if target.kind == "duckdb":
        relations, skipped = _duckdb_catalog(connection, schema)
    elif target.kind == "postgres":
        relations, skipped = _postgres_catalog(connection, schema, limits)
    else:
        relations, skipped = _mysql_catalog(connection, schema)
    cells = _Cells(limits)
    cells.tables(len(relations))
    reader = _Tables(connection, target.kind, schema)
    for relation in relations:
        try:
            counted = reader.count(relation)
        except duckdb.OutOfMemoryException:
            raise
        except duckdb.Error as error:
            raise _unreadable(relation.name, error) from None
        cells.table(relation.name, len(relation.columns), counted)
    tables: list[SnapshotTable] = []
    extra: list[Skipped] = []
    for relation in relations:
        columns = relation.columns
        if not columns:
            extra.append(Skipped(relation.name, _NO_COLUMNS))
            continue
        primary = _key(relation.primary_key or (), columns)
        try:
            arrow = reader.rows(relation, primary or columns).to_arrow_table()
        except duckdb.OutOfMemoryException:
            raise
        except duckdb.Error as error:
            raise _unreadable(relation.name, error) from None
        try:
            found = parquet.from_arrow(arrow)
        except parquet.UnsupportedTypeError as error:
            raise refused(
                RefusalCode.UNSUPPORTED_FORMAT,
                "A column's type is not read: the table ",
                _name(relation.name),
                ", column ",
                _name(columns[error.column]),
                f", of the type {error.arrow_type}",
                alternatives=DATABASE_TYPES,
            ) from None
        except parquet.UnreadableParquetError as error:
            if error.column is not None:
                raise refused(
                    RefusalCode.UNPARSEABLE_SOURCE,
                    "The table ",
                    _name(relation.name),
                    ", column ",
                    _name(columns[error.column]),
                    f", cannot be read: {error}",
                ) from None
            raise refused(
                RefusalCode.UNPARSEABLE_SOURCE,
                "The table ",
                _name(relation.name),
                f" cannot be read: {error}",
            ) from None
        if target.kind == "mysql":
            try:
                _zero_dates(reader, relation, found)
            except duckdb.OutOfMemoryException:
                raise
            except duckdb.Error as error:
                raise _unreadable(relation.name, error) from None
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
    return Snapshot(tuple(tables), *_kept([*skipped, *extra], limits))


def _fetched(
    connection: "duckdb.DuckDBPyConnection", sql: str, parameters: Mapping[str, object]
) -> list[tuple[object, ...]]:
    return cast(list[tuple[object, ...]], connection.execute(sql, dict(parameters)).fetchall())


def _comment(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _relations(
    tables: Sequence[tuple[str, object]],
    columns: Iterable[_Column],
    keys: Iterable[tuple[str, str, Sequence[str], str | None, Sequence[str]]],
    only: Collection[str] = (),
) -> list[_Relation]:
    """Relations from rows of (table, comment); columns, in the database's order of each
    table's; and (table, ``p`` or ``f``, columns, parent or ``None`` for one not read, parent
    columns). ``only`` names Postgres's ordinary tables."""
    named: dict[str, list[str]] = {name: [] for name, _ in tables}
    comments: dict[str, dict[str, str | None]] = {name: {} for name, _ in tables}
    casts: dict[str, dict[str, tuple[str, ...]]] = {name: {} for name, _ in tables}
    text: dict[str, set[str]] = {name: set() for name, _ in tables}
    output: dict[str, set[str]] = {name: set() for name, _ in tables}
    for column in columns:
        if column.table in named:
            named[column.table].append(column.name)
            comments[column.table][column.name] = _comment(column.comment)
            if column.casts:
                casts[column.table][column.name] = column.casts
            if column.text:
                text[column.table].add(column.name)
            if column.output:
                output[column.table].add(column.name)
    primary: dict[str, tuple[str, ...]] = {}
    foreign: dict[str, list[ForeignKey]] = {}
    for table, kind, key_columns, parent, parent_columns in keys:
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
            casts[name],
            name in only,
            frozenset(text[name]),
            frozenset(output[name]),
        )
        for name, comment in sorted(tables, key=lambda row: row[0])
    ]


def _unknown_schema(schema: str, known: Iterable[str]) -> ImportRefused:
    alternatives: list[Segment] = [_name(name) for name in sorted(known)]
    return refused(
        RefusalCode.INVALID_VALUE,
        "The database has no schema ",
        data(schema),
        alternatives=alternatives,
    )


# --- DuckDB's catalogue ---


_DUCKDB_TABLES = (
    "SELECT table_name, comment FROM duckdb_tables() "
    "WHERE database_name = $source AND schema_name = $schema"
)
_DUCKDB_VIEWS = (
    "SELECT view_name FROM duckdb_views() WHERE database_name = $source AND schema_name = $schema"
)
_DUCKDB_SCHEMAS = "SELECT schema_name FROM duckdb_schemas() WHERE database_name = $source"
_DUCKDB_COLUMNS = (
    "SELECT table_name, column_name, comment, column_default IS NOT NULL, data_type = 'VARCHAR', "
    "data_type = 'TIME WITH TIME ZONE' "
    "FROM duckdb_columns() WHERE database_name = $source AND schema_name = $schema "
    "ORDER BY table_name, column_index"
)
_DUCKDB_KEYS = (
    "SELECT table_name, CASE constraint_type WHEN 'PRIMARY KEY' THEN 'p' ELSE 'f' END, "
    "constraint_column_names, referenced_table, referenced_column_names FROM duckdb_constraints() "
    "WHERE database_name = $source AND schema_name = $schema "
    "AND constraint_type IN ('PRIMARY KEY', 'FOREIGN KEY') ORDER BY table_name, constraint_index"
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
            raise _generated_refused(table, str(row[0]))


def _zoned_time_refused(table: str, column: str) -> ImportRefused:
    """A ``time with time zone`` column, whose offset Arrow's ``time`` drops (D309)."""
    return refused(
        RefusalCode.UNSUPPORTED_FORMAT,
        "A time with a time zone is not read, since its offset would be lost: the table ",
        _name(table),
        ", column ",
        _name(column),
        alternatives=("a time without a time zone, or a timestamp",),
    )


def _duckdb_catalog(
    connection: "duckdb.DuckDBPyConnection", schema: str
) -> tuple[list[_Relation], list[Skipped]]:
    given = {"source": SOURCE, "schema": schema}
    schemas = [str(row[0]) for row in _fetched(connection, _DUCKDB_SCHEMAS, {"source": SOURCE})]
    if schema not in schemas:
        raise _unknown_schema(schema, schemas)
    tables = [(str(name), comment) for name, comment in _fetched(connection, _DUCKDB_TABLES, given)]
    views = [
        Skipped(str(row[0]), _not_base("a view"))
        for row in _fetched(connection, _DUCKDB_VIEWS, given)
    ]
    read = {name for name, _ in tables}
    columns: list[_Column] = []
    defaulted: dict[str, set[str]] = {}
    for table, column, comment, has_default, text, zoned_time in _fetched(
        connection, _DUCKDB_COLUMNS, given
    ):
        if zoned_time and str(table) in read:
            raise _zoned_time_refused(str(table), str(column))
        columns.append(_Column(str(table), str(column), comment, text=bool(text)))
        if has_default:
            defaulted.setdefault(str(table), set()).add(str(column))
    for table, names in sorted(defaulted.items()):
        _generated(connection, schema, table, names)
    keys = (
        (
            str(table),
            str(kind),
            cast(list[str], child),
            cast(str | None, parent),
            cast(list[str], parent_columns),
        )
        for table, kind, child, parent, parent_columns in _fetched(connection, _DUCKDB_KEYS, given)
    )
    return _relations(tables, columns, keys), views


# --- Postgres's catalogue ---


_EQ = "OPERATOR(pg_catalog.=)"
_NE = "OPERATOR(pg_catalog.<>)"
_TEXT = "pg_catalog.text"
_GT = "OPERATOR(pg_catalog.>)"
_POSTGRES_SCHEMAS = (
    f"SELECT nspname::{_TEXT} FROM pg_catalog.pg_namespace "
    f"WHERE nspname::{_TEXT} {_NE} 'pg_toast' "
    f"AND nspname::{_TEXT} OPERATOR(pg_catalog.!~) '^pg_(toast_)?temp_'"
)
"""The catalogue queries of a Postgres snapshot. Each names every function, type and operator by
its schema, ``pg_catalog``, as well as resolving names in it alone (``urls.POSTGRES_OPTIONS``),
so that nothing the imported schema holds can stand in for one (D309)."""
_POSTGRES_RELATIONS = (
    f"SELECT c.relname::{_TEXT}, c.relkind::{_TEXT}, "
    "pg_catalog.obj_description(c.oid, 'pg_class'), c.relispartition, "
    f"(SELECT rn.nspname::{_TEXT} FROM pg_catalog.pg_class r "
    f"JOIN pg_catalog.pg_namespace rn ON rn.oid {_EQ} r.relnamespace "
    f"WHERE r.oid {_EQ} pg_catalog.pg_partition_root(c.oid)) AS root_schema, "
    f"c.relkind {_EQ} 'p' AND EXISTS (SELECT 1 FROM pg_catalog.pg_partition_tree(c.oid) t "
    f"JOIN pg_catalog.pg_class f ON f.oid {_EQ} t.relid WHERE f.relkind {_EQ} 'f') "
    "AS foreign_tree, "
    "c.relispartition AND EXISTS (SELECT 1 FROM pg_catalog.pg_partition_ancestors(c.oid) a "
    f"JOIN pg_catalog.pg_inherits i ON i.inhrelid {_EQ} a.relid "
    "WHERE (pg_catalog.to_jsonb(i) OPERATOR(pg_catalog.->>) 'inhdetachpending')"
    "::pg_catalog.bool) AS detaching, "
    "c.relrowsecurity OR c.relforcerowsecurity AS row_security, "
    "EXISTS (SELECT 1 FROM pg_catalog.pg_attribute g "
    f"WHERE g.attrelid {_EQ} c.oid AND g.attnum OPERATOR(pg_catalog.>) 0 "
    f"AND NOT g.attisdropped AND g.attgenerated {_EQ} 'v') AS computed "
    f"FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid {_EQ} c.relnamespace "
    f"WHERE c.relkind::{_TEXT} {_EQ} ANY (ARRAY['r', 'p', 'v', 'm', 'f']) "
    f"AND n.nspname::{_TEXT} {_EQ} {{schema}}"
)
_POSTGRES_COLUMNS = (
    f"SELECT c.relname::{_TEXT}, a.attname::{_TEXT}, "
    "pg_catalog.col_description(c.oid, a.attnum), a.atttypid::pg_catalog.int8, "
    "(SELECT tn.nspname::pg_catalog.text OPERATOR(pg_catalog.<>) 'pg_catalog' "
    "FROM pg_catalog.pg_type ty "
    f"JOIN pg_catalog.pg_namespace tn ON tn.oid {_EQ} ty.typnamespace "
    f"WHERE ty.oid {_EQ} a.atttypid) AS output "
    f"FROM pg_catalog.pg_attribute a JOIN pg_catalog.pg_class c ON c.oid {_EQ} a.attrelid "
    f"JOIN pg_catalog.pg_namespace n ON n.oid {_EQ} c.relnamespace "
    "WHERE a.attnum OPERATOR(pg_catalog.>) 0 AND NOT a.attisdropped "
    f"AND c.relkind::{_TEXT} {_EQ} ANY (ARRAY['r', 'p']) "
    f"AND NOT c.relispartition AND n.nspname::{_TEXT} {_EQ} {{schema}} "
    "ORDER BY c.relname, a.attnum"
)
_POSTGRES_DOMAINS = (
    "SELECT oid::pg_catalog.int8, typbasetype::pg_catalog.int8 FROM pg_catalog.pg_type "
    f"WHERE typtype {_EQ} 'd'"
)
_POSTGRES_ARRAYS = (
    "SELECT oid::pg_catalog.int8, typelem::pg_catalog.int8 FROM pg_catalog.pg_type "
    f"WHERE typcategory {_EQ} 'A' AND typelem {_NE} 0"
)
_POSTGRES_KEYS = (
    f"SELECT c.relname::{_TEXT} AS table_name, k.contype::{_TEXT} AS kind, "
    f"ARRAY(SELECT a.attname::{_TEXT} FROM pg_catalog.unnest(k.conkey) WITH ORDINALITY "
    "AS u(attnum, i) "
    f"JOIN pg_catalog.pg_attribute a ON a.attrelid {_EQ} k.conrelid AND a.attnum {_EQ} u.attnum "
    "ORDER BY u.i) AS columns, "
    f"pn.nspname::{_TEXT} AS parent_schema, pc.relname::{_TEXT} AS parent, "
    f"ARRAY(SELECT a.attname::{_TEXT} FROM pg_catalog.unnest(k.confkey) WITH ORDINALITY "
    "AS u(attnum, i) "
    f"JOIN pg_catalog.pg_attribute a ON a.attrelid {_EQ} k.confrelid AND a.attnum {_EQ} u.attnum "
    "ORDER BY u.i) AS parent_columns "
    f"FROM pg_catalog.pg_constraint k JOIN pg_catalog.pg_class c ON c.oid {_EQ} k.conrelid "
    f"JOIN pg_catalog.pg_namespace n ON n.oid {_EQ} c.relnamespace "
    f"LEFT JOIN pg_catalog.pg_class pc ON pc.oid {_EQ} k.confrelid "
    f"LEFT JOIN pg_catalog.pg_namespace pn ON pn.oid {_EQ} pc.relnamespace "
    f"WHERE k.contype::{_TEXT} {_EQ} ANY (ARRAY['p', 'f']) AND NOT c.relispartition "
    f"AND n.nspname::{_TEXT} {_EQ} {{schema}} "
    "ORDER BY c.relname, k.conname"
)
_POSTGRES_OWNER_CODE = (
    "WITH RECURSIVE cols AS ("
    f"SELECT c.relname::{_TEXT} AS relname, a.atttypid AS typ "
    "FROM pg_catalog.pg_class c "
    f"JOIN pg_catalog.pg_namespace n ON n.oid {_EQ} c.relnamespace "
    f"JOIN pg_catalog.pg_attribute a ON a.attrelid {_EQ} c.oid "
    f"WHERE c.relkind::{_TEXT} {_EQ} ANY (ARRAY['r', 'p']) AND NOT c.relispartition "
    f"AND n.nspname::{_TEXT} {_EQ} {{schema}} "
    f"AND a.attnum {_GT} 0 AND NOT a.attisdropped), "
    "closure AS (SELECT relname, typ FROM cols UNION "
    "SELECT cl.relname, sub.oid FROM closure cl "
    f"JOIN pg_catalog.pg_type t ON t.oid {_EQ} cl.typ "
    "JOIN LATERAL ("
    f"SELECT t.typbasetype AS oid WHERE t.typtype {_EQ} 'd' AND t.typbasetype {_NE} 0 "
    f"UNION ALL SELECT t.typelem WHERE t.typelem {_NE} 0 "
    f"UNION ALL SELECT r.rngsubtype FROM pg_catalog.pg_range r WHERE r.rngtypid {_EQ} t.oid "
    "UNION ALL SELECT m.rngtypid FROM pg_catalog.pg_range m "
    "WHERE (pg_catalog.to_jsonb(m) OPERATOR(pg_catalog.->>) 'rngmultitypid')::pg_catalog.oid "
    f"{_EQ} t.oid "
    "UNION ALL SELECT ca.atttypid FROM pg_catalog.pg_attribute ca "
    f"WHERE ca.attrelid {_EQ} t.typrelid AND t.typtype {_EQ} 'c' "
    f"AND ca.attnum {_GT} 0 AND NOT ca.attisdropped) sub ON TRUE) "
    f"SELECT DISTINCT cl.relname FROM closure cl JOIN pg_catalog.pg_type t ON t.oid {_EQ} cl.typ "
    "WHERE EXISTS (SELECT 1 FROM pg_catalog.pg_proc p "
    f"JOIN pg_catalog.pg_namespace pn ON pn.oid {_EQ} p.pronamespace "
    f"WHERE p.oid {_EQ} ANY (ARRAY[t.typoutput, t.typsend]) AND p.oid {_NE} 0 "
    f"AND pn.nspname::{_TEXT} {_NE} 'pg_catalog' "
    "AND NOT EXISTS (SELECT 1 FROM pg_catalog.pg_depend d "
    f"WHERE d.classid {_EQ} 1255 AND d.objid {_EQ} p.oid "
    f"AND d.refclassid {_EQ} 3079 AND d.deptype {_EQ} 'e'))"
)
"""The base tables of the schema whose reading would still run a function the snapshot does not
trust after the read reaches every column of a non-``pg_catalog`` type through its output function
(``_postgres_read``): a column of a type (through its domains, arrays, ranges, multiranges and
composite fields) whose own output or send function is outside ``pg_catalog`` and belongs to no
installed extension (``pg_depend`` of the function, ``pg_proc``'s class 1255, on ``pg_extension``,
class 3079, ``deptype`` ``e``), which the output function reached on the read would run. Such a
table is skipped and noted, as a row-security one is (D309). Only a superuser can make a base type
or set its I/O functions, and an extension's (``citext``, ``hstore``, ``ltree``…) was installed by
a superuser or as a trusted extension, so its type is read through its output function as an
enum's is; a schema owner's function cast, which the read no longer resolves, flags no table. A
multirange's range is found through ``pg_range.rngmultitypid``, read through ``to_jsonb`` so that
a server from before PostgreSQL 14, which has no multiranges, reads none."""
_POSTGRES_QUERY = "SELECT * FROM postgres_query($source, $query)"
_POSTGRES_SKIPPED = {"v": "a view", "m": "a materialized view", "f": "a foreign table"}
_OTHER_ROOT = "a partition of a table of another schema, which is not read"
_FOREIGN_PARTITION = (
    "a partitioned table with a foreign partition, whose rows lie outside the snapshot, which is "
    "not read"
)
"""A partitioned table whose partition tree holds a foreign table: its rows would be read through
the foreign server, outside the snapshot's transaction and from a database the provenance does
not name (D309). The foreign partition is noted as a foreign table."""
_DETACHING = (
    "a partition being detached, or a partition of one, whose rows its table no longer holds, "
    "which is not read"
)
"""A partition, or a partition beneath one, that a ``DETACH PARTITION … CONCURRENTLY`` left
pending (``pg_inherits.inhdetachpending``, read through ``to_jsonb`` so that a server from
before PostgreSQL 14, which has no such column, reads none): its table's rows no longer include
its own (D309)."""


_ROW_SECURITY = (
    "a table with row security, whose rows would be those its owner's policies choose, which is "
    "not read"
)
"""A table with row security on (``relrowsecurity`` or ``relforcerowsecurity``): its policies
are its owner's expressions, which reading it would run as the import's user and which choose
the rows it gives (D309). The session's ``row_security`` is off as well
(``urls.POSTGRES_OPTIONS``), so that any read a policy would filter fails."""
_COMPUTED = (
    "a table with a virtual generated column, whose values its owner's expression would compute "
    "as it is read, which is not read"
)
"""A table with a virtual generated column (``attgenerated`` ``v``, from PostgreSQL 18), whose
expression, its owner's, runs as the table is read (D309)."""
_OWNER_CAST = (
    "a table with a column of a base type whose output or send function is neither pg_catalog's "
    "nor an installed extension's, which reading it would run, which is not read"
)
"""A table a column of which reaches a base type whose own output or send function is outside
``pg_catalog`` and belongs to no installed extension: reading the column runs that function,
through the output function the read reaches, as the import's role, so the table is skipped and
noted (D309). A superuser made such a type outside any extension, so the snapshot does not trust
its code; an extension's type is read, and a schema owner's function cast is not this, since the
read resolves no cast (``_postgres_read``)."""
_TIMETZ = 1266
"""``time with time zone``: read as its exact text, since Arrow's ``time`` has no zone and drops
the offset, collapsing distinct values (D309)."""


def _postgres_quoted(name: str) -> str:
    """``name`` as a Postgres identifier: double quotes, each ``"`` doubled, which no server
    setting reads otherwise (identifier quoting honours no escape), so that a name from the
    catalogue cannot be quoted wrongly (D309)."""
    return '"' + name.replace('"', '""') + '"'


def _postgres_lock(schema: str, table: str, only: bool) -> str:
    """A ``SELECT`` that takes ``ACCESS SHARE`` on a base table read, in the snapshot's
    transaction, before its columns are read, so that a ``RENAME`` cannot commit between the
    catalogue read and the row read and mislabel a column (D306). The table is referenced as it
    is read: an ordinary one ``ONLY`` (``only``), so that its inheritance children, which are not
    read, are not locked, and a partitioned one with its partitions, each of which the planner
    locks since ``LIMIT 0`` prunes none. It returns one row, a zero."""
    named = f"{'ONLY ' if only else ''}{_postgres_quoted(schema)}.{_postgres_quoted(table)}"
    return f"SELECT pg_catalog.count(*) FROM (SELECT FROM {named} LIMIT 0) AS l"


def _postgres_waits(limits: ImportLimits) -> str:
    """A ``SELECT`` that bounds, for the rest of the snapshot's transaction, how long the server
    waits for a lock (``lock_timeout``) to half of ``reader_seconds``, below the worker's
    deadline, so that a session the worker leaves when it is stopped does not wait on, holding
    the locks it took, until whoever holds the lock it waits for lets go (D306); and, on a
    server from PostgreSQL 14 not on Windows, where it can, how often it checks that its client
    is still there (``client_connection_check_interval``). Set as the snapshot starts, since the
    limits reach only the worker."""
    waits = f"{limits.reader_seconds * _WAIT_SHARE}"
    return (
        f"SELECT pg_catalog.set_config('lock_timeout', '{waits}', true) AS waits, "
        "(SELECT pg_catalog.set_config(s.name, "
        f"'{_CHECKED_EVERY}', true) FROM pg_catalog.pg_settings s "
        f"WHERE s.name {_EQ} 'client_connection_check_interval' "
        "AND pg_catalog.version() OPERATOR(pg_catalog.!~*) 'windows|mingw|cygwin|visual c') "
        "AS checks"
    )


_WAIT_SHARE = 500
"""The milliseconds a Postgres snapshot waits for a lock for each second of ``reader_seconds``:
half of them."""
_CHECKED_EVERY = 1000
"""The milliseconds between a Postgres session's checks that its client is still there."""


def _lock_refused(table: str, error: BaseException, limits: ImportLimits) -> ImportRefused:
    """A lock on ``table`` the snapshot waited for past ``lock_timeout``, which the server
    names so, or what else the server raised taking it (D306)."""
    if "lock timeout" not in str(error):
        return _unreadable(table, error)
    return refused(
        RefusalCode.LIMIT_EXCEEDED,
        "The table ",
        _name(table),
        f" cannot be read: another session held a lock on it for more than "
        f"{limits.reader_seconds * _WAIT_SHARE / 1000:g} seconds",
        limit=(READER_SECONDS, limits.reader_seconds),
    )


def _postgres_name(name: str) -> str:
    """A Postgres expression whose value is ``name``, written as the hexadecimal digits of its
    UTF-8, which no setting of the server's (``standard_conforming_strings``, say) reads
    otherwise, so that the catalogue is filtered on the server by a name that cannot be quoted
    wrongly (D309)."""
    return (
        f"pg_catalog.convert_from(pg_catalog.decode('{name.encode('utf-8').hex()}', 'hex'), 'UTF8')"
    )


def _postgres_base(type_id: int, domains: Mapping[int, int]) -> int:
    """The type a Postgres domain is declared over, through the domains it is declared by."""
    seen: set[int] = set()
    while type_id in domains and type_id not in seen:
        seen.add(type_id)
        type_id = domains[type_id]
    return type_id


def _postgres_casts(
    type_id: int, domains: Mapping[int, int], arrays: Mapping[int, int]
) -> tuple[str, ...]:
    """The types a Postgres column of the type ``type_id`` is cast to on the server, through the
    domains it is declared by, each's base type by its OID, and an array's to its element's as
    an array (D309): a ``numeric`` can hold ``NaN``, and without a precision ``Infinity`` and
    more than ``EXACT_DIGITS`` digits, none of which a DuckDB decimal holds, and a ``money`` is
    text in the server's locale's format; an array of either, or of a ``timetz``, is read as a
    list of text."""
    base = _postgres_base(type_id, domains)
    element = _postgres_base(arrays[base], domains) if base in arrays else None
    if base == _TIMETZ:
        return ("text",)
    if base == _NUMERIC:
        return ("text",)
    if element == _TIMETZ:
        return ("text[]",)
    if base == _MONEY:
        return ("numeric", "text")
    if element == _NUMERIC:
        return ("text[]",)
    if element == _MONEY:
        return ("numeric[]", "text[]")
    return ()


def _postgres_catalog(
    connection: "duckdb.DuckDBPyConnection", schema: str, limits: ImportLimits
) -> tuple[list[_Relation], list[Skipped]]:
    import duckdb

    def query(sql: str) -> list[tuple[object, ...]]:
        return _fetched(connection, _POSTGRES_QUERY, {"source": SOURCE, "query": sql})

    query(_postgres_waits(limits))
    schemas = [str(row[0]) for row in query(_POSTGRES_SCHEMAS)]
    if schema not in schemas:
        raise _unknown_schema(schema, schemas)
    named = _postgres_name(schema)
    owner_code = {str(row[0]) for row in query(_POSTGRES_OWNER_CODE.format(schema=named))}
    tables: list[tuple[str, object]] = []
    ordinary: set[str] = set()
    skipped: list[Skipped] = []
    relations = query(_POSTGRES_RELATIONS.format(schema=named))
    for name, kind, comment, partition, root, foreign, detaching, secured, computed in relations:
        if detaching:
            skipped.append(Skipped(str(name), _DETACHING))
            continue
        if partition and root != schema:
            skipped.append(Skipped(str(name), _OTHER_ROOT))
            continue
        if partition and kind != "f":
            continue
        if foreign:
            skipped.append(Skipped(str(name), _FOREIGN_PARTITION))
            continue
        if kind in ("r", "p") and secured:
            skipped.append(Skipped(str(name), _ROW_SECURITY))
            continue
        if kind in ("r", "p") and computed:
            skipped.append(Skipped(str(name), _COMPUTED))
            continue
        if kind in ("r", "p") and str(name) in owner_code:
            skipped.append(Skipped(str(name), _OWNER_CAST))
            continue
        if kind in ("r", "p"):
            tables.append((str(name), comment))
            if kind == "r":
                ordinary.add(str(name))
        else:
            skipped.append(Skipped(str(name), _not_base(_POSTGRES_SKIPPED[str(kind)])))
    for name, _ in tables:
        try:
            query(_postgres_lock(schema, name, name in ordinary))
        except duckdb.OutOfMemoryException:
            raise
        except duckdb.Error as error:
            raise _lock_refused(name, error, limits) from None
    domains = {
        int(cast(int, domain)): int(cast(int, base)) for domain, base in query(_POSTGRES_DOMAINS)
    }
    arrays = {
        int(cast(int, array)): int(cast(int, element)) for array, element in query(_POSTGRES_ARRAYS)
    }
    columns = (
        _Column(
            str(table),
            str(column),
            comment,
            () if output else _postgres_casts(int(cast(int, type_id)), domains, arrays),
            output=bool(output),
        )
        for table, column, comment, type_id, output in query(_POSTGRES_COLUMNS.format(schema=named))
    )
    keys = (
        (
            str(table),
            str(kind),
            cast(list[str], child),
            None if parent_space != schema else cast(str | None, parent),
            cast(list[str], parent_columns),
        )
        for table, kind, child, parent_space, parent, parent_columns in query(
            _POSTGRES_KEYS.format(schema=named)
        )
    )
    return _relations(tables, columns, keys, ordinary), skipped


# --- MySQL's catalogue ---


_MYSQL_QUERY = "SELECT * FROM mysql_query($source, $query)"
_MYSQL_LEVELS = ("SELECT @@SESSION.transaction_isolation", "SELECT @@SESSION.tx_isolation")
"""The session's isolation level, as MySQL (from 8.0) and MariaDB (before 11.1) each name it."""
_MYSQL_TABLES = (
    f"SELECT t.TABLE_NAME, t.TABLE_TYPE, t.TABLE_COMMENT, e.TRANSACTIONS "
    f"FROM {SOURCE}.information_schema.TABLES AS t "
    f"LEFT JOIN {SOURCE}.information_schema.ENGINES AS e ON e.ENGINE = t.ENGINE "
    "WHERE t.TABLE_SCHEMA = $database"
)
_MYSQL_COLUMNS = (
    "SELECT TABLE_NAME, COLUMN_NAME, COLUMN_COMMENT, DATA_TYPE, NUMERIC_PRECISION "
    f"FROM {SOURCE}.information_schema.COLUMNS "
    "WHERE TABLE_SCHEMA = $database ORDER BY TABLE_NAME, ORDINAL_POSITION"
)
_MYSQL_KEYS = (
    "SELECT TABLE_NAME, CONSTRAINT_NAME, COLUMN_NAME, REFERENCED_TABLE_SCHEMA, "
    "REFERENCED_TABLE_NAME, REFERENCED_COLUMN_NAME "
    f"FROM {SOURCE}.information_schema.KEY_COLUMN_USAGE "
    "WHERE TABLE_SCHEMA = $database "
    "AND (CONSTRAINT_NAME = 'PRIMARY' OR REFERENCED_TABLE_NAME IS NOT NULL) "
    "ORDER BY TABLE_NAME, CONSTRAINT_NAME, ORDINAL_POSITION"
)
_MYSQL_BASE = frozenset({"BASE TABLE", "SYSTEM VERSIONED"})
"""The ``TABLE_TYPE`` of a base table: MariaDB's system-versioned tables are read as they are
now."""
_MYSQL_SKIPPED = {"VIEW": "a view", "SYSTEM VIEW": "a system view", "SEQUENCE": "a sequence"}
_MYSQL_SCHEMA_WEIGHTS = (
    "SELECT SCHEMA_NAME, HEX(WEIGHT_STRING(SCHEMA_NAME)), 'BASE TABLE' "
    "FROM information_schema.SCHEMATA"
)
_MYSQL_TABLE_WEIGHTS = (
    "SELECT TABLE_NAME, HEX(WEIGHT_STRING(TABLE_NAME)), TABLE_TYPE FROM information_schema.TABLES "
    "WHERE HEX(TABLE_SCHEMA) = '{schema}'"
)
"""The names of the server's databases, or of a database's relations, each with its weight under
the collation of its column, by which the server compares them; the database is named by the
hexadecimal digits of its UTF-8, which no quoting can get wrong."""


def _collided(what: str, name: str, weighed: Sequence[tuple[str, str]]) -> None:
    """Refuse ``name`` if another of ``weighed`` (names and their weights) equals it under the
    server's collation, which may ignore case and accents (MariaDB's ``utf8mb3_general_ci``
    does), or ignoring the case of ASCII letters, as DuckDB's catalogue matches names (D308)."""
    mine = {weight for found, weight in weighed if found == name}
    same = sorted(
        {
            found
            for found, weight in weighed
            if found == name or weight in mine or ascii_folded(found) == ascii_folded(name)
        }
    )
    if len(same) > 1:
        named: list[Segment | str] = []
        for place, other in enumerate(same):
            named.extend([", ", _name(other)] if place else [_name(other)])
        raise refused(
            RefusalCode.UNSUPPORTED_FORMAT,
            f"The server has {what} whose names are equal under the server's collation, or "
            "ignoring the case of ASCII letters as the MySQL reader's catalogue does, which it "
            "cannot tell apart: ",
            *named,
        )


def _mysql_collisions(connection: "duckdb.DuckDBPyConnection", database: str) -> None:
    """Refuse the database, or a base table of it, whose name another's equals as ``_collided``
    compares them: the server's weights are read by ``mysql_query``, before the snapshot's
    transaction starts (D308). The URL's database must be one of the server's names exactly, as
    ``SCHEMATA`` gives them, which a server that folds names' case (``lower_case_table_names``
    1 or 2) may give otherwise than the URL writes them."""

    def weighed(sql: str) -> list[tuple[str, str, str]]:
        return [
            (str(name), f"={name}" if weight is None else str(weight), str(kind))
            for name, weight, kind in _fetched(
                connection, _MYSQL_QUERY, {"source": SOURCE, "query": sql}
            )
        ]

    schemas = weighed(_MYSQL_SCHEMA_WEIGHTS)
    if database not in {name for name, _, _ in schemas}:
        raise refused(
            RefusalCode.INVALID_VALUE,
            "The server has no database whose name is exactly the one the connection's URL names, ",
            _name(database),
            alternatives=[
                _name(name)
                for name in sorted({name for name, _, _ in schemas})
                if name.casefold() == database.casefold()
            ],
        )
    _collided("databases", database, [(name, weight) for name, weight, _ in schemas])
    hexed = database.encode("utf-8").hex().upper()
    relations = weighed(_MYSQL_TABLE_WEIGHTS.format(schema=hexed))
    pairs = [(name, weight) for name, weight, _ in relations]
    for name, _, kind in relations:
        if kind in _MYSQL_BASE:
            _collided("relations", name, pairs)


def _mysql_isolation(connection: "duckdb.DuckDBPyConnection") -> None:
    """Refuse a MySQL session whose transactions read no snapshot, whose level ``mysql_query``
    reads before the snapshot's transaction starts, on the one pooled connection that then holds
    it (D308)."""
    import duckdb

    level: str | None = None
    failed: duckdb.Error | None = None
    for sql in _MYSQL_LEVELS:
        try:
            found = _fetched(connection, _MYSQL_QUERY, {"source": SOURCE, "query": sql})
        except duckdb.OutOfMemoryException:
            raise
        except duckdb.Error as error:
            failed = error
            continue
        level = str(found[0][0]).replace("-", " ")
        break
    if level is None:
        raise refused(
            RefusalCode.UNPARSEABLE_SOURCE,
            f"The MySQL session's isolation level cannot be read ({type(failed).__name__})",
        )
    if level not in MYSQL_ISOLATION:
        raise refused(
            RefusalCode.UNSUPPORTED_FORMAT,
            "The MySQL session's transactions read no snapshot, at the isolation level ",
            data(escaped(level)),
            alternatives=MYSQL_ISOLATION,
        )


_UNQUOTABLE = (
    "holds a character DuckDB's MySQL scanner cannot quote safely in what it sends the server (a "
    "backtick, a backslash or a control character), which could make it read rows from "
    "elsewhere"
)


def _quotable(name: str, table: str | None = None) -> None:
    """Refuse a base table's name, or its column's, that DuckDB's MySQL scanner cannot quote
    (``urls.mysql_quotable``), before any row is read (D308)."""
    if mysql_quotable(name):
        return
    if table is None:
        raise refused(
            RefusalCode.UNSUPPORTED_FORMAT,
            "The name of the table ",
            _name(name),
            f" {_UNQUOTABLE}",
        )
    raise refused(
        RefusalCode.UNSUPPORTED_FORMAT,
        "The name of the table ",
        _name(table),
        ", column ",
        _name(name),
        f", {_UNQUOTABLE}",
    )


def _mysql_tables(
    connection: "duckdb.DuckDBPyConnection", database: str
) -> tuple[list[tuple[str, object]], list[Skipped]]:
    """The base tables of ``database`` and its other relations, skipped, once none of them is
    refused (D308): every base table's name must be one the scanner quotes safely, which it writes
    into what it sends the server, as it never writes a view's or a sequence's."""
    tables: list[tuple[str, object]] = []
    skipped: list[Skipped] = []
    listed = _fetched(connection, _MYSQL_TABLES, {"database": database})
    for name in sorted(str(row[0]) for row in listed if row[1] in _MYSQL_BASE):
        _quotable(name)
    for name, kind, comment, transactions in listed:
        if kind not in _MYSQL_BASE:
            what = _MYSQL_SKIPPED.get(str(kind), "a relation of another kind")
            skipped.append(Skipped(str(name), _not_base(what)))
            continue
        if transactions != "YES":
            raise refused(
                RefusalCode.UNSUPPORTED_FORMAT,
                "The table ",
                _name(str(name)),
                " is kept by an engine without transactions, whose rows no snapshot holds",
                alternatives=("tables of an engine with transactions, such as InnoDB",),
            )
        tables.append((str(name), comment))
    return tables, skipped


def _mysql_catalog(
    connection: "duckdb.DuckDBPyConnection", database: str
) -> tuple[list[_Relation], list[Skipped]]:
    given = {"database": database}
    tables, skipped = _mysql_tables(connection, database)
    read = {name for name, _ in tables}
    columns: list[_Column] = []
    for table, column, comment, kind, precision in _fetched(connection, _MYSQL_COLUMNS, given):
        if str(table) in read:
            _quotable(str(column), str(table))
        if (
            str(table) in read
            and str(kind).lower() == "decimal"
            and int(cast(int, precision)) > EXACT_DIGITS
        ):
            raise refused(
                RefusalCode.UNSUPPORTED_FORMAT,
                f"A decimal of more than {EXACT_DIGITS} digits is not read: the table ",
                _name(str(table)),
                ", column ",
                _name(str(column)),
                alternatives=(f"decimals of at most {EXACT_DIGITS} digits",),
            )
        columns.append(_Column(str(table), str(column), comment))
    grouped: dict[tuple[str, str], list[tuple[object, ...]]] = {}
    for row in _fetched(connection, _MYSQL_KEYS, given):
        grouped.setdefault((str(row[0]), str(row[1])), []).append(row)

    def keys() -> Iterator[tuple[str, str, Sequence[str], str | None, Sequence[str]]]:
        for (table, name), rows in grouped.items():
            child = [str(row[2]) for row in rows]
            if name == "PRIMARY":
                yield table, "p", child, None, ()
                continue
            same = all(row[3] == database for row in rows)
            parent = str(rows[0][4]) if same else None
            yield table, "f", child, parent, [str(row[5]) for row in rows]

    return _relations(tables, columns, keys()), skipped


__all__ = [
    "DATABASE_TYPES",
    "EXTENSIONS",
    "KINDS",
    "MYSQL_ISOLATION",
    "SERVERS",
    "SQLITE_VALUES",
    "ForeignKey",
    "Kind",
    "Skipped",
    "Snapshot",
    "SnapshotTable",
    "Target",
    "ascii_folded",
    "extension_path",
    "read_snapshot",
]

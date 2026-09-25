"""The query engine's DuckDB sessions, and the query worker's child process (SPEC §12.2, §14;
D293).

A session is an in-memory database whose configuration is set once and locked before any query
runs: extensions are neither installed nor loaded on demand (the Parquet reader is built into
the wheel), the only files it may open are the ones it is given (the document's table blobs),
external access is otherwise disabled, nothing spills to a temporary directory (a query that
needs more memory than ``memory`` bytes fails), the time zone is UTC, and it runs ``threads``
threads. ``run`` executes statements with their bound parameters and returns their rows and
their number of columns; each
statement must be one ``SELECT``, as DuckDB's own parser reads it, so that nothing a session runs
writes a file, not even one of the files it may read.

``serve`` is the query worker's child (``worker``): it asks the kernel to kill it first when
memory runs out, limits its CPU time and address space, writes no core file (which would hold
the rows it read), reads one request, runs it in a session
and answers with the rows packed as integers, column by column; its parent watches its resident
memory. A failed query is answered with its error's class alone, never DuckDB's message, which
can hold paths, SQL and values. This module imports nothing of aibi's, so that the child loads
DuckDB and this alone, and the server's process never loads DuckDB.
"""

import contextlib
import os
import pickle
import re
import resource
import signal
import socket
import struct
import sys
from array import array
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import cast

import duckdb

_PR_SET_PDEATHSIG = 1
_KIND = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,63}")
WIDTHS = ("b", "h", "i", "q")
"""The array type codes an answer's columns are packed in, narrowest first."""
VALUE, MEMORY, TOO_LARGE, ERROR = b"V", b"M", b"A", b"E"
"""The first byte of an answer: rows, out of memory, an answer larger than it may be, or an
error, followed by its class's name."""


@dataclass(frozen=True)
class Session:
    """A session's settings."""

    paths: tuple[str, ...]
    """The files queries may read, and no other."""
    memory: int
    """DuckDB's memory limit, in bytes."""
    threads: int = 1


@dataclass(frozen=True)
class Statement:
    """One query and its parameters, by name."""

    sql: str
    parameters: Mapping[str, object]


@dataclass(frozen=True)
class Result:
    """A statement's rows, and how many columns they have, which an answer states even for no
    rows."""

    columns: int
    rows: list[tuple[object, ...]]


class OutOfMemory(Exception):  # noqa: N818 - a limit reached, as DuckDB reports it
    """DuckDB could not allocate what a query needed within its memory limit."""


class NotAQuery(Exception):  # noqa: N818 - the class's name is what a failed run reports
    """A statement that is not one ``SELECT``."""


class NotIntegers(Exception):  # noqa: N818 - the class's name is what a failed run reports
    """Rows holding a value that is not a 64-bit integer, which an answer cannot carry."""


def connect(session: Session) -> duckdb.DuckDBPyConnection:
    """A new in-memory database, configured as the session says and locked."""
    connection = duckdb.connect(
        ":memory:",
        config={
            "autoinstall_known_extensions": False,
            "autoload_known_extensions": False,
            "threads": session.threads,
            "memory_limit": f"{session.memory}B",
            "temp_directory": "",
            "preserve_insertion_order": False,
        },
    )
    try:
        connection.execute("SET TimeZone = 'UTC'")
        if session.paths:
            connection.execute("SET allowed_paths = $paths", {"paths": list(session.paths)})
        connection.execute("SET enable_external_access = false")
        connection.execute("SET lock_configuration = true")
    except BaseException:
        connection.close()
        raise
    return connection


def _bound(parameters: Mapping[str, object]) -> dict[str, object]:
    """Parameters as DuckDB binds them: a tuple as a list."""
    return {
        name: list(cast(tuple[object, ...], value)) if isinstance(value, tuple) else value
        for name, value in parameters.items()
    }


def run(session: Session, statements: Sequence[Statement]) -> list[Result]:
    """Each statement's result, in one session. Raises ``NotAQuery`` for a statement that is not
    one ``SELECT``, ``OutOfMemory`` when a query runs out of memory; any other DuckDB error
    propagates."""
    connection = connect(session)
    try:
        found: list[Result] = []
        for statement in statements:
            parsed = connection.extract_statements(statement.sql)
            if len(parsed) != 1 or parsed[0].type != duckdb.StatementType.SELECT:
                raise NotAQuery
            try:
                cursor = connection.execute(statement.sql, _bound(statement.parameters))
                rows = [tuple(row) for row in cursor.fetchall()]
                found.append(Result(len(cursor.description or ()), rows))
            except duckdb.OutOfMemoryException:
                raise OutOfMemory from None
        return found
    finally:
        connection.close()


# --- The query worker's child ------------------------------------------------------------------


def _limit(kind: int, soft: int, hard: int) -> None:
    """Set a resource limit of this process, never above its hard limit."""
    _, most = resource.getrlimit(kind)
    if most != resource.RLIM_INFINITY:
        soft, hard = min(soft, most), min(hard, most)
    resource.setrlimit(kind, (soft, hard))


def _die_with(parent: int) -> None:
    """Be killed when the server dies (Linux), and end now if it already has."""
    if sys.platform.startswith("linux"):
        import ctypes  # only the child calls it

        try:
            libc = ctypes.CDLL(None, use_errno=True)
            libc.prctl(_PR_SET_PDEATHSIG, signal.SIGKILL, 0, 0, 0)
        except (OSError, AttributeError):
            pass
    if os.getppid() != parent:
        os._exit(1)


def _first_to_go() -> None:
    """Ask the kernel's OOM killer to pick this process before any other (Linux)."""
    with contextlib.suppress(OSError), open("/proc/self/oom_score_adj", "w") as adjustment:
        adjustment.write("1000")


def _exactly(sock: socket.socket, size: int) -> bytes | None:
    buffer = bytearray(size)
    view = memoryview(buffer)
    while view:
        received = sock.recv_into(view)
        if not received:
            return None
        view = view[received:]
    return bytes(buffer)


def _frame(sock: socket.socket) -> bytes | None:
    """The parent's next frame, an 8-byte length and its bytes; ``None`` once it closed."""
    header = _exactly(sock, 8)
    if header is None:
        return None
    (size,) = struct.unpack("!Q", header)
    return _exactly(sock, size)


def _packed(column: Sequence[object]) -> array[int]:
    """A column of integers in the narrowest array type that holds it."""
    for value in column:
        if type(value) is not int and type(value) is not bool:
            raise NotIntegers
    numbers = cast(Sequence[int], column)
    low, high = (min(numbers), max(numbers)) if numbers else (0, 0)
    for code in WIDTHS:
        bits = array(code).itemsize * 8
        if -(1 << (bits - 1)) <= low and high < 1 << (bits - 1):
            return array(code, numbers)
    raise NotIntegers


def answer(results: Sequence[Result], most: int) -> bytes:
    """The rows as an answer: ``VALUE``, the number of results, and for each its row and column
    counts and each column's type code and values (a type code alone for no rows);
    ``TOO_LARGE`` past ``most`` bytes."""
    parts = [VALUE, struct.pack("!I", len(results))]
    size = len(VALUE) + 4
    for result in results:
        rows = result.rows
        parts.append(struct.pack("!QI", len(rows), result.columns))
        size += 12
        for index in range(result.columns):
            packed = _packed([row[index] for row in rows])
            size += 1 + len(packed) * packed.itemsize
            if size > most:
                return TOO_LARGE
            parts += [packed.typecode.encode("ascii"), packed.tobytes()]
    return b"".join(parts)


def _failed(error: BaseException) -> bytes:
    """An error answer: its class's name, which holds no path, SQL or value."""
    name = type(error).__name__
    return ERROR + (name if _KIND.fullmatch(name) else "Error").encode("ascii")


def serve(descriptor: int, parent: int) -> None:
    """The child, on its end of the parent's socket: its limits (``(cpu, hard cpu, address
    space)``), then one request (``Session`` fields, the largest answer, and ``(sql,
    parameters)`` pairs), answered as ``answer`` packs rows, or with ``MEMORY``, ``TOO_LARGE``
    or ``ERROR``."""
    _die_with(parent)
    _first_to_go()
    sock = socket.socket(fileno=descriptor)
    given = _frame(sock)
    if given is None:
        return
    cpu, hard, address = cast(tuple[int, int, int], pickle.loads(given))
    _limit(resource.RLIMIT_CPU, cpu, hard)
    _limit(resource.RLIMIT_AS, address, address)
    _limit(resource.RLIMIT_CORE, 0, 0)
    written: bytes
    try:
        request = _frame(sock)
        if request is None:
            return
        paths, limit, threads, most, queries = cast(
            tuple[tuple[str, ...], int, int, int, list[tuple[str, dict[str, object]]]],
            pickle.loads(request),
        )
        del request
        statements = [Statement(sql, parameters) for sql, parameters in queries]
        written = answer(run(Session(paths, limit, threads), statements), most)
    except (MemoryError, OutOfMemory):
        written = MEMORY
    except Exception as error:
        written = _failed(error)
    sock.sendall(struct.pack("!Q", len(written)))
    sock.sendall(written)


__all__ = [
    "NotAQuery",
    "NotIntegers",
    "OutOfMemory",
    "Result",
    "Session",
    "Statement",
    "answer",
    "connect",
    "run",
    "serve",
]

"""Queries in worker processes that can be killed (SPEC §14; D293).

A query runs over data the server does not bound: what DuckDB allocates depends on the rows, and
a query cannot be stopped from inside once it runs. So queries never run in the server's
process, which never loads DuckDB. Each run of a document's queries (``Workers.run``) starts a
child process of its own (``duck.serve``), sends it the queries, reads their rows and stops it:

- The child is a fresh interpreter (never a fork of the server, which holds threads and locks),
  in a session of its own, with ``-P`` (the working directory is not on its path), standard
  streams on ``/dev/null``, and an environment holding ``PATH``, ``PYTHONPATH`` and a UTF-8
  locale only. It imports ``duck``, which imports DuckDB and nothing of aibi's; nothing of the
  server's state reaches it but the limits and the queries: SQL the compiler rendered, never a
  client's, its parameters by name, and the table blobs it may read, which are the only files
  its session may open, each ``SELECT`` alone.
- Before it reads the queries it asks the kernel's OOM killer to pick it first, limits its CPU
  time to ``query_seconds`` times ``query_threads`` and ``CPU_SLACK`` seconds more (``SIGXCPU``;
  ``SIGKILL`` ``CPU_GRACE`` seconds later), and its address space to ``ADDRESS_FACTOR`` times
  ``query_memory`` and ``ADDRESS_SLACK`` more, a backstop far above what it uses, as DuckDB's
  allocator reserves address space it never touches; it writes no core file, which would hold
  the rows it read; on Linux it asks to be killed when the server dies. DuckDB runs with half
  of ``query_memory`` as its memory limit, locked (``duck.Session``), and reports running out
  of it itself. The server watches the child's
  resident memory while it waits (every ``POLL`` seconds, from ``/proc``) and kills it once it
  holds more than ``query_memory`` bytes, which bounds what DuckDB does not count (lists it
  aggregates, the answer); a child can overshoot by what it touches between two looks. Without
  ``/proc`` there is no watch, and no worker runs: ``Workers`` refuses to start, and a run whose
  child cannot be watched is stopped.
- At most ``query_workers`` children run at once in a server; a run waits for its place at most
  ``query_seconds`` (``LIMIT_EXCEEDED`` naming ``query_workers``). A run's deadline is
  ``query_seconds`` from when it has its place, its start included; past it the child's whole
  process group is killed (``LIMIT_EXCEEDED`` naming ``query_seconds``). A caller's own deadline
  (``ends``, a tool call's) ends the wait and the run sooner, so that no run outlives its call,
  its child killed as well; that is ``CallerDeadline``, which the caller answers by its own
  limit, as no limit of the worker's was hit. A deadline already past starts no child.
- The answer is one frame of at most ``MAX_QUERY_ANSWER_BYTES`` (``LIMIT_EXCEEDED`` naming
  ``query_answer_bytes`` when the child finds its rows larger), read and parsed under the
  deadline into a buffer of its size: integers packed column by column, which the server reads
  in place (``Rows``), making no object per row until one is read. Each query says how many
  columns its rows have (``Query.columns``, at least one), and a result of another number of
  columns is a fault, so that what the server makes of a frame is bounded by the queries it
  sent, not by what the child claims. A frame of another shape, or a larger one, is a fault.
- The child's whole process group is killed, and a process it started that left the group (a
  session of its own) would not be: nothing a child runs starts one, as its session loads no
  extension and runs one ``SELECT`` per statement.

Running out of memory is ``LIMIT_EXCEEDED`` naming ``query_memory``: DuckDB's own report, a
``MemoryError``, the watch finding the child over ``query_memory``, or a child killed
(``SIGKILL``, as the kernel's OOM killer does) or aborted (``SIGABRT``, as a failed allocation in
C++ ends). A child that closes its socket is waited for, until the deadline, and judged by how
it ended. Running out of CPU time (``SIGXCPU``) is ``query_seconds``. Any other end of the child
before it answers, or an error of DuckDB's, is a fault of the compiler or the engine, raised as
``QueryError`` naming the error's class alone: DuckDB's messages can hold paths, SQL and values.
The worker bounds what a query makes the engine consume; it is not a privilege boundary.
"""

import contextlib
import os
import pickle
import re
import signal
import socket
import struct
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, overload

import aibi
from aibi.core.schema.limits import (
    MAX_QUERY_ANSWER_BYTES,
    QUERY_ANSWER_BYTES,
    QUERY_MEMORY,
    QUERY_SECONDS,
    QUERY_WORKERS,
    QueryLimits,
)
from aibi.core.schema.output import text
from aibi.core.schema.refusals import Limit, Refusal, RefusalCode

_CHUNK = 1 << 20
POLL = 0.05
"""Seconds between two looks at a child's resident memory."""
EXIT_POLL = 0.005
"""Seconds between two looks for the end of a child that closed its socket."""
CPU_SLACK = 5
"""CPU seconds a child has beyond its share of the deadline, which the deadline stops first."""
CPU_GRACE = 2
"""Seconds of CPU time between ``SIGXCPU`` and ``SIGKILL``."""
ADDRESS_FACTOR = 4
ADDRESS_SLACK = 1 << 30
_CHILD = (
    "import sys; from aibi.core.engine.duck import serve; serve(int(sys.argv[1]), int(sys.argv[2]))"
)
_VALUE, _MEMORY, _TOO_LARGE, _ERROR = b"V", b"M", b"A", b"E"
"""The first byte of an answer, as ``duck.answer`` writes it."""
_WIDTHS: dict[int, Literal["b", "h", "i", "q"]] = {
    ord("b"): "b",
    ord("h"): "h",
    ord("i"): "i",
    ord("q"): "q",
}
"""The type codes of an answer's columns, by their byte."""
_KIND = re.compile(rb"[A-Za-z][A-Za-z0-9_]{0,63}")


class QueryRefused(Exception):  # noqa: N818 - the spec's word
    """A run of queries that hit a limit."""

    def __init__(self, refusal: Refusal) -> None:
        super().__init__(f"{refusal.code}: {refusal.limit.name if refusal.limit else ''}")
        self.refusal = refusal


class QueryError(RuntimeError):
    """A query failed as no limit explains: a fault of the compiler or the engine. Its message
    is the worker's own, never DuckDB's."""


class CallerDeadline(Exception):  # noqa: N818 - a deadline, not an error of the query's
    """The caller's own deadline (``ends``) ended the wait for a place or the run before the
    worker's limits did, or had passed before it began; the caller answers it, naming its own
    limit, since no limit of the worker's was hit."""


@dataclass(frozen=True)
class Query:
    """One statement for a worker: SQL the compiler rendered, its parameters by name, and how
    many columns its rows have, which its answer must hold (D293)."""

    sql: str
    parameters: Mapping[str, object]
    columns: int

    def __post_init__(self) -> None:
        if self.columns < 1:
            raise ValueError("a query's rows have at least one column")


class Rows(Sequence[tuple[int, ...]]):
    """A query's rows as the answer holds them, columns of integers packed in place; each row
    is made as it is read."""

    __slots__ = ("_columns", "_count")

    def __init__(self, columns: Sequence[memoryview], count: int) -> None:
        self._columns = tuple(columns)
        self._count = count

    @property
    def columns(self) -> tuple[memoryview, ...]:
        """The columns as the answer packs them, each ``len(self)`` integers long."""
        return self._columns

    def __len__(self) -> int:
        return self._count

    @overload
    def __getitem__(self, index: int) -> tuple[int, ...]: ...

    @overload
    def __getitem__(self, index: slice) -> Sequence[tuple[int, ...]]: ...

    def __getitem__(self, index: int | slice) -> tuple[int, ...] | Sequence[tuple[int, ...]]:
        if isinstance(index, slice):
            return [self[at] for at in range(*index.indices(self._count))]
        if index < 0:
            index += self._count
        if not 0 <= index < self._count:
            raise IndexError(index)
        return tuple(int(column[index]) for column in self._columns)

    def __iter__(self) -> Iterator[tuple[int, ...]]:
        for index in range(self._count):
            yield tuple(int(column[index]) for column in self._columns)


def _refused(message: str, name: str, maximum: int) -> QueryRefused:
    return QueryRefused(
        Refusal(
            code=RefusalCode.LIMIT_EXCEEDED,
            path=None,
            message=[text(message)],
            limit=Limit(name=name, max=maximum),
        )
    )


class _Late(Exception):  # noqa: N818 - a signal between two functions, not an error
    """The run passed its deadline."""


class _Greedy(Exception):  # noqa: N818 - a signal between two functions, not an error
    """The child holds more memory than it may."""


class _Blind(Exception):  # noqa: N818 - a signal between two functions, not an error
    """The child's resident memory cannot be read."""


def _resident(pid: int) -> int | None:
    """A process's resident memory in bytes, from ``/proc``: 0 for one that has ended and not
    been reaped, ``None`` where it cannot be read."""
    try:
        with open(f"/proc/{pid}/status", "rb") as status:
            for line in status:
                if line.startswith(b"VmRSS:"):
                    return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        return None
    return 0


def _received(
    sock: socket.socket, size: int, deadline: float, watch: Callable[[], None]
) -> bytearray:
    """``size`` bytes, read under ``deadline`` into a buffer of that size, calling ``watch``
    every ``POLL`` seconds."""
    buffer = bytearray(size)
    view = memoryview(buffer)
    while view:
        left = deadline - time.monotonic()
        if left <= 0:
            raise _Late
        watch()
        sock.settimeout(min(left, POLL))
        try:
            found = sock.recv_into(view, min(len(view), _CHUNK))
        except TimeoutError:
            continue
        if not found:
            raise EOFError
        view = view[found:]
    return buffer


def _sent(sock: socket.socket, data: bytes, deadline: float) -> None:
    for part in (struct.pack("!Q", len(data)), data):
        view = memoryview(part)
        while view:
            left = deadline - time.monotonic()
            if left <= 0:
                raise _Late
            sock.settimeout(left)
            try:
                view = view[sock.send(view[:_CHUNK]) :]
            except TimeoutError:
                raise _Late from None


def _environment() -> dict[str, str]:
    source = str(Path(aibi.__file__).parent.parent)
    path = os.pathsep.join(filter(None, [source, os.environ.get("PYTHONPATH")]))
    return {
        "PATH": os.environ.get("PATH", os.defpath),
        "PYTHONPATH": path,
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
    }


@dataclass(frozen=True)
class _Child:
    process: subprocess.Popen[bytes]
    sock: socket.socket

    def ended(self, deadline: float) -> int | None:
        """Wait until the child has ended, at most until ``deadline``, then stop it: how it
        ended, or ``None`` if it was still running at the deadline."""
        pid = self.process.pid
        while os.waitid(os.P_PID, pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is None:
            left = deadline - time.monotonic()
            if left <= 0:
                self.stop()
                return None
            time.sleep(min(EXIT_POLL, left))
        return self.stop()

    def stop(self) -> int | None:
        """Kill the child's process group and reap it: how it ended, as ``returncode`` says, or
        ``None`` if it was still running."""
        pid = self.process.pid
        running = os.waitid(os.P_PID, pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is None
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(pid, signal.SIGKILL)
        self.sock.close()
        try:
            _, status = os.waitpid(pid, 0)
        except ChildProcessError:  # reaped already, which only this method does
            return None
        self.process.returncode = os.waitstatus_to_exitcode(status)
        return None if running else self.process.returncode


def _launch() -> _Child:
    parent, child = socket.socketpair()
    try:
        with child:
            process = subprocess.Popen(
                [sys.executable, "-P", "-c", _CHILD, str(child.fileno()), str(os.getpid())],
                pass_fds=(child.fileno(),),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=_environment(),
                start_new_session=True,
            )
    except BaseException:
        parent.close()
        raise
    return _Child(process, parent)


class _Malformed(Exception):  # noqa: N818 - a signal between two functions, not an error
    """An answer of a shape the child does not write."""


def _rows(frame: memoryview, queries: Sequence[Query], deadline: float) -> list[Rows]:
    """A ``VALUE`` answer's rows, one ``Rows`` for each query, each of the query's number of
    columns, parsed under ``deadline``."""
    try:
        (count,) = struct.unpack_from("!I", frame, 1)
        at = 5
        if count != len(queries):
            raise _Malformed
        found: list[Rows] = []
        for query in queries:
            if time.monotonic() > deadline:
                raise _Late
            rows, columns = struct.unpack_from("!QI", frame, at)
            at += 12
            if columns != query.columns:
                raise _Malformed
            packed: list[memoryview] = []
            for _ in range(columns):
                code = _WIDTHS.get(frame[at])
                if code is None:
                    raise _Malformed
                end = at + 1 + rows * struct.calcsize(code)
                if end > len(frame):
                    raise _Malformed
                packed.append(frame[at + 1 : end].cast(code))
                at = end
            found.append(Rows(packed, rows))
    except (struct.error, IndexError):
        raise _Malformed from None
    if at != len(frame):
        raise _Malformed
    return found


class Workers:
    """The query workers of one server: ``run`` runs a document's queries in a child of its own
    (see the module's docstring). Raises ``RuntimeError`` where no child's memory can be
    watched."""

    def __init__(self, limits: QueryLimits) -> None:
        if _resident(os.getpid()) is None:
            raise RuntimeError(
                "query workers need /proc to watch their resident memory, which this system "
                "does not provide"
            )
        self.limits = limits
        self._places = threading.BoundedSemaphore(limits.query_workers)

    def run(
        self, paths: Sequence[str], queries: Sequence[Query], *, ends: float | None = None
    ) -> list[Rows]:
        """Each query's rows. ``paths`` are the only files its queries may read. ``ends``, a
        ``time.monotonic()`` instant, is the caller's deadline, which the wait for a place and
        the run end by. Raises ``QueryRefused``, ``QueryError`` or, when ``ends`` came first,
        ``CallerDeadline``."""
        limits = self.limits
        now = time.monotonic()
        waits = now + limits.query_seconds
        until = waits if ends is None else min(waits, ends)
        if not self._places.acquire(timeout=max(0.0, until - now)):
            if ends is not None and ends < waits:
                raise CallerDeadline
            raise _refused(
                f"Every query worker was busy for {limits.query_seconds} seconds",
                QUERY_WORKERS,
                limits.query_workers,
            )
        try:
            runs = time.monotonic() + limits.query_seconds
            deadline = runs if ends is None else min(runs, ends)
            return self._run(paths, queries, deadline, deadline < runs)
        finally:
            self._places.release()

    def _run(
        self, paths: Sequence[str], queries: Sequence[Query], deadline: float, caller: bool
    ) -> list[Rows]:
        """The run, until ``deadline``, which is the caller's when ``caller``; a deadline that
        has passed starts no child."""
        if time.monotonic() >= deadline:
            if caller:
                raise CallerDeadline
            raise self._late()
        limits = self.limits
        cpu = limits.query_seconds * limits.query_threads + CPU_SLACK
        given = (cpu, cpu + CPU_GRACE, ADDRESS_FACTOR * limits.query_memory + ADDRESS_SLACK)
        request = (
            tuple(paths),
            limits.query_memory // 2,
            limits.query_threads,
            MAX_QUERY_ANSWER_BYTES,
            [(query.sql, dict(query.parameters)) for query in queries],
        )
        child = _launch()

        def watch() -> None:
            found = _resident(child.process.pid)
            if found is None:
                raise _Blind
            if found > limits.query_memory:
                raise _Greedy

        try:
            _sent(child.sock, pickle.dumps(given), deadline)
            _sent(child.sock, pickle.dumps(request, protocol=pickle.HIGHEST_PROTOCOL), deadline)
            (size,) = struct.unpack("!Q", _received(child.sock, 8, deadline, watch))
            if not 1 <= size <= MAX_QUERY_ANSWER_BYTES:
                raise _Malformed
            frame = memoryview(_received(child.sock, size, deadline, watch))
            rows = self._read(frame, queries, deadline)
        except _Late:
            child.stop()
            if caller:
                raise CallerDeadline from None
            raise self._late() from None
        except _Greedy:
            child.stop()
            raise self._memory() from None
        except _Blind:
            child.stop()
            raise QueryError("a query worker's resident memory could not be read") from None
        except (EOFError, OSError):
            code = child.ended(deadline)
            if code is None and caller:
                raise CallerDeadline from None
            raise self._ended(code) from None
        except _Malformed:
            child.stop()
            raise QueryError("a query worker's answer is not one the child writes") from None
        except BaseException:
            child.stop()
            raise
        child.stop()
        return rows

    def _read(self, frame: memoryview, queries: Sequence[Query], deadline: float) -> list[Rows]:
        """The answer's rows, or the refusal or fault it reports; raises ``_Malformed`` for a
        frame the child does not write, and ``_Late`` past ``deadline``."""
        kind = bytes(frame[:1])
        if kind == _VALUE:
            return _rows(frame, queries, deadline)
        if len(frame) == 1 and kind == _MEMORY:
            raise self._memory()
        if len(frame) == 1 and kind == _TOO_LARGE:
            raise _refused(
                f"The queries' rows take more than {MAX_QUERY_ANSWER_BYTES} bytes",
                QUERY_ANSWER_BYTES,
                MAX_QUERY_ANSWER_BYTES,
            )
        name = bytes(frame[1:])
        if kind == _ERROR and _KIND.fullmatch(name):
            raise QueryError(f"a query failed in its worker: {name.decode('ascii')}")
        raise _Malformed

    def _late(self) -> QueryRefused:
        seconds = self.limits.query_seconds
        return _refused(f"The queries took more than {seconds} seconds", QUERY_SECONDS, seconds)

    def _memory(self) -> QueryRefused:
        memory = self.limits.query_memory
        return _refused(
            f"The queries need more than {memory} bytes of memory", QUERY_MEMORY, memory
        )

    def _ended(self, code: int | None) -> Exception:
        if code in (-signal.SIGKILL, -signal.SIGABRT):
            return self._memory()
        if code is None or code == -signal.SIGXCPU:
            return self._late()
        return QueryError(f"a query worker ended with {code} before it answered")


__all__ = ["CallerDeadline", "Query", "QueryError", "QueryRefused", "Rows", "Workers"]

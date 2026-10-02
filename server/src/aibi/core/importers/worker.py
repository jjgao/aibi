"""Reading workbooks and Parquet files in a worker process that can be killed (SPEC §14, D225).

python-calamine and pyarrow read bytes an import does not trust, and what they allocate is not
bounded by those bytes: a workbook's sheet from A1 to a far cell, a shared string that every
cell names, an ODS cell repeated, a Parquet dictionary decoded into every row; and a malformed
ODS can keep calamine busy forever. Neither can be stopped from inside once it runs, so they
never run in the server's process. An import's workbooks and Parquet files are read in one
child process (``Reader``), started for the first of them and stopped when the import ends:

- It is a fresh interpreter, executed as ``spawn`` would start one, never a fork of the server,
  which holds threads and locks; unlike ``spawn`` it does not import the server's main module,
  and the working directory is not on its path (``-P``). It imports the ``aibi`` the server
  imported, and nothing of the server's state reaches it but the limits and each read's
  function and arguments: its environment holds ``PATH``, ``PYTHONPATH`` and a UTF-8 locale
  only, its standard input and output are ``/dev/null``, and its standard error is a pipe the
  server drains and discards, keeping its last 4 KiB to tell how the child ended.
- At most ``reader_workers`` of these run at once in the server's process, over every import;
  an import waits for its own to start at most ``reader_seconds`` and the seconds in which one
  stopped at its deadline is reaped, so that an import queued behind one that runs out of time
  gets it (``LIMIT_EXCEEDED`` naming ``reader_workers``). They bound the workers, not the
  imports: what an import holds in the server adds up over the imports that run at once (D225).
- Before it reads anything it limits its own address space (``RLIMIT_AS``, soft and hard) to
  ``reader_memory`` bytes and its CPU time (``RLIMIT_CPU``) to ``reader_seconds`` + 5 seconds,
  and on Linux asks to be killed when the server dies (``PR_SET_PDEATHSIG``; the thread that
  starts it, the import's, outlives it). It runs in a session of its own, and the server kills
  its whole process group once the import's reads have taken ``reader_seconds`` together, its
  start included, and when the import ends.
- The strings it returns, over every read of the import, hold at most ``decoded_bytes`` bytes of
  UTF-8 together, counted before it answers.
- The server reads each answer's frame itself, under the same deadline, and refuses one longer
  than ``reader_memory`` bytes; it unpickles the answer allowing no class but those a read
  returns (``Sheet``, ``TypedSource``, ``ParquetSource``) and ``datetime``'s ``date``,
  ``datetime``, ``timedelta`` and ``timezone``, looked up in a fixed table without importing
  anything.

The worker bounds what a hostile file makes a reader consume; it is not a privilege boundary.
It runs as the server's user, with its files and its network, so a reader that a file could
take over could act as the server does.

A read that refuses (``ImportRefused``) refuses the import as it is. Running out of memory is
``LIMIT_EXCEEDED`` naming ``reader_memory``: a ``MemoryError``, a child killed (``SIGKILL``, as
the kernel's OOM killer does), and a pyo3 ``PanicException`` or a child that aborts
(``SIGABRT``, as a failed Rust allocation ends) when its peak memory came within 90 % of
``reader_memory`` or Rust wrote that an allocation failed (a line of its standard error that is
exactly ``memory allocation of N bytes failed``). Its peak memory is its peak address space
(``VmPeak``), which the server reads from ``/proc`` every 0.05 seconds while it waits for an
answer and once more before it kills the child, and which a child that panics sends with its
answer (a non-negative integer, no more than any address space holds; anything else is a fault).
Where there is no ``/proc`` (not Linux) it is the child's peak resident size; on Linux that is
never read, since exec folds the peak of the memory a child starts with, the server's, into it
(D225). Running out of time, or of CPU time, is ``LIMIT_EXCEEDED`` naming ``reader_seconds``.
Any other ``BaseException`` that is no ``Exception``, a panic or an abort otherwise, and a child
that ends in any other way before it answers are ``UNPARSEABLE_SOURCE``. A ``MemoryError`` in
the server is ``LIMIT_EXCEEDED`` too: naming ``import_bytes`` while it pickles and sends a
request, which holds a file's bytes, and ``decoded_bytes`` while it receives or unpickles an
answer. Any other exception, and an answer that breaks the framing or holds another class, is a
fault of the reader, raised as ``ReaderError``. After a failure the child is gone, and the import
is refused.
"""

import contextlib
import dataclasses
import os
import pickle
import re
import resource
import signal
import socket
import struct
import subprocess
import sys
import threading
import time
import traceback
from collections import deque
from collections.abc import Callable, Sequence
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import TracebackType
from typing import IO, Any, Self, cast

import aibi
from aibi.core.importers.errors import ImportRefused, out_of_memory, refused
from aibi.core.importers.sheets import Sheet
from aibi.core.schema.limits import (
    DECODED_BYTES,
    IMPORT_BYTES,
    READER_MEMORY,
    READER_SECONDS,
    READER_WORKERS,
    ImportLimits,
)
from aibi.core.schema.refusals import Refusal, RefusalCode
from aibi.core.store.parquet import ParquetSource
from aibi.core.store.sources import TypedSource

_ALLOWED: dict[tuple[str, str], type] = {
    (kind.__module__, kind.__qualname__): kind
    for kind in (date, datetime, timedelta, timezone, Sheet, TypedSource, ParquetSource)
}
"""The only classes an answer may name: what a read returns, and the values of ``datetime``."""
_KINDS = frozenset({"value", "refused", "memory", "decoded", "error", "panic"})
_STOPPED = 5
"""Seconds a child that closed its socket is given to end, before it is killed; and seconds an
import waits for a worker beyond ``reader_seconds``, in which one stopped at its deadline is
killed and reaped."""
_CPU_SLACK = 5
"""CPU seconds a child has beyond ``reader_seconds``, which its deadline stops first unless it
runs threads."""
_NEAR = 0.9
"""The share of ``reader_memory`` from which a panic or an abort is running out of memory."""
_POLL = 0.05
"""Seconds between two readings of a child's peak address space while an answer is awaited."""
_ADDRESSES = 1 << 57
"""Bytes of the largest address space of any 64-bit machine (57-bit virtual addresses): no peak
a child reports is larger."""
_ALLOCATION = re.compile(rb"(?m)^memory allocation of \d+ bytes failed$")
"""What Rust writes, on a line of its own, when an allocation fails."""
_TAIL = 4096
_CHUNK = 1 << 20
_PR_SET_PDEATHSIG = 1
_CHILD = (
    "import sys; from aibi.core.importers.worker import serve; "
    "serve(int(sys.argv[1]), int(sys.argv[2]))"
)


class ReaderError(RuntimeError):
    """A reader raised an exception no hostile file should make it raise: a fault to fix."""


class _Late(Exception):  # noqa: N818 - a signal between two functions, not an error
    """The import's reads ran past ``reader_seconds``."""


# --- The child ------------------------------------------------------------------------------


def _utf8(text: str) -> int:
    return len(text) if text.isascii() else len(text.encode("utf-8", "surrogatepass"))


def _decoded(value: object) -> int:
    """The bytes, in UTF-8, of the strings in a reader's answer."""
    total, stack = 0, [value]
    while stack:
        current = stack.pop()
        if isinstance(current, str):
            total += _utf8(current)
        elif isinstance(current, tuple | list):
            stack.extend(cast(tuple[object, ...], current))
        elif dataclasses.is_dataclass(current) and not isinstance(current, type):
            stack.extend(getattr(current, field.name) for field in dataclasses.fields(current))
    return total


def _answer(
    limits: ImportLimits, call: tuple[Callable[..., object], Sequence[object]] | None, decoded: int
) -> tuple[tuple[object, ...], int]:
    try:
        if call is None:
            return ("memory",), decoded
        function, arguments = call
        value = function(*arguments)
        decoded += _decoded(value)
        if decoded > limits.decoded_bytes:
            return ("decoded",), decoded
        return ("value", value), decoded
    except ImportRefused as error:
        return ("refused", [r.model_dump(mode="json") for r in error.refusals]), decoded
    except MemoryError:
        return ("memory",), decoded
    except Exception as error:
        return ("error", "".join(traceback.format_exception(error))), decoded
    except BaseException as error:
        return ("panic", type(error).__name__, _peak("self")), decoded


def _limit(kind: int, value: int) -> None:
    """Set a resource limit of this process, soft and hard, never above its hard limit."""
    _, hard = resource.getrlimit(kind)
    if hard != resource.RLIM_INFINITY:
        value = min(value, hard)
    resource.setrlimit(kind, (value, value))


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


def _fill(sock: socket.socket, view: memoryview) -> bool:
    while view:
        received = sock.recv_into(view)
        if not received:
            return False
        view = view[received:]
    return True


def _header(sock: socket.socket) -> int | None:
    header = bytearray(4)
    if not _fill(sock, memoryview(header)):
        return None
    (size,) = struct.unpack("!i", header)
    if size == -1:
        header = bytearray(8)
        if not _fill(sock, memoryview(header)):
            return None
        (size,) = struct.unpack("!Q", header)
    return size


def _request(sock: socket.socket) -> bytearray | MemoryError | None:
    """The parent's next frame, read into one buffer (``MemoryError`` if it cannot be held, once
    it is read past); ``None`` once the parent has closed the socket."""
    size = _header(sock)
    if size is None:
        return None
    try:
        buffer = bytearray(size)
    except MemoryError:
        scratch = memoryview(bytearray(_CHUNK))
        while size:
            if not _fill(sock, scratch[: min(size, _CHUNK)]):
                return None
            size -= min(size, _CHUNK)
        return MemoryError()
    return buffer if _fill(sock, memoryview(buffer)) else None


def serve(descriptor: int, parent: int) -> None:
    """The child, on its end of the parent's socket: the limits, then requests (a function and
    its arguments, pickled), each answered with a pickled tuple, until the parent closes the
    socket or an answer is not a value. Frames are ``Connection``'s, read into one buffer."""
    _die_with(parent)
    sock = socket.socket(fileno=descriptor)
    given = _request(sock)
    if not isinstance(given, bytearray):
        return
    limits = cast(ImportLimits, pickle.loads(given))
    del given
    _limit(resource.RLIMIT_AS, limits.reader_memory)
    _limit(resource.RLIMIT_CPU, limits.reader_seconds + _CPU_SLACK)
    decoded = 0
    while True:
        # Nothing of the previous read is held while the next arrives, and the request's bytes
        # are let go once they are unpickled, so the file is held once while it is read.
        answer = written = call = None
        request = _request(sock)
        if request is None:
            return
        try:
            call = None if isinstance(request, MemoryError) else pickle.loads(request)
        except MemoryError:
            call = None
        del request
        answer, decoded = _answer(limits, call, decoded)
        del call
        try:
            written = pickle.dumps(answer, protocol=pickle.HIGHEST_PROTOCOL)
        except MemoryError:
            answer, written = ("memory",), pickle.dumps(("memory",))
        sock.sendall(_frame(len(written)))
        sock.sendall(written)
        if answer[0] != "value":
            return


def _frame(size: int) -> bytes:
    """A frame's header, as ``Connection.send_bytes`` writes it."""
    return struct.pack("!i", size) if size <= 0x7FFFFFFF else struct.pack("!iQ", -1, size)


# --- The parent -----------------------------------------------------------------------------


class _Unpickler(pickle.Unpickler):
    def find_class(self, module: str, name: str) -> Any:
        found = _ALLOWED.get((module, name))
        if found is None:
            raise pickle.UnpicklingError(f"a reader's answer holds {module}.{name}")
        return found


class _Chunks:
    """A received frame as the unpickler reads it, each chunk let go once it is read."""

    def __init__(self, chunks: deque[bytes]) -> None:
        self._chunks = chunks
        self._at = 0

    def read(self, size: int = -1) -> bytes:
        parts: list[bytes] = []
        while size != 0 and self._chunks:
            chunk = self._chunks[0]
            taken = len(chunk) - self._at if size < 0 else min(size, len(chunk) - self._at)
            parts.append(chunk[self._at : self._at + taken])
            self._at += taken
            size -= taken if size > 0 else 0
            if self._at == len(chunk):
                self._chunks.popleft()
                self._at = 0
        return parts[0] if len(parts) == 1 else b"".join(parts)

    def readinto(self, buffer: bytearray | memoryview) -> int:
        view = memoryview(buffer).cast("B")
        read = self.read(len(view))
        view[: len(read)] = read
        return len(read)

    def readline(self) -> bytes:
        parts: list[bytes] = []
        while self._chunks:
            chunk = self._chunks[0]
            end = chunk.find(b"\n", self._at)
            if end >= 0:
                parts.append(self.read(end + 1 - self._at))
                break
            parts.append(self.read(len(chunk) - self._at))
        return b"".join(parts)


def _received(sock: socket.socket, size: int, deadline: float, watch: Callable[[], None]) -> bytes:
    """At most ``size`` bytes, read under ``deadline``, calling ``watch`` before each wait of at
    most ``_POLL`` seconds."""
    while True:
        left = deadline - time.monotonic()
        if left <= 0:
            raise _Late
        watch()
        sock.settimeout(min(left, _POLL))
        try:
            found = sock.recv(size)
        except TimeoutError:
            continue
        if not found:
            raise EOFError
        return found


def _exactly(sock: socket.socket, size: int, deadline: float, watch: Callable[[], None]) -> bytes:
    parts: list[bytes] = []
    while size:
        parts.append(_received(sock, size, deadline, watch))
        size -= len(parts[-1])
    return b"".join(parts)


def _receive(
    sock: socket.socket, deadline: float, maximum: int, watch: Callable[[], None]
) -> deque[bytes]:
    """One frame, as ``serve`` writes it, read under ``deadline`` while ``watch`` is called: at
    most ``maximum`` bytes, in chunks."""
    (size,) = struct.unpack("!i", _exactly(sock, 4, deadline, watch))
    if size == -1:
        (size,) = struct.unpack("!Q", _exactly(sock, 8, deadline, watch))
    if size < 0 or size > maximum:
        raise ReaderError(f"a reader's answer claims {size} bytes; at most {maximum} are read")
    chunks: deque[bytes] = deque()
    while size:
        chunks.append(_received(sock, min(size, _CHUNK), deadline, watch))
        size -= len(chunks[-1])
    return chunks


def _panic(answer: tuple[Any, ...]) -> tuple[str, int] | None:
    """A panic's exception name and the peak address space its child sent, if the answer is
    one the child writes: the child is not trusted to write one."""
    if len(answer) != 3:
        return None
    _, name, peak = answer
    if type(name) is not str or type(peak) is not int or not 0 <= peak <= _ADDRESSES:
        return None
    return name, peak


def _send(sock: socket.socket, data: bytes, deadline: float) -> None:
    """One frame, as ``serve`` reads it, written under ``deadline``."""
    for part in (_frame(len(data)), data):
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


class _Tail:
    """A child's standard error, drained on a thread of its own: its last ``_TAIL`` bytes."""

    def __init__(self, stream: IO[bytes]) -> None:
        self.text = b""
        self._thread = threading.Thread(target=self._drain, args=(stream,), daemon=True)
        self._thread.start()

    def _drain(self, stream: IO[bytes]) -> None:
        with stream:
            try:
                while chunk := os.read(stream.fileno(), 1 << 16):
                    self.text = (self.text + chunk)[-_TAIL:]
            except OSError:
                pass

    def ended(self) -> bytes:
        """What it holds once every holder of the pipe closed it, or after a second."""
        self._thread.join(1.0)
        return self.text


@dataclasses.dataclass(frozen=True)
class _Ended:
    code: int | None
    """How the child ended, as ``Popen.returncode`` says, or ``None`` if the server killed it."""
    peak: int
    """Its peak memory in bytes. On Linux, its peak address space as far as it was seen: the most
    ``VmPeak`` read while an answer was awaited and before it was killed, and what it sent with a
    panic; never its peak resident size, into which exec folds the server's (D225). Where there
    is no ``/proc``, its peak resident size."""
    cpu: float
    stderr: bytes


def _peak(process: int | str) -> int:
    """A living process's peak address space (``process``, its pid, or ``"self"``), in bytes,
    from ``/proc``; 0 where there is none, as for a process that ended, whose status holds no
    memory, and for one that cannot allocate to read it."""
    try:
        with open(f"/proc/{process}/status", "rb") as status:
            for line in status:
                if line.startswith(b"VmPeak:"):
                    return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError, MemoryError):
        pass
    return 0


def _exited(pid: int, seconds: float) -> bool:
    """Whether the child ends within ``seconds``, left unreaped, so that its pid stays its own
    until its group is killed."""
    end = time.monotonic() + seconds
    while True:
        if os.waitid(os.P_PID, pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is not None:
            return True
        if time.monotonic() >= end:
            return False
        time.sleep(0.01)


@dataclasses.dataclass
class _Child:
    process: subprocess.Popen[bytes]
    sock: socket.socket
    stderr: _Tail
    peak: int = 0
    """The most of its peak address space seen so far, and of what it sent with a panic."""

    def watch(self) -> None:
        """Read its peak address space while it lives, which it has none of once it ended."""
        self.peak = max(self.peak, _peak(self.process.pid))

    def stop(self, wait: float = 0.0) -> _Ended:
        """Give the child ``wait`` seconds to end, kill its process group, and reap it."""
        pid = self.process.pid
        exited = _exited(pid, wait)
        self.watch()
        peak = self.peak
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(pid, signal.SIGKILL)
        self.sock.close()
        try:
            _, status, usage = os.wait4(pid, 0)
        except ChildProcessError:  # reaped already, which only this method does
            return _Ended(None, peak, 0.0, self.stderr.ended())
        self.process.returncode = os.waitstatus_to_exitcode(status)
        if not sys.platform.startswith("linux"):
            peak = max(peak, usage.ru_maxrss * (1 if sys.platform == "darwin" else 1024))
        return _Ended(
            self.process.returncode if exited else None,
            peak,
            usage.ru_utime + usage.ru_stime,
            self.stderr.ended(),
        )


def _environment() -> dict[str, str]:
    source = str(Path(aibi.__file__).parent.parent)
    path = os.pathsep.join(filter(None, [source, os.environ.get("PYTHONPATH")]))
    return {
        "PATH": os.environ.get("PATH", os.defpath),
        "PYTHONPATH": path,
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
    }


def _launch() -> _Child:
    parent, child = socket.socketpair()
    try:
        with child:
            process = subprocess.Popen(
                [sys.executable, "-P", "-c", _CHILD, str(child.fileno()), str(os.getpid())],
                pass_fds=(child.fileno(),),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                env=_environment(),
                start_new_session=True,
            )
    except BaseException:
        parent.close()
        raise
    return _Child(process, parent, _Tail(cast(IO[bytes], process.stderr)))


class _Workers:
    """The reader processes running in the server, over every import."""

    def __init__(self) -> None:
        self._changed = threading.Condition()
        self._running = 0

    def acquire(self, limits: ImportLimits) -> None:
        """Take a worker, waiting at most ``reader_seconds`` and the ``_STOPPED`` seconds in
        which one stopped at its deadline is reaped: an import queued behind one that runs out of
        time gets its worker, rather than a refusal as it is let go."""
        seconds = limits.reader_seconds + _STOPPED
        workers = limits.reader_workers
        with self._changed:
            if not self._changed.wait_for(lambda: self._running < workers, seconds):
                raise refused(
                    RefusalCode.LIMIT_EXCEEDED,
                    ("1 import was" if workers == 1 else f"{workers} imports were")
                    + f" reading workbooks or Parquet files for more than {seconds} seconds",
                    limit=(READER_WORKERS, workers),
                )
            self._running += 1

    def release(self) -> None:
        with self._changed:
            self._running -= 1
            self._changed.notify_all()


_WORKERS = _Workers()


class Reader:
    """One import's worker process: ``run`` calls a function there (see the module's docstring).
    The function and its arguments are pickled to the child, so the function is a module's."""

    def __init__(self, limits: ImportLimits) -> None:
        self.limits = limits
        self._child: _Child | None = None
        self._deadline = 0.0

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        kind: type[BaseException] | None,
        error: BaseException | None,
        trace: TracebackType | None,
    ) -> None:
        self.close()

    def start(self) -> None:
        """Start the child ahead of the first read, so that it starts while the parent works.
        Raises ``ImportRefused`` when no worker is free within ``reader_seconds``."""
        self._start()

    def _start(self) -> _Child:
        if self._child is None:
            _WORKERS.acquire(self.limits)
            try:
                child = _launch()
            except BaseException:
                _WORKERS.release()
                raise
            self._child = child
            self._deadline = time.monotonic() + self.limits.reader_seconds
            # If this fails, the child is gone, and the first read finds out how it ended.
            with contextlib.suppress(OSError, _Late):
                _send(child.sock, pickle.dumps(self.limits), self._deadline)
        return self._child

    def run[**P, T](self, function: Callable[P, T], *args: P.args, **kwargs: P.kwargs) -> T:
        """``function(*args)``, called in the child. Raises ``ImportRefused`` or
        ``ReaderError``."""
        assert not kwargs, "a reader's function takes its arguments by position"
        child = self._start()
        held = (IMPORT_BYTES, self.limits.import_bytes)  # what a MemoryError names: a file's bytes
        try:
            request = pickle.dumps((function, args), protocol=pickle.HIGHEST_PROTOCOL)
            _send(child.sock, request, self._deadline)
            del request
            held = (DECODED_BYTES, self.limits.decoded_bytes)  # then the answer's text
            answer = _Unpickler(
                _Chunks(
                    _receive(child.sock, self._deadline, self.limits.reader_memory, child.watch)
                )
            ).load()
        except _Late:
            self._kill()
            raise refused(
                RefusalCode.LIMIT_EXCEEDED,
                f"Reading the files took more than {self.limits.reader_seconds} seconds",
                limit=(READER_SECONDS, self.limits.reader_seconds),
            ) from None
        except (EOFError, OSError):
            raise self._ended(self._kill(_STOPPED), None) from None
        except MemoryError:
            self._kill()
            raise out_of_memory(*held) from None
        except ReaderError:
            self._kill()
            raise
        except Exception as error:
            self._kill()
            raise ReaderError(f"a reader's answer cannot be read: {error!r}") from None
        if not isinstance(answer, tuple) or not answer or answer[0] not in _KINDS:
            self._kill()
            raise ReaderError("a reader's answer is not one the child writes")
        found = cast(tuple[Any, ...], answer)
        kind = found[0]
        if kind == "value":
            return cast(T, found[1])
        panic = _panic(found) if kind == "panic" else None
        if kind == "panic":
            if panic is None:
                self._kill()
                raise ReaderError("a reader's panic is not one the child writes")
            child.peak = max(child.peak, panic[1])
        ended = self._kill()
        if kind == "refused":
            raise ImportRefused([Refusal.model_validate(one) for one in found[1]])
        if kind == "memory":
            raise self._memory()
        if kind == "decoded":
            raise refused(
                RefusalCode.LIMIT_EXCEEDED,
                f"The files read hold more than {self.limits.decoded_bytes} bytes of text",
                limit=(DECODED_BYTES, self.limits.decoded_bytes),
            )
        if panic is not None:
            raise self._ended(ended, panic[0])
        raise ReaderError(found[1])

    def _memory(self) -> ImportRefused:
        return refused(
            RefusalCode.LIMIT_EXCEEDED,
            f"Reading the file needs more than {self.limits.reader_memory} bytes of memory",
            limit=(READER_MEMORY, self.limits.reader_memory),
        )

    def _ended(self, ended: _Ended | None, panic: str | None) -> ImportRefused:
        """The refusal for a child that panicked (``panic``, the exception's name) or ended
        before it answered."""
        code = None if ended is None else ended.code
        if ended is not None and ended.cpu >= self.limits.reader_seconds + _CPU_SLACK - 1:
            return refused(
                RefusalCode.LIMIT_EXCEEDED,
                f"Reading the files took more than {self.limits.reader_seconds} seconds of CPU",
                limit=(READER_SECONDS, self.limits.reader_seconds),
            )
        if code == -signal.SIGKILL:
            return self._memory()
        if ended is not None and (panic is not None or code == -signal.SIGABRT):
            near = ended.peak >= _NEAR * self.limits.reader_memory
            if near or _ALLOCATION.search(ended.stderr):
                return self._memory()
        if panic is not None:
            return refused(RefusalCode.UNPARSEABLE_SOURCE, f"The file cannot be read ({panic})")
        return refused(
            RefusalCode.UNPARSEABLE_SOURCE,
            f"The file cannot be read: its reader ended with {code}",
        )

    def _kill(self, wait: float = 0.0) -> _Ended | None:
        child, self._child = self._child, None
        if child is None:
            return None
        try:
            return child.stop(wait)
        finally:
            _WORKERS.release()

    def close(self) -> None:
        """Stop the child, if it was started: it holds nothing to finish."""
        self._kill()


__all__ = ["Reader", "ReaderError", "serve"]

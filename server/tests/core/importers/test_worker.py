"""The worker process that reads workbooks and Parquet files (SPEC §14, D225): how it ends, what
it may answer, what it inherits, and what bounds it and the server.

The children here run code given to ``eval`` and ``exec``, which pickle by reference as builtins,
to act as a reader that a file took over would."""

import contextlib
import dataclasses
import functools
import io
import json
import math
import os
import pickle
import signal
import socket
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path
from typing import Any, NoReturn

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from aibi.core.importers import files, worker
from aibi.core.importers.errors import ImportRefused
from aibi.core.importers.files import read_parquet
from aibi.core.importers.worker import Reader, ReaderError
from aibi.core.schema.limits import ImportLimits
from aibi.core.schema.output import text
from aibi.core.store import build
from aibi.core.store.sources import ErrorCell

SMALL = ImportLimits(reader_memory=512 << 20, reader_seconds=5)
Roots = Any
Importing = Any


def _refusal(function: Any, *args: Any, limits: ImportLimits = SMALL) -> tuple[str, str | None]:
    with Reader(limits) as reader, pytest.raises(ImportRefused) as refused:
        reader.run(function, *args)
    [refusal] = refused.value.refusals
    return refusal.code, None if refusal.limit is None else refusal.limit.name


def _alive(pid: int) -> bool:
    """Whether ``pid`` is a process that has not ended (a zombie has)."""
    try:
        with open(f"/proc/{pid}/stat") as stat:
            return stat.read().rsplit(")", 1)[1].split()[0] != "Z"
    except FileNotFoundError:
        return False


def _gone(pid: int, seconds: float = 5.0) -> bool:
    end = time.monotonic() + seconds
    while _alive(pid):
        if time.monotonic() > end:
            return False
        time.sleep(0.02)
    return True


_NEAR = """
import os
status = open("/proc/self/status").read()
size = int(status.split("VmSize:")[1].split()[0]) * 1024
block = b"x" * (int(0.95 * {limit}) - size)
"""
"""Code that takes the child's address space to 95 % of ``limit``."""


# --- How a child ends -----------------------------------------------------------------------


def test_how_a_worker_ends_decides_the_refusal() -> None:
    with Reader(SMALL) as reader, pytest.raises(ReaderError, match="ZeroDivision"):
        reader.run(divmod, 1, 0)
    near = _NEAR.format(limit=SMALL.reader_memory)
    rust = "memory allocation of 1099511627776 bytes failed\\n"
    failed = f"import sys; sys.stderr.write('{rust}')"
    # More than the 4 KiB kept of the child's standard error comes before what Rust wrote.
    after = f"import sys; sys.stderr.write('x' * 100_000 + '\\n{rust}')"
    # What Rust writes is a line of its own: the words inside another line are not it.
    spoofed = f"import sys; sys.stderr.write('thread panicked: {rust}')"
    for function, args, expected in (
        (eval, ("[0] * (1 << 40)",), ("LIMIT_EXCEEDED", "reader_memory")),  # MemoryError
        (os.kill, (0, signal.SIGKILL), ("LIMIT_EXCEEDED", "reader_memory")),  # the OOM killer
        (functools.partial(os._exit, 3), (), ("UNPARSEABLE_SOURCE", None)),
        (exec, ("raise SystemExit(3)",), ("UNPARSEABLE_SOURCE", None)),
        # A panic or an abort far from the limit is no memory, near it or after Rust says an
        # allocation failed it is.
        (exec, ("raise KeyboardInterrupt",), ("UNPARSEABLE_SOURCE", None)),
        (exec, (near + "raise KeyboardInterrupt",), ("LIMIT_EXCEEDED", "reader_memory")),
        (os.abort, (), ("UNPARSEABLE_SOURCE", None)),
        (exec, (failed + "; import os; os.abort()",), ("LIMIT_EXCEEDED", "reader_memory")),
        (exec, (after + "; import os; os.abort()",), ("LIMIT_EXCEEDED", "reader_memory")),
        (exec, (spoofed + "; import os; os.abort()",), ("UNPARSEABLE_SOURCE", None)),
    ):
        assert _refusal(function, *args) == expected, (function, args)


def test_a_panic_or_an_abort_is_memory_by_the_child_s_peak_or_what_rust_wrote() -> None:
    reader = Reader(SMALL)
    limit = SMALL.reader_memory

    def refusal(
        code: int | None, peak: float, stderr: bytes = b"", panic: str | None = None
    ) -> Any:
        found = reader._ended(worker._Ended(code, int(peak), 0.0, stderr), panic)
        [one] = found.refusals
        return one.code, one.limit and one.limit.name

    memory = ("LIMIT_EXCEEDED", "reader_memory")
    unparseable = ("UNPARSEABLE_SOURCE", None)
    assert refusal(-signal.SIGABRT, math.ceil(0.9 * limit)) == memory
    assert refusal(-signal.SIGABRT, math.floor(0.9 * limit) - 1) == unparseable
    assert refusal(None, 0.95 * limit, panic="PanicException") == memory
    assert refusal(None, 0.5 * limit, panic="PanicException") == unparseable
    assert refusal(-signal.SIGABRT, 0, b"...\nmemory allocation of 8 bytes failed\n") == memory
    for spoofed in (
        b"thread panicked: memory allocation of 8 bytes failed\n",
        b"memory allocation of 8 bytes failed, it said\n",
    ):
        assert refusal(-signal.SIGABRT, 0, spoofed) == unparseable
        assert refusal(None, 0, spoofed, "PanicException") == unparseable
    assert refusal(-signal.SIGSEGV, limit) == unparseable
    assert refusal(-signal.SIGKILL, 0) == memory
    cpu = reader._ended(worker._Ended(-signal.SIGKILL, 0, 10.0, b""), None)
    assert cpu.refusals[0].limit is not None
    assert cpu.refusals[0].limit.name == "reader_seconds"


_INFLATED = textwrap.dedent(
    """
    import json, os, resource
    from aibi.core.importers.errors import ImportRefused
    from aibi.core.importers.worker import Reader
    from aibi.core.schema.limits import ImportLimits
    block = b"x" * (600 << 20)
    del block
    found = [resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024]
    for function, args in ((os.abort, ()), (exec, ("raise KeyboardInterrupt",))):
        with Reader(ImportLimits(reader_memory=512 << 20, reader_seconds=30)) as reader:
            try:
                reader.run(function, *args)
            except ImportRefused as refused:
                found.append(refused.refusals[0].code)
    print(json.dumps(found))
    """
)
"""A server that touched 600 MiB and let it go, then read with a child that aborts and one that
panics, far from their 512 MiB: its own peak resident size, then the two refusals' codes."""


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="exec folding the peak is Linux's")
def test_a_server_s_own_peak_never_makes_a_panic_or_an_abort_far_from_the_limit_memory() -> None:
    """Exec folds the peak resident size of the memory a child starts with, the server's, into
    the child's; run in a process of its own, so that pytest's memory decides nothing."""
    ended = subprocess.run(
        [sys.executable, "-c", _INFLATED], capture_output=True, text=True, timeout=180, check=True
    )
    peak, *codes = json.loads(ended.stdout)
    assert peak >= 0.9 * (512 << 20)
    assert codes == ["UNPARSEABLE_SOURCE", "UNPARSEABLE_SOURCE"]


def test_a_child_that_panics_near_the_limit_sends_its_peak(monkeypatch: pytest.MonkeyPatch) -> None:
    """The server reads nothing of the child's memory here: what the child sent decides."""
    monkeypatch.setattr(worker, "_peak", lambda process: 0)
    near = _NEAR.format(limit=SMALL.reader_memory)
    assert _refusal(exec, near + "raise KeyboardInterrupt") == ("LIMIT_EXCEEDED", "reader_memory")
    assert _refusal(exec, "raise KeyboardInterrupt") == ("UNPARSEABLE_SOURCE", None)


def test_a_child_that_aborts_near_the_limit_is_seen_there_while_it_lives() -> None:
    """Once it has ended its status holds no memory: only what was read while it lived says it
    came near the limit."""
    near = _NEAR.format(limit=SMALL.reader_memory)
    aborts = near + "import os, time; time.sleep(1); os.abort()"
    assert _refusal(exec, aborts) == ("LIMIT_EXCEEDED", "reader_memory")


def test_a_child_s_cpu_time_and_address_space_are_limited_soft_and_hard() -> None:
    with Reader(SMALL) as reader:
        found = reader.run(
            eval,
            "[__import__('resource').getrlimit(getattr(__import__('resource'), name)) "
            "for name in ('RLIMIT_AS', 'RLIMIT_CPU')]",
        )
    assert found == [(512 << 20,) * 2, (10,) * 2]


def test_a_child_inherits_a_minimal_environment_and_not_the_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "planted.py").write_text("raise SystemExit('imported from the working directory')")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AIBI_SECRET", "curator token")
    with Reader(SMALL) as reader:
        assert reader.run(eval, "sorted(__import__('os').environ)") == [
            "LANG",
            "LC_ALL",
            "PATH",
            "PYTHONPATH",
        ]
        assert reader.run(eval, "__import__('os').environ['LC_ALL']") == "C.UTF-8"
        assert reader.run(eval, "__import__('importlib.util').util.find_spec('planted')") is None


def test_a_child_s_standard_error_never_reaches_the_server_s(
    capfd: pytest.CaptureFixture[str],
) -> None:
    with Reader(SMALL) as reader:
        reader.run(exec, "import sys; sys.stderr.write('x' * 100_000); sys.stderr.flush()")
        reader.run(exec, "import os; os.write(2, b'y' * 100_000)")
    assert capfd.readouterr().err == ""


# --- What bounds a child that misbehaves ------------------------------------------------------

_WRITE = "import os, struct, sys, time; os.write(int(sys.argv[1]), {frame}); {then}"


def test_a_child_that_writes_part_of_a_frame_and_stops_is_killed_at_the_deadline() -> None:
    limits = dataclasses.replace(SMALL, reader_seconds=1)
    partial = _WRITE.format(frame="struct.pack('!i', 1 << 20) + b'x' * 100", then="time.sleep(60)")
    started = time.monotonic()
    with Reader(limits) as reader:
        reader.start()
        assert reader._child is not None
        pid = reader._child.process.pid
        with pytest.raises(ImportRefused) as refused:
            reader.run(exec, partial)
        assert reader._child is None
    assert time.monotonic() - started < 3
    assert refused.value.refusals[0].limit is not None
    assert refused.value.refusals[0].limit.name == "reader_seconds"
    assert _gone(pid)


def test_a_frame_longer_than_the_child_s_memory_is_a_fault_not_an_allocation() -> None:
    for frame in ("struct.pack('!iQ', -1, 1 << 40)", "struct.pack('!i', -7)"):
        huge = _WRITE.format(frame=frame, then="time.sleep(60)")
        with Reader(SMALL) as reader:
            with pytest.raises(ReaderError, match="claims"):
                reader.run(exec, huge)
            assert reader._child is None


def test_a_panic_whose_peak_the_child_wrote_malformed_is_a_fault() -> None:
    frame = "(lambda p: struct.pack('!i', len(p)) + p)(__import__('pickle').dumps({answer!r}))"
    for answer, expected in (
        (("panic", "X", 0), ("UNPARSEABLE_SOURCE", None)),
        (("panic", "X", 1 << 57), ("LIMIT_EXCEEDED", "reader_memory")),
    ):
        written = _WRITE.format(frame=frame.format(answer=answer), then="time.sleep(60)")
        assert _refusal(exec, written) == expected, answer
    for answer in (
        ("panic", "X"),
        ("panic", "X", 0, 0),
        ("panic", "X", -1),
        ("panic", "X", (1 << 57) + 1),
        ("panic", "X", True),
        ("panic", "X", 1.0),
        ("panic", "X", "0"),
        ("panic", 3, 0),
    ):
        written = _WRITE.format(frame=frame.format(answer=answer), then="time.sleep(60)")
        with Reader(SMALL) as reader:
            with pytest.raises(ReaderError, match="panic"):
                reader.run(exec, written)
            assert reader._child is None, answer


def test_an_answer_names_no_class_but_those_a_read_returns() -> None:
    """Classes are looked up in a fixed table: nothing is imported, no other dataclass of aibi
    and no other value of ``datetime`` (``time``, or ``time.time``, whose name is one) passes."""
    with Reader(SMALL) as reader:
        assert reader.run(eval, "__import__('datetime').date(2026, 1, 1)").year == 2026
        assert reader.run(eval, "{'a': [1.5, None, True, b'x', frozenset({1})]}")
    for code in (
        "__import__('decimal').Decimal(1)",
        "__import__('time').time",
        "__import__('datetime').time(1)",
        "__import__('aibi.core.store.sources').core.store.sources.ErrorCell('#N/A')",
        "__import__('aibi.core.importers.worker').core.importers.worker.ReaderError('x')",
    ):
        with Reader(SMALL) as reader:
            with pytest.raises(ReaderError, match="holds"):
                reader.run(eval, code)
            assert reader._child is None, code
    assert ErrorCell  # a dataclass of aibi, which an answer used to be allowed to hold


def test_the_unpickler_imports_nothing() -> None:
    assert "tabnanny" not in sys.modules
    for written in (b"ctabnanny\ncheck\n.", b"caibi.packs.nowhere\nThing\n."):
        with pytest.raises(pickle.UnpicklingError):
            worker._Unpickler(io.BytesIO(written)).load()
    assert "tabnanny" not in sys.modules
    assert "aibi.packs.nowhere" not in sys.modules


class _Held:
    """An argument the server has not the memory to pickle, as it may lack it for a file's bytes."""

    def __reduce__(self) -> NoReturn:
        raise MemoryError


def test_the_server_running_out_of_memory_for_a_request_or_an_answer_is_a_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A request holds a file's bytes, which ``import_bytes`` bounds, and an answer its text,
    which ``decoded_bytes`` bounds; either way the child is stopped."""

    def fails(self: Any) -> Any:
        raise MemoryError

    with Reader(SMALL) as reader:
        with pytest.raises(ImportRefused) as request:
            reader.run(str, _Held())
        assert reader._child is None
    monkeypatch.setattr(worker._Unpickler, "load", fails)
    with Reader(SMALL) as reader:
        with pytest.raises(ImportRefused) as answer:
            reader.run(str, "abc")
        assert reader._child is None
    for refused, limit in ((request, "import_bytes"), (answer, "decoded_bytes")):
        [refusal] = refused.value.refusals
        assert (refusal.code, refusal.limit and refusal.limit.name) == ("LIMIT_EXCEEDED", limit)


def test_the_server_running_out_of_memory_for_a_release_is_a_limit(
    roots: Roots, importing: Importing, make_xlsx: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """While the importer reads, ``import_bytes``; while the store builds, ``decoded_bytes`` if
    any table is a typed source, ``import_bytes`` otherwise."""

    def fails(given: Any) -> Any:
        raise MemoryError

    workbook = make_xlsx([("S", [["id"], [1]])])
    roots.write("mixed/a.csv", b"id\n1\n")
    roots.write("mixed/b.xlsx", workbook)
    for patched, path, limit in (
        ((files, "infer"), roots.write("read.xlsx", workbook), "import_bytes"),
        ((files, "infer"), roots.write("read.csv", b"id\n1\n"), "import_bytes"),
        ((build, "encode"), roots.write("a.xlsx", workbook), "decoded_bytes"),
        ((build, "encode"), roots.write("a.csv", b"id\n1\n"), "import_bytes"),
        ((build, "encode"), roots.inside / "mixed", "decoded_bytes"),
    ):
        with monkeypatch.context() as patch:
            patch.setattr(*patched, fails)
            with pytest.raises(ImportRefused) as refused:
                importing(path)
        [refusal] = refused.value.refusals
        found = (refusal.code, refusal.limit and refusal.limit.name)
        assert found == ("LIMIT_EXCEEDED", limit), (patched, path)


def test_decoded_text_is_counted_in_utf_8_over_every_read_of_an_import() -> None:
    limits = dataclasses.replace(SMALL, decoded_bytes=10)
    with Reader(limits) as reader:
        assert reader.run(str, "abc") == "abc"
        assert reader.run(str, "\U0001f600") == "\U0001f600"  # 4 bytes, 1 character
        assert reader.run(str, "\xe9") == "\xe9"
        with pytest.raises(ImportRefused) as refused:
            reader.run(str, "\xe9")
    assert refused.value.refusals[0].limit is not None
    assert refused.value.refusals[0].limit.name == "decoded_bytes"


# --- Workers and the server's processes -------------------------------------------------------


def test_at_most_reader_workers_run_at_once_and_an_import_waits_for_one() -> None:
    """An import waits ``reader_seconds`` and the 5 seconds in which a worker stopped at its
    deadline is reaped, and is woken as soon as a worker is let go."""
    one = dataclasses.replace(SMALL, reader_workers=1, reader_seconds=1)
    with Reader(one) as first:
        first.start()
        started = time.monotonic()
        with Reader(one) as second, pytest.raises(ImportRefused) as refused:
            second.start()
        assert 5.9 < time.monotonic() - started < 8
        waiting = Reader(dataclasses.replace(one, reader_seconds=10))
        timer = threading.Timer(0.3, first.close)
        timer.start()
        started = time.monotonic()
        with waiting:
            waiting.start()
            assert time.monotonic() - started < 5  # woken, well before its 15 seconds
            assert waiting.run(str, "read") == "read"
        timer.join()
    [refusal] = refused.value.refusals
    assert refusal.limit is not None
    assert (refusal.limit.name, refusal.limit.max) == ("reader_workers", 1)
    assert refusal.message == [
        text("1 import was reading workbooks or Parquet files for more than 6 seconds")
    ]
    with Reader(one) as again:  # every worker was let go, the one refused included
        again.start()


def test_an_import_queued_behind_one_that_runs_out_of_time_gets_its_worker() -> None:
    """The queued import started waiting after the hanging one's reads started: the hanging
    one's worker is stopped at its deadline and let go before the queued one's wait ends."""
    one = dataclasses.replace(SMALL, reader_workers=1, reader_seconds=1)
    refusals: list[ImportRefused] = []

    def hang(reader: Reader) -> None:
        with reader:
            try:
                reader.run(exec, "import time; time.sleep(60)")
            except ImportRefused as refused:
                refusals.append(refused)

    hanging = Reader(one)
    hanging.start()
    thread = threading.Thread(target=hang, args=(hanging,))
    thread.start()
    started = time.monotonic()
    try:
        with Reader(one) as queued:
            queued.start()
            waited = time.monotonic() - started
            assert queued.run(str, "read") == "read"
    finally:
        thread.join()
    assert 0.5 < waited < 3
    [[refusal]] = [refused.refusals for refused in refusals]
    assert refusal.limit is not None
    assert refusal.limit.name == "reader_seconds"


def test_at_least_one_worker_runs() -> None:
    assert ImportLimits(reader_workers=1).reader_workers == 1
    for workers in (0, -1):
        with pytest.raises(ValueError, match=f"reader_workers is at least 1, not {workers}"):
            ImportLimits(reader_workers=workers)


_FORK = (
    "[os := __import__('os'), (pid := os.fork()) == 0 and (__import__('time').sleep(60), "
    "os._exit(0)), pid][-1]"
)
"""Code that forks a grandchild that sleeps for a minute, and gives its pid."""


def test_stopping_a_worker_kills_its_process_group() -> None:
    with Reader(SMALL) as reader:
        grandchild = reader.run(eval, _FORK)
        assert reader._child is not None
        child = reader._child.process.pid
        assert _alive(grandchild)
    assert _gone(child)
    assert _gone(grandchild)


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="PR_SET_PDEATHSIG is Linux's")
def test_a_worker_dies_with_the_server(tmp_path: Path) -> None:
    """The server is killed once its child is inside a read that sleeps (it touched
    ``reading``), and so reads nothing from its socket: only ``PR_SET_PDEATHSIG`` ends it."""
    reading = tmp_path / "reading"
    sleeps = f"open({str(reading)!r}, 'w').close(); __import__('time').sleep(60)"
    server = textwrap.dedent(
        f"""
        from aibi.core.importers.worker import Reader
        from aibi.core.schema.limits import ImportLimits
        reader = Reader(ImportLimits(reader_seconds=60))
        grandchild = reader.run(eval, {_FORK!r})
        print(reader._child.process.pid, grandchild, flush=True)
        reader.run(exec, {sleeps!r})
        """
    )
    process = subprocess.Popen([sys.executable, "-c", server], stdout=subprocess.PIPE, text=True)
    pids: list[int] = []
    try:
        assert process.stdout is not None
        pids.extend(map(int, process.stdout.readline().split()))
        end = time.monotonic() + 30
        while not reading.exists():
            assert process.poll() is None, "the server ended before its child read"
            assert time.monotonic() < end, "the child never read"
            time.sleep(0.01)
        process.kill()
        process.wait()
        assert _gone(pids[0])
    finally:
        process.kill()
        process.wait()
        if process.stdout is not None:
            process.stdout.close()
        # A grandchild outlives it (nothing is left to kill its group), bounded by its CPU time,
        # and so would a child that did not die with the server.
        for pid in filter(_alive, pids):
            with contextlib.suppress(ProcessLookupError):
                os.kill(pid, signal.SIGKILL)


def test_a_worker_whose_server_died_before_it_asked_to_die_with_it_ends() -> None:
    """A child whose parent is no longer the server that started it ends before it reads, with
    1; one that read would find the socket closed and end with 0."""
    server = subprocess.Popen([sys.executable, "-c", ""])
    server.wait()  # the server that started it, dead
    ours, theirs = socket.socketpair()
    ours.close()
    with theirs:
        ended = subprocess.run(
            [sys.executable, "-P", "-c", worker._CHILD, str(theirs.fileno()), str(server.pid)],
            pass_fds=(theirs.fileno(),),
            env=worker._environment(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=60,
        )
    assert ended.returncode == 1


def test_a_parquet_file_of_many_cells_crosses_in_chunks() -> None:
    """An answer of many frames and chunks unpickles as the child wrote it."""
    table = pa.table({"id": list(range(200_000)), "s": [f"v{i}" for i in range(200_000)]})
    written = io.BytesIO()
    pq.write_table(table, written)
    limits = dataclasses.replace(SMALL, reader_memory=1 << 30)  # pyarrow's arenas reserve
    with Reader(limits) as reader:
        read = reader.run(read_parquet, written.getvalue(), limits, 1_000_000)
    assert read.rows[199_999] == (199_999, "v199999")
    assert len(read.rows) == 200_000

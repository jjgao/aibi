"""Queries in worker processes that can be killed (§14, D293): the rows a session gives, the
limits in time, memory, places and answers, how a child's end is judged, what a child is given
and may read or run, and the server's process never loading DuckDB."""

import os
import signal
import socket
import subprocess
import sys
import threading
import time
import tracemalloc
from array import array
from collections.abc import Callable, Sequence
from pathlib import Path
from types import MappingProxyType
from typing import Any

import pytest

from aibi.core.engine import worker
from aibi.core.engine.data import Release
from aibi.core.engine.queries import run_cohorts
from aibi.core.engine.resolve import ResolvedCohort
from aibi.core.engine.sql import CompiledCohort, TruthValues, compile_cohort
from aibi.core.engine.truth import Truth
from aibi.core.engine.worker import (
    CallerDeadline,
    Query,
    QueryError,
    QueryRefused,
    Rows,
    Workers,
)
from aibi.core.schema.limits import MIN_QUERY_MEMORY, QueryLimits

City = Callable[..., Release]
Doc = Callable[..., dict[str, Any]]
Runner = Callable[..., Any]

SLOW = "SELECT count(*) FROM range(100000000000) AS a(x) WHERE x % 7 = 3"
GREEDY = (
    "SELECT count(DISTINCT x) FROM (SELECT range::VARCHAR || '-padding-' AS x FROM range(30000000))"
)
SECRET = "a-server-secret-7f3a"


def _places(workers: Workers) -> threading.BoundedSemaphore:
    return workers._places  # pyright: ignore[reportPrivateUsage]


def _limit(refused: pytest.ExceptionInfo[QueryRefused]) -> tuple[str, int]:
    limit = refused.value.refusal.limit
    assert limit is not None
    assert refused.value.refusal.code == "LIMIT_EXCEEDED"
    return limit.name, limit.max


def _fake(script: str) -> str:
    """A child that runs ``script`` on its end of the socket (``sock``) instead of ``duck``'s."""
    return (
        "import os, signal, socket, struct, sys\n"
        "sock = socket.socket(fileno=int(sys.argv[1]))\n"
        "import resource\n"
        "resource.setrlimit(resource.RLIMIT_CORE, (0, 0))\n"
        "sock.recv(8)\n"
        "def send(data):\n"
        "    sock.sendall(struct.pack('!Q', len(data)) + data)\n" + script
    )


# --- Rows --------------------------------------------------------------------------------------


def test_a_worker_counts_every_cohort_of_a_document_as_the_evaluator_does(
    run: Runner, city: City, doc: Doc, blobs: Callable[..., Any]
) -> None:
    release = city(
        {
            "establishments": [
                {"establishment_id": "e0", "grade": "A", "owner_id": "o0"},
                {"establishment_id": "e1", "grade": "pending", "owner_id": "gone"},
                {"establishment_id": "e2", "grade": "exempt"},
            ],
            "owners": [{"owner_id": "o0", "region": "north"}],
            "inspections": [{"inspection_id": "i0", "establishment_id": "e0", "kind": "routine"}],
        }
    )
    written = doc(
        {
            "graded": [{"kind": "value", "column": "establishments.grade", "values": ["A"]}],
            "north": [{"kind": "value", "column": "owners.region", "values": ["north"]}],
            "inspected": [{"kind": "exists", "table": "inspections", "where": []}],
        }
    )
    result = run(written, release)
    sources = {release.manifest: blobs(release)}
    cohorts = list(result.resolution.cohorts.values())
    workers = Workers(QueryLimits())
    counted = run_cohorts(cohorts, sources, workers, values=True)
    for cohort, found in zip(cohorts, counted, strict=True):
        expected = result.results[cohort.name]
        assert isinstance(found.values, TruthValues)
        assert tuple(found.values) == expected.values
        assert (found.accounting.n_true, found.accounting.n_unknown) == (
            expected.n_true,
            expected.n_unknown,
        )
        assert found.sql[0].startswith("WITH ")
        assert isinstance(found.parameters, MappingProxyType)
        assert all("/" not in str(value) for value in found.parameters.values())
    counts = run_cohorts(cohorts, sources, workers)
    assert [found.values for found in counts] == [None, None, None]
    assert [found.accounting for found in counts] == [found.accounting for found in counted]


def test_rows_are_integers_read_in_place_from_columns_of_the_narrowest_type() -> None:
    workers = Workers(QueryLimits())
    query = Query(
        "SELECT range AS a, range * 1000 AS b, $large AS c, $small AS d FROM range(3)",
        {"large": 2**63 - 1, "small": -(2**63)},
        4,
    )
    [rows] = workers.run([], [query])
    assert len(rows) == 3
    assert list(rows) == [(n, n * 1000, 2**63 - 1, -(2**63)) for n in range(3)]
    assert rows[-1] == rows[2] == (2, 2000, 2**63 - 1, -(2**63))
    assert list(rows[1:]) == list(rows)[1:]
    with pytest.raises(IndexError):
        rows[3]
    [empty] = workers.run([], [Query("SELECT 1 WHERE false", {}, 1)])
    assert list(empty) == []


def test_parameters_are_bound_by_name() -> None:
    workers = Workers(QueryLimits())
    text = "'; DROP TABLE x; --"
    query = Query("SELECT $b, length($a), len($c)", {"a": text, "b": 2, "c": (1, 2)}, 3)
    assert [list(rows) for rows in workers.run([], [query])] == [[(2, len(text), 2)]]


def test_rows_that_are_not_integers_are_a_fault() -> None:
    with pytest.raises(QueryError, match="NotIntegers"):
        Workers(QueryLimits()).run([], [Query("SELECT 'text'", {}, 1)])


def test_a_query_says_its_rows_have_at_least_one_column() -> None:
    with pytest.raises(ValueError, match="at least one column"):
        Query("SELECT 1", {}, 0)


def _cohort(run: Runner, city: City, doc: Doc, blobs: Callable[..., Any]) -> tuple[Any, ...]:
    """A cohort of one clause on three establishments, resolved and compiled; it raises no
    flag."""
    release = city(
        {
            "establishments": [
                {"establishment_id": "e0", "grade": "A"},
                {"establishment_id": "e1", "grade": "pending"},
                {"establishment_id": "e2", "grade": "B"},
            ]
        }
    )
    result = run(
        doc([{"kind": "value", "column": "establishments.grade", "values": ["A"]}]), release
    )
    cohort: ResolvedCohort = result.resolution.cohorts["c"]
    sources = {release.manifest: blobs(release)}
    return cohort, sources, compile_cohort(cohort, sources[release.manifest])


def _rows(*columns: Sequence[int]) -> Rows:
    return Rows([memoryview(array("q", column)) for column in columns], len(columns[0]))


class _Answering(Workers):
    """Workers whose runs answer with the given rows, as a child that lies would."""

    def __init__(self, answers: list[Rows]) -> None:
        super().__init__(QueryLimits())
        self.answers = answers

    def run(
        self, paths: Sequence[str], queries: Sequence[Query], *, ends: float | None = None
    ) -> list[Rows]:
        return self.answers


def test_rows_a_cohort_s_queries_do_not_give_are_a_fault_never_a_value_error(
    run: Runner, city: City, doc: Doc, blobs: Callable[..., Any]
) -> None:
    cohort, sources, compiled = _cohort(run, city, doc, blobs)
    assert isinstance(compiled, CompiledCohort)
    counts = [1, 1, 1, 1, 0, 0, 0, 0, 0, 1, 0]
    assert len(counts) == compiled.counts_columns
    good = _rows(*([value] for value in counts))
    for answers in (
        [_rows(*([value, value] for value in counts))],
        [_rows(*([value] for value in counts[:-1]))],
        [_rows(*([value] for value in [-1, *counts[1:]]))],
        [good, _rows([0, 1, 2], [2, 1, 0])],
        [good, _rows([0, 2, 1], [2, 1, 0], [0, 1, 0])],
        [good, _rows([0, 1, 2], [2, 3, 0], [0, 1, 0])],
        [good, _rows([0, 1, 2], [2, 1, 0], [0, 0, 0])],
        [good, _rows([0, 1, 2], [2, 1, 0], [1, 1, 0])],
        [good, _rows([0, 1, 2], [2, 1, 0], [0, 1 << 6, 0])],
    ):
        with pytest.raises(QueryError):
            run_cohorts([cohort], sources, _Answering(answers), values=True)
    [counted] = run_cohorts(
        [cohort], sources, _Answering([good, _rows([0, 1, 2], [2, 1, 0], [0, 1, 0])]), values=True
    )
    assert counted.values is not None
    assert [value.value for value in counted.values] == [Truth.TRUE, Truth.UNKNOWN, Truth.FALSE]


def test_truth_values_are_made_as_they_are_read_so_a_million_units_take_no_memory_of_their_own(
    run: Runner, city: City, doc: Doc, blobs: Callable[..., Any]
) -> None:
    """The values query's columns stay packed as the answer holds them (D293)."""
    _, _, compiled = _cohort(run, city, doc, blobs)
    units = 1_000_000
    rows = Rows(
        [
            memoryview(array("i", range(units))),
            memoryview(array("b", [2, 1, 0]) * (units // 3) + array("b", [2])),
            memoryview(array("b", [0, 1, 0]) * (units // 3) + array("b", [0])),
        ],
        units,
    )
    tracemalloc.start()
    try:
        values = compiled.truth_values(rows)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 1 << 20
    assert len(values) == units
    assert (values[0].value, values[1].value, values[-1].value) == (
        Truth.TRUE,
        Truth.UNKNOWN,
        Truth.TRUE,
    )
    assert [value.value for value in values[1:3]] == [Truth.UNKNOWN, Truth.FALSE]
    with pytest.raises(IndexError):
        values[units]


def test_a_caller_s_deadline_already_past_starts_no_child(
    run: Runner, city: City, doc: Doc, blobs: Callable[..., Any], spied: list[int]
) -> None:
    workers = Workers(QueryLimits())
    for ends in (time.monotonic(), time.monotonic() - 1):
        with pytest.raises(CallerDeadline):
            workers.run([], [Query("SELECT 1", {}, 1)], ends=ends)
    places = _places(workers)
    for _ in range(QueryLimits().query_workers):
        assert places.acquire(timeout=1)
    try:
        with pytest.raises(CallerDeadline):
            workers.run([], [Query("SELECT 1", {}, 1)], ends=time.monotonic() - 1)
    finally:
        for _ in range(QueryLimits().query_workers):
            places.release()
    cohort, sources, _ = _cohort(run, city, doc, blobs)
    with pytest.raises(CallerDeadline):
        run_cohorts([cohort], sources, workers, ends=time.monotonic() - 1)
    assert spied == []
    [counted] = run_cohorts([cohort], sources, workers, ends=time.monotonic() + 60)
    assert counted.accounting.n_true == 1
    assert len(spied) == 1


# --- Limits ------------------------------------------------------------------------------------


def test_a_run_past_its_deadline_is_refused_within_a_second_of_query_seconds() -> None:
    workers = Workers(QueryLimits(query_seconds=2))
    started = time.monotonic()
    with pytest.raises(QueryRefused) as refused:
        workers.run([], [Query(SLOW, {}, 1)])
    assert 2 <= time.monotonic() - started < 3
    assert _limit(refused) == ("query_seconds", 2)


def test_a_child_out_of_cpu_time_is_refused_naming_query_seconds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``SIGXCPU`` at the soft limit of one CPU second, well before the deadline of 30 seconds,
    which a loaded host leaves room for; the hard limit's ``SIGKILL`` would name
    ``query_memory``."""
    monkeypatch.setattr(worker, "CPU_SLACK", 1 - 30)
    workers = Workers(QueryLimits(query_seconds=30))
    started = time.monotonic()
    with pytest.raises(QueryRefused) as refused:
        workers.run([], [Query(SLOW, {}, 1)])
    assert time.monotonic() - started < 20
    assert _limit(refused) == ("query_seconds", 30)


def _workers_left() -> list[int]:
    """The query workers this process started that are still running: children given its pid
    as the parent to watch."""
    found: list[int] = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            arguments = (entry / "cmdline").read_bytes().split(b"\0")
            stat = (entry / "stat").read_text()
        except OSError:
            continue
        parent = int(stat.rsplit(")", 1)[1].split()[1])
        if parent == os.getpid() and b"-P" in arguments and b"serve" in b" ".join(arguments):
            found.append(int(entry.name))
    return found


class _Late:
    """A place that is had only once the caller's deadline has passed."""

    def acquire(self, timeout: float) -> bool:
        time.sleep(timeout + 0.1)
        return True

    def release(self) -> None:
        pass


def test_a_place_had_after_the_caller_s_deadline_starts_no_child(
    monkeypatch: pytest.MonkeyPatch, spied: list[int]
) -> None:
    workers = Workers(QueryLimits())
    monkeypatch.setattr(workers, "_places", _Late())
    with pytest.raises(CallerDeadline):
        workers.run([], [Query("SELECT 1", {}, 1)], ends=time.monotonic() + 0.2)
    assert spied == []


def test_a_run_ends_by_its_caller_s_deadline_and_leaves_no_worker_running() -> None:
    """``ends`` before ``query_seconds`` ends the run then, its child killed, and the caller
    answers it by its own limit."""
    workers = Workers(QueryLimits(query_seconds=20))
    started = time.monotonic()
    with pytest.raises(CallerDeadline):
        workers.run([], [Query(SLOW, {}, 1)], ends=started + 1.5)
    assert 1.4 <= time.monotonic() - started < 3
    assert _workers_left() == []


def test_a_wait_for_a_place_ends_by_the_caller_s_deadline() -> None:
    workers = Workers(QueryLimits(query_seconds=20, query_workers=1))
    places = _places(workers)
    assert places.acquire(timeout=1)
    started = time.monotonic()
    try:
        with pytest.raises(CallerDeadline):
            workers.run([], [Query("SELECT 1", {}, 1)], ends=started + 0.5)
    finally:
        places.release()
    assert 0.4 <= time.monotonic() - started < 2


def test_a_child_that_hangs_up_and_runs_on_past_the_caller_s_deadline_ends_the_caller_s_way(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A child that closes its socket and keeps running is waited for until the caller's
    deadline, which then names the caller's own limit (``tool_seconds``, D301), not the
    worker's."""
    hang_up = "sock.close()\nimport time\ntime.sleep(60)\n"
    monkeypatch.setattr(worker, "_CHILD", _fake(hang_up))
    workers = Workers(QueryLimits(query_seconds=20))
    started = time.monotonic()
    with pytest.raises(CallerDeadline):
        workers.run([], [Query("SELECT 1", {}, 1)], ends=started + 1.0)
    assert 0.9 <= time.monotonic() - started < 3
    assert _workers_left() == []


def test_a_caller_s_deadline_after_the_worker_s_own_leaves_its_limits_named() -> None:
    workers = Workers(QueryLimits(query_seconds=1))
    with pytest.raises(QueryRefused) as refused:
        workers.run([], [Query(SLOW, {}, 1)], ends=time.monotonic() + 30)
    limit = refused.value.refusal.limit
    assert limit is not None
    assert (limit.name, limit.max) == ("query_seconds", 1)


def test_a_run_that_needs_more_memory_than_it_has_is_refused_naming_query_memory() -> None:
    workers = Workers(QueryLimits(query_memory=MIN_QUERY_MEMORY))
    with pytest.raises(QueryRefused) as refused:
        workers.run([], [Query(GREEDY, {}, 1)])
    assert _limit(refused) == ("query_memory", MIN_QUERY_MEMORY)


def test_a_memory_error_in_a_child_is_refused_naming_query_memory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Python's own ``MemoryError``, as the address-space backstop makes an allocation fail."""
    child = (
        "import sys\n"
        "from aibi.core.engine import duck\n"
        "def run(session, statements):\n"
        "    raise MemoryError\n"
        "duck.run = run\n"
        "duck.serve(int(sys.argv[1]), int(sys.argv[2]))\n"
    )
    monkeypatch.setattr(worker, "_CHILD", child)
    with pytest.raises(QueryRefused) as refused:
        Workers(QueryLimits()).run([], [Query("SELECT 1", {}, 1)])
    assert _limit(refused)[0] == "query_memory"


def test_a_child_that_holds_more_than_query_memory_is_killed_and_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The watch on the child's resident memory, which bounds what DuckDB does not count."""
    own = os.getpid()
    monkeypatch.setattr(worker, "_resident", lambda pid: 0 if pid == own else 1 << 40)
    with pytest.raises(QueryRefused) as refused:
        Workers(QueryLimits()).run([], [Query(SLOW, {}, 1)])
    assert _limit(refused)[0] == "query_memory"


def test_resident_memory_is_none_where_it_cannot_be_read_and_zero_for_a_child_not_reaped() -> None:
    unused = int(Path("/proc/sys/kernel/pid_max").read_text()) + 1
    assert worker._resident(unused) is None  # pyright: ignore[reportPrivateUsage]
    own = worker._resident(os.getpid())  # pyright: ignore[reportPrivateUsage]
    assert own is not None
    assert own > 0
    ended = subprocess.Popen([sys.executable, "-c", ""])
    try:
        os.waitid(os.P_PID, ended.pid, os.WEXITED | os.WNOWAIT)
        assert worker._resident(ended.pid) == 0  # pyright: ignore[reportPrivateUsage]
    finally:
        ended.wait(timeout=30)


def test_no_worker_runs_where_resident_memory_cannot_be_watched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(worker, "_resident", lambda pid: None)
    with pytest.raises(RuntimeError, match="/proc"):
        Workers(QueryLimits())
    own = os.getpid()
    monkeypatch.setattr(worker, "_resident", lambda pid: 0 if pid == own else None)
    started = time.monotonic()
    with pytest.raises(QueryError, match="resident memory"):
        Workers(QueryLimits()).run([], [Query(SLOW, {}, 1)])
    assert time.monotonic() - started < 5


@pytest.mark.parametrize(
    "death",
    [
        "held = bytearray(300 << 20)\nheld[::4096] = b'x' * len(range(0, len(held), 4096))\n"
        "os.abort()\n",
        "os.kill(os.getpid(), signal.SIGKILL)\n",
    ],
    ids=["an abort after holding memory", "a kill"],
)
def test_a_child_that_is_killed_or_aborts_is_always_refused_naming_query_memory(
    monkeypatch: pytest.MonkeyPatch, death: str
) -> None:
    """The child's end is waited for once its socket closes, never read before it is known
    (D293): the kernel's OOM killer sends ``SIGKILL``, and a failed allocation aborts."""
    monkeypatch.setattr(worker, "_CHILD", _fake(death))
    workers = Workers(QueryLimits())
    found: list[str] = []
    for _ in range(60):
        try:
            workers.run([], [Query("SELECT 1", {}, 1)])
        except QueryRefused as refused:
            found.append(refused.refusal.limit.name if refused.refusal.limit else "")
        except QueryError as error:
            found.append(str(error))
    assert found == ["query_memory"] * 60


def test_a_run_waits_for_a_place_at_most_query_seconds() -> None:
    workers = Workers(QueryLimits(query_seconds=1, query_workers=1))
    places = _places(workers)
    assert places.acquire(timeout=1)
    try:
        with pytest.raises(QueryRefused) as refused:
            workers.run([], [Query("SELECT 1", {}, 1)])
    finally:
        places.release()
    assert _limit(refused) == ("query_workers", 1)
    assert [list(rows) for rows in workers.run([], [Query("SELECT 1", {}, 1)])] == [[(1,)]]


def test_a_run_waits_for_a_place_that_frees() -> None:
    workers = Workers(QueryLimits(query_seconds=5, query_workers=1))
    places = _places(workers)
    assert places.acquire(timeout=1)
    freed = threading.Timer(0.5, places.release)
    freed.start()
    try:
        assert [list(rows) for rows in workers.run([], [Query("SELECT 1", {}, 1)])] == [[(1,)]]
    finally:
        freed.join()


def test_rows_larger_than_an_answer_may_be_are_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(worker, "MAX_QUERY_ANSWER_BYTES", 1000)
    with pytest.raises(QueryRefused) as refused:
        Workers(QueryLimits()).run([], [Query("SELECT range FROM range(1000)", {}, 1)])
    assert _limit(refused) == ("query_answer_bytes", 1000)


@pytest.mark.parametrize(
    "answer",
    [
        "sock.sendall(struct.pack('!Q', 1 << 62))\n",
        "sock.sendall(struct.pack('!Q', 0))\n",
        "send(b'V' + struct.pack('!IQ', 1, 5))\n",
        "send(b'V' + struct.pack('!IQI', 1, 1, 1))\n",
        "send(b'V' + struct.pack('!IQI', 1, 1, 1) + b'x' + b'\\0')\n",
        "send(b'V' + struct.pack('!I', 0))\n",
        "send(b'V' + struct.pack('!IQI', 1, 1, 1) + b'b' + b'\\1' + b'more')\n",
        "send(b'V' + struct.pack('!I', 2) + (struct.pack('!QI', 1, 1) + b'b\\1') * 2)\n",
        "send(b'V' + struct.pack('!IQI', 1, 1, 0))\n",
        "send(b'V' + struct.pack('!IQI', 1, 1, 2) + b'b\\1' * 2)\n",
        "send(b'E/srv/data/x y')\n",
        "import pickle\nsend(pickle.dumps(('value', [[(1,)]])))\n",
    ],
    ids=[
        "a frame too large to read",
        "an empty frame",
        "a truncated header",
        "a column without its type",
        "a column of an unknown type",
        "fewer results than queries",
        "bytes after the last column",
        "more results than queries",
        "a result of no columns",
        "a result of more columns than its query has",
        "an error that is not a class name",
        "a pickle",
    ],
)
def test_an_answer_the_child_does_not_write_is_a_fault_the_server_never_reads_past(
    monkeypatch: pytest.MonkeyPatch, answer: str
) -> None:
    monkeypatch.setattr(worker, "_CHILD", _fake("sock.recv(1 << 20)\n" + answer + "sock.recv(1)\n"))
    started = time.monotonic()
    with pytest.raises(QueryError) as failed:
        Workers(QueryLimits(query_seconds=5)).run([], [Query("SELECT 1", {}, 1)])
    assert time.monotonic() - started < 4
    assert "/srv" not in str(failed.value)


def test_an_answer_claiming_millions_of_columns_is_a_fault_found_fast_in_bounded_memory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """What the server makes of a frame is bounded by the queries it sent (D293): a child that
    claims 20 million empty columns makes it no view of each."""
    claim = "send(b'V' + struct.pack('!IQI', 1, 0, 20_000_000) + b'b' * 20_000_000)\n"
    monkeypatch.setattr(worker, "_CHILD", _fake("sock.recv(1 << 20)\n" + claim + "sock.recv(1)\n"))
    started = time.monotonic()
    tracemalloc.start()
    try:
        with pytest.raises(QueryError, match="not one the child writes"):
            Workers(QueryLimits(query_seconds=20)).run([], [Query("SELECT 1", {}, 1)])
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert time.monotonic() - started < 10
    assert peak < 2 * 20_000_000


def test_a_run_past_its_deadline_kills_the_child_s_whole_process_group(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    written = tmp_path / "pid"
    below = (
        "import time\n"
        "below = os.fork()\n"
        "if below == 0:\n"
        "    time.sleep(120)\n"
        "    os._exit(0)\n"
        f"open({str(written)!r}, 'w').write(str(below))\n"
        "sock.recv(1 << 20)\n"
        "time.sleep(120)\n"
    )
    monkeypatch.setattr(worker, "_CHILD", _fake(below))
    with pytest.raises(QueryRefused) as refused:
        Workers(QueryLimits(query_seconds=2)).run([], [Query("SELECT 1", {}, 1)])
    assert _limit(refused) == ("query_seconds", 2)
    pid = int(written.read_text())
    for _ in range(200):
        if _gone(pid):
            break
        time.sleep(0.05)
    assert _gone(pid)


def test_a_child_whose_server_is_not_its_parent_ends_at_once() -> None:
    """It was given the pid of the server that started it; any other parent means the server
    died before the child could ask to die with it."""
    unused = int(Path("/proc/sys/kernel/pid_max").read_text()) + 1
    ours, theirs = socket.socketpair()
    with ours, theirs:
        ended = subprocess.run(
            [sys.executable, "-P", "-c", worker._CHILD, str(theirs.fileno()), str(unused)],  # pyright: ignore[reportPrivateUsage]
            pass_fds=(theirs.fileno(),),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=60,
            check=False,
        )
    assert ended.returncode == 1


# --- What a child is given, may read and may run -------------------------------------------------


def test_a_child_reads_no_file_but_those_it_is_given_and_its_faults_name_no_path(
    tmp_path: Path,
) -> None:
    given, other = tmp_path / "given.csv", tmp_path / "other.csv"
    given.write_text("a\n1\n2\n")
    other.write_text("a\n1\n")
    workers = Workers(QueryLimits())
    read = "SELECT count(*) FROM read_csv($path)"
    assert [
        list(rows) for rows in workers.run([str(given)], [Query(read, {"path": str(given)}, 1)])
    ] == [[(2,)]]
    with pytest.raises(QueryError) as failed:
        workers.run([str(given)], [Query(read, {"path": str(other)}, 1)])
    assert str(failed.value) == "a query failed in its worker: PermissionException"
    assert str(tmp_path) not in repr(failed.value)


@pytest.mark.parametrize(
    "statement",
    [
        "COPY (SELECT 1 AS a) TO $path (FORMAT csv, USE_TMP_FILE false)",
        "SET enable_external_access = true",
        "SELECT 1; SELECT 2",
        "EXPLAIN SELECT 1",
    ],
)
def test_a_child_runs_one_select_alone_and_writes_no_file(tmp_path: Path, statement: str) -> None:
    given = tmp_path / "given.csv"
    given.write_text("a\n1\n")
    workers = Workers(QueryLimits())
    parameters = {"path": str(given)} if "$path" in statement else {}
    with pytest.raises(QueryError, match="NotAQuery"):
        workers.run([str(given)], [Query(statement, parameters, 1)])
    assert given.read_text() == "a\n1\n"


def test_a_session_is_configured_as_d293_says_and_locked() -> None:
    limits = QueryLimits(query_memory=1 << 30, query_threads=2)
    settings = [
        "(current_setting('TimeZone') = 'UTC')::INT",
        "current_setting('threads')::INT",
        "(current_setting('memory_limit') = '512.0 MiB')::INT",
        "current_setting('autoinstall_known_extensions')::INT",
        "current_setting('autoload_known_extensions')::INT",
        "(current_setting('temp_directory') = '')::INT",
        "current_setting('enable_external_access')::INT",
        "current_setting('lock_configuration')::INT",
    ]
    [rows] = Workers(limits).run([], [Query("SELECT " + ", ".join(settings), {}, len(settings))])
    assert list(rows) == [(1, 2, 1, 0, 0, 1, 0, 1)]
    code = (
        "from aibi.core.engine import duck\n"
        "connection = duck.connect(duck.Session((), 1 << 28))\n"
        "connection.execute('SET threads = 4')\n"
    )
    locked = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=60, check=False
    )
    assert locked.returncode != 0
    assert "configuration" in locked.stderr


@pytest.fixture
def spied(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """The pids of the children the workers launch."""
    launched: list[int] = []
    original = worker._launch  # pyright: ignore[reportPrivateUsage]

    def spy() -> Any:
        child = original()
        launched.append(child.process.pid)
        return child

    monkeypatch.setattr(worker, "_launch", spy)
    return launched


def _limits_of(pid: int) -> dict[str, tuple[str, str]]:
    found: dict[str, tuple[str, str]] = {}
    for line in Path(f"/proc/{pid}/limits").read_text().splitlines()[1:]:
        name, soft, hard = line[:26].strip(), line[26:47].strip(), line[47:68].strip()
        found[name] = (soft, hard)
    return found


def _settled(pid: int) -> dict[str, tuple[str, str]]:
    """The child's limits once it has set them, which it does before it reads the queries."""
    for _ in range(200):
        found = _limits_of(pid)
        if found["Max cpu time"][0] != "unlimited":
            return found
        time.sleep(0.05)
    raise AssertionError("the child set no limit")


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="reads the child's /proc")
def test_a_child_is_given_its_limits_and_nothing_of_the_servers_but_the_queries(
    monkeypatch: pytest.MonkeyPatch, spied: list[int]
) -> None:
    monkeypatch.setenv("AIBI_TEST_SECRET", SECRET)
    limits = QueryLimits(query_seconds=20, query_memory=1 << 30, query_threads=2)
    workers = Workers(limits)
    outcome: list[BaseException] = []

    def running() -> None:
        try:
            workers.run([], [Query(SLOW, {}, 1)])
        except BaseException as error:
            outcome.append(error)

    thread = threading.Thread(target=running)
    thread.start()
    try:
        for _ in range(200):
            if spied:
                break
            time.sleep(0.01)
        [pid] = spied
        found = _settled(pid)
        cpu = limits.query_seconds * limits.query_threads + worker.CPU_SLACK
        assert found["Max cpu time"] == (str(cpu), str(cpu + worker.CPU_GRACE))
        space = str(worker.ADDRESS_FACTOR * limits.query_memory + worker.ADDRESS_SLACK)
        assert found["Max address space"] == (space, space)
        assert found["Max core file size"] == ("0", "0")
        environment = Path(f"/proc/{pid}/environ").read_bytes()
        names = {entry.split(b"=", 1)[0] for entry in environment.split(b"\0") if entry}
        assert names == {b"PATH", b"PYTHONPATH", b"LANG", b"LC_ALL"}
        assert SECRET.encode() not in environment
        arguments = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
        assert arguments[1] == b"-P"
        assert {os.readlink(f"/proc/{pid}/fd/{fd}") for fd in (0, 1, 2)} == {"/dev/null"}
        assert Path(f"/proc/{pid}/oom_score_adj").read_text().strip() == "1000"
        assert os.getsid(pid) == pid
        os.killpg(pid, signal.SIGKILL)
    finally:
        thread.join(timeout=30)
    [error] = outcome
    assert isinstance(error, QueryRefused)
    assert error.refusal.limit is not None
    assert error.refusal.limit.name == "query_memory"


_ORPHAN = f"""
from aibi.core.engine import worker
from aibi.core.schema.limits import QueryLimits
original = worker._launch
def spy():
    child = original()
    print(child.process.pid, flush=True)
    return child
worker._launch = spy
worker.Workers(QueryLimits(query_seconds=60)).run([], [worker.Query({SLOW!r}, {{}}, 1)])
"""


def _gone(pid: int) -> bool:
    try:
        status = Path(f"/proc/{pid}/status").read_text()
    except FileNotFoundError:
        return True
    return "\nState:\tZ" in status


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="a Linux parent-death signal")
def test_a_child_dies_with_the_server() -> None:
    server = subprocess.Popen(
        [sys.executable, "-c", _ORPHAN], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
    )
    try:
        assert server.stdout is not None
        pid = int(server.stdout.readline())
        _settled(pid)
        server.kill()
        server.wait(timeout=30)
        for _ in range(200):
            if _gone(pid):
                break
            time.sleep(0.05)
        assert _gone(pid)
    finally:
        server.kill()
        server.wait(timeout=30)
        if server.stdout is not None:
            server.stdout.close()


def test_the_limits_refuse_values_a_worker_cannot_run_with() -> None:
    for given in (
        {"query_seconds": 0},
        {"query_workers": 0},
        {"query_threads": 0},
        {"query_memory": MIN_QUERY_MEMORY - 1},
    ):
        with pytest.raises(ValueError, match="at least"):
            QueryLimits(**given)


def test_the_servers_process_never_loads_duckdb() -> None:
    """The compiler, the worker and the store import no DuckDB: only a child does."""
    code = (
        "import sys; import aibi.core.engine.queries, aibi.core.engine.sql, "
        "aibi.core.store.store; print('duckdb' in sys.modules)"
    )
    found = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True, timeout=60
    )
    assert found.stdout.strip() == "False"

"""What both loaders share: the pause of the cyclic garbage collector, and how unions are chosen."""

import ast
import gc
import json
import os
import signal
import sys
import threading
import time
import warnings
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from pydantic import TypeAdapter

import aibi.core.schema
from aibi.core.schema import loading
from aibi.core.schema.descriptors import Descriptor
from aibi.core.schema.document import Document
from aibi.core.schema.limits import MAX_DOCUMENT_BYTES
from aibi.core.schema.loading import _paused, load_descriptor, load_document, load_request
from aibi.core.schema.operator import ImportRequest

DOCUMENT = {"aibi": "1", "dataset": "d", "unit": "t", "cohorts": {"c": {"all": []}}}
TABLE = {
    "kind": "table",
    "id": "t",
    "version": 1,
    "label": "T",
    "fields": {},
    "extensions": {},
    "curation": {
        "/label": {"status": "imported", "by": "importer:x@1", "at": "2026-01-01T00:00:00Z"}
    },
}
LOADS: dict[str, Callable[[], object]] = {
    "document": lambda: load_document(json.dumps(DOCUMENT)),
    "wrong document": lambda: load_document(json.dumps({**DOCUMENT, "unit": 5, "x": None})),
    "unparseable document": lambda: load_document("{"),
    "large document": lambda: load_document(" " * (MAX_DOCUMENT_BYTES + 1)),
    "descriptor": lambda: load_descriptor(json.dumps(TABLE)),
    "wrong descriptor": lambda: load_descriptor(json.dumps({**TABLE, "version": None})),
    "unparseable descriptor": lambda: load_descriptor("[1,"),
    "request": lambda: load_request(b'{"source": {"path": "/x"}}', ImportRequest),
    "wrong request": lambda: load_request(b'{"source": 5, "x": null}', ImportRequest),
}


@pytest.fixture
def collector() -> Iterator[None]:
    enabled = gc.isenabled()
    yield
    if enabled:
        gc.enable()


@pytest.mark.parametrize("load", LOADS.values(), ids=LOADS.keys())
@pytest.mark.usefixtures("collector")
def test_a_load_leaves_the_collector_as_it_found_it(load: Callable[[], object]) -> None:
    gc.enable()
    load()
    assert gc.isenabled()
    gc.disable()
    load()
    assert not gc.isenabled()


def _off() -> None:
    assert not gc.isenabled()


def _nested_then_raise() -> None:
    assert not gc.isenabled()
    _paused(_off)
    assert not gc.isenabled()
    raise RuntimeError


@pytest.mark.usefixtures("collector")
def test_pauses_nest_and_end_on_an_exception() -> None:
    gc.enable()
    with pytest.raises(RuntimeError):
        _paused(_nested_then_raise)
    assert gc.isenabled()


@pytest.mark.usefixtures("collector")
def test_loads_in_several_threads_share_one_pause() -> None:
    """The collector's state belongs to the process: a load that ends while another runs must
    neither enable it under the other nor leave it disabled."""
    gc.enable()
    interval = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)  # switch threads as often as possible
    failures: list[BaseException] = []

    def work() -> None:
        try:
            for _ in range(60):
                for load in LOADS.values():
                    load()
        except BaseException as error:
            failures.append(error)

    try:
        threads = [threading.Thread(target=work) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
    finally:
        sys.setswitchinterval(interval)
    assert not failures
    assert gc.isenabled()


def test_every_union_is_chosen_by_a_function() -> None:
    """Every discriminated union is chosen by a function that returns one of its tags, never by
    a member's name, so Pydantic's own tag errors, which the loaders do not map, never arise."""
    root = Path(next(iter(aibi.core.schema.__path__)))
    for module in sorted(root.glob("*.py")):
        for node in ast.walk(ast.parse(module.read_text())):
            if not isinstance(node, ast.Call):
                continue
            name = node.func.id if isinstance(node.func, ast.Name) else None
            if name == "Discriminator":
                assert node.args, module.name
                assert not isinstance(node.args[0], ast.Constant), module.name
            assert all(keyword.arg != "discriminator" for keyword in node.keywords), module.name


# --- Round 4: the pause under races, exceptions, forks and reentry; union functions -------------


class _Switching:
    """The collector, with a thread switch around every call, so that a race in the pause's
    bookkeeping shows."""

    def isenabled(self) -> bool:
        time.sleep(0)
        enabled = gc.isenabled()
        time.sleep(0)
        return enabled

    def disable(self) -> None:
        time.sleep(0)
        gc.disable()
        time.sleep(0)

    def enable(self) -> None:
        time.sleep(0)
        gc.enable()
        time.sleep(0)


@pytest.mark.usefixtures("collector")
def test_the_collector_is_off_inside_every_pause_whatever_the_threads_do(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(loading, "gc", _Switching())
    gc.enable()
    wrong: list[str] = []

    def check() -> None:
        if gc.isenabled():
            wrong.append("on inside a pause")

    def work() -> None:
        for _ in range(300):
            _paused(check)

    threads = [threading.Thread(target=work) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert wrong == []
    assert gc.isenabled()
    assert loading._PAUSE.depth == 0  # pyright: ignore[reportPrivateUsage]


class _Interrupted(_Switching):
    """A signal handler's exception, raised just after the collector is disabled."""

    def disable(self) -> None:
        gc.disable()
        raise KeyboardInterrupt


@pytest.mark.usefixtures("collector")
def test_an_exception_as_the_pause_begins_still_ends_it(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(loading, "gc", _Interrupted())
    gc.enable()
    with pytest.raises(KeyboardInterrupt):
        _paused(_off)
    assert gc.isenabled()
    assert loading._PAUSE.depth == 0  # pyright: ignore[reportPrivateUsage]


@pytest.mark.usefixtures("collector")
def test_a_forked_child_starts_with_no_pause() -> None:
    """The child of a fork made while a load ran holds no load, and a lock no one holds; the
    pause that was running ends nothing in the child (a signal handler's fork)."""
    pause = loading._PAUSE  # pyright: ignore[reportPrivateUsage]
    gc.enable()
    locks: list[object] = []

    def fork_inside() -> None:
        locks.append(pause.lock)
        loading._after_fork_in_child()  # pyright: ignore[reportPrivateUsage]
        assert gc.isenabled()
        assert pause.depth == 0
        assert pause.lock is not locks[0]

    _paused(fork_inside)
    assert pause.depth == 0  # not -1: the pause began in the parent's generation
    _paused(_off)  # later pauses still pause
    assert gc.isenabled()


@pytest.mark.usefixtures("collector")
def test_a_load_by_the_thread_holding_the_pause_lock_does_not_deadlock() -> None:
    """A signal handler may load while its thread holds the lock."""
    gc.enable()
    with loading._PAUSE.lock:  # pyright: ignore[reportPrivateUsage]
        assert load_descriptor("{}").refusals
    assert gc.isenabled()


def _tagged_unions(schema: Any) -> Iterator[dict[str, Any]]:
    pending = [schema]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, dict):
            if current.get("type") == "tagged-union":
                yield current
            pending.extend(current.values())
        elif isinstance(current, list):
            pending.extend(current)


_PROBES: list[object] = [
    None,
    True,
    0,
    1.5,
    "x",
    "",
    [],
    [1],
    {},
    {"kind": None},
    {"kind": 1},
    {"kind": "x"},
    {"kind": "value"},
    {"all": 1},
    {"all": [], "any": []},
    {"op": "x"},
    object(),
]


@pytest.mark.parametrize("root", [Document, Descriptor], ids=["document", "descriptor"])
def test_every_union_function_returns_one_of_its_tags_or_has_its_own_error(root: object) -> None:
    """A function that returns None needs the union's custom_error_type: Pydantic's own tag
    errors are not mapped by the loaders."""
    schema = TypeAdapter(root).core_schema
    unions = list(_tagged_unions(schema))
    assert unions
    for union in unions:
        choose = union["discriminator"]
        if not callable(choose) or "custom_error_type" in union:
            continue
        for probe in _PROBES:
            assert choose(probe) in union["choices"], (choose, probe)


# --- Round 5: a pause cut short, an interrupted wait, and real forks ------------------------------


class _InterruptedReading(_Switching):
    """A signal handler's exception, raised as the collector's state is read."""

    def isenabled(self) -> bool:
        gc.isenabled()
        raise KeyboardInterrupt


@pytest.mark.usefixtures("collector")
def test_a_pause_cut_short_before_it_reads_the_collector_enables_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The last pause to end forgets that the collector was on, so a pause interrupted before it
    reads the collector's state does not enable a collector the application has turned off."""
    gc.enable()
    load_descriptor("{}")
    gc.disable()
    monkeypatch.setattr(loading, "gc", _InterruptedReading())
    with pytest.raises(KeyboardInterrupt):
        _paused(_off)
    assert not gc.isenabled()
    assert loading._PAUSE.depth == 0  # pyright: ignore[reportPrivateUsage]


class _OnceInterruptedLock:
    """A lock whose wait is interrupted once, after ``calm`` waits."""

    def __init__(self, calm: int) -> None:
        self.lock = threading.RLock()
        self.calm = calm

    def acquire(self) -> bool:
        if self.calm == 0:
            self.calm = -1
            raise KeyboardInterrupt
        self.calm -= 1
        return self.lock.acquire()

    def release(self) -> None:
        self.lock.release()

    def __enter__(self) -> "_OnceInterruptedLock":
        self.acquire()
        return self

    def __exit__(self, *raised: object) -> None:
        self.release()


@pytest.mark.usefixtures("collector")
def test_an_interrupted_wait_to_end_the_pause_still_ends_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gc.enable()
    pause = loading._PAUSE  # pyright: ignore[reportPrivateUsage]
    monkeypatch.setattr(pause, "lock", _OnceInterruptedLock(calm=1))
    with pytest.raises(KeyboardInterrupt):
        _paused(_off)
    assert pause.depth == 0
    assert gc.isenabled()


def _in_child(check: Callable[[], bool]) -> bool:
    """Whether ``check`` holds in a forked child, which exits at once and is ended by an alarm
    if it hangs."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)  # forking beside other threads
        pid = os.fork()
    if pid == 0:  # pragma: no cover - the child's result is its exit status
        code = 1
        try:
            signal.alarm(10)
            code = 0 if check() else 1
        finally:
            os._exit(code)
    _, status = os.waitpid(pid, 0)
    return os.waitstatus_to_exitcode(status) == 0


def _loads_with_the_collector_off() -> bool:
    return bool(load_descriptor("{}").refusals) and not gc.isenabled()


_FORK = pytest.mark.skipif(not hasattr(os, "fork"), reason="no fork on this platform")


@_FORK
@pytest.mark.usefixtures("collector")
def test_a_fork_with_no_load_running_keeps_the_collector_off() -> None:
    gc.disable()
    assert _in_child(_loads_with_the_collector_off)


@_FORK
@pytest.mark.usefixtures("collector")
def test_a_fork_inside_a_pause_begun_with_the_collector_off_keeps_it_off() -> None:
    gc.disable()
    held: list[bool] = []
    _paused(lambda: held.append(_in_child(_loads_with_the_collector_off)))
    assert held == [True]


@_FORK
@pytest.mark.usefixtures("collector")
def test_a_child_forked_while_another_thread_holds_the_pause_lock_can_load() -> None:
    gc.enable()
    holding, done = threading.Event(), threading.Event()

    def hold() -> None:
        with loading._PAUSE.lock:  # pyright: ignore[reportPrivateUsage]
            holding.set()
            done.wait(20)

    thread = threading.Thread(target=hold)
    thread.start()
    try:
        holding.wait(20)
        assert _in_child(lambda: bool(load_descriptor("{}").refusals) and gc.isenabled())
    finally:
        done.set()
        thread.join()

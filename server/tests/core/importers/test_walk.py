"""The walk alone (SPEC §14, D232, issue #146): a directory opened from ``/`` one name at a time,
no symlink followed, tested apart from the confinement that uses it, so that neither hides the
other's failure. Every test of the walk runs twice: with ``O_PATH``, and with the module loaded
as where the system has none (macOS), whose walk opens the directories it passes ``O_RDONLY``.

A component at every position is swapped for a symlink out (or toggled between the two at every
pair and every triple of the walk's calls), and a thread flips one while walks run: the walk
refuses, or gives the directory inside, never one outside. Every open after ``/`` is one name under
a descriptor, with ``O_NOFOLLOW`` and ``O_DIRECTORY``; at most two descriptors are held, and none
is left open on any exit; a failure names nothing the walk met."""

import errno
import importlib.util
import itertools
import os
import shutil
import tempfile
import threading
import time
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from aibi.core.importers import walk as walk_module

NAMES = ("c1", "c2", "c3", "c4")
"""The components below the base of the directory walked to."""
MARK = "QZXplantedcomponent"
"""A component's name no message may hold."""


def _without_o_path() -> ModuleType:
    """The module as it loads where ``os`` has no ``O_PATH``: a copy, ``sys.modules`` untouched."""
    spec = importlib.util.find_spec(walk_module.__name__)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    saved = os.O_PATH
    del os.O_PATH
    try:
        spec.loader.exec_module(module)
    finally:
        os.O_PATH = saved
    return module


@pytest.fixture(params=["o_path", "no_o_path"])
def walking(request: pytest.FixtureRequest) -> ModuleType:
    return walk_module if request.param == "o_path" else _without_o_path()


def test_the_module_loads_without_o_path_and_passes_directories_read_only() -> None:
    bound = os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    fallback = _without_o_path()
    flags = {"SEARCH": os.O_PATH | bound, "LIST": os.O_RDONLY | bound}
    assert {name: getattr(walk_module, name) for name in flags} == flags
    flags["SEARCH"] = os.O_RDONLY | bound
    assert {name: getattr(fallback, name) for name in flags} == flags


def _descriptors() -> set[str]:
    return set(os.listdir("/proc/self/fd"))


def _identity(descriptor: int) -> tuple[int, int]:
    status = os.fstat(descriptor)
    return status.st_dev, status.st_ino


def _of(path: Path) -> tuple[int, int]:
    status = os.stat(path, follow_symlinks=False)
    return status.st_dev, status.st_ino


class _Tree:
    """``inside/c1/c2/c3/c4``, and for each position ``m`` an outside directory holding the
    components below it, so that the component at ``m`` swapped for a symlink to it makes the
    path name a directory outside."""

    def __init__(self, base: Path) -> None:
        self.inside = base / "inside"
        self.target = self.inside.joinpath(*NAMES)
        self.target.mkdir(parents=True)
        self.outside = base / "outside"
        for m in range(len(NAMES)):
            self.outside.joinpath(f"m{m}", *NAMES[m + 1 :]).mkdir(parents=True)
        self.swapped: int | None = None

    def component(self, m: int) -> Path:
        return self.inside.joinpath(*NAMES[: m + 1])

    def swap(self, m: int) -> None:
        os.rename(self.component(m), self.component(m).with_name("kept"))
        os.symlink(self.outside / f"m{m}", self.component(m))
        self.swapped = m

    def unswap(self) -> None:
        if self.swapped is not None:
            os.unlink(self.component(self.swapped))
            kept = self.component(self.swapped).with_name("kept")
            os.rename(kept, self.component(self.swapped))
            self.swapped = None

    def toggle(self, m: int) -> None:
        if self.swapped is None:
            self.swap(m)
        else:
            self.unswap()


class _Opens:
    """``os.open`` replaced: each call recorded, and ``before`` run before the call numbered by
    the key it is under (from 0)."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, before: dict[int, Callable[[], None]]):
        self.calls: list[tuple[str, int, int | None]] = []
        self.before = before
        self.real = os.open
        monkeypatch.setattr(os, "open", self.open)

    def open(self, path: Any, flags: int, *rest: Any, **given: Any) -> int:
        index = len(self.calls)
        self.calls.append((os.fspath(path), flags, given.get("dir_fd")))
        if index in self.before:
            self.before[index]()
        return self.real(path, flags, *rest, **given)


LASTS = {"search": "SEARCH", "list": "LIST"}


def test_a_walk_opens_the_directory_itself(walking: ModuleType, tmp_path: Path) -> None:
    tree = _Tree(tmp_path)
    for last in LASTS.values():
        descriptor = walking.walk(tree.target, getattr(walking, last))
        try:
            assert _identity(descriptor) == _of(tree.target)
        finally:
            os.close(descriptor)


@pytest.mark.parametrize("last", LASTS)
@pytest.mark.parametrize("m", range(len(NAMES)))
def test_a_symlink_at_any_component_stops_the_walk(
    walking: ModuleType, tmp_path: Path, m: int, last: str
) -> None:
    """Including the last (``c4``), whatever the symlink names, and a file in a directory's
    place."""
    tree = _Tree(tmp_path)
    before = _descriptors()
    tree.swap(m)
    with pytest.raises(walking.Unwalked) as stopped:
        walking.walk(tree.target, getattr(walking, LASTS[last]))
    assert stopped.value.errno in (errno.ELOOP, errno.ENOTDIR)
    tree.unswap()
    os.rename(tree.component(m), tree.component(m).with_name("kept"))
    tree.component(m).write_bytes(b"a file\n")
    with pytest.raises(walking.Unwalked) as stopped:
        walking.walk(tree.target, getattr(walking, LASTS[last]))
    assert stopped.value.errno == errno.ENOTDIR
    assert _descriptors() == before


def test_the_last_component_is_always_a_directory_and_never_a_followed_symlink(
    walking: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Whatever flags a caller passes for the last component (here ``O_RDONLY`` alone), the walk
    adds ``O_DIRECTORY``, ``O_NOFOLLOW`` and ``O_CLOEXEC``: a symlink there is not followed and
    a file there is no directory, as it is for every component before it."""
    bound = os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    tree = _Tree(tmp_path)
    opens = _Opens(monkeypatch, {})
    os.close(walking.walk(tree.target, os.O_RDONLY))
    assert opens.calls[-1][1] == os.O_RDONLY | bound
    monkeypatch.undo()
    before = _descriptors()
    tree.swap(len(NAMES) - 1)  # the last component, a symlink to a directory outside
    with pytest.raises(walking.Unwalked) as followed:
        walking.walk(tree.target, os.O_RDONLY)
    assert followed.value.errno in (errno.ELOOP, errno.ENOTDIR)
    tree.unswap()
    os.rename(tree.target, tree.target.with_name("kept"))
    tree.target.write_bytes(b"a file\n")  # the last component, a file
    with pytest.raises(walking.Unwalked) as file:
        walking.walk(tree.target, os.O_RDONLY)
    assert file.value.errno == errno.ENOTDIR
    assert _descriptors() == before


def test_a_symlink_above_the_base_stops_the_walk_too(walking: ModuleType, tmp_path: Path) -> None:
    """The walk starts at ``/``: a symlink among the ancestors (a root's own components, to the
    confinement) is refused as any other is, so the walk of a path is never relative to a
    directory opened by its path text."""
    _Tree(tmp_path / "real")
    (tmp_path / "link").symlink_to(tmp_path / "real")
    with pytest.raises(walking.Unwalked):
        walking.walk(tmp_path / "link" / "inside" / Path(*NAMES))


@pytest.mark.parametrize("size", [1, 2, 3])
@pytest.mark.parametrize("m", range(len(NAMES)))
def test_a_component_toggled_at_any_calls_never_leads_outside(
    walking: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, m: int, size: int
) -> None:
    """The component at ``m`` swapped for a symlink out, or back, before each of ``size`` of the
    walk's opens, at every such set of them (every single call, pair and triple, and the end):
    the walk gives the directory inside or refuses."""
    tree = _Tree(tmp_path)
    inside = _of(tree.target)
    count = len(tree.target.parts)
    tried = refused = 0
    before = _descriptors()
    for last in LASTS.values():
        for at in itertools.combinations(range(count + 1), size):
            with monkeypatch.context() as patched:
                opens = _Opens(patched, {index: lambda: tree.toggle(m) for index in at})
                try:
                    descriptor = walking.walk(tree.target, getattr(walking, last))
                except walking.Unwalked:
                    refused += 1
                else:
                    try:
                        assert _identity(descriptor) == inside, (at, last)
                    finally:
                        os.close(descriptor)
                assert len(opens.calls) == count or refused
            tree.unswap()
            tried += 1
    assert refused, "no toggle ever reached the walk"
    assert tried == 2 * len(list(itertools.combinations(range(count + 1), size)))
    assert _descriptors() == before


RACE_WALKS = 3000
"""At least this many walks race the flipper."""
RACE_OBSERVED = 5
"""And the race runs on until this many walks were given the directory, and this many refused:
progress that does not depend on how a loaded machine hands the GIL over."""
RACE_DEADLINE = 120.0
"""Seconds, a generous bound that only a machine that starves a thread for minutes passes."""


def test_a_thread_flipping_a_component_never_leads_a_walk_outside(
    walking: ModuleType, tmp_path: Path
) -> None:
    """A race of 3,000 walks at least, run on until the walks have seen the flipper both ways (at
    least ``RACE_OBSERVED`` given the directory and as many refused), within a deadline, while
    another thread flips ``c2`` between the directory and a symlink out; each walk refuses or
    gives the directory inside, and the counts seen are asserted, so that the check below always
    checked something."""
    tree = _Tree(tmp_path)
    inside = _of(tree.target)
    stop = threading.Event()

    def flip() -> None:
        while not stop.is_set():
            try:
                tree.swap(1)
                tree.unswap()
            except OSError:
                pass

    flipper = threading.Thread(target=flip, daemon=True)
    outside = given = refused = walks = 0
    before = _descriptors()
    deadline = time.monotonic() + RACE_DEADLINE
    flipper.start()
    try:
        while (walks < RACE_WALKS or min(given, refused) < RACE_OBSERVED) and (
            time.monotonic() < deadline
        ):
            walks += 1
            try:
                descriptor = walking.walk(tree.target, walking.LIST)
            except walking.Unwalked:
                refused += 1
                continue
            given += 1
            try:
                outside += _identity(descriptor) != inside
            finally:
                os.close(descriptor)
    finally:
        stop.set()
        flipper.join()
    assert outside == 0
    assert given >= RACE_OBSERVED, (given, refused, walks)
    assert refused >= RACE_OBSERVED, (given, refused, walks)
    assert _descriptors() == before


@pytest.mark.parametrize("last", LASTS)
def test_every_open_after_the_root_is_one_name_under_a_descriptor_never_followed(
    walking: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, last: str
) -> None:
    tree = _Tree(tmp_path)
    opens = _Opens(monkeypatch, {})
    os.close(walking.walk(tree.target, getattr(walking, LASTS[last])))
    monkeypatch.undo()
    (first, first_flags, first_parent), *rest = opens.calls
    assert (first, first_parent) == ("/", None)
    assert len(rest) == len(tree.target.parts) - 1
    passed = os.O_PATH if walking.SEARCH & os.O_PATH else os.O_RDONLY
    for index, (name, flags, parent) in enumerate([("/", first_flags, None), *rest]):
        assert index == 0 or parent is not None, name
        assert name == "/" or ("/" not in name and name not in ("", ".", "..")), name
        for flag in (os.O_NOFOLLOW, os.O_DIRECTORY, os.O_CLOEXEC):
            assert flags & flag, (name, flag)
        if index < len(rest):
            assert flags & ~(os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC) == passed, name
    assert rest[-1][1] == getattr(walking, LASTS[last])


def test_a_walk_holds_two_descriptors_at_most_and_closes_them_on_every_exit(
    walking: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """At each open, at most one other of the walk's is held; failing any open, after any number
    succeeded, leaves none open; and so does the walk that succeeds once its result is closed."""
    tree = _Tree(tmp_path)
    count = len(tree.target.parts)
    before = _descriptors()
    held: list[int] = []

    def measuring() -> None:
        held.append(len(_descriptors() - before))

    with monkeypatch.context() as patched:
        _Opens(patched, dict.fromkeys(range(count), measuring))
        os.close(walking.walk(tree.target))
    assert held == [0] + [1] * (count - 1)
    for at in range(count):

        def failing() -> None:
            raise OSError(errno.EIO, "injected")

        with monkeypatch.context() as patched:
            _Opens(patched, {at: failing})
            with pytest.raises(walking.Unwalked):
                walking.walk(tree.target)
        assert _descriptors() == before, at


def test_root_itself_has_no_component_and_is_opened_with_the_last_flags(
    walking: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    opens = _Opens(monkeypatch, {})
    descriptor = walking.walk(Path("/"), walking.LIST)
    monkeypatch.undo()
    try:
        assert opens.calls == [("/", walking.LIST, None)]
        assert _identity(descriptor) == _of(Path("/"))
        assert os.listdir(descriptor)
    finally:
        os.close(descriptor)


@pytest.mark.parametrize("given", ["relative/dir", "/a/../b", "//a/b", "/a/.."])
def test_a_path_not_absolute_and_resolved_is_refused_before_anything_is_opened(
    walking: ModuleType, monkeypatch: pytest.MonkeyPatch, given: str
) -> None:
    opens = _Opens(monkeypatch, {})
    with pytest.raises(ValueError, match="not an absolute path"):
        walking.walk(Path(given))
    assert opens.calls == []


def test_a_failure_names_nothing_the_walk_met(walking: ModuleType, tmp_path: Path) -> None:
    """Neither the component's name nor its step: the errno alone, raised from nothing."""
    target = tmp_path / MARK / "below"
    (tmp_path / MARK).mkdir()
    with pytest.raises(walking.Unwalked) as missing:
        walking.walk(target)
    error = missing.value
    assert error.errno == errno.ENOENT
    assert (error.filename, error.filename2) == (None, None)
    for shown in (str(error), repr(error), str(error.args)):
        assert MARK not in shown
    assert error.__cause__ is None
    assert error.__suppress_context__
    for name in (MARK + "\0", MARK + "\ud800"):  # a NUL, a lone surrogate
        with pytest.raises(ValueError, match="not a name the system takes") as unnamed:
            walking.walk(tmp_path / name)
        assert MARK not in str(unnamed.value)
        assert unnamed.value.__cause__ is None
        assert unnamed.value.__suppress_context__


def _search_only(module: ModuleType, base: Path) -> bytes:
    """What a child process (unprivileged: ``nobody`` when this one is root, whose permission
    checks would pass anyway) sees walking through ``base/x/so/y``, ``so`` being searchable but
    not readable: ``ok`` when the walk passes it, else the errno."""
    target = base / "x" / "so" / "y"
    target.mkdir(parents=True)
    for directory in (base, base / "x"):
        os.chmod(directory, 0o711)
    os.chmod(target, 0o755)  # listed: read, as the final directory of a listing must be
    os.chmod(base / "x" / "so", 0o111)
    read, write = os.pipe()
    child = os.fork()
    if child == 0:  # pragma: no cover - the child reports through the pipe
        os.close(read)
        try:
            if os.geteuid() == 0:
                os.setgroups([])
                os.setgid(65534)
                os.setuid(65534)
            os.close(module.walk(target, module.LIST))
            said = b"ok"
        except module.Unwalked as error:
            said = errno.errorcode.get(error.errno or 0, "?").encode()
        except BaseException as error:  # reported whatever it is: the child exits
            said = repr(error).encode()
        os.write(write, said)
        os._exit(0)
    os.close(write)
    os.waitpid(child, 0)
    said = os.read(read, 1000)
    os.close(read)
    return said


@pytest.fixture
def open_base() -> Any:
    """A directory under the temporary directory itself (``/tmp``, which everyone can search),
    so that a child that drops to ``nobody`` reaches it; removed after."""
    base = Path(tempfile.mkdtemp(prefix="aibi-walk-"))
    os.chmod(base, 0o711)
    yield base
    # Top-down, each subdirectory made searchable and readable before ``os.walk`` descends into it,
    # whichever a test left unreadable (mode 0o111, say, which a walk cannot list to restore it).
    os.chmod(base, 0o700)
    for directory, subdirectories, _ in os.walk(base):
        for name in subdirectories:
            if not os.path.islink(os.path.join(directory, name)):
                os.chmod(os.path.join(directory, name), 0o700)
    shutil.rmtree(base)


def test_a_directory_that_can_be_searched_and_not_read_is_passed_through(open_base: Path) -> None:
    """``O_PATH`` needs the search permission alone, as the kernel's own resolution does; the
    module without it needs the read permission, and refuses (the stated limit where the system
    has no ``O_PATH``). It runs, never skips: as root, in a child that is not."""
    assert _search_only(walk_module, open_base / "o_path") == b"ok"
    assert _search_only(_without_o_path(), open_base / "no_o_path") == b"EACCES"

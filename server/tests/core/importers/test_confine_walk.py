"""Confinement through the walk (SPEC §14, D232, D253, issue #146): what is at a confined path is
reached from ``/`` one name at a time, never through path text after ``realpath``, for the
identity recorded, the read and the listing, in the operator's mode and for a pack's path.

A middle directory swapped for a symlink out of the roots (at any call of an operation, swapped
back or not, by a thread racing it, an ancestor of a root included) leaks no byte and no identity;
the database snapshot, which trusts the identity, refuses the swapped file. What the real swap is
refused with is each operation's text (one test each, the stated change from the module before the
walk), and nothing it says names the component. Roots may not nest; a root is a walk of no
component below it; a listing records each file's identity as listed and follows no symlink; a
pack's ``..`` may follow a directory only; a confinement out of descriptors says so, never that the
file changed or is missing; a directory that can be searched and not read is passed through."""

import errno
import itertools
import os
import resource
import shutil
import sqlite3
import tempfile
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import pytest

from aibi.core.importers import snapshot
from aibi.core.importers.confine import Confinement
from aibi.core.importers.databases import Connection, resolve
from aibi.core.importers.errors import ImportRefused
from aibi.core.importers.walk import LIST, SEARCH
from aibi.core.schema.limits import ImportLimits
from aibi.core.schema.pack_api import ConfinedPath

LEVELS = ("l1", "l2", "l3")
MISSING = "No such file or directory: "
CANNOT = "The file cannot be opened as confined: "
NOT_LISTABLE = "Not a directory that can be opened as confined: "
CHANGED = "The file changed after it was confined: "
SECRET = b"SECRET\n"
MARK = "QZXplantedcomponent"
READ = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK | os.O_NOCTTY


class _Tree:
    """``imports/l1/l2/l3/a.txt`` (and ``b.txt``), and for each level ``m`` an outside directory
    that holds the levels below it, so that ``l1``…``l(m+1)`` swapped for a symlink to it makes
    the same path name an outside file, whose bytes are ``SECRET``."""

    def __init__(self, base: Path, levels: tuple[str, ...] = LEVELS) -> None:
        self.levels = levels
        self.inside = base / "imports"
        self.outside = base / "elsewhere"
        self.directory = self.inside.joinpath(*levels)
        self.directory.mkdir(parents=True)
        self.leaf = self.directory / "a.txt"
        self.leaf.write_bytes(b"inside\n")
        (self.directory / "b.txt").write_bytes(b"b\n")
        for m in range(len(levels)):
            below = self.outside.joinpath(f"m{m}", *levels[m + 1 :])
            below.mkdir(parents=True)
            (below / "a.txt").write_bytes(SECRET)
            (below / "b.txt").write_bytes(SECRET)
        self.swapped: int | None = None

    def component(self, m: int) -> Path:
        return self.inside.joinpath(*self.levels[: m + 1])

    def outside_identity(self, m: int, name: str = "a.txt") -> tuple[int, int]:
        status = os.stat(self.outside.joinpath(f"m{m}", *self.levels[m + 1 :], name))
        return status.st_dev, status.st_ino

    def swap(self, m: int) -> None:
        os.rename(self.component(m), self.component(m).with_name("kept"))
        os.symlink(self.outside / f"m{m}", self.component(m))
        self.swapped = m

    def unswap(self) -> None:
        if self.swapped is not None:
            os.unlink(self.component(self.swapped))
            os.rename(self.component(self.swapped).with_name("kept"), self.component(self.swapped))
            self.swapped = None


def _text(refused: pytest.ExceptionInfo[ImportRefused] | ImportRefused) -> str:
    error = refused if isinstance(refused, ImportRefused) else refused.value
    [refusal] = error.refusals
    assert refusal.code == "PATH_NOT_CONFINED"
    return refusal.message[0].model_dump()["text"]


def _descriptors() -> int:
    return len(os.listdir("/proc/self/fd"))


# --- What each operation opens ----------------------------------------------------------------

OPERATIONS = ("read", "files", "identity", "read-pack", "files-pack")
WALKS = {"read": 1, "files": 1, "identity": 1, "read-pack": 2, "files-pack": 2}
"""The walks of each operation on a path: one, but for a pack's path, walked for its identity and
then for the read or the listing (``_unlinked`` ``lstat``s its components through the path as
written, opening nothing)."""


def _operation(confinement: Confinement, tree: _Tree, operation: str) -> Callable[[], Any]:
    """The operation on the tree's file or directory, what it is given confined beforehand (a
    pack's path joined below its confined source, through a ``..`` the walk never takes)."""
    if operation == "identity":
        return lambda: confinement.confine(tree.leaf)
    if operation == "read":
        confined = confinement.confine(tree.leaf)
        return lambda: confinement.read(confined, 100)
    if operation == "files":
        directory = confinement.confine(tree.directory)
        return lambda: confinement.files(directory)
    below = Path(confinement.confine(tree.inside / "l1")) / "l2" / ".." / "l2" / "l3"
    if operation == "read-pack":
        return lambda: confinement.read(ConfinedPath(below / "a.txt"), 100)
    return lambda: confinement.files(ConfinedPath(below))


@pytest.mark.parametrize("operation", OPERATIONS)
def test_every_open_is_one_name_under_a_descriptor_after_one_root_per_walk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    """No path text is opened but ``/``, once per walk and as its first open; every other open
    is one name under the descriptor before it, never ``..``, with ``O_NOFOLLOW``; a directory
    passed through is ``O_PATH|O_DIRECTORY``, the file read is opened once with the read's flags
    by its own name, the directory listed once with ``O_RDONLY|O_DIRECTORY``. No ``stat`` is
    made by path text after ``realpath``: the identity's is the leaf's name under its walked
    directory."""
    tree = _Tree(tmp_path)
    confinement = Confinement.of(tree.inside)
    call = _operation(confinement, tree, operation)
    opened: list[tuple[str, int, int | None]] = []
    statted: list[tuple[str, int | None, bool]] = []
    real_open, real_stat = os.open, os.stat

    def spy_open(path: Any, flags: int, *rest: Any, **given: Any) -> int:
        opened.append((os.fspath(path), flags, given.get("dir_fd")))
        return real_open(path, flags, *rest, **given)

    def spy_stat(path: Any, *rest: Any, **given: Any) -> os.stat_result:
        statted.append((os.fspath(path), given.get("dir_fd"), given.get("follow_symlinks", True)))
        return real_stat(path, *rest, **given)

    monkeypatch.setattr(os, "open", spy_open)
    monkeypatch.setattr(os, "stat", spy_stat)
    call()
    monkeypatch.undo()
    roots = [index for index, (path, _, _) in enumerate(opened) if path == "/"]
    assert len(roots) == WALKS[operation], opened
    assert roots[0] == 0, opened
    for path, flags, parent in opened:
        assert flags & os.O_NOFOLLOW, path
        assert flags & os.O_CLOEXEC, path
        if path == "/":
            assert (parent, flags) == (None, SEARCH)
            continue
        assert parent is not None, path
        assert "/" not in path, path
        assert path not in ("", ".", ".."), path
        assert flags in (SEARCH, READ, LIST), (path, flags)
    files = [(path, flags) for path, flags, _ in opened if flags != SEARCH]
    if operation.startswith("read"):
        assert files == [("a.txt", READ)]
    elif operation.startswith("files"):
        assert files == [("l3", LIST)]
    else:
        assert files == []
    walked = 0 if operation in ("read", "files") else 1
    leaf = "l3" if operation == "files-pack" else "a.txt"
    assert [(path, parent is not None, follow) for path, parent, follow in statted] == [
        (leaf, True, False)
    ] * walked


# --- A middle directory swapped at any call ---------------------------------------------------

CALLS = ("open", "stat", "lstat", "fstat", "readlink", "scandir")


class _Swapping:
    """Every call of ``CALLS`` counted; before the call numbered ``at``, the level ``m`` swapped
    for a symlink out, and, with ``back``, swapped back before the next call."""

    def __init__(self, tree: _Tree, at: int, m: int, back: bool) -> None:
        self.tree, self.at, self.m, self.back = tree, at, m, back
        self.count = 0
        self.fired = False
        self.on = True

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for name in CALLS:
            monkeypatch.setattr(os, name, self._hook(getattr(os, name)))

    def _hook(self, real: Callable[..., Any]) -> Callable[..., Any]:
        def hooked(*given: Any, **more: Any) -> Any:
            if self.on:
                self.on = False
                try:
                    if self.count == self.at:
                        self.tree.swap(self.m)
                        self.fired = True
                    elif self.back and self.count == self.at + 1:
                        self.tree.unswap()
                finally:
                    self.on = True
                self.count += 1
            return real(*given, **more)

        return hooked


def _leaked(
    confinement: Confinement, tree: _Tree, operation: str, outside: set[tuple[int, int]]
) -> list[str]:
    """What of the outside the operation gave, confining included: its bytes, or an outside
    file's identity (each file listed read, and its identity looked up)."""
    leaks: list[str] = []
    try:
        if operation == "identity":
            if confinement.identity(confinement.confine(tree.leaf)) in outside:
                leaks.append("identity")
        elif operation in ("read", "read-pack"):
            if SECRET in _operation(confinement, tree, operation)():
                leaks.append("bytes")
        else:
            for entry in _operation(confinement, tree, operation)():
                if entry.path is None:
                    continue
                if confinement.identity(entry.path) in outside:
                    leaks.append(f"identity of {entry.name}")
                if SECRET in confinement.read(entry.path, 100):
                    leaks.append(f"bytes of {entry.name}")
    except ImportRefused:
        pass
    return leaks


@pytest.mark.parametrize("operation", OPERATIONS)
def test_a_middle_directory_swapped_at_any_call_leaks_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    """Before each call of the operation (confining included), each level swapped for a symlink
    to an outside tree of the same shape, and swapped back at the next call or not: no outside
    byte is read and no outside identity recorded, and every swap happened."""
    tree = _Tree(tmp_path)
    outside = {tree.outside_identity(m, name) for m in range(3) for name in ("a.txt", "b.txt")}
    counting = _Swapping(tree, -1, 0, False)
    confinement = Confinement.of(tree.inside)
    with monkeypatch.context() as patched:
        counting.install(patched)
        assert _leaked(confinement, tree, operation, outside) == []
    calls = counting.count
    assert calls > 10
    before = _descriptors()
    found: list[tuple[int, int, bool, list[str]]] = []
    for at in range(calls):
        for m in range(len(LEVELS)):
            for back in (False, True):
                swapping = _Swapping(tree, at, m, back)
                confinement = Confinement.of(tree.inside)
                with monkeypatch.context() as patched:
                    swapping.install(patched)
                    leaks = _leaked(confinement, tree, operation, outside)
                tree.unswap()
                assert swapping.fired, (at, m, back)
                if leaks:
                    found.append((at, m, back, leaks))
    assert found == []
    assert _descriptors() == before


RACE_CONFINEMENTS = 3000
"""At least this many confinements race the flipper."""
RACE_OBSERVED = 5
"""And the race runs on until this many got through and this many were refused: progress that
does not depend on how a loaded machine hands the GIL over."""
RACE_DEADLINE = 120.0
"""Seconds, a generous bound that only a machine that starves a thread for minutes passes."""


def test_a_thread_racing_a_middle_directory_leaks_nothing(tmp_path: Path) -> None:
    """A race of 3,000 confinements, identities and reads (of the operator's path and of a
    pack's) at least, run on until they have seen the flipper both ways (at least
    ``RACE_OBSERVED`` got through and as many were refused), within a deadline, while another
    thread flips ``l2`` between the directory and a symlink out; the counts seen are asserted."""
    tree = _Tree(tmp_path)
    outside = tree.outside_identity(1)
    stop = threading.Event()

    def flip() -> None:
        while not stop.is_set():
            try:
                tree.swap(1)
                tree.unswap()
            except OSError:
                pass

    flipper = threading.Thread(target=flip, daemon=True)
    leaks = done = refusals = index = 0
    deadline = time.monotonic() + RACE_DEADLINE
    flipper.start()
    try:
        while (index < RACE_CONFINEMENTS or min(done, refusals) < RACE_OBSERVED) and (
            time.monotonic() < deadline
        ):
            index += 1
            confinement = Confinement.of(tree.inside)
            try:
                if index % 2:
                    path = confinement.confine(tree.leaf)
                    leaks += confinement.identity(path) == outside
                else:
                    source = Path(confinement.confine(tree.inside / "l1"))
                    path = ConfinedPath(source / "l2" / "l3" / "a.txt")
                leaks += SECRET in confinement.read(path, 100)
                done += 1
            except ImportRefused:
                refusals += 1
    finally:
        stop.set()
        flipper.join()
        tree.swapped = None
    assert leaks == 0
    assert done >= RACE_OBSERVED, (done, refusals, index)
    assert refusals >= RACE_OBSERVED, (done, refusals, index)


def test_a_swapped_ancestor_of_a_root_is_not_followed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The walk starts at ``/``, not at the root's path text: the root's own components swapped
    for a symlink to a copy (between ``realpath`` and the identity, or after the confining) are
    refused, never read through."""
    base = tmp_path / "base"
    tree = _Tree(base / "deep")
    copy = tmp_path / "copy"
    shutil.copytree(base, copy, symlinks=True)
    copy.joinpath("deep", "imports", *LEVELS, "a.txt").write_bytes(SECRET)
    confinement = Confinement.of(tree.inside)
    confined = confinement.confine(tree.leaf)
    directory = confinement.confine(tree.directory)

    def swap() -> None:
        os.rename(base, tmp_path / "kept")
        os.symlink(copy, base)

    swap()
    with pytest.raises(ImportRefused) as read:
        confinement.read(confined, 100)
    assert _text(read) == CANNOT
    with pytest.raises(ImportRefused) as listed:
        confinement.files(directory)
    assert _text(listed) == NOT_LISTABLE
    os.unlink(base)
    os.rename(tmp_path / "kept", base)
    real = os.path.realpath

    def swapping(*given: Any, **more: Any) -> str:
        resolved = real(*given, **more)
        monkeypatch.setattr(os.path, "realpath", real)
        swap()
        return resolved

    fresh = Confinement.of(tree.inside)
    monkeypatch.setattr(os.path, "realpath", swapping)
    with pytest.raises(ImportRefused) as confining:
        fresh.confine(tree.leaf)
    assert _text(confining) == MISSING


# --- The real swap: each operation's text, naming nothing -------------------------------------


def _swap_after_realpath(monkeypatch: pytest.MonkeyPatch, swap: Callable[[], None]) -> list[int]:
    real = os.path.realpath
    fired: list[int] = []

    def swapping(*given: Any, **more: Any) -> str:
        resolved = real(*given, **more)
        if not fired:
            fired.append(1)
            swap()
        return resolved

    monkeypatch.setattr(os.path, "realpath", swapping)
    return fired


def test_a_read_through_a_middle_swapped_for_a_symlink_cannot_be_opened(tmp_path: Path) -> None:
    """Before the walk, the open followed it, reached the outside file and refused it as changed;
    the walk refuses at the symlink, with the open's text (a stated change, D232)."""
    tree = _Tree(tmp_path)
    confinement = Confinement.of(tree.inside)
    confined = confinement.confine(tree.leaf)
    tree.swap(0)
    with pytest.raises(ImportRefused) as refused:
        confinement.read(confined, 100)
    assert _text(refused) == CANNOT


def test_a_listing_through_a_middle_swapped_for_a_symlink_is_not_a_directory_to_open(
    tmp_path: Path,
) -> None:
    tree = _Tree(tmp_path)
    confinement = Confinement.of(tree.inside)
    directory = confinement.confine(tree.directory)
    tree.swap(0)
    with pytest.raises(ImportRefused) as refused:
        confinement.files(directory)
    assert _text(refused) == NOT_LISTABLE


def test_an_identity_through_a_middle_swapped_after_realpath_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Before the walk, the identity was the outside file's; now the walk refuses it as the
    identity's ``stat`` refused a missing file."""
    tree = _Tree(tmp_path)
    confinement = Confinement.of(tree.inside)
    fired = _swap_after_realpath(monkeypatch, lambda: tree.swap(0))
    with pytest.raises(ImportRefused) as refused:
        confinement.confine(tree.leaf)
    assert fired
    assert _text(refused) == MISSING


@pytest.mark.parametrize("operation", ["read", "files", "identity"])
def test_a_refusal_of_a_swap_names_no_component_in_its_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    """The component the walk stopped at (a distinctive name here) is in no text segment and no
    alternative; the data segment is the path the operation was given, as before the walk; the
    refusal is raised from nothing."""
    tree = _Tree(tmp_path, (MARK, "l2", "l3"))
    confinement = Confinement.of(tree.inside)
    given: Any = None
    if operation == "identity":
        _swap_after_realpath(monkeypatch, lambda: tree.swap(0))
        given = str(tree.leaf)
    else:
        given = confinement.confine(tree.leaf if operation == "read" else tree.directory)
        tree.swap(0)
    call = {
        "read": lambda: confinement.read(given, 100),
        "files": lambda: confinement.files(given),
        "identity": lambda: confinement.confine(given),
    }[operation]
    with pytest.raises(ImportRefused) as refused:
        call()
    [refusal] = refused.value.refusals
    segments = [segment.model_dump() for segment in refusal.message]
    texts = [segment["text"] for segment in segments if "text" in segment]
    texts += [alternative.model_dump().get("text", "") for alternative in refusal.alternatives]
    assert texts
    assert not any(MARK in text for text in texts)
    assert [segment["data"] for segment in segments if "data" in segment] == [str(given)]
    assert refused.value.__cause__ is None
    assert refused.value.__suppress_context__


# --- The listing -------------------------------------------------------------------------------


def test_a_pack_s_listing_holds_real_paths_and_follows_no_symlink_entry(tmp_path: Path) -> None:
    """A directory a pack names through ``..`` is listed at its real path: each file's path is
    that path and its name, with no ``..``, its identity the listing's; a symlink entry (to a file
    inside, to a directory inside) is returned as one, never followed."""
    tree = _Tree(tmp_path)
    (tree.directory / "to_file").symlink_to(tree.leaf)
    (tree.directory / "to_dir").symlink_to(tree.inside / "l1")
    confinement = Confinement.of(tree.inside)
    source = Path(confinement.confine(tree.inside / "l1"))
    entries = confinement.files(ConfinedPath(source / "l2" / ".." / "l2" / "l3"))
    assert [(entry.name, entry.kind) for entry in entries] == [
        ("a.txt", "file"),
        ("b.txt", "file"),
        ("to_dir", "symlink"),
        ("to_file", "symlink"),
    ]
    for entry in entries:
        if entry.path is not None:
            assert entry.path == tree.directory / entry.name
            status = os.stat(tree.directory / entry.name)
            assert confinement.identity(entry.path) == (status.st_dev, status.st_ino)
        else:
            assert entry.kind == "symlink"


@pytest.mark.parametrize("by", ["another file", "a symlink to it"])
def test_a_listed_file_replaced_before_its_read_is_refused(tmp_path: Path, by: str) -> None:
    """The file read is the file listed (D232): the listing recorded its identity, and the read
    checks the descriptor it opens against it, or cannot open a symlink."""
    tree = _Tree(tmp_path)
    confinement = Confinement.of(tree.inside)
    [first, _] = confinement.files(confinement.confine(tree.directory))
    assert first.path is not None
    os.rename(tree.leaf, tree.inside / "kept")  # the inode stays in use
    if by == "another file":
        tree.leaf.write_bytes(b"other\n")
    else:
        tree.leaf.symlink_to(tree.inside / "kept")
    with pytest.raises(ImportRefused) as refused:
        confinement.read(first.path, 100)
    assert _text(refused) == (CHANGED if by == "another file" else CANNOT)


# --- Roots ----------------------------------------------------------------------------------------


@pytest.mark.parametrize("how", ["inside", "holding", "the same", "the same through a symlink"])
def test_roots_that_nest_are_refused(tmp_path: Path, how: str) -> None:
    outer = tmp_path / "outer"
    inner = outer / "team" / "data"
    inner.mkdir(parents=True)
    (tmp_path / "link").symlink_to(outer)
    roots = {
        "inside": (outer, inner),
        "holding": (inner, outer),
        "the same": (outer, outer),
        "the same through a symlink": (outer, tmp_path / "link"),
    }[how]
    with pytest.raises(ImportRefused) as refused:
        Confinement.of(*roots)
    [refusal] = refused.value.refusals
    assert refusal.code == "PATH_NOT_CONFINED"
    assert refusal.message[0].model_dump()["text"] == (
        "The import directories nest: one lies inside another, or is another"
    )
    assert refusal.alternatives
    (tmp_path / "apart").mkdir()
    assert Confinement.of(outer, tmp_path / "apart").roots == (outer, tmp_path / "apart")


def _spellings(tree: Path, name: str, number: int) -> list[str]:
    """The ways a test can write the directory ``tree/name``: as it is, with a trailing slash, a
    doubled slash, a ``.`` and a ``..`` in it, and through a symlink."""
    link = tree / "links" / str(number)
    link.symlink_to(tree / name)
    return [
        f"{tree}/{name}",
        f"{tree}/{name}/",
        f"{tree}//{name.replace('/', '//')}",
        f"{tree}/./{name}",
        f"{tree}/other/../{name}",
        str(link),
    ]


NEST_NAMES = (
    "imports",
    "imports2",
    "imports/team",
    "imports2/team",
    "imports/team2",
    "imports/team/deep",
    "imports/team/deep/er",
    "import",
    "other",
    "other/imports",
)


def test_roots_nest_exactly_when_one_path_holds_the_other(tmp_path: Path) -> None:
    """Held against ``os.path.commonpath`` on the real paths, over every ordered pair of directories
    (equal, nested, deeper, and siblings whose names share a prefix: ``imports`` and ``imports2``
    do not nest) written in every way the API admits, alike and unlike: nested by path
    components, never by the text's prefix."""
    tree = tmp_path / "tree"
    (tree / "links").mkdir(parents=True)
    for name in NEST_NAMES:
        (tree / name).mkdir(parents=True, exist_ok=True)
    spelt = {name: _spellings(tree, name, number) for number, name in enumerate(NEST_NAMES)}
    wrong: list[tuple[str, str, bool]] = []
    nested = apart = 0
    for first in NEST_NAMES:
        for second in NEST_NAMES:
            for how in range(6):
                for other in (how, (how + 1) % 6):
                    a, b = spelt[first][how], spelt[second][other]
                    real_a, real_b = os.path.realpath(a), os.path.realpath(b)
                    expected = os.path.commonpath([real_a, real_b]) in (real_a, real_b)
                    try:
                        Confinement.of(Path(a), Path(b))
                        refused = False
                    except ImportRefused:
                        refused = True
                    nested += expected
                    apart += not expected
                    if refused != expected:
                        wrong.append((a, b, expected))
    assert wrong == []
    assert nested > 100  # both outcomes were held against the oracle
    assert apart > 100


def test_roots_nest_exactly_when_some_pair_of_three_does(tmp_path: Path) -> None:
    """The same oracle over every ordered triple of directories, each written in one of six ways
    (the second's and the third's spelling rotated from the first's), so that a root compared
    with the first alone, or with the last alone, is not enough: the roots are refused exactly
    when some pair of them nests."""
    tree = tmp_path / "tree"
    (tree / "links").mkdir(parents=True)
    for name in NEST_NAMES:
        (tree / name).mkdir(parents=True, exist_ok=True)
    spelt = {name: _spellings(tree, name, number) for number, name in enumerate(NEST_NAMES)}
    real = {name: [os.path.realpath(written) for written in spelt[name]] for name in NEST_NAMES}
    wrong: list[tuple[str, ...]] = []
    nested = apart = checked = 0
    for names in itertools.product(NEST_NAMES, repeat=3):
        for how in range(6):
            spellings = [how, (how + 1) % 6, (how + 3) % 6]
            written = [
                spelt[name][spelling] for name, spelling in zip(names, spellings, strict=True)
            ]
            paths = [real[name][spelling] for name, spelling in zip(names, spellings, strict=True)]
            expected = any(
                os.path.commonpath([paths[earlier], paths[later]]) in (paths[earlier], paths[later])
                for later in range(3)
                for earlier in range(later)
            )
            try:
                Confinement.of(*map(Path, written))
                refused = False
            except ImportRefused:
                refused = True
            checked += 1
            nested += expected
            apart += not expected
            if refused != expected:
                wrong.append(tuple(written))
    assert wrong == []
    assert checked == len(NEST_NAMES) ** 3 * 6
    assert nested > 1000  # both outcomes were held against the oracle
    assert apart > 1000


def test_a_root_is_a_walk_of_no_component_below_it(tmp_path: Path) -> None:
    """A root itself, and ``/`` as a root, confine, list and locate; read refuses it as no
    regular file."""
    (tmp_path / "f.txt").write_bytes(b"f\n")
    confinement = Confinement.of(tmp_path)
    root = confinement.confine(tmp_path)
    assert confinement.location(root) == "."
    assert [entry.name for entry in confinement.files(root)] == ["f.txt"]
    with pytest.raises(ImportRefused) as refused:
        confinement.read(root, 10)
    assert _text(refused) == "Not a regular file: "
    everything = Confinement.of(Path("/"))
    top = everything.confine("/")
    assert (top, everything.location(top)) == (Path("/"), ".")
    status = os.stat("/")
    assert everything.identity(top) == (status.st_dev, status.st_ino)
    assert sorted(entry.name for entry in everything.files(top)) == sorted(os.listdir("/"))
    with pytest.raises(ImportRefused) as read:
        everything.read(top, 10)
    assert _text(read) == "Not a regular file: "
    assert everything.read(everything.confine(tmp_path / "f.txt"), 10) == b"f\n"


# --- A pack's ``..`` ------------------------------------------------------------------------------


@pytest.mark.parametrize("below", ["a.txt/..", "a.txt/../b.txt", "b.txt/../.."])
def test_a_dot_dot_after_a_file_is_missing_to_every_operation(tmp_path: Path, below: str) -> None:
    """Before the walk, ``src/file/..`` had the identity of ``src`` and was refused by the open
    of ``read`` and ``files`` alone, each with its own text; it is now missing to all three, as
    ``src/file/x`` is (a stated change, D232). A ``..`` after a directory is accepted."""
    tree = _Tree(tmp_path)
    confinement = Confinement.of(tree.inside)
    source = Path(confinement.confine(tree.directory))
    path = ConfinedPath(source / below)
    for operation in (confinement.identity, confinement.files, lambda p: confinement.read(p, 10)):
        with pytest.raises(ImportRefused) as refused:
            operation(path)
        assert _text(refused) == MISSING
    up = ConfinedPath(source / ".." / "l3")
    assert [entry.name for entry in confinement.files(up)] == ["a.txt", "b.txt"]


def test_a_dot_dot_straight_after_the_root_of_the_file_system_is_the_root(tmp_path: Path) -> None:
    """``/..`` is ``/`` to the kernel: a pack's ``/../<source>/…`` is accepted for every
    operation, as it was before the walk, whatever follows it, where a ``..`` after a file is
    refused (the walk never takes a ``..``: the path normalised lexically is the real path)."""
    tree = _Tree(tmp_path)
    confinement = Confinement.of(tree.inside)
    for up in ("/..", "/../..", "/../../.."):
        below = ConfinedPath(Path(up + str(tree.directory)))
        assert [entry.name for entry in confinement.files(below)] == ["a.txt", "b.txt"]
        leaf = ConfinedPath(Path(up + str(tree.leaf)))
        assert confinement.read(leaf, 100) == b"inside\n"
        status = os.stat(tree.leaf)
        assert confinement.identity(leaf) == (status.st_dev, status.st_ino)


# --- Descriptors ---------------------------------------------------------------------------------


def _is_open(descriptor: int) -> bool:
    try:
        os.fstat(descriptor)
    except OSError:
        return False
    return True


def _with_free(count: int, call: Callable[[], Any]) -> Any:
    """``call``'s outcome with ``count`` descriptors free (the lowest numbers not open, under a
    limit set just above them), and none other: the refusal's text, or ``ok``."""
    listed = {int(name) for name in os.listdir("/proc/self/fd")}  # the listing's own too
    free = [number for number in range(max(listed) + count + 2) if not _is_open(number)]
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    resource.setrlimit(resource.RLIMIT_NOFILE, (free[count], hard))
    try:
        call()
        return "ok"
    except ImportRefused as error:
        return _text(error)
    finally:
        resource.setrlimit(resource.RLIMIT_NOFILE, (soft, hard))


def test_out_of_descriptors_is_said_as_such_never_changed_or_missing(tmp_path: Path) -> None:
    """A walk holds two descriptors: with one free, the read and the identity refuse with the
    read's text ("cannot be opened as confined", for a directory as well) and the listing with
    its own, never "changed" or "No such file or directory" (a stated change, D232: before, the
    read needed one and the identity none, and a listing with exactly one free was a raw
    ``OSError(EMFILE)`` from the duplication of its descriptor, not a refusal). With two free,
    each passes."""
    tree = _Tree(tmp_path)
    confinement = Confinement.of(tree.inside)
    confined = confinement.confine(tree.leaf)
    directory = confinement.confine(tree.directory)
    calls: dict[str, Callable[[], Any]] = {
        "read": lambda: confinement.read(confined, 100),
        "files": lambda: confinement.files(directory),
        "confine a file": lambda: Confinement.of(tree.inside).confine(tree.leaf),
        "confine a directory": lambda: Confinement.of(tree.inside).confine(tree.directory),
    }
    expected = {"read": CANNOT, "files": NOT_LISTABLE}
    for name, call in calls.items():
        for free in (0, 1):
            assert _with_free(free, call) == expected.get(name, CANNOT), (name, free)
        assert _with_free(2, call) == "ok", name


def test_a_directory_that_can_be_searched_and_not_read_is_passed_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """As the open by path passed it before the walk: a file below a directory of mode
    ``0o111`` is confined and read, by a process that may not read that directory (as root, a
    child that is not, root's reads passing anyway). It runs, never skips."""
    base = Path(tempfile.mkdtemp(prefix="aibi-confine-"))
    try:
        target = base / "imports" / "so" / "y"
        target.mkdir(parents=True)
        (target / "a.txt").write_bytes(b"ok\n")
        os.chmod(target / "a.txt", 0o644)
        for directory in (base, base / "imports", target):
            os.chmod(directory, 0o755)
        os.chmod(base / "imports" / "so", 0o111)
        read, write = os.pipe()
        child = os.fork()
        if child == 0:  # pragma: no cover - the child reports through the pipe
            os.close(read)
            try:
                if os.geteuid() == 0:
                    os.setgroups([])
                    os.setgid(65534)
                    os.setuid(65534)
                confinement = Confinement.of(base / "imports")
                said = confinement.read(confinement.confine(target / "a.txt"), 10)
                listed = [entry.name for entry in confinement.files(confinement.confine(target))]
                said += b"listed " + ",".join(listed).encode()
            except BaseException as error:  # reported whatever it is: the child exits
                said = repr(error).encode()
            os.write(write, said)
            os._exit(0)
        os.close(write)
        os.waitpid(child, 0)
        got = os.read(read, 1000)
        os.close(read)
        assert got == b"ok\nlisted a.txt"
    finally:
        os.chmod(base / "imports" / "so", 0o755)
        shutil.rmtree(base)


# --- The database snapshot trusts the identity --------------------------------------------------


def _databases(base: Path) -> tuple[Path, Path, Callable[[], None]]:
    """``imports/team/db/a.sqlite`` (table ``inside_table``) and an outside tree of the same
    shape (``secret_table``); the swap makes ``team`` a symlink to the outside one."""
    inside, outside = base / "imports", base / "elsewhere"
    for root, table in ((inside, "inside_table"), (outside, "secret_table")):
        folder = root / "team" / "db"
        folder.mkdir(parents=True)
        os.chmod(folder, 0o755)
        connection = sqlite3.connect(folder / "a.sqlite")
        connection.execute(f"CREATE TABLE {table} (v TEXT)")
        connection.execute(f"INSERT INTO {table} VALUES ('x')")
        connection.commit()
        connection.close()
        os.chmod(folder / "a.sqlite", 0o644)

    def swap() -> None:
        os.rename(inside / "team", inside / "kept")
        os.symlink(outside / "team", inside / "team")

    return inside, inside / "team" / "db" / "a.sqlite", swap


DATABASE_CHANGED = (
    "The database file changed after it was confined: ",
    "The database file opened is not the one confined: ",
)


@pytest.mark.parametrize("when", ["the identity's walk", "the identity's stat"])
def test_a_database_file_swapped_while_its_identity_is_taken_is_never_snapshotted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, when: str
) -> None:
    """The identity a snapshot checks the file it opens against is the walk's (before the
    walk, the ``stat`` by path took the outside file's, and its tables were snapshotted): a
    middle directory swapped as the walk starts is refused at ``resolve``; swapped once the walk
    holds the file's directory, the identity is still the inside file's, and the snapshot, which
    opens by path text, refuses what it opened."""
    inside, path, swap = _databases(tmp_path)
    confinement = Confinement.of(inside)
    fired: list[str] = []
    real_open, real_stat = os.open, os.stat

    def at_walk(target: Any, *rest: Any, **given: Any) -> int:
        if not fired and target == "/":
            fired.append(when)
            swap()
        return real_open(target, *rest, **given)

    def at_stat(target: Any, *rest: Any, **given: Any) -> os.stat_result:
        if not fired and target == "a.sqlite" and given.get("dir_fd") is not None:
            fired.append(when)
            swap()
        return real_stat(target, *rest, **given)

    with monkeypatch.context() as patched:
        if when == "the identity's walk":
            patched.setattr(os, "open", at_walk)
        else:
            patched.setattr(os, "stat", at_stat)
        if when == "the identity's walk":
            with pytest.raises(ImportRefused) as refused:
                resolve(Connection("d", "sqlite", path=path), confinement)
            assert fired == [when]
            assert _text(refused) == MISSING
            return
        resolved = resolve(Connection("d", "sqlite", path=path), confinement)
    assert fired == [when]
    with pytest.raises(ImportRefused) as refused:
        snapshot.read_snapshot(resolved.target, ImportLimits())
    assert _text(refused) in DATABASE_CHANGED


@pytest.mark.parametrize("when", ["after resolve", "at the connect"])
def test_a_database_file_swapped_after_its_identity_is_never_snapshotted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, when: str
) -> None:
    inside, path, swap = _databases(tmp_path)
    resolved = resolve(Connection("d", "sqlite", path=path), Confinement.of(inside))
    fired: list[str] = []
    if when == "after resolve":
        swap()
        fired.append(when)
    else:
        connect = sqlite3.connect

        def swapping(*given: Any, **more: Any) -> sqlite3.Connection:
            if not fired:
                fired.append(when)
                swap()
            return connect(*given, **more)

        monkeypatch.setattr(snapshot.sqlite3, "connect", swapping)
    with pytest.raises(ImportRefused) as refused:
        snapshot.read_snapshot(resolved.target, ImportLimits())
    assert fired == [when]
    assert _text(refused) in DATABASE_CHANGED


# --- Every exit closes what it opened ------------------------------------------------------------


@pytest.mark.parametrize("operation", ["read", "files", "identity"])
@pytest.mark.parametrize("change", ["none", "middle", "leaf", "missing"])
def test_no_descriptor_outlives_a_call_whatever_it_meets(
    tmp_path: Path, operation: str, change: str
) -> None:
    tree = _Tree(tmp_path)
    confinement = Confinement.of(tree.inside)
    confined = confinement.confine(tree.leaf)
    directory = confinement.confine(tree.directory)
    before = _descriptors()
    if change == "middle":
        tree.swap(1)
    elif change == "leaf":
        os.rename(tree.leaf, tree.inside / "kept")
        tree.leaf.symlink_to(tree.inside / "kept")
    elif change == "missing":
        shutil.rmtree(tree.inside / "l1")
    try:
        if operation == "read":
            confinement.read(confined, 100)
        elif operation == "files":
            confinement.files(directory)
        else:
            confinement.confine(tree.leaf)
    except ImportRefused:
        assert change != "none"
    assert _descriptors() == before


@pytest.mark.parametrize("form", ["/", "/.", "//", "/./."])
@pytest.mark.parametrize("confined_first", [False, True])
def test_no_descriptor_outlives_a_file_written_in_a_directory_s_form(
    tmp_path: Path, form: str, confined_first: bool
) -> None:
    """``a.txt/`` given as a ``str`` is refused by the read, and the descriptor it opened for the
    file is closed (the form is judged on that descriptor)."""
    tree = _Tree(tmp_path)
    confinement = Confinement.of(tree.inside)
    if confined_first:
        confinement.confine(tree.leaf)
    written = cast(ConfinedPath, f"{tree.leaf}{form}")
    before = _descriptors()
    with pytest.raises(ImportRefused) as refused:
        confinement.read(written, 100)
    assert _text(refused) == "The file cannot be opened as confined: "
    assert _descriptors() == before


def test_the_form_of_a_file_s_path_is_judged_with_no_stat_of_its_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The read of ``a.txt/`` (a ``str``, its file confined) is refused with no ``os.stat`` that is
    not relative to a descriptor: nothing after ``realpath`` goes through path text (D232)."""
    tree = _Tree(tmp_path)
    confinement = Confinement.of(tree.inside)
    confinement.confine(tree.leaf)
    stat = os.stat
    by_text: list[Any] = []

    def recording(*given: Any, **more: Any) -> os.stat_result:
        if more.get("dir_fd") is None:
            by_text.append(given)
        return stat(*given, **more)

    with monkeypatch.context() as patched:
        patched.setattr(os, "stat", recording)
        with pytest.raises(ImportRefused) as refused:
            confinement.read(cast(ConfinedPath, f"{tree.leaf}/"), 100)
    assert _text(refused) == "The file cannot be opened as confined: "
    assert by_text == []


def test_a_form_that_cannot_be_judged_is_refused_and_closes_its_descriptor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The descriptor of ``a.txt/`` that cannot be told to be a directory (``fstat`` fails) is
    closed, and the read is refused as the open of that form was: the form never fits."""
    tree = _Tree(tmp_path)
    confinement = Confinement.of(tree.inside)
    confinement.confine(tree.leaf)
    before = _descriptors()

    def failing(descriptor: int) -> os.stat_result:
        raise OSError(errno.EIO, "Input/output error")

    with monkeypatch.context() as patched:
        patched.setattr(os, "fstat", failing)
        with pytest.raises(ImportRefused) as refused:
            confinement.read(cast(ConfinedPath, f"{tree.leaf}/"), 100)
    assert _text(refused) == "The file cannot be opened as confined: "
    assert _descriptors() == before


def test_a_root_leaves_no_descriptor_open(tmp_path: Path) -> None:
    """The identity of ``/``, the walk of no component (its descriptor is closed on every exit),
    and of a root itself, in every operation that reaches one."""
    (tmp_path / "f.txt").write_bytes(b"f\n")
    for root in (Path("/"), tmp_path):
        confinement = Confinement.of(root)
        before = _descriptors()
        confined = confinement.confine(root)
        confinement.identity(confined)
        assert confinement.files(confined)
        with pytest.raises(ImportRefused):
            confinement.read(confined, 10)
        assert _descriptors() == before, root


def test_errors_of_the_walk_keep_the_lease_s_text_at_the_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``EWOULDBLOCK`` at any open of the read (the walk's or the file's) is the lease's text,
    as it was at the one open before the walk."""
    tree = _Tree(tmp_path)
    confinement = Confinement.of(tree.inside)
    confined = confinement.confine(tree.leaf)
    real = os.open

    def blocking(at: int) -> Callable[..., int]:
        count = [0]

        def opening(*given: Any, **more: Any) -> int:
            count[0] += 1
            if count[0] - 1 == at:
                raise BlockingIOError(errno.EWOULDBLOCK, "Resource temporarily unavailable")
            return real(*given, **more)

        return opening

    for at in range(len(tree.leaf.parts)):
        with monkeypatch.context() as patched:
            patched.setattr(os, "open", blocking(at))
            with pytest.raises(ImportRefused) as refused:
                confinement.read(confined, 100)
        assert _text(refused) == (
            "The file is locked by another process, and opening it does not wait: "
        )

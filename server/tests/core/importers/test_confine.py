"""Confining what an import reads (SPEC §14, D232): every file resolves inside the upload area
or an import directory, and is read once, as it was when confined."""

import errno
import fcntl
import os
import signal
import socket
import subprocess
import sys
import textwrap
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from aibi.core.importers.confine import Confinement
from aibi.core.importers.errors import ImportRefused
from aibi.core.importers.files import FileImporter
from aibi.core.schema.pack_api import ConfinedPath, DirectoryEntry

Roots = Any


def _refusal(error: pytest.ExceptionInfo[ImportRefused]) -> tuple[str, str]:
    [refusal] = error.value.refusals
    return refusal.code, refusal.message[0].model_dump()["text"]


def test_a_file_inside_a_root_is_read(roots: Roots) -> None:
    path = roots.write("a.csv", b"id\n1\n")
    confined = roots.confinement.confine(path)
    assert roots.confinement.read(confined, 100) == b"id\n1\n"
    assert roots.confinement.location(confined) == "a.csv"


def test_a_file_outside_every_root_is_refused(roots: Roots) -> None:
    outside = roots.outside / "b.csv"
    outside.write_bytes(b"id\n")
    with pytest.raises(ImportRefused) as refused:
        roots.confinement.confine(outside)
    assert _refusal(refused) == (
        "PATH_NOT_CONFINED",
        "The path resolves outside every import root: ",
    )


def test_dot_dot_traversal_is_resolved_before_it_is_checked(roots: Roots) -> None:
    (roots.outside / "b.csv").write_bytes(b"id\n")
    with pytest.raises(ImportRefused):
        roots.confinement.confine(f"{roots.inside}/../elsewhere/b.csv")


def test_a_symlink_out_is_refused_and_one_within_is_read(roots: Roots) -> None:
    (roots.outside / "b.csv").write_bytes(b"secret\n")
    (roots.inside / "out.csv").symlink_to(roots.outside / "b.csv")
    with pytest.raises(ImportRefused):
        roots.confinement.confine(roots.inside / "out.csv")
    target = roots.write("real.csv", b"id\n1\n")
    (roots.inside / "within.csv").symlink_to(target)
    confined = roots.confinement.confine(roots.inside / "within.csv")
    assert confined == target.resolve()
    assert roots.confinement.read(confined, 100) == b"id\n1\n"


def test_a_root_that_is_itself_a_symlink_is_resolved(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    (real / "a.csv").write_bytes(b"id\n")
    (tmp_path / "link").symlink_to(real)
    confinement = Confinement.of(tmp_path / "link")
    assert confinement.roots == (real.resolve(),)
    assert confinement.confine(tmp_path / "link" / "a.csv") == (real / "a.csv").resolve()


@pytest.mark.parametrize(
    "given",
    ["http://example.org/a.csv", "file:///etc/passwd", "s3://bucket/a.csv", "data:,x"],
)
def test_urls_are_refused(roots: Roots, given: str) -> None:
    with pytest.raises(ImportRefused) as refused:
        roots.confinement.confine(given)
    assert _refusal(refused) == ("PATH_NOT_CONFINED", "A URL is not a path: ")


@pytest.mark.parametrize("pattern", ["*.csv", "a?.csv", "[ab].csv"])
def test_globs_are_refused(roots: Roots, pattern: str) -> None:
    with pytest.raises(ImportRefused) as refused:
        roots.confinement.confine(f"{roots.inside}/{pattern}")
    assert _refusal(refused) == ("PATH_NOT_CONFINED", "A glob is not a path: ")


def test_relative_and_missing_paths_are_refused(roots: Roots) -> None:
    with pytest.raises(ImportRefused) as relative:
        roots.confinement.confine("imports/a.csv")
    assert _refusal(relative)[1] == "The path is not absolute: "
    with pytest.raises(ImportRefused) as missing:
        roots.confinement.confine(roots.inside / "none.csv")
    assert _refusal(missing)[1] == "No such file or directory: "


def test_a_refusal_carries_the_path_as_data(roots: Roots) -> None:
    with pytest.raises(ImportRefused) as refused:
        roots.confinement.confine("http://example.org/a.csv")
    [refusal] = refused.value.refusals
    assert refusal.message[1].model_dump() == {"data": "http://example.org/a.csv"}
    assert refusal.alternatives


def test_directory_entries_are_classified_by_their_own_names_before_anything_is_resolved(
    roots: Roots,
) -> None:
    roots.write("dir/a.csv", b"id\n")
    roots.write("dir/[odd] name.csv", b"id\n")
    roots.write("dir/sub/c.csv", b"id\n")
    (roots.outside / "b.csv").write_bytes(b"id\n")
    (roots.inside / "dir" / "z.csv").symlink_to(roots.outside / "b.csv")
    (roots.inside / "dir" / "y.csv").symlink_to(roots.inside / "dir" / "a.csv")
    os.mkfifo(roots.inside / "dir" / "pipe.csv")
    directory = roots.confinement.confine(roots.inside / "dir")
    entries = roots.confinement.files(directory)
    assert [(entry.name, entry.kind) for entry in entries] == [
        ("[odd] name.csv", "file"),
        ("a.csv", "file"),
        ("pipe.csv", "other"),
        ("sub", "directory"),
        ("y.csv", "symlink"),
        ("z.csv", "symlink"),
    ]
    assert entries[1].path == (roots.inside / "dir" / "a.csv").resolve()
    assert [entry.path for entry in entries[2:]] == [None] * 4


def test_a_directory_swapped_after_it_was_confined_is_not_listed(roots: Roots) -> None:
    roots.write("dir/a.csv", b"id\n")
    (roots.outside / "secret.csv").write_bytes(b"id\n")
    directory = roots.confinement.confine(roots.inside / "dir")
    os.rename(roots.inside / "dir", roots.inside / "was")
    (roots.inside / "dir").symlink_to(roots.outside)
    with pytest.raises(ImportRefused) as refused:
        roots.confinement.files(directory)
    assert _refusal(refused) == (
        "PATH_NOT_CONFINED",
        "Not a directory that can be opened as confined: ",
    )
    (roots.inside / "dir").unlink()
    roots.write("dir/b.csv", b"id\n")
    with pytest.raises(ImportRefused) as refused:
        roots.confinement.files(directory)
    assert _refusal(refused)[1] == "The directory changed after it was confined: "


def test_a_file_swapped_while_its_directory_is_listed_is_not_confined(
    roots: Roots, monkeypatch: pytest.MonkeyPatch
) -> None:
    listed = roots.write("dir/a.csv", b"id\n1\n")
    os.link(listed, roots.inside / "kept.csv")  # so that its inode is not reused
    directory = roots.confinement.confine(roots.inside / "dir")
    real = os.lstat

    def swapping(path: str, **given: Any) -> os.stat_result:
        found = real(path, **given)
        if given:
            os.replace(roots.write("b.csv", b"id\n2\n"), listed)
        return found

    monkeypatch.setattr(os, "lstat", swapping)
    [entry] = roots.confinement.files(directory)
    assert (entry.name, entry.kind, entry.path) == ("a.csv", "unconfined", None)


def test_a_path_with_a_nul_character_is_refused(roots: Roots) -> None:
    with pytest.raises(ImportRefused) as refused:
        roots.confinement.confine(f"{roots.inside}/a\0.csv")
    assert _refusal(refused) == ("PATH_NOT_CONFINED", "A path cannot hold a NUL character: ")


def test_a_name_with_glob_characters_that_exists_is_a_path(roots: Roots) -> None:
    for name in ("[draft] a.csv", "what?.csv", "all*.csv"):
        path = roots.write(name, b"id\n")
        assert roots.confinement.confine(str(path)) == path.resolve()


def test_a_file_that_grows_after_its_size_was_read_is_refused(
    roots: Roots, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The size ``fstat`` gives is no bound: the read stops one byte past the limit."""
    confined = roots.confinement.confine(roots.write("a.csv", b"x" * 101))
    real = os.fstat

    def shrunk(descriptor: int) -> os.stat_result:
        found = list(real(descriptor))
        found[6] = 50
        return os.stat_result(found)

    monkeypatch.setattr(os, "fstat", shrunk)
    with pytest.raises(ImportRefused) as refused:
        roots.confinement.read(confined, 100)
    assert refused.value.refusals[0].code == "LIMIT_EXCEEDED"
    assert roots.confinement.read(confined, 101) == b"x" * 101


def test_a_file_swapped_after_it_was_confined_is_refused(roots: Roots) -> None:
    path = roots.write("a.csv", b"id\n1\n")
    confined = roots.confinement.confine(path)
    replacement = roots.write("b.csv", b"id\n2\n")
    os.replace(replacement, path)
    with pytest.raises(ImportRefused) as refused:
        roots.confinement.read(confined, 100)
    assert _refusal(refused)[1] == "The file changed after it was confined: "


def test_a_file_swapped_for_a_symlink_is_not_followed(roots: Roots) -> None:
    path = roots.write("a.csv", b"id\n1\n")
    confined = roots.confinement.confine(path)
    (roots.outside / "b.csv").write_bytes(b"secret\n")
    path.unlink()
    path.symlink_to(roots.outside / "b.csv")
    with pytest.raises(ImportRefused):
        roots.confinement.read(confined, 100)


def test_a_file_over_the_limit_is_refused(roots: Roots) -> None:
    confined = roots.confinement.confine(roots.write("a.csv", b"x" * 101))
    assert roots.confinement.read(confined, 101) == b"x" * 101
    with pytest.raises(ImportRefused) as refused:
        roots.confinement.read(confined, 100)
    [refusal] = refused.value.refusals
    assert refusal.code == "LIMIT_EXCEEDED"
    assert refusal.limit is not None
    assert (refusal.limit.name, refusal.limit.max) == (
        "import_bytes",
        100,
    )


def test_several_roots_are_each_allowed(roots: Roots, tmp_path: Path) -> None:
    (roots.outside / "b.csv").write_bytes(b"id\n")
    confinement = Confinement.of(roots.inside, roots.outside)
    confined = confinement.confine(roots.outside / "b.csv")
    assert confinement.location(confined) == "b.csv"
    with pytest.raises(ImportRefused):
        confinement.confine(tmp_path)


class _Hung(BaseException):
    """A read that did not end: not an ``Exception``, so that no ``except OSError`` takes it."""


@pytest.fixture
def no_hang() -> Iterator[None]:
    """A read that waits for a writer fails after 5 seconds instead of holding the suite."""

    def hung(signum: int, frame: Any) -> None:
        raise _Hung("a read did not end")

    before = signal.signal(signal.SIGALRM, hung)
    signal.alarm(5)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, before)


def _open_descriptors() -> int:
    return len(os.listdir("/proc/self/fd"))


def _become_directory(path: Path) -> None:
    path.unlink()
    path.mkdir()


def _become_fifo(path: Path) -> None:
    """A FIFO nothing has open for writing: opening it for reading, blocking, waits forever."""
    path.unlink()
    os.mkfifo(path)


def _become_other_file(path: Path) -> None:
    os.replace(path.with_name("other.csv"), path)


SWAPS: dict[str, Callable[[Path], None]] = {
    "directory": _become_directory,
    "fifo": _become_fifo,
    "other_file": _become_other_file,
}


@pytest.mark.parametrize("swap", SWAPS)
@pytest.mark.usefixtures("no_hang")
def test_a_file_swapped_for_a_directory_or_a_fifo_is_refused_and_its_descriptor_closed(
    roots: Roots, swap: str
) -> None:
    path = roots.write("a.csv", b"id\n1\n")
    roots.write("other.csv", b"id\n2\n")
    confined = roots.confinement.confine(path)
    os.link(path, roots.inside / "kept.csv")  # so that the file system reuses no inode
    SWAPS[swap](path)
    before = _open_descriptors()
    for _ in range(3):
        with pytest.raises(ImportRefused) as refused:
            roots.confinement.read(confined, 100)
        assert _refusal(refused) == (
            "PATH_NOT_CONFINED",
            "The file changed after it was confined: ",
        )
    assert _open_descriptors() == before


def _swapping_after_listing(roots: Roots, name: str, swap: str) -> None:
    """Makes the confinement's listing, once made, swap the entry ``name`` as ``swap`` says: the
    race between ``files`` and ``read``, without a race."""
    listed = roots.confinement.files

    def files(directory: ConfinedPath) -> list[DirectoryEntry]:
        entries = listed(directory)
        SWAPS[swap](Path(directory) / name)
        return entries

    object.__setattr__(roots.confinement, "files", files)  # a frozen dataclass


@pytest.mark.parametrize("swap", ["directory", "fifo", "other_file"])
@pytest.mark.usefixtures("no_hang")
def test_a_csv_directory_with_a_file_swapped_after_the_listing_is_refused(
    roots: Roots, swap: str
) -> None:
    """Through the core's own import (D225): the reader's refusal is the import's, as
    ``PATH_NOT_CONFINED``; no exception of another kind (which the server would answer
    ``INTERNAL_ERROR``) leaves it, and no descriptor is left open."""
    roots.write("survey/birds.csv", b"id\n1\n")
    roots.write("survey/hides.csv", b"id\n2\n")
    roots.write("survey/other.csv", b"id\n3\n")
    directory = roots.confinement.confine(roots.inside / "survey")
    _swapping_after_listing(roots, "birds.csv", swap)
    before = _open_descriptors()
    with pytest.raises(ImportRefused) as refused:
        FileImporter().import_source(directory, roots.options())
    assert [refusal.code for refusal in refused.value.refusals] == ["PATH_NOT_CONFINED"]
    assert _open_descriptors() == before


@pytest.mark.parametrize("swap", ["directory", "fifo"])
@pytest.mark.usefixtures("no_hang")
def test_a_swapped_file_refuses_the_whole_import_and_publishes_nothing(
    roots: Roots, lifecycle: Any, store: Any, swap: str
) -> None:
    roots.write("survey/birds.csv", b"id\n1\n")
    _swapping_after_listing(roots, "birds.csv", swap)
    with pytest.raises(ImportRefused) as refused:
        lifecycle.import_(roots.inside / "survey")
    assert [refusal.code for refusal in refused.value.refusals] == ["PATH_NOT_CONFINED"]
    assert store.latest("d") is None


def _confined_as_is(roots: Roots, path: Path) -> ConfinedPath:
    """What ``confine`` would record for ``path`` had it been a regular file: its device and
    inode, whatever it is now. A FIFO or a socket is never confined (``confine`` refuses it); this
    is what is left of a confined file that was swapped for one whose inode the file system
    handed back, so that the descriptor's identity matches and only its kind tells."""
    status = os.stat(path, follow_symlinks=False)
    roots.confinement._confined[path.resolve()] = (status.st_dev, status.st_ino)  # pyright: ignore[reportPrivateUsage]
    return ConfinedPath(path.resolve())


def _a_fifo(path: Path) -> None:
    os.mkfifo(path)


def _a_directory(path: Path) -> None:
    path.mkdir()


@pytest.mark.parametrize("make", [_a_fifo, _a_directory], ids=["fifo", "directory"])
@pytest.mark.usefixtures("no_hang")
def test_what_is_not_a_regular_file_is_refused_even_with_the_identity_confined(
    roots: Roots, make: Callable[[Path], None]
) -> None:
    """The kind is checked on the descriptor, whatever the file system does with inode numbers
    (tmpfs never reuses one, ext4 does): the identity here is equal by construction."""
    path = roots.inside / "node"
    make(path)
    confined = _confined_as_is(roots, path)
    before = _open_descriptors()
    with pytest.raises(ImportRefused) as refused:
        roots.confinement.read(confined, 100)
    assert _refusal(refused) == ("PATH_NOT_CONFINED", "Not a regular file: ")
    assert _open_descriptors() == before


def test_a_directory_confined_as_one_is_not_a_file_to_read(roots: Roots) -> None:
    (roots.inside / "dir").mkdir()
    confined = roots.confinement.confine(roots.inside / "dir")
    with pytest.raises(ImportRefused) as refused:
        roots.confinement.read(confined, 100)
    assert _refusal(refused) == ("PATH_NOT_CONFINED", "Not a regular file: ")


def test_a_socket_in_the_place_of_a_file_cannot_be_opened_as_confined(roots: Roots) -> None:
    path = roots.inside / "node"
    with socket.socket(socket.AF_UNIX) as listener:
        listener.bind(str(path))
        confined = _confined_as_is(roots, path)
        before = _open_descriptors()
        with pytest.raises(ImportRefused) as refused:
            roots.confinement.read(confined, 100)
        assert _open_descriptors() == before
    assert _refusal(refused) == ("PATH_NOT_CONFINED", "The file cannot be opened as confined: ")


def test_a_file_swapped_for_a_changed_identity_says_so_and_a_kind_says_that(roots: Roots) -> None:
    """The two texts are not interchangeable: a directory read is not 'changed'."""
    other = roots.write("other.csv", b"id\n")
    confined = roots.confinement.confine(roots.write("a.csv", b"id\n"))
    os.link(roots.inside / "a.csv", roots.inside / "kept.csv")
    os.replace(other, roots.inside / "a.csv")
    with pytest.raises(ImportRefused) as refused:
        roots.confinement.read(confined, 100)
    assert _refusal(refused)[1] == "The file changed after it was confined: "


def test_a_symlink_to_the_confined_file_itself_is_not_followed(roots: Roots) -> None:
    """A link is no file: the open does not follow it, whatever it points at (so that the
    identity, which would match, is no excuse for opening where the listing never looked)."""
    path = roots.write("a.csv", b"id\n1\n")
    confined = roots.confinement.confine(path)
    kept = roots.inside / "kept.csv"
    os.rename(path, kept)
    path.symlink_to(kept)
    assert os.stat(path).st_ino == roots.confinement.identity(confined)[1]
    with pytest.raises(ImportRefused) as refused:
        roots.confinement.read(confined, 100)
    assert _refusal(refused) == ("PATH_NOT_CONFINED", "The file cannot be opened as confined: ")


def test_the_file_is_opened_once_and_without_waiting_or_following(
    roots: Roots, monkeypatch: pytest.MonkeyPatch
) -> None:
    confined = roots.confinement.confine(roots.write("a.csv", b"id\n1\n"))
    opened: list[int] = []
    real = os.open

    def spy(path: Any, flags: int, *rest: Any, **given: Any) -> int:
        opened.append(flags)
        return real(path, flags, *rest, **given)

    monkeypatch.setattr(os, "open", spy)
    assert roots.confinement.read(confined, 100) == b"id\n1\n"
    [flags] = opened
    for flag in ("O_NOFOLLOW", "O_NONBLOCK", "O_NOCTTY", "O_CLOEXEC"):
        assert flags & getattr(os, flag), flag
    assert flags & os.O_ACCMODE == os.O_RDONLY


def test_the_bytes_read_are_those_of_the_descriptor_opened(
    roots: Roots, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Checked and read through the descriptor, not through the path: the path is swapped for
    another file right after the open, and the file that was opened is the one read."""
    path = roots.write("a.csv", b"id\n1\n")
    confined = roots.confinement.confine(path)
    other = roots.write("other.csv", b"other\n")
    real = os.open

    def swapping(target: Any, flags: int, *rest: Any, **given: Any) -> int:
        descriptor = real(target, flags, *rest, **given)
        monkeypatch.setattr(os, "open", real)
        os.link(path, roots.inside / "kept.csv")  # the file opened keeps its inode
        os.replace(other, path)
        return descriptor

    monkeypatch.setattr(os, "open", swapping)
    assert roots.confinement.read(confined, 100) == b"id\n1\n"


@pytest.mark.parametrize("make", [_a_fifo, _a_directory], ids=["fifo", "directory"])
@pytest.mark.usefixtures("no_hang")
def test_the_kind_is_that_of_the_descriptor_opened_not_of_the_path(
    roots: Roots, monkeypatch: pytest.MonkeyPatch, make: Callable[[Path], None]
) -> None:
    """What was opened is a directory or a FIFO whose identity is the confined one; the path is
    made a regular file right after the open. The refusal is for what was opened."""
    path = roots.inside / "node"
    make(path)
    confined = _confined_as_is(roots, path)
    real = os.open

    def swapping(target: Any, flags: int, *rest: Any, **given: Any) -> int:
        descriptor = real(target, flags, *rest, **given)
        monkeypatch.setattr(os, "open", real)
        os.rename(path, roots.inside / "kept")
        path.write_bytes(b"id\n1\n")
        return descriptor

    before = _open_descriptors()
    monkeypatch.setattr(os, "open", swapping)
    with pytest.raises(ImportRefused) as refused:
        roots.confinement.read(confined, 100)
    assert _refusal(refused) == ("PATH_NOT_CONFINED", "Not a regular file: ")
    assert _open_descriptors() == before


FAULTS: dict[str, Callable[[], BaseException]] = {
    "oserror": lambda: OSError(errno.EIO, "an I/O error"),
    "keyboard_interrupt": KeyboardInterrupt,
}


@pytest.mark.parametrize("step", ["fstat", "set_blocking", "fdopen"])
@pytest.mark.parametrize("fault", FAULTS)
def test_a_descriptor_is_closed_whatever_fails_after_it_is_opened(
    roots: Roots, monkeypatch: pytest.MonkeyPatch, step: str, fault: str
) -> None:
    confined = roots.confinement.confine(roots.write("a.csv", b"id\n1\n"))

    def failing(*given: Any, **more: Any) -> Any:
        raise FAULTS[fault]()

    before = _open_descriptors()
    with monkeypatch.context() as patched:
        patched.setattr(os, step, failing)
        with pytest.raises(type(FAULTS[fault]())):
            roots.confinement.read(confined, 100)
    assert _open_descriptors() == before
    assert roots.confinement.read(confined, 100) == b"id\n1\n"


def test_a_file_that_cannot_be_opened_without_waiting_is_refused_as_locked(
    roots: Roots, monkeypatch: pytest.MonkeyPatch
) -> None:
    confined = roots.confinement.confine(roots.write("a.csv", b"id\n1\n"))

    def locked(*given: Any, **more: Any) -> int:
        raise BlockingIOError(errno.EWOULDBLOCK, "Resource temporarily unavailable")

    with monkeypatch.context() as patched:
        patched.setattr(os, "open", locked)
        with pytest.raises(ImportRefused) as refused:
            roots.confinement.read(confined, 100)
    assert _refusal(refused) == (
        "PATH_NOT_CONFINED",
        "The file is locked by another process, and opening it does not wait: ",
    )


_LEASE = textwrap.dedent(
    """
    import fcntl, os, signal, sys, time
    signal.signal(signal.SIGIO, signal.SIG_IGN)
    descriptor = os.open(sys.argv[1], os.O_RDONLY)
    try:
        fcntl.fcntl(descriptor, fcntl.F_SETLEASE, fcntl.F_WRLCK)
    except OSError as error:
        print("no lease: %s" % error, flush=True)
        sys.exit(0)
    print("leased", flush=True)
    time.sleep(30)
    """
)


def test_a_file_under_another_process_s_write_lease_is_refused_at_once(roots: Roots) -> None:
    """As a file server holding an oplock would: a blocking open waits for the lease to break."""
    if not hasattr(fcntl, "F_SETLEASE"):
        pytest.skip("this system has no file leases (fcntl.F_SETLEASE)")
    path = roots.write("a.csv", b"id\n1\n")
    confined = roots.confinement.confine(path)
    holder = subprocess.Popen(
        [sys.executable, "-I", "-c", _LEASE, str(path)],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout is not None
        said = holder.stdout.readline().strip()
        if said != "leased":
            pytest.skip(f"the file system or the user cannot take a write lease: {said}")
        with pytest.raises(ImportRefused) as refused:
            roots.confinement.read(confined, 100)
        assert _refusal(refused) == (
            "PATH_NOT_CONFINED",
            "The file is locked by another process, and opening it does not wait: ",
        )
    finally:
        holder.kill()
        holder.wait()
        if holder.stdout is not None:
            holder.stdout.close()
    assert roots.confinement.read(confined, 100) == b"id\n1\n"

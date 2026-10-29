"""Confining the files an import reads (SPEC §14, D232).

Every file that any importer, core or pack, opens resolves inside the upload area or an import
directory, after symlinks are followed. ``Confinement.confine`` refuses, first, a path that cannot
be a file's name (a lone surrogate outside U+DC80-DCFF, which no file system's bytes decode to,
D398), then a path holding a NUL character, then a URL (a scheme, or ``://``), a glob (``*``, ``?``
or ``[`` in a path that does not exist as written, so a file named ``[draft] a.csv`` is a path), a
relative path, a path that does not exist, anything that is neither a regular file nor a directory,
and anything whose real path is outside every root. A path whose symlinks are too many for
``realpath`` to follow (a chain Python's recursion cannot resolve) is refused as one that cannot be
resolved, as a loop is. It records the device and inode of what it confined. A hard link is the file
it links, wherever its other names are: it is accepted, as any file an operator put inside a root
is (D232).

A path ``read``, ``files`` or ``identity`` is given that the reader did not hand out (one a pack
joined below its source) is confined in its turn, and must then be its own real path as it is
written: no component of it but ``..`` may be a symlink (each is classified by ``lstat``, in
order, through the path as written; "The path passes through a symlink"), a ``..`` may follow only
a directory (``src/file/..`` is refused as missing, as ``src/file/x`` is), and the path normalised
lexically must be the real path ``confine`` returned ("The path is not its own real path"). So a
symlink is followed nowhere in a pack's path, not even one a later ``..`` cancels, and a ``..``
with no symlink before it is accepted where it leads inside the roots. A path ``confine`` returned
is its own real path and is not checked again. A path written as a ``str`` with a trailing
separator or ``.`` component after a file (``a.csv/``, ``a.csv/.``) has the file's identity and is
refused by ``read`` ("cannot be opened as confined"), as the open of it always was: the walk opens
the file by its own name, so the form is judged on the descriptor ``read`` opens, which must be a
directory. Only a direct caller of ``Confinement`` can give that form: a ``Path`` holds neither,
and the pack's reader takes nothing else.

Nothing after ``realpath`` goes through path text again: what is at the real path is reached by a
walk (``walk.walk``) that opens ``/`` and then each component by its own name under the descriptor
of the one before, ``O_NOFOLLOW`` and ``O_DIRECTORY`` at every step, so a directory on the way
swapped for a symlink (or for anything but a directory) after ``realpath`` stops the walk instead
of leading it out of the roots (issue #146). The identity is taken through that walk: the leaf's
``lstat`` relative to its walked directory, the leaf itself never opened (a FIFO's open would
wait). Roots are compared by their real paths and may not nest (``Confinement.of``), so that no
root's ancestor lies inside another root.

``read`` walks to the file's directory again and opens the file there by its name, once, with
``O_NOFOLLOW``, ``O_NOCTTY`` and ``O_NONBLOCK``, so that opening never waits: a FIFO that replaced
the file waits for no writer, and a file under another process's write lease is refused at once
(``PATH_NOT_CONFINED``, saying that the file is locked and opening does not wait). On a regular file
the open answers ``EWOULDBLOCK`` under ``O_NONBLOCK`` for that and nothing else; a node of another
kind that answered it would be refused with the same text, since its kind can only be read from a
descriptor the open did not give. The descriptor is checked: it must be the file confined (the same
device and inode, else "changed after it was confined") and a regular file (else "not a regular
file": a directory or a FIFO). The descriptor is closed on every refusal; ``O_NONBLOCK`` is cleared,
and the file is read into memory under a limit, which a file that grows while it is read cannot
pass. Everything after works on those bytes, so nothing is opened twice and no file can be swapped
between the check and the use. ``files`` lists a directory in the same way: the walk opens it, the
last step with ``O_DIRECTORY`` and ``O_NOFOLLOW``, checks through that descriptor that it is the
directory confined, and lists and classifies (``lstat``) each entry directly inside it through the
descriptor, by its own name, before anything is resolved, so a directory swapped after it was
confined is refused rather than listed. A regular file is confined as listed: its path is the
directory's real path and its name, and its identity is that ``lstat``'s, which ``read`` then checks
(a file swapped after it was listed is refused when it is read). A symlink is never followed, and
it, a directory, any other kind of entry and an entry whose ``lstat`` failed (for any error: one
gone before it was classified, in the main) are returned for the importer to skip and note (D225),
so that none of them refuses the whole directory.

A failed call is refused with one table (``_failed``): every step of the walk takes the text of the
call it replaced, whatever the error (``read``'s the open's, ``files``' the directory open's, the
identity's "No such file or directory"), but for a walk out of descriptors (``EMFILE``,
``ENFILE``), which a walk needs two of: the identity's is then "The file cannot be opened as
confined", as the read's is. No refusal names the step or a component that failed.
"""

import errno
import os
import re
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from aibi.core.importers.errors import ImportRefused, refused
from aibi.core.importers.walk import LIST, Unwalked, walk
from aibi.core.schema.limits import IMPORT_BYTES
from aibi.core.schema.output import data
from aibi.core.schema.pack_api import ConfinedPath, DirectoryEntry
from aibi.core.schema.refusals import RefusalCode

_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
_GLOB = frozenset("*?[")
_CHUNK = 1 << 20
_WHERE = "An absolute path to a file or directory inside the upload area or an import directory"
_APART = "Import directories none of which lies inside another or is another, by real paths"
_MISSING = "No such file or directory: "
_CANNOT_OPEN = "The file cannot be opened as confined: "
_LOCKED = "The file is locked by another process, and opening it does not wait: "
_NOT_LISTABLE = "Not a directory that can be opened as confined: "
_READ = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK | os.O_NOCTTY
_EXHAUSTED = frozenset({errno.EMFILE, errno.ENFILE})
_WOULD_BLOCK = frozenset({errno.EAGAIN, errno.EWOULDBLOCK, errno.EALREADY, errno.EINPROGRESS})
"""The errors Python raises as ``BlockingIOError``: of an open under ``O_NONBLOCK``, a lease."""

Operation = Literal["identity", "read", "listing"]
Step = Literal["resolve", "components", "walk"]


def _failed(operation: Operation, step: Step, error: BaseException) -> str:
    """The text a failed call is refused with, by operation, step and error: the one table.

    - ``identity``, at ``resolve`` (``realpath``) or ``components`` (a pack path's ``lstat``),
      any error: "No such file or directory".
    - ``identity``, at ``walk`` (the walk, and the leaf's ``lstat`` through it): ``EMFILE`` or
      ``ENFILE``, "The file cannot be opened as confined"; any other, "No such file or
      directory".
    - ``read``, at ``walk`` (the walk, and the file's open through it): ``EAGAIN`` (an error
      Python raises as ``BlockingIOError``), "The file is locked by another process, and opening
      it does not wait"; any other, "The file cannot be opened as confined".
    - ``listing``, at ``walk`` (the walk, the directory's open its last step), any error: "Not a
      directory that can be opened as confined".

    Each step of the walk takes the text of the one call it replaced (the open by path, the
    identity's ``stat`` by path), whatever failed on the way, so that a component swapped for a
    symlink is refused as the open of a swapped file was; "changed after it was confined" is the
    descriptor's comparison's alone. The identity's walk out of descriptors (a walk holds two,
    where the ``stat`` held none) is no missing file, so it says what the read's says, for a
    directory too. The text names neither the step nor what failed."""
    code = getattr(error, "errno", None)
    if operation == "identity":
        return _CANNOT_OPEN if step == "walk" and code in _EXHAUSTED else _MISSING
    if operation == "read":
        return _LOCKED if code in _WOULD_BLOCK else _CANNOT_OPEN
    return _NOT_LISTABLE


def _directory_form(path: ConfinedPath) -> bool:
    """Whether ``path`` is written with a trailing separator or a ``.`` component, which the kernel
    takes as naming a directory. A ``Path`` holds neither form (the pack's reader takes nothing
    else), so only a ``str`` given to ``Confinement`` itself can."""
    return os.path.basename(os.fspath(path)) in ("", os.curdir)


def _require_directory(descriptor: int) -> None:
    """Closes ``descriptor`` and raises ``Unwalked`` (``ENOTDIR``, as the open of ``a.csv/`` always
    was) unless it is a directory: the form of a path written as a directory's is judged on the
    descriptor the read opened, with no second look at the path."""
    kept = False
    try:
        if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise Unwalked(errno.ENOTDIR)
        kept = True
    finally:
        if not kept:
            os.close(descriptor)


def _not_confined(message: str, given: str) -> ImportRefused:
    return refused(RefusalCode.PATH_NOT_CONFINED, message, data(given), alternatives=[_WHERE])


def _status(real: Path) -> os.stat_result:
    """The status of what is at the real path ``real``, its directories walked and the leaf
    ``lstat``ed through the last of them: the leaf is never opened (a FIFO's open would wait)."""
    if not real.name:  # ``/``, a root
        descriptor = walk(real)
        try:
            return os.fstat(descriptor)
        finally:
            os.close(descriptor)
    parent = walk(real.parent)
    try:
        return os.stat(real.name, dir_fd=parent, follow_symlinks=False)
    finally:
        os.close(parent)


def _opened(real: Path, flags: int) -> int:
    """A descriptor of what is at the real path ``real``: its directories walked, and the leaf
    opened with ``flags`` by its name through the last of them."""
    if not real.name:  # ``/``, a root
        return walk(real, flags)
    parent = walk(real.parent)
    try:
        return os.open(real.name, flags, dir_fd=parent)
    finally:
        os.close(parent)


@dataclass(frozen=True)
class Confinement:
    """The roots an import may read below, each a real directory path, none inside another."""

    roots: tuple[Path, ...]
    _confined: dict[Path, tuple[int, int]] = field(
        default_factory=dict[Path, tuple[int, int]], compare=False, repr=False
    )

    @classmethod
    def of(cls, *roots: Path) -> "Confinement":
        """The roots, each resolved to its real path, which must be a directory; roots that nest
        (one inside another, or the same) are refused, as the configuration refuses them at
        start (D253): a root inside another would have an ancestor that an import's writer can
        swap."""
        resolved: list[Path] = []
        for root in roots:
            real = Path(os.path.realpath(root, strict=True))
            if not real.is_dir():
                raise NotADirectoryError(f"an import root is a directory: {root}")
            if any(real.is_relative_to(other) or other.is_relative_to(real) for other in resolved):
                raise refused(
                    RefusalCode.PATH_NOT_CONFINED,
                    "The import directories nest: one lies inside another, or is another",
                    alternatives=[_APART],
                )
            resolved.append(real)
        return cls(tuple(resolved))

    def confine(self, given: str | os.PathLike[str]) -> ConfinedPath:
        """The real path of ``given``, if it is a file or directory inside a root; otherwise a
        ``PATH_NOT_CONFINED`` refusal (``ImportRefused``)."""
        written = os.fspath(given)
        try:
            os.fsencode(written)
        except UnicodeError:
            raise _not_confined("A path that cannot be a file's name: ", written) from None
        if "\0" in written:
            shown = written.replace("\0", "\\0")
            raise _not_confined("A path cannot hold a NUL character: ", shown)
        if _SCHEME.match(written) or "://" in written:
            raise _not_confined("A URL is not a path: ", written)
        if _GLOB & set(written) and not os.path.lexists(written):
            raise _not_confined("A glob is not a path: ", written)
        if not os.path.isabs(written):
            raise _not_confined("The path is not absolute: ", written)
        return self._resolved(written)

    def _resolved(self, written: str) -> ConfinedPath:
        try:
            real = Path(os.path.realpath(written, strict=True))
        except (OSError, ValueError, RecursionError) as error:
            # Python 3.12's ``realpath`` recurses once per symlink, so a chain of about a thousand
            # links raises ``RecursionError``: the path cannot be resolved, as a loop cannot.
            raise _not_confined(_failed("identity", "resolve", error), written) from None
        try:
            status = _status(real)
        except (OSError, ValueError) as error:
            raise _not_confined(_failed("identity", "walk", error), written) from None
        if not (stat.S_ISREG(status.st_mode) or stat.S_ISDIR(status.st_mode)):
            raise _not_confined("Neither a regular file nor a directory: ", written)
        if not any(real.is_relative_to(root) for root in self.roots):
            raise _not_confined("The path resolves outside every import root: ", written)
        self._confined[real] = (status.st_dev, status.st_ino)
        return ConfinedPath(real)

    def identity(self, path: ConfinedPath) -> tuple[int, int]:
        """The device and inode of what was confined at ``path``."""
        return self._confined_at(path)[1]

    def _confined_at(self, path: ConfinedPath) -> tuple[Path, tuple[int, int]]:
        """The real path ``path`` names and the identity recorded for it; a path not confined yet
        (one a pack joined below its source) is confined now, must be its own real path as
        written (``_unlinked``), and its identity is that of its real path, which the descriptor
        opened there must then be."""
        found = self._confined.get(Path(path))
        if found is not None:
            return Path(path), found
        real = Path(self.confine(path))
        self._unlinked(os.fspath(path), real)
        return real, self._confined[real]

    @staticmethod
    def _unlinked(written: str, real: Path) -> None:
        """Refuses ``written`` unless it is its own real path ``real`` as written: each of its
        components but ``..`` is classified by ``lstat`` through the path as written, in order,
        none may be a symlink and a ``..`` must follow a directory; then the path normalised
        lexically must be ``real``."""
        parts = Path(written).parts
        prefix = parts[0]
        status: os.stat_result | None = None
        for part in parts[1:]:
            prefix = os.path.join(prefix, part)
            if part == os.pardir:
                if status is not None and not stat.S_ISDIR(status.st_mode):
                    raise _not_confined(_MISSING, written)  # as ``file/x`` would be
                continue
            try:
                status = os.lstat(prefix)
            except OSError as error:  # a NUL, the one ValueError, was refused by ``confine``
                raise _not_confined(_failed("identity", "components", error), written) from None
            if stat.S_ISLNK(status.st_mode):
                raise _not_confined("The path passes through a symlink: ", written)
        if os.path.normpath(written) != os.fspath(real):
            raise _not_confined("The path is not its own real path: ", written)

    def read(self, path: ConfinedPath, limit: int) -> bytes:
        """The bytes of the regular file confined at ``path``, at most ``limit`` of them."""
        real, identity = self._confined_at(path)
        try:
            descriptor = _opened(real, _READ)
            if _directory_form(path):
                _require_directory(descriptor)
        except OSError as error:
            raise _not_confined(_failed("read", "walk", error), str(path)) from None
        adopted = False
        try:
            status = os.fstat(descriptor)
            if (status.st_dev, status.st_ino) != identity:
                raise _not_confined("The file changed after it was confined: ", str(path))
            if not stat.S_ISREG(status.st_mode):
                raise _not_confined("Not a regular file: ", str(path))
            os.set_blocking(descriptor, True)
            file = os.fdopen(descriptor, "rb")
            adopted = True
        finally:
            if not adopted:
                os.close(descriptor)
        with file:
            if status.st_size > limit:
                raise self._too_large(limit)
            chunks: list[bytes] = []
            size = 0
            while chunk := file.read(min(_CHUNK, limit + 1 - size)):
                chunks.append(chunk)
                size += len(chunk)
                if size > limit:
                    raise self._too_large(limit)
        return b"".join(chunks)

    @staticmethod
    def _too_large(limit: int) -> ImportRefused:
        return refused(
            RefusalCode.LIMIT_EXCEEDED,
            f"The file has more than {limit} bytes",
            limit=(IMPORT_BYTES, limit),
        )

    def files(self, directory: ConfinedPath) -> list[DirectoryEntry]:
        """The entries directly inside a confined directory, by name, each regular file
        confined as listed."""
        real, identity = self._confined_at(directory)
        try:
            descriptor = walk(real, LIST)
        except OSError as error:
            raise _not_confined(_failed("listing", "walk", error), str(directory)) from None
        try:
            status = os.fstat(descriptor)
            if (status.st_dev, status.st_ino) != identity or not stat.S_ISDIR(status.st_mode):
                message = "The directory changed after it was confined: "
                raise _not_confined(message, str(directory))
            with os.scandir(descriptor) as listed:
                names = sorted(entry.name for entry in listed)
            return [self._entry(real, descriptor, name) for name in names]
        finally:
            os.close(descriptor)

    def _entry(self, directory: Path, descriptor: int, name: str) -> DirectoryEntry:
        """The entry ``name`` of the real directory ``directory`` open at ``descriptor``, by its
        ``lstat`` there; a regular file is confined at the directory's real path and its name,
        with that ``lstat``'s identity, which ``read`` checks the file it opens against."""
        try:
            status = os.lstat(name, dir_fd=descriptor)
        except OSError:
            return DirectoryEntry(name, "unconfined")
        if stat.S_ISLNK(status.st_mode):
            return DirectoryEntry(name, "symlink")
        if stat.S_ISDIR(status.st_mode):
            return DirectoryEntry(name, "directory")
        if not stat.S_ISREG(status.st_mode):
            return DirectoryEntry(name, "other")
        path = directory / name
        self._confined[path] = (status.st_dev, status.st_ino)
        return DirectoryEntry(name, "file", ConfinedPath(path))

    def location(self, path: ConfinedPath) -> str:
        """The path relative to the root that holds it, ``.`` for the root itself."""
        for root in self.roots:
            if Path(path).is_relative_to(root):
                return Path(path).relative_to(root).as_posix()
        raise _not_confined("The path is inside no import root: ", str(path))


__all__ = ["Confinement"]

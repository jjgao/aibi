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
order, through the path as written; "The path passes through a symlink"), and the path normalised
lexically must be the real path ``confine`` returned ("The path is not its own real path"). So a
symlink is followed nowhere in a pack's path, not even one a later ``..`` cancels, and a ``..``
with no symlink before it is accepted where it leads inside the roots. A path ``confine`` returned
is its own real path and is not checked again. That a component is swapped for a symlink between
these checks and the open is the race of issue #146, which this module does not close.

``read`` opens the path it is given once (the real path ``confine`` returned, or a pack's path that
passed the rule above), with ``O_NOFOLLOW``, ``O_NOCTTY`` and ``O_NONBLOCK``, so that opening
never waits: a FIFO that replaced the file waits for no writer, and a file under
another process's write lease is refused at once (``PATH_NOT_CONFINED``, saying that the file is
locked and opening does not wait). On a regular file the open answers ``EWOULDBLOCK`` under
``O_NONBLOCK`` for that and nothing else; a node of another kind that answered it would be
refused with the same text, since its kind can only be read from a descriptor the open did not
give. The descriptor is checked: it must be the file confined (the same device and inode, else
"changed after it was confined") and a regular file (else "not a regular file": a directory or a
FIFO). The descriptor is closed on every refusal; ``O_NONBLOCK`` is cleared, and the file is read
into memory under a limit, which a file that grows while it is read cannot pass. Everything after
works on those bytes, so nothing is opened twice and no file can be swapped between the check and
the use, but for the race named above. ``files`` lists a directory in the same way: it opens the
path once, with ``O_DIRECTORY`` and ``O_NOFOLLOW``, checks through that descriptor that it is the
directory confined, and lists and classifies (``lstat``) each entry directly inside it through the
descriptor, by its own name, before anything is resolved, so a directory swapped after it was
confined is refused rather than listed. A regular file is confined in its turn (an entry's name is
a name, never a glob), and must be the file listed; a symlink is never followed, and it, a
directory, any other kind of entry and a file that cannot be confined (one swapped while it is
listed) are returned for the importer to skip and note (D225), so that none of them refuses the
whole directory.
"""

import os
import re
import stat
from dataclasses import dataclass, field
from pathlib import Path

from aibi.core.importers.errors import ImportRefused, refused
from aibi.core.schema.limits import IMPORT_BYTES
from aibi.core.schema.output import data
from aibi.core.schema.pack_api import ConfinedPath, DirectoryEntry
from aibi.core.schema.refusals import RefusalCode

_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
_GLOB = frozenset("*?[")
_CHUNK = 1 << 20
_WHERE = "An absolute path to a file or directory inside the upload area or an import directory"
_MISSING = "No such file or directory: "


def _not_confined(message: str, given: str) -> ImportRefused:
    return refused(RefusalCode.PATH_NOT_CONFINED, message, data(given), alternatives=[_WHERE])


@dataclass(frozen=True)
class Confinement:
    """The roots an import may read below, each a real directory path."""

    roots: tuple[Path, ...]
    _confined: dict[Path, tuple[int, int]] = field(
        default_factory=dict[Path, tuple[int, int]], compare=False, repr=False
    )

    @classmethod
    def of(cls, *roots: Path) -> "Confinement":
        """The roots, each resolved to its real path, which must be a directory."""
        resolved: list[Path] = []
        for root in roots:
            real = Path(os.path.realpath(root, strict=True))
            if not real.is_dir():
                raise NotADirectoryError(f"an import root is a directory: {root}")
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
            status = os.stat(real, follow_symlinks=False)
        except (OSError, ValueError, RecursionError):
            # Python 3.12's ``realpath`` recurses once per symlink, so a chain of about a thousand
            # links raises ``RecursionError``: the path cannot be resolved, as a loop cannot.
            raise _not_confined(_MISSING, written) from None
        if not (stat.S_ISREG(status.st_mode) or stat.S_ISDIR(status.st_mode)):
            raise _not_confined("Neither a regular file nor a directory: ", written)
        if not any(real.is_relative_to(root) for root in self.roots):
            raise _not_confined("The path resolves outside every import root: ", written)
        self._confined[real] = (status.st_dev, status.st_ino)
        return ConfinedPath(real)

    def identity(self, path: ConfinedPath) -> tuple[int, int]:
        """The device and inode of what was confined at ``path``."""
        return self._identity(path)

    def _identity(self, path: ConfinedPath) -> tuple[int, int]:
        """The identity recorded for ``path``; a path not confined yet (one a pack joined below
        its source) is confined now, must be its own real path as written (``_unlinked``), and
        its identity is that of its real path, which the descriptor opened at ``path`` must then
        be."""
        found = self._confined.get(Path(path))
        if found is None:
            real = self.confine(path)
            self._unlinked(os.fspath(path), real)
            found = self._confined[Path(real)]
        return found

    @staticmethod
    def _unlinked(written: str, real: ConfinedPath) -> None:
        """Refuses ``written`` unless it is its own real path ``real`` as written: each of its
        components but ``..`` is classified by ``lstat`` through the path as written, in order,
        and none may be a symlink; then the path normalised lexically must be ``real``."""
        parts = Path(written).parts
        prefix = parts[0]
        for part in parts[1:]:
            prefix = os.path.join(prefix, part)
            if part == os.pardir:
                continue
            try:
                status = os.lstat(prefix)
            except OSError:  # a NUL, the one ValueError, was refused by ``confine``
                raise _not_confined(_MISSING, written) from None
            if stat.S_ISLNK(status.st_mode):
                raise _not_confined("The path passes through a symlink: ", written)
        if os.path.normpath(written) != os.fspath(real):
            raise _not_confined("The path is not its own real path: ", written)

    def read(self, path: ConfinedPath, limit: int) -> bytes:
        """The bytes of the regular file confined at ``path``, at most ``limit`` of them."""
        identity = self._identity(path)
        flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK | os.O_NOCTTY
        try:
            descriptor = os.open(path, flags)
        except BlockingIOError:
            raise _not_confined(
                "The file is locked by another process, and opening it does not wait: ", str(path)
            ) from None
        except OSError:
            raise _not_confined("The file cannot be opened as confined: ", str(path)) from None
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
        confined."""
        identity = self._identity(directory)
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
        try:
            descriptor = os.open(directory, flags)
        except OSError:
            message = "Not a directory that can be opened as confined: "
            raise _not_confined(message, str(directory)) from None
        try:
            status = os.fstat(descriptor)
            if (status.st_dev, status.st_ino) != identity or not stat.S_ISDIR(status.st_mode):
                message = "The directory changed after it was confined: "
                raise _not_confined(message, str(directory))
            with os.scandir(descriptor) as listed:
                names = sorted(entry.name for entry in listed)
            return [self._entry(directory, descriptor, name) for name in names]
        finally:
            os.close(descriptor)

    def _entry(self, directory: ConfinedPath, descriptor: int, name: str) -> DirectoryEntry:
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
        try:
            confined = self._resolved(os.path.join(directory, name))
        except ImportRefused:
            return DirectoryEntry(name, "unconfined")
        if self._confined[Path(confined)] != (status.st_dev, status.st_ino):
            return DirectoryEntry(name, "unconfined")
        return DirectoryEntry(name, "file", confined)

    def location(self, path: ConfinedPath) -> str:
        """The path relative to the root that holds it, ``.`` for the root itself."""
        for root in self.roots:
            if Path(path).is_relative_to(root):
                return Path(path).relative_to(root).as_posix()
        raise _not_confined("The path is inside no import root: ", str(path))


__all__ = ["Confinement"]

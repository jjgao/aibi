"""Confining the files an import reads (SPEC §14, D232).

Every file that any importer, core or pack, opens resolves inside the upload area or an import
directory, after symlinks are followed. ``Confinement.confine`` refuses a URL (a scheme, or
``://``), a path holding a NUL character, a glob (``*``, ``?`` or ``[`` in a path that does not
exist as written, so a file named ``[draft] a.csv`` is a path), a relative path, a path that does
not exist, anything that is neither a regular file nor a directory, and anything whose real path
is outside every root. It records the device and inode of what it confined. A hard link is the
file it links, wherever its other names are: it is accepted, as any file an operator put inside
a root is (D232).

``read`` opens the real path once, with ``O_NOFOLLOW``, checks through that descriptor that it
is the regular file confined (the same device and inode) and reads it into memory under a limit,
which a file that grows while it is read cannot pass. Everything after works on those bytes, so
nothing is opened twice and no file can be swapped between the check and the use. ``files``
lists a directory in the same way: it opens the real path once, with ``O_DIRECTORY`` and
``O_NOFOLLOW``, checks through that descriptor that it is the directory confined, and lists and
classifies (``lstat``) each entry directly inside it through the descriptor, by its own name,
before anything is resolved, so a directory swapped after it was confined is refused rather than
listed. A regular file is confined in its turn (an entry's name is a name, never a glob), and
must be the file listed; a symlink is never followed, and it, a directory, any other kind of
entry and a file that cannot be confined (one swapped while it is listed) are returned for the
importer to skip and note (D225), so that none of them refuses the whole directory.
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
        except OSError:
            raise _not_confined("No such file or directory: ", written) from None
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
        found = self._confined.get(Path(path))
        if found is None:
            self.confine(path)
            found = self._confined[Path(path)]
        return found

    def read(self, path: ConfinedPath, limit: int) -> bytes:
        """The bytes of the regular file confined at ``path``, at most ``limit`` of them."""
        identity = self._identity(path)
        try:
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        except OSError:
            raise _not_confined("The file cannot be opened as confined: ", str(path)) from None
        with os.fdopen(descriptor, "rb") as file:
            status = os.fstat(file.fileno())
            if (status.st_dev, status.st_ino) != identity or not stat.S_ISREG(status.st_mode):
                raise _not_confined("The file changed after it was confined: ", str(path))
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

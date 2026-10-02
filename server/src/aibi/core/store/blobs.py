"""Content-addressed blobs (SPEC §12.2, §14).

Every piece of release content is an immutable file named by the SHA-256 of its bytes, in
lowercase hexadecimal, under ``<root>/blobs``. A digest is checked before it becomes a path, so
paths are built only from hashes. A blob is written to ``<root>/tmp`` and renamed into place once
its bytes are on disk, so it appears whole or not at all; writing a blob that exists writes
nothing, unless its file no longer holds its bytes, which the write then replaces.

A blob can be pinned before it appears (``Holder``), so that a sweep running beside the write
cannot delete it (§12.2, Deletion): ``put`` pins the digest, then writes.
"""

import hashlib
import os
import re
import secrets
import stat
from collections.abc import Iterable
from pathlib import Path
from typing import Protocol

from aibi.core.schema.ids import HEX64

BLOBS = "blobs"
TMP = "tmp"
_DIGEST = re.compile(HEX64)
_CHUNK = 1 << 20


class BlobError(Exception):
    """A blob that cannot be used: no such blob, or bytes that do not match its name."""


class InvalidDigestError(BlobError, ValueError):
    def __init__(self, digest: str) -> None:
        super().__init__(f"not a SHA-256 digest in lowercase hexadecimal: {digest[:80]!r}")


class MissingBlobError(BlobError, LookupError):
    def __init__(self, digest: str) -> None:
        super().__init__(f"no blob {digest}")
        self.digest = digest


class CorruptBlobError(BlobError):
    def __init__(self, digest: str) -> None:
        super().__init__(f"blob {digest} does not hold the bytes its name is the hash of")
        self.digest = digest


class Holder(Protocol):
    """Pins digests; ``add`` returns once the digest is pinned (§12.2, Deletion)."""

    def add(self, digest: str) -> None: ...


def digest_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def checked(digest: str) -> str:
    """The digest, if it is 64 lowercase hexadecimal digits; else ``InvalidDigestError``."""
    if not _DIGEST.fullmatch(digest):
        raise InvalidDigestError(digest)
    return digest


class BlobStore:
    """The blobs of one deployment, under ``root``."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self._blobs = root / BLOBS
        self._tmp = root / TMP
        self._blobs.mkdir(parents=True, exist_ok=True)
        self._tmp.mkdir(parents=True, exist_ok=True)
        self._verified: dict[str, tuple[int, int, int, int, int]] = {}
        """Each blob ``verified`` found whole, by digest: its file's device, inode, size,
        modification time and change time then."""

    def path(self, digest: str) -> Path:
        """Where the blob is, whether or not it exists."""
        return self._blobs / checked(digest)

    def exists(self, digest: str) -> bool:
        return self.path(digest).is_file()

    def put(self, data: bytes, holder: Holder | None = None) -> str:
        """Store ``data`` and return its digest; ``holder`` pins it first."""
        digest = digest_of(data)
        if holder is not None:
            holder.add(digest)
        if not self._whole(digest):
            self._write(digest, (data,))
        return digest

    def put_chunks(self, chunks: Iterable[bytes], holder: Holder | None = None) -> str:
        """Store bytes given in pieces, such as a large upload, without holding them all."""
        temporary = self._temporary()
        hasher = hashlib.sha256()
        try:
            with open(temporary, "xb") as file:
                for chunk in chunks:
                    hasher.update(chunk)
                    file.write(chunk)
                file.flush()
                os.fsync(file.fileno())
            digest = hasher.hexdigest()
            if holder is not None:
                holder.add(digest)
            if self._whole(digest):
                temporary.unlink()
            else:
                self._commit(temporary, digest)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
        return digest

    def read(self, digest: str, *, verify: bool = True) -> bytes:
        """The blob's bytes; with ``verify``, ``CorruptBlobError`` unless they hash to its name."""
        path = self.path(digest)
        try:
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        except FileNotFoundError:
            raise MissingBlobError(digest) from None
        with os.fdopen(descriptor, "rb") as file:
            data = file.read()
        if verify and digest_of(data) != digest:
            raise CorruptBlobError(digest)
        return data

    def verify(self, digest: str) -> None:
        """Raises ``MissingBlobError`` or ``CorruptBlobError`` unless the blob is whole."""
        hasher = hashlib.sha256()
        try:
            descriptor = os.open(self.path(digest), os.O_RDONLY | os.O_NOFOLLOW)
        except FileNotFoundError:
            raise MissingBlobError(digest) from None
        with os.fdopen(descriptor, "rb") as file:
            while chunk := file.read(_CHUNK):
                hasher.update(chunk)
        if hasher.hexdigest() != digest:
            raise CorruptBlobError(digest)

    def verified(self, digest: str) -> Path:
        """The blob's path, once its file is known to hold its bytes (D221): verified the first
        time, and again whenever the file is another (device, inode, size, modification time or
        change time, which a write changes even when the modification time is set back).
        Raises ``MissingBlobError`` or ``CorruptBlobError`` (a link or another kind of
        file in its place included)."""
        path = self.path(digest)
        try:
            info = os.stat(path, follow_symlinks=False)
        except FileNotFoundError:
            raise MissingBlobError(digest) from None
        if not stat.S_ISREG(info.st_mode):
            raise CorruptBlobError(digest)
        mark = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
        if self._verified.get(digest) != mark:
            self.verify(digest)
            self._verified[digest] = mark
        return path

    def _whole(self, digest: str) -> bool:
        """Whether the blob is stored and holds its bytes; a damaged one, or a link in its place,
        is written again."""
        try:
            self.verify(digest)
        except (BlobError, OSError):
            return False
        return True

    def delete(self, digest: str) -> bool:
        """Delete the blob; whether it existed."""
        self._verified.pop(digest, None)
        try:
            self.path(digest).unlink()
        except FileNotFoundError:
            return False
        return True

    def digests(self) -> list[str]:
        """Every blob's digest, sorted. Files whose names are not digests are left alone."""
        return sorted(
            entry.name
            for entry in os.scandir(self._blobs)
            if _DIGEST.fullmatch(entry.name) and entry.is_file(follow_symlinks=False)
        )

    def clean(self) -> int:
        """Remove what interrupted writes left in ``tmp``; how many files. Run at start."""
        removed = 0
        for entry in os.scandir(self._tmp):
            if entry.is_file(follow_symlinks=False):
                Path(entry.path).unlink(missing_ok=True)
                removed += 1
        return removed

    def _temporary(self) -> Path:
        return self._tmp / f"{secrets.token_hex(16)}.part"

    def _write(self, digest: str, chunks: Iterable[bytes]) -> None:
        temporary = self._temporary()
        try:
            with open(temporary, "xb") as file:
                for chunk in chunks:
                    file.write(chunk)
                file.flush()
                os.fsync(file.fileno())
            self._commit(temporary, digest)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise

    def _commit(self, temporary: Path, digest: str) -> None:
        temporary.chmod(0o444)
        os.replace(temporary, self.path(digest))
        _sync_directory(self._blobs)


def _sync_directory(path: Path) -> None:
    """Make a rename in ``path`` durable."""
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


__all__ = [
    "BLOBS",
    "TMP",
    "BlobError",
    "BlobStore",
    "CorruptBlobError",
    "Holder",
    "InvalidDigestError",
    "MissingBlobError",
    "checked",
    "digest_of",
]

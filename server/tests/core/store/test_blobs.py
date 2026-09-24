"""Blobs: named by their SHA-256, written whole or not at all, and paths built only from digests."""

import hashlib
import os
import stat
from collections.abc import Iterator
from pathlib import Path

import pytest

from aibi.core.store.blobs import (
    BLOBS,
    TMP,
    BlobStore,
    CorruptBlobError,
    InvalidDigestError,
    MissingBlobError,
)


@pytest.fixture
def blobs(tmp_path: Path) -> BlobStore:
    return BlobStore(tmp_path)


def test_a_blob_is_named_by_the_hash_of_its_bytes(blobs: BlobStore, tmp_path: Path) -> None:
    digest = blobs.put(b"hello")
    assert digest == hashlib.sha256(b"hello").hexdigest()
    assert blobs.read(digest) == b"hello"
    assert blobs.path(digest) == tmp_path / BLOBS / digest
    assert blobs.exists(digest)
    assert blobs.digests() == [digest]


def test_blobs_are_read_only_and_writing_one_twice_writes_nothing(blobs: BlobStore) -> None:
    digest = blobs.put(b"x" * 1000)
    path = blobs.path(digest)
    assert stat.S_IMODE(path.stat().st_mode) == 0o444
    before = path.stat().st_mtime_ns
    assert blobs.put(b"x" * 1000) == digest
    assert path.stat().st_mtime_ns == before
    assert os.listdir(blobs.root / TMP) == []


@pytest.mark.parametrize(
    "digest",
    [
        "../../etc/passwd",
        "A" * 64,
        "a" * 63,
        "a" * 65,
        "g" * 64,
        "a" * 64 + "\n",
        "sha256:" + "a" * 64,
        "",
        "a" * 32 + "/" + "a" * 31,
    ],
)
def test_a_path_is_built_only_from_a_digest(blobs: BlobStore, digest: str) -> None:
    for call in (blobs.path, blobs.exists, blobs.read, blobs.delete, blobs.verify):
        with pytest.raises(InvalidDigestError):
            call(digest)


def test_a_missing_or_altered_blob_is_reported(blobs: BlobStore) -> None:
    with pytest.raises(MissingBlobError):
        blobs.read("0" * 64)
    with pytest.raises(MissingBlobError):
        blobs.verify("0" * 64)
    digest = blobs.put(b"original")
    path = blobs.path(digest)
    path.chmod(0o644)
    path.write_bytes(b"altered")
    with pytest.raises(CorruptBlobError):
        blobs.read(digest)
    with pytest.raises(CorruptBlobError):
        blobs.verify(digest)
    assert blobs.read(digest, verify=False) == b"altered"


def test_writing_a_damaged_blob_again_repairs_it(blobs: BlobStore) -> None:
    digest = blobs.put(b"original")
    path = blobs.path(digest)
    path.chmod(0o644)
    path.write_bytes(b"altered")
    assert blobs.put(b"original") == digest
    assert blobs.read(digest) == b"original"
    path.chmod(0o644)
    path.write_bytes(b"altered")
    assert blobs.put_chunks(iter([b"orig", b"inal"])) == digest
    assert blobs.read(digest) == b"original"


def test_a_link_in_a_blob_s_place_is_not_followed(blobs: BlobStore, tmp_path: Path) -> None:
    digest = hashlib.sha256(b"elsewhere").hexdigest()
    (tmp_path / "elsewhere").write_bytes(b"elsewhere")
    blobs.path(digest).symlink_to(tmp_path / "elsewhere")
    with pytest.raises(OSError, match="symbolic links"):
        blobs.read(digest)
    with pytest.raises(OSError, match="symbolic links"):
        blobs.verify(digest)
    assert blobs.put(b"elsewhere") == digest  # the link is replaced by the blob
    assert not blobs.path(digest).is_symlink()


def test_a_blob_written_in_chunks_is_the_blob_of_its_bytes(blobs: BlobStore) -> None:
    chunks = [b"a" * 10, b"b" * (1 << 20), b"c"]
    digest = blobs.put_chunks(iter(chunks))
    assert digest == hashlib.sha256(b"".join(chunks)).hexdigest()
    assert blobs.put_chunks(iter(chunks)) == digest
    assert os.listdir(blobs.root / TMP) == []


def test_an_interrupted_write_leaves_nothing(blobs: BlobStore) -> None:
    def failing() -> Iterator[bytes]:
        yield b"part"
        raise OSError("disk gone")

    with pytest.raises(OSError, match="disk gone"):
        blobs.put_chunks(failing())
    assert blobs.digests() == []
    assert os.listdir(blobs.root / TMP) == []


class _Recording:
    """A holder that checks each blob is pinned before it exists."""

    def __init__(self, blobs: BlobStore) -> None:
        self.blobs = blobs
        self.pinned: list[str] = []

    def add(self, digest: str) -> None:
        assert not self.blobs.exists(digest)
        self.pinned.append(digest)


def test_a_blob_is_pinned_before_it_appears(blobs: BlobStore) -> None:
    holder = _Recording(blobs)
    first = blobs.put(b"one", holder)
    second = blobs.put_chunks(iter([b"t", b"wo"]), holder)
    assert holder.pinned == [first, second]
    assert blobs.exists(first)
    assert blobs.exists(second)


def test_only_digest_named_files_are_blobs(blobs: BlobStore, tmp_path: Path) -> None:
    digest = blobs.put(b"kept")
    (tmp_path / BLOBS / "notes.txt").write_text("not a blob")
    (tmp_path / BLOBS / ("b" * 64)).symlink_to(tmp_path / BLOBS / "notes.txt")
    assert blobs.digests() == [digest]


def test_clean_removes_what_interrupted_writes_left(blobs: BlobStore, tmp_path: Path) -> None:
    (tmp_path / TMP / "half.part").write_bytes(b"half")
    assert blobs.clean() == 1
    assert os.listdir(tmp_path / TMP) == []


def test_delete_says_whether_the_blob_existed(blobs: BlobStore) -> None:
    digest = blobs.put(b"gone")
    assert blobs.delete(digest)
    assert not blobs.delete(digest)
    assert not blobs.exists(digest)

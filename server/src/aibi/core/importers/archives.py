"""Zip archives and containers, checked entry by entry under limits (SPEC §14, D233).

A ``.zip`` dataset (``members``) and a zip-based workbook (``check_container``, for XLSX, XLSM and
ODS, before python-calamine sees it) are checked alike. An entry is refused
(``ARCHIVE_REFUSED``) when its name is absolute, has a drive letter or a backslash, or has a
``..`` segment; when it is a symbolic link (``S_IFLNK`` in the Unix mode of its external
attributes); when it is encrypted; and when its name repeats an earlier entry's. A dataset's
archive holds no archive (a nested ``.zip``). Directories are skipped.

Sizes are counted while the members are decompressed, reading at most one byte past a limit,
whatever the headers declare: members (``archive_members``), bytes per member
(``member_bytes``), bytes together (``archive_bytes``), and the ratio of uncompressed to
compressed bytes (``archive_ratio``) for a member beyond ``RATIO_FLOOR`` bytes and for the
total, against the archive's own size. Over a limit, the refusal is ``LIMIT_EXCEEDED`` and names
it. A member that does not decompress to what its header says (a lying size, larger or smaller,
or a bad checksum) is refused, and so is an archive the zip module cannot read, whatever it
raises.
"""

import io
import re
import stat
import zipfile
from collections.abc import Callable

from aibi.core.importers.errors import ImportRefused, refused
from aibi.core.schema.limits import (
    ARCHIVE_BYTES,
    ARCHIVE_MEMBERS,
    ARCHIVE_RATIO,
    MEMBER_BYTES,
    RATIO_FLOOR,
    ImportLimits,
)
from aibi.core.schema.output import data
from aibi.core.schema.refusals import RefusalCode

_DRIVE = re.compile(r"^[A-Za-z]:")
_CHUNK = 1 << 20
_ENCRYPTED = 0x1


def _archive_refused(message: str, name: str) -> ImportRefused:
    return refused(RefusalCode.ARCHIVE_REFUSED, message, data(name))


def _over(name: str, maximum: int, message: str) -> ImportRefused:
    return refused(RefusalCode.LIMIT_EXCEEDED, message, limit=(name, maximum))


def _check_entry(info: zipfile.ZipInfo, dataset: bool) -> None:
    name = info.filename
    if name.startswith("/") or _DRIVE.match(name) or "\\" in name:
        raise _archive_refused("An archive entry has an absolute name: ", name)
    if ".." in name.split("/"):
        raise _archive_refused("An archive entry leads out of the archive: ", name)
    if stat.S_ISLNK(info.external_attr >> 16):
        raise _archive_refused("An archive entry is a symbolic link: ", name)
    if info.flag_bits & _ENCRYPTED:
        raise _archive_refused("An archive entry is encrypted: ", name)
    if dataset and name.lower().endswith(".zip"):
        raise _archive_refused("An archive holds another archive: ", name)


def _walk(
    data_: bytes,
    limits: ImportLimits,
    *,
    dataset: bool,
    unreadable: RefusalCode,
    keep: Callable[[str], bool],
) -> list[tuple[str, bytes | None]]:
    """Every file entry checked and decompressed under the limits, with the bytes of those
    ``keep`` names. Anything the zip module raises on bytes it cannot read refuses the
    archive."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(data_))
    except Exception:
        raise refused(unreadable, "Not a zip archive that can be read") from None
    with archive:
        entries = archive.infolist()
        if len(entries) > limits.archive_members:
            message = f"The archive has more than {limits.archive_members} entries"
            raise _over(ARCHIVE_MEMBERS, limits.archive_members, message)
        seen: set[str] = set()
        for info in entries:
            _check_entry(info, dataset)
            if info.filename in seen:
                raise _archive_refused("Two archive entries have the same name: ", info.filename)
            seen.add(info.filename)
        total = 0
        found: list[tuple[str, bytes | None]] = []
        for info in entries:
            if info.is_dir():
                continue
            chunks: list[bytes] | None = [] if keep(info.filename) else None
            size = 0
            try:
                with archive.open(info) as member:
                    while chunk := member.read(_CHUNK):
                        size += len(chunk)
                        total += len(chunk)
                        _limit(limits, info, size, total, len(data_))
                        if chunks is not None:
                            chunks.append(chunk)
            except ImportRefused:
                raise
            except Exception:
                raise _archive_refused(
                    "An archive entry does not decompress to what its header says: ",
                    info.filename,
                ) from None
            if size != info.file_size:
                raise _archive_refused(
                    "An archive entry does not decompress to what its header says: ",
                    info.filename,
                )
            found.append((info.filename, None if chunks is None else b"".join(chunks)))
    return found


def _limit(limits: ImportLimits, info: zipfile.ZipInfo, size: int, total: int, whole: int) -> None:
    if size > limits.member_bytes:
        message = f"An archive entry has more than {limits.member_bytes} bytes uncompressed"
        raise _over(MEMBER_BYTES, limits.member_bytes, message)
    if total > limits.archive_bytes:
        message = f"The archive has more than {limits.archive_bytes} bytes uncompressed"
        raise _over(ARCHIVE_BYTES, limits.archive_bytes, message)
    ratio = limits.archive_ratio
    compressed = max(1, min(info.compress_size, whole))
    if size > RATIO_FLOOR and size > ratio * compressed:
        message = f"An archive entry decompresses to more than {ratio} times its size"
        raise _over(ARCHIVE_RATIO, ratio, message)
    if total > RATIO_FLOOR and total > ratio * max(1, whole):
        message = f"The archive decompresses to more than {ratio} times its size"
        raise _over(ARCHIVE_RATIO, ratio, message)


def members(
    data_: bytes, limits: ImportLimits, *, keep: Callable[[str], bool] = lambda _: True
) -> list[tuple[str, bytes | None]]:
    """The files of a ``.zip`` dataset, by their names in the archive, in the archive's order,
    with the bytes of those ``keep`` names (``None`` for the others, which are checked and
    counted but not held). Raises ``ImportRefused``."""
    return _walk(data_, limits, dataset=True, unreadable=RefusalCode.ARCHIVE_REFUSED, keep=keep)


def check_container(data_: bytes, limits: ImportLimits) -> list[str]:
    """Check a zip-based workbook under the same rules, keeping nothing; the names of its
    files. Raises ``ImportRefused``."""
    found = _walk(
        data_,
        limits,
        dataset=False,
        unreadable=RefusalCode.UNPARSEABLE_SOURCE,
        keep=lambda _: False,
    )
    return [name for name, _ in found]


__all__ = ["check_container", "members"]

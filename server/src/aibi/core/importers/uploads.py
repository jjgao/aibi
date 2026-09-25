"""The upload area (SPEC §12.2, §14, D234).

Uploaded files are kept at ``<data root>/uploads/<dataset id>/<sha256 hex>.<extension>``: the
path is built only from a validated identifier, a hash and an extension from the allow-list, and
the file's original name travels in the import's options instead. A file is written to a
temporary name in ``<data root>/uploads.tmp``, outside the upload area and so inside no import
root, and renamed into place once its bytes are on disk, so it appears whole or not at all, and
never beyond ``import_bytes``. The upload area is always one of the roots an import may
read below. ``delete_dataset`` deletes a dataset's files, as an erasure does (§12.2, Erasure).
"""

import hashlib
import os
import secrets
import shutil
from collections.abc import Iterable
from pathlib import Path

from aibi.core.importers.errors import refused
from aibi.core.schema.ids import is_identifier
from aibi.core.schema.limits import IMPORT_BYTES
from aibi.core.schema.output import data
from aibi.core.schema.pack_api import ConfinedPath
from aibi.core.schema.refusals import RefusalCode

UPLOADS = "uploads"
EXTENSIONS = ("csv", "tsv", "txt", "xlsx", "xlsm", "ods", "parquet", "zip")
"""The extensions an upload may have, which are those the file importer reads."""
TEMPORARY = "uploads.tmp"
"""Beside the upload area, on the same file system, so that a file being written is inside no
import root."""


class UploadArea:
    def __init__(self, data_root: Path) -> None:
        self._root = data_root / UPLOADS
        self._root.mkdir(parents=True, exist_ok=True)
        self._temporary = data_root / TEMPORARY
        self._temporary.mkdir(exist_ok=True)

    @property
    def root(self) -> Path:
        """The directory the uploads are kept in, a root of every import's confinement."""
        return self._root

    def _dataset(self, dataset: str) -> Path:
        if not is_identifier(dataset):
            raise refused(
                RefusalCode.INVALID_VALUE, "A dataset id is an identifier: ", data(dataset)
            )
        return self._root / dataset

    def path(self, dataset: str, digest: str, extension: str) -> Path:
        """Where an upload with this hash and extension is kept, whether or not it exists."""
        if len(digest) != 64 or not all(c in "0123456789abcdef" for c in digest):
            raise refused(RefusalCode.INVALID_VALUE, "Not a SHA-256 digest: ", data(digest))
        return self._dataset(dataset) / f"{digest}.{_extension(extension)}"

    def put(
        self, dataset: str, chunks: Iterable[bytes], extension: str, *, limit: int
    ) -> ConfinedPath:
        """Keep an uploaded file, given in pieces; refused beyond ``limit`` bytes."""
        extension = _extension(extension)
        directory = self._dataset(dataset)
        temporary = self._temporary / secrets.token_hex(16)
        hasher = hashlib.sha256()
        size = 0
        try:
            with open(temporary, "xb") as file:
                for chunk in chunks:
                    size += len(chunk)
                    if size > limit:
                        raise refused(
                            RefusalCode.LIMIT_EXCEEDED,
                            f"The upload has more than {limit} bytes",
                            limit=(IMPORT_BYTES, limit),
                        )
                    hasher.update(chunk)
                    file.write(chunk)
                file.flush()
                os.fsync(file.fileno())
            directory.mkdir(exist_ok=True)
            final = directory / f"{hasher.hexdigest()}.{extension}"
            os.replace(temporary, final)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
        return ConfinedPath(final.resolve(strict=True))

    def delete_dataset(self, dataset: str) -> None:
        """Delete every file uploaded for the dataset: the erasure hook (§12.2)."""
        shutil.rmtree(self._dataset(dataset), ignore_errors=True)


def _extension(extension: str) -> str:
    lowered = extension.lower().removeprefix(".")
    if lowered not in EXTENSIONS:
        raise refused(
            RefusalCode.UNSUPPORTED_FORMAT,
            "Files of this kind are not imported: ",
            data(extension),
            alternatives=[f".{known}" for known in EXTENSIONS],
        )
    return lowered


__all__ = ["EXTENSIONS", "TEMPORARY", "UPLOADS", "UploadArea"]

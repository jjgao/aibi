"""Release manifests (SPEC §12.2, D213).

A release is a manifest: its dataset and its blobs by role and hash. Format 1:

```jsonc
{
  "format": 1,
  "dataset": "<dataset id>",
  "descriptors": "<hex>",          // the definitional descriptors, sorted by id
  "sources": [{"name": "<source>", "kind": "text" | "rows", "hash": "<hex>"}],
  "tables": [{"id": "<table>", "hash": "<hex>", "source": "<source>",
              "columns": [{"id": "<column>", "name": "<original name>"}]}],
  "statistics": "<hex>",           // optional: computed when the release is built (§5.2)
  "tombstones": "<hex>"            // optional: removed inferred fields (§12.3)
}
```

Sources are sorted by name and tables by id. A table's ``columns`` are its source columns in
source order, with the names the source gave them; its derived columns follow from its
descriptors. The manifest is serialised with RFC 8785, stored as a blob, and identified by
``sha256:`` and the hash of those bytes.
"""

import hashlib
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from aibi.core.schema.descriptors import String
from aibi.core.schema.ids import HEX64, SHA256_RE, ColumnId, DatasetId, Identifier, TableId
from aibi.core.schema.jsonio import canonical

Hex = Annotated[str, StringConstraints(pattern=rf"^{HEX64}$")]
FORMAT = 1


class _Model(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)


class SourceEntry(_Model):
    name: Identifier
    kind: Literal["text", "rows"]
    hash: Hex


class SourceColumn(_Model):
    id: ColumnId
    name: String
    """The column's name in the source, as it was read."""


class TableEntry(_Model):
    id: TableId
    hash: Hex
    source: Identifier
    columns: tuple[SourceColumn, ...]

    @model_validator(mode="after")
    def _distinct(self) -> Self:
        ids = [column.id for column in self.columns]
        if len(set(ids)) != len(ids):
            raise ValueError(f"table {self.id} lays out a column twice")
        return self


class Manifest(_Model):
    format: Literal[1] = FORMAT
    dataset: DatasetId
    descriptors: Hex
    sources: tuple[SourceEntry, ...] = Field(default=())
    tables: tuple[TableEntry, ...] = Field(default=())
    statistics: Hex | None = None
    tombstones: Hex | None = None

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        names = [source.name for source in self.sources]
        if names != sorted(set(names)):
            raise ValueError("sources are distinct and sorted by name")
        ids = [table.id for table in self.tables]
        if ids != sorted(set(ids)):
            raise ValueError("tables are distinct and sorted by id")
        known = set(names)
        for table in self.tables:
            if table.source not in known:
                raise ValueError(f"table {table.id} is read from no source of the release")
        return self

    def canonical_bytes(self) -> bytes:
        return canonical(self.model_dump(mode="json", exclude_none=True))

    @property
    def hash(self) -> str:
        """The release's id: ``sha256:`` and the hash of the manifest's bytes."""
        return "sha256:" + hashlib.sha256(self.canonical_bytes()).hexdigest()

    def blobs(self) -> frozenset[str]:
        """Every blob the release references, the manifest itself left out."""
        found = {self.descriptors, *(s.hash for s in self.sources), *(t.hash for t in self.tables)}
        found.update(blob for blob in (self.statistics, self.tombstones) if blob is not None)
        return frozenset(found)

    def table(self, table: str) -> TableEntry | None:
        return next((entry for entry in self.tables if entry.id == table), None)

    def source(self, name: str) -> SourceEntry | None:
        return next((entry for entry in self.sources if entry.name == name), None)

    @classmethod
    def from_bytes(cls, data: bytes) -> "Manifest":
        manifest = cls.model_validate_json(data)
        if manifest.canonical_bytes() != data:
            raise ValueError("a manifest is stored in RFC 8785 form")
        return manifest


def hex_of(manifest_hash: str) -> str:
    """The blob digest of a manifest hash, ``sha256:<hex>``."""
    if not SHA256_RE.fullmatch(manifest_hash):
        raise ValueError(f"not a manifest hash: {manifest_hash[:80]!r}")
    return manifest_hash.removeprefix("sha256:")


__all__ = ["FORMAT", "Manifest", "SourceColumn", "SourceEntry", "TableEntry", "hex_of"]

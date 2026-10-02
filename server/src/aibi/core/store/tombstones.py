"""Tombstones: the removals a re-import respects (SPEC §12.3, D240).

Removing a field whose curation entry has an ``inferred`` value records a tombstone of the field:
its descriptor, its JSON Pointer, that inference and the descriptor's version then. Removing a
whole descriptor records one tombstone with the pointer ``""``, whose ``inferred`` maps the
pointer of each of its inferred fields to the inference. A re-import treats a tombstone as a
carried field without a value, so the removal stands while the importer's inference is the
tombstone's; one that differs or is absent consumes it, so that every tombstone holds the latest
import's inference. The engine never reads tombstones.

A release's tombstones are one blob, ``{"format": "aibi.tombstones/1", "tombstones": [...]}`` in
RFC 8785 form, sorted by descriptor and pointer, each ``{"descriptor", "pointer", "inferred",
"version"}``; a release without any has no blob (the manifest's ``tombstones`` is absent).
"""

import json
from collections.abc import Iterable
from dataclasses import dataclass
from typing import cast

from pydantic import JsonValue

from aibi.core.schema.jsonio import canonical

TOMBSTONES_FORMAT = "aibi.tombstones/1"


@dataclass(frozen=True)
class Tombstone:
    descriptor: str
    pointer: str
    """``""`` for a whole descriptor."""
    inferred: JsonValue
    """The field's inference, or, for a whole descriptor, its inferences by pointer."""
    version: int
    """The descriptor's version when it was removed."""

    @property
    def key(self) -> tuple[str, str]:
        return (self.descriptor, self.pointer)


def ordered(tombstones: Iterable[Tombstone]) -> tuple[Tombstone, ...]:
    """Tombstones by descriptor and pointer; two of the same field raise ``ValueError``."""
    found = sorted(tombstones, key=lambda tombstone: tombstone.key)
    keys = [tombstone.key for tombstone in found]
    if len(set(keys)) != len(keys):
        raise ValueError("two tombstones of the same field")
    return tuple(found)


def encode(tombstones: Iterable[Tombstone]) -> bytes | None:
    """The tombstones blob, or ``None`` when there are none, and so no blob."""
    found = ordered(tombstones)
    if not found:
        return None
    written: list[JsonValue] = [
        {
            "descriptor": tombstone.descriptor,
            "pointer": tombstone.pointer,
            "inferred": tombstone.inferred,
            "version": tombstone.version,
        }
        for tombstone in found
    ]
    return canonical({"format": TOMBSTONES_FORMAT, "tombstones": written})


def decode(data: bytes) -> tuple[Tombstone, ...]:
    """The tombstones of a blob ``encode`` wrote; anything else raises ``ValueError``."""
    parsed: object = json.loads(data)
    if not isinstance(parsed, dict):
        raise ValueError(f"a tombstones blob is {TOMBSTONES_FORMAT}")
    blob = cast(dict[str, JsonValue], parsed)
    listed = blob.get("tombstones")
    if blob.get("format") != TOMBSTONES_FORMAT or not isinstance(listed, list):
        raise ValueError(f"a tombstones blob is {TOMBSTONES_FORMAT}")
    found: list[Tombstone] = []
    for item in listed:
        if not isinstance(item, dict) or set(item) != {
            "descriptor",
            "pointer",
            "inferred",
            "version",
        }:
            raise ValueError("a tombstone has a descriptor, a pointer, an inference and a version")
        descriptor, pointer, version = item["descriptor"], item["pointer"], item["version"]
        if (
            not isinstance(descriptor, str)
            or not isinstance(pointer, str)
            or not isinstance(version, int)
            or isinstance(version, bool)
        ):
            raise ValueError("a tombstone has a descriptor, a pointer, an inference and a version")
        found.append(Tombstone(descriptor, pointer, item["inferred"], version))
    tombstones = ordered(found)
    if encode(tombstones) != data:
        raise ValueError("a tombstones blob is stored in RFC 8785 form, sorted")
    return tombstones


__all__ = ["TOMBSTONES_FORMAT", "Tombstone", "decode", "encode", "ordered"]

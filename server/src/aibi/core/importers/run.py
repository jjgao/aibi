"""An import, from the importer to a release built through the validation gate (SPEC §10.1,
§12.3, §13, D235).

1. The importer reads the source: the core's ``FileImporter``, or the importer of the pack the
   operator names, through the same ``Importer`` protocol and ``ImportOptions``, whose reader is
   the only way either opens a file (§14).
2. The validators of the importing pack and of the packs in the dataset's ``packs`` check the
   source (``validate_source``); any refusal stops the import.
3. The store builds the release through the gate (§13.2), with the importer's notes in its import
   report; a structural error stops the import, and a failing proposal is dropped.
4. The same validators check the release built (``validate_descriptors``), through a view whose
   label is the one it would be published as: a ``ReleaseView`` has a label, and an import has
   none until it is published. Any refusal stops the import.

Nothing is published: the release's blobs stay pinned by the caller's pin until it publishes
``@1`` or lets go (§12.3). Every refusal is raised as ``ImportRefused``. The server running out
of memory (a ``MemoryError``) while the importer reads is ``LIMIT_EXCEEDED`` naming
``import_bytes`` (the worker names ``decoded_bytes`` while an answer is received or unpickled),
and while the store encodes and builds, ``decoded_bytes`` when any table is a typed source and
``import_bytes`` otherwise: the limits that bound what is held (D225).
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal

from aibi.core.importers.errors import ImportRefused, out_of_memory, refused
from aibi.core.importers.files import FileImporter
from aibi.core.schema.descriptors import DatasetDescriptor, Descriptor
from aibi.core.schema.limits import DECODED_BYTES, IMPORT_BYTES
from aibi.core.schema.output import data
from aibi.core.schema.pack_api import (
    ConfinedPath,
    Importer,
    ImportNote,
    ImportOptions,
    ImportResult,
    PackRegistry,
    UnknownPack,
    Validator,
)
from aibi.core.schema.refusals import RefusalCode
from aibi.core.store.build import BuildRefused, Built
from aibi.core.store.sources import TypedSource
from aibi.core.store.store import Pin, Store


@dataclass(frozen=True)
class Imported:
    built: Built
    result: ImportResult
    """What the importer returned, before the gate dropped anything."""
    notes: tuple[ImportNote, ...]
    """The import report's notes."""


@dataclass(frozen=True)
class _View:
    dataset: str
    manifest: str
    label: int | Literal["draft"]
    packs: Sequence[str]
    descriptors: Mapping[str, Descriptor]


def _packs(descriptors: Sequence[Descriptor]) -> list[str]:
    for descriptor in descriptors:
        if isinstance(descriptor, DatasetDescriptor):
            return list(descriptor.fields.packs or ())
    return []


def import_dataset(
    store: Store,
    pin: Pin,
    source: ConfinedPath,
    options: ImportOptions,
    *,
    registry: PackRegistry | None = None,
    pack: str | None = None,
) -> Imported:
    """Import ``source`` as a new, unpublished release of ``options.dataset``, with the core's
    file importer, or the importer of ``pack`` in ``registry``. Raises ``ImportRefused``."""
    importer: Importer = FileImporter()
    if pack is not None:
        found = None
        try:
            found = None if registry is None else registry.importer(pack)
        except UnknownPack:
            found = None
        if found is None:
            raise refused(RefusalCode.INVALID_VALUE, "No registered pack imports as ", data(pack))
        importer = found
    try:
        result = importer.import_source(source, options)
    except MemoryError:
        raise out_of_memory(IMPORT_BYTES, options.limits.import_bytes) from None
    consulted = {*_packs(result.descriptors), *([pack] if pack is not None else [])}
    validators: list[Validator] = []
    if registry is not None and consulted:
        try:
            validators = registry.validators(consulted)
        except UnknownPack as error:
            raise refused(
                RefusalCode.INVALID_VALUE,
                "The dataset lists a pack that is not registered: ",
                data(str(error)),
            ) from None
    refusals = [r for validator in validators for r in validator.validate_source(source, result)]
    if refusals:
        raise ImportRefused(refusals)
    try:
        built = store.import_release(
            pin,
            options.dataset,
            result.descriptors,
            result.sources,
            result.layouts,
            notes=result.notes,
        )
    except BuildRefused as error:
        raise ImportRefused(error.refusals) from None
    except MemoryError:
        if any(isinstance(raw, TypedSource) for raw in result.sources.values()):
            raise out_of_memory(DECODED_BYTES, options.limits.decoded_bytes) from None
        raise out_of_memory(IMPORT_BYTES, options.limits.import_bytes) from None
    labels = [label.label for label in store.labels(options.dataset)]
    view = _View(
        options.dataset,
        built.manifest.hash,
        max(labels, default=0) + 1,
        tuple(sorted(consulted)),
        MappingProxyType({descriptor.id: descriptor for descriptor in built.descriptors}),
    )
    refusals = [r for validator in validators for r in validator.validate_descriptors(view)]
    if refusals:
        raise ImportRefused(refusals)
    return Imported(built, result, built.notes)


__all__ = ["Imported", "import_dataset"]

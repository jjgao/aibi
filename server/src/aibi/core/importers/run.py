"""Imports and re-imports, from the importer to a published release (SPEC §10.1, §12.3, §13,
D235–D242).

``build_import`` builds a release without publishing it:

1. The importer reads the source: the core's ``FileImporter``, or the importer of the pack the
   operator names, through the same ``Importer`` protocol and ``ImportOptions``, whose reader is
   the only way either opens a file (§14), a pack's result checked by the core, which also
   contains what its importer raises (``checks``, D385); or, for a named connection resolved by
   ``databases.resolve``, the core's ``DatabaseImporter``, and never a pack's (a pack named with
   one is ``CONFLICTING_MEMBERS``, D305). An entry by the importer without an ``inferred`` value
   takes its field's value as one, as the core's importer writes it (D227, D239).
2. The validators of the importing pack and of the packs in the dataset's ``packs`` check the
   source (``validate_source``, given a database's ``DatabaseSource``); any refusal stops the
   import.
3. The descriptors pass the pack checks of every descriptor write (``check_writes``, D247), within
   the step ceiling of an importer's write, refused at ``/descriptors/<id>/…``.
4. The store builds the release through the gate (§13.2), with the importer's notes in its import
   report; a structural error stops the import, and a failing proposal is dropped.
5. The same validators check the release built (``validate_descriptors``), through a view whose
   label is the one it would be published as. Any refusal stops the import, its path, which
   points into the view's descriptors by id, under ``/descriptors`` (D246).

``import_dataset`` publishes it as the dataset's next label (``@1``, or the highest ever issued plus
one when every earlier release was withdrawn), and is refused (``DATASET_EXISTS``) while the dataset
has a published release (D237). ``reimport_dataset`` builds the next release from the latest one:
the importer gets the latest release's names (``ImportOptions.previous``, D238), and its descriptors
are carried forward (``carry.carry_forward``, D239), with the latest release's tombstones, the gate
in import mode (D241), versions set against the latest release (D243), and a note in the report for
every change (``reimported``, D242). It is refused (``NO_CHANGE``) when its manifest equals the
latest's but for the report and statistics, and (``RELEASE_WITHDRAWN``) when it equals a withdrawn
one. Both hold the dataset's operation slot throughout, refused while another operation runs or a
session is open (D236), audit themselves as ``import`` and ``reimport``, and then run the curation
proposers of the dataset's packs (D249). Every refusal is raised as ``ImportRefused``. The server
running out of memory (a ``MemoryError``) while the importer reads is ``LIMIT_EXCEEDED`` naming
``import_bytes``, or ``decoded_bytes`` for a database snapshot, whose cells it bounds (D309; the
worker names ``decoded_bytes`` while an answer is received or unpickled), and
while the store encodes and builds, ``decoded_bytes`` when any table is a typed source and
``import_bytes`` otherwise: the limits that bound what is held (D225).
"""

import os
from collections.abc import Generator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

from aibi.core.engine.resolve import Operation
from aibi.core.importers.checks import for_validator, run_importer
from aibi.core.importers.databases import DatabaseImporter, Resolved
from aibi.core.importers.errors import ImportRefused, out_of_memory, refused
from aibi.core.importers.files import FileImporter
from aibi.core.importers.reshaped import declared, owned, undeclared
from aibi.core.importers.reshaped import notes as reshaped_notes
from aibi.core.schema.copiers import pack_refusals
from aibi.core.schema.descriptors import Descriptor, TableDescriptor
from aibi.core.schema.guards import Hook, PackFailed
from aibi.core.schema.ids import assignment_order
from aibi.core.schema.jsonschemas import WRITE_STEPS_MAX
from aibi.core.schema.limits import DECODED_BYTES, IMPORT_BYTES
from aibi.core.schema.output import data, text
from aibi.core.schema.pack_api import (
    ConfinedPath,
    DatabaseSource,
    Importer,
    ImportNote,
    ImportOptions,
    ImportResult,
    ImportSource,
    PackRegistry,
    Previous,
    UnknownPack,
    Validator,
)
from aibi.core.schema.refusals import Refusal, RefusalCode
from aibi.core.store.build import BuildRefused, Built
from aibi.core.store.carry import CarryRefused, Change, carry_forward, with_inferences
from aibi.core.store.manifest import Manifest
from aibi.core.store.proposals import ProposersRun, run_proposers
from aibi.core.store.sessions import operator
from aibi.core.store.sources import TypedSource
from aibi.core.store.store import Pin, Store, StoreRefused
from aibi.core.store.writes import (
    by_id,
    check_writes,
    failed,
    packs_of,
    rooted,
    validated,
    versions,
)


@dataclass(frozen=True)
class Imported:
    built: Built
    result: ImportResult
    """What the importer returned, before the gate dropped anything."""
    notes: tuple[ImportNote, ...]
    """The import report's notes."""


@dataclass(frozen=True)
class Published:
    label: int
    manifest: str
    notes: tuple[ImportNote, ...]
    """The import report's notes."""
    changes: tuple[Change, ...] = ()
    """What a re-import changed (D239)."""
    proposers: ProposersRun | None = None
    """What the curation proposers proposed on the release, given a registry."""


_HAPPENED = {
    "proposed": "The field takes the importer's new proposal: its inference changed, or the guess "
    "carried from the previous release no longer holds",
    "removed": "Removed: the importer no longer infers this, or the guess carried from the "
    "previous release no longer holds and the importer proposes nothing in its place",
    "returned": "The importer's inference differs from the one recorded when this was removed, "
    "so its new proposal returns",
    "added": "New in this import",
    "gone": "What this names is gone from the source, so it was removed",
    "dropped": "The importer's proposal was dropped: it does not hold against the carried "
    "curation, or it names a descriptor whose removal stands or that was dropped",
}


def reimported_notes(changes: Sequence[Change]) -> list[ImportNote]:
    """A report note for each change a re-import made (D242): its subject, and a message by what
    happened, without values."""
    return [
        ImportNote(
            "reimported", change.descriptor + change.pointer, [text(_HAPPENED[change.happened])]
        )
        for change in changes
    ]


def previous_names(descriptors: Sequence[Descriptor], manifest: Manifest) -> Previous:
    """The names a re-import keeps the ids of (D238): each table's ``/label`` inference, ordered
    by that name and, within one name, in the order its ids were assigned (the id without a
    suffix first, then by numeric suffix), and each table's source columns."""
    tables: list[tuple[str, str]] = []
    for descriptor in descriptors:
        if not isinstance(descriptor, TableDescriptor):
            continue
        entry = descriptor.curation.get("/label")
        if entry is not None and isinstance(entry.inferred, str):
            tables.append((entry.inferred, descriptor.id))
    tables.sort(key=lambda pair: (pair[0], assignment_order(pair[0], pair[1], "table")))
    columns = {
        entry.id: tuple((column.name, column.id) for column in entry.columns)
        for entry in manifest.tables
    }
    return Previous(tuple(tables), columns)


@contextmanager
def _slot(store: Store, dataset: str, kind: Literal["import", "reimport"]) -> Generator[None]:
    try:
        with store.exclusive(dataset, kind):
            yield
    except StoreRefused as error:
        raise ImportRefused([error.refusal]) from None


def _importer(registry: PackRegistry | None, pack: str) -> Hook[Importer]:
    found = None
    try:
        found = None if registry is None else registry.importer(pack)
    except UnknownPack:
        found = None
    if found is None:
        raise refused(RefusalCode.INVALID_VALUE, "No registered pack imports as ", data(pack))
    return found


def _validators(registry: PackRegistry | None, consulted: set[str]) -> list[Hook[Validator]]:
    if registry is None or not consulted:
        return []
    try:
        return registry.validators(consulted)
    except UnknownPack as error:
        raise refused(
            RefusalCode.INVALID_VALUE,
            "The dataset lists a pack that is not registered: ",
            data(str(error)),
        ) from None


def _read(
    source: ConfinedPath | Resolved,
    options: ImportOptions,
    registry: PackRegistry | None,
    pack: str | None,
    packs: Sequence[str] = (),
) -> tuple[ImportResult, list[Hook[Validator]]]:
    """What the importer read, with an inference in every entry of its own, once the validators
    of the importing pack and the dataset's packs accept the source. A pack's importer is called
    through its own guard (``run_importer``, D385), which its handle enters (D388)."""
    try:
        if isinstance(source, Resolved):
            if pack is not None:
                raise refused(
                    RefusalCode.CONFLICTING_MEMBERS,
                    "A named connection is read by the core's importer, never a pack's: name no "
                    "pack with it (D305)",
                )
            try:
                read = DatabaseImporter().import_database(source, options)
            except MemoryError:
                raise out_of_memory(DECODED_BYTES, options.limits.decoded_bytes) from None
        elif pack is None:
            read = FileImporter().import_source(source, options)
        else:
            importer = _importer(registry, pack)
            confined = source
            read = importer.enter(lambda h: run_importer(h, pack, confined, options))
            if read.reshaped:
                assert registry is not None
                version = registry.pack(pack).manifest.version
                read = declared(read, f"importer:{pack}@{version}", options.at)
    except MemoryError:
        raise out_of_memory(IMPORT_BYTES, options.limits.import_bytes) from None
    result = replace(read, descriptors=tuple(with_inferences(read.descriptors)))
    consulted = {*packs_of(result.descriptors), *packs, *([pack] if pack is not None else [])}
    found = _validators(registry, consulted)
    given = source.source if isinstance(source, Resolved) else source
    refusals = [r for validator in found for r in _source_refusals(validator, given, result)]
    if refusals:
        raise ImportRefused(refusals)
    return result, found


def _source_refusals(
    validator: Hook[Validator], source: ImportSource, result: ImportResult
) -> list[Refusal]:
    """One validator's refusals of a source, through its handle's guard (D388): it is given a
    copy of its own of the source and of what was read (``for_validator``); a validator that
    fails refuses the import (``PACK_FAILED``)."""
    pack = validator.pack
    source_copy = _source_copy(source)
    result_copy = for_validator(result)
    try:
        return list(
            validator.call(
                lambda h: pack_refusals(h.validate_source, source_copy, result_copy, pack=pack)
            )
        )
    except PackFailed:
        return [failed(pack, "validator")]


def _source_copy(source: ImportSource) -> ImportSource:
    """A copy of the core's source for one validator: a confined path made anew from its text,
    or a database snapshot's source made anew from its fields."""
    if isinstance(source, DatabaseSource):
        return DatabaseSource(source.connection, source.kind, source.location)
    return ConfinedPath(Path(os.fspath(source)))


def _out_of_memory(result: ImportResult, options: ImportOptions) -> ImportRefused:
    """The refusal of the server running out of memory while the store encodes and builds:
    ``decoded_bytes`` when a table is a typed source, ``import_bytes`` otherwise (D225)."""
    if any(isinstance(raw, TypedSource) for raw in result.sources.values()):
        return out_of_memory(DECODED_BYTES, options.limits.decoded_bytes)
    return out_of_memory(IMPORT_BYTES, options.limits.import_bytes)


def _check(
    store: Store,
    dataset: str,
    built: Built,
    validators: Sequence[Hook[Validator]],
    operation: Operation,
) -> None:
    label = max((label.label for label in store.labels(dataset)), default=0) + 1
    hashed = built.manifest.hash
    refusals = validated(validators, operation, dataset, hashed, label, built.descriptors)
    if refusals:
        raise ImportRefused(rooted(refusals, "descriptors"))


def build_import(
    store: Store,
    pin: Pin,
    source: ConfinedPath | Resolved,
    options: ImportOptions,
    *,
    registry: PackRegistry | None = None,
    pack: str | None = None,
    operation: Operation | None = None,
) -> Imported:
    """Import ``source`` as a new, unpublished release of ``options.dataset``, with the core's
    file importer, its database importer for a named connection, or the importer of ``pack`` in
    ``registry``; its blobs stay pinned by ``pin``, and its validators read their packs' views
    in ``operation``. Raises ``ImportRefused``."""
    result, validators = _read(source, options, registry, pack)
    refusals = check_writes(result.descriptors, registry, ceiling=WRITE_STEPS_MAX)
    if refusals:
        raise ImportRefused(by_id(refusals, result.descriptors, "descriptors"))
    try:
        built = store.import_release(
            pin,
            options.dataset,
            result.descriptors,
            result.sources,
            result.layouts,
            notes=[*result.notes, *reshaped_notes(result.reshaped)],
            reshaped=result.reshaped,
        )
    except BuildRefused as error:
        raise ImportRefused(error.refusals) from None
    except MemoryError:
        raise _out_of_memory(result, options) from None
    _check(store, options.dataset, built, validators, operation or Operation())
    return Imported(built, result, built.notes)


def _publish(
    store: Store,
    dataset: str,
    built: Built,
    by: str,
    action: Literal["import", "reimport"],
    base: str | None = None,
) -> int:
    try:
        with store.db.transaction() as db:
            detail = {} if base is None else {"base": base}
            return store.commit_label(db, dataset, built.manifest.hash, by, action, detail)
    except StoreRefused as error:
        raise ImportRefused([error.refusal]) from None


def _proposers(
    store: Store, dataset: str, registry: PackRegistry | None, operation: Operation
) -> ProposersRun | None:
    return None if registry is None else run_proposers(store, dataset, registry, operation)


def _by(by: str) -> None:
    try:
        operator(by)
    except StoreRefused as error:
        raise ImportRefused([error.refusal]) from None


def import_dataset(
    store: Store,
    source: ConfinedPath | Resolved,
    options: ImportOptions,
    by: str,
    *,
    registry: PackRegistry | None = None,
    pack: str | None = None,
) -> Published:
    """Import ``source`` as a new dataset and publish it, as the operator ``by`` (D237). Raises
    ``ImportRefused``."""
    _by(by)
    dataset = options.dataset
    operation = Operation()
    with _slot(store, dataset, "import"):
        if store.latest(dataset) is not None:
            raise refused(
                RefusalCode.DATASET_EXISTS,
                "The dataset has a published release: re-import it instead",
            )
        with store.pin() as pin:
            imported = build_import(
                store, pin, source, options, registry=registry, pack=pack, operation=operation
            )
            label = _publish(store, dataset, imported.built, by, "import")
    manifest = imported.built.manifest.hash
    proposers = _proposers(store, dataset, registry, operation)
    return Published(label, manifest, imported.notes, (), proposers)


def _unchanged(new: Manifest, latest: Manifest) -> bool:
    """Whether two manifests are the same but for their reports, statistics and the digests of
    what a reshaped table's coverage check last read (D241, D386): a re-import always checks the
    coverage again, and a digest that differs only by the check's version changes no content."""
    return _compared(new).hash == _compared(latest).hash


def _compared(manifest: Manifest) -> Manifest:
    tables = tuple(
        table
        if table.reshaped is None
        else table.model_copy(
            update={"reshaped": table.reshaped.model_copy(update={"checked": None})}
        )
        for table in manifest.tables
    )
    return manifest.model_copy(update={"report": None, "statistics": None, "tables": tables})


def reimport_dataset(
    store: Store,
    source: ConfinedPath | Resolved,
    options: ImportOptions,
    by: str,
    *,
    registry: PackRegistry | None = None,
    pack: str | None = None,
) -> Published:
    """Re-import ``source`` as the dataset's next release, carrying its curation forward, as the
    operator ``by`` (§12.3, D238–D242). Raises ``ImportRefused``."""
    _by(by)
    dataset = options.dataset
    operation = Operation()
    with _slot(store, dataset, "reimport"), store.pin() as pin:
        latest = store.latest(dataset)
        if latest is None:
            raise refused(
                RefusalCode.UNKNOWN_RELEASE, "The dataset has no published release to re-import"
            )
        base = pin.manifest(latest.manifest)
        before = store.descriptors(latest.manifest)
        tombstones = store.tombstones(latest.manifest)
        given = replace(options, previous=previous_names(before, base))
        result, validators = _read(source, given, registry, pack, packs_of(before))
        if pack is not None and (tables := undeclared(before, result)):
            raise refused(
                RefusalCode.PACK_FAILED,
                "The importer of the pack ",
                data(pack),
                " re-imported a table it unpivoted without declaring what it dropped (D386): ",
                data(tables[0]),
            )
        try:
            mine = owned(before, tombstones, result.descriptors, result.reshaped)
            carried = carry_forward(mine.descriptors, mine.tombstones, result.descriptors)
        except CarryRefused as error:
            raise ImportRefused(error.refusals) from None
        refusals = check_writes(carried.descriptors, registry, ceiling=WRITE_STEPS_MAX)
        if refusals:
            raise ImportRefused(by_id(refusals, carried.descriptors, "descriptors"))

        def revise(found: tuple[Descriptor, ...]) -> Sequence[Descriptor]:
            return versions(before, found, tombstones)

        try:
            built = store.import_release(
                pin,
                dataset,
                carried.descriptors,
                result.sources,
                result.layouts,
                notes=[
                    *result.notes,
                    *reshaped_notes(result.reshaped),
                    *mine.notes,
                    *reimported_notes(carried.changes),
                ],
                reshaped=result.reshaped,
                tombstones=carried.tombstones,
                revise=revise,
            )
        except BuildRefused as error:
            raise ImportRefused(by_id(error.refusals, carried.descriptors, "descriptors")) from None
        except MemoryError:
            raise _out_of_memory(result, given) from None
        if _unchanged(built.manifest, base):
            raise refused(
                RefusalCode.NO_CHANGE,
                "The re-import gives the latest published release again: nothing changed",
            )
        _check(store, dataset, built, validators, operation)
        label = _publish(store, dataset, built, by, "reimport", latest.manifest)
    proposers = _proposers(store, dataset, registry, operation)
    return Published(label, built.manifest.hash, built.notes, carried.changes, proposers)


__all__ = [
    "Imported",
    "Published",
    "build_import",
    "import_dataset",
    "previous_names",
    "reimport_dataset",
    "reimported_notes",
]

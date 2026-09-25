"""The catalogue index (SPEC §11.1, §12.2, D273, D274).

One entry per dataset, for its latest published release: what ``search_catalog`` filters on and
shows, built from the release's descriptors, its catalogue statistics after the disclosure pass and
the facets of the packs the dataset lists. It is kept in the app DB's ``catalog`` table, one row per
dataset, with the *basis* it was built on: the release's manifest, the deployment's floor and the
registered packs' versions. ``entries`` brings the table up to date before it reads it: a dataset
whose latest release, floor or packs changed is indexed again, and a dataset with no published
release left loses its row, as does one whose latest release was built before catalogue statistics
were kept, which is left out, since the catalogue reads no rows (§14, D270). An entry is written
while its release is pinned, under the store's lock, and only if that release is still the dataset's
latest published one: a release withdrawn while its entry was built, by an erasure or otherwise, is
left out, and an erasure's redaction, which waits for the pin, deletes whatever the entry of a
release it withdraws after that (D273). The index holds no cell values, only descriptor text,
concepts, facet values and disclosed counts; an erasure deletes the dataset's row all the same,
since it was built from a release the erasure withdrew (``redaction``).

A pack's facet (``facet(release) -> {name: [values]}``, §10.1) is called on a view of the
release's descriptors for each registered pack the dataset lists, and its facets are named
``<pack id>.<name>``. A facet that raises, or returns anything but identifiers naming at most
``MAX_ENTRIES`` lists of at most ``MAX_ENTRIES`` strings of Unicode text of at most
``MAX_STRING`` characters, is left out of the entry and logged without its values, never
raised, so that a pack cannot take the catalogue down.
"""

import hashlib
import logging
from collections.abc import Mapping, Sequence
from typing import cast

from pydantic import BaseModel, ConfigDict, JsonValue

from aibi.core.catalog.disclosure import DisclosedTable, disclose_table, effective_k
from aibi.core.schema.descriptors import (
    ColumnDescriptor,
    DatasetDescriptor,
    Descriptor,
    EndpointDescriptor,
    TableDescriptor,
)
from aibi.core.schema.ids import IDENTIFIER_RE
from aibi.core.schema.jsonio import canonical, is_text
from aibi.core.schema.limits import MAX_ENTRIES, MAX_STRING
from aibi.core.schema.pack_api import PackRegistry
from aibi.core.store import statistics
from aibi.core.store.blobs import MissingBlobError
from aibi.core.store.manifest import Manifest
from aibi.core.store.store import Store
from aibi.core.store.writes import view

_logger = logging.getLogger(__name__)


class _Entry(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)


class IndexedColumn(_Entry):
    id: str
    datatype: str | None
    concepts: tuple[str, ...]
    present: int | None
    """PRESENT cells, as disclosed."""


class IndexedTable(_Entry):
    id: str
    label: str
    grain: str | None
    role: str | None
    rows: int | None
    """Rows, as disclosed."""
    columns: tuple[IndexedColumn, ...]


class Entry(_Entry):
    dataset: str
    label: int
    manifest: str
    k: int | None
    """The effective ``min_cell_count`` (§8.4)."""
    title: str
    name: str | None
    description: str | None
    domain_tags: tuple[str, ...]
    data_use: tuple[tuple[str, str, str], ...]
    """``(system, code, label)``."""
    packs: tuple[str, ...]
    concepts: tuple[str, ...]
    facets: Mapping[str, tuple[str, ...]]
    tables: tuple[IndexedTable, ...]
    words: str
    """The text ``search_catalog``'s words are found in, case folded."""
    suppressed: bool
    """Some count of the entry was suppressed (§8.4)."""


def _asserted_concept(descriptor: Descriptor) -> str | None:
    fields = cast(object, descriptor.__dict__.get("fields"))
    mapping = getattr(fields, "maps_to", None)
    entry = descriptor.curation.get("/fields/maps_to")
    if mapping is None or entry is None or entry.status != "asserted":
        return None
    return cast(str, mapping.concept)


def basis(manifest: str, floor: int | None, registry: PackRegistry | None) -> str:
    """What an entry was built on: the release, the floor and the packs' versions."""
    packs: dict[str, JsonValue] = {}
    if registry is not None:
        packs = {pack: registry.pack(pack).manifest.version for pack in registry.ids}
    return hashlib.sha256(
        canonical({"manifest": manifest, "floor": floor, "packs": packs})
    ).hexdigest()


def _facet_values(found: object) -> dict[str, tuple[str, ...]] | None:
    if not isinstance(found, Mapping) or len(cast(Mapping[object, object], found)) > MAX_ENTRIES:
        return None
    kept: dict[str, tuple[str, ...]] = {}
    for name, values in cast(Mapping[object, object], found).items():
        if not isinstance(name, str) or IDENTIFIER_RE.fullmatch(name) is None or "__" in name:
            return None
        if isinstance(values, str) or not isinstance(values, Sequence):
            return None
        listed = cast(Sequence[object], values)
        if len(listed) > MAX_ENTRIES or not all(
            isinstance(v, str) and 0 < len(v) <= MAX_STRING and is_text(v) for v in listed
        ):
            return None
        kept[name] = tuple(cast(Sequence[str], listed))
    return kept


def facets(
    registry: PackRegistry | None,
    dataset: str,
    manifest: str,
    label: int,
    descriptors: Sequence[Descriptor],
) -> dict[str, tuple[str, ...]]:
    """The facets of the registered packs the dataset lists, ``<pack id>.<name>``."""
    if registry is None:
        return {}
    released = view(dataset, manifest, label, descriptors)
    found: dict[str, tuple[str, ...]] = {}
    for pack in released.packs:
        if pack not in registry.ids:
            continue
        for facet in registry.facets([pack]):
            try:
                given = _facet_values(facet(released))
            except Exception:
                given = None
            if given is None:
                _logger.warning("the %s pack's facet was left out of the catalogue", pack)
                continue
            found.update({f"{pack}.{name}": values for name, values in given.items()})
    return dict(sorted(found.items()))


def kept_statistics(
    store: Store, manifest: Manifest
) -> dict[str, statistics.TableStatistics] | None:
    """The statistics its build counted for a release (D270), or ``None`` for a release built
    before they were kept: the catalogue reads no rows (§14), so it never counts them itself."""
    if manifest.statistics is None:
        return None
    return statistics.decode(store.blobs.read(manifest.statistics))


def disclosed_tables(
    store: Store, manifest: Manifest, k: int | None
) -> dict[str, DisclosedTable] | None:
    """Every table's statistics after the disclosure pass (D271), or ``None`` for a release
    without them (``kept_statistics``)."""
    raw = kept_statistics(store, manifest)
    if raw is None:
        return None
    return {table: disclose_table(stats, k) for table, stats in raw.items()}


def dataset_k(descriptors: Sequence[Descriptor]) -> int | None:
    for descriptor in descriptors:
        if isinstance(descriptor, DatasetDescriptor):
            disclosure = descriptor.fields.disclosure
            return None if disclosure is None else disclosure.min_cell_count
    return None


def build_entry(
    store: Store,
    registry: PackRegistry | None,
    floor: int | None,
    dataset: str,
    label: int,
    manifest: str,
    pinned: Manifest,
) -> Entry | None:
    """The entry of the published release ``manifest``, which the caller has pinned as
    ``pinned``; ``None`` for one without catalogue statistics, which the catalogue leaves out
    (D270)."""
    descriptors = store.descriptors(manifest)
    k = effective_k(floor, dataset_k(descriptors))
    disclosed = disclosed_tables(store, pinned, k)
    if disclosed is None:
        return None
    by_table: dict[str, list[ColumnDescriptor]] = {}
    words: list[str] = []
    concepts: set[str] = set()
    dataset_descriptor: DatasetDescriptor | None = None
    table_descriptors: list[TableDescriptor] = []
    for descriptor in descriptors:
        concept = _asserted_concept(descriptor)
        if concept is not None:
            concepts.add(concept)
        if isinstance(descriptor, DatasetDescriptor):
            dataset_descriptor = descriptor
        elif isinstance(descriptor, TableDescriptor):
            table_descriptors.append(descriptor)
        elif isinstance(descriptor, ColumnDescriptor):
            by_table.setdefault(descriptor.id.split(".", 1)[0], []).append(descriptor)
        elif isinstance(descriptor, EndpointDescriptor):
            words.extend([descriptor.id, descriptor.label])
        if isinstance(descriptor, TableDescriptor | ColumnDescriptor):
            words.extend([descriptor.id, descriptor.label, descriptor.definition or ""])
    assert dataset_descriptor is not None, "a release holds its dataset descriptor"
    fields = dataset_descriptor.fields
    indexed_tables: list[IndexedTable] = []
    suppressed = False
    for table in table_descriptors:
        found = disclosed.get(table.id)
        suppressed = suppressed or (found is not None and found.suppressed)
        columns = tuple(
            IndexedColumn(
                id=column.id,
                datatype=column.fields.datatype,
                concepts=tuple(c for c in [_asserted_concept(column)] if c is not None),
                present=None
                if found is None
                else found.states[column.id.split(".", 1)[1]]["PRESENT"],
            )
            for column in by_table.get(table.id, [])
        )
        words.append(table.fields.grain or "")
        indexed_tables.append(
            IndexedTable(
                id=table.id,
                label=table.label,
                grain=table.fields.grain,
                role=table.fields.role,
                rows=None if found is None else found.n_rows,
                columns=columns,
            )
        )
    tags = tuple(fields.domain_tags or ())
    words.extend(
        [
            dataset,
            dataset_descriptor.label,
            fields.name or "",
            fields.description or "",
            dataset_descriptor.definition or "",
            *tags,
        ]
    )
    return Entry(
        dataset=dataset,
        label=label,
        manifest=manifest,
        k=k,
        title=dataset_descriptor.label,
        name=fields.name,
        description=fields.description,
        domain_tags=tags,
        data_use=tuple((ref.system, ref.code, ref.label) for ref in fields.data_use or ()),
        packs=tuple(fields.packs or ()),
        concepts=tuple(sorted(concepts)),
        facets=facets(registry, dataset, manifest, label, descriptors),
        tables=tuple(indexed_tables),
        words="\n".join(word for word in words if word).casefold(),
        suppressed=suppressed,
    )


def entries(store: Store, registry: PackRegistry | None, floor: int | None) -> list[Entry]:
    """Every dataset's entry, by dataset id, for its latest published release; the index is
    brought up to date first (module docstring)."""
    with store.lock:
        current = {
            dataset: latest
            for dataset in store.datasets()
            if (latest := store.latest(dataset)) is not None
        }
        rows = {
            cast(str, row[0]): (cast(str, row[1]), cast(str, row[2]))
            for row in store.db.connection.execute("SELECT dataset, basis, entry FROM catalog")
        }
    found: list[Entry] = []
    uncounted: list[str] = []
    for dataset, latest in sorted(current.items()):
        wanted = basis(latest.manifest, floor, registry)
        row = rows.get(dataset)
        if row is not None and row[0] == wanted:
            found.append(Entry.model_validate_json(row[1]))
            continue
        try:
            with store.pin() as pin:
                pinned = pin.manifest(latest.manifest)
                entry = build_entry(
                    store, registry, floor, dataset, latest.label, latest.manifest, pinned
                )
                if entry is None:
                    _logger.warning("a release of %s has no catalogue statistics", dataset)
                    uncounted.append(dataset)
                    continue
                with store.lock:
                    now = store.latest(dataset)
                    if now is None or now.manifest != latest.manifest:
                        continue
                    with store.db.transaction() as db:
                        db.execute(
                            "INSERT OR REPLACE INTO catalog (dataset, basis, entry) "
                            "VALUES (?, ?, ?)",
                            (dataset, wanted, entry.model_dump_json()),
                        )
        except MissingBlobError:
            continue
        found.append(entry)
    stale = sorted((set(rows) - set(current)) | (set(rows) & set(uncounted)))
    if stale:
        with store.db.transaction() as db:
            db.executemany("DELETE FROM catalog WHERE dataset = ?", [(d,) for d in stale])
    return found


__all__ = [
    "Entry",
    "IndexedColumn",
    "IndexedTable",
    "basis",
    "build_entry",
    "dataset_k",
    "disclosed_tables",
    "entries",
    "facets",
    "kept_statistics",
]

"""Tables a pack's importer unpivoted, as the core records them (SPEC §10.1, D401).

A pack that unpivots a matrix into a long table declares, by table, its value column, the values
it dropped as "assessed, nothing there" (``absent``), and how many cells it dropped as absent and
as empty (``Reshaped``). The core, not the pack:

- writes the column's ``absent`` from the declaration (``declared``), with an ``imported``
  curation entry by the pack's importer, before the importer's inferences are taken, so that a
  re-import carries it by its inference as every importer field is (D239);
- writes the import report's ``reshaped`` notes, one for the absent cells and one for the empty
  cells of each table (``notes``), which no importer may write (D400);
- on a re-import, makes the fields that are the importer's (a reshaped table's ``source`` and
  ``primary_key``, and its column's ``absent``) the new import's in the latest release before its
  curation is carried forward (``owned``), so that a value an operator set before these fields
  were the importer's is not carried, and the field tombstones of those fields go. A coverage's
  ``parents`` is not among them: an operator who removed it keeps it removed (D239), which leaves
  the relationship open.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import cast

from pydantic import JsonValue, TypeAdapter

from aibi.core.schema.descriptors import ColumnDescriptor, Descriptor, TableDescriptor
from aibi.core.schema.output import Segment, data, text
from aibi.core.schema.pack_api import ImportNote, ImportResult, Reshaped
from aibi.core.store.tombstones import Tombstone

_ADAPTER: TypeAdapter[Descriptor] = TypeAdapter(Descriptor)
TABLE_FIELDS = ("source", "primary_key")
"""A reshaped table's fields that are the importer's (D401)."""
COLUMN_FIELDS = ("absent",)
"""A reshaped table's value column's fields that are the importer's (D401)."""

Json = dict[str, JsonValue]


def declared(result: ImportResult, by: str, at: str) -> ImportResult:
    """``result`` with each reshaped table's value column's ``absent`` written, its entry
    ``imported`` by ``by`` at ``at``."""
    if not result.reshaped:
        return result
    columns = {f"{table}.{found.column}": found for table, found in result.reshaped.items()}
    written: list[Descriptor] = []
    for descriptor in result.descriptors:
        found = columns.get(descriptor.id)
        if found is None or not isinstance(descriptor, ColumnDescriptor):
            written.append(descriptor)
            continue
        dumped = cast(Json, descriptor.model_dump(mode="json"))
        cast(Json, dumped["fields"])["absent"] = list(found.absent)
        entry: Json = {"status": "imported", "by": by, "at": at}
        cast(Json, dumped["curation"])["/fields/absent"] = entry
        written.append(_ADAPTER.validate_python(dumped))
    return replace(result, descriptors=written)


def notes(reshaped: Mapping[str, Reshaped]) -> list[ImportNote]:
    """The import report's ``reshaped`` notes: per table, its absent cells with the values that
    mean them, and its empty cells, each counted (D231, D401)."""
    found: list[ImportNote] = []
    for table, declaration in sorted(reshaped.items()):
        subject = f"{table}.{declaration.column}"
        values: list[Segment] = [
            text(
                "Source cells the pack's importer dropped because their value means nothing there: "
            )
        ]
        for position, value in enumerate(declaration.absent):
            if position:
                values.append(text(", "))
            values.append(data(value))
        found.append(ImportNote("reshaped", subject, values, declaration.dropped))
        message = (
            "Empty source cells the pack's importer dropped as not assessed; the coverage "
            "leaves them out"
        )
        found.append(ImportNote("reshaped", subject, [text(message)], declaration.empty))
    return found


@dataclass(frozen=True)
class Owned:
    descriptors: tuple[Descriptor, ...]
    """The latest release's descriptors, the importer's fields the new import's."""
    tombstones: tuple[Tombstone, ...]
    """The latest release's tombstones, without those of the importer's fields."""
    notes: tuple[ImportNote, ...]
    """A ``reimported`` note for each field whose value the new import changed."""


def undeclared(before: Sequence[Descriptor], result: ImportResult) -> list[str]:
    """The tables the latest release holds as a pack's unpivot (a column with ``absent`` values,
    a ``pack`` source) that ``result`` lays out again without declaring what it dropped (D401):
    a pack declares its absent values from the format, even when it dropped none, so such a
    re-import would drop cells the core cannot check."""
    tables = {d.id: d for d in before if isinstance(d, TableDescriptor)}
    found: set[str] = set()
    for descriptor in before:
        if isinstance(descriptor, ColumnDescriptor) and descriptor.fields.absent is not None:
            table = descriptor.id.split(".", 1)[0]
            described = tables.get(table)
            source = None if described is None else described.fields.source
            if source is not None and source.kind == "pack":
                found.add(table)
    laid = [table for table in found if table in result.layouts]
    return sorted(table for table in laid if table not in result.reshaped)


def owned(
    before: Sequence[Descriptor],
    tombstones: Sequence[Tombstone],
    new: Sequence[Descriptor],
    reshaped: Mapping[str, Reshaped],
) -> Owned:
    """The latest release's descriptors and tombstones with the importer's fields of each table
    the new import reshaped set as the new import sets them, value and entry."""
    fields: dict[str, tuple[str, ...]] = {}
    for table, declaration in reshaped.items():
        fields[table] = TABLE_FIELDS
        fields[f"{table}.{declaration.column}"] = COLUMN_FIELDS
    by_id = {descriptor.id: descriptor for descriptor in new}
    changed: list[ImportNote] = []
    written: list[Descriptor] = []
    for descriptor in before:
        names = fields.get(descriptor.id)
        source = by_id.get(descriptor.id)
        kind = TableDescriptor if descriptor.id in reshaped else ColumnDescriptor
        if names is None or not isinstance(source, kind) or not isinstance(descriptor, kind):
            written.append(descriptor)
            continue
        dumped = cast(Json, descriptor.model_dump(mode="json"))
        given = cast(Json, source.model_dump(mode="json"))
        for name in names:
            pointer = f"/fields/{name}"
            had = cast(Json, dumped["fields"]).get(name)
            value = cast(Json, given["fields"]).get(name)
            entry = cast(Json, given["curation"]).get(pointer)
            for part, put in (("fields", value), ("curation", entry)):
                held = cast(Json, dumped[part])
                key = name if part == "fields" else pointer
                if put is None:
                    held.pop(key, None)
                else:
                    held[key] = put
            if had != value:
                message = "The importer's own field, set by the new import (D401)"
                changed.append(ImportNote("reimported", descriptor.id + pointer, [text(message)]))
        written.append(_ADAPTER.validate_python(dumped))
    owned_pointers = {(id_, f"/fields/{name}") for id_, names in fields.items() for name in names}
    kept = tuple(
        tombstone
        for tombstone in tombstones
        if (tombstone.descriptor, tombstone.pointer) not in owned_pointers
    )
    return Owned(tuple(written), kept, tuple(changed))


__all__ = ["COLUMN_FIELDS", "TABLE_FIELDS", "Owned", "declared", "notes", "owned"]

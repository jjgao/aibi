"""Descriptors from what an importer read and inferred, with their curation entries (SPEC §5.1,
§12.3, D227).

Every field the importer sets has a curation entry by ``importer:<name>@<version>`` at the
import's ``at``, with the field's value as ``inferred``, so that a re-import can tell a curated
value from its own inference (§12.3), and with evidence that names rules and counts, never a
cell value (A6). The statuses:

- ``imported``: what the source gave. Labels from original names, a column's original name,
  a sheet, Parquet or database table's ``source``, a datatype a Parquet file or a database
  declares, the dataset's ``name``, ``source`` and ``packs``, and what a database declares
  (D307): a table's and a column's comment as its ``definition``, a primary key, and a foreign
  key's tables and columns.
- ``imported_default``: what a convention filled in. The ``source`` of a text file, whose parse
  settings were detected (curation is per member of ``fields``, so they take the status of
  ``source`` as a whole); conventional missing codes; a datetime datatype read without offsets
  (§12.2); a label made from an id, for an empty original name, a relationship or a coverage.
- ``proposed``: everything else the importer inferred. Grain, role, primary key, relationships
  and their cardinality, coverage, list syntax, identifiers, and the datatypes it did not read
  from the source.

Nothing is ever ``asserted``: only an operator asserts (§5.1).
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from pydantic import JsonValue, TypeAdapter

from aibi.core.importers.infer import Inferred, Status
from aibi.core.schema.descriptors import Descriptor, ParseSettings

_ADAPTER: TypeAdapter[Descriptor] = TypeAdapter(Descriptor)


@dataclass(frozen=True)
class TableOrigin:
    """Where a table came from."""

    kind: Literal["file", "sheet", "database"]
    original_name: str
    """The file's name, the sheet's, or the database table's."""
    label: str
    """The table's original name: a file's stem, a sheet's name, or both."""
    columns: tuple[str, ...]
    """The source column names, in source order."""
    parse: ParseSettings | None = None
    parse_evidence: str | None = None
    comment: str | None = None
    """What a database declares the table to be (D307)."""
    column_comments: tuple[str | None, ...] = ()
    """What a database declares each column to be, in source order (D307)."""


@dataclass(frozen=True)
class DatasetOrigin:
    name: str
    location: str
    packs: tuple[str, ...] = ()
    kind: Literal["files", "database"] = "files"


class _Curation:
    def __init__(self, by: str, at: str) -> None:
        self.by = by
        self.at = at

    def entry(self, status: Status, value: JsonValue, evidence: str | None) -> JsonValue:
        found: dict[str, JsonValue] = {"status": status, "by": self.by, "at": self.at}
        if evidence:
            found["evidence"] = evidence
        found["inferred"] = value
        return found

    def descriptor(
        self,
        kind: str,
        id: str,
        label: tuple[str, Status, str | None],
        fields: Mapping[str, tuple[JsonValue, Status, str | None]],
        definition: str | None = None,
    ) -> Descriptor:
        """A descriptor whose ``label`` and ``fields`` each have (value, status, evidence), with
        an ``imported`` ``definition`` when one is given."""
        curation: dict[str, JsonValue] = {"/label": self.entry(label[1], label[0], label[2])}
        described: dict[str, JsonValue] = {}
        if definition:
            described["definition"] = definition
            curation["/definition"] = self.entry("imported", definition, None)
        members: dict[str, JsonValue] = {}
        for name, (value, status, evidence) in fields.items():
            members[name] = value
            curation[f"/fields/{name}"] = self.entry(status, value, evidence)
        return _ADAPTER.validate_python(
            {
                "kind": kind,
                "id": id,
                "version": 1,
                "label": label[0],
                **described,
                "fields": members,
                "curation": curation,
            }
        )


def describe(
    dataset: DatasetOrigin,
    origins: Mapping[str, TableOrigin],
    inferred: Inferred,
    *,
    by: str,
    at: str,
) -> list[Descriptor]:
    """The dataset's descriptors: the dataset, each table and its columns in source order, then
    the relationships and their coverage. ``origins`` is by table id, in the tables' order."""
    curation = _Curation(by, at)
    found: list[Descriptor] = [
        curation.descriptor(
            "dataset",
            "dataset",
            (dataset.name, "imported", None),
            {
                "name": (dataset.name, "imported", None),
                "source": (
                    {"kind": dataset.kind, "location": dataset.location},
                    "imported",
                    None,
                ),
                "packs": (list(dataset.packs), "imported", None),
            },
        )
    ]
    for table in inferred.tables:
        origin = origins[table.id]
        source: dict[str, JsonValue] = {
            "kind": origin.kind,
            "name": table.id,
            "original_name": origin.original_name,
        }
        source_status: Status = "imported"
        if origin.parse is not None:
            source["parse"] = origin.parse.model_dump(mode="json")
            source_status = "imported_default"
        fields: dict[str, tuple[JsonValue, Status, str | None]] = {
            "source": (source, source_status, origin.parse_evidence),
            "role": (table.role, "proposed", table.role_evidence),
        }
        if table.key is not None:
            fields["primary_key"] = (list(table.key), table.key_status, table.key_evidence)
            grain = "One row per " + " and ".join(table.key)
            fields["grain"] = (grain, "proposed", "From the key (D229)")
        found.append(
            curation.descriptor(
                "table", table.id, (origin.label, "imported", None), fields, origin.comment
            )
        )
        comments = origin.column_comments or (None,) * len(origin.columns)
        for name, comment, (column, guess) in zip(
            origin.columns, comments, table.columns.items(), strict=True
        ):
            members: dict[str, tuple[JsonValue, Status, str | None]] = {}
            if guess.datatype is not None:
                members["datatype"] = (guess.datatype, guess.status, guess.evidence)
            if guess.missing_codes:
                count = len(guess.missing_codes)
                evidence = (
                    f"{count} conventional missing code{'s' if count != 1 else ''} found in the "
                    "column, declared UNKNOWN (D227)"
                )
                codes: dict[str, JsonValue] = dict(guess.missing_codes)
                members["missing_codes"] = (codes, "imported_default", evidence)
            if guess.list_syntax is not None:
                syntax = guess.list_syntax.model_dump(mode="json")
                members["list_syntax"] = (syntax, "proposed", guess.list_evidence)
            if guess.identifier:
                members["identifier"] = (True, "proposed", guess.identifier_evidence)
            members["source"] = ({"original_name": name}, "imported", None)
            label = (name, "imported", None) if name else (column, "imported_default", None)
            found.append(
                curation.descriptor("column", f"{table.id}.{column}", label, members, comment)
            )
    for relationship in inferred.relationships:
        why = relationship.evidence
        status = relationship.status
        columns = "+".join(relationship.columns)
        found.append(
            curation.descriptor(
                "relationship",
                relationship.id,
                (
                    f"{relationship.child}.{columns} to {relationship.parent}",
                    "imported_default",
                    None,
                ),
                {
                    "child_table": (relationship.child, status, why),
                    "child_columns": (list(relationship.columns), status, why),
                    "parent_table": (relationship.parent, status, why),
                    "parent_columns": (list(relationship.parent_columns), status, why),
                    "cardinality": (
                        "one-to-one" if relationship.one_to_one else "many-to-one",
                        "proposed",
                        why,
                    ),
                },
            )
        )
    for relationship in inferred.relationships:
        if not relationship.coverage:
            continue
        coverage = "cov:" + relationship.id.removeprefix("rel:")
        why = "Proposed for a relationship whose child table is an entity or a link table (§5.6)"
        found.append(
            curation.descriptor(
                "coverage",
                coverage,
                (f"Coverage of {relationship.id}", "imported_default", None),
                {
                    "relationship": (relationship.id, "proposed", why),
                    "parents": ("all", "proposed", why),
                },
            )
        )
    return found


def by_importer(name: str, version: str) -> str:
    return f"importer:{name}@{version}"


__all__ = ["DatasetOrigin", "TableOrigin", "by_importer", "describe"]

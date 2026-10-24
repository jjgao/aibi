"""A cBioPortal study's descriptors, raw snapshots and layouts (SPEC §5, §10.1, §12.2, D407, D409).

``describe`` turns what discovery found and the clinical files the reader read into what the
pack's importer returns. Everything it writes is declared by the study (its meta files and the
clinical files' header rows) or fixed by the format, never computed from the rows (§10.1, D271),
but for four things the rows decide, each by a rule the core's own importers keep: whether a
``STRING`` column is a category (D228), a column's added missing spellings (D437), whether the
relationship from the samples to the patients is imported or proposed (D230, D241), and the
notes, whose counts D277 suppresses under *k*.

- **The dataset:** its ``name`` (the operator's, else the study meta's ``name``, else its
  ``cancer_study_identifier``, ``imported_default``), the study meta's ``description``,
  ``citation`` and ``references`` (``PMID:<id>`` for each id of ``pmid``), the source's location,
  the pack, and the ``onco`` extension's ``cancer_study_identifier``, ``type_of_cancer`` and
  ``reference_genome``, each as written (D407).
- **The tables** ``patients`` and ``samples``: entities, mapped to ``onco:patient`` and
  ``onco:sample``, keyed by ``PATIENT_ID`` and ``SAMPLE_ID``, each a text snapshot of its file's
  bytes read with ``clinical.SETTINGS``, with its meta pair as the ``meta`` member.
- **The columns**, in source order: label and definition from header rows 1 and 2, the header
  name as the original name, the priority row as ``attribute_priority``; a key is a ``string``
  and an identifier, with no missing codes; any other column has ``FIXED`` and its additions
  (``imported`` with none, ``imported_default`` with some), and the datatype its row declares:
  ``number``, ``boolean``, or for ``STRING`` a ``category`` (``proposed``) or a ``string``; the
  four survival times are a ``time_offset`` in months (``attributes.TIME_OFFSETS``).
- **The relationship** ``rel:samples.<patient id>``, many-to-one to the patients' key:
  ``imported`` when every sample's patient has a row, ``proposed`` otherwise, so that the gate
  drops it (D230), with a note of the samples whose patient has none.

Every entry is ``importer:onco@<version>`` at the import's ``at``; the core writes each entry's
``inferred`` (``carry.with_inferences``). Ids come from ``normalise_names`` with the previous
release's names (D238); the tables' ids are fixed.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal

from pydantic import JsonValue, TypeAdapter

from aibi.core.schema.descriptors import Descriptor
from aibi.core.schema.ids import normalise_names
from aibi.core.schema.output import Segment, data, text
from aibi.core.schema.pack_api import ImportNote, Previous
from aibi.core.store.build import Layout
from aibi.core.store.sources import RawSource, TextSource
from aibi.packs.onco.attributes import MONTHS_UNITS, PATIENT_ID, SAMPLE_ID, TIME_OFFSETS
from aibi.packs.onco.clinical import SETTINGS, Clinical, Column
from aibi.packs.onco.facets import PACK_ID
from aibi.packs.onco.meta import PATIENT, SAMPLE, StudyFields
from aibi.packs.onco.missing import FIXED

Status = Literal["imported", "imported_default", "proposed"]
Entry = tuple[JsonValue, Status, str | None]

_ADAPTER: TypeAdapter[Descriptor] = TypeAdapter(Descriptor)

PATIENTS = "patients"
SAMPLES = "samples"
TABLES: Mapping[str, tuple[str, str, str, str]] = MappingProxyType(
    {
        PATIENTS: ("Patients", "One row per patient", "onco:patient", f"CLINICAL:{PATIENT}"),
        SAMPLES: ("Samples", "One row per sample", "onco:sample", f"CLINICAL:{SAMPLE}"),
    }
)
"""Each table's label, grain, table concept and ``meta`` pair."""
KEYS: Mapping[str, str] = MappingProxyType({PATIENTS: PATIENT_ID, SAMPLES: SAMPLE_ID})

FORMAT = "cBioPortal's clinical file format: four header rows, then tab-separated fields"
DECLARED = "The clinical file's datatype row declares it"
CATEGORY = "A STRING column with few distinct values, at most 50 and at most half (D228)"
KEY = "The clinical file's key column (cBioPortal's clinical file format)"
FIXED_ONLY = "cBioPortal's null words, as its importer defines them (MissingAttributeValues)"
WITH_ADDITIONS = FIXED_ONLY + ", and spellings of them that occur in this column (D437)"
MONTHS = "cBioPortal's clinical file format: the survival time in months"
RELATIONSHIP = "A sample file's PATIENT_ID names the sample's patient"
PADDED = (
    " has cells with spaces around them, read as written; cBioPortal trims them, and trimming is "
    "not proposed"
)
DANGLING = (
    "samples whose patient has no row in the patient file; the relationship is proposed and the "
    "gate drops it; it returns when the patient file holds every sample's patient. If an operator "
    "removed it in a session, only putting it back in a session restores it"
)
NO_PATIENTS = "the study has no patient file, so there is no patients table and no relationship"


class _Curation:
    def __init__(self, by: str, at: str) -> None:
        self.by = by
        self.at = at

    def entry(self, status: Status, evidence: str | None) -> dict[str, JsonValue]:
        found: dict[str, JsonValue] = {"status": status, "by": self.by, "at": self.at}
        if evidence is not None:
            found["evidence"] = evidence
        return found

    def descriptor(
        self,
        kind: str,
        id: str,
        label: tuple[str, Status],
        fields: Mapping[str, Entry],
        definition: str | None = None,
        extensions: Mapping[str, Entry] | None = None,
    ) -> Descriptor:
        curation: dict[str, JsonValue] = {"/label": self.entry(label[1], None)}
        written: dict[str, JsonValue] = {
            "kind": kind,
            "id": id,
            "version": 1,
            "label": label[0],
        }
        if definition is not None:
            written["definition"] = definition
            curation["/definition"] = self.entry("imported", None)
        members: dict[str, JsonValue] = {}
        for name, (value, status, evidence) in fields.items():
            members[name] = value
            curation[f"/fields/{name}"] = self.entry(status, evidence)
        written["fields"] = members
        if extensions:
            own: dict[str, JsonValue] = {}
            for name, (value, status, evidence) in extensions.items():
                own[name] = value
                curation[f"/extensions/{PACK_ID}/{name}"] = self.entry(status, evidence)
            written["extensions"] = {PACK_ID: own}
        written["curation"] = curation
        return _ADAPTER.validate_python(written)


def _dataset(
    curation: _Curation, fields: StudyFields, name: str | None, location: str | None
) -> Descriptor:
    title: tuple[str, Status]
    if name is not None:
        title = (name, "imported")
    elif fields.name is not None:
        title = (fields.name, "imported")
    else:
        title = (fields.cancer_study_identifier, "imported_default")
    members: dict[str, Entry] = {"name": (title[0], title[1], None)}
    if fields.description is not None:
        members["description"] = (fields.description, "imported", None)
    if fields.citation is not None:
        members["citation"] = ([fields.citation], "imported", None)
    if fields.pmid is not None:
        references: list[JsonValue] = [f"PMID:{token.strip()}" for token in fields.pmid.split(",")]
        members["references"] = (references, "imported", None)
    if location is not None:
        members["source"] = ({"kind": "files", "location": location}, "imported", None)
    members["packs"] = ([PACK_ID], "imported", None)
    extensions: dict[str, Entry] = {
        "cancer_study_identifier": (fields.cancer_study_identifier, "imported", None),
        "type_of_cancer": (fields.type_of_cancer, "imported", None),
    }
    if fields.reference_genome is not None:
        extensions["reference_genome"] = (fields.reference_genome, "imported", None)
    return curation.descriptor("dataset", "dataset", title, members, extensions=extensions)


def _datatype(column: Column) -> tuple[Entry, Entry | None]:
    """A column's datatype entry, and its units' where it has units."""
    if column.key:
        return ("string", "imported", KEY), None
    if column.declared == "NUMBER":
        timed = TIME_OFFSETS.get(column.name)
        if timed is not None:
            return ("time_offset", timed, MONTHS), (MONTHS_UNITS, timed, MONTHS)
        return ("number", "imported", DECLARED), None
    if column.declared == "BOOLEAN":
        return ("boolean", "imported", DECLARED), None
    if column.category:
        return ("category", "proposed", CATEGORY), None
    return ("string", "imported", DECLARED), None


def _column(curation: _Curation, table: str, id: str, column: Column) -> Descriptor:
    datatype, units = _datatype(column)
    fields: dict[str, Entry] = {"datatype": datatype}
    if units is not None:
        fields["units"] = units
    if column.key:
        fields["identifier"] = (True, "imported", KEY)
    else:
        codes: dict[str, JsonValue] = {**FIXED, **column.additions}
        ordered: dict[str, JsonValue] = dict(sorted(codes.items()))
        status: Status = "imported_default" if column.additions else "imported"
        evidence = WITH_ADDITIONS if column.additions else FIXED_ONLY
        fields["missing_codes"] = (ordered, status, evidence)
    fields["source"] = ({"original_name": column.name}, "imported", None)
    extensions: dict[str, Entry] = {"attribute_priority": (column.priority, "imported", None)}
    return curation.descriptor(
        "column",
        f"{table}.{id}",
        (column.label, "imported"),
        fields,
        definition=column.definition,
        extensions=extensions,
    )


def _table(curation: _Curation, table: str, file: Clinical, key: str) -> Descriptor:
    label, grain, concept, meta = TABLES[table]
    source: dict[str, JsonValue] = {
        "kind": "file",
        "name": table,
        "original_name": file.name,
        "parse": SETTINGS.model_dump(mode="json"),
    }
    fields: dict[str, Entry] = {
        "grain": (grain, "imported", FORMAT),
        "role": ("entity", "imported", FORMAT),
        "primary_key": ([key], "imported", FORMAT),
        "maps_to": ({"concept": concept}, "imported", FORMAT),
        "source": (source, "imported_default", FORMAT),
    }
    extensions: dict[str, Entry] = {"meta": (meta, "imported", None)}
    return curation.descriptor(
        "table", table, (label, "imported_default"), fields, extensions=extensions
    )


def _renamed(subject: str, original: str) -> ImportNote:
    return ImportNote("renamed", subject, [text("The original name was "), data(original)])


def _padded(subject: str, column: Column) -> ImportNote:
    message: list[Segment] = [text("The column "), data(column.name), text(PADDED)]
    return ImportNote(
        "not_proposed", subject, message, count=column.padded, rows=column.padded_rows
    )


@dataclass(frozen=True)
class Described:
    """What ``describe`` gives: the importer's result's parts, and the notes, in order."""

    descriptors: list[Descriptor]
    sources: dict[str, RawSource]
    layouts: dict[str, Layout]
    notes: list[ImportNote]


def describe(
    fields: StudyFields,
    files: Sequence[Clinical],
    *,
    by: str,
    at: str,
    location: str | None,
    name: str | None = None,
    previous: Previous | None = None,
) -> Described:
    """The study's descriptors, snapshots, layouts and notes: ``files`` are the clinical files,
    the patient file first where there is one."""
    curation = _Curation(by, at)
    descriptors: list[Descriptor] = [_dataset(curation, fields, name, location)]
    sources: dict[str, RawSource] = {}
    layouts: dict[str, Layout] = {}
    notes: list[ImportNote] = []
    ids_of: dict[str, list[str]] = {}
    for file in files:
        table = PATIENTS if file.role == "patient" else SAMPLES
        before = previous.columns.get(table) if previous is not None else None
        ids = normalise_names([column.name for column in file.columns], "column", before)
        ids_of[table] = ids
        key = ids[[column.name for column in file.columns].index(KEYS[table])]
        descriptors.append(_table(curation, table, file, key))
        for id, column in zip(ids, file.columns, strict=True):
            subject = f"{table}.{id}"
            descriptors.append(_column(curation, table, id, column))
            if id != column.name.lower():
                notes.append(_renamed(subject, column.name))
        for id, column in zip(ids, file.columns, strict=True):
            if column.padded:
                notes.append(_padded(f"{table}.{id}", column))
        sources[table] = TextSource(file.data)
        layouts[table] = Layout(
            table, tuple((id, column.name) for id, column in zip(ids, file.columns, strict=True))
        )
    sample = next(file for file in files if file.role == "sample")
    if PATIENTS in ids_of:
        child = ids_of[SAMPLES][[column.name for column in sample.columns].index(PATIENT_ID)]
        parent = ids_of[PATIENTS][[column.name for column in files[0].columns].index(PATIENT_ID)]
        status: Status = "proposed" if sample.dangling else "imported"
        relationship = f"rel:{SAMPLES}.{child}"
        descriptors.append(
            curation.descriptor(
                "relationship",
                relationship,
                (f"{SAMPLES}.{child} to {PATIENTS}", "imported_default"),
                {
                    "child_table": (SAMPLES, status, RELATIONSHIP),
                    "child_columns": ([child], status, RELATIONSHIP),
                    "parent_table": (PATIENTS, status, RELATIONSHIP),
                    "parent_columns": ([parent], status, RELATIONSHIP),
                    "cardinality": ("many-to-one", status, RELATIONSHIP),
                },
            )
        )
        if sample.dangling:
            notes.append(
                ImportNote(
                    "not_proposed",
                    relationship,
                    [text(DANGLING)],
                    count=sample.dangling,
                    rows=sample.dangling_rows,
                )
            )
    else:
        notes.append(ImportNote("not_proposed", None, [text(NO_PATIENTS)]))
    return Described(descriptors, sources, layouts, notes)


__all__ = ["PATIENTS", "SAMPLES", "Described", "describe"]

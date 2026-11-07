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
from aibi.core.store.sources import RawSource, SourceValue, TextSource, TypedSource
from aibi.packs.onco.attributes import MONTHS_UNITS, PATIENT_ID, SAMPLE_ID, TIME_OFFSETS
from aibi.packs.onco.caselists import CaseList
from aibi.packs.onco.clinical import SETTINGS, Clinical, Column
from aibi.packs.onco.facets import PACK_ID
from aibi.packs.onco.meta import PATIENT, SAMPLE, StudyFields
from aibi.packs.onco.missing import FIXED
from aibi.packs.onco.mutation import (
    AMINO_ACID,
    BARCODE,
    COUNTS,
    ENTREZ,
    HUGO,
    IDENTIFIERS,
    POSITIONS,
    Maf,
    MafColumn,
)

Status = Literal["imported", "imported_default", "proposed"]
Entry = tuple[JsonValue, Status, str | None]

_ADAPTER: TypeAdapter[Descriptor] = TypeAdapter(Descriptor)

PATIENTS = "patients"
SAMPLES = "samples"
MUTATIONS = "mutations"
CASE_LISTS = "case_lists"
MEMBERS = "case_list_members"
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


MAF_FORMAT = "cBioPortal's mutation file format (MAF): tab-separated, a line of column names"
MAF_INTEGER = "cBioPortal's validator reads this column as an integer"
MAF_MISSING = "The spellings cBioPortal's validator reads as no value in this column"
MAF_CATEGORY = "A MAF column whose values the format lists, with few distinct values (D228)"
MAF_TEXT = "A MAF column the pack has no rule for, read as text"
MAF_GENE = "cBioPortal's mutation file format: the gene's symbol, as written"
MAF_SAMPLE = "cBioPortal's mutation file format: a sample's id"
MUTATION_RELATIONSHIP = "A MAF's Tumor_Sample_Barcode names the sample (D444: every barcode is one)"
CASE_LIST_FORMAT = "cBioPortal's case list format: one file a list, read as keys and values"
MEMBER_RELATIONSHIP = "A case list's case_list_ids name samples of the sample file"
LIST_RELATIONSHIP = "A member row belongs to the case list it was read from"
MAF_MISSING_CODES: Mapping[str, Mapping[str, str]] = MappingProxyType(
    {
        ENTREZ: MappingProxyType({"0": "UNKNOWN", "NA": "UNKNOWN"}),
        **{column: MappingProxyType({"NA": "UNKNOWN"}) for column in POSITIONS},
        **{column: MappingProxyType({".": "UNKNOWN"}) for column in COUNTS},
    }
)
"""The spellings the reference reads as no value in an integer column: ``0`` and ``NA`` as an
Entrez id (V:1813-1816), ``NA`` as a position (V:1876, V:2388), ``.`` as a count (V:2244)."""
MAF_TEXT_MISSING_CODES: Mapping[str, Mapping[str, str]] = MappingProxyType(
    {
        HUGO: MappingProxyType({"NA": "UNKNOWN", "Unknown": "UNKNOWN"}),
        **{column: MappingProxyType({"NA": "UNKNOWN", "NULL": "UNKNOWN"}) for column in AMINO_ACID},
    }
)
"""The spellings the reference reads as no value in a text column, exactly as written: ``NA`` and
``Unknown`` as a gene symbol (V:1806-1810), ``NULL`` and ``NA`` as an amino-acid change
(V:1637). A padded ``NA`` or ``Unknown`` symbol, which the reference strips first, is refused on
a validated row (``PADDED_VALUE``, D444)."""


def _maf_column(curation: "_Curation", id: str, column: MafColumn) -> Descriptor:
    fields: dict[str, Entry] = {}
    codes = MAF_MISSING_CODES.get(column.name)
    text_codes = MAF_TEXT_MISSING_CODES.get(column.name)
    if codes is not None:
        fields["datatype"] = ("integer", "imported", MAF_INTEGER)
        written: dict[str, JsonValue] = dict(sorted(codes.items()))
        fields["missing_codes"] = (written, "imported", MAF_MISSING)
    elif column.category:
        fields["datatype"] = ("category", "proposed", MAF_CATEGORY)
    else:
        fields["datatype"] = ("string", "imported", MAF_TEXT)
    if text_codes is not None:
        found: dict[str, JsonValue] = dict(sorted(text_codes.items()))
        fields["missing_codes"] = (found, "imported", MAF_MISSING)
    if column.name in IDENTIFIERS:
        fields["identifier"] = (True, "imported", MAF_SAMPLE)
    if column.name == HUGO:
        fields["maps_to"] = ({"concept": "onco:gene_symbol"}, "imported", MAF_GENE)
    fields["source"] = ({"original_name": column.name}, "imported", None)
    return curation.descriptor(
        "column", f"{MUTATIONS}.{id}", (column.name, "imported_default"), fields
    )


def _relationship(
    curation: "_Curation", child: str, column: str, parent: str, key: str, evidence: str
) -> Descriptor:
    return curation.descriptor(
        "relationship",
        f"rel:{child}.{column}",
        (f"{child}.{column} to {parent}", "imported_default"),
        {
            "child_table": (child, "imported", evidence),
            "child_columns": ([column], "imported", evidence),
            "parent_table": (parent, "imported", evidence),
            "parent_columns": ([key], "imported", evidence),
            "cardinality": ("many-to-one", "imported", evidence),
        },
    )


def _plain_table(
    curation: "_Curation",
    table: str,
    label_grain: tuple[str, str],
    role: str,
    key: list[JsonValue] | None,
    concept: str,
    source: dict[str, JsonValue],
    evidence: str,
    extensions: Mapping[str, Entry] | None = None,
) -> Descriptor:
    label, grain = label_grain
    fields: dict[str, Entry] = {
        "grain": (grain, "imported", evidence),
        "role": (role, "imported", evidence),
    }
    if key is not None:
        fields["primary_key"] = (key, "imported", evidence)
    fields["maps_to"] = ({"concept": concept}, "imported", evidence)
    fields["source"] = (source, "imported_default", evidence)
    return curation.descriptor(
        "table", table, (label, "imported_default"), fields, extensions=extensions
    )


def _text_column(
    curation: "_Curation", table: str, id: str, label: str, identifier: bool = False
) -> Descriptor:
    fields: dict[str, Entry] = {"datatype": ("string", "imported", CASE_LIST_FORMAT)}
    if identifier:
        fields["identifier"] = (True, "imported", CASE_LIST_FORMAT)
    fields["source"] = ({"original_name": id}, "imported", None)
    return curation.descriptor("column", f"{table}.{id}", (label, "imported"), fields)


def _mutations(
    curation: "_Curation", maf: Maf, previous: Previous | None, sample_key: str
) -> tuple[list[Descriptor], RawSource, Layout, list[ImportNote]]:
    before = previous.columns.get(MUTATIONS) if previous is not None else None
    names = [column.name for column in maf.columns]
    ids = normalise_names(names, "column", before)
    source: dict[str, JsonValue] = {
        "kind": "file",
        "name": MUTATIONS,
        "original_name": maf.name,
        "parse": maf.settings.model_dump(mode="json"),
    }
    descriptors = [
        _plain_table(
            curation,
            MUTATIONS,
            ("Mutations", "One row per mutation call of the MAF"),
            "measurement",
            None,
            "onco:mutation",
            source,
            MAF_FORMAT,
            {"meta": ("MUTATION_EXTENDED:MAF", "imported", None)},
        )
    ]
    notes: list[ImportNote] = []
    for id, column in zip(ids, maf.columns, strict=True):
        descriptors.append(_maf_column(curation, id, column))
        if id != column.name.lower():
            notes.append(_renamed(f"{MUTATIONS}.{id}", column.name))
    barcode = ids[names.index(BARCODE)]
    descriptors.append(
        _relationship(curation, MUTATIONS, barcode, SAMPLES, sample_key, MUTATION_RELATIONSHIP)
    )
    layout = Layout(MUTATIONS, tuple(zip(ids, names, strict=True)))
    return descriptors, TextSource(maf.data), layout, notes


LIST_COLUMNS = ("stable_id", "name", "description", "category")
MEMBER_COLUMNS = ("case_list", "sample_id")


def _case_lists(
    curation: "_Curation", lists: Sequence[CaseList], sample_key: str
) -> tuple[list[Descriptor], dict[str, RawSource], dict[str, Layout]]:
    def source(table: str) -> dict[str, JsonValue]:
        return {"kind": "pack", "name": table, "original_name": "case_lists"}

    descriptors = [
        _plain_table(
            curation,
            CASE_LISTS,
            ("Case lists", "One row per case list of the study"),
            "entity",
            ["stable_id"],
            "onco:case_list",
            source(CASE_LISTS),
            CASE_LIST_FORMAT,
        ),
        _text_column(curation, CASE_LISTS, "stable_id", "Stable id"),
        _text_column(curation, CASE_LISTS, "name", "Name"),
        _text_column(curation, CASE_LISTS, "description", "Description"),
        _text_column(curation, CASE_LISTS, "category", "Category"),
        _plain_table(
            curation,
            MEMBERS,
            ("Case list members", "One row per sample id a case list names, repeats kept"),
            "link",
            None,
            "onco:case_list_member",
            source(MEMBERS),
            CASE_LIST_FORMAT,
        ),
        _text_column(curation, MEMBERS, "case_list", "Case list"),
        _text_column(curation, MEMBERS, "sample_id", "Sample id", identifier=True),
        _relationship(curation, MEMBERS, "case_list", CASE_LISTS, "stable_id", LIST_RELATIONSHIP),
        _relationship(curation, MEMBERS, "sample_id", SAMPLES, sample_key, MEMBER_RELATIONSHIP),
    ]
    rows: list[tuple[SourceValue, ...]] = [
        (found.stable_id, found.name, found.description, found.category) for found in lists
    ]
    members: list[tuple[SourceValue, ...]] = [
        (found.stable_id, sample) for found in lists for sample in found.ids
    ]
    sources: dict[str, RawSource] = {
        CASE_LISTS: TypedSource(LIST_COLUMNS, tuple(rows)),
        MEMBERS: TypedSource(MEMBER_COLUMNS, tuple(members)),
    }
    layouts = {
        CASE_LISTS: Layout(CASE_LISTS, tuple((name, name) for name in LIST_COLUMNS)),
        MEMBERS: Layout(MEMBERS, tuple((name, name) for name in MEMBER_COLUMNS)),
    }
    return descriptors, sources, layouts


def list_cells(lists: Sequence[CaseList]) -> int:
    """The cells the core counts for the two case-list tables (columns times rows, at least one
    row each), when the study has any list."""
    if not lists:
        return 0
    members = sum(len(found.ids) for found in lists)
    return len(LIST_COLUMNS) * max(1, len(lists)) + len(MEMBER_COLUMNS) * max(1, members)


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
    maf: Maf | None = None,
    lists: Sequence[CaseList] = (),
) -> Described:
    """The study's descriptors, snapshots, layouts and notes: ``files`` are the clinical files,
    the patient file first where there is one; ``maf`` the study's MAF, read, and ``lists`` its
    case lists (D444)."""
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
    sample_key = ids_of[SAMPLES][[column.name for column in sample.columns].index(SAMPLE_ID)]
    if maf is not None:
        found, raw, layout, renamed = _mutations(curation, maf, previous, sample_key)
        descriptors.extend(found)
        sources[MUTATIONS] = raw
        layouts[MUTATIONS] = layout
        notes.extend(renamed)
    if lists:
        more, raws, laid = _case_lists(curation, lists, sample_key)
        descriptors.extend(more)
        sources.update(raws)
        layouts.update(laid)
    return Described(descriptors, sources, layouts, notes)


__all__ = [
    "CASE_LISTS",
    "MEMBERS",
    "MUTATIONS",
    "PATIENTS",
    "SAMPLES",
    "Described",
    "describe",
    "list_cells",
]

"""The oncology pack's importer and validator, end to end (M4.2a-1b, D409's clinical files, D437,
D438): a study imported through ``PACK.importer`` into a store, its descriptors, notes and
relationship, re-imports, sessions, the wiring's own refusal, the note budget, the rows-only rule
and the validator.

The study is the synthetic ``fixtures/onco/demo_study`` or one built here; nothing of a real study
is in the repository.
"""

import importlib
import json
import logging
import shutil
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from pydantic import JsonValue

from aibi.core.importers.confine import Confinement
from aibi.core.importers.errors import ImportRefused
from aibi.core.schema.descriptors import (
    ColumnDescriptor,
    DatasetDescriptor,
    Descriptor,
    RelationshipDescriptor,
    TableDescriptor,
)
from aibi.core.schema.limits import MAX_QUEUE_BYTES, MAX_QUEUE_ITEMS, ImportLimits
from aibi.core.schema.output import DataSegment, TextSegment
from aibi.core.schema.pack_api import ImportOptions, ImportResult, PackRegistry
from aibi.core.store.edits import EditRefused

HERE = Path(__file__).resolve().parent
DEMO = HERE.parents[3] / "fixtures" / "onco" / "demo_study"
BY = "importer:onco@0.3.0"


def copy_demo(world: Any, name: str = "demo") -> Path:
    target = world.root / "imports" / name
    shutil.copytree(DEMO, target)
    return target


def descriptors(world: Any, manifest: str) -> dict[str, Descriptor]:
    return {d.id: d for d in world.store.descriptors(manifest)}


def latest(world: Any, dataset: str) -> dict[str, Descriptor]:
    found = world.store.latest(dataset)
    assert found is not None
    return descriptors(world, found.manifest)


def notes_said(notes: Any) -> list[str]:
    return [
        f"{n.kind} {n.subject}: "
        + "".join(
            s.text if isinstance(s, TextSegment) else f"\u00ab{s.data}\u00bb" for s in n.message
        )
        + ("" if n.count is None else f" ({n.count}; rows {list(n.rows)})")
        for n in notes
    ]


def refusal_of(error: pytest.ExceptionInfo[ImportRefused]) -> tuple[str, str]:
    (refusal,) = error.value.refusals
    said = "".join(
        s.text if isinstance(s, TextSegment) else f"\u00ab{s.data}\u00bb" for s in refusal.message
    )
    return str(refusal.code), said


# --- The demo study ------------------------------------------------------------------------------


def test_the_demo_study_imports(world: Any, registry: PackRegistry) -> None:
    published = world.publish("demo", copy_demo(world), registry, pack="onco")
    assert published.label == 1
    found = descriptors(world, published.manifest)
    dataset = found["dataset"]
    assert isinstance(dataset, DatasetDescriptor)
    assert dataset.label == "Demo oncology study (synthetic)"
    assert dataset.fields.citation == ["aibi fixtures, 2026"]
    assert dataset.fields.references == ["PMID:12345"]
    assert dataset.fields.packs == ["onco"]
    assert dataset.fields.source is not None
    assert (dataset.fields.source.kind, dataset.fields.source.location) == ("files", "demo")
    assert dataset.extensions["onco"] == {
        "cancer_study_identifier": "demo_onco_2026",
        "type_of_cancer": "mixed",
        "reference_genome": "hg19",
    }
    assert {d.id for d in found.values() if isinstance(d, TableDescriptor)} == {
        "patients",
        "samples",
    }
    patients = found["patients"]
    assert isinstance(patients, TableDescriptor)
    assert patients.fields.primary_key == ["patient_id"]
    assert patients.fields.role == "entity"
    assert patients.fields.maps_to is not None
    assert patients.fields.maps_to.concept == "onco:patient"
    assert patients.extensions["onco"] == {"meta": "CLINICAL:PATIENT_ATTRIBUTES"}
    assert patients.fields.source is not None
    assert patients.fields.source.original_name == "data_clinical_patient.txt"
    types = {d.id: d.fields.datatype for d in found.values() if isinstance(d, ColumnDescriptor)}
    assert types == {
        "patients.patient_id": "string",
        "patients.sex": "category",
        "patients.age": "number",
        "patients.os_status": "category",
        "patients.os_months": "time_offset",
        "patients.dfs_status": "category",
        "patients.dfs_months": "time_offset",
        "patients.smoker": "boolean",
        "samples.patient_id": "string",
        "samples.sample_id": "string",
        "samples.sample_type": "category",
        "samples.cancer_type": "category",
        "samples.oncotree_code": "string",
        "samples.tmb_nonsynonymous": "number",
    }
    os_months = found["patients.os_months"]
    assert isinstance(os_months, ColumnDescriptor)
    assert os_months.fields.units == "mo"
    assert os_months.label == "Overall Survival (Months)"
    assert os_months.definition == "Overall survival in months since initial diagnosis."
    assert os_months.extensions["onco"] == {"attribute_priority": 1}
    assert os_months.curation["/fields/datatype"].status == "imported_default"
    key = found["patients.patient_id"]
    assert isinstance(key, ColumnDescriptor)
    assert key.fields.missing_codes is None
    assert key.fields.identifier is True
    relationship = found["rel:samples.patient_id"]
    assert isinstance(relationship, RelationshipDescriptor)
    assert relationship.curation["/fields/child_columns"].status == "imported"
    assert all(entry.by == BY for d in found.values() for entry in d.curation.values()
               if d.kind != "concept")  # fmt: skip
    said = notes_said(published.notes)
    assert (
        "skipped_source None: a meta file of a type M4.2a does not import: "
        "\u00abmeta_mutations.txt\u00bb" in said
    )
    assert any(
        s.startswith("not_proposed patients.sex: The column \u00abSEX\u00bb has cells")
        for s in said
    )
    assert any(s.startswith("unparsed samples.tmb_nonsynonymous") for s in said)


DEMO_NOTES = [
    "not_proposed patients.sex: The column \u00abSEX\u00bb has cells with spaces around them, "
    "read as written; cBioPortal trims them, and trimming is not proposed (1; rows [7])",
    "skipped_source None: a data file of a meta file M4.2a does not import: "
    "\u00abdata_mutations.txt\u00bb",
    "skipped_source None: a meta file of a type M4.2a does not import: "
    "\u00abmeta_mutations.txt\u00bb",
    "skipped_source None: a subdirectory (case lists are M4.2b's): \u00abcase_lists\u00bb",
    "unparsed samples.tmb_nonsynonymous: Cells whose value does not parse as the column's "
    "datatype, which are UNKNOWN (1; rows [5])",
]
"""The demo study's notes, exactly, in order: nothing is noted of a key column, of a column that
reads clean or of a table, and each note is the template's text with its data."""


def test_the_demo_study_s_notes_are_exactly_these(world: Any, registry: PackRegistry) -> None:
    published = world.publish("demo", copy_demo(world), registry, pack="onco")
    assert notes_said(published.notes) == DEMO_NOTES


def test_a_survival_time_is_a_time_offset_only_where_it_is_declared_a_number(
    world: Any, registry: PackRegistry
) -> None:
    """``PFS_MONTHS`` and ``DSS_MONTHS`` are read in months, as proposals, when the datatype row
    says NUMBER; declared STRING they are text, with no units and no proposal (the name alone is
    not the reading)."""

    def found(kind: str) -> dict[str, Descriptor]:
        patient = (
            f"#P\tL\tL\n#P\tD\tD\n#STRING\t{kind}\t{kind}\n#1\t1\t1\n"
            "PATIENT_ID\tPFS_MONTHS\tDSS_MONTHS\nP1\t1.5\t2.5\nP2\tabc\tdef\n"
        )
        files = study_files(["P1\tS1", "P2\tS2"], ["P1\tF", "P2\tM"])
        files["data_clinical_patient.txt"] = patient.encode()
        name = f"study_{kind.lower()}"
        published = world.publish(name, world.write(name, files), registry, pack="onco")
        return descriptors(world, published.manifest)

    for kind, datatype in (("NUMBER", "time_offset"), ("STRING", "string")):
        columns = found(kind)
        for name in ("pfs_months", "dss_months"):
            column = columns[f"patients.{name}"]
            assert isinstance(column, ColumnDescriptor)
            assert column.fields.datatype == datatype, (kind, name)
            assert (column.fields.units == "mo") == (kind == "NUMBER"), (kind, name)
            status = column.curation["/fields/datatype"].status
            assert status == ("proposed" if kind == "NUMBER" else "imported"), (kind, name)


@pytest.mark.parametrize("name", ["st\ufffe", "st\U0010ffff", "st\udcff", "\ufdd0"])
def test_a_study_directory_whose_name_is_not_text_records_no_source(
    world: Any, registry: PackRegistry, name: str
) -> None:
    """The directory's name is the dataset's source location where it is Unicode text of a
    descriptor string's length; where it is not (a noncharacter, or bytes that are no UTF-8), the
    dataset records no source and a note says so, and the import never fails as the pack's."""
    importer = importlib.import_module("aibi.packs.onco.importer")
    published = world.publish("s", world.write(name, CLOSED), registry, pack="onco")
    dataset = descriptors(world, published.manifest)["dataset"]
    assert isinstance(dataset, DatasetDescriptor)
    assert dataset.fields.source is None
    assert "/fields/source" not in dataset.curation
    (said,) = [
        n for n in published.notes if n.subject is None and n.kind == "not_proposed"
        and any(isinstance(s, TextSegment) and s.text == importer.NO_LOCATION for s in n.message)
    ]  # fmt: skip
    assert said.count is None


class Located:
    """A reader that gives every path the location it is told to, and reads as ``inner`` does."""

    def __init__(self, inner: Confinement, location: str) -> None:
        self.inner = inner
        self.where = location

    def files(self, directory: Any) -> Any:
        return self.inner.files(directory)

    def read(self, path: Any, limit: int) -> bytes:
        return self.inner.read(path, limit)

    def location(self, path: Any) -> str:
        return self.where


@pytest.mark.parametrize(
    ("location", "recorded"),
    [
        ("demo", "demo"),
        (".", "demo"),
        ("", None),
        ("x" * 4_096, "x" * 4_096),
        ("x" * 4_097, None),
        ("a\ufffeb", None),
        ("a\udcffb", None),
    ],
)
def test_the_dataset_records_a_source_only_for_a_location_that_is_text(
    world: Any, onco: ModuleType, location: str, recorded: str | None
) -> None:
    """The reader's location of the study's directory (a ``"."`` is the directory's own name) is
    the dataset's source when it is Unicode text of at most a descriptor string's characters and
    not empty; otherwise there is no source and one note says so. The guards a real directory
    cannot reach (an empty location, one past 4,096 characters) are held through a reader."""
    importer = importlib.import_module("aibi.packs.onco.importer")
    directory = copy_demo(world)
    confinement = Confinement.of(directory.parent)
    reader = Located(confinement, location)
    options = ImportOptions(
        dataset="demo", reader=reader, limits=ImportLimits(), at="2026-01-01T00:00:00Z"
    )
    source = confinement.confine(directory)
    result = onco.PACK.importer.import_source(source, options)
    (dataset,) = [d for d in result.descriptors if isinstance(d, DatasetDescriptor)]
    if recorded is None:
        assert dataset.fields.source is None
        assert "/fields/source" not in dataset.curation
    else:
        assert dataset.fields.source is not None
        assert dataset.fields.source.location == recorded
    said = [
        isinstance(seg, TextSegment) and seg.text == importer.NO_LOCATION
        for n in result.notes
        for seg in n.message
    ]
    assert sum(said) == (recorded is None)


def test_the_snapshot_is_the_file_s_bytes(world: Any, onco: ModuleType) -> None:
    directory = copy_demo(world)
    confinement = Confinement.of(directory.parent)
    options = ImportOptions(
        dataset="demo", reader=confinement, limits=ImportLimits(), at="2026-01-01T00:00:00Z"
    )
    result: ImportResult = onco.PACK.importer.import_source(confinement.confine(directory), options)
    assert result.sources["patients"].data == (directory / "data_clinical_patient.txt").read_bytes()  # type: ignore[union-attr]
    assert result.sources["samples"].data == (directory / "data_clinical_sample.txt").read_bytes()  # type: ignore[union-attr]
    assert result.layouts["patients"].source == "patients"
    assert result.layouts["patients"].columns[0] == ("patient_id", "PATIENT_ID")


def column(found: dict[str, Descriptor], id: str) -> ColumnDescriptor:
    descriptor = found[id]
    assert isinstance(descriptor, ColumnDescriptor)
    return descriptor


def test_missing_codes_and_their_status(
    world: Any, registry: PackRegistry, onco: ModuleType
) -> None:
    missing = importlib.import_module("aibi.packs.onco.missing")
    directory = copy_demo(world)
    patient = (directory / "data_clinical_patient.txt").read_text()
    (directory / "data_clinical_patient.txt").write_text(patient.replace("\t70\t", "\t NA\t"))
    found = descriptors(world, world.publish("demo", directory, registry, pack="onco").manifest)
    sex = column(found, "patients.sex")
    assert sex.fields.missing_codes == dict(missing.FIXED)
    assert sex.curation["/fields/missing_codes"].status == "imported"
    age = column(found, "patients.age")
    assert age.fields.missing_codes == {**missing.FIXED, " NA": "UNKNOWN"}
    assert age.curation["/fields/missing_codes"].status == "imported_default"
    evidence = age.curation["/fields/missing_codes"].evidence or ""
    assert "D437" in evidence
    assert "NA" not in evidence.replace("MissingAttributeValues", "")


# --- The relationship -----------------------------------------------------------------------------


def study_files(sample_rows: list[str], patient_rows: list[str] | None) -> dict[str, bytes]:
    files = {
        "meta_study.txt": (DEMO / "meta_study.txt").read_bytes(),
        "meta_clinical_sample.txt": (DEMO / "meta_clinical_sample.txt").read_bytes(),
        "data_clinical_sample.txt": (
            "#Patient\tSample\n#P\tS\n#STRING\tSTRING\n#1\t1\nPATIENT_ID\tSAMPLE_ID\n"
            + "".join(row + "\n" for row in sample_rows)
        ).encode(),
    }
    if patient_rows is not None:
        files["meta_clinical_patient.txt"] = (DEMO / "meta_clinical_patient.txt").read_bytes()
        files["data_clinical_patient.txt"] = (
            "#Patient\tSex\n#P\tS\n#STRING\tSTRING\n#1\t1\nPATIENT_ID\tSEX\n"
            + "".join(row + "\n" for row in patient_rows)
        ).encode()
    return files


def rewrite(directory: Path, files: dict[str, bytes]) -> Path:
    for entry in directory.iterdir():
        entry.unlink()
    for name, content in files.items():
        (directory / name).write_bytes(content)
    return directory


CLOSED = study_files(["P1\tS1", "P2\tS2"], ["P1\tF", "P2\tM"])
DANGLING = study_files(["P1\tS1", "P9\tS2", "P8\tS3"], ["P1\tF", "P2\tM"])


def test_a_dangling_sample_drops_the_relationship_with_a_note(
    world: Any, registry: PackRegistry
) -> None:
    published = world.publish("s", world.write("s", DANGLING), registry, pack="onco")
    found = descriptors(world, published.manifest)
    assert "rel:samples.patient_id" not in found
    said = notes_said(published.notes)
    assert any(s.startswith("dropped rel:samples.patient_id") for s in said)
    (dangling,) = [
        n
        for n in published.notes
        if n.kind == "not_proposed" and n.subject == "rel:samples.patient_id"
    ]
    assert (dangling.count, list(dangling.rows)) == (2, [2, 3])


def test_no_patient_file(world: Any, registry: PackRegistry) -> None:
    files = study_files(["P1\tS1"], None)
    published = world.publish("s", world.write("s", files), registry, pack="onco")
    found = descriptors(world, published.manifest)
    assert "patients" not in found
    assert not any(isinstance(d, RelationshipDescriptor) for d in found.values())
    assert any("the study has no patient file" in s for s in notes_said(published.notes))


def test_the_relationship_through_re_imports_and_sessions(
    world: Any, registry: PackRegistry
) -> None:
    """Imported while closed; a dangling re-import refused; removed in a session, the dangling
    re-import publishes; closed again, it stays removed (its tombstone holds the same inference),
    until a session puts it back; a closed re-import then keeps it (D239, D241)."""
    directory = world.write("s", CLOSED)
    world.publish("s", directory, registry, pack="onco")
    assert "rel:samples.patient_id" in latest(world, "s")
    rewrite(directory, DANGLING)
    with pytest.raises(ImportRefused) as raised:
        world.reimport("s", directory, registry, pack="onco")
    assert refusal_of(raised)[0] == "DANGLING_REFERENCE"
    world.curate("s", registry, {"op": "remove_descriptor", "descriptor": "rel:samples.patient_id"})
    assert "rel:samples.patient_id" not in latest(world, "s")
    world.reimport("s", directory, registry, pack="onco")
    assert "rel:samples.patient_id" not in latest(world, "s")
    rewrite(directory, CLOSED)
    world.reimport("s", directory, registry, pack="onco")
    assert "rel:samples.patient_id" not in latest(world, "s")
    put: JsonValue = {
        "op": "put",
        "descriptor": {
            "kind": "relationship",
            "id": "rel:samples.patient_id",
            "label": "samples.patient_id to patients",
            "fields": {
                "child_table": "samples",
                "child_columns": ["patient_id"],
                "parent_table": "patients",
                "parent_columns": ["patient_id"],
                "cardinality": "many-to-one",
            },
        },
    }
    world.curate("s", registry, put)  # type: ignore[arg-type]
    assert "rel:samples.patient_id" in latest(world, "s")
    rewrite(directory, study_files(["P1\tS1", "P2\tS2", "P2\tS3"], ["P1\tF", "P2\tM"]))
    world.reimport("s", directory, registry, pack="onco")
    assert "rel:samples.patient_id" in latest(world, "s")


def test_a_re_import_of_the_same_bytes_changes_nothing(world: Any, registry: PackRegistry) -> None:
    directory = copy_demo(world)
    world.publish("demo", directory, registry, pack="onco")
    with pytest.raises(ImportRefused) as raised:
        world.reimport("demo", directory, registry, pack="onco")
    assert refusal_of(raised)[0] == "NO_CHANGE"


# --- Meta files that cannot be read (the settlement's end-to-end rows) ----------------------------


@pytest.mark.parametrize(
    ("content", "reason"),
    [
        (b"cancer_study_identifier: demo_onco_2026\nno colon here\n", "has a line without ':'"),
        (b"cancer_study_identifier: d\xe9mo\n", "is not UTF-8"),
    ],
)
def test_an_unreadable_patient_meta_refuses_the_first_import(
    world: Any, registry: PackRegistry, content: bytes, reason: str
) -> None:
    directory = copy_demo(world)
    (directory / "meta_clinical_patient.txt").write_bytes(content)
    with pytest.raises(ImportRefused) as raised:
        world.publish("demo", directory, registry, pack="onco")
    assert refusal_of(raised) == (
        "onco.META_FILE",
        f"\u00abmeta_clinical_patient.txt\u00bb {reason}",
    )


def test_a_symlinked_patient_meta_refuses_the_first_import(
    world: Any, registry: PackRegistry
) -> None:
    directory = copy_demo(world)
    (directory / "meta_clinical_patient.txt").rename(directory.parent / "elsewhere.txt")
    (directory / "meta_clinical_patient.txt").symlink_to(directory.parent / "elsewhere.txt")
    with pytest.raises(ImportRefused) as raised:
        world.publish("demo", directory, registry, pack="onco")
    assert refusal_of(raised) == (
        "onco.META_FILE",
        "\u00abmeta_clinical_patient.txt\u00bb is a symlink",
    )


def test_a_curated_study_re_imported_with_a_defect_keeps_its_release(
    world: Any, registry: PackRegistry
) -> None:
    directory = copy_demo(world)
    world.publish("demo", directory, registry, pack="onco")
    label = world.curate(
        "demo",
        registry,
        {"op": "set", "descriptor": "patients.sex", "pointer": "/label", "value": "Sex at birth"},
    )
    assert label == 2
    (directory / "meta_clinical_patient.txt").write_bytes(b"genetic_alteration_type CLINICAL\n")
    with pytest.raises(ImportRefused) as raised:
        world.reimport("demo", directory, registry, pack="onco")
    assert refusal_of(raised)[0] == "onco.META_FILE"
    release = world.store.latest("demo")
    assert release is not None
    assert release.label == 2
    found = descriptors(world, release.manifest)
    assert "patients" in found
    assert found["patients.sex"].label == "Sex at birth"


# --- The wiring ----------------------------------------------------------------------------------


def test_an_import_through_the_pack_refuses_a_bad_path_value(
    world: Any, registry: PackRegistry, oracle: Any
) -> None:
    """The closure guard (D436) holds the ``study`` module; this holds that ``PACK.importer``
    reaches it: a non-clinical meta naming a clinical data file is refused through the import. The
    meta is of ``paths_oracle.NEVER_IMPORTED``'s type, which no slice of M4.2b reads (until
    M4.2b-1a, the demo's own mutation meta, which M4.2b imports)."""
    alteration, datatype, _ = oracle.NEVER_IMPORTED
    directory = copy_demo(world)
    (directory / "meta_methylation.txt").write_text(
        f"cancer_study_identifier: demo_onco_2026\ngenetic_alteration_type: {alteration}\n"
        f"datatype: {datatype}\nstable_id: methylation_hm27\nshow_profile_in_analysis_tab: true\n"
        "profile_name: M\nprofile_description: M\ndata_filename: data_clinical_sample.txt\n"
    )
    with pytest.raises(ImportRefused) as raised:
        world.publish("demo", directory, registry, pack="onco")
    code, said = refusal_of(raised)
    assert code == "onco.DATA_FILE"
    assert said.startswith(
        "\u00abmeta_methylation.txt\u00bb has a data_filename that names a meta file or a clinical"
    )


def test_the_validators(world: Any, registry: PackRegistry, onco: ModuleType) -> None:
    """``type_of_cancer`` is kept as an operator writes it in a session while OncoTree's form
    takes it, and refused (``onco.TYPE_OF_CANCER``) where it does not; ``validate_source``
    refuses nothing."""
    from aibi.core.schema.curation import ChangeRequest
    from aibi.core.store import sessions

    world.publish("demo", copy_demo(world), registry, pack="onco")
    pointer = "/extensions/onco/type_of_cancer"
    for value in ("BRCA", "mixed", "MDS/MPN"):
        edit: JsonValue = {"op": "set", "descriptor": "dataset", "pointer": pointer, "value": value}
        world.curate("demo", registry, edit)
        assert latest(world, "demo")["dataset"].extensions["onco"]["type_of_cancer"] == value
    for value in ("brca!", "a b", "X" * 33):
        opened = sessions.open_session(world.store, "demo", "operator:Ada")
        request = ChangeRequest.model_validate(
            {"edits": [{"op": "set", "descriptor": "dataset", "pointer": pointer, "value": value}]}
        )
        try:
            with pytest.raises(EditRefused) as raised:
                sessions.change(
                    world.store, "demo", opened.handle, opened.draft, request, "operator:Ada",
                    registry=registry,
                )  # fmt: skip
            refusals = raised.value.refusals
            codes = [str(r.code) for r in refusals]
            assert codes == ["onco.TYPE_OF_CANCER"], (value, raised.value)
            paths = [r.path for r in refusals]
            assert paths == ["/draft/dataset/extensions/onco/type_of_cancer"], paths
        finally:
            sessions.discard(world.store, "demo", opened.handle, opened.draft, "operator:Ada")
    assert onco.PACK.validator.validate_source(None, None) == []


def test_the_facet_serves_the_study_s_own_spelling(world: Any, registry: PackRegistry) -> None:
    published = world.publish("demo", copy_demo(world), registry, pack="onco")
    facets = importlib.import_module("aibi.packs.onco.facets")

    class View:
        dataset = "demo"
        manifest = published.manifest
        label = 1
        packs = ("onco",)
        descriptors = descriptors(world, published.manifest)

    assert facets.facet(View()) == {"type_of_cancer": ["mixed"], "reference_genome": ["hg19"]}


# --- Rows only, determinism -----------------------------------------------------------------------


def comparable(found: dict[str, Descriptor]) -> str:
    return json.dumps(
        {k: d.model_dump(mode="json") for k, d in sorted(found.items())}, sort_keys=True
    )


def test_two_studies_that_differ_only_in_rows_have_the_same_descriptor_text(
    make_world: Callable[[str], Any], registry: PackRegistry
) -> None:
    """§10.1, D271: rows that keep every category a category, add no spelling and close the
    relationship change no descriptor, curation included."""
    one, two = make_world("one"), make_world("two")
    first = copy_demo(one)
    second = copy_demo(two)
    patient = (second / "data_clinical_patient.txt").read_text()
    patient = patient.replace("P08\tMale\t39", "P08\tFemale\t44")
    patient = patient.replace("23.1\t0:DiseaseFree\t23.1", "29.9\t0:DiseaseFree\t29.9")
    (second / "data_clinical_patient.txt").write_text(patient)
    sample = (second / "data_clinical_sample.txt").read_text().replace("\t4.2\n", "\t7.7\n")
    (second / "data_clinical_sample.txt").write_text(sample)
    a = one.publish("demo", first, registry, pack="onco")
    b = two.publish("demo", second, registry, pack="onco")
    assert a.manifest != b.manifest
    assert comparable(descriptors(one, a.manifest)) == comparable(descriptors(two, b.manifest))


def test_the_same_study_imports_the_same_way_twice(
    make_world: Callable[[str], Any], registry: PackRegistry
) -> None:
    one, two = make_world("one"), make_world("two")
    a = one.publish("demo", copy_demo(one), registry, pack="onco")
    b = two.publish("demo", copy_demo(two), registry, pack="onco")
    assert a.manifest == b.manifest
    assert notes_said(a.notes) == notes_said(b.notes)


# --- The note budget ------------------------------------------------------------------------------

DESERET = [chr(0x10400 + i) for i in range(40)]
"""Deseret capitals: four UTF-8 bytes each, and upper case by ``str.isupper()``."""


def wide_name(index: int, length: int = 200) -> str:
    """A name of ``length`` Deseret capitals (four bytes each), distinct for each index below
    64,000."""
    head = DESERET[index // 1600] + DESERET[(index // 40) % 40] + DESERET[index % 40]
    return head + DESERET[0] * (length - 3)


def wide(names: list[str], keys: list[str]) -> bytes:
    n = len(keys) + len(names)
    lines = [
        "#" + "\t".join(["L"] * n),
        "#" + "\t".join(["D"] * n),
        "#" + "\t".join(["STRING"] * n),
    ]
    lines += ["#" + "\t".join(["1"] * n), "\t".join([*keys, *names])]
    lines += ["\t".join([*(f"{k[0]}1" for k in keys), *([" x"] * len(names))])]
    return ("\n".join(lines) + "\n").encode()


def widest_study(world: Any, columns: int, length: int) -> Path:
    """Both files at ``columns`` columns, every name renamed (``length`` Deseret capitals) and every
    column padded, and 201 entries discovery skips: notes over both of the core's bounds."""
    patient = [wide_name(i, length) for i in range(columns - 1)]
    sample = [wide_name(columns - 1 + i, length) for i in range(columns - 2)]
    files = dict(study_files([], ["P1\tF"]))
    files["data_clinical_patient.txt"] = wide(patient, ["PATIENT_ID"])
    files["data_clinical_sample.txt"] = wide(sample, ["PATIENT_ID", "SAMPLE_ID"])
    for i in range(201):
        files[f"extra_{i:03d}.txt"] = b"x"
    return world.write("wide", files)


@pytest.fixture
def widest(world: Any) -> Path:
    """The study the suite imports through the core's checks: 2,600 columns in each file, names of
    210 capitals (over 10,000 notes, and over the byte bound in the first 9,999);
    ``test_the_widest_study_publishes`` builds the full 4,096 columns of 200."""
    return widest_study(world, 2_600, 210)


def test_the_note_budget_holds_a_legal_study_s_notes(
    onco: ModuleType, widest: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The study's notes, unbudgeted, are over both of the core's bounds (so a budget of notes
    alone, or none, would fail the import as the pack's), and the importer's, taken through the
    core's checks as they stand (``run_importer``), are within them with the one note that counts
    the rest. The import is not built and published here (``test_the_widest_study_publishes``,
    by hand, does): the checks are what refuse a result past either bound."""
    from aibi.core.importers.checks import run_importer

    notes = importlib.import_module("aibi.packs.onco.notes")
    importer = importlib.import_module("aibi.packs.onco.importer")
    every: list[Any] = []

    class Recording(notes.Budget):  # type: ignore[misc, name-defined]
        """The real budget, which also records every note it is given."""

        def add(self, more: Any) -> None:
            more = list(more)
            every.extend(more)
            super().add(more)

    confinement = Confinement.of(widest.parent)
    options = ImportOptions(
        dataset="wide", reader=confinement, limits=ImportLimits(), at="2026-01-01T00:00:00Z"
    )
    monkeypatch.setattr(importer, "Budget", Recording)
    result = run_importer(onco.PACK.importer, "onco", confinement.confine(widest), options)
    sizes = [notes.size(note) for note in every]
    first = sizes[: MAX_QUEUE_ITEMS - 1]
    assert sum(first) > MAX_QUEUE_BYTES  # a budget of notes alone would fail on bytes
    assert len(every) > MAX_QUEUE_ITEMS
    kept = [n for n in result.notes if n.kind in ("renamed", "not_proposed", "skipped_source")]
    (last,) = [n for n in kept if n.subject is None and n.count is not None and any(
        isinstance(s, TextSegment) and s.text == notes.LEFT_OUT for s in n.message)]  # fmt: skip
    assert last.count is not None
    assert last.count > 0
    assert len(kept) <= MAX_QUEUE_ITEMS
    assert sum(notes.size(n) for n in kept) <= MAX_QUEUE_BYTES


@pytest.mark.measurement
def test_the_widest_study_publishes(world: Any, registry: PackRegistry) -> None:
    """By hand (the ``measurement`` marker): a study of 4,096 columns in both files, names of 200
    capitals, built and published whole (about 40 s), the widest the core takes."""
    widest = widest_study(world, 4_096, 200)
    published = world.publish("wide", widest, registry, pack="onco")
    notes = importlib.import_module("aibi.packs.onco.notes")
    kept = [n for n in published.notes if n.kind in ("renamed", "not_proposed", "skipped_source")]
    assert len(kept) <= MAX_QUEUE_ITEMS
    assert sum(notes.size(n) for n in kept) <= MAX_QUEUE_BYTES


def test_the_budget_counts_bytes_and_notes(onco: ModuleType) -> None:
    notes = importlib.import_module("aibi.packs.onco.notes")
    from aibi.core.schema.output import text
    from aibi.core.schema.pack_api import ImportNote

    note = ImportNote("not_proposed", "t.c", [text("x" * 97)])
    assert notes.size(note) == 100
    budget = notes.Budget(items=4, bytes_=10_000)
    budget.add([note] * 3)
    assert len(budget.notes) == 3
    budget.add([note])
    assert [n.count for n in budget.notes] == [None, None, None, 1]
    size = notes.size(notes.left_out(0))
    exact = notes.Budget(items=100, bytes_=300 + size)
    exact.add([note] * 4)
    assert [n.count for n in exact.notes] == [None, None, None, 1]


def test_no_note_is_kept_once_one_is_left_out(onco: ModuleType) -> None:
    """Past the first note that does not fit, none is listed, whatever its size, and all are
    counted in the one note that counts them (not a later note that is small enough)."""
    notes = importlib.import_module("aibi.packs.onco.notes")
    from aibi.core.schema.output import text
    from aibi.core.schema.pack_api import ImportNote

    small = ImportNote("not_proposed", "t.c", [text("x" * 97)])
    big = ImportNote("not_proposed", "t.c", [text("x" * 297)])
    budget = notes.Budget(items=100, bytes_=notes.size(notes.left_out(0)) + 400)
    budget.add([small, small])
    budget.add([big])  # 200 + 300 > 400: left out
    budget.add([small])  # 200 + 100 would fit: left out all the same
    assert [n.count for n in budget.notes] == [None, None, 2]
    items = notes.Budget(items=4, bytes_=10_000)
    items.add([small] * 3)
    items.add([small, small])
    assert [n.count for n in items.notes] == [None, None, None, 2]


# --- The sentinel, through the import -------------------------------------------------------------

SENTINEL = "zQ7sEnTiNeL"


def test_no_note_refusal_or_log_shows_a_cell(
    world: Any, registry: PackRegistry, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    directory = copy_demo(world)
    patient = (directory / "data_clinical_patient.txt").read_text()
    (directory / "data_clinical_patient.txt").write_text(
        patient.replace("Female \t", f" {SENTINEL} \t").replace("\t70\t", f"\t{SENTINEL}\t")
    )
    published = world.publish("demo", directory, registry, pack="onco")
    assert SENTINEL.lower() not in "\n".join(notes_said(published.notes)).lower()
    bad = copy_demo(world, "bad")
    patient = (bad / "data_clinical_patient.txt").read_text()
    (bad / "data_clinical_patient.txt").write_text(patient.replace("TRUE\n", f"{SENTINEL}\n", 1))
    with pytest.raises(ImportRefused) as raised:
        world.publish("bad", bad, registry, pack="onco")
    assert refusal_of(raised)[0] == "onco.BOOLEAN_VALUE"
    assert SENTINEL.lower() not in refusal_of(raised)[1].lower()
    assert SENTINEL.lower() not in caplog.text.lower()
    chain: BaseException | None = raised.value
    while chain is not None:
        assert SENTINEL.lower() not in str(chain).lower()
        chain = chain.__cause__ or chain.__context__


def test_a_header_name_is_shown_only_as_data(world: Any, registry: PackRegistry) -> None:
    directory = copy_demo(world)
    patient = (directory / "data_clinical_patient.txt").read_text()
    name = SENTINEL.upper() + " X"
    (directory / "data_clinical_patient.txt").write_text(
        patient.replace("\tSMOKER\n", f"\t{name}\n")
    )
    published = world.publish("demo", directory, registry, pack="onco")
    for note in published.notes:
        for segment in note.message:
            if isinstance(segment, TextSegment):
                assert SENTINEL.lower() not in segment.text.lower()
    renamed = [n for n in published.notes if n.kind == "renamed"]
    assert [[s.data for s in n.message if isinstance(s, DataSegment)] for n in renamed] == [[name]]
    assert renamed[0].subject is not None
    assert SENTINEL.lower() in renamed[0].subject.lower()  # the id is made from the name


# --- Totality through the core's checks ----------------------------------------------------------

NAME_PARTS = st.sampled_from(
    ["PATIENT_ID", "SAMPLE_ID", "SEX", "OS_STATUS", "OS_MONTHS", "AGE", "X"]
)
FIELDS = st.lists(
    st.sampled_from([*"aZ09 -:#|[]_.", "NA", "TRUE", "1:DECEASED", "\xa0", "\u00e9"]), max_size=6
).map("".join)


LABELS = st.text(alphabet="abcXYZ ()", min_size=1, max_size=5).filter(lambda t: t.strip() != "")
KEYS = st.sampled_from(["P1", "P2", "P3", "S1", "NA", "P 1", "P1 "])


def clinical_bytes(draw: st.DrawFn, keys: list[str]) -> bytes:
    """A clinical file mostly well formed (so that the core takes most of them), with faults
    drawn now and then: a cell of any shape, a type a column does not hold, a stray byte."""
    extra = draw(st.lists(NAME_PARTS, max_size=3, unique=True))
    names = [*keys, *(name for name in extra if name not in keys)]
    n = len(names)
    types = ["STRING"] * len(keys) + draw(
        st.lists(
            st.sampled_from(["STRING", "NUMBER", "BOOLEAN"]),
            min_size=n - len(keys),
            max_size=n - len(keys),
        )
    )
    rows = draw(
        st.lists(
            st.tuples(
                st.lists(KEYS, min_size=len(keys), max_size=len(keys)),
                st.lists(FIELDS, min_size=n - len(keys), max_size=n - len(keys)),
            ),
            max_size=4,
        )
    )
    lines = ["#" + "\t".join(draw(st.lists(LABELS, min_size=n, max_size=n))) for _ in range(2)]
    lines += ["#" + "\t".join(types), "#" + "\t".join(["1"] * n), "\t".join(names)]
    lines += ["\t".join([*k, *f]) for k, f in rows]
    end = draw(st.sampled_from(["\n"] * 6 + ["", "\r\n", "\n\n"]))
    tail = draw(st.sampled_from([b""] * 8 + [b"\xff", b"\x01", b"\xef\xbb\xbf"]))
    return ("\n".join(lines) + end).encode() + tail


@st.composite
def studies(draw: st.DrawFn) -> tuple[bytes, bytes]:
    return clinical_bytes(draw, ["PATIENT_ID"]), clinical_bytes(draw, ["PATIENT_ID", "SAMPLE_ID"])


@settings(
    max_examples=150,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
)
@given(files=studies())
def test_the_importer_is_total_through_the_core_s_checks(
    tmp_path_factory: pytest.TempPathFactory, files: tuple[bytes, bytes]
) -> None:
    """Any clinical files: the pack's result is taken by the core's checks, or it refuses with one
    of its own codes; never the pack's failure (``PACK_FAILED``)."""
    from aibi.core.importers.checks import run_importer

    onco = importlib.import_module("aibi.packs.onco")
    directory = tmp_path_factory.mktemp("total") / "imports" / "study"
    directory.mkdir(parents=True)
    for name in ("meta_study.txt", "meta_clinical_sample.txt", "meta_clinical_patient.txt"):
        shutil.copy(DEMO / name, directory / name)
    (directory / "data_clinical_patient.txt").write_bytes(files[0])
    (directory / "data_clinical_sample.txt").write_bytes(files[1])
    confinement = Confinement.of(directory.parent)
    options = ImportOptions(
        dataset="t", reader=confinement, limits=ImportLimits(), at="2026-01-01T00:00:00Z"
    )
    try:
        run_importer(onco.PACK.importer, "onco", confinement.confine(directory), options)
    except ImportRefused as error:
        codes = [str(r.code) for r in error.refusals]
        assert all(code.startswith("onco.") for code in codes), codes


def test_a_study_without_a_name_is_titled_by_its_identifier_as_a_default(
    world: Any, registry: PackRegistry
) -> None:
    """An empty ``name:`` in the study meta, and no name given by the operator: the dataset's
    label is the study's ``cancer_study_identifier``, its status ``imported_default`` (a name
    the study gives, or the operator, is ``imported``)."""
    directory = copy_demo(world)
    meta = (directory / "meta_study.txt").read_text()
    assert "name: Demo oncology study (synthetic)\n" in meta
    (directory / "meta_study.txt").write_text(
        meta.replace("name: Demo oncology study (synthetic)\n", "name:\n")
    )
    found = descriptors(world, world.publish("demo", directory, registry, pack="onco").manifest)
    dataset = found["dataset"]
    assert isinstance(dataset, DatasetDescriptor)
    assert dataset.label == "demo_onco_2026"
    assert dataset.curation["/label"].status == "imported_default"
    named = descriptors(
        world, world.publish("demo2", copy_demo(world, "demo2"), registry, pack="onco").manifest
    )
    assert named["dataset"].curation["/label"].status == "imported"

"""biai's examples, imported by the file importer (SPEC §13.1): what it proposes for them, and
that the release it builds is published, loaded and answers a question.

The core is domain-neutral (SPEC P8): this data is biomedical, like the fixture of
``tests/core/engine/test_biomedical.py``, and is only data here. The files are copied byte for
byte from biai (``fixtures/biai``): patients with treatments, as two CSV files, and a workbook of
patients, samples and treatments, with two more patient sheets and an empty one.
"""

import json
from pathlib import Path
from typing import Any

from aibi.core.engine.evaluate import evaluate
from aibi.core.engine.resolve import resolve
from aibi.core.schema.descriptors import ColumnDescriptor, Descriptor, TableDescriptor
from aibi.core.schema.loading import load_document
from aibi.core.store.store import Store

Roots = Any
Importing = Any
FIXTURES = Path(__file__).resolve().parents[4] / "fixtures" / "biai"


def _by_id(descriptors: tuple[Descriptor, ...]) -> dict[str, Descriptor]:
    return {descriptor.id: descriptor for descriptor in descriptors}


def _column(found: dict[str, Descriptor], column: str) -> tuple[str | None, str]:
    descriptor = found[column]
    assert isinstance(descriptor, ColumnDescriptor)
    return descriptor.fields.datatype, descriptor.curation["/fields/datatype"].status


def test_two_csv_files_give_patients_and_their_treatments(
    roots: Roots, importing: Importing
) -> None:
    for name in ("patients.csv", "treatments.csv"):
        roots.write(f"visits/{name}", (FIXTURES / name).read_bytes())
    imported = importing(roots.inside / "visits")
    found = _by_id(imported.built.descriptors)
    tables = {id: d for id, d in found.items() if isinstance(d, TableDescriptor)}
    assert {id: (t.fields.primary_key, t.fields.role) for id, t in tables.items()} == {
        "patients": (["patient_id"], "entity"),
        "treatments": (["treatment_id"], "measurement"),
    }
    relationship = found["rel:treatments.patient_id"]
    assert relationship.fields.model_dump(mode="json") == {
        "child_table": "treatments",
        "child_columns": ["patient_id"],
        "parent_table": "patients",
        "parent_columns": ["patient_id"],
        "cardinality": "many-to-one",
    }
    assert {entry.status for entry in relationship.curation.values()} == {
        "proposed",
        "imported_default",
    }
    assert not [id for id in found if id.startswith("cov:")]
    assert _column(found, "patients.gender") == ("category", "proposed")
    assert _column(found, "patients.age") == ("integer", "proposed")


def _count(store: Store, manifest: str, clauses: list[Any]) -> Any:
    written = {
        "aibi": "1",
        "dataset": "trial",
        "unit": "patients",
        "cohorts": {"c": {"all": clauses}},
    }
    loaded = load_document(json.dumps(written))
    assert loaded.document is not None
    resolution = resolve(loaded.document, {"trial": store.load(manifest)}, loaded.positions)
    assert resolution.refusals == []
    return evaluate(resolution.cohorts["c"])


def test_the_workbook_gives_a_table_per_sheet_and_answers_a_question(
    roots: Roots, importing: Importing, store: Store
) -> None:
    path = roots.write("trial.xlsx", (FIXTURES / "clinical_trial_data.xlsx").read_bytes())
    imported = importing(path, dataset="trial")
    found = _by_id(imported.built.descriptors)
    tables = [id for id, d in found.items() if isinstance(d, TableDescriptor)]
    assert tables == ["patients", "samples", "treatments", "patients_append", "patients_upsert"]
    skipped = [n for n in imported.notes if n.kind == "skipped_source"]
    assert [segment.model_dump() for segment in skipped[0].message][1] == {"data": "Empty_Sheet"}
    relationships = sorted(id for id in found if id.startswith("rel:"))
    assert relationships == ["rel:samples.patient_id", "rel:treatments.patient_id"]
    assert {found[id].fields.model_dump()["parent_table"] for id in relationships} == {"patients"}
    assert _column(found, "patients.diagnosis_date") == ("date", "proposed")
    assert _column(found, "samples.purity") == ("number", "proposed")
    assert _column(found, "patients.age") == ("integer", "proposed")
    assert imported.built.gate is not None
    assert (imported.built.gate.refusals, imported.built.gate.dropped) == ((), ())
    assert [label.label for label in store.labels("trial")] == [1]
    women = {"kind": "value", "column": "patients.gender", "values": ["Female"]}
    pure = {
        "kind": "exists",
        "table": "samples",
        "where": [{"kind": "value", "column": "samples.purity", "range": {"gt": 0.8}}],
    }
    assert _count(store, imported.built.manifest.hash, [women]).n_true == 3
    assert _count(store, imported.built.manifest.hash, [pure]).n_true == 2


def test_the_files_together_propose_no_join_between_two_tables_keys(
    roots: Roots, importing: Importing
) -> None:
    """The workbook's treatments sheet and the treatments file share treatment ids by chance of
    numbering: a key contained in another table's key is never proposed as a foreign key."""
    for name in ("clinical_trial_data.xlsx", "patients.csv", "treatments.csv"):
        roots.write(f"all/{name}", (FIXTURES / name).read_bytes())
    imported = importing(roots.inside / "all", dataset="trial")
    found = _by_id(imported.built.descriptors)
    assert [id for id in found if id.startswith("rel:")] == []
    noted = [
        note.subject
        for note in imported.notes
        if note.kind == "not_proposed"
        and "two tables' keys" in "".join(s.model_dump().get("text", "") for s in note.message)
    ]
    assert noted == [
        "clinical_trial_data_patients.patient_id",
        "clinical_trial_data_treatments.treatment_id",
        "patients.patient_id",
    ]


def test_the_fixtures_are_biais_bytes() -> None:
    assert sorted(path.name for path in FIXTURES.iterdir()) == [
        "README.md",
        "clinical_trial_data.xlsx",
        "patients.csv",
        "treatments.csv",
    ]

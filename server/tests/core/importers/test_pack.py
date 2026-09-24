"""A pack's importer and validator (SPEC §10.1, D235), with the test-only ``birds`` pack: its
import goes through the same pipeline and gate as the core's, and its refusals stop it."""

import json
import sys
from typing import Any

import pytest

from aibi.core.importers.errors import ImportRefused
from aibi.core.schema.pack_api import ConfinedPath

Roots = Any
Importing = Any
Birds = Any


class Recording:
    """A reader that records what it reads, around the import's own."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.read_paths: list[str] = []

    def read(self, path: ConfinedPath, limit: int) -> bytes:
        self.read_paths.append(str(path))
        return self.inner.read(path, limit)

    def files(self, directory: ConfinedPath) -> Any:
        return self.inner.files(directory)

    def location(self, path: ConfinedPath) -> str:
        return self.inner.location(path)


def _survey(roots: Roots, birds: Birds, **changes: Any) -> Any:
    return roots.write("survey.json", json.dumps(birds.survey(**changes)).encode())


def test_a_pack_imports_through_its_importer_and_the_reader(
    roots: Roots, importing: Importing, birds: Birds, store: Any
) -> None:
    path = _survey(roots, birds)
    reader = Recording(roots.confinement)
    imported = importing(path, registry=birds.registry, pack="birds", reader=reader)
    assert reader.read_paths == [str(path.resolve())]
    assert birds.importer.reads == [str(path.resolve())]
    descriptors = {d.id: d for d in imported.built.descriptors}
    assert descriptors["dataset"].extensions == {"birds": {"protocol": "stationary"}}
    assert descriptors["counts"].fields.primary_key == ["checklist_id", "species"]
    assert {entry.status for d in descriptors.values() for entry in d.curation.values()} == {
        "imported"
    }
    assert imported.built.gate is not None
    assert imported.built.gate.dropped == ()
    assert [(g.subject, g.count, g.rows) for g in imported.built.gate.gaps] == [
        ("cov:counts.checklist_id", 1, (3,))
    ]
    release = store.load(imported.built.manifest.hash)
    assert release.parent("rel:counts.checklist_id", 2) == 1


def test_a_source_the_pack_refuses_stops_the_import(
    roots: Roots, importing: Importing, birds: Birds, store: Any
) -> None:
    counts = [{"checklist_id": "c1", "species": "wren", "count": -2}]
    path = _survey(roots, birds, counts=counts)
    with pytest.raises(ImportRefused) as refused:
        importing(path, registry=birds.registry, pack="birds")
    assert [r.code for r in refused.value.refusals] == ["birds.NEGATIVE_COUNT"]
    assert store.labels("d") == []


def test_descriptors_the_pack_refuses_stop_the_import(
    roots: Roots, importing: Importing, birds: Birds, store: Any
) -> None:
    survey = birds.survey()
    del survey["protocol"]
    path = roots.write("survey.json", json.dumps(survey).encode())
    with pytest.raises(ImportRefused) as refused:
        importing(path, registry=birds.registry, pack="birds")
    [refusal] = refused.value.refusals
    assert refusal.code == "birds.NO_PROTOCOL"
    assert refusal.message[0].model_dump() == {"text": "Release @1 has no survey protocol"}
    assert store.labels("d") == []


def test_the_importing_pack_validates_even_when_the_dataset_does_not_list_it(
    roots: Roots, importing: Importing, birds: Birds, store: Any
) -> None:
    counts = [{"checklist_id": "c1", "species": "wren", "count": -2}]
    survey = birds.survey(counts=counts, packs=[])
    del survey["protocol"]
    path = roots.write("survey.json", json.dumps(survey).encode())
    with pytest.raises(ImportRefused) as refused:
        importing(path, registry=birds.registry, pack="birds")
    assert [r.code for r in refused.value.refusals] == ["birds.NEGATIVE_COUNT"]


def test_the_release_is_validated_as_the_label_it_would_be_published_as(
    roots: Roots, importing: Importing, birds: Birds
) -> None:
    for _ in range(2):
        importing(_survey(roots, birds), registry=birds.registry, pack="birds")
    survey = birds.survey()
    del survey["protocol"]
    path = roots.write("survey.json", json.dumps(survey).encode())
    with pytest.raises(ImportRefused) as refused:
        importing(path, registry=birds.registry, pack="birds")
    [refusal] = refused.value.refusals
    assert refusal.message[0].model_dump() == {"text": "Release @3 has no survey protocol"}


def test_a_declared_dangling_key_stops_the_import_instead_of_being_dropped(
    roots: Roots, importing: Importing, birds: Birds
) -> None:
    counts = [{"checklist_id": "c9", "species": "wren", "count": 1}]
    path = _survey(roots, birds, counts=counts)
    with pytest.raises(ImportRefused) as refused:
        importing(path, registry=birds.registry, pack="birds")
    assert [(r.code, r.path) for r in refused.value.refusals] == [
        ("DANGLING_REFERENCE", "/21/fields/child_columns")
    ]


def test_an_unknown_pack_is_refused(roots: Roots, importing: Importing, birds: Birds) -> None:
    path = _survey(roots, birds)
    with pytest.raises(ImportRefused) as refused:
        importing(path, registry=birds.registry, pack="bees")
    assert [r.code for r in refused.value.refusals] == ["INVALID_VALUE"]


def test_the_core_suite_loads_no_pack() -> None:
    assert not [m for m in sys.modules if m == "aibi.packs" or m.startswith("aibi.packs.")]

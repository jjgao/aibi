"""Re-import (SPEC §5.1, §12.3, §13.2, D238–D243): curation is carried forward by comparing the
importer's inferences, removals stand until the evidence changes, and an unchanged re-import
publishes nothing. The lending library of ``fixtures/library`` is re-imported as its next
export, ``fixtures/library_next``."""

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

import aibi
from aibi.core.importers.errors import ImportRefused
from aibi.core.schema.curation import ChangeRequest
from aibi.core.schema.pack_api import PackRegistry
from aibi.core.store import sessions
from aibi.core.store.carry import Change
from aibi.core.store.proposals import curation_queue
from aibi.core.store.store import Store

Lifecycle = Any
Variant = Any
Roots = Any
Birds = Any
ADA = "operator:ada"
NEXT = Path(__file__).resolve().parents[4] / "fixtures" / "library_next"


def curate(store: Store, *edits: dict[str, Any], dataset: str = "d") -> str:
    """Publish the edits through a curation session; the new release's manifest."""
    opened = sessions.open_session(store, dataset, ADA)
    request = ChangeRequest.model_validate({"edits": list(edits)})
    draft = sessions.change(store, dataset, opened.handle, opened.draft, request, ADA)
    sessions.publish(store, dataset, opened.handle, draft, ADA)
    return draft


def descriptors(store: Store, manifest: str) -> dict[str, Any]:
    return {descriptor.id: descriptor for descriptor in store.descriptors(manifest)}


def codes(error: pytest.ExceptionInfo[ImportRefused]) -> list[tuple[str, str | None]]:
    return [(str(refusal.code), refusal.path) for refusal in error.value.refusals]


def test_curation_is_carried_forward_with_its_entries_byte_identical(
    lifecycle: Lifecycle, library_variant: Variant, store: Store
) -> None:
    lifecycle.import_(library_variant())
    curated = curate(
        store,
        {"op": "confirm", "descriptor": "shelvings", "pointers": ["/fields/primary_key"]},
        {"op": "confirm", "descriptor": "rel:copies.book_id"},
        {"op": "set", "descriptor": "members", "pointer": "/label", "value": "Members"},
    )
    before = descriptors(store, curated)
    after = descriptors(store, lifecycle.reimport(library_variant(source="library_next")).manifest)
    for identifier in ("shelvings", "rel:copies.book_id", "members"):
        assert after[identifier].model_dump() == before[identifier].model_dump(), identifier
    assert after["members"].curation["/label"].by == ADA


def test_a_changed_field_is_listed_in_the_queue(
    lifecycle: Lifecycle, library_variant: Variant, store: Store
) -> None:
    lifecycle.import_(library_variant())
    published = lifecycle.reimport(library_variant(source="library_next"))
    age = descriptors(store, published.manifest)["members.age"]
    assert (age.fields.datatype, age.curation["/fields/datatype"].status) == ("number", "proposed")
    queue = curation_queue(store, "d")
    assert ("members.age", "/fields/datatype", "proposed") in [
        (item.descriptor, item.pointer, item.status) for item in queue.fields
    ]
    reimported = {note.subject for note in queue.notes if note.kind == "reimported"}
    assert {"members.age/fields/datatype", "members.branch", "reviews"} <= reimported


def test_a_removed_proposal_stays_removed_while_its_inference_is_the_same(
    lifecycle: Lifecycle, library_variant: Variant, store: Store
) -> None:
    lifecycle.import_(library_variant())
    curate(store, {"op": "remove", "descriptor": "members.age", "pointer": "/fields/datatype"})
    reviews = (NEXT / "reviews.csv").read_bytes()
    kept = lifecycle.reimport(library_variant(files={"reviews.csv": reviews}))
    assert descriptors(store, kept.manifest)["members.age"].fields.datatype is None
    assert [t.key for t in store.tombstones(kept.manifest)] == [("members.age", "/fields/datatype")]
    back = lifecycle.reimport(library_variant(source="library_next"))
    age = descriptors(store, back.manifest)["members.age"]
    assert age.fields.datatype == "number"
    assert store.tombstones(back.manifest) == ()
    assert ("members.age", "/fields/datatype", "returned") in [
        (c.descriptor, c.pointer, c.happened) for c in back.changes
    ]


def test_a_removed_relationship_and_its_coverage_stay_removed(
    lifecycle: Lifecycle, library_variant: Variant, store: Store
) -> None:
    lifecycle.import_(library_variant())
    curate(
        store,
        {"op": "remove_descriptor", "descriptor": "cov:copies.book_id"},
        {"op": "remove_descriptor", "descriptor": "rel:copies.book_id"},
    )
    published = lifecycle.reimport(library_variant(source="library_next"))
    found = descriptors(store, published.manifest)
    assert "rel:copies.book_id" not in found
    assert "cov:copies.book_id" not in found
    assert [t.key for t in store.tombstones(published.manifest)] == [
        ("cov:copies.book_id", ""),
        ("rel:copies.book_id", ""),
    ]


def test_an_unchanged_re_import_is_refused(
    lifecycle: Lifecycle, library_variant: Variant, store: Store
) -> None:
    lifecycle.import_(library_variant())
    curate(store, {"op": "confirm", "descriptor": "members"})
    with pytest.raises(ImportRefused) as refused:
        lifecycle.reimport(library_variant())
    assert codes(refused) == [("NO_CHANGE", None)]
    assert [label.label for label in store.labels("d")] == [1, 2]


def test_a_re_import_is_refused_while_a_session_is_open(
    lifecycle: Lifecycle, library_variant: Variant, store: Store
) -> None:
    lifecycle.import_(library_variant())
    sessions.open_session(store, "d", ADA)
    with pytest.raises(ImportRefused) as refused:
        lifecycle.reimport(library_variant(source="library_next"))
    assert codes(refused) == [("DATASET_BUSY", None)]


def test_a_re_import_needs_a_published_release(
    lifecycle: Lifecycle, library_variant: Variant
) -> None:
    with pytest.raises(ImportRefused) as refused:
        lifecycle.reimport(library_variant())
    assert codes(refused) == [("UNKNOWN_RELEASE", None)]


def test_a_carried_asserted_key_that_the_new_data_break_refuses_the_re_import(
    lifecycle: Lifecycle, roots: Roots, store: Store
) -> None:
    path = roots.write("people.csv", b"id,name\np1,Ada\np2,Grace\n")
    lifecycle.import_(path)
    curate(
        store,
        {"op": "set", "descriptor": "people", "pointer": "/fields/primary_key", "value": ["name"]},
    )
    path.write_bytes(b"id,name\np1,Ada\np2,Grace\np3,Ada\n")
    with pytest.raises(ImportRefused) as refused:
        lifecycle.reimport(path)
    assert codes(refused) == [("KEY_NOT_UNIQUE", "/descriptors/people/fields/primary_key")]


def test_a_carried_proposed_key_that_the_new_data_break_is_dropped_and_noted(
    lifecycle: Lifecycle, roots: Roots, birds: Birds, store: Store
) -> None:
    def survey(**changes: Any) -> Path:
        written = birds.survey(keys_proposed=True, **changes)
        return roots.write("survey.json", json.dumps(written).encode())

    lifecycle.import_(survey(), registry=birds.registry, pack="birds")
    counts = [
        {"checklist_id": "c1", "species": "wren", "count": 3},
        {"checklist_id": "c1", "species": "wren", "count": 4},
    ]
    published = lifecycle.reimport(survey(counts=counts), registry=birds.registry, pack="birds")
    assert descriptors(store, published.manifest)["counts"].fields.primary_key is None
    assert ("dropped", "counts") in [(note.kind, note.subject) for note in published.notes]
    declared = roots.write("survey.json", json.dumps(birds.survey(counts=counts)).encode())
    with pytest.raises(ImportRefused) as refused:
        lifecycle.reimport(declared, registry=birds.registry, pack="birds")
    assert ("KEY_NOT_UNIQUE", "/descriptors/counts/fields/primary_key") in codes(refused)


def test_a_table_that_is_gone_takes_what_names_it_with_it(
    lifecycle: Lifecycle, library_variant: Variant, store: Store
) -> None:
    lifecycle.import_(library_variant())
    endpoint = {
        "kind": "endpoint",
        "id": "ep:copied",
        "label": "Copied",
        "fields": {"table": "copies"},
    }
    curate(store, {"op": "put", "descriptor": endpoint})
    published = lifecycle.reimport(library_variant(without=["copies.csv"]))
    found = descriptors(store, published.manifest)
    assert not [identifier for identifier in found if "copies" in identifier]
    assert "ep:copied" not in found
    happened = {(c.descriptor, c.happened) for c in published.changes}
    assert {
        ("copies", "gone"),
        ("copies.copy_id", "gone"),
        ("rel:copies.book_id", "removed"),
        ("cov:copies.book_id", "removed"),
        ("rel:loans_loans.copy_id", "removed"),
        ("ep:copied", "gone"),
    } <= happened


def test_the_dataset_descriptor_is_carried_by_inference_like_every_other(
    lifecycle: Lifecycle, library_variant: Variant, store: Store
) -> None:
    lifecycle.import_(library_variant())
    curated = curate(
        store, {"op": "set", "descriptor": "dataset", "pointer": "/label", "value": "The library"}
    )
    before = descriptors(store, curated)["dataset"]
    published = lifecycle.reimport(library_variant(source="library_next"))
    assert descriptors(store, published.manifest)["dataset"] == before
    renamed = lifecycle.reimport(library_variant(source="library"), name="Renamed")
    after = descriptors(store, renamed.manifest)["dataset"]
    assert after.label == "Renamed"
    assert after.curation["/label"].by.startswith("importer:")
    assert Change("dataset", "/label", "proposed") in renamed.changes
    assert after.fields.packs == before.fields.packs


def test_previous_ids_survive_a_moved_column_a_duplicate_header_and_an_empty_header(
    lifecycle: Lifecycle, roots: Roots, store: Store
) -> None:
    directory = roots.inside / "tallies"
    directory.mkdir()
    (directory / "T.csv").write_bytes(b"id,x\n1,a\n2,b\n")
    (directory / "t.csv").write_bytes(b"id,x,x,,y\n1,a,b,c,d\n2,e,f,g,h\n")
    first = lifecycle.import_(directory)
    table: Any = store.manifest(first.manifest).table("t_2")
    assert [(c.id, c.name) for c in table.columns] == [
        ("id", "id"),
        ("x", "x"),
        ("x_2", "x"),
        ("c_4", ""),
        ("y", "y"),
    ]
    curate(store, {"op": "set", "descriptor": "t_2.c_4", "pointer": "/label", "value": "Blank"})
    (directory / "T.csv").unlink()
    (directory / "t.csv").write_bytes(b"x,,id,x,y,z\na,c,1,b,d,z\ne,g,2,f,h,z\n")
    published = lifecycle.reimport(directory)
    found = store.manifest(published.manifest)
    assert [entry.id for entry in found.tables] == ["t_2"]
    assert [(c.id, c.name) for c in found.tables[0].columns] == [
        ("x", "x"),
        ("c_4", ""),
        ("id", "id"),
        ("x_2", "x"),
        ("y", "y"),
        ("z", "z"),
    ]
    assert descriptors(store, published.manifest)["t_2.c_4"].label == "Blank"


def test_versions_bump_only_on_the_descriptors_that_changed(
    lifecycle: Lifecycle, library_variant: Variant, store: Store
) -> None:
    lifecycle.import_(library_variant())
    published = lifecycle.reimport(library_variant(source="library_next"))
    found = descriptors(store, published.manifest)
    versions = {i: found[i].version for i in ("members.age", "members", "books", "reviews")}
    assert versions == {"members.age": 2, "members": 1, "books": 1, "reviews": 1}


def test_a_re_import_of_a_withdrawn_release_s_bytes_is_refused(
    lifecycle: Lifecycle, library_variant: Variant, store: Store
) -> None:
    lifecycle.import_(library_variant())
    lifecycle.reimport(library_variant(source="library_next"))
    store.withdraw("d", 2, ADA)
    with pytest.raises(ImportRefused) as refused:
        lifecycle.reimport(library_variant(source="library_next"))
    assert codes(refused) == [("RELEASE_WITHDRAWN", None)]


# --- Derived columns, re-created descriptors, new proposals, the dataset -----------------------

AGE2 = {
    "kind": "column",
    "id": "members.age2",
    "label": "Age x2",
    "fields": {
        "datatype": "number",
        "derived": {"op": "arith", "operator": "*", "args": ["age", 2]},
    },
}


def test_a_derived_column_is_curation_and_survives_a_re_import(
    lifecycle: Lifecycle, library_variant: Variant, store: Store
) -> None:
    lifecycle.import_(library_variant())
    curated = curate(store, {"op": "put", "descriptor": AGE2})
    with pytest.raises(ImportRefused) as refused:
        lifecycle.reimport(library_variant())
    assert codes(refused) == [("NO_CHANGE", None)]
    published = lifecycle.reimport(library_variant(source="library_next"))
    found = descriptors(store, published.manifest)
    assert found["members.age2"] == descriptors(store, curated)["members.age2"]
    assert "members.age2" not in {change.descriptor for change in published.changes}


def test_a_derived_column_whose_input_is_gone_goes_with_it(
    lifecycle: Lifecycle, library_variant: Variant, store: Store
) -> None:
    lifecycle.import_(library_variant())
    curate(store, {"op": "put", "descriptor": AGE2})
    members = (NEXT.parent / "library" / "members.csv").read_text().splitlines()
    header = members[1].split(",")
    at = header.index("age")
    rows = [",".join(v for i, v in enumerate(line.split(",")) if i != at) for line in members[1:]]
    without = "\n".join([members[0], *rows]) + "\n"
    published = lifecycle.reimport(library_variant(files={"members.csv": without.encode()}))
    assert "members.age2" not in descriptors(store, published.manifest)
    assert Change("members.age2", "", "gone") in published.changes


def test_removing_then_putting_a_descriptor_keeps_the_removal_of_a_field_it_leaves_out(
    lifecycle: Lifecycle, library_variant: Variant, store: Store
) -> None:
    lifecycle.import_(library_variant())
    coverage = {
        "kind": "coverage",
        "id": "cov:copies.book_id",
        "label": "Coverage of rel:copies.book_id",
        "fields": {"relationship": "rel:copies.book_id"},
    }
    curated = curate(
        store,
        {"op": "remove_descriptor", "descriptor": "cov:copies.book_id"},
        {"op": "put", "descriptor": coverage},
    )
    assert [(t.key, t.inferred) for t in store.tombstones(curated)] == [
        (("cov:copies.book_id", "/fields/parents"), "all")
    ]
    with pytest.raises(ImportRefused) as refused:
        lifecycle.reimport(library_variant())
    assert codes(refused) == [("NO_CHANGE", None)]


def test_a_new_proposal_that_conflicts_with_curation_is_dropped_not_refused(
    lifecycle: Lifecycle, library_variant: Variant, store: Store
) -> None:
    lifecycle.import_(library_variant())
    key = ["book_id", "title"]
    curate(
        store,
        {"op": "set", "descriptor": "books", "pointer": "/fields/primary_key", "value": key},
        {"op": "remove_descriptor", "descriptor": "cov:copies.book_id"},
        {"op": "remove_descriptor", "descriptor": "rel:copies.book_id"},
        {"op": "remove_descriptor", "descriptor": "cov:shelvings.book_id"},
        {"op": "remove_descriptor", "descriptor": "rel:shelvings.book_id"},
    )
    published = lifecycle.reimport(library_variant(source="library_next"))
    found = descriptors(store, published.manifest)
    assert found["books"].fields.primary_key == key
    assert "rel:reviews.book_id" not in found
    assert "reviews" in found
    assert Change("rel:reviews.book_id", "", "dropped") in published.changes
    notes = {(note.kind, note.subject) for note in published.notes}
    assert ("reimported", "rel:reviews.book_id") in notes


def test_a_pack_s_dataset_extension_is_carried_by_its_inference(
    lifecycle: Lifecycle, roots: Roots, birds: Birds, store: Store
) -> None:
    def survey(**changes: Any) -> Path:
        return roots.write("survey.json", json.dumps(birds.survey(**changes)).encode())

    first = lifecycle.import_(survey(), registry=birds.registry, pack="birds")
    entry = descriptors(store, first.manifest)["dataset"].curation["/extensions/birds/protocol"]
    assert entry.inferred == "stationary"
    published = lifecycle.reimport(
        survey(protocol="travelling"), registry=birds.registry, pack="birds"
    )
    dataset = descriptors(store, published.manifest)["dataset"]
    assert dataset.extensions["birds"]["protocol"] == "travelling"
    assert dataset.fields.packs == ["birds"]
    assert Change("dataset", "/extensions/birds/protocol", "proposed") in published.changes


def test_an_operator_s_field_leaves_no_tombstone_and_the_importer_s_proposal_comes_back(
    lifecycle: Lifecycle, roots: Roots, store: Store
) -> None:
    """An accepted limitation (D239, D240): a field an operator wrote before the importer inferred
    it has no inference, so removing it leaves no tombstone, and the importer's proposal of it
    comes back, as ``proposed``, at the next re-import."""
    path = roots.write("people.csv", b"id,name\np1,Ada\np1,Ada\n")
    first = lifecycle.import_(path)
    assert "grain" not in descriptors(store, first.manifest)["people"].fields.model_fields_set
    grain = {"op": "set", "descriptor": "people", "pointer": "/fields/grain", "value": "A person"}
    curate(store, grain)
    path.write_bytes(b"id,name\np1,Ada\np2,Grace\n")
    inferred = lifecycle.reimport(path)
    people = descriptors(store, inferred.manifest)["people"]
    assert (people.fields.grain, people.curation["/fields/grain"].by) == ("A person", ADA)
    assert "inferred" not in people.curation["/fields/grain"].model_fields_set
    removed = curate(store, {"op": "remove", "descriptor": "people", "pointer": "/fields/grain"})
    assert store.tombstones(removed) == ()
    back = descriptors(store, lifecycle.reimport(path).manifest)["people"]
    assert back.fields.grain == "One row per id"
    assert back.curation["/fields/grain"].status == "proposed"


# --- Provenance, removals that stand, the pack checks ------------------------------------------


def test_an_operator_s_provenance_survives_a_re_import_that_sets_none(
    lifecycle: Lifecycle, library_variant: Variant, store: Store
) -> None:
    """D239: the core's importer sets no provenance, so an operator's put of it is kept, and an
    unchanged re-import publishes nothing."""
    lifecycle.import_(library_variant())
    dataset = descriptors(store, store.resolve("d").manifest)["dataset"]
    body = dataset.model_dump(mode="json", exclude_none=True, exclude={"version", "curation"})
    body["provenance"] = {"citation": ["doi:10.1000/xyz"]}
    curate(store, {"op": "put", "descriptor": body})
    with pytest.raises(ImportRefused) as refused:
        lifecycle.reimport(library_variant())
    assert codes(refused) == [("NO_CHANGE", None)]
    published = lifecycle.reimport(library_variant(source="library_next"))
    kept = descriptors(store, published.manifest)["dataset"].provenance
    assert kept is not None
    assert kept.model_dump(mode="json", exclude_none=True) == {"citation": ["doi:10.1000/xyz"]}


def _birds_coverage_changed(birds: Birds, monkeypatch: pytest.MonkeyPatch) -> None:
    """The birds importer now proposes a plain coverage of its relationship, which is unchanged."""
    original = birds.importer.import_source

    def import_source(source: Any, options: Any) -> Any:
        result = original(source, options)
        found = []
        for descriptor in result.descriptors:
            if descriptor.id == "cov:counts.checklist_id":
                dumped = descriptor.model_dump(mode="json")
                dumped["fields"]["parents"] = "all"
                descriptor = type(descriptor).model_validate(dumped)
            found.append(descriptor)
        return replace(result, descriptors=found)

    monkeypatch.setattr(birds.importer, "import_source", import_source)


def test_a_pack_s_changed_coverage_of_a_relationship_whose_removal_stands_is_dropped(
    lifecycle: Lifecycle, roots: Roots, birds: Birds, store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D240: the coverage's inference changed but its relationship's did not, so the
    relationship's removal stands and the coverage's tombstone goes; the coverage, naming a
    removed relationship, is dropped, although the pack imports its fields rather than proposing
    them. Re-importing the same survey again changes nothing."""

    def survey(**changes: Any) -> Path:
        return roots.write("survey.json", json.dumps(birds.survey(**changes)).encode())

    lifecycle.import_(survey(), registry=birds.registry, pack="birds")
    opened = sessions.open_session(store, "d", ADA)
    request = ChangeRequest.model_validate(
        {
            "edits": [
                {"op": "remove_descriptor", "descriptor": "cov:counts.checklist_id"},
                {"op": "remove_descriptor", "descriptor": "rel:counts.checklist_id"},
            ]
        }
    )
    draft = sessions.change(
        store, "d", opened.handle, opened.draft, request, ADA, registry=birds.registry
    )
    sessions.publish(store, "d", opened.handle, draft, ADA, registry=birds.registry)
    removed = store.tombstones(draft)
    _birds_coverage_changed(birds, monkeypatch)
    published = lifecycle.reimport(survey(), registry=birds.registry, pack="birds")
    found = descriptors(store, published.manifest)
    assert not {"cov:counts.checklist_id", "rel:counts.checklist_id"} & found.keys()
    kept = [t for t in removed if t.descriptor == "rel:counts.checklist_id"]
    assert list(store.tombstones(published.manifest)) == kept
    assert [(c.descriptor, c.pointer, c.happened) for c in published.changes] == [
        ("cov:counts.checklist_id", "", "dropped")
    ]
    with pytest.raises(ImportRefused) as refused:
        lifecycle.reimport(survey(), registry=birds.registry, pack="birds")
    assert codes(refused) == [("NO_CHANGE", None)]


def test_a_carried_extension_the_pack_s_schema_refuses_points_into_the_descriptors(
    lifecycle: Lifecycle, roots: Roots, birds: Birds, store: Store
) -> None:
    path = roots.write("survey.json", json.dumps(birds.survey()).encode())
    lifecycle.import_(path, registry=birds.registry, pack="birds")
    schema = {"type": "object", "properties": {"protocol": {"enum": ["travelling"]}}}
    stricter = replace(birds.pack, extension_schemas={"dataset": schema})
    registry = PackRegistry([stricter], core_version=aibi.__version__)
    with pytest.raises(ImportRefused) as refused:
        lifecycle.reimport(path, registry=registry, pack="birds")
    assert codes(refused) == [
        ("INVALID_EXTENSION", "/descriptors/dataset/extensions/birds/protocol")
    ]

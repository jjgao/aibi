"""Carrying curation forward on re-import (SPEC §12.3, D239, D240): inferences are compared, not
values, so curation survives an unchanged inference, and a removal stands until the evidence
changes."""

from dataclasses import replace
from typing import Any

import pytest
from hypothesis import assume, example, given, settings
from hypothesis import strategies as st
from pydantic import TypeAdapter

from aibi.core.schema.curation import ChangeRequest
from aibi.core.schema.descriptors import Descriptor
from aibi.core.schema.release import check_release
from aibi.core.store.carry import Carried, CarryRefused, Change, carry_forward, with_inferences
from aibi.core.store.edits import EditRefused, apply
from aibi.core.store.tombstones import Tombstone

AT = "2026-01-01T00:00:00Z"
LATER = "2026-02-01T00:00:00Z"
IMPORTER = "importer:test@1"
OPERATOR = "operator:ada"
_ADAPTER: TypeAdapter[Descriptor] = TypeAdapter(Descriptor)


def described(
    kind: str,
    id: str,
    fields: dict[str, Any],
    *,
    label: str | None = None,
    status: str = "proposed",
    inferred: bool = True,
) -> Descriptor:
    """A descriptor as the importer proposes it: every entry has its value as ``inferred``."""

    def entry(value: Any, chosen: str) -> dict[str, Any]:
        found: dict[str, Any] = {"status": chosen, "by": IMPORTER, "at": AT}
        if inferred:
            found["inferred"] = value
        return found

    name = label or id
    curation = {"/label": entry(name, "imported")}
    curation.update({f"/fields/{key}": entry(value, status) for key, value in fields.items()})
    return _ADAPTER.validate_python(
        {
            "kind": kind,
            "id": id,
            "version": 1,
            "label": name,
            "fields": fields,
            "curation": curation,
        }
    )


def asserted(descriptor: Descriptor, pointer: str, value: Any = None, **entry: Any) -> Descriptor:
    """The descriptor with a field asserted by an operator, its inference kept; ``value`` given
    replaces the field's."""
    dumped: dict[str, Any] = descriptor.model_dump(mode="json")
    if value is not None:
        member = pointer.removeprefix("/fields/")
        if pointer.startswith("/fields/"):
            dumped["fields"][member] = value
        else:
            dumped[pointer[1:]] = value
    old = dumped["curation"].get(pointer, {})
    new: dict[str, Any] = {"status": "asserted", "by": OPERATOR, "at": LATER, **entry}
    if "inferred" in old:
        new["inferred"] = old["inferred"]
    dumped["curation"][pointer] = new
    return _ADAPTER.validate_python(dumped)


def dataset() -> Descriptor:
    return described("dataset", "dataset", {"name": "Test"}, status="imported")


def table(id: str = "t", key: list[str] | None = None, **fields: Any) -> Descriptor:
    given: dict[str, Any] = {"role": "entity", **fields}
    if key is not None:
        given["primary_key"] = key
    return described("table", id, given)


def column(id: str, datatype: str = "string", **fields: Any) -> Descriptor:
    return described("column", id, {"datatype": datatype, **fields})


def relationship(child: str, column_id: str, parent: str, label: str | None = None) -> Descriptor:
    return described(
        "relationship",
        f"rel:{child}.{column_id}",
        {
            "child_table": child,
            "child_columns": [column_id],
            "parent_table": parent,
            "parent_columns": ["id"],
            "cardinality": "many-to-one",
        },
        label=label,
    )


def coverage(relationship_id: str, label: str | None = None) -> Descriptor:
    return described(
        "coverage",
        "cov:" + relationship_id.removeprefix("rel:"),
        {"relationship": relationship_id, "parents": "all"},
        label=label,
    )


def release(*extra: Descriptor, age: str = "integer") -> list[Descriptor]:
    return [
        dataset(),
        table("p", ["id"]),
        column("p.id"),
        column("p.age", age),
        table("c", ["id"], role="event"),
        column("c.id"),
        column("c.p_id"),
        relationship("c", "p_id", "p"),
        *extra,
    ]


def by_id(carried: Carried) -> dict[str, Any]:
    return {descriptor.id: descriptor for descriptor in carried.descriptors}


def test_an_unchanged_import_carries_the_release_as_it_is() -> None:
    base = release(coverage("rel:c.p_id"))
    carried = carry_forward(base, [], base)
    assert list(carried.descriptors) == sorted(base, key=lambda d: d.id)
    assert (carried.tombstones, carried.changes) == ((), ())


def test_curation_survives_an_unchanged_inference_with_its_entry_verbatim() -> None:
    base = release()
    base[1] = asserted(base[1], "/label", "People", evidence="Checked")
    base[3] = asserted(base[3], "/fields/datatype")
    carried = by_id(carry_forward(base, [], release()))
    assert carried["p"] == base[1]
    assert carried["p.age"] == base[3]


def test_a_changed_inference_takes_the_new_proposal_even_over_an_assertion() -> None:
    base = release()
    base[3] = asserted(base[3], "/fields/datatype")
    carried = carry_forward(base, [], release(age="number"))
    age = by_id(carried)["p.age"]
    assert age.fields.datatype == "number"
    assert age.curation["/fields/datatype"].status == "proposed"
    assert carried.changes == (Change("p.age", "/fields/datatype", "proposed"),)


def test_a_field_the_importer_no_longer_infers_is_removed() -> None:
    base = release()
    new = release()
    new[1] = table("p")
    carried = carry_forward(base, [], new)
    assert by_id(carried)["p"].fields.primary_key is None
    assert Change("p", "/fields/primary_key", "removed") in carried.changes


def test_a_removed_field_stays_removed_while_its_inference_is_the_same() -> None:
    base = release()
    base[1] = table("p", ["id"], grain="One row per id")
    without = _ADAPTER.validate_python(
        {
            **base[1].model_dump(mode="json"),
            "fields": {"role": "entity", "primary_key": ["id"]},
            "curation": {
                k: v
                for k, v in base[1].model_dump(mode="json")["curation"].items()
                if k != "/fields/grain"
            },
        }
    )
    tombstone = Tombstone("p", "/fields/grain", "One row per id", 1)
    new = release()
    new[1] = table("p", ["id"], grain="One row per id")
    base[1] = without
    kept = carry_forward(base, [tombstone], new)
    assert by_id(kept)["p"].fields.grain is None
    assert kept.tombstones == (tombstone,)
    assert kept.changes == ()
    new[1] = table("p", ["id"], grain="One row per person")
    returned = carry_forward(base, [tombstone], new)
    assert by_id(returned)["p"].fields.grain == "One row per person"
    assert returned.tombstones == ()
    assert returned.changes == (Change("p", "/fields/grain", "returned"),)


def test_a_removed_descriptor_stays_removed_until_its_inferences_differ() -> None:
    relationship_id = "rel:c.p_id"
    new = release(coverage(relationship_id))
    removed = next(d for d in new if d.id == "cov:c.p_id")
    inferences = {p: e.inferred for p, e in sorted(removed.curation.items())}
    tombstone = Tombstone("cov:c.p_id", "", inferences, 3)
    base = release()
    kept = carry_forward(base, [tombstone], new)
    assert "cov:c.p_id" not in by_id(kept)
    assert kept.tombstones == (tombstone,)
    other = [
        described("coverage", "cov:c.p_id", {"relationship": relationship_id})
        if d.id == "cov:c.p_id"
        else d
        for d in new
    ]
    returned = carry_forward(base, [tombstone], other)
    assert by_id(returned)["cov:c.p_id"].fields.parents is None
    assert returned.changes == (Change("cov:c.p_id", "", "returned"),)
    assert returned.tombstones == ()


def test_an_operator_s_field_is_carried_and_the_new_inference_is_not_written_into_it() -> None:
    base = release()
    dumped: dict[str, Any] = base[3].model_dump(mode="json")
    dumped["fields"]["units"] = "a"
    dumped["curation"]["/fields/units"] = {"status": "asserted", "by": OPERATOR, "at": LATER}
    base[3] = _ADAPTER.validate_python(dumped)
    new = release()
    new[3] = column("p.age", "integer", units="mo")
    carried = by_id(carry_forward(base, [], new))
    assert carried["p.age"] == base[3]


def test_a_table_that_is_gone_takes_its_columns_relationships_and_coverage_with_it() -> None:
    base = release(coverage("rel:c.p_id"))
    tombstone = Tombstone("c.p_id", "/fields/identifier", True, 1)
    new = [d for d in release() if not d.id.startswith("c") and not d.id.startswith("rel:")]
    carried = carry_forward(base, [tombstone], new)
    assert sorted(by_id(carried)) == ["dataset", "p", "p.age", "p.id"]
    assert set(carried.changes) == {
        Change("c", "", "gone"),
        Change("c.id", "", "gone"),
        Change("c.p_id", "", "gone"),
        Change("rel:c.p_id", "", "removed"),
        Change("cov:c.p_id", "", "removed"),
    }
    assert carried.tombstones == ()


def test_an_operator_s_descriptor_is_carried_until_what_it_names_is_gone() -> None:
    added = described(
        "coverage", "cov:c.p_id", {"relationship": "rel:c.p_id", "parents": "all"}, inferred=False
    )
    carried = carry_forward(release(added), [], release())
    assert by_id(carried)["cov:c.p_id"] == added
    new = [d for d in release() if d.id != "rel:c.p_id"]
    gone = carry_forward(release(added), [], new)
    assert "cov:c.p_id" not in by_id(gone)
    assert Change("cov:c.p_id", "", "gone") in gone.changes


def test_the_dataset_descriptor_is_carried_by_inference_keeping_its_packs() -> None:
    base = release()
    base[0] = described("dataset", "dataset", {"name": "Test", "packs": ["a"]}, status="imported")
    base[0] = asserted(base[0], "/label", "Curated name")
    base[0] = asserted(base[0], "/fields/packs", ["a", "b"])
    new = release()
    new[0] = described("dataset", "dataset", {"name": "Renamed", "packs": ["c"]}, status="imported")
    carried = carry_forward(base, [], new)
    found = by_id(carried)["dataset"]
    assert (found.label, found.fields.name, found.fields.packs) == (
        "Curated name",
        "Renamed",
        ["a", "b"],
    )
    assert found.curation["/fields/packs"] == base[0].curation["/fields/packs"]
    assert carried.changes == (Change("dataset", "/fields/name", "proposed"),)
    unchanged = release()
    unchanged[0] = new[0]
    assert by_id(carry_forward([unchanged[0], *base[1:]], [], new))["dataset"] == new[0]


def test_a_carried_guess_that_no_longer_holds_takes_the_new_proposal() -> None:
    base = release()
    dumped: dict[str, Any] = base[3].model_dump(mode="json")
    dumped["fields"]["units"] = "a"
    dumped["curation"]["/fields/units"] = {"status": "proposed", "by": "agent:x", "at": LATER}
    base[3] = _ADAPTER.validate_python(dumped)
    carried = carry_forward(base, [], release(age="string"))
    age = by_id(carried)["p.age"]
    assert (age.fields.datatype, age.fields.units) == ("string", None)
    assert Change("p.age", "/fields/units", "removed") in carried.changes


def test_a_carried_assertion_that_no_longer_holds_refuses_the_re_import() -> None:
    base = release()
    dumped: dict[str, Any] = base[3].model_dump(mode="json")
    dumped["fields"]["units"] = "a"
    dumped["curation"]["/fields/units"] = {"status": "asserted", "by": OPERATOR, "at": LATER}
    base[3] = _ADAPTER.validate_python(dumped)
    with pytest.raises(CarryRefused) as refused:
        carry_forward(base, [], release(age="string"))
    assert [r.path for r in refused.value.refusals] == ["/descriptors/p.age/fields/units"]


def test_an_importer_s_entries_without_an_inference_take_their_value_as_one() -> None:
    found = with_inferences([described("table", "t", {"role": "entity"}, inferred=False)])
    assert found[0].curation["/fields/role"].inferred == "entity"
    assert found[0].curation["/label"].inferred == "t"


def test_an_absent_inference_consumes_a_field_tombstone() -> None:
    """D240: a tombstone stands only while the importer's inference is the same; once the importer
    no longer infers the field, its next inference is a new proposal."""
    tombstone = Tombstone("p", "/fields/grain", "One row per id", 1)
    carried = carry_forward(release(), [tombstone], release())
    assert (carried.tombstones, carried.changes) == ((), ())
    assert "grain" not in by_id(carried)["p"].fields.model_fields_set
    new = release()
    new[1] = table("p", ["id"], grain="One row per id")
    again = carry_forward(carried.descriptors, carried.tombstones, new)
    assert by_id(again)["p"].fields.grain == "One row per id"
    assert again.changes == (Change("p", "/fields/grain", "proposed"),)


def test_a_descriptor_tombstone_goes_when_the_importer_no_longer_proposes_the_descriptor() -> None:
    tombstone = Tombstone(
        "cov:c.p_id",
        "",
        {"/fields/parents": "all", "/fields/relationship": "rel:c.p_id", "/label": "cov:c.p_id"},
        1,
    )
    endpoint = Tombstone("ep:done", "", {"/label": "Done"}, 1)
    carried = carry_forward(release(), [endpoint, tombstone], release())
    assert (carried.tombstones, carried.changes) == ((), ())
    again = carry_forward(carried.descriptors, carried.tombstones, release(coverage("rel:c.p_id")))
    assert again.changes == (Change("cov:c.p_id", "", "added"),)


def test_a_descriptor_tombstone_goes_with_the_table_it_is_on() -> None:
    tombstone = Tombstone("cov:c.p_id", "", {"/fields/parents": "all"}, 1)
    new = [d for d in release() if not d.id.startswith(("c", "rel:"))]
    assert carry_forward(release(), [tombstone], new).tombstones == ()


def curated(
    carried: Carried, *edits: dict[str, Any]
) -> tuple[tuple[Descriptor, ...], tuple[Tombstone, ...]]:
    """The descriptors and tombstones after an operator's edits."""
    request = ChangeRequest.model_validate({"edits": list(edits)})
    applied = apply(
        carried.descriptors, carried.tombstones, request.edits, by=OPERATOR, at=LATER, proposals={}
    )
    assert check_release(applied.descriptors) == []
    return applied.descriptors, applied.tombstones


def test_a_field_set_again_after_an_import_that_did_not_infer_it_survives_that_import() -> None:
    """Had the tombstone outlived the import that lacked the inference,
    setting the field would have recorded that stale inference, and re-importing the same files
    would have removed the operator's value."""
    base = release()
    base[1] = table("p", ["id"], grain="One row per id")
    removed = curated(
        carry_forward(base, [], base),
        {"op": "remove", "descriptor": "p", "pointer": "/fields/grain"},
    )
    lacking = carry_forward(*removed, release())
    assert lacking.tombstones == ()
    again = curated(
        lacking, {"op": "set", "descriptor": "p", "pointer": "/fields/grain", "value": "A person"}
    )
    grain = {d.id: d for d in again[0]}["p"].curation["/fields/grain"]
    assert "inferred" not in grain.model_fields_set
    carried = carry_forward(*again, release())
    assert by_id(carried)["p"].fields.grain == "A person"
    assert (carried.descriptors, carried.tombstones, carried.changes) == (*again, ())


def test_a_descriptor_put_again_after_an_import_that_did_not_propose_it_survives_that_import() -> (
    None
):
    base = release(coverage("rel:c.p_id"))
    removed = curated(
        carry_forward(base, [], base), {"op": "remove_descriptor", "descriptor": "cov:c.p_id"}
    )
    lacking = carry_forward(*removed, release())
    assert lacking.tombstones == ()
    written = coverage("rel:c.p_id").model_dump(mode="json")
    del written["version"], written["curation"]
    again = curated(lacking, {"op": "put", "descriptor": written})
    carried = carry_forward(*again, release())
    assert "cov:c.p_id" in by_id(carried)
    assert (carried.descriptors, carried.tombstones, carried.changes) == (*again, ())


def test_a_descriptor_removed_after_an_import_that_did_not_infer_one_field_stays_removed() -> None:
    """The coverage's tombstone holds only what the latest import inferred,
    so re-importing the same files neither brings it back nor notes a changed inference."""
    base = release(coverage("rel:c.p_id"))
    removed = curated(
        carry_forward(base, [], base),
        {"op": "remove", "descriptor": "cov:c.p_id", "pointer": "/fields/parents"},
    )
    without_parents = release(described("coverage", "cov:c.p_id", {"relationship": "rel:c.p_id"}))
    lacking = carry_forward(*removed, without_parents)
    assert lacking.tombstones == ()
    again = curated(lacking, {"op": "remove_descriptor", "descriptor": "cov:c.p_id"})
    assert [t.inferred for t in again[1]] == [
        {"/fields/relationship": "rel:c.p_id", "/label": "cov:c.p_id"}
    ]
    carried = carry_forward(*again, without_parents)
    assert "cov:c.p_id" not in by_id(carried)
    assert (carried.descriptors, carried.tombstones, carried.changes) == (*again, ())


def test_a_field_tombstone_keeps_the_field_out_of_a_descriptor_new_to_the_release() -> None:
    """The gate dropped the coverage after the operator removed its ``parents``: the removal stands
    when the importer proposes the coverage again with the same inference, and not otherwise."""
    tombstone = Tombstone("cov:c.p_id", "/fields/parents", "all", 2)
    carried = carry_forward(release(), [tombstone], release(coverage("rel:c.p_id")))
    assert by_id(carried)["cov:c.p_id"].fields.parents is None
    assert "/fields/parents" not in by_id(carried)["cov:c.p_id"].curation
    assert carried.tombstones == (tombstone,)
    assert carried.changes == (Change("cov:c.p_id", "", "added"),)
    other = replace(tombstone, inferred="the inference then")
    returned = carry_forward(release(), [other], release(coverage("rel:c.p_id")))
    assert by_id(returned)["cov:c.p_id"].fields.parents == "all"
    assert returned.tombstones == ()
    assert returned.changes == (
        Change("cov:c.p_id", "", "added"),
        Change("cov:c.p_id", "/fields/parents", "returned"),
    )


def test_a_carried_default_that_no_longer_holds_takes_the_new_proposal() -> None:
    base = release()
    dumped: dict[str, Any] = base[3].model_dump(mode="json")
    dumped["fields"]["units"] = "a"
    dumped["curation"]["/fields/units"] = {"status": "imported_default", "by": IMPORTER, "at": AT}
    base[3] = _ADAPTER.validate_python(dumped)
    carried = carry_forward(base, [], release(age="string"))
    assert by_id(carried)["p.age"].fields.units is None
    assert Change("p.age", "/fields/units", "removed") in carried.changes


def test_an_operator_s_descriptor_that_fails_otherwise_than_by_naming_what_is_gone_refuses() -> (
    None
):
    added = _ADAPTER.validate_python(
        {
            **relationship("c", "id", "p").model_dump(mode="json"),
            "fields": {
                "child_table": "c",
                "child_columns": ["id"],
                "parent_table": "p",
                "parent_columns": ["age"],
                "cardinality": "many-to-one",
            },
            "curation": {
                pointer: {"status": "asserted", "by": OPERATOR, "at": LATER}
                for pointer in (
                    "/label",
                    "/fields/child_table",
                    "/fields/child_columns",
                    "/fields/parent_table",
                    "/fields/parent_columns",
                    "/fields/cardinality",
                )
            },
        }
    )
    base = release(added)
    assert [r.code for r in check_release(base)] == ["INVALID_VALUE"]
    with pytest.raises(CarryRefused) as refused:
        carry_forward(base, [], release())
    assert [r.path for r in refused.value.refusals] == [
        "/descriptors/rel:c.id/fields/parent_columns"
    ]


def test_provenance_is_the_new_import_s_when_it_has_one_else_the_base_s() -> None:
    def provenanced(descriptor: Descriptor, version: str) -> Descriptor:
        dumped: dict[str, Any] = descriptor.model_dump(mode="json")
        dumped["provenance"] = {"pipeline": {"name": "loader", "version": version}}
        return _ADAPTER.validate_python(dumped)

    base, new = release(), release()
    base[1] = provenanced(base[1], "1")
    new[1] = provenanced(new[1], "2")
    carried = by_id(carry_forward(base, [], new))
    assert carried["p"].model_dump(mode="json")["provenance"]["pipeline"]["version"] == "2"
    new[1] = release()[1]
    kept = carry_forward(base, [], new)
    assert by_id(kept)["p"] == base[1]
    assert kept.changes == ()
    assert "provenance" not in by_id(carry_forward(release(), [], new))["p"].model_dump(
        mode="json", exclude_none=True
    )


def derived(id: str, of: str) -> Descriptor:
    return _ADAPTER.validate_python(
        {
            "kind": "column",
            "id": id,
            "version": 1,
            "label": id,
            "fields": {
                "datatype": "number",
                "derived": {"op": "arith", "operator": "*", "args": [of, 2]},
            },
            "curation": {
                "/label": {"status": "asserted", "by": OPERATOR, "at": LATER},
                "/fields/datatype": {"status": "asserted", "by": OPERATOR, "at": LATER},
                "/fields/derived": {"status": "asserted", "by": OPERATOR, "at": LATER},
            },
        }
    )


def test_a_derived_column_is_carried_until_its_input_is_gone() -> None:
    base = release(derived("p.age2", "age"))
    assert check_release(base) == []
    carried = carry_forward(base, [], release())
    assert by_id(carried)["p.age2"] == base[-1]
    assert carried.changes == ()
    new = [d for d in release() if d.id != "p.age"]
    gone = carry_forward(base, [], new)
    assert "p.age2" not in by_id(gone)
    assert Change("p.age2", "", "gone") in gone.changes


def test_a_new_proposal_that_fails_against_carried_curation_is_dropped() -> None:
    base = [d for d in release() if d.id != "rel:c.p_id"]
    base[1] = asserted(base[1], "/fields/primary_key", ["id", "age"])
    assert check_release(base) == []
    new = release(relationship("c", "id", "p"), coverage("rel:c.id"))
    carried = carry_forward(base, [], new)
    found = by_id(carried)
    assert found["p"].fields.primary_key == ["id", "age"]
    assert not {"rel:c.id", "cov:c.id", "rel:c.p_id"} & found.keys()
    assert {
        Change("rel:c.id", "", "dropped"),
        Change("cov:c.id", "", "dropped"),
        Change("rel:c.p_id", "", "dropped"),
    } <= set(carried.changes)
    assert check_release(carried.descriptors) == []


def _pointing_at_age(descriptors: list[Descriptor]) -> list[Descriptor]:
    """The descriptors with the importer's relationship now pointing at ``p.age``."""
    found = list(descriptors)
    dumped: dict[str, Any] = found[7].model_dump(mode="json")
    dumped["fields"]["parent_columns"] = ["age"]
    dumped["curation"]["/fields/parent_columns"]["inferred"] = ["age"]
    found[7] = _ADAPTER.validate_python(dumped)
    return found


def test_a_failing_new_proposal_that_its_descriptor_needs_goes_back_to_the_base_s() -> None:
    """D239: a descriptor the base has is not dropped for a new guess; the field goes back to the
    base's value and entry, so the coverage an operator confirmed stays."""
    base = release(coverage("rel:c.p_id"))
    base[8] = asserted(base[8], "/fields/parents")
    assert check_release(base) == []
    carried = carry_forward(base, [], _pointing_at_age(release(coverage("rel:c.p_id"))))
    assert list(carried.descriptors) == sorted(base, key=lambda d: d.id)
    assert carried.changes == (Change("rel:c.p_id", "/fields/parent_columns", "dropped"),)


def test_a_descriptor_that_names_a_dropped_one_is_dropped_with_it() -> None:
    """The importer's new key for ``p`` breaks the relationship's carried guess, which is dropped
    with the coverage that names it."""
    base = release(coverage("rel:c.p_id"))
    assert check_release(base) == []
    new = release(coverage("rel:c.p_id"))
    new[1] = table("p", ["id", "age"])
    carried = carry_forward(base, [], new)
    assert not {"rel:c.p_id", "cov:c.p_id"} & by_id(carried).keys()
    assert carried.changes == (
        Change("cov:c.p_id", "", "dropped"),
        Change("p", "/fields/primary_key", "proposed"),
        Change("rel:c.p_id", "", "dropped"),
    )
    assert check_release(carried.descriptors) == []


def test_a_guess_the_importer_does_not_make_is_dropped_with_what_it_names() -> None:
    """An agent's coverage, which the importer does not propose, names the relationship dropped:
    it is dropped with it, not gone, since nothing it names is gone from the source."""
    base = release(coverage("rel:c.p_id"))
    dumped: dict[str, Any] = base[8].model_dump(mode="json")
    dumped["curation"] = {
        pointer: {"status": "proposed", "by": "agent:x", "at": LATER}
        for pointer in dumped["curation"]
    }
    base[8] = _ADAPTER.validate_python(dumped)
    new = release()
    new[1] = table("p", ["id", "age"])
    carried = carry_forward(base, [], new)
    assert Change("cov:c.p_id", "", "dropped") in carried.changes
    assert "cov:c.p_id" not in by_id(carried)


def test_a_descriptor_with_an_asserted_field_is_never_dropped_for_naming_a_dropped_one() -> None:
    base = release(coverage("rel:c.p_id"))
    base[8] = asserted(base[8], "/fields/parents")
    new = release(coverage("rel:c.p_id"))
    new[1] = table("p", ["id", "age"])
    with pytest.raises(CarryRefused) as refused:
        carry_forward(base, [], new)
    assert [(r.code, r.path) for r in refused.value.refusals] == [
        ("UNKNOWN_DESCRIPTOR", "/descriptors/cov:c.p_id/fields/relationship")
    ]


def test_a_failing_new_proposal_in_a_descriptor_someone_asserted_refuses() -> None:
    base = release()
    base[1] = asserted(base[1], "/fields/primary_key", ["id", "age"])
    base[7] = asserted(base[7], "/label", "Visits of a person")
    with pytest.raises(CarryRefused) as refused:
        carry_forward(base, [], release())
    assert [r.path for r in refused.value.refusals] == [
        "/descriptors/rel:c.p_id/fields/parent_columns"
    ]


def test_a_failing_guess_of_a_descriptor_the_importer_no_longer_proposes_refuses() -> None:
    added = _ADAPTER.validate_python(
        {
            **relationship("c", "id", "p").model_dump(mode="json"),
            "fields": {
                "child_table": "c",
                "child_columns": ["id"],
                "parent_table": "p",
                "parent_columns": ["age"],
                "cardinality": "many-to-one",
            },
            "curation": {
                pointer: {"status": "proposed", "by": "agent:x", "at": LATER}
                for pointer in (
                    "/label",
                    "/fields/child_table",
                    "/fields/child_columns",
                    "/fields/parent_table",
                    "/fields/parent_columns",
                    "/fields/cardinality",
                )
            },
        }
    )
    with pytest.raises(CarryRefused):
        carry_forward(release(added), [], release())


def test_a_new_proposed_field_that_fails_against_carried_curation_is_dropped_alone() -> None:
    base = release()
    base[3] = asserted(base[3], "/fields/datatype", "string")
    new = release()
    new[3] = column("p.age", "integer", units="mo")
    carried = carry_forward(base, [], new)
    age = by_id(carried)["p.age"]
    assert (age.fields.datatype, age.fields.units) == ("string", None)
    assert carried.changes == (Change("p.age", "/fields/units", "dropped"),)


def test_a_new_imported_field_that_fails_against_carried_curation_refuses() -> None:
    base = release()
    base[3] = asserted(base[3], "/fields/datatype", "string")
    new = release()
    new[3] = column("p.age", "integer", units="mo")
    dumped: dict[str, Any] = new[3].model_dump(mode="json")
    dumped["curation"]["/fields/units"]["status"] = "imported"
    new[3] = _ADAPTER.validate_python(dumped)
    with pytest.raises(CarryRefused):
        carry_forward(base, [], new)


def test_an_operator_s_removal_of_a_field_it_wrote_leaves_no_tombstone() -> None:
    """An accepted limitation (D239, D240): an operator's field has no inference, so removing it
    leaves no tombstone, and a later inference of it is proposed."""
    base = release()
    request = ChangeRequest.model_validate(
        {
            "edits": [
                {"op": "set", "descriptor": "p", "pointer": "/fields/grain", "value": "A person"},
                {"op": "remove", "descriptor": "p", "pointer": "/fields/grain"},
            ]
        }
    )
    applied = apply(base, [], request.edits, by=OPERATOR, at=LATER, proposals={})
    assert applied.tombstones == ()
    new = release()
    new[1] = table("p", ["id"], grain="One row per id")
    carried = carry_forward(applied.descriptors, applied.tombstones, new)
    assert by_id(carried)["p"].curation["/fields/grain"].status == "proposed"
    assert carried.changes == (Change("p", "/fields/grain", "proposed"),)


def _inferred(descriptor: Descriptor) -> dict[str, Any]:
    return {
        written: entry.inferred
        for written, entry in sorted(descriptor.curation.items())
        if "inferred" in entry.model_fields_set
    }


@pytest.mark.parametrize("status", ["proposed", "imported"])
def test_a_descriptor_naming_one_whose_removal_stands_is_dropped_and_its_tombstone_consumed(
    status: str,
) -> None:
    """The operator removed the relationship and its coverage; the importer's inference for the
    coverage changed but not the relationship's. The relationship's removal stands; the coverage's
    tombstone no longer holds the import's inference, so it goes (D240), and the coverage, naming a
    removed relationship, is dropped, whether the importer proposes its fields (the core's) or
    imports them (a pack's)."""
    base = [d for d in release() if d.id != "rel:c.p_id"]
    rel = relationship("c", "p_id", "p")
    cov = described(
        "coverage", "cov:c.p_id", {"relationship": "rel:c.p_id", "parents": "all"}, status=status
    )
    tombstones = [
        Tombstone("cov:c.p_id", "", {**_inferred(cov), "/fields/parents": "none of them"}, 2),
        Tombstone("rel:c.p_id", "", _inferred(rel), 3),
    ]
    carried = carry_forward(base, tombstones, [*release(), cov])
    assert not {"rel:c.p_id", "cov:c.p_id"} & by_id(carried).keys()
    assert carried.tombstones == (tombstones[1],)
    assert carried.changes == (Change("cov:c.p_id", "", "dropped"),)


def test_a_coverage_put_back_after_its_stale_tombstone_went_survives_the_same_files() -> None:
    """Had the coverage's tombstone of ``parents`` of an earlier import been kept, its inference
    would have gone into the operator's entry when the coverage was put back, and re-importing the
    same files would have turned the operator's ``parents`` back into the importer's
    proposal."""
    base = [d for d in release() if d.id != "rel:c.p_id"]
    rel = relationship("c", "p_id", "p")
    cov = coverage("rel:c.p_id")
    tombstones = [
        Tombstone("cov:c.p_id", "", {**_inferred(cov), "/fields/parents": ["x"]}, 2),
        Tombstone("rel:c.p_id", "", _inferred(rel), 3),
    ]
    first = carry_forward(base, tombstones, [*release(), cov])
    again = curated(
        first,
        {"op": "put", "descriptor": _written(rel)},
        {"op": "put", "descriptor": _written(cov)},
    )
    parents = {d.id: d for d in again[0]}["cov:c.p_id"].curation["/fields/parents"]
    assert (parents.status, "inferred" in parents.model_fields_set) == ("asserted", False)
    carried = carry_forward(*again, [*release(), cov])
    assert (carried.descriptors, carried.tombstones, carried.changes) == (*again, ())


@pytest.mark.parametrize("status", ["proposed", "imported"])
def test_a_new_descriptor_naming_one_whose_removal_stands_is_dropped(status: str) -> None:
    base = [d for d in release() if d.id != "rel:c.p_id"]
    tombstone = Tombstone("rel:c.p_id", "", _inferred(relationship("c", "p_id", "p")), 3)
    cov = described(
        "coverage", "cov:c.p_id", {"relationship": "rel:c.p_id", "parents": "all"}, status=status
    )
    carried = carry_forward(base, [tombstone], [*release(), cov])
    assert not {"rel:c.p_id", "cov:c.p_id"} & by_id(carried).keys()
    assert carried.tombstones == (tombstone,)
    assert carried.changes == (Change("cov:c.p_id", "", "dropped"),)


def test_a_returning_descriptor_that_is_dropped_leaves_its_tombstone_consumed() -> None:
    """D240: the tombstone's inferences differ from the import's, which is why the relationship
    returned; put back after the check dropped it, it would hold a stale inference."""
    base = [d for d in release() if d.id != "rel:c.p_id"]
    base[1] = asserted(base[1], "/fields/primary_key", ["id", "age"])
    tombstone = Tombstone("rel:c.p_id", "", {"/fields/parent_columns": ["age"]}, 3)
    carried = carry_forward(base, [tombstone], release())
    assert "rel:c.p_id" not in by_id(carried)
    assert carried.tombstones == ()
    assert carried.changes == (Change("rel:c.p_id", "", "dropped"),)


def test_a_field_tombstone_a_new_descriptor_cannot_hold_without_is_consumed() -> None:
    """The coverage left the release (the gate dropped it, say) after the operator removed its
    ``relationship``, which a coverage requires: proposed again with the same inference, it cannot
    hold without the field, so the field returns and the tombstone goes."""
    tombstone = Tombstone("cov:c.p_id", "/fields/relationship", "rel:c.p_id", 2)
    carried = carry_forward(release(), [tombstone], release(coverage("rel:c.p_id")))
    assert by_id(carried)["cov:c.p_id"].fields.relationship == "rel:c.p_id"
    assert carried.tombstones == ()
    assert carried.changes == (
        Change("cov:c.p_id", "", "added"),
        Change("cov:c.p_id", "/fields/relationship", "returned"),
    )


def test_a_field_tombstone_consumed_only_to_hold_comes_back_when_its_descriptor_is_dropped() -> (
    None
):
    """The coverage needed its ``relationship`` back, so the tombstone was consumed; the coverage
    is then dropped, since the relationship is not in the release, and the tombstone, which holds
    the import's inference, stands again."""
    tombstone = Tombstone("cov:c.p_id", "/fields/relationship", "rel:c.p_id", 2)
    base = [d for d in release() if d.id != "rel:c.p_id"]
    carried = carry_forward(base, [tombstone], [*base, coverage("rel:c.p_id")])
    assert "cov:c.p_id" not in by_id(carried)
    assert carried.tombstones == (tombstone,)
    assert carried.changes == (Change("cov:c.p_id", "", "dropped"),)


def test_a_field_tombstone_of_a_dropped_descriptor_stands_only_while_inferred_the_same() -> None:
    base = [d for d in release() if d.id != "rel:c.p_id"]
    tombstones = [
        Tombstone("cov:c.p_id", "/fields/parents", "all", 2),
        Tombstone("cov:c.p_id", "/label", "An earlier label", 2),
        Tombstone("rel:c.p_id", "", _inferred(relationship("c", "p_id", "p")), 3),
    ]
    carried = carry_forward(base, tombstones, release(coverage("rel:c.p_id")))
    assert "cov:c.p_id" not in by_id(carried)
    assert carried.tombstones == (tombstones[0], tombstones[2])


def test_the_importer_s_own_inference_is_compared_not_its_value() -> None:
    new = release()
    dumped: dict[str, Any] = new[3].model_dump(mode="json")
    dumped["fields"]["datatype"] = "number"
    new[3] = _ADAPTER.validate_python(dumped)
    assert new[3].curation["/fields/datatype"].inferred == "integer"
    carried = carry_forward(release(), [], new)
    assert by_id(carried)["p.age"].fields.datatype == "integer"
    assert carried.changes == ()


def test_a_failing_proposal_of_a_descriptor_new_to_the_release_is_dropped_alone() -> None:
    """D239: the field goes alone when its descriptor holds without it, a new table included."""
    carried = carry_forward(release(), [], release(table("q", ["nope"]), column("q.x")))
    assert by_id(carried)["q"].fields.primary_key is None
    assert "/fields/primary_key" not in by_id(carried)["q"].curation
    assert set(carried.changes) == {
        Change("q", "", "added"),
        Change("q", "/fields/primary_key", "dropped"),
        Change("q.x", "", "added"),
    }


def test_a_new_source_descriptor_whose_failing_field_is_imported_refuses() -> None:
    imported = described(
        "table", "q", {"role": "entity", "primary_key": ["nope"]}, status="imported"
    )
    with pytest.raises(CarryRefused) as refused:
        carry_forward(release(), [], release(imported, column("q.x")))
    assert [(r.code, r.path) for r in refused.value.refusals] == [
        ("UNKNOWN_DESCRIPTOR", "/descriptors/q/fields/primary_key/0")
    ]


def test_a_new_descriptor_whose_failing_field_is_imported_refuses() -> None:
    rel = _ADAPTER.validate_python(
        {
            **relationship("c", "id", "p").model_dump(mode="json"),
            "fields": {
                "child_table": "c",
                "child_columns": ["id"],
                "parent_table": "p",
                "parent_columns": ["age"],
                "cardinality": "many-to-one",
            },
            "curation": {
                pointer: {"status": "imported", "by": IMPORTER, "at": AT}
                for pointer in (
                    "/label",
                    "/fields/child_table",
                    "/fields/child_columns",
                    "/fields/parent_table",
                    "/fields/parent_columns",
                    "/fields/cardinality",
                )
            },
        }
    )
    with pytest.raises(CarryRefused) as refused:
        carry_forward(release(), [], release(rel))
    assert [r.path for r in refused.value.refusals] == [
        "/descriptors/rel:c.id/fields/parent_columns"
    ]


def test_a_descriptor_the_importer_still_proposes_that_names_one_removed_refuses() -> None:
    base = release(coverage("rel:c.p_id"))
    base[8] = asserted(base[8], "/fields/relationship")
    new = [d for d in release(coverage("rel:c.p_id")) if d.id != "rel:c.p_id"]
    with pytest.raises(CarryRefused) as refused:
        carry_forward(base, [], new)
    assert [(r.code, r.path) for r in refused.value.refusals] == [
        ("UNKNOWN_DESCRIPTOR", "/descriptors/cov:c.p_id/fields/relationship")
    ]


# --- Properties --------------------------------------------------------------------------------

VARIANTS = st.fixed_dictionaries(
    {
        "age": st.sampled_from(["integer", "number", "string"]),
        "coverage": st.booleans(),
        "relationship": st.booleans(),
        "grain": st.sampled_from([None, "One row per id", "One row per person"]),
        "relationship_label": st.sampled_from([None, "Linked"]),
        "coverage_label": st.sampled_from([None, "Covered"]),
    }
)


def variant(given: dict[str, Any]) -> list[Descriptor]:
    found = release(age=given["age"])
    if given["grain"] is not None:
        found[1] = table("p", ["id"], grain=given["grain"])
    if not given["relationship"]:
        return [d for d in found if d.id != "rel:c.p_id"]
    found[7] = relationship("c", "p_id", "p", given["relationship_label"])
    if given["coverage"]:
        found.append(coverage("rel:c.p_id", given["coverage_label"]))
    return found


@settings(max_examples=60, deadline=None)
@given(VARIANTS, VARIANTS)
def test_carrying_is_idempotent_and_gives_a_valid_release(
    first: dict[str, Any], second: dict[str, Any]
) -> None:
    base, new = variant(first), variant(second)
    once = carry_forward(base, [], new)
    assert check_release(once.descriptors) == []
    twice = carry_forward(once.descriptors, once.tombstones, new)
    assert twice.descriptors == once.descriptors
    assert twice.tombstones == once.tombstones
    assert twice.changes == ()


@settings(max_examples=30, deadline=None)
@given(VARIANTS)
def test_carrying_the_import_that_made_a_release_gives_it_back(
    given_variant: dict[str, Any],
) -> None:
    base = variant(given_variant)
    carried = carry_forward(base, [], base)
    assert list(carried.descriptors) == sorted(base, key=lambda d: d.id)
    assert carried.changes == ()


def _written(descriptor: Descriptor) -> dict[str, Any]:
    dumped: dict[str, Any] = descriptor.model_dump(mode="json")
    del dumped["version"], dumped["curation"]
    return dumped


EDITS: list[dict[str, Any]] = [
    {"op": "remove", "descriptor": "p", "pointer": "/fields/grain"},
    {"op": "remove", "descriptor": "p", "pointer": "/fields/primary_key"},
    {"op": "remove", "descriptor": "cov:c.p_id", "pointer": "/fields/parents"},
    {"op": "set", "descriptor": "p", "pointer": "/fields/grain", "value": "Curated"},
    {"op": "set", "descriptor": "p", "pointer": "/label", "value": "People"},
    {"op": "confirm", "descriptor": "c"},
    {"op": "remove_descriptor", "descriptor": "cov:c.p_id"},
    {"op": "remove_descriptor", "descriptor": "rel:c.p_id"},
    {"op": "put", "descriptor": _written(coverage("rel:c.p_id"))},
    {"op": "put", "descriptor": _written(relationship("c", "p_id", "p"))},
    {"op": "set", "descriptor": "p", "pointer": "/fields/primary_key", "value": ["id", "age"]},
    {"op": "set", "descriptor": "p", "pointer": "/fields/primary_key", "value": ["id"]},
]


def _curate(
    descriptors: tuple[Descriptor, ...], tombstones: tuple[Tombstone, ...], chosen: list[int]
) -> tuple[tuple[Descriptor, ...], tuple[Tombstone, ...]]:
    """The release after each chosen edit that a session would accept."""
    for index in chosen:
        request = ChangeRequest.model_validate({"edits": [EDITS[index]]})
        try:
            applied = apply(
                descriptors, tombstones, request.edits, by=OPERATOR, at=LATER, proposals={}
            )
        except EditRefused:
            continue
        if check_release(applied.descriptors) == []:
            descriptors, tombstones = applied.descriptors, applied.tombstones
    return descriptors, tombstones


_CHOSEN = st.lists(st.integers(0, len(EDITS) - 1), max_size=4)


def _v(**given: Any) -> dict[str, Any]:
    return {
        "age": "integer",
        "coverage": False,
        "relationship": True,
        "grain": None,
        "relationship_label": None,
        "coverage_label": None,
        **given,
    }


@settings(max_examples=150, deadline=None)
@given(VARIANTS, _CHOSEN, VARIANTS, _CHOSEN)
@example(_v(grain="One row per id"), [0], _v(), [3])
@example(_v(), [7], _v(relationship=False), [9])
@example(_v(coverage=True), [6], _v(), [8])
@example(_v(coverage=True), [6, 7], _v(coverage=True, coverage_label="Covered"), [9, 8])
@example(_v(), [7, 10], _v(relationship_label="Linked"), [11, 9])
def test_re_importing_the_same_files_after_curating_a_re_import_changes_nothing(
    first: dict[str, Any], before: list[int], second: dict[str, Any], after: list[int]
) -> None:
    """Whatever was curated before and after a re-import, re-importing the same files gives the
    curated release back, byte for byte. The only changes are the importer's proposals dropped
    again, which a re-import reports each time it makes them, and a descriptor the re-import
    dropped arriving once the curation that kept it out is undone (a coverage whose relationship
    the operator put back); a dropped descriptor the operator put back is carried like any
    other. D239's accepted limitation is left out: a field an operator wrote before the importer
    inferred it, removed after, is proposed again. The explicit examples: a coverage whose
    inference changed while its relationship's removal stood, and a relationship that returned
    and was dropped, each put back by the operator."""
    base = variant(first)
    curated = _curate(*_released(base), before)
    try:
        carried = carry_forward(*curated, variant(second))
    except CarryRefused:
        assume(False)
        raise
    again = _curate(carried.descriptors, carried.tombstones, after)
    assume(not _operators_removed(carried.descriptors, again[0]))
    unchanged = carry_forward(*again, variant(second))
    dropped = {c.descriptor for c in carried.changes if (c.pointer, c.happened) == ("", "dropped")}
    withheld = dropped - {descriptor.id for descriptor in again[0]}

    def kept(descriptors: tuple[Descriptor, ...]) -> list[Descriptor]:
        return [descriptor for descriptor in descriptors if descriptor.id not in withheld]

    assert kept(unchanged.descriptors) == kept(again[0])
    assert unchanged.tombstones == again[1]
    assert all(c.happened == "dropped" or c.descriptor in withheld for c in unchanged.changes)


def _operators_removed(carried: tuple[Descriptor, ...], curated: tuple[Descriptor, ...]) -> bool:
    """Whether the operator removed a field that has no inference (D239's accepted limitation)."""
    after = {descriptor.id: descriptor for descriptor in curated}
    for descriptor in carried:
        for written, entry in descriptor.curation.items():
            if "inferred" in entry.model_fields_set:
                continue
            found = after.get(descriptor.id)
            if found is None or written not in found.curation:
                return True
    return False


def _released(base: list[Descriptor]) -> tuple[tuple[Descriptor, ...], tuple[Tombstone, ...]]:
    carried = carry_forward(base, [], base)
    return carried.descriptors, carried.tombstones

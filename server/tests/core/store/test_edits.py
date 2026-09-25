"""Edits to a draft (SPEC §5.1, §12.3, D240, D245, D248): only operators assert, every field with
a value keeps exactly one entry, and an edit never erases the inference a re-import compares."""

from typing import Any

import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st
from pydantic import TypeAdapter, ValidationError

from aibi.core.engine import build
from aibi.core.schema.curation import ChangeRequest
from aibi.core.schema.descriptors import Descriptor
from aibi.core.schema.loading import refusal_from_error
from aibi.core.store import edits as edits_module
from aibi.core.store.appdb import StoredProposal
from aibi.core.store.edits import Applied, EditRefused, apply, holds, propose
from aibi.core.store.tombstones import Tombstone

AT = "2026-03-01T00:00:00Z"
OPERATOR = "operator:ada"
_ADAPTER: TypeAdapter[Descriptor] = TypeAdapter(Descriptor)


def inferred(descriptor: Descriptor) -> Descriptor:
    """The descriptor with every entry's value as its inference, as an importer writes it."""
    dumped: dict[str, Any] = descriptor.model_dump(mode="json")
    for pointer, entry in dumped["curation"].items():
        tokens = pointer.split("/")[1:]
        value: Any = dumped
        for token in tokens:
            value = value[token]
        entry.update(status="proposed", by="importer:test@1", inferred=value)
    return _ADAPTER.validate_python(dumped)


def draft() -> list[Descriptor]:
    return [
        build.dataset(),
        inferred(build.table("members", ["member_id"], grain="One row per member")),
        inferred(build.column("members.member_id", "string")),
        build.column("members.age", "integer", units="a"),
        build.column(
            "members.months",
            "number",
            derived={"op": "unit_convert", "input": "age", "units": "mo"},
        ),
        build.table("loans", ["loan_id"], role="event"),
        build.column("loans.loan_id", "integer"),
        build.column("loans.member_id", "string"),
        inferred(build.relationship("loans", ["member_id"], "members")),
        inferred(build.coverage("rel:loans.member_id", "all")),
    ]


def applied(
    *edits: dict[str, Any],
    tombstones: tuple[Tombstone, ...] = (),
    proposals: dict[int, StoredProposal] | None = None,
) -> Applied:
    try:
        request = ChangeRequest.model_validate({"edits": list(edits)})
    except ValidationError as error:
        found = error.errors(include_url=False, include_input=False)
        raise EditRefused(
            [refusal_from_error(details, ChangeRequest) for details in found]
        ) from None
    return apply(draft(), tombstones, request.edits, by=OPERATOR, at=AT, proposals=proposals or {})


def found(result: Applied, identifier: str) -> Any:
    return next(d for d in result.descriptors if d.id == identifier)


def refusal(*edits: dict[str, Any], **given: Any) -> tuple[str, str | None]:
    with pytest.raises(EditRefused) as refused:
        applied(*edits, **given)
    [first] = refused.value.refusals
    return (str(first.code), first.path)


EVERY_EDIT = [
    {"op": "set", "descriptor": "members", "pointer": "/definition", "value": "People"},
    {"op": "set", "descriptor": "members.age", "pointer": "/fields/units", "value": "mo"},
    {"op": "remove", "descriptor": "members", "pointer": "/fields/grain"},
    {"op": "confirm", "descriptor": "members.member_id"},
    {"op": "remove_descriptor", "descriptor": "members.months"},
    {
        "op": "put",
        "descriptor": {
            "kind": "endpoint",
            "id": "ep:left",
            "label": "Left",
            "fields": {"table": "loans"},
        },
    },
]


@pytest.mark.parametrize("edit", EVERY_EDIT, ids=[edit["op"] for edit in EVERY_EDIT])
def test_after_every_edit_kind_every_valued_field_has_exactly_one_entry(
    edit: dict[str, Any],
) -> None:
    for descriptor in applied(edit).descriptors:
        assert set(descriptor.curation) == descriptor.curated_pointers()


def test_set_asserts_by_the_operator_at_the_server_s_time_and_keeps_the_inference() -> None:
    result = applied(
        {
            "op": "set",
            "descriptor": "members",
            "pointer": "/fields/grain",
            "value": "One row per person",
            "evidence": "Checked",
        }
    )
    entry = found(result, "members").curation["/fields/grain"]
    assert (entry.status, entry.by, entry.at, entry.evidence) == (
        "asserted",
        OPERATOR,
        AT,
        "Checked",
    )
    assert entry.inferred == "One row per member"
    assert result.touched == (("members", "/fields/grain"),)


def test_confirm_asserts_every_field_or_those_named_and_keeps_their_inferences() -> None:
    every = found(applied({"op": "confirm", "descriptor": "members"}), "members")
    assert {entry.status for entry in every.curation.values()} == {"asserted"}
    assert every.curation["/fields/primary_key"].inferred == ["member_id"]
    named = found(
        applied({"op": "confirm", "descriptor": "members", "pointers": ["/fields/role"]}), "members"
    )
    assert named.curation["/fields/role"].status == "asserted"
    assert named.curation["/fields/grain"].status == "proposed"


def test_removing_an_inferred_field_leaves_a_tombstone_that_setting_it_again_lifts() -> None:
    removed = applied({"op": "remove", "descriptor": "members", "pointer": "/fields/grain"})
    assert found(removed, "members").fields.grain is None
    assert removed.tombstones == (Tombstone("members", "/fields/grain", "One row per member", 1),)
    again = applied(
        {"op": "set", "descriptor": "members", "pointer": "/fields/grain", "value": "Rows"},
        tombstones=removed.tombstones,
    )
    assert again.tombstones == ()
    assert found(again, "members").curation["/fields/grain"].inferred == "One row per member"


def test_removing_a_field_nobody_inferred_leaves_no_tombstone() -> None:
    removed = applied({"op": "remove", "descriptor": "members.age", "pointer": "/fields/units"})
    assert removed.tombstones == ()


@pytest.mark.parametrize(
    ("edit", "expected"),
    [
        (
            {"op": "remove", "descriptor": "members", "pointer": "/label"},
            ("INVALID_VALUE", "/edits/0/pointer"),
        ),
        (
            {"op": "remove", "descriptor": "members", "pointer": "/definition"},
            ("INVALID_VALUE", "/edits/0/pointer"),
        ),
        (
            {"op": "set", "descriptor": "members", "pointer": "/curation", "value": {}},
            ("INVALID_VALUE", "/edits/0/pointer"),
        ),
        (
            {"op": "set", "descriptor": "nobody", "pointer": "/label", "value": "x"},
            ("UNKNOWN_DESCRIPTOR", "/edits/0/descriptor"),
        ),
        (
            {"op": "remove_descriptor", "descriptor": "dataset"},
            ("INVALID_VALUE", "/edits/0/descriptor"),
        ),
        (
            {"op": "remove_descriptor", "descriptor": "loans"},
            ("COLUMNS_CHANGED", "/edits/0/descriptor"),
        ),
        (
            {"op": "remove_descriptor", "descriptor": "members.age"},
            ("COLUMNS_CHANGED", "/edits/0/descriptor"),
        ),
        (
            {"op": "set", "descriptor": "members", "pointer": "/fields/role", "value": "person"},
            ("INVALID_VALUE", "/draft/members/fields/role"),
        ),
    ],
)
def test_an_edit_that_cannot_apply_refuses_the_change(
    edit: dict[str, Any], expected: tuple[str, str]
) -> None:
    assert refusal(edit) == expected


@pytest.mark.parametrize("member", ["version", "curation"])
def test_a_client_sent_version_or_curation_is_refused(member: str) -> None:
    written = {"kind": "endpoint", "id": "ep:left", "label": "Left", "fields": {}, member: 1}
    assert refusal({"op": "put", "descriptor": written}) == (
        "INVALID_VALUE",
        f"/edits/0/descriptor/{member}",
    )


def test_put_keeps_the_entries_of_equal_fields_and_asserts_the_others() -> None:
    before = next(d for d in draft() if d.id == "members")
    written = before.model_dump(mode="json")
    del written["version"], written["curation"]
    written["fields"]["grain"] = "One row per person"
    del written["fields"]["role"]
    result = applied({"op": "put", "descriptor": written, "evidence": "Rewritten"})
    after = found(result, "members")
    assert after.curation["/fields/primary_key"] == before.curation["/fields/primary_key"]
    grain = after.curation["/fields/grain"]
    assert (grain.status, grain.evidence, grain.inferred) == (
        "asserted",
        "Rewritten",
        "One row per member",
    )
    assert "/fields/role" not in after.curation
    assert Tombstone("members", "/fields/role", "entity", 1) in result.tombstones


def test_removing_a_descriptor_leaves_one_tombstone_that_putting_it_back_lifts() -> None:
    removed = applied({"op": "remove_descriptor", "descriptor": "cov:loans.member_id"})
    [tombstone] = removed.tombstones
    assert tombstone.key == ("cov:loans.member_id", "")
    assert tombstone.inferred == {
        "/fields/parents": "all",
        "/fields/relationship": "rel:loans.member_id",
        "/label": "cov:loans.member_id",
    }
    written = {
        "kind": "coverage",
        "id": "cov:loans.member_id",
        "label": "Back",
        "fields": {"relationship": "rel:loans.member_id", "parents": "all"},
    }
    back = applied(
        {"op": "remove_descriptor", "descriptor": "cov:loans.member_id"},
        {"op": "put", "descriptor": written},
    )
    assert back.tombstones == ()
    entry = found(back, "cov:loans.member_id").curation["/fields/parents"]
    assert (entry.status, entry.inferred) == ("asserted", "all")


def _proposal(**given: Any) -> StoredProposal:
    fields: dict[str, Any] = {
        "id": 7,
        "dataset": "lib",
        "release": "sha256:" + "0" * 64,
        "descriptor": "members",
        "pointer": "/definition",
        "value": "Library members",
        "remove": False,
        "proposer": "agent:helper",
        "evidence": "Because the file says so",
        "at": AT,
        "status": "open",
    }
    fields.update(given)
    return StoredProposal(**fields)


def test_accepting_a_proposal_asserts_it_with_evidence_naming_it_and_not_its_rationale() -> None:
    result = applied({"op": "accept", "proposal": 7}, proposals={7: _proposal()})
    entry = found(result, "members").curation["/definition"]
    assert (entry.status, entry.by, entry.evidence) == (
        "asserted",
        OPERATOR,
        "Proposal 7",
    )
    assert result.accepted == (7,)


def test_accepting_a_removal_removes_and_an_unknown_or_repeated_proposal_is_refused() -> None:
    removal = _proposal(pointer="/fields/grain", value=None, remove=True)
    result = applied({"op": "accept", "proposal": 7}, proposals={7: removal})
    assert found(result, "members").fields.grain is None
    assert refusal({"op": "accept", "proposal": 8}) == ("UNKNOWN_PROPOSAL", "/edits/0/proposal")
    assert refusal(
        {"op": "accept", "proposal": 7},
        {"op": "accept", "proposal": 7},
        proposals={7: _proposal()},
    ) == ("CONFLICT", "/edits/1/proposal")
    gone = _proposal(descriptor="ep:gone", pointer="/label")
    assert refusal({"op": "accept", "proposal": 7}, proposals={7: gone}) == (
        "UNKNOWN_DESCRIPTOR",
        "/edits/0/proposal",
    )


def test_a_proposal_is_applied_as_proposed_by_its_proposer() -> None:
    result = propose(draft(), (), _proposal())
    entry = found(result, "members").curation["/definition"]
    assert (entry.status, entry.by, entry.evidence) == (
        "proposed",
        "agent:helper",
        "Because the file says so",
    )
    with pytest.raises(EditRefused) as refused:
        propose(draft(), (), _proposal(pointer="/fields/role", value="person"))
    assert [r.path for r in refused.value.refusals] == ["/descriptors/members/fields/role"]


def test_putting_back_a_removed_descriptor_without_a_field_keeps_that_field_s_removal() -> None:
    written = {
        "kind": "coverage",
        "id": "cov:loans.member_id",
        "label": "Back",
        "fields": {"relationship": "rel:loans.member_id"},
    }
    back = applied(
        {"op": "remove_descriptor", "descriptor": "cov:loans.member_id"},
        {"op": "put", "descriptor": written},
    )
    assert back.tombstones == (Tombstone("cov:loans.member_id", "/fields/parents", "all", 1),)
    direct = applied({"op": "put", "descriptor": written})
    assert direct.tombstones == back.tombstones
    assert found(direct, "cov:loans.member_id").fields == found(back, "cov:loans.member_id").fields


def test_accepting_a_whole_descriptor_proposal_over_a_removed_one_keeps_field_removals() -> None:
    value = {
        "kind": "coverage",
        "id": "cov:loans.member_id",
        "label": "Back",
        "fields": {"relationship": "rel:loans.member_id"},
    }
    proposal = _proposal(descriptor="cov:loans.member_id", pointer="", value=value)
    back = applied(
        {"op": "remove_descriptor", "descriptor": "cov:loans.member_id"},
        {"op": "accept", "proposal": 7},
        proposals={7: proposal},
    )
    assert back.tombstones == (Tombstone("cov:loans.member_id", "/fields/parents", "all", 1),)


def test_a_whole_descriptor_proposal_names_its_descriptor() -> None:
    value = {"kind": "endpoint", "id": "ep:other", "label": "Other", "fields": {"table": "loans"}}
    with pytest.raises(EditRefused) as refused:
        propose(draft(), (), _proposal(descriptor="ep:left", pointer="", value=value))
    assert [(str(r.code), r.path) for r in refused.value.refusals] == [("INVALID_VALUE", "")]


def test_removing_a_descriptor_absorbs_its_field_tombstones() -> None:
    removed = applied(
        {"op": "remove", "descriptor": "cov:loans.member_id", "pointer": "/fields/parents"},
        {"op": "remove_descriptor", "descriptor": "cov:loans.member_id"},
    )
    [tombstone] = removed.tombstones
    assert tombstone.key == ("cov:loans.member_id", "")
    assert tombstone.inferred == {
        "/fields/parents": "all",
        "/fields/relationship": "rel:loans.member_id",
        "/label": "cov:loans.member_id",
    }


def test_confirming_a_field_without_a_value_names_its_pointer_as_data() -> None:
    with pytest.raises(EditRefused) as refused:
        applied({"op": "confirm", "descriptor": "members", "pointers": ["/definition"]})
    [first] = refused.value.refusals
    assert first.message[-1].model_dump() == {"data": "/definition"}


# --- Shapes, and what a draft holds -----------------------------------------------------------


@pytest.mark.parametrize(
    ("member", "value", "path"),
    [
        ("fields", None, "/edits/0/descriptor/fields"),
        ("fields", ["a"], "/edits/0/descriptor/fields"),
        ("extensions", None, "/edits/0/descriptor/extensions"),
        ("extensions", [], "/edits/0/descriptor/extensions"),
        ("extensions", {"p": 5}, "/edits/0/descriptor/extensions/p"),
        ("extensions", {"p/q": None}, "/edits/0/descriptor/extensions/p~1q"),
    ],
)
def test_put_refuses_fields_or_extensions_that_are_not_objects(
    member: str, value: Any, path: str
) -> None:
    descriptor = {"kind": "coverage", "id": "cov:loans.member_id", "label": "L", member: value}
    with pytest.raises(EditRefused) as refused:
        applied({"op": "put", "descriptor": descriptor})
    [first] = refused.value.refusals
    assert (str(first.code), first.path) == ("WRONG_TYPE", path)
    assert first.message


def test_a_whole_descriptor_proposal_with_null_fields_is_refused_not_raised() -> None:
    value = {"kind": "coverage", "id": "cov:loans.member_id", "label": "L", "fields": None}
    with pytest.raises(EditRefused) as refused:
        propose(draft(), (), _proposal(descriptor="cov:loans.member_id", pointer="", value=value))
    assert [(str(r.code), r.path) for r in refused.value.refusals] == [
        ("WRONG_TYPE", "/descriptor/fields")
    ]


_ANY_JSON = st.recursive(
    st.none() | st.booleans() | st.integers(-2, 2) | st.text(max_size=3),
    lambda inner: (
        st.lists(inner, max_size=3) | st.dictionaries(st.text(max_size=3), inner, max_size=3)
    ),
    max_leaves=8,
)
_PUT_SHAPES = st.fixed_dictionaries(
    {
        "kind": st.sampled_from(["coverage", "endpoint", "table", "x"]),
        "id": st.sampled_from(["cov:loans.member_id", "ep:new", "members"]),
    },
    optional={
        "label": _ANY_JSON,
        "definition": _ANY_JSON,
        "fields": _ANY_JSON
        | st.dictionaries(st.sampled_from(["relationship", "parents", "table"]), _ANY_JSON),
        "extensions": _ANY_JSON
        | st.dictionaries(st.text(max_size=3), st.dictionaries(st.text(max_size=3), _ANY_JSON)),
    },
)


@settings(max_examples=400, deadline=None)
@given(_PUT_SHAPES)
@example({"kind": "table", "id": "members", "extensions": {"\ufffe": {}}})
def test_put_applies_or_refuses_whatever_shape_it_is_given(descriptor: dict[str, Any]) -> None:
    """A ``fields`` or ``extensions`` that is not an object is refused with a code, a path and a
    message, never raised. A body the request model itself refuses (a key that is not Unicode
    text) counts as refused with the refusals its errors map to, as a body is validated before
    any edit runs (D246)."""
    for attempt in (
        lambda: applied({"op": "put", "descriptor": descriptor}),
        lambda: propose(
            draft(), (), _proposal(descriptor=descriptor["id"], pointer="", value=descriptor)
        ),
    ):
        try:
            attempt()
        except EditRefused as refused:
            found = refused.refusals
        else:
            continue
        assert found
        assert all(r.code and r.path is not None and r.message for r in found)


def test_holds_says_whether_the_draft_still_holds_a_proposal() -> None:
    by_id = {descriptor.id: descriptor for descriptor in draft()}
    accepted = applied({"op": "accept", "proposal": 7}, proposals={7: _proposal()})
    after = {descriptor.id: descriptor for descriptor in accepted.descriptors}
    assert not holds(by_id, _proposal())
    assert holds(after, _proposal())
    edited = applied(
        {"op": "accept", "proposal": 7},
        {"op": "set", "descriptor": "members", "pointer": "/definition", "value": "Other"},
        proposals={7: _proposal()},
    )
    assert not holds({d.id: d for d in edited.descriptors}, _proposal())
    removal = _proposal(pointer="/fields/grain", value=None, remove=True)
    assert not holds(by_id, removal)
    assert holds(after | {"members": _without_grain(by_id["members"])}, removal)
    gone = _proposal(descriptor="members.months", pointer="", value=None, remove=True)
    assert not holds(by_id, gone)
    assert holds({k: v for k, v in by_id.items() if k != "members.months"}, gone)


def test_holds_compares_a_whole_descriptor_as_put_would_write_it() -> None:
    value = {
        "kind": "endpoint",
        "id": "ep:done",
        "label": "Done",
        "fields": {"table": "loans"},
        "extensions": {},
    }
    proposed = _proposal(descriptor="ep:done", pointer="", value=value)
    result = applied({"op": "accept", "proposal": 7}, proposals={7: proposed})
    by_id = {descriptor.id: descriptor for descriptor in result.descriptors}
    assert holds(by_id, proposed)
    relabelled = applied(
        {"op": "accept", "proposal": 7},
        {"op": "set", "descriptor": "ep:done", "pointer": "/label", "value": "Finished"},
        proposals={7: proposed},
    )
    assert not holds({d.id: d for d in relabelled.descriptors}, proposed)
    assert not holds({d.id: d for d in draft()}, proposed)


def _without_grain(descriptor: Descriptor) -> Descriptor:
    dumped: dict[str, Any] = descriptor.model_dump(mode="json")
    del dumped["fields"]["grain"], dumped["curation"]["/fields/grain"]
    return _ADAPTER.validate_python(dumped)


def test_a_field_removal_is_held_by_a_draft_that_lacks_its_descriptor() -> None:
    removal = _proposal(descriptor="members.months", pointer="/definition", value=None, remove=True)
    by_id = {descriptor.id: descriptor for descriptor in draft()}
    assert holds({k: v for k, v in by_id.items() if k != "members.months"}, removal)


def test_holds_replays_a_whole_descriptor_proposal_as_proposed_so_the_comparison_decides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Replayed as ``asserted`` by an agent, ``put`` would be refused wherever it writes an
    entry, and ``holds`` would answer from the refusal rather than from the descriptors."""
    value = {
        "kind": "endpoint",
        "id": "ep:done",
        "label": "Done",
        "fields": {"table": "loans"},
        "extensions": {},
    }
    proposed = _proposal(descriptor="ep:done", pointer="", value=value)
    result = applied({"op": "accept", "proposal": 7}, proposals={7: proposed})
    relabelled = applied(
        {"op": "accept", "proposal": 7},
        {"op": "set", "descriptor": "ep:done", "pointer": "/label", "value": "Finished"},
        proposals={7: proposed},
    )
    compared: list[bytes] = []
    real = edits_module._content

    def recording(descriptor: Descriptor) -> bytes:
        compared.append(content := real(descriptor))
        return content

    monkeypatch.setattr(edits_module, "_content", recording)
    assert not holds({d.id: d for d in relabelled.descriptors}, proposed)
    assert compared
    assert holds({d.id: d for d in result.descriptors}, proposed)

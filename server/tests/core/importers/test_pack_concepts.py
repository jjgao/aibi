"""Pack concepts at every descriptor write's call point (SPEC §10.1, §12.3, D247, D405): import,
re-import, a session's change and publish, and a proposal, with the birds pack given concepts of
each sort and an importer that maps ``sites.habitat`` to one; a pack removed or upgraded after a
concept was written; what a re-import repairs, and what it refuses whole; and the ways back."""

import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import pytest
from pydantic import TypeAdapter

import aibi
from aibi.core.importers.errors import ImportRefused
from aibi.core.schema.curation import ChangeRequest, ProposalInput
from aibi.core.schema.descriptors import ConceptDescriptor, ConceptFields, Descriptor
from aibi.core.schema.pack_api import ImportOptions, ImportResult, PackRegistry, Proposal
from aibi.core.store import sessions
from aibi.core.store.edits import EditRefused
from aibi.core.store.proposals import propose_descriptor, run_proposers
from aibi.core.store.store import Store

Lifecycle = Any
Roots = Any
Birds = Any
ADA = "operator:ada"
_ADAPTER: TypeAdapter[Descriptor] = TypeAdapter(Descriptor)
V1 = {
    "birds:habitat": "value",
    "birds:site": "table",
    "birds:return": "endpoint",
    "birds:origin.visit": "time_origin",
}
V2 = {
    "birds:habitat_type": "value",
    "birds:place": "table",
    "birds:comeback": "endpoint",
    "birds:origin.visit": "time_origin",
}
"""Version 2 renames three of version 1's concepts."""
MAPS = "/fields/maps_to/concept"


@dataclass
class Mapping:
    """The birds importer, which also maps ``sites.habitat`` to ``concept``, as an importer's
    inference."""

    inner: Any
    concept: str

    def import_source(self, source: Any, options: ImportOptions) -> ImportResult:
        result = self.inner.import_source(source, options)
        return replace(result, descriptors=[self._mapped(d) for d in result.descriptors])

    def _mapped(self, descriptor: Descriptor) -> Descriptor:
        if descriptor.id != "sites.habitat":
            return descriptor
        dumped = descriptor.model_dump(mode="json")
        dumped["fields"]["maps_to"] = {"concept": self.concept, "transform": None}
        dumped["curation"]["/fields/maps_to"] = dumped["curation"]["/fields/datatype"]
        return _ADAPTER.validate_python(dumped)


def concept(id: str, sort: str) -> ConceptDescriptor:
    return ConceptDescriptor(
        kind="concept",
        id=id,
        version=1,
        label=id,
        definition=f"A {sort} concept of bird surveys.",
        fields=ConceptFields(sort=sort),  # type: ignore[arg-type]
    )


def packs(
    birds: Birds,
    concepts: dict[str, str],
    *,
    maps: str = "birds:habitat",
    version: str = "1.0.0",
    **given: Any,
) -> PackRegistry:
    """The birds pack at ``version``, with ``concepts`` (id -> sort), its importer mapping
    ``sites.habitat`` to ``maps``, and no validator (a survey here may list no pack)."""
    pack = replace(
        birds.pack,
        manifest=birds.pack.manifest.model_copy(update={"version": version}),
        concepts=[concept(id, sort) for id, sort in concepts.items()],
        importer=Mapping(birds.importer, maps),
        validator=None,
        **given,
    )
    return PackRegistry([pack], core_version=aibi.__version__)


def survey(roots: Roots, birds: Birds, *, listed: bool = False) -> Path:
    """A survey whose dataset lists no pack and has no extension, unless ``listed``: a pack's
    concept is allowed in a dataset that does not list the pack (D405)."""
    written = birds.survey() if listed else birds.survey(packs=[])
    if not listed:
        del written["protocol"]
    return roots.write("survey.json", json.dumps(written).encode())


def request(*edits: dict[str, Any]) -> ChangeRequest:
    return ChangeRequest.model_validate({"edits": list(edits)})


def codes(error: pytest.ExceptionInfo[Any]) -> list[tuple[str, str | None]]:
    return [(str(refusal.code), refusal.path) for refusal in error.value.refusals]


def maps_to(store: Store, id: str) -> Any:
    latest = store.latest("d")
    assert latest is not None
    [found] = [d for d in store.descriptors(latest.manifest) if d.id == id]
    return found.fields.model_dump(mode="json").get("maps_to")


def change(store: Store, registry: PackRegistry | None, *edits: dict[str, Any]) -> tuple[str, str]:
    """A session with ``edits`` made under ``registry``: its handle and its draft."""
    opened = sessions.open_session(store, "d", ADA)
    draft = sessions.change(
        store, "d", opened.handle, opened.draft, request(*edits), ADA, registry=registry
    )
    return opened.handle, draft


def publish(store: Store, registry: PackRegistry | None, handle: str, draft: str) -> int:
    return sessions.publish(store, "d", handle, draft, ADA, registry=registry).label


def mapped(descriptor: str, concept: str) -> dict[str, Any]:
    value = {"concept": concept, "transform": None}
    return {"op": "set", "descriptor": descriptor, "pointer": "/fields/maps_to", "value": value}


DERIVED = {
    "kind": "column",
    "id": "sites.wooded",
    "label": "Wooded",
    "fields": {
        "datatype": "category",
        "derived": {"op": "value_map", "input": "habitat", "map": {"wood": "yes"}},
        "maps_to": {"concept": "birds:habitat", "transform": None},
    },
}
"""A derived column an operator adds, naming version 1's value concept: no field of it has an
importer's inference, so a re-import carries it as it is (§12.3)."""


# --- The five call points ------------------------------------------------------------------------


def test_an_import_naming_a_concept_no_installed_pack_registers_is_refused(
    lifecycle: Lifecycle, roots: Roots, birds: Birds, store: Store
) -> None:
    for wrong in ("birds:habitta", "birds:site", "other:habitat"):
        with pytest.raises(ImportRefused) as refused:
            lifecycle.import_(
                survey(roots, birds), registry=packs(birds, V1, maps=wrong), pack="birds"
            )
        code = "INVALID_VALUE" if wrong == "birds:site" else "UNKNOWN_DESCRIPTOR"
        assert codes(refused) == [(code, "/descriptors/sites.habitat" + MAPS)]
    assert store.latest("d") is None
    lifecycle.import_(survey(roots, birds), registry=packs(birds, V1), pack="birds")
    assert maps_to(store, "sites.habitat") == {"concept": "birds:habitat", "transform": None}


def test_a_re_import_whose_importer_names_a_concept_no_longer_registered_is_refused(
    lifecycle: Lifecycle, roots: Roots, birds: Birds, store: Store
) -> None:
    lifecycle.import_(survey(roots, birds), registry=packs(birds, V1), pack="birds")
    with pytest.raises(ImportRefused) as refused:
        lifecycle.reimport(survey(roots, birds), registry=packs(birds, V2), pack="birds")
    assert codes(refused) == [("UNKNOWN_DESCRIPTOR", "/descriptors/sites.habitat" + MAPS)]
    assert store.resolve("d").label == 1


def test_a_change_checks_what_it_touches_and_a_publish_checks_everything(
    lifecycle: Lifecycle, roots: Roots, birds: Birds, store: Store
) -> None:
    """A change is refused for a concept it writes, and not for one elsewhere that the running
    registry no longer has; the publish then refuses that one (D247, D405)."""
    v1 = packs(birds, V1)
    lifecycle.import_(survey(roots, birds), registry=v1, pack="birds")
    opened = sessions.open_session(store, "d", ADA)
    with pytest.raises(EditRefused) as refused:
        sessions.change(
            store,
            "d",
            opened.handle,
            opened.draft,
            request(mapped("sites.site_id", "birds:habitta")),
            ADA,
            registry=v1,
        )
    assert codes(refused) == [("UNKNOWN_DESCRIPTOR", "/draft/sites.site_id" + MAPS)]
    sessions.discard(store, "d", opened.handle, opened.draft, ADA)
    edit = {"op": "set", "descriptor": "sites", "pointer": "/label", "value": "Sites"}
    for running in (None, PackRegistry([], core_version=aibi.__version__), packs(birds, V2)):
        handle, draft = change(store, running, edit)
        with pytest.raises(EditRefused) as refused:
            publish(store, running, handle, draft)
        assert codes(refused) == [("UNKNOWN_DESCRIPTOR", "/draft/sites.habitat" + MAPS)]
        assert store.resolve("d").label == 1
        sessions.discard(store, "d", handle, draft, ADA)
    handle, draft = change(store, v1, edit)
    assert publish(store, v1, handle, draft) == 2


def test_a_change_checks_what_it_changes_and_not_what_an_earlier_change_of_the_session_did(
    lifecycle: Lifecycle, roots: Roots, birds: Birds, store: Store
) -> None:
    """The draft a change starts from is what it is compared with, not the release the session
    opened on (D405): a descriptor an earlier change wrote, naming a concept the running registry
    has since lost, is not refused again by a change of another descriptor; the publish refuses
    it, with every other descriptor that names it."""
    lifecycle.import_(survey(roots, birds), registry=packs(birds, V1), pack="birds")
    opened = sessions.open_session(store, "d", ADA)
    first = sessions.change(
        store,
        "d",
        opened.handle,
        opened.draft,
        request(mapped("sites.site_id", "birds:habitat")),
        ADA,
        registry=packs(birds, V1),
    )
    v2 = packs(birds, V2)
    edit = {"op": "set", "descriptor": "sites", "pointer": "/label", "value": "Sites"}
    second = sessions.change(store, "d", opened.handle, first, request(edit), ADA, registry=v2)
    with pytest.raises(EditRefused) as refused:
        publish(store, v2, opened.handle, second)
    assert codes(refused) == [
        ("UNKNOWN_DESCRIPTOR", "/draft/sites.habitat" + MAPS),
        ("UNKNOWN_DESCRIPTOR", "/draft/sites.site_id" + MAPS),
    ]
    with pytest.raises(EditRefused) as refused:
        sessions.change(
            store,
            "d",
            opened.handle,
            second,
            request(mapped("sites.site_id", "birds:habitat")),
            ADA,
            registry=v2,
        )
    assert codes(refused) == [("UNKNOWN_DESCRIPTOR", "/draft/sites.site_id" + MAPS)]


def test_a_publish_after_an_upgrade_that_changes_a_concept_s_sort_is_refused(
    lifecycle: Lifecycle, roots: Roots, birds: Birds, store: Store
) -> None:
    lifecycle.import_(survey(roots, birds), registry=packs(birds, V1), pack="birds")
    upgraded = packs(birds, {**V1, "birds:habitat": "table"}, version="2.0.0")
    edit = {"op": "set", "descriptor": "sites", "pointer": "/label", "value": "Sites"}
    handle, draft = change(store, upgraded, edit)
    with pytest.raises(EditRefused) as refused:
        publish(store, upgraded, handle, draft)
    assert codes(refused) == [("INVALID_VALUE", "/draft/sites.habitat" + MAPS)]


def test_a_proposal_checks_the_descriptor_it_touches_whole_and_no_other(
    lifecycle: Lifecycle, roots: Roots, birds: Birds, store: Store
) -> None:
    lifecycle.import_(survey(roots, birds), registry=packs(birds, V1), pack="birds")
    agent = "agent:helper"
    wrong = ProposalInput(descriptor="sites", pointer="/fields/time_origin", value="birds:vist")
    with pytest.raises(EditRefused) as refused:
        propose_descriptor(store, "d", wrong, agent, registry=packs(birds, V1))
    assert codes(refused) == [("UNKNOWN_DESCRIPTOR", "/descriptors/sites/fields/time_origin")]
    v2 = packs(birds, V2)
    elsewhere = ProposalInput(descriptor="sites", pointer="/label", value="Sites")
    assert propose_descriptor(store, "d", elsewhere, agent, registry=v2) > 0
    same = ProposalInput(descriptor="sites.habitat", pointer="/label", value="Habitat")
    with pytest.raises(EditRefused) as refused:
        propose_descriptor(store, "d", same, agent, registry=v2)
    assert codes(refused) == [("UNKNOWN_DESCRIPTOR", "/descriptors/sites.habitat" + MAPS)]


def test_a_curation_proposer_s_proposal_passes_the_same_check(
    lifecycle: Lifecycle, roots: Roots, birds: Birds, store: Store
) -> None:
    def propose(release: Any) -> list[Proposal]:
        return [
            Proposal("sites", "/fields/time_origin", "birds:origin.vist"),
            Proposal("checklists", "/fields/time_origin", "birds:origin.visit"),
        ]

    registry = packs(birds, V1, proposer=propose)
    lifecycle.import_(survey(roots, birds, listed=True), registry=registry, pack="birds")
    found = run_proposers(store, "d", registry)
    assert [(s.descriptor, s.codes) for s in found.skipped] == [("sites", ("UNKNOWN_DESCRIPTOR",))]
    assert len(found.proposals) == 1


# --- A re-import refused whole (round 3's probe 4) -----------------------------------------------


def test_a_re_import_carrying_curation_that_names_a_concept_gone_is_refused_whole(
    lifecycle: Lifecycle, roots: Roots, birds: Birds, store: Store
) -> None:
    """An operator's derived column, an accepted endpoint and a coverage's parent scope name
    version 1's concepts; carry-forward never deletes them for it (``check_release`` is
    core-only), so the re-import under version 2 is refused whole, naming each, and the latest
    release is unchanged."""
    v1 = packs(birds, V1)
    lifecycle.import_(survey(roots, birds), registry=v1, pack="birds")
    endpoint = {
        "kind": "endpoint",
        "id": "ep:return",
        "label": "Return",
        "fields": {"maps_to": {"concept": "birds:return", "transform": None}},
    }
    number = propose_descriptor(
        store,
        "d",
        ProposalInput(descriptor="ep:return", pointer="", value=endpoint),
        "agent:helper",
        registry=v1,
    )
    scope = {"kind": "exists", "table": "birds:site"}
    handle, draft = change(
        store,
        v1,
        {"op": "put", "descriptor": DERIVED},
        {"op": "accept", "proposal": number},
        {
            "op": "set",
            "descriptor": "cov:counts.checklist_id",
            "pointer": "/fields/parent_scope",
            "value": scope,
        },
    )
    assert publish(store, v1, handle, draft) == 2
    before = store.descriptors(store.resolve("d").manifest)
    v2 = packs(birds, V2, maps="birds:habitat_type", version="2.0.0")
    with pytest.raises(ImportRefused) as refused:
        lifecycle.reimport(survey(roots, birds), registry=v2, pack="birds")
    assert codes(refused) == [
        ("UNKNOWN_DESCRIPTOR", "/descriptors/cov:counts.checklist_id/fields/parent_scope/table"),
        ("UNKNOWN_DESCRIPTOR", "/descriptors/ep:return" + MAPS),
        ("UNKNOWN_DESCRIPTOR", "/descriptors/sites.wooded" + MAPS),
    ]
    assert store.resolve("d").label == 2
    assert store.descriptors(store.resolve("d").manifest) == before


# --- The ways back (D405) ------------------------------------------------------------------------


def test_a_re_import_alone_repairs_what_the_importer_inferred(
    lifecycle: Lifecycle, roots: Roots, birds: Birds, store: Store
) -> None:
    """(a) A concept renamed, and no reference without an importer's inference: the importer's
    field, confirmed by an operator or not, takes the importer's new proposal."""
    v1 = packs(birds, V1)
    lifecycle.import_(survey(roots, birds), registry=v1, pack="birds")
    confirm = {"op": "confirm", "descriptor": "sites.habitat", "pointers": ["/fields/maps_to"]}
    handle, draft = change(store, v1, confirm)
    assert publish(store, v1, handle, draft) == 2
    v2 = packs(birds, V2, maps="birds:habitat_type", version="2.0.0")
    assert lifecycle.reimport(survey(roots, birds), registry=v2, pack="birds").label == 3
    assert maps_to(store, "sites.habitat")["concept"] == "birds:habitat_type"


def test_with_a_reference_of_no_inference_every_stale_reference_is_edited_by_hand(
    lifecycle: Lifecycle, roots: Roots, birds: Birds, store: Store
) -> None:
    """(b) The operator's derived column refuses the re-import; a session that rewrites it
    alone cannot publish while the importer's own stale reference remains, since a publish
    checks every descriptor; one that rewrites both publishes, and the re-import then succeeds
    with the importer's new mapping."""
    v1 = packs(birds, V1)
    lifecycle.import_(survey(roots, birds), registry=v1, pack="birds")
    handle, draft = change(store, v1, {"op": "put", "descriptor": DERIVED})
    assert publish(store, v1, handle, draft) == 2
    v2 = packs(birds, V2, maps="birds:habitat_type", version="2.0.0")
    with pytest.raises(ImportRefused) as refused:
        lifecycle.reimport(survey(roots, birds), registry=v2, pack="birds")
    assert codes(refused) == [("UNKNOWN_DESCRIPTOR", "/descriptors/sites.wooded" + MAPS)]
    handle, draft = change(store, v2, mapped("sites.wooded", "birds:habitat_type"))
    with pytest.raises(EditRefused) as refused:
        publish(store, v2, handle, draft)
    assert codes(refused) == [("UNKNOWN_DESCRIPTOR", "/draft/sites.habitat" + MAPS)]
    sessions.discard(store, "d", handle, draft, ADA)
    handle, draft = change(
        store,
        v2,
        mapped("sites.wooded", "birds:habitat_type"),
        mapped("sites.habitat", "birds:habitat_type"),
    )
    assert publish(store, v2, handle, draft) == 3
    assert lifecycle.reimport(survey(roots, birds), registry=v2, pack="birds").label == 4
    assert maps_to(store, "sites.habitat")["concept"] == "birds:habitat_type"
    assert maps_to(store, "sites.wooded")["concept"] == "birds:habitat_type"


def test_with_the_pack_removed_every_reference_is_removed_by_hand(
    lifecycle: Lifecycle, roots: Roots, birds: Birds, store: Store
) -> None:
    """(c) No importer is left to bring a mapping back: a publish is refused until a session
    removes (or rewrites) every reference to the pack's concepts."""
    v1 = packs(birds, V1)
    lifecycle.import_(survey(roots, birds), registry=v1, pack="birds")
    handle, draft = change(store, v1, {"op": "put", "descriptor": DERIVED})
    assert publish(store, v1, handle, draft) == 2
    remove = {"op": "remove", "descriptor": "sites.wooded", "pointer": "/fields/maps_to"}
    handle, draft = change(store, None, remove)
    with pytest.raises(EditRefused) as refused:
        publish(store, None, handle, draft)
    assert codes(refused) == [("UNKNOWN_DESCRIPTOR", "/draft/sites.habitat" + MAPS)]
    sessions.discard(store, "d", handle, draft, ADA)
    handle, draft = change(
        store,
        None,
        remove,
        {"op": "remove", "descriptor": "sites.habitat", "pointer": "/fields/maps_to"},
    )
    assert publish(store, None, handle, draft) == 3
    assert maps_to(store, "sites.habitat") is None

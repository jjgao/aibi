"""The guard at the sites an import, a re-import, a session and the proposals call a pack at
(D247, D249, D400, D403): the importer, ``validate_source``, ``validate_descriptors`` (at import, at
re-import, on a change and at publish), the ontology validator and the proposer, each against every
kind of raise (``raisers``). A validator that fails refuses (``PACK_FAILED``), so that no release
passes a validator that did not run; a proposer that fails is skipped as ``PACK_FAILED``, never by
its type's name; the passed types pass as new instances; and the views of one pack never reach
another's."""

import json
import re
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from tests.core import raisers

import aibi
from aibi.core.importers.errors import ImportRefused
from aibi.core.schema.curation import ChangeRequest
from aibi.core.schema.pack_api import (
    ImportResult,
    Pack,
    PackManifest,
    PackRegistry,
    Proposal,
    ReleaseView,
)
from aibi.core.schema.refusals import Refusal
from aibi.core.store import proposals as proposals_module
from aibi.core.store import sessions
from aibi.core.store.edits import EditRefused
from aibi.core.store.store import Store

Roots = Any
Birds = Any
Lifecycle = Any
ADA = "operator:ada"
SYSTEM = "BIRD-CODES"


def _survey(roots: Roots, birds: Birds) -> Path:
    return roots.write("survey.json", json.dumps(birds.survey()).encode())


def _registry(pack: Pack) -> PackRegistry:
    return PackRegistry([pack], core_version=aibi.__version__)


def _request(*edits: dict[str, Any]) -> ChangeRequest:
    return ChangeRequest.model_validate({"edits": list(edits)})


_DATA_USE = {
    "op": "set",
    "descriptor": "dataset",
    "pointer": "/fields/data_use",
    "value": [{"system": SYSTEM, "code": "x1", "label": "Birds", "relation": "exact"}],
}


class _Validator:
    """A validator whose ``validate_source`` or ``validate_descriptors`` raises, as ``at``
    says."""

    def __init__(self, raise_: Callable[..., Any], at: str) -> None:
        self.raise_ = raise_
        self.at = at

    def validate_source(
        self, source: Any, result: ImportResult
    ) -> list[Refusal] | tuple[Refusal, ...]:
        if self.at == "source":
            self.raise_()
        return []

    def validate_descriptors(self, release: ReleaseView) -> list[Refusal] | tuple[Refusal, ...]:
        if self.at == "descriptors":
            self.raise_()
        return []


def _site(
    site: str,
    raise_: Callable[..., Any],
    lifecycle: Lifecycle,
    roots: Roots,
    birds: Birds,
    store: Store,
) -> Any:
    """Run the operation that calls ``site``'s hook, which raises by ``raise_``; what it
    returns, or its refusal."""
    survey = _survey(roots, birds)
    if site == "importer":

        class Raising:
            def import_source(self, source: Any, options: Any) -> Any:
                raise_()

        pack = replace(birds.pack, importer=Raising())
        return lifecycle.import_(survey, registry=_registry(pack), pack="birds")
    if site in ("source", "descriptors"):
        pack = replace(birds.pack, validator=_Validator(raise_, site))
        return lifecycle.import_(survey, registry=_registry(pack), pack="birds")
    if site == "proposer":
        pack = replace(birds.pack, proposer=raise_)
        return lifecycle.import_(survey, registry=_registry(pack), pack="birds")
    lifecycle.import_(survey, registry=birds.registry, pack="birds")
    if site == "reimport":
        pack = replace(birds.pack, validator=_Validator(raise_, "descriptors"))
        changed = roots.write(
            "survey.json", json.dumps(birds.survey(protocol="travelling")).encode()
        )
        return lifecycle.reimport(changed, registry=_registry(pack), pack="birds")
    opened = sessions.open_session(store, "d", ADA)
    if site == "change":
        pack = replace(birds.pack, validator=_Validator(raise_, "descriptors"))
        edit = {"op": "set", "descriptor": "sites", "pointer": "/label", "value": "Sites"}
        return sessions.change(
            store, "d", opened.handle, opened.draft, _request(edit), ADA, registry=_registry(pack)
        )
    if site == "ontology":
        pack = replace(birds.pack, ontology_systems={SYSTEM: raise_})
        return sessions.change(
            store,
            "d",
            opened.handle,
            opened.draft,
            _request(_DATA_USE),
            ADA,
            registry=_registry(pack),
        )
    assert site == "publish"
    edit = {"op": "set", "descriptor": "sites", "pointer": "/label", "value": "Sites"}
    draft = sessions.change(
        store, "d", opened.handle, opened.draft, _request(edit), ADA, registry=birds.registry
    )
    pack = replace(birds.pack, validator=_Validator(raise_, "descriptors"))
    return sessions.publish(store, "d", opened.handle, draft, ADA, registry=_registry(pack))


_SITES = ("importer", "source", "descriptors", "reimport", "change", "publish", "ontology")


@pytest.mark.parametrize("site", [*_SITES, "proposer"])
@pytest.mark.parametrize("kind", list(raisers.FAILURES))
def test_whatever_a_hook_raises_fails_closed_and_is_quoted_nowhere(
    lifecycle: Lifecycle,
    roots: Roots,
    birds: Birds,
    store: Store,
    site: str,
    kind: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    raise_ = raisers.raising(raisers.FAILURES[kind])
    if site == "proposer":
        published = _site(site, raise_, lifecycle, roots, birds, store)
        assert published.proposers is not None
        assert [(s.pack, s.codes) for s in published.proposers.skipped] == [
            ("birds", ("PACK_FAILED",))
        ]
        assert store.resolve("d").label == 1
        assert raisers.SECRET not in caplog.text
        return
    with pytest.raises((ImportRefused, EditRefused)) as refused:
        _site(site, raise_, lifecycle, roots, birds, store)
    found = refused.value.refusals
    assert [str(r.code) for r in found] == ["PACK_FAILED"], found
    assert raisers.SECRET not in json.dumps([r.model_dump(mode="json") for r in found])
    assert raisers.SECRET not in caplog.text
    assert "Owned" not in caplog.text
    assert "Base" not in caplog.text
    if site == "ontology":
        assert found[0].path == "/draft/dataset/fields/data_use/0/code"
    if site in ("change", "publish", "ontology"):
        assert store.resolve("d").label == 1


@pytest.mark.parametrize("site", [*_SITES, "proposer"])
@pytest.mark.parametrize("kind", list(raisers.PASSING))
def test_a_passed_type_a_hook_raises_passes_anew(
    lifecycle: Lifecycle, roots: Roots, birds: Birds, store: Store, site: str, kind: str
) -> None:
    passed = raisers.PASSING[kind]
    if site == "importer" and passed is MemoryError:
        with pytest.raises(ImportRefused) as refused:
            _site(site, raisers.passing(passed), lifecycle, roots, birds, store)
        [refusal] = refused.value.refusals
        assert refusal.limit is not None
        assert refusal.limit.name == "import_bytes"  # D400: the importer's own mapping
        return
    with pytest.raises(passed) as raised:
        _site(site, raisers.passing(passed), lifecycle, roots, birds, store)
    raisers.passed_anew(raised.value, passed)


def test_an_ontology_validator_that_gives_what_is_not_true_refuses_the_code(
    lifecycle: Lifecycle, roots: Roots, birds: Birds, store: Store
) -> None:
    lifecycle.import_(_survey(roots, birds), registry=birds.registry, pack="birds")
    for answer in (1, "yes", [True], re.fullmatch(r"x\d", "x1")):
        pack = replace(birds.pack, ontology_systems={SYSTEM: lambda code, a=answer: a})
        opened = sessions.open_session(store, "d", ADA)
        with pytest.raises(EditRefused) as refused:
            sessions.change(
                store,
                "d",
                opened.handle,
                opened.draft,
                _request(_DATA_USE),
                ADA,
                registry=_registry(pack),
            )
        assert [str(r.code) for r in refused.value.refusals] == ["INVALID_VALUE"]
        sessions.discard(store, "d", opened.handle, opened.draft, ADA)
    pack = replace(birds.pack, ontology_systems={SYSTEM: lambda code: True})
    opened = sessions.open_session(store, "d", ADA)
    sessions.change(
        store, "d", opened.handle, opened.draft, _request(_DATA_USE), ADA, registry=_registry(pack)
    )


def test_a_proposal_s_ontology_check_fails_closed_through_the_tool_too(
    lifecycle: Lifecycle, roots: Roots, birds: Birds, store: Store
) -> None:
    lifecycle.import_(_survey(roots, birds), registry=birds.registry, pack="birds")
    pack = replace(
        birds.pack, ontology_systems={SYSTEM: raisers.raising(raisers.FAILURES["value"])}
    )
    from aibi.core.schema.curation import ProposalInput

    proposal = ProposalInput.model_validate(
        {"descriptor": "dataset", "pointer": "/fields/data_use", "value": _DATA_USE["value"]}
    )
    with pytest.raises(EditRefused) as refused:
        proposals_module.propose_descriptor(
            store, "d", proposal, "agent:helper", registry=_registry(pack)
        )
    assert [str(r.code) for r in refused.value.refusals] == ["PACK_FAILED"]


# --- The views of one operation ---------------------------------------------------------------


class _MarkingValidator:
    def __init__(self, pack: str, seen: list[tuple[str, bool]]) -> None:
        self.pack = pack
        self.seen = seen

    def validate_source(
        self, source: Any, result: ImportResult
    ) -> list[Refusal] | tuple[Refusal, ...]:
        return []

    def validate_descriptors(self, release: ReleaseView) -> list[Refusal] | tuple[Refusal, ...]:
        extensions: Any = release.descriptors["sites"].extensions
        marks = sorted(key for key in extensions if key.startswith("mark-"))
        self.seen.append((self.pack, bool([m for m in marks if m != f"mark-{self.pack}"])))
        extensions[f"mark-{self.pack}"] = {"x": 1}
        return []


def _marker(pack: str, seen: list[tuple[str, bool]]) -> Pack:
    return Pack(
        manifest=PackManifest(id=pack, version="1.0.0", results_version=1, requires_core=">=0"),
        validator=_MarkingValidator(pack, seen),
        proposer=lambda release, p=pack: _proposing(release, p, seen),
    )


def _proposing(release: ReleaseView, pack: str, seen: list[tuple[str, bool]]) -> list[Proposal]:
    extensions: Any = release.descriptors["sites"].extensions
    seen.append((f"{pack} proposer", any(k != f"mark-{pack}" for k in extensions)))
    extensions[f"mark-{pack}"] = {"x": 1}
    return []


def test_no_pack_s_validator_or_proposer_sees_what_another_pack_did_to_its_view(
    lifecycle: Lifecycle, roots: Roots, birds: Birds, store: Store
) -> None:
    seen: list[tuple[str, bool]] = []
    survey = _survey(roots, birds)
    lifecycle.import_(survey, registry=birds.registry, pack="birds")
    packs = [replace(birds.pack, validator=None, proposer=None), _marker("ma", seen)]
    packs.append(_marker("mb", seen))
    registry = PackRegistry(packs, core_version=aibi.__version__)
    edit = {"op": "set", "descriptor": "dataset", "pointer": "/fields/packs", "value": []}
    listing = {**edit, "value": ["birds", "ma", "mb"]}
    opened = sessions.open_session(store, "d", ADA)
    draft = sessions.change(
        store, "d", opened.handle, opened.draft, _request(listing), ADA, registry=registry
    )
    sessions.publish(store, "d", opened.handle, draft, ADA, registry=registry)
    assert seen
    assert not any(other for _, other in seen), seen
    assert {pack for pack, _ in seen} >= {"ma", "mb", "ma proposer", "mb proposer"}
    for descriptor in store.descriptors(store.resolve("d").manifest):
        assert not any(key.startswith("mark-") for key in descriptor.extensions)


def test_at_publish_validators_read_the_draft_and_proposers_the_label_published(
    lifecycle: Lifecycle, roots: Roots, birds: Birds, store: Store
) -> None:
    labels: list[tuple[str, object]] = []

    class Validator:
        def validate_source(
            self, source: Any, result: ImportResult
        ) -> list[Refusal] | tuple[Refusal, ...]:
            return []

        def validate_descriptors(self, release: ReleaseView) -> list[Refusal] | tuple[Refusal, ...]:
            labels.append(("validator", release.label))
            release.descriptors["sites"].extensions["seen"] = {"by": "validator"}  # type: ignore[index]
            return []

    def propose(release: ReleaseView) -> list[Proposal]:
        labels.append(("proposer", release.label))
        labels.append(("proposer saw", "seen" in release.descriptors["sites"].extensions))
        return []

    lifecycle.import_(_survey(roots, birds), registry=birds.registry, pack="birds")
    registry = _registry(replace(birds.pack, validator=Validator(), proposer=propose))
    opened = sessions.open_session(store, "d", ADA)
    edit = {"op": "set", "descriptor": "sites", "pointer": "/label", "value": "Sites"}
    draft = sessions.change(
        store, "d", opened.handle, opened.draft, _request(edit), ADA, registry=registry
    )
    labels.clear()
    sessions.publish(store, "d", opened.handle, draft, ADA, registry=registry)
    assert labels == [("validator", "draft"), ("proposer", 2), ("proposer saw", False)]

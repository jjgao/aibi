"""The release lifecycle through the operator's functions (SPEC §10.1, §12.3, §13.2, D236–D252):
an import publishes, labels are never reused, withdrawn releases never come back, the pack
extension points run at their points, and random sequences of operations keep the lifecycle's
invariants."""

import itertools
import json
import shutil
import tempfile
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, precondition, rule

import aibi
from aibi.core.importers import run as run_module
from aibi.core.importers.confine import Confinement
from aibi.core.importers.errors import ImportRefused
from aibi.core.importers.run import Published, import_dataset, reimport_dataset
from aibi.core.schema.curation import ChangeRequest, ProposalInput
from aibi.core.schema.jsonschemas import WRITE_STEPS_MAX
from aibi.core.schema.limits import ImportLimits
from aibi.core.schema.pack_api import ImportOptions, PackError, PackRegistry, Proposal
from aibi.core.store import proposals as proposals_module
from aibi.core.store import sessions
from aibi.core.store.edits import EditRefused
from aibi.core.store.erasure import erase
from aibi.core.store.manifest import hex_of
from aibi.core.store.proposals import curation_queue, propose_descriptor, reject_proposal
from aibi.core.store.store import Pin, Store, StoreRefused

Lifecycle = Any
Variant = Any
Roots = Any
Birds = Any
ADA = "operator:ada"
AT = "2026-01-01T00:00:00Z"


def request(*edits: dict[str, Any]) -> ChangeRequest:
    return ChangeRequest.model_validate({"edits": list(edits)})


def codes(error: pytest.ExceptionInfo[Any]) -> list[tuple[str, str | None]]:
    return [(str(refusal.code), refusal.path) for refusal in error.value.refusals]


def curate(store: Store, *edits: dict[str, Any], dataset: str = "d", **given: Any) -> str:
    opened = sessions.open_session(store, dataset, ADA)
    draft = sessions.change(
        store, dataset, opened.handle, opened.draft, request(*edits), ADA, **given
    )
    sessions.publish(store, dataset, opened.handle, draft, ADA, **given)
    return draft


# --- Labels, import and withdrawal (§12.3) -----------------------------------------------------


def test_an_import_publishes_the_first_release_with_its_proposals_in_the_queue(
    lifecycle: Lifecycle, library_variant: Variant, store: Store
) -> None:
    published = lifecycle.import_(library_variant())
    assert published.label == 1
    assert store.resolve("d").manifest == published.manifest
    queue = curation_queue(store, "d")
    assert {item.status for item in queue.fields} == {"proposed", "imported_default"}
    assert ("members", "/fields/primary_key") in [(i.descriptor, i.pointer) for i in queue.fields]
    for descriptor in store.descriptors(published.manifest):
        assert all(entry.status != "asserted" for entry in descriptor.curation.values())
    action = store.db.connection.execute("SELECT action, actor FROM audit").fetchall()
    assert action == [("import", ADA)]


def test_a_second_import_of_a_dataset_is_refused(
    lifecycle: Lifecycle, library_variant: Variant
) -> None:
    lifecycle.import_(library_variant())
    with pytest.raises(ImportRefused) as refused:
        lifecycle.import_(library_variant())
    assert codes(refused) == [("DATASET_EXISTS", None)]


def test_an_import_after_every_release_is_withdrawn_gives_the_next_label(
    lifecycle: Lifecycle, library_variant: Variant, store: Store
) -> None:
    lifecycle.import_(library_variant())
    store.withdraw("d", 1, ADA)
    again = lifecycle.import_(library_variant(source="library_next"))
    assert again.label == 2
    assert [label.withdrawn for label in store.labels("d")] == [True, False]


def test_labels_are_never_reused(
    lifecycle: Lifecycle, library_variant: Variant, store: Store
) -> None:
    lifecycle.import_(library_variant())
    lifecycle.reimport(library_variant(source="library_next"))
    store.withdraw("d", 2, ADA)
    curate(store, {"op": "set", "descriptor": "books", "pointer": "/label", "value": "Books"})
    assert [label.label for label in store.labels("d")] == [1, 2, 3]
    assert store.resolve("d").label == 3


def test_an_import_is_an_operator_s(lifecycle: Lifecycle, library_variant: Variant) -> None:
    with pytest.raises(ImportRefused) as refused:
        lifecycle.import_(library_variant(), by="agent:helper")
    assert codes(refused) == [("INVALID_VALUE", None)]


def test_imports_re_imports_withdrawals_and_erasures_are_refused_while_a_session_is_open(
    lifecycle: Lifecycle, library_variant: Variant, store: Store
) -> None:
    lifecycle.import_(library_variant())
    sessions.open_session(store, "d", ADA)
    for call in (lifecycle.import_, lifecycle.reimport):
        with pytest.raises(ImportRefused) as refused:
            call(library_variant(source="library_next"))
        assert codes(refused) == [("DATASET_BUSY", None)]
    with pytest.raises(StoreRefused) as withdrawn:
        store.withdraw("d", 1, ADA)
    assert withdrawn.value.refusal.code == "DATASET_BUSY"
    with pytest.raises(StoreRefused) as erased:
        erase(store, "d", "members", ["m01"], ADA)
    assert erased.value.refusal.code == "ERASURE_BLOCKED"


def test_a_proposed_failure_in_a_draft_is_refused_not_dropped(
    lifecycle: Lifecycle, library_variant: Variant, store: Store
) -> None:
    lifecycle.import_(library_variant())
    opened = sessions.open_session(store, "d", ADA)
    codes_edit = {
        "op": "set",
        "descriptor": "members.member_id",
        "pointer": "/fields/missing_codes",
        "value": {"m01": "UNKNOWN"},
    }
    with pytest.raises(EditRefused) as refused:
        sessions.change(store, "d", opened.handle, opened.draft, request(codes_edit), ADA)
    assert ("KEY_NULL", "/draft/members/fields/primary_key") in codes(refused)


# --- The extension points of a pack (§10.1, D247, D249) ----------------------------------------


def _survey(roots: Roots, birds: Birds, **changes: Any) -> Path:
    return roots.write("survey.json", json.dumps(birds.survey(**changes)).encode())


def _import_birds(lifecycle: Lifecycle, roots: Roots, birds: Birds, **changes: Any) -> Published:
    return lifecycle.import_(
        _survey(roots, birds, **changes), registry=birds.registry, pack="birds"
    )


def _change(store: Store, birds: Birds, *edits: dict[str, Any]) -> pytest.ExceptionInfo[Any]:
    opened = sessions.open_session(store, "d", ADA)
    with pytest.raises(EditRefused) as refused:
        sessions.change(
            store, "d", opened.handle, opened.draft, request(*edits), ADA, registry=birds.registry
        )
    return refused


def test_an_extension_that_breaks_its_schema_is_refused_on_a_pack_import(
    lifecycle: Lifecycle, roots: Roots, birds: Birds, store: Store
) -> None:
    with pytest.raises(ImportRefused) as refused:
        _import_birds(lifecycle, roots, birds, protocol="flying")
    assert codes(refused) == [
        ("INVALID_EXTENSION", "/descriptors/dataset/extensions/birds/protocol")
    ]
    assert store.labels("d") == []


def test_an_extension_that_breaks_its_schema_is_refused_on_a_change(
    lifecycle: Lifecycle, roots: Roots, birds: Birds, store: Store
) -> None:
    _import_birds(lifecycle, roots, birds)
    edit = {
        "op": "set",
        "descriptor": "dataset",
        "pointer": "/extensions/birds/protocol",
        "value": "flying",
    }
    assert codes(_change(store, birds, edit)) == [
        ("INVALID_EXTENSION", "/draft/dataset/extensions/birds/protocol")
    ]


def test_an_extension_that_breaks_its_schema_is_refused_on_a_proposal(
    lifecycle: Lifecycle, roots: Roots, birds: Birds, store: Store
) -> None:
    _import_birds(lifecycle, roots, birds)
    given = ProposalInput(
        descriptor="dataset", pointer="/extensions/birds/protocol", value="flying"
    )
    with pytest.raises(EditRefused) as refused:
        propose_descriptor(store, "d", given, "agent:helper", registry=birds.registry)
    assert codes(refused) == [
        ("INVALID_EXTENSION", "/descriptors/dataset/extensions/birds/protocol")
    ]


def test_an_unregistered_pack_in_packs_is_refused(
    lifecycle: Lifecycle, roots: Roots, birds: Birds, store: Store
) -> None:
    with pytest.raises(ImportRefused) as refused:
        _import_birds(lifecycle, roots, birds, packs=["bees", "birds"])
    assert [code for code, _ in codes(refused)] == ["INVALID_VALUE"]
    _import_birds(lifecycle, roots, birds)
    edit = {
        "op": "set",
        "descriptor": "dataset",
        "pointer": "/fields/packs",
        "value": ["bees", "birds"],
    }
    assert ("INVALID_VALUE", "/draft/dataset/fields/packs/0") in codes(_change(store, birds, edit))


def test_the_pack_s_descriptor_validator_refusing_a_draft_change_stops_it(
    lifecycle: Lifecycle, roots: Roots, birds: Birds, store: Store
) -> None:
    _import_birds(lifecycle, roots, birds)
    edit = {"op": "remove", "descriptor": "dataset", "pointer": "/extensions/birds/protocol"}
    [refusal] = _change(store, birds, edit).value.refusals
    assert refusal.code == "birds.NO_PROTOCOL"
    assert refusal.path == "/draft/dataset/extensions"
    assert refusal.message[0].model_dump() == {"text": "Release @draft has no survey protocol"}


def test_a_coverage_naming_an_unknown_parent_refuses_a_change(
    lifecycle: Lifecycle, roots: Roots, birds: Birds, store: Store
) -> None:
    _import_birds(lifecycle, roots, birds)
    edit = {
        "op": "set",
        "descriptor": "cov:counts.checklist_id",
        "pointer": "/fields/parents",
        "value": {"table": "protocol_species", "parent_columns": {"protocol": "checklist_id"}},
    }
    refusals = codes(_change(store, birds, edit))
    assert (
        "COVERAGE_UNKNOWN",
        "/draft/cov:counts.checklist_id/fields/parents/parent_columns",
    ) in refusals


def test_the_proposer_s_proposals_enter_the_queue_after_import_and_after_publish_once(
    lifecycle: Lifecycle, roots: Roots, birds: Birds, store: Store
) -> None:
    published = _import_birds(lifecycle, roots, birds)
    assert published.proposers is not None
    first = published.proposers.proposals
    assert len(first) == 5
    queued = curation_queue(store, "d").proposals
    assert {p.proposer for p in queued} == {"importer:birds@1.0.0"}
    assert {p.descriptor for p in queued} == {
        "assignments",
        "checklists",
        "counts",
        "protocol_species",
        "sites",
    }
    curate(
        store,
        {"op": "set", "descriptor": "sites", "pointer": "/label", "value": "Sites"},
        registry=birds.registry,
    )
    assert [p.id for p in curation_queue(store, "d").proposals] == sorted(first)


def test_an_invalid_pack_proposal_is_skipped_and_reported(
    lifecycle: Lifecycle, roots: Roots, birds: Birds
) -> None:
    def propose(release: Any) -> list[Proposal]:
        return [
            Proposal("sites", "/fields/role", "person"),
            Proposal("nowhere", "/label", "x"),
            Proposal("sites", "/definition", "Places"),
        ]

    pack = replace(birds.pack, proposer=propose)
    registry = PackRegistry([pack], core_version=aibi.__version__)
    published = lifecycle.import_(_survey(roots, birds), registry=registry, pack="birds")
    assert published.proposers is not None
    assert len(published.proposers.proposals) == 1
    assert [(s.descriptor, s.codes) for s in published.proposers.skipped] == [
        ("sites", ("INVALID_VALUE",)),
        ("nowhere", ("UNKNOWN_DESCRIPTOR",)),
    ]


def test_a_proposer_that_fails_is_reported_and_the_import_stands(
    lifecycle: Lifecycle, roots: Roots, birds: Birds, store: Store
) -> None:
    def failing(release: Any) -> list[Proposal]:
        raise RuntimeError("the pack failed")

    registry = PackRegistry([replace(birds.pack, proposer=failing)], core_version=aibi.__version__)
    published = lifecycle.import_(_survey(roots, birds), registry=registry, pack="birds")
    assert published.proposers is not None
    assert [(s.pack, s.codes) for s in published.proposers.skipped] == [
        ("birds", ("RuntimeError",))
    ]
    assert store.resolve("d").label == 1


def test_a_proposal_that_raises_anything_is_skipped_and_the_publish_stands(
    lifecycle: Lifecycle, roots: Roots, birds: Birds, store: Store
) -> None:
    class Broken:
        @property
        def descriptor(self) -> str:
            raise KeyError("broken")

    def propose(release: Any) -> list[Any]:
        return [Broken(), Proposal("sites", "/definition", "Places")]

    registry = PackRegistry([replace(birds.pack, proposer=propose)], core_version=aibi.__version__)
    published = lifecycle.import_(_survey(roots, birds), registry=registry, pack="birds")
    assert published.proposers is not None
    assert [(s.descriptor, s.codes) for s in published.proposers.skipped] == [(None, ("KeyError",))]
    assert len(published.proposers.proposals) == 1


def test_an_exception_out_of_recording_a_proposal_is_skipped_after_a_session_publishes(
    lifecycle: Lifecycle, roots: Roots, birds: Birds, store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    _import_birds(lifecycle, roots, birds)

    def failing(*args: Any, **kwargs: Any) -> int:
        raise RecursionError("deep")

    monkeypatch.setattr(proposals_module, "propose_descriptor", failing)
    found = proposals_module.run_proposers(store, "d", birds.registry)
    assert {s.codes for s in found.skipped} == {("RecursionError",)}
    curate(
        store,
        {"op": "set", "descriptor": "sites", "pointer": "/label", "value": "Sites"},
        registry=birds.registry,
    )
    assert store.resolve("d").label == 2


def test_a_session_publish_runs_the_proposers_and_reports_them(
    lifecycle: Lifecycle, roots: Roots, birds: Birds, store: Store
) -> None:
    def propose(release: Any) -> list[Proposal]:
        label = release.descriptors["sites"].label
        return [Proposal("sites", "/definition", f"The sites, called {label}")]

    registry = PackRegistry([replace(birds.pack, proposer=propose)], core_version=aibi.__version__)
    lifecycle.import_(_survey(roots, birds), registry=registry, pack="birds")
    opened = sessions.open_session(store, "d", ADA)
    edit = {"op": "set", "descriptor": "sites", "pointer": "/label", "value": "Sites"}
    draft = sessions.change(
        store, "d", opened.handle, opened.draft, request(edit), ADA, registry=registry
    )
    published = sessions.publish(store, "d", opened.handle, draft, ADA, registry=registry)
    assert published.label == 2
    assert published.proposers is not None
    [number] = published.proposers.proposals
    [found] = [p for p in curation_queue(store, "d").proposals if p.id == number]
    assert found.value == "The sites, called Sites"
    opened = sessions.open_session(store, "d", ADA)
    edit = {"op": "set", "descriptor": "sites", "pointer": "/label", "value": "Places"}
    draft = sessions.change(
        store, "d", opened.handle, opened.draft, request(edit), ADA, registry=registry
    )
    with pytest.raises(EditRefused) as refused:
        sessions.publish(store, "d", opened.handle, draft, ADA)
    assert [(r.code, r.path) for r in refused.value.refusals] == [
        ("INVALID_VALUE", "/draft/dataset/fields/packs/0")
    ]
    assert sessions.publish(store, "d", opened.handle, draft, ADA, registry=registry).label == 3


def test_publishing_checks_every_descriptor_against_the_registry_it_runs_under(
    lifecycle: Lifecycle, roots: Roots, birds: Birds, store: Store
) -> None:
    """A change checks only the descriptors it touches (D247): once the pack is gone, or its
    schema is stricter, a change to another descriptor passes, and the publish refuses."""
    _import_birds(lifecycle, roots, birds)
    none = PackRegistry([], core_version=aibi.__version__)
    schema = {"type": "object", "properties": {"protocol": {"enum": ["travelling"]}}}
    stricter = PackRegistry(
        [replace(birds.pack, extension_schemas={"dataset": schema})], core_version=aibi.__version__
    )
    opened = sessions.open_session(store, "d", ADA)
    edit = {"op": "set", "descriptor": "sites", "pointer": "/label", "value": "Sites"}
    draft = sessions.change(
        store, "d", opened.handle, opened.draft, request(edit), ADA, registry=none
    )
    for registry, expected in (
        (none, ("INVALID_VALUE", "/draft/dataset/fields/packs/0")),
        (stricter, ("INVALID_EXTENSION", "/draft/dataset/extensions/birds/protocol")),
    ):
        with pytest.raises(EditRefused) as refused:
            sessions.publish(store, "d", opened.handle, draft, ADA, registry=registry)
        assert [(r.code, r.path) for r in refused.value.refusals] == [expected]
    assert store.resolve("d").label == 1
    assert (
        sessions.publish(store, "d", opened.handle, draft, ADA, registry=birds.registry).label == 2
    )


def test_imports_and_re_imports_evaluate_extensions_within_a_write_s_step_ceiling(
    lifecycle: Lifecycle, roots: Roots, birds: Birds, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[Any] = []
    real = run_module.check_writes

    def recording(descriptors: Any, registry: Any, **given: Any) -> Any:
        seen.append(given)
        return real(descriptors, registry, **given)

    monkeypatch.setattr(run_module, "check_writes", recording)
    _import_birds(lifecycle, roots, birds)
    lifecycle.reimport(
        _survey(roots, birds, protocol="travelling"), registry=birds.registry, pack="birds"
    )
    assert seen == [{"ceiling": WRITE_STEPS_MAX}] * 2


def test_a_rejected_pack_proposal_is_not_proposed_again(
    lifecycle: Lifecycle, roots: Roots, birds: Birds, store: Store
) -> None:
    _import_birds(lifecycle, roots, birds)
    [sites] = [p for p in curation_queue(store, "d").proposals if p.descriptor == "sites"]
    reject_proposal(store, "d", sites.id, ADA)
    curate(
        store,
        {"op": "set", "descriptor": "counts", "pointer": "/label", "value": "Counts"},
        registry=birds.registry,
    )
    assert "sites" not in {p.descriptor for p in curation_queue(store, "d").proposals}
    assert len(store.db.proposals("d", status="rejected")) == 1


def test_a_pack_s_validator_refusing_an_import_points_into_the_descriptors(
    lifecycle: Lifecycle, roots: Roots, birds: Birds, store: Store
) -> None:
    survey = birds.survey()
    del survey["protocol"]
    path = roots.write("survey.json", json.dumps(survey).encode())
    with pytest.raises(ImportRefused) as refused:
        lifecycle.import_(path, registry=birds.registry, pack="birds")
    assert codes(refused) == [("birds.NO_PROTOCOL", "/descriptors/dataset/extensions")]


def test_a_self_referring_extension_schema_is_refused_at_registration(birds: Birds) -> None:
    pack = replace(birds.pack, extension_schemas={"dataset": {"$ref": "#"}})
    with pytest.raises(PackError, match="refers to itself"):
        PackRegistry([pack], core_version=aibi.__version__)


# --- Curation commutes with an unchanged re-import (§12.3) --------------------------------------

PEOPLE = b"person_id,name,age\np1,Ada,36\np2,Grace,45\np3,Alan,41\np4,Edsger,70\n"
VISITS = b"visit_id,person_id,day\nv1,p1,2024-01-02\nv2,p1,2024-01-05\nv3,p3,2024-02-01\n"
EDITS: list[dict[str, Any]] = [
    {"op": "confirm", "descriptor": "people"},
    {"op": "confirm", "descriptor": "rel:visits.person_id"},
    {"op": "set", "descriptor": "people", "pointer": "/label", "value": "People"},
    {"op": "set", "descriptor": "visits", "pointer": "/definition", "value": "Visits"},
    {"op": "remove", "descriptor": "visits", "pointer": "/fields/grain"},
    {"op": "set", "descriptor": "people.age", "pointer": "/fields/units", "value": "a"},
    {"op": "remove_descriptor", "descriptor": "rel:visits.person_id"},
    {"op": "confirm", "descriptor": "people.age", "pointers": ["/fields/datatype"]},
    {
        "op": "put",
        "descriptor": {
            "kind": "relationship",
            "id": "rel:visits.person_id",
            "label": "Visitor",
            "fields": {
                "child_table": "visits",
                "child_columns": ["person_id"],
                "parent_table": "people",
                "parent_columns": ["person_id"],
                "cardinality": "many-to-one",
            },
        },
    },
]


def _clock() -> Callable[[], datetime]:
    ticks = itertools.count()
    start = datetime(2026, 1, 1, tzinfo=UTC)
    return lambda: start + timedelta(seconds=next(ticks))


class _Tiny:
    """A dataset of two CSV files in a fresh store."""

    def __init__(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.data = self.root / "in" / "tiny"
        self.data.mkdir(parents=True)
        self.confinement = Confinement.of(self.root / "in")
        self.store = Store(self.root / "store", clock=_clock())
        self.options = ImportOptions("d", self.confinement, ImportLimits(), AT)

    def write(self, people: bytes = PEOPLE, visits: bytes = VISITS) -> None:
        (self.data / "people.csv").write_bytes(people)
        (self.data / "visits.csv").write_bytes(visits)

    def import_(self) -> Published:
        return import_dataset(self.store, self.confinement.confine(self.data), self.options, ADA)

    def reimport(self) -> Published:
        path = self.confinement.confine(self.data)
        return reimport_dataset(self.store, path, self.options, ADA)

    def close(self) -> None:
        self.store.close()
        shutil.rmtree(self.root, ignore_errors=True)


def _applicable(edits: list[dict[str, Any]]) -> bool:
    """No edit names a descriptor an earlier edit removed, but a ``put`` that re-creates it."""
    removed: set[str] = set()
    for edit in edits:
        if edit["op"] == "put":
            removed.discard(edit["descriptor"]["id"])
            continue
        if edit["descriptor"] in removed:
            return False
        if edit["op"] == "remove_descriptor":
            removed.add(edit["descriptor"])
    return True


@settings(max_examples=25, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    st.lists(st.sampled_from(EDITS), min_size=1, max_size=4, unique_by=json.dumps).filter(
        _applicable
    )
)
def test_curation_commutes_with_an_unchanged_re_import(edits: list[dict[str, Any]]) -> None:
    tiny = _Tiny()
    try:
        tiny.write()
        tiny.import_()
        curated = curate(tiny.store, *edits)
        with pytest.raises(ImportRefused) as refused:
            tiny.reimport()
        assert codes(refused) == [("NO_CHANGE", None)]
        tiny.write(people=PEOPLE.replace(b",36\n", b",36.5\n"))
        changed = tiny.reimport()
        before = {d.id: d for d in tiny.store.descriptors(curated)}
        after = {d.id: d for d in tiny.store.descriptors(changed.manifest)}
        assert before.keys() == after.keys()
        assert {i for i in before if before[i] != after[i]} == {"people.age"}
    finally:
        tiny.close()


# --- The lifecycle's invariants under any sequence ----------------------------------------------

VARIANTS = (
    (PEOPLE, VISITS),
    (PEOPLE.replace(b",36\n", b",36.5\n"), VISITS),
    (PEOPLE, VISITS + b"v4,p4,2024-03-01\n"),
)
_TABLES = ("people", "visits")


class _Lifecycle(RuleBasedStateMachine):
    """Random imports, re-imports, session operations, proposals, withdrawals, pins and sweeps."""

    def __init__(self) -> None:
        super().__init__()
        self.tiny = _Tiny()
        self.store = self.tiny.store
        self.handle: str | None = None
        self.stale: list[str] = []
        self.pins: list[Pin] = []
        self.labels: list[int] = []
        self.withdrawn: dict[str, frozenset[int]] = {}
        self.removed: dict[str, Any] = {}

    def teardown(self) -> None:
        for pin in self.pins:
            pin.release()
        self.tiny.close()

    # --- Helpers ---

    def snapshot(self) -> tuple[Any, ...]:
        connection = self.store.db.connection
        tables = [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
            )
        ]
        dumped = tuple(
            tuple(connection.execute(f'SELECT * FROM "{table}" ORDER BY rowid'))
            for table in tables
            if not table.startswith("sqlite_")
        )
        return (dumped, frozenset(self.store.db.live_manifests()))

    def attempt(self, call: Callable[[], Any], *allowed: str) -> Any:
        """The call's result, or ``None`` when it is refused with one of ``allowed``, in which
        case nothing changed."""
        before = self.snapshot()
        try:
            return call()
        except (StoreRefused, ImportRefused, EditRefused) as error:
            found = (
                [str(error.refusal.code)]
                if isinstance(error, StoreRefused)
                else [str(r.code) for r in error.refusals]
            )
            assert set(found) <= set(allowed), found
            assert self.snapshot() == before
            return None

    def session(self) -> Any:
        return self.store.db.open_session_of("d")

    def published(self, label: int | None) -> None:
        if label is not None:
            assert label == max(self.labels, default=0) + 1
            self.labels.append(label)

    # --- Rules ---

    @precondition(lambda self: not self.labels)
    @rule()
    def import_(self) -> None:
        self.tiny.write()
        found = self.attempt(self.tiny.import_, "DATASET_BUSY", "DATASET_EXISTS")
        self.published(found.label if found is not None else None)

    @precondition(lambda self: self.labels)
    @rule(variant=st.sampled_from(VARIANTS))
    def reimport(self, variant: tuple[bytes, bytes]) -> None:
        self.tiny.write(*variant)
        found = self.attempt(
            self.tiny.reimport,
            "DATASET_BUSY",
            "NO_CHANGE",
            "RELEASE_WITHDRAWN",
            "UNKNOWN_RELEASE",
            "KEY_NOT_UNIQUE",
        )
        self.published(found.label if found is not None else None)

    @precondition(lambda self: self.labels)
    @rule()
    def open(self) -> None:
        opened = self.attempt(
            lambda: sessions.open_session(self.store, "d", ADA), "DATASET_BUSY", "UNKNOWN_RELEASE"
        )
        if opened is not None:
            self.handle = opened.handle

    @precondition(lambda self: self.handle is not None)
    @rule(data=st.data())
    def change(self, data: st.DataObject) -> None:
        session = self.session()
        assert session is not None
        assert self.handle is not None
        choices: list[dict[str, Any]] = [
            {"op": "confirm", "descriptor": data.draw(st.sampled_from(_TABLES))},
            {
                "op": "set",
                "descriptor": data.draw(st.sampled_from(_TABLES)),
                "pointer": "/label",
                "value": data.draw(st.sampled_from(["A", "B"])),
            },
            {"op": "remove_descriptor", "descriptor": "rel:visits.person_id"},
        ]
        written = self.removed.get("rel:visits.person_id")
        if written is not None:
            choices.append({"op": "put", "descriptor": written})
        proposals = self.store.db.proposals("d")
        if proposals:
            chosen = data.draw(st.sampled_from(proposals))
            choices.append({"op": "accept", "proposal": chosen.id})
        edit = data.draw(st.sampled_from(choices))
        if edit["op"] == "remove_descriptor":
            found = {d.id: d for d in self.store.descriptors(session.draft)}.get(edit["descriptor"])
            if found is not None:
                dumped = found.model_dump(mode="json")
                del dumped["version"], dumped["curation"]
                self.removed[edit["descriptor"]] = dumped
        self.attempt(
            lambda: sessions.change(
                self.store, "d", self.handle or "", session.draft, request(edit), ADA
            ),
            "UNKNOWN_DESCRIPTOR",
            "UNKNOWN_PROPOSAL",
            "INVALID_VALUE",
            "CONFLICT",
        )

    @precondition(lambda self: self.handle is not None)
    @rule(data=st.data())
    def stale_change(self, data: st.DataObject) -> None:
        session = self.session()
        assert session is not None
        assert self.handle is not None
        stale = data.draw(st.sampled_from(["handle", "draft"]))
        if stale == "handle" and not self.stale:
            return
        handle = self.stale[-1] if stale == "handle" else self.handle
        expected = session.draft
        if stale == "draft":
            expected = session.base if session.base != session.draft else "sha256:" + "0" * 64
        edit = {"op": "confirm", "descriptor": "people"}
        result = self.attempt(
            lambda: sessions.change(self.store, "d", handle, expected, request(edit), ADA),
            "CONFLICT",
        )
        assert result is None

    @precondition(lambda self: self.handle is not None)
    @rule()
    def take_over(self) -> None:
        taken = sessions.take_over(self.store, "d", "operator:grace")
        assert self.handle is not None
        self.stale.append(self.handle)
        self.handle = taken.handle

    @precondition(lambda self: self.handle is not None)
    @rule()
    def publish(self) -> None:
        session = self.session()
        assert session is not None
        assert self.handle is not None
        published = self.attempt(
            lambda: sessions.publish(self.store, "d", self.handle or "", session.draft, ADA),
            "NO_CHANGE",
            "RELEASE_WITHDRAWN",
        )
        if published is not None:
            self.published(published.label)
            self.handle = None

    @precondition(lambda self: self.handle is not None)
    @rule()
    def discard(self) -> None:
        session = self.session()
        assert session is not None
        assert self.handle is not None
        sessions.discard(self.store, "d", self.handle, session.draft, ADA)
        self.handle = None

    @precondition(lambda self: self.labels)
    @rule(data=st.data())
    def withdraw(self, data: st.DataObject) -> None:
        label = data.draw(st.sampled_from(self.store.labels("d")))
        found = self.attempt(
            lambda: self.store.withdraw("d", label.label, ADA), "DATASET_BUSY", "RELEASE_WITHDRAWN"
        )
        if found is not None:
            self.withdrawn[label.manifest] = frozenset(found)

    @precondition(lambda self: self.labels)
    @rule(table=st.sampled_from(_TABLES), text=st.sampled_from(["One", "Two"]))
    def propose(self, table: str, text: str) -> None:
        given = ProposalInput(descriptor=table, pointer="/definition", value=text)
        self.attempt(
            lambda: propose_descriptor(self.store, "d", given, "agent:helper"), "UNKNOWN_RELEASE"
        )

    @precondition(lambda self: self.labels)
    @rule(data=st.data())
    def reject(self, data: st.DataObject) -> None:
        proposals = self.store.db.proposals("d")
        if not proposals:
            return
        chosen = data.draw(st.sampled_from(proposals))
        self.attempt(lambda: reject_proposal(self.store, "d", chosen.id, ADA), "CONFLICT")

    @precondition(lambda self: self.labels)
    @rule(data=st.data())
    def pin(self, data: st.DataObject) -> None:
        label = data.draw(st.sampled_from(self.store.labels("d")))
        pin = self.store.pin()
        try:
            pin.manifest(label.manifest)
        except Exception:
            pin.release()
            return
        self.pins.append(pin)

    @precondition(lambda self: self.pins)
    @rule(data=st.data())
    def unpin(self, data: st.DataObject) -> None:
        pin = data.draw(st.sampled_from(self.pins))
        self.pins.remove(pin)
        pin.release()

    @rule()
    def sweep(self) -> None:
        self.store.sweep()

    # --- Invariants ---

    @invariant()
    def at_most_one_session_is_open(self) -> None:
        assert sum(session.open for session in self.store.db.sessions("d")) <= 1

    @invariant()
    def labels_grow_by_one_and_the_latest_is_the_highest_not_withdrawn(self) -> None:
        labels = self.store.labels("d")
        assert [label.label for label in labels] == self.labels
        latest = self.store.latest("d")
        live = [label for label in labels if not label.withdrawn]
        assert (latest.label if latest else None) == (live[-1].label if live else None)

    @invariant()
    def no_withdrawn_manifest_gains_a_label(self) -> None:
        for manifest, labels in self.withdrawn.items():
            found = {label.label for label in self.store.labels("d") if label.manifest == manifest}
            assert found == labels

    @invariant()
    def every_live_manifest_is_whole(self) -> None:
        for manifest in self.store.db.live_manifests():
            assert self.store.blobs.exists(hex_of(manifest))
            assert all(self.store.blobs.exists(b) for b in self.store.manifest(manifest).blobs())

    @invariant()
    def the_open_session_s_base_is_the_latest(self) -> None:
        session = self.session()
        if session is not None:
            latest = self.store.latest("d")
            assert latest is not None
            assert session.base == latest.manifest

    @invariant()
    def only_operators_assert_and_tombstoned_fields_are_absent(self) -> None:
        for manifest in self.store.db.live_manifests():
            found = {d.id: d for d in self.store.descriptors(manifest)}
            for descriptor in found.values():
                for entry in descriptor.curation.values():
                    assert entry.status != "asserted" or entry.by.startswith("operator:")
            for tombstone in self.store.tombstones(manifest):
                if tombstone.pointer == "":
                    assert tombstone.descriptor not in found
                elif tombstone.descriptor in found:
                    assert tombstone.pointer not in found[tombstone.descriptor].curation


def test_the_lifecycle_keeps_its_invariants_under_any_sequence() -> None:
    runner = _Lifecycle.TestCase
    runner.settings = settings(
        max_examples=20,
        stateful_step_count=20,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow],
    )
    runner().runTest()

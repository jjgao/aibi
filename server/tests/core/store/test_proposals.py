"""The proposal queue and the curation queue (SPEC §11.1, §12.3, §14, D248, D250): proposals
change no release until an operator accepts one in a draft, and the queue lists what is left to
curate, without cell values."""

import threading
from typing import Any

import pytest

from aibi.core.engine import build
from aibi.core.schema.curation import ChangeRequest, ProposalInput
from aibi.core.schema.jsonschemas import WRITE_STEPS_MAX
from aibi.core.schema.limits import MAX_OPEN_PROPOSALS, MAX_PROPOSAL_BYTES
from aibi.core.store import proposals as queue_module
from aibi.core.store import sessions as sessions_module
from aibi.core.store.edits import EditRefused
from aibi.core.store.proposals import curation_queue, propose_descriptor, reject_proposal
from aibi.core.store.redaction import MARK, Terms
from aibi.core.store.sessions import change, discard, open_session, publish
from aibi.core.store.store import Store, StoreRefused

ADA = "operator:ada"
AGENT = "agent:helper"


def proposal(**given: Any) -> ProposalInput:
    fields: dict[str, Any] = {
        "descriptor": "members",
        "pointer": "/definition",
        "value": "People who borrow books",
        "evidence": "The file's name",
    }
    fields.update(given)
    return ProposalInput.model_validate(fields)


def accept(number: int) -> ChangeRequest:
    return ChangeRequest.model_validate({"edits": [{"op": "accept", "proposal": number}]})


def status(store: Store, number: int) -> str:
    return store.db.connection.execute(
        "SELECT status FROM proposals WHERE id = ?", (number,)
    ).fetchone()[0]


def test_an_agent_s_proposal_is_recorded_against_the_latest_release_and_changes_none(
    store: Store, imported: str
) -> None:
    number = propose_descriptor(store, "lib", proposal(), AGENT)
    [stored] = store.db.proposals("lib")
    assert (stored.id, stored.release, stored.proposer, stored.value) == (
        number,
        imported,
        AGENT,
        "People who borrow books",
    )
    assert store.resolve("lib").manifest == imported
    assert [label.label for label in store.labels("lib")] == [1]


@pytest.mark.parametrize("by", [ADA, "someone", "agent:"])
def test_only_a_model_an_agent_or_an_importer_proposes(
    store: Store, imported: str, by: str
) -> None:
    with pytest.raises(StoreRefused) as refused:
        propose_descriptor(store, "lib", proposal(), by)
    assert refused.value.refusal.code == "INVALID_VALUE"


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        (
            {"pointer": "/fields/role", "value": "person"},
            ("INVALID_VALUE", "/descriptors/members/fields/role"),
        ),
        (
            {"pointer": "/fields/primary_key", "value": ["nope"]},
            ("UNKNOWN_DESCRIPTOR", "/descriptors/members/fields/primary_key/0"),
        ),
        ({"descriptor": "ep:nowhere", "pointer": "/label"}, ("UNKNOWN_DESCRIPTOR", "/descriptor")),
    ],
)
def test_an_invalid_proposal_is_refused_with_its_refusals(
    store: Store, imported: str, given: dict[str, Any], expected: tuple[str, str]
) -> None:
    with pytest.raises(EditRefused) as refused:
        propose_descriptor(store, "lib", proposal(**given), AGENT)
    assert expected in [(r.code, r.path) for r in refused.value.refusals]
    assert store.db.proposals("lib") == []


def test_an_identical_open_proposal_returns_its_id(store: Store, imported: str) -> None:
    first = propose_descriptor(store, "lib", proposal(), AGENT)
    assert propose_descriptor(store, "lib", proposal(), AGENT) == first
    assert propose_descriptor(store, "lib", proposal(evidence="Other"), AGENT) != first


def test_the_open_proposals_of_a_dataset_are_capped(store: Store, imported: str) -> None:
    with store.db.transaction() as db:
        for index in range(MAX_OPEN_PROPOSALS):
            store.db.add_proposal(
                db,
                dataset="lib",
                release=imported,
                descriptor="members",
                pointer="/definition",
                value=f"guess {index}",
                proposer=AGENT,
                evidence=None,
                at=store.now(),
            )
    with pytest.raises(StoreRefused) as refused:
        propose_descriptor(store, "lib", proposal(), AGENT)
    assert refused.value.refusal.code == "LIMIT_EXCEEDED"
    assert refused.value.refusal.limit is not None
    assert refused.value.refusal.limit.model_dump() == {"name": "open_proposals", "max": 10_000}


def test_a_proposed_value_has_at_most_its_byte_limit_in_rfc_8785_form(
    store: Store, imported: str
) -> None:
    """D248: 10,000 open proposals of a whole document each would fill the app DB."""
    with pytest.raises(StoreRefused) as refused:
        propose_descriptor(store, "lib", proposal(value="x" * (MAX_PROPOSAL_BYTES - 1)), AGENT)
    assert refused.value.refusal.code == "LIMIT_EXCEEDED"
    assert refused.value.refusal.limit is not None
    assert refused.value.refusal.limit.model_dump() == {"name": "proposal_bytes", "max": 65_536}
    with pytest.raises(EditRefused):  # at the limit, the release's checks refuse it instead
        propose_descriptor(store, "lib", proposal(value="x" * (MAX_PROPOSAL_BYTES - 2)), AGENT)
    assert store.db.proposals("lib") == []


def test_accepting_then_publishing_asserts_the_proposal_as_the_operator_s(
    store: Store, imported: str
) -> None:
    number = propose_descriptor(store, "lib", proposal(), AGENT)
    opened = open_session(store, "lib", ADA)
    draft = change(store, "lib", opened.handle, opened.draft, accept(number), ADA)
    assert status(store, number) == "open"
    publish(store, "lib", opened.handle, draft, ADA)
    members = next(d for d in store.descriptors(draft) if d.id == "members")
    entry = members.curation["/definition"]
    assert (members.definition, entry.status, entry.by) == (
        "People who borrow books",
        "asserted",
        ADA,
    )
    assert entry.evidence == f"Proposal {number}"
    assert status(store, number) == "accepted"


def test_accepting_then_discarding_leaves_the_proposal_open(store: Store, imported: str) -> None:
    number = propose_descriptor(store, "lib", proposal(), AGENT)
    opened = open_session(store, "lib", ADA)
    draft = change(store, "lib", opened.handle, opened.draft, accept(number), ADA)
    discard(store, "lib", opened.handle, draft, ADA)
    assert status(store, number) == "open"


def test_rejecting_a_proposal_is_audited_and_removes_it_from_the_queue(
    store: Store, imported: str
) -> None:
    number = propose_descriptor(store, "lib", proposal(), AGENT)
    assert [p.id for p in curation_queue(store, "lib").proposals] == [number]
    reject_proposal(store, "lib", number, ADA)
    assert status(store, number) == "rejected"
    assert curation_queue(store, "lib").proposals == []
    action = store.db.connection.execute(
        "SELECT action, actor, detail FROM audit ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert action == ("reject_proposal", ADA, f'{{"proposal":{number}}}')
    with pytest.raises(StoreRefused) as again:
        reject_proposal(store, "lib", number, ADA)
    assert again.value.refusal.code == "UNKNOWN_PROPOSAL"


def test_a_proposal_the_open_draft_accepted_is_not_rejected(store: Store, imported: str) -> None:
    number = propose_descriptor(store, "lib", proposal(), AGENT)
    opened = open_session(store, "lib", ADA)
    change(store, "lib", opened.handle, opened.draft, accept(number), ADA)
    with pytest.raises(StoreRefused) as refused:
        reject_proposal(store, "lib", number, ADA)
    assert refused.value.refusal.code == "CONFLICT"
    [listed] = curation_queue(store, "lib").proposals
    assert listed.accepted_in_draft


def test_a_whole_descriptor_proposal_is_accepted_as_a_new_descriptor(
    store: Store, imported: str
) -> None:
    coverage = {
        "kind": "coverage",
        "id": "cov:loans.member",
        "label": "Loans of members",
        "fields": {"relationship": "rel:loans.member", "parents": "all"},
    }
    number = propose_descriptor(
        store, "lib", proposal(descriptor="cov:loans.member", pointer="", value=coverage), AGENT
    )
    opened = open_session(store, "lib", ADA)
    draft = change(store, "lib", opened.handle, opened.draft, accept(number), ADA)
    added = next(d for d in store.descriptors(draft) if d.id == "cov:loans.member")
    assert (added.version, added.curation["/fields/parents"].status) == (1, "asserted")


def test_erasure_redacts_the_proposals_values_and_evidence(store: Store, imported: str) -> None:
    number = propose_descriptor(
        store, "lib", proposal(value="Includes m-17", evidence="m-17 said so"), AGENT
    )
    store.redact("lib", Terms(["m-17"]), [])
    [stored] = store.db.proposals("lib")
    assert (stored.id, stored.value, stored.evidence) == (
        number,
        f"Includes {MARK}",
        f"{MARK} said so",
    )


# --- The curation queue (D250) -----------------------------------------------------------------


def test_the_queue_lists_undeclared_fields_from_its_fixed_list(store: Store, imported: str) -> None:
    queue = curation_queue(store, "lib")
    assert (queue.dataset, queue.release, queue.label) == ("lib", imported, 1)
    undeclared = {(item.descriptor, item.pointer) for item in queue.undeclared}
    assert undeclared == {
        ("members", "/fields/grain"),
        ("loans", "/fields/grain"),
        ("books", "/fields/grain"),
        ("members.age_months", "/fields/units"),
        ("loans.overdue", "/fields/units"),
        ("cov:loans.member", ""),
        ("cov:loans.book", ""),
    }
    assert queue.fields == []
    assert queue.truncated == 0


def test_a_proposal_made_against_an_earlier_release_is_stale(store: Store, imported: str) -> None:
    number = propose_descriptor(store, "lib", proposal(), AGENT)
    opened = open_session(store, "lib", ADA)
    relabel = ChangeRequest.model_validate(
        {"edits": [{"op": "set", "descriptor": "books", "pointer": "/label", "value": "Books"}]}
    )
    draft = change(store, "lib", opened.handle, opened.draft, relabel, ADA)
    publish(store, "lib", opened.handle, draft, ADA)
    [listed] = curation_queue(store, "lib").proposals
    assert (listed.id, listed.stale, listed.release) == (number, True, imported)
    assert curation_queue(store, "lib", release=1).label == 1


def test_the_queue_is_capped_and_says_how_much_it_left_out(
    store: Store, imported: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    propose_descriptor(store, "lib", proposal(), AGENT)
    whole = curation_queue(store, "lib")
    items = len(whole.fields) + len(whole.undeclared) + len(whole.proposals) + len(whole.notes)
    monkeypatch.setattr(queue_module, "MAX_QUEUE_ITEMS", 3)
    queue = curation_queue(store, "lib")
    assert queue.undeclared == whole.undeclared[:3]
    assert (queue.proposals, queue.notes) == ([], [])
    assert queue.truncated == items - 3


def test_the_queue_of_a_withdrawn_release_is_refused(store: Store, imported: str) -> None:
    store.withdraw("lib", 1, ADA)
    with pytest.raises(StoreRefused) as refused:
        curation_queue(store, "lib", release=1)
    assert refused.value.refusal.code == "RELEASE_WITHDRAWN"


# --- What a proposal costs, and staleness -----------------------------------------------------


def test_a_repeated_proposal_costs_no_release_check(
    store: Store, imported: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = propose_descriptor(store, "lib", proposal(), AGENT)
    checked: list[int] = []
    real = queue_module.propose

    def counting(*args: Any, **kwargs: Any) -> Any:
        checked.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(queue_module, "propose", counting)
    assert propose_descriptor(store, "lib", proposal(), AGENT) == first
    assert checked == []


def test_the_cap_is_checked_before_the_release_check(
    store: Store, imported: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(queue_module, "MAX_OPEN_PROPOSALS", 1)
    propose_descriptor(store, "lib", proposal(), AGENT)

    def refusing(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("the release was checked")

    monkeypatch.setattr(queue_module, "propose", refusing)
    with pytest.raises(StoreRefused) as refused:
        propose_descriptor(store, "lib", proposal(value="Other"), AGENT)
    assert refused.value.refusal.code == "LIMIT_EXCEEDED"


def test_a_proposal_made_again_against_the_latest_release_is_no_longer_stale(
    store: Store, imported: str
) -> None:
    number = propose_descriptor(store, "lib", proposal(), AGENT)
    opened = open_session(store, "lib", ADA)
    relabel = ChangeRequest.model_validate(
        {"edits": [{"op": "set", "descriptor": "books", "pointer": "/label", "value": "Books"}]}
    )
    draft = change(store, "lib", opened.handle, opened.draft, relabel, ADA)
    publish(store, "lib", opened.handle, draft, ADA)
    assert curation_queue(store, "lib").proposals[0].stale
    assert propose_descriptor(store, "lib", proposal(), AGENT) == number
    [listed] = curation_queue(store, "lib").proposals
    assert (listed.id, listed.stale, listed.release) == (number, False, draft)


def test_a_decided_proposal_keeps_its_release(store: Store, imported: str) -> None:
    number = propose_descriptor(store, "lib", proposal(), AGENT)
    reject_proposal(store, "lib", number, ADA)
    with pytest.raises(Exception, match="keeps its release"), store.db.transaction() as db:
        db.execute("UPDATE proposals SET release = ? WHERE id = ?", ("sha256:" + "1" * 64, number))


def test_a_stored_proposal_s_repr_holds_no_value_or_evidence(store: Store, imported: str) -> None:
    propose_descriptor(store, "lib", proposal(value="Secret words", evidence="Hidden"), AGENT)
    [stored] = store.db.proposals("lib")
    assert "Secret words" not in repr(stored)
    assert "Hidden" not in repr(stored)


def test_a_time_offset_column_without_units_is_undeclared(store: Store, library: Any) -> None:
    tenure = build.column(
        "members.tenure",
        "time_offset",
        derived={"op": "date_diff", "from": "joined", "to": "joined", "units": "d"},
    )
    with store.pin() as pin:
        built = store.import_release(
            pin, "lib", library.descriptors(extra=[tenure]), library.sources(), library.layouts
        )
        store.publish("lib", built.manifest.hash, ADA)
    undeclared = {
        (item.descriptor, item.pointer) for item in curation_queue(store, "lib").undeclared
    }
    assert ("members.tenure", "/fields/units") in undeclared


# --- Accepted means held, and what a write reads ----------------------------------------------


def _set_definition(value: str) -> ChangeRequest:
    return ChangeRequest.model_validate(
        {
            "edits": [
                {"op": "set", "descriptor": "members", "pointer": "/definition", "value": value}
            ]
        }
    )


def test_a_proposal_the_draft_was_edited_away_from_is_not_accepted(
    store: Store, imported: str
) -> None:
    """D248: accepted means the release holds it; once a later edit overwrites the field, the
    queue no longer says it is accepted, rejecting it works, and publishing leaves it open."""
    number = propose_descriptor(store, "lib", proposal(), AGENT)
    opened = open_session(store, "lib", ADA)
    draft = change(store, "lib", opened.handle, opened.draft, accept(number), ADA)
    draft = change(store, "lib", opened.handle, draft, _set_definition("Other"), ADA)
    [listed] = curation_queue(store, "lib").proposals
    assert not listed.accepted_in_draft
    publish(store, "lib", opened.handle, draft, ADA)
    assert status(store, number) == "open"


def test_a_proposal_edited_away_from_can_be_rejected_or_accepted_again(
    store: Store, imported: str
) -> None:
    first = propose_descriptor(store, "lib", proposal(), AGENT)
    second = propose_descriptor(store, "lib", proposal(value="Readers"), AGENT)
    opened = open_session(store, "lib", ADA)
    draft = change(store, "lib", opened.handle, opened.draft, accept(first), ADA)
    draft = change(store, "lib", opened.handle, draft, accept(second), ADA)
    reject_proposal(store, "lib", first, ADA)
    assert status(store, first) == "rejected"
    draft = change(store, "lib", opened.handle, draft, _set_definition("Other"), ADA)
    draft = change(store, "lib", opened.handle, draft, accept(second), ADA)
    [listed] = curation_queue(store, "lib").proposals
    assert (listed.id, listed.accepted_in_draft) == (second, True)
    with pytest.raises(StoreRefused) as refused:
        reject_proposal(store, "lib", second, ADA)
    assert refused.value.refusal.code == "CONFLICT"
    assert "change the draft away from the proposal" in refused.value.refusal.model_dump_json()
    publish(store, "lib", opened.handle, draft, ADA)
    assert (status(store, first), status(store, second)) == ("rejected", "accepted")


def test_a_whole_descriptor_proposal_edited_after_acceptance_stays_open(
    store: Store, imported: str
) -> None:
    coverage = {
        "kind": "coverage",
        "id": "cov:loans.member",
        "label": "Loans of members",
        "fields": {"relationship": "rel:loans.member", "parents": "all"},
    }
    given = proposal(descriptor="cov:loans.member", pointer="", value=coverage)
    kept = propose_descriptor(store, "lib", given, AGENT)
    opened = open_session(store, "lib", ADA)
    draft = change(store, "lib", opened.handle, opened.draft, accept(kept), ADA)
    [listed] = curation_queue(store, "lib").proposals
    assert listed.accepted_in_draft
    relabel = {"op": "set", "descriptor": "cov:loans.member", "pointer": "/label", "value": "L"}
    edited = ChangeRequest.model_validate({"edits": [relabel]})
    draft = change(store, "lib", opened.handle, draft, edited, ADA)
    publish(store, "lib", opened.handle, draft, ADA)
    assert status(store, kept) == "open"


def test_a_change_reads_only_the_proposals_it_accepts(
    store: Store, imported: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Parsing every open proposal on each change would read up to 10,000 of 64 KiB."""
    numbers = [
        propose_descriptor(store, "lib", proposal(value=f"People {i}"), AGENT) for i in range(3)
    ]
    opened = open_session(store, "lib", ADA)
    read: list[list[int] | None] = []
    real = store.db.proposals

    def recording(dataset: str, **given: Any) -> Any:
        read.append(given.get("ids"))
        return real(dataset, **given)

    monkeypatch.setattr(store.db, "proposals", recording)
    draft = change(store, "lib", opened.handle, opened.draft, _set_definition("Other"), ADA)
    change(store, "lib", opened.handle, draft, accept(numbers[1]), ADA)
    assert None not in read
    assert {number for ids in read if ids for number in ids} == {numbers[1]}


def test_a_proposal_runs_the_pack_checks_on_the_descriptor_it_changes_only(
    store: Store, imported: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Validating every extension of the release on each proposal would make one large extension
    value slow every later write; a dataset-descriptor proposal checks every extension against
    its ``packs``, which needs no schema."""
    seen: list[Any] = []
    real = queue_module.check_writes

    def recording(descriptors: Any, registry: Any, **given: Any) -> Any:
        assert "ceiling" not in given  # a proposal's, ``STEPS_MAX``
        seen.append(given.get("only"))
        return real(descriptors, registry, **given)

    monkeypatch.setattr(queue_module, "check_writes", recording)
    propose_descriptor(store, "lib", proposal(), AGENT)
    dataset = proposal(descriptor="dataset", pointer="/definition", value="Books")
    propose_descriptor(store, "lib", dataset, AGENT)
    assert seen == [{"members"}, {"dataset"}]


def test_a_change_cannot_accept_again_a_proposal_its_draft_holds(
    store: Store, imported: str
) -> None:
    number = propose_descriptor(store, "lib", proposal(), AGENT)
    opened = open_session(store, "lib", ADA)
    draft = change(store, "lib", opened.handle, opened.draft, accept(number), ADA)
    with pytest.raises(EditRefused) as refused:
        change(store, "lib", opened.handle, draft, accept(number), ADA)
    assert [(r.code, r.path) for r in refused.value.refusals] == [
        ("UNKNOWN_PROPOSAL", "/edits/0/proposal")
    ]


def test_a_change_runs_the_pack_checks_on_what_it_changes_and_publish_on_everything(
    store: Store, imported: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An operator's write evaluates extensions within a write's step ceiling, above a
    proposal's (D247)."""
    seen: list[Any] = []
    real = sessions_module.check_writes

    def recording(descriptors: Any, registry: Any, **given: Any) -> Any:
        seen.append((given.get("only"), given.get("ceiling")))
        return real(descriptors, registry, **given)

    monkeypatch.setattr(sessions_module, "check_writes", recording)
    opened = open_session(store, "lib", ADA)
    draft = change(store, "lib", opened.handle, opened.draft, _set_definition("Readers"), ADA)
    publish(store, "lib", opened.handle, draft, ADA)
    assert seen == [({"members"}, WRITE_STEPS_MAX), (None, WRITE_STEPS_MAX)]


# --- The queue under concurrent changes, and its size ------------------------------------------


def test_the_queue_pins_the_draft_it_read_before_a_change_can_sweep_it(
    store: Store, imported: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A change committed between reading the open session and pinning its draft would sweep
    the draft the queue is about to read."""
    number = propose_descriptor(store, "lib", proposal(), AGENT)
    opened = open_session(store, "lib", ADA)
    draft = change(store, "lib", opened.handle, opened.draft, accept(number), ADA)
    real = store.db.open_session_of
    racing: list[threading.Thread] = []

    def committing(dataset: str) -> Any:
        found = real(dataset)
        if not racing:
            racing.append(
                threading.Thread(
                    target=change,
                    args=(store, "lib", opened.handle, draft, _set_definition("Other"), ADA),
                )
            )
            racing[0].start()
            racing[0].join(timeout=1)
        return found

    monkeypatch.setattr(store.db, "open_session_of", committing)
    queue = curation_queue(store, "lib")
    racing[0].join()
    assert [(p.id, p.accepted_in_draft) for p in queue.proposals] == [(number, True)]
    assert store.resolve("lib", "draft").manifest != draft


def test_the_queue_pins_the_release_it_resolved_before_a_withdrawal_can_sweep_it(
    store: Store, imported: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    opened = open_session(store, "lib", ADA)
    draft = change(store, "lib", opened.handle, opened.draft, _set_definition("Readers"), ADA)
    publish(store, "lib", opened.handle, draft, ADA)
    real = store.resolve
    racing: list[threading.Thread] = []

    def withdrawing(dataset: str, pin: Any = None) -> Any:
        found = real(dataset, pin)
        if not racing:
            racing.append(threading.Thread(target=store.withdraw, args=("lib", 1, ADA)))
            racing[0].start()
            racing[0].join(timeout=1)
        return found

    monkeypatch.setattr(store, "resolve", withdrawing)
    queue = curation_queue(store, "lib", release=1)
    racing[0].join()
    assert (queue.label, queue.release) == (1, imported)
    assert real("lib", 1).status == "withdrawn"


def _size(*items: Any) -> int:
    return sum(len(item.model_dump_json().encode()) for item in items)


def test_the_queue_holds_at_most_its_bytes_and_counts_what_it_left_out(
    store: Store, imported: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D250: 10,000 open proposals of 64 KiB and their evidence would make a queue of about a
    gigabyte; it stops at ``MAX_QUEUE_BYTES`` and reads no proposal past the first left out."""
    numbers = [
        propose_descriptor(store, "lib", proposal(value=f"People {i}"), AGENT) for i in range(5)
    ]
    whole = curation_queue(store, "lib")
    fits = _size(*whole.fields, *whole.undeclared, whole.proposals[0])
    monkeypatch.setattr(queue_module, "MAX_QUEUE_BYTES", fits)
    monkeypatch.setattr(queue_module, "_PAGE", 1)
    read: list[int] = []
    real = store.db.proposals

    def recording(dataset: str, **given: Any) -> Any:
        read.append(given.get("after", 0))
        return real(dataset, **given)

    monkeypatch.setattr(store.db, "proposals", recording)
    queue = curation_queue(store, "lib")
    assert (queue.undeclared, [p.id for p in queue.proposals]) == (whole.undeclared, numbers[:1])
    assert queue.notes == []
    assert queue.truncated == 4 + len(whole.notes)
    assert read == [0, numbers[0]]
    monkeypatch.setattr(queue_module, "MAX_QUEUE_BYTES", fits - 1)
    queue = curation_queue(store, "lib")
    assert (queue.proposals, queue.truncated) == ([], 5 + len(whole.notes))

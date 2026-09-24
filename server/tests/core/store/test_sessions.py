"""Curation sessions (SPEC §12.3, §13.2, D243–D246, D251, D252): one open session per dataset,
handles and expected drafts, the structural checks on every change, publish and discard, the
states a draft took, and the audit trail."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from aibi.core.schema.curation import ChangeRequest, ProposalInput
from aibi.core.store import sessions as sessions_module
from aibi.core.store.edits import EditRefused
from aibi.core.store.proposals import propose_descriptor
from aibi.core.store.sessions import (
    HANDLE_PREFIX,
    Opened,
    change,
    discard,
    open_session,
    publish,
    take_over,
)
from aibi.core.store.store import Store, StoreRefused

Library = Any
ADA = "operator:ada"


def request(*edits: dict[str, Any]) -> ChangeRequest:
    return ChangeRequest.model_validate({"edits": list(edits)})


def relabel(identifier: str = "members", label: str = "Library members") -> ChangeRequest:
    return request({"op": "set", "descriptor": identifier, "pointer": "/label", "value": label})


def refused(call: Any, *args: Any, **kwargs: Any) -> str:
    with pytest.raises(StoreRefused) as found:
        call(*args, **kwargs)
    return str(found.value.refusal.code)


def change_refusals(store: Store, opened: Opened, *edits: dict[str, Any]) -> list[Any]:
    with pytest.raises(EditRefused) as found:
        change(store, "lib", opened.handle, opened.draft, request(*edits), ADA)
    return list(found.value.refusals)


def test_opening_a_session_makes_a_draft_of_the_latest_release(store: Store, imported: str) -> None:
    opened = open_session(store, "lib", ADA)
    assert (opened.base, opened.draft) == (imported, imported)
    assert opened.handle.startswith(HANDLE_PREFIX)
    assert len(opened.handle) == 4 + 43
    assert opened.handle not in repr(opened)
    assert store.resolve("lib", "draft").manifest == imported


def test_a_second_session_is_refused_while_one_is_open(store: Store, imported: str) -> None:
    open_session(store, "lib", ADA)
    assert refused(open_session, store, "lib", "operator:grace") == "DATASET_BUSY"


def test_a_session_needs_a_published_release_and_an_operator(store: Store) -> None:
    assert refused(open_session, store, "lib", ADA) == "UNKNOWN_RELEASE"
    assert refused(open_session, store, "lib", "agent:helper") == "INVALID_VALUE"


def test_a_stale_handle_is_refused_after_a_takeover_and_the_new_one_works(
    store: Store, imported: str
) -> None:
    first = open_session(store, "lib", ADA)
    second = take_over(store, "lib", "operator:grace")
    assert (second.session, second.draft) == (first.session, first.draft)
    stale = refused(change, store, "lib", first.handle, first.draft, relabel(), ADA)
    assert stale == "CONFLICT"
    draft = change(store, "lib", second.handle, second.draft, relabel(), "operator:grace")
    assert store.resolve("lib", "draft").manifest == draft


def test_a_stale_expected_draft_is_refused(store: Store, imported: str) -> None:
    opened = open_session(store, "lib", ADA)
    draft = change(store, "lib", opened.handle, opened.draft, relabel(), ADA)
    again = refused(change, store, "lib", opened.handle, opened.draft, relabel(label="x"), ADA)
    assert again == "CONFLICT"
    assert refused(discard, store, "lib", opened.handle, opened.draft, ADA) == "CONFLICT"
    assert refused(publish, store, "lib", "ses_wrong", draft, ADA) == "CONFLICT"


def test_session_operations_need_an_open_session(store: Store, imported: str) -> None:
    assert refused(change, store, "lib", "ses_x", imported, relabel(), ADA) == "NO_SESSION"
    assert refused(take_over, store, "lib", ADA) == "NO_SESSION"


def test_an_unchanged_publish_is_refused(store: Store, imported: str) -> None:
    opened = open_session(store, "lib", ADA)
    assert refused(publish, store, "lib", opened.handle, opened.draft, ADA) == "NO_CHANGE"


def test_publishing_labels_the_draft_and_ends_the_session(store: Store, imported: str) -> None:
    opened = open_session(store, "lib", ADA)
    draft = change(store, "lib", opened.handle, opened.draft, relabel(), ADA)
    assert publish(store, "lib", opened.handle, draft, ADA).label == 2
    assert store.resolve("lib").manifest == draft
    assert not any(session.open for session in store.db.sessions("lib"))
    members = next(d for d in store.descriptors(draft) if d.id == "members")
    assert (members.label, members.version) == ("Library members", 2)
    assert members.curation["/label"].by == ADA


def test_a_base_that_is_no_longer_the_latest_makes_publish_a_conflict(
    store: Store, library: Library, imported: str
) -> None:
    opened = open_session(store, "lib", ADA)
    draft = change(store, "lib", opened.handle, opened.draft, relabel(), ADA)
    with store.pin() as pin:
        built = store.import_release(
            pin,
            "lib",
            library.descriptors(),
            library.sources(loans=library.loans[:2]),
            library.layouts,
        )
        store.publish("lib", built.manifest.hash, ADA)
    assert refused(publish, store, "lib", opened.handle, draft, ADA) == "CONFLICT"


def test_discarding_ends_the_session_and_sweeps_the_draft_s_own_blobs(
    store: Store, imported: str, library: Library
) -> None:
    opened = open_session(store, "lib", ADA)
    missing = request(
        {
            "op": "set",
            "descriptor": "members.age",
            "pointer": "/fields/missing_codes",
            "value": {"NA": "UNKNOWN"},
        }
    )
    draft = change(store, "lib", opened.handle, opened.draft, missing, ADA)
    own = store.manifest(draft).blobs() - store.manifest(imported).blobs()
    assert own
    assert all(store.blobs.exists(blob) for blob in own)
    discard(store, "lib", opened.handle, draft, ADA)
    assert not any(store.blobs.exists(blob) for blob in own)
    assert store.resolve("lib", draft).status == "discarded"
    assert store.resolve("lib").manifest == imported


def test_earlier_draft_states_resolve_to_discarded_and_draft_only_while_open(
    store: Store, imported: str
) -> None:
    opened = open_session(store, "lib", ADA)
    first = change(store, "lib", opened.handle, opened.draft, relabel(), ADA)
    second = change(store, "lib", opened.handle, first, relabel(label="Members"), ADA)
    assert store.resolve("lib", first).status == "discarded"
    assert store.resolve("lib", second).status == "draft"
    assert store.resolve("lib").manifest == imported
    assert store.resolve("lib", 1).status == "published"
    publish(store, "lib", opened.handle, second, ADA)
    assert store.resolve("lib", second).status == "published"
    with pytest.raises(StoreRefused):
        store.resolve("lib", "draft")


def test_a_draft_edited_back_to_a_withdrawn_release_is_never_published_again(
    tmp_path: Path, library: Library
) -> None:
    store = Store(tmp_path / "data", clock=lambda: datetime(2026, 1, 1, tzinfo=UTC))
    try:
        with store.pin() as pin:
            built = store.import_release(
                pin, "lib", library.descriptors(), library.sources(), library.layouts
            )
            store.publish("lib", built.manifest.hash, ADA)
        opened = open_session(store, "lib", ADA)
        draft = change(store, "lib", opened.handle, opened.draft, relabel(), ADA)
        publish(store, "lib", opened.handle, draft, ADA)
        store.withdraw("lib", 2, ADA)
        again = open_session(store, "lib", ADA)
        same = change(store, "lib", again.handle, again.draft, relabel(), ADA)
        assert same == draft
        assert refused(publish, store, "lib", again.handle, same, ADA) == "RELEASE_WITHDRAWN"
    finally:
        store.close()


def test_the_audit_trail_records_every_step_with_the_operator_and_no_handle(
    store: Store, imported: str
) -> None:
    opened = open_session(store, "lib", ADA)
    draft = change(store, "lib", opened.handle, opened.draft, relabel(), ADA)
    taken = take_over(store, "lib", "operator:grace")
    publish(store, "lib", taken.handle, draft, "operator:grace")
    again = open_session(store, "lib", ADA)
    discard(store, "lib", again.handle, again.draft, ADA)
    rows = store.db.connection.execute(
        "SELECT action, actor, session, detail FROM audit WHERE dataset = 'lib' ORDER BY id"
    ).fetchall()
    steps = [(action, actor, session is not None) for action, actor, session, _ in rows]
    assert steps == [
        ("publish", ADA, False),
        ("open", ADA, True),
        ("change", ADA, True),
        ("take_over", "operator:grace", True),
        ("publish", "operator:grace", True),
        ("open", ADA, True),
        ("discard", ADA, True),
    ]
    details = " ".join(detail for *_, detail in rows)
    for handle in (opened.handle, taken.handle, again.handle):
        assert handle not in details


def test_the_app_db_holds_only_the_handle_s_hash(store: Store, imported: str) -> None:
    opened = open_session(store, "lib", ADA)
    change(store, "lib", opened.handle, opened.draft, relabel(), ADA)
    store.db.checkpoint()
    for path in store.root.iterdir():
        if path.is_file():
            assert opened.handle.encode() not in path.read_bytes(), path.name
    [session] = store.db.sessions("lib")
    assert len(session.handle_hash) == 64
    assert session.handle_hash not in repr(session)


# --- The structural checks on every change (§13.2, D246) ---------------------------------------

STRUCTURAL = {
    "a key that repeats": (
        {
            "op": "set",
            "descriptor": "loans",
            "pointer": "/fields/primary_key",
            "value": ["member_id"],
        },
        ("KEY_NOT_UNIQUE", "/draft/loans/fields/primary_key"),
    ),
    "a missing code that nulls a key cell": (
        {
            "op": "set",
            "descriptor": "members.member_id",
            "pointer": "/fields/missing_codes",
            "value": {"m-4": "UNKNOWN"},
        },
        ("KEY_NULL", "/draft/members/fields/primary_key"),
    ),
    "a relationship with dangling rows": (
        {
            "op": "put",
            "descriptor": {
                "kind": "relationship",
                "id": "rel:loans.wrong",
                "label": "Wrong",
                "fields": {
                    "child_table": "loans",
                    "child_columns": ["member_id"],
                    "parent_table": "books",
                    "parent_columns": ["book_id"],
                    "cardinality": "many-to-one",
                    "role": "wrong",
                },
            },
        },
        ("DANGLING_REFERENCE", "/draft/rel:loans.wrong/fields/child_columns"),
    ),
    "a one-to-one relationship whose parent has two children": (
        {
            "op": "set",
            "descriptor": "rel:loans.member",
            "pointer": "/fields/cardinality",
            "value": "one-to-one",
        },
        ("CARDINALITY_VIOLATED", "/draft/rel:loans.member/fields/cardinality"),
    ),
}


@pytest.mark.parametrize("name", sorted(STRUCTURAL))
def test_a_structural_error_refuses_a_change_with_counts(
    store: Store, imported: str, name: str
) -> None:
    edit, (code, path) = STRUCTURAL[name]
    opened = open_session(store, "lib", ADA)
    before = store.db.connection.execute("SELECT count(*) FROM audit").fetchone()
    [refusal] = change_refusals(store, opened, edit)
    assert (refusal.code, refusal.path) == (code, path)
    assert "row" in refusal.message[0].model_dump()["text"]
    assert store.resolve("lib", "draft").manifest == imported
    assert store.db.connection.execute("SELECT count(*) FROM audit").fetchone() == before


def test_a_record_filter_violation_refuses_a_change(store: Store, imported: str) -> None:
    opened = open_session(store, "lib", ADA)
    coverage = {
        "kind": "coverage",
        "id": "cov:loans.member",
        "label": "Loans of members",
        "fields": {"relationship": "rel:loans.member", "record_filter": {"member_id": ["m-1"]}},
    }
    refusals = change_refusals(
        store,
        opened,
        {
            "op": "set",
            "descriptor": "loans.member_id",
            "pointer": "/fields/datatype",
            "value": "category",
        },
        {"op": "put", "descriptor": coverage},
    )
    assert [(r.code, r.path) for r in refusals] == [
        ("OUTSIDE_RECORD_FILTER", "/draft/cov:loans.member/fields/record_filter/member_id")
    ]


def test_model_and_release_problems_refuse_at_the_draft_s_descriptor(
    store: Store, imported: str
) -> None:
    opened = open_session(store, "lib", ADA)
    [model] = change_refusals(
        store,
        opened,
        {"op": "set", "descriptor": "members", "pointer": "/fields/role", "value": "person"},
    )
    assert model.path == "/draft/members/fields/role"
    [release] = change_refusals(
        store,
        opened,
        {"op": "set", "descriptor": "loans", "pointer": "/fields/primary_key", "value": ["nope"]},
    )
    assert (release.code, release.path) == (
        "UNKNOWN_DESCRIPTOR",
        "/draft/loans/fields/primary_key/0",
    )


def test_a_missing_code_change_rebuilds_only_its_table_and_a_label_change_none(
    store: Store, imported: str
) -> None:
    opened = open_session(store, "lib", ADA)
    codes = request(
        {
            "op": "set",
            "descriptor": "members.age",
            "pointer": "/fields/missing_codes",
            "value": {"NA": "UNKNOWN"},
        }
    )

    def tables(manifest: str) -> dict[str, str]:
        return {entry.id: entry.hash for entry in store.manifest(manifest).tables}

    first = change(store, "lib", opened.handle, opened.draft, codes, ADA)
    base, rebuilt = tables(imported), tables(first)
    second = change(store, "lib", opened.handle, first, relabel(), ADA)
    relabelled = tables(second)
    assert {t for t in base if base[t] != rebuilt[t]} == {"members"}
    assert relabelled == rebuilt


def test_a_delimiter_change_that_gives_other_columns_is_a_re_import(
    store: Store, imported: str
) -> None:
    opened = open_session(store, "lib", ADA)
    members = next(d for d in store.descriptors(imported) if d.id == "members")
    source = members.model_dump(mode="json")["fields"]["source"]
    source["parse"]["delimiter"] = "|"
    [refusal] = change_refusals(
        store,
        opened,
        {"op": "set", "descriptor": "members", "pointer": "/fields/source", "value": source},
    )
    assert (refusal.code, refusal.path) == ("COLUMNS_CHANGED", "/draft/members/fields/source/parse")


def test_a_table_a_pack_reshaped_is_not_rebuilt_in_a_draft(store: Store, imported: str) -> None:
    opened = open_session(store, "lib", ADA)
    reshaped = {"kind": "pack", "name": "loans", "original_name": "Loans"}
    [refusal] = change_refusals(
        store,
        opened,
        {"op": "set", "descriptor": "loans", "pointer": "/fields/source", "value": reshaped},
        {
            "op": "set",
            "descriptor": "loans.days",
            "pointer": "/fields/missing_codes",
            "value": {"-1": "UNKNOWN"},
        },
    )
    assert (refusal.code, refusal.path) == ("NOT_SUPPORTED", "/draft/loans")
    relabelled = change(
        store,
        "lib",
        opened.handle,
        opened.draft,
        request(
            {"op": "set", "descriptor": "loans", "pointer": "/fields/source", "value": reshaped}
        ),
        ADA,
    )
    assert store.resolve("lib", "draft").manifest == relabelled


def test_versions_count_per_release_not_per_change(store: Store, imported: str) -> None:
    before = int(next(d for d in store.descriptors(imported) if d.id == "members").version)
    opened = open_session(store, "lib", ADA)
    first = change(store, "lib", opened.handle, opened.draft, relabel(label="One"), ADA)
    second = change(store, "lib", opened.handle, first, relabel(label="Two"), ADA)
    members = next(d for d in store.descriptors(second) if d.id == "members")
    assert members.version == before + 1


def test_a_proposal_decided_while_a_change_accepts_it_is_a_conflict(
    store: Store, imported: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    given = {"descriptor": "members", "pointer": "/definition", "value": "People"}
    number = propose_descriptor(store, "lib", ProposalInput.model_validate(given), "agent:x")
    opened = open_session(store, "lib", ADA)
    real = sessions_module.apply

    def racing(*args: Any, **kwargs: Any) -> Any:
        found = real(*args, **kwargs)
        with store.db.transaction() as db:
            store.db.decide(db, number, "rejected", store.now(), ADA)
        return found

    monkeypatch.setattr(sessions_module, "apply", racing)
    accept = request({"op": "accept", "proposal": number})
    assert refused(change, store, "lib", opened.handle, opened.draft, accept, ADA) == "CONFLICT"

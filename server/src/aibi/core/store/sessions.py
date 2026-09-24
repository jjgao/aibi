"""Curation sessions (SPEC §11.2, §12.3, D236, D243–D246, D251, D252).

A dataset has at most one open session. Opening one makes a draft that starts as the latest
published release's manifest, and returns a **handle**: ``ses_`` and 43 base64url characters from
``secrets``, returned once. Only its SHA-256 is stored, and it is compared in constant time; a
handle never appears in the app DB, the audit trail, a refusal or a ``repr``. Every change,
publish and discard carries the handle and the draft manifest hash it expects: a handle that is
not the current one (wrong, or replaced by a takeover), or a hash other than the draft's, is a
``CONFLICT``, and no open session is ``NO_SESSION``. Taking over needs no handle and issues a new
one, invalidating the old.

A **change** applies its edits (``edits``, D245), sets versions against the session's base (D243),
and checks the result in this order, the first stage that fails refusing it with all its refusals
(D246): the descriptors' models; ``check_release``; the pack checks (``writes.check_writes``, within
the ceiling of an operator's write) on the descriptors it adds or changes; the build, which rebuilds
only the tables whose parsing changed and refuses other source columns; the validation gate in
change mode over every table; and the validators of the dataset's packs (``validate_descriptors``)
on a view labelled ``"draft"``. Refusal paths point at ``/draft/<descriptor id>/…``. A change reads
only the proposals its ``accept`` edits name, and one the draft accepted and still holds cannot be
accepted again. The new draft, its state in ``drafts``, the proposals it accepted and the audit
entry are then written in one transaction, and the previous draft's blobs are swept once its pin is
released.

**Publish** is refused as a ``CONFLICT`` when the session's base is no longer the latest published
release, and ``NO_CHANGE`` when the draft equals it. It then runs the pack checks on every
descriptor of the draft, and the validators of the dataset's packs, against the registry it is
given, refused as a change is: a change checks only what it touches, and a pack removed or upgraded
since a descriptor was written would otherwise publish what the running registry refuses (D247).
Otherwise one transaction labels the draft, ends the session and marks ``accepted`` the proposals it
accepted and still holds (D248); the curation proposers of the dataset's packs then run, and what
they proposed or skipped is returned with the label (D249). **Discard** ends the session in one
transaction, leaving its accepted proposals open. Both then sweep. Every operation holds the
dataset's operation slot, and the operator's name is the audit trail's actor (Q7).
"""

import hashlib
import hmac
import secrets
from dataclasses import dataclass, field

from pydantic import JsonValue

from aibi.core.schema.curation import Accept, ChangeRequest
from aibi.core.schema.descriptors import Descriptor
from aibi.core.schema.jsonschemas import WRITE_STEPS_MAX
from aibi.core.schema.pack_api import PackRegistry
from aibi.core.schema.refusals import RefusalCode
from aibi.core.schema.release import check_release
from aibi.core.store.appdb import Session
from aibi.core.store.build import BuildRefused
from aibi.core.store.edits import EditRefused, apply
from aibi.core.store.proposals import ProposersRun, holding, run_proposers
from aibi.core.store.store import Store, StoreRefused
from aibi.core.store.writes import (
    attributed,
    by_id,
    changed,
    check_writes,
    packs_of,
    rooted,
    validators,
    versions,
    view,
)

HANDLE_PREFIX = "ses_"


@dataclass(frozen=True)
class SessionPublished:
    label: int
    proposers: ProposersRun | None = None
    """What the curation proposers proposed on the release, given a registry (D249)."""


@dataclass(frozen=True)
class Opened:
    session: int
    handle: str = field(repr=False)
    """Returned once; only its hash is stored."""
    base: str
    draft: str


def _hash(handle: str) -> str:
    return hashlib.sha256(handle.encode("utf-8", "surrogatepass")).hexdigest()


def _new_handle() -> tuple[str, str]:
    handle = HANDLE_PREFIX + secrets.token_urlsafe(32)
    return handle, _hash(handle)


def operator(by: str) -> str:
    """``by`` if it is an operator's attribution (§5.1); refused otherwise."""
    if not attributed(by, ("operator",)):
        raise StoreRefused(
            RefusalCode.INVALID_VALUE,
            "Curation is an operator's: the actor is operator:<name>, as the server attributes it",
        )
    return by


def _open(store: Store, dataset: str) -> Session:
    session = store.db.open_session_of(dataset)
    if session is None:
        raise StoreRefused(RefusalCode.NO_SESSION, "No curation session is open on the dataset")
    return session


def _checked(store: Store, dataset: str, handle: str, expected: str) -> Session:
    session = _open(store, dataset)
    if not hmac.compare_digest(_hash(handle), session.handle_hash):
        raise StoreRefused(
            RefusalCode.CONFLICT,
            "The session handle is not the current one: the session was taken over, or the "
            "handle is wrong",
        )
    if expected != session.draft:
        raise StoreRefused(
            RefusalCode.CONFLICT,
            "The draft is not at the state expected: another change came first",
        )
    return session


def open_session(store: Store, dataset: str, by: str) -> Opened:
    """Open a curation session on the dataset's latest published release."""
    operator(by)
    with store.exclusive(dataset, "session"):
        if store.db.open_session_of(dataset) is not None:
            raise StoreRefused(
                RefusalCode.DATASET_BUSY,
                "A curation session is open on the dataset: take it over, publish or discard it",
            )
        latest = store.latest(dataset)
        if latest is None:
            raise StoreRefused(RefusalCode.UNKNOWN_RELEASE, "The dataset has no published release")
        handle, hashed = _new_handle()
        at = store.now()
        with store.db.transaction() as db:
            session = store.db.open_session(db, dataset, hashed, latest.manifest, at, by)
            store.db.record_draft(db, session, latest.manifest, at)
            detail: JsonValue = {
                "label": latest.label,
                "base": latest.manifest,
                "draft": latest.manifest,
            }
            store.db.audit(db, at, dataset, by, "open", detail, session)
    return Opened(session, handle, latest.manifest, latest.manifest)


def change(
    store: Store,
    dataset: str,
    handle: str,
    expected: str,
    request: ChangeRequest,
    by: str,
    *,
    registry: PackRegistry | None = None,
) -> str:
    """Apply a change to the draft; returns the new draft's manifest hash. Raises
    ``StoreRefused`` or ``EditRefused``."""
    operator(by)
    with store.exclusive(dataset, "session"):
        session = _checked(store, dataset, handle, expected)
        with store.pin() as pin:
            pin.manifest(session.draft)
            before = store.descriptors(session.draft)
            named = sorted({e.proposal for e in request.edits if isinstance(e, Accept)})
            held = holding(store, session, before, ids=named)
            proposals = {
                p.id: p for p in store.db.proposals(dataset, ids=named) if p.id not in held
            }
            applied = apply(
                before,
                store.tombstones(session.draft),
                request.edits,
                by=by,
                at=store.now(),
                proposals=proposals,
            )
            pin.manifest(session.base)
            descriptors = versions(
                store.descriptors(session.base), applied.descriptors, store.tombstones(session.base)
            )
            only = changed(before, descriptors)
            checked = (
                check_release(descriptors),
                check_writes(descriptors, registry, only=only, ceiling=WRITE_STEPS_MAX),
            )
            for refusals in checked:
                if refusals:
                    raise EditRefused(by_id(refusals, descriptors, "draft"))
            try:
                built = store.change_release(
                    pin, session.draft, descriptors, tombstones=applied.tombstones
                )
            except BuildRefused as error:
                raise EditRefused(by_id(error.refusals, descriptors, "draft")) from None
            draft = built.manifest.hash
            released = view(dataset, draft, "draft", descriptors)
            refusals = [
                refusal
                for validator in validators(registry, packs_of(descriptors))
                for refusal in validator.validate_descriptors(released)
            ]
            if refusals:
                raise EditRefused(rooted(refusals, "draft"))
            at = store.now()
            with store.db.transaction() as db:
                accepted = store.db.proposals(dataset, ids=list(applied.accepted))
                if len(accepted) != len(applied.accepted):
                    raise StoreRefused(
                        RefusalCode.CONFLICT,
                        "A proposal the change accepts was decided meanwhile",
                    )
                store.db.set_draft(db, session.id, draft)
                store.db.record_draft(db, session.id, draft, at)
                for proposal in applied.accepted:
                    store.db.decide_in_session(db, session.id, proposal)
                detail: JsonValue = {
                    "base": session.base,
                    "previous": session.draft,
                    "draft": draft,
                    "edits": [[identifier, written] for identifier, written in applied.touched],
                    "proposals": list(applied.accepted),
                }
                store.db.audit(db, at, dataset, by, "change", detail, session.id)
    return draft


def _registered(
    dataset: str, draft: str, descriptors: tuple[Descriptor, ...], registry: PackRegistry | None
) -> None:
    """Refuse a draft that the running registry refuses: the pack checks on every descriptor and
    the validators of the dataset's packs, as a change runs them (D246, D247)."""
    refusals = check_writes(descriptors, registry, ceiling=WRITE_STEPS_MAX)
    if refusals:
        raise EditRefused(by_id(refusals, descriptors, "draft"))
    released = view(dataset, draft, "draft", descriptors)
    found = [
        refusal
        for validator in validators(registry, packs_of(descriptors))
        for refusal in validator.validate_descriptors(released)
    ]
    if found:
        raise EditRefused(rooted(found, "draft"))


def publish(
    store: Store,
    dataset: str,
    handle: str,
    expected: str,
    by: str,
    *,
    registry: PackRegistry | None = None,
) -> SessionPublished:
    """Publish the draft as the dataset's next label, and end the session; raises
    ``StoreRefused`` or ``EditRefused``. The curation proposers of the dataset's packs then run on
    it, given a registry, and what they did is returned with the label (D249)."""
    operator(by)
    with store.exclusive(dataset, "session"):
        session = _checked(store, dataset, handle, expected)
        latest = store.latest(dataset)
        if latest is None or latest.manifest != session.base:
            raise StoreRefused(
                RefusalCode.CONFLICT,
                "The release the session was opened from is no longer the latest published one",
            )
        if session.draft == latest.manifest:
            raise StoreRefused(
                RefusalCode.NO_CHANGE, "The draft equals the latest published release"
            )
        with store.pin() as pin:
            pin.manifest(session.draft)
            descriptors = store.descriptors(session.draft)
            _registered(dataset, session.draft, descriptors, registry)
            accepted = holding(store, session, descriptors)
        at = store.now()
        with store.db.transaction() as db:
            detail: dict[str, JsonValue] = {"base": session.base, "draft": session.draft}
            label = store.commit_label(
                db, dataset, session.draft, by, "publish", detail, session.id
            )
            store.db.end_session(db, session.id, "published", at)
            for proposal in sorted(accepted):
                store.db.decide(db, proposal, "accepted", at, by)
        store.sweep()
    proposers = None if registry is None else run_proposers(store, dataset, registry)
    return SessionPublished(label, proposers)


def discard(store: Store, dataset: str, handle: str, expected: str, by: str) -> None:
    """End the session without a release; the proposals its draft accepted stay open."""
    operator(by)
    with store.exclusive(dataset, "session"):
        session = _checked(store, dataset, handle, expected)
        at = store.now()
        with store.db.transaction() as db:
            store.db.end_session(db, session.id, "discarded", at)
            detail: JsonValue = {"base": session.base, "draft": session.draft}
            store.db.audit(db, at, dataset, by, "discard", detail, session.id)
        store.sweep()


def take_over(store: Store, dataset: str, by: str) -> Opened:
    """Issue a new handle for the open session, invalidating the old one; the draft is kept."""
    operator(by)
    with store.exclusive(dataset, "session"):
        session = _open(store, dataset)
        handle, hashed = _new_handle()
        with store.db.transaction() as db:
            store.db.set_handle(db, session.id, hashed)
            detail: JsonValue = {"base": session.base, "draft": session.draft}
            store.db.audit(db, store.now(), dataset, by, "take_over", detail, session.id)
    return Opened(session.id, handle, session.base, session.draft)


__all__ = [
    "HANDLE_PREFIX",
    "Opened",
    "SessionPublished",
    "change",
    "discard",
    "open_session",
    "operator",
    "publish",
    "take_over",
]

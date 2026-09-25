"""The proposal queue and the curation queue (SPEC §10.1, §11.1, §12.3, §14, D248–D250).

**Proposals.** ``propose_descriptor`` records a proposal against the latest published release: a
value for a curated field, or at the pointer ``""`` a whole descriptor, or its removal, with
evidence of at most 10,000 characters; a value has at most ``MAX_PROPOSAL_BYTES`` in RFC 8785 form
(``LIMIT_EXCEEDED``, ``proposal_bytes``), less than a descriptor field can hold (a long
``permissible_values`` list), which an operator's session or an importer writes instead (D248).
``by`` is the server's attribution and must be a model's, an agent's or an importer's (§5.1): an
operator edits a draft instead. The proposal is checked by applying it, as ``proposed``, to the
latest release's descriptors (their models, ``check_release`` and the pack checks; no data is read),
refused with those refusals, paths at ``/descriptors/<id>/…``. It changes no release. An open
proposal that is the same (descriptor, pointer, value, proposer and evidence) is returned rather
than recorded twice, and then counts as made against the latest release; a dataset has at most
``MAX_OPEN_PROPOSALS`` open (``LIMIT_EXCEEDED``). Both are checked before the proposal is applied,
so that repeats and spam cost no release check; the pack checks evaluate the descriptors the
proposal adds or changes, within a proposal's step ceiling (``STEPS_MAX``), and a session's publish
checks the others against the running registry (D247). Accepting one is a draft edit
(``edits``); it becomes ``accepted`` when that session publishes, if the draft still holds what it
proposes (``holding``): a later edit of the same field or descriptor leaves it open.
``reject_proposal`` needs no session, is audited, and is refused for a proposal the open draft
accepted and still holds.

**Proposers.** ``run_proposers`` runs the curation proposers of the packs the dataset lists on a
view of its latest release, after every publish and on request (D249); their proposals enter the
queue by ``importer:<pack id>@<pack version>``, and one that is invalid or raises, or a proposer
that raises, is skipped and reported, never raised. A proposal the same proposer made before and
an operator rejected is not made again.

**The curation queue** (D250) lists, for the latest release or a given label: every curated field
whose status is ``imported_default`` or ``proposed``; the fields nobody declared from a fixed list
(a table's ``role``, ``primary_key`` and ``grain``, a column's ``datatype``, the ``units`` of a
number or time offset column, a coverage's ``parents``, and a relationship without coverage); the
open proposals, marked ``stale`` when made against another release than the latest and
``accepted_in_draft`` when the open session's draft accepted them and still holds them; and the
import report's notes, counts and row references without values. It holds its items in that order
until ``MAX_QUEUE_ITEMS`` items or ``MAX_QUEUE_BYTES`` bytes of them in JSON, and says how many it
left out (``truncated``); it reads the open proposals a page at a time and stops once it is full,
so that 10,000 of 64 KiB each cost no more than a full queue. The release and the open session's
draft are read and pinned under the store's lock.
"""

import sqlite3
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Literal, cast

from pydantic import JsonValue, ValidationError

from aibi.core.schema.curation import (
    CurationQueue,
    ProposalInput,
    QueueField,
    QueueNote,
    QueueProposal,
    QueueUndeclared,
    ReleaseKind,
)
from aibi.core.schema.descriptors import (
    ColumnDescriptor,
    CoverageDescriptor,
    Descriptor,
    RelationshipDescriptor,
    TableDescriptor,
)
from aibi.core.schema.jsonio import canonical, lookup
from aibi.core.schema.limits import (
    MAX_OPEN_PROPOSALS,
    MAX_PROPOSAL_BYTES,
    MAX_QUEUE_BYTES,
    MAX_QUEUE_ITEMS,
    OPEN_PROPOSALS,
    PROPOSAL_BYTES,
)
from aibi.core.schema.output import Output
from aibi.core.schema.pack_api import PackRegistry
from aibi.core.schema.refusals import Limit, RefusalCode
from aibi.core.schema.release import check_release
from aibi.core.store.appdb import Session, StoredProposal
from aibi.core.store.build import read_report
from aibi.core.store.edits import EditRefused, holds, propose
from aibi.core.store.store import Store, StoreRefused
from aibi.core.store.writes import (
    attributed,
    by_id,
    changed,
    check_writes,
    packs_of,
    versions,
    view,
)


@dataclass(frozen=True)
class Skipped:
    """A pack's proposal that was not recorded, or a proposer that failed (``descriptor``
    ``None``), with why."""

    pack: str
    descriptor: str | None
    pointer: str | None
    codes: tuple[str, ...]


@dataclass(frozen=True)
class ProposersRun:
    proposals: tuple[int, ...]
    """The ids of the proposals recorded or found open already."""
    skipped: tuple[Skipped, ...]


def propose_descriptor(
    store: Store,
    dataset: str,
    proposal: ProposalInput,
    by: str,
    *,
    registry: PackRegistry | None = None,
) -> int:
    """Record a proposal; returns its id. Raises ``StoreRefused`` or ``EditRefused``.

    The cheap checks come first: an open proposal that is the same, made against the latest
    release, is returned at once, and the cap is checked before the proposal is applied to the
    release. The same proposal open against an earlier release is checked again, and then counts
    as made against the latest (``stale`` no longer)."""
    if not attributed(by, ("model", "agent", "importer")):
        raise StoreRefused(
            RefusalCode.INVALID_VALUE,
            "Proposals come from a model, an agent or an importer; an operator edits a draft",
        )
    if not proposal.remove and len(canonical(proposal.value)) > MAX_PROPOSAL_BYTES:
        raise StoreRefused(
            RefusalCode.LIMIT_EXCEEDED,
            f"The proposed value has more than {MAX_PROPOSAL_BYTES} bytes in RFC 8785 form; an "
            "operator can set a larger one in a curation session",
            limit=Limit(name=PROPOSAL_BYTES, max=MAX_PROPOSAL_BYTES),
        )
    latest = store.latest(dataset)
    if latest is None:
        raise StoreRefused(RefusalCode.UNKNOWN_RELEASE, "The dataset has no published release")
    stored = StoredProposal(
        id=0,
        dataset=dataset,
        release=latest.manifest,
        descriptor=proposal.descriptor,
        pointer=proposal.pointer,
        value=None if proposal.remove else proposal.value,
        remove=proposal.remove,
        proposer=by,
        evidence=proposal.evidence,
        at=store.now(),
        status="open",
    )
    with store.db.transaction() as db:
        found = _like(store, db, stored)
        if found is not None and found[1] == latest.manifest:
            return found[0]
        if found is None:
            _capped(store, db, dataset)
    with store.pin() as pin:
        pin.manifest(latest.manifest)
        descriptors = store.descriptors(latest.manifest)
        tombstones = store.tombstones(latest.manifest)
        applied = propose(descriptors, tombstones, stored)
        proposed = versions(descriptors, applied.descriptors, tombstones)
        only = changed(descriptors, proposed)
        for refusals in (check_release(proposed), check_writes(proposed, registry, only=only)):
            if refusals:
                raise EditRefused(by_id(refusals, proposed, "descriptors"))
    with store.db.transaction() as db:
        found = _like(store, db, stored)
        if found is not None:
            store.db.restate(db, found[0], latest.manifest)
            return found[0]
        _capped(store, db, dataset)
        return store.db.add_proposal(
            db,
            dataset=dataset,
            release=stored.release,
            descriptor=stored.descriptor,
            pointer=stored.pointer,
            value=stored.value,
            remove=stored.remove,
            proposer=by,
            evidence=stored.evidence,
            at=stored.at,
        )


def _like(
    store: Store,
    db: sqlite3.Connection,
    stored: StoredProposal,
    status: Literal["open", "rejected"] = "open",
) -> tuple[int, str] | None:
    return store.db.proposal_like(
        db,
        dataset=stored.dataset,
        descriptor=stored.descriptor,
        pointer=stored.pointer,
        value=stored.value,
        remove=stored.remove,
        proposer=stored.proposer,
        evidence=stored.evidence,
        status=status,
    )


def _capped(store: Store, db: sqlite3.Connection, dataset: str) -> None:
    if store.db.open_proposals(db, dataset) >= MAX_OPEN_PROPOSALS:
        raise StoreRefused(
            RefusalCode.LIMIT_EXCEEDED,
            f"The dataset has {MAX_OPEN_PROPOSALS} open proposals: decide some first",
            limit=Limit(name=OPEN_PROPOSALS, max=MAX_OPEN_PROPOSALS),
        )


def _rejected(store: Store, dataset: str, proposal: ProposalInput, by: str) -> bool:
    """Whether the proposer made the same proposal before and an operator rejected it."""
    stored = StoredProposal(
        id=0,
        dataset=dataset,
        release="",
        descriptor=proposal.descriptor,
        pointer=proposal.pointer,
        value=None if proposal.remove else proposal.value,
        remove=proposal.remove,
        proposer=by,
        evidence=proposal.evidence,
        at="",
        status="rejected",
    )
    with store.db.transaction() as db:
        return _like(store, db, stored, "rejected") is not None


def holding(
    store: Store,
    session: Session,
    draft: Sequence[Descriptor],
    *,
    ids: Sequence[int] | None = None,
) -> set[int]:
    """The open proposals, among ``ids`` or of every id, that the session's draft accepted and
    still holds (D248): an ``accept`` edit applied them and ``draft``, its descriptors, still
    holds what they propose. One the draft was edited away from is not accepted, and a later
    ``accept`` may apply it again."""
    wanted = set(store.db.session_decisions(session.id))
    if ids is not None:
        wanted &= set(ids)
    if not wanted:
        return set()
    by_id = {descriptor.id: descriptor for descriptor in draft}
    return {
        p.id for p in store.db.proposals(session.dataset, ids=sorted(wanted)) if holds(by_id, p)
    }


def reject_proposal(store: Store, dataset: str, proposal: int, by: str) -> None:
    """Reject an open proposal, as the operator ``by``."""
    if not attributed(by, ("operator",)):
        raise StoreRefused(
            RefusalCode.INVALID_VALUE, "Rejecting a proposal is an operator's: operator:<name>"
        )
    at = store.now()
    with store.db.transaction() as db:
        if not store.db.proposals(dataset, ids=[proposal]):
            raise StoreRefused(
                RefusalCode.UNKNOWN_PROPOSAL,
                f"No open proposal of the dataset has the id {proposal}",
            )
        session = store.db.open_session_of(dataset)
        if session is not None:
            with store.pin() as pin:
                pin.manifest(session.draft)
                draft = store.descriptors(session.draft)
            if holding(store, session, draft, ids=[proposal]):
                raise StoreRefused(
                    RefusalCode.CONFLICT,
                    "The open session's draft accepted the proposal and holds it: discard the "
                    "session, or change the draft away from the proposal, first",
                )
        store.db.decide(db, proposal, "rejected", at, by)
        detail: JsonValue = {"proposal": proposal}
        store.db.audit(db, at, dataset, by, "reject_proposal", detail)


def _pack_proposals(found: object) -> list[object]:
    if isinstance(found, list | tuple):
        return list(cast(Sequence[object], found))
    raise TypeError("a proposer returns a sequence of proposals")


def _shown(item: object, name: str) -> str | None:
    try:
        found = getattr(item, name, None)
    except Exception:
        return None
    return found if isinstance(found, str) else None


def _given(item: object) -> ProposalInput:
    """A pack's proposal as a proposal input; raises ``ValidationError`` or whatever the item
    raises."""
    given: dict[str, object] = {
        "descriptor": getattr(item, "descriptor", None),
        "pointer": getattr(item, "pointer", None),
    }
    if getattr(item, "remove", False) is True:
        given["remove"] = True
    else:
        given["value"] = getattr(item, "value", None)
    evidence = getattr(item, "evidence", None)
    if evidence is not None:
        given["evidence"] = evidence
    return ProposalInput.model_validate(given)


def run_proposers(store: Store, dataset: str, registry: PackRegistry) -> ProposersRun:
    """Run the curation proposers of the registered packs the dataset lists, on its latest
    release (D249). Whatever a proposer or one of its proposals does, it is skipped and reported,
    never raised: this runs after a publish has committed. A proposal the same proposer made
    before and an operator rejected is not made again."""
    latest = store.latest(dataset)
    if latest is None:
        return ProposersRun((), ())
    with store.pin() as pin:
        pin.manifest(latest.manifest)
        descriptors = store.descriptors(latest.manifest)
    packs = [pack for pack in packs_of(descriptors) if pack in registry.ids]
    released = view(dataset, latest.manifest, latest.label, descriptors)
    ids: list[int] = []
    skipped: list[Skipped] = []
    for pack in registry.listed(packs):
        if pack.proposer is None:
            continue
        by = f"importer:{pack.id}@{pack.manifest.version}"
        try:
            found = _pack_proposals(pack.proposer(released))
        except Exception as error:
            skipped.append(Skipped(pack.id, None, None, (type(error).__name__,)))
            continue
        for item in found:
            shown = (_shown(item, "descriptor"), _shown(item, "pointer"))
            try:
                proposal = _given(item)
                if _rejected(store, dataset, proposal, by):
                    continue
                ids.append(propose_descriptor(store, dataset, proposal, by, registry=registry))
            except ValidationError:
                skipped.append(Skipped(pack.id, *shown, (RefusalCode.INVALID_VALUE.value,)))
            except EditRefused as error:
                codes = tuple(sorted({str(refusal.code) for refusal in error.refusals}))
                skipped.append(Skipped(pack.id, *shown, codes))
            except StoreRefused as error:
                skipped.append(Skipped(pack.id, *shown, (str(error.refusal.code),)))
            except Exception as error:
                skipped.append(Skipped(pack.id, *shown, (type(error).__name__,)))
    return ProposersRun(tuple(ids), tuple(skipped))


# --- The curation queue (D250) ---------------------------------------------------------------


def _fields(descriptors: Sequence[Descriptor]) -> Iterator[QueueField]:
    for descriptor in descriptors:
        dumped: JsonValue = None
        for written, entry in sorted(descriptor.curation.items()):
            if entry.status not in ("imported_default", "proposed"):
                continue
            if dumped is None:
                dumped = cast(JsonValue, descriptor.model_dump(mode="json"))
            yield QueueField(
                descriptor=descriptor.id,
                kind=cast(ReleaseKind, descriptor.kind),
                pointer=written,
                status=entry.status,
                by=entry.by,
                at=entry.at,
                evidence=entry.evidence,
                value=cast(JsonValue, lookup(dumped, written)),
            )


def _undeclared(descriptors: Sequence[Descriptor]) -> Iterator[QueueUndeclared]:
    covered = {d.fields.relationship for d in descriptors if isinstance(d, CoverageDescriptor)}
    for descriptor in descriptors:
        given = cast(set[str], descriptor.__dict__["fields"].model_fields_set)
        missing: list[str] = []
        if isinstance(descriptor, TableDescriptor):
            missing = [name for name in ("role", "primary_key", "grain") if name not in given]
        elif isinstance(descriptor, ColumnDescriptor):
            datatype = descriptor.fields.datatype
            if datatype is None:
                missing = ["datatype"]
            elif datatype in ("number", "time_offset") and descriptor.fields.units is None:
                missing = ["units"]
        elif isinstance(descriptor, CoverageDescriptor):
            missing = [] if descriptor.fields.parents is not None else ["parents"]
        elif isinstance(descriptor, RelationshipDescriptor) and descriptor.id not in covered:
            coverage = "cov:" + descriptor.id.removeprefix("rel:")
            yield QueueUndeclared(descriptor=coverage, kind="coverage", pointer="")
        for name in missing:
            yield QueueUndeclared(
                descriptor=descriptor.id,
                kind=cast(ReleaseKind, descriptor.kind),
                pointer=f"/fields/{name}",
            )


_PAGE = 100
"""Open proposals the queue reads at a time, so that it stops reading once it is full."""


def _open_proposals(store: Store, dataset: str) -> Iterator[StoredProposal]:
    after = 0
    while True:
        page = store.db.proposals(dataset, after=after, limit=_PAGE)
        yield from page
        if len(page) < _PAGE:
            return
        after = page[-1].id


def _bytes(item: Output) -> int:
    return len(item.model_dump_json().encode("utf-8", "surrogatepass"))


class _Filling:
    """The queue's items, in order, until ``MAX_QUEUE_ITEMS`` of them or ``MAX_QUEUE_BYTES`` of
    their JSON; the first that does not fit and every item after it are left out, and counted."""

    def __init__(self) -> None:
        self.items: dict[str, list[Output]] = {
            "fields": [],
            "undeclared": [],
            "proposals": [],
            "notes": [],
        }
        self.counted = 0
        self.size = 0
        self.truncated = 0
        self.full = False

    def add(self, kind: str, item: Output) -> None:
        if not self.full:
            cost = _bytes(item)
            if self.counted < MAX_QUEUE_ITEMS and self.size + cost <= MAX_QUEUE_BYTES:
                self.counted += 1
                self.size += cost
                self.items[kind].append(item)
                return
            self.full = True
        self.truncated += 1


def curation_queue(store: Store, dataset: str, *, release: int | None = None) -> CurationQueue:
    """The curation queue of the latest published release, or of the published label
    ``release``. The release, the open session and its draft are read, and pinned, under the
    store's lock, so that no change, discard or withdrawal sweeps them in between."""
    with store.pin() as pin:
        with store.lock:
            latest = store.latest(dataset)
            if release is None:
                if latest is None:
                    raise StoreRefused(
                        RefusalCode.UNKNOWN_RELEASE, "The dataset has no published release"
                    )
                manifest, label = latest.manifest, latest.label
            else:
                resolved = store.resolve(dataset, release)
                if resolved.status == "withdrawn":
                    raise StoreRefused(
                        RefusalCode.RELEASE_WITHDRAWN, f"Release @{release} was withdrawn"
                    )
                manifest, label = resolved.manifest, release
            found = pin.manifest(manifest)
            session = store.db.open_session_of(dataset)
            accepted: set[int] = set()
            if session is not None:
                pin.manifest(session.draft)
                accepted = holding(store, session, store.descriptors(session.draft))
        descriptors = store.descriptors(manifest)
        notes = [] if found.report is None else read_report(store.blobs.read(found.report))
    current = latest.manifest if latest is not None else None
    queue = _Filling()
    for field in _fields(descriptors):
        queue.add("fields", field)
    for undeclared in _undeclared(descriptors):
        queue.add("undeclared", undeclared)
    last = 0
    for proposal in () if queue.full else _open_proposals(store, dataset):
        last = proposal.id
        queue.add(
            "proposals",
            QueueProposal(
                id=proposal.id,
                descriptor=proposal.descriptor,
                pointer=proposal.pointer,
                value=proposal.value,
                remove=proposal.remove,
                proposer=proposal.proposer,
                evidence=proposal.evidence,
                at=proposal.at,
                release=proposal.release,
                stale=proposal.release != current,
                accepted_in_draft=proposal.id in accepted,
            ),
        )
        if queue.full:
            break
    if queue.full:
        with store.lock:
            queue.truncated += store.db.open_proposals(store.db.connection, dataset, after=last)
    for note in notes:
        queue.add("notes", QueueNote.model_validate(note))
    return CurationQueue(
        dataset=dataset,
        release=manifest,
        label=label,
        fields=cast(list[QueueField], queue.items["fields"]),
        undeclared=cast(list[QueueUndeclared], queue.items["undeclared"]),
        proposals=cast(list[QueueProposal], queue.items["proposals"]),
        notes=cast(list[QueueNote], queue.items["notes"]),
        truncated=queue.truncated,
    )


__all__ = [
    "ProposersRun",
    "Skipped",
    "curation_queue",
    "holding",
    "propose_descriptor",
    "reject_proposal",
    "run_proposers",
]

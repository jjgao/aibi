"""The store of one deployment: its blobs and its app DB, and the rules that span them (SPEC
§12.2, §12.3).

**Labels.** Publishing a manifest gives it the next label of its dataset, the highest ever issued
plus one. Withdrawal applies to a manifest and so to every label that refers to it; a withdrawn
manifest is never published again. The latest published release is the highest label whose
manifest is not withdrawn. Every publish, whether an import's, a re-import's or a session's,
labels its manifest through ``commit_label``, in the transaction that records what else it did.

**Operation slots** (§12.3, D236). Each dataset has one slot, held by this process for the whole
of an import, a re-import, a withdrawal, an erasure or a session operation (``exclusive``). A
second operation on the dataset is refused at once (``DATASET_BUSY``), never queued, and while a
curation session is open, imports, re-imports and withdrawals are refused too; an erasure refuses
an open session itself (``ERASURE_BLOCKED``, D223). A slot is taken under the store's lock, only
long enough to check and mark it, and released however the operation ends; it is not persisted,
since a crash ends every running operation (open sessions persist in the app DB). The order is
always the slot, then the store's lock.

**Resolution** (D251). A manifest hash resolves to its highest label's status, to ``draft`` for
the open session's current draft, and to ``discarded`` for a state a draft took that is not live
and was never labelled; ``resolve(dataset)`` and a label never give a draft.

**Deletion.** A manifest is live while a published label or an open session's draft refers to
it. ``sweep`` deletes every blob that no live manifest references, except the manifests of
withdrawn releases, which are kept alone so that their ids still resolve. It runs after every
publish and withdrawal, and after draft changes and session ends. A blob pinned by a
running operation or query (``pin``) is skipped, and deleted once its pins are released if
nothing live references it then.

**Pins** are held by this process, which is the only writer of the store (§11.2): it holds an
exclusive ``flock`` on ``<root>/lock`` while the store is open, so a second process (an operator
CLI, say) cannot open it and sweep what this one pinned. A crash releases the pins and the lock. A
pin is added under the store's lock before its blob is written, and the sweep holds that lock
from reading the pins to its last deletion, so a sweep never deletes a blob an operation is about
to reference. Releasing a pin sweeps only when a blob it freed is referenced by nothing live,
so the end of a query on a live release costs no scan of the blobs. A tool call's pin
(``pin(background=True)``) is released without waiting on the store's lock, and what its
release frees is swept, and the redactions it held off run, with the vacuum after them, in a
housekeeping thread of the store's, never in the call's thread (D300); ``housekept`` waits for
that thread.

**Loading** a release verifies every table blob against its hash, as the manifest and the
descriptors are verified when read.

**Withdrawal** also calls the hooks registered with ``on_withdraw``, which purge the release's
cached results (M2). **Redaction** (§12.2, Erasure) runs once no pin holds a withdrawn manifest,
or a blob of it that no live release references (a query on the new release does not hold it
off); until then it waits in the app DB, and it runs when those pins are released. After it, the
app DB is vacuumed, so that no page keeps a copy of a row as it was before (``AppDB.vacuum``), and
the WAL is checkpointed. The vacuum is recorded as due in the redaction's own transaction and
cleared only once a vacuum and the checkpoint after it finished (D223): while a reader keeps the
checkpoint from finishing, or a vacuum or a checkpoint fails (a full disk), the redaction counts
as pending, and both are retried when the store opens and, at most once a second
(``CHECKPOINT_RETRY``), when a pin is released (a tool call's in the housekeeping thread), so a
long reader does not make every query wait on it. A vacuum that finished is not run again for a
checkpoint retried in the same process.
"""

import fcntl
import json
import logging
import os
import sqlite3
import threading
from collections import Counter
from collections.abc import Callable, Collection, Generator, Iterable, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from types import TracebackType
from typing import Literal, Self, cast

from pydantic import JsonValue

from aibi.core.engine.data import Release
from aibi.core.schema.descriptors import Descriptor
from aibi.core.schema.ids import SHA256_RE
from aibi.core.schema.limits import LogLimits
from aibi.core.schema.output import text
from aibi.core.schema.pack_api import ImportNote
from aibi.core.schema.refusals import Limit, Refusal, RefusalCode
from aibi.core.store import build, parquet, redaction, tables, tombstones
from aibi.core.store.appdb import AppDB, Label
from aibi.core.store.blobs import BlobStore, MissingBlobError, checked
from aibi.core.store.build import Built, Layout
from aibi.core.store.derivations import (
    OPEN_BATCHES,
    DerivationLog,
    DerivationRecord,
    IssuanceRecord,
    stamp,
)
from aibi.core.store.gate import Mode
from aibi.core.store.manifest import Manifest, hex_of
from aibi.core.store.sources import RawSource
from aibi.core.store.tables import TableSource
from aibi.core.store.tombstones import Tombstone

APP_DB = "app.db"
LOCK = "lock"
_log = logging.getLogger(__name__)
CHECKPOINT_RETRY = 1.0
"""Seconds between retries of a checkpoint that a reader kept from finishing."""

Status = Literal["published", "withdrawn", "draft", "discarded"]
OperationKind = Literal["import", "reimport", "withdraw", "erase", "session"]
_REFUSED_WHILE_OPEN: frozenset[OperationKind] = frozenset({"import", "reimport", "withdraw"})


IdStatus = Literal[
    "issued", "not_issued", "unknown", "withdrawn", "discarded", "unknown_release", "erased"
]


@dataclass(frozen=True)
class Explained:
    """What the derivation log says of an id (§7.6, §12.2, D289): a derivation id's record, or an
    issuance id's with its derivation's. ``not_issued`` is a derivation id the log does not hold,
    ``unknown`` an issuance id it does not (never issued, or pruned), and ``unknown_release`` a
    derivation over a release the store has no record of, which no path of the store records."""

    id: str
    status: IdStatus
    derivation: DerivationRecord | None = None
    issuance: IssuanceRecord | None = None


class StoreLockedError(RuntimeError):
    """Another process has the store open (D221)."""


class StoreRefused(Exception):  # noqa: N818 - the spec's word
    def __init__(self, code: RefusalCode, message: str, *, limit: Limit | None = None) -> None:
        super().__init__(f"{code}: {message}")
        self.refusal = Refusal(code=code, path=None, message=[text(message)], limit=limit)


@dataclass(frozen=True)
class Resolution:
    """What a dataset reference names: a manifest, its highest label (if any) and its status."""

    dataset: str
    manifest: str
    label: int | Literal["draft"] | None
    status: Status
    """``discarded`` for a draft state no session holds and no label names (D251)."""


class Pin:
    """Blobs an operation or a query holds until it commits, fails or finishes (§12.2). A tool
    call's pin, made with ``background``, leaves what its release frees to the store's
    housekeeping thread (``Store.pin``)."""

    def __init__(self, store: "Store", *, background: bool = False) -> None:
        self._store = store
        self._background = background
        self._guard = threading.Lock()
        self._held: Counter[str] = Counter()
        self._released = False

    def add(self, digest: str) -> None:
        """Pin a blob, before it is written or read."""
        checked(digest)
        with self._store.lock, self._guard:
            if self._released:
                raise RuntimeError("this pin was released")
            self._store.pinned[digest] += 1
            self._held[digest] += 1

    def manifest(self, manifest: str) -> Manifest:
        """Pin a release: its manifest and every blob it references, all of which must exist."""
        with self._store.lock:
            digest = hex_of(manifest)
            self.add(digest)
            found = self._store.manifest(manifest)
            for blob in sorted(found.blobs()):
                self.add(blob)
                if not self._store.blobs.exists(blob):
                    raise MissingBlobError(blob)
            return found

    def release(self) -> None:
        if self._background:
            with self._guard:
                if self._released:
                    return
                self._released = True
                held, self._held = self._held, Counter()
            self._store.release_later(held)
            return
        with self._store.lock:
            with self._guard:
                if self._released:
                    return
                self._released = True
                held, self._held = self._held, Counter()
            self._store.released(self._store.unpin(held))

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        kind: type[BaseException] | None,
        error: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.release()


def _now() -> datetime:
    return datetime.now(UTC)


class Store:
    """The blobs under ``root``/blobs and the app DB at ``root``/app.db."""

    def __init__(
        self, root: Path, *, clock: Callable[[], datetime] = _now, log: LogLimits | None = None
    ) -> None:
        """Opens the store; ``StoreLockedError`` if another process has it open. ``log`` is how
        long the derivation log keeps ``count_cohort``'s issuances and how large it grows
        (D300): up to ``OPEN_BATCHES`` batches of the expired ones are pruned now, and the log's
        thread prunes the rest, and what expires later, until the store closes."""
        root.mkdir(parents=True, exist_ok=True)
        self.root = root
        self.clock = clock
        self._lock_file = _lock(root / LOCK)
        db: AppDB | None = None
        try:
            self.blobs = BlobStore(root)
            self.blobs.clean()
            self.db = db = AppDB(root / APP_DB)
            self.lock: threading.RLock = self.db.lock
            self.derivations = DerivationLog(self.db, self.now, limits=log)
            self.pinned: Counter[str] = Counter()
            self._manifests: dict[str, Manifest] = {}
            self._descriptors: dict[str, tuple[Descriptor, ...]] = {}
            self._withdraw_hooks: list[Callable[[str], None]] = []
            self._running: dict[str, OperationKind] = {}
            self._checkpoint_due = True
            self._checkpoint_tried: float | None = None
            self._vacuumed = False
            self._housekeeping = threading.Condition()
            self._releases: list[tuple[Counter[str], list[str]]] = []
            self._housekeeper: threading.Thread | None = None
            self._housekeeping_busy = False
            self._closing = False
            # This process holds no pin yet: what a crash left pinned is swept now.
            self.sweep()
            self.run_pending_redactions()
            opened = self.derivations.prune_expired(batches=OPEN_BATCHES)
            self.derivations.start_pruning(first=None if opened.finished else 0.0)
        except BaseException:
            if db is not None:
                db.close()
            os.close(self._lock_file)
            raise

    def close(self) -> None:
        """Close the store, once its housekeeping thread has ended the batch under way; what it
        had still to do is done when the store next opens, as after a crash."""
        with self._housekeeping:
            self._closing = True
            self._housekeeping.notify_all()
            housekeeper = self._housekeeper
        if housekeeper is not None:
            housekeeper.join()
        self.derivations.stop_pruning()
        self.db.close()
        os.close(self._lock_file)  # closing the file releases its flock

    def now(self) -> str:
        """RFC 3339 in UTC, as curation statuses write it."""
        return stamp(self.clock())

    # --- Releases ------------------------------------------------------------------------------

    def manifest(self, manifest: str) -> Manifest:
        """A stored manifest, by its hash; ``MissingBlobError`` once it is deleted."""
        found = self._manifests.get(manifest)
        if found is None:
            data = self.blobs.read(hex_of(manifest))
            found = Manifest.from_bytes(data)
            self._manifests[manifest] = found
        return found

    def descriptors(self, manifest: str) -> tuple[Descriptor, ...]:
        found = self._descriptors.get(manifest)
        if found is None:
            found = build.read_descriptors(self.blobs.read(self.manifest(manifest).descriptors))
            # A sweep racing this may leave the entry after deleting the blob: harmless, since
            # the blob is content-addressed, so what is cached is what it held.
            self._descriptors[manifest] = found
        return found

    def load(self, manifest: str) -> Release:
        """The release in memory, as the reference evaluator reads it (§13.3)."""
        found = self.manifest(manifest)
        loaded = {
            entry.id: tables.decode(entry.id, self.blobs.read(entry.hash)) for entry in found.tables
        }
        return Release(found.dataset, manifest, self.descriptors(manifest), loaded)

    def outline(self, manifest: str) -> Release:
        """The release's descriptors without its rows: what resolution and the SQL compiler
        read, while the rows stay in their blobs for a query to read (§12.2)."""
        found = self.manifest(manifest)
        return Release(found.dataset, manifest, self.descriptors(manifest), {})

    def sources(self, manifest: str) -> dict[str, TableSource]:
        """Each table's blob, by table id, as a query reads it in place (D293): its path, and
        the columns it stores. Each blob is verified as ``load`` verifies it (D221), once per
        file (``BlobStore.verified``), so a damaged blob raises ``CorruptBlobError`` and a
        missing one ``MissingBlobError`` rather than counting what it holds. A caller pins the
        release first, for as long as its query runs (``pin``)."""
        found: dict[str, TableSource] = {}
        for entry in self.manifest(manifest).tables:
            path = self.blobs.verified(entry.hash)
            found[entry.id] = TableSource(str(path), frozenset(parquet.names(path)))
        return found

    def pin(self, *, background: bool = False) -> Pin:
        """A pin; ``background`` for a tool call's, whose release never waits on the store's
        lock nor sweeps nor runs a redaction or a vacuum in the call's thread (D300): it drops
        its blobs' pins at once when the lock is free, and hands them, or what they freed, to
        the store's housekeeping thread (``release_later``)."""
        return Pin(self, background=background)

    def import_release(
        self,
        pin: Pin,
        dataset: str,
        descriptors: Sequence[Descriptor],
        sources: Mapping[str, RawSource],
        layouts: Mapping[str, Layout],
        *,
        notes: Sequence[ImportNote] = (),
        tombstones: Iterable[Tombstone] = (),
        revise: Callable[[tuple[Descriptor, ...]], Sequence[Descriptor]] | None = None,
    ) -> Built:
        """Build a release from new raw snapshots, through the validation gate, with the
        importer's ``notes`` in its import report and ``tombstones``; its blobs stay pinned by
        ``pin``. ``revise`` is ``build.import_release``'s."""
        return build.import_release(
            self.blobs,
            dataset,
            descriptors,
            sources,
            layouts,
            holder=pin,
            tombstones=self._tombstones_blob(pin, tombstones),
            notes=notes,
            revise=revise,
        )

    def change_release(
        self,
        pin: Pin,
        base: str,
        descriptors: Sequence[Descriptor],
        *,
        tombstones: Iterable[Tombstone] | None = None,
        gate_mode: Mode | None = "change",
    ) -> Built:
        """Build a release from ``base`` with new descriptors, through the gate in change mode;
        its blobs stay pinned, and so do the base's. The base's tombstones are kept unless
        ``tombstones`` are given."""
        manifest = pin.manifest(base)
        return build.change_release(
            self.blobs,
            manifest,
            self.descriptors(base),
            descriptors,
            holder=pin,
            tombstones=None if tombstones is None else self._tombstones_blob(pin, tombstones),
            keep_tombstones=tombstones is None,
            gate_mode=gate_mode,
        )

    def _tombstones_blob(self, pin: Pin, found: Iterable[Tombstone]) -> str | None:
        data = tombstones.encode(found)
        return None if data is None else self.blobs.put(data, pin)

    def tombstones(self, manifest: str) -> tuple[Tombstone, ...]:
        """The release's tombstones (§12.3, D240)."""
        digest = self.manifest(manifest).tombstones
        return () if digest is None else tombstones.decode(self.blobs.read(digest))

    # --- Operation slots (§12.3, D236) --------------------------------------------------------

    @contextmanager
    def exclusive(self, dataset: str, kind: OperationKind) -> Generator[None]:
        """Hold the dataset's operation slot for an operation of ``kind``. Refused
        (``DATASET_BUSY``) at once if another operation of the dataset runs, or, for an import,
        a re-import or a withdrawal, while a session is open on it (D236)."""
        with self.lock:
            running = self._running.get(dataset)
            if running is not None:
                raise StoreRefused(
                    RefusalCode.DATASET_BUSY,
                    f"Another operation ({running}) is running on the dataset; try again once it "
                    "ends",
                )
            if kind in _REFUSED_WHILE_OPEN:
                session = self.db.open_session_of(dataset)
                if session is not None:
                    raise StoreRefused(
                        RefusalCode.DATASET_BUSY,
                        f"Curation session {session.id} is open on the dataset; publish or "
                        "discard it first",
                    )
            self._running[dataset] = kind
        try:
            yield
        finally:
            with self.lock:
                del self._running[dataset]

    # --- Labels (§12.3) ------------------------------------------------------------------------

    def publish(self, dataset: str, manifest: str, by: str, *, action: str = "publish") -> int:
        """Give ``manifest`` the dataset's next label, recorded in the audit trail as ``action``,
        one of ``redaction.RELEASE_ACTIONS``, then sweep. This is the store's own step, which
        takes no operation slot: imports and sessions publish through ``commit_label``."""
        with self.db.transaction() as db:
            label = self.commit_label(db, dataset, manifest, by, action)
        self.sweep()
        return label

    def commit_label(
        self,
        db: sqlite3.Connection,
        dataset: str,
        manifest: str,
        by: str,
        action: str,
        detail: Mapping[str, JsonValue] | None = None,
        session: int | None = None,
    ) -> int:
        """In the transaction ``db``, give ``manifest`` the dataset's next label, with an audit
        entry of ``action`` (one of ``redaction.RELEASE_ACTIONS``) whose detail is the label, the
        manifest and ``detail``. Refused for a withdrawn manifest, whose blobs a sweep may have
        deleted, before anything else; the transaction holds the store's lock from the check that
        every blob is stored to the label, so no sweep runs between them."""
        if action not in redaction.RELEASE_ACTIONS:
            raise ValueError(f"{action} is not an action that names releases")
        if self.db.is_withdrawn(manifest):
            raise StoreRefused(
                RefusalCode.RELEASE_WITHDRAWN,
                "The release was withdrawn, and is never published again",
            )
        found = self._whole(manifest)
        if found.dataset != dataset:
            raise StoreRefused(
                RefusalCode.INVALID_VALUE, f"The release is of dataset {found.dataset}"
            )
        at = self.now()
        self.db.record_manifest(db, manifest, dataset, at)
        label = self.db.next_label(db, dataset)
        self.db.add_label(db, dataset, label, manifest, at, by)
        written: dict[str, JsonValue] = {**(detail or {}), "label": label, "manifest": manifest}
        self.db.audit(db, at, dataset, by, action, written, session)
        return label

    def _whole(self, manifest: str) -> Manifest:
        """The manifest, if it and every blob it references are stored."""
        try:
            found = self.manifest(manifest)
            missing = [blob for blob in sorted(found.blobs()) if not self.blobs.exists(blob)]
        except (MissingBlobError, ValueError):
            raise StoreRefused(RefusalCode.UNKNOWN_RELEASE, "No such release is stored") from None
        if missing:
            raise StoreRefused(
                RefusalCode.UNKNOWN_RELEASE, f"The release's blob {missing[0]} is not stored"
            )
        return found

    def datasets(self) -> list[str]:
        """The datasets that have a label, published or withdrawn, in order."""
        return self.db.datasets()

    def labels(self, dataset: str) -> list[Label]:
        return self.db.labels(dataset)

    def latest(self, dataset: str) -> Label | None:
        """The latest published release: the highest label whose manifest is not withdrawn."""
        published = [label for label in self.labels(dataset) if not label.withdrawn]
        return published[-1] if published else None

    def resolve(self, dataset: str, pin: int | str | None = None) -> Resolution:
        """What ``dataset``, ``dataset@<n>``, ``dataset@sha256:<hex>`` or ``dataset@draft``
        names (§7.1): the latest published release when ``pin`` is ``None``."""
        if isinstance(pin, bool):
            raise StoreRefused(RefusalCode.UNKNOWN_RELEASE, "Not a release pin")
        labels = self.labels(dataset)
        if pin is None:
            latest = self.latest(dataset)
            if latest is None:
                raise StoreRefused(RefusalCode.UNKNOWN_RELEASE, "The dataset has no release")
            return Resolution(dataset, latest.manifest, latest.label, "published")
        if isinstance(pin, int):
            for label in labels:
                if label.label == pin:
                    status: Status = "withdrawn" if label.withdrawn else "published"
                    return Resolution(dataset, label.manifest, label.label, status)
            raise StoreRefused(RefusalCode.UNKNOWN_RELEASE, f"The dataset has no release @{pin}")
        drafts = [s.draft for s in self.db.sessions(dataset) if s.open]
        if pin == "draft":
            if not drafts:
                raise StoreRefused(RefusalCode.UNKNOWN_RELEASE, "No session is open")
            return Resolution(dataset, drafts[0], "draft", "draft")
        if not SHA256_RE.fullmatch(pin):
            raise StoreRefused(RefusalCode.UNKNOWN_RELEASE, "Not a release pin")
        named = [label for label in labels if label.manifest == pin]
        if named:
            status = "withdrawn" if named[-1].withdrawn else "published"
            return Resolution(dataset, pin, named[-1].label, status)
        if pin in drafts:
            return Resolution(dataset, pin, "draft", "draft")
        if self.db.is_draft_state(dataset, pin):
            return Resolution(dataset, pin, None, "discarded")
        raise StoreRefused(RefusalCode.UNKNOWN_RELEASE, "No release of the dataset has that hash")

    def explain(self, identifier: str) -> Explained:
        """What the log holds for a derivation or issuance id, and what the id resolves to: a
        derivation that erasure took is *erased*; one over a withdrawn release *withdrawn*; one
        over a draft state that is no longer live *discarded*; one over a release the store has
        no record of *unknown_release*; any other *issued* (§7.6, D289)."""
        if identifier.startswith("iss:"):
            issuance = self.derivations.issuance(identifier)
            if issuance is None:
                return Explained(identifier, "unknown")
            found = self.explain(issuance.derivation)
            return Explained(identifier, found.status, found.derivation, issuance)
        derivation = self.derivations.derivation(identifier)
        if derivation is None:
            return Explained(identifier, "not_issued")
        if derivation.erased:
            return Explained(identifier, "erased", derivation)
        statuses: set[str] = set()
        for release in cast(list[dict[str, str]], derivation.releases):
            try:
                statuses.add(self.resolve(release["dataset"], release["manifest"]).status)
            except StoreRefused:
                statuses.add("unknown_release")
        order: tuple[IdStatus, ...] = ("withdrawn", "discarded", "unknown_release")
        for status in order:
            if status in statuses:
                return Explained(identifier, status, derivation)
        return Explained(identifier, "issued", derivation)

    def withdraw(self, dataset: str, release: int | str, by: str) -> list[int]:
        """Withdraw a release, by label or manifest hash: every label of its manifest (§12.3),
        in the dataset's operation slot. Returns those labels."""
        with self.exclusive(dataset, "withdraw"):
            manifest = self.resolve(dataset, release).manifest
            with self.db.transaction() as db:
                withdrawn = self.record_withdrawal(db, dataset, manifest, by)
            self.withdrawn([manifest])
        return withdrawn

    def record_withdrawal(
        self, db: sqlite3.Connection, dataset: str, manifest: str, by: str
    ) -> list[int]:
        """Withdraw a manifest in the transaction ``db``, with its audit entry; returns its
        labels. ``withdrawn`` is called once the transaction commits."""
        withdrawn = [label.label for label in self.labels(dataset) if label.manifest == manifest]
        if not withdrawn:
            raise StoreRefused(RefusalCode.UNKNOWN_RELEASE, "Only a published release is withdrawn")
        if self.db.is_withdrawn(manifest):
            raise StoreRefused(RefusalCode.RELEASE_WITHDRAWN, "The release is withdrawn already")
        at = self.now()
        self.db.withdraw(db, manifest, at)
        detail: JsonValue = {"labels": [*withdrawn], "manifest": manifest}
        self.db.audit(db, at, dataset, by, "withdraw", detail)
        return withdrawn

    def withdrawn(self, manifests: Iterable[str]) -> None:
        """After manifests are withdrawn: call the hooks, and sweep."""
        for manifest in manifests:
            for hook in self._withdraw_hooks:
                hook(manifest)
        self.sweep()

    def on_withdraw(self, hook: Callable[[str], None]) -> None:
        """Call ``hook`` with a manifest's hash after it is withdrawn (to purge caches, M2)."""
        self._withdraw_hooks.append(hook)

    # --- Deletion (§12.2) ----------------------------------------------------------------------

    def referenced(self) -> set[str]:
        """The blobs no sweep deletes, pins aside: every live manifest's, and withdrawn
        manifests alone. Raises if a live manifest cannot be read, so nothing is deleted."""
        with self.lock:
            kept = {hex_of(manifest) for manifest in self.db.withdrawn_manifests()}
            for manifest in self.db.live_manifests():
                kept.add(hex_of(manifest))
                kept.update(self.manifest(manifest).blobs())
            return kept

    def sweep(self) -> list[str]:
        """Delete every blob that is neither referenced nor pinned; returns their digests."""
        with self.lock:
            kept = self.referenced()
            waiting = set(self.pinned) - kept
            deleted = [digest for digest in self.blobs.digests() if digest not in kept | waiting]
            for digest in deleted:
                self.blobs.delete(digest)
                self._manifests.pop("sha256:" + digest, None)
                self._descriptors.pop("sha256:" + digest, None)
            return deleted

    def unpin(self, held: Counter[str]) -> list[str]:
        """Drop the pins ``held`` counts, under the store's lock; the blobs no pin holds any
        more."""
        freed: list[str] = []
        with self.lock:
            for digest, count in held.items():
                self.pinned[digest] -= count
                if self.pinned[digest] <= 0:
                    del self.pinned[digest]
                    freed.append(digest)
        return freed

    def release_later(self, held: Counter[str]) -> None:
        """Release the pins ``held`` counts for a tool call (D300): at once if the store's lock
        is free, and in the housekeeping thread otherwise, which then sweeps and runs waiting
        redactions, and the vacuum after them (``released``), so that the call never waits on
        them. Holding a pin a little longer only keeps its blobs and holds off a redaction."""
        freed: list[str] = []
        if self.lock.acquire(blocking=False):
            try:
                freed, held = self.unpin(held), Counter()
            finally:
                self.lock.release()
        with self._housekeeping:
            if self._closing:
                return
            self._releases.append((held, freed))
            if self._housekeeper is None:
                self._housekeeper = threading.Thread(
                    target=self._housekeep, name="aibi-store-housekeeping", daemon=True
                )
                self._housekeeper.start()
            self._housekeeping.notify_all()

    def housekept(self, timeout: float | None = None) -> bool:
        """Wait until the housekeeping thread has done what released pins handed it, or the
        store is closing; whether it had within ``timeout`` seconds."""
        with self._housekeeping:
            return self._housekeeping.wait_for(
                lambda: not self._housekeeping_busy and (not self._releases or self._closing),
                timeout,
            )

    def _housekeep(self) -> None:
        while True:
            with self._housekeeping:
                self._housekeeping.wait_for(lambda: self._releases or self._closing)
                if self._closing:
                    return
                batch, self._releases = self._releases, []
                self._housekeeping_busy = True
            try:
                with self.lock:
                    freed = [
                        digest for held, done in batch for digest in (*done, *self.unpin(held))
                    ]
                    self.released(freed)
            except Exception as error:  # the thread must outlive one failed sweep or redaction
                _log.warning("housekeeping after released pins failed: %s", type(error).__name__)
            finally:
                with self._housekeeping:
                    self._housekeeping_busy = False
                    self._housekeeping.notify_all()

    def released(self, freed: Collection[str]) -> None:
        """After pins are released, ``freed`` being the blobs no pin holds any more: sweep if
        one of them is referenced by nothing live (what an operation that failed, was abandoned
        or read a release since withdrawn leaves), and run waiting redactions."""
        with self.lock:
            if freed:
                kept = self.referenced()
                if any(digest not in kept for digest in freed):
                    self.sweep()
            self.run_pending_redactions()

    # --- Redaction (§12.2, Erasure) ------------------------------------------------------------

    def request_redaction(
        self,
        db: sqlite3.Connection,
        dataset: str,
        terms: redaction.Terms,
        manifests: Iterable[str],
    ) -> int:
        """Record, in the transaction ``db``, a redaction of ``terms`` from the dataset's rows in
        the app DB, to run once no pin holds the blobs of ``manifests`` (the releases withdrawn
        for it); returns its id. It runs when ``run_pending_redactions`` next finds it free:
        when a pin is released, when the store opens, or when the caller runs it."""
        cursor = db.execute(
            "INSERT INTO pending_redactions (dataset, requested_at, manifests, terms)"
            " VALUES (?, ?, ?, ?)",
            (dataset, self.now(), json.dumps(sorted(set(manifests))), terms.dumps()),
        )
        assert cursor.lastrowid is not None
        return cursor.lastrowid

    def redact(self, dataset: str, terms: redaction.Terms, manifests: Iterable[str]) -> bool:
        """Request a redaction and run what no pin blocks; returns whether this one ran and was
        checkpointed (``redacted``)."""
        with self.db.transaction() as db:
            request = self.request_redaction(db, dataset, terms, manifests)
        self.run_pending_redactions()
        return self.redacted([request])

    def pending_redactions(self, dataset: str) -> dict[int, redaction.Terms]:
        """The dataset's redactions that have still to run, by id."""
        with self.lock:
            rows = self.db.connection.execute(
                "SELECT id, terms FROM pending_redactions WHERE dataset = ? ORDER BY id", (dataset,)
            ).fetchall()
        return {int(row[0]): redaction.Terms.loads(row[1]) for row in rows}

    def redacted(self, requests: Collection[int]) -> bool:
        """Whether the redactions ``requests`` ran, and the vacuum and the WAL checkpoint after
        them finished."""
        with self.lock:
            waiting = self.db.connection.execute(
                "SELECT count(*) FROM pending_redactions WHERE id IN "
                "(SELECT value FROM json_each(?))",
                (json.dumps(sorted(requests)),),
            ).fetchone()[0]
            return waiting == 0 and not self._checkpoint_due and not self.db.vacuum_is_due()

    def run_pending_redactions(self) -> int:
        """Run the waiting redactions no pin blocks, each recording in its transaction that a
        vacuum is due, then vacuum the app DB and checkpoint the WAL (``_finish``); how many ran
        and were vacuumed and checkpointed. A vacuum or checkpoint that failed, or that a reader
        kept from finishing, is retried here, at most once every ``CHECKPOINT_RETRY`` seconds
        unless a redaction ran."""
        ran = 0
        with self.lock:
            pending = self.db.connection.execute(
                "SELECT id, dataset, manifests, terms FROM pending_redactions ORDER BY id"
            ).fetchall()
            for identifier, dataset, manifests, terms in pending:
                if any(self._holds(manifest) for manifest in json.loads(manifests)):
                    continue
                with self.db.transaction() as db:
                    redaction.redact(db, dataset, redaction.Terms.loads(terms))
                    db.execute("DELETE FROM pending_redactions WHERE id = ?", (identifier,))
                    self.db.due_vacuum(db)
                ran += 1
            if ran:
                self._checkpoint_due = True
                self._vacuumed = False
            now = monotonic()
            retry = self._checkpoint_due and (
                ran > 0
                or self._checkpoint_tried is None
                or now - self._checkpoint_tried >= CHECKPOINT_RETRY
            )
            if retry:
                self._checkpoint_tried = now
                self._checkpoint_due = not self._finish()
        return 0 if self._checkpoint_due else ran

    def _finish(self) -> bool:
        """Vacuum the app DB if a redaction left that due and this process has not vacuumed it
        since, then checkpoint the WAL, and clear the vacuum's record once both finished (and
        checkpoint that too); whether they finished. A vacuum or a checkpoint that fails
        (``sqlite3.OperationalError``: a full disk, say) leaves the vacuum due, to be retried
        (D223)."""
        try:
            due = self.db.vacuum_is_due()
            if due and not self._vacuumed:
                self.db.vacuum()
                self._vacuumed = True
            if not self.db.checkpoint():
                return False
            if not due:
                return True
            self.db.vacuumed()
            return self.db.checkpoint()
        except sqlite3.OperationalError:
            return False

    def _holds(self, manifest: str) -> bool:
        """Whether a pin holds the manifest, or a blob it references that nothing live does."""
        digest = hex_of(manifest)
        if digest in self.pinned:
            return True
        try:
            referenced = self.manifest(manifest).blobs()
        except MissingBlobError:
            return False
        live = self.referenced()
        return any(blob in self.pinned for blob in referenced - live)


def _lock(path: Path) -> int:
    """An open file on which this process holds an exclusive ``flock``."""
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(descriptor)
        raise StoreLockedError(f"another process has the store at {path.parent} open") from None
    return descriptor


__all__ = [
    "APP_DB",
    "CHECKPOINT_RETRY",
    "LOCK",
    "OperationKind",
    "Pin",
    "Resolution",
    "Status",
    "Store",
    "StoreLockedError",
    "StoreRefused",
]

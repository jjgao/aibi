"""The store of one deployment: its blobs and its app DB, and the rules that span them (SPEC
§12.2, §12.3).

**Labels.** Publishing a manifest gives it the next label of its dataset, the highest ever issued
plus one. Withdrawal applies to a manifest and so to every label that refers to it; a withdrawn
manifest is never published again. The latest published release is the highest label whose
manifest is not withdrawn.

**Deletion.** A manifest is live while a published label or an open session's draft refers to
it. ``sweep`` deletes every blob that no live manifest references, except the manifests of
withdrawn releases, which are kept alone so that their ids still resolve. It runs after every
publish and withdrawal here, and after draft changes and session ends (#10). A blob pinned by a
running operation or query (``pin``) is skipped, and deleted once its pins are released if
nothing live references it then.

**Pins** are held by this process, which is the only writer of the store (§11.2): it holds an
exclusive ``flock`` on ``<root>/lock`` while the store is open, so a second process (an operator
CLI, say) cannot open it and sweep what this one pinned. A crash releases the pins and the lock. A
pin is added under the store's lock before its blob is written, and the sweep holds that lock
from reading the pins to its last deletion, so a sweep never deletes a blob an operation is about
to reference. Releasing a pin sweeps only when a blob it freed is referenced by nothing live,
so the end of a query on a live release costs no scan of the blobs.

**Loading** a release verifies every table blob against its hash, as the manifest and the
descriptors are verified when read.

**Withdrawal** also calls the hooks registered with ``on_withdraw``, which purge the release's
cached results (M2). **Redaction** (§12.2, Erasure) runs once no pin holds a withdrawn manifest,
or a blob of it that no live release references (a query on the new release does not hold it
off); until then it waits in the app DB, and it runs when those pins are released. After it, the
WAL is checkpointed; while a reader keeps the checkpoint from finishing, the redaction counts as
pending, and the checkpoint is retried when the store opens and, at most once a second
(``CHECKPOINT_RETRY``), when a pin is released, so a long reader does not make every query wait
on it.
"""

import fcntl
import json
import os
import sqlite3
import threading
from collections import Counter
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from types import TracebackType
from typing import Literal, Self

from pydantic import JsonValue

from aibi.core.engine.data import Release
from aibi.core.schema.descriptors import Descriptor
from aibi.core.schema.ids import SHA256_RE
from aibi.core.schema.output import text
from aibi.core.schema.refusals import Refusal, RefusalCode
from aibi.core.store import build, redaction, tables
from aibi.core.store.appdb import AppDB, Label
from aibi.core.store.blobs import BlobStore, MissingBlobError, checked
from aibi.core.store.build import Built, Layout
from aibi.core.store.manifest import Manifest, hex_of
from aibi.core.store.sources import RawSource

APP_DB = "app.db"
LOCK = "lock"
CHECKPOINT_RETRY = 1.0
"""Seconds between retries of a checkpoint that a reader kept from finishing."""

Status = Literal["published", "withdrawn", "draft"]


class StoreLockedError(RuntimeError):
    """Another process has the store open (D221)."""


class StoreRefused(Exception):  # noqa: N818 - the spec's word
    def __init__(self, code: RefusalCode, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.refusal = Refusal(code=code, path=None, message=[text(message)])


@dataclass(frozen=True)
class Resolution:
    """What a dataset reference names: a manifest, its highest label (if any) and its status."""

    dataset: str
    manifest: str
    label: int | Literal["draft"] | None
    status: Status


class Pin:
    """Blobs an operation or a query holds until it commits, fails or finishes (§12.2)."""

    def __init__(self, store: "Store") -> None:
        self._store = store
        self._held: Counter[str] = Counter()
        self._released = False

    def add(self, digest: str) -> None:
        """Pin a blob, before it is written or read."""
        checked(digest)
        with self._store.lock:
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
        with self._store.lock:
            if self._released:
                return
            self._released = True
            freed: list[str] = []
            for digest, count in self._held.items():
                self._store.pinned[digest] -= count
                if self._store.pinned[digest] <= 0:
                    del self._store.pinned[digest]
                    freed.append(digest)
            self._held.clear()
            self._store.released(freed)

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

    def __init__(self, root: Path, *, clock: Callable[[], datetime] = _now) -> None:
        """Opens the store; ``StoreLockedError`` if another process has it open."""
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
            self.pinned: Counter[str] = Counter()
            self._manifests: dict[str, Manifest] = {}
            self._descriptors: dict[str, tuple[Descriptor, ...]] = {}
            self._withdraw_hooks: list[Callable[[str], None]] = []
            self._checkpoint_due = True
            self._checkpoint_tried: float | None = None
            # This process holds no pin yet: what a crash left pinned is swept now.
            self.sweep()
            self.run_pending_redactions()
        except BaseException:
            if db is not None:
                db.close()
            os.close(self._lock_file)
            raise

    def close(self) -> None:
        self.db.close()
        os.close(self._lock_file)  # closing the file releases its flock

    def now(self) -> str:
        """RFC 3339 in UTC, as curation statuses write it."""
        return self.clock().astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")

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

    def pin(self) -> Pin:
        return Pin(self)

    def import_release(
        self,
        pin: Pin,
        dataset: str,
        descriptors: Sequence[Descriptor],
        sources: Mapping[str, RawSource],
        layouts: Mapping[str, Layout],
    ) -> Built:
        """Build a release from new raw snapshots; its blobs stay pinned by ``pin``."""
        return build.import_release(self.blobs, dataset, descriptors, sources, layouts, holder=pin)

    def change_release(
        self,
        pin: Pin,
        base: str,
        descriptors: Sequence[Descriptor],
        *,
        statistics: str | None = None,
    ) -> Built:
        """Build a release from ``base`` with new descriptors; its blobs stay pinned, and so
        do the base's."""
        manifest = pin.manifest(base)
        return build.change_release(
            self.blobs,
            manifest,
            self.descriptors(base),
            descriptors,
            holder=pin,
            statistics=statistics,
        )

    # --- Labels (§12.3) ------------------------------------------------------------------------

    def publish(self, dataset: str, manifest: str, by: str, *, action: str = "publish") -> int:
        """Give ``manifest`` the dataset's next label, recorded in the audit trail as ``action``,
        one of ``redaction.RELEASE_ACTIONS``. Refused for a withdrawn manifest, whose blobs a
        sweep may have deleted, before anything else; the store's lock is held from the check
        that every blob is stored to the label, so no sweep runs between them."""
        if action not in redaction.RELEASE_ACTIONS:
            raise ValueError(f"{action} is not an action that names releases")
        with self.lock:
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
            label = self._label(dataset, manifest, by, action)
        self.sweep()
        return label

    def _label(self, dataset: str, manifest: str, by: str, action: str) -> int:
        at = self.now()
        with self.db.transaction() as db:
            self.db.record_manifest(db, manifest, dataset, at)
            label = self.db.next_label(db, dataset)
            self.db.add_label(db, dataset, label, manifest, at, by)
            self.db.audit(db, at, dataset, by, action, {"label": label, "manifest": manifest})
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
        raise StoreRefused(RefusalCode.UNKNOWN_RELEASE, "No release of the dataset has that hash")

    def withdraw(self, dataset: str, release: int | str, by: str) -> list[int]:
        """Withdraw a release, by label or manifest hash: every label of its manifest (§12.3).
        Returns those labels."""
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
        """Whether the redactions ``requests`` ran and the WAL checkpoint after them finished."""
        with self.lock:
            waiting = self.db.connection.execute(
                "SELECT count(*) FROM pending_redactions WHERE id IN "
                "(SELECT value FROM json_each(?))",
                (json.dumps(sorted(requests)),),
            ).fetchone()[0]
            return waiting == 0 and not self._checkpoint_due

    def run_pending_redactions(self) -> int:
        """Run the waiting redactions no pin blocks, then checkpoint the WAL; how many ran and
        were checkpointed. A checkpoint that a reader kept from finishing is retried here, at
        most once every ``CHECKPOINT_RETRY`` seconds unless a redaction ran."""
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
                ran += 1
            now = monotonic()
            retry = self._checkpoint_due and (
                self._checkpoint_tried is None or now - self._checkpoint_tried >= CHECKPOINT_RETRY
            )
            if ran or retry:
                self._checkpoint_tried = now
                self._checkpoint_due = not self.db.checkpoint()
        return 0 if self._checkpoint_due else ran

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
    "Pin",
    "Resolution",
    "Status",
    "Store",
    "StoreLockedError",
    "StoreRefused",
]

"""The app DB (SPEC §12.2, §12.3): release labels and statuses, curation sessions, the audit
trail, the proposal queue and what erasures have still to do (redactions waiting on pins, and
upload areas still to delete), in SQLite. Later milestones add tables by adding migrations.

The server process is its only writer (§11.2); a connection is shared behind a lock, and every
change runs in one transaction. The database runs in WAL mode with foreign keys on, full
synchronisation, and ``secure_delete``, so that what redaction overwrites or deletes is not left
in free pages (§12.2, Erasure); after a redaction the WAL is checkpointed and truncated, and
``checkpoint`` says whether that finished.

Rules the schema holds itself, by triggers: a label is never removed or changed; a manifest is
never removed, and is only ever withdrawn, once; the audit trail is never removed from, and only
its details change, by redaction. A label's status is its manifest's: *withdrawn* once the manifest
is. At most one session per dataset is open. A session is never removed, and changes only while it
is open, and then only its draft and its handle's hash; once ended it never changes. Every state a
session's draft took is recorded (``drafts``, D251), and so is every proposal an ``accept`` edit
applied to a draft (``session_decisions``), whether or not the draft still holds it (D248); neither
is ever removed. A proposal is never removed, only its value and evidence change (by redaction),
and its release while it is open (when the same proposal is made again against a later release),
and it is decided once: from *open* to *accepted* or *rejected*, never back (D248).
``proposals.value`` is the value's RFC 8785 text, ``NULL`` for a removal, and a ``pointer`` of
``""`` names a whole descriptor.
"""

import json
import sqlite3
import threading
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from pydantic import JsonValue

from aibi.core.schema.jsonio import canonical

CHECKPOINT_WAIT = 0.1
"""Seconds a checkpoint waits for readers of the WAL to finish before it gives up."""

MIGRATIONS: tuple[str, ...] = (
    # 1: M1 (#8)
    """
    CREATE TABLE manifests (
        hash TEXT PRIMARY KEY CHECK (hash GLOB 'sha256:*' AND length(hash) = 71),
        dataset TEXT NOT NULL,
        recorded_at TEXT NOT NULL,
        withdrawn_at TEXT
    ) STRICT;
    CREATE TABLE labels (
        dataset TEXT NOT NULL,
        label INTEGER NOT NULL CHECK (label >= 1),
        manifest TEXT NOT NULL REFERENCES manifests (hash),
        published_at TEXT NOT NULL,
        published_by TEXT NOT NULL,
        PRIMARY KEY (dataset, label)
    ) STRICT;
    CREATE INDEX labels_by_manifest ON labels (manifest);
    CREATE TABLE sessions (
        id INTEGER PRIMARY KEY,
        dataset TEXT NOT NULL,
        handle_hash TEXT NOT NULL UNIQUE,
        base TEXT NOT NULL REFERENCES manifests (hash),
        draft TEXT NOT NULL CHECK (draft GLOB 'sha256:*' AND length(draft) = 71),
        opened_at TEXT NOT NULL,
        opened_by TEXT NOT NULL,
        ended_at TEXT,
        outcome TEXT CHECK (outcome IN ('published', 'discarded')),
        CHECK ((ended_at IS NULL) = (outcome IS NULL))
    ) STRICT;
    CREATE UNIQUE INDEX one_open_session ON sessions (dataset) WHERE ended_at IS NULL;
    CREATE TABLE audit (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        at TEXT NOT NULL,
        dataset TEXT NOT NULL,
        actor TEXT NOT NULL,
        action TEXT NOT NULL,
        session INTEGER REFERENCES sessions (id),
        detail TEXT NOT NULL
    ) STRICT;
    CREATE TABLE proposals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        dataset TEXT NOT NULL,
        release TEXT NOT NULL,
        descriptor TEXT NOT NULL,
        pointer TEXT NOT NULL,
        value TEXT,
        proposer TEXT NOT NULL,
        evidence TEXT,
        at TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'open'
            CHECK (status IN ('open', 'accepted', 'rejected')),
        decided_at TEXT,
        decided_by TEXT
    ) STRICT;
    CREATE TABLE pending_redactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        dataset TEXT NOT NULL,
        requested_at TEXT NOT NULL,
        manifests TEXT NOT NULL,
        terms TEXT NOT NULL
    ) STRICT;
    CREATE TABLE pending_uploads (dataset TEXT PRIMARY KEY) STRICT;
    CREATE TRIGGER labels_kept BEFORE DELETE ON labels
        BEGIN SELECT RAISE(ABORT, 'a label is never removed'); END;
    CREATE TRIGGER labels_fixed BEFORE UPDATE ON labels
        BEGIN SELECT RAISE(ABORT, 'a label is never changed'); END;
    CREATE TRIGGER manifests_kept BEFORE DELETE ON manifests
        BEGIN SELECT RAISE(ABORT, 'a manifest is never removed'); END;
    CREATE TRIGGER manifests_withdrawn_once BEFORE UPDATE ON manifests
        WHEN OLD.withdrawn_at IS NOT NULL OR NEW.withdrawn_at IS NULL
            OR NEW.hash IS NOT OLD.hash OR NEW.dataset IS NOT OLD.dataset
            OR NEW.recorded_at IS NOT OLD.recorded_at
        BEGIN SELECT RAISE(ABORT, 'a manifest is only ever withdrawn, once'); END;
    CREATE TRIGGER audit_kept BEFORE DELETE ON audit
        BEGIN SELECT RAISE(ABORT, 'the audit trail is append-only'); END;
    CREATE TRIGGER audit_redacted_only BEFORE UPDATE OF id, at, dataset, actor, action, session
        ON audit BEGIN SELECT RAISE(ABORT, 'only redaction changes the audit trail'); END;
    """,
    # 2: M1 (#10)
    """
    CREATE TABLE drafts (
        manifest TEXT NOT NULL CHECK (manifest GLOB 'sha256:*' AND length(manifest) = 71),
        session INTEGER NOT NULL REFERENCES sessions (id),
        recorded_at TEXT NOT NULL,
        PRIMARY KEY (manifest, session)
    ) STRICT;
    CREATE TABLE session_decisions (
        session INTEGER NOT NULL REFERENCES sessions (id),
        proposal INTEGER NOT NULL REFERENCES proposals (id),
        PRIMARY KEY (session, proposal)
    ) STRICT;
    CREATE INDEX proposals_open ON proposals (dataset, status);
    CREATE TRIGGER sessions_kept BEFORE DELETE ON sessions
        BEGIN SELECT RAISE(ABORT, 'a session is never removed'); END;
    CREATE TRIGGER sessions_ended_fixed BEFORE UPDATE ON sessions WHEN OLD.ended_at IS NOT NULL
        BEGIN SELECT RAISE(ABORT, 'an ended session never changes'); END;
    CREATE TRIGGER sessions_fixed BEFORE UPDATE OF id, dataset, base, opened_at, opened_by
        ON sessions BEGIN SELECT RAISE(ABORT, 'a session keeps its base and opening'); END;
    CREATE TRIGGER drafts_kept BEFORE DELETE ON drafts
        BEGIN SELECT RAISE(ABORT, 'a draft state is never removed'); END;
    CREATE TRIGGER drafts_fixed BEFORE UPDATE ON drafts
        BEGIN SELECT RAISE(ABORT, 'a draft state is never changed'); END;
    CREATE TRIGGER session_decisions_kept BEFORE DELETE ON session_decisions
        BEGIN SELECT RAISE(ABORT, 'a session decision is never removed'); END;
    CREATE TRIGGER session_decisions_fixed BEFORE UPDATE ON session_decisions
        BEGIN SELECT RAISE(ABORT, 'a session decision is never changed'); END;
    CREATE TRIGGER proposals_kept BEFORE DELETE ON proposals
        BEGIN SELECT RAISE(ABORT, 'a proposal is never removed'); END;
    CREATE TRIGGER proposals_fixed BEFORE UPDATE OF id, dataset, descriptor, pointer, proposer,
        at ON proposals
        BEGIN SELECT RAISE(ABORT, 'a proposal keeps what it proposes'); END;
    CREATE TRIGGER proposals_release_while_open BEFORE UPDATE OF release ON proposals
        WHEN OLD.status != 'open'
        BEGIN SELECT RAISE(ABORT, 'a decided proposal keeps its release'); END;
    CREATE TRIGGER proposals_decided_once BEFORE UPDATE OF status, decided_at, decided_by
        ON proposals
        WHEN OLD.status != 'open' OR NEW.status NOT IN ('accepted', 'rejected')
            OR NEW.decided_at IS NULL OR NEW.decided_by IS NULL
        BEGIN SELECT RAISE(ABORT, 'a proposal is decided once'); END;
    """,
)


@dataclass(frozen=True)
class Label:
    dataset: str
    label: int
    manifest: str
    withdrawn: bool


@dataclass(frozen=True)
class Session:
    id: int
    dataset: str
    base: str
    draft: str
    open: bool
    handle_hash: str = field(default="", repr=False)
    opened_by: str = ""


@dataclass(frozen=True)
class StoredProposal:
    """A proposal in the queue (D248); ``value`` is ``None`` for a removal, as ``remove`` says.
    Its value and evidence, which erasure may redact, are kept out of its ``repr``."""

    id: int
    dataset: str
    release: str
    descriptor: str
    pointer: str
    value: JsonValue = field(repr=False)
    remove: bool
    proposer: str
    evidence: str | None = field(repr=False)
    at: str
    status: str


class AppDB:
    """The app DB at ``path``, migrated to the latest schema when opened."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock = threading.RLock()
        self.connection = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        for pragma in (
            "PRAGMA journal_mode = WAL",
            "PRAGMA foreign_keys = ON",
            "PRAGMA synchronous = FULL",
            "PRAGMA secure_delete = ON",
        ):
            self.connection.execute(pragma)
        self._migrate()

    def close(self) -> None:
        with self.lock:
            self.connection.close()

    @property
    def version(self) -> int:
        return int(self.connection.execute("PRAGMA user_version").fetchone()[0])

    def _migrate(self) -> None:
        with self.transaction() as db:
            current = int(db.execute("PRAGMA user_version").fetchone()[0])
            if current > len(MIGRATIONS):
                raise RuntimeError(f"the app DB is at version {current}, newer than this server")
            for number, script in enumerate(MIGRATIONS[current:], start=current + 1):
                for statement in _statements(script):
                    db.execute(statement)
                db.execute(f"PRAGMA user_version = {number}")

    @contextmanager
    def transaction(self) -> Generator[sqlite3.Connection]:
        """One write transaction, taken at once so that it never waits to upgrade a lock."""
        with self.lock:
            self.connection.execute("BEGIN IMMEDIATE")
            try:
                yield self.connection
            except BaseException:
                self.connection.execute("ROLLBACK")
                raise
            self.connection.execute("COMMIT")

    def checkpoint(self, wait: float = CHECKPOINT_WAIT) -> bool:
        """Write the WAL into the database and empty it, so no old page stays in the WAL.
        Returns whether it finished: a reader (another connection's read transaction) keeps it
        from finishing, and SQLite retries for at most ``wait`` seconds before giving up."""
        with self.lock:
            previous = int(self.connection.execute("PRAGMA busy_timeout").fetchone()[0])
            self.connection.execute(f"PRAGMA busy_timeout = {int(wait * 1000)}")
            try:
                busy, pages, copied = self.connection.execute(
                    "PRAGMA wal_checkpoint(TRUNCATE)"
                ).fetchone()
            finally:
                self.connection.execute(f"PRAGMA busy_timeout = {previous}")
        return busy == 0 and pages == copied

    # --- Manifests and labels (§12.3) ---------------------------------------------------------

    def record_manifest(self, db: sqlite3.Connection, manifest: str, dataset: str, at: str) -> None:
        db.execute(
            "INSERT INTO manifests (hash, dataset, recorded_at) VALUES (?, ?, ?)"
            " ON CONFLICT (hash) DO NOTHING",
            (manifest, dataset, at),
        )

    def is_withdrawn(self, manifest: str) -> bool:
        with self.lock:
            found = self.connection.execute(
                "SELECT withdrawn_at IS NOT NULL FROM manifests WHERE hash = ?", (manifest,)
            ).fetchone()
        return bool(found and found[0])

    def next_label(self, db: sqlite3.Connection, dataset: str) -> int:
        """The highest label ever issued for the dataset, plus one."""
        found = db.execute("SELECT max(label) FROM labels WHERE dataset = ?", (dataset,)).fetchone()
        return int(found[0] or 0) + 1

    def add_label(
        self, db: sqlite3.Connection, dataset: str, label: int, manifest: str, at: str, by: str
    ) -> None:
        db.execute(
            "INSERT INTO labels (dataset, label, manifest, published_at, published_by)"
            " VALUES (?, ?, ?, ?, ?)",
            (dataset, label, manifest, at, by),
        )

    def datasets(self) -> list[str]:
        """The datasets that have a label, in order."""
        with self.lock:
            rows = self.connection.execute(
                "SELECT DISTINCT dataset FROM labels ORDER BY dataset"
            ).fetchall()
        return [row[0] for row in rows]

    def labels(self, dataset: str) -> list[Label]:
        with self.lock:
            rows = self.connection.execute(
                "SELECT l.dataset, l.label, l.manifest, m.withdrawn_at IS NOT NULL"
                " FROM labels l JOIN manifests m ON m.hash = l.manifest"
                " WHERE l.dataset = ? ORDER BY l.label",
                (dataset,),
            ).fetchall()
        return [Label(row[0], int(row[1]), row[2], bool(row[3])) for row in rows]

    def withdraw(self, db: sqlite3.Connection, manifest: str, at: str) -> None:
        db.execute("UPDATE manifests SET withdrawn_at = ? WHERE hash = ?", (at, manifest))

    def live_manifests(self) -> set[str]:
        """Manifests a published label or an open session's draft refers to (§12.2)."""
        with self.lock:
            rows = self.connection.execute(
                "SELECT l.manifest FROM labels l JOIN manifests m ON m.hash = l.manifest"
                " WHERE m.withdrawn_at IS NULL"
                " UNION SELECT draft FROM sessions WHERE ended_at IS NULL"
            ).fetchall()
        return {row[0] for row in rows}

    def withdrawn_manifests(self) -> set[str]:
        with self.lock:
            rows = self.connection.execute(
                "SELECT hash FROM manifests WHERE withdrawn_at IS NOT NULL"
            ).fetchall()
        return {row[0] for row in rows}

    # --- Sessions (§12.3; the rules are #10's) -------------------------------------------------

    def open_session(
        self,
        db: sqlite3.Connection,
        dataset: str,
        handle_hash: str,
        base: str,
        at: str,
        by: str,
    ) -> int:
        cursor = db.execute(
            "INSERT INTO sessions (dataset, handle_hash, base, draft, opened_at, opened_by)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (dataset, handle_hash, base, base, at, by),
        )
        assert cursor.lastrowid is not None
        return cursor.lastrowid

    def set_draft(self, db: sqlite3.Connection, session: int, draft: str) -> None:
        db.execute(
            "UPDATE sessions SET draft = ? WHERE id = ? AND ended_at IS NULL", (draft, session)
        )

    def end_session(self, db: sqlite3.Connection, session: int, outcome: str, at: str) -> None:
        db.execute(
            "UPDATE sessions SET ended_at = ?, outcome = ? WHERE id = ? AND ended_at IS NULL",
            (at, outcome, session),
        )

    def sessions(self, dataset: str) -> list[Session]:
        with self.lock:
            rows = self.connection.execute(
                "SELECT id, dataset, base, draft, ended_at IS NULL, handle_hash, opened_by"
                " FROM sessions WHERE dataset = ? ORDER BY id",
                (dataset,),
            ).fetchall()
        return [Session(int(r[0]), r[1], r[2], r[3], bool(r[4]), r[5], r[6]) for r in rows]

    def open_session_of(self, dataset: str) -> Session | None:
        """The dataset's open session, if any."""
        return next((session for session in self.sessions(dataset) if session.open), None)

    def set_handle(self, db: sqlite3.Connection, session: int, handle_hash: str) -> None:
        db.execute(
            "UPDATE sessions SET handle_hash = ? WHERE id = ? AND ended_at IS NULL",
            (handle_hash, session),
        )

    def record_draft(self, db: sqlite3.Connection, session: int, manifest: str, at: str) -> None:
        """Record a state of the session's draft (D251); a state it took before is kept."""
        db.execute(
            "INSERT INTO drafts (manifest, session, recorded_at) VALUES (?, ?, ?)"
            " ON CONFLICT DO NOTHING",
            (manifest, session, at),
        )

    def is_draft_state(self, dataset: str, manifest: str) -> bool:
        """Whether ``manifest`` was a state of a draft of one of the dataset's sessions."""
        with self.lock:
            found = self.connection.execute(
                "SELECT 1 FROM drafts d JOIN sessions s ON s.id = d.session"
                " WHERE s.dataset = ? AND d.manifest = ?",
                (dataset, manifest),
            ).fetchone()
        return found is not None

    def decide_in_session(self, db: sqlite3.Connection, session: int, proposal: int) -> None:
        db.execute(
            "INSERT INTO session_decisions (session, proposal) VALUES (?, ?)"
            " ON CONFLICT DO NOTHING",
            (session, proposal),
        )

    def session_decisions(self, session: int) -> list[int]:
        """The proposals an ``accept`` edit of the session applied, by id; which of them the
        draft still holds is ``proposals.holding``'s (D248)."""
        with self.lock:
            rows = self.connection.execute(
                "SELECT proposal FROM session_decisions WHERE session = ? ORDER BY proposal",
                (session,),
            ).fetchall()
        return [int(row[0]) for row in rows]

    # --- Erasure (§12.2) --------------------------------------------------------------------------

    def add_pending_upload(self, db: sqlite3.Connection, dataset: str) -> None:
        """Record that the dataset's source files are still to be deleted from the upload area."""
        db.execute(
            "INSERT INTO pending_uploads (dataset) VALUES (?) ON CONFLICT DO NOTHING", (dataset,)
        )

    def upload_pending(self, dataset: str) -> bool:
        with self.lock:
            found = self.connection.execute(
                "SELECT 1 FROM pending_uploads WHERE dataset = ?", (dataset,)
            ).fetchone()
        return found is not None

    def uploads_deleted(self, dataset: str) -> None:
        with self.transaction() as db:
            db.execute("DELETE FROM pending_uploads WHERE dataset = ?", (dataset,))

    # --- Audit trail and proposals --------------------------------------------------------------

    def audit(
        self,
        db: sqlite3.Connection,
        at: str,
        dataset: str,
        actor: str,
        action: str,
        detail: JsonValue,
        session: int | None = None,
    ) -> None:
        db.execute(
            "INSERT INTO audit (at, dataset, actor, action, session, detail)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (at, dataset, actor, action, session, canonical(detail).decode()),
        )

    def add_proposal(
        self,
        db: sqlite3.Connection,
        *,
        dataset: str,
        release: str,
        descriptor: str,
        pointer: str,
        value: JsonValue,
        proposer: str,
        evidence: str | None,
        at: str,
        remove: bool = False,
    ) -> int:
        cursor = db.execute(
            "INSERT INTO proposals"
            " (dataset, release, descriptor, pointer, value, proposer, evidence, at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                dataset,
                release,
                descriptor,
                pointer,
                None if remove else canonical(value).decode(),
                proposer,
                evidence,
                at,
            ),
        )
        assert cursor.lastrowid is not None
        return cursor.lastrowid

    def proposal_like(
        self,
        db: sqlite3.Connection,
        *,
        dataset: str,
        descriptor: str,
        pointer: str,
        value: JsonValue,
        remove: bool,
        proposer: str,
        evidence: str | None,
        status: Literal["open", "rejected"] = "open",
    ) -> tuple[int, str] | None:
        """The first proposal of ``status`` with the same descriptor, pointer, value, proposer and
        evidence: its id and release."""
        found = db.execute(
            "SELECT id, release FROM proposals WHERE dataset = ? AND status = ? AND descriptor = ?"
            " AND pointer = ? AND value IS ? AND proposer = ? AND evidence IS ? ORDER BY id",
            (
                dataset,
                status,
                descriptor,
                pointer,
                None if remove else canonical(value).decode(),
                proposer,
                evidence,
            ),
        ).fetchone()
        return None if found is None else (int(found[0]), str(found[1]))

    def restate(self, db: sqlite3.Connection, proposal: int, release: str) -> None:
        """Record that an open proposal was made again, and checked, against ``release``."""
        db.execute(
            "UPDATE proposals SET release = ? WHERE id = ? AND status = 'open'", (release, proposal)
        )

    def open_proposals(self, db: sqlite3.Connection, dataset: str, *, after: int = 0) -> int:
        """How many open proposals the dataset has whose id is above ``after``."""
        found = db.execute(
            "SELECT count(*) FROM proposals WHERE dataset = ? AND status = 'open' AND id > ?",
            (dataset, after),
        ).fetchone()
        return int(found[0])

    def proposals(
        self,
        dataset: str,
        *,
        status: str | None = "open",
        ids: list[int] | None = None,
        after: int = 0,
        limit: int | None = None,
    ) -> list[StoredProposal]:
        """The dataset's proposals, by id: those of ``status`` (every one when ``None``), or,
        given ``ids``, those among them; only those whose id is above ``after``, and at most
        ``limit`` of them."""
        query = (
            "SELECT id, dataset, release, descriptor, pointer, value, proposer, evidence, at,"
            " status FROM proposals WHERE dataset = ? AND id > ?"
        )
        parameters: list[object] = [dataset, after]
        if status is not None:
            query += " AND status = ?"
            parameters.append(status)
        if ids is not None:
            query += " AND id IN (SELECT value FROM json_each(?))"
            parameters.append(json.dumps(sorted(ids)))
        query += " ORDER BY id"
        if limit is not None:
            query += " LIMIT ?"
            parameters.append(limit)
        with self.lock:
            rows = self.connection.execute(query, parameters).fetchall()
        return [
            StoredProposal(
                int(row[0]),
                row[1],
                row[2],
                row[3],
                row[4],
                None if row[5] is None else loads(row[5]),
                row[5] is None,
                row[6],
                row[7],
                row[8],
                row[9],
            )
            for row in rows
        ]

    def decide(
        self,
        db: sqlite3.Connection,
        proposal: int,
        status: Literal["accepted", "rejected"],
        at: str,
        by: str,
    ) -> bool:
        """Decide an open proposal; whether it was open."""
        cursor = db.execute(
            "UPDATE proposals SET status = ?, decided_at = ?, decided_by = ?"
            " WHERE id = ? AND status = 'open'",
            (status, at, by, proposal),
        )
        return cursor.rowcount == 1


def _statements(script: str) -> list[str]:
    """The statements of a migration, whole, triggers included."""
    statements: list[str] = []
    pending = ""
    for line in script.splitlines(keepends=True):
        pending += line
        if sqlite3.complete_statement(pending):
            if pending.strip():
                statements.append(pending.strip())
            pending = ""
    if pending.strip():
        raise ValueError(f"an incomplete statement in a migration: {pending.strip()[:80]}")
    return statements


def loads(text: str) -> JsonValue:
    """A JSON column's value."""
    return json.loads(text)


__all__ = [
    "CHECKPOINT_WAIT",
    "MIGRATIONS",
    "AppDB",
    "Label",
    "Session",
    "StoredProposal",
    "loads",
]

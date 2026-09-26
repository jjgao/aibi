"""The app DB (SPEC §12.2, §12.3): release labels and statuses, curation sessions, the audit
trail, the proposal queue, the catalogue index (D273) and what erasures have still to do
(redactions waiting on pins, and upload areas still to delete), in SQLite. Later milestones
add tables by adding migrations.

The server process is its only writer (§11.2); a connection is shared behind a lock, and every
change runs in one transaction. The database runs in WAL mode with foreign keys on, full
synchronisation, and ``secure_delete``, so that what redaction overwrites or deletes is not left
in free pages (§12.2, Erasure); after a redaction the database is vacuumed, so that no page keeps
in its unallocated space a copy of a row that rebalancing moved, and the WAL is checkpointed and
truncated, and ``checkpoint`` says whether that finished. That a vacuum is due is recorded in the
redaction's own transaction (``due_vacuum``) and cleared once a vacuum and the checkpoint after
it finished (``vacuumed``), so one that failed is retried, even after a restart.

The derivation log's tables (M2, D289, D300) are ``derivations``'s, which says what their
triggers hold; migration 5 moves the texts of its issuances into ``log_texts``, each stored once
by its digest, lets pruning remove a derivation no issuance names, and keeps in ``log_usage`` the
bytes of what pruning can free (``LOG_USAGE``).

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

import hashlib
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


def text_digest(text: str) -> str:
    """``sha256:`` and the hex SHA-256 of a text's UTF-8 bytes: the key the log stores a text
    under (D300)."""
    return "sha256:" + hashlib.sha256(text.encode()).hexdigest()


def request_text(written: str, params: str) -> str:
    """The RFC 8785 text of an issuance's request: the document as written and the parameters
    used, given as the texts the log held them in before migration 5."""
    request: JsonValue = {"document": loads(written), "params": loads(params)}
    return canonical(request).decode()


CHECKPOINT_WAIT = 0.1
"""Seconds a checkpoint waits for readers of the WAL to finish before it gives up."""

LOG_PAGE_BYTES = 4096
"""The app DB's page size, which ``AppDB`` sets on a new database: the unit ``LOG_USAGE``
charges rows' pages in. ``PRAGMA page_size`` changes only a database not yet written, so
``AppDB`` refuses to open one of another page size (``PageSizeError``), whose pages the log's
accounting would not bound (D300)."""


class PageSizeError(RuntimeError):
    """The app DB's pages are not of ``LOG_PAGE_BYTES`` bytes."""


_MAX_LOCAL = LOG_PAGE_BYTES - 35
_MIN_LOCAL = (LOG_PAGE_BYTES - 12) * 32 // 255 - 23
_OVERFLOW = LOG_PAGE_BYTES - 4
LOG_CELL_BYTES = 40
"""What a row's cell takes in a table's leaf page beyond its payload: its size and rowid, an
overflow page's number, its pointer, and a share of the interior page above the leaf, and of
the page's header, rounded up."""
LOG_TEXT_BYTES = 192
"""What a text of the derivation log takes beyond its row: its digest's entry in the index of
digests, whose random keys leave its pages half full or more, twice over, and a share of that
index's interior pages (D300)."""
LOG_DERIVATION_BYTES = 256
"""What a derivation takes beyond its row and its releases' rows: its id's entry in the index of
ids, twice over, as for a text's digest."""
LOG_RELEASE_FACTOR = 4
"""What a derivation's releases take, in bytes for each byte of its list of them: a
``derivation_releases`` row of each and their entries in its two indexes, each twice over."""
LOG_ISSUANCE_BYTES = 1024
"""What an issuance takes beyond its packs' text: its row, of which only the packs and the
engine vary, the entries of its six indexes, the three of random keys (derivation, request and
SQL) twice over, and a share of their interior pages."""


def stored_sql(payload: str) -> str:
    """The bytes a row of ``payload`` bytes (an SQL expression) can take in a table whose rows
    are appended in rowid order (D300): its cell twice over, and its overflow pages whole. A row
    fills a leaf page with the cells before it, and the next one opens a page when it does not
    fit, which wastes less than its own cell of the page before; so charging each cell twice
    bounds every leaf page, however rows of different sizes alternate. A payload above
    ``_MAX_LOCAL`` keeps SQLite's local part in the leaf and the rest in overflow pages of
    ``_OVERFLOW`` bytes."""
    surplus = f"({_MIN_LOCAL} + (({payload}) - {_MIN_LOCAL}) % {_OVERFLOW})"
    return (
        f"(CASE WHEN ({payload}) <= {_MAX_LOCAL} THEN 2 * (({payload}) + {LOG_CELL_BYTES})"
        f" WHEN {surplus} <= {_MAX_LOCAL} THEN 2 * ({surplus} + {LOG_CELL_BYTES})"
        f" + (({payload}) - {_MIN_LOCAL}) / {_OVERFLOW} * {LOG_PAGE_BYTES}"
        f" ELSE 2 * ({_MIN_LOCAL} + {LOG_CELL_BYTES})"
        f" + ((({payload}) - {_MIN_LOCAL} + {_OVERFLOW - 1}) / {_OVERFLOW}) * {LOG_PAGE_BYTES}"
        " END)"
    )


def stored_bytes(payload: int) -> int:
    """``stored_sql`` of a row of ``payload`` bytes, computed here."""
    if payload <= _MAX_LOCAL:
        return 2 * (payload + LOG_CELL_BYTES)
    surplus = _MIN_LOCAL + (payload - _MIN_LOCAL) % _OVERFLOW
    if surplus <= _MAX_LOCAL:
        overflow = (payload - _MIN_LOCAL) // _OVERFLOW
        return 2 * (surplus + LOG_CELL_BYTES) + overflow * LOG_PAGE_BYTES
    overflow = -(-(payload - _MIN_LOCAL) // _OVERFLOW)
    return 2 * (_MIN_LOCAL + LOG_CELL_BYTES) + overflow * LOG_PAGE_BYTES


def _bytes(column: str) -> str:
    return f"length(CAST({column} AS BLOB))"


def _text(row: str) -> str:
    """A text's bytes: its row (the text, its digest and the record's header) and
    ``LOG_TEXT_BYTES``."""
    return f"({stored_sql(_bytes(f'{row}text') + ' + 80')} + {LOG_TEXT_BYTES})"


def _derivation(row: str) -> str:
    """A derivation's bytes: its row (its object, its list of releases, its id, kind and time,
    and the record's header), what its releases take (``LOG_RELEASE_FACTOR``) and
    ``LOG_DERIVATION_BYTES``."""
    listed = _bytes(f"{row}releases")
    stored = stored_sql(f"{_bytes(f'{row}hashed')} + {listed} + 112")
    return f"({stored} + {LOG_RELEASE_FACTOR} * {listed} + {LOG_DERIVATION_BYTES})"


def _issuance(row: str) -> str:
    return f"({_bytes(f'{row}packs')} + {LOG_ISSUANCE_BYTES})"


LOG_USAGE = f"""
    (SELECT coalesce(sum({_text("")}), 0) FROM log_texts)
    + (SELECT coalesce(sum({_derivation("")}), 0) FROM derivations WHERE hashed IS NOT NULL)
    + (SELECT coalesce(sum({_issuance("")}), 0) FROM issuances)
"""
"""The bytes ``log_bytes`` bounds (D300): every text, every derivation that is not erased, with
its object, and every issuance, each with the pages its rows and index entries can take, so
that the pages the log adds to the app DB as it grows are at most what it counts (a row is
charged its cell twice over and its overflow pages, ``stored_sql``). That is what pruning can
free (an erased derivation, which only erasure makes, is kept and not counted), so pruning
before now brings it down to 0 (D318); the pages pruning leaves
partly full are SQLite's to reuse. ``log_usage`` keeps it, by triggers; the constants are part
of migration 5's triggers, so changing them needs a migration."""
_TEXT = _text("NEW.")
_OLD_TEXT = _text("OLD.")
_DERIVATION = _derivation("{row}.")
_ISSUANCE = _issuance("{row}.")

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
    # 3: M1 (#12)
    """
    CREATE TABLE catalog (
        dataset TEXT PRIMARY KEY,
        basis TEXT NOT NULL,
        entry TEXT NOT NULL
    ) STRICT;
    ALTER TABLE proposals ADD COLUMN client TEXT;
    """,
    # 4: M2 (#15), the derivation log (D289)
    """
    CREATE TABLE derivations (
        id TEXT PRIMARY KEY CHECK (id GLOB 'drv:*' AND length(id) = 68),
        kind TEXT NOT NULL CHECK (kind IN ('cohort', 'result')),
        hashed TEXT,
        releases TEXT NOT NULL,
        recorded_at TEXT NOT NULL
    ) STRICT;
    CREATE TABLE derivation_releases (
        derivation TEXT NOT NULL REFERENCES derivations (id),
        dataset TEXT NOT NULL,
        manifest TEXT NOT NULL CHECK (manifest GLOB 'sha256:*' AND length(manifest) = 71),
        PRIMARY KEY (derivation, dataset)
    ) STRICT;
    CREATE INDEX derivation_releases_by_dataset ON derivation_releases (dataset);
    CREATE TABLE issuances (
        id TEXT PRIMARY KEY CHECK (id GLOB 'iss:*' AND length(id) = 30),
        derivation TEXT NOT NULL REFERENCES derivations (id),
        tool TEXT NOT NULL CHECK (tool IN ('count_cohort', 'run_analysis')),
        written TEXT NOT NULL,
        params TEXT NOT NULL,
        sql TEXT,
        values_from TEXT NOT NULL,
        engine TEXT NOT NULL,
        packs TEXT NOT NULL,
        at TEXT NOT NULL,
        CHECK ((sql IS NULL) = (values_from != id))
    ) STRICT;
    CREATE INDEX issuances_by_derivation ON issuances (derivation);
    CREATE INDEX issuances_by_time ON issuances (tool, at);
    CREATE INDEX issuances_by_source ON issuances (values_from);
    CREATE TABLE vacuum_due (due INTEGER PRIMARY KEY CHECK (due = 1)) STRICT;
    CREATE TABLE log_permits (
        kind TEXT PRIMARY KEY CHECK (kind IN ('pruning', 'redaction')),
        before TEXT,
        CHECK ((kind = 'pruning') = (before IS NOT NULL))
    ) STRICT;
    CREATE TRIGGER log_permits_fixed BEFORE UPDATE ON log_permits
        BEGIN SELECT RAISE(ABORT, 'a permit is written and removed, never changed'); END;
    CREATE TRIGGER derivations_recorded_once BEFORE INSERT ON derivations
        WHEN NEW.hashed IS NULL OR EXISTS (SELECT 1 FROM derivations WHERE id = NEW.id)
        BEGIN SELECT RAISE(ABORT, 'a derivation is recorded once, with its object'); END;
    CREATE TRIGGER derivations_kept BEFORE DELETE ON derivations
        BEGIN SELECT RAISE(ABORT, 'a derivation is never removed'); END;
    CREATE TRIGGER derivations_erased_once BEFORE UPDATE ON derivations
        WHEN OLD.hashed IS NULL OR NEW.hashed IS NOT NULL OR NEW.id IS NOT OLD.id
            OR NEW.kind IS NOT OLD.kind OR NEW.releases IS NOT OLD.releases
            OR NEW.recorded_at IS NOT OLD.recorded_at
            OR NOT EXISTS (SELECT 1 FROM log_permits WHERE kind = 'redaction')
        BEGIN SELECT RAISE(ABORT, 'a derivation changes only when erased, once'); END;
    CREATE TRIGGER derivation_releases_listed BEFORE INSERT ON derivation_releases
        WHEN EXISTS (
                SELECT 1 FROM derivation_releases
                WHERE derivation = NEW.derivation AND dataset = NEW.dataset
            )
            OR NOT EXISTS (
                SELECT 1 FROM derivations d, json_each(d.releases) r
                WHERE d.id = NEW.derivation
                    AND json_extract(r.value, '$.dataset') IS NEW.dataset
                    AND json_extract(r.value, '$.manifest') IS NEW.manifest
            )
            OR EXISTS (
                SELECT 1 FROM manifests WHERE hash = NEW.manifest
                    AND (withdrawn_at IS NOT NULL OR dataset IS NOT NEW.dataset)
            )
        BEGIN
            SELECT RAISE(
                ABORT, 'a derivation''s releases are those it lists, once, live, of their dataset'
            );
        END;
    CREATE TRIGGER derivation_releases_kept BEFORE DELETE ON derivation_releases
        BEGIN SELECT RAISE(ABORT, 'the releases of a derivation are never removed'); END;
    CREATE TRIGGER derivation_releases_fixed BEFORE UPDATE ON derivation_releases
        BEGIN SELECT RAISE(ABORT, 'the releases of a derivation never change'); END;
    CREATE TRIGGER issuances_recorded_once BEFORE INSERT ON issuances
        WHEN EXISTS (SELECT 1 FROM issuances WHERE id = NEW.id)
            OR (SELECT hashed FROM derivations WHERE id = NEW.derivation) IS NULL
            OR EXISTS (
                SELECT 1 FROM derivation_releases r JOIN manifests m ON m.hash = r.manifest
                WHERE r.derivation = NEW.derivation AND m.withdrawn_at IS NOT NULL
            )
            OR (NEW.values_from IS NOT NEW.id AND NOT EXISTS (
                SELECT 1 FROM issuances
                WHERE id = NEW.values_from AND values_from = id AND derivation = NEW.derivation
            ))
        BEGIN
            SELECT RAISE(ABORT, 'an issuance is recorded once, of a live derivation it names');
        END;
    CREATE TRIGGER issuances_redacted_only BEFORE UPDATE ON issuances
        WHEN NEW.id IS NOT OLD.id OR NEW.derivation IS NOT OLD.derivation
            OR NEW.tool IS NOT OLD.tool OR NEW.values_from IS NOT OLD.values_from
            OR NEW.engine IS NOT OLD.engine OR NEW.packs IS NOT OLD.packs OR NEW.at IS NOT OLD.at
            OR (NEW.written IS NOT OLD.written
                AND NOT (json_valid(NEW.written) AND instr(NEW.written, '[erased]') > 0))
            OR (NEW.params IS NOT OLD.params
                AND NOT (json_valid(NEW.params) AND instr(NEW.params, '[erased]') > 0))
            OR (NEW.sql IS NOT OLD.sql
                AND NOT (json_valid(NEW.sql) AND instr(NEW.sql, '[erased]') > 0))
            OR NOT EXISTS (SELECT 1 FROM log_permits WHERE kind = 'redaction')
        BEGIN SELECT RAISE(ABORT, 'only redaction changes an issuance'); END;
    CREATE TRIGGER issuances_removed_by_erasure_or_pruning BEFORE DELETE ON issuances
        WHEN NOT (
            (
                (SELECT hashed FROM derivations WHERE id = OLD.derivation) IS NULL
                AND EXISTS (SELECT 1 FROM log_permits WHERE kind = 'redaction')
            )
            OR EXISTS (
                SELECT 1 FROM log_permits p
                WHERE p.kind = 'pruning' AND OLD.tool = 'count_cohort' AND OLD.at < p.before
                    AND NOT EXISTS (
                        SELECT 1 FROM issuances k
                        WHERE k.values_from = OLD.id AND k.id IS NOT OLD.id
                            AND NOT (k.tool = 'count_cohort' AND k.at < p.before)
                    )
            )
        )
        BEGIN SELECT RAISE(ABORT, 'an issuance is removed only by erasure or pruning'); END;
    """,
    # 5: M2 (#17), the log's texts, each stored once, its size, and pruned derivations (D300)
    f"""
    CREATE TABLE log_texts (
        digest TEXT PRIMARY KEY CHECK (digest GLOB 'sha256:*' AND length(digest) = 71),
        text TEXT NOT NULL CHECK (json_valid(text))
    ) STRICT;
    CREATE TABLE log_usage (
        one INTEGER PRIMARY KEY CHECK (one = 1),
        bytes INTEGER NOT NULL
    ) STRICT;
    CREATE TABLE issuances_v5 (
        id TEXT PRIMARY KEY CHECK (id GLOB 'iss:*' AND length(id) = 30),
        derivation TEXT NOT NULL REFERENCES derivations (id),
        tool TEXT NOT NULL CHECK (tool IN ('count_cohort', 'run_analysis')),
        request TEXT NOT NULL REFERENCES log_texts (digest),
        sql TEXT REFERENCES log_texts (digest),
        values_from TEXT NOT NULL,
        engine TEXT NOT NULL,
        packs TEXT NOT NULL,
        at TEXT NOT NULL,
        CHECK ((sql IS NULL) = (values_from != id))
    ) STRICT;
    INSERT INTO log_texts (digest, text)
        SELECT aibi_digest(aibi_request(written, params)), aibi_request(written, params)
        FROM issuances
        UNION SELECT aibi_digest(sql), sql FROM issuances WHERE sql IS NOT NULL;
    INSERT INTO issuances_v5
        SELECT id, derivation, tool, aibi_digest(aibi_request(written, params)),
            CASE WHEN sql IS NULL THEN NULL ELSE aibi_digest(sql) END,
            values_from, engine, packs, at
        FROM issuances;
    DROP TABLE issuances;
    ALTER TABLE issuances_v5 RENAME TO issuances;
    CREATE INDEX issuances_by_derivation ON issuances (derivation);
    CREATE INDEX issuances_by_time ON issuances (tool, at);
    CREATE INDEX issuances_by_source ON issuances (values_from);
    CREATE INDEX issuances_by_request ON issuances (request);
    CREATE INDEX issuances_by_sql ON issuances (sql);
    DROP TRIGGER derivations_kept;
    DROP TRIGGER derivation_releases_kept;
    DELETE FROM derivation_releases WHERE derivation IN (
        SELECT id FROM derivations d WHERE hashed IS NOT NULL
            AND NOT EXISTS (SELECT 1 FROM issuances WHERE derivation = d.id)
    );
    DELETE FROM derivations WHERE hashed IS NOT NULL
        AND NOT EXISTS (SELECT 1 FROM issuances WHERE derivation = derivations.id);
    CREATE TRIGGER derivations_removed_by_pruning BEFORE DELETE ON derivations
        WHEN OLD.hashed IS NULL
            OR EXISTS (SELECT 1 FROM issuances WHERE derivation = OLD.id)
            OR NOT EXISTS (SELECT 1 FROM log_permits WHERE kind = 'pruning')
        BEGIN
            SELECT RAISE(ABORT, 'only pruning removes a derivation, once no issuance names it');
        END;
    CREATE TRIGGER derivation_releases_removed_by_pruning BEFORE DELETE ON derivation_releases
        WHEN (SELECT hashed FROM derivations WHERE id = OLD.derivation) IS NULL
            OR EXISTS (SELECT 1 FROM issuances WHERE derivation = OLD.derivation)
            OR NOT EXISTS (SELECT 1 FROM log_permits WHERE kind = 'pruning')
        BEGIN
            SELECT RAISE(ABORT, 'the releases of a derivation go only with it, by pruning');
        END;
    INSERT INTO log_usage (one, bytes) VALUES (1, {LOG_USAGE});
    CREATE TRIGGER log_usage_kept BEFORE DELETE ON log_usage
        BEGIN SELECT RAISE(ABORT, 'the log''s size is never removed'); END;
    CREATE TRIGGER log_texts_stored_once BEFORE INSERT ON log_texts
        WHEN EXISTS (SELECT 1 FROM log_texts WHERE digest = NEW.digest AND text IS NOT NEW.text)
        BEGIN SELECT RAISE(ABORT, 'a text of the log is stored once, under its digest'); END;
    CREATE TRIGGER log_texts_fixed BEFORE UPDATE ON log_texts
        BEGIN SELECT RAISE(ABORT, 'a text of the log never changes'); END;
    CREATE TRIGGER log_texts_removed_by_erasure_or_pruning BEFORE DELETE ON log_texts
        WHEN NOT EXISTS (SELECT 1 FROM log_permits)
        BEGIN SELECT RAISE(ABORT, 'a text of the log is removed only by erasure or pruning'); END;
    CREATE TRIGGER log_texts_counted AFTER INSERT ON log_texts
        BEGIN UPDATE log_usage SET bytes = bytes + {_TEXT}; END;
    CREATE TRIGGER log_texts_uncounted AFTER DELETE ON log_texts
        BEGIN UPDATE log_usage SET bytes = bytes - ({_OLD_TEXT}); END;
    CREATE TRIGGER derivations_counted AFTER INSERT ON derivations
        BEGIN UPDATE log_usage SET bytes = bytes + {_DERIVATION.format(row="NEW")}; END;
    CREATE TRIGGER derivations_uncounted AFTER UPDATE OF hashed ON derivations
        WHEN OLD.hashed IS NOT NULL AND NEW.hashed IS NULL
        BEGIN UPDATE log_usage SET bytes = bytes - ({_DERIVATION.format(row="OLD")}); END;
    CREATE TRIGGER derivations_pruned AFTER DELETE ON derivations WHEN OLD.hashed IS NOT NULL
        BEGIN UPDATE log_usage SET bytes = bytes - ({_DERIVATION.format(row="OLD")}); END;
    CREATE TRIGGER issuances_counted AFTER INSERT ON issuances
        BEGIN UPDATE log_usage SET bytes = bytes + {_ISSUANCE.format(row="NEW")}; END;
    CREATE TRIGGER issuances_uncounted AFTER DELETE ON issuances
        BEGIN UPDATE log_usage SET bytes = bytes - ({_ISSUANCE.format(row="OLD")}); END;
    CREATE TRIGGER issuances_recorded_once BEFORE INSERT ON issuances
        WHEN EXISTS (SELECT 1 FROM issuances WHERE id = NEW.id)
            OR (SELECT hashed FROM derivations WHERE id = NEW.derivation) IS NULL
            OR EXISTS (
                SELECT 1 FROM derivation_releases r JOIN manifests m ON m.hash = r.manifest
                WHERE r.derivation = NEW.derivation AND m.withdrawn_at IS NOT NULL
            )
            OR (NEW.values_from IS NOT NEW.id AND NOT EXISTS (
                SELECT 1 FROM issuances
                WHERE id = NEW.values_from AND values_from = id AND derivation = NEW.derivation
            ))
        BEGIN
            SELECT RAISE(ABORT, 'an issuance is recorded once, of a live derivation it names');
        END;
    CREATE TRIGGER issuances_redacted_only BEFORE UPDATE ON issuances
        WHEN NEW.id IS NOT OLD.id OR NEW.derivation IS NOT OLD.derivation
            OR NEW.tool IS NOT OLD.tool OR NEW.values_from IS NOT OLD.values_from
            OR NEW.engine IS NOT OLD.engine OR NEW.packs IS NOT OLD.packs OR NEW.at IS NOT OLD.at
            OR (NEW.request IS NOT OLD.request AND NOT EXISTS (
                SELECT 1 FROM log_texts WHERE digest = NEW.request AND instr(text, '[erased]') > 0
            ))
            OR (NEW.sql IS NOT OLD.sql AND NOT EXISTS (
                SELECT 1 FROM log_texts WHERE digest = NEW.sql AND instr(text, '[erased]') > 0
            ))
            OR NOT EXISTS (SELECT 1 FROM log_permits WHERE kind = 'redaction')
        BEGIN SELECT RAISE(ABORT, 'only redaction changes an issuance'); END;
    CREATE TRIGGER issuances_removed_by_erasure_or_pruning BEFORE DELETE ON issuances
        WHEN NOT (
            (
                (SELECT hashed FROM derivations WHERE id = OLD.derivation) IS NULL
                AND EXISTS (SELECT 1 FROM log_permits WHERE kind = 'redaction')
            )
            OR EXISTS (
                SELECT 1 FROM log_permits p
                WHERE p.kind = 'pruning' AND OLD.tool = 'count_cohort' AND OLD.at < p.before
                    AND NOT EXISTS (
                        SELECT 1 FROM issuances k
                        WHERE k.values_from = OLD.id AND k.id IS NOT OLD.id
                            AND NOT (k.tool = 'count_cohort' AND k.at < p.before)
                    )
            )
        )
        BEGIN SELECT RAISE(ABORT, 'an issuance is removed only by erasure or pruning'); END;
    """,
    # 6: M3 (#18), the issuances of results and of every count pruned (D318)
    """
    DROP TRIGGER issuances_removed_by_erasure_or_pruning;
    CREATE TRIGGER issuances_removed_by_erasure_or_pruning BEFORE DELETE ON issuances
        WHEN NOT (
            (
                (SELECT hashed FROM derivations WHERE id = OLD.derivation) IS NULL
                AND EXISTS (SELECT 1 FROM log_permits WHERE kind = 'redaction')
            )
            OR EXISTS (
                SELECT 1 FROM log_permits p
                WHERE p.kind = 'pruning' AND OLD.at < p.before
                    AND NOT EXISTS (
                        SELECT 1 FROM issuances k
                        WHERE k.values_from = OLD.id AND k.id IS NOT OLD.id
                            AND NOT k.at < p.before
                    )
            )
        )
        BEGIN SELECT RAISE(ABORT, 'an issuance is removed only by erasure or pruning'); END;
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
            f"PRAGMA page_size = {LOG_PAGE_BYTES}",
            "PRAGMA journal_mode = WAL",
            "PRAGMA foreign_keys = ON",
            "PRAGMA synchronous = FULL",
            "PRAGMA secure_delete = ON",
        ):
            self.connection.execute(pragma)
        size = int(self.connection.execute("PRAGMA page_size").fetchone()[0])
        if size != LOG_PAGE_BYTES:
            self.connection.close()
            raise PageSizeError(
                f"The app DB's pages are of {size} bytes; the derivation log's accounting needs "
                f"{LOG_PAGE_BYTES} (D300): rebuild it with that page size"
            )
        # Migration 5 moves the log's texts into ``log_texts`` by their digests (D300).
        self.connection.create_function("aibi_digest", 1, text_digest, deterministic=True)
        self.connection.create_function("aibi_request", 2, request_text, deterministic=True)
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

    def due_vacuum(self, db: sqlite3.Connection) -> None:
        """Record, in a redaction's transaction ``db``, that the database is to be vacuumed
        (D223)."""
        db.execute("INSERT OR IGNORE INTO vacuum_due (due) VALUES (1)")

    def vacuum_is_due(self) -> bool:
        """Whether a redaction ran since a vacuum and the checkpoint after it last finished."""
        with self.lock:
            return self.connection.execute("SELECT 1 FROM vacuum_due").fetchone() is not None

    def vacuumed(self) -> None:
        """Clear the record of a vacuum due, once a vacuum and the checkpoint after it
        finished."""
        with self.transaction() as db:
            db.execute("DELETE FROM vacuum_due")

    def vacuum(self) -> None:
        """Rebuild the database after a redaction: ``secure_delete`` zeroes what is deleted, but
        a page that rebalancing rebuilt keeps, in its unallocated space, the bytes of the rows
        it moved, which may be a row as it was before redaction changed it (§12.2, Erasure)."""
        with self.lock:
            self.connection.execute("VACUUM")

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
        client: str | None = None,
    ) -> int:
        """Record an open proposal; ``client`` is the key of the client an agent's came from
        (D277)."""
        cursor = db.execute(
            "INSERT INTO proposals"
            " (dataset, release, descriptor, pointer, value, proposer, evidence, at, client)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                dataset,
                release,
                descriptor,
                pointer,
                None if remove else canonical(value).decode(),
                proposer,
                evidence,
                at,
                client,
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

    def open_proposals(
        self,
        db: sqlite3.Connection,
        dataset: str,
        *,
        after: int = 0,
        kind: str | None = None,
        client: str | None = None,
    ) -> int:
        """How many open proposals the dataset has whose id is above ``after``; with ``kind``,
        only those whose proposer is ``<kind>:…``, and with ``client``, only those that came from
        that client (D277)."""
        query = "SELECT count(*) FROM proposals WHERE dataset = ? AND status = 'open' AND id > ?"
        parameters: list[object] = [dataset, after]
        if kind is not None:
            query += " AND substr(proposer, 1, ?) = ?"
            parameters.extend((len(kind) + 1, kind + ":"))
        if client is not None:
            query += " AND client = ?"
            parameters.append(client)
        return int(db.execute(query, parameters).fetchone()[0])

    def open_ids(
        self,
        db: sqlite3.Connection,
        dataset: str,
        *,
        kind: str | None = None,
        proposer: str | None = None,
    ) -> list[int]:
        """The ids of the dataset's open proposals, by id: every one, those whose proposer is
        ``<kind>:…``, or those of ``proposer``."""
        query = "SELECT id FROM proposals WHERE dataset = ? AND status = 'open'"
        parameters: list[object] = [dataset]
        if kind is not None:
            query += " AND substr(proposer, 1, ?) = ?"
            parameters.extend((len(kind) + 1, kind + ":"))
        if proposer is not None:
            query += " AND proposer = ?"
            parameters.append(proposer)
        return [int(row[0]) for row in db.execute(query + " ORDER BY id", parameters)]

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
    "LOG_CELL_BYTES",
    "LOG_DERIVATION_BYTES",
    "LOG_ISSUANCE_BYTES",
    "LOG_PAGE_BYTES",
    "LOG_RELEASE_FACTOR",
    "LOG_TEXT_BYTES",
    "LOG_USAGE",
    "MIGRATIONS",
    "AppDB",
    "Label",
    "PageSizeError",
    "Session",
    "StoredProposal",
    "loads",
    "request_text",
    "stored_bytes",
    "stored_sql",
    "text_digest",
]

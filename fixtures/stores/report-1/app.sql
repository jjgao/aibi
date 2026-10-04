BEGIN TRANSACTION;
CREATE TABLE audit (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        at TEXT NOT NULL,
        dataset TEXT NOT NULL,
        actor TEXT NOT NULL,
        action TEXT NOT NULL,
        session INTEGER REFERENCES sessions (id),
        detail TEXT NOT NULL
    ) STRICT;
INSERT INTO "audit" VALUES(1,'2026-01-01T00:00:03.000000Z','hostile','operator:ada','import',NULL,'{"label":1,"manifest":"sha256:edb0774fe553c6b14429d23a3ad84b6b249a60ee2c6d2f40de84237677fe638b"}');
INSERT INTO "audit" VALUES(2,'2026-01-01T00:00:05.000000Z','every','operator:ada','import',NULL,'{"label":1,"manifest":"sha256:9445b8d831c741bc464d9d284cea1c3c58a6d8e68cce141259264740476b949e"}');
INSERT INTO "audit" VALUES(3,'2026-01-01T00:00:06.000000Z','hostile','operator:ada','open',1,'{"base":"sha256:edb0774fe553c6b14429d23a3ad84b6b249a60ee2c6d2f40de84237677fe638b","draft":"sha256:edb0774fe553c6b14429d23a3ad84b6b249a60ee2c6d2f40de84237677fe638b","label":1}');
INSERT INTO "audit" VALUES(4,'2026-01-01T00:00:08.000000Z','hostile','operator:ada','change',1,'{"base":"sha256:edb0774fe553c6b14429d23a3ad84b6b249a60ee2c6d2f40de84237677fe638b","draft":"sha256:dc2b941e308f167778377b72f84e8f97eb230233f359acfff49519baa603b333","edits":[["b","/definition"]],"previous":"sha256:edb0774fe553c6b14429d23a3ad84b6b249a60ee2c6d2f40de84237677fe638b","proposals":[]}');
INSERT INTO "audit" VALUES(5,'2026-01-01T00:00:10.000000Z','hostile','operator:ada','publish',1,'{"base":"sha256:edb0774fe553c6b14429d23a3ad84b6b249a60ee2c6d2f40de84237677fe638b","draft":"sha256:dc2b941e308f167778377b72f84e8f97eb230233f359acfff49519baa603b333","label":2,"manifest":"sha256:dc2b941e308f167778377b72f84e8f97eb230233f359acfff49519baa603b333"}');
CREATE TABLE catalog (
        dataset TEXT PRIMARY KEY,
        basis TEXT NOT NULL,
        entry TEXT NOT NULL
    ) STRICT;
CREATE TABLE derivation_releases (
        derivation TEXT NOT NULL REFERENCES derivations (id),
        dataset TEXT NOT NULL,
        manifest TEXT NOT NULL CHECK (manifest GLOB 'sha256:*' AND length(manifest) = 71),
        PRIMARY KEY (derivation, dataset)
    ) STRICT;
CREATE TABLE derivations (
        id TEXT PRIMARY KEY CHECK (id GLOB 'drv:*' AND length(id) = 68),
        kind TEXT NOT NULL CHECK (kind IN ('cohort', 'result')),
        hashed TEXT,
        releases TEXT NOT NULL,
        recorded_at TEXT NOT NULL
    ) STRICT;
CREATE TABLE drafts (
        manifest TEXT NOT NULL CHECK (manifest GLOB 'sha256:*' AND length(manifest) = 71),
        session INTEGER NOT NULL REFERENCES sessions (id),
        recorded_at TEXT NOT NULL,
        PRIMARY KEY (manifest, session)
    ) STRICT;
INSERT INTO "drafts" VALUES('sha256:edb0774fe553c6b14429d23a3ad84b6b249a60ee2c6d2f40de84237677fe638b',1,'2026-01-01T00:00:06.000000Z');
INSERT INTO "drafts" VALUES('sha256:dc2b941e308f167778377b72f84e8f97eb230233f359acfff49519baa603b333',1,'2026-01-01T00:00:08.000000Z');
CREATE TABLE "issuances" (
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
CREATE TABLE labels (
        dataset TEXT NOT NULL,
        label INTEGER NOT NULL CHECK (label >= 1),
        manifest TEXT NOT NULL REFERENCES manifests (hash),
        published_at TEXT NOT NULL,
        published_by TEXT NOT NULL,
        PRIMARY KEY (dataset, label)
    ) STRICT;
INSERT INTO "labels" VALUES('hostile',1,'sha256:edb0774fe553c6b14429d23a3ad84b6b249a60ee2c6d2f40de84237677fe638b','2026-01-01T00:00:03.000000Z','operator:ada');
INSERT INTO "labels" VALUES('every',1,'sha256:9445b8d831c741bc464d9d284cea1c3c58a6d8e68cce141259264740476b949e','2026-01-01T00:00:05.000000Z','operator:ada');
INSERT INTO "labels" VALUES('hostile',2,'sha256:dc2b941e308f167778377b72f84e8f97eb230233f359acfff49519baa603b333','2026-01-01T00:00:10.000000Z','operator:ada');
CREATE TABLE log_permits (
        kind TEXT PRIMARY KEY CHECK (kind IN ('pruning', 'redaction')),
        before TEXT,
        CHECK ((kind = 'pruning') = (before IS NOT NULL))
    ) STRICT;
CREATE TABLE log_texts (
        digest TEXT PRIMARY KEY CHECK (digest GLOB 'sha256:*' AND length(digest) = 71),
        text TEXT NOT NULL CHECK (json_valid(text))
    ) STRICT;
CREATE TABLE log_usage (
        one INTEGER PRIMARY KEY CHECK (one = 1),
        bytes INTEGER NOT NULL
    ) STRICT;
INSERT INTO "log_usage" VALUES(1,0);
CREATE TABLE manifests (
        hash TEXT PRIMARY KEY CHECK (hash GLOB 'sha256:*' AND length(hash) = 71),
        dataset TEXT NOT NULL,
        recorded_at TEXT NOT NULL,
        withdrawn_at TEXT
    ) STRICT;
INSERT INTO "manifests" VALUES('sha256:edb0774fe553c6b14429d23a3ad84b6b249a60ee2c6d2f40de84237677fe638b','hostile','2026-01-01T00:00:03.000000Z',NULL);
INSERT INTO "manifests" VALUES('sha256:9445b8d831c741bc464d9d284cea1c3c58a6d8e68cce141259264740476b949e','every','2026-01-01T00:00:05.000000Z',NULL);
INSERT INTO "manifests" VALUES('sha256:dc2b941e308f167778377b72f84e8f97eb230233f359acfff49519baa603b333','hostile','2026-01-01T00:00:10.000000Z',NULL);
CREATE TABLE pending_redactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        dataset TEXT NOT NULL,
        requested_at TEXT NOT NULL,
        manifests TEXT NOT NULL,
        terms TEXT NOT NULL
    ) STRICT;
CREATE TABLE pending_uploads (dataset TEXT PRIMARY KEY) STRICT;
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
    , client TEXT) STRICT;
CREATE TABLE result_cache (
        result TEXT PRIMARY KEY CHECK (result GLOB 'drv:*' AND length(result) = 68),
        issuance TEXT NOT NULL REFERENCES issuances (id) ON DELETE CASCADE,
        engine TEXT NOT NULL,
        packs TEXT NOT NULL CHECK (json_valid(packs)),
        k INTEGER CHECK (k IS NULL OR k >= 2),
        bytes INTEGER NOT NULL CHECK (bytes > 0),
        used INTEGER NOT NULL
    ) STRICT;
CREATE TABLE result_cache_contents (
        result TEXT PRIMARY KEY REFERENCES result_cache (result) ON DELETE CASCADE,
        content BLOB NOT NULL
    ) STRICT;
CREATE TABLE result_cache_usage (
        one INTEGER PRIMARY KEY CHECK (one = 1),
        bytes INTEGER NOT NULL,
        used INTEGER NOT NULL
    ) STRICT;
INSERT INTO "result_cache_usage" VALUES(1,0,0);
CREATE TABLE session_decisions (
        session INTEGER NOT NULL REFERENCES sessions (id),
        proposal INTEGER NOT NULL REFERENCES proposals (id),
        PRIMARY KEY (session, proposal)
    ) STRICT;
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
INSERT INTO "sessions" VALUES(1,'hostile','e2e045891b3f1866cb6cf046ae69e8baa5330abf27b7c8e5e44c3a0c361dd060','sha256:edb0774fe553c6b14429d23a3ad84b6b249a60ee2c6d2f40de84237677fe638b','sha256:dc2b941e308f167778377b72f84e8f97eb230233f359acfff49519baa603b333','2026-01-01T00:00:06.000000Z','operator:ada','2026-01-01T00:00:09.000000Z','published');
CREATE TABLE vacuum_due (due INTEGER PRIMARY KEY CHECK (due = 1)) STRICT;
CREATE INDEX labels_by_manifest ON labels (manifest);
CREATE UNIQUE INDEX one_open_session ON sessions (dataset) WHERE ended_at IS NULL;
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
CREATE INDEX derivation_releases_by_dataset ON derivation_releases (dataset);
CREATE TRIGGER log_permits_fixed BEFORE UPDATE ON log_permits
        BEGIN SELECT RAISE(ABORT, 'a permit is written and removed, never changed'); END;
CREATE TRIGGER derivations_recorded_once BEFORE INSERT ON derivations
        WHEN NEW.hashed IS NULL OR EXISTS (SELECT 1 FROM derivations WHERE id = NEW.id)
        BEGIN SELECT RAISE(ABORT, 'a derivation is recorded once, with its object'); END;
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
CREATE TRIGGER derivation_releases_fixed BEFORE UPDATE ON derivation_releases
        BEGIN SELECT RAISE(ABORT, 'the releases of a derivation never change'); END;
CREATE INDEX issuances_by_derivation ON issuances (derivation);
CREATE INDEX issuances_by_time ON issuances (tool, at);
CREATE INDEX issuances_by_source ON issuances (values_from);
CREATE INDEX issuances_by_request ON issuances (request);
CREATE INDEX issuances_by_sql ON issuances (sql);
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
        BEGIN UPDATE log_usage SET bytes = bytes + ((CASE WHEN (length(CAST(NEW.text AS BLOB)) + 80) <= 4061 THEN 2 * ((length(CAST(NEW.text AS BLOB)) + 80) + 40) WHEN (489 + ((length(CAST(NEW.text AS BLOB)) + 80) - 489) % 4092) <= 4061 THEN 2 * ((489 + ((length(CAST(NEW.text AS BLOB)) + 80) - 489) % 4092) + 40) + ((length(CAST(NEW.text AS BLOB)) + 80) - 489) / 4092 * 4096 ELSE 2 * (489 + 40) + (((length(CAST(NEW.text AS BLOB)) + 80) - 489 + 4091) / 4092) * 4096 END) + 192); END;
CREATE TRIGGER log_texts_uncounted AFTER DELETE ON log_texts
        BEGIN UPDATE log_usage SET bytes = bytes - (((CASE WHEN (length(CAST(OLD.text AS BLOB)) + 80) <= 4061 THEN 2 * ((length(CAST(OLD.text AS BLOB)) + 80) + 40) WHEN (489 + ((length(CAST(OLD.text AS BLOB)) + 80) - 489) % 4092) <= 4061 THEN 2 * ((489 + ((length(CAST(OLD.text AS BLOB)) + 80) - 489) % 4092) + 40) + ((length(CAST(OLD.text AS BLOB)) + 80) - 489) / 4092 * 4096 ELSE 2 * (489 + 40) + (((length(CAST(OLD.text AS BLOB)) + 80) - 489 + 4091) / 4092) * 4096 END) + 192)); END;
CREATE TRIGGER derivations_counted AFTER INSERT ON derivations
        BEGIN UPDATE log_usage SET bytes = bytes + ((CASE WHEN (length(CAST(NEW.hashed AS BLOB)) + length(CAST(NEW.releases AS BLOB)) + 112) <= 4061 THEN 2 * ((length(CAST(NEW.hashed AS BLOB)) + length(CAST(NEW.releases AS BLOB)) + 112) + 40) WHEN (489 + ((length(CAST(NEW.hashed AS BLOB)) + length(CAST(NEW.releases AS BLOB)) + 112) - 489) % 4092) <= 4061 THEN 2 * ((489 + ((length(CAST(NEW.hashed AS BLOB)) + length(CAST(NEW.releases AS BLOB)) + 112) - 489) % 4092) + 40) + ((length(CAST(NEW.hashed AS BLOB)) + length(CAST(NEW.releases AS BLOB)) + 112) - 489) / 4092 * 4096 ELSE 2 * (489 + 40) + (((length(CAST(NEW.hashed AS BLOB)) + length(CAST(NEW.releases AS BLOB)) + 112) - 489 + 4091) / 4092) * 4096 END) + 4 * length(CAST(NEW.releases AS BLOB)) + 256); END;
CREATE TRIGGER derivations_uncounted AFTER UPDATE OF hashed ON derivations
        WHEN OLD.hashed IS NOT NULL AND NEW.hashed IS NULL
        BEGIN UPDATE log_usage SET bytes = bytes - (((CASE WHEN (length(CAST(OLD.hashed AS BLOB)) + length(CAST(OLD.releases AS BLOB)) + 112) <= 4061 THEN 2 * ((length(CAST(OLD.hashed AS BLOB)) + length(CAST(OLD.releases AS BLOB)) + 112) + 40) WHEN (489 + ((length(CAST(OLD.hashed AS BLOB)) + length(CAST(OLD.releases AS BLOB)) + 112) - 489) % 4092) <= 4061 THEN 2 * ((489 + ((length(CAST(OLD.hashed AS BLOB)) + length(CAST(OLD.releases AS BLOB)) + 112) - 489) % 4092) + 40) + ((length(CAST(OLD.hashed AS BLOB)) + length(CAST(OLD.releases AS BLOB)) + 112) - 489) / 4092 * 4096 ELSE 2 * (489 + 40) + (((length(CAST(OLD.hashed AS BLOB)) + length(CAST(OLD.releases AS BLOB)) + 112) - 489 + 4091) / 4092) * 4096 END) + 4 * length(CAST(OLD.releases AS BLOB)) + 256)); END;
CREATE TRIGGER derivations_pruned AFTER DELETE ON derivations WHEN OLD.hashed IS NOT NULL
        BEGIN UPDATE log_usage SET bytes = bytes - (((CASE WHEN (length(CAST(OLD.hashed AS BLOB)) + length(CAST(OLD.releases AS BLOB)) + 112) <= 4061 THEN 2 * ((length(CAST(OLD.hashed AS BLOB)) + length(CAST(OLD.releases AS BLOB)) + 112) + 40) WHEN (489 + ((length(CAST(OLD.hashed AS BLOB)) + length(CAST(OLD.releases AS BLOB)) + 112) - 489) % 4092) <= 4061 THEN 2 * ((489 + ((length(CAST(OLD.hashed AS BLOB)) + length(CAST(OLD.releases AS BLOB)) + 112) - 489) % 4092) + 40) + ((length(CAST(OLD.hashed AS BLOB)) + length(CAST(OLD.releases AS BLOB)) + 112) - 489) / 4092 * 4096 ELSE 2 * (489 + 40) + (((length(CAST(OLD.hashed AS BLOB)) + length(CAST(OLD.releases AS BLOB)) + 112) - 489 + 4091) / 4092) * 4096 END) + 4 * length(CAST(OLD.releases AS BLOB)) + 256)); END;
CREATE TRIGGER issuances_counted AFTER INSERT ON issuances
        BEGIN UPDATE log_usage SET bytes = bytes + (length(CAST(NEW.packs AS BLOB)) + 1024); END;
CREATE TRIGGER issuances_uncounted AFTER DELETE ON issuances
        BEGIN UPDATE log_usage SET bytes = bytes - ((length(CAST(OLD.packs AS BLOB)) + 1024)); END;
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
                WHERE p.kind = 'pruning' AND OLD.at < p.before
                    AND NOT EXISTS (
                        SELECT 1 FROM issuances k
                        WHERE k.values_from = OLD.id AND k.id IS NOT OLD.id
                            AND NOT k.at < p.before
                    )
            )
        )
        BEGIN SELECT RAISE(ABORT, 'an issuance is removed only by erasure or pruning'); END;
CREATE INDEX result_cache_by_use ON result_cache (used);
CREATE INDEX result_cache_by_issuance ON result_cache (issuance);
CREATE INDEX derivation_releases_by_manifest ON derivation_releases (manifest);
CREATE TRIGGER result_cache_usage_kept BEFORE DELETE ON result_cache_usage
        BEGIN SELECT RAISE(ABORT, 'the result cache''s size is never removed'); END;
CREATE TRIGGER result_cache_filled_once BEFORE INSERT ON result_cache
        WHEN EXISTS (SELECT 1 FROM result_cache WHERE result = NEW.result)
        BEGIN SELECT RAISE(ABORT, 'a cached result is removed before it is filled again'); END;
CREATE TRIGGER result_cache_filled_by_its_result BEFORE INSERT ON result_cache
        WHEN NOT EXISTS (
            SELECT 1 FROM issuances i JOIN derivations d ON d.id = i.derivation
            WHERE i.id = NEW.issuance AND i.values_from = i.id AND i.derivation = NEW.result
                AND d.kind = 'result' AND d.hashed IS NOT NULL
                AND i.engine = NEW.engine AND i.packs = NEW.packs
                AND NEW.k IS json_extract(d.hashed, '$.disclosure.min_cell_count')
        )
        BEGIN
            SELECT RAISE(ABORT, 'a cached result is filled by an issuance of it whose queries ran');
        END;
CREATE TRIGGER result_cache_over_published_releases BEFORE INSERT ON result_cache
        WHEN NOT EXISTS (SELECT 1 FROM derivation_releases WHERE derivation = NEW.result)
            OR EXISTS (
                SELECT 1 FROM derivation_releases r LEFT JOIN manifests m ON m.hash = r.manifest
                WHERE r.derivation = NEW.result AND (m.hash IS NULL OR m.withdrawn_at IS NOT NULL)
            )
        BEGIN
            SELECT RAISE(ABORT, 'a cached result reads published releases not withdrawn only');
        END;
CREATE TRIGGER result_cache_fixed BEFORE UPDATE OF result, issuance, engine, packs, k, bytes
        ON result_cache
        BEGIN SELECT RAISE(ABORT, 'a cached result is changed only by being used'); END;
CREATE TRIGGER result_cache_contents_counted BEFORE INSERT ON result_cache_contents
        WHEN NOT EXISTS (
            SELECT 1 FROM result_cache
            WHERE result = NEW.result AND bytes >= length(NEW.content)
        )
        BEGIN SELECT RAISE(ABORT, 'a cached result''s content is counted in its bytes'); END;
CREATE TRIGGER result_cache_contents_once BEFORE INSERT ON result_cache_contents
        WHEN EXISTS (SELECT 1 FROM result_cache_contents WHERE result = NEW.result)
        BEGIN SELECT RAISE(ABORT, 'a cached result''s content is never changed'); END;
CREATE TRIGGER result_cache_contents_fixed BEFORE UPDATE ON result_cache_contents
        BEGIN SELECT RAISE(ABORT, 'a cached result''s content is never changed'); END;
CREATE TRIGGER result_cache_contents_kept BEFORE DELETE ON result_cache_contents
        WHEN EXISTS (SELECT 1 FROM result_cache WHERE result = OLD.result)
        BEGIN SELECT RAISE(ABORT, 'a cached result''s content goes only with its row'); END;
CREATE TRIGGER result_cache_withdrawn AFTER UPDATE OF withdrawn_at ON manifests
        WHEN NEW.withdrawn_at IS NOT NULL
        BEGIN
            DELETE FROM result_cache WHERE result IN (
                SELECT derivation FROM derivation_releases WHERE manifest = NEW.hash
            );
        END;
CREATE TRIGGER result_cache_counted AFTER INSERT ON result_cache
        BEGIN UPDATE result_cache_usage SET bytes = bytes + NEW.bytes; END;
CREATE TRIGGER result_cache_uncounted AFTER DELETE ON result_cache
        BEGIN UPDATE result_cache_usage SET bytes = bytes - OLD.bytes; END;
DELETE FROM "sqlite_sequence";
INSERT INTO "sqlite_sequence" VALUES('audit',5);
COMMIT;

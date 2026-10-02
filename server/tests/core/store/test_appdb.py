"""The app DB (SPEC §12.2, §12.3, D222): its migrations, and the rules its schema holds itself."""

import sqlite3
from pathlib import Path

import pytest

from aibi.core.store.appdb import MIGRATIONS, AppDB

M1 = "sha256:" + "1" * 64
M2 = "sha256:" + "2" * 64
AT = "2026-01-01T00:00:00.000000Z"


@pytest.fixture
def db(tmp_path: Path) -> AppDB:
    opened = AppDB(tmp_path / "app.db")
    with opened.transaction() as connection:
        opened.record_manifest(connection, M1, "lib", AT)
        opened.record_manifest(connection, M2, "lib", AT)
        opened.add_label(connection, "lib", 1, M1, AT, "operator:ada")
        opened.audit(connection, AT, "lib", "operator:ada", "publish", {"label": 1})
    return opened


def test_a_new_database_is_migrated_to_the_latest_version(db: AppDB, tmp_path: Path) -> None:
    assert db.version == len(MIGRATIONS)
    modes = [
        db.connection.execute(f"PRAGMA {name}").fetchone()[0]
        for name in ("journal_mode", "foreign_keys", "secure_delete")
    ]
    assert modes == ["wal", 1, 1]
    db.close()
    again = AppDB(tmp_path / "app.db")  # migrating again changes nothing
    assert again.version == len(MIGRATIONS)
    assert again.labels("lib")[0].manifest == M1


def test_a_database_newer_than_the_server_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "app.db"
    sqlite3.connect(path).execute(f"PRAGMA user_version = {len(MIGRATIONS) + 1}")
    with pytest.raises(RuntimeError, match="newer"):
        AppDB(path)


@pytest.mark.parametrize(
    ("statement", "message"),
    [
        ("DELETE FROM labels", "a label is never removed"),
        ("UPDATE labels SET manifest = '" + M2 + "'", "a label is never changed"),
        ("DELETE FROM manifests WHERE hash = '" + M1 + "'", "a manifest is never removed"),
        ("UPDATE manifests SET dataset = 'other'", "only ever withdrawn"),
        # A withdrawal changes nothing else.
        ("UPDATE manifests SET withdrawn_at = 'now', dataset = 'other'", "only ever withdrawn"),
        ("UPDATE manifests SET withdrawn_at = 'now', recorded_at = 'later'", "only ever withdrawn"),
        ("UPDATE manifests SET withdrawn_at = 'now', hash = '" + M2 + "'", "only ever withdrawn"),
        ("DELETE FROM audit", "append-only"),
        ("UPDATE audit SET action = 'nothing'", "only redaction"),
    ],
)
def test_the_schema_keeps_what_is_never_changed(db: AppDB, statement: str, message: str) -> None:
    with pytest.raises(sqlite3.DatabaseError, match=message), db.transaction() as connection:
        connection.execute(statement)


def test_a_manifest_is_withdrawn_once(db: AppDB) -> None:
    with db.transaction() as connection:
        db.withdraw(connection, M1, AT)
    assert db.is_withdrawn(M1)
    assert db.labels("lib")[0].withdrawn
    with pytest.raises(sqlite3.DatabaseError, match="once"), db.transaction() as connection:
        db.withdraw(connection, M1, AT)
    assert db.withdrawn_manifests() == {M1}
    assert db.live_manifests() == set()


def test_the_audit_trail_changes_only_by_redaction(db: AppDB) -> None:
    with db.transaction() as connection:
        connection.execute("UPDATE audit SET detail = '{}'")
    assert db.connection.execute("SELECT detail FROM audit").fetchone() == ("{}",)


def test_labels_count_up_from_the_highest_ever_issued(db: AppDB) -> None:
    with db.transaction() as connection:
        assert db.next_label(connection, "lib") == 2
        assert db.next_label(connection, "other") == 1
        db.add_label(connection, "lib", 2, M2, AT, "operator:ada")
        db.withdraw(connection, M2, AT)
        assert db.next_label(connection, "lib") == 3  # a withdrawn label keeps its number


def test_a_dataset_has_at_most_one_open_session(db: AppDB) -> None:
    with db.transaction() as connection:
        first = db.open_session(connection, "lib", "h1", M1, AT, "operator:ada")
    with pytest.raises(sqlite3.IntegrityError), db.transaction() as connection:
        db.open_session(connection, "lib", "h2", M1, AT, "operator:ada")
    assert db.live_manifests() == {M1}
    with db.transaction() as connection:
        db.set_draft(connection, first, M2)
    assert db.live_manifests() == {M1, M2}
    with db.transaction() as connection:
        db.end_session(connection, first, "discarded", AT)
        db.open_session(connection, "lib", "h2", M1, AT, "operator:ada")
    assert [session.open for session in db.sessions("lib")] == [False, True]


def test_a_transaction_that_fails_changes_nothing(db: AppDB) -> None:
    def twice(connection: sqlite3.Connection) -> None:
        db.add_label(connection, "lib", 2, M2, AT, "operator:ada")
        db.add_label(connection, "lib", 2, M2, AT, "operator:ada")

    with pytest.raises(sqlite3.IntegrityError), db.transaction() as connection:
        twice(connection)
    assert [label.label for label in db.labels("lib")] == [1]


@pytest.fixture
def curated(db: AppDB) -> AppDB:
    """The database with an ended session, an open one, their draft states and a decided and an
    open proposal."""
    with db.transaction() as connection:
        ended = db.open_session(connection, "lib", "h1", M1, AT, "operator:ada")
        db.record_draft(connection, ended, M2, AT)
        db.end_session(connection, ended, "discarded", AT)
        db.open_session(connection, "lib", "h2", M1, AT, "operator:ada")
        for value in ("a", "b"):
            db.add_proposal(
                connection,
                dataset="lib",
                release=M1,
                descriptor="t",
                pointer="/label",
                value=value,
                proposer="agent:x",
                evidence=None,
                at=AT,
            )
        db.decide_in_session(connection, ended, 1)
        db.decide(connection, 1, "rejected", AT, "operator:ada")
    return db


@pytest.mark.parametrize(
    ("statement", "message"),
    [
        ("DELETE FROM sessions", "a session is never removed"),
        ("UPDATE sessions SET draft = '" + M1 + "' WHERE id = 1", "an ended session never changes"),
        ("UPDATE sessions SET handle_hash = 'h3' WHERE id = 1", "an ended session never changes"),
        ("UPDATE sessions SET base = '" + M2 + "' WHERE id = 2", "keeps its base"),
        ("DELETE FROM drafts", "a draft state is never removed"),
        ("UPDATE drafts SET recorded_at = 'later'", "a draft state is never changed"),
        ("DELETE FROM session_decisions", "a session decision is never removed"),
        ("DELETE FROM proposals", "a proposal is never removed"),
        ("UPDATE proposals SET descriptor = 'u'", "keeps what it proposes"),
        ("UPDATE proposals SET status = 'open' WHERE id = 1", "decided once"),
        ("UPDATE proposals SET status = 'accepted' WHERE id = 1", "decided once"),
        ("UPDATE proposals SET status = 'accepted' WHERE id = 2", "decided once"),
    ],
)
def test_the_schema_keeps_sessions_drafts_and_decisions(
    curated: AppDB, statement: str, message: str
) -> None:
    with pytest.raises(sqlite3.DatabaseError, match=message), curated.transaction() as connection:
        connection.execute(statement)


def test_an_open_session_changes_its_draft_and_handle_and_a_proposal_is_redacted(
    curated: AppDB,
) -> None:
    with curated.transaction() as connection:
        curated.set_draft(connection, 2, M2)
        curated.set_handle(connection, 2, "h3")
        connection.execute("UPDATE proposals SET value = '\"[erased]\"', evidence = NULL")
        assert curated.decide(connection, 2, "accepted", AT, "operator:ada")
        assert not curated.decide(connection, 2, "rejected", AT, "operator:ada")
    session = curated.open_session_of("lib")
    assert session is not None
    assert (session.draft, session.handle_hash) == (M2, "h3")
    assert curated.is_draft_state("lib", M2)
    assert not curated.is_draft_state("other", M2)
    assert [p.status for p in curated.proposals("lib", status=None)] == ["rejected", "accepted"]

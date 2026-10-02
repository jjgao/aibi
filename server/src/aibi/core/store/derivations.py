"""The derivation log (SPEC §7.6, §12.2; D289, D290, D300).

Tables of the app DB. **Derivations**, keyed by derivation id, hold the object the id hashes
(the canonical form with its unit, versions, disclosure setting and packs) in RFC 8785 form, and
the releases it was computed from; they are kept while an issuance names them. A derivation is
recorded when an output first issues its id, and recording it again with the same object changes
nothing; an id is recorded only with the object it is the hash of, so no two objects share one.
**Issuances**, keyed by ``iss:`` and a ULID, record each time ``count_cohort`` or
``run_analysis`` produced an output: its derivation, its request (the document as written and
the parameters used), the SQL as run (``None`` for a cache hit, which names the issuance whose
SQL produced the values instead), the engine and pack versions, and a timestamp. Requests and
SQL are **texts** (``log_texts``), each stored once under its digest and named by it, so that
the cohorts of one call share its request, and a count made again adds its issuance alone
(D300). ``log_usage`` keeps the bytes of what pruning can free (``appdb.LOG_USAGE``): the texts,
the derivations not erased with their objects, and the issuances, each with its row;
``issue_all`` refuses what would take it past ``log_bytes`` (``LogFullError``). Issuance ids hold
80 fresh random bits each and increase with the order they are recorded in, across restarts too
(``Ulids``, seeded from the log's greatest), so that one cannot be guessed from another (D302).

A call's issuances are recorded together, in one transaction, which ``admit`` can still roll back
before it commits: ``count_cohort`` records none for a call that will not be answered (D300).
Their ids are drawn in it, with their times. ``count_cohort``'s issuances are pruned after
``keep_count_issuances_days`` (``prune_expired``: when the store opens, at most
``OPEN_BATCHES`` batches, and in a thread of the log's own every ``PRUNE_EVERY`` seconds, never
in a tool call's) and by an operator (``prune``), ``PRUNE_BATCH`` issuances to a transaction, so
that no call waits long for the app DB; the derivations and texts no issuance names then go with
them.

Erasure's redactors of the log are ``redaction``'s (D290).

The app DB holds its rules by triggers, against every write, ``INSERT OR REPLACE`` included: a
derivation is recorded once, with its object, is removed only by pruning, once no issuance names
it, and changes only once, when erasure takes its object (it keeps its id and kind, resolves to
*erased*, and is never removed); its releases are those its object's record lists, each dataset
once, none withdrawn and each of the dataset the store records its manifest for, never change,
and go only with it; an issuance is recorded once, of a derivation whose object is kept and none
of whose releases is withdrawn (a cache hit naming an issuance of it whose SQL ran), changes only
by redaction, which points its request and SQL at texts that hold ``[erased]``, and is removed
only once its derivation is erased or, for ``count_cohort``'s, by pruning, never one a kept
issuance names; a text never changes, and is removed only while no issuance names it. Each of
those changes needs the permit (``log_permits``) that redaction or pruning writes first and
removes last within its transaction. The triggers guard against the store's own mistakes, not
against a writer with SQL access to the app DB, who could write a permit or drop a trigger
(D289).
"""

import logging
import secrets
import sqlite3
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Literal, cast

from pydantic import JsonValue

from aibi.core.engine.ids import derivation_id
from aibi.core.engine.resolve import typed_constant
from aibi.core.schema.ids import DERIVATION_ID_RE, ISSUANCE_ID_RE
from aibi.core.schema.jsonio import canonical
from aibi.core.schema.limits import LogLimits
from aibi.core.store.appdb import LOG_USAGE, AppDB, loads, text_digest

Kind = Literal["cohort", "result"]
Tool = Literal["count_cohort", "run_analysis"]

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def ulid(milliseconds: int, randomness: bytes) -> str:
    """A ULID in Crockford's base 32: 48 bits of Unix milliseconds, then 80 random bits."""
    if not 0 <= milliseconds < 2**48 or len(randomness) != 10:
        raise ValueError("a ULID is 48 bits of milliseconds and 80 random bits")
    value = (milliseconds << 80) | int.from_bytes(randomness, "big")
    chars = [_CROCKFORD[(value >> (5 * place)) & 31] for place in range(25, -1, -1)]
    return "".join(chars)


def ulid_milliseconds(value: str) -> int:
    """The milliseconds of a ULID, its first 48 bits."""
    number = 0
    for char in value:
        number = number * 32 + _CROCKFORD.index(char)
    return number >> 80


class Ulids:
    """ULIDs that only ever increase from one source, each with 80 fresh random bits: within a
    millisecond, or when the clock steps back, the next is in the millisecond after the last
    one's, so that order comes from the time alone and no id is its neighbour's plus one (D302).
    Many ids in one millisecond run ahead of the clock, a millisecond each; ``seed`` moves the
    last one forward, to an id the source did not make (a log's greatest, after a restart)."""

    def __init__(
        self,
        clock: Callable[[], int] = time.time_ns,
        entropy: Callable[[int], bytes] = secrets.token_bytes,
    ) -> None:
        self._clock = clock
        """Nanoseconds since the Unix epoch."""
        self._entropy = entropy
        self._lock = threading.Lock()
        self._last = -1

    def seed(self, milliseconds: int) -> None:
        """Make every later id's time greater than ``milliseconds``."""
        with self._lock:
            self._last = max(self._last, milliseconds)

    def __call__(self) -> str:
        with self._lock:
            milliseconds = max(self._clock() // 1_000_000, self._last + 1)
            self._last = milliseconds
        return ulid(milliseconds, self._entropy(10))


_ULIDS = Ulids()


def new_issuance_id(ids: Ulids = _ULIDS) -> str:
    """``iss:`` and a new ULID (§5.1), greater than every one ``ids`` made or was seeded with
    before, with 80 fresh random bits: never hashed."""
    return "iss:" + ids()


def kind_of(hashed: JsonValue) -> Kind | None:
    """The kind of derivation an object is the hashed object of: a cohort's holds its
    ``cohort``, a result's its ``view`` (§7.6); ``None`` for neither."""
    if isinstance(hashed, dict):
        if "cohort" in hashed and "view" not in hashed:
            return "cohort"
        if "view" in hashed and "cohort" not in hashed:
            return "result"
    return None


def text_of(value: JsonValue) -> str:
    """A value's RFC 8785 text, as the log stores it."""
    return canonical(value).decode()


@dataclass(frozen=True)
class DerivationRecord:
    id: str
    kind: Kind
    hashed: JsonValue | None = field(repr=False)
    """The object the id hashes; ``None`` once erasure took it. It may hold a person's keys, so
    it is kept out of the ``repr``, as what redaction rewrites is (D290)."""
    releases: JsonValue
    """The releases it was computed from, as outputs list them (§8.1)."""
    recorded_at: str

    @property
    def erased(self) -> bool:
        return self.hashed is None


@dataclass(frozen=True)
class IssuanceRecord:
    id: str
    derivation: str
    tool: Tool
    written: JsonValue = field(repr=False)
    """The document as written; it, the parameters and the SQL are kept out of the ``repr``."""
    params: JsonValue = field(repr=False)
    sql: JsonValue | None = field(repr=False)
    """The SQL as run; ``None`` for a cache hit."""
    values_from: str
    engine: str
    packs: JsonValue
    at: str

    @property
    def cache_hit(self) -> bool:
        return self.values_from != self.id


class DerivationConflictError(ValueError):
    """A derivation id given with an object it is not the hash of, or one recorded before with
    another object, or erased, or with releases that are not its object's."""


class WithdrawnReleaseError(ValueError):
    """A derivation over a withdrawn release, erased or not, which takes no issuance: an
    output computed before the withdrawal and issued after it would record again what an
    erasure took (D289)."""


TIME_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"
"""How the store's clock writes times (``Store.now``, ``stamp``): RFC 3339 in UTC, to the
microsecond, the year in four digits."""


def stamp(moment: datetime) -> str:
    """A time as ``TIME_FORMAT`` writes it, in UTC, the year in four digits whatever the
    platform's ``strftime`` does with years before 1000 (D289)."""
    moment = moment.astimezone(UTC)
    return f"{moment.year:04d}-" + moment.strftime(TIME_FORMAT.removeprefix("%Y-"))


def utc(time: str) -> str:
    """An RFC 3339 time with its offset (``T`` or ``t``, ``Z``, ``z`` or ``±HH:MM``, four-digit
    years, a fraction to the microsecond), written as ``TIME_FORMAT`` writes it; ``ValueError``
    for any other text (ISO 8601's basic and week forms, an offset of hours alone, no offset)
    or an instant beyond the years a time is written in."""
    parsed = typed_constant(time, "datetime")
    if not isinstance(parsed, datetime):
        raise ValueError("a time is RFC 3339, with its offset, within years 0001 to 9999")
    return stamp(parsed)


def _listed(
    kind: Kind, hashed: JsonValue, releases: Sequence[JsonValue]
) -> list[dict[str, JsonValue]]:
    """The releases a derivation lists: at least one, each dataset once, each with its dataset
    and manifest; a cohort's exactly the manifests its object's ``cohort`` names (D289)."""
    listed: list[dict[str, JsonValue]] = []
    for release in releases:
        if not isinstance(release, dict):
            break
        dataset, manifest = release.get("dataset"), release.get("manifest")
        if not isinstance(dataset, str) or not isinstance(manifest, str):
            break
        listed.append(release)
    datasets = {str(release["dataset"]) for release in listed}
    if not listed or len(listed) != len(releases) or len(datasets) != len(listed):
        raise DerivationConflictError(
            "a derivation lists the releases it was computed from, each dataset once"
        )
    if kind == "cohort":
        cohort = cast(dict[str, JsonValue], hashed)["cohort"]
        named = sorted(cohort) if isinstance(cohort, dict) else []
        if sorted(str(release["manifest"]) for release in listed) != named:
            raise DerivationConflictError(
                "a cohort's releases are exactly those its object's cohort is over"
            )
    return listed


def _recorded(db: sqlite3.Connection, listed: Sequence[dict[str, JsonValue]]) -> None:
    """Refuse a release whose manifest the store records under another dataset: the dataset
    is what erasure finds a derivation by (D289, D290)."""
    for release in listed:
        found = db.execute(
            "SELECT dataset FROM manifests WHERE hash = ?", (str(release["manifest"]),)
        ).fetchone()
        if found is not None and found[0] != release["dataset"]:
            raise DerivationConflictError(
                "a derivation lists each release under the dataset the store records it for"
            )


def _live(db: sqlite3.Connection, manifests: Sequence[str]) -> None:
    """Refuse releases any of which is withdrawn, as the transaction ``db`` sees them."""
    withdrawn = db.execute(
        "SELECT count(*) FROM manifests WHERE withdrawn_at IS NOT NULL"
        " AND hash IN (SELECT value FROM json_each(?))",
        (text_of(cast(JsonValue, list(manifests))),),
    ).fetchone()[0]
    if withdrawn:
        raise WithdrawnReleaseError("a derivation over a withdrawn release takes no issuance")


@dataclass(frozen=True)
class Issue:
    """One issuance for ``issue_all`` to record; its id is drawn as it is recorded."""

    derivation: str
    kind: Kind
    hashed: JsonValue = field(repr=False)
    releases: Sequence[JsonValue]
    tool: Tool
    written: JsonValue = field(repr=False)
    """The document as written; it, the parameters and the SQL are kept out of the ``repr``."""
    params: JsonValue = field(repr=False)
    sql: JsonValue | None = field(repr=False)
    engine: str
    packs: JsonValue
    values_from: str | None = None


class LogFullError(ValueError):
    """Recording would take the log past ``log_bytes`` (D300)."""

    def __init__(self, log_bytes: int) -> None:
        super().__init__(f"the derivation log holds its limit of {log_bytes} bytes")
        self.log_bytes = log_bytes


class ErasedMeanwhileError(ValueError):
    """An erasure of a dataset the issuances name was recorded after the mark they were
    canonicalised under: erasure is one pass over what the log holds when it runs, so recording
    them could keep what it took (D290)."""


ERASURES = "erase"
"""The audit trail's action of an erasure (``redaction.ERASE_ACTION``, which imports this module
and so cannot be imported here)."""


class NotAdmittedError(ValueError):
    """``admit`` said, before the transaction committed, that its issuances are not to be
    recorded: the call they belong to will not be answered (D300)."""


PRUNE_EVERY = 3600.0
"""Seconds between two prunings of expired ``count_cohort`` issuances by the log's thread."""
PRUNE_BATCH = 1000
"""The issuances one transaction of a pruning removes, with the derivations and texts they
leave unnamed: tens of milliseconds of the app DB's lock."""
OPEN_BATCHES = 10
"""The batches of expired issuances the store prunes as it opens; the log's thread prunes the
rest at once."""

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Pruning:
    """What a pruning did: the issuances it removed, and whether it removed all it could."""

    removed: int
    finished: bool


def request_of(written: JsonValue, params: JsonValue) -> JsonValue:
    """An issuance's request, as the log stores it: the document as written and the parameters
    used."""
    return {"document": written, "params": params}


def store_text(db: sqlite3.Connection, value: JsonValue) -> str:
    """Store a value's RFC 8785 text in ``log_texts`` in the transaction ``db``, unless it is
    stored; its digest."""
    written = text_of(value)
    digest = text_digest(written)
    db.execute(
        "INSERT INTO log_texts (digest, text) VALUES (?, ?) ON CONFLICT (digest) DO NOTHING",
        (digest, written),
    )
    return digest


def drop_unnamed_texts(db: sqlite3.Connection) -> int:
    """Remove, in a transaction holding a permit, the texts no issuance names; how many."""
    cursor = db.execute(
        "DELETE FROM log_texts WHERE NOT EXISTS (SELECT 1 FROM issuances WHERE request = digest)"
        " AND NOT EXISTS (SELECT 1 FROM issuances WHERE sql = digest)"
    )
    return cursor.rowcount


def _listing(values: Sequence[str]) -> str:
    return text_of(cast(JsonValue, sorted(set(values))))


class DerivationLog:
    """The derivation log in the app DB; ``now`` gives each record's timestamp, ``limits`` how
    long it keeps ``count_cohort``'s issuances and how large it grows, and ``ids`` its issuance
    ids, seeded with the greatest it holds, so that they increase across restarts."""

    def __init__(
        self,
        db: AppDB,
        now: Callable[[], str],
        *,
        limits: LogLimits | None = None,
        ids: Ulids = _ULIDS,
    ) -> None:
        self.db = db
        self.now = now
        self.limits = LogLimits() if limits is None else limits
        self._ids = ids
        with db.lock:
            greatest = db.connection.execute("SELECT max(id) FROM issuances").fetchone()[0]
        if greatest is not None:
            ids.seed(ulid_milliseconds(str(greatest).removeprefix("iss:")))
        self._pruning = threading.Lock()
        self._stop: threading.Event | None = None
        self._thread: threading.Thread | None = None

    def record(
        self,
        db: sqlite3.Connection,
        identifier: str,
        kind: Kind,
        hashed: JsonValue,
        releases: Sequence[JsonValue],
    ) -> bool:
        """Record a derivation in the transaction ``db``, unless it is recorded; whether it was
        new. Raises ``DerivationConflictError`` for an id that is not the hash of ``hashed``,
        recorded with another object, or erased, or for releases that are not its object's or
        are listed under another dataset than the store records their manifest for, and
        ``WithdrawnReleaseError`` for a new one over a withdrawn release."""
        if not DERIVATION_ID_RE.fullmatch(identifier) or derivation_id(hashed) != identifier:
            raise DerivationConflictError("a derivation id is the hash of the object it names")
        if kind != kind_of(hashed):
            raise DerivationConflictError(
                "a derivation's kind is its object's: a cohort's names a cohort, a result's a view"
            )
        text = text_of(hashed)
        found = db.execute("SELECT hashed FROM derivations WHERE id = ?", (identifier,)).fetchone()
        if found is not None:
            if found[0] != text:
                raise DerivationConflictError(
                    "this derivation is recorded erased or with another object"
                )
            return False
        listed = _listed(kind, hashed, releases)
        _recorded(db, listed)
        _live(db, [str(release["manifest"]) for release in listed])
        db.execute(
            "INSERT INTO derivations (id, kind, hashed, releases, recorded_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (identifier, kind, text, text_of(cast(JsonValue, listed)), self.now()),
        )
        db.executemany(
            "INSERT INTO derivation_releases (derivation, dataset, manifest) VALUES (?, ?, ?)",
            [(identifier, str(release["dataset"]), str(release["manifest"])) for release in listed],
        )
        return True

    def issue(
        self,
        *,
        derivation: str,
        kind: Kind,
        hashed: JsonValue,
        releases: Sequence[JsonValue],
        tool: Tool,
        written: JsonValue,
        params: JsonValue,
        sql: JsonValue | None,
        engine: str,
        packs: JsonValue,
        values_from: str | None = None,
    ) -> str:
        """Record one issuance (``issue_all``); its id."""
        issue = Issue(
            derivation=derivation,
            kind=kind,
            hashed=hashed,
            releases=releases,
            tool=tool,
            written=written,
            params=params,
            sql=sql,
            engine=engine,
            packs=packs,
            values_from=values_from,
        )
        [identifier] = self.issue_all([issue])
        return identifier

    def erasure_mark(self) -> int:
        """The mark ``issue_all``'s ``erasures_after`` compares erasures with: the greatest id of
        the audit trail's erasures, which increase as they are recorded (D290)."""
        with self.db.lock:
            found = self.db.connection.execute(
                "SELECT coalesce(max(id), 0) FROM audit WHERE action = ?", (ERASURES,)
            ).fetchone()
        return int(found[0])

    def issue_all(
        self,
        issues: Sequence[Issue],
        *,
        admit: Callable[[], bool] | None = None,
        erasures_after: int | None = None,
    ) -> list[str]:
        """Record issuances, and their derivations that are new, in one transaction; their ids,
        in order, drawn in it with their times. A cache hit gives no SQL and names the issuance
        whose SQL produced its values. A derivation any of whose releases the transaction sees
        withdrawn is refused (``WithdrawnReleaseError``), so that an erasure is never undone by
        an output computed before it and issued after it; so is what would take the log past
        ``log_bytes`` (``LogFullError``); and when ``admit``, asked last, says no, nothing is
        recorded (``NotAdmittedError``). Given ``erasures_after``, the ``erasure_mark`` taken
        before the issuances were canonicalised, an erasure of a dataset they name recorded
        since refuses them all (``ErasedMeanwhileError``, D290). It prunes nothing: the log's
        thread does."""
        for issue in issues:
            if (issue.sql is None) != (issue.values_from is not None):
                raise ValueError("a cache hit names where its values came from, and has no SQL")
            if issue.values_from is not None and not ISSUANCE_ID_RE.fullmatch(issue.values_from):
                raise ValueError("values_from is an issuance id")
        with self.db.transaction() as db:
            if erasures_after is not None:
                _not_erased(db, issues, erasures_after)
            identifiers = [self._issue(db, issue) for issue in issues]
            used = int(db.execute("SELECT bytes FROM log_usage").fetchone()[0])
            if used > self.limits.log_bytes:
                raise LogFullError(self.limits.log_bytes)
            if admit is not None and not admit():
                raise NotAdmittedError("the call these issuances belong to is not answered")
        return identifiers

    def _issue(self, db: sqlite3.Connection, issue: Issue) -> str:
        self.record(db, issue.derivation, issue.kind, issue.hashed, issue.releases)
        recorded = db.execute(
            "SELECT manifest FROM derivation_releases WHERE derivation = ?", (issue.derivation,)
        ).fetchall()
        _live(db, [row[0] for row in recorded])
        if issue.values_from is not None:
            source = db.execute(
                "SELECT derivation FROM issuances WHERE id = ? AND values_from = id",
                (issue.values_from,),
            ).fetchone()
            if source is None or source[0] != issue.derivation:
                raise ValueError(
                    "values_from names an issuance of the same derivation whose SQL ran"
                )
        request = store_text(db, request_of(issue.written, issue.params))
        sql = None if issue.sql is None else store_text(db, issue.sql)
        identifier = new_issuance_id(self._ids)
        db.execute(
            "INSERT INTO issuances (id, derivation, tool, request, sql, values_from, engine,"
            " packs, at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                identifier,
                issue.derivation,
                issue.tool,
                request,
                sql,
                identifier if issue.values_from is None else issue.values_from,
                issue.engine,
                text_of(issue.packs),
                self.now(),
            ),
        )
        return identifier

    def derivation(self, identifier: str) -> DerivationRecord | None:
        with self.db.lock:
            row = self.db.connection.execute(
                "SELECT id, kind, hashed, releases, recorded_at FROM derivations WHERE id = ?",
                (identifier,),
            ).fetchone()
        if row is None:
            return None
        return DerivationRecord(
            row[0], row[1], None if row[2] is None else loads(row[2]), loads(row[3]), row[4]
        )

    def issuance(self, identifier: str) -> IssuanceRecord | None:
        with self.db.lock:
            row = self.db.connection.execute(
                "SELECT i.id, i.derivation, i.tool, r.text, s.text, i.values_from, i.engine,"
                " i.packs, i.at FROM issuances i JOIN log_texts r ON r.digest = i.request"
                " LEFT JOIN log_texts s ON s.digest = i.sql WHERE i.id = ?",
                (identifier,),
            ).fetchone()
        if row is None:
            return None
        request = cast(dict[str, JsonValue], loads(row[3]))
        return IssuanceRecord(
            row[0],
            row[1],
            row[2],
            request["document"],
            request["params"],
            None if row[4] is None else loads(row[4]),
            row[5],
            row[6],
            loads(row[7]),
            row[8],
        )

    def issuances(self, derivation: str) -> list[str]:
        """The ids of a derivation's issuances, in the order they were recorded: by id."""
        with self.db.lock:
            rows = self.db.connection.execute(
                "SELECT id FROM issuances WHERE derivation = ? ORDER BY id", (derivation,)
            ).fetchall()
        return [row[0] for row in rows]

    def usage(self) -> int:
        """The bytes of what pruning can free, as ``log_usage`` keeps them (``log_bytes``)."""
        with self.db.lock:
            return int(self.db.connection.execute("SELECT bytes FROM log_usage").fetchone()[0])

    def measured(self) -> int:
        """The bytes ``usage`` gives, counted again from the tables (``appdb.LOG_USAGE``)."""
        with self.db.lock:
            return int(self.db.connection.execute(f"SELECT {LOG_USAGE}").fetchone()[0])

    def prune(self, before: str) -> int:
        """Remove ``count_cohort``'s issuances recorded before ``before``, but those a kept
        issuance names as ``values_from``, and the derivations and texts no issuance names then;
        how many issuances went (§12.2). ``before`` is an RFC 3339 time with its offset, in any
        spelling, written in UTC as the store's clock writes times (``TIME_FORMAT``) before it is
        compared; ``ValueError`` for anything else (``utc``). It runs ``PRUNE_BATCH`` issuances
        to a transaction, each of which writes the pruning's permit, which the triggers ask of a
        removal, first and removes it last."""
        return self._pruned(utc(before), None).removed

    def _pruned(self, cutoff: str, batches: int | None) -> Pruning:
        removed = done = 0
        while batches is None or done < batches:
            with self.db.transaction() as db:
                found = _prune_batch(db, cutoff)
            removed += found
            done += 1
            if found < PRUNE_BATCH:
                return Pruning(removed, True)
        return Pruning(removed, False)

    def expired(self) -> str | None:
        """The time before which ``count_cohort``'s issuances have expired, as the store's clock
        writes times; ``None`` when they are kept until an operator prunes."""
        days = self.limits.keep_count_issuances_days
        if days is None:
            return None
        now = datetime.strptime(self.now(), TIME_FORMAT).replace(tzinfo=UTC)
        return stamp(now - timedelta(days=days))

    def prune_expired(self, *, batches: int | None = None) -> Pruning:
        """Prune the issuances that ``keep_count_issuances_days`` let expire (``prune``), at most
        ``batches`` batches of them. One pruning runs at a time; another asked meanwhile removes
        nothing and is finished."""
        if not self._pruning.acquire(blocking=False):
            return Pruning(0, True)
        try:
            before = self.expired()
            return Pruning(0, True) if before is None else self._pruned(before, batches)
        finally:
            self._pruning.release()

    def start_pruning(self, *, every: float = PRUNE_EVERY, first: float | None = None) -> None:
        """Prune what expires in a thread of the log's own, ``first`` seconds from now
        (``every`` by default) and every ``every`` seconds after, until ``stop_pruning``. A
        failed pruning is logged by the error's type alone and tried again next time."""
        if self._thread is not None:
            raise RuntimeError("the log's pruning has started")
        stop = threading.Event()
        thread = threading.Thread(
            target=self._prune_every,
            args=(stop, every, every if first is None else first),
            name="aibi-log-pruning",
            daemon=True,
        )
        self._stop, self._thread = stop, thread
        thread.start()

    def stop_pruning(self) -> None:
        """Stop the log's thread, waiting for a pruning under way to end its batch."""
        stop, thread = self._stop, self._thread
        if stop is None or thread is None:
            return
        stop.set()
        thread.join()
        self._stop = self._thread = None

    def _prune_every(self, stop: threading.Event, every: float, first: float) -> None:
        wait = first
        while not stop.wait(wait):
            wait = every
            try:
                while not stop.is_set() and not self.prune_expired(batches=1).finished:
                    pass
            except Exception as error:  # the thread must outlive one failed pruning
                _log.warning("pruning the derivation log failed: %s", type(error).__name__)


def _not_erased(db: sqlite3.Connection, issues: Sequence[Issue], after: int) -> None:
    """Refuse, in the recording transaction, issuances naming a dataset erased since the mark
    ``after``, through their derivations' releases or in their documents as written or their
    parameters (``names_dataset``, D290)."""
    erased = {
        str(row[0])
        for row in db.execute(
            "SELECT DISTINCT dataset FROM audit WHERE action = ? AND id > ?", (ERASURES, after)
        )
        if row[0] is not None
    }
    for issue in issues:
        if erased & {str(release["dataset"]) for release in _releases(issue.releases)} or any(
            names_dataset(issue.written, issue.params, dataset) for dataset in sorted(erased)
        ):
            raise ErasedMeanwhileError("a dataset these issuances name was erased since the mark")


def names_dataset(written: JsonValue, params: JsonValue, dataset: str) -> bool:
    """Whether a document as written names the dataset (its ``dataset``, a cohort's ``dataset``
    or ``datasets``), or a parameter does, one given with the issuance or in the document's own
    ``params``, or one in a list of them: an issuance names a dataset so, or through its
    derivation's releases (D290)."""

    def named(value: JsonValue) -> bool:
        return isinstance(value, str) and value.split("@", 1)[0] == dataset

    found: list[JsonValue] = []
    given = [params]
    if isinstance(written, dict):
        found.append(written.get("dataset"))
        given.append(written.get("params"))
        cohorts = written.get("cohorts")
        if isinstance(cohorts, dict):
            for cohort in cohorts.values():
                if isinstance(cohort, dict):
                    found.append(cohort.get("dataset"))
                    datasets = cohort.get("datasets")
                    if isinstance(datasets, list):
                        found.extend(datasets)
    for values in given:
        if isinstance(values, dict):
            found.extend(values.values())
            for value in values.values():
                if isinstance(value, list):
                    found.extend(value)
    return any(named(value) for value in found)


def _releases(releases: Sequence[JsonValue]) -> list[dict[str, JsonValue]]:
    return [release for release in releases if isinstance(release, dict)]


def _prune_batch(db: sqlite3.Connection, cutoff: str) -> int:
    """One batch of a pruning before ``cutoff``, in the transaction ``db``: at most
    ``PRUNE_BATCH`` issuances, the newest first, so that one a later batch would remove never
    names one already gone, and the derivations and texts they leave unnamed; how many
    issuances went."""
    rows = db.execute(
        "SELECT id, derivation, request, sql FROM issuances i"
        " WHERE tool = 'count_cohort' AND at < ? AND NOT EXISTS ("
        " SELECT 1 FROM issuances k WHERE k.values_from = i.id AND k.id != i.id"
        " AND NOT (k.tool = 'count_cohort' AND k.at < ?))"
        " ORDER BY at DESC, id DESC LIMIT ?",
        (cutoff, cutoff, PRUNE_BATCH),
    ).fetchall()
    if not rows:
        return 0
    db.execute("INSERT OR REPLACE INTO log_permits (kind, before) VALUES ('pruning', ?)", (cutoff,))
    db.execute(
        "DELETE FROM issuances WHERE id IN (SELECT value FROM json_each(?))",
        (_listing([row[0] for row in rows]),),
    )
    derivations = _listing([row[1] for row in rows])
    db.execute(
        "DELETE FROM derivation_releases WHERE derivation IN (SELECT value FROM json_each(?))"
        " AND NOT EXISTS (SELECT 1 FROM issuances i"
        " WHERE i.derivation = derivation_releases.derivation)"
        " AND (SELECT hashed FROM derivations d"
        " WHERE d.id = derivation_releases.derivation) IS NOT NULL",
        (derivations,),
    )
    db.execute(
        "DELETE FROM derivations WHERE id IN (SELECT value FROM json_each(?))"
        " AND NOT EXISTS (SELECT 1 FROM issuances i WHERE i.derivation = derivations.id)"
        " AND hashed IS NOT NULL",
        (derivations,),
    )
    texts = [row[2] for row in rows] + [row[3] for row in rows if row[3] is not None]
    db.execute(
        "DELETE FROM log_texts WHERE digest IN (SELECT value FROM json_each(?))"
        " AND NOT EXISTS (SELECT 1 FROM issuances WHERE request = log_texts.digest)"
        " AND NOT EXISTS (SELECT 1 FROM issuances WHERE sql = log_texts.digest)",
        (_listing(texts),),
    )
    db.execute("DELETE FROM log_permits WHERE kind = 'pruning'")
    return len(rows)


__all__ = [
    "ERASURES",
    "OPEN_BATCHES",
    "PRUNE_BATCH",
    "PRUNE_EVERY",
    "TIME_FORMAT",
    "DerivationConflictError",
    "DerivationLog",
    "DerivationRecord",
    "ErasedMeanwhileError",
    "IssuanceRecord",
    "Issue",
    "Kind",
    "LogFullError",
    "NotAdmittedError",
    "Pruning",
    "Tool",
    "Ulids",
    "WithdrawnReleaseError",
    "drop_unnamed_texts",
    "kind_of",
    "names_dataset",
    "new_issuance_id",
    "request_of",
    "stamp",
    "store_text",
    "text_of",
    "ulid",
    "ulid_milliseconds",
    "utc",
]

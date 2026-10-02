"""The derivation log (SPEC §7.6, §12.2; D289, D290).

Two tables of the app DB. **Derivations**, keyed by derivation id, hold the object the id hashes
(the canonical form with its unit, versions, disclosure setting and packs) in RFC 8785 form, and
the releases it was computed from; they are kept permanently. A derivation is recorded when an
output first issues its id, and recording it again with the same object changes nothing; an id
is recorded only with the object it is the hash of, so no two objects share one. **Issuances**,
keyed by ``iss:`` and a ULID, record each time ``count_cohort`` or ``run_analysis`` produced an
output: its derivation, the document as written, the parameters used, the SQL as run (``None``
for a cache hit, which names the issuance whose SQL produced the values instead), the engine and
pack versions, and a timestamp.

Erasure's redactors of both tables are ``redaction``'s (D290).

The app DB holds its rules by triggers, against every write, ``INSERT OR REPLACE`` included: a
derivation is recorded once, with its object, is never removed, and changes only once, when
erasure takes its object (it keeps its id and kind, and resolves to *erased*); its releases are
those its object's record lists, each dataset once, none withdrawn and each of the dataset the
store records its manifest for, and never change; an issuance
is recorded once, of a derivation whose object is kept and none of whose releases is withdrawn
(a cache hit naming an issuance of it whose SQL ran), changes only by redaction, which rewrites
its document, parameters and SQL into JSON that holds ``[erased]``, and is removed only once its
derivation is erased or, for ``count_cohort``'s, by pruning, never one a kept issuance names.
Each of those changes needs the permit (``log_permits``) that redaction or pruning writes first
and removes last within its transaction. The triggers guard against the store's own mistakes, not
against a writer with SQL access to the app DB, who could write a permit or drop a trigger
(D289).
"""

import secrets
import sqlite3
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal, cast

from pydantic import JsonValue

from aibi.core.engine.ids import derivation_id
from aibi.core.engine.resolve import typed_constant
from aibi.core.schema.ids import DERIVATION_ID_RE, ISSUANCE_ID_RE
from aibi.core.schema.jsonio import canonical
from aibi.core.store.appdb import AppDB, loads

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


class Ulids:
    """ULIDs that only ever increase from one source, as the ULID specification's monotonic
    generation makes them: within a millisecond, or when the clock steps back, the next is the
    last one's randomness plus one, in the last one's millisecond (the next millisecond once the
    randomness is spent)."""

    def __init__(
        self,
        clock: Callable[[], int] = time.time_ns,
        entropy: Callable[[int], bytes] = secrets.token_bytes,
    ) -> None:
        self._clock = clock
        """Nanoseconds since the Unix epoch."""
        self._entropy = entropy
        self._lock = threading.Lock()
        self._last: tuple[int, int] = (-1, 0)

    def __call__(self) -> str:
        with self._lock:
            milliseconds = self._clock() // 1_000_000
            last, randomness = self._last
            if milliseconds > last:
                randomness = int.from_bytes(self._entropy(10), "big")
            else:
                milliseconds, randomness = last, randomness + 1
                if randomness == 2**80:
                    milliseconds, randomness = last + 1, 0
            self._last = (milliseconds, randomness)
        return ulid(milliseconds, randomness.to_bytes(10, "big"))


_ULIDS = Ulids()


def new_issuance_id() -> str:
    """``iss:`` and a new ULID (§5.1), greater than every one this process made before: never
    hashed."""
    return "iss:" + _ULIDS()


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


class DerivationLog:
    """The derivation log in the app DB; ``now`` gives each record's timestamp."""

    def __init__(self, db: AppDB, now: Callable[[], str]) -> None:
        self.db = db
        self.now = now

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
        """Record an issuance, and its derivation if it is new, in one transaction; its id. A
        cache hit gives no SQL and names the issuance whose SQL produced its values. A
        derivation any of whose releases the transaction sees withdrawn is refused
        (``WithdrawnReleaseError``), so that an erasure is never undone by an output computed
        before it and issued after it."""
        if (sql is None) != (values_from is not None):
            raise ValueError("a cache hit names where its values came from, and has no SQL")
        if values_from is not None and not ISSUANCE_ID_RE.fullmatch(values_from):
            raise ValueError("values_from is an issuance id")
        identifier = new_issuance_id()
        with self.db.transaction() as db:
            self.record(db, derivation, kind, hashed, releases)
            recorded = db.execute(
                "SELECT manifest FROM derivation_releases WHERE derivation = ?", (derivation,)
            ).fetchall()
            _live(db, [row[0] for row in recorded])
            if values_from is not None:
                source = db.execute(
                    "SELECT derivation FROM issuances WHERE id = ? AND values_from = id",
                    (values_from,),
                ).fetchone()
                if source is None or source[0] != derivation:
                    raise ValueError(
                        "values_from names an issuance of the same derivation whose SQL ran"
                    )
            db.execute(
                "INSERT INTO issuances (id, derivation, tool, written, params, sql, values_from,"
                " engine, packs, at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    identifier,
                    derivation,
                    tool,
                    text_of(written),
                    text_of(params),
                    None if sql is None else text_of(sql),
                    identifier if values_from is None else values_from,
                    engine,
                    text_of(packs),
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
                "SELECT id, derivation, tool, written, params, sql, values_from, engine, packs, at"
                " FROM issuances WHERE id = ?",
                (identifier,),
            ).fetchone()
        if row is None:
            return None
        return IssuanceRecord(
            row[0],
            row[1],
            row[2],
            loads(row[3]),
            loads(row[4]),
            None if row[5] is None else loads(row[5]),
            row[6],
            row[7],
            loads(row[8]),
            row[9],
        )

    def issuances(self, derivation: str) -> list[str]:
        """The ids of a derivation's issuances, oldest first."""
        with self.db.lock:
            rows = self.db.connection.execute(
                "SELECT id FROM issuances WHERE derivation = ? ORDER BY at, id", (derivation,)
            ).fetchall()
        return [row[0] for row in rows]

    def prune(self, before: str) -> int:
        """Remove ``count_cohort``'s issuances recorded before ``before``, but those a kept
        issuance names as ``values_from``; how many went (§12.2). ``before`` is an RFC 3339
        time with its offset, at any offset, written in UTC as the store's clock writes times
        (``utc``) before it is compared; ``ValueError`` for anything else. The
        pruning's permit, which the triggers ask of a removal, is written first and removed
        last, within the transaction."""
        cutoff = utc(before)
        with self.db.transaction() as db:
            db.execute(
                "INSERT OR REPLACE INTO log_permits (kind, before) VALUES ('pruning', ?)",
                (cutoff,),
            )
            cursor = db.execute(
                "DELETE FROM issuances WHERE tool = 'count_cohort' AND at < ? AND id NOT IN"
                " (SELECT values_from FROM issuances WHERE values_from != id"
                " AND NOT (tool = 'count_cohort' AND at < ?))",
                (cutoff, cutoff),
            )
            db.execute("DELETE FROM log_permits WHERE kind = 'pruning'")
        return cursor.rowcount


__all__ = [
    "TIME_FORMAT",
    "DerivationConflictError",
    "DerivationLog",
    "DerivationRecord",
    "IssuanceRecord",
    "Kind",
    "Tool",
    "Ulids",
    "WithdrawnReleaseError",
    "kind_of",
    "new_issuance_id",
    "stamp",
    "text_of",
    "ulid",
    "utc",
]

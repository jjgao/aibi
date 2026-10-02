"""The result cache (SPEC §8.1, §12.2, §14; D375): a result's digested content, by result id,
in the app DB, so that a call can give it again without running its queries.

A row names the issuance whose queries gave the content (its filler, an issuance of that result
with SQL of its own) and, as the log holds them for it, the engine and the packs' versions that
ran them and the effective *k* its id holds; its bytes and when it was last used; the content
itself is a row apart (``result_cache_contents``), so that marking a row used writes a small one.
The manifests it read are its result's own (``derivation_releases``), which the log keeps while
any issuance of the result does. The app DB's triggers (migration 7) keep it consistent whatever
path changes the log:

- a row is filled only by an issuance of its own result whose queries ran, with that issuance's
  engine and packs' versions and its result's *k*, and only while every release the result read
  is published and not withdrawn; once filled, only its use changes, and its content only goes
  with it;
- a row goes with its filler (its foreign key cascades when pruning or erasure removes it), and
  with any manifest its result read when that manifest is withdrawn (§12.2, D173), in the same
  transaction;
- ``result_cache_usage`` keeps its bytes, which ``result_bytes`` bounds, apart from the
  derivation log's (``log_bytes`` counts only what pruning frees).

Erasure empties the whole cache (``redaction``): it is evictable, and so a result that held the
person, or whose cohorts' derivations erasure took, is never given again. ``fill`` and ``touch``
run in the caller's transaction (the one that records the call's issuances), and ``lookup``
reads under the app DB's lock; what a lookup finds is checked again in that transaction by
whoever gives it (D375)."""

import logging
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass, field

from pydantic import JsonValue

from aibi.core.schema.jsonio import canonical
from aibi.core.schema.limits import CacheLimits
from aibi.core.store.appdb import AppDB, stored_sql

LOGGER = logging.getLogger(__name__)

ROW_BYTES = 512
"""What a row of ``result_cache`` and one of ``result_cache_contents`` take beside the content,
the engine and the packs' text (charged twice over, as every cell is): their keys, filler and
counters, and their entries in the table's three indexes and the contents' primary key, with a
share of their interior pages."""


@dataclass(frozen=True)
class Cached:
    """A result's cached content and the issuance whose queries gave it; the content is kept
    out of the ``repr``."""

    result: str
    issuance: str
    content: bytes = field(repr=False)


def _packs(packs: Mapping[str, JsonValue]) -> str:
    return canonical(dict(packs)).decode()


class ResultCache:
    """The result cache in the app DB ``db`` (module docstring), bounded by ``limits``."""

    def __init__(self, db: AppDB, limits: CacheLimits | None = None) -> None:
        self.db = db
        self.limits = CacheLimits() if limits is None else limits

    def lookup(self, result: str, engine: str, packs: Mapping[str, JsonValue]) -> Cached | None:
        """The cached content of ``result`` filled by this engine with these packs' versions,
        or ``None``: a row another engine or other versions filled is a miss (D375)."""
        with self.db.lock:
            found = self.db.connection.execute(
                "SELECT c.issuance, t.content FROM result_cache c"
                " JOIN result_cache_contents t ON t.result = c.result"
                " WHERE c.result = ? AND c.engine = ? AND c.packs = ?",
                (result, engine, _packs(packs)),
            ).fetchone()
        return None if found is None else Cached(result, found[0], bytes(found[1]))

    def fill(self, db: sqlite3.Connection, *, result: str, issuance: str, content: bytes) -> bool:
        """Cache ``content`` for ``result``, filled by ``issuance``, in the caller's transaction
        ``db``; whether it was cached. The engine, the packs' versions and *k* are the log's for
        them. None is when ``result_bytes`` is 0, the row would take more than a quarter of it,
        or a release the result read is not published (a draft) or withdrawn; the least
        recently used rows are evicted until it fits. A row filled by this engine with these
        versions stays as it is (the first fill is kept, and a second whose content differs is
        logged, by its kind alone: the same id gave another content, §7.6); one another engine
        or other versions filled is replaced. An issuance that is not of ``result`` or ran no
        query is refused (``sqlite3.IntegrityError``, by the app DB's triggers), and the fill's
        writes are undone before it is raised."""
        if not db.in_transaction:
            raise RuntimeError("the result cache is filled in the transaction that records it")
        unpublished = db.execute(
            "SELECT 1 FROM derivation_releases r LEFT JOIN manifests m ON m.hash = r.manifest"
            " WHERE r.derivation = ? AND (m.hash IS NULL OR m.withdrawn_at IS NOT NULL)",
            (result,),
        ).fetchone()
        if unpublished is not None:
            return False
        found = db.execute(
            "SELECT i.engine, i.packs, json_extract(d.hashed, '$.disclosure.min_cell_count')"
            " FROM issuances i JOIN derivations d ON d.id = i.derivation"
            " WHERE i.id = ? AND i.derivation = ? AND i.values_from = i.id",
            (issuance, result),
        ).fetchone()
        engine, packs, k = (None, None, None) if found is None else found
        bound = self.limits.result_bytes
        size = int(
            db.execute(
                f"SELECT {stored_sql(':n')} + 2 * (length(:engine) + length(:packs)) + :row",
                {"n": len(content), "engine": engine or "", "packs": packs or "", "row": ROW_BYTES},
            ).fetchone()[0]
        )
        if bound == 0 or 4 * size > bound:
            return False
        kept = db.execute(
            "SELECT t.content FROM result_cache c"
            " JOIN result_cache_contents t ON t.result = c.result"
            " WHERE c.result = ? AND c.engine = ? AND c.packs = ?",
            (result, engine, packs),
        ).fetchone()
        if kept is not None:
            if bytes(kept[0]) != content:
                LOGGER.error("result cache: a second fill of one result gave another content")
            return False
        db.execute("SAVEPOINT result_cache_fill")
        try:
            db.execute("DELETE FROM result_cache WHERE result = ?", (result,))
            self._evict(db, bound - size)
            db.execute(
                "INSERT INTO result_cache (result, issuance, engine, packs, k, bytes, used)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (result, issuance, engine, packs, k, size, self._used(db)),
            )
            db.execute(
                "INSERT INTO result_cache_contents (result, content) VALUES (?, ?)",
                (result, content),
            )
        except sqlite3.Error:
            db.execute("ROLLBACK TO result_cache_fill")
            db.execute("RELEASE result_cache_fill")
            raise
        db.execute("RELEASE result_cache_fill")
        return True

    def touch(self, db: sqlite3.Connection, result: str) -> None:
        """Mark ``result`` used, in the caller's transaction ``db`` (the one that records the
        hit), so that eviction takes it after the rows used before it."""
        if not db.in_transaction:
            raise RuntimeError("a cached result is marked used in the transaction of its hit")
        db.execute("UPDATE result_cache SET used = ? WHERE result = ?", (self._used(db), result))

    def drop(self, db: sqlite3.Connection, result: str) -> None:
        """Remove ``result``'s row in the caller's transaction ``db``: one whose content no
        longer reads back for its view, which that call's fill then replaces (D376)."""
        if not db.in_transaction:
            raise RuntimeError("a cached result is dropped in the transaction of its call")
        db.execute("DELETE FROM result_cache WHERE result = ?", (result,))

    def usage(self) -> int:
        """The bytes the cached results take, as ``result_bytes`` counts them."""
        with self.db.lock:
            return int(
                self.db.connection.execute("SELECT bytes FROM result_cache_usage").fetchone()[0]
            )

    def clear(self) -> int:
        """Remove every cached result; how many there were."""
        with self.db.transaction() as db:
            return db.execute("DELETE FROM result_cache").rowcount

    def purge(self, floor: int | None) -> int:
        """Remove the results cached under a lower disclosure floor than ``floor`` (their
        effective *k* below it, or none), which no call gives again once the floor is raised
        (§8.4: their ids hold their *k*), so that the rows it now withholds are not kept; how
        many."""
        if floor is None:
            return 0
        with self.db.transaction() as db:
            return db.execute(
                "DELETE FROM result_cache WHERE k IS NULL OR k < ?", (floor,)
            ).rowcount

    def trim(self) -> int:
        """Evict the least recently used results until the cache is within ``result_bytes``
        (all of them at 0), as when the bound was lowered since they were filled; how many."""
        if self.usage() <= self.limits.result_bytes:
            return 0
        with self.db.transaction() as db:
            before = int(db.execute("SELECT count(*) FROM result_cache").fetchone()[0])
            self._evict(db, self.limits.result_bytes)
            return before - int(db.execute("SELECT count(*) FROM result_cache").fetchone()[0])

    def _evict(self, db: sqlite3.Connection, room: int) -> None:
        while int(db.execute("SELECT bytes FROM result_cache_usage").fetchone()[0]) > room:
            oldest = db.execute("SELECT result FROM result_cache ORDER BY used LIMIT 1").fetchone()
            if oldest is None:
                return
            db.execute("DELETE FROM result_cache WHERE result = ?", (oldest[0],))

    def _used(self, db: sqlite3.Connection) -> int:
        db.execute("UPDATE result_cache_usage SET used = used + 1")
        return int(db.execute("SELECT used FROM result_cache_usage").fetchone()[0])


__all__ = ["ROW_BYTES", "Cached", "ResultCache"]

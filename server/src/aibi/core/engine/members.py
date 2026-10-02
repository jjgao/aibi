"""A cohort's members' unit keys, by the reference evaluator, their order and a page of them
(SPEC §7.6, §9.3, §13.3, §14; D331, D333).

``summary.members`` lists the unit keys of a cohort's units, those for which it is TRUE. A key is
its values in the unit table's key columns, in key order, as they are stored (§12.2): integers,
doubles, booleans, text (a string or category column, or one whose datatype nobody declared),
dates and datetimes (in UTC). A key column's cells are all PRESENT, which the validation gate
checks when a release is built (``KEY_NULL``, §13.2).

``keys`` gives a cohort's members' keys by the reference evaluator; the SQL compiler gives the
same (``sql.compile_members``), and a differential test holds the two together (§13.3). ``written``
is a key as the canonical form writes it (``resolved.constant_json``: integers beyond
±(2^53 − 1) as decimal strings, integral doubles as integers, dates as ``YYYY-MM-DD``, datetimes
in UTC with a four-digit year), and keys are ordered as step 8 of §7.6 orders an ``ids`` leaf's
members, by ``digests.order_key`` (their RFC 8785 serialisation compared as UTF-16 code units),
never by DuckDB's collation: one order on every platform, whatever the key's types.

**A page** (``select``) is found without building a key object per member: each member's sort
key (its ``order_key`` with its index appended, which orders as the key alone, since no key's
serialisation begins another's) is made and sorted in runs of ``DEADLINE_KEYS``, and the runs are
merged lazily up to the page's last key, so the server holds one ``bytes`` per member (about 60
bytes for an integer key) and looks at the call's deadline between runs and every
``DEADLINE_KEYS`` keys merged, never sorting more than a run at once (D333).
"""

import heapq
import time
from collections.abc import Iterator, Sequence
from itertools import islice

from pydantic import JsonValue

from aibi.core.engine.evaluate import evaluate
from aibi.core.engine.resolve import ResolvedCohort
from aibi.core.engine.resolved import Constant, constant_json
from aibi.core.engine.worker import CallerDeadline
from aibi.core.schema.digests import order_key

Key = tuple[Constant, ...]
"""A unit key: its values in key order, in their stored types."""

DEADLINE_KEYS = 1 << 16
"""How many keys are keyed, sorted or merged between looks at the call's deadline."""
_INDEX_BYTES = 4
"""The bytes of a member's index appended to its sort key: a listing holds fewer than 2^32."""


def key_columns(cohort: ResolvedCohort) -> tuple[str, ...]:
    """The unit table's key columns, in key order, by name."""
    columns = cohort.release.primary_key(cohort.unit)
    if not columns:
        raise ValueError("a cohort's unit table is keyed (§5.3)")
    return tuple(columns)


def keys(cohort: ResolvedCohort) -> list[Key]:
    """The keys of a cohort's members by the reference evaluator, in row order."""
    columns = key_columns(cohort)
    rows = cohort.release.rows(cohort.unit)
    found: list[Key] = []
    for row, value in enumerate(evaluate(cohort).values):
        if not value.is_true:
            continue
        parts: list[Constant] = []
        for column in columns:
            cell = rows.cell(row, column)
            if cell.value is None or isinstance(cell.value, tuple):
                raise ValueError("a key column's cells are PRESENT and hold one value")
            parts.append(cell.value)
        found.append(tuple(parts))
    return found


def written(key: Key) -> list[JsonValue]:
    """A key as the canonical form writes it (module docstring)."""
    return [constant_json(part) for part in key]


def _look(ends: float | None) -> None:
    if ends is not None and time.monotonic() >= ends:
        raise CallerDeadline


def ranked(given: Sequence[Key], ends: float | None = None) -> Iterator[int]:
    """The indices of ``given`` in the keys' order (module docstring); raises ``CallerDeadline``
    once ``time.monotonic()`` passes ``ends``, looked at every ``DEADLINE_KEYS`` keys."""
    if len(given) >= 1 << (8 * _INDEX_BYTES):
        raise ValueError("a listing holds fewer than 2^32 keys")
    runs: list[list[bytes]] = []
    for start in range(0, len(given), DEADLINE_KEYS):
        _look(ends)
        run = [
            order_key(written(given[index])) + index.to_bytes(_INDEX_BYTES)
            for index in range(start, min(start + DEADLINE_KEYS, len(given)))
        ]
        run.sort()
        runs.append(run)
    _look(ends)
    for merged, found in enumerate(heapq.merge(*runs)):
        if not merged % DEADLINE_KEYS:
            _look(ends)
        yield int.from_bytes(found[-_INDEX_BYTES:])


def ordered(given: Sequence[Key], ends: float | None = None) -> list[Key]:
    """Every key in the keys' order (``ranked``)."""
    return [given[index] for index in ranked(given, ends)]


def select(
    given: Sequence[Key], offset: int, limit: int, ends: float | None = None
) -> tuple[list[Key], bool]:
    """The page of keys from the ``offset``-th in the keys' order, at most ``limit`` of them, and
    whether more follow (module docstring); an offset past the last key reads none."""
    more = offset + limit < len(given)
    if offset >= len(given):
        return [], more
    picked = islice(ranked(given, ends), offset, offset + limit)
    return [given[index] for index in picked], more


__all__ = [
    "DEADLINE_KEYS",
    "Key",
    "key_columns",
    "keys",
    "ordered",
    "ranked",
    "select",
    "written",
]

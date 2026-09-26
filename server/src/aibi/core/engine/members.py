"""A cohort's members' unit keys, by the reference evaluator, and their order (SPEC §7.6, §9.3,
§13.3; D331, D333).

``summary.members`` lists the unit keys of a cohort's units, those for which it is TRUE. A key is
its values in the unit table's key columns, in key order, as they are stored (§12.2): integers,
doubles, booleans, text (a string or category column, or one whose datatype nobody declared),
dates and datetimes (in UTC). A key column's cells are all PRESENT, which the validation gate
checks when a release is built (``KEY_NULL``, §13.2).

``keys`` gives a cohort's members' keys by the reference evaluator; the SQL compiler gives the
same (``sql.compile_members``), and a differential test holds the two together (§13.3). ``written``
is a key as the canonical form writes it (``resolved.constant_json``: integers beyond
±(2^53 − 1) as decimal strings, integral doubles as integers, dates as ``YYYY-MM-DD``, datetimes
in UTC), and ``ordered`` sorts keys as step 8 of §7.6 sorts an ``ids`` leaf's members, by their
RFC 8785 serialisation compared as UTF-16 code units, never by DuckDB's collation: one order on
every platform, whatever the key's types.
"""

import time
from collections.abc import Iterable, Sequence

from pydantic import JsonValue

from aibi.core.engine.evaluate import evaluate
from aibi.core.engine.resolve import ResolvedCohort
from aibi.core.engine.resolved import Constant, constant_json
from aibi.core.engine.worker import CallerDeadline
from aibi.core.schema.jsonio import canonical, utf16_key

Key = tuple[Constant, ...]
"""A unit key: its values in key order, in their stored types."""

DEADLINE_KEYS = 1 << 16
"""How many keys are ordered or read between looks at the call's deadline."""


def key_columns(cohort: ResolvedCohort) -> tuple[str, ...]:
    """The unit table's key columns, in key order, by name."""
    columns = cohort.release.primary_key(cohort.unit)
    if not columns:
        raise ValueError("a cohort's unit table is keyed (§5.3)")
    return tuple(columns)


def keys(cohort: ResolvedCohort) -> list[Key]:
    """The keys of a cohort's members by the reference evaluator, in row order."""
    columns = key_columns(cohort)
    release = cohort.release
    rows = release.rows(cohort.unit)
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


def ordered(given: Iterable[Key], ends: float | None = None) -> list[Key]:
    """Keys in the canonical form's order (module docstring); raises ``CallerDeadline`` once
    ``time.monotonic()`` passes ``ends``, looked at every ``DEADLINE_KEYS`` keys."""
    sorting: list[tuple[bytes, Key]] = []
    for index, key in enumerate(given):
        if ends is not None and not index % DEADLINE_KEYS and time.monotonic() >= ends:
            raise CallerDeadline
        sorting.append((utf16_key(canonical(written(key)).decode()), key))
    sorting.sort(key=lambda pair: pair[0])
    return [key for _, key in sorting]


def page(given: Sequence[Key], offset: int, limit: int) -> tuple[list[Key], bool]:
    """The keys from the ``offset``-th, at most ``limit`` of them, and whether more follow."""
    return list(given[offset : offset + limit]), offset + limit < len(given)


__all__ = [
    "DEADLINE_KEYS",
    "Key",
    "key_columns",
    "keys",
    "ordered",
    "page",
    "written",
]

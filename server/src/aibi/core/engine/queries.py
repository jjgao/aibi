"""Cohorts evaluated by SQL, in a query worker (SPEC §6.6, §12.2, §14; D291–D294).

``run_cohorts`` compiles each resolved cohort against its release's table blobs (``sql``), runs
every query of the document in one worker (``worker``), and reads their rows: each cohort's
accounting, which ``count_parts`` takes as it takes the reference evaluator's, and, when asked,
each unit's truth value, made as it is read from the answer (``TruthValues``, D293). Rows the
queries do not give are a fault, ``QueryError``. It returns the SQL as run and its parameters
beside them, which the derivation log records for an issuance (§12.2): blob paths are recorded
as their digests, so the log names what was read and not where the server keeps it.

The queries may read the table blobs they name, and no other file (D293). A caller pins the
releases (``Store.pin``) before it takes their sources (``Store.sources``, which verifies each
blob as ``Store.load`` does, D221) and until the run ends (§12.2), and gives its own deadline
(``ends``), which the run ends by.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType

from pydantic import JsonValue

from aibi.core.engine.resolve import ResolvedCohort
from aibi.core.engine.sql import Accounting, TruthValues, compile_cohort
from aibi.core.engine.worker import Query, QueryError, Workers
from aibi.core.store.tables import TableSource


@dataclass(frozen=True)
class Counted:
    """A cohort evaluated by SQL."""

    accounting: Accounting
    values: TruthValues | None
    """Each unit's truth value, in row order, when asked for."""
    sql: tuple[str, ...]
    """The statements as run: the counts query, then the values query when asked for."""
    parameters: Mapping[str, JsonValue]
    """Their parameters, blob paths as the blobs' digests, in a mapping that cannot be
    changed."""


def run_cohorts(
    cohorts: Sequence[ResolvedCohort],
    sources: Mapping[str, Mapping[str, TableSource]],
    workers: Workers,
    *,
    values: bool = False,
    ends: float | None = None,
) -> list[Counted]:
    """Each cohort counted, in one worker run. ``sources`` gives each release's tables by
    manifest hash; ``ends`` is the caller's deadline (``Workers.run``). Raises
    ``CompileError``, ``QueryRefused``, ``QueryError`` or ``CallerDeadline``."""
    compiled = [compile_cohort(cohort, sources[cohort.release.manifest]) for cohort in cohorts]
    queries: list[Query] = []
    for cohort in compiled:
        queries.append(Query(cohort.counts_sql, cohort.parameters, cohort.counts_columns))
        if values:
            queries.append(Query(cohort.values_sql, cohort.parameters, cohort.values_columns))
    paths = sorted({path for cohort in compiled for path in cohort.paths})
    rows = workers.run(paths, queries, ends=ends)
    found: list[Counted] = []
    step = 2 if values else 1
    for index, cohort in enumerate(compiled):
        counts = rows[index * step]
        if len(counts) != 1:
            raise QueryError("a counts query gives one row")
        found.append(
            Counted(
                accounting=cohort.accounting(counts[0]),
                values=cohort.truth_values(rows[index * step + 1]) if values else None,
                sql=(cohort.counts_sql, cohort.values_sql) if values else (cohort.counts_sql,),
                parameters=MappingProxyType(cohort.parameters_json()),
            )
        )
    return found


__all__ = ["Counted", "run_cohorts"]

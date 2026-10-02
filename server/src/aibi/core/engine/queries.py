"""Cohorts evaluated by SQL, in a query worker (SPEC §6.6, §12.2, §14; D291–D294).

``run_cohorts`` compiles each resolved cohort against its release's table blobs (``sql``), runs
every query of the document in one worker (``worker``), and reads their rows: each cohort's
accounting, which ``count_parts`` takes as it takes the reference evaluator's, and, when asked,
each unit's truth value, made as it is read from the answer (``TruthValues``, D293).
``run_crossed`` counts cohorts and, in the same run, crosses cohorts with predicates
(``sql.compile_crossing``), whose answers are integer counts whose size never depends on the number
of units, and ``run_views`` materialises variables over cohorts in that run too
(``sql.compile_materialised``), whose answers grow with the distinct values and, for ``max``,
``min`` and ``mean``, with the units, which the answer cap bounds: what ``run_analysis`` runs
(D318, D327); and it lists cohorts' members' unit keys in that run too (``sql.compile_members``), a
row per member, which the caller pages in the canonical form's order (D333); and it lists the
members of a pack analysis's cohorts with each variable's value in that run too
(``sql.compile_inputs``), a row per member and variable, which the caller orders by key (D342).
Rows the queries do not give are a fault, ``QueryError``. It returns the SQL as run and its
parameters beside them, which the derivation log records for an issuance (§12.2): blob paths are
recorded as their digests, so the log names what was read and not where the server keeps it.

The queries may read the table blobs they name, and no other file (D293). A caller pins the
releases (``Store.pin``) before it takes their sources (``Store.sources``, which verifies each
blob as ``Store.load`` does, D221) and until the run ends (§12.2), and gives its own deadline
(``ends``), which the run ends by.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType

from pydantic import JsonValue

from aibi.core.engine.inputs import Listed, TooManyCells
from aibi.core.engine.members import Key
from aibi.core.engine.resolve import ResolvedCohort, ResolvedVariable
from aibi.core.engine.sql import (
    Accounting,
    Crossing,
    TooManyListed,
    TruthValues,
    compile_cohort,
    compile_crossing,
    compile_inputs,
    compile_materialised,
    compile_members,
)
from aibi.core.engine.variables import Joint, Materialised
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


@dataclass(frozen=True)
class CrossingRun:
    """Cohorts crossed with predicates by SQL (``sql.compile_crossing``), with the statements
    as run and their parameters, as ``Counted`` has them."""

    crossing: Crossing
    sql: tuple[str, ...]
    parameters: Mapping[str, JsonValue]


def run_crossed(
    cohorts: Sequence[ResolvedCohort],
    crossings: Sequence[tuple[Sequence[ResolvedCohort], Sequence[ResolvedCohort]]],
    sources: Mapping[str, Mapping[str, TableSource]],
    workers: Workers,
    *,
    ends: float | None = None,
) -> tuple[list[Counted], list[CrossingRun]]:
    """Each cohort counted and each crossing (its cohorts and predicates) counted, in one worker
    run; no query gives a row per unit (D318). Raises as ``run_cohorts`` does."""
    ran = run_views(cohorts, crossings, (), sources, workers, ends=ends)
    return ran.counted, ran.crossed


@dataclass(frozen=True)
class MaterialisedRun:
    """Variables materialised over cohorts by SQL (``sql.compile_materialised``): for each
    cohort, each variable's values and, for two or more, their joint accounting; with the
    statements as run and their parameters, as ``Counted`` has them."""

    materialised: tuple[tuple[tuple[Materialised, ...], Joint | None], ...]
    sql: tuple[str, ...]
    parameters: Mapping[str, JsonValue]
    shared: tuple[int, ...] = ()
    """The units each pair of cohorts shares, when asked for (``sql.pairs``' order, D339)."""


@dataclass(frozen=True)
class MembersRun:
    """A cohort's members' unit keys by SQL (``sql.compile_members``), in row order and made as
    they are read (``sql.ListedKeys``), for the caller to take a page of (``members.select``),
    with the statement as run and its parameters, as ``Counted`` has them."""

    keys: Sequence[Key]
    sql: tuple[str, ...]
    parameters: Mapping[str, JsonValue]


@dataclass(frozen=True)
class InputsRun:
    """Cohorts' members with each variable's value by SQL (``sql.compile_inputs``), per cohort in
    ``rid`` order, for the caller to order by key (``inputs.ordered``), with the statements as
    run and their parameters, as ``Counted`` has them (D342)."""

    listed: tuple[Listed, ...]
    sql: tuple[str, ...]
    parameters: Mapping[str, JsonValue]


@dataclass(frozen=True)
class ViewsRun:
    counted: list[Counted]
    crossed: list[CrossingRun]
    materialised: list[MaterialisedRun]
    listed: list[MembersRun] = field(default_factory=list[MembersRun])
    inputs: list[InputsRun] = field(default_factory=list[InputsRun])


def run_views(
    cohorts: Sequence[ResolvedCohort],
    crossings: Sequence[tuple[Sequence[ResolvedCohort], Sequence[ResolvedCohort]]],
    materialisations: Sequence[tuple[Sequence[ResolvedCohort], Sequence[ResolvedVariable]]],
    sources: Mapping[str, Mapping[str, TableSource]],
    workers: Workers,
    *,
    members: Sequence[ResolvedCohort] = (),
    shared: Sequence[bool] = (),
    declared: Sequence[bool] = (),
    inputs: Sequence[tuple[Sequence[ResolvedCohort], Sequence[ResolvedVariable]]] = (),
    ends: float | None = None,
) -> ViewsRun:
    """Each cohort counted, each crossing counted, each materialisation (its cohorts and
    variables) read, with the units each pair of its cohorts shares where ``shared`` says so (by
    materialisation, none by default, D339) and memberships' declared categories alone where
    ``declared`` does (by materialisation, none by default, D384), each of ``members``' members'
    keys listed, and each of ``inputs``' cohorts' members listed with its variables' values, in
    one worker run (D318, D327, D333, D342), the server's reading of a materialisation's and a
    listing's rows held to ``ends`` as the worker is. Raises as ``run_cohorts`` does, and
    ``sql.TooManyListed`` or ``inputs.TooManyCells`` for a listing of inputs over its caps,
    naming it (``listing``)."""
    compiled = [compile_cohort(cohort, sources[cohort.release.manifest]) for cohort in cohorts]
    crossed = [
        compile_crossing(members, asked, sources[members[0].release.manifest])
        for members, asked in crossings
    ]
    overlaps = [*shared, *[False] * (len(materialisations) - len(shared))]
    listed_declared = [*declared, *[False] * (len(materialisations) - len(declared))]
    made = [
        compile_materialised(
            members,
            variables,
            sources[members[0].release.manifest],
            shared=counted,
            declared=fixed,
        )
        for (members, variables), counted, fixed in zip(
            materialisations, overlaps, listed_declared, strict=True
        )
    ]
    listing = [compile_members(cohort, sources[cohort.release.manifest]) for cohort in members]
    handed = [
        compile_inputs(cohorts_of, variables, sources[cohorts_of[0].release.manifest])
        for cohorts_of, variables in inputs
    ]
    queries = [Query(c.counts_sql, c.parameters, c.counts_columns) for c in compiled]
    for crossing in crossed:
        queries += [
            Query(statement, crossing.parameters, columns)
            for statement, columns in zip(crossing.statements, crossing.columns, strict=True)
        ]
    for materialisation in made:
        queries += [
            Query(statement, materialisation.parameters, columns, values)
            for statement, columns, values in zip(
                materialisation.statements,
                materialisation.columns,
                materialisation.values,
                strict=True,
            )
        ]
    queries += [Query(m.statement, m.parameters, m.columns, m.values) for m in listing]
    for given in handed:
        queries += [
            Query(statement, given.parameters, columns, values)
            for statement, columns, values in zip(
                given.statements, given.columns, given.values, strict=True
            )
        ]
    paths = sorted(
        {path for c in compiled for path in c.paths}
        | {p for x in crossed for p in x.paths}
        | {p for m in made for p in m.paths}
        | {p for m in listing for p in m.paths}
        | {p for i in handed for p in i.paths}
    )
    rows = workers.run(paths, queries, ends=ends)
    counted: list[Counted] = []
    for index, cohort in enumerate(compiled):
        if len(rows[index]) != 1:
            raise QueryError("a counts query gives one row")
        counted.append(
            Counted(
                accounting=cohort.accounting(rows[index][0]),
                values=None,
                sql=(cohort.counts_sql,),
                parameters=MappingProxyType(cohort.parameters_json()),
            )
        )
    found: list[CrossingRun] = []
    at = len(compiled)
    for crossing in crossed:
        answers = rows[at : at + len(crossing.statements)]
        at += len(crossing.statements)
        found.append(
            CrossingRun(
                crossing.read(answers),
                crossing.statements,
                MappingProxyType(crossing.parameters_json()),
            )
        )
    materialised: list[MaterialisedRun] = []
    for materialisation in made:
        answers = rows[at : at + len(materialisation.statements)]
        at += len(materialisation.statements)
        materialised.append(
            MaterialisedRun(
                materialisation.read(answers, ends),
                materialisation.statements,
                MappingProxyType(materialisation.parameters_json()),
                materialisation.read_shared(answers),
            )
        )
    listed = [
        MembersRun(
            compiled_members.read(rows[at + index]),
            (compiled_members.statement,),
            MappingProxyType(compiled_members.parameters_json()),
        )
        for index, compiled_members in enumerate(listing)
    ]
    at += len(listing)
    read: list[InputsRun] = []
    for index, given in enumerate(handed):
        answers = rows[at : at + len(given.statements)]
        at += len(given.statements)
        try:
            listed_inputs = given.read(answers, ends)
        except (TooManyListed, TooManyCells) as many:
            many.listing = index
            raise
        read.append(
            InputsRun(
                listed_inputs,
                given.statements,
                MappingProxyType(given.parameters_json()),
            )
        )
    return ViewsRun(counted, found, materialised, listed, read)


__all__ = [
    "Counted",
    "CrossingRun",
    "InputsRun",
    "MaterialisedRun",
    "MembersRun",
    "ViewsRun",
    "run_cohorts",
    "run_crossed",
    "run_views",
]

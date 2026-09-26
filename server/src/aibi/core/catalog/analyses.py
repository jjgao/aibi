"""``run_analysis``: a document's views run, disclosed, issued and returned as result envelopes
(SPEC §7.4, §8, §9, §11.1, §12.2; D318).

The document is loaded, its releases resolved and pinned, its cohorts canonicalised and its
views checked as ``validate_document`` does them (``cohorts.canonical_document``), and the call
fails with the first refusal, as every tool but ``validate_document`` does; a document without
views is refused (``MISSING_MEMBER`` at ``/views``). Every query of the document then runs in one
query worker (§14), which ends ``RECORD_SECONDS`` before the call's deadline
(``queries.run_crossed``): each cohort counted once, and each crossing of a view's cohorts with its
predicates (each predicate that holds a lift under the other lift rule too) counted once, views
asking the same one sharing it; and each materialisation of a view's variables over its cohorts
read once, views asking the same one sharing it (D327); and each cohort a ``summary.members`` view
names has its members' keys listed once (D333). A crossing's answer is integer counts,
whose size depends on the number of cohorts and predicates and never on the units (D318); a
materialisation's grows with the values the units hold, and for ``max``, ``min`` and ``mean``
with the units, so the answer cap can bound it, and a listing of keys has a row per member: an
answer over the cap, or a run over the worker's seconds or memory, names the first view that
lists keys, else the view whose materialisation is widest, else the one whose crossing is. Each
view is then computed by its analysis (``existence.compare``, ``distribution.summarise``,
``members.list_members``), disclosed under its effective *k*,
and made into its envelope (``results.envelope``); a column with more categories than a result
lists refuses the call (``LIMIT_EXCEEDED``, D328).

A view of an analysis that assumes independent groups whose cohorts share units, without
``overlap: "allow"``, refuses the call (``COHORTS_OVERLAP``, §7.4): the refusal reports each pair
of cohorts that share units as the count of the cohort of their shared units, counted in a second
worker run, disclosed and issued as ``count_cohort``'s counts are (§8.6).

**Issuances** (§12.2): one per view, of its result, naming the call's request (the document as
written and the parameters used, stored once) and the SQL as run, its cohorts' counts and then its
crossing or its materialisation, ``{"queries": [{"statements", "parameters"}, …]}``; and one per
cohort the views name, of its cohort id, with its SQL, so that ``explain`` resolves every id a
result names. They are recorded in one transaction, only while ``ANSWER_SECONDS`` of the deadline
are left, and not at all after a withdrawal, an erasure of a dataset the document names recorded
meanwhile, or with the log full, as ``count_cohort``'s are (D300). Results are not cached in this
slice (D318): every issuance's values are its own.
"""

import time
from collections.abc import Callable, Mapping, Sequence
from typing import cast

from pydantic import JsonValue

from aibi.core.analyses import distribution, members
from aibi.core.analyses.existence import CohortAt, compare
from aibi.core.analyses.results import Outcome, envelope, issued_packs
from aibi.core.analyses.views import CheckedView
from aibi.core.catalog.cohorts import (
    ANSWER_SECONDS,
    ENGINE,
    RECORD_SECONDS,
    canonical_document,
    counted_cohort,
    erased_meanwhile,
    late,
    log_full,
    named_count,
    parameters,
    withdrawn,
)
from aibi.core.catalog.service import DEADLINE, Catalog, Deadline, ToolRefused
from aibi.core.engine.canonical import CanonicalCohort, intersection
from aibi.core.engine.queries import ViewsRun, run_cohorts, run_views
from aibi.core.engine.resolve import ResolvedCohort, ResolvedVariable
from aibi.core.engine.variables import Joint, Materialised
from aibi.core.engine.worker import CallerDeadline, QueryRefused, Workers
from aibi.core.schema.analyses import DistributionParams, ExistenceParams, MembersParams
from aibi.core.schema.cohorts import AnalysisResults, RunAnalysis
from aibi.core.schema.jsonio import canonical, pointer
from aibi.core.schema.limits import (
    CATEGORIES,
    MAX_CATEGORIES,
    QUERY_ANSWER_BYTES,
    QUERY_MEMORY,
    QUERY_SECONDS,
)
from aibi.core.schema.output import Segment, data, text
from aibi.core.schema.refusals import Limit, Refusal, RefusalCode
from aibi.core.schema.results import CohortCount
from aibi.core.store.derivations import (
    ErasedMeanwhileError,
    Issue,
    LogFullError,
    NotAdmittedError,
    WithdrawnReleaseError,
)
from aibi.core.store.tables import TableSource


def _no_workers() -> ToolRefused:
    return ToolRefused(
        [
            Refusal(
                code=RefusalCode.NOT_SUPPORTED,
                path=None,
                message=[text("This server runs no query workers, so it runs no analysis")],
            )
        ]
    )


def _guarded[T](
    run: Callable[[float | None], T], deadline: Deadline | None, path: str | None = None
) -> T:
    ends = None if deadline is None else deadline.at - RECORD_SECONDS
    if ends is not None and time.monotonic() >= ends:
        raise late(cast(Deadline, deadline))
    try:
        return run(ends)
    except CallerDeadline:
        raise late(cast(Deadline, deadline)) from None
    except QueryRefused as refused:
        found = refused.refusal
        if (
            found.path is None
            and found.limit is not None
            and found.limit.name in (QUERY_ANSWER_BYTES, QUERY_SECONDS, QUERY_MEMORY)
        ):
            found = found.model_copy(update={"path": path})
        raise ToolRefused([found]) from None


def _run(
    cohorts: Sequence[ResolvedCohort],
    crossings: Sequence[tuple[Sequence[ResolvedCohort], Sequence[ResolvedCohort]]],
    materialisations: Sequence[tuple[Sequence[ResolvedCohort], Sequence[ResolvedVariable]]],
    sources: Mapping[str, Mapping[str, TableSource]],
    workers: Workers,
    deadline: Deadline | None,
    widest: CheckedView,
    listed: Sequence[ResolvedCohort] = (),
) -> ViewsRun:
    """The call's queries in one run, ``listed`` the cohorts whose members' keys are listed; an
    answer over the cap names the view to narrow (``widest``, module docstring)."""
    return _guarded(
        lambda ends: run_views(
            cohorts, crossings, materialisations, sources, workers, members=listed, ends=ends
        ),
        deadline,
        pointer(["views", widest.index]),
    )


def _too_large(view: CheckedView, column: int) -> ToolRefused:
    return ToolRefused(
        [
            Refusal(
                code=RefusalCode.NOT_SUPPORTED,
                path=pointer(["views", view.index, "params", "columns", column]),
                message=[
                    text("The column's values, or their standard deviation, lie beyond "),
                    text("±(2^53 − 1), which an output does not hold (§8.2, D328)"),
                ],
            )
        ]
    )


def _widest(views: Sequence[CheckedView]) -> CheckedView:
    """The view an answer over the cap, or a run over its seconds or memory, names: the first that
    lists keys, a row per member, else the one whose materialisation is widest, whose answer grows
    with the units, else the one whose crossing is (module docstring)."""
    listing = [view for view in views if isinstance(view.params, MembersParams)]
    if listing:
        return listing[0]
    materialising = [view for view in views if view.variables]
    if materialising:
        return max(materialising, key=lambda view: len(view.cohorts) * len(view.variables))
    return max(views, key=lambda view: len(view.cohorts) * (len(view.predicates) + 1))


def _too_many(view: CheckedView, column: int) -> ToolRefused:
    return ToolRefused(
        [
            Refusal(
                code=RefusalCode.LIMIT_EXCEEDED,
                path=pointer(["views", view.index, "params", "columns", column]),
                message=[
                    text(f"The column has more than {MAX_CATEGORIES} categories in a cohort, "),
                    text("more than a result lists (§14)"),
                ],
                limit=Limit(name=CATEGORIES, max=MAX_CATEGORIES),
            )
        ]
    )


def _issue(
    catalog: Catalog, issues: Sequence[Issue], deadline: Deadline | None, erasures: int
) -> list[str]:
    def admit() -> bool:
        return deadline is None or time.monotonic() < deadline.at - ANSWER_SECONDS

    try:
        return catalog.store.derivations.issue_all(issues, admit=admit, erasures_after=erasures)
    except WithdrawnReleaseError:
        raise ToolRefused([withdrawn([])]) from None
    except ErasedMeanwhileError:
        raise ToolRefused([erased_meanwhile()]) from None
    except LogFullError as full:
        raise ToolRefused([log_full(full.log_bytes)]) from None
    except NotAdmittedError:
        raise late(cast(Deadline, deadline)) from None


def run_analysis(catalog: Catalog, request: RunAnalysis) -> AnalysisResults:
    """Each view of a document run, disclosed and issued (module docstring)."""
    workers = catalog.workers
    if workers is None:
        raise _no_workers()
    deadline = DEADLINE.get()
    store = catalog.store
    erasures = store.derivations.erasure_mark()
    with store.pin(background=True) as pin:
        found = canonical_document(catalog, pin, request.document)
        refusals = found.every_refusal()
        if refusals or found.canonical is None or found.loaded.document is None:
            raise ToolRefused(refusals[:1])
        document = found.loaded.document
        if not document.views:
            raise ToolRefused(
                [
                    Refusal(
                        code=RefusalCode.MISSING_MEMBER,
                        path="/views",
                        message=[
                            text("run_analysis runs a document's views, and it has none; "),
                            text("count_cohort counts its cohorts"),
                        ],
                    )
                ]
            )
        views = found.views
        cohorts: dict[str, CanonicalCohort] = {}
        for view in views:
            for cohort in view.cohorts:
                cohorts.setdefault(cohort.computation_id, cohort)
        crossings: dict[tuple[tuple[str, ...], tuple[str, ...]], int] = {}
        asked: list[tuple[list[ResolvedCohort], list[ResolvedCohort]]] = []
        materialisations: dict[tuple[tuple[str, ...], tuple[str, ...]], int] = {}
        read: list[tuple[list[ResolvedCohort], list[ResolvedVariable]]] = []
        listings: dict[str, int] = {}
        listed: list[ResolvedCohort] = []
        for view in views:
            if isinstance(view.params, MembersParams):
                [cohort] = view.cohorts
                if cohort.computation_id not in listings:
                    listings[cohort.computation_id] = len(listed)
                    listed.append(cohort.resolved)
                continue
            members_of = [cohort.resolved for cohort in view.cohorts]
            if view.variables:
                key = _materialisation_key(view)
                if key not in materialisations:
                    materialisations[key] = len(read)
                    read.append((members_of, _distinct(view)))
                continue
            key = _crossing_key(view)
            if key not in crossings:
                crossings[key] = len(asked)
                asked.append((members_of, [predicate.resolved for predicate in view.predicates]))
        manifests = sorted({cohort.release.manifest for cohort in cohorts.values()})
        sources = {manifest: store.sources(manifest) for manifest in manifests}
        ran_views = _run(
            [cohort.resolved for cohort in cohorts.values()],
            asked,
            read,
            sources,
            workers,
            deadline,
            _widest(views),
            listed,
        )
        by_id = dict(zip(cohorts, ran_views.counted, strict=True))
        written = dict(request.document)
        params = dict(found.loaded.params_used)
        outcomes: list[Outcome] = []
        issues: list[Issue] = []
        for view in views:
            positions = [
                CohortAt(cohort, by_id[cohort.computation_id].accounting) for cohort in view.cohorts
            ]
            used = [by_id[cohort.computation_id] for cohort in view.cohorts]
            queries: list[JsonValue] = [
                {"statements": list(run.sql), "parameters": dict(run.parameters)} for run in used
            ]
            if isinstance(view.params, MembersParams):
                [position] = positions
                run_listed = ran_views.listed[listings[position.cohort.computation_id]]
                outcomes.append(
                    members.list_members(position, run_listed.keys, view.params, k=view.disclosure)
                )
                queries.append(
                    {"statements": list(run_listed.sql), "parameters": dict(run_listed.parameters)}
                )
                issues.append(_result_issue(view, {"queries": queries}, written, params))
                continue
            if isinstance(view.params, DistributionParams):
                made = ran_views.materialised[materialisations[_materialisation_key(view)]]
                try:
                    outcomes.append(
                        distribution.summarise(
                            positions,
                            view.variables,
                            _expanded(view, made.materialised),
                            view.params,
                            k=view.disclosure,
                            ends=None if deadline is None else deadline.at - RECORD_SECONDS,
                        )
                    )
                except CallerDeadline:
                    raise late(cast(Deadline, deadline)) from None
                except distribution.TooManyCategories as many:
                    raise _too_many(view, many.column) from None
                except distribution.TooLarge as large:
                    raise _too_large(view, large.column) from None
                queries.append({"statements": list(made.sql), "parameters": dict(made.parameters)})
                issues.append(_result_issue(view, {"queries": queries}, written, params))
                continue
            ran = ran_views.crossed[crossings[_crossing_key(view)]]
            shared = ran.crossing.overlapping()
            if (
                shared
                and view.analysis.entry.fields.assumes_independent_groups
                and not view.overlap
            ):
                _overlap(
                    catalog, view, shared, sources, workers, deadline, erasures, written, params
                )
            params_of = view.params
            assert isinstance(params_of, ExistenceParams), "the core's analyses are known"
            outcomes.append(
                compare(
                    positions,
                    view.predicates,
                    ran.crossing,
                    params_of,
                    reference=view.reference,
                    overlap=bool(shared) and view.overlap,
                    k=view.disclosure,
                )
            )
            queries.append({"statements": list(ran.sql), "parameters": dict(ran.parameters)})
            issues.append(_result_issue(view, {"queries": queries}, written, params))
        for identifier, cohort in cohorts.items():
            run = by_id[identifier]
            _, issue = counted_cohort(
                cohort,
                run.accounting,
                run.sql,
                run.parameters,
                written,
                params,
                tool="run_analysis",
            )
            issues.append(issue)
        issued = _issue(catalog, issues, deadline, erasures)
    results = [
        envelope(
            view,
            outcome,
            issuance=issuance,
            written=written,
            params=params,
            engine=ENGINE,
        )
        for view, outcome, issuance in zip(views, outcomes, issued[: len(views)], strict=True)
    ]
    return AnalysisResults(results=results, params=parameters(found.loaded))


def _crossing_key(view: CheckedView) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """A view's crossing, by its cohorts' and predicates' computation ids: views that ask the
    same crossing share its run."""
    return (
        tuple(cohort.computation_id for cohort in view.cohorts),
        tuple(predicate.computation_id for predicate in view.predicates),
    )


def _forms(view: CheckedView) -> list[str]:
    return [canonical(variable.form).decode() for variable in view.variables]


def _materialisation_key(view: CheckedView) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """A view's materialisation, by its cohorts' computation ids and its distinct variables'
    canonical forms: views that read the same one share its run, and a variable a view reads
    twice is read once (D327)."""
    return (
        tuple(cohort.computation_id for cohort in view.cohorts),
        tuple(dict.fromkeys(_forms(view))),
    )


def _distinct(view: CheckedView) -> list[ResolvedVariable]:
    """A view's variables, each canonical form once, in the order they first appear."""
    firsts: dict[str, ResolvedVariable] = {}
    for form, variable in zip(_forms(view), view.variables, strict=True):
        firsts.setdefault(form, variable.resolved)
    return list(firsts.values())


def _expanded(
    view: CheckedView, read: Sequence[tuple[Sequence[Materialised], Joint | None]]
) -> list[tuple[list[Materialised], Joint | None]]:
    """A materialisation of a view's distinct variables given back for each of its variables;
    the joint accounting of distinct variables is that of all of them, and one variable read
    several times is its own."""
    forms = _forms(view)
    order = list(dict.fromkeys(forms))
    expanded: list[tuple[list[Materialised], Joint | None]] = []
    for found, together in read:
        joined = together
        if joined is None and len(forms) > 1:
            [one] = found
            joined = Joint(one.n, one.excluded_units, one.excluded)
        expanded.append(([found[order.index(form)] for form in forms], joined))
    return expanded


def _result_issue(
    view: CheckedView, sql: JsonValue, written: dict[str, JsonValue], params: dict[str, JsonValue]
) -> Issue:
    return Issue(
        derivation=view.identity.id,
        kind="result",
        hashed=view.identity.hashed(),
        releases=[cast(JsonValue, view.release.model_dump(mode="json"))],
        tool="run_analysis",
        written=written,
        params=params,
        sql=sql,
        engine=ENGINE,
        packs=cast(JsonValue, issued_packs(view)),
    )


def _overlap(
    catalog: Catalog,
    view: CheckedView,
    shared: Sequence[tuple[int, int]],
    sources: Mapping[str, Mapping[str, TableSource]],
    workers: Workers,
    deadline: Deadline | None,
    erasures: int,
    written: dict[str, JsonValue],
    params: dict[str, JsonValue],
) -> None:
    """Refuse a view whose cohorts share units (§7.4), reporting each shared part as a cohort
    count, counted, disclosed and issued (§8.6)."""
    overlaps: list[CanonicalCohort] = []
    for first, second in shared:
        made = intersection(view.cohorts[first], view.cohorts[second], registry=catalog.registry)
        if isinstance(made, Refusal):
            if made.path is None:
                made = made.model_copy(update={"path": pointer(["views", view.index])})
            raise ToolRefused([made])
        overlaps.append(made)
    runs = _guarded(
        lambda ends: run_cohorts(
            [cohort.resolved for cohort in overlaps], sources, workers, ends=ends
        ),
        deadline,
        pointer(["views", view.index]),
    )
    counted = [
        counted_cohort(
            cohort, run.accounting, run.sql, run.parameters, written, params, tool="run_analysis"
        )
        for cohort, run in zip(overlaps, runs, strict=True)
    ]
    issued = _issue(catalog, [issue for _, issue in counted], deadline, erasures)
    counts: list[CohortCount] = [
        named_count(cohort, parts, identifier).count
        for cohort, (parts, _), identifier in zip(overlaps, counted, issued, strict=True)
    ]
    pairs: list[Segment] = []
    for index, (first, second) in enumerate(shared):
        if index:
            pairs.append(text("; "))
        pairs += [data(view.cohorts[first].name), text(" with "), data(view.cohorts[second].name)]
    raise ToolRefused(
        [
            Refusal(
                code=RefusalCode.COHORTS_OVERLAP,
                path=pointer(["views", view.index]),
                message=[
                    text("Cohorts of this view share units, and its analysis assumes "),
                    text("independent groups; the counts report each shared part, in order: "),
                    *pairs,
                ],
                alternatives=[
                    text("compare a subset with the rest of its base: "),
                    text('{"all": [{"kind": "cohort", "cohort": <base>}, {"not": <subset>}]}'),
                    text('; or write overlap: "allow", for each cohort\'s values alone'),
                ],
                counts=counts,
            )
        ]
    )


__all__ = ["run_analysis"]

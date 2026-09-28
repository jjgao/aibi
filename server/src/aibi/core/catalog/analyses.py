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
``members.list_members``, ``columns.compare_columns``), disclosed under its effective *k*,
and made into its envelope (``results.envelope``); a column with more categories than a result
lists refuses the call (``LIMIT_EXCEEDED``, D328).

A view of an analysis that assumes independent groups whose cohorts share units, without
``overlap: "allow"``, refuses the call (``COHORTS_OVERLAP``, §7.4): the refusal reports each pair
of cohorts that share units as the count of the cohort of their shared units, counted in a second
worker run, disclosed and issued as ``count_cohort``'s counts are (§8.6). A crossing counts the
units its cohorts share, and so does the materialisation of such a view's variables
(``compare.columns``, D339).

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

from aibi.core.analyses import columns, cox, distribution, members, packs, survival
from aibi.core.analyses.existence import CohortAt, compare
from aibi.core.analyses.registry import withheld
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
from aibi.core.engine.inputs import Listed, TooManyCells, ordered, shared
from aibi.core.engine.members import Key
from aibi.core.engine.queries import ViewsRun, run_cohorts, run_views
from aibi.core.engine.resolve import ResolvedCohort, ResolvedVariable
from aibi.core.engine.sql import TooManyListed, pairs
from aibi.core.engine.variables import Joint, Materialised
from aibi.core.engine.worker import CallerDeadline, QueryRefused, Workers
from aibi.core.schema.analyses import (
    ColumnsParams,
    CoxParams,
    DistributionParams,
    ExistenceParams,
    MembersParams,
    PackParams,
    SurvivalParams,
)
from aibi.core.schema.cohorts import AnalysisResults, RunAnalysis
from aibi.core.schema.jsonio import canonical, pointer
from aibi.core.schema.limits import (
    CATEGORIES,
    COX_PARAMETERS,
    CURVE_STEPS,
    INPUT_CELLS,
    LISTED_MEMBERS,
    MAX_CATEGORIES,
    MAX_CURVE_STEPS,
    MAX_STRATA,
    MAX_TEXT,
    QUERY_ANSWER_BYTES,
    QUERY_MEMORY,
    QUERY_SECONDS,
    STRATA,
    TEXT_CHARACTERS,
)
from aibi.core.schema.loading import refusal_as_written
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
    shared: Sequence[bool] = (),
    inputs: Sequence[tuple[Sequence[ResolvedCohort], Sequence[ResolvedVariable]]] = (),
    packed: Sequence[CheckedView] = (),
) -> ViewsRun:
    """The call's queries in one run, ``listed`` the cohorts whose members' keys are listed,
    ``shared`` the materialisations that count the units their cohorts share and ``inputs`` the
    listings of packs' and survival analyses' inputs; an answer over the cap names the view to
    narrow (``widest``, module docstring), and a listing of inputs over its caps the first view
    that reads it (``packed``, a view per listing)."""
    try:
        return _guarded(
            lambda ends: run_views(
                cohorts,
                crossings,
                materialisations,
                sources,
                workers,
                members=listed,
                shared=shared,
                inputs=inputs,
                ends=ends,
            ),
            deadline,
            pointer(["views", widest.index]),
        )
    except TooManyListed as many:
        view = packed[many.listing or 0]
        raise ToolRefused(
            [
                Refusal(
                    code=RefusalCode.LIMIT_EXCEEDED,
                    path=pointer(["views", view.index]),
                    message=[
                        text(f"A cohort has more than {many.most} members, more than the inputs "),
                        text("of a pack's or a survival analysis list (§14, D342, D347): narrow "),
                        text("the cohort"),
                    ],
                    limit=Limit(name=LISTED_MEMBERS, max=many.most),
                )
            ]
        ) from None
    except TooManyCells as many:
        raise _too_many_cells(packed[many.listing or 0], many) from None


def _too_many_cells(view: CheckedView, many: TooManyCells) -> ToolRefused:
    return ToolRefused(
        [
            Refusal(
                code=RefusalCode.LIMIT_EXCEEDED,
                path=pointer(["views", view.index]),
                message=[
                    text(f"The analysis's inputs hold {many.cells} cells or more, members times "),
                    text(f"columns, and at most {many.most} may be (§14, D342): narrow the "),
                    text("cohorts or read fewer columns"),
                ],
                limit=Limit(name=INPUT_CELLS, max=many.most),
            )
        ]
    )


def _too_large(view: CheckedView, column: int) -> ToolRefused:
    said: list[Segment] = (
        [
            text("The column's values, or a statistic or a difference computed from them, lie "),
            text("beyond ±(2^53 − 1), which an output does not hold (§8.2, D336)"),
        ]
        if isinstance(view.params, ColumnsParams)
        else [
            text("The column's values, or their standard deviation, lie beyond "),
            text("±(2^53 − 1), which an output does not hold (§8.2, D328)"),
        ]
    )
    return ToolRefused(
        [
            Refusal(
                code=RefusalCode.NOT_SUPPORTED,
                path=pointer(["views", view.index, "params", "columns", column]),
                message=said,
            )
        ]
    )


def _widest(views: Sequence[CheckedView]) -> CheckedView:
    """The view an answer over the cap, or a run over its seconds or memory, names: the first that
    lists keys or a pack's or survival analysis's inputs, a row per member, else the one whose
    materialisation is widest, whose answer grows with the units, else the one whose crossing is
    (module docstring)."""
    listing = [
        view
        for view in views
        if isinstance(view.params, MembersParams | PackParams | SurvivalParams | CoxParams)
    ]
    if listing:
        return listing[0]
    materialising = [view for view in views if view.variables]
    if materialising:
        return max(materialising, key=lambda view: len(view.cohorts) * len(view.variables))
    return max(views, key=lambda view: len(view.cohorts) * (len(view.predicates) + 1))


def _listed(
    view: CheckedView, position: CohortAt, keys: Sequence[Key], deadline: Deadline | None
) -> members.Outcome:
    """A ``summary.members`` view's page (D333), taken by the call's deadline, less the time to
    record; a cohort with more members than a listing reads refuses the call, and so does a page
    holding a text key longer than a result writes."""
    assert isinstance(view.params, MembersParams), "a members view"
    params = view.params
    try:
        return _guarded(
            lambda ends: members.list_members(position, keys, params, k=view.disclosure, ends=ends),
            deadline,
        )
    except members.TooManyMembers as many:
        raise ToolRefused(
            [
                Refusal(
                    code=RefusalCode.LIMIT_EXCEEDED,
                    path=pointer(["views", view.index]),
                    message=[
                        text(f"The cohort has more than {many.most} members, more than a "),
                        text("listing reads to take a page (§14, D333): narrow the cohort"),
                    ],
                    limit=Limit(name=LISTED_MEMBERS, max=many.most),
                )
            ]
        ) from None
    except members.LongKey as long:
        raise ToolRefused(
            [
                Refusal(
                    code=RefusalCode.LIMIT_EXCEEDED,
                    path=pointer(["views", view.index]),
                    message=[
                        text(f"A key on the page has more than {long.most} characters in "),
                        data(long.column),
                        text(", more than a result writes (§14, D331)"),
                    ],
                    limit=Limit(name=TEXT_CHARACTERS, max=long.most),
                )
            ]
        ) from None


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
            none = Refusal(
                code=RefusalCode.MISSING_MEMBER,
                path="/views",
                message=[
                    text("run_analysis runs a document's views, and it has none; "),
                    text("count_cohort counts its cohorts"),
                ],
            )
            raise ToolRefused([refusal_as_written(none, found.loaded.positions)])
        try:
            views = found.views
            for view in views:
                if withheld(view.analysis, view.disclosure):
                    raise ValueError("a view refused for disclosure is never run (§8.4, D353)")
            cohorts: dict[str, CanonicalCohort] = {}
            for view in views:
                for cohort in view.cohorts:
                    cohorts.setdefault(cohort.computation_id, cohort)
            crossings: dict[tuple[tuple[str, ...], tuple[str, ...]], int] = {}
            asked: list[tuple[list[ResolvedCohort], list[ResolvedCohort]]] = []
            materialisations: dict[tuple[tuple[str, ...], tuple[str, ...]], int] = {}
            read: list[tuple[list[ResolvedCohort], list[ResolvedVariable]]] = []
            shared_asked: list[bool] = []
            listings: dict[str, int] = {}
            listed: list[ResolvedCohort] = []
            handed: dict[tuple[tuple[str, ...], tuple[str, ...]], int] = {}
            inputs: list[tuple[list[ResolvedCohort], list[ResolvedVariable]]] = []
            for view in views:
                if isinstance(view.params, PackParams | SurvivalParams | CoxParams):
                    key = _inputs_key(view)
                    if key not in handed:
                        handed[key] = len(inputs)
                        inputs.append(
                            ([cohort.resolved for cohort in view.cohorts], _listed_variables(view))
                        )
                    continue
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
                        shared_asked.append(False)
                    shared_asked[materialisations[key]] |= _counts_shared(view)
                    continue
                key = _crossing_key(view)
                if key not in crossings:
                    crossings[key] = len(asked)
                    asked.append(
                        (members_of, [predicate.resolved for predicate in view.predicates])
                    )
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
                shared_asked,
                inputs,
                _first_readers(views, handed),
            )
            by_id = dict(zip(cohorts, ran_views.counted, strict=True))
            written = dict(request.document)
            params = dict(found.loaded.params_used)
            outcomes: list[Outcome] = []
            issues: list[Issue] = []
            in_order: dict[int, list[Listed]] = {}
            for view in views:
                positions = [
                    CohortAt(cohort, by_id[cohort.computation_id].accounting)
                    for cohort in view.cohorts
                ]
                used = [by_id[cohort.computation_id] for cohort in view.cohorts]
                queries: list[JsonValue] = [
                    {"statements": list(run.sql), "parameters": dict(run.parameters)}
                    for run in used
                ]
                if isinstance(view.params, MembersParams):
                    [position] = positions
                    run_listed = ran_views.listed[listings[position.cohort.computation_id]]
                    outcomes.append(_listed(view, position, run_listed.keys, deadline))
                    queries.append(
                        {
                            "statements": list(run_listed.sql),
                            "parameters": dict(run_listed.parameters),
                        }
                    )
                    issues.append(_result_issue(view, {"queries": queries}, written, params))
                    continue
                if isinstance(view.params, SurvivalParams):
                    given = ran_views.inputs[handed[_inputs_key(view)]]
                    outcomes.append(
                        _survived(
                            catalog,
                            view,
                            positions,
                            given.listed,
                            sources,
                            workers,
                            deadline,
                            erasures,
                            written,
                            params,
                        )
                    )
                    queries.append(
                        {"statements": list(given.sql), "parameters": dict(given.parameters)}
                    )
                    issues.append(_result_issue(view, {"queries": queries}, written, params))
                    continue
                if isinstance(view.params, CoxParams):
                    given = ran_views.inputs[handed[_inputs_key(view)]]
                    outcomes.append(
                        _coxed(
                            catalog,
                            view,
                            positions,
                            given.listed,
                            sources,
                            workers,
                            deadline,
                            erasures,
                            written,
                            params,
                        )
                    )
                    queries.append(
                        {"statements": list(given.sql), "parameters": dict(given.parameters)}
                    )
                    issues.append(_result_issue(view, {"queries": queries}, written, params))
                    continue
                if isinstance(view.params, PackParams):
                    at = handed[_inputs_key(view)]
                    given = ran_views.inputs[at]
                    if at not in in_order:
                        in_order[at] = _in_order(given.listed, deadline)
                    outcomes.append(
                        _packed(
                            catalog,
                            view,
                            positions,
                            in_order[at],
                            sources,
                            workers,
                            deadline,
                            erasures,
                            written,
                            params,
                        )
                    )
                    queries.append(
                        {"statements": list(given.sql), "parameters": dict(given.parameters)}
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
                    own = made.sql[:-1] if made.shared else made.sql
                    queries.append({"statements": list(own), "parameters": dict(made.parameters)})
                    issues.append(_result_issue(view, {"queries": queries}, written, params))
                    continue
                if isinstance(view.params, ColumnsParams):
                    made = ran_views.materialised[materialisations[_materialisation_key(view)]]
                    together = [
                        pair
                        for pair, count in zip(pairs(len(view.cohorts)), made.shared, strict=True)
                        if count
                    ]
                    if together and not view.overlap:
                        _overlap(
                            catalog,
                            view,
                            together,
                            sources,
                            workers,
                            deadline,
                            erasures,
                            written,
                            params,
                        )
                    try:
                        outcomes.append(
                            columns.compare_columns(
                                positions,
                                view.variables,
                                _expanded(view, made.materialised),
                                view.params,
                                reference=view.reference,
                                overlap=bool(together),
                                k=view.disclosure,
                                computation=view.identity.computation_id,
                                ends=None if deadline is None else deadline.at - RECORD_SECONDS,
                            )
                        )
                    except CallerDeadline:
                        raise late(cast(Deadline, deadline)) from None
                    except distribution.TooManyCategories as many:
                        raise _too_many(view, many.column) from None
                    except distribution.TooLarge as large:
                        raise _too_large(view, large.column) from None
                    queries.append(
                        {"statements": list(made.sql), "parameters": dict(made.parameters)}
                    )
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
        except ToolRefused as refused:
            substituted = found.loaded.positions
            raise ToolRefused(
                [refusal_as_written(refusal, substituted) for refusal in refused.refusals]
            ) from None
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


def _survived(
    catalog: Catalog,
    view: CheckedView,
    positions: Sequence[CohortAt],
    listed: Sequence[Listed],
    sources: Mapping[str, Mapping[str, TableSource]],
    workers: Workers,
    deadline: Deadline | None,
    erasures: int,
    written: dict[str, JsonValue],
    params: dict[str, JsonValue],
) -> survival.Outcome:
    """A survival view run on its endpoint rows (D347, D348): each position's listing put in the
    order of §9.3; cohorts that share units refuse the view without ``overlap: "allow"``, as a
    pack analysis's do (§7.4, D339); a curve of more steps than a result reports without a grid
    refuses the call (``LIMIT_EXCEEDED`` at the view's ``grid``), and a unit's time or entry
    beyond ±(2^53 − 1) ``NOT_SUPPORTED`` at the view."""
    assert isinstance(view.params, SurvivalParams), "a survival view"
    survival_params = view.params
    ends = None if deadline is None else deadline.at - RECORD_SECONDS
    try:
        found = [ordered(one, ends) for one in listed]
    except CallerDeadline:
        raise late(cast(Deadline, deadline)) from None
    together = shared(found)
    if together and not view.overlap:
        _overlap(catalog, view, together, sources, workers, deadline, erasures, written, params)
    [endpoint] = view.endpoints
    try:
        rows = [survival.endpoint_rows(endpoint, one, ends=ends) for one in found]
        return survival.survive(
            positions,
            rows,
            survival_params,
            reference=view.reference,
            overlap=bool(together),
            computation=view.identity.computation_id,
            ends=ends,
        )
    except CallerDeadline:
        raise late(cast(Deadline, deadline)) from None
    except survival.TooManySteps as many:
        raise ToolRefused(
            [
                Refusal(
                    code=RefusalCode.LIMIT_EXCEEDED,
                    path=pointer(["views", view.index, "params", "grid"]),
                    message=[
                        text(f"A cohort's curve has {many.steps} steps, one wherever a unit's "),
                        text(f"follow-up ends, and a result reports at most {MAX_CURVE_STEPS} "),
                        text("without a grid (D348): give grid times"),
                    ],
                    limit=Limit(name=CURVE_STEPS, max=MAX_CURVE_STEPS),
                )
            ]
        ) from None
    except survival.TooLarge:
        raise _time_too_large(view) from None


def _time_too_large(view: CheckedView) -> ToolRefused:
    """A survival view refused for a unit's time or entry that no output holds (D348, D368)."""
    return ToolRefused(
        [
            Refusal(
                code=RefusalCode.NOT_SUPPORTED,
                path=pointer(["views", view.index]),
                message=[
                    text("A unit's time or entry in this view's endpoint rows lies beyond "),
                    text("±(2^53 − 1), which an output does not hold (§8.2, D348)"),
                ],
            )
        ]
    )


def _coxed(
    catalog: Catalog,
    view: CheckedView,
    positions: Sequence[CohortAt],
    listed: Sequence[Listed],
    sources: Mapping[str, Mapping[str, TableSource]],
    workers: Workers,
    deadline: Deadline | None,
    erasures: int,
    written: dict[str, JsonValue],
    params: dict[str, JsonValue],
) -> cox.Outcome:
    """A ``survival.cox`` view run on its members' cells (D368): each position's listing put in
    the order of §9.3; cohorts that share units refuse the view without ``overlap: "allow"``, as
    ``survival.km``'s do (§7.4, D339); a unit's time or entry beyond ±(2^53 − 1) refuses it
    ``NOT_SUPPORTED`` at the view, as ``survival.km``'s; a number covariate's value beyond it, or
    one whose column the fit cannot scale, ``NOT_SUPPORTED`` at the covariate, and a level longer
    than a result writes ``LIMIT_EXCEEDED`` there; more columns than a model has after coding
    ``LIMIT_EXCEEDED`` at ``covariates``, and more stratum levels than it takes at ``stratum``."""
    assert isinstance(view.params, CoxParams), "a survival.cox view"
    cox_params = view.params
    ends = None if deadline is None else deadline.at - RECORD_SECONDS
    try:
        found = [ordered(one, ends) for one in listed]
    except CallerDeadline:
        raise late(cast(Deadline, deadline)) from None
    together = shared(found)
    if together and not view.overlap:
        _overlap(catalog, view, together, sources, workers, deadline, erasures, written, params)
    [endpoint] = view.endpoints
    covariates: list[cox.Covariate] = []
    for variable in view.variables[: len(cox_params.covariates)]:
        coded = cox.covariate_of(variable.resolved)
        assert coded is not None, "phase 2 refuses a covariate of no coding"
        covariates.append(coded)
    at: list[str | int] = ["views", view.index, "params"]
    try:
        return cox.analyse(
            positions,
            found,
            endpoint,
            covariates,
            cox_params,
            reference=view.reference,
            overlap=bool(together),
            ends=ends,
        )
    except CallerDeadline:
        raise late(cast(Deadline, deadline)) from None
    except survival.TooLarge:
        raise _time_too_large(view) from None
    except cox.TooLarge as large:
        raise _refused(
            RefusalCode.NOT_SUPPORTED,
            [*at, "covariates", large.covariate],
            text("The covariate's values lie beyond ±(2^53 − 1) among the complete cases, where "),
            text("an integer has no exact double (D368)"),
        ) from None
    except cox.Unscalable as unscalable:
        raise _refused(
            RefusalCode.NOT_SUPPORTED,
            [*at, "covariates", unscalable.covariate],
            text("The covariate's values spread too little or too much for the fit to scale "),
            text("them in a double (D368)"),
        ) from None
    except cox.LongLevel as long:
        raise _refused(
            RefusalCode.LIMIT_EXCEEDED,
            [*at, "covariates", long.covariate],
            text(f"A level of the covariate has more than {MAX_TEXT} characters, more than a "),
            text("result writes (§14, D368)"),
            limit=Limit(name=TEXT_CHARACTERS, max=MAX_TEXT),
        ) from None
    except cox.TooManyParameters as many:
        raise _refused(
            RefusalCode.LIMIT_EXCEEDED,
            [*at, "covariates"],
            text(f"The covariates code to {many.count} columns among the complete cases, and a "),
            text(f"model has at most {cox.MAX_PARAMETERS} (§9.5, D363): a category has a column "),
            text("per level but its baseline; give fewer covariates or categories of fewer levels"),
            limit=Limit(name=COX_PARAMETERS, max=cox.MAX_PARAMETERS),
        ) from None
    except cox.TooManyStrata as many:
        raise _refused(
            RefusalCode.LIMIT_EXCEEDED,
            [*at, "stratum"],
            text(f"The stratum has {many.count} levels among the complete cases, and a model has "),
            text(f"at most {MAX_STRATA} (§9.5, D363)"),
            limit=Limit(name=STRATA, max=MAX_STRATA),
        ) from None


def _refused(
    code: RefusalCode, path: list[str | int], *message: Segment, limit: Limit | None = None
) -> ToolRefused:
    """One refusal at ``path`` after substitution, which ``run_analysis`` points as written."""
    return ToolRefused([Refusal(code=code, path=pointer(path), message=list(message), limit=limit)])


def _packed(
    catalog: Catalog,
    view: CheckedView,
    positions: Sequence[CohortAt],
    listed: Sequence[Listed],
    sources: Mapping[str, Mapping[str, TableSource]],
    workers: Workers,
    deadline: Deadline | None,
    erasures: int,
    written: dict[str, JsonValue],
    params: dict[str, JsonValue],
) -> packs.Outcome:
    """A view of a pack's analysis run on its inputs (D342, D343), ``listed`` each position's
    listing in the order of §9.3 (``_in_order``); cohorts that share units refuse a view of an
    analysis that assumes independent groups without ``overlap: "allow"``, as a
    materialisation's do (§7.4, D339);
    inputs over ``MAX_INPUT_CELLS`` refuse the call (``LIMIT_EXCEEDED``), and a pack that fails
    ``PACK_FAILED`` at the view's ``analysis``."""
    assert isinstance(view.params, PackParams), "a view of a pack's analysis"
    pack_params = view.params
    ends = None if deadline is None else deadline.at - RECORD_SECONDS
    found = list(listed)
    fields = view.analysis.entry.fields
    together = shared(found)
    if together and fields.assumes_independent_groups and not view.overlap:
        _overlap(catalog, view, together, sources, workers, deadline, erasures, written, params)
    analysis, _, returns = catalog.analyses.implementation(view.analysis.id)
    try:
        return packs.run_pack(
            analysis,
            returns,
            positions,
            list(zip(view.roles, view.variables, strict=True)),
            found,
            pack_params,
            reference=view.reference,
            overlapping=bool(together),
            computation=view.identity.computation_id,
            ends=ends,
            endpoints=list(zip(view.endpoint_roles, view.endpoints, strict=True)),
        )
    except CallerDeadline:
        raise late(cast(Deadline, deadline)) from None
    except TooManyCells as many:
        raise _too_many_cells(view, many) from None
    except packs.PackFailed as failed:
        raise ToolRefused(
            [
                Refusal(
                    code=RefusalCode.PACK_FAILED,
                    path=pointer(["views", view.index, "analysis"]),
                    message=list(failed.message),
                    limit=failed.limit,
                )
            ]
        ) from None


def _in_order(listed: Sequence[Listed], deadline: Deadline | None) -> list[Listed]:
    """Each position's listing put in the order of §9.3 once, for every view that reads it,
    by the call's deadline less the time to record (D342)."""
    ends = None if deadline is None else deadline.at - RECORD_SECONDS
    try:
        return [ordered(one, ends) for one in listed]
    except CallerDeadline:
        raise late(cast(Deadline, deadline)) from None


def _first_readers(
    views: Sequence[CheckedView], handed: Mapping[tuple[tuple[str, ...], tuple[str, ...]], int]
) -> list[CheckedView]:
    """The first view that reads each listing of inputs, by the listing's index."""
    firsts: dict[int, CheckedView] = {}
    for view in views:
        if isinstance(view.params, PackParams | SurvivalParams | CoxParams):
            firsts.setdefault(handed[_inputs_key(view)], view)
    return [firsts[index] for index in range(len(firsts))]


def _inputs_key(view: CheckedView) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """A pack or survival view's inputs, by its cohorts' computation ids and its variables' and
    endpoints' canonical forms in order: views that list the same inputs share their run."""
    endpoints = [canonical(endpoint.form).decode() for endpoint in view.endpoints]
    return (
        tuple(cohort.computation_id for cohort in view.cohorts),
        (*_forms(view), *endpoints),
    )


def _listed_variables(view: CheckedView) -> list[ResolvedVariable]:
    """What a pack or survival view's inputs list of each member: its variables, then each of its
    endpoints' columns (D347, D352)."""
    found = [variable.resolved for variable in view.variables]
    for endpoint in view.endpoints:
        found += endpoint.variables
    return found


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
    twice is read once (D327). It counts the units its cohorts share when one of its views asks
    (``_counts_shared``), and a view that does not records its statements without that one
    (D339)."""
    return (
        tuple(cohort.computation_id for cohort in view.cohorts),
        tuple(dict.fromkeys(_forms(view))),
    )


def _counts_shared(view: CheckedView) -> bool:
    """Whether a view's materialisation counts the units its cohorts share: a view of an
    analysis that assumes independent groups over two cohorts or more (D339)."""
    independent = view.analysis.entry.fields.assumes_independent_groups
    return bool(independent) and len(view.cohorts) > 1


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

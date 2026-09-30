"""Phase 2 of canonicalisation: each view checked against its analysis (SPEC §7.4, §7.6; D317).

A view is checked in two steps, around phase 1:

1. ``parse``, before the cohorts are resolved: its ``analysis`` is one the registry holds
   (``UNKNOWN_ANALYSIS``, listing those it holds; a core analysis of §9.5 a later slice implements
   is passed over, and ``deferred`` gives its ``NOT_SUPPORTED``, D317); its ``params`` are the
   analysis's parameters (a pack's, ``PackParams``: its ``column`` requirements bound by role, its
   ``options`` checked against its ``params`` schema, D341, and its ``endpoint`` requirements bound
   by role, D352), refused where they are written in the document; an analysis that declares
   ``uses_reference`` has its ``cohorts`` listed (``MISSING_MEMBER``); and each predicate of its
   parameters holds no ``ids`` and no ``cohort`` leaf (``LEAF_NOT_ALLOWED``: a predicate is asked of
   every unit, and those belong in a cohort) and at most as many pack leaves as a cohort; and the
   ``where`` of each variable of its parameters holds no ``ids`` or ``cohort`` leaf either
   (``LEAF_NOT_ALLOWED``) and at most as many pack leaves as a cohort (D345); and a view of
   ``summary.members`` names exactly one cohort, listed in its ``cohorts`` (else ``INVALID_VALUE``
   there) or the document's only one (else ``MISSING_MEMBER`` at ``cohorts``) (D331); and a view of
   ``survival.cox`` that lists one cohort gives a covariate (else ``MISSING_MEMBER`` at
   ``params/covariates``, D367). Its predicates (``survival.cox``'s predicate covariates too,
   D370), variables (``survival.cox``'s column covariates, then its stratum, D366) and endpoints (a
   survival analysis's one, ``params.endpoint`` or the one usable endpoint on the unit table, D347;
   a pack's by role, D352) are then handed to ``canonicalise`` (``ViewPredicate``,
   ``ViewVariable``, ``ViewEndpoint``), resolved with the cohorts in the release of the view's
   cohorts, on the unit table.
2. ``checked``, after phase 1: a view whose cohorts, predicates, variables and endpoints all
   canonicalised, none of whose parameters' members its analysis withholds under the effective *k*
   (``_withheld_form``, D379, D383: ``summary.distribution``'s ``count: "rows"``, and its ``each``
   where the descriptors bound the rows each unit reaches, ``WITHHELD_UNDER_K`` at the member,
   checked first), and whose variables its analysis takes
   (``summary.distribution``'s: categories or numbers, ``bins`` only for numbers, under *k* a
   number's histogram edges from ``bins`` or a declared range, and under *k* one set of edges for
   a column's values across the call's views, D328, D329; ``compare.columns``': categories or
   numbers, and no ``bins``, D336; a
   pack's: no dates or datetimes, and each of its role's ``datatype`` and ``on``, D341;
   ``survival.cox``'s: a coding for each covariate and the stratum, no identifier column's values,
   not the endpoint's own time or status, and no ``bins``, D367, and predicates that test neither,
   D370), whose disclosure allows it (``_disclosure``, D353: no view of a ``refused`` analysis under
   any disclosure setting, ``WITHHELD_UNDER_K`` at ``analysis``, and none of one that lists or hands
   each member's values in the keys' order where the dataset allows no row ids,
   ``ROW_IDS_NOT_ALLOWED`` there), and, for a pack's analysis, whose requirement predicates hold of
   the release (``NOT_SUPPORTED`` at ``analysis`` otherwise, D341), gets its canonical form and ids
   (``ViewIdentity``): its cohorts in view order, or by computation id when it lists none (D284);
   its reference's position, for an analysis that declares ``uses_reference``, the first cohort's by
   default; ``overlap``, for one that declares ``assumes_independent_groups``; its canonical
   parameters, every default written, every predicate as its canonical clause tree, every variable
   as its canonical form and every endpoint as its (``{"id", "time"}``); the effective *k* over its
   cohorts and predicates and the floor; and the results versions of the packs of its analysis,
   cohorts, predicates and variables. A view one of whose cohorts, predicates, variables or
   endpoints was refused is left out; their refusals say why. A view whose cohorts are of more than
   one release is ``MIXED_RELEASES``, which resolution refuses first.

A view's readback is its analysis's, rendered from its canonical form and the release's descriptors
(§7.7); its static caveats are those its result carries that need no data: the fields its cohorts,
predicates, variables and endpoints read that are not confirmed (an undeclared entry among them,
§5.8), a draft release, and what the packs' caveat rules raised for its cohorts and predicates,
which are the canonical parts of a view a pack can read (D287, D317).
"""

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from functools import partial
from typing import Literal, cast

from pydantic import JsonValue, ValidationError

from aibi.core.analyses import columns, cox, distribution, existence, members, packs, survival
from aibi.core.analyses.registry import (
    CORE,
    DISCLOSED,
    LATER,
    Analyses,
    DisclosureClass,
    Registered,
    disclosure_of,
    withheld_form,
)
from aibi.core.engine.canonical import (
    CanonicalCohort,
    Canonicalisation,
    CanonicalVariable,
    ViewIdentity,
    predicate_variable,
)
from aibi.core.engine.data import Release
from aibi.core.engine.resolve import (
    FieldRead,
    Reads,
    ResolvedEndpoint,
    ResolvedVariable,
    ViewEndpoint,
    ViewPredicate,
    ViewVariable,
    identifying,
    in_document_order,
)
from aibi.core.engine.resolved import (
    Origin,
    RAll,
    RAny,
    RClause,
    RCovered,
    RExists,
    RKnown,
    RNot,
    RUnknown,
    RValue,
)
from aibi.core.schema.analyses import (
    ColumnsParams,
    CoxParams,
    DistributionParams,
    ExistenceParams,
    MembersParams,
    PackParams,
    PredicateCovariate,
    SurvivalParams,
    Variable,
)
from aibi.core.schema.caveats import CORE_SEVERITIES, Caveat, CaveatCode, sort_caveats
from aibi.core.schema.descriptors import Disclosure, Requirement
from aibi.core.schema.document import (
    PARSED,
    Clause,
    CohortLeaf,
    DocModel,
    Document,
    IdsLeaf,
    PackLeaf,
    walk,
)
from aibi.core.schema.jsonio import canonical, pointer
from aibi.core.schema.jsonschemas import OUT_OF_STEPS, UNEVALUABLE, WRITE_STEPS_MAX, steps
from aibi.core.schema.limits import (
    MAX_PACK_LEAVES,
    MAX_VARIABLES,
    PACK_LEAVES,
    PACK_OPTION_STEPS,
    VARIABLES,
)
from aibi.core.schema.loading import refusal_as_written, refusal_from_error
from aibi.core.schema.output import Segment, data, text
from aibi.core.schema.params import Position
from aibi.core.schema.refusals import Limit, Refusal, RefusalCode
from aibi.core.schema.results import PackVersion, ReleaseRef

LISTED = 64
"""Analyses a refusal lists as the alternatives before it counts the rest."""


@dataclass(frozen=True)
class ParsedView:
    """A view that passed the first step, with its predicates to canonicalise."""

    index: int
    analysis: Registered
    params: DocModel
    names: tuple[str, ...] | None
    """The cohorts it lists, in view order; ``None`` for every cohort, by id."""
    reference: str | None
    overlap: bool
    predicates: tuple[ViewPredicate, ...]
    variables: tuple[ViewVariable, ...] = ()
    roles: tuple[str, ...] = ()
    """For a pack's analysis, the role each variable is bound to, as ``variables`` orders them
    (D341)."""
    endpoints: tuple[ViewEndpoint, ...] = ()
    """The endpoints it reads: a survival analysis's one, and each endpoint a pack's analysis
    binds (D347, D352)."""
    endpoint_roles: tuple[str, ...] = ()
    """For a pack's analysis, the role each endpoint is bound to (D352)."""
    forms: tuple[Literal["column", "predicate"], ...] = ()
    """For ``survival.cox``, each covariate's form in order (D370): its ``variables`` hold the
    column covariates, then the stratum, and its ``predicates`` the predicates."""


class _Refusals:
    def __init__(self, positions: Mapping[Position, str]) -> None:
        self.positions = positions
        self.found: list[Refusal] = []

    def add(
        self,
        code: RefusalCode,
        at: Position,
        *message: Segment,
        alternatives: Sequence[Segment] = (),
        limit: Limit | None = None,
    ) -> None:
        refusal = Refusal(
            code=code,
            path=pointer(list(at)),
            message=list(message),
            alternatives=list(alternatives),
            limit=limit,
        )
        self.found.append(refusal_as_written(refusal, self.positions))

    def of_error(self, error: ValidationError, model: type[DocModel], at: Position) -> None:
        """A parameter model's errors, pointed into the document as written."""
        for details in error.errors(include_url=False, include_input=False):
            refusal = refusal_from_error(details, model)
            path = pointer(list(at)) + (refusal.path or "")
            self.found.append(
                refusal_as_written(refusal.model_copy(update={"path": path}), self.positions)
            )


def _listed(names: Sequence[str]) -> list[Segment]:
    shown: list[Segment] = [data(name) for name in names[:LISTED]]
    if len(names) > LISTED:
        shown.append(text(f"and {len(names) - LISTED} more"))
    return shown


def _reference_of(document: Document, name: str | None) -> str | None:
    """The dataset reference as written of a cohort, or of the document."""
    cohort = None if name is None else document.cohorts.get(name)
    if cohort is not None and cohort.dataset is not None:
        return cohort.dataset
    return document.dataset


def deferred(document: Document, positions: Mapping[Position, str]) -> list[Refusal]:
    """The views of a core analysis of §9.5 that a later slice implements (``registry.LATER``),
    which ``parse`` passes over: each ``NOT_SUPPORTED``, naming the slice. ``count_cohort``
    counts the document's cohorts beside them; ``run_analysis`` refuses them, and
    ``validate_document`` lists them among its refusals (D317)."""
    refusals = _Refusals(positions)
    for index, view in enumerate(document.views or []):
        slice_ = LATER.get(view.analysis)
        if slice_ is not None:
            refusals.add(
                RefusalCode.NOT_SUPPORTED,
                ("views", index, "analysis"),
                text("The core's analysis "),
                data(view.analysis),
                text(f" (§9.5) is run from {slice_}; this server runs "),
                *_listed(sorted(CORE)),
            )
    return refusals.found


def parse(
    document: Document, positions: Mapping[Position, str], analyses: Analyses
) -> tuple[list[ParsedView], list[Refusal]]:
    """The first step (module docstring): each view's analysis, parameters and cohorts
    checked, and its predicates to resolve."""
    refusals = _Refusals(positions)
    parsed: list[ParsedView] = []
    for index, view in enumerate(document.views or []):
        at: Position = ("views", index)
        if view.analysis in LATER:
            continue
        found = analyses.get(view.analysis)
        if found is None:
            refusals.add(
                RefusalCode.UNKNOWN_ANALYSIS,
                (*at, "analysis"),
                text("The registry holds no analysis "),
                data(view.analysis),
                alternatives=_listed(analyses.ids()),
            )
            continue
        before = len(refusals.found)
        model = found.params or PackParams
        params: DocModel | None = None
        try:
            params = model.model_validate(view.params or {}, context={PARSED: True})
        except ValidationError as error:
            refusals.of_error(error, model, (*at, "params"))
        fields = found.entry.fields
        if fields.uses_reference and view.cohorts is None:
            refusals.add(
                RefusalCode.MISSING_MEMBER,
                (*at, "cohorts"),
                text("This analysis compares cohorts with a reference: list them in cohorts, "),
                text("in view order, the reference first or named by reference"),
            )
        predicates: tuple[ViewPredicate, ...] = ()
        variables: tuple[ViewVariable, ...] = ()
        roles: tuple[str, ...] = ()
        endpoints: tuple[ViewEndpoint, ...] = ()
        endpoint_roles: tuple[str, ...] = ()
        first = view.cohorts[0] if view.cohorts else next(iter(document.cohorts), None)
        reference = _reference_of(document, first)
        if isinstance(params, ExistenceParams):
            predicates = _predicates(
                [
                    (("views", index, "params", "predicates", j), clause, f"{index}/{j}")
                    for j, clause in enumerate(params.predicates)
                ],
                reference,
                refusals,
            )
        elif isinstance(params, DistributionParams | ColumnsParams):
            independent = bool(fields.assumes_independent_groups)
            written = [(("columns", j), v) for j, v in enumerate(params.columns)]
            check = _summarises if isinstance(params, DistributionParams) else _compares
            variables = _variables(
                written, index, reference, refusals, independent, [check] * len(written)
            )
        elif isinstance(params, MembersParams):
            _one_cohort(view.cohorts, len(document.cohorts), index, refusals)
        elif isinstance(params, SurvivalParams | CoxParams) and reference is not None:
            endpoints = (
                ViewEndpoint(
                    f"{index}/endpoint",
                    reference,
                    ("views", index, "params", "endpoint"),
                    params.endpoint,
                ),
            )
        forms: tuple[Literal["column", "predicate"], ...] = ()
        if isinstance(params, CoxParams):
            _modelled(view.cohorts, params, index, refusals)
            modelled: list[tuple[tuple[str | int, ...], Variable]] = [
                (("covariates", j), v)
                for j, v in enumerate(params.covariates)
                if isinstance(v, Variable)
            ]
            if params.stratum is not None:
                modelled.append((("stratum",), params.stratum))
            coxes: Reads = partial(_coxes, f"{index}/endpoint")
            variables = _variables(
                modelled, index, reference, refusals, True, [coxes] * len(modelled)
            )
            asked = [
                (
                    ("views", index, "params", "covariates", j, "predicate"),
                    covariate.predicate,
                    f"{index}/covariates/{j}",
                )
                for j, covariate in enumerate(params.covariates)
                if isinstance(covariate, PredicateCovariate)
            ]
            predicates = _predicates(asked, reference, refusals)
            forms = tuple(
                "predicate" if isinstance(covariate, PredicateCovariate) else "column"
                for covariate in params.covariates
            )
        elif isinstance(params, PackParams):
            cohorts = len(view.cohorts) if view.cohorts is not None else len(document.cohorts)
            written = _pack_params(found, params, cohorts, index, analyses, refusals)
            required = {r.role: r for r in found.entry.fields.requires if r.kind == "column"}
            reads: list[Reads] = [partial(_packs, required[str(place[1])]) for place, _ in written]
            variables = _variables(written, index, reference, refusals, True, reads)
            roles = tuple(str(place[1]) for place, _ in written)
            bound = _pack_endpoints(found, params, index, reference, refusals)
            endpoints = tuple(endpoint for _, endpoint in bound)
            endpoint_roles = tuple(role for role, _ in bound)
        if len(refusals.found) > before or params is None:
            continue
        parsed.append(
            ParsedView(
                index=index,
                analysis=found,
                params=params,
                names=None if view.cohorts is None else tuple(view.cohorts),
                reference=view.reference,
                overlap=view.overlap == "allow",
                predicates=predicates,
                variables=variables,
                roles=roles,
                endpoints=endpoints,
                endpoint_roles=endpoint_roles,
                forms=forms,
            )
        )
    return parsed, refusals.found


def _pack_params(
    found: Registered,
    params: PackParams,
    cohorts: int,
    index: int,
    analyses: Analyses,
    refusals: _Refusals,
) -> list[tuple[tuple[str | int, ...], Variable]]:
    """What phase 2 checks of a pack analysis's parameters before resolution (D341): the view's
    cohorts number what its ``cohorts`` requirement allows; ``columns`` binds only the roles of
    its ``column`` requirements, each at least ``min`` (1 by default) and at most ``max``
    variables, and at most ``MAX_VARIABLES`` in all; and its ``options`` satisfy its ``params``
    schema, within the steps of an extension object of their size. Gives each variable with its
    place below ``params``: ``("columns", <role>, <j>)``, roles in their entry's order."""
    at: Position = ("views", index)
    fields = found.entry.fields
    for requirement in fields.requires:
        if requirement.role != "cohorts" or requirement.kind is not None:
            continue
        least = 1 if requirement.min is None else requirement.min
        most = requirement.max
        if cohorts < least or (most is not None and cohorts > most):
            refusals.add(
                RefusalCode.INVALID_VALUE,
                (*at, "cohorts"),
                text(f"This analysis takes {least} cohorts at least"),
                text("" if most is None else f" and {most} at most"),
                text(f", and the view has {cohorts}"),
            )
    column_roles = [r for r in fields.requires if r.kind == "column"]
    given = params.columns or {}
    known = {requirement.role for requirement in column_roles}
    for role in given:
        if role not in known:
            refusals.add(
                RefusalCode.UNKNOWN_MEMBER,
                (*at, "params", "columns", role),
                text("This analysis requires no columns of that role: "),
                data(role),
                alternatives=_listed(sorted(known)),
            )
    written: list[tuple[tuple[str | int, ...], Variable]] = []
    for requirement in column_roles:
        role = requirement.role
        bound = given.get(role, [])
        least = 1 if requirement.min is None else requirement.min
        most = requirement.max
        if len(bound) < least:
            refusals.add(
                RefusalCode.MISSING_MEMBER,
                (*at, "params", "columns", *((role,) if bound else ())),
                text(f"This analysis requires {least} columns at least of role "),
                data(role),
            )
        elif most is not None and len(bound) > most:
            refusals.add(
                RefusalCode.INVALID_VALUE,
                (*at, "params", "columns", role),
                text(f"This analysis takes {most} columns at most of role "),
                data(role),
            )
        written += [(("columns", role, j), variable) for j, variable in enumerate(bound)]
    total = sum(len(bound) for bound in given.values())
    if total > MAX_VARIABLES:
        refusals.add(
            RefusalCode.LIMIT_EXCEEDED,
            (*at, "params", "columns"),
            text(f"The view gives {total} columns, and at most {MAX_VARIABLES} may be: each is "),
            text("resolved, queried and listed for every member of every cohort"),
            limit=Limit(name=VARIABLES, max=MAX_VARIABLES),
        )
    _options(found, params, index, analyses, refusals)
    return written


def _pack_endpoints(
    found: Registered,
    params: PackParams,
    index: int,
    reference: str | None,
    refusals: _Refusals,
) -> list[tuple[str, ViewEndpoint]]:
    """The endpoints a pack analysis's view binds (D352): ``endpoints`` names only roles of its
    entry's ``endpoint`` requirements (``UNKNOWN_MEMBER`` otherwise, listing them), and each such
    requirement reads the endpoint given for its role, or without one the one usable endpoint
    that meets it; a requirement whose ``min`` is 0 binds none unless one is given. Each is
    resolved on the unit table, on it itself where its requirement says ``"on": "unit"``, in
    the entry's order of requirements."""
    at: Position = ("views", index, "params", "endpoints")
    requirements = [r for r in found.entry.fields.requires if r.kind == "endpoint"]
    given = params.endpoints or {}
    known = {requirement.role for requirement in requirements}
    for role in given:
        if role not in known:
            refusals.add(
                RefusalCode.UNKNOWN_MEMBER,
                (*at, role),
                text("This analysis requires no endpoint of that role: "),
                data(role),
                alternatives=_listed(sorted(known)),
            )
    if reference is None:
        return []
    bound: list[tuple[str, ViewEndpoint]] = []
    for requirement in requirements:
        role = requirement.role
        if role not in given and requirement.min == 0:
            continue
        bound.append(
            (
                role,
                ViewEndpoint(
                    f"{index}/endpoints/{role}",
                    reference,
                    (*at, role),
                    given.get(role),
                    on_unit=requirement.on == "unit",
                ),
            )
        )
    return bound


def _options(
    found: Registered, params: PackParams, index: int, analyses: Analyses, refusals: _Refusals
) -> None:
    """A pack analysis's ``options`` checked against its entry's ``params`` schema, as registered
    (D341), each failure where it is written."""
    options = cast(JsonValue, dict(params.options or {}))
    checker, _ = analyses.implementation(found.id)[1:]
    ceiling = steps(options, WRITE_STEPS_MAX)
    at: Position = ("views", index, "params", "options")
    for failure in checker.failures(options, ceiling=WRITE_STEPS_MAX):
        if failure.keyword == OUT_OF_STEPS:
            refusals.add(
                RefusalCode.LIMIT_EXCEEDED,
                at,
                text("Checking the options against the analysis's params schema takes more "),
                text("steps than it may; give fewer or smaller options"),
                limit=Limit(name=PACK_OPTION_STEPS, max=ceiling),
            )
        elif failure.keyword == UNEVALUABLE:
            refusals.add(
                RefusalCode.INVALID_VALUE,
                at,
                text("The params schema of analysis "),
                data(found.id),
                text(" cannot evaluate these options"),
            )
        else:
            refusals.add(
                RefusalCode.INVALID_VALUE,
                (*at, *failure.path),
                text("The options do not satisfy the params schema of analysis "),
                data(found.id),
                text(": its keyword "),
                data(failure.keyword),
                text(" fails here"),
            )


def _one_cohort(
    listed: Sequence[str] | None, cohorts: int, index: int, refusals: _Refusals
) -> None:
    """``summary.members`` lists the keys of exactly one cohort (D331): the one its view lists,
    or the document's only one."""
    if listed is not None and len(listed) != 1:
        refusals.add(
            RefusalCode.INVALID_VALUE,
            ("views", index, "cohorts"),
            text(f"summary.members lists the keys of exactly one cohort, and {len(listed)} are "),
            text("listed: list one, in a view of its own for each"),
        )
    elif listed is None and cohorts != 1:
        refusals.add(
            RefusalCode.MISSING_MEMBER,
            ("views", index, "cohorts"),
            text("summary.members lists the keys of exactly one cohort, and the document has "),
            text(f"{cohorts}: list one in cohorts"),
        )


def _modelled(
    listed: Sequence[str] | None, params: CoxParams, index: int, refusals: _Refusals
) -> None:
    """A ``survival.cox`` view has a term (D367): a second cohort or a covariate, since a model
    of one cohort without covariates, stratified or not, has none."""
    if listed is not None and len(listed) == 1 and not params.covariates:
        refusals.add(
            RefusalCode.MISSING_MEMBER,
            ("views", index, "params", "covariates"),
            text("A Cox model of one cohort compares its units by their covariates, and the "),
            text("view gives none: give covariates, or list a second cohort to compare with"),
        )


def _predicates(
    written: Sequence[tuple[Position, Clause, str]], reference: str | None, refusals: _Refusals
) -> tuple[ViewPredicate, ...]:
    """A view's predicates to resolve, each with its place in the document and its key: each
    holds no ``ids`` and no ``cohort`` leaf and at most as many pack leaves as a cohort (D317,
    D370)."""
    before = len(refusals.found)
    for at, clause, _ in written:
        pack_leaves = 0
        for leaf, path, _ in walk([clause], []):
            pack_leaves += isinstance(leaf, PackLeaf)
            if isinstance(leaf, IdsLeaf | CohortLeaf):
                refusals.add(
                    RefusalCode.LEAF_NOT_ALLOWED,
                    (*at, *path[1:]),
                    text(f"A predicate holds no {leaf.kind} leaf: it is asked of every unit of "),
                    text("the view's cohorts, and a list of units or another cohort belongs in a "),
                    text("cohort"),
                )
        if pack_leaves > MAX_PACK_LEAVES:
            refusals.add(
                RefusalCode.LIMIT_EXCEEDED,
                at,
                text(f"The predicate has {pack_leaves} pack leaves, and at most "),
                text(f"{MAX_PACK_LEAVES} may be: each is compiled by its pack (D285)"),
                limit=Limit(name=PACK_LEAVES, max=MAX_PACK_LEAVES),
            )
    if reference is None or len(refusals.found) > before:
        return ()
    return tuple(
        ViewPredicate(key=key, reference=reference, at=at, clause=clause)
        for at, clause, key in written
    )


def _variables(
    written: Sequence[tuple[tuple[str | int, ...], Variable]],
    index: int,
    reference: str | None,
    refusals: _Refusals,
    independent: bool,
    reads: Sequence[Reads],
) -> tuple[ViewVariable, ...]:
    """A view's variables to resolve (D325), each with its place below ``params``: the ``where``
    of each holds no ``ids`` and no ``cohort`` leaf, as a predicate holds none, and at most as
    many pack leaves as a cohort, which resolution expands (D345); ``count: "rows"`` is a
    descriptive analysis's (D378), never one that reads one value per unit (``independent``:
    one that assumes independent groups, or a pack's; §9.2, D335). Resolution refuses ``each``
    there, and where the column has no memberships, and a form the column does not take,
    offering only the forms that run in its place, by the analysis's own phase-2 checks of each
    (``reads``, one for each: ``_summarises``, ``_compares``, ``_coxes`` or ``_packs``, D380);
    ``summary.distribution`` reads memberships (D382)."""
    base: list[str | int] = ["views", index, "params"]
    before = len(refusals.found)
    for place, variable in written:
        if variable.count is not None and independent:
            refusals.add(
                RefusalCode.INVALID_VALUE,
                (*base, *place, "count"),
                text("This analysis reads one value per unit of each variable (§9.2, D335), not "),
                text("its rows: leave count out"),
            )
        where: list[str | int] = [*base, *place, "where"]
        pack_leaves = 0
        for leaf, path, _ in walk(list(variable.where or []), where):
            pack_leaves += isinstance(leaf, PackLeaf)
            if isinstance(leaf, IdsLeaf | CohortLeaf):
                refusals.add(
                    RefusalCode.LEAF_NOT_ALLOWED,
                    tuple(path),
                    text(f"A column's where holds no {leaf.kind} leaf: it is asked of the rows a "),
                    text("column aggregates, and a list of units or a cohort belongs in a cohort"),
                )
        if pack_leaves > MAX_PACK_LEAVES:
            refusals.add(
                RefusalCode.LIMIT_EXCEEDED,
                tuple(where),
                text(f"The column's where has {pack_leaves} pack leaves, and at most "),
                text(f"{MAX_PACK_LEAVES} may be: each is compiled by its pack (D285)"),
                limit=Limit(name=PACK_LEAVES, max=MAX_PACK_LEAVES),
            )
    if reference is None or len(refusals.found) > before:
        return ()
    return tuple(
        ViewVariable(
            key="/".join(str(token) for token in (index, *place[1:])),
            reference=reference,
            at=(*base, *place),
            variable=variable,
            independent=independent,
            reads=read,
        )
        for (place, variable), read in zip(written, reads, strict=True)
    )


@dataclass(frozen=True)
class CheckedView:
    """A view in canonical form, with what its result is computed from (module docstring)."""

    index: int
    analysis: Registered
    params: DocModel
    cohorts: tuple[CanonicalCohort, ...]
    """In view order."""
    reference: int
    """The reference's position: 0 for an analysis that declares no ``uses_reference``."""
    overlap: bool
    """Whether the view allows its cohorts to share units, for an analysis that assumes
    independent groups."""
    predicates: tuple[CanonicalCohort, ...]
    identity: ViewIdentity
    packs: Mapping[str, PackVersion]
    variables: tuple[CanonicalVariable, ...] = ()
    roles: tuple[str, ...] = ()
    """For a pack's analysis, the role each variable is bound to (D341)."""
    endpoints: tuple[ResolvedEndpoint, ...] = ()
    """The endpoints it reads (D347, D352), in ``ParsedView.endpoints``' order."""
    endpoint_roles: tuple[str, ...] = ()
    """For a pack's analysis, the role each endpoint is bound to (D352)."""

    @property
    def release(self) -> ReleaseRef:
        return self.cohorts[0].release

    @property
    def disclosure(self) -> int | None:
        return self.identity.disclosure

    def readback(self) -> list[Segment]:
        if isinstance(self.params, ExistenceParams):
            return existence.view_readback(
                len(self.cohorts), self.reference, self.predicates, self.params
            )
        if isinstance(self.params, MembersParams):
            return members.view_readback(self.cohorts[0], self.params)
        if isinstance(self.params, ColumnsParams):
            return columns.view_readback(
                len(self.cohorts), self.reference, self.variables, self.params
            )
        if isinstance(self.params, SurvivalParams):
            [endpoint] = self.endpoints
            return survival.view_readback(len(self.cohorts), self.reference, endpoint, self.params)
        if isinstance(self.params, CoxParams):
            [endpoint] = self.endpoints
            return cox.view_readback(
                len(self.cohorts),
                self.reference,
                endpoint,
                self.variables,
                self.params,
                self.predicates,
            )
        if isinstance(self.params, PackParams):
            fields = self.analysis.entry.fields
            return packs.view_readback(
                self.analysis.entry.label,
                self.analysis.id,
                len(self.cohorts),
                self.reference if fields.uses_reference else None,
                list(zip(self.roles, self.variables, strict=True)),
                dict(self.params.options or {}),
                list(zip(self.endpoint_roles, self.endpoints, strict=True)),
            )
        assert isinstance(self.params, DistributionParams), "the core's analyses are known"
        return distribution.view_readback(len(self.cohorts), self.variables)

    def static_caveats(self) -> list[Caveat]:
        """The caveats its result carries that need no data (module docstring): for
        ``summary.members``, the key's fields too, which set how its values are carried."""
        reads = members.key_reads(self.cohorts[0]) if isinstance(self.params, MembersParams) else []
        reads += [read for endpoint in self.endpoints for read in endpoint.unconfirmed]
        return static_caveats(self.cohorts, self.predicates, self.variables, reads)


def static_caveats(
    cohorts: Sequence[CanonicalCohort],
    predicates: Sequence[CanonicalCohort],
    variables: Sequence[CanonicalVariable] = (),
    reads: Sequence[FieldRead] = (),
) -> list[Caveat]:
    found: list[Caveat] = []
    groups = (
        (cohorts, [read for cohort in cohorts for read in cohort.resolved.unconfirmed]),
        (
            predicates,
            [
                *(read for cohort in predicates for read in cohort.resolved.unconfirmed),
                *(read for variable in variables for read in variable.resolved.unconfirmed),
                *reads,
            ],
        ),
    )
    for (group, unconfirmed), affects in zip(groups, ("/population", "/values"), strict=True):
        reads = sorted(set(unconfirmed))
        if reads:
            message: list[Segment] = [text("These fields were read but are not confirmed: ")]
            for position, read in enumerate(reads):
                if position:
                    message.append(text(", "))
                message += [data(read.descriptor + read.pointer), text(f" ({read.status})")]
            found.append(_caveat(CaveatCode.UNCONFIRMED_SEMANTICS, affects, message))
        ruled = sorted({(c.code, c.severity, c.pack) for cohort in group for c in cohort.caveats})
        found += [
            Caveat(
                code=code,
                severity=severity,
                message=[text("Raised by the caveat rule of pack "), data(pack)],
                affects=[affects],
            )
            for code, severity, pack in ruled
        ]
    if cohorts and cohorts[0].release.status == "draft":
        found.append(
            _caveat(
                CaveatCode.DRAFT_RELEASE,
                "/derivation/releases",
                [
                    text("Computed against the draft of a curation session of "),
                    data(cohorts[0].release.dataset),
                ],
            )
        )
    return sort_caveats(found)


def _caveat(code: CaveatCode, affects: str, message: list[Segment]) -> Caveat:
    return Caveat(code=code, severity=CORE_SEVERITIES[code], message=message, affects=[affects])


def checked(
    document: Document,
    parsed: Sequence[ParsedView],
    canonical: Canonicalisation,
    analyses: Analyses | None = None,
    positions: Mapping[Position, str] | None = None,
) -> tuple[list[CheckedView], list[Refusal]]:
    """The second step (module docstring): the views whose cohorts and predicates all
    canonicalised, in canonical form; and a refusal for a view whose cohorts are of more than
    one release (``MIXED_RELEASES``), which resolution refuses first (§7.4). ``analyses`` runs
    a pack analysis's requirement predicates; without it, none holds. Each refusal is pointed
    into the document as written by ``positions``, the substituted positions (D368)."""
    found: list[CheckedView] = []
    refusals: list[Refusal] = []
    edges_of: dict[tuple[str, ...], JsonValue] = {}
    for view in parsed:
        names = view.names if view.names is not None else tuple(document.cohorts)
        if any(name not in canonical.cohorts for name in names):
            continue
        if any(predicate.key not in canonical.predicates for predicate in view.predicates):
            continue
        if any(variable.key not in canonical.variables for variable in view.variables):
            continue
        if any(endpoint.key not in canonical.endpoints for endpoint in view.endpoints):
            continue
        cohorts = [canonical.cohorts[name] for name in names]
        if view.names is None:
            order = ViewIdentity.default_order([cohort.identity for cohort in cohorts])
            by_identity = {id(cohort.identity): cohort for cohort in cohorts}
            cohorts = [by_identity[id(identity)] for identity in order]
            names = tuple(cohort.name for cohort in cohorts)
        if len({cohort.release.manifest for cohort in cohorts}) != 1:
            refusals.append(
                Refusal(
                    code=RefusalCode.MIXED_RELEASES,
                    path=pointer(["views", view.index, "cohorts"]),
                    message=[text("A view's cohorts are of one release, and these are not")],
                )
            )
            continue
        predicates = tuple(canonical.predicates[p.key] for p in view.predicates)
        variables = tuple(canonical.variables[v.key] for v in view.variables)
        endpoints = tuple(canonical.endpoints[e.key] for e in view.endpoints)
        fields = view.analysis.entry.fields
        reference = names.index(view.reference) if view.reference in names else 0
        involved: dict[str, PackVersion] = {}
        for part in (*cohorts, *predicates, *variables):
            involved.update(part.packs)
        if view.analysis.version is not None and view.analysis.pack is not None:
            involved[view.analysis.pack] = view.analysis.version
        settings = [part.identity.disclosure for part in (*cohorts, *predicates)]
        given = [k for k in settings if k is not None]
        disclosure = max(given) if given else None
        published = canonical.published.get(cohorts[0].release.manifest)
        form = _withheld_form(view, cohorts[0], variables, disclosure, published)
        if form is not None:
            refusals.append(form)
            continue
        if isinstance(view.params, DistributionParams):
            wrong = _distributed(view.index, view.params, variables, disclosure, edges_of)
            if wrong:
                refusals += wrong
                continue
        if isinstance(view.params, ColumnsParams):
            wrong = _compared(view.index, view.params, variables)
            if wrong:
                refusals += wrong
                continue
        withheld = _disclosure(view, cohorts[0], disclosure, published)
        if withheld is not None:
            refusals.append(withheld)
            continue
        if isinstance(view.params, PackParams):
            wrong = _packed(view, cohorts[0], variables, analyses, endpoints)
            if wrong:
                refusals += wrong
                continue
        if isinstance(view.params, CoxParams):
            wrong = _coxed(view, cohorts[0], variables, endpoints)
            wrong += _coxed_predicates(view, cohorts[0], predicates, endpoints)
            if wrong:
                refusals += wrong
                continue
            variables = _interleaved(view, variables, predicates)
        identity = ViewIdentity(
            analysis=view.analysis.id,
            version=view.analysis.entry.version,
            cohorts=tuple(cohort.identity for cohort in cohorts),
            params=_canonical_params(
                view.params, predicates, variables, view.roles, endpoints, view.endpoint_roles
            ),
            packs={pack: version.results_version for pack, version in sorted(involved.items())},
            disclosure=disclosure,
            reference=reference if fields.uses_reference else None,
            overlap=view.overlap if fields.assumes_independent_groups else None,
        )
        found.append(
            CheckedView(
                index=view.index,
                analysis=view.analysis,
                params=view.params,
                cohorts=tuple(cohorts),
                reference=reference if fields.uses_reference else 0,
                overlap=view.overlap,
                predicates=predicates,
                identity=identity,
                packs=dict(sorted(involved.items())),
                variables=variables,
                roles=view.roles,
                endpoints=endpoints,
                endpoint_roles=view.endpoint_roles,
            )
        )
    return found, [refusal_as_written(refusal, positions or {}) for refusal in refusals]


def _alternatives() -> list[Segment]:
    """What a view refused for disclosure offers: ``count_cohort`` and the core's ``disclosed``
    analyses (D353)."""
    return [data(name) for name in ("count_cohort", *sorted(DISCLOSED))]


def _disclosure(
    view: ParsedView, cohort: CanonicalCohort, k: int | None, published: int | None
) -> Refusal | None:
    """What phase 2 refuses of a view for disclosure (§8.4, D353), before anything else phase 2
    checks of it that could run pack code or report an identifier column (``_packed``), so that
    no query runs and no pack code is called: where the dataset allows no
    row ids, a view of an analysis that lists or hands each member's values in their keys'
    order (``summary.members``, every pack's), ``ROW_IDS_NOT_ALLOWED``; under the effective
    setting ``k`` (the floor's included, and a draft's latest published release's,
    ``published``, D275), a view of a ``refused`` analysis, whatever its cohorts' sizes,
    ``WITHHELD_UNDER_K`` naming the setting that binds (``_setting``) and why. Each names the
    analysis and the setting, never a value of the data, and offers ``count_cohort`` and the
    core's ``disclosed`` analyses."""
    disclosure, lists_keys, because = disclosure_of(view.analysis)
    dataset = cohort.resolved.release.dataset_descriptor
    settings = None if dataset is None else dataset.fields.disclosure
    at = pointer(["views", view.index, "analysis"])
    if lists_keys and settings is not None and not settings.allow_row_ids:
        return Refusal(
            code=RefusalCode.ROW_IDS_NOT_ALLOWED,
            path=at,
            message=[
                text("The dataset does not allow row ids, so no view of "),
                data(view.analysis.id),
                text(" is run: it lists or hands each member's values in their keys' order, "),
                text("which name units (§8.4, D332, D344, D353): "),
                data(cohort.release.dataset),
            ],
            alternatives=_alternatives(),
        )
    if k is not None and disclosure is DisclosureClass.REFUSED:
        source = _setting(settings, k, published)
        return Refusal(
            code=RefusalCode.WITHHELD_UNDER_K,
            path=at,
            message=[
                text(f"Under a disclosure setting ({source}, {k}) no view of "),
                data(view.analysis.id),
                text(f" is run, whatever its cohorts' sizes: {because} (§8.4, D353)"),
            ],
            alternatives=_alternatives(),
        )
    return None


def _withheld_form(
    view: ParsedView,
    cohort: CanonicalCohort,
    variables: Sequence[CanonicalVariable],
    k: int | None,
    published: int | None,
) -> Refusal | None:
    """What phase 2 refuses of a view of a ``disclosed`` analysis for a form of its parameters
    the analysis withholds (``registry.withheld_form``, D379): under the effective setting ``k``,
    whatever its cohorts' sizes, ``WITHHELD_UNDER_K`` at the first such member, naming the setting
    that binds (``_setting``) and why, never a value; checked before the analysis's own checks of
    its variables (``_distributed``), so that none reports another member of a view that is not
    run, and none records edges it would not read. It offers the forms of the variable that holds
    the member which run under ``k`` in its place (``distribution.forms_under_k``): for
    ``summary.distribution``'s ``count: "rows"``, each aggregate the column takes, with the
    ``bins`` or ``values`` it needs, ``count`` counting each unit's rows as its value, which the
    pass protects. ``summary.distribution`` also withholds its memberships (``each``) where the
    descriptors bound the rows each unit reaches, which they decide rather than the member's
    path (``distribution.withheld_under_k``, D383), refused so at ``…/each``, the first withheld
    member in the columns' order refused, offering ``some`` and ``every`` with ``values`` where the
    path allows them (``resolve.aggregates_of``), each a question's split, which the pass protects
    one at a time (D329). Leaving ``count`` or ``each`` out is none of them: the column is
    multi-valued, so without an aggregate it is refused (``resolve._not_single``)."""
    if k is None:
        return None
    candidates: list[tuple[tuple[str | int, ...], str]] = []
    found = withheld_form(view.analysis, view.params, k)
    if found is not None:
        place, because = found
        candidates.append((("views", view.index, "params", *place), because))
    if isinstance(view.params, DistributionParams):
        candidates += [
            ((*given.at, "each"), why)
            for given, variable in zip(view.variables, variables, strict=True)
            if (why := distribution.withheld_under_k(variable)) is not None
        ]
    if not candidates:
        return None
    at, because = min(candidates, key=lambda candidate: _column_of(candidate[0]))
    decision = "D383" if at[-1] == "each" else "D379"
    alternatives: list[Segment] = [
        text(form)
        for given, variable in zip(view.variables, variables, strict=True)
        if at[: len(given.at)] == given.at
        for form in distribution.forms_under_k(variable, given.variable)
    ]
    dataset = cohort.resolved.release.dataset_descriptor
    settings = None if dataset is None else dataset.fields.disclosure
    source = _setting(settings, k, published)
    return Refusal(
        code=RefusalCode.WITHHELD_UNDER_K,
        path=pointer(list(at)),
        message=[
            text(f"Under a disclosure setting ({source}, {k}) no view of "),
            data(view.analysis.id),
            text(f" gives this member, whatever its cohorts' sizes: {because} (§8.4, {decision})"),
        ],
        alternatives=alternatives,
    )


def _column_of(at: tuple[str | int, ...]) -> int:
    """The index among a view's variables of the member at ``at`` that is withheld, the order in
    which a view's withheld members are refused: each lies below ``params/columns/<j>``."""
    column = at[4]
    assert at[3] == "columns", "a withheld member is a column's"
    assert isinstance(column, int), "a withheld member is a column's"
    return column


def _packed(
    view: ParsedView,
    cohort: CanonicalCohort,
    variables: Sequence[CanonicalVariable],
    analyses: Analyses | None,
    endpoints: Sequence[ResolvedEndpoint] = (),
) -> list[Refusal]:
    """What phase 2 refuses of a pack analysis's resolved view once its disclosure is checked
    (``_disclosure``; D341, D342, D352): an input column that reads the values of an
    identifier column (§5.4: declared so, a table's primary key or a relationship's column;
    ``count`` reads no value),
    which would hand units', rows' or people's identities, or whose ``where`` tests one; an
    endpoint (``endpoints``, as the view binds them) whose time, status or entry column is an
    identifier column, since its rows are handed too; an input column whose values are dates or
    datetimes; one whose column is not of the datatype, or not on the unit table, that its role
    requires (as applicability matches them, on the column's own datatype); and a ``predicate``
    requirement that does not hold of the release."""
    at = pointer(["views", view.index, "analysis"])
    found: list[Refusal] = []
    release = cohort.resolved.release
    requirements = {r.role: r for r in view.analysis.entry.fields.requires if r.kind == "column"}
    for role, given, variable in zip(view.roles, view.variables, variables, strict=True):
        written = pointer([*given.at, "column"])
        resolved = variable.resolved
        requirement = requirements[role]
        tested = _identifiers_tested(release, resolved)
        unread = _pack_unread(release, requirement, resolved)
        if unread == "identifier":
            found.append(
                Refusal(
                    code=RefusalCode.ROW_IDS_NOT_ALLOWED,
                    path=written,
                    message=[
                        text("A pack's analysis is handed no identifier column's values, which "),
                        text("name units, rows or people (§5.4, D342); count its rows instead: "),
                        data(resolved.column),
                    ],
                    alternatives=[data("count")],
                )
            )
        elif unread == "tested":
            found.append(
                Refusal(
                    code=RefusalCode.ROW_IDS_NOT_ALLOWED,
                    path=pointer([*given.at, "where"]),
                    message=[
                        text("A pack's analysis is handed no value computed from an identifier "),
                        text("column, which names units, rows or people (§5.4, D342), and this "),
                        text("variable's where tests one: "),
                        data(tested[0]),
                    ],
                )
            )
        elif unread == "values":
            found.append(
                Refusal(
                    code=RefusalCode.NOT_SUPPORTED,
                    path=written,
                    message=[
                        text("A pack's analysis is handed categories, booleans, numbers and "),
                        text("text, and this column's values are none of them: "),
                        data(resolved.column),
                    ],
                    alternatives=[data(name) for name in packs.TAKEN],
                )
            )
        elif unread == "datatype":
            found.append(
                Refusal(
                    code=RefusalCode.INVALID_VALUE,
                    path=written,
                    message=[
                        text("Its role takes columns of datatype "),
                        data(str(requirement.datatype)),
                        text(", and this one is not: "),
                        data(resolved.column),
                    ],
                )
            )
        elif unread == "unit":
            found.append(
                Refusal(
                    code=RefusalCode.INVALID_VALUE,
                    path=written,
                    message=[
                        text("Its role takes a column of the unit table itself, read as it is, "),
                        text("and this one is not: "),
                        data(resolved.column),
                    ],
                )
            )
    for given, endpoint in zip(view.endpoints, endpoints, strict=True):
        named = [v.column for v in endpoint.variables if _identifies(release, v.column)]
        if named:
            found.append(
                Refusal(
                    code=RefusalCode.ROW_IDS_NOT_ALLOWED,
                    path=pointer(list(given.at)),
                    message=[
                        text("A pack's analysis is handed no identifier column's values, which "),
                        text("name units, rows or people (§5.4, D342, D352), and this endpoint's "),
                        text("rows read one: "),
                        data(named[0]),
                    ],
                )
            )
    if found:
        return found
    release = cohort.resolved.release
    unmet = (
        [r.role for r in view.analysis.entry.fields.requires if r.predicate is not None]
        if analyses is None
        else analyses.unmet(
            view.analysis,
            release.descriptors,
            dataset=release.dataset,
            manifest=release.manifest,
        )
    )
    if unmet:
        found.append(
            Refusal(
                code=RefusalCode.NOT_SUPPORTED,
                path=at,
                message=[
                    text("The release does not meet this analysis's requirements: "),
                    *_listed(unmet),
                ],
                alternatives=[text("list_analyses gives each analysis's applicability")],
            )
        )
    return found


def _coxed(
    view: ParsedView,
    cohort: CanonicalCohort,
    variables: Sequence[CanonicalVariable],
    endpoints: Sequence[ResolvedEndpoint],
) -> list[Refusal]:
    """What phase 2 refuses of a ``survival.cox`` view's resolved covariates and stratum once its
    disclosure is checked (``_disclosure``; D367):

    - a column whose values have no coding (``cox.covariate_of``: dates and datetimes, which
      resolution leaves only as columns);
    - an identifier column's values (§5.4; ``count`` reads none), which name units, rows or
      people and as numbers mean nothing;
    - the endpoint's own time or status column, which would model the outcome by itself (an
      endpoint is on the unit table, so its columns are read as they are, and a covariate or
      stratum that names one reads it so too: resolution takes no question or aggregate of a
      unit's own column);
    - an aggregate whose rows' conditions test an identifier or the endpoint's columns
      (``_leaves_refused``), as a predicate's would (D370);
    - ``bins``, which divide only a histogram."""
    release = cohort.resolved.release
    [endpoint] = endpoints
    outcome = _outcome(endpoint)
    found: list[Refusal] = []
    for given, variable in zip(view.variables, variables, strict=True):
        resolved = variable.resolved
        column = pointer([*given.at, "column"])
        if resolved.rows is not None:
            found += _leaves_refused(release, outcome, resolved.rows, (*given.at, "where"))
        unread = _cox_unread(release, outcome, resolved)
        if unread == "coding":
            found.append(
                Refusal(
                    code=RefusalCode.NOT_SUPPORTED,
                    path=column,
                    message=[
                        text("survival.cox models categories, booleans, strings and numbers, "),
                        text("and this column's datatype is none of them: "),
                        data(resolved.column),
                        text(" ("),
                        data(resolved.datatype or "undeclared"),
                        text(")"),
                    ],
                    alternatives=[data(name) for name in cox.TAKEN],
                )
            )
        elif unread == "identifier":
            found.append(
                Refusal(
                    code=RefusalCode.INVALID_VALUE,
                    path=column,
                    message=[
                        text("An identifier column's values name units, rows or people (§5.4), "),
                        text("and are no covariate or stratum; count its rows instead: "),
                        data(resolved.column),
                    ],
                    alternatives=[data("count")],
                )
            )
        elif unread == "outcome":
            found.append(
                Refusal(
                    code=RefusalCode.INVALID_VALUE,
                    path=column,
                    message=[
                        text("The endpoint's time or status is the outcome the model explains, "),
                        text("and no covariate or stratum: "),
                        data(resolved.column),
                    ],
                )
            )
        elif given.variable.bins is not None:
            found.append(
                Refusal(
                    code=RefusalCode.INVALID_VALUE,
                    path=pointer([*given.at, "bins"]),
                    message=[
                        text("bins divide a histogram, which survival.cox does not draw; "),
                        text("summary.distribution draws one"),
                    ],
                )
            )
    return found


_PackUnread = Literal["identifier", "tested", "values", "datatype", "unit"]
_CoxUnread = Literal["coding", "identifier", "outcome", "conditions"]


def _pack_unread(
    release: Release, requirement: Requirement, variable: ResolvedVariable
) -> _PackUnread | None:
    """What phase 2 refuses of a pack analysis's input column (``_packed``, D341, D342), the
    first that holds: the values of an identifier column (``count`` reads none), a ``where``
    that tests one, values that are none of ``packs.TAKEN``, a column not of its role's
    datatype, and one not read as it is on the unit table where its role requires that; ``None``
    where it refuses none. A pack is handed one value per unit of each column (D335, D341), so a
    count of rows and memberships are values it is not handed; that check of their kinds is
    defensive, since resolution refuses both there and never offers either in place of another
    form (``ViewVariable.independent``, D380, D382)."""
    table, _, _ = variable.column.partition(".")
    if _identifies(release, variable.column) and variable.function != "count":
        return "identifier"
    if _identifiers_tested(release, variable):
        return "tested"
    if variable.kind in ("rows", "memberships") or not packs.taken(variable):
        return "values"
    if requirement.datatype is not None and variable.datatype != requirement.datatype:
        return "datatype"
    if requirement.on == "unit" and (
        table != variable.unit or variable.kind != "column" or variable.via
    ):
        return "unit"
    return None


def _cox_unread(
    release: Release, outcome: Collection[str], variable: ResolvedVariable
) -> _CoxUnread | None:
    """What phase 2 refuses of a ``survival.cox`` covariate or stratum but its ``bins``
    (``_coxed``, D367, D370), the first that holds: a column with no coding, an identifier's
    values (``count`` reads none), the endpoint's time or status (``outcome``), and conditions of
    its rows that test either (``_leaves_refused``, which phase 2 refuses beside the others);
    ``None`` where it refuses none. A covariate is one value per unit (D335, D367), so a count
    of rows and memberships have no coding; that check of their kinds is defensive, since
    resolution refuses both there and never offers either in place of another form
    (``ViewVariable.independent``, D380, D382)."""
    if variable.kind in ("rows", "memberships") or cox.covariate_of(variable) is None:
        return "coding"
    if _identifies(release, variable.column) and variable.function != "count":
        return "identifier"
    if variable.column in outcome:
        return "outcome"
    if variable.rows is not None and _leaves_refused(release, outcome, variable.rows, ()):
        return "conditions"
    return None


def _outcome(endpoint: ResolvedEndpoint) -> frozenset[str]:
    """An endpoint's time and status columns, the outcome a model explains (D367)."""
    return frozenset({endpoint.time.column, endpoint.status.column})


def _summarises(variable: ResolvedVariable, endpoints: Mapping[str, ResolvedEndpoint]) -> bool:
    """Whether ``summary.distribution`` reads a variable in a form offered in place of another
    (``resolve.Reads``): as its phase 2 does (``distribution.summarises``), a count of rows and
    memberships included (D378, D382)."""
    return distribution.summarises(variable)


def _compares(variable: ResolvedVariable, endpoints: Mapping[str, ResolvedEndpoint]) -> bool:
    """Whether ``compare.columns`` reads a variable in a form offered in place of another
    (``resolve.Reads``): as its phase 2 does (``distribution.summarises``), one value per unit
    (D335, D336), so neither a count of rows nor memberships; that check of their kinds is
    defensive, since resolution refuses both there and never offers either in place of another
    form (``ViewVariable.independent``, D380, D382)."""
    return variable.kind not in ("rows", "memberships") and distribution.summarises(variable)


def _coxes(key: str, variable: ResolvedVariable, endpoints: Mapping[str, ResolvedEndpoint]) -> bool:
    """Whether ``survival.cox`` reads a covariate or stratum in a form offered in place of
    another (``resolve.Reads``), as its phase 2 does (``_cox_unread``) against the view's
    endpoint, ``key``, as resolved; an endpoint refused is the view's refusal, and leaves no
    column out."""
    endpoint = endpoints.get(key)
    outcome = frozenset[str]() if endpoint is None else _outcome(endpoint)
    return _cox_unread(variable.release, outcome, variable) is None


def _packs(
    requirement: Requirement,
    variable: ResolvedVariable,
    endpoints: Mapping[str, ResolvedEndpoint],
) -> bool:
    """Whether a pack's analysis reads an input column of the role of ``requirement`` in a form
    offered in place of another (``resolve.Reads``), as its phase 2 does (``_pack_unread``)."""
    return _pack_unread(variable.release, requirement, variable) is None


def _coxed_predicates(
    view: ParsedView,
    cohort: CanonicalCohort,
    predicates: Sequence[CanonicalCohort],
    endpoints: Sequence[ResolvedEndpoint],
) -> list[Refusal]:
    """What phase 2 refuses of a ``survival.cox`` view's predicates (D370), as ``_coxed``
    refuses a variable: each leaf ``_leaves_refused`` refuses."""
    release = cohort.resolved.release
    [endpoint] = endpoints
    found: list[Refusal] = []
    for given, predicate in zip(view.predicates, predicates, strict=True):
        found += _leaves_refused(release, _outcome(endpoint), predicate.resolved.tree, given.at)
    return found


def _leaves_refused(
    release: Release, outcome: Collection[str], clause: RClause, at: Position
) -> list[Refusal]:
    """The leaves of a ``survival.cox`` covariate's clause (a predicate, or an aggregate's rows'
    conditions) that phase 2 refuses (D367, D370): one that tests an identifier column (a value
    leaf on one, or a ``covered`` leaf scoped by one: §5.4), which marks named units, and one that
    tests the endpoint's own time or status (``outcome``), which models the outcome by itself;
    each ``INVALID_VALUE`` at the first place it was written (``at`` where none is known)."""
    found: list[Refusal] = []
    for column, origin in _tested(clause):
        path = pointer(list(min(origin, key=in_document_order, default=at)))
        if _identifies(release, column):
            found.append(
                Refusal(
                    code=RefusalCode.INVALID_VALUE,
                    path=path,
                    message=[
                        text("A covariate's conditions test no identifier column, whose values "),
                        text("name units, rows or people (§5.4): "),
                        data(column),
                    ],
                )
            )
        elif column in outcome:
            found.append(
                Refusal(
                    code=RefusalCode.INVALID_VALUE,
                    path=path,
                    message=[
                        text("The endpoint's time or status is the outcome the model "),
                        text("explains, and no covariate's conditions test it: "),
                        data(column),
                    ],
                )
            )
    return found


def _interleaved(
    view: ParsedView,
    variables: Sequence[CanonicalVariable],
    predicates: Sequence[CanonicalCohort],
) -> tuple[CanonicalVariable, ...]:
    """A ``survival.cox`` view's variables in its covariates' order, then its stratum (D370): a
    column covariate its variable, a predicate its truth (``predicate_variable``)."""
    columns = iter(variables)
    asked = iter(zip(view.predicates, predicates, strict=True))
    found: list[CanonicalVariable] = []
    for form in view.forms:
        if form == "column":
            found.append(next(columns))
        else:
            given, predicate = next(asked)
            found.append(predicate_variable(given.key, predicate))
    return (*found, *columns)


def _distributed(
    index: int,
    params: DistributionParams,
    variables: Sequence[CanonicalVariable],
    k: int | None,
    edges_of: dict[tuple[str, ...], JsonValue],
) -> list[Refusal]:
    """What ``summary.distribution`` refuses of its resolved variables (D328, D329): a column
    whose values are neither categories nor numbers, ``bins`` for categories (memberships, which
    take none, and are categories, D382), under *k* a number's histogram whose edges
    only the data would give (§8.4), and, under *k*, a histogram of a column that the call, in
    this view or an earlier one, reads already with other edges (``edges_of``, shared by the
    call's views; the edges it takes, from ``bins`` or the declared range, so that writing the
    range's own edges is no conflict): a column's values by any variable over it (the column
    itself, or ``max``, ``min`` or ``mean`` of it, whatever rows), a ``count`` by the rows it
    counts (``_counted``), since two histograms of one quantity give by difference the counts
    that merging hides. Without *k* every count is shown, so there is nothing to difference. The
    key holds the release's manifest: columns of two datasets are two quantities, which only
    views over several datasets (M6) can meet. A view's edges join ``edges_of`` only where it
    is refused nothing here, since a view that is not run reads none."""
    found: list[Refusal] = []
    edges: dict[tuple[str, ...], JsonValue] = {}
    for position, (variable, given) in enumerate(zip(variables, params.columns, strict=True)):
        at: list[str | int] = ["views", index, "params", "columns", position]
        if not distribution.summarised(variable):
            found.append(
                Refusal(
                    code=RefusalCode.NOT_SUPPORTED,
                    path=pointer([*at, "column"]),
                    message=[
                        text("summary.distribution summarises categories and numbers, and "),
                        text("this column's values are neither: "),
                        data(variable.resolved.column),
                    ],
                    alternatives=[
                        data(name)
                        for name in ("category", "boolean", "number", "integer", "time_offset")
                    ],
                )
            )
            continue
        if distribution.categorical(variable):
            if given.bins is not None:
                found.append(
                    Refusal(
                        code=RefusalCode.INVALID_VALUE,
                        path=pointer([*at, "bins"]),
                        message=[
                            text("bins divide numbers, and this column's values are categories")
                        ],
                    )
                )
            continue
        if k is not None and distribution.needs_edges(variable, given.bins):
            found.append(
                Refusal(
                    code=RefusalCode.MISSING_MEMBER,
                    path=pointer([*at, "bins"]),
                    message=[
                        text("Under the disclosure settings a histogram's edges never come from "),
                        text("the data (§8.4), and this column declares no range: give bins"),
                    ],
                )
            )
            continue
        if k is None:
            continue
        resolved = variable.resolved
        quantity = (
            (resolved.release.manifest, "count", canonical(_counted(variable.form)).decode())
            if resolved.function == "count"
            else (resolved.release.manifest, "values", resolved.column)
        )
        bins: JsonValue = list[JsonValue](distribution.effective_edges(variable, given.bins) or [])
        taken = edges_of.get(quantity, edges.get(quantity))
        if taken is not None and taken != bins:
            found.append(
                Refusal(
                    code=RefusalCode.CONFLICTING_MEMBERS,
                    path=pointer([*at, "bins"]),
                    message=[
                        text("The call reads these values already with other bins, and "),
                        text("two histograms of one column give by difference the counts that "),
                        text("merging hides (§8.4): "),
                        data(resolved.column),
                    ],
                )
            )
            continue
        edges.setdefault(quantity, bins)
    if not found:
        edges_of.update(edges)
    return found


def _compared(
    index: int, params: ColumnsParams, variables: Sequence[CanonicalVariable]
) -> list[Refusal]:
    """What ``compare.columns`` refuses of its resolved variables (D336): a column whose values
    are neither categories nor numbers, and ``bins``, which divide only a histogram, which it
    does not draw."""
    found: list[Refusal] = []
    for position, (variable, given) in enumerate(zip(variables, params.columns, strict=True)):
        at: list[str | int] = ["views", index, "params", "columns", position]
        if not distribution.summarised(variable):
            found.append(
                Refusal(
                    code=RefusalCode.NOT_SUPPORTED,
                    path=pointer([*at, "column"]),
                    message=[
                        text("compare.columns compares categories and numbers, and this "),
                        text("column's values are neither: "),
                        data(variable.resolved.column),
                    ],
                    alternatives=[
                        data(name)
                        for name in ("category", "boolean", "number", "integer", "time_offset")
                    ],
                )
            )
        elif given.bins is not None:
            found.append(
                Refusal(
                    code=RefusalCode.INVALID_VALUE,
                    path=pointer([*at, "bins"]),
                    message=[
                        text("bins divide a histogram, which compare.columns does not draw; "),
                        text("summary.distribution draws one"),
                    ],
                )
            )
    return found


def _counted(form: JsonValue) -> JsonValue:
    """What a ``count`` counts: its canonical form without the column it names and the lookups
    to it, which a count does not read (D326), so that counts of one canonical set of rows are
    one; the same rows written otherwise (a redundant condition) are another key, as §8.4's
    Limits say."""
    assert isinstance(form, dict)
    return {key: member for key, member in form.items() if key not in ("column", "lookup")}


def _identifies(release: Release, column: str) -> bool:
    """Whether a column identifies rows or people (§5.4, ``resolve.identifying``)."""
    table, _, name = column.partition(".")
    descriptor = release.column(table, name)
    return descriptor is not None and identifying(release, descriptor)


def _tested(clause: RClause) -> list[tuple[str, Origin]]:
    """The columns a clause tests, each with the places it was written: the column of each value
    leaf, and the scope columns of each ``covered`` leaf, in the order they are met (D370)."""
    found: list[tuple[str, Origin]] = []
    pending: list[RClause] = [clause]
    while pending:
        node = pending.pop()
        if isinstance(node, RValue):
            found.append((node.column, node.origin))
        elif isinstance(node, RCovered):
            found += [(f"{node.table}.{column}", node.origin) for column, _ in node.scope or ()]
        elif isinstance(node, RExists):
            pending.extend(node.where)
        elif isinstance(node, RAll | RAny):
            pending.extend(node.members)
        elif isinstance(node, RNot | RKnown | RUnknown):
            pending.append(node.member)
    return found


def _identifiers_tested(release: Release, variable: ResolvedVariable) -> list[str]:
    """The identifier columns an aggregate's ``where`` tests, sorted: the value leaves of its
    rows' conditions, and the scope columns of its ``covered`` leaves (D342). A question takes no
    ``where``, and the column it asks about is the variable's own."""
    if variable.rows is None:
        return []
    return sorted({column for column, _ in _tested(variable.rows) if _identifies(release, column)})


def _setting(settings: Disclosure | None, k: int, published: int | None) -> str:
    """The disclosure setting that binds at ``k``: the release's own ``min_cell_count``, a
    draft's latest published release's (``published``, D275), or else the deployment's
    floor (D332, D344)."""
    own = None if settings is None else settings.min_cell_count
    if own == k:
        return "the dataset's min_cell_count"
    if published == k:
        return "the latest published release's min_cell_count"
    return "the deployment's floor"


def _canonical_params(
    params: DocModel,
    predicates: Sequence[CanonicalCohort],
    variables: Sequence[CanonicalVariable],
    roles: Sequence[str] = (),
    endpoints: Sequence[ResolvedEndpoint] = (),
    endpoint_roles: Sequence[str] = (),
) -> JsonValue:
    """A view's canonical parameters: every default written, each predicate as its canonical
    clause tree, and each variable as its canonical form, with a number's ``bins`` (``null``
    for none) (§7.6, D325); a pack analysis's, each role's variables' forms, each bound
    endpoint's form by role where it binds any, and its options as written (D341, D352); a
    survival analysis's, its endpoint's form, its grid (``null`` for none), landmarks and level
    (D348); ``survival.cox``'s, its covariates' forms in order, its endpoint's form, its level
    and its stratum's form (``null`` for none) (D366)."""
    if isinstance(params, SurvivalParams):
        [endpoint] = endpoints
        return {
            "endpoint": endpoint.form,
            "grid": None if params.grid is None else list[JsonValue](params.grid),
            "landmarks": list[JsonValue](params.landmarks or []),
            "level": params.level,
        }
    if isinstance(params, CoxParams):
        [endpoint] = endpoints
        stratified = params.stratum is not None
        forms = [variable.form for variable in variables]
        return {
            "covariates": forms[: len(forms) - stratified],
            "endpoint": endpoint.form,
            "level": params.level,
            "stratum": forms[-1] if stratified else None,
        }
    if isinstance(params, PackParams):
        by_role: dict[str, list[JsonValue]] = {}
        for role, variable in zip(roles, variables, strict=True):
            by_role.setdefault(role, []).append(variable.form)
        found: dict[str, JsonValue] = {
            "columns": cast(dict[str, JsonValue], by_role),
            "options": dict(params.options or {}),
        }
        if endpoints:
            found["endpoints"] = {
                role: endpoint.form
                for role, endpoint in zip(endpoint_roles, endpoints, strict=True)
            }
        return found
    if isinstance(params, ExistenceParams):
        return {
            "level": params.level,
            "predicates": [p.form[p.release.manifest] for p in predicates],
        }
    if isinstance(params, DistributionParams):
        columns: list[JsonValue] = []
        for variable, given in zip(variables, params.columns, strict=True):
            form = dict(cast(dict[str, JsonValue], variable.form))
            if not distribution.categorical(variable):
                form["bins"] = None if given.bins is None else list[JsonValue](given.bins)
            columns.append(form)
        return {"columns": columns}
    if isinstance(params, ColumnsParams):
        return {"columns": [variable.form for variable in variables], "level": params.level}
    if isinstance(params, MembersParams):
        return {"limit": params.limit, "offset": params.offset}
    raise ValueError("the canonical parameters of an analysis the core does not run")


__all__ = [
    "LISTED",
    "CheckedView",
    "ParsedView",
    "checked",
    "deferred",
    "parse",
    "static_caveats",
]

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
   there) or the document's only one (else ``MISSING_MEMBER`` at ``cohorts``) (D331). Its
   predicates, variables and endpoints (a survival analysis's one, ``params.endpoint`` or the one
   usable endpoint on the unit table, D347; a pack's by role, D352) are then handed to
   ``canonicalise`` (``ViewPredicate``, ``ViewVariable``, ``ViewEndpoint``), resolved with the
   cohorts in the release of the view's cohorts, on the unit table.
2. ``checked``, after phase 1: a view whose cohorts, predicates, variables and endpoints all
   canonicalised, and whose variables its analysis takes (``summary.distribution``'s: categories or
   numbers, ``bins`` only for numbers, under *k* a number's histogram edges from ``bins`` or a
   declared range, and under *k* one set of edges for a column's values across the call's views,
   D328, D329; ``compare.columns``': categories or numbers, and no ``bins``, D336; a pack's: no
   dates or datetimes, and each of its role's ``datatype`` and ``on``, D341), whose disclosure
   allows it (``_disclosure``, D353: no view of a ``refused`` analysis under any disclosure
   setting, ``WITHHELD_UNDER_K`` at ``analysis``, and none of one that lists or hands each
   member's values in the keys' order where the dataset allows no row ids,
   ``ROW_IDS_NOT_ALLOWED`` there), and, for a pack's analysis, whose requirement predicates hold
   of the release (``NOT_SUPPORTED`` at ``analysis`` otherwise, D341), gets its canonical form
   and ids (``ViewIdentity``): its cohorts in view order, or by computation id when it lists none
   (D284); its reference's position, for an analysis that declares ``uses_reference``, the
   first cohort's by default; ``overlap``, for one that declares ``assumes_independent_groups``; its
   canonical parameters, every default written, every predicate as its canonical clause tree, every
   variable as its canonical form and every endpoint as its (``{"id", "time"}``); the effective *k*
   over its cohorts and predicates and the floor; and the results versions of the packs of its
   analysis, cohorts, predicates and variables. A view one of whose cohorts, predicates, variables
   or endpoints was refused is left out; their refusals say why. A view whose cohorts are of more
   than one release is ``MIXED_RELEASES``, which resolution refuses first.

A view's readback is its analysis's, rendered from its canonical form and the release's descriptors
(§7.7); its static caveats are those its result carries that need no data: the fields its cohorts,
predicates, variables and endpoints read that are not confirmed (an undeclared entry among them,
§5.8), a draft release, and what the packs' caveat rules raised for its cohorts and predicates,
which are the canonical parts of a view a pack can read (D287, D317).
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import cast

from pydantic import JsonValue, ValidationError

from aibi.core.analyses import columns, distribution, existence, members, packs, survival
from aibi.core.analyses.registry import (
    CORE,
    DISCLOSED,
    LATER,
    Analyses,
    DisclosureClass,
    Registered,
    disclosure_of,
)
from aibi.core.engine.canonical import (
    CanonicalCohort,
    Canonicalisation,
    CanonicalVariable,
    ViewIdentity,
)
from aibi.core.engine.data import Release
from aibi.core.engine.resolve import (
    PER_CATEGORY,
    FieldRead,
    ResolvedEndpoint,
    ResolvedVariable,
    ViewEndpoint,
    ViewPredicate,
    ViewVariable,
    identifying,
)
from aibi.core.engine.resolved import (
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
    DistributionParams,
    ExistenceParams,
    MembersParams,
    PackParams,
    SurvivalParams,
    Variable,
)
from aibi.core.schema.caveats import CORE_SEVERITIES, Caveat, CaveatCode, sort_caveats
from aibi.core.schema.descriptors import Disclosure
from aibi.core.schema.document import (
    PARSED,
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
from aibi.core.schema.loading import as_written, refusal_from_error
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
        written, parameter = as_written(at, self.positions)
        segments = list(message)
        if parameter is not None:
            segments += [text(" (in the value of parameter "), data(parameter), text(")")]
        self.found.append(
            Refusal(
                code=code,
                path=pointer(list(written)),
                message=segments,
                alternatives=list(alternatives),
                limit=limit,
            )
        )

    def of_error(self, error: ValidationError, model: type[DocModel], at: Position) -> None:
        """A parameter model's errors, pointed into the document as written."""
        for details in error.errors(include_url=False, include_input=False):
            refusal = refusal_from_error(details, model)
            tokens = () if refusal.path is None else _tokens(refusal.path)
            written, parameter = as_written((*at, *tokens), self.positions)
            message = list(refusal.message)
            if parameter is not None:
                message += [text(" (in the value of parameter "), data(parameter), text(")")]
            self.found.append(
                refusal.model_copy(update={"path": pointer(list(written)), "message": message})
            )


def _tokens(path: str) -> Position:
    return tuple(token.replace("~1", "/").replace("~0", "~") for token in path.split("/")[1:])


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
            predicates = _predicates(params, index, reference, refusals)
        elif isinstance(params, DistributionParams | ColumnsParams):
            independent = bool(fields.assumes_independent_groups)
            written = [(("columns", j), v) for j, v in enumerate(params.columns)]
            variables = _variables(written, index, reference, refusals, independent)
        elif isinstance(params, MembersParams):
            _one_cohort(view.cohorts, len(document.cohorts), index, refusals)
        elif isinstance(params, SurvivalParams) and reference is not None:
            endpoints = (
                ViewEndpoint(
                    f"{index}/endpoint",
                    reference,
                    ("views", index, "params", "endpoint"),
                    params.endpoint,
                ),
            )
        elif isinstance(params, PackParams):
            cohorts = len(view.cohorts) if view.cohorts is not None else len(document.cohorts)
            written = _pack_params(found, params, cohorts, index, analyses, refusals)
            variables = _variables(written, index, reference, refusals, True)
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


def _predicates(
    params: ExistenceParams, index: int, reference: str | None, refusals: _Refusals
) -> tuple[ViewPredicate, ...]:
    base: list[str | int] = ["views", index, "params", "predicates"]
    before = len(refusals.found)
    pack_leaves = [0] * len(params.predicates)
    for leaf, path, _ in walk(list(params.predicates), base):
        position = int(path[len(base)])
        pack_leaves[position] += isinstance(leaf, PackLeaf)
        if isinstance(leaf, IdsLeaf | CohortLeaf):
            refusals.add(
                RefusalCode.LEAF_NOT_ALLOWED,
                tuple(path),
                text(f"A predicate holds no {leaf.kind} leaf: it is asked of every unit of the "),
                text("view's cohorts, and a list of units or another cohort belongs in a cohort"),
            )
    for position, count in enumerate(pack_leaves):
        if count > MAX_PACK_LEAVES:
            refusals.add(
                RefusalCode.LIMIT_EXCEEDED,
                (*base, position),
                text(f"The predicate has {count} pack leaves, and at most "),
                text(f"{MAX_PACK_LEAVES} may be: each is compiled by its pack (D285)"),
                limit=Limit(name=PACK_LEAVES, max=MAX_PACK_LEAVES),
            )
    if reference is None or len(refusals.found) > before:
        return ()
    return tuple(
        ViewPredicate(
            key=f"{index}/{position}",
            reference=reference,
            at=(*base, position),
            clause=clause,
        )
        for position, clause in enumerate(params.predicates)
    )


def _variables(
    written: Sequence[tuple[tuple[str | int, ...], Variable]],
    index: int,
    reference: str | None,
    refusals: _Refusals,
    independent: bool,
) -> tuple[ViewVariable, ...]:
    """A view's variables to resolve (D325), each with its place below ``params``: the ``where``
    of each holds no ``ids`` and no ``cohort`` leaf, as a predicate holds none, and at most as
    many pack leaves as a cohort, which resolution expands (D345); ``count: "rows"`` is a
    descriptive analysis's, from M3.2e, and never one that assumes independent groups
    (``independent``), which compares units (§9.2, D335)."""
    base: list[str | int] = ["views", index, "params"]
    before = len(refusals.found)
    for place, variable in written:
        if variable.count is not None and independent:
            refusals.add(
                RefusalCode.INVALID_VALUE,
                (*base, *place, "count"),
                text("This analysis compares units, one value each (§9.2), so a variable counts "),
                text("units, not rows: leave count out"),
            )
        elif variable.count is not None:
            refusals.add(
                RefusalCode.NOT_SUPPORTED,
                (*base, *place, "count"),
                text(f'Counting rows (count: "rows", §9.2) comes with {PER_CATEGORY}; a '),
                text("variable counts units until then"),
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
        )
        for place, variable in written
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
) -> tuple[list[CheckedView], list[Refusal]]:
    """The second step (module docstring): the views whose cohorts and predicates all
    canonicalised, in canonical form; and a refusal for a view whose cohorts are of more than
    one release (``MIXED_RELEASES``), which resolution refuses first (§7.4). ``analyses`` runs
    a pack analysis's requirement predicates; without it, none holds."""
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
        published = canonical.published.get(cohorts[0].release.manifest)
        withheld = _disclosure(view, cohorts[0], disclosure, published)
        if withheld is not None:
            refusals.append(withheld)
            continue
        if isinstance(view.params, PackParams):
            wrong = _packed(view, cohorts[0], variables, analyses, endpoints)
            if wrong:
                refusals += wrong
                continue
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
    return found, refusals


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
        table, _, _ = resolved.column.partition(".")
        tested = _identifiers_tested(release, resolved)
        if _identifies(release, resolved.column) and resolved.function != "count":
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
        elif tested:
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
        elif not packs.taken(resolved):
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
        elif requirement.datatype is not None and resolved.datatype != requirement.datatype:
            found.append(
                Refusal(
                    code=RefusalCode.INVALID_VALUE,
                    path=written,
                    message=[
                        text("Its role takes columns of datatype "),
                        data(requirement.datatype),
                        text(", and this one is not: "),
                        data(resolved.column),
                    ],
                )
            )
        elif requirement.on == "unit" and (
            table != resolved.unit or resolved.kind != "column" or resolved.via
        ):
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


def _distributed(
    index: int,
    params: DistributionParams,
    variables: Sequence[CanonicalVariable],
    k: int | None,
    edges_of: dict[tuple[str, ...], JsonValue],
) -> list[Refusal]:
    """What ``summary.distribution`` refuses of its resolved variables (D328, D329): a column
    whose values are neither categories nor numbers, ``bins`` for categories, under *k* a
    number's histogram whose edges only the data would give (§8.4), and, under *k*, a histogram
    of a column that the call, in this view or an earlier one, reads already with other edges
    (``edges_of``, shared by the call's views; the edges it takes, from ``bins`` or the declared
    range, so that writing the range's own edges is no conflict): a column's values by any
    variable over it (the column itself, or ``max``, ``min`` or ``mean`` of it, whatever rows),
    a ``count`` by the rows it counts (``_counted``), since two histograms of one quantity give
    by difference the counts that merging hides. Without *k* every count is shown, so there is
    nothing to difference. The key holds the release's manifest: columns of two datasets are
    two quantities, which only views over several datasets (M6) can meet."""
    found: list[Refusal] = []
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
        if quantity in edges_of and edges_of[quantity] != bins:
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
        edges_of.setdefault(quantity, bins)
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


def _identifiers_tested(release: Release, variable: ResolvedVariable) -> list[str]:
    """The identifier columns an aggregate's ``where`` tests, sorted: the value leaves of its
    rows' conditions, and the scope columns of its ``covered`` leaves (D342). A question takes no
    ``where``, and the column it asks about is the variable's own."""
    found: set[str] = set()
    pending: list[RClause] = [] if variable.rows is None else [variable.rows]
    while pending:
        node = pending.pop()
        if isinstance(node, RValue):
            if _identifies(release, node.column):
                found.add(node.column)
        elif isinstance(node, RCovered):
            for column, _ in node.scope or ():
                if _identifies(release, f"{node.table}.{column}"):
                    found.add(f"{node.table}.{column}")
        elif isinstance(node, RExists):
            pending.extend(node.where)
        elif isinstance(node, RAll | RAny):
            pending.extend(node.members)
        elif isinstance(node, RNot | RKnown | RUnknown):
            pending.append(node.member)
    return sorted(found)


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
    (D348)."""
    if isinstance(params, SurvivalParams):
        [endpoint] = endpoints
        return {
            "endpoint": endpoint.form,
            "grid": None if params.grid is None else list[JsonValue](params.grid),
            "landmarks": list[JsonValue](params.landmarks or []),
            "level": params.level,
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

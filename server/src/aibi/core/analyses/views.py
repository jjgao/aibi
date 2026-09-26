"""Phase 2 of canonicalisation: each view checked against its analysis (SPEC §7.4, §7.6; D317).

A view is checked in two steps, around phase 1:

1. ``parse``, before the cohorts are resolved: its ``analysis`` is one the registry holds
   (``UNKNOWN_ANALYSIS``, listing those it holds; a pack's analysis is ``NOT_SUPPORTED`` until
   M3.2d, D316, D324; a core analysis of §9.5 a later slice implements is passed over, and
   ``deferred`` gives its ``NOT_SUPPORTED``, D317); its ``params`` are the analysis's
   parameters, refused where they are written in the document; an analysis that declares
   ``uses_reference`` has its ``cohorts`` listed (``MISSING_MEMBER``); and each predicate of
   its parameters holds no ``ids`` and no ``cohort`` leaf (``LEAF_NOT_ALLOWED``: a predicate is
   asked of every unit, and those belong in a cohort) and at most as many pack leaves as a
   cohort; and the ``where`` of each variable of its parameters holds no ``ids`` or ``cohort``
   leaf either (``LEAF_NOT_ALLOWED``) and no pack leaf (``NOT_SUPPORTED`` until M3.2d, D324).
   Its predicates and variables are then handed to ``canonicalise`` (``ViewPredicate``,
   ``ViewVariable``), resolved with the cohorts in the release of the view's cohorts, on the
   unit table.
2. ``checked``, after phase 1: a view whose cohorts, predicates and variables all canonicalised, and
   whose variables its analysis takes (``summary.distribution``'s: categories or numbers, ``bins``
   only for numbers, under *k* a number's histogram edges from ``bins`` or a declared range, and
   under *k* one set of edges for a column's values across the call's views, D328, D329), gets its
   canonical form and ids (``ViewIdentity``): its cohorts in view order, or by computation id when
   it lists none (D284); its reference's position, for an analysis that declares ``uses_reference``,
   the first cohort's by default; ``overlap``, for one that declares ``assumes_independent_groups``;
   its canonical parameters, every default written, every predicate as its canonical clause tree and
   every variable as its canonical form; the effective *k* over its cohorts and predicates and the
   floor; and the results versions of the packs of its analysis, cohorts and predicates. A view one
   of whose cohorts or predicates was refused is left out; their refusals say why. A view whose
   cohorts are of more than one release is ``MIXED_RELEASES``, which resolution refuses first.

A view's readback is its analysis's, rendered from its canonical form and the release's descriptors
(§7.7); its static caveats are those its result carries that need no data: the fields its cohorts,
predicates and variables read that are not confirmed, a draft release, and what the packs' caveat
rules raised for its cohorts and predicates, which are the canonical parts of a view a pack can read
(D287, D317).
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import cast

from pydantic import JsonValue, ValidationError

from aibi.core.analyses import distribution, existence
from aibi.core.analyses.registry import CORE, LATER, PACK_ANALYSES, Analyses, Registered
from aibi.core.engine.canonical import (
    CanonicalCohort,
    Canonicalisation,
    CanonicalVariable,
    ViewIdentity,
)
from aibi.core.engine.resolve import ViewPredicate, ViewVariable
from aibi.core.schema.analyses import DistributionParams, ExistenceParams
from aibi.core.schema.caveats import CORE_SEVERITIES, Caveat, CaveatCode, sort_caveats
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
from aibi.core.schema.limits import MAX_PACK_LEAVES, PACK_LEAVES
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
        if found.params is None:
            refusals.add(
                RefusalCode.NOT_SUPPORTED,
                (*at, "analysis"),
                text(f"A pack's analysis is run from {PACK_ANALYSES}, which gives it the inputs "),
                text("its requirements name; this one is listed, not run: "),
                data(view.analysis),
            )
            continue
        before = len(refusals.found)
        params: DocModel | None = None
        try:
            params = found.params.model_validate(view.params or {}, context={PARSED: True})
        except ValidationError as error:
            refusals.of_error(error, found.params, (*at, "params"))
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
        first = view.cohorts[0] if view.cohorts else next(iter(document.cohorts), None)
        reference = _reference_of(document, first)
        if isinstance(params, ExistenceParams):
            predicates = _predicates(params, index, reference, refusals)
        elif isinstance(params, DistributionParams):
            variables = _variables(params, index, reference, refusals)
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
            )
        )
    return parsed, refusals.found


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
    params: DistributionParams, index: int, reference: str | None, refusals: _Refusals
) -> tuple[ViewVariable, ...]:
    """A view's variables to resolve (D325): the ``where`` of each holds no ``ids`` and no
    ``cohort`` leaf, as a predicate holds none, and no pack leaf, which the slice that runs
    packs' analyses expands (D324)."""
    base: list[str | int] = ["views", index, "params", "columns"]
    before = len(refusals.found)
    for position, variable in enumerate(params.columns):
        if variable.count is not None:
            refusals.add(
                RefusalCode.NOT_SUPPORTED,
                (*base, position, "count"),
                text('Counting rows (count: "rows", §9.2) comes with M3.2c; a variable counts '),
                text("units until then"),
            )
        where: list[str | int] = [*base, position, "where"]
        for leaf, path, _ in walk(list(variable.where or []), where):
            if isinstance(leaf, IdsLeaf | CohortLeaf):
                refusals.add(
                    RefusalCode.LEAF_NOT_ALLOWED,
                    tuple(path),
                    text(f"A column's where holds no {leaf.kind} leaf: it is asked of the rows a "),
                    text("column aggregates, and a list of units or a cohort belongs in a cohort"),
                )
            elif isinstance(leaf, PackLeaf):
                refusals.add(
                    RefusalCode.NOT_SUPPORTED,
                    tuple(path),
                    text(f"A pack leaf in a column's where is expanded from {PACK_ANALYSES}; "),
                    text("write its conditions with the core's leaves"),
                )
    if reference is None or len(refusals.found) > before:
        return ()
    return tuple(
        ViewVariable(
            key=f"{index}/{position}",
            reference=reference,
            at=(*base, position),
            variable=variable,
        )
        for position, variable in enumerate(params.columns)
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
        assert isinstance(self.params, DistributionParams), "the core's analyses are known"
        return distribution.view_readback(len(self.cohorts), self.variables)

    def static_caveats(self) -> list[Caveat]:
        """The caveats its result carries that need no data (module docstring)."""
        return static_caveats(self.cohorts, self.predicates, self.variables)


def static_caveats(
    cohorts: Sequence[CanonicalCohort],
    predicates: Sequence[CanonicalCohort],
    variables: Sequence[CanonicalVariable] = (),
) -> list[Caveat]:
    found: list[Caveat] = []
    groups = (
        (cohorts, [read for cohort in cohorts for read in cohort.resolved.unconfirmed]),
        (
            predicates,
            [
                *(read for cohort in predicates for read in cohort.resolved.unconfirmed),
                *(read for variable in variables for read in variable.resolved.unconfirmed),
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
    document: Document, parsed: Sequence[ParsedView], canonical: Canonicalisation
) -> tuple[list[CheckedView], list[Refusal]]:
    """The second step (module docstring): the views whose cohorts and predicates all
    canonicalised, in canonical form; and a refusal for a view whose cohorts are of more than
    one release (``MIXED_RELEASES``), which resolution refuses first (§7.4)."""
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
        fields = view.analysis.entry.fields
        reference = names.index(view.reference) if view.reference in names else 0
        packs: dict[str, PackVersion] = {}
        for part in (*cohorts, *predicates):
            packs.update(part.packs)
        settings = [part.identity.disclosure for part in (*cohorts, *predicates)]
        given = [k for k in settings if k is not None]
        disclosure = max(given) if given else None
        if isinstance(view.params, DistributionParams):
            wrong = _distributed(view.index, view.params, variables, disclosure, edges_of)
            if wrong:
                refusals += wrong
                continue
        identity = ViewIdentity(
            analysis=view.analysis.id,
            version=view.analysis.entry.version,
            cohorts=tuple(cohort.identity for cohort in cohorts),
            params=_canonical_params(view.params, predicates, variables),
            packs={pack: version.results_version for pack, version in sorted(packs.items())},
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
                packs=dict(sorted(packs.items())),
                variables=variables,
            )
        )
    return found, refusals


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


def _counted(form: JsonValue) -> JsonValue:
    """What a ``count`` counts: its canonical form without the column it names and the lookups
    to it, which a count does not read (D326), so that counts of one set of rows are one."""
    assert isinstance(form, dict)
    return {key: member for key, member in form.items() if key not in ("column", "lookup")}


def _canonical_params(
    params: DocModel,
    predicates: Sequence[CanonicalCohort],
    variables: Sequence[CanonicalVariable],
) -> JsonValue:
    """A view's canonical parameters: every default written, each predicate as its canonical
    clause tree, and each variable as its canonical form, with a number's ``bins`` (``null``
    for none) (§7.6, D325)."""
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

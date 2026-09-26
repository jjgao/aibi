"""Phase 2 of canonicalisation: each view checked against its analysis (SPEC §7.4, §7.6; D317).

A view is checked in two steps, around phase 1:

1. ``parse``, before the cohorts are resolved: its ``analysis`` is one the registry holds
   (``UNKNOWN_ANALYSIS``, listing those it holds; a pack's analysis is ``NOT_SUPPORTED`` until
   M3.2, D316; a core analysis of §9.5 a later slice implements is passed over, and
   ``deferred`` gives its ``NOT_SUPPORTED``, D317); its ``params`` are the analysis's
   parameters, refused where they are written in the document; an analysis that declares
   ``uses_reference`` has its ``cohorts`` listed (``MISSING_MEMBER``); and each predicate of
   its parameters holds no ``ids`` and no ``cohort`` leaf (``LEAF_NOT_ALLOWED``: a predicate is
   asked of every unit, and those belong in a cohort) and at most as many pack leaves as a
   cohort. Its predicates are then handed to ``canonicalise`` (``ViewPredicate``), resolved with
   the cohorts in the release of the view's cohorts, on the unit table.
2. ``checked``, after phase 1: a view whose cohorts and predicates all canonicalised gets its
   canonical form and ids (``ViewIdentity``): its cohorts in view order, or by computation id
   when it lists none (D284); its reference's position, for an analysis that declares
   ``uses_reference``, the first cohort's by default; ``overlap``, for one that declares
   ``assumes_independent_groups``; its canonical parameters, every default written and every
   predicate as its canonical clause tree; the effective *k* over its cohorts and predicates and
   the floor; and the results versions of the packs of its analysis, cohorts and predicates. A
   view one of whose cohorts or predicates was refused is left out; their refusals say why. A
   view whose cohorts are of more than one release is ``MIXED_RELEASES``, which resolution
   refuses first.

A view's readback is its analysis's, rendered from its canonical form and the release's
descriptors (§7.7); its static caveats are those its result carries that need no data: the
fields its cohorts and predicates read that are not confirmed, a draft release, and what the
packs' caveat rules raised for its cohorts and predicates, which are the canonical parts of a
view a pack can read (D287, D317).
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from pydantic import JsonValue, ValidationError

from aibi.core.analyses.existence import view_readback
from aibi.core.analyses.registry import LATER, Analyses, Registered
from aibi.core.engine.canonical import CanonicalCohort, Canonicalisation, ViewIdentity
from aibi.core.engine.resolve import ViewPredicate
from aibi.core.schema.analyses import ExistenceParams
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
from aibi.core.schema.jsonio import pointer
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
                data("compare.existence"),
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
                text("A pack's analysis is run from M3.2, which gives it the inputs its "),
                text("requirements name; this one is listed, not run: "),
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
        if isinstance(params, ExistenceParams):
            first = view.cohorts[0] if view.cohorts else next(iter(document.cohorts), None)
            reference = _reference_of(document, first)
            predicates = _predicates(params, index, reference, refusals)
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

    @property
    def release(self) -> ReleaseRef:
        return self.cohorts[0].release

    @property
    def disclosure(self) -> int | None:
        return self.identity.disclosure

    def readback(self) -> list[Segment]:
        assert isinstance(self.params, ExistenceParams), "the core's analyses are known"
        return view_readback(len(self.cohorts), self.reference, self.predicates, self.params)

    def static_caveats(self) -> list[Caveat]:
        """The caveats its result carries that need no data (module docstring)."""
        return static_caveats(self.cohorts, self.predicates)


def static_caveats(
    cohorts: Sequence[CanonicalCohort], predicates: Sequence[CanonicalCohort]
) -> list[Caveat]:
    found: list[Caveat] = []
    for group, affects in ((cohorts, "/population"), (predicates, "/values")):
        reads = sorted({read for cohort in group for read in cohort.resolved.unconfirmed})
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
    for view in parsed:
        names = view.names if view.names is not None else tuple(document.cohorts)
        if any(name not in canonical.cohorts for name in names):
            continue
        if any(predicate.key not in canonical.predicates for predicate in view.predicates):
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
        fields = view.analysis.entry.fields
        reference = names.index(view.reference) if view.reference in names else 0
        packs: dict[str, PackVersion] = {}
        for part in (*cohorts, *predicates):
            packs.update(part.packs)
        settings = [part.identity.disclosure for part in (*cohorts, *predicates)]
        given = [k for k in settings if k is not None]
        identity = ViewIdentity(
            analysis=view.analysis.id,
            version=view.analysis.entry.version,
            cohorts=tuple(cohort.identity for cohort in cohorts),
            params=_canonical_params(view.params, predicates),
            packs={pack: version.results_version for pack, version in sorted(packs.items())},
            disclosure=max(given) if given else None,
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
            )
        )
    return found, refusals


def _canonical_params(params: DocModel, predicates: Sequence[CanonicalCohort]) -> JsonValue:
    """A view's canonical parameters: every default written, and each predicate as its
    canonical clause tree (§7.6)."""
    if isinstance(params, ExistenceParams):
        return {
            "level": params.level,
            "predicates": [p.form[p.release.manifest] for p in predicates],
        }
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

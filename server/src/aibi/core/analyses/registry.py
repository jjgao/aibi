"""The analysis registry and applicability (SPEC §9.1, §9.4, §10.1; D316).

The registry holds the core's analyses, each an entry (a descriptor of kind ``analysis``) with its
parameters' model and its implementation, and the analyses of the installed packs, each an entry
and a ``run``. Analyses are reachable only through it: a view names one by id, and no other code
path computes a result. The core's are ``compare.existence`` (D319), ``summary.distribution``
(D328) and ``summary.members`` (D331); the rest of §9.5 follows in the later slices of M3 (D315,
D324).

A pack's analysis is registered, listed and matched for applicability like the core's; the core
runs it from the slice that hands it the inputs its ``requires`` name, materialised (the columns,
aggregates and endpoint rows of §10.1), and discloses its values, M3.2d (``PACK_ANALYSES``). Until
then a view that names one is refused, ``NOT_SUPPORTED`` (D316, D324).

**Applicability** (§9.4) matches an entry's ``requires`` against a release's descriptors, for a
unit table or, with none named, for each keyed table of the release in turn, the best status
kept: a requirement is met by the descriptors of its ``kind`` (endpoints, columns of its
``datatype``, tables other than the unit), on the unit table when it says ``"on": "unit"``, at
least ``min`` of them (1 by default; 0 is always met); a requirement with a ``predicate`` also
needs the predicate of its pack to hold of the release, and a pack that is not installed, a
predicate it does not register or one that raises meets nothing. Requirements without a
``kind`` (the view's ``cohorts``) are the view's to meet. An analysis is ``unavailable`` when a
requirement is not met, naming its roles; ``available_with_caveats`` when a requirement is met,
but not by its ``min`` descriptors without a field whose status is ``imported_default``,
``proposed`` or ``undeclared`` (an endpoint whose event coding was imported by default); and
``available`` otherwise. A requirement predicate reads the release, not the unit table, so it runs
once for all of them.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from aibi.core.analyses import distribution, existence, members
from aibi.core.engine.resolve import UNCONFIRMED, DescriptorCopies, PackView, pack_failed
from aibi.core.schema.analyses import DistributionParams, ExistenceParams, MembersParams
from aibi.core.schema.catalog import ApplicableAnalysis
from aibi.core.schema.descriptors import (
    AnalysisDescriptor,
    ColumnDescriptor,
    DatasetDescriptor,
    Descriptor,
    EndpointDescriptor,
    TableDescriptor,
)
from aibi.core.schema.document import DocModel
from aibi.core.schema.ids import CORE_ANALYSIS_FAMILIES
from aibi.core.schema.pack_api import PackRegistry, UnknownPack


@dataclass(frozen=True)
class CoreAnalysis:
    """One of the core's analyses: its entry and its parameters' model."""

    entry: AnalysisDescriptor
    params: type[DocModel]


CORE: Mapping[str, CoreAnalysis] = {
    existence.ENTRY.id: CoreAnalysis(existence.ENTRY, ExistenceParams),
    distribution.ENTRY.id: CoreAnalysis(distribution.ENTRY, DistributionParams),
    members.ENTRY.id: CoreAnalysis(members.ENTRY, MembersParams),
}
"""The core's analyses, by id."""

LATER: Mapping[str, str] = {
    "compare.columns": "M3.2c",
    "survival.km": "M3.3",
    "survival.cox": "M3.3",
}
"""The core's analyses of §9.5 that a later slice of M3 implements, and the slice (D315, D317,
D324): a view of one is ``NOT_SUPPORTED``, not an unknown analysis."""

PACK_ANALYSES = "M3.2d"
"""The slice that runs packs' analyses (D316, D324)."""


@dataclass(frozen=True)
class Registered:
    """An analysis the registry holds: its entry, the pack that registered it (``None`` for the
    core's) and, for the core's, its parameters' model."""

    entry: AnalysisDescriptor
    pack: str | None
    params: type[DocModel] | None

    @property
    def id(self) -> str:
        return self.entry.id


@dataclass(frozen=True)
class Analyses:
    """The registry over the installed packs."""

    packs: PackRegistry | None = None

    def get(self, analysis_id: str) -> Registered | None:
        """The analysis of that id, or ``None``, a pack that is not installed included."""
        core = CORE.get(analysis_id)
        if core is not None:
            return Registered(core.entry.model_copy(deep=True), None, core.params)
        if self.packs is None or analysis_id.partition(".")[0] in CORE_ANALYSIS_FAMILIES:
            return None
        try:
            found = self.packs.analysis(analysis_id)
        except UnknownPack:
            return None
        if found is None:
            return None
        return Registered(found.entry, analysis_id.partition(".")[0], None)

    def all(self) -> list[Registered]:
        """Every analysis, by id."""
        found = [Registered(a.entry.model_copy(deep=True), None, a.params) for a in CORE.values()]
        if self.packs is not None:
            found += [
                Registered(a.entry, a.entry.id.partition(".")[0], None)
                for a in self.packs.analyses()
            ]
        return sorted(found, key=lambda analysis: analysis.id)

    def ids(self) -> list[str]:
        return [analysis.id for analysis in self.all()]

    def applicable(
        self,
        descriptors: Sequence[Descriptor],
        *,
        dataset: str,
        manifest: str,
        unit: str | None = None,
    ) -> list[ApplicableAnalysis]:
        """Each analysis's applicability to a release (module docstring): for ``unit``, or, with
        none, the best status over the release's keyed tables."""
        view = _view(descriptors, dataset, manifest)
        keyed = [
            descriptor.id
            for descriptor in descriptors
            if isinstance(descriptor, TableDescriptor) and descriptor.fields.primary_key
        ]
        units = [unit] if unit is not None else keyed
        held: dict[str, bool] = {}
        found: list[ApplicableAnalysis] = []
        for analysis in self.all():
            outcomes = [self._matched(analysis, descriptors, view, table, held) for table in units]
            if not outcomes:
                outcomes = [(_UNAVAILABLE, ["unit"], list[str]())]
            found.append(_best(analysis, outcomes))
        return found

    def _matched(
        self,
        analysis: Registered,
        descriptors: Sequence[Descriptor],
        view: PackView,
        unit: str,
        held: dict[str, bool],
    ) -> tuple[int, list[str], list[str]]:
        """The status rank of one analysis for one unit, the roles it misses and those met only
        by unconfirmed descriptors; ``held`` keeps what each requirement predicate gave, which
        reads the release and not the unit, so that it runs once."""
        missing: list[str] = []
        unconfirmed: list[str] = []
        for requirement in analysis.entry.fields.requires:
            if requirement.kind is None and requirement.predicate is None:
                continue
            matches = _matches(
                requirement.kind, requirement.on, requirement.datatype, descriptors, unit
            )
            least = 1 if requirement.min is None else requirement.min
            reference = requirement.predicate
            if reference is not None and reference not in held:
                held[reference] = self._holds(reference, view)
            holds = reference is None or held[reference]
            if (requirement.kind is not None and len(matches) < least) or not holds:
                missing.append(requirement.role)
            elif (
                requirement.kind is not None
                and least
                and (sum(not _unsettled(match) for match in matches) < least)
            ):
                unconfirmed.append(requirement.role)
        rank = _UNAVAILABLE if missing else _CAVEATS if unconfirmed else _AVAILABLE
        return rank, missing, unconfirmed

    def _holds(self, reference: str, view: PackView) -> bool:
        if self.packs is None:
            return False
        try:
            predicate = self.packs.requirement_predicate(reference)
        except UnknownPack:
            return False
        if predicate is None:
            return False
        try:
            return predicate(view) is True
        except MemoryError:
            raise
        except Exception as error:
            pack_failed(reference.partition(".")[0], "requirement predicate", error)
            return False


_AVAILABLE, _CAVEATS, _UNAVAILABLE = 0, 1, 2
_STATUS = {
    _AVAILABLE: "available",
    _CAVEATS: "available_with_caveats",
    _UNAVAILABLE: "unavailable",
}


def _best(
    analysis: Registered, outcomes: Sequence[tuple[int, list[str], list[str]]]
) -> ApplicableAnalysis:
    rank, missing, unconfirmed = min(outcomes, key=lambda outcome: outcome[0])
    return ApplicableAnalysis.model_validate(
        {
            "analysis": analysis.id,
            "version": analysis.entry.version,
            "status": _STATUS[rank],
            "missing": sorted(set(missing)),
            "unconfirmed": sorted(set(unconfirmed)),
        }
    )


def _view(descriptors: Sequence[Descriptor], dataset: str, manifest: str) -> PackView:
    """What a requirement predicate reads of the release: its descriptors, copied (§10.1)."""
    found = next((d for d in descriptors if isinstance(d, DatasetDescriptor)), None)
    packs = () if found is None else tuple(found.fields.packs or ())
    return PackView(dataset, manifest, packs, DescriptorCopies({d.id: d for d in descriptors}))


def _matches(
    kind: str | None,
    on: str | None,
    datatype: str | None,
    descriptors: Sequence[Descriptor],
    unit: str,
) -> list[Descriptor]:
    """The descriptors that meet a requirement of ``kind`` for the unit table ``unit``."""
    found: list[Descriptor] = []
    for descriptor in descriptors:
        if kind == "endpoint" and isinstance(descriptor, EndpointDescriptor):
            if on != "unit" or descriptor.fields.table == unit:
                found.append(descriptor)
        elif kind == "column" and isinstance(descriptor, ColumnDescriptor):
            table = descriptor.id.split(".", 1)[0]
            if (on != "unit" or table == unit) and (
                datatype is None or descriptor.fields.datatype == datatype
            ):
                found.append(descriptor)
        elif (
            kind == "table"
            and isinstance(descriptor, TableDescriptor)
            and descriptor.id != unit
            and descriptor.fields.role != "coverage"
        ):
            found.append(descriptor)
    return found


def _unsettled(descriptor: Descriptor) -> bool:
    """Whether a descriptor has a field whose status is not settled by an operator (§5.1)."""
    return any(entry.status in UNCONFIRMED for entry in descriptor.curation.values())


__all__ = ["CORE", "LATER", "PACK_ANALYSES", "Analyses", "CoreAnalysis", "Registered"]

"""The analysis registry and applicability (SPEC §9.1, §9.4, §10.1; D316).

The registry holds the core's analyses, each an entry (a descriptor of kind ``analysis``) with its
parameters' model and its implementation, and the analyses of the installed packs, each an entry
and a ``run``. Analyses are reachable only through it: a view names one by id, and no other code
path computes a result. The core's are ``compare.existence`` (D319), ``summary.distribution``
(D328), ``summary.members`` (D331), ``compare.columns`` (D336) and ``survival.km`` (D348);
``survival.cox`` follows in M3.3b (D346).

A pack's analysis is registered, listed and matched for applicability like the core's, and run
from M3.2d on the inputs its ``requires`` name, materialised (``analyses.packs``, D341–D343): its
columns and aggregates, and from M3.3 its endpoints' rows (D352). A pack's values are no counts
the disclosure pass can protect, so under any disclosure setting a view of one is refused and it
is ``unavailable``, ``missing`` naming ``min_cell_count`` (D344); so is a survival analysis
(``WITHHELD``, D351).

**Applicability** (§9.4) matches an entry's ``requires`` against a release's descriptors, for a
unit table or, with none named, for each keyed table of the release in turn, the best status
kept: a requirement is met by the descriptors of its ``kind`` (endpoints an analysis can use,
whose table, time, status and event coding are declared, D347; columns of its ``datatype``;
tables other than the unit), on the unit table when it says ``"on": "unit"``, at
least ``min`` of them (1 by default; 0 is always met); a requirement with a ``predicate`` also
needs the predicate of its pack to hold of the release, and a pack that is not installed, a
predicate it does not register or one that raises meets nothing. Requirements without a
``kind`` (the view's ``cohorts``) are the view's to meet. An analysis is ``unavailable`` when a
requirement is not met, naming its roles; ``available_with_caveats`` when a requirement is met,
but not by its ``min`` descriptors without a field whose status is ``imported_default``,
``proposed`` or ``undeclared`` (an endpoint whose event coding was imported by default, or whose
entry is not declared, which its views read as undeclared, D347); and
``available`` otherwise. A requirement predicate reads the release, not the unit table, so it runs
once for all of them. An analysis that lists units' keys (``summary.members``) is ``unavailable``
under any disclosure setting and where the dataset allows no row ids, ``missing`` naming the
setting (``min_cell_count``, ``allow_row_ids``), since every view of it is refused there (D332).
An analysis that shows nothing of numbers but their units under a disclosure setting
(``compare.columns``) is ``unavailable`` there for a unit over which no view could compare
categories: its table has no column of categories (``category``, ``boolean``,
``list<category>``) and is in no relationship, through which a path reaches every other table and
down which (directly, or with a ``via`` back down one it went up) ``some`` and ``every`` make
categories of any column; ``missing`` names ``columns`` and ``min_cell_count`` (D337). A pack's
analysis is ``unavailable`` under any disclosure setting and where the dataset allows no row ids,
as ``summary.members`` is (D344), and wherever it requires an endpoint, or a column of a datatype
no input column is handed (dates and datetimes), naming those roles, since every view of it is
refused (D341).
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from aibi.core.analyses import columns, distribution, existence, members, survival
from aibi.core.engine.resolve import (
    UNCONFIRMED,
    DescriptorCopies,
    PackView,
    pack_failed,
    usable_endpoint,
)
from aibi.core.schema.analyses import (
    ColumnsParams,
    DistributionParams,
    ExistenceParams,
    MembersParams,
    SurvivalParams,
)
from aibi.core.schema.catalog import ApplicableAnalysis
from aibi.core.schema.descriptors import (
    AnalysisDescriptor,
    ColumnDescriptor,
    DatasetDescriptor,
    Descriptor,
    EndpointDescriptor,
    RelationshipDescriptor,
    TableDescriptor,
)
from aibi.core.schema.document import DocModel
from aibi.core.schema.ids import CORE_ANALYSIS_FAMILIES
from aibi.core.schema.jsonschemas import Checker
from aibi.core.schema.pack_api import Analysis, PackRegistry, UnknownPack
from aibi.core.schema.results import PackVersion


@dataclass(frozen=True)
class CoreAnalysis:
    """One of the core's analyses: its entry and its parameters' model."""

    entry: AnalysisDescriptor
    params: type[DocModel]


CORE: Mapping[str, CoreAnalysis] = {
    existence.ENTRY.id: CoreAnalysis(existence.ENTRY, ExistenceParams),
    distribution.ENTRY.id: CoreAnalysis(distribution.ENTRY, DistributionParams),
    members.ENTRY.id: CoreAnalysis(members.ENTRY, MembersParams),
    columns.ENTRY.id: CoreAnalysis(columns.ENTRY, ColumnsParams),
    survival.ENTRY.id: CoreAnalysis(survival.ENTRY, SurvivalParams),
}
"""The core's analyses, by id."""

LATER: Mapping[str, str] = {
    "survival.cox": "M3.3b",
}
"""The core's analyses of §9.5 that a later slice of M3 implements, and the slice (D315, D317,
D324, D346): a view of one is ``NOT_SUPPORTED``, not an unknown analysis."""

LISTS_KEYS = frozenset({members.ANALYSIS_ID})
"""The core's analyses that list units' keys, which are refused under any disclosure setting
and where the dataset allows no row ids (D332), and so are ``unavailable`` there, naming the
setting: ``min_cell_count``, ``allow_row_ids``."""
CATEGORIES_UNDER_K = frozenset({columns.ANALYSIS_ID})
"""The analyses that show only numbers' units under a disclosure setting (D337), and so compare
something there only of a column of categories."""
CATEGORIES = frozenset({"category", "boolean", "list<category>"})

WITHHELD = frozenset({survival.ANALYSIS_ID})
"""The core's analyses refused under any disclosure setting, since what they report can give
counts below *k* that §8.4's rules do not yet protect (D351), and so ``unavailable`` there, naming
``min_cell_count``; a pack's analysis is too (D344)."""


@dataclass(frozen=True)
class Registered:
    """An analysis the registry holds: its entry, the pack that registered it (``None`` for the
    core's) and, for the core's, its parameters' model."""

    entry: AnalysisDescriptor
    pack: str | None
    params: type[DocModel] | None
    version: PackVersion | None = None
    """For a pack's, the pack's version and results version, which its views' ids hash."""

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
        return self._pack_analysis(found)

    def _pack_analysis(self, found: Analysis) -> Registered:
        assert self.packs is not None, "a pack's analysis is of an installed pack"
        entry = found.entry
        manifest = self.packs.pack(entry.id.partition(".")[0]).manifest
        version = PackVersion(version=manifest.version, results_version=manifest.results_version)
        return Registered(entry, manifest.id, None, version)

    def implementation(self, analysis_id: str) -> tuple[Analysis, Checker, Checker]:
        """A registered pack analysis's implementation and the checkers of its entry's
        ``params`` and ``returns`` schemas, as registered (D341, D343)."""
        assert self.packs is not None, "a pack's analysis is of an installed pack"
        found = self.packs.analysis(analysis_id)
        assert found is not None, "the analysis is registered"
        params, returns = self.packs.analysis_checkers(analysis_id)
        return found, params, returns

    def unmet(
        self,
        analysis: Registered,
        descriptors: Sequence[Descriptor],
        *,
        dataset: str,
        manifest: str,
    ) -> list[str]:
        """The roles of a pack analysis's ``predicate`` requirements that do not hold of a
        release, each run as applicability runs it (D341)."""
        view = _view(descriptors, dataset, manifest)
        held: dict[str, bool] = {}
        missing: list[str] = []
        for requirement in analysis.entry.fields.requires:
            reference = requirement.predicate
            if reference is None:
                continue
            if reference not in held:
                held[reference] = self._holds(reference, view)
            if not held[reference]:
                missing.append(requirement.role)
        return missing

    def all(self) -> list[Registered]:
        """Every analysis, by id."""
        found = [Registered(a.entry.model_copy(deep=True), None, a.params) for a in CORE.values()]
        if self.packs is not None:
            found += [self._pack_analysis(analysis) for analysis in self.packs.analyses()]
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
        k: int | None = None,
    ) -> list[ApplicableAnalysis]:
        """Each analysis's applicability to a release (module docstring): for ``unit``, or, with
        none, the best status over the release's keyed tables; ``k`` is the release's effective
        disclosure setting, the floor's included, under which an analysis that lists unit keys
        is ``unavailable`` (``LISTS_KEYS``), and one that shows only numbers' units for a unit
        over which no view compares categories (``CATEGORIES_UNDER_K``), and a pack's analysis
        (D344)."""
        view = _view(descriptors, dataset, manifest)
        withheld = _withheld(descriptors, k)
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
            if withheld and (analysis.id in LISTS_KEYS or analysis.pack is not None):
                outcomes = [
                    (_UNAVAILABLE, [*missing, *withheld], list[str]()) for _, missing, _ in outcomes
                ]
            elif k is not None and analysis.id in WITHHELD:
                outcomes = [
                    (_UNAVAILABLE, [*missing, "min_cell_count"], list[str]())
                    for _, missing, _ in outcomes
                ]
            if k is not None and analysis.id in CATEGORIES_UNDER_K and units:
                outcomes = [
                    outcome
                    if _categories(descriptors, table)
                    else (_UNAVAILABLE, [*outcome[1], "columns", "min_cell_count"], list[str]())
                    for table, outcome in zip(units, outcomes, strict=True)
                ]
            unrun = _unrun(analysis)
            if unrun:
                outcomes = [
                    (_UNAVAILABLE, [*missing, *unrun], list[str]()) for _, missing, _ in outcomes
                ]
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


UNHANDED = ("date", "datetime")
"""The datatypes of the columns a pack's analysis is not handed (D341)."""


def _unrun(analysis: Registered) -> list[str]:
    """The roles of a pack analysis's requirements that no view of it can meet, so that it is
    ``unavailable`` wherever its views are refused (D341): a column of a datatype no input
    column is handed, required at least once (``min`` 1 by default). An endpoint's rows are
    handed from M3.3a (D352)."""
    if analysis.pack is None:
        return []
    return [
        requirement.role
        for requirement in analysis.entry.fields.requires
        if (requirement.min is None or requirement.min > 0)
        and requirement.kind == "column"
        and requirement.datatype in UNHANDED
    ]


def _withheld(descriptors: Sequence[Descriptor], k: int | None) -> list[str]:
    """The disclosure settings under which no unit's key is listed (D332): a ``min_cell_count``
    (the effective *k*, the floor's included) and ``allow_row_ids: false``."""
    found = [] if k is None else ["min_cell_count"]
    dataset = next((d for d in descriptors if isinstance(d, DatasetDescriptor)), None)
    settings = None if dataset is None else dataset.fields.disclosure
    if settings is not None and not settings.allow_row_ids:
        found.append("allow_row_ids")
    return found


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
            if usable_endpoint(descriptor) and (on != "unit" or descriptor.fields.table == unit):
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


def _categories(descriptors: Sequence[Descriptor], unit: str) -> bool:
    """Whether a view of ``unit`` could compare categories (``CATEGORIES_UNDER_K``): its table
    has a column of categories, or is in a relationship (a coverage table is in none, which the
    release's checks refuse), since a path through it reaches every other table's columns, and a
    step down one, directly or with a ``via`` back down the one it went up, makes categories
    (false and true) of any column by ``some`` and ``every`` (§6.1, §9.2)."""
    return any(
        (
            isinstance(descriptor, RelationshipDescriptor)
            and unit in (descriptor.fields.child_table, descriptor.fields.parent_table)
        )
        or (
            isinstance(descriptor, ColumnDescriptor)
            and descriptor.id.split(".", 1)[0] == unit
            and descriptor.fields.datatype in CATEGORIES
        )
        for descriptor in descriptors
    )


def _unsettled(descriptor: Descriptor) -> bool:
    """Whether a descriptor has a field whose status is not settled by an operator (§5.1): one
    curated as unconfirmed, or an endpoint's ``entry`` not declared at all, which every view of
    it reads as ``undeclared`` (§5.8, D347)."""
    if isinstance(descriptor, EndpointDescriptor) and descriptor.fields.entry is None:
        return True
    return any(entry.status in UNCONFIRMED for entry in descriptor.curation.values())


__all__ = [
    "CATEGORIES_UNDER_K",
    "CORE",
    "LATER",
    "LISTS_KEYS",
    "WITHHELD",
    "Analyses",
    "CoreAnalysis",
    "Registered",
]

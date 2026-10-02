"""The analysis registry and applicability (SPEC §9.1, §9.4, §10.1; D316).

The registry holds the core's analyses, each an entry (a descriptor of kind ``analysis``) with its
parameters' model and its implementation, and the analyses of the installed packs, each an entry
and a ``run``. Analyses are reachable only through it: a view names one by id, and no other code
path computes a result. The core's are ``compare.existence`` (D319), ``summary.distribution``
(D328), ``summary.members`` (D331), ``compare.columns`` (D336), ``survival.km`` (D348) and
``survival.cox`` (D366).

A pack's analysis is registered, listed and matched for applicability like the core's, and run
from M3.2d on the inputs its ``requires`` name, materialised (``analyses.packs``, D341–D343): its
columns and aggregates, and from M3.3 its endpoints' rows (D352).

**Disclosure classes** (§8.4, D353). Every analysis is ``disclosed`` or ``refused``, as the core
states it (``CoreAnalysis.disclosure``; every pack's analysis is ``refused``, ``disclosure_of``):
a ``disclosed`` one runs under a disclosure setting and the pass applies to what it shows; a
``refused`` one's output is nothing the pass can protect (a list of units, D332; what a pack
computes, D344; survival curves and Cox models, D351), so under any setting a view of it is
refused in phase 2 and it is ``unavailable``, ``missing`` naming ``min_cell_count``. An analysis
that lists or hands each member's values in the keys' order (``lists_keys``: ``summary.members``
and every pack's) is also refused, and ``unavailable`` naming ``allow_row_ids``, where the dataset
allows no row ids.

A ``disclosed`` analysis may also **withhold a variable form** under any setting
(``CoreAnalysis.withheld_forms``, D379): a member of its parameters, by its path below them, whose
presence the pass cannot protect (``summary.distribution``'s ``count: "rows"``, a sum of each
unit's number of rows, a statistic of values D329 withholds). Under any setting a view that gives
one is refused in phase 2 at that member, before anything else phase 2 checks of its variables
(``withheld_form``), and the analysis stays ``available``: its other views run. Its memberships
(``each``) are disclosed by D383, but where the descriptors bound the rows each unit reaches,
which they decide rather than a member's path, and which phase 2 withholds by the analysis's own
check (``distribution.withheld_under_k``) in the same way.

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
once for all of them. A ``refused`` analysis is ``unavailable`` under any disclosure setting, and
one that ``lists_keys`` where the dataset allows no row ids, ``missing`` naming the setting
(``min_cell_count``, ``allow_row_ids``), since every view of it is refused there (D353). An
analysis that shows nothing of numbers but their units under a disclosure setting
(``compare.columns``) is ``unavailable`` there for a unit over which no view could compare
categories: its table has no column of categories (``category``, ``boolean``,
``list<category>``) and is in no relationship, through which a path reaches every other table and
down which (directly, or with a ``via`` back down one it went up) ``some`` and ``every`` make
categories of any column; ``missing`` names ``columns`` and ``min_cell_count`` (D337). A pack's
analysis is also ``unavailable`` wherever it requires a column of a datatype no input column is
handed (dates and datetimes), naming those roles, since every view of it is refused (D341).
"""

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from types import UnionType
from typing import Annotated, Union, cast, get_args, get_origin

from pydantic import BaseModel

from aibi.core.analyses import columns, cox, distribution, existence, members, survival
from aibi.core.engine.resolve import UNCONFIRMED, Operation, usable_endpoint
from aibi.core.schema.analyses import (
    ColumnsParams,
    ColumnsValues,
    CoxParams,
    CoxValues,
    DistributionParams,
    DistributionValues,
    ExistenceParams,
    ExistenceValues,
    MembersParams,
    MembersValues,
    SurvivalParams,
    SurvivalValues,
)
from aibi.core.schema.catalog import ApplicableAnalysis
from aibi.core.schema.copiers import is_true
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
from aibi.core.schema.guards import PackFailed
from aibi.core.schema.ids import CORE_ANALYSIS_FAMILIES
from aibi.core.schema.jsonschemas import Checker
from aibi.core.schema.output import Output
from aibi.core.schema.pack_api import PackRegistry, RegisteredAnalysis, UnknownPack
from aibi.core.schema.results import PackVersion


class DisclosureClass(StrEnum):
    """An analysis's disclosure class (§8.4, D353)."""

    DISCLOSED = "disclosed"
    """It runs under a disclosure setting, and the pass protects what it shows by the rule it
    cites."""
    REFUSED = "refused"
    """Its output is nothing the pass can protect, so a view of it is refused under any
    setting."""


@dataclass(frozen=True)
class CoreAnalysis:
    """One of the core's analyses: its entry, its parameters' model, its values' model (which
    reads its digested values back, D374), its disclosure class and whether it lists or hands
    each member's values in the keys' order (D353); ``because`` says why, for a ``refused`` one,
    in a refusal's words; ``withheld_forms``, for a ``disclosed`` one, the members of its
    parameters it withholds under any setting, each by its path below them (``/``-separated
    member names, ``*`` for any item of a list) with why, in a refusal's words (D379)."""

    entry: AnalysisDescriptor
    params: type[DocModel]
    values: type[Output]
    disclosure: DisclosureClass
    lists_keys: bool = False
    because: str | None = None
    withheld_forms: Mapping[str, str] = field(default_factory=dict[str, str])

    def __post_init__(self) -> None:
        if (self.disclosure is DisclosureClass.REFUSED) != bool(
            self.because and self.because.strip()
        ):
            raise ValueError("a refused analysis says why, and only a refused one")
        if self.lists_keys and self.disclosure is not DisclosureClass.REFUSED:
            raise ValueError("an analysis that lists keys is refused under k (D332)")
        if self.withheld_forms and self.disclosure is not DisclosureClass.DISCLOSED:
            raise ValueError("only a disclosed analysis withholds a form, a refused one all (D379)")
        for form, why in self.withheld_forms.items():
            if not why.strip() or not all(form.split("/")):
                raise ValueError("a withheld form is a path below the parameters, and says why")
            _member_at(self.params, form)


def _member_at(params: type[BaseModel], form: str) -> None:
    """Refuses a withheld form (D379) whose path does not lie below ``params``: each name a
    member of the model it is below, and each ``*`` an item of a list, so that a misspelt form
    cannot stop withholding what it names."""
    below: object = params
    for segment in form.split("/"):
        below = _unwrapped(below)
        if segment == "*":
            if get_origin(below) is not list:
                raise ValueError(f"a withheld form's * is an item of a list: {form}")
            below = get_args(below)[0]
        elif isinstance(below, type) and issubclass(below, BaseModel):
            found = below.model_fields.get(segment)
            if found is None:
                raise ValueError(f"a withheld form names a member of its model: {form}")
            below = found.annotation
        else:
            raise ValueError(f"a withheld form names a member of its model: {form}")


def _unwrapped(annotation: object) -> object:
    """An annotation without its metadata (``Annotated``) and without ``None`` as an option."""
    while True:
        origin = get_origin(annotation)
        if origin is Annotated:
            annotation = get_args(annotation)[0]
        elif origin in (Union, UnionType):
            options = [one for one in get_args(annotation) if one is not type(None)]
            if len(options) != 1:
                return annotation
            annotation = options[0]
        else:
            return annotation


CORE: Mapping[str, CoreAnalysis] = {
    existence.ENTRY.id: CoreAnalysis(
        existence.ENTRY, ExistenceParams, ExistenceValues, DisclosureClass.DISCLOSED
    ),
    distribution.ENTRY.id: CoreAnalysis(
        distribution.ENTRY,
        DistributionParams,
        DistributionValues,
        DisclosureClass.DISCLOSED,
        withheld_forms={
            "columns/*/count": "a column's rows total each unit's number of rows, a statistic of "
            "values that the pass withholds, and one unit's rows can be k or more (D329)",
        },
    ),
    members.ENTRY.id: CoreAnalysis(
        members.ENTRY,
        MembersParams,
        MembersValues,
        DisclosureClass.REFUSED,
        lists_keys=True,
        because="a key is held by one unit, and lists give by difference the units of a set of "
        "any size (D332)",
    ),
    columns.ENTRY.id: CoreAnalysis(
        columns.ENTRY, ColumnsParams, ColumnsValues, DisclosureClass.DISCLOSED
    ),
    survival.ENTRY.id: CoreAnalysis(
        survival.ENTRY,
        SurvivalParams,
        SurvivalValues,
        DisclosureClass.REFUSED,
        because="a curve's values can give the censorings between its event times, which no "
        "rule yet protects (D351)",
    ),
    cox.ENTRY.id: CoreAnalysis(
        cox.ENTRY,
        CoxParams,
        CoxValues,
        DisclosureClass.REFUSED,
        because="a Cox model's hazard ratios and tests are functions of the risk sets at every "
        "event time, as a curve's values are, which no rule yet protects (D351)",
    ),
}
"""The core's analyses, by id: ``compare.existence`` is disclosed by D320,
``summary.distribution`` by D329 and ``compare.columns`` by D337."""

PACK_BECAUSE = (
    "what a pack computes from every member's values is nothing the pass can read, so no rule "
    "over counts protects it (D344)"
)
"""Why a pack's analysis is refused under a disclosure setting."""

LATER: Mapping[str, str] = {}
"""The core's analyses of §9.5 that a later slice implements, and the slice (D315, D317, D324,
D346): a view of one is ``NOT_SUPPORTED``, not an unknown analysis. Since M3.3e-2 none is
(D366)."""

CATEGORIES_UNDER_K = frozenset({columns.ANALYSIS_ID})
"""The analyses that show only numbers' units under a disclosure setting (D337), and so compare
something there only of a column of categories."""
CATEGORIES = frozenset({"category", "boolean", "list<category>"})

REFUSED = frozenset(
    name for name, analysis in CORE.items() if analysis.disclosure is DisclosureClass.REFUSED
)
"""The core's ``refused`` analyses (D353)."""
DISCLOSED = frozenset(CORE) - REFUSED
"""The core's ``disclosed`` analyses, which a refusal under a disclosure setting offers."""


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


def disclosure_of(analysis: Registered) -> tuple[DisclosureClass, bool, str]:
    """An analysis's disclosure class, whether it lists or hands each member's values in the
    keys' order, and why it is refused (D353): the core states its own; every pack's analysis is
    ``refused`` and hands its members' values, whatever its entry says, since a pack's values are
    nothing the core can read (D344)."""
    core = None if analysis.pack is not None else CORE.get(analysis.id)
    if core is None:
        return DisclosureClass.REFUSED, True, PACK_BECAUSE
    return core.disclosure, core.lists_keys, core.because or ""


def withheld(analysis: Registered, k: int | None) -> bool:
    """Whether a view of ``analysis`` under the effective disclosure setting ``k`` is refused
    (D353), which phase 2 refuses and each place that runs one asserts."""
    return k is not None and disclosure_of(analysis)[0] is DisclosureClass.REFUSED


def withheld_form(
    analysis: Registered, params: DocModel, k: int | None
) -> tuple[tuple[str | int, ...], str] | None:
    """The first member of a view's parameters, in their order (``_given``), that ``analysis``
    withholds under the effective disclosure setting ``k`` (D379), its place below ``params``
    and why, or ``None``: phase 2 refuses it, and each place that runs a view asserts none."""
    core = None if analysis.pack is not None else CORE.get(analysis.id)
    if k is None or core is None or not core.withheld_forms:
        return None
    forms = {tuple(form.split("/")): why for form, why in core.withheld_forms.items()}
    below = {form[:end] for form in forms for end in range(1, len(form))}
    for place in _given(params, (), below):
        why = forms.get(_form(place))
        if why is not None:
            return place, why
    return None


def _form(place: tuple[str | int, ...]) -> tuple[str, ...]:
    return tuple("*" if isinstance(segment, int) else segment for segment in place)


def _given(
    value: object, at: tuple[str | int, ...], below: set[tuple[str, ...]]
) -> Iterator[tuple[str | int, ...]]:
    """Each place below ``value`` that gives a member, in the parameters' order (a model's
    members as it declares them, a list's items in turn, a member before those below it),
    descending only where ``below`` holds a form's path so far."""
    if value is None:
        return
    if at:
        yield at
        if _form(at) not in below:
            return
    if isinstance(value, BaseModel):
        for name in type(value).model_fields:
            yield from _given(getattr(value, name), (*at, name), below)
    elif isinstance(value, list):
        for index, item in enumerate(cast(list[object], value)):
            yield from _given(item, (*at, index), below)


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

    def _pack_analysis(self, found: RegisteredAnalysis) -> Registered:
        assert self.packs is not None, "a pack's analysis is of an installed pack"
        entry = found.entry
        manifest = self.packs.pack(entry.id.partition(".")[0]).manifest
        version = PackVersion(version=manifest.version, results_version=manifest.results_version)
        return Registered(entry, manifest.id, None, version)

    def implementation(self, analysis_id: str) -> tuple[RegisteredAnalysis, Checker, Checker]:
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
        operation: Operation | None = None,
    ) -> list[str]:
        """The roles of a pack analysis's ``predicate`` requirements that do not hold of a
        release, each run as applicability runs it (D341), on its pack's view of the release in
        ``operation`` (D403)."""
        views = Operation() if operation is None else operation
        release = _Release(dataset, manifest, descriptors)
        held: dict[str, bool] = {}
        missing: list[str] = []
        for requirement in analysis.entry.fields.requires:
            reference = requirement.predicate
            if reference is None:
                continue
            if reference not in held:
                held[reference] = self._holds(reference, views, release)
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
        operation: Operation | None = None,
    ) -> list[ApplicableAnalysis]:
        """Each analysis's applicability to a release (module docstring): for ``unit``, or, with
        none, the best status over the release's keyed tables; ``k`` is the release's effective
        disclosure setting, the floor's included, under which a ``refused`` analysis is
        ``unavailable`` (D353), and one that shows only numbers' units for a unit over which no
        view compares categories (``CATEGORIES_UNDER_K``, D337). Each requirement predicate runs
        once, on its pack's view of the release in ``operation`` (D403)."""
        views = Operation() if operation is None else operation
        release = _Release(dataset, manifest, descriptors)
        rowless = _rowless(descriptors)
        keyed = [
            descriptor.id
            for descriptor in descriptors
            if isinstance(descriptor, TableDescriptor) and descriptor.fields.primary_key
        ]
        units = [unit] if unit is not None else keyed
        held: dict[str, bool] = {}
        found: list[ApplicableAnalysis] = []
        for analysis in self.all():
            outcomes = [self._matched(analysis, release, views, table, held) for table in units]
            if not outcomes:
                outcomes = [(_UNAVAILABLE, ["unit"], list[str]())]
            refused = _refused(analysis, k, rowless)
            if refused:
                outcomes = [
                    (_UNAVAILABLE, [*missing, *refused], list[str]()) for _, missing, _ in outcomes
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
        release: "_Release",
        views: Operation,
        unit: str,
        held: dict[str, bool],
    ) -> tuple[int, list[str], list[str]]:
        """The status rank of one analysis for one unit, the roles it misses and those met only
        by unconfirmed descriptors; ``held`` keeps what each requirement predicate gave, which
        reads the release and not the unit, so that it runs once."""
        descriptors = release.descriptors
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
                held[reference] = self._holds(reference, views, release)
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

    def _holds(self, reference: str, views: Operation, release: "_Release") -> bool:
        """Whether a requirement predicate holds of the release: called through its handle's
        guard on its pack's view of the release (D403); one that fails does not hold."""
        if self.packs is None:
            return False
        try:
            predicate = self.packs.requirement_predicate(reference)
        except UnknownPack:
            return False
        if predicate is None:
            return False
        view = views.unlabelled(
            predicate.pack, release.dataset, release.manifest, release.descriptors
        )
        try:
            return predicate.call(lambda h: is_true(h, view))
        except PackFailed:
            return False


@dataclass(frozen=True)
class _Release:
    """The release applicability reads: its dataset, manifest and descriptors."""

    dataset: str
    manifest: str
    descriptors: Sequence[Descriptor]


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


def _rowless(descriptors: Sequence[Descriptor]) -> bool:
    """Whether the release's dataset allows no row ids (``allow_row_ids: false``, §8.4)."""
    dataset = next((d for d in descriptors if isinstance(d, DatasetDescriptor)), None)
    settings = None if dataset is None else dataset.fields.disclosure
    return settings is not None and not settings.allow_row_ids


def _refused(analysis: Registered, k: int | None, rowless: bool) -> list[str]:
    """The settings that refuse every view of ``analysis`` (D353), as applicability's
    ``missing`` names them: ``min_cell_count`` for a ``refused`` one under the effective *k*,
    and ``allow_row_ids`` for one that lists keys where the dataset allows no row ids."""
    disclosure, lists_keys, _ = disclosure_of(analysis)
    found = ["min_cell_count"] if k is not None and disclosure is DisclosureClass.REFUSED else []
    if lists_keys and rowless:
        found.append("allow_row_ids")
    return found


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
    "DISCLOSED",
    "LATER",
    "PACK_BECAUSE",
    "REFUSED",
    "Analyses",
    "CoreAnalysis",
    "DisclosureClass",
    "Registered",
    "disclosure_of",
    "withheld",
    "withheld_form",
]

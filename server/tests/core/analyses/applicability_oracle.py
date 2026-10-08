"""Applicability's oracle and the releases and registries it is compared over (SPEC §9.4, D420).

``Scan`` is the registry as it was before applicability was indexed: its ``applicable`` and
``_matched``, and the functions ``_matches``, ``_categories`` and ``_unsettled``, kept here
verbatim, a brute force that scans every descriptor for each analysis, requirement and unit. The
indexed registry must give exactly what it gives, and call the packs' requirement predicates in
the same order (``outcome``), for every release and registry ``releases`` and ``registries``
draw: tables keyed and not (none keyed, none at all, a table given twice, keyed coverage tables),
columns of every datatype and undeclared, endpoints usable and not, on tables that exist and not,
statuses on every field, relationships, disclosure settings with row ids allowed and not; and
packs whose requirements are of every kind, ``on`` and datatype (``on`` and ``datatype`` on
endpoint and table requirements too), of ``min`` none to 4, 63 to 72 and 2^53-1 (past the width
of a byte, a word and a float's integers) and, in a third of them, one of the counts of the
release, less one, as it is or plus one (``Counts``, ``least_of``); without a kind, with only a
predicate, bare, with a predicate that holds, does not, raises, is not registered or is of a
pack that is not; entries given twice under two roles, in an order no sort would keep, and
analyses without a requirement. Releases of none to 20 tables (``releases``), tables of every
role and none; and graded ones (``graded_release``, ``wide_release``, ``wide_registry``) of 9,
17, 33, 63, 64, 65, 127, 129 or 200 keyed units, whose tables are poor before a cut and rich
after it, three of them of 60 to 70 columns: the unit that decides is past a byte, a word, two,
and the bitsets of the units are wider than that. No domain: tables, columns and roles are
letters and numbers. Besides them, drawn by seed and not by hypothesis, ``edge_release`` and
``scale_release``: the deciding unit at each edge of a width, and many tables for the index's
per-table counts.
"""

import random
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from functools import cache
from typing import Any, NamedTuple

from hypothesis import strategies as st
from pydantic import JsonValue

import aibi
from aibi.core.analyses.registry import (
    _AVAILABLE,
    _CAVEATS,
    _UNAVAILABLE,
    CATEGORIES,
    CATEGORIES_UNDER_K,
    Analyses,
    Registered,
    _best,
    _refused,
    _Release,
    _rowless,
    _unrun,
)
from aibi.core.engine import build
from aibi.core.engine.resolve import UNCONFIRMED, Operation, usable_endpoint
from aibi.core.schema.catalog import ApplicableAnalysis
from aibi.core.schema.descriptors import (
    AnalysisDescriptor,
    ColumnDescriptor,
    Descriptor,
    EndpointDescriptor,
    RelationshipDescriptor,
    TableDescriptor,
)
from aibi.core.schema.pack_api import AnalysisInputs, Pack, PackManifest, PackRegistry


class Scan(Analyses):
    """The registry with the scan for applicability, verbatim (the base of D420)."""

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


_CORE = Analyses().all()


def _listed(analyses: Analyses) -> list[Registered]:
    """What ``Analyses.all`` gives, but with its entries copied once for each registry and not
    on every call (the copies are not applicability's, D420), so that a property test runs
    thousands of examples in seconds."""
    return list(_CORE) if analyses.packs is None else _packed(analyses.packs)


@cache
def _packed(packs: PackRegistry) -> list[Registered]:
    analyses = Analyses(packs)
    found = [*_CORE, *(analyses._pack_analysis(one) for one in packs.analyses())]
    return sorted(found, key=lambda analysis: analysis.id)


class Quick(Analyses):
    """The registry, its core entries copied once (``_listed``)."""

    def all(self) -> list[Registered]:
        return _listed(self)


class QuickScan(Scan):
    """The scan, its core entries copied once (``_listed``)."""

    def all(self) -> list[Registered]:
        return _listed(self)


# --- The scan's judgements, for the tests of the index's parts ---


def brute_signature(
    descriptors: Sequence[Descriptor],
    units: Sequence[str],
    kind: str,
    on: str | None,
    datatype: str | None,
    least: int,
) -> tuple[int, int]:
    """The units at which the scan finds a requirement not met, and those at which it finds it
    met but unconfirmed, as bitsets over ``units``."""
    missed = unconfirmed = 0
    for position, unit in enumerate(units):
        found = _matches(kind, on, datatype, descriptors, unit)
        if len(found) < least:
            missed |= 1 << position
        elif least and sum(not _unsettled(one) for one in found) < least:
            unconfirmed |= 1 << position
    return missed, unconfirmed


def brute_count(
    descriptors: Sequence[Descriptor], kind: str, on: str | None, datatype: str | None, unit: str
) -> tuple[int, int]:
    USED["brute_count"] += 1
    found = _matches(kind, on, datatype, descriptors, unit)
    return len(found), sum(not _unsettled(one) for one in found)


def first_best(missed: Sequence[bool], unconfirmed: Sequence[bool]) -> int:
    """Where the scan judges an analysis whose requirements are missed (anywhere) and met but
    unconfirmed (at the units that are not): the first unit met by settled descriptors, else the
    first met, else the first."""
    USED["first_best"] += 1
    met = [position for position, one in enumerate(missed) if not one]
    settled = [position for position in met if not unconfirmed[position]]
    return settled[0] if settled else met[0] if met else 0


def scan_choice(
    registry: PackRegistry | None,
    descriptors: Sequence[Descriptor],
    units: Sequence[str],
    k: int | None,
    analysis: Registered,
    held: dict[str, bool],
) -> int:
    """Where the scan judges ``analysis`` among ``units`` (at least one): the first of the
    best outcomes, refused and unrun ones and those without categories under *k* unavailable."""
    scan = Scan(registry)
    release = _Release("d", "m", descriptors)
    rowless = _rowless(descriptors)
    outcomes = [scan._matched(analysis, release, Operation(), unit, held) for unit in units]
    if _refused(analysis, k, rowless) or _unrun(analysis):
        outcomes = [(_UNAVAILABLE, [], []) for _ in outcomes]
    if k is not None and analysis.id in CATEGORIES_UNDER_K:
        outcomes = [
            outcome if _categories(descriptors, unit) else (_UNAVAILABLE, [], [])
            for unit, outcome in zip(units, outcomes, strict=True)
        ]
    ranks = [rank for rank, _, _ in outcomes]
    return ranks.index(min(ranks))


# --- Releases and registries ---

DATATYPES = (
    "integer",
    "number",
    "string",
    "boolean",
    "category",
    "list<category>",
    "date",
    "datetime",
    "time_offset",
)
STATUSES = ("asserted", "imported", "imported_default", "proposed")
CALLS: list[str] = []
"""The requirement predicates called, in order, by name."""
USED: Counter[str] = Counter()
"""How many times each oracle function ran (``first_best``, ``brute_count``, ``is_the_scan``), so
that a test can tell that its comparison went through the oracle and not around it."""

_Fields = tuple[tuple[str, Any], ...]


@cache
def _built(kind: str, id: str, fields: _Fields, status: str, statuses: _Fields) -> Descriptor:
    """A descriptor, built once for each way it is drawn (descriptors are never changed)."""
    given = {name: _thawed(value) for name, value in fields}
    return build.descriptor(kind, id, given, status=status, statuses=dict(statuses))


def _thawed(value: Any) -> Any:
    if isinstance(value, tuple) and value and value[0] == "{}":
        return {name: _thawed(one) for name, one in value[1:]}
    if isinstance(value, tuple):
        return [_thawed(one) for one in value]
    return value


@cache
def _column(id: str, datatype: str | None, status: str, fields: str) -> Descriptor:
    """A column whose label has ``status`` and whose fields have ``fields``."""
    given: dict[str, Any] = {}
    if datatype is not None:
        given["datatype"] = datatype
    if datatype in ("category", "list<category>"):
        given["permissible_values"] = {"values": [{"value": "a"}]}
    if datatype == "list<category>":
        given["list_syntax"] = {"format": "delimited", "delimiter": ";"}
    statuses = dict.fromkeys(given, fields)
    return build.descriptor("column", id, given, status=status, statuses=statuses)


@cache
def _relationship(child: str, parent: str, role: str) -> Descriptor:
    return build.relationship(child, ["c0"], parent, role=role)


@cache
def _keyed(table: str) -> Descriptor:
    return build.table(table, ["c0"])


_COLUMNS = [
    (datatype, status, fields)
    for datatype in (None, *DATATYPES)
    for status in STATUSES
    for fields in STATUSES
]
ROLES = (None, "entity", "link", "measurement", "event", "coverage")
_TABLES = [
    (role, keyed, status, fields)
    for role in ROLES
    for keyed in (False, True)
    for status in STATUSES
    for fields in STATUSES
]
_ENDPOINT_FIELDS = (
    ("time_column", "c1"),
    ("status_column", "c2"),
    ("event_coding", ("{}", ("event", ("a",)), ("censored", ("b",)))),
    ("entry", "at_origin"),
)


def _digits(number: int, base: int, count: int) -> list[int]:
    """``number``'s first ``count`` digits in ``base``, least significant first: one draw
    decoded into several choices, which keeps a property test's draws few."""
    found: list[int] = []
    for _ in range(count):
        number, digit = divmod(number, base)
        found.append(digit)
    return found


_DISCLOSURES: list[tuple[Any, ...]] = [
    (),
    ("{}", ("min_cell_count", 3)),
    ("{}", ("allow_row_ids", True)),
    ("{}", ("min_cell_count", 3), ("allow_row_ids", False)),
]


_TABLE_COLUMNS = st.lists(st.sampled_from(_COLUMNS), max_size=4)


def _release_of(tables: int) -> st.SearchStrategy[tuple[Any, ...]]:
    """What a release of ``tables`` tables is drawn as, as plain values: its disclosure; each
    table's kind, columns and endpoints (each endpoint's table by position, ``tables`` for one
    that does not exist and ``tables + 1`` for none, its fields present, their statuses and its
    status); relationships by position; and a table given twice."""
    endpoint = st.tuples(
        st.integers(0, tables + 1),
        st.integers(0, 2**5 - 1),
        st.integers(0, 5**5 - 1),
        st.sampled_from(STATUSES),
    )
    table = st.tuples(
        st.sampled_from(_TABLES),
        _TABLE_COLUMNS,
        st.lists(endpoint, max_size=2),
    )
    position = st.integers(0, max(tables - 1, 0))
    return st.tuples(
        st.sampled_from(_DISCLOSURES),
        st.lists(table, min_size=tables, max_size=tables),
        st.lists(st.tuples(position, position), max_size=min(tables - 1, 4) if tables > 1 else 0),
        st.tuples(st.booleans(), position) if tables else st.just((False, 0)),
    )


_TABLE_COUNTS = (0, 1, 2, 3, 4, 5, 6, 9, 10, 12, 16, 20)
_RELEASES = {tables: _release_of(tables) for tables in _TABLE_COUNTS}
UNITS = (9, 17, 33, 63, 64, 65, 127, 129, 200)
"""The numbers of keyed units a graded release has, on both sides of 8, 16, 32, 64 and 128: the
widths at which a bitset gains a byte, and a word is full."""
BIG = 16
"""The share (in a hundred) of the releases drawn by ``releases`` that are graded."""


@st.composite
def releases(draw: st.DrawFn, big: int = BIG) -> tuple[list[Descriptor], list[str]]:
    """A release's descriptors and its tables' names: in most draws ``t0``…, none to 20, and in
    ``big`` in a hundred a graded one (``graded_release``) of ``UNITS`` keyed units."""
    if draw(st.integers(0, 99)) < big:
        chosen = random.Random(draw(st.integers(0, 2**32 - 1)))
        return graded_release(chosen, chosen.choice(UNITS))
    disclosure, tables, related, twice = draw(_RELEASES[draw(st.sampled_from(_TABLE_COUNTS))])
    names = [f"t{position}" for position in range(len(tables))]
    fixture: _Fields = (("name", "Fixture"),) + (
        (("disclosure", disclosure),) if disclosure else ()
    )
    found: list[Descriptor] = [_built("dataset", "dataset", fixture, "asserted", ())]
    for name, ((role, keyed, status, kept), columns, endpoints) in zip(names, tables, strict=True):
        given: _Fields = (() if role is None else (("role", role),)) + (
            (("primary_key", ("c0",)),) if keyed else ()
        )
        found.append(_built("table", name, given, status, tuple((one, kept) for one, _ in given)))
        for column, (datatype, status, kept) in enumerate(columns):
            found.append(_column(f"{name}.c{column}", datatype, status, kept))
        for number, (at, present, statuses, status) in enumerate(endpoints):
            table = [*names, "ghost", None][at]
            # each of the time and status columns is missing once in 8, the event coding and
            # the entry half the time
            lacking, coded, entered = present % 8, present // 8 % 2, present // 16
            fields: _Fields = (
                (() if table is None else (("table", table),))
                + (() if lacking == 0 else (_ENDPOINT_FIELDS[0],))
                + (() if lacking == 1 else (_ENDPOINT_FIELDS[1],))
                + ((_ENDPOINT_FIELDS[2],) if coded else ())
                + ((_ENDPOINT_FIELDS[3],) if entered else ())
            )
            per_field: _Fields = tuple(
                (field, STATUSES[digit - 1])
                for (field, _), digit in zip(fields, _digits(statuses, 5, 5), strict=False)
                if digit
            )
            found.append(_built("endpoint", f"ep:{name}e{number}", fields, status, per_field))
    for child, parent in related:
        if child != parent:
            found.append(_relationship(names[child], names[parent], f"x{len(found)}"))
    if twice[0]:
        found.append(_keyed(names[twice[1]]))
    return found, names


def entry(pack: str, number: int, requires: list[dict[str, Any]]) -> AnalysisDescriptor:
    """A pack analysis's entry with these requirements."""
    fields = {
        "requires": requires,
        "params": {},
        "returns": {},
        "methods": {},
        "assumptions": [],
        "uses_reference": False,
        "assumes_independent_groups": True,
        "cross_dataset": None,
        "caveats": [],
    }
    return AnalysisDescriptor.model_validate(
        {
            "kind": "analysis",
            "id": f"{pack}.a{number:03d}",
            "version": "1.0.0",
            "label": "A",
            "fields": fields,
        }
    )


class Implementation:
    """A pack analysis that applicability never runs."""

    def __init__(self, entry: AnalysisDescriptor) -> None:
        self._entry = entry

    @property
    def entry(self) -> AnalysisDescriptor:
        return self._entry

    def run(self, inputs: AnalysisInputs) -> Mapping[str, JsonValue]:
        raise AssertionError("applicability runs no analysis")


def predicate(name: str, value: bool | None) -> Callable[[object], bool]:
    """A requirement predicate that records its call and gives ``value``, or raises."""

    def holds(release: object) -> bool:
        CALLS.append(name)
        if value is None:
            raise RuntimeError("fails")
        return value

    return holds


def manifest(pack: str) -> PackManifest:
    return PackManifest.model_validate(
        {"id": pack, "version": "1.0.0", "results_version": 1, "requires_core": ">=0"}
    )


def registry_of(
    analyses: Mapping[str, Sequence[Sequence[Mapping[str, Any]]]],
    predicates: Mapping[str, Mapping[str, Callable[[object], bool]]] | None = None,
) -> PackRegistry:
    """Packs by id, each analysis given by its requirements, and each pack's requirement
    predicates by name."""
    return PackRegistry(
        [
            Pack(
                manifest=manifest(pack),
                analyses=[
                    Implementation(entry(pack, number, [dict(one) for one in requires]))
                    for number, requires in enumerate(given)
                ],
                requirement_predicates=dict((predicates or {}).get(pack, {})),
            )
            for pack, given in analyses.items()
        ],
        core_version=aibi.__version__,
    )


_Spec = tuple[tuple[str, tuple[tuple[_Fields, ...], ...]], ...]


@cache
def _registry(spec: _Spec) -> PackRegistry:
    """A registry built once for each way it is drawn: its predicates hold (``yes``), do not
    (``no``) and raise (``boom``)."""
    return registry_of(
        {pack: [[dict(one) for one in requires] for requires in given] for pack, given in spec},
        {
            pack: {
                "yes": predicate(f"{pack}.yes", True),
                "no": predicate(f"{pack}.no", False),
                "boom": predicate(f"{pack}.boom", None),
            }
            for pack, _ in spec
        },
    )


_KINDS = ("endpoint", "column", "table", None)
_ON = (None, "unit")
_DATATYPES = (None, *DATATYPES)
_MINS = (None, 0, 1, 2, 3, 4, 63, 64, 65, 72, 2**53 - 1)
_PREDICATES = (None, None, "yes", "no", "boom", "ghost", "p0.yes", "zz.yes")
_REQUIREMENTS = len(_KINDS) * len(_ON) * len(_DATATYPES) * len(_MINS) * len(_PREDICATES)


class Counts:
    """What the scan counts in a release, to draw a ``min`` from: the columns of each table and
    of the whole release, of each datatype or any, the usable endpoints of each table and in
    all, and the tables other than coverage tables."""

    def __init__(self, descriptors: Sequence[Descriptor]) -> None:
        self.columns: Counter[tuple[str | None, str | None]] = Counter()
        self.endpoints: Counter[str | None] = Counter()
        self.tables: set[str] = set()
        self.others = 0
        for descriptor in descriptors:
            if isinstance(descriptor, ColumnDescriptor):
                table = descriptor.id.split(".", 1)[0]
                for key in (table, None):
                    self.columns[(key, None)] += 1
                    if descriptor.fields.datatype is not None:
                        self.columns[(key, descriptor.fields.datatype)] += 1
            elif isinstance(descriptor, EndpointDescriptor):
                if usable_endpoint(descriptor):
                    self.endpoints[descriptor.fields.table] += 1
                    self.endpoints[None] += 1
            elif isinstance(descriptor, TableDescriptor):
                self.tables.add(descriptor.id)
                if descriptor.fields.role != "coverage":
                    self.others += 1

    def of(self, kind: str | None, on: str | None, datatype: str | None) -> list[int]:
        """The counts the scan finds for a requirement of this kind, ``on`` and datatype, at
        each table (``"on": "unit"``) or in all: the values a ``min`` is met or just missed at
        (``least_of``)."""
        units = on == "unit"
        if kind == "column":
            keys = [(table, datatype) for table in self.tables] if units else [(None, datatype)]
            found = [self.columns[key] for key in keys]
        elif kind == "endpoint":
            found = (
                [self.endpoints[table] for table in self.tables]
                if units
                else [self.endpoints[None]]
            )
        elif kind == "table":
            found = [self.others, max(self.others - 1, 0)]
        else:
            found = []
        return sorted({*found})


def counts_of(descriptors: Sequence[Descriptor]) -> Counts:
    return Counts(descriptors)


def least_of(counts: Sequence[int], pick: int) -> int:
    """A ``min`` drawn as one of ``counts`` (the greatest one in two of three, where a table of
    60 to 70 columns is met or just missed) less one, as it is or plus one (``pick`` decoded):
    where a requirement with these counts is just met or just missed."""
    if not counts:
        return pick % 5
    pick, offset = divmod(pick, 3)
    pick, kind = divmod(pick, 3)
    return max((counts[-1] if kind else counts[pick % len(counts)]) + offset - 1, 0)


def requirement(
    number: int, pack: str, role: str, pick: int = 0, counts: Counts | None = None
) -> _Fields:
    """The ``number``-th requirement (of ``_REQUIREMENTS``): of any kind (or none), ``on``,
    datatype, ``min`` and predicate: one of its pack's, not registered (``ghost``), of another
    pack (``p0``) or of a pack not installed (``zz``). With the ``counts`` of a release, one in
    three requirements of a kind has the ``min`` that ``pick`` draws from its own counts there,
    less one, as they are or plus one (``least_of``)."""
    choices: list[int] = []
    for options in (_KINDS, _ON, _DATATYPES, _MINS, _PREDICATES):
        number, digit = divmod(number, len(options))
        choices.append(digit)
    kind, on, datatype, least, name = (
        _KINDS[choices[0]],
        _ON[choices[1]],
        _DATATYPES[choices[2]],
        _MINS[choices[3]],
        _PREDICATES[choices[4]],
    )
    if counts is not None and kind is not None and pick % 3 == 0:
        least = least_of(counts.of(kind, on, datatype if kind == "column" else None), pick // 3)
    found: _Fields = (("role", role),)
    found += (("kind", kind),) if kind is not None else ()
    found += (("on", on),) if on is not None else ()
    found += (("datatype", datatype),) if datatype is not None else ()
    found += (("min", least),) if least is not None else ()
    if name is not None:
        found += (("predicate", name if "." in name else f"{pack}.{name}"),)
    return found


def _required(chosen: list[tuple[int, int]], twice: int) -> list[tuple[int, int]]:
    """The requirements drawn for one analysis, with one given again (the ``twice``-th, if the
    analysis has so many): one entry under two roles."""
    return [*chosen, *([chosen[twice]] if twice < len(chosen) else [])]


@st.composite
def registries(
    draw: st.DrawFn, counts: Counts | None = None, big: bool = False
) -> PackRegistry | None:
    """The core's registry alone (``None``), or with one or two packs of one to four analyses
    of up to five requirements each (none, or one of them given twice under two roles), whose
    roles are not in the order of their names (``st.permutations``), so that a sort of the
    entries by role changes what is read first. Given the ``counts`` of a release, a third of
    the requirements of a kind have a ``min`` that is one of their own, less one, or plus one
    (``least_of``). ``big`` (for a release of dozens of units, which the
    scan takes a time over) draws up to two analyses of up to three requirements."""
    if draw(st.integers(0, 9)) == 0:
        return None
    packs = draw(
        st.lists(
            st.lists(
                st.tuples(
                    st.lists(
                        st.tuples(st.integers(0, _REQUIREMENTS - 1), st.integers(0, 2**20)),
                        max_size=3 if big else 5,
                    ),
                    st.integers(0, 9),
                    st.permutations(range(6)),
                ),
                min_size=1,
                max_size=2 if big else 4,
            ),
            min_size=1,
            max_size=2,
        )
    )
    spec: _Spec = tuple(
        (
            f"p{number}",
            tuple(
                tuple(
                    requirement(chosen, f"p{number}", f"r{order[role]}", pick, counts)
                    for role, (chosen, pick) in enumerate(_required(requires, twice))
                )
                for requires, twice, order in analyses
            ),
        )
        for number, analyses in enumerate(packs)
    )
    return _registry(spec)


# --- Graded releases: bitsets past a byte and a word, counts past 64 ---

_WIDE_MINS = (None, 0, 1, 2, 5, 30, 60, 63, 64, 65, 66, 67, 68, 69, 70, 71, 72, 100, 2**53 - 1)
_GRADED_TYPES = (None, "integer", "number", "string", "boolean", "category", "date")
_POOR_TYPES = (None, "integer", "string")


def _typed(id: str, datatype: str | None, status: str) -> Descriptor:
    extra: dict[str, Any] = {}
    if datatype == "category":
        extra["permissible_values"] = {"values": [{"value": "a"}]}
    return build.column(id, datatype, status=status, **extra)


def _cut(draw: random.Random, units: int) -> int:
    """Where a release of ``units`` becomes rich: anywhere, in the last eight units, or on a
    unit just either side of a width (the 8th, 16th, 32nd, 64th or 128th)."""
    edges = [edge + draw.randint(-2, 2) for edge in (8, 16, 32, 64, 128) if edge < units]
    return draw.choice(
        [
            draw.randint(0, units),
            *([max(units - draw.randint(1, 8), 0)] * 2),
            *(edges or [units // 2]),
        ]
    )


def _categories_from(draw: random.Random, units: int, cut: int) -> int:
    """The unit from which a graded release has columns of categories: the first, the cut, or
    (in half of them) one of the last six."""
    return draw.choice([0, cut, *([max(units - draw.randint(1, 6), 0)] * 2)])


def graded_release(draw: random.Random, units: int) -> tuple[list[Descriptor], list[str]]:
    """A release of ``units`` keyed tables (and up to two unkeyed, and one keyed twice) of every
    role and none, graded by a cut (``_cut``; in about one release of three the poor and the
    rich tables are mixed, not cut): the tables before it are poor (up to two columns of a few
    datatypes, mostly proposed, in no relationship, with no endpoint) and those from it rich
    (three to six columns of mostly one datatype, usually integers, and others, mostly asserted,
    endpoints, relationships; up to three of 60 to 70 columns, mostly of 62 to 65), so that
    what meets a requirement, settled, or has categories is found only from the ``cut``-th
    unit on, at a bit past a byte, a word, or two.
    The descriptors are in no order but the tables', which are the units'."""
    settings: list[dict[str, Any]] = [
        {},
        {"disclosure": {"min_cell_count": 3}},
        {"disclosure": {"min_cell_count": 3, "allow_row_ids": False}},
    ]
    names = [f"t{position:03d}" for position in range(units)]
    cut = _cut(draw, units)
    scattered = draw.random() < 0.35
    rich = [draw.random() < 0.5 if scattered else position >= cut for position in range(units)]
    late = [position for position in range(units) if rich[position]]
    wide = set(draw.sample(late, min(len(late), draw.randint(0, 3))))
    # categories (which a view can compare under *k*) from the first table, the cut, or one of
    # the last six: so that the first unit with them is, in half of the releases, a late one
    categories = _categories_from(draw, units, cut)
    tables: list[Descriptor] = []
    others: list[Descriptor] = [build.dataset(**draw.choice(settings))]
    for position, name in enumerate(names):
        given: dict[str, Any] = {"primary_key": ["c0"]}
        if (role := draw.choice(ROLES)) is not None:
            given["role"] = role
        status = draw.choice(STATUSES) if draw.random() < 0.2 else "asserted"
        tables.append(build.descriptor("table", name, given, status=status))
        if not rich[position]:
            for column in range(draw.randint(0, 2)):
                datatype = draw.choice(_POOR_TYPES)
                status = "proposed" if draw.random() < 0.7 else "asserted"
                others.append(_typed(f"{name}.c{column}", datatype, status))
            continue
        # the rich: three to six columns of integers (a wide one 60 to 70, mostly) and others
        main = "integer" if draw.random() < 0.7 else draw.choice(_GRADED_TYPES)
        width = (
            draw.choice([62, 63, 64, 65, draw.randint(60, 70)])
            if position in wide
            else draw.randint(3, 6)
        )
        for column in range(width + (0 if position in wide else draw.randint(0, 3))):
            datatype = (
                main if column < width and draw.random() < 0.9 else draw.choice(_GRADED_TYPES)
            )
            if datatype == "category" and position < categories:
                datatype = "string"
            status = "asserted" if draw.random() < 0.8 else "proposed"
            others.append(_typed(f"{name}.c{column}", datatype, status))
        if draw.random() < 0.4:
            fields: dict[str, Any] = {
                "table": name,
                "time_column": "c1",
                "status_column": "c2",
                "event_coding": {"event": ["a"], "censored": ["b"]},
            }
            if draw.random() < 0.7:
                fields["entry"] = "at_origin"
            others.append(build.descriptor("endpoint", f"ep:{name}", fields))
        if position >= categories and position and rich[position - 1] and draw.random() < 0.1:
            others.append(_relationship(name, names[position - 1], f"x{position}"))
    for number in range(draw.randint(0, 2)):
        tables.insert(draw.randint(0, len(tables)), build.table(f"u{number}", None, role="entity"))
    if draw.random() < 0.1:
        tables.insert(draw.randint(0, len(tables)), _keyed(draw.choice(names)))
    draw.shuffle(others)
    found: list[Descriptor] = []
    while tables or others:
        if tables and (not others or draw.random() < len(tables) / (len(tables) + len(others))):
            found.append(tables.pop(0))
        else:
            found.append(others.pop())
    return found, names


def wide_release(draw: random.Random) -> tuple[list[Descriptor], list[str]]:
    """A graded release (``graded_release``) of ``UNITS`` keyed units."""
    return graded_release(draw, draw.choice(UNITS))


def _graded_requirement(draw: random.Random, counts: Counts) -> dict[str, Any]:
    """A requirement a graded release can decide: mostly a column on the unit (integers, any
    datatype or another), an endpoint, a table or a predicate alone, and a ``min`` that is,
    three times in four, one of its own counts less one, as it is or plus one (the greatest
    count in two of three: where a table of 60 to 70 columns is met or just missed), else one of
    ``_WIDE_MINS``."""
    kind = draw.choice(["column"] * 6 + ["endpoint"] * 2 + ["table", None])
    one: dict[str, Any] = {} if kind is None else {"kind": kind}
    if kind is not None and draw.random() < 0.85:
        one["on"] = "unit"
    datatype = draw.choice([None, "integer", "integer", "integer", "string", "category", "date"])
    if kind == "column" and datatype is not None:
        one["datatype"] = datatype
    if kind is not None:
        if draw.random() < 0.75:
            found = counts.of(kind, one.get("on"), one.get("datatype"))
            least = least_of(found, draw.randrange(2**20))
        else:
            least = draw.choice(_WIDE_MINS)
        if least is not None:
            one["min"] = least
    if kind is None or draw.random() < 0.15:
        one["predicate"] = f"p0.{draw.choice(['yes', 'no', 'boom'])}"
    return one


def wide_registry(draw: random.Random, counts: Counts) -> PackRegistry:
    """One pack of one to six analyses of up to four requirements each, mostly one or two
    (``_graded_requirement``), one given twice under two roles and the roles not in name order;
    some with a predicate (``yes``, ``no`` or ``boom``); and, in most releases each, an
    analysis of a requirement for the integer columns of a unit, and one for those of the
    release, at the greatest count there is, less one, as it is or plus one (the last twice
    as often), with another that only the rich units meet."""
    analyses: list[list[dict[str, Any]]] = []
    for _ in range(draw.randint(1, 6)):
        chosen = [
            _graded_requirement(draw, counts) for _ in range(draw.choice([0, 1, 1, 1, 2, 2, 3, 4]))
        ]
        if chosen and draw.random() < 0.3:
            chosen.append(dict(draw.choice(chosen)))
        roles = draw.sample(range(len(chosen)), len(chosen))
        analyses.append(
            [{"role": f"r{role}", **one} for role, one in zip(roles, chosen, strict=True)]
        )
    for on in ("unit", None):
        if draw.random() < 0.85:
            # a requirement on the integer columns at the greatest count there is (a table of
            # 60 to 70, or the release's), less one, as it is or plus one, and another that only
            # the rich units meet, so that where the first is judged decides the unit
            kind = {"kind": "column", "datatype": "integer", **({"on": on} if on else {})}
            found = counts.of("column", on, "integer")
            least = max((found[-1] if found else 0) + draw.choice([-1, 0, 1, 1]), 0)
            rich = {"role": "r1", "kind": "column", "on": "unit", "datatype": "integer", "min": 3}
            analyses.append([{"role": "r0", **kind, "min": least}, rich])
    return registry_of(
        {"p0": analyses},
        {
            "p0": {
                "yes": predicate("p0.yes", True),
                "no": predicate("p0.no", False),
                "boom": predicate("p0.boom", None),
            }
        },
    )


# --- Edge releases: the deciding unit at each width's edge, deterministic by seed ---

EDGES = (1, 2, 7, 8, 9, 15, 16, 17, 31, 32, 33, 63, 64, 65, 127, 128, 129, 199)
"""The positions (from 0) at which the unit that decides is put: beside each width of a bitset
(releases of 127 to 210 units are a fifth of them)."""


class Edge(NamedTuple):
    """An edge release: its descriptors, the names of its keyed tables, whether it is wide, the
    position of the deciding unit, the mode of the units before it, and whether a table is
    given twice."""

    found: list[Descriptor]
    names: list[str]
    wide: bool
    at: int
    mode: str
    twice: bool


def edge_release(draw: random.Random) -> Edge:
    """A release of up to 210 keyed tables (a table given twice in three of ten) in which the
    unit that decides (the first with the integer column no longer unconfirmed, or present) is
    at one of ``EDGES`` or anywhere, the units before it differing in what the output shows
    (the column unconfirmed, absent, or both); in a quarter of them (the third value, whether
    the release is wide) the deciding unit, the three before it and the next have 62 to 66
    integer columns; strings, booleans, dates, numbers and categories besides, and an endpoint
    on half of the tables, tables of every role and none; and two tables that are no unit, ``u0``
    (unkeyed) and ``cv0`` (a coverage table). The descriptors are in no order but the tables'."""
    units = draw.choice([edge + 1 + draw.randint(0, 3) for edge in EDGES] + [draw.randint(2, 210)])
    at = min(draw.choice([*EDGES, draw.randrange(units)]), units - 1)
    mode = draw.choice(["unconfirmed", "missing", "both"])
    wide = draw.random() < 0.25
    found: list[Descriptor] = [
        build.dataset(**draw.choice([{}, {"disclosure": {"min_cell_count": 3}}]))
    ]
    tables: list[Descriptor] = []
    others: list[Descriptor] = []
    for position in range(units):
        name = f"t{position:03d}"
        role = draw.choice(["entity", "event", "link", "measurement", None])
        given: dict[str, Any] = {"primary_key": ["c0"], **({} if role is None else {"role": role})}
        tables.append(build.descriptor("table", name, given))
        before = position < at
        has = not (before and mode in ("missing", "both") and draw.random() < 0.8)
        width = (
            draw.choice([62, 63, 64, 65, 66])
            if wide and at - 3 <= position <= at + 1
            else draw.choice([1, 2, 3])
        )
        for column in range(width if has else 0):
            proposed = (
                before and mode in ("unconfirmed", "both") and (column == 0 or draw.random() < 0.3)
            )
            status = "proposed" if proposed else "asserted"
            others.append(build.column(f"{name}.c{column}", "integer", status=status))
        for column in range(draw.choice([0, 1, 2])):
            datatype = draw.choice(["string", "boolean", "date", "number", "category"])
            extra: dict[str, Any] = {}
            if datatype == "category":
                extra["permissible_values"] = {"values": [{"value": "a"}]}
            status = draw.choice(["asserted", "proposed"])
            others.append(build.column(f"{name}.x{column}", datatype, status=status, **extra))
        if draw.random() < 0.5:
            others.append(build.column(f"{name}.s1", "string"))
            others.append(build.column(f"{name}.s2", "string"))
            fields: dict[str, Any] = {
                "table": name,
                "time_column": "s1",
                "status_column": "s2",
                "event_coding": {"event": ["a"], "censored": ["b"]},
            }
            if draw.random() < 0.8:
                fields["entry"] = "at_origin"
            others.append(build.descriptor("endpoint", f"ep:{name}", fields))
    twice = draw.random() < 0.3
    if twice:
        tables.insert(
            draw.randint(0, len(tables)), build.table(f"t{draw.randrange(units):03d}", ["c0"])
        )
    others.append(build.descriptor("table", "u0", {"role": "entity"}))
    others.append(build.descriptor("table", "cv0", {"role": "coverage"}))
    others.append(build.column("u0.c0", "integer"))
    if draw.random() < 0.5:
        draw.shuffle(others)
    found += tables + others if draw.random() < 0.5 else [*tables, *others]
    names = [f"t{position:03d}" for position in range(units)]
    return Edge(found, names, wide, at, mode, twice)


def edge_registry(draw: random.Random, wide: bool) -> PackRegistry:
    """One pack of one to four analyses, each of the integer columns of a unit (``min`` none,
    1 to 3, or 62 to 67 for a wide release, with a predicate that holds or fails in half of them)
    and one other requirement: an endpoint, a ``min`` of
    2^53-1 for any column, a predicate that holds or fails, a column of strings or booleans, or
    tables (``min`` 1, 64 or 65); the roles in no order."""
    column = {"kind": "column", "on": "unit", "datatype": "integer"}
    analyses = []
    for _ in range(draw.randint(1, 4)):
        least = draw.choice([None, 1, 2, 3] if not wide else [62, 63, 64, 65, 66, 67])
        requires = [{"role": "x", **column, **({} if least is None else {"min": least})}]
        if draw.random() < 0.5:
            requires[0]["predicate"] = f"p.{draw.choice(['yes', 'no'])}"
        extra = draw.choice(["none", "endpoint", "huge", "predicate", "anycol", "table"])
        if extra == "endpoint":
            requires.append({"role": "e", "kind": "endpoint", "on": "unit"})
        elif extra == "huge":
            requires.append({"role": "h", "kind": "column", "min": 2**53 - 1})
        elif extra == "predicate":
            requires.append({"role": "p", "predicate": f"p.{draw.choice(['yes', 'no'])}"})
        elif extra == "anycol":
            datatype = draw.choice(["string", "boolean"])
            requires.append({"role": "a", "kind": "column", "on": "unit", "datatype": datatype})
        elif extra == "table":
            requires.append({"role": "t", "kind": "table", "min": draw.choice([1, 64, 65])})
        draw.shuffle(requires)
        analyses.append(requires)
    return registry_of(
        {"p": analyses}, {"p": {"yes": predicate("p.yes", True), "no": predicate("p.no", False)}}
    )


def scale_release(draw: random.Random, tables: int) -> list[Descriptor]:
    """``tables`` keyed tables (a coverage table in twelve, a quarter proposed, two given
    twice), each of two to six columns of several datatypes, a third of them proposed, and an
    endpoint on two in three (a quarter proposed): so that the index's per-table counts are
    many (past 64 endpoint tables, past 300 column keys) and its counts differ by status."""
    found: list[Descriptor] = [build.dataset()]
    datatypes = ["integer", "string", "boolean", "number", "date", "category"]
    for position in range(tables):
        name = f"t{position:03d}"
        role = (
            "coverage"
            if position % 12 == 11
            else draw.choice(["entity", "event", "link", "measurement", None])
        )
        status = "proposed" if draw.random() < 0.25 else "asserted"
        fields: dict[str, Any] = {"primary_key": ["c0"], **({} if role is None else {"role": role})}
        found.append(build.descriptor("table", name, fields, status=status))
        for column in range(draw.randint(2, 6)):
            datatype = datatypes[(position + column) % len(datatypes)]
            extra: dict[str, Any] = {}
            if datatype == "category":
                extra["permissible_values"] = {"values": [{"value": "a"}]}
            status = "proposed" if draw.random() < 0.34 else "asserted"
            found.append(build.column(f"{name}.c{column}", datatype, status=status, **extra))
        if position % 3:
            found.append(build.column(f"{name}.s1", "string"))
            found.append(build.column(f"{name}.s2", "string"))
            ending: dict[str, Any] = {
                "table": name,
                "time_column": "s1",
                "status_column": "s2",
                "event_coding": {"event": ["a"], "censored": ["b"]},
            }
            if draw.random() < 0.7:
                ending["entry"] = "at_origin"
            state = "proposed" if draw.random() < 0.25 else "asserted"
            found.append(build.descriptor("endpoint", f"ep:{name}", ending, status=state))
    # two tables given twice, once settled and once not: a unit each time
    for position in (tables // 3, tables - 2):
        again = {"role": "entity", "primary_key": ["c0"]}
        found.append(build.descriptor("table", f"t{position:03d}", again, status="proposed"))
    return found


def is_the_scan(
    packs: PackRegistry | None,
    descriptors: Sequence[Descriptor],
    unit: str | None,
    k: int | None,
    indexed: type[Analyses] = Quick,
) -> bool:
    """Whether the indexed registry (``Quick``, its core entries copied once) answers as the scan
    does (``QuickScan``) for this release, unit and *k*: outputs and the predicates it called, in
    order."""
    USED["is_the_scan"] += 1
    return outcome(indexed(packs), descriptors, unit, k) == outcome(
        QuickScan(packs), descriptors, unit, k
    )


def outcome(
    analyses: Analyses,
    descriptors: Sequence[Descriptor],
    unit: str | None,
    k: int | None,
    manifest: str = "m",
) -> tuple[list[dict[str, Any]], list[str]]:
    """What ``applicable`` gives, as JSON, and the requirement predicates it called, in order."""
    CALLS.clear()
    found = analyses.applicable(descriptors, dataset="d", manifest=manifest, unit=unit, k=k)
    return [one.model_dump() for one in found], list(CALLS)

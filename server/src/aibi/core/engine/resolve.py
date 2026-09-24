"""A document resolved against its releases, or refused (SPEC §6, §7.2, §7.6).

This is phase 1 of canonicalisation without its last steps: names become descriptor ids, paths
become explicit (§6.1), quantifiers are resolved per down step (§7.2), constants are typed and
predicates normalised (§6.4, §7.6 step 4), existence questions are split into single-step chains
with trailing lookups moved into the last ``where`` (step 5), ``cohort`` leaves are inlined, and
combinators are flattened (step 6). Each rule is applied as the tree is built, bottom up, so the
result is already the fixpoint that repeating steps 4 to 6 would reach. Sorting and hashing are
M2.2's (steps 8 and later).

Everything §6.5 refuses is refused here too: a scope or filtered column mentioned other than in
a top-level ``values`` conjunct without ``negate`` (and, for a filtered column, with allowed
values only), scope columns under ``every``, ``exclude_self`` that does not return to its row's
table, and unknown scope columns in ``covered``. Refusals point into the document as written.

Refused until later milestones: pack leaves (M2.2), concept references and cross-dataset
cohorts (M6), and a coverage's ``parent_scope`` holding more than value predicates and
combinators.
"""

import math
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta, timezone
from typing import Literal, cast

from aibi.core.engine.data import Release
from aibi.core.engine.graph import PATH_SEARCH_STEPS, Graph, Path, Step, down_steps, render
from aibi.core.engine.resolved import (
    Bounds,
    Constant,
    Deduplicated,
    Lift,
    Predicate,
    Quantifier,
    RAll,
    RAny,
    RClause,
    RCovered,
    RExists,
    RIds,
    RKnown,
    RUnknown,
    RValue,
    Values,
    all_of,
    any_of,
    conjuncts,
    intermediate,
    measure,
    not_of,
    origin_of,
    prefixed,
    value_leaves,
)
from aibi.core.engine.units import convertible
from aibi.core.schema.descriptors import (
    ColumnDescriptor,
    CoverageDescriptor,
    Descriptor,
    DirectCoverage,
    GroupedCoverage,
)
from aibi.core.schema.document import (
    AllClause,
    AnyClause,
    ClauseModel,
    CohortLeaf,
    CoveredLeaf,
    Document,
    ExistsLeaf,
    IdsLeaf,
    KnownClause,
    NotClause,
    PackLeaf,
    SomeAtLeast,
    UnitKey,
    UnknownClause,
    ValueLeaf,
)
from aibi.core.schema.document import Step as DocStep
from aibi.core.schema.ids import DECIMAL_INTEGER_RE, MAX_SAFE_INTEGER
from aibi.core.schema.jsonio import pointer
from aibi.core.schema.limits import (
    CLAUSE_DEPTH,
    LEAVES,
    MAX_CLAUSE_DEPTH,
    MAX_LEAVES,
    MAX_PATH_STEPS,
    PATH_SEARCH,
    PATH_STEPS,
)
from aibi.core.schema.loading import as_written
from aibi.core.schema.output import Segment, data, text
from aibi.core.schema.params import Position
from aibi.core.schema.refusals import Limit, Refusal, RefusalCode, finish_refusals

UNCONFIRMED = frozenset({"imported_default", "proposed", "undeclared"})
"""Curation statuses that raise ``UNCONFIRMED_SEMANTICS`` for a field read (§5.1)."""

_NUMERIC = ("number", "integer", "time_offset")
_ORDERED = ("number", "integer", "date", "datetime", "time_offset")
_LISTED = 64
"""Alternatives listed in a refusal before the rest are counted."""


@dataclass(frozen=True, order=True)
class FieldRead:
    """A descriptor field that resolution or evaluation reads, with its curation status."""

    descriptor: str
    pointer: str
    status: str


@dataclass(frozen=True)
class Coverage:
    """A relationship's coverage as evaluation reads it (§5.6, §6.5)."""

    relationship: str
    parent_table: str
    child_table: str
    form: Literal["undeclared", "all", "direct", "grouped"]
    proposed: bool
    """``parents`` is ``proposed``: answers that rely on it carry ``COVERAGE_PROPOSED``."""
    parent_scope: RClause | None
    """Resolved on the parent table."""
    record_filter: tuple[tuple[str, frozenset[str]], ...]
    scope_columns: tuple[str, ...]
    """The child's scope columns, in the coverage's order."""
    descriptor: CoverageDescriptor | None


@dataclass(frozen=True)
class ResolvedCohort:
    name: str
    dataset: str
    """The dataset reference as written."""
    release: Release
    unit: str
    clauses: tuple[RClause, ...]
    """The top-level clauses: the members of the canonical cohort's top-level ``all``."""
    leaves: Mapping[Position, frozenset[int]]
    """Each leaf of the cohort as written, and the top-level clauses it became part of (§6.6). A
    cohort leaf is one leaf; the referenced cohort's leaves are in its own map."""
    fields: tuple[FieldRead, ...]
    coverage: Mapping[str, Coverage]
    """Each relationship a question asks about, by id."""

    @property
    def tree(self) -> RClause:
        return self.clauses[0] if len(self.clauses) == 1 else RAll(self.clauses)

    @property
    def unconfirmed(self) -> tuple[FieldRead, ...]:
        """The fields that raise ``UNCONFIRMED_SEMANTICS``; a proposed ``parents`` does not,
        since ``COVERAGE_PROPOSED`` reports it where it matters (§5.1)."""
        return tuple(
            read
            for read in self.fields
            if read.status in UNCONFIRMED
            and not (read.pointer == "/fields/parents" and read.status == "proposed")
        )


@dataclass(frozen=True)
class Resolution:
    cohorts: Mapping[str, ResolvedCohort]
    """The cohorts resolved; a cohort with a refusal, or that depends on one, is left out."""
    refusals: list[Refusal]


def resolve(
    document: Document,
    releases: Mapping[str, Release],
    positions: Mapping[Position, str] | None = None,
) -> Resolution:
    """Resolve a loaded document. ``releases`` maps each dataset reference as written (``d``,
    ``d@3``) to its release; ``positions`` are the loader's, so that refusals point into the
    document as written."""
    return _Resolver(document, releases, positions or {}).run()


def check_parent_scopes(release: Release) -> list[Refusal]:
    """Refusals for the parent scopes of a release's coverages that do not resolve against it,
    as ``finish_refusals`` returns them, with paths into the list of descriptors.

    Resolution types a parent scope's constants and checks its paths, which ``check_release``
    cannot do without the engine, so a release is checked by both (§13.2)."""
    graph = Graph.of(release)
    refusals: list[Refusal] = []
    for index, descriptor in enumerate(release.descriptors):
        if not isinstance(descriptor, CoverageDescriptor):
            continue
        link = release.relationship(descriptor.fields.relationship)
        if descriptor.fields.parent_scope is None or link is None:
            continue  # check_release refuses a coverage of an unknown relationship
        table = link.fields.parent_table
        if table not in graph.nodes:
            continue
        context = _Cohort("", release.dataset, release, graph, table)
        _, found, _ = _Resolver.scope_of(context, descriptor, table)
        prefix = pointer([index, "fields"])
        refusals.extend(
            Refusal(
                code=refusal.code,
                path=prefix + (refusal.path or ""),
                message=refusal.message,
                alternatives=refusal.alternatives,
                limit=refusal.limit,
            )
            for refusal in found
        )
    return finish_refusals(refusals)


# --- Constants (§6.4) --------------------------------------------------------------------------

_DATE_RE = re.compile(r"^([0-9]{4})-([0-9]{2})-([0-9]{2})$")
_DATETIME_RE = re.compile(
    r"^([0-9]{4})-([0-9]{2})-([0-9]{2})[Tt]([0-9]{2}):([0-9]{2}):([0-9]{2})(?:\.([0-9]+))?"
    r"([Zz]|[+-][0-9]{2}:[0-9]{2})$"
)
_EXPECTED = {
    "number": "finite numbers, integers beyond ±(2^53 - 1) written as decimal strings",
    "integer": "64-bit integers, those beyond ±(2^53 - 1) written as decimal strings",
    "time_offset": "finite numbers, in the predicate's units",
    "string": "strings",
    "category": "strings",
    "list<category>": "strings",
    "boolean": "true or false",
    "date": "dates written YYYY-MM-DD",
    "datetime": "RFC 3339 date-times with an explicit offset, to the microsecond",
}


def _day(text: str) -> date | None:
    match = _DATE_RE.fullmatch(text)
    if match is None:
        return None
    try:
        day = date(*(int(part) for part in match.groups()))
    except ValueError:
        return None
    return day if day.year > 0 else None


def _instant(text: str) -> datetime | None:
    match = _DATETIME_RE.fullmatch(text)
    if match is None:
        return None
    year, month, day, hour, minute, second, fraction, offset = match.groups()
    digits = fraction or ""
    if len(digits) > 6 and digits[6:].strip("0"):
        return None  # finer than the microsecond datetimes are stored to
    micro = int((digits[:6]).ljust(6, "0")) if digits else 0
    if offset in ("Z", "z"):
        zone = UTC
    else:
        sign = 1 if offset[0] == "+" else -1
        hours, minutes = int(offset[1:3]), int(offset[4:6])
        if hours > 23 or minutes > 59:
            return None
        zone = timezone(sign * timedelta(hours=hours, minutes=minutes))
    try:
        moment = datetime(
            int(year), int(month), int(day), int(hour), int(minute), int(second), micro, zone
        )
    except ValueError:
        return None
    try:
        return moment.astimezone(UTC)
    except OverflowError:
        return None


_INT64 = 2**63


def typed_constant(value: object, datatype: str) -> Constant | None:
    """A document constant as a value of the column's type, or ``None`` (§6.4).

    An integer written as a decimal string is a double for number and time-offset columns, as
    the column's values are and as SQL compares them (D203), and must be finite as one; for an
    integer column it must lie in the 64-bit range the column's values do."""
    if datatype in ("number", "integer", "time_offset"):
        if isinstance(value, bool):
            return None
        if isinstance(value, str):
            if not DECIMAL_INTEGER_RE.fullmatch(value) or len(value.lstrip("-")) > 400:
                return None
            whole = int(value)
            if abs(whole) <= MAX_SAFE_INTEGER:
                return None  # written as a number, not a string (§5.1)
            if datatype == "integer":
                return whole if -_INT64 <= whole < _INT64 else None
            try:
                return float(whole)
            except OverflowError:
                return None
        if isinstance(value, float):
            return None if datatype == "integer" else value
        return value if isinstance(value, int) else None
    if datatype in ("string", "category", "list<category>"):
        return value if isinstance(value, str) else None
    if datatype == "boolean":
        return value if isinstance(value, bool) else None
    if datatype == "date":
        return _day(value) if isinstance(value, str) else None
    if datatype == "datetime":
        return _instant(value) if isinstance(value, str) else None
    return None


_JSON_NUMBER = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?")


def _key_constant(text: str, datatype: str) -> Constant | None:
    """A unit key written as text, ``"<dataset>:<key>"``, typed against its key column: a number
    is written as JSON writes one, and an integer in decimal."""
    if datatype in ("number", "integer", "time_offset"):
        if len(text) > 400 or not _JSON_NUMBER.fullmatch(text):
            return None
        if DECIMAL_INTEGER_RE.fullmatch(text):
            whole = int(text)
            if datatype == "integer":
                return whole if -_INT64 <= whole < _INT64 else None
            try:
                return float(whole) if abs(whole) > MAX_SAFE_INTEGER else whole
            except OverflowError:
                return None
        if datatype == "integer":
            return None
        number = float(text)
        return number if math.isfinite(number) else None
    if datatype == "boolean":
        return {"true": True, "false": False}.get(text)
    if datatype in ("string", "category"):
        return text
    return typed_constant(text, datatype)


# --- The resolver ----------------------------------------------------------------------------


@dataclass
class _Cohort:
    """What resolving one cohort accumulates."""

    name: str
    dataset: str
    release: Release
    graph: Graph
    unit: str
    curated: bool = False
    """A coverage's parent scope, written by curators: the row-id rule is for documents."""
    served: bool = False
    """Resolving a ``where`` that a path's trailing lookups serve (§7.6, step 5)."""
    fields: set[FieldRead] = field(default_factory=set[FieldRead])
    coverage: dict[str, Coverage] = field(default_factory=dict[str, Coverage])
    failed: bool = False
    written: set[Position] = field(default_factory=set[Position])
    """The positions of the cohort's leaves as written, cohort leaves included."""
    deduplicated: Deduplicated = field(default_factory=Deduplicated)
    """The cohort's nodes as step 8 leaves them, for mentions and the caps."""


class _Resolver:
    def __init__(
        self,
        document: Document | None,
        releases: Mapping[str, Release],
        positions: Mapping[Position, str],
    ) -> None:
        self._given = document
        """The document resolved; a release's parent scopes are resolved without one."""
        self.releases = releases
        self.positions = positions
        self.refusals: list[Refusal] = []
        self.resolved: dict[str, ResolvedCohort | None] = {}
        self.graphs: dict[int, Graph] = {}

    @property
    def document(self) -> Document:
        assert self._given is not None, "only a document's resolution reads the document"
        return self._given

    # --- Refusals --------------------------------------------------------------------------

    def refuse(
        self,
        code: RefusalCode,
        at: Position,
        *message: Segment,
        alternatives: Iterable[Segment] = (),
        limit: Limit | None = None,
    ) -> None:
        written, parameter = as_written(at, self.positions) if self.positions else (at, None)
        segments = list(message)
        if parameter is not None:
            segments += [text(" (in the value of parameter "), data(parameter), text(")")]
        self.refusals.append(
            Refusal(
                code=code,
                path=pointer(list(written)),
                message=segments,
                alternatives=list(alternatives),
                limit=limit,
            )
        )

    @staticmethod
    def listed(names: Sequence[str]) -> list[Segment]:
        shown: list[Segment] = [data(name) for name in names[:_LISTED]]
        if len(names) > _LISTED:
            shown.append(text(f"and {len(names) - _LISTED} more"))
        return shown

    # --- Documents and cohorts -------------------------------------------------------------

    def run(self) -> Resolution:
        self._mixed_releases()
        for name in self._order():
            self.resolved[name] = self._cohort(name)
        cohorts = {name: cohort for name, cohort in self.resolved.items() if cohort is not None}
        return Resolution(cohorts, finish_refusals(self.refusals))

    def _order(self) -> list[str]:
        """Cohorts after those they reference, in document order otherwise."""
        cohorts = self.document.cohorts
        references: dict[str, list[str]] = {}
        for name, cohort in cohorts.items():
            found: list[str] = []
            pending: list[ClauseModel] = list(cohort.all)
            while pending:
                clause = pending.pop()
                if isinstance(clause, CohortLeaf):
                    found.append(clause.cohort)
                elif isinstance(clause, AllClause | AnyClause):
                    pending.extend(clause.all if isinstance(clause, AllClause) else clause.any)
                elif isinstance(clause, NotClause):
                    pending.append(clause.not_)
                elif isinstance(clause, KnownClause):
                    pending.append(clause.known)
                elif isinstance(clause, UnknownClause):
                    pending.append(clause.unknown)
            references[name] = found
        # Depth first, without recursion: a nested function that calls itself would keep what
        # it holds in a reference cycle. A cycle among cohorts is the document checks' to refuse.
        ordered: list[str] = []
        started: set[str] = set()
        for first in cohorts:
            stack: list[tuple[str, bool]] = [(first, False)]
            while stack:
                name, done = stack.pop()
                if done:
                    ordered.append(name)
                    continue
                if name in started or name not in cohorts:
                    continue
                started.add(name)
                stack.append((name, True))
                stack.extend((other, False) for other in reversed(references[name]))
        return ordered

    def _reference(self, name: str) -> tuple[str, Position]:
        cohort = self.document.cohorts[name]
        if cohort.dataset is not None:
            return cohort.dataset, ("cohorts", name, "dataset")
        return self.document.dataset or "", ("dataset",)

    def _mixed_releases(self) -> None:
        """A document resolves each dataset to exactly one release (§7.1)."""
        seen: dict[str, str] = {}
        for name in self.document.cohorts:
            reference, at = self._reference(name)
            release = self.releases.get(reference)
            if release is None:
                continue
            first = seen.setdefault(release.dataset, release.manifest)
            if first != release.manifest:
                self.refuse(
                    RefusalCode.MIXED_RELEASES,
                    at,
                    text("This names another release of dataset "),
                    data(release.dataset),
                    text(" than the rest of the document; one document uses one release of each"),
                )

    def _cohort(self, name: str) -> ResolvedCohort | None:
        cohort = self.document.cohorts[name]
        base: Position = ("cohorts", name)
        if cohort.datasets is not None:
            self.refuse(
                RefusalCode.NOT_SUPPORTED,
                (*base, "datasets"),
                text("Cross-dataset cohorts are resolved from M6"),
            )
            return None
        reference, at = self._reference(name)
        if not reference:
            return None  # no dataset: the document checks refuse it
        release = self.releases.get(reference)
        if release is None:
            self.refuse(
                RefusalCode.UNKNOWN_DATASET,
                at,
                text("No release is given for dataset "),
                data(reference),
            )
            return None
        unit = self._unit(release)
        if unit is None:
            return None
        graph = self.graphs.get(id(release))
        if graph is None:
            graph = self.graphs[id(release)] = Graph.of(release)
        context = _Cohort(name, reference, release, graph, unit)
        self._read(context, release.table(unit), unit, "/fields/primary_key")
        clauses: list[RClause] = []
        for index, clause in enumerate(cohort.all):
            resolved = self._clause(context, clause, (*base, "all", index), unit, False)
            if resolved is not None:
                clauses.append(resolved)
        if context.failed:
            return None
        top = self._within_caps(context, (*base, "all"), tuple(clauses))
        if top is None:
            return None
        # Every leaf of the cohort as written maps to the top-level clauses it became part of;
        # one that became none (a reference to an empty cohort) maps to no clause.
        leaves: dict[Position, list[int]] = {position: [] for position in context.written}
        for index, clause in enumerate(top):
            for position in clause.origin:
                leaves.setdefault(position, []).append(index)
        return ResolvedCohort(
            name,
            reference,
            release,
            unit,
            top,
            _interned(leaves),
            tuple(sorted(context.fields)),
            dict(sorted(context.coverage.items())),
        )

    def _within_caps(
        self, context: _Cohort, at: Position, clauses: tuple[RClause, ...]
    ) -> tuple[RClause, ...] | None:
        """The top-level clauses of a cohort's canonical form, duplicates removed, or ``None``
        if it breaks the caps on depth and leaves (§7.1)."""
        members, depth, leaves = measure(clauses, context.deduplicated)
        if depth > MAX_CLAUSE_DEPTH:
            self.refuse(
                RefusalCode.LIMIT_EXCEEDED,
                at,
                text(f"The cohort's canonical form nests clauses {depth} deep, and at most "),
                text(f"{MAX_CLAUSE_DEPTH} may be; each down step of a path is a question of its "),
                text("own, whose where holds the rest of the path (§6.1)"),
                limit=Limit(name=CLAUSE_DEPTH, max=MAX_CLAUSE_DEPTH),
            )
        if leaves > MAX_LEAVES:
            self.refuse(
                RefusalCode.LIMIT_EXCEEDED,
                at,
                text(f"The cohort's canonical form has {leaves} leaves, and at most {MAX_LEAVES} "),
                text("may be, counting those inside every where and those of the cohorts it "),
                text("references; each down step of a path is a leaf of its own (§6.1)"),
                limit=Limit(name=LEAVES, max=MAX_LEAVES),
            )
        return members if depth <= MAX_CLAUSE_DEPTH and leaves <= MAX_LEAVES else None

    def _unit(self, release: Release) -> str | None:
        unit = self.document.unit
        if ":" in unit:
            self.refuse(
                RefusalCode.NOT_SUPPORTED,
                ("unit",),
                text("A concept as the unit is resolved from M6, with cross-dataset queries"),
            )
            return None
        if release.table(unit) is None:
            self.refuse(
                RefusalCode.UNKNOWN_TABLE,
                ("unit",),
                text("The release has no table "),
                data(unit),
                alternatives=self.listed(
                    [
                        t
                        for t in release.table_ids
                        if t not in release.coverage_tables and release.primary_key(t) is not None
                    ]
                ),
            )
            return None
        if unit in release.coverage_tables:
            self.refuse(
                RefusalCode.INVALID_UNIT,
                ("unit",),
                text("A coverage table is not in the table graph, so it cannot be the unit: "),
                data(unit),
            )
            return None
        if release.primary_key(unit) is None:
            self.refuse(
                RefusalCode.INVALID_UNIT,
                ("unit",),
                text("The unit is a table with a declared key; this one has none: "),
                data(unit),
            )
            return None
        return unit

    # --- Fields read (§5.1) ------------------------------------------------------------------

    def _read(
        self,
        context: _Cohort,
        descriptor: Descriptor | None,
        descriptor_id: str,
        at: str,
        *,
        absent_counts: bool = False,
    ) -> None:
        """Record a field read. An absent field is recorded only where its absence changes the
        answer (``absent_counts``), as undeclared."""
        if descriptor is None:
            if absent_counts:
                context.fields.add(FieldRead(descriptor_id, at, "undeclared"))
            return
        entry = descriptor.curation.get(at)
        if entry is not None:
            context.fields.add(FieldRead(descriptor_id, at, entry.status))
        elif absent_counts:
            context.fields.add(FieldRead(descriptor_id, at, "undeclared"))

    def _read_column(self, context: _Cohort, descriptor: ColumnDescriptor) -> None:
        fields = descriptor.fields
        self._read(context, descriptor, descriptor.id, "/fields/datatype")
        if fields.datatype in _NUMERIC:
            # A numeric column without declared units raises UNCONFIRMED_SEMANTICS (§6.4).
            self._read(context, descriptor, descriptor.id, "/fields/units", absent_counts=True)
        if fields.permissible_values is not None:
            self._read(context, descriptor, descriptor.id, "/fields/permissible_values")
        if fields.missing_codes is not None:
            self._read(context, descriptor, descriptor.id, "/fields/missing_codes")

    def _read_path(self, context: _Cohort, path: Path) -> None:
        for step in path:
            relationship = context.release.relationship(step.rel)
            if relationship is not None:
                for at in ("/fields/child_columns", "/fields/parent_columns"):
                    self._read(context, relationship, relationship.id, at)

    # --- Clauses -----------------------------------------------------------------------------

    def _clause(
        self, context: _Cohort, clause: ClauseModel, at: Position, table: str, in_where: bool
    ) -> RClause | None:
        if isinstance(clause, AllClause):
            members = self._members(context, clause.all, (*at, "all"), table, in_where)
            return None if members is None else all_of(members) if members else RAll(())
        if isinstance(clause, AnyClause):
            members = self._members(context, clause.any, (*at, "any"), table, in_where)
            return None if members is None else any_of(members) if members else RAny(())
        if isinstance(clause, NotClause):
            member = self._clause(context, clause.not_, (*at, "not"), table, in_where)
            return None if member is None else not_of(member, frozenset())
        if isinstance(clause, KnownClause):
            member = self._clause(context, clause.known, (*at, "known"), table, in_where)
            return None if member is None else RKnown(member, member.origin)
        if isinstance(clause, UnknownClause):
            member = self._clause(context, clause.unknown, (*at, "unknown"), table, in_where)
            return None if member is None else RUnknown(member, member.origin)
        resolved: RClause | None
        context.written.add(at)
        if isinstance(clause, ValueLeaf):
            resolved = self._value(context, clause, at, table)
        elif isinstance(clause, ExistsLeaf):
            resolved = self._exists(context, clause, at, table)
        elif isinstance(clause, CoveredLeaf):
            resolved = self._covered(context, clause, at, table)
        elif isinstance(clause, IdsLeaf):
            resolved = self._ids(context, clause, at)
        elif isinstance(clause, CohortLeaf):
            resolved = self._inlined(context, clause, at)
        else:
            resolved = self._pack(clause, at)
        if resolved is None:
            context.failed = True
        return resolved

    def _members(
        self,
        context: _Cohort,
        clauses: Sequence[ClauseModel],
        at: Position,
        table: str,
        in_where: bool,
    ) -> list[RClause] | None:
        members, failed = self._resolved(context, clauses, at, table, in_where)
        return None if failed else members

    def _resolved(
        self,
        context: _Cohort,
        clauses: Sequence[ClauseModel],
        at: Position,
        table: str,
        in_where: bool,
    ) -> tuple[list[RClause], bool]:
        """The members that resolved, and whether any was refused."""
        members: list[RClause] = []
        failed = False
        for index, clause in enumerate(clauses):
            resolved = self._clause(context, clause, (*at, index), table, in_where)
            if resolved is None:
                failed = True
            else:
                members.append(resolved)
        return members, failed

    def _pack(self, clause: PackLeaf, at: Position) -> None:
        self.refuse(
            RefusalCode.NOT_SUPPORTED,
            (*at, "kind"),
            text("Pack leaves are expanded by their pack's compiler from M2.2: "),
            data(clause.kind),
        )

    def _inlined(self, context: _Cohort, clause: CohortLeaf, at: Position) -> RClause | None:
        """The referenced cohort's canonical form, inlined (§7.6, step 3): each of its top-level
        clauses has the reference as written as its origin, not the referenced cohort's leaves."""
        other = self.resolved.get(clause.cohort)
        if other is None:
            return None  # its own refusals, or the document checks', say why
        context.fields.update(other.fields)
        context.coverage.update(other.coverage)
        # The cohort leaf is a leaf as written: it maps to every clause its expansion joins, and
        # an empty cohort, an empty all (TRUE), maps to where it is inlined.
        origin = frozenset({at})
        marked = tuple(replace(clause, origin=origin) for clause in other.clauses)
        return marked[0] if len(marked) == 1 else RAll(marked, origin)

    # --- Tables and paths --------------------------------------------------------------------

    def _graph_tables(self, context: _Cohort) -> list[str]:
        return sorted(context.graph.nodes)

    def _table(self, context: _Cohort, name: str, at: Position) -> str | None:
        """A table of the table graph, or refused at ``at``."""
        if ":" in name:
            self.refuse(
                RefusalCode.NOT_SUPPORTED,
                at,
                text("Concept references are resolved from M6: "),
                data(name),
            )
            return None
        release = context.release
        if release.table(name) is None:
            self.refuse(
                RefusalCode.UNKNOWN_TABLE,
                at,
                text("The release has no table "),
                data(name),
                alternatives=self.listed(self._graph_tables(context)),
            )
            return None
        if name not in context.graph.nodes:
            self.refuse(
                RefusalCode.NO_PATH,
                at,
                text("A coverage table is not in the table graph, so no path reaches it: "),
                data(name),
            )
            return None
        return name

    def _steps_from(self, context: _Cohort, table: str) -> list[Segment]:
        """The steps that leave a table, as alternatives."""
        graph = context.graph
        steps = [
            Step(rel, direction)
            for rel in graph.edges
            for direction in ("up", "down")
            if graph.target(table, Step(rel, direction)) is not None
        ]
        return self.listed([render((step,)) for step in sorted(steps)])

    def _path(
        self,
        context: _Cohort,
        via: Sequence[DocStep] | Mapping[str, object] | None,
        at: Position,
        start: str,
        target: str,
        member: str,
    ) -> Path | None:
        """The path from ``start`` to ``target``: ``via`` checked step by step, or the one
        implicit path, refused at ``member`` (§6.1)."""
        if isinstance(via, Mapping):
            self.refuse(
                RefusalCode.NOT_SUPPORTED,
                (*at, "via"),
                text("A via by dataset is for cross-dataset cohorts, resolved from M6"),
            )
            return None
        graph = context.graph
        if via is not None:
            steps: list[Step] = []
            current = start
            for index, given in enumerate(via):
                rel = given.rel
                step = Step(rel, given.dir)
                if context.release.relationship(rel) is None or rel not in graph.edges:
                    self.refuse(
                        RefusalCode.UNKNOWN_RELATIONSHIP,
                        (*at, "via", index, "rel"),
                        text("The release has no relationship "),
                        data(rel),
                        alternatives=self._steps_from(context, current),
                    )
                    return None
                reached = graph.target(current, step)
                if reached is None:
                    self.refuse(
                        RefusalCode.INVALID_PATH,
                        (*at, "via", index),
                        text("This step does not leave table "),
                        data(current),
                        text(", where the path is; the steps that do are listed"),
                        alternatives=self._steps_from(context, current),
                    )
                    return None
                steps.append(step)
                current = reached
            if current != target:
                self.refuse(
                    RefusalCode.INVALID_PATH,
                    (*at, "via"),
                    text("The path ends at table "),
                    data(current),
                    text(", not at "),
                    data(target),
                )
                return None
            return tuple(steps)
        search = graph.paths(start, target, limit=_LISTED + 1)
        if search.exhausted and len(search.paths) < 2:
            self.refuse(
                RefusalCode.LIMIT_EXCEEDED,
                (*at, member),
                text("The search for a path from "),
                data(start),
                text(" to "),
                data(target),
                text(" took too many steps; give the path as via"),
                limit=Limit(name=PATH_SEARCH, max=PATH_SEARCH_STEPS),
            )
            return None
        if search.too_long:
            self.refuse(
                RefusalCode.LIMIT_EXCEEDED,
                (*at, member),
                text("Every path from table "),
                data(start),
                text(" to "),
                data(target),
                text(f" that visits no table twice has more than {MAX_PATH_STEPS} steps, the "),
                text("most a path may have"),
                limit=Limit(name=PATH_STEPS, max=MAX_PATH_STEPS),
            )
            return None
        if not search.paths:
            self.refuse(
                RefusalCode.NO_PATH,
                (*at, member),
                text("No path of relationships leads from table "),
                data(start),
                text(" to "),
                data(target),
            )
            return None
        if len(search.paths) > 1:
            # The search stops after one path more than it lists.
            listing: list[Segment] = [data(render(path)) for path in search.paths[:_LISTED]]
            if len(search.paths) > _LISTED:
                listing.append(text("and more"))
            elif search.exhausted:
                listing.append(text("and perhaps more, which the search stopped before finding"))
            self.refuse(
                RefusalCode.AMBIGUOUS_PATH,
                (*at, member),
                text("Several paths lead from table "),
                data(start),
                text(" to "),
                data(target),
                text("; name one with via"),
                alternatives=listing,
            )
            return None
        return next(iter(search.paths))

    # --- Quantifiers (§7.2) -----------------------------------------------------------------

    def _quantifiers(
        self,
        leaf: ValueLeaf | ExistsLeaf,
        at: Position,
        path: Path,
        min_count: int | None,
    ) -> list[tuple[Quantifier, int | None]] | None:
        count = down_steps(path)
        given = leaf.quantifier
        if given is None:
            quantifiers = [_quantifier("some")] * count
        elif isinstance(given, list):
            if len(given) != count:
                steps = "1 down step" if count == 1 else f"{count} down steps"
                entries = "1 entry" if count == 1 else f"{count} entries"
                self.refuse(
                    RefusalCode.QUANTIFIER_MISMATCH,
                    (*at, "quantifier"),
                    text(f"The resolved path has {steps}, so a quantifier list has {entries}, "),
                    text(f"not {len(given)}; the path is "),
                    data(render(path)),
                )
                return None
            quantifiers = [_quantifier(item) for item in given]
        else:
            quantifiers = [_quantifier(given)] * count
        if min_count is not None and quantifiers:
            quantifiers[-1] = ("some", min_count)
        return quantifiers

    # --- Leaves --------------------------------------------------------------------------------

    def _value(self, context: _Cohort, leaf: ValueLeaf, at: Position, table: str) -> RClause | None:
        if ":" in leaf.column:
            self.refuse(
                RefusalCode.NOT_SUPPORTED,
                (*at, "column"),
                text("Concept references are resolved from M6: "),
                data(leaf.column),
            )
            return None
        name, column = leaf.column.split(".", 1)
        target = self._table(context, name, (*at, "column"))
        if target is None:
            return None
        release = context.release
        descriptor = release.column(target, column)
        if descriptor is None:
            self.refuse(
                RefusalCode.UNKNOWN_COLUMN,
                (*at, "column"),
                text("Table "),
                data(target),
                text(" has no column "),
                data(column),
                alternatives=self.listed(list(release.columns(target))),
            )
            return None
        path = self._path(context, leaf.via, at, table, target, "column")
        if context.curated and path is not None and down_steps(path):
            self.refuse(
                RefusalCode.NOT_SUPPORTED,
                (*at, "via") if leaf.via is not None else (*at, "column"),
                text("This path goes down to rows below the parent table, which asks a "),
                text("question; in v1 a parent scope holds value predicates on the parent row "),
                text("or rows it looks up (D207)"),
            )
            return None
        datatype = descriptor.fields.datatype
        if datatype is None:
            self.refuse(
                RefusalCode.UNDECLARED_DATATYPE,
                (*at, "column"),
                text("The column's datatype is undeclared, so its constants cannot be typed: "),
                data(descriptor.id),
            )
            return None
        ok = path is not None
        if (
            not context.curated
            and self._identifier(context, descriptor)
            and not self._row_ids(context)
        ):
            self.refuse(
                RefusalCode.ROW_IDS_NOT_ALLOWED,
                (*at, "column"),
                text("The dataset does not allow row ids, so predicates on identifier columns "),
                text("are refused (§8.4): "),
                data(descriptor.id),
            )
            ok = False
        typed = self._predicate(context, leaf, at, descriptor)
        units_ok, units = self._units(leaf, at, descriptor)
        match_ok, match = self._match(leaf, at, descriptor)
        if not (ok and units_ok and match_ok) or path is None or typed is None:
            return None
        predicate, negate = typed
        self._read_column(context, descriptor)
        self._read_path(context, path)
        node = RValue(descriptor.id, predicate, (), negate, units, match, frozenset({at}))
        quantifiers = self._quantifiers(leaf, at, path, None)
        if quantifiers is None:
            return None
        if not quantifiers:
            return replace(node, via=path)
        return self._chain(
            context, path, quantifiers, leaf.lift or "strict", False, (node,), at, table
        )

    def _identifier(self, context: _Cohort, descriptor: ColumnDescriptor) -> bool:
        """Whether a column identifies rows: declared so, or a key or foreign key (§5.4)."""
        if descriptor.fields.identifier:
            return True
        table, column = descriptor.id.split(".", 1)
        release = context.release
        if column in (release.primary_key(table) or ()):
            return True
        return any(
            (r.fields.child_table == table and column in r.fields.child_columns)
            or (r.fields.parent_table == table and column in r.fields.parent_columns)
            for r in release.relationships
        )

    def _row_ids(self, context: _Cohort) -> bool:
        dataset = context.release.dataset_descriptor
        disclosure = None if dataset is None else dataset.fields.disclosure
        return disclosure is None or disclosure.allow_row_ids

    def _constant(
        self, value: object, descriptor: ColumnDescriptor, at: Position
    ) -> Constant | None:
        """A constant typed against a column, and within its permissible values (§6.4)."""
        datatype = descriptor.fields.datatype or ""
        typed = typed_constant(value, datatype)
        if typed is None:
            self.refuse(
                RefusalCode.INVALID_CONSTANT,
                at,
                text(f"Constants of {datatype} columns are {_EXPECTED.get(datatype, 'typed')}; "),
                text("the column is "),
                data(descriptor.id),
            )
            return None
        allowed = descriptor.fields.permissible_values
        if allowed is not None and datatype in ("category", "list<category>"):
            names = [entry.value for entry in allowed.values]
            if typed not in names:
                self.refuse(
                    RefusalCode.NOT_PERMISSIBLE,
                    at,
                    text("Not a permissible value of "),
                    data(descriptor.id),
                    text(": "),
                    data(str(typed)),
                    alternatives=self.listed(names),
                )
                return None
        return typed

    def _orderable(self, descriptor: ColumnDescriptor) -> bool:
        datatype = descriptor.fields.datatype
        if datatype in _ORDERED:
            return True
        allowed = descriptor.fields.permissible_values
        return (
            datatype in ("category", "list<category>") and allowed is not None and allowed.ordered
        )

    def _predicate(
        self, context: _Cohort, leaf: ValueLeaf, at: Position, descriptor: ColumnDescriptor
    ) -> tuple[Predicate, bool] | None:
        """The predicate as ``values`` or a range, with ``negate`` (§7.6, step 4)."""
        negate = leaf.negate
        if leaf.values is not None:
            typed = [
                self._constant(value, descriptor, (*at, "values", index))
                for index, value in enumerate(leaf.values)
            ]
            if any(value is None for value in typed):
                return None
            return Values(tuple(cast(list[Constant], typed))), negate
        if leaf.range is not None:
            if not self._orderable(descriptor):
                self._no_range(descriptor, (*at, "range"))
                return None
            bounds: dict[str, Constant | None] = {}
            failed = False
            for name in ("gt", "gte", "lt", "lte"):
                bound = getattr(leaf.range, name)
                if bound is None:
                    continue
                typed_bound = self._constant(bound, descriptor, (*at, "range", name))
                failed = failed or typed_bound is None
                bounds[name] = typed_bound
            return None if failed else (Bounds(**bounds), negate)
        op, value = leaf.op, leaf.value
        if op in ("=", "!="):
            typed_value = self._constant(value, descriptor, (*at, "value"))
            if typed_value is None:
                return None
            return Values((typed_value,)), negate != (op == "!=")
        if not self._orderable(descriptor):
            self._no_range(descriptor, (*at, "op"))
            return None
        typed_value = self._constant(value, descriptor, (*at, "value"))
        if typed_value is None:
            return None
        name = {">": "gt", ">=": "gte", "<": "lt", "<=": "lte"}[cast(str, op)]
        return Bounds(**{name: typed_value}), negate

    def _no_range(self, descriptor: ColumnDescriptor, at: Position) -> None:
        self.refuse(
            RefusalCode.RANGE_NOT_ALLOWED,
            at,
            text(
                "Ranges apply to numbers, dates, datetimes, time offsets and ordered categories; "
            ),
            text("not to "),
            data(descriptor.id),
            text(f", a {descriptor.fields.datatype} column"),
        )

    def _units(
        self, leaf: ValueLeaf, at: Position, descriptor: ColumnDescriptor
    ) -> tuple[bool, str | None]:
        """Whether the units are accepted, and the predicate's units: given, or the column's
        (§6.4)."""
        numeric = descriptor.fields.datatype in _NUMERIC
        column_units = descriptor.fields.units
        if leaf.units is None:
            return True, column_units if numeric else None
        if not numeric:
            self.refuse(
                RefusalCode.MEMBER_NOT_APPLICABLE,
                (*at, "units"),
                text("units apply to number, integer and time_offset columns; "),
                data(descriptor.id),
                text(f" is a {descriptor.fields.datatype} column"),
            )
            return False, None
        if column_units is None:
            self.refuse(
                RefusalCode.UNITS_UNCONVERTIBLE,
                (*at, "units"),
                text("The column's units are undeclared, so no conversion exists from "),
                data(leaf.units),
                text(": "),
                data(descriptor.id),
            )
            return False, None
        if not convertible(leaf.units, column_units):
            self.refuse(
                RefusalCode.UNITS_UNCONVERTIBLE,
                (*at, "units"),
                text("No conversion from "),
                data(leaf.units),
                text(" to the column's units, "),
                data(column_units),
            )
            return False, None
        return True, leaf.units

    def _match(
        self, leaf: ValueLeaf, at: Position, descriptor: ColumnDescriptor
    ) -> tuple[bool, Literal["any", "all"] | None]:
        """Whether ``match`` is accepted, and its value: for list columns only (§6.4)."""
        if descriptor.fields.datatype == "list<category>":
            return True, leaf.match or "any"
        if leaf.match is not None:
            self.refuse(
                RefusalCode.MEMBER_NOT_APPLICABLE,
                (*at, "match"),
                text("match applies to list columns; "),
                data(descriptor.id),
                text(" holds one value per row"),
            )
            return False, None
        return True, None

    def _exists(
        self, context: _Cohort, leaf: ExistsLeaf, at: Position, table: str
    ) -> RClause | None:
        target = self._table(context, leaf.table, (*at, "table"))
        if target is None:
            return None
        path = self._path(context, leaf.via, at, table, target, "table")
        if path is None:
            return None
        where_at: Position = (*at, "where")
        written = (*at, "via") if leaf.via is not None else (*at, "table")
        if not path:
            self.refuse(
                RefusalCode.INVALID_PATH,
                written,
                text("An existence question goes down at least one step; the table is the one "),
                text("the question is asked from, "),
                data(table),
            )
            return None
        if not down_steps(path):
            self.refuse(
                RefusalCode.INVALID_PATH,
                written,
                text(
                    "An existence question goes down at least one step; this path only looks up: "
                ),
                data(render(path)),
            )
            return None
        trailing = _trailing(path)
        if trailing and not leaf.where:
            self.refuse(
                RefusalCode.INVALID_PATH,
                written,
                text("A path that ends with an up step needs a where, which those lookups serve"),
            )
            return None
        quantifiers = self._quantifiers(leaf, at, path, leaf.min_count)
        if quantifiers is None:
            return None
        first = next(index for index, step in enumerate(path) if step.dir == "down")
        if leaf.exclude_self and context.graph.follow(table, path)[first] != table:
            self.refuse(
                RefusalCode.EXCLUDE_SELF_NOT_ALLOWED,
                (*at, "exclude_self"),
                text("exclude_self leaves out the row the path started from, so the first down "),
                text("step enters that row's table, "),
                data(table),
            )
            return None
        if leaf.exclude_self and context.served:
            # Step 5 would start the question at the row before the lookups (D211).
            self.refuse(
                RefusalCode.EXCLUDE_SELF_NOT_ALLOWED,
                (*at, "exclude_self"),
                text("exclude_self is not allowed in a where that a path's trailing lookups "),
                text("serve: canonicalisation asks the question from the row before the "),
                text("lookups, so it would leave out another row. Ask the outer question "),
                text("without the lookups, and look up in this question's via"),
            )
            return None
        served = context.served
        context.served = bool(trailing)
        try:
            where, failed = self._resolved(context, leaf.where or [], where_at, target, True)
        finally:
            context.served = served
        lift = leaf.lift or "strict"
        chained = self._chain(
            context, path, quantifiers, lift, leaf.exclude_self, conjuncts(where), at, table
        )
        # With a member refused, the others are still checked against the coverage.
        return None if failed else chained

    def _chain(
        self,
        context: _Cohort,
        path: Path,
        quantifiers: Sequence[tuple[Quantifier, int | None]],
        lift: Lift,
        exclude_self: bool,
        where: tuple[RClause, ...],
        at: Position,
        table: str,
    ) -> RClause | None:
        """A chain of single-step ``exists`` for a path, the last one's ``where`` holding
        ``where`` with the trailing lookups prefixed (§7.6, step 5)."""
        segments: list[tuple[Path, Step]] = []
        ups: list[Step] = []
        for step in path:
            if step.dir == "up":
                ups.append(step)
            else:
                segments.append((tuple(ups), step))
                ups = []
        reached = context.graph.follow(table, path)
        children = [
            cast(str, table_reached)
            for step, table_reached in zip(path, reached, strict=True)
            if step.dir == "down"
        ]
        node: RClause | None = None
        clauses = prefixed(tuple(ups), where)
        origin = frozenset({at}) | origin_of(where)
        for index in range(len(segments) - 1, -1, -1):
            before, down = segments[index]
            quantifier, count = quantifiers[index]
            inner = clauses if node is None else (node,)
            question = RExists(
                table=children[index],
                via=(*before, down),
                quantifier=quantifier,
                where=inner,
                min_count=count if quantifier == "some" else None,
                lift=lift if intermediate(inner) else None,
                exclude_self=exclude_self and index == 0,
                origin=origin,
            )
            coverage = self._coverage(context, down.rel, at)
            if coverage is None or not self._mentions(context, question, coverage):
                return None
            node = question
        self._read_path(context, path)
        return node

    def _covered(
        self, context: _Cohort, leaf: CoveredLeaf, at: Position, table: str
    ) -> RClause | None:
        target = self._table(context, leaf.table, (*at, "table"))
        if target is None:
            return None
        path = self._path(context, leaf.via, at, table, target, "table")
        if path is None:
            return None
        if not path or path[-1].dir != "down":
            self.refuse(
                RefusalCode.INVALID_PATH,
                (*at, "via") if leaf.via is not None else (*at, "table"),
                text("covered asks about a down step, so its path ends with one: "),
                data(render(path)),
            )
            return None
        coverages: list[Coverage] = []
        for step in path:
            if step.dir == "down":
                coverage = self._coverage(context, step.rel, at)
                if coverage is None:
                    return None
                coverages.append(coverage)
        last = coverages[-1]
        scope: list[tuple[str, tuple[Constant, ...]]] | None = None
        if leaf.scope is not None:
            scope = []
            failed = False
            for column, values in leaf.scope.items():
                if column not in last.scope_columns:
                    self.refuse(
                        RefusalCode.UNKNOWN_SCOPE_COLUMN,
                        (*at, "scope", column),
                        text("Not a scope column of the coverage of "),
                        data(last.relationship),
                        text(": "),
                        data(column),
                        alternatives=self.listed(list(last.scope_columns)),
                    )
                    failed = True
                    continue
                descriptor = context.release.column(target, column)
                if descriptor is None or descriptor.fields.datatype is None:
                    self.refuse(
                        RefusalCode.UNDECLARED_DATATYPE,
                        (*at, "scope", column),
                        text("The scope column's datatype is undeclared: "),
                        data(f"{target}.{column}"),
                    )
                    failed = True
                    continue
                self._read_column(context, descriptor)
                typed = [
                    self._constant(value, descriptor, (*at, "scope", column, index))
                    for index, value in enumerate(values)
                ]
                if any(value is None for value in typed):
                    failed = True
                    continue
                scope.append((column, tuple(cast(list[Constant], typed))))
            if failed:
                return None
        self._read_path(context, path)
        lift = (leaf.lift or "strict") if down_steps(path) > 1 else None
        return RCovered(
            target, path, None if scope is None else tuple(scope), lift, frozenset({at})
        )

    def _ids(self, context: _Cohort, leaf: IdsLeaf, at: Position) -> RClause | None:
        release = context.release
        if not self._row_ids(context):
            self.refuse(
                RefusalCode.ROW_IDS_NOT_ALLOWED,
                at,
                text("The dataset does not allow row ids, so ids leaves are refused (§8.4)"),
            )
            return None
        columns = release.primary_key(context.unit) or ()
        descriptors = [release.column(context.unit, column) for column in columns]
        undeclared = [
            f"{context.unit}.{column}"
            for column, descriptor in zip(columns, descriptors, strict=True)
            if descriptor is None or descriptor.fields.datatype is None
        ]
        if undeclared:
            self.refuse(
                RefusalCode.UNDECLARED_DATATYPE,
                (*at, "ids"),
                text("The datatype of the unit's key is undeclared, so its keys cannot be typed: "),
                *[data(name) for name in undeclared],
            )
            return None
        for descriptor in descriptors:
            assert descriptor is not None
            self._read_column(context, descriptor)
        datatypes = [
            cast(str, descriptor.fields.datatype) for descriptor in descriptors if descriptor
        ]
        keys: list[tuple[str, tuple[Constant, ...]]] = []
        failed = False
        for index, member in enumerate(leaf.ids):
            member_at: Position = (*at, "ids", index)
            if isinstance(member, UnitKey):
                dataset, given = member.dataset, list(member.key)
                typed: list[Constant | None] = (
                    [
                        typed_constant(part, datatype)
                        for part, datatype in zip(given, datatypes, strict=True)
                    ]
                    if len(given) == len(columns)
                    else []
                )
            else:
                dataset, _, key = member.partition(":")
                if len(columns) != 1:
                    self.refuse(
                        RefusalCode.INVALID_KEY,
                        member_at,
                        text("The text form is for single-column keys; the unit's key has "),
                        text(f'{len(columns)} columns, so give {{"dataset", "key": [...]}}'),
                        alternatives=[data(column) for column in columns],
                    )
                    failed = True
                    continue
                typed = [_key_constant(key, datatypes[0])]
            if dataset != release.dataset:
                self.refuse(
                    RefusalCode.UNKNOWN_DATASET,
                    member_at,
                    text("A unit key of this cohort is in dataset "),
                    data(release.dataset),
                    text(", not "),
                    data(dataset),
                )
                failed = True
                continue
            if len(typed) != len(columns) or any(part is None for part in typed):
                self.refuse(
                    RefusalCode.INVALID_KEY,
                    member_at,
                    text("A unit key has one value of its column's type per key column, in key "),
                    text("order"),
                    alternatives=[data(column) for column in columns],
                )
                failed = True
                continue
            keys.append((release.manifest, tuple(cast(list[Constant], typed))))
        if failed:
            return None
        return RIds(tuple(keys), frozenset({at}))

    # --- Coverage (§5.6, §6.5) ---------------------------------------------------------------

    def _coverage(self, context: _Cohort, relationship: str, at: Position) -> Coverage | None:
        found = context.coverage.get(relationship)
        if found is not None:
            return found
        release = context.release
        link = release.relationship(relationship)
        assert link is not None, "a path's steps are the release's relationships"
        descriptor = release.coverage(relationship)
        coverage_id = "cov:" + relationship.removeprefix("rel:")
        parents = None if descriptor is None else descriptor.fields.parents
        # A proposed parents is reported through COVERAGE_PROPOSED only (§5.1).
        self._read(context, descriptor, coverage_id, "/fields/parents", absent_counts=True)
        status = None
        if descriptor is not None and "/fields/parents" in descriptor.curation:
            status = descriptor.curation["/fields/parents"].status
        form: Literal["undeclared", "all", "direct", "grouped"] = "undeclared"
        scope_columns: tuple[str, ...] = ()
        if parents == "all":
            form = "all"
        elif isinstance(parents, DirectCoverage):
            form = "direct"
            scope_columns = tuple((parents.scope_columns or {}).values())
        elif isinstance(parents, GroupedCoverage):
            form = "grouped"
            scope_columns = tuple((parents.groups.scope_columns or {}).values())
        record_filter: tuple[tuple[str, frozenset[str]], ...] = ()
        parent_scope: RClause | None = None
        if descriptor is not None:
            filters = descriptor.fields.record_filter
            if filters is not None:
                self._read(context, descriptor, coverage_id, "/fields/record_filter")
                for column in sorted(filters):
                    filtered = release.column(link.fields.child_table, column)
                    if filtered is not None:
                        self._read_column(context, filtered)
                record_filter = tuple(
                    (column, frozenset(values)) for column, values in sorted(filters.items())
                )
            if descriptor.fields.parent_scope is not None:
                self._read(context, descriptor, coverage_id, "/fields/parent_scope")
                parent_scope = self._parent_scope(context, descriptor, link.fields.parent_table, at)
                if parent_scope is None:
                    return None
        coverage = Coverage(
            relationship,
            link.fields.parent_table,
            link.fields.child_table,
            form,
            status == "proposed",
            parent_scope,
            record_filter,
            scope_columns,
            descriptor,
        )
        context.coverage[relationship] = coverage
        return coverage

    def _parent_scope(
        self, context: _Cohort, descriptor: CoverageDescriptor, table: str, at: Position
    ) -> RClause | None:
        """A coverage's parent scope, resolved on the parent table; its first problem is
        reported at the leaf that asks about the relationship."""
        resolved, refusals, fields = self.scope_of(context, descriptor, table)
        if refusals:
            cause = refusals[0]
            self.refuse(
                cause.code if isinstance(cause.code, RefusalCode) else RefusalCode.INVALID_VALUE,
                at,
                text("The parent_scope of "),
                data(descriptor.id),
                text(" does not resolve against the release, at "),
                data(cause.path or ""),
                text(": "),
                *cause.message,
                alternatives=cause.alternatives,
                limit=cause.limit,
            )
            return None
        context.fields.update(fields)
        return resolved

    @staticmethod
    def scope_of(
        context: "_Cohort", descriptor: CoverageDescriptor, table: str
    ) -> tuple[RClause | None, list[Refusal], set[FieldRead]]:
        """A coverage's parent scope resolved on its parent table, the refusals of its leaves (paths
        from ``/parent_scope``) and the fields resolving it read. In v1 a parent scope holds value
        predicates on the parent row or rows it looks up, and combinators (D207)."""
        clause = descriptor.fields.parent_scope
        assert clause is not None
        inner = _Resolver(None, {}, {})
        scope_context = _Cohort(
            context.name, context.dataset, context.release, context.graph, table, curated=True
        )
        top: Position = ("parent_scope",)
        pending: list[tuple[ClauseModel, Position]] = [(clause, top)]
        while pending:
            current, at = pending.pop()
            if isinstance(current, ExistsLeaf | CoveredLeaf | IdsLeaf | CohortLeaf | PackLeaf):
                inner.refuse(
                    RefusalCode.NOT_SUPPORTED,
                    at,
                    text("In v1 a parent scope holds value predicates and combinators; the leaf "),
                    data(current.kind),
                    text(" asks a question"),
                )
            elif isinstance(current, AllClause | AnyClause):
                key, members = (
                    ("all", current.all)
                    if isinstance(current, AllClause)
                    else (
                        "any",
                        current.any,
                    )
                )
                pending.extend((member, (*at, key, index)) for index, member in enumerate(members))
            elif isinstance(current, NotClause):
                pending.append((current.not_, (*at, "not")))
            elif isinstance(current, KnownClause):
                pending.append((current.known, (*at, "known")))
            elif isinstance(current, UnknownClause):
                pending.append((current.unknown, (*at, "unknown")))
        if inner.refusals:
            return None, finish_refusals(inner.refusals), set()
        resolved = inner._clause(scope_context, clause, top, table, False)
        refusals = finish_refusals(inner.refusals)
        return (None if refusals else resolved), refusals, scope_context.fields

    def _mentions(self, context: _Cohort, question: RExists, coverage: Coverage) -> bool:
        """§6.5's rules on what ``W_C`` may say about filtered and scope columns: a column is
        mentioned by a value leaf on the child row itself (no ``via``), outside nested
        questions. They read the ``where`` as step 8 of §7.6 leaves it, duplicates removed,
        as evaluation does: ``any: [x, x]`` is ``x`` (D205)."""
        ok = True
        allowed = dict(coverage.record_filter)
        scope = set(coverage.scope_columns)
        table = coverage.child_table
        for leaf, top in value_leaves(context.deduplicated.conjunction(question.where)):
            if leaf.via:
                continue
            owner, column = leaf.column.split(".", 1)
            if owner != table:
                continue
            simple = top and isinstance(leaf.predicate, Values) and not leaf.negate
            at = min(leaf.origin, key=_in_document_order, default=())
            if column in allowed:
                values = leaf.predicate.values if isinstance(leaf.predicate, Values) else ()
                if not simple or not set(cast(tuple[str, ...], values)) <= allowed[column]:
                    self.refuse(
                        RefusalCode.FILTER_COLUMN_MENTION,
                        at,
                        text("The coverage of "),
                        data(coverage.relationship),
                        text(" says the table holds only some kinds of rows in "),
                        data(column),
                        text("; ask about them only in a top-level values conjunct without "),
                        text("negate, with the values listed"),
                        alternatives=self.listed(sorted(allowed[column])),
                    )
                    ok = False
            if column in scope:
                if question.quantifier == "every":
                    self.refuse(
                        RefusalCode.SCOPE_COLUMN_MENTION,
                        at,
                        text("every over "),
                        data(coverage.relationship),
                        text(" may not mention its scope column "),
                        data(column),
                        text(" (§6.5)"),
                    )
                    ok = False
                elif not simple:
                    self.refuse(
                        RefusalCode.SCOPE_COLUMN_MENTION,
                        at,
                        text("The scope column "),
                        data(column),
                        text(" of "),
                        data(coverage.relationship),
                        text(" may be mentioned only in a top-level values conjunct without "),
                        text("negate, such as a list of its values (§6.5)"),
                    )
                    ok = False
        return ok


def _in_document_order(position: Position) -> tuple[tuple[int, int, str], ...]:
    """A sort key that puts positions in the order of the document as written: list indexes
    as numbers, so that ``10`` comes after ``9``."""
    return tuple((0, part, "") if isinstance(part, int) else (1, 0, part) for part in position)


def _interned(leaves: dict[Position, list[int]]) -> dict[Position, frozenset[int]]:
    """The leaf map with equal sets shared: the leaves of a cohort inlined many times map to
    the same clauses, and one set of them is kept."""
    shared: dict[frozenset[int], frozenset[int]] = {}
    interned: dict[Position, frozenset[int]] = {}
    for position, found in leaves.items():
        clauses = frozenset(found)
        interned[position] = shared.setdefault(clauses, clauses)
    return interned


def _trailing(path: Path) -> Path:
    """The up steps after the last down step."""
    last = max(index for index, step in enumerate(path) if step.dir == "down")
    return path[last + 1 :]


def _quantifier(given: object) -> tuple[Quantifier, int | None]:
    if isinstance(given, SomeAtLeast):
        return "some", given.some
    return ("every", None) if given == "every" else ("some", 1)


__all__ = [
    "UNCONFIRMED",
    "Coverage",
    "FieldRead",
    "Resolution",
    "ResolvedCohort",
    "check_parent_scopes",
    "resolve",
    "typed_constant",
]

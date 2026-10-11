"""The validation gate: the structural checks that need the data (SPEC §13.2, D230).

The checks that need only the descriptors are ``check_release``'s (§4, §5, D199), and run again
here on what is left after the drops below. The checks on the typed tables are:

- a primary-key cell that is not PRESENT (``KEY_NULL``), and two rows with the same key
  (``KEY_NOT_UNIQUE``), keys compared as the evaluator compares them, booleans apart from the
  numbers Python equates with them;
- a relationship's parent columns, whether or not they are the parent's declared key, unique
  among the rows where they are all PRESENT (``KEY_NOT_UNIQUE``), since a child finds one
  parent;
- a child whose foreign-key cells are all PRESENT and match no parent row
  (``DANGLING_REFERENCE``); a foreign key with a cell that is not PRESENT is null, which is
  allowed (§5.5);
- two children of a one-to-one relationship with the same parent (``CARDINALITY_VIOLATED``);
- in coverage, assignment and group tables, a cell that is not PRESENT in a column the coverage
  names (``COVERAGE_NULL``), and a row naming a parent or a group that does not exist
  (``COVERAGE_UNKNOWN``);
- in a column a ``record_filter`` names, a PRESENT value it does not list
  (``OUTSIDE_RECORD_FILTER``), and a NOT_APPLICABLE cell (``NOT_APPLICABLE_IN_FILTER``).

A refusal points at the descriptor field that fails and gives a count and the first five rows,
counted from 1, never a value (A6). At import (``mode="import"``), a failing proposal is dropped
instead of refused (§13.2): a ``primary_key`` (with a proposed ``grain``, which names it, and a
proposed ``role`` of ``link`` or ``entity``, which D229 gives only to a table with a key), a
coverage's ``parents`` or ``record_filter`` whose status is ``proposed``, or a relationship
whose tables and columns are all ``proposed``, with its coverage. Drops cascade: the checks run
again on what is left, until nothing more is dropped, so that a dropped key which leaves a
relationship without a unique parent drops that relationship too if it is proposed. On a draft
change (``mode="change"``) every failure refuses, whatever its status (§12.3).

Semantic gaps are reported and never refuse (§13.2): child rows outside the coverage their
relationship lists (kept, since they are evidence, §6.5), and endpoint rows whose status is
outside ``event_coding``, whose time is negative or whose entry is at or after their time (§5.8).
"""

import hashlib
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol, cast

from pydantic import JsonValue, TypeAdapter

from aibi.core.engine.data import PRESENT, Cell, KeyPart, key_part
from aibi.core.schema.descriptors import (
    ColumnDescriptor,
    CoverageDescriptor,
    Descriptor,
    DirectCoverage,
    EndpointDescriptor,
    EntryColumn,
    GroupedCoverage,
    RelationshipDescriptor,
    TableDescriptor,
)
from aibi.core.schema.jsonio import canonical, pointer
from aibi.core.schema.output import Message, text
from aibi.core.schema.pack_api import cell_digest
from aibi.core.schema.refusals import Refusal, RefusalCode, finish_refusals
from aibi.core.schema.release import check_release
from aibi.core.schema.semantics import ObservationState
from aibi.core.store.manifest import Declared

Mode = Literal["import", "change"]
REFERENCED_ROWS = 5
"""Rows a failure or a gap refers to, at most."""

_ADAPTER: TypeAdapter[Descriptor] = TypeAdapter(Descriptor)
_KEY = "/fields/primary_key"
_GRAIN = "/fields/grain"
_ROLE = "/fields/role"
_KEYED_ROLES = frozenset({"link", "entity"})
"""The roles D229 proposes only for a table with a key."""
_RELATIONSHIP_KEYS = (
    "/fields/child_table",
    "/fields/child_columns",
    "/fields/parent_table",
    "/fields/parent_columns",
)


class Cells(Protocol):
    """A typed table as the gate reads it: its row count and the cells of some of its columns."""

    @property
    def rows(self) -> int: ...

    @property
    def cells(self) -> Mapping[str, Sequence[Cell]]: ...


@dataclass(frozen=True)
class Dropped:
    """A proposal dropped at import: a field (``pointer``) or, without one, a descriptor."""

    descriptor: str
    pointer: str | None
    code: RefusalCode
    message: Message
    """The server's sentence: the gate names no id or value in it, which the note gives as its
    subject and evidence (D397)."""
    count: int
    rows: tuple[int, ...]
    evidence: str | None
    """The proposal's own evidence, from its curation entry."""


@dataclass(frozen=True)
class Gap:
    kind: Literal["outside_coverage", "invalid_endpoint_rows"]
    subject: str
    """The coverage or endpoint descriptor's id."""
    count: int
    rows: tuple[int, ...]


@dataclass(frozen=True)
class GateResult:
    descriptors: tuple[Descriptor, ...]
    """The descriptors given, less what was dropped."""
    dropped: tuple[Dropped, ...]
    gaps: tuple[Gap, ...]
    refusals: tuple[Refusal, ...]
    """The structural errors that stop the import or refuse the change; paths point into the
    descriptors given."""


@dataclass(frozen=True)
class _Failure:
    code: RefusalCode
    at: int
    where: tuple[str | int, ...]
    message: Message
    count: int
    rows: tuple[int, ...]
    drop: str | None = None
    """What an import drops instead of refusing: the pointer of a proposed field, or ``""``
    for the whole descriptor; ``None`` when the failure is not a proposal's."""


def needed(descriptors: Iterable[Descriptor]) -> dict[str, set[str]]:
    """The columns the gate reads, by table: keys, relationship columns, the columns coverages
    name in their tables and in the parent and child, record-filter columns and endpoint
    columns. A builder keeps these in memory and drops the rest."""
    found: dict[str, set[str]] = {}

    def add(table: str, columns: Iterable[str]) -> None:
        found.setdefault(table, set()).update(columns)

    descriptors = list(descriptors)
    relationships = {d.id: d for d in descriptors if isinstance(d, RelationshipDescriptor)}
    for descriptor in descriptors:
        if isinstance(descriptor, TableDescriptor):
            add(descriptor.id, descriptor.fields.primary_key or ())
        elif isinstance(descriptor, RelationshipDescriptor):
            fields = descriptor.fields
            add(fields.child_table, fields.child_columns)
            add(fields.parent_table, fields.parent_columns)
        elif isinstance(descriptor, CoverageDescriptor):
            coverage = descriptor.fields
            relationship = relationships.get(coverage.relationship)
            child = None if relationship is None else relationship.fields.child_table
            parent = None if relationship is None else relationship.fields.parent_table
            parents = coverage.parents
            scopes: Mapping[str, str] = {}
            if isinstance(parents, DirectCoverage):
                add(parents.table, parents.parent_columns)
                add(parents.table, parents.scope_columns or {})
                if parent is not None:
                    add(parent, parents.parent_columns.values())
                scopes = parents.scope_columns or {}
            elif isinstance(parents, GroupedCoverage):
                assignment, groups = parents.assignment, parents.groups
                add(assignment.table, [*assignment.parent_columns, assignment.group_column])
                add(groups.table, [groups.group_column, *(groups.scope_columns or {})])
                if groups.covers_all_column is not None:
                    add(groups.table, [groups.covers_all_column])
                if parent is not None:
                    add(parent, assignment.parent_columns.values())
                scopes = groups.scope_columns or {}
            if child is not None:
                add(child, scopes.values())
                add(child, coverage.record_filter or {})
        elif isinstance(descriptor, ColumnDescriptor) and descriptor.fields.absent is not None:
            table, column = descriptor.id.split(".", 1)
            add(table, [column])
        elif isinstance(descriptor, EndpointDescriptor):
            endpoint = descriptor.fields
            if endpoint.table is not None:
                add(
                    endpoint.table, [c for c in (endpoint.time_column, endpoint.status_column) if c]
                )
                if isinstance(endpoint.entry, EntryColumn):
                    add(endpoint.table, [endpoint.entry.column])
    return found


CHECK_VERSION = "aibi.reshaped-coverage/1"
"""The version of the coverage's check against a declaration (``_Checks.declarations``), digested
with its inputs: bumped whenever that check's logic changes, or a helper it shares with the gate
(``tuples``, ``_part``, ``key_part``, ``cell_digest``, ``decode_cells``), so that a release checked
by an earlier version is checked again at its next change (D401)."""


def declaration_inputs(
    table: str,
    declaration: Declared,
    descriptors: Sequence[Descriptor],
    hashes: Mapping[str, str],
) -> str:
    """The digest of what the check of ``table``'s coverage against ``declaration`` reads
    (``_Checks.declarations``), as RFC 8785 JSON: the check's version; the declaration; the
    table's id and key; the ids of its columns with absent values, with those values; each
    relationship into it (its id, child table and columns, parent columns); each of their
    coverages (its id, relationship and ``parents``); and the content hash of the table and of
    each coverage table. The typed cells it compares are in those tables, whose hashes change
    with their content. The check is a function of these alone, so a build whose digest is the
    one the release kept from its last passing check has the same outcome; any other field (a
    label, a category's label, ``completeness``, ``grain``) does not run it again. The table is
    read by its whole content hash, not by its key cells alone, so a change that rebuilds it (a
    parse field, a derived column on it, D220) runs the check again (D401)."""
    described = next(
        (d for d in descriptors if isinstance(d, TableDescriptor) and d.id == table), None
    )
    absent = sorted(
        [d.id, list(d.fields.absent)]
        for d in descriptors
        if isinstance(d, ColumnDescriptor)
        and d.id.split(".", 1)[0] == table
        and d.fields.absent is not None
    )
    relationships = sorted(
        (
            d
            for d in descriptors
            if isinstance(d, RelationshipDescriptor) and d.fields.child_table == table
        ),
        key=lambda d: d.id,
    )
    ids = {relationship.id for relationship in relationships}
    coverages = sorted(
        (
            d
            for d in descriptors
            if isinstance(d, CoverageDescriptor) and d.fields.relationship in ids
        ),
        key=lambda d: d.id,
    )
    read: set[str] = {table}
    for coverage in coverages:
        parents = coverage.fields.parents
        if isinstance(parents, DirectCoverage):
            read.add(parents.table)
        elif isinstance(parents, GroupedCoverage):
            read.update((parents.assignment.table, parents.groups.table))
    given: JsonValue = {
        "check": CHECK_VERSION,
        "declaration": cast(JsonValue, declaration.model_dump(mode="json", exclude={"checked"})),
        "table": table,
        "key": (
            None
            if described is None or described.fields.primary_key is None
            else list(described.fields.primary_key)
        ),
        "absent": cast(JsonValue, absent),
        "relationships": [
            {
                "id": d.id,
                "child_table": d.fields.child_table,
                "child_columns": list(d.fields.child_columns),
                "parent_columns": list(d.fields.parent_columns),
            }
            for d in relationships
        ],
        "coverages": [
            {
                "id": d.id,
                "relationship": d.fields.relationship,
                "parents": cast(JsonValue, d.model_dump(mode="json")["fields"].get("parents")),
            }
            for d in coverages
        ],
        "tables": {name: hashes.get(name) for name in sorted(read)},
    }
    return hashlib.sha256(canonical(given)).hexdigest()


def check(
    descriptors: Sequence[Descriptor],
    tables: Mapping[str, Cells],
    *,
    mode: Mode,
    declared: Mapping[str, Declared] | None = None,
) -> GateResult:
    """The gate over the descriptors of a release and its typed tables, by table id.
    ``declared`` holds what a pack's importer dropped from each table it unpivoted, as the
    import declared it and the release keeps it, which the coverage of each such table is
    checked against at an import and at every change whose inputs to that check differ from
    those of its last passing check (``declaration_inputs``, D401)."""
    current = list(descriptors)
    positions = list(range(len(descriptors)))
    dropped: list[Dropped] = []
    while True:
        failures = _Checks(current, tables).failures()
        drops = [f for f in failures if mode == "import" and f.drop is not None]
        if not drops:
            break
        current, positions = _drop(current, positions, drops, dropped)
    if not failures:
        checks = _Checks(current, tables)
        checks.absent_rows()
        checks.declarations(declared or {})
        failures = checks.found
    refusals = [_refusal(f.code, [positions[f.at], *f.where], f.message) for f in failures]
    if not refusals:
        refusals = [_moved(refusal, positions) for refusal in check_release(current)]
    gaps = () if refusals else tuple(_Checks(current, tables).gaps())
    return GateResult(
        tuple(current), tuple(dropped), gaps, tuple(finish_refusals(refusals) if refusals else ())
    )


def _refusal(code: RefusalCode, path: Sequence[str | int], message: Message) -> Refusal:
    return Refusal(code=code, path=pointer(path), message=list(message))


def _moved(refusal: Refusal, positions: Sequence[int]) -> Refusal:
    """A refusal into the descriptors left, pointed into the descriptors given."""
    if refusal.path is None:
        return refusal
    tokens = refusal.path.split("/")
    if len(tokens) > 1 and tokens[1].isdigit():
        tokens[1] = str(positions[int(tokens[1])])
    return refusal.model_copy(update={"path": "/".join(tokens)})


def _drop(
    current: list[Descriptor],
    positions: list[int],
    drops: Sequence[_Failure],
    dropped: list[Dropped],
) -> tuple[list[Descriptor], list[int]]:
    """The descriptors without the failing proposals, and the positions of those kept."""
    whole: set[str] = set()
    fields: dict[int, set[str]] = {}
    for failure in drops:
        descriptor = current[failure.at]
        assert failure.drop is not None
        if failure.drop:
            if descriptor.id in whole or failure.drop in fields.get(failure.at, set()):
                continue
            fields.setdefault(failure.at, set()).add(failure.drop)
            evidence = descriptor.curation[failure.drop].evidence
        elif descriptor.id in whole:
            continue
        else:
            whole.add(descriptor.id)
            evidence = _evidence(descriptor)
        dropped.append(
            Dropped(
                descriptor.id,
                failure.drop or None,
                failure.code,
                failure.message,
                failure.count,
                failure.rows,
                evidence,
            )
        )
        if failure.drop == _KEY and isinstance(descriptor, TableDescriptor):
            derived = (
                (_GRAIN, descriptor.fields.grain is not None, _KEY_NAMED),
                (_ROLE, descriptor.fields.role in _KEYED_ROLES, _KEY_RESTED),
            )
            for field, applies, message in derived:
                if applies and _proposed(descriptor, field):
                    fields[failure.at].add(field)
                    evidence = descriptor.curation[field].evidence
                    dropped.append(
                        Dropped(descriptor.id, field, failure.code, message, 0, (), evidence)
                    )
    for descriptor in current:
        if (
            isinstance(descriptor, CoverageDescriptor)
            and descriptor.fields.relationship in whole
            and descriptor.id not in whole
        ):
            whole.add(descriptor.id)
            dropped.append(
                Dropped(
                    descriptor.id,
                    None,
                    RefusalCode.UNKNOWN_DESCRIPTOR,
                    (text("Its relationship was dropped"),),
                    0,
                    (),
                    _evidence(descriptor),
                )
            )
    kept: list[Descriptor] = []
    kept_positions: list[int] = []
    for index, descriptor in enumerate(current):
        if descriptor.id in whole:
            continue
        for field in sorted(fields.get(index, ())):
            descriptor = _without(descriptor, field)
        kept.append(descriptor)
        kept_positions.append(positions[index])
    return kept, kept_positions


_KEY_NAMED: Message = (text("The key it names was dropped"),)
_KEY_RESTED: Message = (text("The key it rests on was dropped"),)


def _evidence(descriptor: Descriptor) -> str | None:
    found = [entry.evidence for _, entry in sorted(descriptor.curation.items()) if entry.evidence]
    return " ".join(found) or None


def _without(descriptor: Descriptor, field: str) -> Descriptor:
    """The descriptor without one of its fields, ``/fields/<name>``, and its curation entry."""
    dumped = cast(dict[str, JsonValue], descriptor.model_dump(mode="json"))
    members = cast(dict[str, JsonValue], dumped["fields"])
    del members[field.removeprefix("/fields/")]
    del cast(dict[str, JsonValue], dumped["curation"])[field]
    return _ADAPTER.validate_python(dumped)


def _proposed(descriptor: Descriptor, *pointers: str) -> bool:
    entries = [descriptor.curation.get(field) for field in pointers]
    return all(entry is not None and entry.status == "proposed" for entry in entries)


def _rows_text(rows: Sequence[int], count: int) -> str:
    listed = ", ".join(str(row) for row in rows)
    more = ", …" if count > len(rows) else ""
    if count == 1:
        return f"1 row (row {listed})"
    return f"{count} rows (rows {listed}{more})"


class _Rows:
    """Rows found by a check: how many, and the first ``REFERENCED_ROWS``, counted from 1."""

    def __init__(self) -> None:
        self.count = 0
        self.first: list[int] = []

    def add(self, row: int) -> None:
        self.count += 1
        if len(self.first) < REFERENCED_ROWS:
            self.first.append(row + 1)

    def __bool__(self) -> bool:
        return self.count > 0

    def text(self) -> str:
        return _rows_text(self.first, self.count)


Tuple = tuple[KeyPart, ...]


def _part(cell: Cell) -> KeyPart | None:
    if cell.state is not PRESENT or cell.value is None or isinstance(cell.value, tuple):
        return None
    return key_part(cell.value)


def _json_key(value: object) -> tuple[str, object] | None:
    """A status value as event codes compare it: numbers by value, strings and booleans apart."""
    if isinstance(value, bool):
        return ("boolean", value)
    if isinstance(value, int | float):
        return ("number", float(value))
    if isinstance(value, str):
        return ("string", value)
    return None


class _Checks:
    def __init__(self, descriptors: Sequence[Descriptor], tables: Mapping[str, Cells]) -> None:
        self.descriptors = descriptors
        self.tables = tables
        self.relationships = {d.id: d for d in descriptors if isinstance(d, RelationshipDescriptor)}
        self.found: list[_Failure] = []

    # --- Reading the tables --------------------------------------------------------------------

    def column(self, table: str, column: str) -> Sequence[Cell] | None:
        found = self.tables.get(table)
        return None if found is None else found.cells.get(column)

    def size(self, table: str) -> int:
        found = self.tables.get(table)
        return 0 if found is None else found.rows

    def tuples(self, table: str, columns: Sequence[str]) -> list[Tuple | None] | None:
        """Each row's values in ``columns``, ``None`` where one is not PRESENT; ``None`` if a
        column is not in memory, and then nothing is checked."""
        read = [self.column(table, column) for column in columns]
        if any(cells is None for cells in read):
            return None
        present = cast(list[Sequence[Cell]], read)
        found: list[Tuple | None] = []
        for row in range(self.size(table)):
            parts = [_part(cells[row]) for cells in present]
            found.append(None if any(p is None for p in parts) else cast(Tuple, tuple(parts)))
        return found

    def fail(
        self,
        code: RefusalCode,
        at: int,
        where: tuple[str | int, ...],
        message: str,
        rows: _Rows,
        drop: str | None,
    ) -> None:
        """A failure at ``where``: ``message`` is the server's own sentence, a literal, which the
        rows found complete; it names no id or value (D397)."""
        said = (text(f"{message} {rows.text()}" if rows else message),)
        self.found.append(_Failure(code, at, where, said, rows.count, tuple(rows.first), drop))

    # --- The structural checks -----------------------------------------------------------------

    def failures(self) -> list[_Failure]:
        for at, descriptor in enumerate(self.descriptors):
            if isinstance(descriptor, TableDescriptor):
                self.key(at, descriptor)
            elif isinstance(descriptor, RelationshipDescriptor):
                self.relationship(at, descriptor)
            elif isinstance(descriptor, CoverageDescriptor):
                self.coverage(at, descriptor)
        return self.found

    def key(self, at: int, descriptor: TableDescriptor) -> None:
        key = descriptor.fields.primary_key
        if not key:
            return
        tuples = self.tuples(descriptor.id, key)
        if tuples is None:
            return
        drop = _KEY if _proposed(descriptor, _KEY) else None
        cells = [cast(Sequence[Cell], self.column(descriptor.id, column)) for column in key]
        nulls, lists, repeated = _Rows(), _Rows(), _Rows()
        seen: set[Tuple] = set()
        for row, found in enumerate(tuples):
            if found is None:
                listed = any(
                    column[row].state is PRESENT and isinstance(column[row].value, tuple)
                    for column in cells
                )
                (lists if listed else nulls).add(row)
            elif found in seen:
                repeated.add(row)
            else:
                seen.add(found)
        where = ("fields", "primary_key")
        if nulls:
            self.fail(RefusalCode.KEY_NULL, at, where, "A key cell is not PRESENT in", nulls, drop)
        if lists:
            message = "A key cell holds a list, which is no key, in"
            self.fail(RefusalCode.KEY_NULL, at, where, message, lists, drop)
        if repeated:
            message = "The key repeats an earlier row's in"
            self.fail(RefusalCode.KEY_NOT_UNIQUE, at, where, message, repeated, drop)

    def relationship(self, at: int, descriptor: RelationshipDescriptor) -> None:
        fields = descriptor.fields
        parents = self.tuples(fields.parent_table, fields.parent_columns)
        children = self.tuples(fields.child_table, fields.child_columns)
        if parents is None or children is None:
            return
        drop = "" if _proposed(descriptor, *_RELATIONSHIP_KEYS) else None
        known: set[Tuple] = set()
        repeated = _Rows()
        for row, found in enumerate(parents):
            if found is None:
                continue
            if found in known:
                repeated.add(row)
            known.add(found)
        if repeated:
            message = "The parent columns are not unique: they repeat an earlier row's in"
            where = ("fields", "parent_columns")
            self.fail(RefusalCode.KEY_NOT_UNIQUE, at, where, message, repeated, drop)
        dangling, shared = _Rows(), _Rows()
        seen: set[Tuple] = set()
        for row, found in enumerate(children):
            if found is None:
                continue
            if found not in known:
                dangling.add(row)
            if found in seen:
                shared.add(row)
            seen.add(found)
        if dangling:
            message = "No parent row has the foreign key of"
            where = ("fields", "child_columns")
            self.fail(RefusalCode.DANGLING_REFERENCE, at, where, message, dangling, drop)
        if shared and fields.cardinality == "one-to-one":
            message = "A one-to-one relationship's parent has another child in"
            where = ("fields", "cardinality")
            self.fail(RefusalCode.CARDINALITY_VIOLATED, at, where, message, shared, drop)

    def coverage(self, at: int, descriptor: CoverageDescriptor) -> None:
        coverage = descriptor.fields
        relationship = self.relationships.get(coverage.relationship)
        if relationship is None:
            return
        parent = relationship.fields.parent_table
        parents = coverage.parents
        drop = "/fields/parents" if _proposed(descriptor, "/fields/parents") else None
        where: tuple[str | int, ...] = ("fields", "parents")
        if isinstance(parents, DirectCoverage):
            self.nulls(at, (*where, "parent_columns"), parents.table, parents.parent_columns, drop)
            self.nulls(
                at, (*where, "scope_columns"), parents.table, parents.scope_columns or {}, drop
            )
            self.unknown_parents(
                at, (*where, "parent_columns"), parents.table, parents.parent_columns, parent, drop
            )
        elif isinstance(parents, GroupedCoverage):
            assignment, groups = parents.assignment, parents.groups
            named = (*where, "assignment")
            self.nulls(
                at, (*named, "parent_columns"), assignment.table, assignment.parent_columns, drop
            )
            self.nulls_in(
                at, (*named, "group_column"), assignment.table, assignment.group_column, drop
            )
            self.unknown_parents(
                at,
                (*named, "parent_columns"),
                assignment.table,
                assignment.parent_columns,
                parent,
                drop,
            )
            listed = (*where, "groups")
            self.nulls_in(at, (*listed, "group_column"), groups.table, groups.group_column, drop)
            self.nulls(
                at, (*listed, "scope_columns"), groups.table, groups.scope_columns or {}, drop
            )
            if groups.covers_all_column is not None:
                self.nulls_in(
                    at, (*listed, "covers_all_column"), groups.table, groups.covers_all_column, drop
                )
            self.unknown_groups(
                at,
                (*named, "group_column"),
                assignment.table,
                assignment.group_column,
                groups.table,
                groups.group_column,
                drop,
            )
        self.record_filter(at, descriptor, relationship.fields.child_table)

    def nulls(
        self,
        at: int,
        where: tuple[str | int, ...],
        table: str,
        columns: Mapping[str, str],
        drop: str | None,
    ) -> None:
        for column in columns:
            self.nulls_in(at, (*where, column), table, column, drop)

    def nulls_in(
        self, at: int, where: tuple[str | int, ...], table: str, column: str, drop: str | None
    ) -> None:
        cells = self.column(table, column)
        if cells is None:
            return
        rows = _Rows()
        for row, cell in enumerate(cells):
            if _part(cell) is None:
                rows.add(row)
        if rows:
            message = "A cell a coverage names is not PRESENT in"
            self.fail(RefusalCode.COVERAGE_NULL, at, where, message, rows, drop)

    def unknown_parents(
        self,
        at: int,
        where: tuple[str | int, ...],
        table: str,
        columns: Mapping[str, str],
        parent: str,
        drop: str | None,
    ) -> None:
        listed = self.tuples(table, list(columns))
        known = self.tuples(parent, list(columns.values()))
        if listed is None or known is None:
            return
        parents = {found for found in known if found is not None}
        rows = _Rows()
        for row, found in enumerate(listed):
            if found is not None and found not in parents:
                rows.add(row)
        if rows:
            message = "No parent row has the parent named in"
            self.fail(RefusalCode.COVERAGE_UNKNOWN, at, where, message, rows, drop)

    def unknown_groups(
        self,
        at: int,
        where: tuple[str | int, ...],
        table: str,
        column: str,
        groups: str,
        group_column: str,
        drop: str | None,
    ) -> None:
        listed = self.tuples(table, [column])
        known = self.tuples(groups, [group_column])
        if listed is None or known is None:
            return
        names = {found for found in known if found is not None}
        rows = _Rows()
        for row, found in enumerate(listed):
            if found is not None and found not in names:
                rows.add(row)
        if rows:
            message = "The group table has no row for the group named in"
            self.fail(RefusalCode.COVERAGE_UNKNOWN, at, where, message, rows, drop)

    def record_filter(self, at: int, descriptor: CoverageDescriptor, child: str) -> None:
        drop = "/fields/record_filter" if _proposed(descriptor, "/fields/record_filter") else None
        for column, values in (descriptor.fields.record_filter or {}).items():
            cells = self.column(child, column)
            if cells is None:
                continue
            allowed = set(values)
            outside, inapplicable = _Rows(), _Rows()
            for row, cell in enumerate(cells):
                if cell.state is PRESENT:
                    if not (isinstance(cell.value, str) and cell.value in allowed):
                        outside.add(row)
                elif cell.state is ObservationState.NOT_APPLICABLE:
                    inapplicable.add(row)
            where = ("fields", "record_filter", column)
            if outside:
                message = "A value the record filter does not list is in"
                self.fail(RefusalCode.OUTSIDE_RECORD_FILTER, at, where, message, outside, drop)
            if inapplicable:
                message = "A filtered column is NOT_APPLICABLE in"
                code = RefusalCode.NOT_APPLICABLE_IN_FILTER
                self.fail(code, at, where, message, inapplicable, drop)

    # --- Reshaped tables (D401) ------------------------------------------------------------------

    def absent_rows(self) -> None:
        """In a column with absent values, every cell PRESENT, none an absent value, and each
        one of the column's permissible values: a missing code can't make a kept cell missing,
        and no kept cell holds what the reshape dropped or a value no question can name."""
        for at, descriptor in enumerate(self.descriptors):
            if not isinstance(descriptor, ColumnDescriptor) or descriptor.fields.absent is None:
                continue
            table, column = descriptor.id.split(".", 1)
            cells = self.column(table, column)
            if cells is None:
                continue
            fields = descriptor.fields
            allowed = (
                None
                if fields.permissible_values is None
                else {entry.value for entry in fields.permissible_values.values}
            )
            absent = set(fields.absent or ())
            missing, outside = _Rows(), _Rows()
            for row, cell in enumerate(cells):
                if cell.state is not PRESENT:
                    missing.add(row)
                elif cell.value in absent or (allowed is not None and cell.value not in allowed):
                    outside.add(row)
            if missing:
                message = (
                    "A cell of a column with absent values is not PRESENT, which a missing code "
                    "made it (D401): set the codes without the values the rows hold, in"
                )
                self.fail(
                    RefusalCode.INVALID_VALUE,
                    at,
                    ("fields", "missing_codes"),
                    message,
                    missing,
                    None,
                )
            if outside:
                message = (
                    "A cell of a column with absent values holds an absent value or one that is "
                    "not permissible (D401), in"
                )
                self.fail(
                    RefusalCode.INVALID_VALUE,
                    at,
                    ("fields", "permissible_values"),
                    message,
                    outside,
                    None,
                )

    def declarations(self, declared: Mapping[str, Declared]) -> None:
        """Each coverage of a relationship into a table a pack unpivoted lists exactly the cells
        its source held: every row's key among the listed cells, and the listed cells without a
        row, the absent cells, as many as the pack dropped and with their digest (D401)."""
        tables = {d.id: d for d in self.descriptors if isinstance(d, TableDescriptor)}
        places = {d.id: at for at, d in enumerate(self.descriptors)}
        columns = {d.id: d for d in self.descriptors if isinstance(d, ColumnDescriptor)}
        for table, declaration in sorted(declared.items()):
            column = columns.get(f"{table}.{declaration.column}")
            if table in places and (column is None or column.fields.absent is None):
                message = "The column the importer declared holds no absent values (D401)"
                self.fail(RefusalCode.INVALID_VALUE, places[table], (), message, _Rows(), None)
        for at, descriptor in enumerate(self.descriptors):
            if not isinstance(descriptor, CoverageDescriptor):
                continue
            relationship = self.relationships.get(descriptor.fields.relationship)
            if relationship is None:
                continue
            child = relationship.fields.child_table
            declaration = declared.get(child)
            table = tables.get(child)
            if declaration is None or table is None:
                continue
            parents = descriptor.fields.parents
            if not isinstance(parents, DirectCoverage | GroupedCoverage):
                continue
            rows = self.tuples(child, table.fields.primary_key or [])
            if rows is None:
                continue
            most = sum(1 for found in rows if found is not None) + declaration.dropped
            problem = self.listed_cells(relationship, parents, table.fields.primary_key or [], most)
            if isinstance(problem, str):
                self.fail(
                    RefusalCode.INVALID_VALUE, at, ("fields", "parents"), problem, _Rows(), None
                )
                continue
            listed = problem
            outside, held = _Rows(), set[Tuple]()
            for row, found in enumerate(rows):
                if found is None:
                    continue
                held.add(found)
                if found not in listed:
                    outside.add(row)
            if outside:
                message = "Rows lie outside the cells the coverage lists (D401), in"
                self.fail(
                    RefusalCode.INVALID_VALUE, at, ("fields", "parents"), message, outside, None
                )
                continue
            missing = listed - held
            digest = cell_digest([str(part[1]) for part in cell] for cell in missing)
            if len(missing) != declaration.dropped or digest != declaration.digest:
                counted = (
                    f"{len(missing)} listed, {declaration.dropped} dropped"
                    if len(missing) != declaration.dropped
                    else f"as many, {len(missing)}, but other cells"
                )
                message = (
                    "The coverage lists cells without a row that are not the cells the importer "
                    f"dropped as absent ({counted}) (D401)"
                )
                self.fail(
                    RefusalCode.INVALID_VALUE, at, ("fields", "parents"), message, _Rows(), None
                )

    def listed_cells(
        self,
        relationship: RelationshipDescriptor,
        parents: DirectCoverage | GroupedCoverage,
        key: Sequence[str],
        most: int,
    ) -> set[Tuple] | str:
        """The distinct cells the coverage lists, as keys of the child table in ``key``'s order
        (its parent columns and scope columns, which ``check_release`` pins to the key), or why
        they can't be read. A grouped coverage lists the product of each parent and its group's
        scopes, which is counted before it is built: a listing of more than ``most`` cells (the
        table's rows and the cells dropped as absent) cannot pass, so it is refused unbuilt."""
        fields = relationship.fields
        stands = dict(zip(fields.parent_columns, fields.child_columns, strict=True))
        if isinstance(parents, DirectCoverage):
            by_parent = {parent: own for own, parent in parents.parent_columns.items()}
            scopes = parents.scope_columns or {}
            source = parents.table
        else:
            by_parent = {parent: own for own, parent in parents.assignment.parent_columns.items()}
            scopes = parents.groups.scope_columns or {}
            source = parents.assignment.table
        if set(by_parent) != set(stands):
            return "The coverage's parent columns do not stand for the relationship's (D401)"
        order = [stands[parent] for parent in fields.parent_columns] + list(scopes.values())
        if sorted(order) != sorted(key):
            return "The coverage's columns are not the reshaped table's key (D401)"
        place = [order.index(column) for column in key]
        owners = self.tuples(source, [by_parent[parent] for parent in fields.parent_columns])
        if owners is None:
            return "The coverage's parent columns are not read"
        cells: set[Tuple] = set()

        def add(parent: Tuple, scope: Tuple) -> None:
            whole = (*parent, *scope)
            cells.add(tuple(whole[i] for i in place))

        if isinstance(parents, DirectCoverage):
            scoped = self.tuples(source, list(scopes))
            if scoped is None:
                return "The coverage's scope columns are not read"
            for owner, scope in zip(owners, scoped, strict=True):
                if owner is not None and scope is not None:
                    add(owner, scope)
            return cells
        assignment, groups = parents.assignment, parents.groups
        named = self.tuples(assignment.table, [assignment.group_column])
        group_names = self.tuples(groups.table, [groups.group_column])
        scoped = self.tuples(groups.table, list(scopes))
        if named is None or group_names is None or scoped is None:
            return "The coverage's group columns are not read"
        members: dict[Tuple, Tuple] = {}
        for owner, group in zip(owners, named, strict=True):
            if owner is None or group is None:
                continue
            if owner in members:
                return "A parent has more than one assignment row in the coverage (D401)"
            members[owner] = group
        listed: dict[Tuple, set[Tuple]] = {}
        for group, scope in zip(group_names, scoped, strict=True):
            if group is not None and scope is not None:
                listed.setdefault(group, set()).add(scope)
        size = sum(len(listed.get(group, ())) for group in members.values())
        if size > most:
            return (
                f"The coverage lists {size} cells, more than the table's rows and the cells "
                f"dropped as absent ({most}) (D401)"
            )
        for owner, group in members.items():
            for scope in listed.get(group, ()):
                add(owner, scope)
        return cells

    # --- Semantic gaps -------------------------------------------------------------------------

    def gaps(self) -> list[Gap]:
        found: list[Gap] = []
        for descriptor in self.descriptors:
            rows: _Rows | None = None
            if isinstance(descriptor, CoverageDescriptor):
                rows = self.outside(descriptor)
                kind: Literal["outside_coverage", "invalid_endpoint_rows"] = "outside_coverage"
            elif isinstance(descriptor, EndpointDescriptor):
                rows = self.invalid(descriptor)
                kind = "invalid_endpoint_rows"
            else:
                continue
            if rows:
                found.append(Gap(kind, descriptor.id, rows.count, tuple(rows.first)))
        return found

    def outside(self, descriptor: CoverageDescriptor) -> _Rows | None:
        """Child rows whose parent, with their scope values, the coverage does not list."""
        coverage = descriptor.fields
        relationship = self.relationships.get(coverage.relationship)
        parents = coverage.parents
        if relationship is None or not isinstance(parents, DirectCoverage | GroupedCoverage):
            return None
        fields = relationship.fields
        key = fields.parent_columns
        if isinstance(parents, DirectCoverage):
            by_parent = {stands: own for own, stands in parents.parent_columns.items()}
            scopes = parents.scope_columns or {}
        else:
            by_parent = {stands: own for own, stands in parents.assignment.parent_columns.items()}
            scopes = parents.groups.scope_columns or {}
        if set(by_parent) != set(key):
            return None
        children = self.tuples(fields.child_table, fields.child_columns)
        child_scopes = self.tuples(fields.child_table, list(scopes.values()))
        if children is None or child_scopes is None:
            return None
        listed = self.listing(parents, [by_parent[column] for column in key], list(scopes))
        if listed is None:
            return None
        rows = _Rows()
        for row, (found, scope) in enumerate(zip(children, child_scopes, strict=True)):
            if found is None or scope is None:
                continue
            if not listed(found, scope):
                rows.add(row)
        return rows

    def listing(
        self,
        parents: DirectCoverage | GroupedCoverage,
        parent_columns: Sequence[str],
        scope_columns: Sequence[str],
    ) -> Callable[[Tuple, Tuple], bool] | None:
        """Whether the coverage lists a parent, for a tuple of scope values."""
        if isinstance(parents, DirectCoverage):
            own = self.tuples(parents.table, parent_columns)
            scoped = self.tuples(parents.table, scope_columns)
            if own is None or scoped is None:
                return None
            pairs = {
                (found, scope)
                for found, scope in zip(own, scoped, strict=True)
                if found is not None and scope is not None
            }
            return lambda parent, scope: (parent, scope) in pairs
        assignment, groups = parents.assignment, parents.groups
        assigned = self.tuples(assignment.table, parent_columns)
        named = self.tuples(assignment.table, [assignment.group_column])
        group_names = self.tuples(groups.table, [groups.group_column])
        scoped = self.tuples(groups.table, scope_columns)
        every = None
        if groups.covers_all_column is not None:
            every = self.column(groups.table, groups.covers_all_column)
        if assigned is None or named is None or group_names is None or scoped is None:
            return None
        members: dict[Tuple, set[Tuple]] = {}
        for found, group in zip(assigned, named, strict=True):
            if found is not None and group is not None:
                members.setdefault(found, set()).add(group)
        tuples: dict[Tuple, set[Tuple]] = {}
        covers_all: set[Tuple] = set()
        for row, (group, scope) in enumerate(zip(group_names, scoped, strict=True)):
            if group is None:
                continue
            listed = tuples.setdefault(group, set())
            if scope is not None:
                listed.add(scope)
            if every is not None and every[row].state is PRESENT and every[row].value is True:
                covers_all.add(group)

        def lists(parent: Tuple, scope: Tuple) -> bool:
            return any(
                group in tuples
                and (not scope_columns or group in covers_all or scope in tuples[group])
                for group in members.get(parent, ())
            )

        return lists

    def invalid(self, descriptor: EndpointDescriptor) -> _Rows | None:
        """Rows with every cell PRESENT whose status is not coded, whose time is negative, or
        whose entry is at or after their time (§5.8)."""
        endpoint = descriptor.fields
        if (
            endpoint.table is None
            or endpoint.time_column is None
            or endpoint.status_column is None
            or endpoint.event_coding is None
        ):
            return None
        times = self.column(endpoint.table, endpoint.time_column)
        statuses = self.column(endpoint.table, endpoint.status_column)
        entries = None
        if isinstance(endpoint.entry, EntryColumn):
            entries = self.column(endpoint.table, endpoint.entry.column)
            if entries is None:
                return None
        if times is None or statuses is None:
            return None
        coding = endpoint.event_coding
        coded = {_json_key(value) for value in (*coding.event, *coding.censored)}
        rows = _Rows()
        for row in range(self.size(endpoint.table)):
            time, status = times[row], statuses[row]
            entry = None if entries is None else entries[row]
            if time.state is not PRESENT or status.state is not PRESENT:
                continue
            if entry is not None and entry.state is not PRESENT:
                continue
            if not isinstance(time.value, int | float) or isinstance(time.value, bool):
                continue
            late = (
                entry is not None
                and isinstance(entry.value, int | float)
                and entry.value >= time.value
            )
            if _json_key(status.value) not in coded or time.value < 0 or late:
                rows.add(row)
        return rows


__all__ = [
    "CHECK_VERSION",
    "REFERENCED_ROWS",
    "Cells",
    "Dropped",
    "Gap",
    "GateResult",
    "Mode",
    "check",
    "declaration_inputs",
    "needed",
]

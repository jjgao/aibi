"""Rules that span the descriptors of one release (SPEC §4, §5.1–§5.8, §13.2).

A single descriptor is checked by its model; these rules need the others:

- ids are unique, and there is one dataset descriptor;
- relationships from the same child columns have roles;
- every table, column and relationship a descriptor names is in the release, including those a
  coverage's ``parent_scope`` names (with the scope columns of its ``covered`` leaves), and
  every ``core:`` concept it names is a core concept of the right sort;
- a relationship leads to its parent's key, and so do a coverage's parent columns;
- coverage tables have role ``coverage`` and appear in no relationship, nor does any table with
  that role;
- a record filter lists values of category columns;
- an endpoint's table has a key, and its time and entry columns are time offsets;
- derived columns form no cycle;
- every pack with extensions is in the dataset's ``packs``.

A rule whose inputs are undeclared is not checked: a table whose key or role is undeclared, or a
dataset whose ``packs`` is, say. A rule is checked as far as the release allows: a coverage of
an unknown relationship still has its tables checked. Paths point into the list of
descriptors. The structural checks of the validation gate, which need the
data, come with the importers.
"""

from collections import defaultdict
from collections.abc import Hashable, Mapping, Sequence

from aibi.core.schema.checks import walk
from aibi.core.schema.concepts import CORE_CONCEPTS
from aibi.core.schema.descriptors import (
    RELEASE_KINDS,
    ColumnDescriptor,
    ConceptMapping,
    CoverageDescriptor,
    DatasetDescriptor,
    Descriptor,
    DirectCoverage,
    EndpointDescriptor,
    EntryColumn,
    GroupedCoverage,
    RelationshipDescriptor,
    TableDescriptor,
    derived_inputs,
)
from aibi.core.schema.document import ClauseModel, CoveredLeaf, ExistsLeaf, ValueLeaf
from aibi.core.schema.jsonio import pointer
from aibi.core.schema.output import Segment, data, text
from aibi.core.schema.refusals import Refusal, RefusalCode, finish_refusals

Path = list[str | int]


def check_release(descriptors: Sequence[Descriptor]) -> list[Refusal]:
    """Refusals for the rules that span descriptors, as ``finish_refusals`` returns them."""
    checker = _Checker(descriptors)
    checker.ids()
    checker.roles()
    for index, descriptor in enumerate(descriptors):
        checker.references(index, descriptor)
    checker.coverage_tables()
    checker.cycles()
    checker.packs()
    return finish_refusals(checker.refusals)


_CORE_SORTS = {concept.id: concept.fields.sort for concept in CORE_CONCEPTS}


class _Checker:
    def __init__(self, descriptors: Sequence[Descriptor]) -> None:
        self.descriptors = descriptors
        self.refusals: list[Refusal] = []
        self.tables: dict[str, TableDescriptor] = {}
        self.columns: dict[str, dict[str, int]] = defaultdict(dict)
        """Column ids of each table, with the index of their descriptor."""
        self.relationships: dict[str, RelationshipDescriptor] = {}
        for index, descriptor in enumerate(descriptors):
            if isinstance(descriptor, TableDescriptor):
                self.tables.setdefault(descriptor.id, descriptor)
            elif isinstance(descriptor, ColumnDescriptor):
                table, column = descriptor.id.split(".", 1)
                self.columns[table].setdefault(column, index)
            elif isinstance(descriptor, RelationshipDescriptor):
                self.relationships.setdefault(descriptor.id, descriptor)

    def refuse(self, code: RefusalCode, path: Path, message: str, *names: str) -> None:
        """A refusal whose message is ``message`` followed by ``names`` as data, joined by and."""
        segments: list[Segment] = [text(message)]
        for position, name in enumerate(names):
            if position:
                segments.append(text(" and "))
            segments.append(data(name))
        self.say(code, path, *segments)

    def say(self, code: RefusalCode, path: Path, *message: Segment) -> None:
        self.refusals.append(Refusal(code=code, path=pointer(path), message=list(message)))

    def table(self, path: Path, name: str) -> bool:
        """Whether the release has table ``name``; refused at ``path`` if not."""
        if name in self.tables:
            return True
        self.refuse(RefusalCode.UNKNOWN_DESCRIPTOR, path, "The release has no table ", name)
        return False

    def column(self, path: Path, table: str, name: str) -> ColumnDescriptor | None:
        """The column, if the release has it; refused at ``path`` if not."""
        if name not in self.columns[table]:
            message = "The release has no column "
            self.refuse(RefusalCode.UNKNOWN_DESCRIPTOR, path, message, f"{table}.{name}")
            return None
        found = self.descriptors[self.columns[table][name]]
        assert isinstance(found, ColumnDescriptor)
        return found

    def key(self, table: str) -> tuple[bool, list[str] | None]:
        """Whether the table's key is declared, and its columns or ``None`` for no key."""
        descriptor = self.tables.get(table)
        if descriptor is None or "primary_key" not in descriptor.fields.model_fields_set:
            return False, None
        return True, descriptor.fields.primary_key

    def leads_to_key(self, path: Path, table: str, columns: Sequence[str], what: str) -> None:
        """``columns`` of ``table`` are its declared key, in any order (§4, §5.6)."""
        declared, key = self.key(table)
        if not declared:
            return
        if key is None:
            self.refuse(RefusalCode.INVALID_VALUE, path, f"{what} a table with a key; not ", table)
        elif set(columns) != set(key):
            self.say(
                RefusalCode.INVALID_VALUE,
                path,
                text(f"{what} the key of "),
                data(table),
                text(", in any order: "),
                data(", ".join(key)),
            )

    def time_offset(self, path: Path, column: ColumnDescriptor | None, what: str) -> None:
        if column is not None and column.fields.datatype not in (None, "time_offset"):
            self.refuse(
                RefusalCode.INVALID_VALUE, path, f"{what} is a time_offset column: ", column.id
            )

    def concept(self, path: Path, mapping: ConceptMapping | str | None, sort: str) -> None:
        """A ``core:`` concept named here is a core concept of ``sort`` (§5.7)."""
        concept = mapping.concept if isinstance(mapping, ConceptMapping) else mapping
        if concept is None or not concept.startswith("core:"):
            return
        found = _CORE_SORTS.get(concept)
        if found is None:
            self.refuse(RefusalCode.UNKNOWN_DESCRIPTOR, path, "The core has no concept ", concept)
        elif found != sort:
            message = f"Expected a concept of sort {sort}, not {found}: "
            self.refuse(RefusalCode.INVALID_VALUE, path, message, concept)

    # --- The rules -----------------------------------------------------------------------------

    def ids(self) -> None:
        first: dict[str, int] = {}
        for index, descriptor in enumerate(self.descriptors):
            if descriptor.kind not in RELEASE_KINDS:
                self.refusals.append(
                    Refusal(
                        code=RefusalCode.INVALID_VALUE,
                        path=f"/{index}/kind",
                        message=[text(f"A release holds no {descriptor.kind} descriptors")],
                        alternatives=[text(kind) for kind in RELEASE_KINDS],
                    )
                )
            if descriptor.id in first:
                self.refuse(
                    RefusalCode.DUPLICATE_ENTRY,
                    [index, "id"],
                    f"The id is already used by descriptor {first[descriptor.id]}: ",
                    descriptor.id,
                )
            first.setdefault(descriptor.id, index)
        datasets = self._datasets()
        if len(datasets) != 1:
            self.refuse(
                RefusalCode.INVALID_VALUE,
                [] if not datasets else [datasets[1]],
                f"A release has one dataset descriptor, not {len(datasets)}",
            )

    def roles(self) -> None:
        """Roles tell apart relationships from the same child columns, in any order (§5.1).

        They are required when the same child columns reference two parents, and distinct from
        the child table's column ids. Roles are unique within their child table because the
        ids made from them are.
        """
        by_columns: dict[tuple[str, frozenset[str]], list[int]] = defaultdict(list)
        for index, descriptor in enumerate(self.descriptors):
            if not isinstance(descriptor, RelationshipDescriptor):
                continue
            fields = descriptor.fields
            by_columns[(fields.child_table, frozenset(fields.child_columns))].append(index)
            if fields.role is not None and fields.role in self.columns[fields.child_table]:
                self.refuse(
                    RefusalCode.CONFLICTING_MEMBERS,
                    [index, "fields", "role"],
                    "A role must differ from the child table's column ids: ",
                    fields.role,
                )
        for (table, columns), indices in by_columns.items():
            if len(indices) < 2:
                continue
            for index in indices:
                relationship = self.descriptors[index]
                assert isinstance(relationship, RelationshipDescriptor)
                if relationship.fields.role is None:
                    self.refuse(
                        RefusalCode.MISSING_MEMBER,
                        [index, "fields", "role"],
                        "Relationships from the same child columns need roles: ",
                        f"{table}.{'+'.join(sorted(columns))}",
                    )

    def references(self, index: int, descriptor: Descriptor) -> None:
        """Every table, column and relationship the descriptor names is in the release."""
        fields: Path = [index, "fields"]
        if isinstance(descriptor, ColumnDescriptor):
            table, column = descriptor.id.split(".", 1)
            if self.table([index, "id"], table) and descriptor.fields.derived is not None:
                for at, name in derived_inputs(descriptor.fields.derived):
                    if name != column:
                        self.column([*fields, "derived", *at], table, name)
            self.concept([*fields, "maps_to", "concept"], descriptor.fields.maps_to, "value")
        elif isinstance(descriptor, TableDescriptor):
            for position, name in enumerate(descriptor.fields.primary_key or []):
                self.column([*fields, "primary_key", position], descriptor.id, name)
            self.concept([*fields, "maps_to", "concept"], descriptor.fields.maps_to, "table")
            self.concept([*fields, "time_origin"], descriptor.fields.time_origin, "time_origin")
        elif isinstance(descriptor, RelationshipDescriptor):
            relationship = descriptor.fields
            sides = (
                ("child", relationship.child_table, relationship.child_columns),
                ("parent", relationship.parent_table, relationship.parent_columns),
            )
            for side, table, columns in sides:
                if self.table([*fields, f"{side}_table"], table):
                    for position, name in enumerate(columns):
                        self.column([*fields, f"{side}_columns", position], table, name)
            self.leads_to_key(
                [*fields, "parent_columns"],
                relationship.parent_table,
                relationship.parent_columns,
                "A relationship leads to",
            )
        elif isinstance(descriptor, CoverageDescriptor):
            self._coverage(fields, descriptor)
        elif isinstance(descriptor, EndpointDescriptor):
            self._endpoint(fields, descriptor)

    def _endpoint(self, fields: Path, descriptor: EndpointDescriptor) -> None:
        """Its table has a key, and its time and entry columns are time offsets (§5.8)."""
        endpoint = descriptor.fields
        self.concept([*fields, "maps_to", "concept"], endpoint.maps_to, "endpoint")
        self.concept([*fields, "time_origin"], endpoint.time_origin, "time_origin")
        if endpoint.table is None or not self.table([*fields, "table"], endpoint.table):
            return
        if self.key(endpoint.table) == (True, None):
            self.refuse(
                RefusalCode.INVALID_VALUE,
                [*fields, "table"],
                "An endpoint is on a table with a key; not ",
                endpoint.table,
            )
        if endpoint.time_column is not None:
            time = self.column([*fields, "time_column"], endpoint.table, endpoint.time_column)
            self.time_offset([*fields, "time_column"], time, "time_column")
        if endpoint.status_column is not None:
            self.column([*fields, "status_column"], endpoint.table, endpoint.status_column)
        if isinstance(endpoint.entry, EntryColumn):
            at: Path = [*fields, "entry", "column"]
            entry = self.column(at, endpoint.table, endpoint.entry.column)
            self.time_offset(at, entry, "An entry column")

    def _coverage(self, fields: Path, descriptor: CoverageDescriptor) -> None:
        """The relationship, each coverage table (its role and the columns it maps), the record
        filter's columns and what the parent scope names."""
        coverage = descriptor.fields
        relationship = self.relationships.get(coverage.relationship)
        if relationship is None:
            self.refuse(
                RefusalCode.UNKNOWN_DESCRIPTOR,
                [*fields, "relationship"],
                "The release has no relationship ",
                coverage.relationship,
            )
        # A side the release lacks is refused once, where it is named, and not checked further.
        parent = child = None
        if relationship is not None:
            parent = self._known(relationship.fields.parent_table)
            child = self._known(relationship.fields.child_table)
        parents = coverage.parents
        at: Path = [*fields, "parents"]
        if isinstance(parents, DirectCoverage):
            table = self._coverage_table([*at, "table"], parents.table)
            self._mapped([*at, "parent_columns"], table, parents.parent_columns, parent)
            self._mapped([*at, "scope_columns"], table, parents.scope_columns, child)
            if parent is not None:
                self.leads_to_key(
                    [*at, "parent_columns"],
                    parent,
                    list(parents.parent_columns.values()),
                    "Parent columns stand for",
                )
        elif isinstance(parents, GroupedCoverage):
            assignment, groups = parents.assignment, parents.groups
            table = self._coverage_table([*at, "assignment", "table"], assignment.table)
            self._mapped(
                [*at, "assignment", "parent_columns"], table, assignment.parent_columns, parent
            )
            if table is not None:
                self.column([*at, "assignment", "group_column"], table, assignment.group_column)
            if parent is not None:
                self.leads_to_key(
                    [*at, "assignment", "parent_columns"],
                    parent,
                    list(assignment.parent_columns.values()),
                    "Parent columns stand for",
                )
            table = self._coverage_table([*at, "groups", "table"], groups.table)
            self._mapped([*at, "groups", "scope_columns"], table, groups.scope_columns, child)
            if table is not None:
                self.column([*at, "groups", "group_column"], table, groups.group_column)
                if groups.covers_all_column is not None:
                    self.column(
                        [*at, "groups", "covers_all_column"], table, groups.covers_all_column
                    )
        for column in coverage.record_filter or {}:
            path: Path = [*fields, "record_filter", column]
            found = None if child is None else self.column(path, child, column)
            if found is not None and found.fields.datatype not in (None, "category"):
                message = "A record filter lists values of category columns; not "
                self.refuse(RefusalCode.INVALID_VALUE, path, message, found.id)
        if coverage.parent_scope is not None:
            self._named([*fields, "parent_scope"], coverage.parent_scope)

    def _known(self, table: str) -> str | None:
        return table if table in self.tables else None

    def _named(self, path: Path, clause: ClauseModel) -> None:
        """Every table, column and relationship a clause names is in the release, with the scope
        columns its ``covered`` leaves give, and every ``core:`` concept it names is a core
        concept of the right sort. (A ``via`` by dataset is refused with the descriptor.)"""
        for inner, at, _ in walk([clause], []):
            here: Path = [*path, *at[1:]]
            if isinstance(inner, ValueLeaf):
                if ":" in inner.column:
                    self.concept([*here, "column"], inner.column, "value")
                else:
                    table, column = inner.column.split(".", 1)
                    if self.table([*here, "column"], table):
                        self.column([*here, "column"], table, column)
            elif isinstance(inner, ExistsLeaf | CoveredLeaf):
                if ":" in inner.table:
                    self.concept([*here, "table"], inner.table, "table")
                elif self.table([*here, "table"], inner.table) and isinstance(inner, CoveredLeaf):
                    for column in inner.scope or {}:
                        self.column([*here, "scope", column], inner.table, column)
            if isinstance(inner, ValueLeaf | ExistsLeaf | CoveredLeaf) and isinstance(
                inner.via, list
            ):
                for position, step in enumerate(inner.via):
                    if step.rel not in self.relationships:
                        self.refuse(
                            RefusalCode.UNKNOWN_DESCRIPTOR,
                            [*here, "via", position, "rel"],
                            "The release has no relationship ",
                            step.rel,
                        )

    def _coverage_table(self, path: Path, name: str) -> str | None:
        """The table, if the release has it; its role, when declared, is ``coverage``."""
        if not self.table(path, name):
            return None
        if self.tables[name].fields.role not in (None, "coverage"):
            self.refuse(
                RefusalCode.INVALID_VALUE, path, "A coverage table has role coverage: ", name
            )
        return name

    def _mapped(
        self,
        path: Path,
        table: str | None,
        columns: Mapping[str, str] | None,
        target: str | None,
    ) -> None:
        """Columns of a coverage table, each standing for a column of ``target``: one refusal
        per entry, naming every column the release lacks. A table that is ``None`` is not in
        the release, and its side is not checked."""
        for column, stands_for in (columns or {}).items():
            missing = [
                f"{owner}.{name}"
                for owner, name in ((table, column), (target, stands_for))
                if owner is not None and name not in self.columns[owner]
            ]
            if missing:
                self.refuse(
                    RefusalCode.UNKNOWN_DESCRIPTOR,
                    [*path, column],
                    "The release has no column "
                    if len(missing) == 1
                    else "The release has no columns ",
                    *missing,
                )

    def coverage_tables(self) -> None:
        """Coverage tables are not part of the table graph, so no relationship names one: no
        table with role ``coverage``, and none of undeclared role that a coverage names. A
        coverage naming a table of another role is refused where it names it, and the
        relationships on either side of that table are not refused for it."""
        named = {
            table
            for descriptor in self.descriptors
            if isinstance(descriptor, CoverageDescriptor)
            for table in descriptor.fields.tables()
            if table in self.tables and self.tables[table].fields.role is None
        }
        for index, descriptor in enumerate(self.descriptors):
            if not isinstance(descriptor, RelationshipDescriptor):
                continue
            for side in ("child", "parent"):
                table = getattr(descriptor.fields, f"{side}_table")
                found = self.tables.get(table)
                if table in named or (found is not None and found.fields.role == "coverage"):
                    self.refuse(
                        RefusalCode.INVALID_VALUE,
                        [index, "fields", f"{side}_table"],
                        "A relationship joins no coverage table: ",
                        table,
                    )

    def cycles(self) -> None:
        """Derived columns read other columns of their table, but never through a cycle."""
        reads: dict[tuple[str, str], list[tuple[str, str]]] = {}
        where: dict[tuple[str, str], int] = {}
        for index, descriptor in enumerate(self.descriptors):
            if isinstance(descriptor, ColumnDescriptor) and descriptor.fields.derived is not None:
                table, column = descriptor.id.split(".", 1)
                reads.setdefault(
                    (table, column),
                    [(table, name) for _, name in derived_inputs(descriptor.fields.derived)],
                )
                where.setdefault((table, column), index)
        for table, column in sorted(on_cycles(reads), key=lambda node: where[node]):
            self.refuse(
                RefusalCode.INVALID_VALUE,
                [where[(table, column)], "fields", "derived"],
                "The column is derived from itself, through other derived columns: ",
                f"{table}.{column}",
            )

    def packs(self) -> None:
        """Every pack whose extensions appear is in the dataset's ``packs`` (§5.2)."""
        datasets = self._datasets()
        if len(datasets) != 1:
            return
        dataset = self.descriptors[datasets[0]]
        assert isinstance(dataset, DatasetDescriptor)
        if "packs" not in dataset.fields.model_fields_set:
            return
        listed = set(dataset.fields.packs or [])
        for index, descriptor in enumerate(self.descriptors):
            for pack in descriptor.extensions:
                if pack not in listed:
                    self.refuse(
                        RefusalCode.INVALID_VALUE,
                        [index, "extensions", pack],
                        "Extensions of a pack the dataset does not list in packs: ",
                        pack,
                    )

    def _datasets(self) -> list[int]:
        return [i for i, d in enumerate(self.descriptors) if isinstance(d, DatasetDescriptor)]


def on_cycles[Node: Hashable](graph: Mapping[Node, Sequence[Node]]) -> set[Node]:
    """The nodes on some cycle of ``graph``: those in a strongly connected component of more
    than one node, or with an edge to themselves. Edges to nodes outside ``graph`` are ignored.

    Tarjan's algorithm, without recursion, so the time is linear in the size of the graph.
    """
    index: dict[Node, int] = {}
    low: dict[Node, int] = {}
    stack: list[Node] = []
    on_stack: set[Node] = set()
    found: set[Node] = set()
    for root in graph:
        if root in index:
            continue
        work: list[tuple[Node, int]] = [(root, 0)]
        while work:
            node, position = work.pop()
            successors = graph[node]
            if position == 0:
                index[node] = low[node] = len(index)
                stack.append(node)
                on_stack.add(node)
            else:
                low[node] = min(low[node], low[successors[position - 1]])
            while position < len(successors):
                successor = successors[position]
                position += 1
                if successor not in graph:
                    continue
                if successor not in index:
                    work.append((node, position))
                    work.append((successor, 0))
                    break
                if successor in on_stack:
                    low[node] = min(low[node], index[successor])
            else:
                if low[node] == index[node]:
                    component: list[Node] = []
                    while True:
                        member = stack.pop()
                        on_stack.discard(member)
                        component.append(member)
                        if member == node:
                            break
                    if len(component) > 1 or node in successors:
                        found.update(component)
    return found


__all__ = ["check_release", "on_cycles"]

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

A concept outside ``core:`` is checked on descriptor writes only (``pack_concepts``, which
``store.writes.check_writes`` runs, D391), by the same traversal: never here, since
carry-forward reads a refusal here as something gone and deletes what names it. A refusal of a
concept lists the nearest ids of the sort its place needs. Every refusal is recorded and built
only if it is returned (``finish_lazy``), as ``finish_refusals`` would return it.
"""

from collections import defaultdict
from collections.abc import Callable, Container, Hashable, Mapping, Sequence

from aibi.core.schema.concepts import CORE_SORTS, core_ids
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
    ValueMap,
    derived_inputs,
)
from aibi.core.schema.document import ClauseModel, CoveredLeaf, ExistsLeaf, ValueLeaf, walk
from aibi.core.schema.jsonio import pointer
from aibi.core.schema.output import data, text
from aibi.core.schema.params import nearest
from aibi.core.schema.refusals import Record, Refusal, RefusalCode, finish_lazy, said

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
    checker.absent()
    return finish_lazy(checker.records)


def pack_concepts(
    descriptors: Sequence[Descriptor],
    sorts: Mapping[str, str],
    ids: Callable[[str], Sequence[str]],
    *,
    only: Container[str] | None = None,
) -> list[Record]:
    """The refusals, as records for ``finish_lazy``, of the concepts the descriptors whose ids
    are in ``only`` (or every descriptor) name outside ``core:``, which ``check_release``
    leaves alone (D391): each must be in ``sorts`` (concept id -> sort, the installed packs'),
    ``UNKNOWN_DESCRIPTOR`` if not, and of the sort the place needs, ``INVALID_VALUE`` if not;
    each refusal lists the nearest of ``ids(sort)``, the ids of the sort the place needs. Paths
    point into ``descriptors``, as ``check_release``'s do; ``core:`` concepts and every other
    rule are left to ``check_release``."""
    checker = _Checker(descriptors, sorts=sorts, ids=ids)
    for index, descriptor in enumerate(descriptors):
        if only is None or descriptor.id in only:
            checker.references(index, descriptor)
    return checker.records


def _not_of_a_release(code: str, at: str | None, kind: str) -> Refusal:
    return Refusal(
        code=code,
        path=at,
        message=[text(f"A release holds no {kind} descriptors")],
        alternatives=[text(known) for known in RELEASE_KINDS],
    )


def _concept_refused(
    code: str,
    at: str | None,
    given: str,
    sort: str,
    found: str | None,
    known: Sequence[str],
    packs: bool,
) -> Refusal:
    """The refusal of concept ``given`` where one of ``sort`` is named, ``found`` its sort if it
    has one, listing the nearest of ``known``, the ids of ``sort``: the core's alone for a
    ``core:`` concept (``packs`` false), the core's and the installed packs' otherwise (D391)."""
    if found is not None:
        message = [text(f"Expected a concept of sort {sort}, not {found}: "), data(given)]
    elif packs:
        message = [text("No installed pack registers the concept "), data(given)]
    else:
        message = [text("The core has no concept "), data(given)]
    listed = nearest(given, known)
    if not known:
        none = f"; the core has no concept of sort {sort}"
        message.append(text(none + ", and no installed pack registers one" if packs else none))
    elif len(listed) < len(known):
        has = "the core and the installed packs have" if packs else "the core has"
        nearest_ = f"and the {len(listed)} nearest are listed"
        message.append(text(f"; {has} {len(known)} concepts of sort {sort}, {nearest_}"))
    return Refusal(code=code, path=at, message=message, alternatives=[data(id_) for id_ in listed])


class _Checker:
    def __init__(
        self,
        descriptors: Sequence[Descriptor],
        *,
        sorts: Mapping[str, str] | None = None,
        ids: Callable[[str], Sequence[str]] = core_ids,
    ) -> None:
        """``sorts`` (the installed packs' concept id -> sort) makes this the concept pass of
        ``pack_concepts``: ``concept`` checks the concepts outside ``core:`` against it, and
        nothing else is recorded; ``ids`` gives the concept ids a refusal of a concept lists."""
        self.descriptors = descriptors
        self.sorts = sorts
        self.listing = ids
        self.records: list[Record] = []
        """What is refused, built only if it is returned (``finish_lazy``)."""
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
        parts = [message]
        for position, name in enumerate(names):
            if position:
                parts.append(" and ")
            parts.append(name)
        self.say(code, path, *parts)

    def say(self, code: RefusalCode, path: Path, *parts: str) -> None:
        """A refusal whose message is ``parts``, text and data in turn, from text; recorded, not
        built. The concept pass records only what ``concept`` finds."""
        if self.sorts is None:
            self.records.append((pointer(path), code, said, parts))

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
                f"{what} the key of ",
                table,
                ", in any order: ",
                ", ".join(key),
            )

    def time_offset(self, path: Path, column: ColumnDescriptor | None, what: str) -> None:
        if column is not None and column.fields.datatype not in (None, "time_offset"):
            self.refuse(
                RefusalCode.INVALID_VALUE, path, f"{what} is a time_offset column: ", column.id
            )

    def concept(self, path: Path, mapping: ConceptMapping | str | None, sort: str) -> None:
        """A ``core:`` concept named here is a core concept of ``sort`` (§5.7); in the concept
        pass, a concept outside ``core:`` is an installed pack's of ``sort`` (D391), and a
        ``core:`` one is left to ``check_release``. A refusal lists the nearest concept ids of
        ``sort``."""
        concept = mapping.concept if isinstance(mapping, ConceptMapping) else mapping
        if concept is None or concept.startswith("core:") != (self.sorts is None):
            return
        found = (CORE_SORTS if self.sorts is None else self.sorts).get(concept)
        if found == sort:
            return
        code = RefusalCode.UNKNOWN_DESCRIPTOR if found is None else RefusalCode.INVALID_VALUE
        arguments = (concept, sort, found, self.listing(sort), self.sorts is not None)
        self.records.append((pointer(path), code, _concept_refused, arguments))

    # --- The rules -----------------------------------------------------------------------------

    def ids(self) -> None:
        first: dict[str, int] = {}
        for index, descriptor in enumerate(self.descriptors):
            if descriptor.kind not in RELEASE_KINDS:
                at = pointer([index, "kind"])
                code = RefusalCode.INVALID_VALUE
                self.records.append((at, code, _not_of_a_release, (descriptor.kind,)))
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

    def absent(self) -> None:
        """A column with ``absent`` values (D386) is the value column of a table a pack
        unpivoted, ``T``, whose dropped cells the rules below keep from reading as anything but
        what they are; an undeclared input fails a rule, unlike the rules above:

        - (a) ``T`` is not a coverage table (an undeclared role is not one), its ``source.kind``
          is ``pack``, and it has one column with absent values;
        - (b) the column is a ``category`` with ``permissible_values``, none of them absent, and
          no missing code is absent;
        - (c) no derived column whose input chain reaches it maps an absent value, and no record
          filter on it lists a value it does not permit;
        - (d) ``T``'s key is declared and does not hold it;
        - (e) every relationship into ``T`` whose coverage declares ``parents`` has them scoped,
          neither ``all`` nor covering every value, their parent columns standing for the
          relationship's, and the relationship's child columns with the scope columns making
          ``T``'s key, each a ``string`` or ``category`` column of the same datatype in ``T`` and
          in the coverage table that stands for it.
        """
        by_table: dict[str, list[tuple[int, ColumnDescriptor]]] = defaultdict(list)
        for index, descriptor in enumerate(self.descriptors):
            if isinstance(descriptor, ColumnDescriptor) and descriptor.fields.absent is not None:
                by_table[descriptor.id.split(".", 1)[0]].append((index, descriptor))
        for table, columns in sorted(by_table.items()):
            for index, column in columns:
                self._absent(table, index, column, len(columns))

    def _absent(self, table: str, index: int, column: ColumnDescriptor, count: int) -> None:
        here: Path = [index, "fields"]
        name = column.id.split(".", 1)[1]
        absent = set(column.fields.absent or ())
        fields = column.fields
        rule = "A column with absent values (D386) "
        found = self.tables.get(table)
        if found is None:
            return
        at = self.descriptors.index(found)
        if found.fields.role == "coverage":
            self.refuse(
                RefusalCode.INVALID_VALUE, [*here, "absent"], rule + "is no coverage's: ", column.id
            )
        if found.fields.source is None or found.fields.source.kind != "pack":
            self.refuse(
                RefusalCode.INVALID_VALUE,
                [at, "fields", "source"],
                rule + "is in a table a pack's importer reshaped, of source kind pack: ",
                table,
            )
        if count > 1:
            self.refuse(
                RefusalCode.INVALID_VALUE,
                [*here, "absent"],
                rule + "is its table's only one: ",
                table,
            )
        if fields.datatype != "category":
            self.refuse(
                RefusalCode.INVALID_VALUE, [*here, "datatype"], rule + "is a category: ", column.id
            )
        permitted = (
            None
            if fields.permissible_values is None
            else {entry.value for entry in fields.permissible_values.values}
        )
        if permitted is None or permitted & absent:
            self.refuse(
                RefusalCode.INVALID_VALUE,
                [*here, "permissible_values"],
                rule + "declares permissible values, none of them absent: ",
                column.id,
            )
        if absent & set(fields.missing_codes or {}):
            self.refuse(
                RefusalCode.INVALID_VALUE,
                [*here, "missing_codes"],
                rule + "has no absent value as a missing code; set the codes without them in a "
                "session on the latest release, then re-import: ",
                column.id,
            )
        self._absent_derived(table, name, absent)
        key = found.fields.primary_key
        if not key or name in key:
            self.refuse(
                RefusalCode.INVALID_VALUE,
                [at, "fields", "primary_key"],
                rule + "is in a table whose key is declared and does not hold it: ",
                table,
            )
            return
        for coverage_at, coverage in self._coverages_into(table):
            self._absent_coverage(coverage_at, coverage, table, name, key, permitted)

    def _absent_derived(self, table: str, name: str, absent: set[str]) -> None:
        reaches = {name}
        changed = True
        derived: dict[str, tuple[int, ColumnDescriptor]] = {}
        for column, index in self.columns[table].items():
            descriptor = self.descriptors[index]
            if isinstance(descriptor, ColumnDescriptor) and descriptor.fields.derived is not None:
                derived[column] = (index, descriptor)
        while changed:
            changed = False
            for column, (_, descriptor) in derived.items():
                assert descriptor.fields.derived is not None
                inputs = {input_ for _, input_ in derived_inputs(descriptor.fields.derived)}
                if column not in reaches and inputs & reaches:
                    reaches.add(column)
                    changed = True
        for _, (index, descriptor) in sorted(derived.items()):
            mapping = descriptor.fields.derived
            if (
                isinstance(mapping, ValueMap)
                and mapping.input in reaches
                and set(mapping.map) & absent
            ):
                self.refuse(
                    RefusalCode.INVALID_VALUE,
                    [index, "fields", "derived", "map"],
                    "A derived column maps a value its input's importer dropped as absent (D386): ",
                    descriptor.id,
                )

    def _coverages_into(self, table: str) -> list[tuple[int, CoverageDescriptor]]:
        into = {
            id_
            for id_, relationship in self.relationships.items()
            if relationship.fields.child_table == table
        }
        return [
            (index, descriptor)
            for index, descriptor in enumerate(self.descriptors)
            if isinstance(descriptor, CoverageDescriptor) and descriptor.fields.relationship in into
        ]

    def _absent_coverage(
        self,
        at: int,
        coverage: CoverageDescriptor,
        table: str,
        name: str,
        key: Sequence[str],
        permitted: set[str] | None,
    ) -> None:
        rule = "The coverage of a table with absent values (D386) "
        fields = coverage.fields
        for column, values in (fields.record_filter or {}).items():
            if column == name and (permitted is None or not set(values) <= permitted):
                self.refuse(
                    RefusalCode.INVALID_VALUE,
                    [at, "fields", "record_filter", column],
                    rule + "filters the column by values it permits: ",
                    f"{table}.{name}",
                )
        parents = fields.parents
        if parents is None:
            return
        path: Path = [at, "fields", "parents"]
        relationship = self.relationships[fields.relationship]
        if isinstance(parents, DirectCoverage):
            stand, scopes, source = (
                parents.parent_columns,
                parents.scope_columns or {},
                parents.table,
            )
            scope_source = parents.table
        elif isinstance(parents, GroupedCoverage):
            stand, scopes = parents.assignment.parent_columns, parents.groups.scope_columns or {}
            source, scope_source = parents.assignment.table, parents.groups.table
            if parents.groups.covers_all_column is not None:
                self.refuse(
                    RefusalCode.INVALID_VALUE,
                    path,
                    rule + "has no group covering every value: ",
                    coverage.id,
                )
        else:
            self.refuse(
                RefusalCode.INVALID_VALUE, path, rule + "lists its cells, not all: ", coverage.id
            )
            return
        if not scopes:
            self.refuse(
                RefusalCode.INVALID_VALUE,
                path,
                rule + "is scoped by the table's other key columns: ",
                coverage.id,
            )
            return
        parent_columns = relationship.fields.parent_columns
        if set(stand.values()) != set(parent_columns):
            self.refuse(
                RefusalCode.INVALID_VALUE,
                path,
                rule + "stands for the relationship's parent columns: ",
                coverage.id,
            )
            return
        by_parent = {parent: own for own, parent in stand.items()}
        pairs = [
            (child, source, by_parent[parent])
            for parent, child in zip(parent_columns, relationship.fields.child_columns, strict=True)
        ]
        pairs += [(child, scope_source, own) for own, child in scopes.items()]
        if sorted(child for child, _, _ in pairs) != sorted(key):
            self.refuse(
                RefusalCode.INVALID_VALUE,
                path,
                rule + "names exactly the table's key, by its relationship and scope: ",
                coverage.id,
            )
            return
        for child, other, own in pairs:
            mine = self._datatype(table, child)
            theirs = self._datatype(other, own)
            if mine not in ("string", "category") or mine != theirs:
                self.refuse(
                    RefusalCode.INVALID_VALUE,
                    path,
                    rule + "keys its cells by string or category columns of one datatype on both "
                    "sides: ",
                    f"{table}.{child}",
                )
        named = [(table, child) for child, _, _ in pairs] + [(o, own) for _, o, own in pairs]
        if isinstance(parents, GroupedCoverage):
            groups = [
                (source, parents.assignment.group_column),
                (scope_source, parents.groups.group_column),
            ]
            kinds = {self._datatype(owner, column) for owner, column in groups}
            if len(kinds) != 1 or not kinds <= {"string", "category"}:
                self.refuse(
                    RefusalCode.INVALID_VALUE,
                    path,
                    rule + "groups its cells by string or category columns of one datatype on "
                    "both sides: ",
                    coverage.id,
                )
            named += groups
        for owner, column in named:
            if self._derived(owner, column):
                self.refuse(
                    RefusalCode.INVALID_VALUE,
                    path,
                    rule + "keys and lists its cells by stored columns, not derived ones: ",
                    f"{owner}.{column}",
                )

    def _derived(self, table: str, column: str) -> bool:
        index = self.columns.get(table, {}).get(column)
        if index is None:
            return False
        descriptor = self.descriptors[index]
        return isinstance(descriptor, ColumnDescriptor) and descriptor.fields.derived is not None

    def _datatype(self, table: str, column: str) -> str | None:
        index = self.columns[table].get(column)
        if index is None:
            return None
        descriptor = self.descriptors[index]
        return descriptor.fields.datatype if isinstance(descriptor, ColumnDescriptor) else None

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


__all__ = ["check_release", "on_cycles", "pack_concepts"]

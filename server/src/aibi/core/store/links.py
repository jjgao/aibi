"""The places a release's descriptors say hold another table's keys, for erasure (SPEC §12.2,
D223, D408): what this module reads is the descriptors alone, never a row, so the store can
record it when a release is published (``Store.commit_label``) and erasure can follow it after
the declaring release is withdrawn.

- A **link** is a child table's columns that hold a parent table's key or its columns: a
  relationship's foreign key, a coverage table's parent columns (a direct coverage's table, or a
  grouped coverage's assignment table), or a scope column composed with what it stands for.
- A **scope** is a coverage's scope column and the child table's column it stands for (a
  direct coverage's table, or a grouped coverage's groups table, which holds the scope columns).
- The **graph** is every relationship as a link, and every table's key as a link from the
  table into itself (a self-edge), so that a scope column standing for a foreign key leads to
  the table it points into, and one standing for the child table's own key to that table.
  ``compose`` reads it, and nothing else: relationships followed as rows stay those the
  releases declare (D223).

A link's column pairs are kept in order of the child's columns, so that the same link is the
same value whichever release declares it and in whichever order its columns were written.
Nothing here imports the store; ``jsonio`` gives the registry's canonical spelling of columns.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from aibi.core.engine.data import Release
from aibi.core.schema.descriptors import (
    CoverageDescriptor,
    Descriptor,
    DirectCoverage,
    GroupedCoverage,
    RelationshipDescriptor,
    TableDescriptor,
)
from aibi.core.schema.jsonio import canonical


@dataclass(frozen=True, order=True)
class Link:
    """A child table's columns holding a parent table's columns (its key, as declared), as some
    published release declares them: the same link whatever declares it, and whichever release
    does."""

    child: str
    child_columns: tuple[str, ...]
    parent: str
    parent_columns: tuple[str, ...]

    def applies(self, release: Release) -> bool:
        """Whether the release's child table has the link's columns."""
        return release.table(self.child) is not None and set(self.child_columns) <= set(
            release.columns(self.child)
        )


@dataclass(frozen=True, order=True)
class Scope:
    """A scope column of a coverage, and the child table's column it stands for."""

    scope_table: str
    scope_column: str
    child_table: str
    child_column: str


def link(
    child: str, child_columns: Sequence[str], parent: str, parent_columns: Sequence[str]
) -> Link:
    """A link, its column pairs in order of the child's columns."""
    pairs = sorted(zip(child_columns, parent_columns, strict=True))
    return Link(
        child, tuple(column for column, _ in pairs), parent, tuple(column for _, column in pairs)
    )


def columns_text(columns: Sequence[str]) -> str:
    """Columns as the registry stores them: their canonical JSON (RFC 8785), as text."""
    return canonical(list(columns)).decode()


def relationships(descriptors: Iterable[Descriptor]) -> frozenset[Link]:
    """Every relationship the descriptors declare, as a link."""
    return frozenset(
        link(f.child_table, f.child_columns, f.parent_table, f.parent_columns)
        for d in descriptors
        if isinstance(d, RelationshipDescriptor)
        for f in (d.fields,)
    )


def _coverages(
    descriptors: Sequence[Descriptor],
) -> list[tuple[CoverageDescriptor, RelationshipDescriptor]]:
    """Each coverage with its relationship, both in the same descriptors."""
    by_id: Mapping[str, RelationshipDescriptor] = {
        d.id: d for d in descriptors if isinstance(d, RelationshipDescriptor)
    }
    found: list[tuple[CoverageDescriptor, RelationshipDescriptor]] = []
    for descriptor in descriptors:
        if isinstance(descriptor, CoverageDescriptor):
            relationship = by_id.get(descriptor.fields.relationship)
            if relationship is not None:
                found.append((descriptor, relationship))
    return found


def covers(descriptors: Iterable[Descriptor]) -> frozenset[Link]:
    """The coverage tables' parent columns, as links into the coverage's parent table: a direct
    coverage's table, and a grouped coverage's assignment table. A coverage of ``all`` parents
    lists none."""
    found: set[Link] = set()
    for coverage, relationship in _coverages(tuple(descriptors)):
        parents, parent = coverage.fields.parents, relationship.fields.parent_table
        if isinstance(parents, DirectCoverage):
            mapped = parents.parent_columns
            found.add(link(parents.table, list(mapped), parent, list(mapped.values())))
        elif isinstance(parents, GroupedCoverage):
            mapped = parents.assignment.parent_columns
            found.add(link(parents.assignment.table, list(mapped), parent, list(mapped.values())))
    return frozenset(found)


def scopes(descriptors: Iterable[Descriptor]) -> frozenset[Scope]:
    """The coverages' scope columns, each with the column of the relationship's child table it
    stands for: a direct coverage's table's, and a grouped coverage's groups table's."""
    found: set[Scope] = set()
    for coverage, relationship in _coverages(tuple(descriptors)):
        parents, child = coverage.fields.parents, relationship.fields.child_table
        mapped: Mapping[str, str] | None = None
        table = ""
        if isinstance(parents, DirectCoverage):
            mapped, table = parents.scope_columns, parents.table
        elif isinstance(parents, GroupedCoverage):
            mapped, table = parents.groups.scope_columns, parents.groups.table
        for scope_column, child_column in (mapped or {}).items():
            found.add(Scope(table, scope_column, child, child_column))
    return frozenset(found)


def graph(descriptors: Iterable[Descriptor]) -> frozenset[Link]:
    """Every relationship as a link, and every table's key as a link into itself."""
    listed = tuple(descriptors)
    keys = {
        link(d.id, d.fields.primary_key, d.id, d.fields.primary_key)
        for d in listed
        if isinstance(d, TableDescriptor) and d.fields.primary_key
    }
    return relationships(listed) | keys


def compose(found: Iterable[Scope], edges: Iterable[Link]) -> frozenset["Cover"]:
    """The covers scope columns make with the graph: for every edge out of a child table whose
    every column some scope columns of one scope table stand for, a cover from that scope table
    into the edge's parent columns whose position *i* is read from any of the scope columns
    standing for the edge's column *i*. Never the product of those choices, which grows as the
    alternatives to the power of the key's columns (8 columns of 3 alternatives are 6,561 links,
    16 of 3 do not fit in memory): one cover per (scope table, edge), so the output is bounded
    by the scope tables times the edges, whatever the registry holds."""
    standing: dict[tuple[str, str], dict[str, set[str]]] = {}
    for scope in found:
        by = standing.setdefault((scope.scope_table, scope.child_table), {})
        by.setdefault(scope.child_column, set()).add(scope.scope_column)
    composed: set[Cover] = set()
    listed = tuple(edges)
    for (scope_table, child), by in standing.items():
        for edge in listed:
            if edge.child != child or not all(c in by for c in edge.child_columns):
                continue
            alternatives = tuple(tuple(sorted(by[column])) for column in edge.child_columns)
            composed.add(Cover(scope_table, alternatives, edge.parent, edge.parent_columns))
    return frozenset(composed)


@dataclass(frozen=True, order=True)
class Cover:
    """What an erasure scans (D408): a table whose rows name keys of ``parent`` over
    ``parent_columns``, position *i* read from any of the columns ``columns[i]`` that the table
    has (a coverage link has one column per position; a composed scope link the scope columns
    that stand for the edge's column *i*, from every declaration)."""

    child: str
    columns: tuple[tuple[str, ...], ...]
    parent: str
    parent_columns: tuple[str, ...]

    @classmethod
    def of(cls, found: Link) -> "Cover":
        return cls(
            found.child,
            tuple((column,) for column in found.child_columns),
            found.parent,
            found.parent_columns,
        )

    def present(self, release: Release) -> tuple[tuple[str, ...], ...]:
        """The columns of each position that the release's child table has."""
        have: set[str] = set()
        if release.table(self.child) is not None:
            have.update(release.columns(self.child))
        return tuple(tuple(c for c in alternatives if c in have) for alternatives in self.columns)

    def applies(self, release: Release) -> bool:
        """Whether the release's child table has a column for every position."""
        return all(self.present(release))


__all__ = [
    "Cover",
    "Link",
    "Scope",
    "columns_text",
    "compose",
    "covers",
    "graph",
    "link",
    "relationships",
    "scopes",
]

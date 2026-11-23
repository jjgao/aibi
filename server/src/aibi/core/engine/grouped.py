"""Which grouped coverages a compiler lists by the one-group path (SPEC D449, refining D291).

A grouped coverage's listing (``sql._Compiler.listing``) is main's join of each parent row to
its assignment rows and their groups' rows, aggregated per parent. The one-group path reads, for
a parent row assigned to exactly one group, that group's own ``tuples``, ``every`` and
``within``, computed once per group, and takes main's join only for parents of two groups or
more. It is exact on every release, but it reads the coverage's tables more often than main
does, so it is used only where its tables' rows are inside a bound (wide keys can still cost
more than main's join, D449's stated residuals):

    the sum, over the grouped coverages a compiler admits, of
    4 * A + P + (3 + L) * G  <=  ROW_BUDGET

where ``A``, ``G`` and ``P`` are the rows of the coverage's assignment, groups and parent tables
and ``L`` the number of listings of it the compiler builds (``sql._Listings``). Coverages are
taken in the order of their relationship ids; one whose term does not fit what is left is
skipped (it takes main's path and counts nothing) and the next ones are still taken. A
coverage whose tables' sizes are unknown takes main's path.

A table's rows are the row count in its blob's Parquet footer (``Store.sources``), clamped up to
the effective ``min_cell_count`` where the sources are built (``clamped``), so that a count the
catalogue withholds (§8.4, D271) cannot change which statements are compiled: no count below
k reaches the rule (D449). The rule is a pure function of its arguments, so the SQL is
a pure function of the document, the release and the effective ``min_cell_count``.
"""

from collections.abc import Mapping
from dataclasses import dataclass, replace

from aibi.core.store.tables import TableSource

ROW_BUDGET = 1_500_000
"""The largest sum of the terms of the coverages one compiler admits (module docstring), as
measured at the caps (D449). The core suite pins its value; the domain job's cost cells pin what
it allows (each asserts its release admitted)."""
ASSIGNMENT_FACTOR = 4
PARENT_FACTOR = 1
GROUPS_FACTOR = 3
"""The factors of ``A``, ``P`` and ``G`` in a coverage's term; ``G`` is also counted once for
each listing."""


@dataclass(frozen=True)
class Sizes:
    """A grouped coverage's tables' rows, as the rule reads them, and its listings."""

    assignment: int
    parents: int
    groups: int
    listings: int


def term(sizes: Sizes) -> int:
    """A coverage's term: ``4 * A + P + (3 + L) * G``."""
    return (
        ASSIGNMENT_FACTOR * sizes.assignment
        + PARENT_FACTOR * sizes.parents
        + (GROUPS_FACTOR + sizes.listings) * sizes.groups
    )


def admitted(coverages: Mapping[str, Sizes | None]) -> frozenset[str]:
    """The coverages, by relationship id, the one-group path lists: in the order of their ids,
    each whose term fits what the ones taken before it leave of ``ROW_BUDGET``; ``None`` sizes
    are unknown and never taken."""
    budget = ROW_BUDGET
    total = 0
    found: set[str] = set()
    for relationship in sorted(coverages):
        sizes = coverages[relationship]
        if sizes is None:
            continue
        cost = term(sizes)
        if total + cost <= budget:
            found.add(relationship)
            total += cost
    return frozenset(found)


def clamped(sources: Mapping[str, TableSource], k: int | None) -> dict[str, TableSource]:
    """Each source with its rows clamped up to ``k``, the effective ``min_cell_count`` of its
    release (``None`` under no disclosure setting): a table of fewer rows than ``k``, none
    included, is given ``k`` rows, as the catalogue withholds its count (§8.4, D271, D449)."""
    return {
        table: source
        if k is None or source.n_rows is None or source.n_rows >= k
        else replace(source, n_rows=k)
        for table, source in sources.items()
    }

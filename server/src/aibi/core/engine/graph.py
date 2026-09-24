"""The table graph and its paths (SPEC §6.1).

Tables other than coverage tables are the nodes; relationships are the edges, from child to
parent. A step goes up a relationship (child to parent, a lookup) or down it (parent to children,
an existence question). An implicit path, from the current table to a referenced one, is a
sequence of at most 16 steps that visits no table twice: if exactly one exists it is used, and
otherwise the reference is refused, listing the paths when there are several. An explicit path
(``via``) may revisit tables.
"""

import json
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass

from aibi.core.engine.data import Direction, Release
from aibi.core.schema.limits import MAX_PATH_STEPS

PATH_SEARCH_STEPS = 100_000
"""Steps an implicit path search may take before it gives up (limit ``path_search``)."""


@dataclass(frozen=True, slots=True, order=True)
class Step:
    rel: str
    dir: Direction

    def document(self) -> dict[str, str]:
        return {"rel": self.rel, "dir": self.dir}


Path = tuple[Step, ...]


def render(path: Path) -> str:
    """A path as a ``via`` a document can use."""
    return json.dumps([step.document() for step in path], separators=(",", ":"))


def down_steps(path: Path) -> int:
    return sum(step.dir == "down" for step in path)


@dataclass(frozen=True, slots=True)
class Edge:
    id: str
    child: str
    parent: str


@dataclass(frozen=True)
class Search:
    """The implicit paths found, up to the number asked for, and whether the search gave up."""

    paths: tuple[Path, ...]
    exhausted: bool
    too_long: bool = False
    """No path was found, but paths that visit no table twice exist, all of them longer than
    ``MAX_PATH_STEPS``."""


class Graph:
    def __init__(self, nodes: Iterable[str], edges: Iterable[Edge]) -> None:
        self.nodes = frozenset(nodes)
        self.edges = {edge.id: edge for edge in edges}
        self._from: dict[str, list[tuple[Step, str]]] = {node: [] for node in self.nodes}
        for edge in sorted(self.edges.values(), key=lambda edge: edge.id):
            if edge.child in self.nodes and edge.parent in self.nodes:
                self._from[edge.child].append((Step(edge.id, "up"), edge.parent))
                self._from[edge.parent].append((Step(edge.id, "down"), edge.child))
        self._searches: dict[tuple[str, str, int], Search] = {}

    @classmethod
    def of(cls, release: Release) -> "Graph":
        nodes = [table for table in release.table_ids if table not in release.coverage_tables]
        edges = [
            Edge(r.id, r.fields.child_table, r.fields.parent_table) for r in release.relationships
        ]
        return cls(nodes, edges)

    def target(self, table: str, step: Step) -> str | None:
        """The table a step from ``table`` reaches, or ``None`` if it does not leave it."""
        edge = self.edges.get(step.rel)
        if edge is None or edge.child not in self.nodes or edge.parent not in self.nodes:
            return None
        start, end = (edge.child, edge.parent) if step.dir == "up" else (edge.parent, edge.child)
        return end if start == table else None

    def follow(self, table: str, path: Sequence[Step]) -> list[str | None]:
        """The table after each step, ``None`` from the first that does not connect."""
        reached: list[str | None] = []
        current: str | None = table
        for step in path:
            current = None if current is None else self.target(current, step)
            reached.append(current)
        return reached

    def paths(self, source: str, target: str, limit: int = 2) -> Search:
        """Up to ``limit`` simple paths of at most ``MAX_PATH_STEPS`` steps from ``source`` to
        ``target``, in a stable order.

        The search follows only the relationships on some simple path between the two, so the
        rest of the graph, however dense, costs it nothing. A path from a table to itself is the
        empty path: any other returns to its start."""
        known = self._searches.get((source, target, limit))
        if known is None:
            known = self._searches[(source, target, limit)] = self._paths(source, target, limit)
        return known

    def _paths(self, source: str, target: str, limit: int) -> Search:
        if source == target:
            return Search(((),), exhausted=False)
        usable = self._between(source, target)
        if not usable:
            return Search((), exhausted=False)
        found: list[Path] = []
        budget = PATH_SEARCH_STEPS
        stack: list[tuple[str, Path, frozenset[str]]] = [(source, (), frozenset({source}))]
        while stack:
            table, path, visited = stack.pop()
            if len(path) >= MAX_PATH_STEPS:
                continue
            for step, reached in reversed(self._from[table]):
                if step.rel not in usable:
                    continue
                budget -= 1
                if budget < 0:
                    return Search(tuple(found), exhausted=True)
                if reached in visited:
                    continue
                if reached == target:
                    found.append((*path, step))
                    if len(found) >= limit:
                        return Search(tuple(sorted(found)), exhausted=False)
                    continue
                stack.append((reached, (*path, step), visited | {reached}))
        # A simple path exists (``usable`` is not empty): if none was found, all are too long.
        return Search(tuple(sorted(found)), exhausted=False, too_long=not found)

    def _between(self, source: str, target: str) -> frozenset[str]:
        """The relationships on some simple path from ``source`` to ``target``, or none if no
        path joins them.

        They are those of the block (biconnected component) that an edge joining the two tables
        would be in: a simple cycle through that edge is a simple path between them plus the
        edge. The blocks are found by Tarjan's depth-first search, without recursion."""
        if source not in self.nodes or target not in self.nodes:
            return frozenset()
        joining = ""  # the edge joining the two; no relationship id is empty
        neighbours: dict[str, list[tuple[str, str]]] = {node: [] for node in self.nodes}
        for edge in self.edges.values():
            if edge.child in self.nodes and edge.parent in self.nodes and edge.child != edge.parent:
                neighbours[edge.child].append((edge.id, edge.parent))
                neighbours[edge.parent].append((edge.id, edge.child))
        neighbours[source].append((joining, target))
        neighbours[target].append((joining, source))
        order = {source: 0}
        low = {source: 0}
        met: list[str] = []  # edges met and not yet assigned to a block
        stack: list[tuple[str, str | None, Iterator[tuple[str, str]]]] = [
            (source, None, iter(neighbours[source]))
        ]
        while stack:
            table, entered_by, pending = stack[-1]
            for rel, other in pending:
                if rel == entered_by:
                    continue
                if other not in order:
                    order[other] = low[other] = len(order)
                    met.append(rel)
                    stack.append((other, rel, iter(neighbours[other])))
                    break
                if order[other] < order[table]:  # an edge back to an ancestor
                    met.append(rel)
                    low[table] = min(low[table], order[other])
            else:
                stack.pop()
                if not stack:
                    break
                above = stack[-1][0]
                low[above] = min(low[above], low[table])
                if low[table] >= order[above]:
                    # The edges met since entering ``table`` form a block.
                    block: set[str] = set()
                    while True:
                        rel = met.pop()
                        block.add(rel)
                        if rel == entered_by:
                            break
                    if joining in block:
                        return frozenset(block - {joining})
        return frozenset()


__all__ = ["PATH_SEARCH_STEPS", "Edge", "Graph", "Path", "Search", "Step", "down_steps", "render"]

"""The resolved clause tree: a cohort's canonical form before sorting and hashing (SPEC §7.6).

Names are descriptor ids, paths are explicit, constants are typed, and every existence question
is an ``exists`` over one down step (§6.1). ``document`` writes a node with exactly the members of
§7.6's table; M2.2 sorts the order-insensitive collections and hashes the result.

Each node records the positions of the leaves of the document as written that it came from
(``origin``), which is not part of the form: it maps written leaves to top-level clauses (§6.6)
and places refusals. A clause inlined from a referenced cohort records the reference as written,
and the nodes below it the referenced cohort's own leaves.
"""

import json
from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
from typing import Literal, cast

from pydantic import JsonValue

from aibi.core.engine.graph import Path, Step
from aibi.core.schema.ids import MAX_SAFE_INTEGER
from aibi.core.schema.params import Position

Constant = int | float | str | bool | date | datetime
"""A typed constant: numbers in the predicate's units, datetimes in UTC (§6.4)."""
Quantifier = Literal["some", "every"]
Lift = Literal["strict", "assessed"]
Origin = frozenset[Position]


@dataclass(frozen=True, slots=True)
class Values:
    """Membership in a set of constants."""

    values: tuple[Constant, ...]


@dataclass(frozen=True, slots=True)
class Bounds:
    """A range: a lower bound (``gt`` or ``gte``) and/or an upper one (``lt`` or ``lte``)."""

    gt: Constant | None = None
    gte: Constant | None = None
    lt: Constant | None = None
    lte: Constant | None = None


Predicate = Values | Bounds


@dataclass(frozen=True, slots=True)
class RValue:
    """A value predicate on a column of the current row, or of a row its ``via`` looks up."""

    column: str
    predicate: Predicate
    via: Path = ()
    negate: bool = False
    units: str | None = None
    """For numeric columns: the constants' units, given or the column's."""
    match: Literal["any", "all"] | None = None
    """For list columns only."""
    origin: Origin = field(default=frozenset[Position](), compare=False, repr=False)


@dataclass(frozen=True, slots=True)
class RExists:
    """An existence question over one down step, the last step of ``via`` (§6.5)."""

    table: str
    via: Path
    quantifier: Quantifier
    where: "tuple[RClause, ...]"
    min_count: int | None = None
    """With ``some``: at least *k* children."""
    lift: Lift | None = None
    """Exactly on an intermediate question."""
    exclude_self: bool = False
    origin: Origin = field(default=frozenset[Position](), compare=False, repr=False)

    @property
    def step(self) -> Step:
        return self.via[-1]


@dataclass(frozen=True, slots=True)
class RCovered:
    """Coverage as a predicate, for the last down step of ``via`` (§6.5)."""

    table: str
    via: Path
    scope: tuple[tuple[str, tuple[Constant, ...]], ...] | None = None
    lift: Lift | None = None
    """Exactly when the path has more than one down step."""
    origin: Origin = field(default=frozenset[Position](), compare=False, repr=False)


@dataclass(frozen=True, slots=True)
class RIds:
    """Unit keys, each ``(manifest hash, typed key in key order)``."""

    keys: tuple[tuple[str, tuple[Constant, ...]], ...]
    origin: Origin = field(default=frozenset[Position](), compare=False, repr=False)


@dataclass(frozen=True, slots=True)
class RAll:
    members: "tuple[RClause, ...]"
    origin: Origin = field(default=frozenset[Position](), compare=False, repr=False)


@dataclass(frozen=True, slots=True)
class RAny:
    members: "tuple[RClause, ...]"
    origin: Origin = field(default=frozenset[Position](), compare=False, repr=False)


@dataclass(frozen=True, slots=True)
class RNot:
    member: "RClause"
    origin: Origin = field(default=frozenset[Position](), compare=False, repr=False)


@dataclass(frozen=True, slots=True)
class RKnown:
    member: "RClause"
    origin: Origin = field(default=frozenset[Position](), compare=False, repr=False)


@dataclass(frozen=True, slots=True)
class RUnknown:
    member: "RClause"
    origin: Origin = field(default=frozenset[Position](), compare=False, repr=False)


RLeaf = RValue | RExists | RCovered | RIds
RClause = RValue | RExists | RCovered | RIds | RAll | RAny | RNot | RKnown | RUnknown


# --- Construction: flattening and folding (§7.6, steps 4 and 6) -------------------------------


def origin_of(nodes: "tuple[RClause, ...] | list[RClause]") -> Origin:
    found: set[Position] = set()
    for node in nodes:
        found.update(node.origin)
    return frozenset(found)


def _with_origin(node: RClause, origin: Origin) -> RClause:
    """The node with ``origin`` added to its own."""
    return node if origin <= node.origin else replace(node, origin=node.origin | origin)


def all_of(members: "list[RClause] | tuple[RClause, ...]") -> RClause:
    """``all``: nested ``all`` members spliced, and a single member unwrapped. The ``all`` has
    the origins of the members as given, so a spliced one's own stay with it."""
    flat: list[RClause] = []
    for member in members:
        flat.extend(member.members if isinstance(member, RAll) else [member])
    if len(flat) == 1:
        return _with_origin(flat[0], origin_of(members))
    return RAll(tuple(flat), origin_of(members))


def any_of(members: "list[RClause] | tuple[RClause, ...]") -> RClause:
    """``any``: nested ``any`` members spliced, and a single member unwrapped, as ``all_of``."""
    flat: list[RClause] = []
    for member in members:
        flat.extend(member.members if isinstance(member, RAny) else [member])
    if len(flat) == 1:
        return _with_origin(flat[0], origin_of(members))
    return RAny(tuple(flat), origin_of(members))


def conjuncts(members: "list[RClause] | tuple[RClause, ...]") -> "tuple[RClause, ...]":
    """A ``where``: its ``all`` members spliced (a ``where`` is a conjunction)."""
    flat: list[RClause] = []
    for member in members:
        flat.extend(member.members if isinstance(member, RAll) else [member])
    return tuple(flat)


def single_valued(node: RClause) -> bool:
    """A value leaf on the row itself or a row it looks up, on a column that is not a list."""
    return isinstance(node, RValue) and node.match is None


def not_of(member: RClause, origin: Origin) -> RClause:
    """``not``: folded into a single-valued value leaf's ``negate``."""
    if isinstance(member, RValue) and single_valued(member):
        return replace(member, negate=not member.negate, origin=member.origin | origin)
    return RNot(member, member.origin | origin)


def prefixed(steps: Path, clauses: "tuple[RClause, ...]") -> "tuple[RClause, ...]":
    """The clauses with ``steps`` prefixed to the ``via`` of every leaf, through combinators
    but not into a nested question's ``where``: lookups made first (§7.6, step 5)."""
    if not steps:
        return clauses
    return tuple(_prefixed(steps, clause) for clause in clauses)


def _prefixed(steps: Path, clause: RClause) -> RClause:
    if isinstance(clause, RValue | RExists | RCovered):
        return replace(clause, via=(*steps, *clause.via))
    if isinstance(clause, RIds):
        return clause
    if isinstance(clause, RAll | RAny):
        return replace(clause, members=tuple(_prefixed(steps, m) for m in clause.members))
    return replace(clause, member=_prefixed(steps, clause.member))


def questions(clauses: "tuple[RClause, ...]") -> Iterator[RExists | RCovered]:
    """The questions in clauses, through combinators, not inside other questions."""
    pending = list(clauses)
    while pending:
        clause = pending.pop()
        if isinstance(clause, RExists | RCovered):
            yield clause
        elif isinstance(clause, RAll | RAny):
            pending.extend(clause.members)
        elif isinstance(clause, RNot | RKnown | RUnknown):
            pending.append(clause.member)


def intermediate(where: "tuple[RClause, ...]") -> bool:
    """Whether a question with this ``where`` holds a nested question (§6.5)."""
    return next(questions(where), None) is not None


def value_leaves(clauses: "tuple[RClause, ...]") -> Iterator[tuple[RValue, bool]]:
    """The value leaves in clauses, through combinators but not into questions, each with
    whether it is a top-level conjunct (a member of the list itself)."""
    pending: list[tuple[RClause, bool]] = [(clause, True) for clause in clauses]
    while pending:
        clause, top = pending.pop()
        if isinstance(clause, RValue):
            yield clause, top
        elif isinstance(clause, RAll | RAny):
            pending.extend((member, False) for member in clause.members)
        elif isinstance(clause, RNot | RKnown | RUnknown):
            pending.append((clause.member, False))


def flipped(node: RClause) -> RClause:
    """The node with every ``lift`` flipped, for ``lift_differs`` (§6.6)."""
    if isinstance(node, RExists):
        lift = None if node.lift is None else _FLIP[node.lift]
        return replace(node, lift=lift, where=tuple(flipped(clause) for clause in node.where))
    if isinstance(node, RCovered):
        return node if node.lift is None else replace(node, lift=_FLIP[node.lift])
    if isinstance(node, RAll | RAny):
        return replace(node, members=tuple(flipped(member) for member in node.members))
    if isinstance(node, RNot | RKnown | RUnknown):
        return replace(node, member=flipped(node.member))
    return node


_FLIP: dict[Lift, Lift] = {"strict": "assessed", "assessed": "strict"}


# --- The caps (§7.1) ----------------------------------------------------------------------------


def measure(
    clauses: "tuple[RClause, ...]", deduplicated: "Deduplicated | None" = None
) -> "tuple[tuple[RClause, ...], int, int]":
    """A cohort's canonical form whose top-level ``all`` holds ``clauses``, as the members of
    that ``all``, with its depth and number of leaves as §7.1 counts them for its caps.

    Step 8 of §7.6 removes duplicates from every ``all``, ``any`` and ``where``, and the steps
    before it apply again to what is left: a combinator left with one member is unwrapped into
    its parent, and a ``not`` left around a single-valued value leaf is folded into it. A kept
    clause takes the origins of its duplicates, and so do the members of a kept ``all``, by
    key, and a combinator unwrapped or spliced gives its own to each member it leaves, so every
    leaf as written maps to exactly the top-level clauses it became part of (§6.6). The order
    that step 8 also gives is M2.2's, and changes neither count. A node that cohort references
    share is done once, so the work grows with the form, not with the tree of references
    written.

    Every node without children counts as a leaf, an empty ``all`` or ``any`` included, so the
    form holds at most as many nodes as its depth times its leaves (D212). ``deduplicated``
    holds the nodes already done, such as the ``where`` lists read for mentions."""
    deduplicated = Deduplicated() if deduplicated is None else deduplicated
    top = deduplicated.done(RAll(tuple(clauses)))
    members = top.members if isinstance(top, RAll) else (top,)
    depth = max((deduplicated.depth(member) for member in members), default=0)
    return members, depth, sum(map(deduplicated.leaves, members))


class Deduplicated:
    """Nodes with duplicates removed as step 8 removes them, each node done once."""

    def __init__(self) -> None:
        self._keys: dict[int, tuple[RClause, object]] = {}
        """Identities by node; holding the node keeps its ``id`` from being reused."""
        self._done: dict[int, tuple[RClause, RClause]] = {}
        self._depths: dict[int, tuple[RClause, int]] = {}
        self._leaves: dict[int, tuple[RClause, int]] = {}
        self._predicates: dict[int, tuple[Predicate, object]] = {}

    def conjunction(self, clauses: "tuple[RClause, ...]") -> "tuple[RClause, ...]":
        """A ``where`` as step 8 leaves it: its clauses done, an ``all`` spliced in, without
        duplicates."""
        return self._unique(clauses, RAll)

    def done(self, node: RClause) -> RClause:
        if isinstance(node, RValue | RCovered | RIds):
            return node
        known = self._done.get(id(node))
        if known is None:
            known = self._done[id(node)] = (node, self._deduplicated(node))
        return known[1]

    def _deduplicated(self, node: RClause) -> RClause:
        if isinstance(node, RAll | RAny):
            members = self._unique(node.members, type(node))
            if len(members) == 1:
                return _with_origin(members[0], node.origin)
            return replace(node, members=members)
        if isinstance(node, RNot):
            member = self.done(node.member)
            if single_valued(member):
                return not_of(member, node.origin)
            return replace(node, member=member)
        if isinstance(node, RKnown | RUnknown):
            return replace(node, member=self.done(node.member))
        if isinstance(node, RExists):
            return replace(node, where=self._unique(node.where, RAll))
        return node

    def _unique(
        self, nodes: "tuple[RClause, ...]", kind: type[RAll] | type[RAny]
    ) -> "tuple[RClause, ...]":
        """The nodes done, a combinator of ``kind`` spliced in, without duplicates: the first of
        each, merged with the others."""
        found: dict[object, list[RClause]] = {}
        for node in nodes:
            done = self.done(node)
            if isinstance(done, kind):
                own = done.origin - origin_of(done.members)
                spliced = tuple(_with_origin(member, own) for member in done.members)
            else:
                spliced = (done,)
            for member in spliced:
                found.setdefault(self.key(member), []).append(member)
        return tuple(map(self._merged, found.values()))

    def _merged(self, duplicates: "list[RClause]") -> RClause:
        """The first of nodes that are done and duplicates of one another, with the origins of
        all of them; an ``all`` with its members merged in turn, by key.

        The origins below a node matter only where they become a top-level clause's (§6.6), and
        only the members of an ``all`` do, when it is spliced into its parent. An ``any`` that is
        done has at least two members or none, so it is never unwrapped again; what lies below it, a
        ``not``, ``known``, ``unknown`` or question stays below it, and is not walked."""
        kept = duplicates[0]
        others = list({id(node): node for node in duplicates[1:] if node is not kept}.values())
        if not others:
            return kept
        origin = set(kept.origin)
        for node in others:
            origin.update(node.origin)
        if isinstance(kept, RAll):
            found: dict[object, list[RClause]] = {}
            for member in kept.members:
                found[self.key(member)] = [member]
            for node in others:
                for member in cast(RAll, node).members:
                    found[self.key(member)].append(member)
            members = tuple(map(self._merged, found.values()))
            merged = replace(kept, members=members, origin=frozenset(origin))
        else:
            merged = replace(kept, origin=frozenset(origin))
        self._keys[id(merged)] = (merged, self.key(kept))
        return merged

    def depth(self, node: RClause) -> int:
        """Clause objects on the longest chain from the node to a leaf, both included."""
        known = self._depths.get(id(node))
        if known is None:
            if isinstance(node, RAll | RAny):
                below = max((self.depth(member) for member in node.members), default=0)
            elif isinstance(node, RNot | RKnown | RUnknown):
                below = self.depth(node.member)
            elif isinstance(node, RExists):
                below = max((self.depth(clause) for clause in node.where), default=0)
            else:
                below = 0
            known = self._depths[id(node)] = (node, 1 + below)
        return known[1]

    def leaves(self, node: RClause) -> int:
        """The leaves in the node, those inside every ``where`` included, and an empty ``all``
        or ``any`` as one."""
        known = self._leaves.get(id(node))
        if known is None:
            if isinstance(node, RAll | RAny):
                count = sum(map(self.leaves, node.members)) or 1
            elif isinstance(node, RNot | RKnown | RUnknown):
                count = self.leaves(node.member)
            elif isinstance(node, RExists):
                count = 1 + sum(map(self.leaves, node.where))
            else:
                count = 1
            known = self._leaves[id(node)] = (node, count)
        return known[1]

    def key(self, node: RClause) -> object:
        """The identity of a node that is done: equal for nodes that step 8 writes alike,
        whatever the order of their collections. A value leaf's is not kept: it is quick to
        make, and a reference inlines many copies of a leaf."""
        if isinstance(node, RValue):
            return self._key(node)
        known = self._keys.get(id(node))
        if known is None:
            known = self._keys[id(node)] = (node, self._key(node))
        return known[1]

    def _constants(self, predicate: Predicate) -> object:
        """A predicate's constants as step 8 writes them; a predicate is shared by the copies
        of a leaf that differ in origin only, such as those a cohort reference inlines."""
        known = self._predicates.get(id(predicate))
        if known is None:
            constants = (
                ("values", frozenset(map(_constant_key, predicate.values)))
                if isinstance(predicate, Values)
                else ("range", *(_bound_key(getattr(predicate, name)) for name in _BOUNDS))
            )
            known = self._predicates[id(predicate)] = (predicate, constants)
        return known[1]

    def _key(self, node: RClause) -> object:
        if isinstance(node, RAll | RAny):
            return (type(node).__name__, frozenset(map(self.key, node.members)))
        if isinstance(node, RNot | RKnown | RUnknown):
            return (type(node).__name__, self.key(node.member))
        if isinstance(node, RExists):
            fields = (node.table, node.via, node.quantifier, node.min_count, node.lift)
            return ("exists", *fields, node.exclude_self, frozenset(map(self.key, node.where)))
        if isinstance(node, RValue):
            constants = self._constants(node.predicate)
            fields = (node.column, constants, node.via, node.negate, node.units, node.match)
            return ("value", *fields)
        if isinstance(node, RCovered):
            scope = (
                None
                if node.scope is None
                else frozenset(
                    (column, frozenset(map(_constant_key, values))) for column, values in node.scope
                )
            )
            return ("covered", node.table, node.via, scope, node.lift)
        keys = frozenset((dataset, tuple(map(_constant_key, key))) for dataset, key in node.keys)
        return ("ids", keys)


_BOUNDS = ("gt", "gte", "lt", "lte")


def _constant_key(value: Constant) -> str:
    """A constant as the canonical form writes it, as text: equal for duplicates."""
    return json.dumps(constant_json(value))


def _bound_key(value: Constant | None) -> str | None:
    return None if value is None else _constant_key(value)


# --- The document form (§7.6, step 7) ----------------------------------------------------------


def constant_json(value: Constant) -> JsonValue:
    """A constant as the canonical form writes it: integers beyond ±(2^53 − 1) as decimal
    strings, integral numbers as integers, dates as ``YYYY-MM-DD`` and datetimes in UTC."""
    if isinstance(value, bool | str):
        return value
    if isinstance(value, float):
        if not value.is_integer():
            return value
        value = int(value)
    if isinstance(value, int):
        return value if abs(value) <= MAX_SAFE_INTEGER else str(value)
    if isinstance(value, datetime):
        utc = value.astimezone(UTC)
        text = utc.strftime("%Y-%m-%dT%H:%M:%S")
        if utc.microsecond:
            text += f".{utc.microsecond:06d}".rstrip("0")
        return text + "Z"
    return value.isoformat()


def _via(path: Path) -> list[JsonValue]:
    return [{"rel": step.rel, "dir": step.dir} for step in path]


def document(node: RClause) -> JsonValue:
    """The node with exactly the members of §7.6's table; absent members are omitted."""
    if isinstance(node, RValue):
        written: dict[str, JsonValue] = {"kind": "value", "column": node.column}
        if isinstance(node.predicate, Values):
            written["values"] = [constant_json(value) for value in node.predicate.values]
        else:
            bounds = {
                name: constant_json(bound)
                for name in ("gt", "gte", "lt", "lte")
                if (bound := getattr(node.predicate, name)) is not None
            }
            written["range"] = bounds
        if node.negate:
            written["negate"] = True
        if node.units is not None:
            written["units"] = node.units
        if node.match is not None:
            written["match"] = node.match
        if node.via:
            written["via"] = _via(node.via)
        return written
    if isinstance(node, RExists):
        written = {
            "kind": "exists",
            "table": node.table,
            "via": _via(node.via),
            "quantifier": node.quantifier,
        }
        if node.min_count is not None:
            written["min_count"] = node.min_count
        written["where"] = [document(clause) for clause in node.where]
        if node.lift is not None:
            written["lift"] = node.lift
        if node.exclude_self:
            written["exclude_self"] = True
        return written
    if isinstance(node, RCovered):
        written = {"kind": "covered", "table": node.table, "via": _via(node.via)}
        if node.scope is not None:
            written["scope"] = {
                column: [constant_json(value) for value in values] for column, values in node.scope
            }
        if node.lift is not None:
            written["lift"] = node.lift
        return written
    if isinstance(node, RIds):
        return {
            "kind": "ids",
            "ids": [
                {"dataset": dataset, "key": [constant_json(part) for part in key]}
                for dataset, key in node.keys
            ],
        }
    if isinstance(node, RAll):
        return {"all": [document(member) for member in node.members]}
    if isinstance(node, RAny):
        return {"any": [document(member) for member in node.members]}
    if isinstance(node, RNot):
        return {"not": document(node.member)}
    if isinstance(node, RKnown):
        return {"known": document(node.member)}
    return {"unknown": document(node.member)}


__all__ = [
    "Bounds",
    "Constant",
    "Deduplicated",
    "Lift",
    "Origin",
    "Predicate",
    "Quantifier",
    "RAll",
    "RAny",
    "RClause",
    "RCovered",
    "RExists",
    "RIds",
    "RKnown",
    "RLeaf",
    "RNot",
    "RUnknown",
    "RValue",
    "Values",
    "all_of",
    "any_of",
    "conjuncts",
    "constant_json",
    "document",
    "flipped",
    "intermediate",
    "measure",
    "not_of",
    "origin_of",
    "prefixed",
    "questions",
    "single_valued",
    "value_leaves",
]

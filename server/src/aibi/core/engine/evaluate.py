"""Evaluation, unit by unit and row by row (SPEC §6.2–§6.6).

``evaluate`` gives each unit of a resolved cohort its truth value, and the cohort's accounting:
``n_true``, ``n_false`` and ``n_unknown``, the unknown units by reason and by top-level clause,
``lift_differs``, and the flags the cohort raises. The rules are §6's, stated here where the
spec leaves a choice:

- A list cell that is not PRESENT takes its base result whatever ``negate`` and ``match`` say:
  ``negate`` applies to items.
- A range over an ordered category is UNKNOWN (``NO_INFORMATION``) for a PRESENT value outside
  the listed values, which has no place in their order.
- A constant in other units than its column's is converted by one multiplication of doubles, by
  the pinned factor rounded to a double, so that SQL computes the same comparison.
- ``covered`` carries ``SCOPE_PARTIAL`` only on a TRUE whose closedness was restricted to listed
  tuples.
"""

import math
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from functools import cached_property
from itertools import product
from typing import Any, cast

from aibi.core.engine.data import PRESENT, Cell, KeyPart, Release, key_part
from aibi.core.engine.graph import Path, Step
from aibi.core.engine.resolve import Coverage, ResolvedCohort
from aibi.core.engine.resolved import (
    Constant,
    RAll,
    RAny,
    RClause,
    RCovered,
    RExists,
    RIds,
    RKnown,
    RNot,
    RUnknown,
    RValue,
    Values,
    flipped,
    questions,
)
from aibi.core.engine.truth import (
    FALSE,
    TRUE,
    Mark,
    Truth,
    TruthValue,
    all_of,
    any_of,
    known,
    not_,
    truth,
    unknown,
    unknown_of,
)
from aibi.core.engine.units import factor
from aibi.core.schema.descriptors import DirectCoverage, GroupedCoverage
from aibi.core.schema.semantics import Flag, ObservationState, Reason

_DROP = {
    "strict": frozenset({Reason.OUT_OF_SCOPE}),
    "assessed": frozenset({Reason.OUT_OF_SCOPE, Reason.NOT_COVERED}),
}
"""The reasons for which an intermediate question drops a child, by lift rule (§6.5, step 3)."""


def _base(cell: Cell) -> TruthValue | None:
    """The base result of a cell that is not PRESENT (§6.4), or ``None`` for one that is."""
    if cell.state is PRESENT:
        return None
    if cell.state is ObservationState.NOT_APPLICABLE:
        return FALSE
    if cell.state is ObservationState.NOT_ASSESSED:
        return unknown({Reason.NOT_ASSESSED})
    return unknown({Reason.NO_INFORMATION})


@dataclass(frozen=True)
class _Closedness:
    closed: bool
    reasons: frozenset[Reason]
    """The closedness reasons, when not closed."""
    restricted: bool
    """Closed only for the listed scope tuples (``SCOPE_PARTIAL``)."""


_CLOSED = _Closedness(True, frozenset(), False)


@dataclass(frozen=True)
class _Listing:
    """What a coverage table lists for one parent (§5.6)."""

    listed: bool
    tuples: frozenset[tuple[KeyPart, ...]]
    every: bool
    """Listed for every scope tuple, through a group covering all of them."""


@dataclass(frozen=True)
class CohortResult:
    """A cohort's truth value per unit, and its accounting (§6.6)."""

    cohort: ResolvedCohort
    values: tuple[TruthValue, ...]
    """By row of the unit table."""
    n_true: int
    n_false: int
    n_unknown: int
    unknown_by_reason: Mapping[Reason, int]
    """Every reason, zeros included; a unit counts under each of its reasons."""
    unknown_by_clause: tuple[int, ...]
    """For each top-level clause, the unknown units for which it is UNKNOWN (M2.2 keys them
    ``leaf:<hash>``)."""
    lift_differs: int
    marks: frozenset[Mark]
    """The flags of every unit's cohort-level value, with their relationships."""

    @property
    def flags(self) -> frozenset[Flag]:
        return frozenset(mark.flag for mark in self.marks)

    @property
    def members(self) -> tuple[int, ...]:
        """The rows of the unit table in the cohort: its predicate is TRUE for them."""
        return tuple(row for row, value in enumerate(self.values) if value.is_true)


def evaluate(cohort: ResolvedCohort) -> CohortResult:
    evaluator = Evaluator(cohort)
    unit = cohort.unit
    rows = range(len(cohort.release.rows(unit)))
    values: list[TruthValue] = []
    by_reason = dict.fromkeys(Reason, 0)
    by_clause = [0] * len(cohort.clauses)
    marks: set[Mark] = set()
    for row in rows:
        clauses = [evaluator.truth(clause, unit, row) for clause in cohort.clauses]
        value = all_of(clauses)
        values.append(value)
        marks.update(value.marks)
        if value.is_unknown:
            for reason in value.reasons:
                by_reason[reason] += 1
            for index, clause in enumerate(clauses):
                if clause.is_unknown:
                    by_clause[index] += 1
    other = [flipped(clause) for clause in cohort.clauses]
    lift_differs = 0
    if any(_lifted(clause) for clause in cohort.clauses):
        for row in rows:
            flip = all_of(evaluator.truth(clause, unit, row) for clause in other)
            lift_differs += flip.value is not values[row].value
    return CohortResult(
        cohort,
        tuple(values),
        sum(value.is_true for value in values),
        sum(value.is_false for value in values),
        sum(value.is_unknown for value in values),
        by_reason,
        tuple(by_clause),
        lift_differs,
        frozenset(marks),
    )


def _lifted(clause: RClause) -> bool:
    """Whether a clause holds any lift."""
    return any(
        node.lift is not None for node in _nodes(clause) if isinstance(node, RExists | RCovered)
    )


def _nodes(clause: RClause) -> Iterator[RClause]:
    pending = [clause]
    while pending:
        node = pending.pop()
        yield node
        if isinstance(node, RExists):
            pending.extend(node.where)
        elif isinstance(node, RAll | RAny):
            pending.extend(node.members)
        elif isinstance(node, RNot | RKnown | RUnknown):
            pending.append(node.member)


class Evaluator:
    """Truth values of a resolved cohort's clauses, row by row."""

    def __init__(self, cohort: ResolvedCohort) -> None:
        self.cohort = cohort
        self.release: Release = cohort.release
        self.coverage: Mapping[str, Coverage] = cohort.coverage
        self._listings: dict[str, dict[tuple[KeyPart, ...], _Listing]] = {}
        self._answers: dict[int, tuple[RClause, dict[tuple[str, int], TruthValue]]] = {}
        """Each question's answers by row, as nested questions ask them again and again; holding
        the question keeps its ``id`` from being reused."""

    def truth(self, node: RClause, table: str, row: int) -> TruthValue:
        """The truth value of ``node`` for a row of ``table``."""
        if isinstance(node, RExists | RCovered):
            answers = self._answers.get(id(node))
            if answers is None:
                answers = self._answers[id(node)] = (node, {})
            answer = answers[1].get((table, row))
            if answer is None:
                answer = answers[1][table, row] = (
                    self._exists(node, table, row)
                    if isinstance(node, RExists)
                    else self._covered(node, table, row)
                )
            return answer
        if isinstance(node, RAll):
            return all_of(self.truth(member, table, row) for member in node.members)
        if isinstance(node, RAny):
            return any_of(self.truth(member, table, row) for member in node.members)
        if isinstance(node, RNot):
            return not_(self.truth(node.member, table, row))
        if isinstance(node, RKnown):
            return known(self.truth(node.member, table, row))
        if isinstance(node, RUnknown):
            return unknown_of(self.truth(node.member, table, row))
        if isinstance(node, RValue):
            return self._value(node, table, row)
        return self._ids(node, table, row)

    # --- Lookups (§6.1) ----------------------------------------------------------------------

    def _follow(self, steps: Iterable[Step], table: str, row: int) -> tuple[str, int] | None:
        """The row the up steps look up, or ``None`` for a null or dangling key."""
        for step in steps:
            relationship = self.release.relationship(step.rel)
            assert relationship is not None
            assert step.dir == "up"
            parent = self.release.parent(step.rel, row)
            if parent is None:
                return None
            table, row = relationship.fields.parent_table, parent
        return table, row

    # --- Value predicates (§6.4) -----------------------------------------------------------

    def _value(self, node: RValue, table: str, row: int) -> TruthValue:
        found = self._follow(node.via, table, row)
        if found is None:
            return unknown({Reason.NO_PARENT})
        owner, column = node.column.split(".", 1)
        cell = self.release.rows(owner).cell(found[1], column)
        base = _base(cell)
        if base is not None:
            # A list that is not PRESENT has no items for negate to apply to (§6.4).
            return base if node.match is not None or not node.negate else not_(base)
        if node.match is not None:
            assert isinstance(cell.value, tuple)
            return self._items(node, cell.value)
        result = self._compare(node, cell.value)
        return not_(result) if node.negate else result

    def _items(self, node: RValue, items: tuple[Cell, ...]) -> TruthValue:
        results: list[TruthValue] = []
        for item in items:
            base = _base(item)
            result = base if base is not None else self._compare(node, item.value)
            results.append(not_(result) if node.negate else result)
        if node.match == "any":
            return any_of(results)
        if not results:
            return unknown({Reason.NO_ROWS})
        return all_of(results)

    @cached_property
    def _order(self) -> Mapping[str, Mapping[str, int]]:
        """Positions of the listed values of every ordered category column."""
        order: dict[str, dict[str, int]] = {}
        for descriptor in self.release.descriptors:
            fields = getattr(descriptor, "fields", None)
            allowed = getattr(fields, "permissible_values", None)
            if allowed is not None and allowed.ordered:
                order[descriptor.id] = {entry.value: at for at, entry in enumerate(allowed.values)}
        return order

    def _compare(self, node: RValue, value: object) -> TruthValue:
        """TRUE or FALSE for a PRESENT value; UNKNOWN for an unlisted ordered category."""
        converted = self._converted(node)
        if isinstance(node.predicate, Values):
            return truth(any(_equal(value, constant) for constant in converted))
        order = self._order.get(node.column)
        if order is None:
            return truth(_within(value, *converted))
        if not isinstance(value, str) or value not in order:
            return unknown({Reason.NO_INFORMATION})
        positions = [None if bound is None else order[str(bound)] for bound in converted]
        return truth(_within(order[value], *positions))

    def _converted(self, node: RValue) -> list[Constant | None]:
        """The predicate's constants, for a range as ``gt, gte, lt, lte``, in the column's units:
        one multiplication of doubles by the pinned factor (§6.4)."""
        predicate = node.predicate
        constants: list[Constant | None] = (
            list(predicate.values)
            if isinstance(predicate, Values)
            else [predicate.gt, predicate.gte, predicate.lt, predicate.lte]
        )
        owner, column = node.column.split(".", 1)
        descriptor = self.release.column(owner, column)
        column_units = None if descriptor is None else descriptor.fields.units
        if node.units is None or column_units is None or node.units == column_units:
            return constants
        ratio = factor(node.units, column_units)
        assert ratio is not None, "units are checked when the document is resolved"
        scale = float(ratio)
        return [
            None if constant is None else float(cast(int | float, constant)) * scale
            for constant in constants
        ]

    # --- Existence questions (§6.5) ----------------------------------------------------------

    def _scope(self, coverage: Coverage, parent: int) -> TruthValue:
        """The parent scope's truth value for the parent row (step 2)."""
        if coverage.parent_scope is None:
            return TRUE
        return self.truth(coverage.parent_scope, coverage.parent_table, parent)

    def _filter(self, coverage: Coverage, child: int) -> TruthValue:
        """The record filter's truth value for a child row (step 1)."""
        rows = self.release.rows(coverage.child_table)
        parts: list[TruthValue] = []
        for column, allowed in coverage.record_filter:
            cell = rows.cell(child, column)
            base = _base(cell)
            parts.append(base if base is not None else truth(cell.value in allowed))
        return all_of(parts)

    def _exists(self, node: RExists, table: str, row: int) -> TruthValue:
        found = self._follow(node.via[:-1], table, row)
        if found is None:
            return unknown({Reason.NO_PARENT})
        parent = found[1]
        step = node.step
        coverage = self.coverage[step.rel]
        scope = self._scope(coverage, parent)
        if scope.is_false:
            return unknown({Reason.OUT_OF_SCOPE})
        children = self.release.children(step.rel, parent)
        if node.exclude_self:
            children = tuple(
                child for child in children if not (coverage.child_table == table and child == row)
            )
        some = node.quantifier == "some"
        evaluated: list[tuple[int, TruthValue]] = []
        for child in children:
            value = all_of(self.truth(clause, coverage.child_table, child) for clause in node.where)
            if coverage.record_filter:
                filtered = self._filter(coverage, child)
                value = all_of((value, filtered)) if some else any_of((not_(filtered), value))
            evaluated.append((child, value))
        final = node.lift is None
        drop = frozenset[Reason]() if final else _DROP[node.lift or "strict"]
        kept = [(c, v) for c, v in evaluated if not (v.is_unknown and v.reasons <= drop)]
        dropped = [v for _, v in evaluated if v.is_unknown and v.reasons <= drop]
        closedness = self._closed(coverage, parent, scope, self._admitted(node), node.quantifier)
        answer = _Answer(coverage, closedness, [v for _, v in kept], dropped)
        if some:
            count = node.min_count or 1
            if len(answer.true) >= count:
                return answer.value(Truth.TRUE, answer.true)
            if len(answer.true) + len(answer.unknown) >= count:
                return answer.unknown_value(_reasons(answer.unknown))
            if final:
                if closedness.closed:
                    return answer.closed_value(Truth.FALSE)
                return answer.unknown_value(frozenset())
            conditions = [self._conditions(node, coverage.child_table, c) for c, _ in kept]
            met = any(condition.is_true for condition in conditions)
            if closedness.closed and met:
                return answer.closed_value(Truth.FALSE)
            reasons: frozenset[Reason] = frozenset()
            if not met:
                reasons = frozenset({Reason.NOT_COVERED}) | _reasons(
                    [condition for condition in conditions if condition.is_unknown]
                )
            return answer.unknown_value(reasons)
        if answer.false:
            return answer.value(Truth.FALSE, answer.false)
        if not kept:
            return answer.unknown_value(
                frozenset({Reason.NO_ROWS if final else Reason.NOT_COVERED})
            )
        if answer.unknown:
            return answer.unknown_value(_reasons(answer.unknown))
        if closedness.closed:
            return answer.closed_value(Truth.TRUE)
        return answer.unknown_value(frozenset())

    def _conditions(self, node: RExists, table: str, row: int) -> TruthValue:
        """A child's conditions: its top-level ``where`` clauses without a nested question."""
        return all_of(
            self.truth(clause, table, row)
            for clause in node.where
            if next(questions((clause,)), None) is None
        )

    @staticmethod
    def _admitted(node: RExists) -> dict[str, frozenset[KeyPart]]:
        """The values ``W_C`` admits on each scope column it mentions, in top-level ``values``
        conjuncts on the child row (the only mentions resolution allows)."""
        admitted: dict[str, frozenset[KeyPart]] = {}
        for clause in node.where:
            if (
                isinstance(clause, RValue)
                and not clause.via
                and not clause.negate
                and isinstance(clause.predicate, Values)
                and clause.column.split(".", 1)[0] == node.table
            ):
                column = clause.column.split(".", 1)[1]
                values = frozenset(key_part(value) for value in clause.predicate.values)
                admitted[column] = admitted[column] & values if column in admitted else values
        return admitted

    # --- Closedness (§6.5, step 4) -----------------------------------------------------------

    def _closed(
        self,
        coverage: Coverage,
        parent: int,
        scope: TruthValue,
        admitted: Mapping[str, frozenset[KeyPart]],
        quantifier: str,
    ) -> _Closedness:
        if coverage.form == "undeclared":
            return _Closedness(False, frozenset({Reason.NO_INFORMATION}), False)
        if coverage.form == "all":
            if scope.is_unknown:
                return _Closedness(False, scope.reasons, False)
            return _CLOSED
        listing = self._listing(coverage, parent)
        not_covered = _Closedness(False, frozenset({Reason.NOT_COVERED}), False)
        if not coverage.scope_columns:
            return _CLOSED if listing.listed else not_covered
        if listing.every:
            return _CLOSED
        if quantifier == "every":
            return _Closedness(True, frozenset(), True) if listing.tuples else not_covered
        columns = [c for c in coverage.scope_columns if c in admitted]
        positions = [coverage.scope_columns.index(c) for c in columns]
        listed = {tuple(scope_tuple[at] for at in positions) for scope_tuple in listing.tuples}
        # Each combination W_C admits must be listed, so there can be no more of them. With no
        # scope column mentioned, the one empty combination is listed if any tuple is; with a
        # column on which W_C admits no value there is no combination, and r is closed.
        if math.prod(len(admitted[column]) for column in columns) > len(listed):
            return not_covered
        combinations = product(*(admitted[column] for column in columns))
        if not all(combination in listed for combination in combinations):
            return not_covered
        return _Closedness(True, frozenset(), len(columns) < len(coverage.scope_columns))

    def _listing(self, coverage: Coverage, parent: int) -> _Listing:
        """What the coverage lists for a parent row, from its coverage tables (§5.6)."""
        by_key = self._listings.get(coverage.relationship)
        if by_key is None:
            by_key = self._listings[coverage.relationship] = self._index(coverage)
        descriptor = coverage.descriptor
        assert descriptor is not None
        parents = descriptor.fields.parents
        mapping = (
            parents.parent_columns
            if isinstance(parents, DirectCoverage)
            else parents.assignment.parent_columns
            if isinstance(parents, GroupedCoverage)
            else {}
        )
        key = self.release.key(coverage.parent_table, parent, list(mapping.values()))
        empty = _Listing(False, frozenset(), False)
        return empty if key is None else by_key.get(key, empty)

    def _index(self, coverage: Coverage) -> dict[tuple[KeyPart, ...], _Listing]:
        descriptor = coverage.descriptor
        assert descriptor is not None
        parents = descriptor.fields.parents
        release = self.release
        found: dict[tuple[KeyPart, ...], tuple[bool, set[tuple[KeyPart, ...]], bool]] = {}
        if isinstance(parents, DirectCoverage):
            table = parents.table
            scope_map = parents.scope_columns or {}
            scope_columns = [
                next(k for k, v in scope_map.items() if v == column)
                for column in coverage.scope_columns
            ]
            for row in range(len(release.rows(table))):
                key = release.key(table, row, list(parents.parent_columns))
                if key is None:
                    continue
                _, tuples, every = found.get(key, (False, set(), False))
                scope_tuple = release.key(table, row, scope_columns) if scope_columns else ()
                if scope_tuple is not None:
                    tuples.add(scope_tuple)
                found[key] = (True, tuples, every)
        elif isinstance(parents, GroupedCoverage):
            assignment, groups = parents.assignment, parents.groups
            scope_map = groups.scope_columns or {}
            scope_columns = [
                next(k for k, v in scope_map.items() if v == column)
                for column in coverage.scope_columns
            ]
            by_group: dict[tuple[KeyPart, ...], tuple[set[tuple[KeyPart, ...]], bool]] = {}
            for row in range(len(release.rows(groups.table))):
                group = release.key(groups.table, row, [groups.group_column])
                if group is None:
                    continue
                tuples, every = by_group.get(group, (set(), False))
                if scope_columns:
                    scope_tuple = release.key(groups.table, row, scope_columns)
                    if scope_tuple is not None:
                        tuples.add(scope_tuple)
                if groups.covers_all_column is not None:
                    cell = release.rows(groups.table).cell(row, groups.covers_all_column)
                    every = every or (cell.state is PRESENT and cell.value is True)
                by_group[group] = (tuples, every)
            for row in range(len(release.rows(assignment.table))):
                key = release.key(assignment.table, row, list(assignment.parent_columns))
                group = release.key(assignment.table, row, [assignment.group_column])
                if key is None or group is None or group not in by_group:
                    continue
                _, tuples, every = found.get(key, (False, set(), False))
                group_tuples, group_every = by_group[group]
                found[key] = (True, tuples | group_tuples, every or group_every)
        return {
            key: _Listing(listed, frozenset(tuples), every)
            for key, (listed, tuples, every) in found.items()
        }

    # --- covered (§6.5) ----------------------------------------------------------------------

    def _covered(self, node: RCovered, table: str, row: int) -> TruthValue:
        segments: list[tuple[Path, Step]] = []
        ups: list[Step] = []
        for step in node.via:
            if step.dir == "up":
                ups.append(step)
            else:
                segments.append((tuple(ups), step))
                ups = []
        return self._covered_from(node, segments, 0, table, row)

    def _covered_from(
        self,
        node: RCovered,
        segments: Sequence[tuple[Path, Step]],
        index: int,
        table: str,
        row: int,
    ) -> TruthValue:
        ups, down = segments[index]
        found = self._follow(ups, table, row)
        if found is None:
            return unknown({Reason.NO_PARENT})
        parent = found[1]
        coverage = self.coverage[down.rel]
        scope = self._scope(coverage, parent)
        if scope.is_false:
            return unknown({Reason.OUT_OF_SCOPE})
        proposed = _proposed(coverage)
        if index == len(segments) - 1:
            admitted = {
                column: frozenset(key_part(value) for value in values)
                for column, values in node.scope or ()
            }
            closedness = self._closed(coverage, parent, scope, admitted, "some")
            if closedness.closed:
                partial = _partial(coverage) if closedness.restricted else frozenset[Mark]()
                return TruthValue(Truth.TRUE, frozenset(), partial | proposed)
            if coverage.form == "undeclared":
                return unknown({Reason.NO_INFORMATION})
            if scope.is_unknown:
                return unknown(scope.reasons)
            return TruthValue(Truth.FALSE, frozenset(), proposed)
        child_table = coverage.child_table
        values = [
            self._covered_from(node, segments, index + 1, child_table, child)
            for child in self.release.children(down.rel, parent)
        ]
        lift = node.lift or "strict"
        drop = _DROP[lift]
        kept = [v for v in values if not (v.is_unknown and v.reasons <= drop)]
        dropped = [v for v in values if v.is_unknown and v.reasons <= drop]
        closedness = self._closed(
            coverage, parent, scope, {}, "every" if lift == "strict" else "some"
        )
        answer = _Answer(coverage, closedness, kept, dropped)
        if not kept:
            return answer.unknown_value(frozenset({Reason.NOT_COVERED}))
        if lift == "strict":
            if answer.false:
                return answer.value(Truth.FALSE, answer.false)
            if answer.unknown:
                return answer.unknown_value(_reasons(answer.unknown))
            if closedness.closed:
                return answer.closed_value(Truth.TRUE)
            return answer.unknown_value(frozenset())
        if closedness.closed and answer.true:
            return answer.value(Truth.TRUE, answer.true).marked(answer.coverage_marks())
        if closedness.closed and len(answer.false) == len(kept):
            return answer.closed_value(Truth.FALSE)
        return answer.unknown_value(_reasons(answer.unknown))

    # --- ids (§7.2) ----------------------------------------------------------------------------

    def _ids(self, node: RIds, table: str, row: int) -> TruthValue:
        columns = self.release.primary_key(table) or ()
        key = self.release.key(table, row, columns)
        keys = {tuple(key_part(value) for value in given) for _, given in node.keys}
        return truth(key is not None and key in keys)


class _Answer:
    """The children of an existence question, sorted by value, and the rules of steps 5 and 7
    for its answer."""

    def __init__(
        self,
        coverage: Coverage,
        closedness: _Closedness,
        kept: Sequence[TruthValue],
        dropped: Sequence[TruthValue],
    ) -> None:
        self.coverage = coverage
        self.closedness = closedness
        self.kept = kept
        self.dropped = dropped
        self.true = [value for value in kept if value.is_true]
        self.false = [value for value in kept if value.is_false]
        self.unknown = [value for value in kept if value.is_unknown]

    def value(self, value: Truth, carriers: Sequence[TruthValue]) -> TruthValue:
        """TRUE from ``some`` or FALSE from ``every``: the flags of the children that decide."""
        return TruthValue(value, frozenset(), _marks(carriers))

    def coverage_marks(self) -> frozenset[Mark]:
        partial = _partial(self.coverage) if self.closedness.restricted else frozenset[Mark]()
        return partial | _proposed(self.coverage)

    def closed_value(self, value: Truth) -> TruthValue:
        """FALSE from ``some`` or TRUE from ``every``, which rely on closedness: the flags of
        every child, remaining or dropped, and the coverage's (step 7)."""
        marks = _marks([*self.kept, *self.dropped]) | self.coverage_marks()
        return TruthValue(value, frozenset(), marks)

    def unknown_value(self, reasons: frozenset[Reason]) -> TruthValue:
        """UNKNOWN, with the closedness reason when not closed (step 5), and the flags of the
        unknown and dropped children (step 7)."""
        marks = _marks([*self.unknown, *self.dropped])
        if not self.closedness.closed:
            reasons = reasons | self.closedness.reasons
            marks = marks | _proposed(self.coverage)
        return unknown(reasons, marks)


def _reasons(values: Iterable[TruthValue]) -> frozenset[Reason]:
    found: set[Reason] = set()
    for value in values:
        found.update(value.reasons)
    return frozenset(found)


def _marks(values: Iterable[TruthValue]) -> frozenset[Mark]:
    found: set[Mark] = set()
    for value in values:
        found.update(value.marks)
    return frozenset(found)


def _proposed(coverage: Coverage) -> frozenset[Mark]:
    if not coverage.proposed:
        return frozenset()
    return frozenset({Mark(Flag.COVERAGE_PROPOSED, coverage.relationship)})


def _partial(coverage: Coverage) -> frozenset[Mark]:
    return frozenset({Mark(Flag.SCOPE_PARTIAL, coverage.relationship)})


def _equal(value: object, constant: Constant | None) -> bool:
    """Equality as JSON values have it: booleans apart from numbers."""
    if constant is None:
        return False
    return key_part(cast(Constant, value)) == key_part(constant)


def _within(value: Any, gt: Any, gte: Any, lt: Any, lte: Any) -> bool:
    """Whether a value of an ordered type lies within the bounds given."""
    return (
        (gt is None or value > gt)
        and (gte is None or value >= gte)
        and (lt is None or value < lt)
        and (lte is None or value <= lte)
    )


__all__ = ["CohortResult", "Evaluator", "evaluate"]

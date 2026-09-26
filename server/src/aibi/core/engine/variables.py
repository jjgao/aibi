"""Variables, one value per unit, by the reference evaluator (SPEC §9.2, §13.3; D325, D326).

A variable (``resolve.ResolvedVariable``) gives each unit of the unit table a value, or excludes
it with its reasons (``UnitValue``):

- **A column** of the unit table, or of the row its up steps look up: the cell's value when it is
  PRESENT; otherwise the unit is excluded, ``NOT_APPLICABLE``, ``NOT_ASSESSED`` or
  ``NO_INFORMATION`` by the cell's state, or ``NO_PARENT`` where a step meets a null or dangling
  key.
- **A question** (``some`` or ``every``): its truth value, TRUE or FALSE; an UNKNOWN unit is
  excluded under each of its reasons (§6.5).
- **An aggregate** (``count``, ``max``, ``min``, ``mean``) of the rows reached at the last down
  step of its path, **pooled** through the rows kept at every earlier step (§9.2):

  - at each step, from the row it starts from, its up steps are followed (``NO_PARENT`` where
    they reach no row) to the parent row; a parent row outside the relationship's parent scope
    is ``OUT_OF_SCOPE``; one that is not closed (§6.5, step 4, for the values the last step's
    conditions admit on its scope columns, which resolution requires to be restricted) adds its
    closedness reasons;
  - at an earlier step each child row is pooled through in turn, and one whose own pooling is
    unknown only for reasons its lift drops (``OUT_OF_SCOPE`` under ``strict``, and
    ``NOT_COVERED`` under ``assessed``) is dropped; with no child kept the step adds
    ``NOT_COVERED``, and a kept child that is unknown adds its reasons;
  - at the last step a child row whose conditions (its ``where`` and the record filter) are TRUE
    is pooled, one whose conditions are FALSE is not, and one whose conditions are UNKNOWN adds
    their reasons;
  - a unit with any reason is excluded under them all, and otherwise its pooled rows are
    aggregated: ``count`` counts them whatever their values; ``max``, ``min`` and ``mean`` read
    each row's value through the lookups after the last down step, skip a NOT_APPLICABLE one,
    and are excluded for an UNKNOWN or NOT_ASSESSED one (``NO_INFORMATION``, ``NOT_ASSESSED``)
    or a lookup that reaches no row (``NO_PARENT``); with no value to aggregate, the unit takes
    ``empty``, or is excluded ``NO_ROWS``. ``max`` and ``min`` of an ordered category compare
    the listed order, and a value outside it is ``NO_INFORMATION``, as a range predicate has it
    (§6.4). ``mean`` pools: the mean of every row's value, never a mean of means.

  A unit's flags are those of every truth value its pooling read (the conditions, and the
  children's own at every step), with ``COVERAGE_PROPOSED`` for every step over proposed
  coverage whose parent row is in scope, and ``SCOPE_PARTIAL`` for one closed only for listed
  scope tuples.

Values are Python's, in the stored types the SQL compiler reads (``store.tables.physical``):
a ``number`` or ``time_offset`` as a double, an ``integer`` as an integer, a ``category`` as its
string, a ``boolean`` as a bool, and a question's TRUE or FALSE as a bool. A double's ``-0.0`` is
``0.0``, as equality has it. ``mean`` is exact for integers (Python's correctly rounded division
of their exact sum) and ``math.fsum`` over doubles, divided once, the doubles scaled by a power of
two so that the sum cannot overflow (§9.3, D321).

``materialise`` counts a variable over one cohort's units (``Materialised``): the units that
have each value, and those excluded under each reason and in all, with their flags; the SQL
compiler counts the same (``sql.compile_materialised``), and the differential tests hold the
two together (§13.3).
"""

import math
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from itertools import repeat
from types import MappingProxyType

from aibi.core.engine.data import PRESENT
from aibi.core.engine.evaluate import Evaluator, Pooled
from aibi.core.engine.graph import Path
from aibi.core.engine.resolve import Function, ResolvedCohort, ResolvedVariable, levels
from aibi.core.engine.truth import Mark, TruthValue
from aibi.core.schema.semantics import ExclusionReason, ObservationState, Reason

Value = int | float | str | bool
"""A unit's value of a variable, in the stored type of its column (module docstring)."""

_DOUBLES = ("number", "time_offset")
_STATE_REASON = {
    ObservationState.NOT_APPLICABLE: ExclusionReason.NOT_APPLICABLE,
    ObservationState.NOT_ASSESSED: ExclusionReason.NOT_ASSESSED,
}


def excluded_reason(reason: Reason) -> ExclusionReason:
    """A truth value's reason as an exclusion reason: the same name (§6.6)."""
    return ExclusionReason(reason.value)


@dataclass(frozen=True)
class UnitValue:
    """A unit's value of a variable, or the reasons it is excluded, and its flags."""

    value: Value | None
    excluded: frozenset[ExclusionReason] = frozenset()
    marks: frozenset[Mark] = frozenset()

    def __post_init__(self) -> None:
        if (self.value is None) == (not self.excluded):
            raise ValueError("a unit has a value, or is excluded for a reason")


@dataclass(frozen=True)
class Joint:
    """Several variables over one cohort's units together: the units that have a value of some
    variable, and those that have none, under every reason any variable gave them (every reason
    listed, zeros included)."""

    known: int
    none: int
    none_by_reason: Mapping[ExclusionReason, int]


def joint(values: Sequence[Sequence[UnitValue]], members: Iterable[int]) -> Joint:
    """Variables over a cohort's units: each variable's ``values`` by row of the unit table, and
    the rows of the cohort's members."""
    known = none = 0
    by_reason = dict.fromkeys(ExclusionReason, 0)
    for row in members:
        found = [variable[row] for variable in values]
        if any(value.value is not None for value in found):
            known += 1
            continue
        none += 1
        for reason in {reason for value in found for reason in value.excluded}:
            by_reason[reason] += 1
    return Joint(known, none, MappingProxyType(by_reason))


@dataclass(frozen=True)
class Materialised:
    """A variable over one cohort's units (``materialise``): each value and the units that have
    it, the units excluded, under each reason (every reason listed, zeros included; a unit
    counts under each of its reasons) and once each, and their flags together."""

    values: Mapping[Value, int]
    excluded_units: int
    excluded: Mapping[ExclusionReason, int]
    marks: frozenset[Mark]

    @property
    def n(self) -> int:
        """The units that have a value."""
        return sum(self.values.values())


def normalised(value: Value, datatype: str | None, function: Function | None = None) -> Value:
    """A value in the stored type of its column (module docstring); ``mean`` and ``count``
    give a double and an integer whatever the column's type."""
    if function == "count":
        return int(value)
    if isinstance(value, bool):
        return value
    if function == "mean" or (datatype in _DOUBLES and isinstance(value, int | float)):
        return float(value) + 0.0
    if isinstance(value, float):
        return value + 0.0
    return value


def aggregated(function: Function, values: Iterable[tuple[Value, int]], rows: int) -> Value | None:
    """``function`` of a unit's pooled rows: each value read and how many rows hold it, and the
    number of pooled rows (``count``); ``None`` when no value is left to aggregate."""
    if function == "count":
        return rows
    given = [(value, times) for value, times in values if times]
    if not given:
        return None
    if function == "max":
        return max(value for value, _ in given)
    if function == "min":
        return min(value for value, _ in given)
    total = sum(times for _, times in given)
    if all(isinstance(value, int) and not isinstance(value, bool) for value, _ in given):
        return sum(int(value) * times for value, times in given) / total
    largest = max(abs(float(value)) for value, _ in given)
    shift = math.frexp(largest)[1] if largest else 0
    summed = math.fsum(
        v for value, times in given for v in repeat(math.ldexp(float(value), -shift), times)
    )
    return math.ldexp(summed / total, shift)


def materialise(values: Sequence[UnitValue], members: Iterable[int]) -> Materialised:
    """A variable over a cohort's units: ``values`` by row of the unit table, and the rows of
    the cohort's members."""
    counted: Counter[Value] = Counter()
    excluded = dict.fromkeys(ExclusionReason, 0)
    excluded_units = 0
    marks: set[Mark] = set()
    for row in members:
        found = values[row]
        marks.update(found.marks)
        if found.value is None:
            excluded_units += 1
            for reason in found.excluded:
                excluded[reason] += 1
        else:
            counted[found.value] += 1
    return Materialised(
        MappingProxyType(dict(counted)),
        excluded_units,
        MappingProxyType(excluded),
        frozenset(marks),
    )


# --- Evaluation --------------------------------------------------------------------------------


def evaluate_variable(variable: ResolvedVariable) -> tuple[UnitValue, ...]:
    """Each unit's value of a variable, by row of the unit table (module docstring)."""
    reader = _Reader(variable)
    return tuple(reader.unit(row) for row in range(len(variable.release.rows(variable.unit))))


class _Reader:
    def __init__(self, variable: ResolvedVariable) -> None:
        self.variable = variable
        self.release = variable.release
        cohort = ResolvedCohort(
            variable.key,
            "",
            variable.release,
            variable.unit,
            (),
            {},
            variable.fields,
            variable.coverage,
        )
        self.evaluator = Evaluator(cohort)
        self.order: dict[str, int] | None = (
            None if variable.order is None else {v: at for at, v in enumerate(variable.order)}
        )

    def unit(self, row: int) -> UnitValue:
        variable = self.variable
        if variable.kind == "question":
            assert variable.question is not None
            found = self.evaluator.truth(variable.question, variable.unit, row)
            return _truth(found)
        if variable.kind == "column":
            return self._cell(variable.via, variable.unit, row)
        assert variable.rows is not None
        pooled = self.evaluator.pool(levels(variable.rows, variable.depth), 0, variable.unit, row)
        if pooled.reasons:
            reasons = frozenset(map(excluded_reason, pooled.reasons))
            return UnitValue(None, reasons, pooled.marks)
        return self._aggregate(pooled)

    def _cell(self, via: Path, table: str, row: int) -> UnitValue:
        variable = self.variable
        found = self.evaluator.follow(via, table, row)
        if found is None:
            return UnitValue(None, frozenset({ExclusionReason.NO_PARENT}))
        owner, column = variable.column.split(".", 1)
        cell = self.release.rows(owner).cell(found[1], column)
        if cell.state is not PRESENT:
            reason = _STATE_REASON.get(cell.state, ExclusionReason.NO_INFORMATION)
            return UnitValue(None, frozenset({reason}))
        assert not isinstance(cell.value, tuple)
        assert cell.value is not None
        return UnitValue(_stored(cell.value, variable.datatype))

    def _aggregate(self, pooled: Pooled) -> UnitValue:
        variable = self.variable
        function = variable.function
        assert function is not None
        if function == "count":
            return UnitValue(len(pooled.rows), marks=pooled.marks)
        rows = variable.rows
        assert rows is not None
        table = levels(rows, variable.depth)[-1].table
        values: Counter[Value] = Counter()
        reasons: set[ExclusionReason] = set()
        for row in pooled.rows:
            read = self._cell(variable.lookup, table, row)
            if read.value is None:
                reasons.update(read.excluded - {ExclusionReason.NOT_APPLICABLE})
                continue
            value = read.value
            if self.order is not None:
                if value not in self.order:
                    reasons.add(ExclusionReason.NO_INFORMATION)
                    continue
                value = self.order[str(value)]
            values[value] += 1
        if reasons:
            return UnitValue(None, frozenset(reasons), pooled.marks)
        found = aggregated(function, values.items(), len(pooled.rows))
        if found is None:
            empty = variable.empty
            if empty is None:
                return UnitValue(None, frozenset({ExclusionReason.NO_ROWS}), pooled.marks)
            return UnitValue(_stored(empty, variable.datatype, function), marks=pooled.marks)
        if self.order is not None:
            found = variable.order[int(found)] if variable.order is not None else found
        return UnitValue(normalised(found, variable.datatype, function), marks=pooled.marks)


def _stored(value: object, datatype: str | None, function: Function | None = None) -> Value:
    assert isinstance(value, int | float | str | bool)
    return normalised(value, datatype, function)


def _truth(found: TruthValue) -> UnitValue:
    if found.is_unknown:
        return UnitValue(None, frozenset(map(excluded_reason, found.reasons)), found.marks)
    return UnitValue(found.is_true, marks=found.marks)


__all__ = [
    "Joint",
    "Materialised",
    "UnitValue",
    "Value",
    "aggregated",
    "evaluate_variable",
    "excluded_reason",
    "joint",
    "materialise",
    "normalised",
]

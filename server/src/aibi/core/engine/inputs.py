"""The inputs of a pack's analysis: a cohort's members with each variable's value, by the
reference evaluator, and their order (SPEC §9.3, §10.1, §13.3; D342).

A pack's analysis is handed, per cohort position, the cohort's members (its units for which it is
TRUE) with each variable's value or the reasons it is excluded (§9.2, D326). ``listed`` gives them
by the reference evaluator: the members' keys (``members.keys``) and each variable's value of each
(``variables.evaluate_variable``), in row order. The SQL compiler gives the same
(``sql.compile_inputs``), and a differential property holds the two together (§13.3).

``ordered`` puts a listing in the order of §9.3, by its members' unit keys (their RFC 8785
serialisation compared as UTF-16 code units, ``members.ranked``), so that the inputs do not
depend on how the rows are stored or which engine read them. The keys order the members and are
never handed to a pack (``analyses.packs``): an analysis reads values, not identities.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import overload

from aibi.core.engine.evaluate import evaluate
from aibi.core.engine.members import Key, keys, ranked
from aibi.core.engine.resolve import ResolvedCohort, ResolvedVariable
from aibi.core.engine.truth import Mark
from aibi.core.engine.variables import UnitValue, Value, evaluate_variable
from aibi.core.schema.semantics import ExclusionReason


@dataclass(frozen=True)
class Listed:
    """A cohort's members with each variable's value (module docstring): their keys, their rows
    of the unit table (which only tell members apart, so that the units two cohorts share are
    found), and per variable each member's value (``None`` where it is excluded) and reasons
    (empty where it has a value), aligned with the keys, and the flags of every member's value
    together."""

    keys: Sequence[Key]
    rows: Sequence[int]
    values: tuple[tuple[Value | None, ...], ...]
    excluded: tuple[tuple[frozenset[ExclusionReason], ...], ...]
    marks: tuple[frozenset[Mark], ...]

    def __post_init__(self) -> None:
        if len(self.values) != len(self.excluded) or len(self.values) != len(self.marks):
            raise ValueError("a listing has values, reasons and flags per variable")
        if len(self.rows) != len(self.keys):
            raise ValueError("a listing has a row per member")
        for values, excluded in zip(self.values, self.excluded, strict=True):
            if len(values) != len(self.keys) or len(excluded) != len(self.keys):
                raise ValueError("a listing has a value or reasons per member and variable")

    @property
    def members(self) -> int:
        return len(self.keys)


class TooManyCells(ValueError):  # noqa: N818 - raised like a limit's refusal
    """Inputs of more than ``most`` cells, one per member and column over a view's positions
    (``MAX_INPUT_CELLS``, D342); ``cells`` as many as were counted before they were refused."""

    def __init__(self, cells: int, most: int) -> None:
        super().__init__(f"{cells} cells, more than {most}")
        self.cells = cells
        self.most = most
        self.listing: int | None = None
        """Which of a run's listings of inputs passed it (``queries.run_views``)."""


def cells(listed: Sequence["Listed"]) -> int:
    """The cells of a view's inputs: each position's members times its columns, one at least."""
    return sum(one.members * max(1, len(one.values)) for one in listed)


def shared(listed: Sequence[Listed]) -> list[tuple[int, int]]:
    """The pairs of positions whose members share units, in ``sql.pairs``' order."""
    held = [frozenset(one.rows) for one in listed]
    return [
        (first, second)
        for first in range(len(held))
        for second in range(first + 1, len(held))
        if held[first] & held[second]
    ]


def of_values(
    given: Sequence[Key], rows: Sequence[int], values: Sequence[Sequence[UnitValue]]
) -> Listed:
    """A listing from each member's key and row and each variable's ``UnitValue`` of each."""
    return Listed(
        given,
        rows,
        tuple(tuple(value.value for value in variable) for variable in values),
        tuple(tuple(value.excluded for value in variable) for variable in values),
        tuple(frozenset(mark for value in variable for mark in value.marks) for variable in values),
    )


def listed(cohort: ResolvedCohort, variables: Sequence[ResolvedVariable]) -> Listed:
    """A cohort's members with each variable's value, by the reference evaluator, in row order."""
    rows = [row for row, value in enumerate(evaluate(cohort).values) if value.is_true]
    values = [evaluate_variable(variable) for variable in variables]
    return of_values(keys(cohort), rows, [[variable[row] for row in rows] for variable in values])


class _Reordered(Sequence[Key]):
    """Keys in another order, each read from the keys it reorders when it is read, so that
    ordering a listing makes no key (D333)."""

    __slots__ = ("_given", "_order")

    def __init__(self, given: Sequence[Key], order: Sequence[int]) -> None:
        self._given = given
        self._order = order

    def __len__(self) -> int:
        return len(self._order)

    @overload
    def __getitem__(self, index: int) -> Key: ...

    @overload
    def __getitem__(self, index: slice) -> Sequence[Key]: ...

    def __getitem__(self, index: int | slice) -> Key | Sequence[Key]:
        if isinstance(index, slice):
            return [self._given[at] for at in self._order[index]]
        return self._given[self._order[index]]


def ordered(given: Listed, ends: float | None = None) -> Listed:
    """A listing in the order of §9.3 (module docstring); raises ``CallerDeadline`` once
    ``time.monotonic()`` passes ``ends``, as ``members.ranked`` does."""
    order = list(ranked(given.keys, ends))
    return Listed(
        _Reordered(given.keys, order),
        [given.rows[index] for index in order],
        tuple(tuple(values[index] for index in order) for values in given.values),
        tuple(tuple(excluded[index] for index in order) for excluded in given.excluded),
        given.marks,
    )


__all__ = ["Listed", "TooManyCells", "cells", "listed", "of_values", "ordered", "shared"]

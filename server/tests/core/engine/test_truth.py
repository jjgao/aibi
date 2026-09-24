"""Truth values and the combinators, against §6.3's rules written out directly."""

from itertools import product

import pytest

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
from aibi.core.schema.semantics import Flag, Reason

A = Mark(Flag.SCOPE_PARTIAL, "rel:a.x")
B = Mark(Flag.COVERAGE_PROPOSED, "rel:b.y")

VALUES = [
    TRUE,
    FALSE,
    truth(True, [A]),
    truth(False, [B]),
    unknown({Reason.NOT_ASSESSED}),
    unknown({Reason.NO_PARENT}, [A]),
    unknown({Reason.NOT_COVERED, Reason.NO_ROWS}, [B]),
]

_RANK = {Truth.FALSE: 0, Truth.UNKNOWN: 1, Truth.TRUE: 2}


def _expected(operands: list[TruthValue], conjunction: bool) -> TruthValue:
    """§6.3: all is the minimum and any the maximum of FALSE < UNKNOWN < TRUE; an UNKNOWN has
    its UNKNOWN operands' reasons; flags come from the operands with the result's value."""
    if not operands:
        return TRUE if conjunction else FALSE
    pick = min if conjunction else max
    value = pick((operand.value for operand in operands), key=_RANK.__getitem__)
    chosen = [operand for operand in operands if operand.value is value]
    reasons = frozenset().union(*(operand.reasons for operand in chosen))
    marks = frozenset().union(*(operand.marks for operand in chosen))
    return TruthValue(value, reasons, marks)


@pytest.mark.parametrize("size", [0, 1, 2, 3])
def test_all_and_any_follow_kleene_with_reasons_and_flags(size: int) -> None:
    for operands in product(VALUES, repeat=size):
        assert all_of(operands) == _expected(list(operands), True)
        assert any_of(operands) == _expected(list(operands), False)


def test_all_true_and_any_false_carry_every_operands_flags() -> None:
    assert all_of([truth(True, [A]), truth(True, [B])]).marks == {A, B}
    assert any_of([truth(False, [A]), truth(False, [B])]).marks == {A, B}
    # A dominant operand decides alone.
    assert all_of([truth(True, [A]), truth(False, [B])]) == truth(False, [B])
    assert any_of([truth(True, [A]), truth(False, [B])]) == truth(True, [A])


@pytest.mark.parametrize("value", VALUES)
def test_not_swaps_true_and_false_and_keeps_unknown_and_flags(value: TruthValue) -> None:
    negated = not_(value)
    assert negated.marks == value.marks
    if value.is_unknown:
        assert negated == value
    else:
        assert negated.value is (Truth.FALSE if value.is_true else Truth.TRUE)
    assert not_(negated) == value


@pytest.mark.parametrize("value", VALUES)
def test_known_and_unknown_are_two_valued_and_keep_flags(value: TruthValue) -> None:
    assert known(value) == truth(not value.is_unknown, value.marks)
    assert unknown_of(value) == truth(value.is_unknown, value.marks)


def test_an_unknown_has_reasons_and_only_an_unknown_has_them() -> None:
    with pytest.raises(ValueError, match="reasons"):
        TruthValue(Truth.UNKNOWN)
    with pytest.raises(ValueError, match="reasons"):
        TruthValue(Truth.TRUE, frozenset({Reason.NO_ROWS}))


def test_marked_adds_marks_and_keeps_the_value() -> None:
    value = unknown({Reason.NO_ROWS}, [A])
    assert value.marked([A]) is value
    assert value.marked([B]) == unknown({Reason.NO_ROWS}, [A, B])
    assert value.marked([B]).flags == {Flag.SCOPE_PARTIAL, Flag.COVERAGE_PROPOSED}

"""``check_values`` decides a value's type from the type's method resolution order, so that no
code of the value's own runs while it is checked (D400)."""

from enum import Enum
from typing import Any, ClassVar

import pytest

from aibi.core.schema.output import OutputError, check_values


class _Recording(type):
    """A metaclass whose types are equal to every type, recording each comparison."""

    seen: ClassVar[list[str]] = []

    def __eq__(cls, other: object) -> bool:
        _Recording.seen.append("eq")
        return True

    def __instancecheck__(cls, instance: object) -> bool:
        _Recording.seen.append("instancecheck")
        return True

    __hash__ = type.__hash__


class _Odd(metaclass=_Recording):
    pass


class _Lies:
    """A value whose ``__class__`` says it is a ``dict``, recording each read."""

    seen: ClassVar[list[str]] = []

    @property
    def __class__(self) -> Any:  # type: ignore[override]
        _Lies.seen.append("__class__")
        return dict


@pytest.mark.parametrize(
    "value", [{"a": [_Odd()]}, [_Odd()], {"a": _Lies()}, [_Lies()], _Lies()], ids=str
)
def test_a_value_of_another_type_is_refused_and_runs_none_of_its_code(value: object) -> None:
    _Recording.seen.clear()
    _Lies.seen.clear()
    with pytest.raises(OutputError):
        check_values(value)
    assert _Recording.seen == []
    assert _Lies.seen == []


class _Claiming(Enum):
    """An enumeration whose members say they are text, recording each time they are asked."""

    seen: ClassVar[list[str]]
    ONE = 1

    @property
    def __class__(self) -> Any:  # type: ignore[override]
        _Lies.seen.append("enum __class__")
        return str


def test_an_enum_member_that_says_it_is_text_is_refused_without_asking_it() -> None:
    """Mutant 29: a member's type is read from its type's method resolution order."""
    _Lies.seen.clear()
    with pytest.raises(OutputError):
        check_values({"a": _Claiming.ONE})
    assert _Lies.seen == []

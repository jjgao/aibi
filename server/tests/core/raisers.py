"""What a hook may raise, for the tests of every site that calls one (D403): each kind that is the
hook's failure, and each type that passes as a new instance of its own."""

from collections.abc import Callable
from typing import Any, NoReturn

SECRET = "s3cr3t-raised-by-a-hook"


class _Meta(type):
    """A metaclass whose comparison and hash raise: looking its class up by ``==`` or in a dict
    runs the pack's code."""

    def __eq__(cls, other: object) -> bool:
        raise RuntimeError(SECRET)

    def __hash__(cls) -> int:
        raise RuntimeError(SECRET)


class Owned(Exception, metaclass=_Meta):  # noqa: N818 - a pack names its own types
    """An exception of a type whose metaclass runs code."""


class Base(BaseException):
    """A ``BaseException`` subclass of the pack's own."""


class Memory(MemoryError):  # noqa: N818 - a pack names its own types
    pass


class Interrupt(KeyboardInterrupt):
    pass


class Exit(SystemExit):
    pass


FAILURES: dict[str, Callable[[], BaseException]] = {
    "value": lambda: ValueError(SECRET),
    "base": lambda: Base(SECRET),
    "metaclass": lambda: Owned(SECRET),
    "memory_subclass": lambda: Memory(SECRET),
    "interrupt_subclass": lambda: Interrupt(SECRET),
    "exit_subclass": lambda: Exit(SECRET),
}
"""What a hook raises that is its failure, made anew for each raise."""

PASSING: dict[str, type[BaseException]] = {
    "memory": MemoryError,
    "interrupt": KeyboardInterrupt,
    "exit": SystemExit,
}
"""What passes, as a new instance of its own type (a ``SystemExit`` with status 1)."""


def raising(make: Callable[[], BaseException]) -> Callable[..., NoReturn]:
    """A hook (or a method of one) that raises what ``make`` makes, whatever it is given."""

    def hook(*given: Any) -> NoReturn:
        raise make()

    return hook


def passing(kind: type[BaseException]) -> Callable[..., NoReturn]:
    return raising(lambda: kind(SECRET))


def passed_anew(error: BaseException, kind: type[BaseException]) -> None:
    """Assert that ``error`` is a new instance of ``kind``, with nothing of the pack's."""
    assert type(error) is kind
    assert SECRET not in str(error)
    assert error.__context__ is None
    assert error.__cause__ is None
    assert error.args == ((1,) if kind is SystemExit else ())

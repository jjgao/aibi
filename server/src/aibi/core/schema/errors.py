"""Validation errors raised by model checks, with the positions they concern.

A check that spans members of a model reports each problem where it lies, relative to the model,
so that a refusal points at the member rather than the object (SPEC §8.6). Messages are fixed
text written by the server; values from the input never go into them (A6).
"""

from collections.abc import Sequence
from typing import LiteralString

from pydantic import ValidationError
from pydantic_core import InitErrorDetails, PydanticCustomError

Location = tuple[str | int, ...]

CHECKED = "checked"
"""The input recorded for a problem found by a check; never ``None``, which marks a null."""


def problem(
    error_type: LiteralString, message: LiteralString, /, **context: object
) -> PydanticCustomError:
    """A validation error with a stable type; ``{name}`` in the message is filled from context."""
    return PydanticCustomError(error_type, message, context or None)


class Problems:
    """Problems found by one check, each at a location relative to the model checked."""

    def __init__(self, title: str) -> None:
        self._title = title
        self._found: list[InitErrorDetails] = []

    def add(self, at: Location, error: PydanticCustomError) -> None:
        self._found.append(InitErrorDetails(type=error, loc=at, input=CHECKED))

    def extend(self, at: Location, errors: Sequence[tuple[Location, PydanticCustomError]]) -> None:
        for where, error in errors:
            self.add((*at, *where), error)

    def __bool__(self) -> bool:
        return bool(self._found)

    def error(self) -> ValidationError:
        """The problems found, as one validation error."""
        return ValidationError.from_exception_data(self._title, self._found)

    def raise_any(self) -> None:
        """Raise the problems found, if any, as one validation error."""
        if self._found:
            raise self.error()


__all__ = ["CHECKED", "Location", "Problems", "problem"]

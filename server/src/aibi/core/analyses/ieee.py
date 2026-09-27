"""Floating-point arithmetic as IEEE 754 and C give it (SPEC §9.5; D349).

Python raises where C's arithmetic gives an infinity or a NaN: a division by zero, ``exp`` past
its range, ``log`` or ``sqrt`` outside their domains, ``math.fsum`` over a sum that overflows or
meets both infinities, and ``float`` of an integer beyond a double. R's ``survival`` is C, which
carries on with those values; the ports of its methods (``timetoevent``, ``survival``) do their
arithmetic through these functions, so that no input can raise where C would go on, and the
values that no output can hold are found by the checks that follow them. A test holds those
modules to it by their syntax (``test_ieee``: no ``/`` but by a non-zero literal, no attribute of
``math`` but its predicates and constants, however reached, and no ``int`` or ``float`` but where
it names why).
"""

import math
from collections.abc import Iterable


def div(numerator: float, denominator: float) -> float:
    """``numerator / denominator`` as IEEE 754 divides: by zero, ±∞ with the sign of both (a
    zero's own sign included), and 0/0 or NaN/0 NaN."""
    if denominator:
        return numerator / denominator
    if numerator == 0 or math.isnan(numerator):
        return math.nan
    return math.copysign(math.inf, numerator) * math.copysign(1.0, denominator)


def exp(value: float) -> float:
    """``exp``: +∞ where the result overflows, 0 where it underflows, NaN at NaN."""
    try:
        return math.exp(value)
    except OverflowError:
        return math.inf


def log(value: float) -> float:
    """``log``: −∞ at 0 and NaN below it or at NaN."""
    if value > 0:
        return math.log(value)
    return -math.inf if value == 0 else math.nan


def sqrt(value: float) -> float:
    """``sqrt``: NaN below 0 or at NaN (−0 is −0, as C gives it)."""
    return math.sqrt(value) if value >= 0 else math.nan


def fsum(values: Iterable[float]) -> float:
    """The correctly rounded sum of ``values`` (``math.fsum``), or where that raises, on a sum
    that overflows or holds both infinities, their sum in order as C adds it: ±∞ or NaN."""
    given = list(values)
    try:
        return math.fsum(given)
    except (OverflowError, ValueError):
        total = 0.0
        for value in given:
            total += value
        return total


def real(value: int | float) -> float:
    """``(double) value``: an integer beyond a double ±∞, and −0 as 0 (a time's sign is its
    value's)."""
    try:
        return float(value) + 0.0
    except OverflowError:
        return math.inf if value > 0 else -math.inf


__all__ = ["div", "exp", "fsum", "log", "real", "sqrt"]

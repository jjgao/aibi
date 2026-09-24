"""Unit conversion for numeric predicates (SPEC §6.4).

A numeric predicate's constants are in its ``units``; they are converted to the column's units
with pinned factors, and refused when no conversion exists. A factor is exact here, as a
fraction; evaluation applies it as one multiplication of doubles, by the factor rounded to a
double, as SQL does (D203). The factors below are UCUM's (https://ucum.org) for a set of atoms,
and a unit is an atom or a quotient of two atoms (``mg/dL``). Any other code converts only to
itself. Conversions that are not a factor, such as temperatures, are not conversions here.
"""

from fractions import Fraction
from typing import NamedTuple

_S = Fraction(1)
_DAY = 86_400 * _S
_JULIAN_YEAR = Fraction(36_525, 100) * _DAY
_GREGORIAN_YEAR = Fraction(3_652_425, 10_000) * _DAY
_INCH = Fraction(254, 10_000)
_POUND = Fraction(45_359_237, 100_000_000) * 1_000

_ATOMS: dict[str, tuple[str, Fraction]] = {
    # time, in seconds
    "s": ("time", _S),
    "ms": ("time", _S / 1_000),
    "us": ("time", _S / 1_000_000),
    "ns": ("time", _S / 1_000_000_000),
    "min": ("time", 60 * _S),
    "h": ("time", 3_600 * _S),
    "d": ("time", _DAY),
    "wk": ("time", 7 * _DAY),
    "a": ("time", _JULIAN_YEAR),
    "a_j": ("time", _JULIAN_YEAR),
    "a_g": ("time", _GREGORIAN_YEAR),
    "mo": ("time", _JULIAN_YEAR / 12),
    "mo_j": ("time", _JULIAN_YEAR / 12),
    "mo_g": ("time", _GREGORIAN_YEAR / 12),
    # mass, in grams
    "g": ("mass", Fraction(1)),
    "kg": ("mass", Fraction(1_000)),
    "mg": ("mass", Fraction(1, 1_000)),
    "ug": ("mass", Fraction(1, 1_000_000)),
    "ng": ("mass", Fraction(1, 1_000_000_000)),
    "[lb_av]": ("mass", _POUND),
    # length, in metres
    "m": ("length", Fraction(1)),
    "km": ("length", Fraction(1_000)),
    "cm": ("length", Fraction(1, 100)),
    "mm": ("length", Fraction(1, 1_000)),
    "um": ("length", Fraction(1, 1_000_000)),
    "nm": ("length", Fraction(1, 1_000_000_000)),
    "[in_i]": ("length", _INCH),
    "[ft_i]": ("length", 12 * _INCH),
    "[mi_i]": ("length", 5_280 * 12 * _INCH),
    # volume, in litres
    "L": ("volume", Fraction(1)),
    "l": ("volume", Fraction(1)),
    "dL": ("volume", Fraction(1, 10)),
    "cL": ("volume", Fraction(1, 100)),
    "mL": ("volume", Fraction(1, 1_000)),
    "uL": ("volume", Fraction(1, 1_000_000)),
    # amount of substance, in moles
    "mol": ("amount", Fraction(1)),
    "mmol": ("amount", Fraction(1, 1_000)),
    "umol": ("amount", Fraction(1, 1_000_000)),
    "nmol": ("amount", Fraction(1, 1_000_000_000)),
    # dimensionless
    "1": ("one", Fraction(1)),
    "%": ("one", Fraction(1, 100)),
}


class _Parsed(NamedTuple):
    dimension: tuple[str, str]
    """The atoms' dimensions, as (numerator, denominator); ``one`` for none."""
    factor: Fraction


def _parse(code: str) -> _Parsed | None:
    numerator, slash, denominator = code.partition("/")
    top = _ATOMS.get(numerator)
    bottom = _ATOMS.get(denominator) if slash else ("one", Fraction(1))
    if top is None or bottom is None or (slash and "/" in denominator):
        return None
    dimension = (top[0], bottom[0])
    if dimension[1] == dimension[0] != "one":
        dimension = ("one", "one")
    return _Parsed(dimension, top[1] / bottom[1])


def factor(given: str, to: str) -> Fraction | None:
    """The factor that converts a number in ``given`` units to ``to`` units, or ``None``."""
    if given == to:
        return Fraction(1)
    source, target = _parse(given), _parse(to)
    if source is None or target is None or source.dimension != target.dimension:
        return None
    return source.factor / target.factor


def convertible(given: str, to: str) -> bool:
    return factor(given, to) is not None


__all__ = ["convertible", "factor"]

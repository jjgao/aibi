"""Pinned conversion factors for numeric predicates (§6.4)."""

from fractions import Fraction

import pytest

from aibi.core.engine.units import convertible, factor


@pytest.mark.parametrize(
    ("given", "to", "expected"),
    [
        ("d", "d", Fraction(1)),
        ("d", "h", Fraction(24)),
        ("h", "d", Fraction(1, 24)),
        ("wk", "d", Fraction(7)),
        ("a", "d", Fraction(36_525, 100)),
        ("a_g", "d", Fraction(3_652_425, 10_000)),
        ("mo", "d", Fraction(36_525, 1_200)),
        ("ms", "s", Fraction(1, 1_000)),
        ("kg", "g", Fraction(1_000)),
        ("[lb_av]", "kg", Fraction(45_359_237, 100_000_000)),
        ("[in_i]", "cm", Fraction(254, 100)),
        ("[ft_i]", "m", Fraction(3_048, 10_000)),
        ("[mi_i]", "km", Fraction(1_609_344, 1_000_000)),
        ("mg/dL", "g/L", Fraction(1, 100)),
        ("mmol/L", "umol/L", Fraction(1_000)),
        ("mg/g", "%", Fraction(1, 10)),
        ("%", "1", Fraction(1, 100)),
        ("g/g", "1", Fraction(1)),
        ("Cel", "Cel", Fraction(1)),
        ("{visits}", "{visits}", Fraction(1)),
    ],
)
def test_factors_are_exact(given: str, to: str, expected: Fraction) -> None:
    assert factor(given, to) == expected
    assert convertible(given, to)


@pytest.mark.parametrize(
    ("given", "to"),
    [
        ("g", "m"),
        ("d", "g"),
        ("mg/dL", "mg"),
        ("mg/dL", "d/L"),
        ("Cel", "K"),
        ("Cel", "[degF]"),
        ("mg/dL/s", "mg/dL/s/"),
        ("/d", "1/d"),
        ("", "1"),
        ("{visits}", "1"),
    ],
)
def test_units_without_a_factor_do_not_convert(given: str, to: str) -> None:
    assert factor(given, to) is None
    assert not convertible(given, to)


def test_a_quotient_converts_both_parts() -> None:
    assert factor("mg/d", "g/wk") == Fraction(7, 1_000)
    assert factor("1/d", "1/h") == Fraction(1, 24)
    assert factor("1/d", "d") is None

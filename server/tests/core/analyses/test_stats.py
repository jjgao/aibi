"""The statistical methods of ``compare.existence`` against R (SPEC §9.3, §9.5, §13.4; D321).

``reference/existence.json`` holds R's outputs, written once by ``reference/existence.R`` with
the pinned calls it names; closed forms agree to 1e-10 relative, as §13.4 asks.
"""

import json
import math
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from aibi.core.analyses import stats

REFERENCE: dict[str, Any] = json.loads(
    (Path(__file__).parent / "reference" / "existence.json").read_text()
)
RELATIVE = 1e-10


def close(found: float, expected: float) -> bool:
    return math.isclose(found, expected, rel_tol=RELATIVE)


def test_the_reference_was_written_by_r() -> None:
    assert REFERENCE["r"].startswith("R version 4.")
    assert all(REFERENCE[name] for name in ("wilson", "newcombe", "katz", "fisher"))


@pytest.mark.parametrize(
    "case", REFERENCE["wilson"], ids=lambda c: f"{c['x']}/{c['n']}@{c['level']}"
)
def test_wilson_intervals_agree_with_prop_test(case: dict[str, Any]) -> None:
    low, high = stats.wilson(case["x"], case["n"], case["level"])
    assert close(low, case["low"])
    assert close(high, case["high"])


def test_wilson_agrees_with_newcombe_s_published_example() -> None:
    low, high = stats.wilson(81, 263, 0.95)
    assert (round(low, 4), round(high, 4)) == (0.2553, 0.3662)


@pytest.mark.parametrize("case", REFERENCE["newcombe"], ids=lambda c: f"{c['x1']}-{c['x2']}")
def test_newcombe_intervals_agree_with_the_named_r_implementation(case: dict[str, Any]) -> None:
    found = stats.newcombe(case["x1"], case["n1"], (case["x2"], case["n2"]), case["level"])
    expected = (case["difference"], case["low"], case["high"])
    assert all(close(a, b) for a, b in zip(found, expected, strict=True))


def test_newcombe_agrees_with_the_published_worked_example() -> None:
    difference, low, high = stats.newcombe(56, 70, (48, 80), 0.95)
    assert (round(difference, 4), round(low, 4), round(high, 4)) == (0.2, 0.0524, 0.3339)


@pytest.mark.parametrize("case", REFERENCE["katz"], ids=lambda c: f"{c['x1']}-{c['x2']}")
def test_katz_intervals_agree_with_the_named_r_implementation(case: dict[str, Any]) -> None:
    found = stats.katz(case["x1"], case["n1"], (case["x2"], case["n2"]), case["level"])
    expected = (case["ratio"], case["low"], case["high"])
    assert all(close(a, b) for a, b in zip(found, expected, strict=True))


def test_katz_refuses_a_zero_numerator() -> None:
    with pytest.raises(ValueError, match="both numerators"):
        stats.katz(0, 10, (3, 10), 0.95)


@pytest.mark.parametrize("case", REFERENCE["fisher"], ids=lambda c: str(c["table"]))
def test_fisher_p_values_agree_with_fisher_test(case: dict[str, Any]) -> None:
    a, b, c, d = case["table"]
    assert close(stats.fisher(((a, b), (c, d))), case["p"])


def test_fisher_gives_the_tea_tasting_p_value_exactly() -> None:
    assert stats.fisher(((3, 1), (1, 3))) == pytest.approx(34 / 70, rel=1e-15)


@pytest.mark.parametrize("case", REFERENCE["chi_squared"], ids=lambda c: str(c["table"]))
def test_chi_squared_tests_agree_with_chisq_test(case: dict[str, Any]) -> None:
    rows, cells = case["rows"], case["table"]
    width = len(cells) // rows
    found = stats.chi_squared([cells[row * width : (row + 1) * width] for row in range(rows)])
    assert close(found.statistic, case["statistic"])
    assert found.df == case["df"]
    assert close(found.p, case["p"])


def test_chi_squared_reports_its_least_expected_count() -> None:
    found = stats.chi_squared([[1, 9], [9, 1]])
    assert found.least_expected == 5.0
    assert stats.chi_squared([[1, 9], [10, 10]]).least_expected == 11 / 3
    assert stats.chi_squared([[10, 20], [30, 40]]).least_expected == 12.0


def test_chi_squared_refuses_an_empty_row_or_column() -> None:
    with pytest.raises(ValueError, match="none of them empty"):
        stats.chi_squared([[0, 0], [3, 4]])
    with pytest.raises(ValueError, match="none of them empty"):
        stats.chi_squared([[0, 5], [0, 4]])


@pytest.mark.parametrize("case", REFERENCE["upper_tail"], ids=lambda c: f"{c['x']}@{c['df']}")
def test_upper_tails_agree_with_pchisq(case: dict[str, Any]) -> None:
    assert close(stats.upper_gamma(case["df"] / 2, case["x"] / 2), case["q"])


@given(st.floats(min_value=1e-6, max_value=200))
def test_upper_tails_of_one_two_and_four_degrees_of_freedom_are_their_closed_forms(
    x: float,
) -> None:
    assert close(stats.upper_gamma(0.5, x / 2), math.erfc(math.sqrt(x / 2)))
    assert close(stats.upper_gamma(1.0, x / 2), math.exp(-x / 2))
    assert close(stats.upper_gamma(2.0, x / 2), math.exp(-x / 2) * (1 + x / 2))


def test_an_upper_tail_at_zero_is_one() -> None:
    assert stats.upper_gamma(3.0, 0.0) == 1.0


@pytest.mark.parametrize("case", REFERENCE["benjamini_hochberg"], ids=lambda c: str(c["p"]))
def test_q_values_agree_with_p_adjust(case: dict[str, Any]) -> None:
    found = stats.benjamini_hochberg(case["p"])
    assert all(close(a, b) for a, b in zip(found, case["q"], strict=True))


@given(st.lists(st.floats(min_value=0, max_value=1), min_size=1, max_size=20))
def test_a_q_value_is_never_below_its_p_value_nor_above_one(p: list[float]) -> None:
    q = stats.benjamini_hochberg(p)
    assert all(pv <= qv <= 1 for pv, qv in zip(p, q, strict=True))


@given(
    st.integers(min_value=0, max_value=60),
    st.integers(min_value=0, max_value=60),
    st.integers(min_value=0, max_value=60),
    st.integers(min_value=0, max_value=60),
)
def test_fisher_p_values_are_probabilities_and_symmetric_in_the_table(
    a: int, b: int, c: int, d: int
) -> None:
    p = stats.fisher(((a, b), (c, d)))
    assert 0 <= p <= 1
    assert close(p, stats.fisher(((c, d), (a, b)))) or p == stats.fisher(((c, d), (a, b)))
    assert close(p, stats.fisher(((b, a), (d, c)))) or p == stats.fisher(((b, a), (d, c)))


@given(st.integers(min_value=1, max_value=500), st.data())
def test_wilson_intervals_hold_their_estimate_within_zero_and_one(
    n: int, data: st.DataObject
) -> None:
    x = data.draw(st.integers(min_value=0, max_value=n))
    low, high = stats.wilson(x, n, 0.95)
    assert 0 <= low <= x / n <= high <= 1


def test_levels_outside_zero_and_one_are_refused() -> None:
    with pytest.raises(ValueError, match="strictly between"):
        stats.z(1.0)

"""The statistical methods of ``compare.existence`` and ``summary.distribution`` against R (SPEC
§9.3, §9.5, §13.4; D321, D328).

``reference/existence.json`` holds R's outputs, written once by ``reference/existence.R`` with
the pinned calls it names; closed forms agree to 1e-10 relative, as §13.4 asks.
"""

import itertools
import json
import math
import random
import time
from collections import Counter
from fractions import Fraction
from operator import itemgetter
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from aibi.core.analyses import stats
from aibi.core.analyses.distribution import histogram
from aibi.core.engine.worker import CallerDeadline

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


def _weighted(values: list[float]) -> stats.Weighted:
    return sorted(Counter(values).items())


@pytest.mark.parametrize("case", REFERENCE["distribution"], ids=lambda c: str(c["values"][:3]))
def test_a_distribution_s_mean_sd_quartiles_and_histogram_agree_with_r(
    case: dict[str, Any],
) -> None:
    weighted = _weighted(case["values"])
    centre = stats.mean(weighted)
    spread = stats.sd(weighted, centre)
    assert close(centre, case["mean"])
    if case["sd"] in (None, 0):
        assert spread is None
    else:
        assert spread is not None
        assert close(spread, case["sd"])
    for name, probability in (("q1", 0.25), ("median", 0.5), ("q3", 0.75)):
        assert close(stats.quantile(weighted, probability), case[name])
    assert [one.count for one in histogram(case["edges"], weighted)] == case["histogram"]


def test_the_mean_of_integers_is_their_exact_sum_divided_once() -> None:
    weighted = [(2**53 + 1, 1), (2**53 + 3, 1)]
    assert stats.mean(weighted) == float(2**53 + 2)


# --- compare.columns (D338) -----------------------------------------------------------------------

COLUMNS: dict[str, Any] = json.loads(
    (Path(__file__).parent / "reference" / "columns.json").read_text()
)


def weighted(values: list[float]) -> stats.Weighted:
    return sorted(Counter(values).items())


def test_the_columns_reference_was_written_by_r() -> None:
    assert COLUMNS["r"].startswith("R version 4.")
    assert all(COLUMNS[name] for name in ("welch", "welch_anova", "mann_whitney", "t_quantile"))


@pytest.mark.parametrize("case", COLUMNS["welch"], ids=lambda c: f"{len(c['x'])}-{c['level']}")
def test_welch_s_test_and_interval_agree_with_t_test(case: dict[str, Any]) -> None:
    """To 1e-10 relative, but a statistic within 1e-12 of 0: the means of 0.1, 0.2 and
    0.30000000000000004 and of 0.1, 0.2 and 0.3 differ by 5.6e-17 exactly, which R's two-pass
    mean rounds to 0 (D328)."""
    found = stats.welch(stats.moments(weighted(case["x"])), stats.moments(weighted(case["y"])))
    assert math.isclose(found.statistic, case["statistic"], rel_tol=RELATIVE, abs_tol=1e-12)
    assert close(found.df, case["df"])
    assert close(found.p, case["p"])
    low, high = found.interval(case["level"])
    assert close(low, case["low"])
    assert close(high, case["high"])


@pytest.mark.parametrize("case", COLUMNS["welch_anova"], ids=lambda c: str(len(c["groups"])))
def test_welch_s_one_way_test_agrees_with_oneway_test(case: dict[str, Any]) -> None:
    found = stats.welch_anova([stats.moments(weighted(group)) for group in case["groups"]])
    assert close(found.statistic, case["statistic"])
    assert found.numerator == case["numerator"]
    assert close(found.denominator, case["denominator"])
    assert close(found.p, case["p"])


@pytest.mark.parametrize("case", COLUMNS["mann_whitney"], ids=lambda c: f"{c['x'][:2]}")
def test_mann_whitney_agrees_with_wilcox_test(case: dict[str, Any]) -> None:
    found = stats.mann_whitney(weighted(case["x"]), weighted(case["y"]))
    if case["p"] is None:
        assert found is None
        return
    assert found is not None
    assert found.statistic == case["statistic"]
    assert close(found.p, case["p"])


def test_mann_whitney_is_exact_only_below_50_values_a_group_and_without_ties() -> None:
    exact = stats.mann_whitney(weighted([1.0, 2.0, 3.0]), weighted([4.0, 5.0, 6.0, 7.0]))
    tied = stats.mann_whitney(weighted([1.0, 2.0, 2.0]), weighted([2.0, 5.0]))
    large = stats.mann_whitney(
        weighted([float(v) for v in range(50)]), weighted([v + 0.5 for v in range(3)])
    )
    assert exact is not None
    assert tied is not None
    assert large is not None
    assert (exact.exact, tied.exact, large.exact) == (True, False, False)
    assert exact.p == 2 / 35


def test_the_rank_sum_counts_are_the_gaussian_binomial_coefficients() -> None:
    for m, n in ((1, 1), (2, 3), (4, 4), (7, 3)):
        counts = stats._rank_sum_counts(m, n)  # pyright: ignore[reportPrivateUsage]
        assert sum(counts) == math.comb(m + n, m)
        assert counts == counts[::-1]
        brute = Counter(
            sum(1 for a in chosen for b in set(range(m + n)) - set(chosen) if a > b)
            for chosen in itertools.combinations(range(m + n), m)
        )
        assert counts == [brute[u] for u in range(m * n + 1)]


@pytest.mark.parametrize("case", COLUMNS["kruskal_wallis"], ids=lambda c: str(len(c["groups"])))
def test_kruskal_wallis_agrees_with_kruskal_test(case: dict[str, Any]) -> None:
    found = stats.kruskal_wallis([weighted(group) for group in case["groups"]])
    assert found is not None
    assert close(found.statistic, case["statistic"])
    assert found.df == case["df"]
    assert close(found.p, case["p"])


def test_rank_tests_of_values_all_tied_are_not_computed() -> None:
    assert stats.mann_whitney(weighted([4.0, 4.0]), weighted([4.0])) is None
    assert stats.kruskal_wallis([weighted([4.0]), weighted([4.0, 4.0])]) is None


@pytest.mark.parametrize("case", COLUMNS["t_tail"], ids=lambda c: f"{c['t']}@{c['df']}")
def test_the_t_tail_agrees_with_pt(case: dict[str, Any]) -> None:
    assert close(stats.t_tail(case["t"], case["df"]), case["p"])


@pytest.mark.parametrize("case", COLUMNS["t_quantile"], ids=lambda c: f"{c['level']}@{c['df']}")
def test_the_t_quantile_agrees_with_qt(case: dict[str, Any]) -> None:
    assert close(stats.t_quantile(case["level"], case["df"]), case["t"])


@pytest.mark.parametrize("case", COLUMNS["f_tail"], ids=lambda c: f"{c['f']}")
def test_the_f_tail_agrees_with_pf(case: dict[str, Any]) -> None:
    found = stats.f_tail(case["f"], case["numerator"], case["denominator"])
    assert close(found, case["p"])


@pytest.mark.parametrize("case", COLUMNS["beta"], ids=lambda c: f"{c['a']}-{c['b']}")
def test_the_incomplete_beta_agrees_with_pbeta_for_small_and_large_parameters(
    case: dict[str, Any],
) -> None:
    """Large parameters are where log-gammas of hundreds of thousands would cancel (D338)."""
    x = case["x"]
    assert close(stats.beta_tail(case["a"], case["b"], x, 1 - x), case["p"])


@given(
    a=st.floats(0.05, 1e7),
    b=st.floats(0.05, 1e7),
    x=st.floats(1e-300, 1 - 1e-6),
)
def test_the_incomplete_beta_is_symmetric_and_its_tails_add_to_one(
    a: float, b: float, x: float
) -> None:
    y = 1 - x
    lower = stats.beta_tail(a, b, x, y)
    upper = stats.beta_tail(b, a, y, x)
    assert 0 <= lower <= 1
    assert math.isclose(lower + upper, 1, rel_tol=1e-9, abs_tol=1e-12)


def test_the_t_quantile_inverts_the_t_tail() -> None:
    for level in (0.5, 0.9, 0.95, 0.999999):
        for df in (1.0, 2.5, 30.0, 1e4):
            assert math.isclose(stats.t_tail(stats.t_quantile(level, df), df), 1 - level)


def test_a_percentile_interval_takes_the_order_statistics_the_level_s_decimal_gives() -> None:
    replicates = [float(value) for value in range(2000, 0, -1)]
    assert stats.percentile_interval(replicates, 0.95) == (50.0, 1950.0)
    assert stats.percentile_interval(replicates, 0.9) == (100.0, 1900.0)
    assert stats.percentile_interval([3.0], 0.95) == (3.0, 3.0)


def test_a_bootstrap_is_a_function_of_its_values_and_its_seed() -> None:
    values = weighted([1.0, 2.0, 2.0, 5.0, 9.0])
    first = stats.bootstrap_medians(values, 17)
    assert first == stats.bootstrap_medians(values, 17)
    assert first != stats.bootstrap_medians(values, 18)
    assert len(first) == stats.REPLICATES
    assert set(first) <= {1.0, 2.0, 5.0, 9.0}
    assert stats.bootstrap_medians(weighted([4.0]), 3) == [4.0] * stats.REPLICATES


@pytest.mark.parametrize("values", [[1.0, 2.0, 3.0, 10.0, 11.0], [0.0, 1.0, 1.0, 4.0, 7.0, 9.0]])
def test_bootstrap_medians_are_distributed_as_those_of_resamples_drawn_one_by_one(
    values: list[float],
) -> None:
    """Each distinct median's share of 20,000 replicates drawn by order statistics is within
    two percentage points of its share of as many resamples drawn value by value with a seeded
    generator, and of its exact probability, counted over every resample (D338)."""
    n = len(values)
    exact: Counter[float] = Counter()
    for drawn in itertools.product(values, repeat=n):
        exact[stats.median(weighted(list(drawn)))] += 1
    total = n**n
    found = Counter(stats.bootstrap_medians(weighted(values), 5, replicates=20_000))
    stream = random.Random(9)
    naive = Counter(
        stats.median(weighted([values[stream.randrange(n)] for _ in range(n)]))
        for _ in range(20_000)
    )
    for median, count in exact.items():
        assert abs(found[median] / 20_000 - count / total) < 0.02
        assert abs(naive[median] / 20_000 - count / total) < 0.02


def test_a_bootstrap_of_a_million_values_takes_about_as_long_as_one_of_ten() -> None:
    """A replicate draws two gammas and a uniform whatever the group's size (D338)."""

    def took(values: stats.Weighted) -> float:
        started = time.perf_counter()
        stats.bootstrap_medians(values, 1)
        return time.perf_counter() - started

    small = took([(float(value), 1) for value in range(10)])
    assert took([(float(value), 10) for value in range(100_000)]) < 3 * small + 0.5


def test_a_draw_at_the_top_of_the_unit_interval_is_the_greatest_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A ratio of gammas that rounds to 1 is the last order statistic, not one past it."""
    shapes: list[int] = []

    def gamma(self: object, shape: int) -> float:
        shapes.append(shape)
        return 1.0 if len(shapes) % 2 else 1e-300

    monkeypatch.setattr(stats._Draws, "gamma", gamma)  # pyright: ignore[reportPrivateUsage]
    assert stats.bootstrap_medians(weighted([1.0, 2.0, 3.0]), 7, replicates=2) == [3.0, 3.0]


@pytest.mark.parametrize("shape", [1, 2, 50])
def test_the_gamma_variates_follow_the_gamma_distribution(shape: int) -> None:
    """Kolmogorov–Smirnov over 40,000 draws of a fixed seed against P(shape, x) = 1 − Q:
    below the 0.1% critical value, 1.95/√n (D338)."""
    draws = stats._Draws(shape)  # pyright: ignore[reportPrivateUsage]
    found = sorted(draws.gamma(shape) for _ in range(40_000))
    n = len(found)
    distance = max(
        max(abs((i + 1) / n - p), abs(p - i / n))
        for i, value in enumerate(found)
        for p in (1 - stats.upper_gamma(shape, value),)
    )
    assert distance < 1.95 / math.sqrt(n)


def test_a_t_tail_beyond_a_double_s_square_is_zero() -> None:
    assert stats.t_tail(1e200, 3.0) == 0.0
    assert stats.t_tail(-math.inf, 3.0) == 0.0


def test_kruskal_wallis_ranks_values_as_doubles_and_groups_ties_as_they_print() -> None:
    """0.1 + 0.2 and 0.3 are two ranks, as R's rank gives them, and one tie of two values in
    the correction, as R's table groups them (D338)."""
    groups = [weighted([0.1, 0.2, 0.1 + 0.2]), weighted([0.3, 0.5]), weighted([1.0, 2.0])]
    found = stats.kruskal_wallis(groups)
    assert found is not None
    sums = [7, 8, 13]
    between = sum(Fraction(r * r, len(g)) for r, g in zip(sums, groups, strict=True))
    statistic = (12 * between / (7 * 8) - 3 * 8) / (1 - Fraction(6, 7**3 - 7))
    assert found.statistic == float(statistic)


def test_the_printing_reference_was_written_where_r_s_long_double_is_x87_s() -> None:
    """``_printed`` emulates R on x86-64, whose long double has a 64-bit mantissa; an R without
    one (arm64) prints some values otherwise, so the fixture holds only an x86-64 R's (D338)."""
    assert (COLUMNS["platform"].split("-")[0], COLUMNS["long_double"]) == ("x86_64", 16)


@pytest.mark.parametrize("case", COLUMNS["printed"], ids=lambda c: c["text"])
def test_a_value_prints_as_r_s_as_character_prints_it(case: dict[str, Any]) -> None:
    """The strings by which Kruskal-Wallis' correction for ties groups values, as R's ``table``
    does: fixed notation, every integer digit included, wherever it is no wider (D338)."""
    assert stats._printed(float(case["x"])) == case["text"]  # pyright: ignore[reportPrivateUsage]
    assert stats._printed_exactly(float(case["x"])) == case["text"]  # pyright: ignore[reportPrivateUsage]


@given(st.floats(allow_nan=False, allow_infinity=False))
def test_the_quick_and_the_exact_printing_agree(value: float) -> None:
    """``_printed`` takes printf's correctly rounded digits wherever R's long double scaling
    cannot round otherwise, and ``_printed_exactly`` scales as R does everywhere."""
    assert stats._printed(value) == stats._printed_exactly(value)  # pyright: ignore[reportPrivateUsage]


EXPANSION = [c for c in COLUMNS["beta"] if c["a"] >= 15 and c["b"] <= 40 and c["x"] > 0.7]


@pytest.mark.parametrize("case", EXPANSION, ids=lambda c: f"{c['a']}-{c['b']}")
def test_the_incomplete_beta_s_expansion_agrees_with_pbeta_to_1e_12(case: dict[str, Any]) -> None:
    """Where the asymptotic expansion computes I_x(a, b), b reduced to (0, 1] by the recurrence
    and b not ½ or 1, the side it computes agrees with R to 1e-12 (D338)."""
    x = case["x"]
    assert math.isclose(stats.beta_tail(case["a"], case["b"], x, 1 - x), case["p"], rel_tol=1e-12)


def _exact_moments(group: list[float]) -> tuple[Fraction, Fraction]:
    values = [Fraction(value) for value in group]
    centre = sum(values, Fraction(0)) / len(values)
    return centre, sum(((v - centre) ** 2 for v in values), Fraction(0)) / (len(values) - 1)


def _scaled_group(scale: int, integers: list[int]) -> list[float]:
    return [math.ldexp(float(value), scale) for value in integers]


MIXED = st.lists(
    st.tuples(
        st.integers(min_value=-1074, max_value=0),
        st.lists(st.integers(min_value=-(2**20), max_value=2**20), min_size=2, max_size=6),
    ).filter(lambda group: len(set(group[1])) > 1),
    min_size=2,
    max_size=6,
)
"""Groups of small integers each scaled by its own power of two, from subnormal to 1."""


@given(MIXED)
def test_welch_s_tests_of_groups_of_any_scales_are_their_exact_statistics(
    groups: list[tuple[int, list[int]]],
) -> None:
    """Welch's t² and its df, and the one-way test's F and second df, are rationals of the
    values, computed here exactly: the tests agree with them to 1e-12 over groups whose
    spreads differ by up to 2^1074 (D338)."""
    found = [_scaled_group(scale, integers) for scale, integers in groups]
    moments = [stats.moments(sorted(Counter(group).items())) for group in found]
    exact = [_exact_moments(group) for group in found]
    if any(m.spread is None for m in moments):
        return
    if len(found) == 2:
        (m1, v1), (m2, v2) = exact
        n1, n2 = len(found[0]), len(found[1])
        errors = v1 / n1 + v2 / n2
        df = errors**2 / ((v1 / n1) ** 2 / (n1 - 1) + (v2 / n2) ** 2 / (n2 - 1))
        welch = stats.welch(moments[0], moments[1])
        assert math.isclose(welch.statistic**2, float((m1 - m2) ** 2 / errors), rel_tol=1e-12)
        assert math.isclose(welch.df, float(df), rel_tol=1e-12)
        return
    weights = [len(group) / variance for group, (_, variance) in zip(found, exact, strict=True)]
    total = sum(weights, Fraction(0))
    centre = sum((w * m for w, (m, _) in zip(weights, exact, strict=True)), Fraction(0)) / total
    k = len(found)
    tmp = sum(
        ((1 - w / total) ** 2 / (len(group) - 1) for w, group in zip(weights, found, strict=True)),
        Fraction(0),
    ) / (k * k - 1)
    between = sum(
        (w * (m - centre) ** 2 for w, (m, _) in zip(weights, exact, strict=True)), Fraction(0)
    )
    statistic = between / (k - 1) / (1 + 2 * (k - 2) * tmp)
    anova = stats.welch_anova(moments)
    assert math.isclose(anova.statistic, float(statistic), rel_tol=1e-12)
    assert math.isclose(anova.denominator, float(1 / (3 * tmp)), rel_tol=1e-12)


def test_welch_s_one_way_test_of_one_tiny_spread_beside_ordinary_ones_is_computed() -> None:
    """Round 2's case: a spread of 1e-155 beside spreads of about 1, whose statistic is about
    10.7, where the means' squares scaled by the least spread would overflow."""
    groups = [[0.0, 1e-155, 2e-155], [1.0, 2.0, 4.0], [1.5, 3.0, 3.5]]
    found = stats.welch_anova([stats.moments(weighted(group)) for group in groups])
    assert math.isclose(found.statistic, 10.676923076923076, rel_tol=1e-12)
    assert math.isclose(found.denominator, 8 / 3, rel_tol=1e-12)


@pytest.mark.parametrize("values", [[1.0, 1.0 + 2**-52], [1.0, 1.0 + 2**-52, 1.0 + 2**-51]])
def test_a_spread_of_a_few_ulps_is_exact_to_rounding(values: list[float]) -> None:
    """The deviations are taken around the exact mean rounded, less n·δ² (D338): of 1 and
    1 + 2^−52 the mean rounds to 1, and without the correction the deviation would be twice
    its square."""
    _, variance = _exact_moments(values)
    found = stats.moments(weighted(values)).sd
    assert found is not None
    assert math.isclose(found, math.sqrt(float(variance)), rel_tol=1e-15)


def test_the_moments_cost_the_distinct_values_not_the_units() -> None:
    """A value held by a billion units is one exact product."""
    started = time.perf_counter()
    found = stats.moments([(0.0, 10**9), (1.0, 10**9)]).sd
    assert time.perf_counter() - started < 0.1
    assert found is not None
    n = 2 * 10**9
    assert math.isclose(found, math.sqrt(float(Fraction(n, 4) / (n - 1))), rel_tol=1e-15)


@given(
    st.dictionaries(
        st.floats(min_value=-1e6, max_value=1e6, allow_nan=False),
        st.integers(min_value=1, max_value=40),
        min_size=1,
        max_size=8,
    )
)
def test_a_value_held_by_many_units_counts_as_that_many_values(held: dict[float, int]) -> None:
    """Its square times its number is summed exactly, so the moments are those of the value
    repeated, to the bit."""
    values = sorted(held.items())
    repeated = [(value, 1) for value, times in values for _ in range(times)]
    found, expected = stats.moments(values), stats.moments(repeated)
    assert (found.mean, found.sd) == (expected.mean, expected.sd)


def test_a_spread_beyond_a_double_is_infinite() -> None:
    top = 1.7976931348623157e308
    assert stats.moments([(-top, 1), (top, 1)]).sd == math.inf


def test_rank_tests_stop_once_the_call_s_deadline_has_passed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ranks are placed 65,536 values at a time, the deadline checked between (D336): a
    clock that passes after the first checks stops them before the rest."""
    groups = [[(float(v), 1) for v in range(k, 400_000, 3)] for k in range(3)]
    for test in (
        lambda ends: stats.kruskal_wallis(groups, ends),
        lambda ends: stats.mann_whitney(groups[0], groups[1], ends),
    ):
        with pytest.raises(CallerDeadline):
            test(time.monotonic() - 1)
        clock = _Clock(passes_after=3)
        monkeypatch.setattr(stats.time, "monotonic", clock)
        with pytest.raises(CallerDeadline):
            test(1.0)
        monkeypatch.undo()
        assert clock.calls == 4


@pytest.mark.parametrize(
    ("values", "starts"),
    [
        ([float(v) for v in range(200_000)], "_printed_ties"),
        ([1.0 + v * 2.0**-52 for v in range(200_000)], "_printed"),
    ],
    ids=["apart", "close"],
)
def test_kruskal_wallis_stops_once_the_deadline_passes_while_its_ties_are_counted(
    monkeypatch: pytest.MonkeyPatch, values: list[float], starts: str
) -> None:
    """Values far apart are ties of their own, and a run of close ones is printed, and each is
    checked 65,536 values at a time (D336): a deadline that passes once the ties are counted, or
    once the first value is printed, stops them before the rest."""
    started: list[int] = []
    given = getattr(stats, starts)

    def watched(*args: Any) -> Any:
        started.append(1)
        return given(*args)

    monkeypatch.setattr(stats, starts, watched)
    monkeypatch.setattr(stats.time, "monotonic", lambda: 2.0 if started else 0.0)
    groups = [[(value, 1) for value in values[k::2]] for k in range(2)]
    with pytest.raises(CallerDeadline):
        stats.kruskal_wallis(groups, 1.0)
    assert len(started) <= 65_536


@pytest.mark.parametrize(
    ("step", "checks"),
    [
        (lambda values, ends: stats.moments(values, ends), 12),
        (lambda values, ends: stats.median(values, ends), 8),
        (lambda values, ends: stats.bootstrap_medians(values, 7, 10, ends), 4),
        (lambda values, ends: stats.checked_sort(values[::-1], ends, itemgetter(0)), 7),
        (lambda values, ends: stats.mann_whitney(values[::2], values[1::2], ends), 24),
        (lambda values, ends: stats.kruskal_wallis([values[::2], values[1::2]], ends), 28),
    ],
    ids=["moments", "median", "bootstrap", "sort", "mann_whitney", "kruskal_wallis"],
)
def test_every_pass_over_a_group_s_values_checks_the_deadline_every_65536_values(
    monkeypatch: pytest.MonkeyPatch, step: Any, checks: int
) -> None:
    """At the answer cap a cohort holds five million values, so each pass over them checks the
    deadline 65,536 values at a time (D336): of 200,000 values, four times a pass, in the
    moments' three passes (the sum, the scale, the squares), the median's count and its two walks
    to the middle (twice each), the bootstrap's one, and a sort's four runs and three merges. A
    rank test of two groups of 100,000 checks twice a group as it counts them and as it reads
    them, seven times as it sorts them, four as it takes the distinct values, twice a group as it
    places them and once after (24); Kruskal–Wallis four times more as it counts ties. One that
    has passed stops the step at its first check."""
    values = [(float(v), 1) for v in range(200_000)]
    clock = _Clock(passes_after=10**9)
    monkeypatch.setattr(stats.time, "monotonic", clock)
    step(values, 1.0)
    assert clock.calls == checks
    passed = _Clock(passes_after=0)
    monkeypatch.setattr(stats.time, "monotonic", passed)
    with pytest.raises(CallerDeadline):
        step(values, 1.0)
    assert passed.calls == 1


@pytest.mark.parametrize("size", [0, 1, 65_536, 65_537, 2 * 65_536 + 5, 4 * 65_536 + 1])
def test_a_checked_sort_is_a_sort(size: int) -> None:
    """Runs of 65,536 merged two at a time, an odd run carried to the next round (D336)."""
    items = [float(v) for v in range(size)]
    random.Random(size).shuffle(items)
    assert stats.checked_sort(items, None, float) == sorted(items)


def test_the_ranks_distinct_values_are_distinct() -> None:
    ranked = stats._ranks([[(1.0, 1), (2.0, 3)], [(2.0, 1), (3.0, 2)]])  # pyright: ignore[reportPrivateUsage]
    assert (ranked.distinct, ranked.tied) == ([1.0, 2.0, 3.0], [1, 4, 2])


class _Clock:
    """A clock at 0 for its first reads and past any deadline after."""

    def __init__(self, passes_after: int) -> None:
        self.calls = 0
        self.passes_after = passes_after

    def __call__(self) -> float:
        self.calls += 1
        return 0.0 if self.calls <= self.passes_after else 2.0

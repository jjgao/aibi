"""The methods of the survival analyses, held to R's survival package and to their own rules
(SPEC §5.8, §9.3, §9.5; D349, D350).

``reference/survival.json`` was written by ``reference/survival.R`` (R 4.3.3, survival 3.5-8):
curves, medians and their intervals, landmarks and grid values, the log-rank test, Cox fits and
the test of proportional hazards, which every value here matches to 1e-10 relative."""

import json
import math
import random
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from aibi.core.analyses import timetoevent
from aibi.core.analyses.timetoevent import ORIGIN, Subject
from aibi.core.engine.worker import CallerDeadline

REFERENCE = json.loads(
    (Path(__file__).parent / "reference" / "survival.json").read_text(encoding="utf-8")
)
CASES = {case["name"]: case for case in REFERENCE["cases"]}
TOLERANCE = 1e-10


def close(found: float | None, expected: float | None) -> bool:
    if found is None or expected is None:
        return found is None and expected is None
    return abs(found - expected) <= TOLERANCE * max(1.0, abs(expected))


def groups_of(case: dict[str, Any]) -> tuple[list[list[Subject]], list[list[int]]]:
    """A case's units by group, in increasing order of group, and their strata."""
    names = sorted(set(case["group"]))
    groups: list[list[Subject]] = [[] for _ in names]
    strata: list[list[int]] = [[] for _ in names]
    for index, group in enumerate(case["group"]):
        entry = ORIGIN if case["entry"] is None else case["entry"][index]
        at = names.index(group)
        groups[at].append((entry, case["time"][index], bool(case["status"][index])))
        strata[at].append(0 if case["stratum"] is None else case["stratum"][index])
    return groups, strata


def test_the_reference_was_written_by_the_r_and_survival_versions_it_names() -> None:
    assert REFERENCE["r"].startswith("R version 4.3.3")
    assert REFERENCE["survival"] == "3.5.8"
    assert len(CASES) == len(REFERENCE["cases"])


@pytest.mark.parametrize("name", sorted(CASES))
def test_curves_are_r_s_survfit_with_log_log_bounds(name: str) -> None:
    case = CASES[name]
    groups, _ = groups_of(case)
    for subjects, expected in zip(groups, case["curves"], strict=True):
        steps = timetoevent.kaplan_meier(subjects, case["level"])
        assert len(steps) == len(expected["km"])
        for step, row in zip(steps, expected["km"], strict=True):
            found = [
                step.time,
                step.at_risk,
                step.events,
                step.censored,
                step.survival,
                step.low,
                step.high,
            ]
            assert all(close(a, b) for a, b in zip(found, row, strict=True)), (found, row)


@pytest.mark.parametrize("name", sorted(CASES))
def test_medians_and_their_intervals_are_quantile_survfit_s(name: str) -> None:
    case = CASES[name]
    groups, _ = groups_of(case)
    for subjects, expected in zip(groups, case["curves"], strict=True):
        found = timetoevent.median(timetoevent.kaplan_meier(subjects, case["level"]))
        assert all(close(a, b) for a, b in zip(found, expected["median"], strict=True))


@pytest.mark.parametrize("name", sorted(CASES))
def test_landmarks_and_grid_times_are_summary_survfit_s(name: str) -> None:
    case = CASES[name]
    groups, _ = groups_of(case)
    for subjects, expected in zip(groups, case["curves"], strict=True):
        steps = timetoevent.kaplan_meier(subjects, case["level"])
        for row in expected["landmarks"]:
            found = timetoevent.survival_at(steps, row[0])
            assert found is not None
            assert all(close(a, b) for a, b in zip(found, row[1:], strict=True))
        counted = timetoevent.grid_counts(subjects, case["grid"])
        for interval, row in zip(counted, expected["grid"], strict=True):
            at = timetoevent.survival_at(steps, row[0])
            found = [interval.at_risk, interval.events, interval.censored]
            found += [None, None, None] if at is None else list(at)
            assert all(close(a, b) for a, b in zip(found, row[1:], strict=True)), (found, row)


@pytest.mark.parametrize("name", sorted(CASES))
def test_the_log_rank_test_is_survdiff_s_or_the_exact_score_test_with_delayed_entry(
    name: str,
) -> None:
    case = CASES[name]
    groups, strata = groups_of(case)
    found = timetoevent.log_rank(timetoevent.risk_table(groups, strata), len(groups))
    expected = case["logrank"]
    assert close(found.statistic, expected["statistic"])
    assert found.df == expected["df"]
    assert close(found.p, expected["p"])


@pytest.mark.parametrize("name", sorted(CASES))
def test_cox_fits_are_coxph_s_with_efron_ties(name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    case = CASES[name]
    monkeypatch.setattr(timetoevent, "ITERATIONS", case["iter_max"])
    groups, strata = groups_of(case)
    expected = case["cox"]
    kept = [index for index, subjects in enumerate(groups) if any(s[2] for s in subjects)]
    table = timetoevent.risk_table([groups[i] for i in kept], [strata[i] for i in kept])
    fit = timetoevent.cox(table, 0)
    assert fit.converged == expected["converged"]
    assert list(fit.infinite) == expected["infinite"]
    assert fit.iterations == expected["iter"]
    for term, (coefficient, spread) in enumerate(
        zip(expected["coef"], expected["se"], strict=True)
    ):
        if expected["infinite"][term]:
            continue
        assert close(fit.coefficients[term], coefficient)
        assert close(math.sqrt(fit.variance[term][term]), spread)
        assert close(fit.loglik, expected["loglik"][1])


@pytest.mark.parametrize("name", sorted(CASES))
def test_the_test_of_proportional_hazards_is_cox_zph_s_global_test(
    name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = CASES[name]
    monkeypatch.setattr(timetoevent, "ITERATIONS", case["iter_max"])
    if any(case["cox"]["infinite"]):
        return
    groups, strata = groups_of(case)
    kept = [index for index, subjects in enumerate(groups) if any(s[2] for s in subjects)]
    fitted = [groups[i] for i in kept]
    table = timetoevent.risk_table(fitted, [strata[i] for i in kept])
    found = timetoevent.proportional_hazards(fitted, table, timetoevent.cox(table, 0), 0)
    expected = case["zph"]
    if expected is None:
        assert found is None
        return
    assert found is not None
    assert close(found.statistic, expected["statistic"])
    assert found.df == expected["df"]
    assert close(found.p, expected["p"])


def test_a_curve_at_one_before_any_event_has_bounds_of_one_and_at_zero_none() -> None:
    steps = timetoevent.kaplan_meier([(ORIGIN, 1.0, False), (ORIGIN, 2.0, True)], 0.95)
    assert [(step.survival, step.low, step.high) for step in steps] == [
        (1.0, 1.0, 1.0),
        (0.0, None, None),
    ]
    assert math.isinf(steps[-1].greenwood)


def test_an_entry_at_the_origin_is_just_before_zero_so_an_event_at_zero_is_at_risk() -> None:
    [step] = timetoevent.kaplan_meier([(ORIGIN, 0.0, True), (ORIGIN, 0.0, False)], 0.95)
    assert (step.at_risk, step.events, step.censored, step.survival) == (2, 1, 1, 0.5)
    entries, times = [0.0], [0.0]
    assert timetoevent.at_risk(entries, times, 0.0) == 0


def test_a_unit_that_enters_after_an_event_is_not_at_risk_at_it() -> None:
    subjects = [(ORIGIN, 2.0, True), (ORIGIN, 5.0, False), (3.0, 6.0, True)]
    steps = timetoevent.kaplan_meier(subjects, 0.95)
    assert [(step.time, step.at_risk) for step in steps] == [(2.0, 2), (5.0, 2), (6.0, 1)]


@pytest.mark.parametrize(
    ("y", "expected"),
    [
        ([0.0, 0.25, 0.75], 2.0),
        ([0.0, 0.5, 0.75], 1.5),
        ([0.0, 0.25, 0.5], 2.0),
        ([0.0, 0.5, 0.5], 1.5),
        ([0.0, 0.25, 0.4], None),
        ([0.0, 0.5 - 1e-9, 0.9], 1.5),
        ([0.0, 0.5 - 1e-7, 0.9], 2.0),
        ([0.0, None, 0.75], 2.0),
        ([None, None, None], None),
        ([0.0, 0.5 + 1e-9, None], None),
    ],
)
def test_findq_takes_the_first_crossing_the_midpoint_of_a_flat_half_or_of_where_it_ends(
    y: list[float | None], expected: float | None
) -> None:
    assert timetoevent.findq([0.0, 1.0, 2.0], y) == expected


def test_a_level_at_a_knot_is_read_at_that_knot() -> None:
    knots = [(0.25, 0), (0.5, 1), (0.75, 2)]
    assert timetoevent._right_constant(knots, 0.5) == 1  # pyright: ignore[reportPrivateUsage]
    assert timetoevent._right_constant(knots, 0.8) is None  # pyright: ignore[reportPrivateUsage]


def test_a_resample_s_median_is_the_median_of_its_curve_whatever_its_ties() -> None:
    rng = random.Random(7)
    for _ in range(200):
        n = rng.randint(1, 12)
        delayed = rng.random() < 0.5
        subjects: list[Subject] = []
        for _ in range(n):
            when = float(rng.randint(1, 6))
            entry = (
                float(rng.randint(0, int(when) - 1)) if delayed and rng.random() < 0.5 else ORIGIN
            )
            subjects.append((entry, when, rng.random() < 0.6))
        seed = rng.getrandbits(64)
        found = timetoevent.bootstrap_medians(subjects, seed, replicates=5)
        stream = random.Random(seed)
        ordered = sorted(subjects, key=lambda subject: (subject[1], not subject[2], subject[0]))
        for median in found:
            drawn = [ordered[int(stream.random() * n)] for _ in range(n)]
            expected = timetoevent.median(timetoevent.kaplan_meier(drawn, 0.95))[0]
            assert median == (math.inf if expected is None else expected)


def test_the_bootstrap_is_the_same_for_the_same_seed_and_units_in_any_order() -> None:
    rng = random.Random(3)
    subjects = [(ORIGIN, float(rng.randint(1, 30)), rng.random() < 0.7) for _ in range(40)]
    first = timetoevent.bootstrap_medians(subjects, 11, replicates=50)
    again = timetoevent.bootstrap_medians(list(reversed(subjects)), 11, replicates=50)
    other = timetoevent.bootstrap_medians(subjects, 12, replicates=50)
    assert first == again
    assert first != other
    assert len(first) == 50


def test_the_bootstrap_stops_at_the_deadline() -> None:
    subjects = [(ORIGIN, 1.0, True), (ORIGIN, 2.0, True)]
    with pytest.raises(CallerDeadline):
        timetoevent.bootstrap_medians(subjects, 1, ends=time.monotonic() - 1)
    with pytest.raises(ValueError, match="at least one unit"):
        timetoevent.bootstrap_medians([], 1)


def test_the_median_interval_counts_a_median_not_reached_as_infinity() -> None:
    inf = math.inf
    mine = [1.0, 2.0, 3.0, inf]
    theirs = [0.0, 0.0, inf, inf]
    assert timetoevent.median_interval(mine, theirs, 0.5) == (None, 2.0)
    assert timetoevent.median_interval([1.0, 2.0, 3.0, 4.0], [0.0] * 4, 0.5) == (1.0, 3.0)
    assert timetoevent.median_interval([inf] * 4, [0.0] * 4, 0.5) == (None, None)
    low, high = timetoevent.median_interval([float(i) for i in range(2000)], [0.0] * 2000, 0.95)
    assert (low, high) == (49.0, 1949.0)
    assert timetoevent.median_interval([3.0], [1.0], 0.5) == (2.0, 2.0)
    for mine, theirs, level in (([1.0], [], 0.95), ([], [], 0.95), ([1.0], [1.0], 1.0)):
        with pytest.raises(ValueError, match="in pairs"):
            timetoevent.median_interval(mine, theirs, level)
    with pytest.raises(ValueError, match="in pairs"):
        timetoevent.median_interval([1.0], [1.0], 0.0)


def test_a_log_rank_test_without_expected_events_in_two_groups_has_no_statistic() -> None:
    censored = [[(ORIGIN, 1.0, False)], [(ORIGIN, 2.0, False)]]
    found = timetoevent.log_rank(timetoevent.risk_table(censored), 2)
    assert (found.statistic, found.p, found.df) == (None, None, 0)
    apart = [[(ORIGIN, 1.0, True)], [(2.0, 3.0, True)]]
    found = timetoevent.log_rank(timetoevent.risk_table(apart), 2)
    assert (found.used, found.statistic, found.df) == ((0, 1), None, 0)


def test_an_event_time_with_one_unit_at_risk_adds_nothing_to_the_log_rank_test() -> None:
    later: list[list[Subject]] = [
        [(2.0, 4.0, True), (2.0, 7.0, False), (2.0, 5.0, True)],
        [(2.0, 5.0, True), (2.0, 6.0, True), (2.0, 3.0, False)],
    ]
    alone = [[(ORIGIN, 1.0, True), *later[0]], later[1]]
    without = timetoevent.log_rank(timetoevent.risk_table(later), 2)
    found = timetoevent.log_rank(timetoevent.risk_table(alone), 2)
    assert found.statistic == pytest.approx(without.statistic, rel=1e-12)
    assert found.df == without.df == 1


def test_a_group_never_at_risk_at_an_event_time_is_left_out_of_the_log_rank_test() -> None:
    groups: list[list[Subject]] = [
        [(ORIGIN, 2.0, True), (ORIGIN, 4.0, True), (ORIGIN, 6.0, False)],
        [(ORIGIN, 1.0, False)],
        [(ORIGIN, 3.0, True), (ORIGIN, 5.0, False), (5.5, 9.0, False)],
        [(3.5, 4.5, False)],
    ]
    found = timetoevent.log_rank(timetoevent.risk_table(groups), 4)
    assert found.used == (0, 2, 3)
    assert found.df == 2


def test_a_singular_information_leaves_its_term_at_zero_and_flags_it() -> None:
    matrix = [[4.0, 2.0], [2.0, 1.0]]
    factored = [list(row) for row in matrix]
    assert timetoevent.cholesky(factored) == 1
    assert timetoevent.solve(factored, [2.0, 1.0]) == [0.5, 0.0]
    assert timetoevent.inverse(factored) == [[0.25, 0.0], [0.0, 0.0]]
    apart = [[(ORIGIN, 1.0, True), (ORIGIN, 4.0, True)], [(ORIGIN, 0.5, False)]]
    fit = timetoevent.cox(timetoevent.risk_table(apart), 0)
    assert fit.coefficients == (0.0,)
    assert fit.singular == (True,)
    assert fit.infinite == (False,)
    assert timetoevent.proportional_hazards(apart, timetoevent.risk_table(apart), fit, 0) is None


def test_a_fit_that_runs_out_of_iterations_does_not_converge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = CASES["random 4"]
    groups, strata = groups_of(case)
    monkeypatch.setattr(timetoevent, "ITERATIONS", 1)
    fit = timetoevent.cox(timetoevent.risk_table(groups, strata), 0)
    assert not fit.converged
    assert fit.infinite == (False, False)


@settings(max_examples=60, deadline=None)
@given(
    st.lists(
        st.tuples(st.integers(0, 3), st.integers(1, 8), st.booleans()), min_size=1, max_size=14
    )
)
def test_a_curve_falls_stays_within_its_bounds_and_counts_every_unit_once(
    rows: Sequence[tuple[int, int, bool]],
) -> None:
    subjects = [
        (ORIGIN if entry == 0 else float(entry) - 0.5, float(when), event)
        for entry, when, event in rows
        if entry == 0 or entry - 0.5 < when
    ]
    if not subjects:
        return
    steps = timetoevent.kaplan_meier(subjects, 0.9)
    assert sum(step.events + step.censored for step in steps) == len(subjects)
    survival = 1.0
    for step in steps:
        assert step.survival <= survival
        survival = step.survival
        if step.low is not None and step.high is not None:
            assert 0 <= step.low <= step.survival <= step.high <= 1


def test_a_test_of_proportional_hazards_over_a_singular_information_has_no_value() -> None:
    together = [[(ORIGIN, 2.0, True), (ORIGIN, 5.0, False)], [(ORIGIN, 2.0, True)]]
    table = timetoevent.risk_table(together)
    fit = timetoevent.cox(table, 0)
    assert fit.converged
    assert fit.singular == (False,)
    assert timetoevent.proportional_hazards(together, table, fit, 0) is None


@settings(max_examples=60, deadline=None)
@given(st.integers(min_value=1, max_value=6), st.randoms(use_true_random=False))
def test_the_factorisation_solves_and_inverts_a_positive_definite_matrix(
    n: int, rng: random.Random
) -> None:
    b = [[rng.uniform(-2, 2) for _ in range(n)] for _ in range(n)]
    a = [
        [math.fsum(b[k][i] * b[k][j] for k in range(n)) + (i == j) for j in range(n)]
        for i in range(n)
    ]
    y = [rng.uniform(-3, 3) for _ in range(n)]
    factored = [list(row) for row in a]
    assert timetoevent.cholesky(factored) == n
    x = timetoevent.solve(factored, y)
    for i in range(n):
        assert math.fsum(a[i][j] * x[j] for j in range(n)) == pytest.approx(y[i], abs=1e-9)
    inverted = timetoevent.inverse(factored)
    for i in range(n):
        for j in range(n):
            found = math.fsum(a[i][k] * inverted[k][j] for k in range(n))
            assert found == pytest.approx(float(i == j), abs=1e-9)


def test_a_singular_matrix_is_factored_to_its_rank_and_inverted_on_its_other_terms() -> None:
    v, w = [1.0, 2.0, 3.0], [0.5, -1.0, 2.0]
    a = [[v[i] * v[j] + w[i] * w[j] for j in range(3)] for i in range(3)]
    factored = [list(row) for row in a]
    assert timetoevent.cholesky(factored) == 2
    inverted = timetoevent.inverse(factored)
    assert inverted[2] == [0.0, 0.0, 0.0]
    assert [row[2] for row in inverted] == [0.0, 0.0, 0.0]
    determinant = a[0][0] * a[1][1] - a[0][1] * a[1][0]
    expected = [[a[1][1] / determinant, -a[0][1] / determinant], [-a[1][0] / determinant, 0.0]]
    expected[1][1] = a[0][0] / determinant
    for i in range(2):
        for j in range(2):
            assert inverted[i][j] == pytest.approx(expected[i][j], rel=1e-9)


@pytest.mark.parametrize(
    ("matrix", "rank"),
    [
        ([[0.0, 0.0], [0.0, 1.0]], 1),
        ([[1.0, 0.0], [0.0, -1.0]], -1),
        ([[1e12, 0.0], [0.0, 0.1]], 1),
        ([[1.0, 0.0], [0.0, -1e-13]], 1),
        ([[math.inf, 0.0], [0.0, 1.0]], 0),
    ],
)
def test_a_pivot_below_the_tolerance_of_the_largest_is_dropped_and_a_negative_one_marks_the_rank(
    matrix: list[list[float]], rank: int
) -> None:
    found = timetoevent.cholesky([list(row) for row in matrix])
    assert found == rank
    assert type(found) is int


def test_a_term_without_information_between_others_is_left_out_of_the_inverse() -> None:
    factored = [[1.0, 1.0, 0.0], [1.0, 1.0, 0.0], [0.0, 0.0, 2.0]]
    assert timetoevent.cholesky(factored) == 2
    assert timetoevent.solve(factored, [2.0, 2.0, 4.0]) == [2.0, 0.0, 2.0]
    assert timetoevent.inverse(factored) == [[1.0, 0.0, 0.0], [0.0, 0.0, 0.0], [0.0, 0.0, 0.5]]


def test_a_fit_already_at_its_maximum_converges_in_one_iteration_as_coxph_does() -> None:
    same: list[Subject] = [(ORIGIN, 1.0, True), (ORIGIN, 2.0, True), (ORIGIN, 3.0, False)]
    fit = timetoevent.cox(timetoevent.risk_table([same, list(same)]), 0)
    assert (fit.coefficients, fit.iterations, fit.converged) == ((0.0,), 1, True)


def test_a_stratum_without_events_leaves_the_fit_to_the_others() -> None:
    groups: list[list[Subject]] = [
        [(ORIGIN, 1.0, False), (ORIGIN, 2.0, True), (ORIGIN, 4.0, True), (ORIGIN, 5.0, False)],
        [(ORIGIN, 1.5, False), (ORIGIN, 3.0, True), (ORIGIN, 3.5, True), (ORIGIN, 6.0, True)],
    ]
    strata = [[0, 1, 1, 1], [0, 1, 1, 1]]
    table = timetoevent.risk_table(groups, strata)
    assert table[0] == []
    fit = timetoevent.cox(table, 0)
    alone = timetoevent.cox(timetoevent.risk_table([group[1:] for group in groups]), 0)
    assert fit.coefficients == alone.coefficients


def test_a_term_without_information_before_others_leaves_theirs_inverted() -> None:
    factored = [
        [1.0, 1.0, 0.0, 0.0],
        [1.0, 1.0, 0.0, 0.0],
        [0.0, 0.0, 2.0, 1.0],
        [0.0, 0.0, 1.0, 2.0],
    ]
    assert timetoevent.cholesky(factored) == 3
    inverted = timetoevent.inverse(factored)
    assert inverted[1] == [0.0, 0.0, 0.0, 0.0]
    expected = [[2 / 3, -1 / 3], [-1 / 3, 2 / 3]]
    for i in range(2):
        for j in range(2):
            assert inverted[2 + i][2 + j] == pytest.approx(expected[i][j], rel=1e-12)


def test_every_method_over_many_units_stops_at_the_call_s_deadline() -> None:
    groups = [
        [(ORIGIN, float(i), True) for i in range(1, 50)],
        [(ORIGIN, i + 0.5, i % 2 == 0) for i in range(1, 50)],
    ]
    table = timetoevent.risk_table(groups)
    fit = timetoevent.cox(table, 0)
    past = time.monotonic() - 1
    with pytest.raises(CallerDeadline):
        timetoevent.kaplan_meier(groups[0], 0.95, past)
    with pytest.raises(CallerDeadline):
        timetoevent.risk_table(groups, ends=past)
    with pytest.raises(CallerDeadline):
        timetoevent.cox(table, 0, past)
    with pytest.raises(CallerDeadline):
        timetoevent.proportional_hazards(groups, table, fit, 0, past)
    later = time.monotonic() + 60
    assert timetoevent.cox(table, 0, later) == fit
    assert timetoevent.risk_table(groups, ends=later) == table


@pytest.mark.parametrize("statistic", [math.inf, -math.inf, math.nan])
def test_a_statistic_that_is_not_finite_has_no_p_value(statistic: float) -> None:
    assert math.isnan(timetoevent.chi_squared_p(statistic, 2))


def test_a_log_log_interval_of_a_variance_that_is_not_a_number_is_none() -> None:
    assert timetoevent.log_log(0.5, math.nan, 0.95) == (None, None)
    assert timetoevent.log_log(0.5, -1.0, 0.95) == (None, None)


def test_a_log_log_interval_whose_exp_overflows_is_0_to_1() -> None:
    assert timetoevent.log_log(0.5, 1e10, 0.95) == (0.0, 1.0)


@pytest.mark.parametrize("coefficient", [1000.0, -1000.0])
def test_a_test_of_proportional_hazards_at_a_coefficient_exp_takes_out_of_range_is_none(
    coefficient: float,
) -> None:
    groups = [
        [(ORIGIN, float(when), True) for when in (1, 2, 3, 4, 5)],
        [(ORIGIN, float(when), when % 2 == 0) for when in (2, 3, 4, 5, 6)],
    ]
    table = timetoevent.risk_table(groups)
    fit = timetoevent.cox(table, 0)
    wide = timetoevent.CoxFit(
        (coefficient,), fit.variance, (False,), (False,), True, fit.loglik, fit.iterations
    )
    assert timetoevent.proportional_hazards(groups, table, fit, 0) is not None
    assert timetoevent.proportional_hazards(groups, table, wide, 0) is None


def test_a_chi_squared_p_value_reads_a_negative_statistic_as_0() -> None:
    assert timetoevent.chi_squared_p(-1e-12, 1) == 1.0
    assert timetoevent.chi_squared_p(3.841458820694124, 1) == pytest.approx(0.05, rel=1e-12)


def _stubbed(
    monkeypatch: pytest.MonkeyPatch, width: int, bad: dict[int, dict[str, Any]]
) -> tuple[list[list[float]], Any]:
    """A fit of a quadratic log-likelihood, −(β₁ − 1)² − 10, whose later terms have no
    information, with the evaluations at the calls ``bad`` names replaced in part; the
    coefficients each call was at, and the fit."""
    seen: list[list[float]] = []

    def evaluate(table: Any, terms: Any, beta: list[float], ends: Any = None) -> Any:
        seen.append(list(beta))
        m = len(beta)
        found: dict[str, Any] = {
            "loglik": -((beta[0] - 1) ** 2) - 10,
            "score": [-2 * (beta[0] - 1)] + [0.0] * (m - 1),
            "information": [[2.0 if i == j == 0 else 0.0 for j in range(m)] for i in range(m)],
        }
        found.update(bad.get(len(seen) - 1, {}))
        return timetoevent._Evaluated(**found)  # pyright: ignore[reportPrivateUsage]

    monkeypatch.setattr(timetoevent, "_evaluate", evaluate)
    table = [[timetoevent.EventTime(1.0, (1,) * width, (1,) + (0,) * (width - 1))]]
    return seen, timetoevent.cox(table, 0)


@pytest.mark.parametrize(
    ("width", "bad"),
    [
        (2, {"loglik": math.nan}),
        (3, {"information": [[2.0, 0.0], [0.0, math.nan]]}),
        (2, {"information": [[0.0]]}),
    ],
    ids=["a log-likelihood not a number", "a diagonal not a number", "a change of rank"],
)
def test_an_iteration_that_fails_halves_the_step(
    monkeypatch: pytest.MonkeyPatch, width: int, bad: dict[str, Any]
) -> None:
    seen, _ = _stubbed(monkeypatch, width, {1: bad})
    assert seen[1][0] == 1.0
    assert seen[2][0] == 0.5


def test_a_converged_fit_whose_score_is_not_a_number_is_flagged_infinite(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen, fit = _stubbed(monkeypatch, 2, {2: {"score": [math.nan]}})
    assert [beta[0] for beta in seen] == [0.0, 1.0, 1.0]
    assert fit.converged
    assert fit.infinite == (True,)


def test_a_converged_fit_whose_score_is_0_is_not_flagged(monkeypatch: pytest.MonkeyPatch) -> None:
    _, fit = _stubbed(monkeypatch, 2, {})
    assert fit.converged
    assert fit.infinite == (False,)


@pytest.mark.parametrize(
    ("bad", "iterations"),
    [({1: {"loglik": 0.0}}, 20), ({0: {"loglik": 0.0}}, 1)],
    ids=["at an iteration", "at the start, out of iterations"],
)
def test_a_log_likelihood_of_0_is_compared_as_c_compares_it(
    monkeypatch: pytest.MonkeyPatch, bad: dict[int, dict[str, Any]], iterations: int
) -> None:
    monkeypatch.setattr(timetoevent, "ITERATIONS", iterations)
    seen, fit = _stubbed(monkeypatch, 2, bad)
    assert len(seen) >= 2
    assert fit.iterations <= iterations


def test_a_sum_of_weights_that_overflows_gives_a_log_likelihood_that_is_not_finite() -> None:
    table = [[timetoevent.EventTime(1.0, (1, 1, 1), (1, 0, 0))]]
    found = timetoevent._evaluate(table, [1, 2], [709.5, 709.5])  # pyright: ignore[reportPrivateUsage]
    assert not math.isfinite(found.loglik)


def test_a_singular_matrix_solved_gives_no_number() -> None:
    solved = timetoevent._solved_quadratic([[0.0, 0.0], [0.0, 0.0]], [1.0, 1.0])  # pyright: ignore[reportPrivateUsage]
    assert math.isnan(solved)

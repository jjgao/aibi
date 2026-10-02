"""The methods of the survival analyses, held to R's survival package and to their own rules
(SPEC §5.8, §9.3, §9.5; D349, D350).

``reference/survival.json`` was written by ``reference/survival.R`` (R 4.3.3, survival 3.5-8):
curves, medians and their intervals, landmarks and grid values and the log-rank test, which every
value here matches to 1e-10 relative."""

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
    assert timetoevent.median_interval([inf, inf, 1.0, 2.0], [inf, inf, 0.0, 0.0], 0.5) == (
        None,
        None,
    )
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


def test_a_singular_matrix_is_factored_to_its_rank_and_solved_at_zero_on_the_dropped_term() -> None:
    matrix = [[4.0, 2.0], [2.0, 1.0]]
    factored = [list(row) for row in matrix]
    assert timetoevent.cholesky(factored) == 1
    assert timetoevent.solve(factored, [2.0, 1.0]) == [0.5, 0.0]


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


@settings(max_examples=60, deadline=None)
@given(st.integers(min_value=1, max_value=6), st.randoms(use_true_random=False))
def test_the_factorisation_solves_a_positive_definite_matrix(n: int, rng: random.Random) -> None:
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


@pytest.mark.parametrize(
    ("matrix", "rank"),
    [
        ([[0.0, 0.0], [0.0, 1.0]], 1),
        ([[1.0, 0.0], [0.0, -1.0]], -1),
        ([[1e12, 0.0], [0.0, 0.1]], 1),
        ([[1.0, 0.0], [0.0, -1e-13]], 1),
        ([[1.0, 0.0], [0.0, -2e-11]], -1),
        ([[1.0, 0.0], [0.0, -7e-12]], 1),
        ([[math.inf, 0.0], [0.0, 1.0]], 0),
    ],
)
def test_a_pivot_below_the_tolerance_of_the_largest_is_dropped_and_a_negative_one_marks_the_rank(
    matrix: list[list[float]], rank: int
) -> None:
    found = timetoevent.cholesky([list(row) for row in matrix])
    assert found == rank
    assert type(found) is int


def test_a_term_without_information_between_others_is_solved_at_zero() -> None:
    factored = [[1.0, 1.0, 0.0], [1.0, 1.0, 0.0], [0.0, 0.0, 2.0]]
    assert timetoevent.cholesky(factored) == 2
    assert timetoevent.solve(factored, [2.0, 2.0, 4.0]) == [2.0, 0.0, 2.0]


def test_every_method_over_many_units_stops_at_the_call_s_deadline() -> None:
    groups = [
        [(ORIGIN, float(i), True) for i in range(1, 50)],
        [(ORIGIN, i + 0.5, i % 2 == 0) for i in range(1, 50)],
    ]
    table = timetoevent.risk_table(groups)
    past = time.monotonic() - 1
    with pytest.raises(CallerDeadline):
        timetoevent.kaplan_meier(groups[0], 0.95, past)
    with pytest.raises(CallerDeadline):
        timetoevent.risk_table(groups, ends=past)
    with pytest.raises(CallerDeadline):
        timetoevent.log_rank(table, 2, past)
    later = time.monotonic() + 60
    assert timetoevent.risk_table(groups, ends=later) == table
    assert timetoevent.log_rank(table, 2, later) == timetoevent.log_rank(table, 2)


@pytest.mark.parametrize("statistic", [math.inf, -math.inf, math.nan])
def test_a_statistic_that_is_not_finite_has_no_p_value(statistic: float) -> None:
    assert math.isnan(timetoevent.chi_squared_p(statistic, 2))


def test_a_log_log_interval_of_a_variance_that_is_not_a_number_is_none() -> None:
    assert timetoevent.log_log(0.5, math.nan, 0.95) == (None, None)
    assert timetoevent.log_log(0.5, -1.0, 0.95) == (None, None)


def test_a_log_log_interval_whose_exp_overflows_is_0_to_1() -> None:
    assert timetoevent.log_log(0.5, 1e10, 0.95) == (0.0, 1.0)


def test_a_chi_squared_p_value_reads_a_negative_statistic_as_0() -> None:
    assert timetoevent.chi_squared_p(-1e-12, 1) == 1.0
    assert timetoevent.chi_squared_p(3.841458820694124, 1) == pytest.approx(0.05, rel=1e-12)


def _reads(monkeypatch: pytest.MonkeyPatch, every: int) -> list[int]:
    """The clock's reads, counted, with ``LOOK_EVERY`` set to ``every``; the deadline never
    passes."""
    reads: list[int] = []

    def clock() -> float:
        reads.append(1)
        return 0.0

    monkeypatch.setattr(timetoevent, "time", type("Clock", (), {"monotonic": staticmethod(clock)}))
    monkeypatch.setattr(timetoevent, "LOOK_EVERY", every)
    return reads


def test_a_watch_looks_once_it_has_spent_look_every_units_and_then_counts_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reads = _reads(monkeypatch, 8)
    watch = timetoevent.Watch(1.0)
    watch.spend(3)
    watch.spend(4)
    assert reads == []
    watch.spend()
    assert len(reads) == 1
    watch.spend(7)
    assert len(reads) == 1
    watch.spend(100)
    assert len(reads) == 2
    watch.spend(5)
    assert len(reads) == 3
    watch.look()
    watch.spend(7)
    assert len(reads) == 4
    watch.spend()
    assert len(reads) == 5
    timetoevent.Watch(None).spend(100)
    timetoevent.Watch(None).look()
    assert len(reads) == 5


def test_a_watch_whose_deadline_has_passed_raises_at_its_next_look() -> None:
    watch = timetoevent.Watch(time.monotonic() - 1)
    watch.spend(timetoevent.LOOK_EVERY - 1)
    with pytest.raises(CallerDeadline):
        watch.spend()
    with pytest.raises(CallerDeadline):
        watch.look()


def test_every_loop_spends_the_work_of_its_turn_so_ties_never_stretch_the_time_between_looks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    every = 40
    groups = [[(ORIGIN, float(6 * i + g + 1), True) for i in range(40)] for g in range(6)]
    times = 6 * 40
    table = timetoevent.risk_table(groups)
    counted = {
        "risk_table": (lambda: timetoevent.risk_table(groups, ends=1.0), 6 * times),
        "log_rank": (lambda: timetoevent.log_rank(table, 6, 1.0), 36 * times),
        "kaplan_meier": (
            lambda: timetoevent.kaplan_meier([s for group in groups for s in group], 0.95, 1.0),
            times,
        ),
        "grid_counts": (
            lambda: timetoevent.grid_counts(
                [s for group in groups for s in group], [float(t) for t in range(1, times + 1)], 1.0
            ),
            2 * times,
        ),
        "bootstrap_medians": (
            lambda: timetoevent.bootstrap_medians(groups[0], 1, replicates=every, ends=1.0),
            every * every,
        ),
    }
    for name, (method, work) in counted.items():
        reads = _reads(monkeypatch, every)
        method()
        assert len(reads) >= work // every, name


def test_a_log_rank_statistic_below_0_by_rounding_is_0(monkeypatch: pytest.MonkeyPatch) -> None:
    groups = [[(ORIGIN, 1.0, True), (ORIGIN, 3.0, True)], [(ORIGIN, 2.0, True)]]
    table = timetoevent.risk_table(groups)
    monkeypatch.setattr(timetoevent, "quadratic", lambda *_: (-1e-17, 1))
    found = timetoevent.log_rank(table, 2)
    assert found.statistic is not None
    assert found.statistic == 0.0
    assert math.copysign(1.0, found.statistic) == 1.0
    assert found.p == 1.0


@settings(max_examples=200, deadline=None)
@given(
    st.lists(
        st.lists(st.tuples(st.integers(1, 40), st.booleans()), min_size=1, max_size=25),
        min_size=2,
        max_size=6,
    ),
    st.integers(0, 3),
)
def test_groups_never_at_risk_together_have_no_log_rank_test_whatever_the_rounding(
    groups: list[list[tuple[int, bool]]], gap: int
) -> None:
    """Each group's units enter after the last follow-up of the group before it ends, so at
    every event time one group alone is at risk: each time's covariance is exactly 0 (§9.5)."""
    subjects: list[list[Subject]] = []
    start = 0.0
    for group in groups:
        rows = [(start, start + 0.5 + when / 7, event) for when, event in group]
        subjects.append(rows)
        start = max(when for _, when, _ in rows) + gap
    found = timetoevent.log_rank(timetoevent.risk_table(subjects), len(subjects))
    assert (found.statistic, found.df, found.p) == (None, 0, None)


def test_a_group_alone_at_risk_adds_nothing_to_the_log_rank_covariance() -> None:
    apart = [
        [(10.0, 13.0, True)],
        [(20.0, 25.0, True), (20.0, 25.0, True), *[(20.0, 26.0, False)] * 17],
    ]
    found = timetoevent.log_rank(timetoevent.risk_table(apart), 2)
    assert (found.statistic, found.df, found.p) == (None, 0, None)


def test_a_resample_whose_curve_ends_just_above_one_half_has_no_median_as_findq_finds_none() -> (
    None
):
    subjects: list[Subject] = [
        *[(ORIGIN, 1.0, True)] * 5766,
        *[(ORIGIN, 1.5, False)] * 226,
        *[(ORIGIN, 2.0, True)] * 226,
        *[(ORIGIN, 3.0, False)] * 5783,
    ]
    steps = timetoevent.kaplan_meier(subjects, 0.95)
    assert 0.5 < steps[-1].survival < 0.5 + timetoevent.TOLERANCE
    assert timetoevent.median(steps)[0] is None
    arranged = timetoevent._Resampled(subjects)  # pyright: ignore[reportPrivateUsage]
    counts = [5766, 0, 0, 226, 226, 0, 0, 5783]
    assert arranged._median(counts, None) == math.inf  # pyright: ignore[reportPrivateUsage]

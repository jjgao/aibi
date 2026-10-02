"""The Cox fit of cohorts and its test of proportional hazards, held to R's survival package and
to the likelihood itself (SPEC §9.5; D355–D357).

``reference/cox.json`` was written by ``reference/cox.R`` (R 4.3.3, survival 3.5-8): the cohorts
the risk sets tie to the reference both ways, ``coxph``'s fit of them alone and ``cox.zph``'s
global test of it, which every value here matches to 1e-10 relative; and, for every set of edges
over two to four cohorts, ``coxph``'s fit of every cohort from two starting points, against which
each cohort's standing is checked."""

import dataclasses
import json
import math
import time
from collections.abc import Sequence
from itertools import permutations
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from aibi.core.analyses import coxfit, timetoevent
from aibi.core.analyses.coxfit import Standing
from aibi.core.analyses.timetoevent import ORIGIN, EventTime, Subject, Watch
from aibi.core.engine.worker import CallerDeadline

REFERENCE = json.loads(
    (Path(__file__).parent / "reference" / "cox.json").read_text(encoding="utf-8")
)
FITS = {case["name"]: case for case in REFERENCE["fits"]}
TOLERANCE = 1e-10
ZPH = 1e-8
"""The test of proportional hazards amplifies the fit's convergence tolerance (D357)."""
RAN_OUT = 1e-3
"""An iteration that runs out stops where rounding carried it; its values are never shown."""
OPTIMUM = 1e-6
"""A fit that converged to R's tolerance and the maximum refitted to 1e-13 agree to this in
coefficient and standard error (measured within 1.2e-7), and in log-likelihood to 1e-10."""
ZPH_OPTIMUM = 1e-5
"""And their tests of proportional hazards to this (measured within 3.9e-6)."""
FAR = 6.0
"""A coefficient R's fit of every cohort drives beyond this has left for ±∞ (30 iterations)."""


def close(found: float | None, expected: float | None, tolerance: float = TOLERANCE) -> bool:
    if found is None or expected is None:
        return found is None and expected is None
    return abs(found - expected) <= tolerance * max(1.0, abs(expected))


def table_of(groups: Sequence[Sequence[Subject]]) -> list[EventTime]:
    found = timetoevent.risk_table(groups)
    return found[0] if found else []


def with_events(case: dict[str, Any]) -> tuple[list[int], list[list[Subject]]]:
    """A case's groups with events, in increasing order, and their units."""
    names = sorted(set(case["group"]))
    groups: list[list[Subject]] = [[] for _ in names]
    for index, group in enumerate(case["group"]):
        entry = ORIGIN if case["entry"] is None else case["entry"][index]
        groups[names.index(group)].append((entry, case["time"][index], bool(case["status"][index])))
    kept = [at for at, subjects in enumerate(groups) if any(event for _, _, event in subjects)]
    return [names[at] for at in kept], [groups[at] for at in kept]


def edges_of(table: Sequence[EventTime], groups: int) -> set[tuple[int, int]]:
    return {
        (present, dying)
        for event in table
        for dying in range(groups)
        if event.events[dying]
        for present in range(groups)
        if event.at_risk[present] and present != dying
    }


def realised(groups: int, mask: int) -> list[list[Subject]]:
    """``cox.R``'s realisation of a set of edges: a window for each group in which one of its
    units has an event alone, and one for each edge h → e in which a unit of e has an event
    while a unit of h is at risk."""
    pairs = [(h, e) for h in range(groups) for e in range(groups) if h != e]
    found: list[list[Subject]] = [[] for _ in range(groups)]
    k = 0
    for group in range(groups):
        k += 1
        found[group].append((k - 0.5, float(k), True))
    for bit, (h, e) in enumerate(pairs):
        if mask >> bit & 1:
            k += 1
            found[e].append((k - 0.5, float(k), True))
            found[h].append((k - 0.5, k + 0.25, False))
    return found


def test_the_reference_was_written_by_the_r_and_survival_versions_it_names() -> None:
    assert REFERENCE["r"].startswith("R version 4.3.3")
    assert REFERENCE["survival"] == "3.5.8"
    assert len(FITS) == len(REFERENCE["fits"])
    assert len(REFERENCE["edges"]) == 2**2 + 2**6 + 2**12


@pytest.mark.parametrize("name", sorted(FITS))
def test_the_fitted_cohorts_are_those_the_risk_sets_tie_to_the_reference_both_ways(
    name: str,
) -> None:
    case = FITS[name]
    names, groups = with_events(case)
    standing = coxfit.standing(table_of(groups), len(groups), 0, Watch(None))
    fitted = [names[at] for at, one in enumerate(standing) if one is Standing.ESTIMATED]
    assert fitted == case["fitted"]


FITTED = sorted(name for name, case in FITS.items() if case["cox"] is not None)
ITERATED = sorted(name for name in FITTED if "error" not in FITS[name]["cox"])
OPTIMA = sorted(name for name in FITTED if FITS[name]["optimum"] is not None)


def fitted_of(case: dict[str, Any]) -> tuple[list[EventTime], list[int], list[int]]:
    """A case's risk table over its cohorts with events, the fitted ones and every one's units."""
    names, groups = with_events(case)
    return table_of(groups), [names.index(g) for g in case["fitted"]], [len(g) for g in groups]


@pytest.mark.parametrize("name", ITERATED)
def test_newton_s_iteration_is_coxph_s_with_efron_ties_from_zero(
    name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``newton`` is R's iteration, point for point; it converges where R's does and ends at
    the maximum, which R's does not where it drops a coefficient as singular."""
    case = FITS[name]
    expected = case["cox"]
    monkeypatch.setattr(coxfit, "ITERATIONS", case["iter_max"])
    table, fitted, _ = fitted_of(case)
    found = coxfit.newton(table, fitted, 0, Watch(None), counting=case["entry"] is not None)
    dropped = None in expected["coef"]
    assert found.converged == (expected["converged"] and not dropped)
    assert found.iterations == expected["iter"]
    tolerance = TOLERANCE if expected["converged"] else RAN_OUT
    for got, wanted in zip(found.coefficients, expected["coef"], strict=True):
        assert wanted is None or close(got, wanted, tolerance), (got, wanted)
    for got, wanted in zip(found.loglik, expected["loglik"], strict=True):
        assert close(got, wanted, tolerance), (got, wanted)
    if found.converged:
        for term, wanted in enumerate(expected["se"]):
            assert close(math.sqrt(found.variance[term][term]), wanted)
        units = fitted_of(case)[2]
        zph = coxfit.proportional_hazards(table, fitted, units, 0, found, Watch(None))
        if case["zph"] is None:
            assert zph is None
        else:
            assert zph is not None
            assert zph.df == case["zph"]["df"] == len(fitted) - 1
            assert close(zph.statistic, case["zph"]["statistic"], ZPH)
            assert close(zph.p, case["zph"]["p"], ZPH)


@pytest.mark.parametrize("name", OPTIMA)
def test_the_fit_reaches_the_maximum_r_finds_from_its_best_start(name: str) -> None:
    """``fit`` is R's iteration from 0 where that ends at the maximum; else it ascends to the
    maximum and ends there by R's iteration, which R reaches only from a better start."""
    case = FITS[name]
    expected = case["optimum"]
    table, fitted, units = fitted_of(case)
    found = coxfit.fit(table, fitted, 0, Watch(None), counting=case["entry"] is not None)
    counting = case["entry"] is not None
    straight = coxfit.newton(table, fitted, 0, Watch(None), counting=counting)
    assert found.converged
    assert found.ascended == (not straight.converged)
    if straight.converged:
        assert found == straight
    for got, wanted in zip(found.coefficients, expected["coef"], strict=True):
        assert close(got, wanted, OPTIMUM), (got, wanted)
    for term, wanted in enumerate(expected["se"]):
        assert close(math.sqrt(found.variance[term][term]), wanted, OPTIMUM)
    assert close(found.loglik[1], expected["loglik"])
    zph = coxfit.proportional_hazards(table, fitted, units, 0, found, Watch(None))
    if expected["zph"] is None:
        assert zph is None
    else:
        assert zph is not None
        assert close(zph.statistic, expected["zph"]["statistic"], ZPH_OPTIMUM)
        assert close(zph.p, expected["zph"]["p"], ZPH_OPTIMUM)


def efron(table: Sequence[EventTime], beta: Sequence[float]) -> float:
    """The Efron partial log-likelihood of every group, group 0's coefficient 0 and the others'
    ``beta``, summed directly (not ``coxfit``'s accumulation)."""
    eta = [0.0, *beta]
    terms: list[float] = []
    for event in table:
        deaths = sum(event.events)
        if not deaths:
            continue
        top = max(eta[g] for g, n in enumerate(event.at_risk) if n)
        risk = math.fsum(n * math.exp(eta[g] - top) for g, n in enumerate(event.at_risk))
        dying = math.fsum(n * math.exp(eta[g] - top) for g, n in enumerate(event.events))
        terms += [n * eta[g] for g, n in enumerate(event.events)]
        terms += [-(math.log(risk - k / deaths * dying) + top) for k in range(deaths)]
    return math.fsum(terms)


def potential(edges: set[tuple[int, int]], groups: int) -> tuple[list[int], list[set[int]]]:
    """A direction along which no term of the likelihood falls (D356), each group's component
    counted by the groups that reach it, less the reference's, so that d_h ≤ d_e on every
    edge h → e and the reference's component is at 0; and each group's component."""
    reach = [[i == j or (i, j) in edges for j in range(groups)] for i in range(groups)]
    for via in range(groups):
        for i in range(groups):
            for j in range(groups):
                reach[i][j] = reach[i][j] or (reach[i][via] and reach[via][j])
    ancestors = [sum(reach[i][g] for i in range(groups)) for g in range(groups)]
    components = [{h for h in range(groups) if reach[g][h] and reach[h][g]} for g in range(groups)]
    return [value - ancestors[0] for value in ancestors], components


def along(
    table: Sequence[EventTime],
    start: Sequence[float],
    direction: Sequence[int],
    far: float,
    shift: dict[int, float],
) -> float:
    """``efron`` at ``start`` moved ``far`` along ``direction``, each group in ``shift`` moved
    by its own amount too."""
    beta = [
        b + far * d + shift.get(g, 0.0)
        for g, (b, d) in enumerate(zip(start, direction, strict=True))
    ]
    return efron(table, beta[1:])


def test_every_set_of_edges_over_two_to_four_cohorts_is_classified_as_the_likelihood_behaves() -> (
    None
):
    """The graph rule (D356) for every set of edges: R's fit of every cohort gives an estimated
    cohort the reduced fit's coefficient from both starting points, and drives a lower one's to
    −∞ and a higher one's to +∞ from both; and along the graph's direction the likelihood rises
    to a limit that the reduced fit's coefficients maximise and that no apart cohort's
    coefficient changes, so that coefficient is not identified."""
    seen: dict[Standing, int] = dict.fromkeys(Standing, 0)
    for case in REFERENCE["edges"]:
        count, mask = case["groups"], case["mask"]
        groups = realised(count, mask)
        table = table_of(groups)
        pairs = [(h, e) for h in range(count) for e in range(count) if h != e]
        edges = {pair for bit, pair in enumerate(pairs) if mask >> bit & 1}
        assert edges_of(table, count) == edges
        standing = coxfit.standing(table, count, 0, Watch(None))
        fitted = [group for group in range(count) if standing[group] is Standing.ESTIMATED]
        reduced = (
            coxfit.fit(table, fitted, 0, Watch(None), counting=True) if len(fitted) > 1 else None
        )
        own = [0.0] * count
        for group in fitted[1:]:
            assert reduced is not None
            assert reduced.converged
            own[group] = reduced.coefficients[fitted.index(group) - 1]
        for group in range(1, count):
            zero, moved = case["zero"][group - 1], case["moved"][group - 1]
            seen[standing[group]] += 1
            where = (count, mask, group, standing[group], zero, moved)
            if standing[group] is Standing.ESTIMATED:
                assert abs(zero) < FAR, where
                assert close(zero, moved, 1e-3), where
                assert close(zero, own[group], 1e-3), (*where, own[group])
            elif standing[group] is Standing.LOWER:
                assert zero < -FAR, where
                assert moved < -FAR, where
            elif standing[group] is Standing.HIGHER:
                assert zero > FAR, where
                assert moved > FAR, where
        d, components = potential(edges, count)
        for group, one in enumerate(standing):
            if one is Standing.ESTIMATED:
                assert d[group] == 0
            elif one is not Standing.APART:
                assert (d[group] < 0) == (one is Standing.LOWER)
        limit = along(table, own, d, 40.0, {})
        assert along(table, own, d, 10.0, {}) <= limit + 1e-9, (count, mask)
        for group in range(1, count):
            if standing[group] is Standing.APART:
                for shift in (-2.0, 2.0):
                    moved = dict.fromkeys(components[group], shift)
                    assert close(along(table, own, d, 40.0, moved), limit, 1e-9), (mask, group)
            elif standing[group] is Standing.ESTIMATED:
                for shift in (-0.5, 0.5):
                    assert along(table, own, d, 40.0, {group: shift}) < limit, (mask, group)
    assert all(seen[one] > 100 for one in Standing), seen


@st.composite
def _risk_tables(draw: st.DrawFn) -> list[list[Subject]]:
    count = draw(st.integers(2, 4))
    groups: list[list[Subject]] = []
    for _ in range(count):
        size = draw(st.integers(1, 5))
        rows: list[Subject] = []
        for _ in range(size):
            time = float(draw(st.integers(1, 6)))
            entry = draw(st.one_of(st.just(ORIGIN), st.integers(0, int(time) - 1).map(float)))
            rows.append((entry, time, draw(st.booleans())))
        groups.append(rows)
    return groups


@settings(max_examples=300, deadline=None)
@given(_risk_tables(), st.data())
def test_a_cohort_s_standing_follows_it_under_any_relabelling_and_a_unit_that_adds_no_edge(
    groups: list[list[Subject]], data: st.DataObject
) -> None:
    count = len(groups)
    standing = coxfit.standing(table_of(groups), count, 0, Watch(None))
    order = data.draw(st.sampled_from(list(permutations(range(count)))))
    moved = [groups[old] for old in order]
    relabelled = coxfit.standing(table_of(moved), count, order.index(0), Watch(None))
    assert relabelled == [standing[old] for old in order]
    last = max(time for subjects in groups for _, time, _ in subjects)
    group = data.draw(st.integers(0, count - 1))
    later = [list(subjects) for subjects in groups]
    later[group].append((last, last + 1.0, False))
    assert coxfit.standing(table_of(later), count, 0, Watch(None)) == standing


def test_a_cohort_s_place_is_decided_by_paths_to_and_from_the_reference() -> None:
    ahead = [(ORIGIN, 1.0, True), (ORIGIN, 2.0, True)]
    behind = [(ORIGIN, 7.0, True), (ORIGIN, 8.0, False)]
    mixed = [(ORIGIN, 3.0, True), (ORIGIN, 5.5, True)]
    table = table_of([mixed, ahead, behind])
    assert coxfit.standing(table, 3, 0, Watch(None)) == [
        Standing.ESTIMATED,
        Standing.HIGHER,
        Standing.LOWER,
    ]
    apart = [(10.0, 11.0, True), (10.0, 12.0, True)]
    assert coxfit.standing(table_of([ahead, apart]), 2, 0, Watch(None)) == [
        Standing.ESTIMATED,
        Standing.APART,
    ]


def test_a_fit_needs_the_reference_and_another_cohort() -> None:
    table = table_of([[(ORIGIN, 1.0, True)], [(ORIGIN, 1.0, True)]])
    with pytest.raises(ValueError, match="reference"):
        coxfit.fit(table, [1], 0, Watch(None), counting=False)
    fit = coxfit.fit(table, [0, 1], 0, Watch(None), counting=False)
    with pytest.raises(ValueError, match="terms"):
        coxfit.proportional_hazards(
            table, [0, 1], [1, 1], 0, dataclasses.replace(fit, coefficients=()), Watch(None)
        )


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


TIED = [
    [(ORIGIN, 1.0, True)] * 40 + [(ORIGIN, float(t), t % 2 == 0) for t in range(2, 8)]
    for _ in range(6)
]
"""Six cohorts alike, 240 deaths tied at one time: shares that cost the terms squared each."""


def test_every_loop_of_the_fit_spends_the_work_of_its_turn_so_ties_never_stretch_its_looks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    every = 40
    table = table_of(TIED)
    everyone = list(range(6))
    fit = coxfit.fit(table, everyone, 0, Watch(None), counting=False)
    assert fit.iterations == 1
    shares = 240 * 25
    counted = {
        "standing": (lambda: coxfit.standing(table, 6, 0, Watch(1.0)), len(table) * 36),
        "fit": (
            lambda: coxfit.fit(table, everyone, 0, Watch(1.0), counting=False),
            2 * (shares + len(table) * 6 * 25),
        ),
        "counting fit": (
            lambda: coxfit.fit(table, everyone, 0, Watch(1.0), counting=True),
            2 * (shares + len(table) * 6 * 25),
        ),
        "proportional_hazards": (
            lambda: coxfit.proportional_hazards(table, everyone, [46] * 6, 0, fit, Watch(1.0)),
            shares + len(table) * 6 * 25,
        ),
    }
    for name, (method, work) in counted.items():
        with monkeypatch.context() as patch:
            reads = _reads(patch, every)
            method()
        assert len(reads) >= work // every, name


def test_every_step_of_the_fit_stops_at_the_call_s_deadline() -> None:
    table = table_of(TIED)
    everyone = list(range(6))
    fit = coxfit.fit(table, everyone, 0, Watch(None), counting=False)
    past = time.monotonic() - 1
    with pytest.raises(CallerDeadline):
        coxfit.standing(table, 6, 0, Watch(past))
    for counting in (False, True):
        with pytest.raises(CallerDeadline):
            coxfit.fit(table, everyone, 0, Watch(past), counting=counting)
    with pytest.raises(CallerDeadline):
        coxfit.proportional_hazards(table, everyone, [46] * 6, 0, fit, Watch(past))
    later = time.monotonic() + 60
    assert coxfit.fit(table, everyone, 0, Watch(later), counting=False) == fit


class Scripted:
    """A model whose log-likelihood, score and information at each call are given in turn, and
    which records the points it was asked for."""

    def __init__(self, points: Sequence[tuple[float, list[float], list[list[float]]]]) -> None:
        self.points = list(points)
        self.seen: list[list[float]] = []

    def __call__(self, beta: Sequence[float]) -> Any:
        self.seen.append(list(beta))
        loglik, score, information = self.points.pop(0)
        return coxfit._Evaluated(loglik, score, information)  # pyright: ignore[reportPrivateUsage]


ONE = [[1.0]]


def test_agfit4_s_iteration_does_not_converge_on_a_halved_step_but_takes_one_more() -> None:
    near = -10 + 5e-9
    model = Scripted(
        [(-10.0, [1.0], ONE), (-11.0, [1.0], ONE), (near, [0.5], ONE), (near, [0.0], ONE)]
    )
    found = coxfit._counting(model, [0.0])  # type: ignore[arg-type]  # pyright: ignore[reportPrivateUsage]
    assert model.seen == [[0.0], [1.0], [0.5], [1.0]]
    assert (found.coefficients, found.iterations, found.converged) == ((1.0,), 3, True)


def test_agfit4_s_iteration_run_out_after_a_fall_beyond_eps_returns_to_the_last_good_point(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(coxfit, "ITERATIONS", 2)
    fall = -9.0 - 9e-8
    model = Scripted(
        [(-10.0, [1.0], ONE), (-9.0, [1.0], ONE), (fall, [1.0], ONE), (-9.0, [1.0], ONE)]
    )
    found = coxfit._counting(model, [0.0])  # type: ignore[arg-type]  # pyright: ignore[reportPrivateUsage]
    assert model.seen == [[0.0], [1.0], [2.0], [1.0]]
    assert (found.coefficients, found.loglik[1], found.converged) == ((1.0,), -9.0, False)


@pytest.mark.parametrize(
    ("points", "seen", "found"),
    [
        ([(-10.0, [5.0], ONE), (-9.0, [0.0], ONE)], [[0.0], [1.0]], [1.0]),
        (
            [(-10.0, [1.0], ONE), (-10.5, [1.0], ONE), (-9.9, [0.0], ONE)],
            [[0.0], [1.0], [0.5]],
            [0.5],
        ),
        (
            [
                (-10.0, [0.5, 0.5], [[1.0, 0.0], [0.0, 0.0]]),
                (-9.0, [0.0, 0.0], [[1.0, 0.0], [0.0, 1.0]]),
            ],
            [[0.0, 0.0], [0.5, 0.5]],
            [0.5, 0.5],
        ),
        ([(-10.0, [0.0], [[0.0]])], [[0.0]], None),
    ],
    ids=[
        "a long step capped",
        "a step that falls halved",
        "the score where the rank is short",
        "a flat point",
    ],
)
def test_the_ascent_caps_and_halves_its_steps_and_follows_the_score_where_newton_cannot(
    points: list[tuple[float, list[float], list[list[float]]]],
    seen: list[list[float]],
    found: list[float] | None,
) -> None:
    model = Scripted(points)
    start = [0.0] * len(points[0][1])
    assert coxfit._ascent(model, start) == found  # type: ignore[arg-type]  # pyright: ignore[reportPrivateUsage]
    assert model.seen == seen


def test_agfit4_s_iteration_run_out_after_a_rise_within_eps_keeps_the_last_point(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(coxfit, "ITERATIONS", 2)
    rise = -9.0 + 9e-10
    model = Scripted([(-10.0, [1.0], ONE), (-9.0, [1.0], ONE), (rise, [1.0], [[0.0]])])
    found = coxfit._counting(model, [0.0])  # type: ignore[arg-type]  # pyright: ignore[reportPrivateUsage]
    assert model.seen == [[0.0], [1.0], [2.0]]
    assert (found.coefficients, found.loglik[1], found.converged) == ((2.0,), rise, False)


def test_agfit4_s_iteration_of_one_iteration_never_returns_to_the_last_good_point(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(coxfit, "ITERATIONS", 1)
    model = Scripted([(-10.0, [1.0], ONE), (-11.0, [1.0], ONE)])
    found = coxfit._counting(model, [0.0])  # type: ignore[arg-type]  # pyright: ignore[reportPrivateUsage]
    assert model.seen == [[0.0], [1.0]]
    assert (found.coefficients, found.loglik[1], found.converged) == ((1.0,), -11.0, False)


@pytest.mark.parametrize(
    ("loglik", "decrement", "reached"),
    [
        (-0.5, 1.5e-9, True),
        (-0.5, 2.5e-9, False),
        (-1000.0, 1.9e-6, True),
        (-1000.0, 2.1e-6, False),
    ],
)
def test_a_point_is_the_maximum_where_a_newton_step_would_gain_at_most_eps_of_the_loglik_or_of_1(
    loglik: float, decrement: float, reached: bool
) -> None:
    evaluated = coxfit._Evaluated(loglik, [math.sqrt(decrement)], [[1.0]])  # pyright: ignore[reportPrivateUsage]
    factored = [[1.0]]
    assert coxfit._at_maximum(evaluated, factored, 1) is reached  # pyright: ignore[reportPrivateUsage]
    assert coxfit._at_maximum(evaluated, factored, 0) is False  # pyright: ignore[reportPrivateUsage]


def test_the_ascent_takes_newton_s_step_where_the_information_has_full_rank() -> None:
    model = Scripted([(-10.0, [2.0], [[4.0]]), (-9.0, [0.0], [[4.0]])])
    assert coxfit._ascent(model, [0.0]) == [0.5]  # type: ignore[arg-type]  # pyright: ignore[reportPrivateUsage]
    assert model.seen == [[0.0], [0.5]]

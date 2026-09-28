"""The Cox model of a design, held to R's ``coxph`` and ``cox.zph`` and to ``coxfit`` (SPEC §9.5;
D358, D359).

``reference/coxph.json`` was written by ``reference/coxph.R`` (R 4.3.3, survival 3.5-8): designs
of numbers, 0/1 and −1/0/1 columns, integers, repeated, constant and summed columns and columns
of scale 1e6 and 1e-6, over strata, with and without entries; ``coxph``'s fit and ``cox.zph``'s
global test of each."""

import dataclasses
import json
import math
import random
import time
from pathlib import Path
from typing import Any

import pytest

from aibi.core.analyses import coxfit, coxph, timetoevent
from aibi.core.analyses.coxph import Unit
from aibi.core.analyses.timetoevent import ORIGIN, Watch
from aibi.core.engine.worker import CallerDeadline

REFERENCE = json.loads(
    (Path(__file__).parent / "reference" / "coxph.json").read_text(encoding="utf-8")
)
CASES = {case["name"]: case for case in REFERENCE["cases"]}
TOLERANCE = 1e-10
ZPH = 1e-8
NEAR = 1e-5
"""A near dependence amplifies rounding by the information's condition: the variance and the
test of such a design agree with R to this (D359)."""
RESIDUE = 1e-3
"""Where ``zph2`` never resets its sums, R's test carries the rounding of the units that left
(D359): a design with such a unit agrees to this."""
SCALES = frozenset({"large", "small"})
"""The columns whose scales alone make R's ``solve`` refuse the test (D359)."""
OVERFLOWS = frozenset({"far"})
"""The designs on which R's ``cox.zph`` stops, its linear predictor overflowing ``exp``."""
FAR = 1.1065708983862
"""The far unit's test in 80-digit arithmetic (round 2 of #60's review), which R cannot give."""
APART = 3.396175775074547
"""The test of strata far apart in 80-digit arithmetic (round 3 of #60's review), which R cannot
give."""


def close(found: float, expected: float, tolerance: float = TOLERANCE) -> bool:
    return abs(found - expected) <= tolerance * max(1.0, abs(expected))


def units_of(case: dict[str, Any]) -> list[Unit]:
    return [
        Unit(
            int(case["stratum"][i]),
            ORIGIN if case["entry"] is None else case["entry"][i],
            case["time"][i],
            bool(case["status"][i]),
            tuple(case["x"][i]),
        )
        for i in range(len(case["time"]))
    ]


def test_the_reference_was_written_by_the_r_and_survival_versions_it_names() -> None:
    assert REFERENCE["r"].startswith("R version 4.3.3")
    assert REFERENCE["survival"] == "3.5.8"
    assert len(CASES) == len(REFERENCE["cases"])


@pytest.mark.parametrize("name", sorted(CASES))
def test_a_design_s_fit_is_coxph_s_and_drops_the_columns_coxph_reports_na(name: str) -> None:
    case = CASES[name]
    found = coxph.fit(units_of(case), Watch(None), counting=case["entry"] is not None)
    assert list(found.dropped) == [j for j, c in enumerate(case["coef"]) if c is None]
    assert list(found.kept) == [j for j, c in enumerate(case["coef"]) if c is not None]
    assert found.fit is not None
    assert case["converged"]
    assert found.fit.converged
    assert not found.fit.ascended
    assert found.fit.iterations == case["iter"]
    loose = NEAR if {"near", "far"} & set(case["kinds"]) else TOLERANCE
    kept = list(found.kept)
    for term, column in enumerate(kept):
        assert close(found.fit.coefficients[term], case["coef"][column], loose)
        assert close(math.sqrt(found.fit.variance[term][term]), case["se"][column], loose)
    wide = len(case["var"]) == len(case["coef"])
    for i, row in enumerate(kept):
        for k, column in enumerate(kept):
            wanted = case["var"][row][column] if wide else case["var"][i][k]
            assert close(found.fit.variance[i][k], wanted, loose)
    for got, wanted in zip(found.fit.loglik, case["loglik"], strict=True):
        assert close(got, wanted)


@pytest.mark.parametrize("name", sorted(CASES))
def test_the_test_of_proportional_hazards_of_a_design_is_cox_zph_s_with_strata(name: str) -> None:
    case = CASES[name]
    units = units_of(case)
    found = coxph.fit(units, Watch(None), counting=case["entry"] is not None)
    tested = coxph.proportional_hazards(units, found, Watch(None))
    expected = case["zph"]
    if expected is None:
        if OVERFLOWS & set(case["kinds"]):
            assert tested is not None
            if name == "a unit far from the others, with entries":
                assert close(tested.statistic, FAR, 1e-10)
            if name == "strata far apart, with entries":
                assert close(tested.statistic, APART, 1e-9)
            return
        assert tested is None or SCALES & set(case["kinds"])
        return
    assert tested is not None
    assert tested.df == expected["df"] == len(found.kept)
    loose = NEAR if "near" in case["kinds"] else RESIDUE if "ghost" in case["kinds"] else ZPH
    assert close(tested.statistic, expected["statistic"], loose)
    assert close(tested.p, expected["p"], loose)


def test_where_only_the_columns_scales_make_r_refuse_the_test_it_is_given() -> None:
    scaled = [
        case for case in CASES.values() if SCALES & set(case["kinds"]) and case["zph"] is None
    ]
    assert scaled
    for case in scaled:
        units = units_of(case)
        found = coxph.fit(units, Watch(None), counting=case["entry"] is not None)
        assert coxph.proportional_hazards(units, found, Watch(None)) is not None


@pytest.mark.parametrize("entered", [False, True])
def test_a_design_of_cohort_indicators_is_coxfit_s_fit_and_test(entered: bool) -> None:
    rng = random.Random(9)
    groups = [
        [
            (
                float(rng.randint(0, 3)) if entered else ORIGIN,
                float(rng.randint(4, 30)),
                rng.random() < 0.7,
            )
            for _ in range(40)
        ]
        for _ in range(3)
    ]
    units = [
        Unit(0, entry, when, event, tuple(1.0 if g == k else 0.0 for k in (1, 2)))
        for g, group in enumerate(groups)
        for entry, when, event in group
    ]
    found = coxph.fit(units, Watch(None), counting=entered)
    [table] = timetoevent.risk_table(groups)
    expected = coxfit.fit(table, [0, 1, 2], 0, Watch(None), counting=entered)
    assert found.fit is not None
    assert found.fit.converged
    for got, wanted in zip(found.fit.coefficients, expected.coefficients, strict=True):
        assert close(got, wanted, 1e-12)
    for got, wanted in zip(found.fit.loglik, expected.loglik, strict=True):
        assert close(got, wanted, 1e-12)
    tested = coxph.proportional_hazards(units, found, Watch(None))
    other = coxfit.proportional_hazards(table, [0, 1, 2], [40] * 3, 0, expected, Watch(None))
    assert tested is not None
    assert other is not None
    assert close(tested.statistic, other.statistic, 1e-10)


def test_agfit4_centres_every_column_by_the_first_unit_it_sorts_and_coxfit6_by_the_mean() -> None:
    units = [
        Unit(2, 0.0, 9.0, True, (7.0, 1.0)),
        Unit(1, 0.0, 5.0, True, (3.0, 0.0)),
        Unit(1, 0.0, 8.0, False, (4.0, 1.0)),
        Unit(1, 0.0, 8.0, True, (6.0, 0.0)),
        Unit(1, 6.0, 7.0, False, (10.0, 0.0)),
    ]
    used = coxph._used(units, True, Watch(None))  # pyright: ignore[reportPrivateUsage]
    centres, scales = coxph._centres(units, used, True, Watch(None))  # pyright: ignore[reportPrivateUsage]
    assert centres == [4.0, 0.0]
    used = [3.0, 4.0, 6.0, 7.0]
    assert scales == [len(used) / sum(abs(v - 4.0) for v in used), 1.0]
    centres, scales = coxph._centres(units, units, False, Watch(None))  # pyright: ignore[reportPrivateUsage]
    assert centres == [sum((3.0, 4.0, 6.0, 10.0, 7.0)) / 5, 0.0]


def test_identical_units_are_one_row_whose_sums_count_them() -> None:
    once = [Unit(0, ORIGIN, float(t), t % 3 != 0, (float(t % 4),)) for t in range(1, 30)]
    rows = coxph._collapsed(once + once, Watch(None))  # pyright: ignore[reportPrivateUsage]
    assert [(row.time, row.count) for row in rows] == [(unit.time, 2) for unit in once]


def _reads(monkeypatch: pytest.MonkeyPatch, every: int) -> list[int]:
    reads: list[int] = []

    def clock() -> float:
        reads.append(1)
        return 0.0

    monkeypatch.setattr(timetoevent, "time", type("Clock", (), {"monotonic": staticmethod(clock)}))
    monkeypatch.setattr(timetoevent, "LOOK_EVERY", every)
    return reads


TIED = [
    Unit(s, ORIGIN, 1.0 if i < 30 else float(i), True, (float(i % 5), float(i % 2)))
    for s in (0, 1)
    for i in range(60)
]
"""Two strata of 60 units, 30 of each with events tied at one time."""


def test_every_loop_of_the_design_s_fit_spends_the_work_of_its_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    every = 40
    found = coxph.fit(TIED, Watch(None), counting=False)
    assert found.fit is not None
    rows = len(coxph._collapsed(TIED, Watch(None)))  # pyright: ignore[reportPrivateUsage]
    evaluations = found.fit.iterations + 2
    work = evaluations * (rows + 60) * 4
    with monkeypatch.context() as patch:
        reads = _reads(patch, every)
        coxph.fit(TIED, Watch(1.0), counting=False)
    assert len(reads) >= work // every
    with monkeypatch.context() as patch:
        reads = _reads(patch, every)
        coxph.proportional_hazards(TIED, found, Watch(1.0))
    assert len(reads) >= (rows + 60) * 4 // every


def test_the_design_s_fit_and_its_test_stop_at_the_call_s_deadline() -> None:
    found = coxph.fit(TIED, Watch(None), counting=False)
    past = time.monotonic() - 1
    entered = [Unit(u.stratum, 0.0, u.time, u.event, u.x) for u in TIED]
    for units, counting in ((TIED, False), (entered, True)):
        with pytest.raises(CallerDeadline):
            coxph.fit(units, Watch(past), counting=counting)
    with pytest.raises(CallerDeadline):
        coxph.proportional_hazards(TIED, found, Watch(past))


def test_a_column_no_double_can_scale_is_left_out_as_unscalable() -> None:
    rng = random.Random(4)
    units = [
        Unit(
            0,
            ORIGIN,
            float(rng.randint(1, 20)),
            rng.random() < 0.7,
            (rng.gauss(0, 1), rng.gauss(0, 1) * 1e-200, 1e308 if i == 0 else rng.gauss(0, 1)),
        )
        for i in range(60)
    ]
    found = coxph.fit(units, Watch(None), counting=False)
    assert (found.kept, found.dropped, found.unscalable) == ((0,), (), (1, 2))
    assert found.fit is not None
    assert all(math.isfinite(v) for row in found.fit.variance for v in row)


def test_a_design_whose_newton_step_lands_where_the_information_vanishes_ascends_as_coxfit() -> (
    None
):
    reference = [(ORIGIN, float(t), True) for t in range(1, 21)]
    late = [(ORIGIN, float(21 + i % 40), True) for i in range(200)] + [(ORIGIN, 1.0, True)]
    groups = [reference, late, list(late)]
    units = [
        Unit(0, entry, when, event, tuple(1.0 if g == k else 0.0 for k in (1, 2)))
        for g, group in enumerate(groups)
        for entry, when, event in group
    ]
    found = coxph.fit(units, Watch(None), counting=False)
    [table] = timetoevent.risk_table(groups)
    expected = coxfit.fit(table, [0, 1, 2], 0, Watch(None), counting=False)
    assert found.fit is not None
    assert (found.fit.converged, found.fit.ascended) == (True, True)
    for got, wanted in zip(found.fit.coefficients, expected.coefficients, strict=True):
        assert close(got, wanted, 1e-8)


def test_a_unit_that_enters_at_an_event_time_is_not_at_risk_then() -> None:
    event = Unit(0, 0.0, 5.0, True, (1.0,))
    enters = Unit(0, 5.0, 6.0, False, (0.0,))
    spans = Unit(0, 4.0, 6.0, False, (0.0,))
    used = coxph._used([event, enters, spans], True, Watch(None))  # pyright: ignore[reportPrivateUsage]
    assert used == [event, spans]


def test_a_column_is_centred_unless_every_unit_s_value_lies_in_minus_one_to_one() -> None:
    units = [
        Unit(0, 0.0, 5.0, True, (0.0,)),
        Unit(0, 0.0, 7.0, False, (1.0,)),
        Unit(0, 8.0, 9.0, False, (5.0,)),
    ]
    used = coxph._used(units, True, Watch(None))  # pyright: ignore[reportPrivateUsage]
    assert all(u.x[0] in (0.0, 1.0) for u in used)
    centres, _ = coxph._centres(units, used, True, Watch(None))  # pyright: ignore[reportPrivateUsage]
    assert centres == [1.0]
    assert coxph._centres(units[:2], units[:2], True, Watch(None))[1] == [1.0]  # pyright: ignore[reportPrivateUsage]


def test_a_column_of_minus_one_zero_and_one_is_neither_centred_nor_scaled() -> None:
    units = [
        Unit(0, ORIGIN, 5.0, True, (-1.0,)),
        Unit(0, ORIGIN, 7.0, False, (1.0,)),
        Unit(0, ORIGIN, 9.0, True, (1.0,)),
    ]
    assert coxph._centres(units, units, False, Watch(None)) == ([0.0], [1.0])  # pyright: ignore[reportPrivateUsage]
    units.append(Unit(0, ORIGIN, 11.0, False, (-2.0,)))
    assert coxph._centres(units, units, False, Watch(None)) != ([0.0], [1.0])  # pyright: ignore[reportPrivateUsage]


def test_a_design_s_entries_must_match_the_fit_it_asks_for() -> None:
    units = [Unit(0, 0.0, 5.0, True, (1.0,)), Unit(0, ORIGIN, 7.0, True, (0.0,))]
    for counting in (False, True):
        with pytest.raises(ValueError, match="entries"):
            coxph.fit(units, Watch(None), counting=counting)


def test_a_fit_that_did_not_converge_has_no_test_of_proportional_hazards() -> None:
    case = CASES["numbers and 0/1"]
    units = units_of(case)
    found = coxph.fit(units, Watch(None), counting=False)
    assert found.fit is not None
    stopped = coxph.Fitted(
        found.kept, found.dropped, dataclasses.replace(found.fit, converged=False)
    )
    assert coxph.proportional_hazards(units, found, Watch(None)) is not None
    assert coxph.proportional_hazards(units, stopped, Watch(None)) is None


def moment(group: list[Unit], beta: float, top: float, power: int) -> float:
    """Σ exp(βx − top)·x^power over ``group``, of one covariate."""
    return math.fsum(math.exp(beta * u.x[0] - top) * u.x[0] ** power for u in group)


def efron(units: list[Unit], beta: float) -> tuple[float, float, float]:
    """The Efron partial log-likelihood of ``units`` of one covariate at ``beta``, its score and
    its information, per stratum over left-truncated risk sets, summed directly with
    log-sum-exp (not ``coxph``'s sweep)."""
    loglik: list[float] = []
    score: list[float] = []
    information: list[float] = []
    for stratum in {u.stratum for u in units}:
        inside = [u for u in units if u.stratum == stratum]
        for when in sorted({u.time for u in inside if u.event}):
            risk = [u for u in inside if u.entry < when <= u.time]
            ending = [u for u in risk if u.event and u.time == when]
            top = max(beta * u.x[0] for u in risk)

            everyone = [moment(risk, beta, top, k) for k in range(3)]
            tied = [moment(ending, beta, top, k) for k in range(3)]
            loglik += [beta * u.x[0] for u in ending]
            score += [u.x[0] for u in ending]
            for k in range(len(ending)):
                share = k / len(ending)
                d, a, c = (e - share * t for e, t in zip(everyone, tied, strict=True))
                loglik.append(-(math.log(d) + top))
                score.append(-a / d)
                information.append(c / d - (a / d) ** 2)
    return math.fsum(loglik), math.fsum(score), math.fsum(information)


@pytest.mark.parametrize("beta", [1.0, -1.0, 0.5])
def test_the_model_is_efron_s_likelihood_however_far_its_linear_predictor_runs(
    beta: float,
) -> None:
    """Linear predictors near 600 and near 0 move the model's shift (``RECENTRE``) at a tied
    time after one of its events has joined its sums, twice units that are one row, and a risk
    set that empties before the shift is needed again; without it ``exp`` overflows. As in R,
    the shift follows the mean linear predictor of the units at risk, so one more than
    ``exp``'s range from that mean overflows there too."""
    far = [Unit(0, 20.0, 30.0, event, (600.0 + k,)) for k, event in enumerate((True, False) * 2)]
    units = [
        Unit(0, 0.0, 10.0, True, (0.0,)),
        Unit(0, 0.0, 10.0, False, (600.0,)),
        Unit(0, 0.0, 8.0, True, (600.0,)),
        Unit(0, 0.0, 8.0, True, (595.0,)),
        Unit(0, 0.0, 8.0, True, (595.0,)),
        Unit(0, 0.0, 8.0, False, (5.0,)),
        Unit(0, 0.0, 6.0, True, (2.0,)),
        Unit(0, 0.0, 6.0, False, (603.0,)),
        Unit(0, 3.0, 5.0, True, (598.0,)),
        Unit(0, 0.0, 4.0, False, (1.0,)),
        *far,
        *far,
        Unit(0, 20.0, 25.0, True, (0.0,)),
    ]
    rows = coxph._collapsed(units, Watch(None))  # pyright: ignore[reportPrivateUsage]
    model = coxph._Rows(rows, [0], Watch(None), recentre=True)  # pyright: ignore[reportPrivateUsage]
    found = model([beta])
    loglik, score, information = efron(units, beta)
    assert close(found.loglik, loglik, 1e-12)
    assert close(found.score[0], score, 1e-9)
    assert close(found.information[0][0], information, 1e-9)


def test_the_shift_counts_every_unit_of_a_row_and_starts_again_when_the_risk_set_empties() -> None:
    """Five units that are one row, at 800, move the mean past ``RECENTRE`` only when all five
    are counted; units at −800 after the risk set has emptied move it back only from nothing."""
    units = [
        Unit(0, 20.0, 30.0, True, (0.0,)),
        *[Unit(0, 20.0, 31.0, False, (800.0,))] * 5,
        Unit(0, 20.0, 29.0, True, (799.0,)),
        Unit(0, 0.0, 10.0, True, (-800.0,)),
        Unit(0, 0.0, 9.0, True, (-801.0,)),
        Unit(0, 0.0, 12.0, False, (-799.0,)),
    ]
    rows = coxph._collapsed(units, Watch(None))  # pyright: ignore[reportPrivateUsage]
    model = coxph._Rows(rows, [0], Watch(None), recentre=True)  # pyright: ignore[reportPrivateUsage]
    found = model([1.0])
    loglik, score, information = efron(units, 1.0)
    assert close(found.loglik, loglik, 1e-12)
    assert close(found.score[0], score, 1e-9)
    assert close(found.information[0][0], information, 1e-9)


def test_a_fit_whose_values_on_the_column_s_own_scale_overflow_has_not_converged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    units = [
        Unit(0, ORIGIN, float(t + 1), t % 3 != 2, (k * 1e-5,))
        for t, k in enumerate((3, 1, 4, 1, 5, 9, 2, 6))
    ]
    reached = coxfit.CoxFit((1.0,), ((1e300,),), True, (-9.0, -8.0), 3)
    monkeypatch.setattr(coxph, "maximise", lambda *_, **__: reached)
    found = coxph.fit(units, Watch(None), counting=False)
    assert found.fit is not None
    assert found.fit.variance[0][0] == math.inf
    assert not found.fit.converged
    assert coxph.proportional_hazards(units, found, Watch(None)) is None

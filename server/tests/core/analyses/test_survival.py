"""``survival.km``: its values, estimability, caveats, endpoint rows, phase 2, readback, ids and
chart (SPEC §5.8, §7.6, §8.1–§8.3, §9.1, §9.3, §9.5; D347–D350), over rows given as they are and
over the shop evaluated by the reference evaluator."""

import hashlib
import json
import math
import random
import sys
import time
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from aibi.core.analyses import coxfit, survival, timetoevent
from aibi.core.analyses.charts import survival_chart
from aibi.core.analyses.existence import CohortAt
from aibi.core.analyses.registry import Analyses
from aibi.core.analyses.stats import z
from aibi.core.engine import build
from aibi.core.engine.evaluate import evaluate
from aibi.core.engine.inputs import Listed
from aibi.core.engine.worker import CallerDeadline
from aibi.core.schema.analyses import SurvivalParams
from aibi.core.schema.caveats import CaveatCode
from aibi.core.schema.ids import MAX_SAFE_INTEGER
from aibi.core.schema.jsonio import canonical
from aibi.core.schema.limits import MAX_CURVE_STEPS
from aibi.core.schema.numbers import NotEstimableReason
from aibi.core.schema.semantics import ExclusionReason

Analyse = Callable[..., list[Any]]
Check = Callable[..., Any]
Shop = Callable[..., Any]
Rows = Callable[..., dict[str, list[dict[str, object]]]]

ORIGIN = timetoevent.ORIGIN
OLD = {"kind": "value", "column": "customers.age", "range": {"gte": 50}}
YOUNG = {"kind": "value", "column": "customers.age", "range": {"lt": 50}}
EVERYONE = {"kind": "value", "column": "customers.age", "range": {"gte": 0}}
NOBODY = {"kind": "value", "column": "customers.age", "range": {"gte": 1000}}
LEFT = {"kind": "value", "column": "customers.left", "values": ["yes"]}
STAYED = {"kind": "value", "column": "customers.left", "values": ["no"]}
NR = NotEstimableReason


def document(
    cohorts: Mapping[str, Sequence[Any]] | None = None, params: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    given = cohorts if cohorts is not None else {"young": [YOUNG], "old": [OLD]}
    return {
        "aibi": "1",
        "dataset": "d",
        "unit": "customers",
        "cohorts": {name: {"all": list(clauses)} for name, clauses in given.items()},
        "views": [
            {"analysis": "survival.km", "cohorts": list(given), "params": dict(params or {})}
        ],
    }


def keyed(row: Mapping[str, object]) -> bytes:
    return json.dumps([row["customer_id"]], ensure_ascii=False).encode("utf-16-be")


def subjects_of(rows: Rows, cohort: Callable[[int], bool], delayed: bool) -> list[Any]:
    """A cohort's retention rows found independently: its customers' tenures, statuses and
    entries, the unassessed left out."""
    found = sorted(
        (row for row in rows(survived=True)["customers"] if cohort(int(str(row["age"])))),
        key=keyed,
    )
    return [
        (row["joined"] if delayed else ORIGIN, row["tenure"], row["left"] == "yes")
        for row in found
        if row["left"] != "?"
    ]


def given_rows(subjects: Sequence[timetoevent.Subject], **excluded: int) -> survival.Rows:
    by_reason = dict.fromkeys(ExclusionReason, 0)
    by_reason.update({ExclusionReason(reason): n for reason, n in excluded.items()})
    return survival.Rows(tuple(subjects), by_reason, sum(excluded.values()), frozenset())


def positions(check: Check, shop: Shop, count: int) -> list[CohortAt]:
    names = {f"c{index}": [EVERYONE] for index in range(count)}
    written = document(names)
    written["views"][0]["overlap"] = "allow"
    [view] = check(written, shop(survived="origin")).views
    return [CohortAt(cohort, evaluate(cohort.resolved)) for cohort in view.cohorts]


Survive = Callable[..., Any]


@pytest.fixture
def survive(check: Check, shop: Shop) -> Survive:
    def run(
        rows: Sequence[survival.Rows],
        *,
        reference: int = 0,
        overlap: bool = False,
        computation: str = "vw:test",
        ends: float | None = None,
        **params: Any,
    ) -> survival.Outcome:
        return survival.survive(
            positions(check, shop, len(rows)),
            rows,
            SurvivalParams.model_validate(params),
            reference=reference,
            overlap=overlap,
            computation=computation,
            ends=ends,
        )

    return run


def codes(found: Any) -> set[str]:
    return {caveat.code for caveat in found.caveats}


def spread(n: int, events: Callable[[int], bool], shift: float = 0.0) -> list[timetoevent.Subject]:
    return [(ORIGIN, float(i + 1) + shift, events(i)) for i in range(n)]


# --- Values ----------------------------------------------------------------------------------


@pytest.mark.parametrize("entered", ["delayed", "origin"])
def test_each_cohort_s_curve_is_the_kaplan_meier_estimate_of_its_members_rows(
    analyse: Analyse, shop: Shop, rows: Rows, entered: str
) -> None:
    [found] = analyse(document(), shop(survived=entered))
    values = found.outcome.values
    for position, cohort in enumerate((lambda age: age < 50, lambda age: age >= 50)):
        subjects = subjects_of(rows, cohort, entered == "delayed")
        steps = timetoevent.kaplan_meier(subjects, 0.95)
        shown = values.positions[position]
        assert [
            (s.time, s.at_risk, s.events, s.censored, s.survival) for s in shown.curve.steps
        ] == [(s.time, s.at_risk, s.events, s.censored, s.survival) for s in steps]
        assert shown.events == sum(event for _, _, event in subjects)
        assert shown.last_follow_up == max(when for _, when, _ in subjects)
        assert found.result.analysed[position].n == len(subjects)
        assert shown.median.estimate == timetoevent.median(steps)[0]


def test_the_log_rank_test_and_the_difference_in_medians_are_those_of_the_members_rows(
    analyse: Analyse, shop: Shop, rows: Rows
) -> None:
    [found] = analyse(document(), shop(survived="delayed"))
    groups = [
        subjects_of(rows, lambda age: age < 50, True),
        subjects_of(rows, lambda age: age >= 50, True),
    ]
    table = timetoevent.risk_table(groups)
    tested = timetoevent.log_rank(table, 2)
    view = found.outcome.values.view
    assert view.test is not None
    assert (view.test.statistic, view.test.p, view.test.df) == (tested.statistic, tested.p, 1)
    difference, ratio = view.effects
    assert (difference.measure, ratio.measure) == ("median_difference", "hazard_ratio")
    assert difference.estimate == (
        timetoevent.median(timetoevent.kaplan_meier(groups[1], 0.95))[0]
        - timetoevent.median(timetoevent.kaplan_meier(groups[0], 0.95))[0]  # type: ignore[operator]
    )


def test_a_view_of_one_cohort_has_no_test_or_effect(analyse: Analyse, shop: Shop) -> None:
    [found] = analyse(document({"old": [OLD]}), shop(survived="origin"))
    view = found.outcome.values.view
    assert (view.test, view.effects) == (None, [])
    [difference] = [c for c in found.result.caveats if c.code == CaveatCode.SMALL_N]
    assert difference.affects == ["/values/positions/0"]


def test_a_grid_gives_each_curve_at_its_times_with_what_happened_since_the_time_before(
    analyse: Analyse, shop: Shop, rows: Rows
) -> None:
    grid = [0, 6, 12.5, 30, 40, 50]
    [found] = analyse(document({"old": [OLD]}, {"grid": grid}), shop(survived="origin"))
    subjects = subjects_of(rows, lambda age: age >= 50, False)
    steps = timetoevent.kaplan_meier(subjects, 0.95)
    curve = found.outcome.values.positions[0].curve
    assert curve.times_from == "grid"
    assert [step.time for step in curve.steps] == grid
    previous = -math.inf
    for step in curve.steps:
        between = [(w, e) for _, w, e in subjects if previous < w <= step.time]
        assert step.events == sum(e for _, e in between)
        assert step.censored == sum(not e for _, e in between)
        assert step.at_risk == sum(w >= step.time for _, w, _ in subjects)
        previous = step.time
        at = timetoevent.survival_at(steps, step.time)
        if at is None:
            assert step.survival is None
            assert step.not_estimable == dict.fromkeys(
                ("/survival", "/ci/low", "/ci/high"), NR.BEYOND_FOLLOW_UP
            )
        else:
            assert (step.survival, step.ci.low, step.ci.high) == at
    assert curve.steps[0].survival == 1.0
    assert curve.steps[-1].survival is None


def test_a_landmark_after_the_last_follow_up_is_beyond_it(analyse: Analyse, shop: Shop) -> None:
    [found] = analyse(
        document({"old": [OLD]}, {"landmarks": [0, 12, 99, 100]}), shop(survived="origin")
    )
    first, _, beyond, further = found.outcome.values.positions[0].landmarks
    assert further.not_estimable == beyond.not_estimable
    assert (first.estimate, first.ci.low, first.ci.high) == (1.0, 1.0, 1.0)
    assert beyond.estimate is None
    assert beyond.not_estimable == dict.fromkeys(
        ("/estimate", "/ci/low", "/ci/high"), NR.BEYOND_FOLLOW_UP
    )


def test_a_cohort_with_no_units_has_no_curve_value_and_no_contrast(
    analyse: Analyse, shop: Shop
) -> None:
    written = document({"old": [OLD], "none": [NOBODY]}, {"landmarks": [3], "grid": [3]})
    [found] = analyse(written, shop(survived="origin"))
    empty = found.outcome.values.positions[1]
    assert empty.last_follow_up is None
    assert empty.not_estimable == {"/last_follow_up": NR.NO_UNITS}
    assert empty.median.not_estimable == dict.fromkeys(
        ("/estimate", "/ci/low", "/ci/high"), NR.NO_UNITS
    )
    assert empty.landmarks[0].not_estimable == dict.fromkeys(
        ("/estimate", "/ci/low", "/ci/high"), NR.NO_UNITS
    )
    [step] = empty.curve.steps
    assert (step.at_risk, step.survival) == (0, None)
    assert step.not_estimable == dict.fromkeys(("/survival", "/ci/low", "/ci/high"), NR.NO_UNITS)
    view = found.outcome.values.view
    assert view.test.positions == [0]
    assert view.test.not_estimable == {"/statistic": NR.NO_UNITS, "/p": NR.NO_UNITS}
    for effect in view.effects:
        assert effect.not_estimable == dict.fromkeys(
            ("/estimate", "/ci/low", "/ci/high"), NR.NO_UNITS
        )
    chart: Any = survival_chart(found.outcome.values, ["old", "none"])
    assert chart["layer"][1]["data"]["values"]


def test_a_cohort_with_no_events_has_no_contrast_but_stays_in_the_log_rank_test(
    analyse: Analyse, shop: Shop
) -> None:
    [found] = analyse(document({"left": [LEFT], "stayed": [STAYED]}), shop(survived="origin"))
    view = found.outcome.values.view
    assert view.test.positions == [0, 1]
    assert view.test.p is not None
    for effect in view.effects:
        assert effect.not_estimable == dict.fromkeys(
            ("/estimate", "/ci/low", "/ci/high"), NR.NO_EVENTS
        )


def test_a_reference_with_no_events_leaves_no_difference_in_medians_and_no_fit(
    survive: Survive,
) -> None:
    quiet = given_rows(spread(12, lambda _: False))
    busy = given_rows(spread(12, lambda _: True))
    found = survive([quiet, busy])
    view = found.values.view
    assert view.test.p is not None
    for effect in view.effects:
        assert effect.not_estimable == dict.fromkeys(
            ("/estimate", "/ci/low", "/ci/high"), NR.NO_EVENTS
        )
    assert view.proportional_hazards.not_estimable == {
        "/statistic": NR.NO_EVENTS,
        "/p": NR.NO_EVENTS,
    }


def test_cohorts_that_share_units_under_overlap_allow_have_no_between_cohort_value(
    survive: Survive,
) -> None:
    rows = given_rows(spread(12, lambda i: i % 2 == 0))
    found = survive([rows, rows], overlap=True)
    view = found.values.view
    overlapping = NR.OVERLAPPING_COHORTS
    assert view.test.not_estimable == {"/statistic": overlapping, "/p": overlapping}
    for effect in view.effects:
        assert effect.not_estimable == dict.fromkeys(
            ("/estimate", "/ci/low", "/ci/high"), overlapping
        )
    assert view.proportional_hazards.not_estimable == {"/statistic": overlapping, "/p": overlapping}
    assert CaveatCode.COHORTS_OVERLAP in codes(found)
    assert found.values.positions[0].median.estimate is not None


def test_a_curve_at_zero_has_no_bounds_from_then_on(survive: Survive) -> None:
    found = survive([given_rows([(ORIGIN, 1.0, True), (ORIGIN, 2.0, True)])], landmarks=[2])
    last = found.values.positions[0].curve.steps[-1]
    assert last.survival == 0.0
    assert (last.ci.low, last.ci.high) == (None, None)
    assert last.not_estimable == {
        "/ci/low": NR.ZERO_DENOMINATOR,
        "/ci/high": NR.ZERO_DENOMINATOR,
    }
    [landmark] = found.values.positions[0].landmarks
    assert landmark.not_estimable == {
        "/ci/low": NR.ZERO_DENOMINATOR,
        "/ci/high": NR.ZERO_DENOMINATOR,
    }


def test_a_median_not_reached_is_not_reached_and_so_is_a_difference_that_uses_it(
    survive: Survive,
) -> None:
    few = given_rows(spread(12, lambda i: i < 2))
    many = given_rows(spread(12, lambda _: True))
    found = survive([many, few])
    median = found.values.positions[1].median
    assert (median.estimate, median.ci.high) == (None, None)
    assert median.not_estimable is not None
    assert median.not_estimable["/estimate"] == median.not_estimable["/ci/high"] == NR.NOT_REACHED
    difference = found.values.view.effects[0]
    assert difference.not_estimable == dict.fromkeys(
        ("/estimate", "/ci/low", "/ci/high"), NR.NOT_REACHED
    )


def test_a_log_rank_test_without_variance_is_zero_variance(survive: Survive) -> None:
    a = given_rows([(ORIGIN, 1.0, False)])
    b = given_rows([(ORIGIN, 2.0, False)])
    test = survive([a, b]).values.view.test
    assert test.not_estimable == {"/statistic": NR.ZERO_VARIANCE, "/p": NR.ZERO_VARIANCE}


@pytest.mark.parametrize("statistic", [math.inf, math.nan, float(2**53)])
def test_a_test_statistic_no_output_holds_is_zero_variance(
    statistic: float, survive: Survive, monkeypatch: pytest.MonkeyPatch
) -> None:
    rng = random.Random(12)
    a = given_rows([(ORIGIN, rng.uniform(0.01, 0.9), rng.random() < 0.8) for _ in range(20)])
    b = given_rows([(ORIGIN, rng.uniform(0.01, 0.5), rng.random() < 0.8) for _ in range(20)])
    view = survive([a, b]).values.view
    assert view.test.statistic is not None
    p = timetoevent.chi_squared_p(statistic, 1)
    monkeypatch.setattr(
        timetoevent, "log_rank", lambda *_: timetoevent.LogRank((0, 1), statistic, 1, p)
    )
    view = survive([a, b]).values.view
    assert view.test.not_estimable == {"/statistic": NR.ZERO_VARIANCE, "/p": NR.ZERO_VARIANCE}


# --- Bootstrap (D350) ------------------------------------------------------------------------


def test_the_seed_of_a_position_is_the_hash_of_its_computation_and_position() -> None:
    expected = hashlib.sha256(canonical({"computation": "vw:x", "position": 2})).digest()
    assert survival.seed("vw:x", 2) == int.from_bytes(expected, "big")
    assert survival.seed("vw:x", 1) != survival.seed("vw:x", 2)


def test_the_median_difference_s_interval_is_the_percentile_interval_of_its_replicates(
    survive: Survive,
) -> None:
    rng = random.Random(5)
    a = [(ORIGIN, float(rng.randint(1, 30)), rng.random() < 0.8) for _ in range(20)]
    b = [(ORIGIN, float(rng.randint(1, 30)), rng.random() < 0.8) for _ in range(20)]
    found = survive([given_rows(a), given_rows(b)], computation="vw:boot")
    mine = timetoevent.bootstrap_medians(b, survival.seed("vw:boot", 1))
    theirs = timetoevent.bootstrap_medians(a, survival.seed("vw:boot", 0))
    low, high = timetoevent.median_interval(mine, theirs, 0.95)
    difference = found.values.view.effects[0]
    assert (difference.ci.low, difference.ci.high) == (low, high)
    other = survive([given_rows(a), given_rows(b)], computation="vw:other")
    assert other.values.view.effects[0].estimate == difference.estimate


def test_the_same_view_gives_the_same_digest_every_time(analyse: Analyse, shop: Shop) -> None:
    first = analyse(document(), shop(survived="delayed"))[0].result
    second = analyse(document(), shop(survived="delayed"))[0].result
    assert first.digest == second.digest


def test_the_analysis_stops_at_the_deadline(survive: Survive) -> None:
    rows = given_rows(spread(12, lambda i: i % 2 == 0))
    with pytest.raises(CallerDeadline):
        survive([rows, rows], ends=time.monotonic() - 1)


@settings(max_examples=25, deadline=None)
@given(st.randoms(use_true_random=False), st.integers(min_value=1, max_value=15))
def test_the_values_do_not_depend_on_the_order_of_a_cohort_s_rows(
    check: Check, shop: Shop, rng: random.Random, n: int
) -> None:
    groups = [
        [
            (
                float(rng.randint(0, 2)) if rng.random() < 0.3 else ORIGIN,
                float(rng.randint(3, 12)),
                rng.random() < 0.6,
            )
            for _ in range(n)
        ]
        for _ in range(2)
    ]
    shuffled = [rng.sample(group, len(group)) for group in groups]
    params = SurvivalParams(landmarks=[5])
    at = positions(check, shop, 2)
    found = [
        survival.survive(
            at,
            [given_rows(group) for group in written],
            params,
            reference=0,
            overlap=False,
            computation="vw:order",
        ).values
        for written in (groups, shuffled)
    ]
    assert found[0] == found[1]


# --- Limits ----------------------------------------------------------------------------------


def test_a_curve_of_more_steps_than_it_may_have_is_refused_but_a_grid_is_not(
    survive: Survive,
) -> None:
    many = given_rows(spread(MAX_CURVE_STEPS + 1, lambda _: True))
    with pytest.raises(survival.TooManySteps):
        survive([many])
    most = given_rows(spread(MAX_CURVE_STEPS, lambda _: True))
    assert len(survive([most]).values.positions[0].curve.steps) == MAX_CURVE_STEPS
    assert len(survive([many], grid=[1, 2]).values.positions[0].curve.steps) == 2


def test_a_time_or_entry_that_no_output_holds_is_refused(survive: Survive) -> None:
    huge = given_rows([(ORIGIN, float(MAX_SAFE_INTEGER) * 4, True)])
    with pytest.raises(survival.TooLarge):
        survive([huge])
    largest = given_rows([(ORIGIN, float(MAX_SAFE_INTEGER), True)])
    assert survive([largest]).values.positions[0].last_follow_up == MAX_SAFE_INTEGER
    early = given_rows([(-float(MAX_SAFE_INTEGER) * 4, 1.0, True)])
    with pytest.raises(survival.TooLarge):
        survive([early], grid=[1])
    earliest = given_rows([(-float(MAX_SAFE_INTEGER), 1.0, True)])
    assert survive([earliest], grid=[1]).values.positions[0].events == 1


# --- Caveats ---------------------------------------------------------------------------------


def test_small_cohorts_and_cohorts_with_few_events_carry_small_n(survive: Survive) -> None:
    small = given_rows(spread(9, lambda _: True))
    large = given_rows(spread(30, lambda i: i % 2 == 0))
    found = survive([large, small])
    small_n = [c for c in found.caveats if c.code == CaveatCode.SMALL_N]
    assert [c.affects for c in small_n] == [["/values/positions/1"]]
    rare = given_rows(spread(30, lambda i: i < 4))
    found = survive([large, rare])
    assert [c.affects for c in found.caveats if c.code == CaveatCode.SMALL_N] == [
        ["/values/positions/1"]
    ]
    enough = survive([large, given_rows(spread(12, lambda i: i < 5))])
    assert [c for c in enough.caveats if c.code == CaveatCode.SMALL_N] == []


def test_members_whose_status_is_not_assessed_carry_unknown_excluded(
    analyse: Analyse, shop: Shop
) -> None:
    [found] = analyse(document(), shop(survived="origin"))
    assert CaveatCode.UNKNOWN_EXCLUDED in {c.code for c in found.result.caveats}
    assert found.result.analysed[0].excluded["NOT_ASSESSED"] == 2


def test_an_undeclared_entry_is_an_unconfirmed_field(analyse: Analyse, shop: Shop) -> None:
    [found] = analyse(document(), shop(survived="undeclared"))
    [unconfirmed] = [c for c in found.result.caveats if c.code == "UNCONFIRMED_SEMANTICS"]
    assert "ep:retention/fields/entry" in json.dumps([m.model_dump() for m in unconfirmed.message])


@pytest.mark.parametrize(
    ("entered", "status"),
    [("undeclared", "available_with_caveats"), ("origin", "available"), ("delayed", "available")],
)
def test_applicability_warns_of_an_endpoint_whose_entry_is_undeclared(
    shop: Shop, entered: str, status: str
) -> None:
    release = shop(survived=entered)
    found = Analyses().applicable(
        list(release.descriptors), dataset=release.dataset, manifest=release.manifest
    )
    [km] = [item for item in found if item.analysis == survival.ANALYSIS_ID]
    assert km.status == status


# --- Endpoint rows (D347) --------------------------------------------------------------------


def flagged(status: str, values: Sequence[Any], event: list[Any], censored: list[Any]) -> Any:
    extras = [
        build.column("customers.state", status),
        build.descriptor(
            "endpoint",
            "ep:state",
            {
                "table": "customers",
                "time_column": "tenure",
                "status_column": "state",
                "event_coding": {"event": event, "censored": censored},
                "entry": "at_origin",
            },
        ),
    ]
    return extras, values


@pytest.mark.parametrize(
    ("datatype", "values", "event", "censored", "expected"),
    [
        ("number", [1, 1.0, 0, 2], [1], [0], [True, True, False, None]),
        ("integer", [1, 0, 3, 1], [1.0], [0], [True, False, None, True]),
        ("boolean", [True, False, True, False], [True], [False], [True, False, True, False]),
        ("boolean", [True, False], [1], [0], [None, None]),
        ("string", ["1", "0", "x", "1"], ["1"], ["0"], [True, False, None, True]),
    ],
)
def test_a_status_is_compared_with_the_coding_as_the_gate_compares_it(
    analyse: Analyse,
    shop: Shop,
    rows: Rows,
    datatype: str,
    values: list[Any],
    event: list[Any],
    censored: list[Any],
    expected: list[bool | None],
) -> None:
    extras, states = flagged(datatype, values, event, censored)
    given = rows(survived=True)
    customers = sorted(given["customers"], key=keyed)
    chosen = customers[: len(states)]
    for row, state in zip(chosen, states, strict=True):
        row["state"] = state
    written = document({"all": [EVERYONE]}, {"endpoint": "ep:state"})
    for row in customers[len(states) :]:
        row["state"] = None
    [found] = analyse(written, shop(given, survived="origin", extras=extras))
    analysed = found.result.analysed[0]
    assert analysed.n == sum(e is not None for e in expected)
    assert analysed.excluded["INVALID_VALUE"] == sum(e is None for e in expected)
    assert found.outcome.values.positions[0].events == sum(e is True for e in expected)


def test_a_time_of_zero_is_valid_and_minus_zero_is_zero(
    analyse: Analyse, shop: Shop, rows: Rows
) -> None:
    given = rows(survived=True)
    first, second = given["customers"][:2]
    first["tenure"], first["left"] = -0.0, "no"
    second["tenure"], second["left"] = 0.0, "yes"
    [found] = analyse(document({"all": [EVERYONE]}), shop(given, survived="origin"))
    assert found.result.analysed[0].excluded["INVALID_VALUE"] == 0
    [step] = found.outcome.values.positions[0].curve.steps[:1]
    assert (step.time, step.events, step.censored) == (0.0, 1, 1)
    assert math.copysign(1.0, step.time) == 1.0


@pytest.mark.parametrize(
    ("tenure", "joined", "entered"),
    [(-1.0, None, "delayed"), (-1.0, None, "origin"), (4.0, 4.0, "delayed"), (4.0, 5.0, "delayed")],
)
def test_a_negative_time_or_an_entry_at_or_after_the_time_is_invalid(
    analyse: Analyse, shop: Shop, rows: Rows, tenure: float, joined: float | None, entered: str
) -> None:
    given = rows(survived=True)
    [first] = [row for row in given["customers"] if row["customer_id"] == "c1"]
    first["tenure"] = tenure
    if joined is not None:
        first["joined"] = joined
    [found] = analyse(document({"all": [EVERYONE]}), shop(given, survived=entered))
    assert found.result.analysed[0].excluded["INVALID_VALUE"] == 1
    [invalid] = [c for c in found.result.caveats if c.code == CaveatCode.INVALID_EXCLUDED]
    assert invalid.affects == ["/analysed"]


def test_a_member_without_an_entry_is_left_out_by_its_reason(
    analyse: Analyse, shop: Shop, rows: Rows
) -> None:
    given = rows(survived=True)
    for row in given["customers"]:
        if row["customer_id"] in ("c1", "c2"):
            row["joined"] = None
    [found] = analyse(document({"all": [EVERYONE]}), shop(given, survived="delayed"))
    analysed = found.result.analysed[0]
    assert analysed.excluded_units == 2 + 2
    assert sum(analysed.excluded.values()) == analysed.excluded_units


# --- Phase 2 ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("params", "expected"),
    [
        ({"landmarks": [3, 2]}, ("INVALID_VALUE", "/views/0/params/landmarks")),
        ({"landmarks": [3, 3]}, ("INVALID_VALUE", "/views/0/params/landmarks")),
        ({"landmarks": [-1]}, ("INVALID_VALUE", "/views/0/params/landmarks/0")),
        ({"grid": []}, ("INVALID_VALUE", "/views/0/params/grid")),
        ({"landmarks": list(range(17))}, ("LIMIT_EXCEEDED", "/views/0/params/landmarks")),
        ({"grid": list(range(101))}, ("LIMIT_EXCEEDED", "/views/0/params/grid")),
        ({"level": 1}, ("INVALID_VALUE", "/views/0/params/level")),
        ({"endpoint": "ep:none"}, ("UNKNOWN_DESCRIPTOR", "/views/0/params/endpoint")),
        ({"endpoint": "ep:retention", "bins": 3}, ("UNKNOWN_MEMBER", "/views/0/params/bins")),
    ],
)
def test_parameters_it_does_not_take_are_refused_where_they_are_written(
    check: Check, shop: Shop, params: dict[str, Any], expected: tuple[str, str]
) -> None:
    found = check(document({"old": [OLD]}, params), shop(survived="delayed"))
    assert [(r.code, r.path) for r in found.refusals] == [expected]


def test_a_release_without_a_usable_endpoint_on_the_unit_table_is_refused(
    check: Check, shop: Shop
) -> None:
    found = check(document({"old": [OLD]}), shop())
    [refusal] = found.refusals
    assert (refusal.code, refusal.path) == ("MISSING_MEMBER", "/views/0/params/endpoint")


def test_an_endpoint_not_on_the_view_s_unit_table_is_refused(check: Check, shop: Shop) -> None:
    written = document({"web": []}, {"endpoint": "ep:retention"})
    written["unit"] = "orders"
    found = check(written, shop(survived="origin"))
    assert [(r.code, r.path) for r in found.refusals] == [
        ("INVALID_VALUE", "/views/0/params/endpoint")
    ]


def test_the_canonical_parameters_hold_the_endpoint_s_form_and_every_default(
    check: Check, shop: Shop
) -> None:
    [view] = check(document({"old": [OLD]}), shop(survived="delayed")).views
    assert view.identity.params == {
        "endpoint": {"id": "ep:retention", "time": "customers.tenure"},
        "grid": None,
        "landmarks": [],
        "level": 0.95,
    }
    [named] = check(
        document({"old": [OLD]}, {"endpoint": "ep:retention", "landmarks": [], "level": 0.95}),
        shop(survived="delayed"),
    ).views
    assert named.identity.id == view.identity.id


def test_times_written_as_integers_or_decimals_give_one_id(check: Check, shop: Shop) -> None:
    [whole] = check(document({"old": [OLD]}, {"grid": [1, 12]}), shop(survived="origin")).views
    [decimal] = check(
        document({"old": [OLD]}, {"grid": [1.0, 12.0]}), shop(survived="origin")
    ).views
    [other] = check(document({"old": [OLD]}, {"grid": [1, 13]}), shop(survived="origin")).views
    assert whole.identity.id == decimal.identity.id != other.identity.id


def test_the_endpoint_s_time_column_enters_the_view_s_id(check: Check, shop: Shop) -> None:
    joined = build.descriptor(
        "endpoint",
        "ep:joined",
        {
            "table": "customers",
            "time_column": "joined",
            "status_column": "left",
            "event_coding": {"event": ["yes"], "censored": ["no"]},
        },
    )
    release = shop(survived="origin", extras=[joined])
    [first] = check(document({"old": [OLD]}, {"endpoint": "ep:retention"}), release).views
    [second] = check(document({"old": [OLD]}, {"endpoint": "ep:joined"}), release).views
    assert second.identity.params["endpoint"] == {"id": "ep:joined", "time": "customers.joined"}
    assert first.identity.id != second.identity.id


# --- Readback and chart ----------------------------------------------------------------------


def said(parts: Sequence[Any]) -> str:
    return "".join(part.model_dump().get("text") or part.model_dump()["data"] for part in parts)


@pytest.mark.parametrize(
    ("entered", "expected"),
    [
        ("delayed", "each unit entering at customers.joined"),
        ("origin", "every unit entering at the origin"),
        ("undeclared", "every unit entering at the origin"),
    ],
)
def test_the_readback_names_the_endpoint_its_columns_and_how_units_enter(
    check: Check, shop: Shop, entered: str, expected: str
) -> None:
    [view] = check(document(params={"grid": [6, 12]}), shop(survived=entered)).views
    shown = said(view.readback())
    for part in (
        "ep:retention",
        "customers.tenure in mo",
        '"yes" an event, "no" censored',
        expected,
        "at the times 6, 12",
        "the reference being the cohort at position 0",
        "2000 replicates",
        "the hazard ratio (Cox fit, Efron ties, Wald interval, tested for proportional hazards)",
        "intervals at 0.95",
    ):
        assert part in shown


def test_the_chart_steps_each_curve_from_one_at_time_zero_and_shows_only_values_it_has(
    survive: Survive,
) -> None:
    found = survive(
        [given_rows([(ORIGIN, 1.0, True), (ORIGIN, 2.0, True)]), given_rows([])],
    )
    chart: Any = survival_chart(found.values, ["a", "b"])
    band, line = (layer["data"]["values"] for layer in chart["layer"])
    assert [(row["time"], row["survival"]) for row in line] == [(0, 1), (1.0, 0.5), (2.0, 0.0)]
    assert (band[0]["time"], band[0]["low"], band[0]["high"]) == (0, 1, 1)
    assert len(band) == 2
    assert {row["cohort"] for row in line} == {"a"}


def test_a_reference_whose_median_is_not_reached_leaves_differences_not_reached(
    survive: Survive,
) -> None:
    few = given_rows(spread(12, lambda i: i < 2))
    many = given_rows(spread(12, lambda _: True))
    difference = survive([few, many]).values.view.effects[0]
    assert difference.not_estimable == dict.fromkeys(
        ("/estimate", "/ci/low", "/ci/high"), NR.NOT_REACHED
    )


def test_a_cohort_of_ten_units_and_five_events_is_not_small(survive: Survive) -> None:
    exact = given_rows(spread(10, lambda i: i < 5))
    found = survive([exact])
    assert CaveatCode.SMALL_N not in codes(found)


def test_an_invalid_row_in_one_cohort_raises_invalid_excluded(survive: Survive) -> None:
    clean = given_rows(spread(12, lambda i: i % 2 == 0))
    flawed = given_rows(spread(12, lambda i: i % 2 == 0), INVALID_VALUE=1)
    assert CaveatCode.INVALID_EXCLUDED in codes(survive([clean, flawed]))
    assert CaveatCode.UNKNOWN_EXCLUDED not in codes(survive([clean, flawed]))
    assert CaveatCode.INVALID_EXCLUDED not in codes(survive([clean, clean]))


def test_the_readback_of_one_cohort_names_no_contrast(check: Check, shop: Shop) -> None:
    [view] = check(document({"old": [OLD]}), shop(survived="origin")).views
    shown = said(view.readback())
    assert "log-rank" not in shown
    assert "hazard ratio" not in shown


# --- The hazard ratio and the test of proportional hazards (D355–D357) ------------------------


def hazard_ratios(view: Any) -> list[Any]:
    return [effect for effect in view.effects if effect.measure == "hazard_ratio"]


def test_the_hazard_ratio_is_the_fit_s_with_its_wald_interval_and_its_test_cox_zph_s(
    survive: Survive,
) -> None:
    rng = random.Random(5)
    groups = [
        [(ORIGIN, float(rng.randint(1, 30)), rng.random() < 0.7) for _ in range(25)]
        for _ in range(3)
    ]
    view = survive([given_rows(group) for group in groups]).values.view
    assert [effect.measure for effect in view.effects] == ["median_difference", "hazard_ratio"] * 2
    [table] = timetoevent.risk_table(groups)
    fit = coxfit.fit(table, [0, 1, 2], 0, timetoevent.Watch(None), counting=False)
    for term, ratio in enumerate(hazard_ratios(view)):
        beta = fit.coefficients[term]
        spread = z(0.95) * math.sqrt(fit.variance[term][term])
        assert (ratio.position, ratio.versus, ratio.not_estimable) == (term + 1, 0, None)
        assert ratio.estimate == math.exp(beta)
        assert (ratio.ci.method, ratio.ci.level) == ("wald", 0.95)
        assert (ratio.ci.low, ratio.ci.high) == (math.exp(beta - spread), math.exp(beta + spread))
    test = coxfit.proportional_hazards(table, [0, 1, 2], [25] * 3, 0, fit, timetoevent.Watch(None))
    assert test is not None
    shown = view.proportional_hazards
    assert (shown.method, shown.positions, shown.df) == ("grambsch_therneau", [0, 1, 2], 2)
    assert (shown.statistic, shown.p) == (test.statistic, test.p)


def test_a_cohort_the_risk_sets_separate_or_leave_apart_has_no_hazard_ratio_and_says_why(
    survive: Survive,
) -> None:
    reference = [(0.0, 3.0, True), (0.0, 4.0, True), (0.0, 5.5, True), (0.0, 6.0, False)]
    tied = [(0.0, 3.0, True), (0.0, 5.0, True), (0.0, 5.2, False)]
    ahead = [(0.0, 1.0, True), (0.0, 2.0, True)]
    behind = [(0.0, 7.0, True), (0.0, 8.0, False)]
    apart = [(10.0, 11.0, True), (10.0, 12.0, True)]
    quiet = [(0.0, 9.0, False)]
    cohorts = [reference, tied, ahead, behind, apart, quiet]
    view = survive([given_rows(cohort) for cohort in cohorts]).values.view
    ratios = hazard_ratios(view)
    reasons = [None, NR.SEPARATION, NR.SEPARATION, NR.ZERO_VARIANCE, NR.NO_EVENTS]
    for ratio, reason in zip(ratios, reasons, strict=True):
        if reason is None:
            assert ratio.not_estimable is None
        else:
            assert ratio.not_estimable == dict.fromkeys(
                ("/estimate", "/ci/low", "/ci/high"), reason
            )
            assert (ratio.estimate, ratio.ci.low, ratio.ci.high) == (None, None, None)
    [table] = timetoevent.risk_table([reference, tied])
    alone = coxfit.fit(table, [0, 1], 0, timetoevent.Watch(None), counting=True)
    assert ratios[0].estimate == math.exp(alone.coefficients[0])
    assert view.proportional_hazards.positions == [0, 1, 2, 3, 4]
    assert view.proportional_hazards.not_estimable == {
        "/statistic": NR.SEPARATION,
        "/p": NR.SEPARATION,
    }
    only_apart = survive([given_rows(cohort) for cohort in (reference, tied, apart)]).values.view
    assert only_apart.proportional_hazards.not_estimable == {
        "/statistic": NR.ZERO_VARIANCE,
        "/p": NR.ZERO_VARIANCE,
    }


HALVING = [
    [
        (0.0, float(t), bool(e))
        for t, e in zip(
            (6, 13, 17, 15, 15, 19, 23, 11, 16, 9, 14),
            (1, 1, 1, 1, 1, 1, 0, 1, 1, 1, 1),
            strict=True,
        )
    ],
    [(0.0, float(t), bool(e)) for t, e in zip((1, 3, 6, 5, 2), (0, 1, 1, 1, 0), strict=True)],
]
"""``cox.R``'s fit that halves its step twice, with entries."""


def test_a_fit_that_reaches_no_maximum_is_not_converged(
    survive: Survive, monkeypatch: pytest.MonkeyPatch
) -> None:
    view = survive([given_rows(group) for group in HALVING]).values.view
    [ratio] = hazard_ratios(view)
    assert ratio.estimate is not None
    monkeypatch.setattr(coxfit, "ITERATIONS", 2)
    [ratio] = hazard_ratios(survive([given_rows(group) for group in HALVING]).values.view)
    assert ratio.estimate is not None
    monkeypatch.setattr(coxfit, "ASCENT", 0)
    view = survive([given_rows(group) for group in HALVING]).values.view
    [ratio] = hazard_ratios(view)
    unconverged = NR.NOT_CONVERGED
    assert ratio.not_estimable == dict.fromkeys(("/estimate", "/ci/low", "/ci/high"), unconverged)
    assert view.proportional_hazards.not_estimable == {"/statistic": unconverged, "/p": unconverged}


FLAT = [
    [(ORIGIN, float(t), True) for t in range(1, 21)],
    *(
        [(ORIGIN, float(21 + i % 40), True) for i in range(200)] + [(ORIGIN, 1.0, True)]
        for _ in range(2)
    ),
]
"""``cox.R``'s first step into a flat region: R's iteration from 0 stops where a pivot vanishes."""


@pytest.mark.parametrize("entered", [False, True])
def test_a_fit_whose_newton_step_lands_where_the_information_vanishes_still_reaches_the_maximum(
    survive: Survive, entered: bool
) -> None:
    cohorts = [[(0.0 if entered else e, t, s) for e, t, s in group] for group in FLAT]
    ratios = hazard_ratios(survive([given_rows(group) for group in cohorts]).values.view)
    for ratio in ratios:
        assert ratio.not_estimable is None
        assert ratio.estimate == pytest.approx(math.exp(-6.384931), rel=1e-6)
        assert ratio.ci.low < ratio.estimate < ratio.ci.high


@settings(
    max_examples=150, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)
@given(
    st.lists(
        st.lists(st.tuples(st.integers(1, 12), st.booleans()), min_size=1, max_size=15),
        min_size=2,
        max_size=2,
    )
)
def test_swapping_the_reference_inverts_the_hazard_ratio(
    survive: Survive, groups: list[list[tuple[int, bool]]]
) -> None:
    rows = [given_rows([(ORIGIN, float(t), e) for t, e in group]) for group in groups]
    [ratio] = hazard_ratios(survive(rows).values.view)
    [swapped] = hazard_ratios(survive(rows, reference=1).values.view)
    assert ratio.not_estimable == swapped.not_estimable
    if ratio.estimate is not None:
        assert swapped.estimate is not None
        assert ratio.estimate * swapped.estimate == pytest.approx(1.0, rel=1e-8)
    for low, high in ((ratio.ci.low, swapped.ci.high), (ratio.ci.high, swapped.ci.low)):
        assert (low is None) == (high is None)
        if low is not None and high is not None:
            assert low * high == pytest.approx(1.0, rel=1e-8)


@pytest.mark.parametrize(
    ("coefficient", "error", "missing"),
    [
        (40.0, 0.1, ("/estimate", "/ci/low", "/ci/high")),
        (-800.0, 0.1, ("/estimate", "/ci/low", "/ci/high")),
        (36.0, 0.5, ("/ci/high",)),
        (-744.0, 1.0, ("/ci/low",)),
        (0.5, 1e200, ("/ci/low", "/ci/high")),
    ],
)
def test_a_hazard_ratio_or_bound_no_output_holds_is_separation_alone(
    survive: Survive,
    monkeypatch: pytest.MonkeyPatch,
    coefficient: float,
    error: float,
    missing: tuple[str, ...],
) -> None:
    fitted = coxfit.CoxFit((coefficient,), ((error * error,),), True, (-1.0, -1.0), 1)
    monkeypatch.setattr(coxfit, "fit", lambda *_, **__: fitted)
    [ratio] = hazard_ratios(survive([given_rows(group) for group in HALVING]).values.view)
    assert ratio.not_estimable == dict.fromkeys(missing, NR.SEPARATION)
    shown = {"/estimate": ratio.estimate, "/ci/low": ratio.ci.low, "/ci/high": ratio.ci.high}
    for member, value in shown.items():
        assert (value is None) == (member in missing)
        assert value is None or 0 < value <= MAX_SAFE_INTEGER


@pytest.mark.parametrize("variance", [0.0, -1e-300, math.nan])
def test_a_hazard_ratio_whose_variance_is_not_positive_has_no_interval_of_width_zero(
    survive: Survive, monkeypatch: pytest.MonkeyPatch, variance: float
) -> None:
    fitted = coxfit.CoxFit((0.5,), ((variance,),), True, (-1.0, -1.0), 1)
    monkeypatch.setattr(coxfit, "fit", lambda *_, **__: fitted)
    [ratio] = hazard_ratios(survive([given_rows(group) for group in HALVING]).values.view)
    assert ratio.not_estimable == dict.fromkeys(
        ("/estimate", "/ci/low", "/ci/high"), NR.ZERO_VARIANCE
    )


@pytest.mark.parametrize("statistic", [1e16, math.inf, math.nan])
def test_a_test_of_proportional_hazards_no_output_holds_is_zero_variance(
    survive: Survive, monkeypatch: pytest.MonkeyPatch, statistic: float
) -> None:
    tested = coxfit.Test(statistic, 1, timetoevent.chi_squared_p(statistic, 1))
    monkeypatch.setattr(coxfit, "proportional_hazards", lambda *_, **__: tested)
    view = survive([given_rows(group) for group in HALVING]).values.view
    assert view.proportional_hazards.not_estimable == {
        "/statistic": NR.ZERO_VARIANCE,
        "/p": NR.ZERO_VARIANCE,
    }


def test_a_test_of_proportional_hazards_over_a_singular_information_is_zero_variance(
    survive: Survive,
) -> None:
    together = [(ORIGIN, 1.0, True), (ORIGIN, 1.0, True), (ORIGIN, 2.0, False)]
    view = survive([given_rows(together), given_rows(together[1:])]).values.view
    [ratio] = hazard_ratios(view)
    assert ratio.estimate is not None
    assert view.proportional_hazards.not_estimable == {
        "/statistic": NR.ZERO_VARIANCE,
        "/p": NR.ZERO_VARIANCE,
    }


def test_a_reference_with_no_units_leaves_no_hazard_ratio_and_no_test(survive: Survive) -> None:
    busy = given_rows(spread(12, lambda _: True))
    view = survive([given_rows([]), busy]).values.view
    [ratio] = hazard_ratios(view)
    assert ratio.not_estimable == dict.fromkeys(("/estimate", "/ci/low", "/ci/high"), NR.NO_UNITS)
    assert view.proportional_hazards.not_estimable == {"/statistic": NR.NO_UNITS, "/p": NR.NO_UNITS}


def test_a_fit_with_few_events_per_term_and_a_failed_test_carry_small_n_and_ph_violated(
    survive: Survive,
) -> None:
    early = [(ORIGIN, float(t), t <= 20) for t in range(1, 41)]
    late = [(ORIGIN, float(t) + 0.5, t > 20) for t in range(1, 41)]
    found = survive([given_rows(early), given_rows(late)])
    test = found.values.view.proportional_hazards
    assert test.p is not None
    assert test.p < survival.PH_LEVEL
    [violated] = [c for c in found.caveats if c.code == CaveatCode.PH_VIOLATED]
    assert violated.affects == ["/values/view/effects", "/values/view/proportional_hazards"]
    assert all(
        c.affects != ["/values/view/effects"] for c in found.caveats if c.code == CaveatCode.SMALL_N
    )
    few = [given_rows(spread(12, lambda i: i < 4, shift)) for shift in (0.0, 0.5)]
    found = survive(few)
    small = [c.affects for c in found.caveats if c.code == CaveatCode.SMALL_N]
    assert ["/values/view/effects"] in small
    enough = [given_rows(spread(12, lambda i: i < 5, shift)) for shift in (0.0, 0.5)]
    small = [c.affects for c in survive(enough).caveats if c.code == CaveatCode.SMALL_N]
    assert ["/values/view/effects"] not in small


# --- Arithmetic and range edges (D348) --------------------------------------------------------

EDGE_TIMES = [
    0.0,
    5e-324,
    1e-300,
    1e-9,
    0.5,
    1.0,
    2.0,
    3.0,
    1e6,
    float(MAX_SAFE_INTEGER - 1),
    float(MAX_SAFE_INTEGER),
    float(MAX_SAFE_INTEGER + 1),
    float(MAX_SAFE_INTEGER + 3),
    1e300,
]
EDGE_ENTRIES = [ORIGIN, -1e300, -float(MAX_SAFE_INTEGER + 1), -float(MAX_SAFE_INTEGER), -1.0, 0.0]
SHOWN_TIMES = [time for time in EDGE_TIMES if time <= MAX_SAFE_INTEGER]


@st.composite
def edge_cohorts(draw: st.DrawFn) -> list[list[timetoevent.Subject]]:
    cohorts: list[list[timetoevent.Subject]] = []
    for _ in range(draw(st.integers(min_value=1, max_value=3))):
        found: list[timetoevent.Subject] = []
        for _ in range(draw(st.integers(min_value=0, max_value=12))):
            when = draw(st.sampled_from(EDGE_TIMES))
            entry = draw(st.sampled_from([e for e in EDGE_ENTRIES if e < when]))
            found.append((entry, when, draw(st.booleans())))
        cohorts.append(found)
    return cohorts


@st.composite
def edge_params(draw: st.DrawFn) -> dict[str, Any]:
    params: dict[str, Any] = {"level": draw(st.sampled_from([0.5, 0.95, 1 - 1e-9]))}
    for member in ("grid", "landmarks"):
        chosen = draw(st.sets(st.sampled_from(SHOWN_TIMES), max_size=4))
        if chosen and draw(st.booleans()):
            params[member] = sorted(chosen)
    return params


@settings(max_examples=150, deadline=None)
@given(cohorts=edge_cohorts(), params=edge_params(), overlap=st.booleans())
def test_extreme_times_give_a_result_or_a_coded_refusal_and_never_an_internal_error(
    check: Check,
    shop: Shop,
    cohorts: list[list[timetoevent.Subject]],
    params: dict[str, Any],
    overlap: bool,
) -> None:
    rows = [given_rows(cohort) for cohort in cohorts]
    beyond = any(
        when > MAX_SAFE_INTEGER or MAX_SAFE_INTEGER < abs(entry) < math.inf
        for cohort in cohorts
        for entry, when, _ in cohort
    )
    try:
        found = survival.survive(
            positions(check, shop, len(rows)),
            rows,
            SurvivalParams.model_validate(params),
            reference=len(rows) - 1,
            overlap=overlap,
            computation="vw:edges",
        )
    except survival.TooLarge:
        assert beyond
        return
    assert not beyond
    assert len(found.values.positions) == len(rows)
    json.dumps(found.values.model_dump(mode="json"), allow_nan=False)
    json.dumps(survival_chart(found.values, [str(i) for i in range(len(rows))]), allow_nan=False)


@pytest.mark.parametrize(
    "cohorts",
    [
        [[(ORIGIN, 1.0, True)] * 20, [(ORIGIN, 1.0, True)] * 20],
        [[(ORIGIN, 1.0, True)], [(ORIGIN, 2.0, False)]],
        [spread(2000, lambda _: True), [(ORIGIN, 1.0, True)] * 3],
        [spread(12, lambda _: True, shift=100.0), spread(12, lambda _: True)],
        [[(ORIGIN, 5e-324, True), (ORIGIN, 1e-300, True)], [(-1.0, 1e-9, True)]],
        [
            [(ORIGIN, float(MAX_SAFE_INTEGER - 1), True), (ORIGIN, float(MAX_SAFE_INTEGER), True)],
            [(ORIGIN, 0.0, True)],
        ],
    ],
    ids=["all ties", "a single event", "2000 steps", "apart", "tiny", "largest"],
)
def test_edge_cohorts_give_a_result(
    survive: Survive, cohorts: list[list[timetoevent.Subject]]
) -> None:
    found = survive([given_rows(cohort) for cohort in cohorts], landmarks=[1])
    json.dumps(found.values.model_dump(mode="json"), allow_nan=False)


@pytest.mark.parametrize(
    ("time_at", "entry"),
    [(math.nan, 0.0), (1.0, math.nan), (-1.0, None), (1.0, 1.0), (10**400, None)],
    ids=["time not a number", "entry not a number", "negative", "entry at the time", "huge"],
)
def test_a_time_or_entry_that_is_not_a_number_or_out_of_order_is_invalid(
    check: Check, shop: Shop, time_at: float, entry: float | None
) -> None:
    [view] = check(document({"all": [EVERYONE]}), shop(survived="delayed")).views
    [endpoint] = view.endpoints
    [event] = endpoint.event
    values: tuple[tuple[Any, ...], ...] = ((time_at, 2.0), (event, event), (entry, 0.0))
    none = (frozenset[ExclusionReason](), frozenset[ExclusionReason]())
    given = Listed([("a",), ("b",)], [0, 1], values, (none, none, none), (frozenset(),) * 3)
    cells = survival.endpoint_cells(endpoint, given)
    if time_at == 10**400:
        assert cells.subjects[0] == (timetoevent.ORIGIN, math.inf, True)
        return
    assert cells.subjects == (None, (0.0, 2.0, True))
    assert cells.reasons[0] == frozenset({ExclusionReason.INVALID_VALUE})


def test_endpoint_rows_stop_at_the_call_s_deadline(
    check: Check, shop: Shop, monkeypatch: pytest.MonkeyPatch
) -> None:
    [view] = check(document({"all": [EVERYONE]}), shop(survived="origin")).views
    [endpoint] = view.endpoints
    [event] = endpoint.event
    count = 5
    values: tuple[tuple[Any, ...], ...] = (tuple([1.0] * count), tuple([event] * count))
    none = tuple(frozenset[ExclusionReason]() for _ in range(count))
    given = Listed(
        [(str(i),) for i in range(count)],
        list(range(count)),
        values,
        (none, none),
        (frozenset(), frozenset()),
    )
    calls: list[int] = []
    passing = [3]

    def clock() -> float:
        calls.append(1)
        return 2.0 if len(calls) > passing[0] else 0.0

    monkeypatch.setattr(timetoevent, "LOOK_EVERY", 1)
    monkeypatch.setattr(timetoevent, "time", type("Clock", (), {"monotonic": staticmethod(clock)}))
    with pytest.raises(CallerDeadline):
        survival.endpoint_cells(endpoint, given, ends=1.0)
    assert len(calls) == 4
    calls.clear()
    passing[0] = count + 1
    with pytest.raises(CallerDeadline):
        survival.endpoint_rows(endpoint, given, ends=1.0)
    assert len(calls) == count + 2
    assert survival.endpoint_rows(endpoint, given).subjects == ((ORIGIN, 1.0, True),) * count


# --- The deadline (D350) ----------------------------------------------------------------------


def _census(
    check: Check,
    shop: Shop,
    monkeypatch: pytest.MonkeyPatch,
    passing: int | None,
    delayed: bool = True,
) -> tuple[Counter[str], int]:
    """Each function's sites that look at the deadline in one view with every part (delayed
    entry or none, a grid, landmarks, three cohorts, every contrast), each loop looking every
    time, and how many looks it made; with ``passing``, the deadline passes at that look."""
    sites: set[tuple[str, int]] = set()
    looks: list[int] = []

    def clock() -> float:
        frame = sys._getframe(1)  # pyright: ignore[reportPrivateUsage]
        while frame.f_code.co_qualname in ("Watch._read", "Watch.look", "Watch.spend", "_deadline"):
            assert frame.f_back is not None
            frame = frame.f_back
        caller = frame.f_back.f_code.co_qualname if frame.f_back is not None else ""
        sites.add((f"{caller} > {frame.f_code.co_qualname}", frame.f_lineno))
        looks.append(1)
        return 2.0 if passing is not None and len(looks) > passing else 0.0

    moment = type("Clock", (), {"monotonic": staticmethod(clock)})
    monkeypatch.setattr(timetoevent, "time", moment)
    monkeypatch.setattr(timetoevent, "LOOK_EVERY", 1)
    monkeypatch.setattr(timetoevent, "REPLICATES", 2)
    rng = random.Random(3)
    rows = [
        given_rows(
            [
                (float(i % 3) - 1 if delayed else ORIGIN, i + 1.5 + shift, rng.random() < 0.7)
                for i in range(12)
            ]
        )
        for shift in (0.0, 0.25, 0.5)
    ]
    try:
        survival.survive(
            positions(check, shop, 3),
            rows,
            SurvivalParams(grid=[2, 6], landmarks=[3, 20]),
            reference=0,
            overlap=False,
            computation="vw:census",
            ends=1.0,
        )
    except CallerDeadline:
        assert passing is not None
        return Counter(name for name, _ in sites), len(looks)
    assert passing is None
    return Counter(name for name, _ in sites), len(looks)


@pytest.mark.parametrize("delayed", [True, False])
def test_every_pass_of_the_analysis_looks_at_the_deadline(
    check: Check, shop: Shop, monkeypatch: pytest.MonkeyPatch, delayed: bool
) -> None:
    found, _ = _census(check, shop, monkeypatch, None, delayed)
    assert found == {
        "_census > survive": 4,
        "survive > _within": 1,
        "survive > kaplan_meier": 3,
        "kaplan_meier > _counts": 1,
        "survive > _position": 4,
        "_grid_steps > grid_counts": 3,
        "grid_counts > _counts": 1,
        "_median > median": 5,
        "median > findq": 4,
        "findq > _right_constant": 2,
        "_log_rank > risk_table": 6,
        "risk_table > _counts": 1,
        "_log_rank > log_rank": 2,
        "cohort_fit > risk_table": 6,
        "cohort_fit > standing": 2,
        f"{'_counting' if delayed else '_right'} > _Model.__call__": 1,
        "_Model.__call__ > _evaluate": 2,
        "cohort_test > proportional_hazards": 2,
        "proportional_hazards > _evaluate": 2,
        "_Replicates.__call__ > bootstrap_medians": 1,
        "bootstrap_medians > _Resampled.__init__": 4 if delayed else 3,
    }


@pytest.mark.parametrize("delayed", [True, False])
def test_the_deadline_passed_at_any_look_stops_the_analysis_there(
    check: Check, shop: Shop, monkeypatch: pytest.MonkeyPatch, delayed: bool
) -> None:
    _, total = _census(check, shop, monkeypatch, None, delayed)
    for passing in range(0, total, max(1, total // 97)):
        with monkeypatch.context() as patch:
            _, looked = _census(check, shop, patch, passing, delayed)
        assert looked == passing + 1

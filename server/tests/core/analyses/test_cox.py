"""``survival.cox``'s model of cohorts and covariates (SPEC §9.5; D362–D365), held to R's
``coxph`` (``reference/survcox.R``) and to ``survival.km``'s cohort fit."""

import dataclasses
import json
import math
import random
import time
from pathlib import Path
from typing import Any

import pytest

from aibi.core.analyses import cox, coxph, survival
from aibi.core.analyses.cox import Covariate, Member
from aibi.core.analyses.timetoevent import ORIGIN
from aibi.core.engine.worker import CallerDeadline
from aibi.core.schema.semantics import ExclusionReason

REFERENCE = json.loads(
    (Path(__file__).parent / "reference" / "survcox.json").read_text(encoding="utf-8")
)
CASES = {case["name"]: case for case in REFERENCE["cases"]}
MISSING = frozenset({ExclusionReason.NO_INFORMATION})


def close(found: float | None, expected: float, tolerance: float = 1e-9) -> bool:
    return found is not None and abs(found - expected) <= tolerance * max(1.0, abs(expected))


def positions_of(case: dict[str, Any]) -> tuple[list[list[Member]], list[Covariate]]:
    kinds = case["kinds"]
    covariates = [Covariate(kind) for kind in (kinds[:-1] if case["stratum"] else kinds)]
    count = max(case["position"]) + 1
    positions: list[list[Member]] = [[] for _ in range(int(count))]
    for i, position in enumerate(case["position"]):
        entry = ORIGIN if case["entry"] is None else case["entry"][i]
        values: list[Any] = []
        for kind, column in zip(kinds, case["covariates"], strict=True):
            value = column[i]
            if value is not None and kind == "boolean":
                value = bool(value)
            values.append(value)
        reasons = tuple(MISSING if value is None else frozenset() for value in values)
        positions[int(position)].append(
            Member(
                (entry, case["time"][i], bool(case["status"][i])),
                frozenset(),
                tuple(values),
                reasons,
            )
        )
    return positions, covariates


def test_the_reference_was_written_by_the_r_and_survival_versions_it_names() -> None:
    assert REFERENCE["r"].startswith("R version 4.3.3")
    assert REFERENCE["survival"] == "3.5.8"


@pytest.mark.parametrize("name", sorted(CASES))
def test_a_model_s_terms_intervals_and_tests_are_coxph_s(name: str) -> None:
    case = CASES[name]
    positions, covariates = positions_of(case)
    found = cox.model(
        positions, covariates, stratified=case["stratum"], reference=0, overlap=False, level=0.95
    )
    view = found.values.view
    assert sum(one.n or 0 for one in found.analysed) == case["n"]
    assert sum(position.events for position in found.values.positions) == case["nevent"]
    assert [term.estimate is not None for term in view.terms] == [True] * len(case["hr"])
    for term, hr, low, high, p in zip(
        view.terms, case["hr"], case["low"], case["high"], case["p"], strict=True
    ):
        assert close(term.estimate, hr)
        assert close(term.ci.low, low)
        assert close(term.ci.high, high)
        assert close(term.p, p, 1e-8)
    tests = [test for test in view.covariate_tests if test.p is not None]
    assert len(tests) == len(case["wald"])
    for test, wald in zip(tests, case["wald"], strict=True):
        assert test.df == wald["df"]
        assert close(test.statistic, wald["statistic"])
        assert close(test.p, wald["p"], 1e-8)
    zph = case["zph"]
    assert view.proportional_hazards.df == zph["df"]
    assert view.proportional_hazards.terms == list(range(len(view.terms)))
    assert close(view.proportional_hazards.statistic, zph["statistic"], 1e-8)
    assert close(view.proportional_hazards.p, zph["p"], 1e-8)


def cohort_members(
    seed: int, groups: int, n: int, *, entries: bool = False, eventless: int | None = None
) -> list[list[Member]]:
    rng = random.Random(seed)
    positions: list[list[Member]] = [[] for _ in range(groups)]
    for i in range(n):
        g = i % groups
        time = round(rng.expovariate(1.0 + 0.3 * g) * 10, 2) + 0.01
        entry = round(rng.random() * time * 0.5, 2) if entries else ORIGIN
        event = rng.random() < 0.8 and g != eventless
        positions[g].append(Member((entry, time, event), frozenset(), (), ()))
    return positions


def km_effects(positions: list[list[Member]]) -> tuple[list[Any], Any]:
    rows = [
        survival.Rows(
            tuple(m.endpoint for m in members if m.endpoint is not None),
            dict.fromkeys(ExclusionReason, 0),
            0,
            frozenset(),
        )
        for members in positions
    ]
    fitted = survival.cohort_fit(rows, 0, None)
    effects = [
        survival.hazard_ratio(rows, fitted, g, 0, 0.95, False) for g in range(1, len(positions))
    ]
    test = survival.cohort_test(rows, fitted, 0, False, None)
    return effects, test


@pytest.mark.parametrize("entries", [False, True])
def test_a_view_of_cohorts_alone_is_survival_km_s_hazard_ratios_and_test(entries: bool) -> None:
    positions = cohort_members(3, 3, 150, entries=entries)
    found = cox.model(positions, [], stratified=False, reference=0, overlap=False, level=0.95)
    effects, test = km_effects(positions)
    for term, effect in zip(found.values.view.terms, effects, strict=True):
        assert term.estimate == effect.estimate
        assert term.ci.low == effect.ci.low
        assert term.ci.high == effect.ci.high
    assert found.values.view.proportional_hazards.statistic == test.statistic
    assert found.values.view.proportional_hazards.p == test.p


def test_a_cohort_separated_on_the_cohort_path_has_its_direction() -> None:
    positions = cohort_members(5, 3, 90)
    early = [Member((ORIGIN, 0.001 * (i + 1), True), frozenset(), (), ()) for i in range(10)]
    late = [Member((ORIGIN, 1000.0 + i, True), frozenset(), (), ()) for i in range(10)]
    positions[1] = early
    positions[2] = late
    found = cox.model(positions, [], stratified=False, reference=0, overlap=False, level=0.95)
    first, second = found.values.view.terms
    assert first.direction == "infinity"
    assert second.direction == "zero"
    assert first.reasons()["/estimate"] == "separation"


def with_values(
    positions: list[list[Member]], value: Any, *, stratum: Any = None
) -> list[list[Member]]:
    extra: tuple[Any, ...] = (value,) if stratum is None else (value, stratum)
    return [
        [
            Member(m.endpoint, m.endpoint_reasons, extra, tuple(frozenset() for _ in extra))
            for m in members
        ]
        for members in positions
    ]


def test_a_stratum_of_one_level_and_a_constant_covariate_change_nothing() -> None:
    positions = cohort_members(7, 3, 120, eventless=2)
    plain = cox.model(positions, [], stratified=False, reference=0, overlap=False, level=0.95)
    constant = cox.model(
        with_values(positions, 3.0),
        [Covariate("number")],
        stratified=False,
        reference=0,
        overlap=False,
        level=0.95,
    )
    one = cox.model(
        with_values(positions, 3.0, stratum="s"),
        [Covariate("number")],
        stratified=True,
        reference=0,
        overlap=False,
        level=0.95,
    )
    base = plain.values.view
    for other in (constant.values.view, one.values.view):
        cohort = [term for term in other.terms if term.kind == "cohort"]
        assert [t.estimate for t in cohort] == [t.estimate for t in base.terms]
        assert other.proportional_hazards.statistic == base.proportional_hazards.statistic
        covariate = [term for term in other.terms if term.kind == "covariate"]
        assert [t.reasons()["/estimate"] for t in covariate] == ["zero_variance"]


def test_no_complete_case_or_no_event_leaves_every_value_without_an_estimate() -> None:
    empty = [[Member(None, MISSING, (), ())] for _ in range(2)]
    found = cox.model(empty, [], stratified=False, reference=0, overlap=False, level=0.95)
    assert {term.reasons()["/estimate"] for term in found.values.view.terms} == {"no_units"}
    assert found.values.view.proportional_hazards.reasons()["/p"] == "no_units"
    quiet = [
        [Member((ORIGIN, float(i + 1), False), frozenset(), (), ()) for i in range(5)]
        for _ in range(2)
    ]
    found = cox.model(quiet, [], stratified=False, reference=0, overlap=False, level=0.95)
    assert {term.reasons()["/estimate"] for term in found.values.view.terms} == {"no_events"}


def test_overlapping_cohorts_leave_every_value_without_an_estimate() -> None:
    positions = cohort_members(9, 2, 60)
    found = cox.model(positions, [], stratified=False, reference=0, overlap=True, level=0.95)
    assert {term.reasons()["/estimate"] for term in found.values.view.terms} == {
        "overlapping_cohorts"
    }
    assert found.values.view.proportional_hazards.reasons()["/p"] == "overlapping_cohorts"


def numbered(
    seed: int, groups: int, n: int, *, reference_events: bool = True
) -> list[list[Member]]:
    rng = random.Random(seed)
    positions: list[list[Member]] = [[] for _ in range(groups)]
    for i in range(n):
        g = i % groups
        x = round(rng.gauss(0, 1), 3)
        time = round(rng.expovariate(math.exp(0.5 * x)) * 10, 2) + 0.01
        event = rng.random() < 0.8 and (g != 0 or reference_events)
        positions[g].append(Member((ORIGIN, time, event), frozenset(), (x,), (frozenset(),)))
    return positions


def test_a_reference_without_events_leaves_the_covariates_estimated() -> None:
    positions = numbered(11, 3, 150, reference_events=False)
    found = cox.model(
        positions, [Covariate("number")], stratified=False, reference=0, overlap=False, level=0.95
    )
    cohorts = [term for term in found.values.view.terms if term.kind == "cohort"]
    assert {term.reasons()["/estimate"] for term in cohorts} == {"no_events"}
    [covariate] = [term for term in found.values.view.terms if term.kind == "covariate"]
    without = cox.model(
        [positions[1], positions[2]],
        [Covariate("number")],
        stratified=False,
        reference=0,
        overlap=False,
        level=0.95,
    )
    [expected] = [term for term in without.values.view.terms if term.kind == "covariate"]
    assert covariate.estimate is not None
    assert math.isclose(covariate.estimate, expected.estimate or 0.0, rel_tol=1e-9)


def test_a_covariate_equal_to_a_cohort_indicator_is_not_identified_with_it() -> None:
    positions = numbered(13, 2, 120)
    marked = [
        [
            Member(
                m.endpoint, m.endpoint_reasons, (m.values[0], float(g)), (frozenset(), frozenset())
            )
            for m in members
        ]
        for g, members in enumerate(positions)
    ]
    found = cox.model(
        marked,
        [Covariate("number"), Covariate("number")],
        stratified=False,
        reference=0,
        overlap=False,
        level=0.95,
    )
    cohort, number, indicator = found.values.view.terms
    assert cohort.reasons()["/estimate"] == "zero_variance"
    assert indicator.reasons()["/estimate"] == "zero_variance"
    assert number.estimate is not None


def test_more_parameters_or_strata_than_the_limits_are_refused() -> None:
    positions = numbered(15, 2, 200)
    levels = [f"c{k}" for k in range(10)]
    wide = [
        [
            Member(m.endpoint, m.endpoint_reasons, (levels[i % 10],), (frozenset(),))
            for i, m in enumerate(members)
        ]
        for members in positions
    ]
    with pytest.raises(cox.TooManyParameters) as caught:
        cox.model(
            wide, [Covariate("category")], stratified=False, reference=0, overlap=False, level=0.95
        )
    assert caught.value.count == 9
    nine = [
        [
            Member(m.endpoint, m.endpoint_reasons, (levels[i % 9],), (frozenset(),))
            for i, m in enumerate(members)
        ]
        for members in positions
    ]
    cox.model(
        nine, [Covariate("category")], stratified=False, reference=0, overlap=False, level=0.95
    )
    many = [
        [
            Member(m.endpoint, m.endpoint_reasons, (i + 1000 * g,), (frozenset(),))
            for i, m in enumerate(members)
        ]
        for g, members in enumerate(positions)
    ]
    with pytest.raises(cox.TooManyStrata):
        cox.model(many, [], stratified=True, reference=0, overlap=False, level=0.95)


def test_a_category_s_baseline_is_its_most_common_level_with_an_event() -> None:
    positions = numbered(17, 2, 160)
    coded = []
    for members in positions:
        rows = []
        for i, m in enumerate(members):
            assert m.endpoint is not None
            level = "a" if i % 4 != 0 else "b"
            endpoint = (m.endpoint[0], m.endpoint[1], m.endpoint[2] and level == "b")
            rows.append(Member(endpoint, frozenset(), (level,), (frozenset(),)))
        coded.append(rows)
    found = cox.model(
        coded, [Covariate("category")], stratified=False, reference=0, overlap=False, level=0.95
    )
    [term] = [term for term in found.values.view.terms if term.kind == "covariate"]
    assert term.baseline is not None
    assert term.baseline.data == "b"
    assert term.level is not None
    assert term.level.data == "a"
    assert term.direction == "zero"


def test_every_position_counts_each_variable_over_every_member() -> None:
    case = CASES["two cohorts, a number, a boolean and a category, with missing values"]
    positions, covariates = positions_of(case)
    found = cox.model(
        positions, covariates, stratified=False, reference=0, overlap=False, level=0.95
    )
    for members, analysed, at in zip(
        positions, found.analysed, found.values.positions, strict=True
    ):
        assert analysed.total() == len(members)
        assert len(at.variables) == len(covariates) + 1
        for counts in at.variables:
            assert counts.total() == len(members)
        assert analysed.excluded_units == sum(
            1 for m in members if any(value is None for value in m.values)
        )


def test_the_model_stops_at_the_call_s_deadline() -> None:
    case = CASES["three cohorts, a category and a stratum, with entries"]
    positions, covariates = positions_of(case)
    with pytest.raises(CallerDeadline):
        cox.model(
            positions,
            covariates,
            stratified=True,
            reference=0,
            overlap=False,
            level=0.95,
            ends=time.monotonic() - 1,
        )


def run(positions: list[list[Member]], covariates: list[Covariate], **given: Any) -> cox.Outcome:
    options: dict[str, Any] = {"stratified": False, "reference": 0, "overlap": False, "level": 0.95}
    options.update(given)
    return cox.model(positions, covariates, **options)


def reason(term: Any) -> str:
    return term.reasons()["/estimate"]


def test_a_category_with_no_complete_case_leaves_every_value_without_units() -> None:
    positions = [[Member(None, MISSING, ("a",), (frozenset(),))] for _ in range(2)]
    found = run(positions, [Covariate("category")])
    assert {reason(term) for term in found.values.view.terms} == {"no_units"}
    empty: list[list[Member]] = [[], []]
    found = run(empty, [Covariate("category"), Covariate("number")])
    assert [len(at.variables) for at in found.values.positions] == [3, 3]


def test_a_member_s_cells_must_be_its_covariates_and_stratum() -> None:
    positions = numbered(21, 2, 20)
    with pytest.raises(ValueError, match="cells"):
        run(positions, [Covariate("number"), Covariate("number")])


def test_the_test_names_the_terms_it_tested_even_where_a_ratio_is_beyond_range() -> None:
    positions = numbered(23, 2, 200)
    scaled = [
        [
            Member(m.endpoint, m.endpoint_reasons, (float(m.values[0] or 0) * 1e-3,), m.reasons)
            for m in members
        ]
        for members in positions
    ]
    found = run(scaled, [Covariate("number")])
    cohort, number = found.values.view.terms
    assert cohort.estimate is not None
    assert number.estimate is None
    assert reason(number) == "separation"
    assert number.direction is None
    test = found.values.view.proportional_hazards
    assert test.terms == [0, 1]
    assert test.df == 2


def test_a_fit_that_does_not_converge_leaves_its_terms_and_test_not_converged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    separated = coxph.separated

    def stalled(*args: Any, **kwargs: Any) -> coxph.Separated:
        found = separated(*args, **kwargs)
        assert found.fit is not None
        return dataclasses.replace(found, fit=dataclasses.replace(found.fit, converged=False))

    monkeypatch.setattr(coxph, "separated", stalled)
    found = run(numbered(25, 2, 120), [Covariate("number")])
    assert {reason(term) for term in found.values.view.terms} == {"not_converged"}
    assert found.values.view.proportional_hazards.reasons()["/p"] == "not_converged"


def test_one_cohort_whose_covariates_code_to_no_column_has_no_test() -> None:
    positions = numbered(27, 1, 60)
    constant = [[Member(m.endpoint, m.endpoint_reasons, (2.0,), m.reasons) for m in positions[0]]]
    found = run(constant, [Covariate("number")])
    assert [reason(term) for term in found.values.view.terms] == ["zero_variance"]
    assert found.values.view.proportional_hazards.reasons()["/p"] == "zero_variance"


def test_a_reference_with_no_complete_case_leaves_the_covariates_estimated() -> None:
    positions = numbered(29, 3, 150)
    positions[0] = [
        Member(m.endpoint, m.endpoint_reasons, (None,), (MISSING,)) for m in positions[0]
    ]
    found = run(positions, [Covariate("number")])
    terms = found.values.view.terms
    assert [reason(term) for term in terms if term.kind == "cohort"] == ["no_units", "no_units"]
    [covariate] = [term for term in terms if term.kind == "covariate"]
    assert covariate.estimate is not None
    cohorts_only = run([positions[0][:0], positions[1], positions[2]], [Covariate("number")])
    assert {reason(term) for term in cohorts_only.values.view.terms if term.kind == "cohort"} == {
        "no_units"
    }


def test_the_reference_s_reason_comes_before_another_position_s() -> None:
    positions = numbered(31, 3, 120, reference_events=False)
    positions[2] = [
        Member(m.endpoint, m.endpoint_reasons, (None,), (MISSING,)) for m in positions[2]
    ]
    found = run(positions, [Covariate("number")])
    cohorts = [term for term in found.values.view.terms if term.kind == "cohort"]
    assert [reason(term) for term in cohorts] == ["no_events", "no_events"]


def test_no_event_among_the_complete_cases_leaves_the_covariates_without_events() -> None:
    positions = numbered(33, 2, 60)
    quiet = [
        [
            Member((m.endpoint[0], m.endpoint[1], False), frozenset(), m.values, m.reasons)
            for m in members
            if m.endpoint is not None
        ]
        for members in positions
    ]
    found = run(quiet, [Covariate("number")])
    assert {reason(term) for term in found.values.view.terms} == {"no_events"}


def test_a_stratum_of_101_levels_is_refused_and_one_of_100_is_not() -> None:
    positions = numbered(35, 2, 404)
    for count, refused in ((100, False), (101, True)):
        cut = [
            [
                Member(m.endpoint, m.endpoint_reasons, (m.values[0], i % count), (frozenset(),) * 2)
                for i, m in enumerate(members)
            ]
            for members in positions
        ]
        levels = {m.values[1] for members in cut for m in members}
        assert len(levels) == count
        if refused:
            with pytest.raises(cox.TooManyStrata):
                run(cut, [Covariate("number")], stratified=True)
        else:
            run(cut, [Covariate("number")], stratified=True)


def test_a_number_whose_higher_values_fail_first_runs_to_infinity() -> None:
    positions = numbered(37, 2, 80)
    ordered = [
        [
            Member(m.endpoint, m.endpoint_reasons, (-m.endpoint[1],), m.reasons)
            for m in members
            if m.endpoint
        ]
        for members in positions
    ]
    found = run(ordered, [Covariate("number")])
    number = found.values.view.terms[-1]
    assert reason(number) == "separation"
    assert number.direction == "infinity"


def test_a_model_all_of_whose_terms_separate_has_its_test_separation() -> None:
    positions = [
        [
            Member((ORIGIN, float(10 + i), True), frozenset(), (float(i),), (frozenset(),))
            for i in range(8)
        ]
    ]
    found = run(positions, [Covariate("number")])
    assert [reason(term) for term in found.values.view.terms] == ["separation"]
    assert found.values.view.proportional_hazards.reasons()["/p"] == "separation"


def test_a_covariate_whose_level_separates_has_that_reason_for_its_joint_test() -> None:
    positions = numbered(39, 2, 180)
    coded = []
    for members in positions:
        rows = []
        for i, m in enumerate(members):
            assert m.endpoint is not None
            level = "abc"[i % 3]
            endpoint = (m.endpoint[0], m.endpoint[1], m.endpoint[2] and level != "c")
            rows.append(Member(endpoint, frozenset(), (level,), (frozenset(),)))
        coded.append(rows)
    found = run(coded, [Covariate("category")])
    [test] = found.values.view.covariate_tests
    assert test.reasons()["/p"] == "separation"


def test_a_constant_boolean_has_one_term_with_no_level() -> None:
    positions = numbered(41, 2, 60)
    flat = [
        [Member(m.endpoint, m.endpoint_reasons, (False,), m.reasons) for m in members]
        for members in positions
    ]
    found = run(flat, [Covariate("boolean")])
    boolean = found.values.view.terms[-1]
    assert reason(boolean) == "zero_variance"
    assert boolean.level is None
    assert boolean.baseline is None


def test_levels_follow_the_declared_order_or_their_canonical_text() -> None:
    positions = numbered(43, 2, 240)
    names = ['a"', "a#", "b", "a\\"]
    coded = [
        [
            Member(
                m.endpoint, m.endpoint_reasons, (names[i % 4] if i % 5 else "b",), (frozenset(),)
            )
            for i, m in enumerate(members)
        ]
        for members in positions
    ]
    found = run(coded, [Covariate("category")])
    levels = [term.level.data for term in found.values.view.terms if term.level is not None]
    assert found.values.view.terms[-1].baseline is not None
    assert found.values.view.terms[-1].baseline.data == "b"
    assert levels == ["a#", 'a"', "a\\"]
    ordered = run(coded, [Covariate("category", order=("b", "a\\", "a#", 'a"'))])
    levels = [term.level.data for term in ordered.values.view.terms if term.level is not None]
    assert levels == ["a\\", "a#", 'a"']


def test_a_stratum_equal_to_a_cohort_leaves_that_cohort_not_identified() -> None:
    positions = numbered(45, 2, 160)
    marked = [
        [
            Member(m.endpoint, m.endpoint_reasons, (m.values[0], g), (frozenset(),) * 2)
            for m in members
        ]
        for g, members in enumerate(positions)
    ]
    found = run(marked, [Covariate("number")], stratified=True)
    cohort, number = found.values.view.terms
    assert reason(cohort) == "zero_variance"
    assert number.estimate is not None


def test_a_term_whose_variance_is_not_positive_is_zero_variance() -> None:
    term = cox._term("covariate", 0, None, None, 0.95, estimate=(0.3, 0.0))  # pyright: ignore[reportPrivateUsage]
    assert reason(term) == "zero_variance"
    term = cox._term("covariate", 0, None, None, 0.95, estimate=(0.3, math.inf))  # pyright: ignore[reportPrivateUsage]
    assert reason(term) == "zero_variance"


def test_overlapping_cohorts_come_before_the_want_of_complete_cases() -> None:
    empty = [[Member(None, MISSING, (), ())] for _ in range(2)]
    found = run(empty, [], overlap=True)
    assert {reason(term) for term in found.values.view.terms} == {"overlapping_cohorts"}


def test_a_joint_statistic_beyond_2_53_is_zero_variance(monkeypatch: pytest.MonkeyPatch) -> None:
    case = CASES["two cohorts, a number, a boolean and a category, with missing values"]
    positions, covariates = positions_of(case)
    monkeypatch.setattr(cox.coxfit, "solved_quadratic", lambda *_: 2.0**60)
    found = run(positions, covariates)
    [test] = found.values.view.covariate_tests
    assert test.reasons()["/p"] == "zero_variance"


def test_the_model_takes_its_members_cells_as_d363_describes_them() -> None:
    positions = numbered(47, 2, 40)
    with pytest.raises(ValueError, match="at most"):
        run(
            [
                [
                    Member(m.endpoint, m.endpoint_reasons, (1.0,) * 9, (frozenset(),) * 9)
                    for m in members
                ]
                for members in positions
            ],
            [Covariate("number")] * 9,
        )
    mixed = [list(members) for members in positions]
    first = mixed[0][0]
    assert first.endpoint is not None
    mixed[0][0] = Member(
        (0.0, first.endpoint[1], first.endpoint[2]), frozenset(), first.values, first.reasons
    )
    with pytest.raises(ValueError, match="entry"):
        run(mixed, [Covariate("number")])
    ints = [
        [Member(m.endpoint, m.endpoint_reasons, (i % 2,), m.reasons) for i, m in enumerate(members)]
        for members in positions
    ]
    with pytest.raises(ValueError, match="boolean"):
        run(ints, [Covariate("boolean")])

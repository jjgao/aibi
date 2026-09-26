"""``compare.existence``: its values, estimability, caveats and disclosure (SPEC §8.1–§8.4, §9.5,
§13.4; D319, D320, D323), over truth values given unit by unit and over the shop evaluated by the
reference evaluator."""

import math
from collections.abc import Callable
from typing import Any

from hypothesis import given, settings
from hypothesis import strategies as st

from aibi.core.analyses import stats
from aibi.core.schema.caveats import CaveatCode
from aibi.core.schema.numbers import NotEstimableReason
from aibi.core.schema.results import ResultEnvelope

Patterned = Callable[..., Any]
Analyse = Callable[..., list[Any]]
Shop = Callable[..., Any]


def share(result: ResultEnvelope, position: int, predicate: int) -> dict[str, Any]:
    found: Any = result.values.positions[position]
    return found["predicates"][predicate]


def contrast(result: ResultEnvelope, predicate: int) -> dict[str, Any]:
    found: Any = result.values.view
    return found["predicates"][predicate]


def codes(result: ResultEnvelope) -> set[str]:
    return {caveat.code for caveat in result.caveats}


# --- Values ------------------------------------------------------------------------------------


def test_a_proportion_counts_the_cohort_s_units_for_which_the_predicate_is_known(
    patterned: Patterned,
) -> None:
    result = patterned(["TTTTTTFF"], ["TTFFIAFT"]).result
    proportion = share(result, 0, 0)["proportion"]
    assert (proportion["numerator"], proportion["denominator"]) == (2, 4)
    assert proportion["estimate"] == 0.5
    assert proportion["excluded"]["NO_INFORMATION"] == 1
    assert proportion["excluded"]["NOT_ASSESSED"] == 1
    assert sum(proportion["excluded"].values()) == 2
    assert proportion["denominator_definition"]["counts"] == "known"
    low, high = stats.wilson(2, 4, 0.95)
    assert (proportion["ci"]["low"], proportion["ci"]["high"]) == (low, high)
    [analysed] = result.analysed
    assert (analysed.n, analysed.excluded_units) == (4, 2)
    assert analysed.variables is None
    assert CaveatCode.UNKNOWN_EXCLUDED in codes(result)


def test_several_predicates_count_the_units_analysed_for_any_of_them(
    patterned: Patterned,
) -> None:
    result = patterned(["TTTTT"], ["TIIFT", "IIFTF"]).result
    [analysed] = result.analysed
    assert (analysed.n, analysed.excluded_units) == (4, 1)
    assert analysed.variables is not None
    assert [(v.n, v.excluded_units) for v in analysed.variables] == [(3, 2), (3, 2)]
    assert analysed.excluded is not None
    assert analysed.excluded["NO_INFORMATION"] == 1


def test_two_cohorts_are_tested_by_fisher_and_contrasted_with_the_reference(
    patterned: Patterned,
) -> None:
    result = patterned(["TTTTTTFFFFFF", "FFFFFFTTTTTT"], ["TTTTTFFFFFFT"]).result
    found = contrast(result, 0)
    assert found["test"]["method"] == "fisher_exact"
    assert found["test"]["p"] == stats.fisher(((5, 1), (1, 5)))
    assert found["test"]["q"] == found["test"]["p"]
    difference, ratio = found["effects"]
    expected = stats.newcombe(1, 6, (5, 6), 0.95)
    assert (difference["estimate"], difference["ci"]["low"], difference["ci"]["high"]) == expected
    assert (difference["measure"], difference["position"], difference["versus"]) == (
        "risk_difference",
        1,
        0,
    )
    expected = stats.katz(1, 6, (5, 6), 0.95)
    assert (ratio["estimate"], ratio["ci"]["low"], ratio["ci"]["high"]) == expected
    assert result.values.view["family"] == {"tests": 1, "not_computed": 0, "suppressed": 0}
    assert [cohort.reference for cohort in result.cohorts] == [True, False]


def test_three_cohorts_are_tested_by_chi_squared_with_a_small_expected_count_flagged(
    patterned: Patterned,
) -> None:
    result = patterned(["TTTFFFFFF", "FFFTTTFFF", "FFFFFFTTT"], ["TTFTFFFFT"]).result
    test = contrast(result, 0)["test"]
    expected = stats.chi_squared([[2, 1], [1, 2], [1, 2]])
    assert test["method"] == "chi_squared"
    assert (test["statistic"], test["df"], test["p"]) == (expected.statistic, 2, expected.p)
    assert CaveatCode.SMALL_N in codes(result)
    [small] = [caveat for caveat in result.caveats if caveat.code == CaveatCode.SMALL_N]
    assert small.affects == ["/values/view/predicates/0/test"]
    assert len(contrast(result, 0)["effects"]) == 4


def test_swapping_the_reference_negates_differences_and_inverts_ratios(
    patterned: Patterned,
) -> None:
    cohorts, predicates = ["TTTTTTFFFFFF", "FFFFFFTTTTTT"], ["TTTTFFFFFFTT"]
    first = patterned(cohorts, predicates, reference=0)
    second = patterned(cohorts, predicates, reference=1)
    assert first.view.identity.id != second.view.identity.id
    assert first.result.digest != second.result.digest
    one, other = contrast(first.result, 0), contrast(second.result, 0)
    assert one["test"]["p"] == other["test"]["p"]
    difference, swapped = one["effects"][0], other["effects"][0]
    assert (swapped["position"], swapped["versus"]) == (0, 1)
    assert math.isclose(swapped["estimate"], -difference["estimate"], abs_tol=1e-15)
    assert math.isclose(swapped["ci"]["low"], -difference["ci"]["high"], rel_tol=1e-12)
    assert math.isclose(swapped["ci"]["high"], -difference["ci"]["low"], rel_tol=1e-12)
    ratio, inverted = one["effects"][1], other["effects"][1]
    assert math.isclose(inverted["estimate"], 1 / ratio["estimate"], rel_tol=1e-12)
    assert math.isclose(inverted["ci"]["low"], 1 / ratio["ci"]["high"], rel_tol=1e-12)
    assert math.isclose(inverted["ci"]["high"], 1 / ratio["ci"]["low"], rel_tol=1e-12)


def test_one_cohort_has_its_proportions_and_no_contrast(patterned: Patterned) -> None:
    result = patterned(["TTTT"], ["TFTF"]).result
    assert contrast(result, 0) == {"effects": []}
    assert "family" not in result.values.view
    assert [cohort.reference for cohort in result.cohorts] == [True]


# --- Estimability (§9.5) ---------------------------------------------------------------------


def test_a_cohort_without_known_units_has_no_proportion_and_no_contrast(
    patterned: Patterned,
) -> None:
    result = patterned(["TTTFFF", "FFFTTT"], ["TFTIII"]).result
    proportion = share(result, 1, 0)["proportion"]
    assert proportion["estimate"] is None
    assert proportion["not_estimable"]["/estimate"] == NotEstimableReason.NO_UNITS
    found = contrast(result, 0)
    assert found["test"]["positions"] == [0]
    assert found["test"]["not_estimable"] == {"/p": "no_units"}
    assert all(effect["not_estimable"]["/estimate"] == "no_units" for effect in found["effects"])
    assert result.values.view["family"] == {"tests": 0, "not_computed": 1, "suppressed": 0}
    assert CaveatCode.NOT_ESTIMABLE in codes(result)


def test_the_test_uses_the_cohorts_with_known_units(patterned: Patterned) -> None:
    result = patterned(["TTFFFF", "FFTTFF", "FFFFTT"], ["TFTFII"]).result
    test = contrast(result, 0)["test"]
    assert test["positions"] == [0, 1]
    assert test["method"] == "fisher_exact"
    assert test["p"] == stats.fisher(((1, 1), (1, 1)))


def test_a_table_whose_answers_are_all_true_has_no_test(patterned: Patterned) -> None:
    result = patterned(["TTFF", "FFTT"], ["TTTT"]).result
    found = contrast(result, 0)
    assert found["test"]["not_estimable"] == {"/p": "degenerate_table"}
    assert found["effects"][0]["estimate"] == 0.0


def test_a_risk_ratio_with_a_zero_numerator_is_not_estimable(patterned: Patterned) -> None:
    result = patterned(["TTTFFF", "FFFTTT"], ["FFFTFF"]).result
    difference, ratio = contrast(result, 0)["effects"]
    assert difference["estimate"] is not None
    assert ratio["estimate"] is None
    assert ratio["not_estimable"] == {
        "/estimate": "no_events",
        "/ci/low": "no_events",
        "/ci/high": "no_events",
    }


def test_a_reference_without_known_units_leaves_no_contrast_with_it(patterned: Patterned) -> None:
    result = patterned(["TTTFFF", "FFFTTT"], ["IIITFT"]).result
    assert share(result, 1, 0)["proportion"]["estimate"] == 2 / 3
    for effect in contrast(result, 0)["effects"]:
        assert effect["not_estimable"]["/estimate"] == "no_units"
    assert contrast(result, 0)["test"]["not_estimable"] == {"/p": "no_units"}


def test_overlapping_cohorts_allowed_give_no_between_cohort_value(patterned: Patterned) -> None:
    result = patterned(["TTTTFF", "FFTTTT"], ["TFTFTF"], overlap=True).result
    found = contrast(result, 0)
    assert found["test"]["not_estimable"] == {"/p": "overlapping_cohorts"}
    assert {effect["not_estimable"]["/estimate"] for effect in found["effects"]} == {
        "overlapping_cohorts"
    }
    assert share(result, 0, 0)["proportion"]["estimate"] == 0.5
    assert CaveatCode.COHORTS_OVERLAP in codes(result)


def test_disjoint_cohorts_that_allow_overlap_are_contrasted(patterned: Patterned) -> None:
    result = patterned(["TTTFFF", "FFFTTT"], ["TFTFTF"], overlap=True).result
    assert contrast(result, 0)["test"]["p"] is not None
    assert CaveatCode.COHORTS_OVERLAP not in codes(result)


def test_tests_that_cannot_be_computed_leave_the_family(patterned: Patterned) -> None:
    result = patterned(
        ["TTTTTTFFFFFF", "FFFFFFTTTTTT"],
        ["TTTTTFFFFFFT", "TTTTTTTTTTTT", "TFTFTFTFTFTF"],
    ).result
    tests = [contrast(result, index)["test"] for index in range(3)]
    assert "q" not in tests[1]
    assert [test["q"] for test in (tests[0], tests[2])] == stats.benjamini_hochberg(
        [tests[0]["p"], tests[2]["p"]]
    )
    assert result.values.view["family"] == {"tests": 2, "not_computed": 1, "suppressed": 0}


# --- Lift (§6.5, D323) -----------------------------------------------------------------------


def test_the_units_whose_answer_the_other_lift_changes_are_counted_per_predicate(
    patterned: Patterned,
) -> None:
    result = patterned(["TTTTFF"], ["TFCCTF"], lifts=["TFFTTT"]).result
    assert share(result, 0, 0)["lift_differs"] == 2
    [lift] = [caveat for caveat in result.caveats if caveat.code == CaveatCode.LIFT_DIFFERS]
    assert "2 units of position 0 for predicate 0" in "".join(
        segment.model_dump().get("text", "") for segment in lift.message
    )


def test_a_predicate_without_a_lift_changes_no_unit(patterned: Patterned) -> None:
    result = patterned(["TTTTFF"], ["TFCCTF"]).result
    assert share(result, 0, 0)["lift_differs"] == 0
    assert CaveatCode.LIFT_DIFFERS not in codes(result)


def test_a_question_through_two_steps_counts_its_lift_on_the_shop(
    analyse: Analyse, shop: Shop
) -> None:
    returned = {
        "kind": "exists",
        "table": "orders",
        "where": [{"kind": "exists", "table": "returns", "where": []}],
    }
    written = {
        "aibi": "1",
        "dataset": "d",
        "unit": "customers",
        "cohorts": {"all": {"all": []}},
        "views": [
            {
                "analysis": "compare.existence",
                "cohorts": ["all"],
                "params": {"predicates": [returned]},
            }
        ],
    }
    [analysed] = analyse(written, shop())
    found = share(analysed.result, 0, 0)
    assert found["lift_differs"] > 0
    assert CaveatCode.LIFT_DIFFERS in codes(analysed.result)
    proportion = found["proportion"]
    assert proportion["excluded"]["NOT_COVERED"] > 0
    assert CaveatCode.UNKNOWN_EXCLUDED in codes(analysed.result)


# --- Disclosure (§8.4, D320) -------------------------------------------------------------------


def test_a_suppressed_split_carries_unknown_excluded_though_none_of_its_units_is_unknown(
    patterned: Patterned,
) -> None:
    """Under *k*, ``UNKNOWN_EXCLUDED`` goes with every suppressed UNKNOWN count, so that its
    absence says nothing of one (D320): here a cohort of 2 hides its split, of no UNKNOWN
    unit."""
    analysed = patterned(["TT" + "F" * 18, "F" * 2 + "T" * 18], ["TT" + "T" * 18], k=3)
    assert analysed.outcome.population[0].n_unknown == 0
    assert CaveatCode.UNKNOWN_EXCLUDED in codes(analysed.result)


def test_under_k_small_counts_and_what_they_derive_are_suppressed(patterned: Patterned) -> None:
    result = patterned(["TTTTTTTTTTFF", "FFFFFFFFTTTT"], ["TTTTTTTTTFFF"], k=3).result
    proportion = share(result, 1, 0)["proportion"]
    assert proportion["numerator"] is None
    assert proportion["not_estimable"]["/numerator"] == "suppressed"
    assert proportion["not_estimable"]["/estimate"] == "suppressed"
    found = contrast(result, 0)
    assert found["test"]["not_estimable"] == {"/p": "suppressed"}
    assert result.values.view["family"] == {"tests": 0, "not_computed": 0, "suppressed": 1}
    assert share(result, 0, 0)["lift_differs"] is None
    assert {CaveatCode.SUPPRESSED, CaveatCode.LIFT_DIFFERS} <= codes(result)
    assert result.derivation.disclosure.min_cell_count == 3


def _cohorts(count: int, size: int = 12) -> list[str]:
    """``count`` cohorts of ``size`` units each, none shared."""
    return [
        "F" * (size * index) + "T" * size + "F" * (size * (count - index - 1))
        for index in range(count)
    ]


def _answers(*parts: str) -> str:
    return "".join(parts)


SHOWN = "TTTTTTFFFFFF"
HIDDEN = "TFFFFFFFFFFF"


def test_an_effect_against_a_suppressed_reference_is_suppressed(patterned: Patterned) -> None:
    result = patterned(_cohorts(2), [_answers(HIDDEN, SHOWN)], k=3).result
    assert share(result, 0, 0)["proportion"]["numerator"] is None
    assert share(result, 1, 0)["proportion"]["numerator"] == 6
    found = contrast(result, 0)
    for effect in found["effects"]:
        assert effect["estimate"] is None
        assert effect["not_estimable"]["/estimate"] == "suppressed"
    assert found["test"]["p"] is None


def test_an_effect_of_a_suppressed_position_is_suppressed_and_the_others_are_shown(
    patterned: Patterned,
) -> None:
    result = patterned(_cohorts(3), [_answers(SHOWN, SHOWN, HIDDEN)], k=3).result
    found = contrast(result, 0)
    estimates = {(e["position"], e["measure"]): e["estimate"] for e in found["effects"]}
    assert estimates == {
        (1, "risk_difference"): 0.0,
        (1, "risk_ratio"): 1.0,
        (2, "risk_difference"): None,
        (2, "risk_ratio"): None,
    }


def test_a_test_is_suppressed_when_one_of_three_cohorts_is(patterned: Patterned) -> None:
    result = patterned(_cohorts(3), [_answers(SHOWN, HIDDEN, SHOWN)], k=3).result
    found = contrast(result, 0)
    assert found["test"]["p"] is None
    assert found["test"]["not_estimable"] == {"/p": "suppressed", "/statistic": "suppressed"}
    assert result.values.view["family"] == {"tests": 0, "not_computed": 0, "suppressed": 1}


def test_a_count_whose_complement_is_small_suppresses_the_numerator_of_its_proportion(
    patterned: Patterned,
) -> None:
    """``TTTTTTTTTI`` (D320): a numerator of 9 beside a hidden denominator would give the one
    unit that is not TRUE, and that it is UNKNOWN."""
    for pattern in ("TTTTTTTTTI", "TTTTTTTTTF", "TTTTTTTTII", "TTTTTTTTFI"):
        result = patterned(["T" * 10], [pattern], k=3).result
        proportion = share(result, 0, 0)["proportion"]
        assert proportion["numerator"] is None, pattern


def test_a_not_estimable_value_keeps_its_reason_under_k(patterned: Patterned) -> None:
    result = patterned(["TTTTTFFFFF", "FFFFFTTTTT"], ["FFFFFTTTTT"], k=2).result
    ratio = contrast(result, 0)["effects"][1]
    assert ratio["not_estimable"]["/estimate"] == "no_events"


def _small(k: int, count: object) -> bool:
    return isinstance(count, int) and not isinstance(count, bool) and 0 < count < k


def _counts(value: object) -> list[object]:
    found: list[object] = []
    if isinstance(value, dict):
        for key, member in value.items():
            if key in ("numerator", "denominator", "lift_differs"):
                found.append(member)
            elif key == "excluded" and isinstance(member, dict):
                found.extend(member.values())
            elif key not in ("denominator_definition", "not_estimable"):
                found.extend(_counts(member))
    elif isinstance(value, list):
        for item in value:
            found.extend(_counts(item))
    return found


@settings(max_examples=150, deadline=None)
@given(
    k=st.integers(min_value=2, max_value=6),
    units=st.lists(st.sampled_from("TFI"), min_size=4, max_size=40),
    data=st.data(),
)
def test_no_shown_count_of_a_result_under_k_lies_from_one_to_k_less_one(
    patterned: Patterned, k: int, units: list[str], data: st.DataObject
) -> None:
    size = len(units)
    cohorts = [
        "".join(data.draw(st.lists(st.sampled_from("TF"), min_size=size, max_size=size)))
        for _ in range(data.draw(st.integers(min_value=1, max_value=3)))
    ]
    predicates = [
        "".join(data.draw(st.lists(st.sampled_from("TFIA"), min_size=size, max_size=size)))
        for _ in range(data.draw(st.integers(min_value=1, max_value=2)))
    ]
    result = patterned(cohorts, predicates, k=k).result
    dumped = result.model_dump(mode="json")
    shown = [
        *_counts(dumped["values"]),
        *(entry.get(key) for entry in dumped["analysed"] for key in ("n", "excluded_units")),
    ]
    assert not [count for count in shown if _small(k, count)]
    for position in dumped["values"]["positions"]:
        for found in position["predicates"]:
            proportion = found["proportion"]
            numerator, denominator = proportion["numerator"], proportion["denominator"]
            if numerator is not None and denominator is not None:
                assert not _small(k, denominator - numerator) or numerator == 0
    _derived_from_shown_counts_only(dumped)


def _lost(positions: list[Any], predicate: int) -> list[bool]:
    """Per position, whether a count the predicate's proportion is computed from is null."""
    found = []
    for position in positions:
        proportion = position["predicates"][predicate]["proportion"]
        found.append(proportion["numerator"] is None or proportion["denominator"] is None)
    return found


def _derived_from_shown_counts_only(dumped: dict[str, Any]) -> None:
    """Every estimate, interval, effect, test and chart row of a result is null, or absent,
    where a count it is computed from is null (§8.4, D320)."""
    positions = dumped["values"]["positions"]
    rows = dumped["charts"][0]["data"]["values"]
    shown_rows = 0
    for at, position in enumerate(positions):
        for predicate, found in enumerate(position["predicates"]):
            proportion = found["proportion"]
            if _lost(positions, predicate)[at]:
                assert proportion["estimate"] is None
                assert (proportion["ci"]["low"], proportion["ci"]["high"]) == (None, None)
            shown_rows += proportion["estimate"] is not None
    assert len(rows) == shown_rows
    assert all(value is not None for row in rows for value in row.values())
    for predicate, found in enumerate(dumped["values"]["view"]["predicates"]):
        lost = _lost(positions, predicate)
        test = found.get("test")
        if test is not None and any(lost):
            assert test["p"] is None
            assert test.get("statistic") is None
            assert "q" not in test
        for effect in found["effects"]:
            if lost[effect["position"]] or lost[effect["versus"]]:
                assert effect["estimate"] is None
                assert (effect["ci"]["low"], effect["ci"]["high"]) == (None, None)

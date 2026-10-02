"""``compare.columns``: its values, estimability, bootstrap, caveats, disclosure and phase 2 (SPEC
§7.6, §8.1–§8.4, §9.2, §9.3, §9.5; D336–D338), over variables materialised as given and over the
shop evaluated by the reference evaluator."""

import json
import math
import time
from collections.abc import Callable, Mapping
from types import MappingProxyType
from typing import Any, cast

import pytest

from aibi.core.analyses import columns, stats
from aibi.core.analyses.charts import columns_charts
from aibi.core.analyses.distribution import TooLarge, TooManyCategories
from aibi.core.engine import build
from aibi.core.engine.variables import Joint, Materialised
from aibi.core.engine.worker import CallerDeadline
from aibi.core.schema.caveats import CaveatCode
from aibi.core.schema.refusals import RefusalCode
from aibi.core.schema.semantics import ExclusionReason

Columned = Callable[..., Any]
Contrasted = Callable[..., Any]
Summarised = Callable[..., Any]
Distributed = Callable[..., Any]
Analyse = Callable[..., list[Any]]
Check = Callable[..., Any]
Shop = Callable[..., Any]

TIER = {"column": "customers.tier"}
AGE = {"column": "customers.age"}
AMOUNT = {"column": "orders.amount", "aggregate": "mean"}
WEB = {"column": "orders.channel", "aggregate": "some", "values": ["web"]}
YOUNG = {"kind": "value", "column": "customers.age", "range": {"lt": 45}}
OLD = {"kind": "value", "column": "customers.age", "range": {"gte": 45}}


def made(values: Mapping[Any, int], excluded: Mapping[str, int] | None = None) -> Materialised:
    """A variable materialised over a cohort: its values, and its units excluded, each under
    one reason."""
    by_reason = dict.fromkeys(ExclusionReason, 0)
    by_reason.update({ExclusionReason(reason): n for reason, n in (excluded or {}).items()})
    return Materialised(
        MappingProxyType(dict(values)),
        sum(by_reason.values()),
        MappingProxyType(by_reason),
        frozenset(),
    )


def weighted(values: list[float]) -> stats.Weighted:
    counted: dict[float, int] = {}
    for value in values:
        counted[value] = counted.get(value, 0) + 1
    return sorted(counted.items())


def numbers(values: list[float]) -> Materialised:
    return made(dict(weighted(values)))


def one_each(*found: Materialised) -> list[tuple[list[Materialised], Joint | None]]:
    return [([one], None) for one in found]


def at(outcome: Any, position: int, column: int = 0) -> dict[str, Any]:
    return outcome.values.positions[position].columns[column].model_dump(mode="json")


def across(outcome: Any, column: int = 0) -> dict[str, Any]:
    return outcome.values.view.columns[column].model_dump(mode="json")


def codes(outcome: Any) -> set[str]:
    return {caveat.code for caveat in outcome.caveats}


def shop_document(*given: Mapping[str, Any], **view: Any) -> dict[str, Any]:
    return {
        "aibi": "1",
        "dataset": "d",
        "unit": "customers",
        "cohorts": {"young": {"all": [YOUNG]}, "old": {"all": [OLD]}},
        "views": [
            {
                "analysis": "compare.columns",
                "cohorts": ["young", "old"],
                "params": {"columns": [dict(column) for column in given]},
                **view,
            }
        ],
    }


X = [1.5, 2.25, 3.0, 4.75, 5.0, 9.0]
Y = [0.0, 2.0, 2.5, 3.1, 8.0, 8.5, 12.0]
Z = [10.0, 11.0, 13.0, 13.5, 20.0]


# --- Categories -----------------------------------------------------------------------------------


def test_categories_are_counted_at_each_position_and_tested_across_them(
    columned: Columned, contrasted: Contrasted
) -> None:
    view = columned([TIER])
    found = contrasted(
        view, [12, 10], one_each(made({"gold": 6, "silver": 4}), made({"gold": 2, "silver": 8}))
    )
    first = at(found, 0)["categories"]
    assert [(c["values"][0]["data"], c["proportion"]["numerator"]) for c in first] == [
        ("gold", 6),
        ("silver", 4),
        ("bronze", 0),
    ]
    compared = across(found)
    assert compared["test"]["method"] == "fisher_exact"
    assert compared["test"]["positions"] == [0, 1]
    assert compared["test"]["p"] == pytest.approx(stats.fisher(((6, 4), (2, 8))), rel=1e-15)
    [gold, silver, bronze] = compared["categories"]
    assert [row["values"][0]["data"] for row in (gold, silver, bronze)] == [
        "gold",
        "silver",
        "bronze",
    ]
    [effect] = gold["effects"]
    expected = stats.newcombe(2, 10, (6, 10), 0.95)
    assert (effect["measure"], effect["position"], effect["versus"]) == (
        "proportion_difference",
        1,
        0,
    )
    assert (effect["estimate"], effect["ci"]["low"], effect["ci"]["high"]) == pytest.approx(
        expected
    )
    assert bronze["effects"][0]["estimate"] == 0.0


def test_three_categories_or_three_cohorts_are_tested_by_chi_squared(
    columned: Columned, contrasted: Contrasted
) -> None:
    view = columned([TIER], cohorts=3)
    rows = [{"gold": 6, "silver": 4, "bronze": 1}, {"gold": 2, "silver": 8}, {"gold": 5}]
    found = contrasted(view, [11, 10, 5], one_each(*(made(row) for row in rows)))
    test = across(found)["test"]
    expected = stats.chi_squared([[6, 4, 1], [2, 8, 0], [5, 0, 0]])
    assert test["method"] == "chi_squared"
    assert (test["statistic"], test["df"], test["p"]) == (
        expected.statistic,
        expected.df,
        expected.p,
    )
    assert CaveatCode.SMALL_N in codes(found)
    assert found.values.view.family.model_dump() == {"tests": 1, "not_computed": 0, "suppressed": 0}
    assert test["q"] == test["p"]


def test_a_category_no_cohort_holds_is_dropped_before_the_test_is_chosen(
    columned: Columned, contrasted: Contrasted
) -> None:
    view = columned([TIER])
    found = contrasted(
        view, [10, 10], one_each(made({"gold": 6, "silver": 4}), made({"gold": 2, "silver": 8}))
    )
    assert across(found)["test"]["method"] == "fisher_exact"


def test_one_category_held_by_every_cohort_is_a_degenerate_table(
    columned: Columned, contrasted: Contrasted
) -> None:
    view = columned([TIER])
    found = contrasted(view, [6, 4], one_each(made({"gold": 6}), made({"gold": 4})))
    test = across(found)["test"]
    assert test["not_estimable"] == {"/p": "degenerate_table", "/statistic": "degenerate_table"}
    assert test["positions"] == [0, 1]
    assert found.values.view.family.model_dump() == {"tests": 0, "not_computed": 1, "suppressed": 0}


def test_a_cohort_with_no_value_has_no_proportion_and_the_test_uses_the_others(
    columned: Columned, contrasted: Contrasted
) -> None:
    view = columned([TIER], cohorts=3)
    found = contrasted(
        view,
        [10, 3, 10],
        one_each(
            made({"gold": 6, "silver": 4}),
            made({}, {"NOT_ASSESSED": 3}),
            made({"gold": 1, "silver": 9}),
        ),
    )
    assert at(found, 1)["categories"][0]["proportion"]["not_estimable"] == {"/estimate": "no_units"}
    test = across(found)["test"]
    assert (test["method"], test["positions"]) == ("fisher_exact", [0, 2])
    gold = across(found)["categories"][0]["effects"]
    assert [effect["position"] for effect in gold] == [1, 2]
    assert gold[0]["not_estimable"] == dict.fromkeys(
        ("/estimate", "/ci/low", "/ci/high"), "no_units"
    )
    assert gold[1]["estimate"] == pytest.approx(0.1 - 0.6)


def test_undeclared_values_any_cohort_holds_are_rows_at_every_position_without_k(
    columned: Columned, contrasted: Contrasted
) -> None:
    view = columned([TIER])
    found = contrasted(
        view, [3, 3], one_each(made({"gold": 2, "zinc": 1}), made({"gold": 1, "Zinc": 2}))
    )
    for position in (0, 1):
        names = [c["values"][0]["data"] for c in at(found, position)["categories"]]
        assert names == ["gold", "silver", "bronze", "Zinc", "zinc"]
    assert [row["values"][0]["data"] for row in across(found)["categories"]][-2:] == [
        "Zinc",
        "zinc",
    ]


def test_more_categories_than_a_result_lists_refuse(
    columned: Columned, contrasted: Contrasted
) -> None:
    view = columned([TIER])
    values = {f"v{index:03}": 1 for index in range(148)}
    with pytest.raises(TooManyCategories):
        contrasted(view, [148, 1], one_each(made(values), made({"gold": 1})))
    del values["v000"]
    contrasted(view, [147, 1], one_each(made(values), made({"gold": 1})))


# --- Numbers --------------------------------------------------------------------------------------


def test_numbers_give_n_mean_sd_and_median_at_each_position(
    columned: Columned, contrasted: Contrasted
) -> None:
    view = columned([AGE])
    found = contrasted(view, [6, 7], one_each(numbers(X), numbers(Y)))
    first = at(found, 0)
    assert first["n"] == 6
    assert first["mean"] == stats.mean(weighted(X))
    assert first["sd"] == stats.moments(weighted(X)).sd
    assert first["median"] == 3.875
    assert "not_estimable" not in first


def test_two_cohorts_are_tested_by_welch_s_t_and_mann_whitney_position_order_first(
    columned: Columned, contrasted: Contrasted
) -> None:
    view = columned([AGE])
    found = contrasted(view, [6, 7], one_each(numbers(X), numbers(Y)))
    compared = across(found)
    welch = stats.welch(stats.moments(weighted(X)), stats.moments(weighted(Y)))
    test = compared["test"]
    assert (test["method"], test["positions"]) == ("welch_t", [0, 1])
    assert (test["statistic"], test["df"], test["p"]) == (welch.statistic, welch.df, welch.p)
    assert test["q"] == test["p"]
    ranked = stats.mann_whitney(weighted(X), weighted(Y))
    assert ranked is not None
    secondary = compared["secondary"]
    assert (secondary["method"], secondary["statistic"], secondary["p"]) == (
        "mann_whitney",
        20.0,
        ranked.p,
    )
    assert "q" not in secondary


def test_the_differences_are_position_minus_reference_with_their_intervals(
    columned: Columned, contrasted: Contrasted
) -> None:
    view = columned([AGE])
    found = contrasted(view, [6, 7], one_each(numbers(X), numbers(Y)))
    mean_difference, median_difference = across(found)["effects"]
    welch = stats.welch(stats.moments(weighted(Y)), stats.moments(weighted(X)))
    assert mean_difference["measure"] == "mean_difference"
    assert (mean_difference["position"], mean_difference["versus"]) == (1, 0)
    assert mean_difference["estimate"] == pytest.approx(welch.difference, rel=1e-15)
    assert (mean_difference["ci"]["low"], mean_difference["ci"]["high"]) == pytest.approx(
        welch.interval(0.95), rel=1e-12
    )
    assert mean_difference["ci"]["method"] == "welch_satterthwaite"
    assert median_difference["measure"] == "median_difference"
    assert median_difference["estimate"] == 3.1 - 3.875
    ci = median_difference["ci"]
    assert ci["method"] == "bootstrap_percentile"
    assert ci["low"] <= median_difference["estimate"] <= ci["high"]


def test_three_cohorts_are_tested_by_welch_s_one_way_test_and_kruskal_wallis(
    columned: Columned, contrasted: Contrasted
) -> None:
    view = columned([AGE], cohorts=3, reference=2)
    found = contrasted(view, [6, 7, 5], one_each(numbers(X), numbers(Y), numbers(Z)))
    compared = across(found)
    anova = stats.welch_anova([stats.moments(weighted(g)) for g in (X, Y, Z)])
    test = compared["test"]
    assert test["method"] == "welch_anova"
    assert (test["statistic"], test["df"], test["df_denominator"], test["p"]) == (
        anova.statistic,
        2,
        anova.denominator,
        anova.p,
    )
    assert compared["secondary"]["method"] == "kruskal_wallis"
    assert compared["secondary"]["df"] == 2
    assert [(e["measure"], e["position"], e["versus"]) for e in compared["effects"]] == [
        ("mean_difference", 0, 2),
        ("median_difference", 0, 2),
        ("mean_difference", 1, 2),
        ("median_difference", 1, 2),
    ]


def test_a_cohort_of_one_value_has_no_sd_welch_test_or_mean_interval_but_its_medians(
    columned: Columned, contrasted: Contrasted
) -> None:
    view = columned([AGE])
    found = contrasted(view, [6, 1], one_each(numbers(X), numbers([4.0])))
    assert at(found, 1)["not_estimable"] == {"/sd": "zero_variance"}
    compared = across(found)
    assert compared["test"]["not_estimable"] == {
        "/p": "zero_variance",
        "/statistic": "zero_variance",
    }
    assert compared["secondary"]["p"] is not None
    mean_difference, median_difference = compared["effects"]
    assert mean_difference["estimate"] == pytest.approx(4.0 - stats.mean(weighted(X)))
    assert mean_difference["not_estimable"] == {
        "/ci/low": "zero_variance",
        "/ci/high": "zero_variance",
    }
    assert median_difference["ci"]["low"] is not None


def test_values_all_tied_have_no_rank_test(columned: Columned, contrasted: Contrasted) -> None:
    view = columned([AGE])
    found = contrasted(view, [3, 2], one_each(numbers([5.0] * 3), numbers([5.0] * 2)))
    compared = across(found)
    assert compared["test"]["not_estimable"]["/p"] == "zero_variance"
    assert compared["secondary"]["not_estimable"] == {
        "/p": "zero_variance",
        "/statistic": "zero_variance",
    }
    _, median_difference = compared["effects"]
    assert (median_difference["estimate"], median_difference["ci"]["low"]) == (0.0, 0.0)


def test_a_cohort_with_no_value_has_no_estimate_and_its_contrasts_are_not_estimable(
    columned: Columned, contrasted: Contrasted
) -> None:
    view = columned([AGE])
    found = contrasted(view, [6, 2], one_each(numbers(X), made({}, {"NO_INFORMATION": 2})))
    assert at(found, 1)["not_estimable"] == dict.fromkeys(("/mean", "/sd", "/median"), "no_units")
    compared = across(found)
    assert compared["test"]["positions"] == [0]
    assert compared["test"]["not_estimable"]["/p"] == "no_units"
    assert all(e["not_estimable"]["/estimate"] == "no_units" for e in compared["effects"])
    assert found.values.view.family.model_dump() == {"tests": 0, "not_computed": 1, "suppressed": 0}


def test_one_cohort_has_no_between_cohort_value(columned: Columned, contrasted: Contrasted) -> None:
    view = columned([AGE, TIER], cohorts=1)
    found = contrasted(
        view,
        [6],
        [([numbers(X), made({"gold": 2})], Joint(6, 0, dict.fromkeys(ExclusionReason, 0)))],
    )
    assert across(found, 0) == {"kind": "numbers", "effects": []}
    assert "test" not in across(found, 1)
    assert found.values.view.family is None


def test_the_family_holds_one_primary_test_a_column(
    columned: Columned, contrasted: Contrasted
) -> None:
    view = columned([AGE, TIER, AMOUNT])
    none = dict.fromkeys(ExclusionReason, 0)
    found = contrasted(
        view,
        [6, 7],
        [
            ([numbers(X), made({"gold": 5, "silver": 1}), numbers([1.0])], Joint(6, 0, none)),
            ([numbers(Y), made({"gold": 1, "silver": 6}), numbers([2.0])], Joint(7, 0, none)),
        ],
    )
    tests = [across(found, column)["test"] for column in range(3)]
    q = stats.benjamini_hochberg([tests[0]["p"], tests[1]["p"]])
    assert [test.get("q") for test in tests] == [q[0], q[1], None]
    assert found.values.view.family.model_dump() == {"tests": 2, "not_computed": 1, "suppressed": 0}


def test_cohorts_that_share_units_under_overlap_allow_get_no_between_cohort_value(
    columned: Columned, contrasted: Contrasted
) -> None:
    view = columned([AGE, TIER], overlap=True)
    none = dict.fromkeys(ExclusionReason, 0)
    found = contrasted(
        view,
        [6, 7],
        [
            ([numbers(X), made({"gold": 6})], Joint(6, 0, none)),
            ([numbers(Y), made({"silver": 7})], Joint(7, 0, none)),
        ],
        overlap=True,
    )
    for column in (0, 1):
        compared = across(found, column)
        assert compared["test"]["not_estimable"]["/p"] == "overlapping_cohorts"
        assert compared["test"]["positions"] == []
    assert across(found, 0)["effects"][0]["not_estimable"]["/estimate"] == "overlapping_cohorts"
    assert at(found, 0, 0)["mean"] == stats.mean(weighted(X))
    assert CaveatCode.COHORTS_OVERLAP in codes(found)


def test_values_beyond_the_output_s_bound_refuse_the_column(
    columned: Columned, contrasted: Contrasted
) -> None:
    view = columned([AGE])
    with pytest.raises(TooLarge):
        contrasted(view, [2, 2], one_each(numbers([0.0, 2.0**53]), numbers([1.0, 2.0])))
    with pytest.raises(TooLarge):
        contrasted(
            view,
            [2, 2],
            one_each(numbers([-(2.0**52), -(2.0**52) + 2]), numbers([2.0**52, 2.0**52 - 2])),
        )


def test_a_value_at_the_output_s_bound_is_compared(
    columned: Columned, contrasted: Contrasted
) -> None:
    view = columned([AGE])
    top = float(2**53 - 1)
    found = contrasted(view, [2, 2], one_each(numbers([top, top - 2]), numbers([top - 4, top - 6])))
    assert at(found, 0)["median"] == top - 1
    assert across(found)["test"]["p"] is not None


def test_a_statistic_beyond_the_output_s_bound_refuses_the_column(
    columned: Columned, contrasted: Contrasted
) -> None:
    """Values an ulp apart in two cohorts a unit apart: Welch's t is about −1.1e16 (D336)."""
    view = columned([AGE])
    first = made({5.0: 50, 5.000000000000001: 50})
    second = made({6.0: 50, 6.000000000000001: 50})
    with pytest.raises(TooLarge):
        contrasted(view, [100, 100], one_each(first, second))


def test_a_difference_in_medians_beyond_the_output_s_bound_refuses_the_column(
    columned: Columned, contrasted: Contrasted
) -> None:
    """Every value, mean, deviation and difference in means is within ±(2^53 − 1), and the
    medians 9.2e15 apart are not."""
    view = columned([AGE])
    with pytest.raises(TooLarge):
        contrasted(view, [1, 3], one_each(made({4.6e15: 1}), made({-4.6e15: 2, 4.6e15: 1})))


def test_welch_s_tests_of_values_of_any_size_are_those_of_the_values_scaled(
    columned: Columned, contrasted: Contrasted
) -> None:
    """Welch's statistics are the same whatever power of two scales every value, so subnormal
    values, and spreads whose squares a double does not hold, are tested as their values
    scaled are, where the squares of their errors would be 0 (D338)."""
    tiny = 2.0**-1074
    pairs = [
        ({0.0: 5, tiny: 5}, {0.0: 3, tiny: 2, 2 * tiny: 4}),
        ({0.0: 5, tiny: 5}, {0.0: 5, tiny: 5}),
    ]
    view = columned([AGE])
    for first, second in pairs:
        small = across(contrasted(view, [10, 9], one_each(made(first), made(second))))["test"]
        scaled = [
            made({value / tiny: n for value, n in group.items()}) for group in (first, second)
        ]
        large = across(contrasted(view, [10, 9], one_each(*scaled)))["test"]
        assert small == large
    three = columned([AGE], cohorts=3)
    for scale in (1e-160, 2.0**-1074):
        groups = [{scale * v: 1 for v in (1.0, 2.0, 4.0, 5.0 + i)} for i in range(3)]
        small = across(contrasted(three, [4] * 3, one_each(*map(made, groups))))["test"]
        scaled = [{v / scale: 1 for v in group} for group in groups]
        large = across(contrasted(three, [4] * 3, one_each(*map(made, scaled))))["test"]
        assert small["method"] == "welch_anova"
        for member in ("statistic", "df", "df_denominator", "p"):
            assert math.isclose(small[member], large[member], rel_tol=1e-12)


def test_welch_s_one_way_statistic_beyond_the_output_s_bound_refuses_the_column(
    columned: Columned, contrasted: Contrasted
) -> None:
    """A group spread by the least double beside groups a unit apart weighs 1/5e-324² in Welch's
    one-way test, whose statistic, about 1e32, is beyond ±(2^53 − 1) (D336)."""
    view = columned([AGE], cohorts=3)
    groups = [{0.0: 1, 2.0**-1074: 1}, {1.0: 1, 1.0 + 2.0**-52: 1}, {2.0: 1, 2.0 + 2.0**-51: 1}]
    with pytest.raises(TooLarge):
        contrasted(view, [2, 2, 2], one_each(*map(made, groups)))


def test_welch_s_tests_of_groups_whose_spreads_differ_by_1e155_are_computed(
    columned: Columned, contrasted: Contrasted
) -> None:
    """Round 2's case: Welch's one-way test of a spread of 1e-155 beside spreads of about 1 is
    about 10.7, and Welch's t of a subnormal spread beside one of 1 is −4/√(1/3), about −6.9,
    with 2 degrees of freedom; neither refuses (D338)."""
    three = columned([AGE], cohorts=3)
    groups = [{0.0: 1, 1e-155: 1, 2e-155: 1}, {1.0: 1, 2.0: 1, 4.0: 1}, {1.5: 1, 3.0: 1, 3.5: 1}]
    test = across(contrasted(three, [3, 3, 3], one_each(*map(made, groups))))["test"]
    assert test["method"] == "welch_anova"
    assert math.isclose(test["statistic"], 10.676923076923076, rel_tol=1e-12)
    two = columned([AGE])
    pair = [{0.0: 2, 2.0**-1074: 2}, {3.0: 1, 4.0: 1, 5.0: 1}]
    test = across(contrasted(two, [4, 3], one_each(*map(made, pair))))["test"]
    assert (test["method"], test["df"]) == ("welch_t", pytest.approx(2.0))
    assert math.isclose(test["statistic"], -4 / math.sqrt(1 / 3), rel_tol=1e-12)


def test_a_value_or_a_deviation_beyond_the_output_s_bound_refuses_the_column(
    columned: Columned, contrasted: Contrasted
) -> None:
    """1e16 among ten thousand zeros has its mean, deviation and median in range, and
    ±9e15 a mean and median of 0 and a deviation of 1.3e16: each refuses (D336)."""
    view = columned([AGE])
    for group in ({0.0: 10_000, 1e16: 1}, {-9e15: 1, 9e15: 1}):
        with pytest.raises(TooLarge):
            contrasted(
                view, [sum(group.values()), 3], one_each(made(group), numbers([1.0, 2.0, 3.0]))
            )


def test_more_categories_than_a_result_lists_refuse_before_their_values_are_sorted(
    columned: Columned, contrasted: Contrasted, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The count of undeclared values is taken first, so a million of them refuse in the time
    it takes to count them (D336)."""

    def unsorted(value: str) -> Any:
        raise AssertionError("sorted before the cap was checked")

    monkeypatch.setattr(columns, "utf16_key", unsorted)
    view = columned([TIER])
    values = {f"v{index:03}": 1 for index in range(148)}
    with pytest.raises(TooManyCategories):
        contrasted(view, [148, 1], one_each(made(values), made({"gold": 1})))


def test_more_declared_categories_than_a_result_lists_refuse_before_any_value_is_read(
    columned: Columned, contrasted: Contrasted
) -> None:
    """A column of 10,000 permissible values refuses without listing its cohorts' values, however
    many they hold beside them (D336)."""

    class Unread(dict[Any, int]):
        def __iter__(self) -> Any:
            raise AssertionError("the values were listed")

    code = build.column(
        "customers.code",
        "category",
        permissible_values={"values": [{"value": f"c{index:05}"} for index in range(10_000)]},
    )
    view = columned([{"column": "customers.code"}], extras=[code])
    none = dict.fromkeys(ExclusionReason, 0)
    found = [
        Materialised(
            MappingProxyType(Unread({f"v{cohort}-{index}": 1 for index in range(1_000)})),
            0,
            MappingProxyType(none),
            frozenset(),
        )
        for cohort in (0, 1)
    ]
    with pytest.raises(TooManyCategories):
        contrasted(view, [1_000, 1_000], one_each(*found))


def test_a_column_of_categories_checks_the_deadline_before_each_position_s_counts(
    columned: Columned, contrasted: Contrasted, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Under *k*, any number of undeclared values are one row, counted at each position: the
    deadline is checked before each position's counts (D336)."""
    counted: list[int] = []
    given = columns._row_counts  # pyright: ignore[reportPrivateUsage]

    def row_counts(*args: Any) -> Any:
        counted.append(1)
        return given(*args)

    def deadline(ends: float | None) -> None:
        if counted:
            raise CallerDeadline

    monkeypatch.setattr(columns, "_row_counts", row_counts)
    monkeypatch.setattr(columns, "_deadline", deadline)
    view = columned([TIER], cohorts=3, reference=2, k=3)
    groups = [made({"gold": 4, "silver": 3, f"other-{cohort}": 5}) for cohort in range(3)]
    with pytest.raises(CallerDeadline):
        contrasted(view, [12, 12, 12], one_each(*groups), k=3)
    assert counted == [1]


def test_a_column_of_categories_checks_the_deadline_before_each_position_s_values_are_listed(
    columned: Columned, contrasted: Contrasted, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without *k*, each position's undeclared values are gathered for the rows after a check of
    the deadline (D336): one that passes after the first position's stops the column before the
    second's, and before any count."""
    listing: list[int] = []
    checks: list[int] = []
    counted: list[int] = []
    given_rows = columns._rows  # pyright: ignore[reportPrivateUsage]
    given_counts = columns._row_counts  # pyright: ignore[reportPrivateUsage]

    def rows(*args: Any) -> Any:
        listing.append(1)
        return given_rows(*args)

    def row_counts(*args: Any) -> Any:
        counted.append(1)
        return given_counts(*args)

    def deadline(ends: float | None) -> None:
        if listing:
            checks.append(1)
            if len(checks) == 2:
                raise CallerDeadline

    monkeypatch.setattr(columns, "_rows", rows)
    monkeypatch.setattr(columns, "_row_counts", row_counts)
    monkeypatch.setattr(columns, "_deadline", deadline)
    view = columned([TIER], cohorts=3, reference=2)
    groups = [made({"gold": 4, f"other-{cohort}": 5}) for cohort in range(3)]
    with pytest.raises(CallerDeadline):
        contrasted(view, [9, 9, 9], one_each(*groups))
    assert counted == []


def test_a_column_of_categories_sums_each_position_s_units_a_bounded_number_of_times(
    columned: Columned, contrasted: Contrasted, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each position's units with a value are summed once for its categories, not once a row or
    a difference (D336)."""
    summed: list[int] = []
    given = Materialised.n

    def n(self: Materialised) -> int:
        summed.append(1)
        return cast(int, given.fget(self))  # pyright: ignore[reportOptionalCall]

    monkeypatch.setattr(Materialised, "n", property(n))
    view = columned([TIER], cohorts=6, reference=5)
    groups = [made({"gold": 4, "silver": 3, "bronze": 5}) for _ in range(6)]
    contrasted(view, [12] * 6, one_each(*groups))
    assert len(summed) <= 3 * 6


def test_a_deviation_below_the_least_double_is_zero_variance(
    columned: Columned, contrasted: Contrasted
) -> None:
    view = columned([AGE])
    flat = made({1e-310: 50, 1e-310 + 2.0**-1074: 1})
    found = contrasted(view, [51, 6], one_each(flat, numbers(X)))
    assert at(found, 0)["not_estimable"] == {"/sd": "zero_variance"}
    assert across(found)["test"]["not_estimable"]["/p"] == "zero_variance"


def test_three_cohorts_of_which_two_hold_values_are_tested_by_welch_s_t(
    columned: Columned, contrasted: Contrasted
) -> None:
    view = columned([AGE], cohorts=3)
    found = contrasted(
        view, [6, 7, 2], one_each(numbers(X), numbers(Y), made({}, {"NO_INFORMATION": 2}))
    )
    compared = across(found)
    assert (compared["test"]["method"], compared["test"]["positions"]) == ("welch_t", [0, 1])
    assert compared["secondary"]["method"] == "mann_whitney"


def test_overlap_comes_before_suppression_for_numbers(
    columned: Columned, contrasted: Contrasted
) -> None:
    view = columned([AGE], overlap=True, k=3)
    found = contrasted(view, [6, 7], one_each(numbers(X), numbers(Y)), k=3, overlap=True)
    compared = across(found)
    for test in (compared["test"], compared["secondary"]):
        assert test["not_estimable"]["/p"] == "overlapping_cohorts"
    assert {e["not_estimable"]["/estimate"] for e in compared["effects"]} == {"overlapping_cohorts"}


def test_a_table_whose_least_expected_count_is_5_carries_no_small_n(
    columned: Columned, contrasted: Contrasted
) -> None:
    """R's chisq.test warns below an expected 5 only."""
    view = columned([TIER])
    found = contrasted(
        view,
        [15, 15],
        one_each(
            made({"gold": 5, "silver": 6, "bronze": 4}), made({"gold": 5, "silver": 4, "bronze": 6})
        ),
    )
    assert across(found)["test"]["method"] == "chi_squared"
    assert CaveatCode.SMALL_N not in codes(found)
    fewer = contrasted(
        view,
        [14, 15],
        one_each(
            made({"gold": 4, "silver": 6, "bronze": 4}), made({"gold": 5, "silver": 4, "bronze": 6})
        ),
    )
    assert CaveatCode.SMALL_N in codes(fewer)


def test_under_k_a_suppressed_test_of_two_cohorts_and_two_rows_names_fisher_s_test(
    columned: Columned, contrasted: Contrasted
) -> None:
    flag = build.column("customers.vip", "boolean")
    view = columned([{"column": "customers.vip"}], extras=[flag], k=3)
    found = contrasted(view, [6, 2], one_each(made({True: 3, False: 3}), made({False: 2})), k=3)
    test = across(found)["test"]
    assert (test["method"], test["not_estimable"]) == ("fisher_exact", {"/p": "suppressed"})


@pytest.mark.parametrize(
    ("passes", "last"),
    [
        (("moments", 1), ["moments"]),
        (("moments", 2), ["moments", "moments"]),
        (("mann_whitney", 1), ["moments", "moments", "mann_whitney"]),
        (("bootstrap_medians", 1), ["moments", "moments", "mann_whitney", "bootstrap_medians"]),
        (
            ("bootstrap_medians", 2),
            ["moments", "moments", "mann_whitney", "bootstrap_medians", "bootstrap_medians"],
        ),
    ],
)
def test_a_deadline_that_passes_within_a_column_stops_it_before_its_next_step(
    columned: Columned,
    contrasted: Contrasted,
    monkeypatch: pytest.MonkeyPatch,
    passes: tuple[str, int],
    last: list[str],
) -> None:
    """Each cohort's moments, the tests and each cohort's bootstrap is a step, and the
    call's deadline is checked before each, and before each column (D336): a deadline that
    passes in one step lets no other start, the next column's included."""
    started: list[str] = []
    passed = [False]

    def watched(name: str) -> None:
        given = getattr(stats, name)

        def step(*args: Any, **kwargs: Any) -> Any:
            started.append(name)
            if (name, started.count(name)) == passes:
                passed[0] = True
            return given(*args, **kwargs)

        monkeypatch.setattr(stats, name, step)

    for name in ("moments", "mann_whitney", "bootstrap_medians"):
        watched(name)
    given_categories = columns._categories  # pyright: ignore[reportPrivateUsage]

    def categories(*args: Any, **kwargs: Any) -> Any:
        started.append("categories")
        return given_categories(*args, **kwargs)

    def deadline(ends: float | None) -> None:
        if passed[0]:
            raise CallerDeadline

    monkeypatch.setattr(columns, "_categories", categories)
    monkeypatch.setattr(columns, "_deadline", deadline)
    view = columned([AGE, TIER])
    none = dict.fromkeys(ExclusionReason, 0)
    with pytest.raises(CallerDeadline):
        contrasted(
            view,
            [6, 7],
            [
                ([numbers(X), made({"gold": 6})], Joint(6, 0, none)),
                ([numbers(Y), made({"gold": 7})], Joint(7, 0, none)),
            ],
        )
    assert started == last


def test_a_deadline_that_passes_while_a_cohort_s_values_are_sorted_stops_before_their_moments(
    columned: Columned, contrasted: Contrasted, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sorting a cohort's values is a step of its own: the deadline is checked after it, before
    the cohort's moments (D336)."""
    passed = [False]
    started: list[str] = []
    given_weighted = columns._weighted  # pyright: ignore[reportPrivateUsage]
    given_moments = stats.moments

    def weighted_values(*args: Any) -> Any:
        found = given_weighted(*args)
        passed[0] = True
        return found

    def moments(*args: Any) -> Any:
        started.append("moments")
        return given_moments(*args)

    def deadline(ends: float | None) -> None:
        if passed[0]:
            raise CallerDeadline

    monkeypatch.setattr(columns, "_weighted", weighted_values)
    monkeypatch.setattr(stats, "moments", moments)
    monkeypatch.setattr(columns, "_deadline", deadline)
    with pytest.raises(CallerDeadline):
        contrasted(columned([AGE]), [6, 7], one_each(numbers(X), numbers(Y)))
    assert started == []


# --- The bootstrap --------------------------------------------------------------------------------


def test_the_bootstrap_is_the_same_on_every_run_and_seeded_from_the_computation_id(
    columned: Columned, contrasted: Contrasted
) -> None:
    view = columned([AGE])
    first = contrasted(view, [6, 7], one_each(numbers(X), numbers(Y)))
    again = contrasted(view, [6, 7], one_each(numbers(X), numbers(Y)))
    assert across(first)["effects"][1] == across(again)["effects"][1]
    other = columned([AGE], level=0.9)
    assert other.identity.computation_id != view.identity.computation_id
    found = stats.bootstrap_medians(weighted(Y), columns.seed(view.identity.computation_id, 0, 1))
    reference = stats.bootstrap_medians(
        weighted(X), columns.seed(view.identity.computation_id, 0, 0)
    )
    low, high = stats.percentile_interval(
        [a - b for a, b in zip(found, reference, strict=True)], 0.95
    )
    ci = across(first)["effects"][1]["ci"]
    assert (ci["low"], ci["high"]) == (low, high)


def test_a_floor_does_not_change_the_seed_of_a_view(columned: Columned) -> None:
    assert columned([AGE]).identity.computation_id == columned([AGE], k=3).identity.computation_id


def test_seeds_differ_by_column_and_by_position() -> None:
    seeds = {columns.seed("drv:x", column, position) for column in (0, 1) for position in (0, 1)}
    assert len(seeds) == 4


# --- Caveats, charts and readback -----------------------------------------------------------------


def test_a_unit_excluded_for_a_reason_other_than_not_applicable_raises_unknown_excluded(
    columned: Columned, contrasted: Contrasted
) -> None:
    view = columned([AGE])
    quiet = contrasted(view, [7, 7], one_each(made({1: 6}, {"NOT_APPLICABLE": 1}), numbers(Y)))
    assert CaveatCode.UNKNOWN_EXCLUDED not in codes(quiet)
    loud = contrasted(view, [7, 7], one_each(made({1: 6}, {"NO_INFORMATION": 1}), numbers(Y)))
    assert CaveatCode.UNKNOWN_EXCLUDED in codes(loud)
    assert quiet.analysed[0].excluded[ExclusionReason.NOT_APPLICABLE] == 1


def test_under_k_unknown_excluded_is_carried_whether_or_not_a_unit_was_excluded(
    columned: Columned, contrasted: Contrasted
) -> None:
    view = columned([AGE], k=3)
    found = contrasted(view, [6, 7], one_each(numbers(X), numbers(Y)), k=3)
    [unknown] = [c for c in found.caveats if c.code == CaveatCode.UNKNOWN_EXCLUDED]
    assert unknown.affects == ["/analysed", "/population"]


def test_charts_draw_each_category_and_each_difference_shown(
    columned: Columned, contrasted: Contrasted
) -> None:
    view = columned([TIER, AGE])
    none = dict.fromkeys(ExclusionReason, 0)
    found = contrasted(
        view,
        [10, 7],
        [
            ([made({"gold": 6, "silver": 4}), numbers(X)], Joint(10, 0, none)),
            ([made({"gold": 7}), numbers(Y)], Joint(7, 0, none)),
        ],
    )
    tiers, ages = cast(list[Any], columns_charts(found.values, ["young", "old"]))
    assert len(tiers["data"]["values"]) == 6
    rows = ages["data"]["values"]
    assert [(row["measure"], row["cohort"]) for row in rows] == [
        ("mean_difference", "old"),
        ("median_difference", "old"),
    ]
    assert "$schema" not in json.dumps([tiers, ages])
    single = contrasted(columned([AGE]), [6, 1], one_each(numbers(X), numbers([4.0])))
    [charted] = cast(list[Any], columns_charts(single.values, ["young", "old"]))
    assert [row["measure"] for row in charted["data"]["values"]] == ["median_difference"]


def test_the_shop_s_comparison_reads_its_columns_through_the_reference_evaluator(
    analyse: Analyse, shop: Shop
) -> None:
    [found] = analyse(shop_document(TIER, AGE, WEB, reference="old"), shop())
    result = found.result
    assert result.derivation.analysis.id == "compare.columns"
    assert [cohort.reference for cohort in result.cohorts] == [False, True]
    tier, age, web = result.values.view["columns"]
    assert tier["kind"] == "categories"
    assert age["test"]["method"] == "welch_t"
    assert [row["values"] for row in web["categories"]] == [[{"data": "false"}], [{"data": "true"}]]
    assert len(result.charts) == 3
    said = "".join(
        s.model_dump().get("text", s.model_dump().get("data", "")) for s in result.readback.view
    )
    assert "reference being the cohort at position 1" in said
    assert "young" not in said


def test_the_readback_names_cohorts_by_position_and_states_each_column(
    check: Check, shop: Shop
) -> None:
    found = check(shop_document(TIER, AGE), shop())
    [view] = found.views
    said = [segment.model_dump() for segment in view.readback()]
    assert {"data": "0.95"} in said
    assert any("Column 1: " in part.get("text", "") for part in said)


# --- Disclosure -----------------------------------------------------------------------------------


def test_under_k_a_number_shows_only_its_units(columned: Columned, contrasted: Contrasted) -> None:
    view = columned([AGE], k=3)
    found = contrasted(view, [6, 7], one_each(numbers(X), numbers(Y)), k=3)
    assert at(found, 0) == {
        "kind": "numbers",
        "n": 6,
        "mean": None,
        "sd": None,
        "median": None,
        "not_estimable": dict.fromkeys(("/mean", "/sd", "/median"), "suppressed"),
    }
    compared = across(found)
    assert compared["test"] == {
        "method": "welch_t",
        "positions": [],
        "statistic": None,
        "p": None,
        "not_estimable": {"/p": "suppressed", "/statistic": "suppressed"},
    }
    assert compared["secondary"]["not_estimable"]["/p"] == "suppressed"
    assert all(set(e["not_estimable"].values()) == {"suppressed"} for e in compared["effects"])
    assert found.values.view.family.model_dump() == {"tests": 0, "not_computed": 0, "suppressed": 1}
    assert cast(Any, columns_charts(found.values, ["a", "b"]))[0]["data"]["values"] == []


def test_under_k_categories_are_merged_as_summary_distribution_merges_them(
    columned: Columned,
    contrasted: Contrasted,
    distributed: Distributed,
    summarised: Summarised,
) -> None:
    view = columned([TIER], k=3)
    given = [
        made({"gold": 2, "silver": 5, "bronze": 4}),
        made({"gold": 6, "silver": 1, "bronze": 5}),
    ]
    found = contrasted(view, [11, 12], one_each(*given), k=3)
    distribution = summarised(distributed([TIER], cohorts=2, k=3), [11, 12], one_each(*given), k=3)
    for position in (0, 1):
        assert (
            at(found, position)["categories"]
            == distribution.values.positions[position]
            .columns[0]
            .model_dump(mode="json")["categories"]
        )
    rows = across(found)["categories"]
    assert [[v["data"] for v in row["values"]] for row in rows] == [
        ["gold", "silver", "bronze"],
        [],
    ]
    assert rows[1]["other_values"] is True
    assert across(found)["test"]["not_estimable"]["/p"] == "degenerate_table"


def test_under_k_the_table_is_the_coarsest_grouping_of_the_rows_each_position_shows(
    columned: Columned, contrasted: Contrasted
) -> None:
    view = columned([TIER], k=3)
    given = [
        made({"gold": 2, "silver": 5, "bronze": 4}),
        made({"gold": 6, "silver": 5, "bronze": 1}),
    ]
    found = contrasted(view, [11, 12], one_each(*given), k=3)
    assert [[v["data"] for v in c["values"]] for c in at(found, 0)["categories"]] == [
        ["gold", "silver"],
        ["bronze"],
        [],
    ]
    assert [[v["data"] for v in c["values"]] for c in at(found, 1)["categories"]] == [
        ["gold"],
        ["silver", "bronze"],
        [],
    ]
    compared = across(found)
    assert [[v["data"] for v in row["values"]] for row in compared["categories"]] == [
        ["gold", "silver", "bronze"],
        [],
    ]
    other = contrasted(
        view, [11, 12], one_each(given[0], made({"gold": 1, "silver": 6, "bronze": 5})), k=3
    )
    table = across(other)
    assert [[v["data"] for v in row["values"]] for row in table["categories"]] == [
        ["gold", "silver"],
        ["bronze"],
        [],
    ]
    assert table["test"]["p"] == pytest.approx(stats.fisher(((7, 4), (7, 5))), rel=1e-15)
    [effect] = table["categories"][0]["effects"]
    assert effect["estimate"] == pytest.approx(7 / 12 - 7 / 11)


def test_under_k_a_position_whose_split_is_hidden_suppresses_the_test_and_its_effects(
    columned: Columned, contrasted: Contrasted
) -> None:
    view = columned([TIER], cohorts=3, k=3)
    given = [
        made({"gold": 6, "silver": 4}),
        made({"gold": 5}, {"NO_INFORMATION": 2}),
        made({"gold": 3, "silver": 7}),
    ]
    found = contrasted(view, [10, 7, 10], one_each(*given), k=3)
    assert at(found, 1)["categories"] is None
    compared = across(found)
    assert compared["test"]["not_estimable"]["/p"] == "suppressed"
    assert compared["test"]["positions"] == []
    first, second = compared["categories"][0]["effects"]
    assert first["not_estimable"]["/estimate"] == "suppressed"
    assert second["estimate"] == pytest.approx(0.3 - 0.6)
    assert found.values.view.family.model_dump() == {"tests": 0, "not_computed": 0, "suppressed": 1}


def test_under_k_nothing_of_a_position_whose_cohort_count_is_suppressed_is_shown(
    columned: Columned, contrasted: Contrasted
) -> None:
    view = columned([TIER, AGE], k=3)
    none = dict.fromkeys(ExclusionReason, 0)
    found = contrasted(
        view,
        [2, 10],
        [
            ([made({"gold": 2}), numbers([1.0, 2.0])], Joint(2, 0, none)),
            ([made({"gold": 10}), numbers([1.0] * 10)], Joint(10, 0, none)),
        ],
        k=3,
    )
    assert found.population[0].n_true is None
    assert at(found, 0, 0)["categories"] is None
    assert at(found, 0, 1)["n"] is None
    assert found.analysed[0].n is None


# --- Phase 2 --------------------------------------------------------------------------------------


def test_compare_columns_needs_its_cohorts_listed(check: Check, shop: Shop) -> None:
    written = shop_document(AGE)
    del written["views"][0]["cohorts"]
    [refusal] = check(written, shop()).refusals
    assert (refusal.code, refusal.path) == (RefusalCode.MISSING_MEMBER, "/views/0/cohorts")


def test_bins_are_refused_since_compare_columns_draws_no_histogram(
    check: Check, shop: Shop
) -> None:
    found = check(shop_document(AGE, {"column": "customers.age", "bins": [0, 50, 100]}), shop())
    [refusal] = found.refusals
    assert (refusal.code, refusal.path) == (
        RefusalCode.INVALID_VALUE,
        "/views/0/params/columns/1/bins",
    )
    assert found.views == []


def test_a_column_the_analysis_does_not_take_is_refused_where_it_is_written(
    check: Check, shop: Shop
) -> None:
    [refusal] = check(shop_document({"column": "customers.customer_id"}), shop()).refusals
    assert (refusal.code, refusal.path) == (
        RefusalCode.NOT_SUPPORTED,
        "/views/0/params/columns/0/column",
    )


def test_a_comparison_counts_no_rows_and_takes_an_aggregate_of_categories_of_several_rows(
    check: Check, shop: Shop
) -> None:
    """``compare.columns`` compares units, one value each (§9.2), so neither refusal names a part
    to come (D335)."""
    rows = check(
        shop_document({"column": "orders.order_id", "aggregate": "count", "count": "rows"}), shop()
    )
    [refusal] = rows.refusals
    assert (refusal.code, refusal.path) == (
        RefusalCode.INVALID_VALUE,
        "/views/0/params/columns/0/count",
    )
    several = check(shop_document({"column": "orders.channel"}), shop())
    [refusal] = several.refusals
    assert (refusal.code, refusal.path) == (
        RefusalCode.AGGREGATE_REQUIRED,
        "/views/0/params/columns/0/column",
    )
    assert [one.text for one in refusal.alternatives or []] == ["some", "every", "max", "min"]
    for found in (rows, several):
        assert "M3.2" not in json.dumps([s.model_dump() for s in found.refusals[0].message])


def test_a_view_s_canonical_parameters_are_its_variables_and_level(
    check: Check, shop: Shop
) -> None:
    found = check(shop_document(AGE, {**TIER, "via": []}), shop())
    [view] = found.views
    assert view.identity.params == {
        "columns": [{"column": "customers.age"}, {"column": "customers.tier"}],
        "level": 0.95,
    }
    assert view.identity.reference == 0
    assert view.identity.overlap is False


def test_the_level_is_a_level_below_one(check: Check, shop: Shop) -> None:
    written = shop_document(AGE)
    written["views"][0]["params"]["level"] = 1
    [refusal] = check(written, shop()).refusals
    assert refusal.path == "/views/0/params/level"


def test_the_entry_declares_its_randomness_and_its_methods() -> None:
    fields = columns.ENTRY.fields
    assert fields.randomness is not None
    assert fields.randomness.model_dump() == {"seeded": True, "replicates": 2000}
    assert {"welch_t", "bootstrap_percentile", "mann_whitney"} <= set(fields.methods)
    assert fields.uses_reference
    assert fields.assumes_independent_groups


def test_a_comparison_stops_once_the_call_s_deadline_has_passed(
    columned: Columned, contrasted: Contrasted
) -> None:
    view = columned([AGE])
    with pytest.raises(CallerDeadline):
        contrasted(view, [6, 7], one_each(numbers(X), numbers(Y)), ends=time.monotonic() - 1)


@pytest.mark.parametrize(("cohorts", "test"), [(2, "mann_whitney"), (3, "kruskal_wallis")])
def test_the_call_s_deadline_reaches_the_rank_tests(
    columned: Columned,
    contrasted: Contrasted,
    monkeypatch: pytest.MonkeyPatch,
    cohorts: int,
    test: str,
) -> None:
    """A rank test checks the deadline as it ranks, so the call's own reaches it (D336)."""
    given = getattr(stats, test)
    reached: list[float | None] = []

    def ranked(*args: Any) -> Any:
        reached.append(args[-1])
        return given(*args)

    monkeypatch.setattr(stats, test, ranked)
    ends = time.monotonic() + 3600
    view = columned([AGE], cohorts=cohorts, reference=cohorts - 1)
    groups = [numbers(X), numbers(Y), numbers(Z)][:cohorts]
    contrasted(view, [6, 7, 5][:cohorts], one_each(*groups), ends=ends)
    assert reached == [ends]


@pytest.mark.parametrize("step", ["checked_sort", "moments", "median", "bootstrap_medians"])
def test_the_call_s_deadline_reaches_every_pass_over_a_cohort_s_values(
    columned: Columned, contrasted: Contrasted, monkeypatch: pytest.MonkeyPatch, step: str
) -> None:
    """A cohort's sort, moments, median and bootstrap check the deadline every 65,536 values,
    so the call's own reaches each (D336)."""
    given = getattr(stats, step)
    reached: list[float | None] = []

    def watched(*args: Any, **kwargs: Any) -> Any:
        reached.append(kwargs["ends"] if "ends" in kwargs else args[1])
        return given(*args, **kwargs)

    monkeypatch.setattr(stats, step, watched)
    ends = time.monotonic() + 3600
    contrasted(columned([AGE]), [6, 7], one_each(numbers(X), numbers(Y)), ends=ends)
    assert set(reached) == {ends}


@pytest.mark.parametrize("k", [None, 3])
def test_a_column_of_categories_stops_at_a_deadline_the_call_gives(
    columned: Columned, contrasted: Contrasted, monkeypatch: pytest.MonkeyPatch, k: int | None
) -> None:
    """The call's own deadline reaches a column of categories: once it passes as the rows are
    made, the first position's values are neither listed (without *k*) nor counted (D336)."""
    started: list[str] = []
    given_rows = columns._rows  # pyright: ignore[reportPrivateUsage]
    given_counts = columns._row_counts  # pyright: ignore[reportPrivateUsage]

    def rows(*args: Any) -> Any:
        started.append("rows")
        found = given_rows(*args)
        started.append("listed")
        return found

    def row_counts(*args: Any) -> Any:
        started.append("counted")
        return given_counts(*args)

    monkeypatch.setattr(columns, "_rows", rows)
    monkeypatch.setattr(columns, "_row_counts", row_counts)
    monkeypatch.setattr(columns.time, "monotonic", lambda: 2.0 if started else 0.0)
    view = columned([TIER], k=k)
    groups = [made({"gold": 4, "silver": 3, f"other-{cohort}": 5}) for cohort in range(2)]
    with pytest.raises(CallerDeadline):
        contrasted(view, [12, 12], one_each(*groups), k=k, ends=1.0)
    assert started == (["rows"] if k is None else ["rows", "listed"])


@pytest.mark.parametrize("k", [None, 3])
def test_a_column_of_categories_looks_its_values_up_in_a_set_of_the_declared_ones(
    columned: Columned, contrasted: Contrasted, monkeypatch: pytest.MonkeyPatch, k: int | None
) -> None:
    """Each value is looked up among the declared ones in a set, never by a scan of their list,
    which cost a minute at 10,000 declared values (D336)."""

    class Unscanned(list[Any]):
        def __contains__(self, value: object) -> bool:
            raise AssertionError("the declared values were scanned")

    given = columns.declared_categories
    monkeypatch.setattr(columns, "declared_categories", lambda variable: Unscanned(given(variable)))
    view = columned([TIER], k=k)
    groups = [made({"gold": 4, "silver": 3, f"other-{cohort}": 5}) for cohort in range(2)]
    contrasted(view, [12, 12], one_each(*groups), k=k)


def test_a_column_of_exactly_as_many_declared_categories_as_a_result_lists_is_compared(
    columned: Columned, contrasted: Contrasted
) -> None:
    code = build.column(
        "customers.code",
        "category",
        permissible_values={"values": [{"value": f"c{index:03}"} for index in range(150)]},
    )
    view = columned([{"column": "customers.code"}], extras=[code])
    found = contrasted(view, [3, 2], one_each(made({"c000": 3}), made({"c149": 2})))
    assert len(at(found, 0)["categories"]) == 150


def test_a_boolean_column_s_categories_are_false_and_true(
    columned: Columned, contrasted: Contrasted
) -> None:
    flag = build.column("customers.vip", "boolean")
    view = columned([{"column": "customers.vip"}], extras=[flag])
    found = contrasted(view, [4, 4], one_each(made({True: 3, False: 1}), made({False: 4})))
    assert [row["values"][0]["data"] for row in across(found)["categories"]] == ["false", "true"]
    assert across(found)["test"]["method"] == "fisher_exact"
    assert math.isclose(across(found)["test"]["p"], stats.fisher(((1, 3), (4, 0))))

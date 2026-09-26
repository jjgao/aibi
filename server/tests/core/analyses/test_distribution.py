"""``summary.distribution``: its values, estimability, caveats, disclosure and phase 2 (SPEC §7.6,
§8.1–§8.4, §9.2, §9.5; D325, D328, D329), over variables materialised as given and over the shop
evaluated by the reference evaluator."""

import json
import math
import time
from collections.abc import Callable, Mapping
from types import MappingProxyType
from typing import Any

import pytest

from aibi.core.analyses import stats
from aibi.core.analyses.charts import distribution_charts
from aibi.core.analyses.distribution import TooLarge, TooManyCategories
from aibi.core.engine import build
from aibi.core.engine.variables import Joint, Materialised
from aibi.core.engine.worker import CallerDeadline
from aibi.core.schema.caveats import CaveatCode
from aibi.core.schema.refusals import RefusalCode
from aibi.core.schema.semantics import ExclusionReason

Distributed = Callable[..., Any]
Summarised = Callable[..., Any]
Analyse = Callable[..., list[Any]]
Check = Callable[..., Any]
Shop = Callable[..., Any]

STATISTICS = ("mean", "sd", "median", "q1", "q3", "min", "max")
TIER = {"column": "customers.tier"}
AGE = {"column": "customers.age"}
AMOUNT = {"column": "orders.amount", "aggregate": "mean"}
ORDERS = {"column": "orders.order_id", "aggregate": "count", "bins": [0, 1, 2, 3]}
WEB = {"column": "orders.channel", "aggregate": "some", "values": ["web"]}
YOUNG = {"kind": "value", "column": "customers.age", "range": {"lt": 45}}
OLD = {"kind": "value", "column": "customers.age", "range": {"gte": 45}}


def made(
    values: Mapping[Any, int],
    excluded: Mapping[str, int] | None = None,
    units: int | None = None,
) -> Materialised:
    """A variable materialised over a cohort: its values, and its units excluded by reason
    (each unit under one reason unless ``units`` says how many units they are)."""
    by_reason = dict.fromkeys(ExclusionReason, 0)
    by_reason.update({ExclusionReason(reason): n for reason, n in (excluded or {}).items()})
    return Materialised(
        MappingProxyType(dict(values)),
        sum(by_reason.values()) if units is None else units,
        MappingProxyType(by_reason),
        frozenset(),
    )


def column(outcome: Any, position: int, index: int) -> dict[str, Any]:
    return outcome.values.positions[position].columns[index].model_dump(mode="json")


def codes(outcome: Any) -> set[str]:
    return {caveat.code for caveat in outcome.caveats}


def shop_document(*columns: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "aibi": "1",
        "dataset": "d",
        "unit": "customers",
        "cohorts": {"young": {"all": [YOUNG]}, "old": {"all": [OLD]}},
        "views": [
            {
                "analysis": "summary.distribution",
                "cohorts": ["young", "old"],
                "params": {"columns": [dict(given) for given in columns]},
            }
        ],
    }


# --- Values ------------------------------------------------------------------------------------


def test_categories_are_listed_as_declared_zeros_included_then_the_others_in_canonical_order(
    distributed: Distributed, summarised: Summarised
) -> None:
    view = distributed([TIER])
    found = summarised(view, [10], [([made({"silver": 4, "gold": 2, "zinc": 1, "Zinc": 1})], None)])
    categories = column(found, 0, 0)["categories"]
    assert [c["values"][0]["data"] for c in categories] == [
        "gold",
        "silver",
        "bronze",
        "Zinc",
        "zinc",
    ]
    assert [c["proportion"]["numerator"] for c in categories] == [2, 4, 0, 1, 1]
    assert {c["proportion"]["denominator"] for c in categories} == {8}
    assert categories[1]["proportion"]["estimate"] == 0.5
    assert categories[0]["proportion"]["denominator_definition"] == {
        "position": 0,
        "predicate": None,
        "counts": "known",
    }


def test_a_question_s_categories_are_false_and_true(
    distributed: Distributed, summarised: Summarised
) -> None:
    view = distributed([WEB])
    found = summarised(view, [5], [([made({True: 3, False: 2})], None)])
    categories = column(found, 0, 0)["categories"]
    assert [(c["values"][0]["data"], c["proportion"]["numerator"]) for c in categories] == [
        ("false", 2),
        ("true", 3),
    ]


def test_numbers_have_n_mean_sd_quartiles_extremes_and_a_histogram_as_r_has_them(
    distributed: Distributed, summarised: Summarised
) -> None:
    view = distributed([{**AGE, "bins": [20, 30, 40]}])
    values = {20: 1, 27: 1, 34: 2, 41: 1, 48: 1}
    found = summarised(view, [7], [([made(values, {"NO_INFORMATION": 1})], None)])
    ages = column(found, 0, 0)
    weighted = sorted(values.items())
    centre = stats.mean(weighted)
    assert (ages["n"], ages["mean"], ages["sd"]) == (6, centre, stats.sd(weighted, centre))
    assert (ages["q1"], ages["median"], ages["q3"]) == tuple(
        stats.quantile(weighted, p) for p in (0.25, 0.5, 0.75)
    )
    assert (ages["min"], ages["max"]) == (20.0, 48.0)
    histogram = ages["histogram"]
    assert histogram["edges_from"] == "params"
    assert [(b["low"], b["high"], b["count"]) for b in histogram["bins"]] == [
        (None, 20, 0),
        (20, 30, 2),
        (30, 40, 2),
        (40, None, 2),
    ]
    assert [(b["includes_low"], b["includes_high"]) for b in histogram["bins"]] == [
        (False, False),
        (True, False),
        (True, True),
        (False, False),
    ]
    assert "q1_bin" not in ages


def test_a_histogram_divides_the_declared_range_else_without_k_the_data(
    distributed: Distributed, summarised: Summarised
) -> None:
    ranged = column(summarised(distributed([AGE]), [2], [([made({30: 1, 58: 1})], None)]), 0, 0)
    assert ranged["histogram"]["edges_from"] == "range"
    assert [b["low"] for b in ranged["histogram"]["bins"]][1:3] == [18, 26]
    assert len(ranged["histogram"]["bins"]) == 12
    view = distributed([{"column": "orders.order_id", "aggregate": "count"}])
    counts = column(summarised(view, [3], [([made({1: 2, 3: 1})], None)]), 0, 0)
    assert counts["histogram"]["edges_from"] == "data"
    bins = counts["histogram"]["bins"]
    assert (bins[1]["low"], bins[-2]["high"], bins[-2]["includes_high"]) == (1, 3, True)
    assert sum(b["count"] for b in bins) == 3
    single = column(summarised(view, [2], [([made({4: 2})], None)]), 0, 0)
    assert [(b["low"], b["high"], b["count"]) for b in single["histogram"]["bins"]] == [
        (None, 4, 0),
        (4, 4, 2),
        (4, None, 0),
    ]


# --- Estimability ------------------------------------------------------------------------------


def test_with_no_value_nothing_is_estimable_but_the_histogram_of_a_range(
    distributed: Distributed, summarised: Summarised
) -> None:
    view = distributed([AGE, {"column": "orders.order_id", "aggregate": "count"}, TIER])
    empty = made({}, {"NOT_ASSESSED": 3})
    found = summarised(view, [3], [([empty, empty, empty], Joint(0, 3, empty.excluded))])
    ages, counts, tiers = (column(found, 0, index) for index in range(3))
    assert ages["n"] == 0
    assert ages["not_estimable"]["/mean"] == "no_units"
    assert sum(b["count"] for b in ages["histogram"]["bins"]) == 0
    assert counts["not_estimable"]["/histogram"] == "no_units"
    assert {c["proportion"]["not_estimable"]["/estimate"] for c in tiers["categories"]} == {
        "no_units"
    }
    assert CaveatCode.UNKNOWN_EXCLUDED in codes(found)


def test_values_all_the_same_have_no_standard_deviation(
    distributed: Distributed, summarised: Summarised
) -> None:
    found = column(summarised(distributed([AGE]), [4], [([made({30: 4})], None)]), 0, 0)
    assert found["mean"] == 30.0
    assert found["sd"] is None
    assert found["not_estimable"] == {"/sd": "zero_variance"}


def test_several_variables_count_the_units_some_one_has_a_value_for(
    distributed: Distributed, summarised: Summarised
) -> None:
    view = distributed([TIER, AGE])
    tiers = made({"gold": 3}, {"NOT_ASSESSED": 2})
    ages = made({30: 4}, {"NO_INFORMATION": 1})
    by_reason = dict.fromkeys(ExclusionReason, 0)
    by_reason.update({ExclusionReason.NOT_ASSESSED: 1, ExclusionReason.NO_INFORMATION: 1})
    found = summarised(view, [5], [([tiers, ages], Joint(4, 1, by_reason))])
    [analysed] = found.analysed
    assert (analysed.n, analysed.excluded_units) == (4, 1)
    assert analysed.variables is not None
    assert [(v.n, v.excluded_units) for v in analysed.variables] == [(3, 2), (4, 1)]


def test_the_shop_s_distribution_reads_its_columns_through_the_reference_evaluator(
    analyse: Analyse, shop: Shop
) -> None:
    [analysed] = analyse(shop_document(TIER, AGE, AMOUNT, ORDERS, WEB), shop(extended=True))
    outcome = analysed.outcome
    tiers = column(outcome, 0, 0)
    assert sum(c["proportion"]["numerator"] for c in tiers["categories"]) == 10
    assert outcome.analysed[0].variables[0].excluded["NOT_ASSESSED"] == 2
    amounts = column(outcome, 0, 2)
    assert amounts["n"] == 10
    assert outcome.analysed[0].variables[2].excluded["NO_INFORMATION"] == 2
    assert column(outcome, 0, 3)["histogram"]["edges_from"] == "params"
    assert [c["values"][0]["data"] for c in column(outcome, 1, 4)["categories"]] == [
        "false",
        "true",
    ]
    result = analysed.result
    assert len(result.charts) == 5
    assert result.derivation.analysis.id == "summary.distribution"
    assert CaveatCode.UNCONFIRMED_SEMANTICS in {caveat.code for caveat in result.caveats}


def test_chart_labels_quote_each_category_and_name_bins_by_their_intervals(
    distributed: Distributed, summarised: Summarised
) -> None:
    categories = distributed([TIER], k=3)
    counts = {"gold": 3, "silver": 1, "bronze": 3, "a, b": 3, "other values": 3}
    found = summarised(categories, [13], [([made(counts)], None)], k=3)
    charts: Any = distribution_charts(found.values, ["all"])
    [chart] = charts
    assert [row["category"] for row in chart["data"]["values"]] == [
        '"gold"',
        '"silver", "bronze"',
        "other values",
    ]
    numbers = distributed([{**AGE, "bins": [0, 30, 60]}], cohorts=2, k=3)
    ages = [([made({10: 3, 40: 3, 70: 3})], None), ([made({10: 3, 40: 1, 70: 3})], None)]
    drawn: Any = distribution_charts(summarised(numbers, [9, 7], ages, k=3).values, ["a", "b"])
    [histogram] = drawn
    shown = [(row["cohort"], row["bin"]) for row in histogram["data"]["values"]]
    assert shown == [
        ("a", "(-∞, 0)"),
        ("a", "[0, 30)"),
        ("a", "[30, 60]"),
        ("a", "(60, ∞)"),
        ("b", "(-∞, 0)"),
        ("b", "[0, 30)"),
        ("b", "[30, ∞)"),
    ]
    assert histogram["spec"]["encoding"]["x"]["sort"] == [
        "(-∞, 0)",
        "[0, 30)",
        "[30, 60]",
        "[30, ∞)",
        "(60, ∞)",
    ]


# --- Disclosure --------------------------------------------------------------------------------


def test_a_split_with_a_small_part_shows_nothing_of_the_variable(
    distributed: Distributed, summarised: Summarised
) -> None:
    view = distributed([TIER], k=3)
    found = summarised(view, [10], [([made({"gold": 8}, {"NOT_ASSESSED": 2})], None)], k=3)
    assert column(found, 0, 0)["categories"] is None
    [analysed] = found.analysed
    assert (analysed.n, analysed.excluded_units, analysed.excluded) == (None, None, None)
    assert CaveatCode.SUPPRESSED in codes(found)


def test_excluded_units_by_reason_are_null_when_any_count_is_small(
    distributed: Distributed, summarised: Summarised
) -> None:
    view = distributed([TIER], k=3)
    given = made({"gold": 5, "silver": 5}, {"NOT_ASSESSED": 4, "NO_INFORMATION": 1})
    [analysed] = summarised(view, [15], [([given], None)], k=3).analysed
    assert (analysed.n, analysed.excluded_units, analysed.excluded) == (10, 5, None)


def test_small_categories_are_merged_with_the_next_in_their_listed_order(
    distributed: Distributed, summarised: Summarised
) -> None:
    view = distributed([TIER], k=3)
    values = {"gold": 6, "silver": 2, "bronze": 2, "tin": 1}
    found = column(summarised(view, [11], [([made(values)], None)], k=3), 0, 0)
    rows = [
        ([v["data"] for v in c["values"]], c.get("other_values"), c["proportion"]["numerator"])
        for c in found["categories"]
    ]
    assert rows == [(["gold"], None, 6), (["silver", "bronze"], True, 5)]
    assert {c["proportion"]["denominator"] for c in found["categories"]} == {11}


def test_a_small_last_category_is_merged_with_the_one_before_it(
    distributed: Distributed, summarised: Summarised
) -> None:
    view = distributed([TIER], k=3)
    values = {"gold": 4, "bronze": 5, "tin": 1}
    found = column(summarised(view, [10], [([made(values)], None)], k=3), 0, 0)
    rows = [
        ([v["data"] for v in c["values"]], c.get("other_values"), c["proportion"]["numerator"])
        for c in found["categories"]
    ]
    assert rows == [(["gold"], None, 4), (["silver"], None, 0), (["bronze"], True, 6)]


def test_bins_are_merged_and_no_value_statistic_is_shown_under_k(
    distributed: Distributed, summarised: Summarised
) -> None:
    view = distributed([{**AGE, "bins": [20, 30, 40, 50]}], k=3)
    merged = column(summarised(view, [9], [([made({25: 1, 35: 4, 45: 4})], None)], k=3), 0, 0)
    assert [b["count"] for b in merged["histogram"]["bins"]] == [0, 5, 4, 0]
    assert merged["histogram"]["bins"][1]["low"] == 20
    assert (merged["q1_bin"], merged["median_bin"], merged["q3_bin"]) == (1, 1, 2)
    kept = column(summarised(view, [9], [([made({25: 3, 35: 3, 45: 3})], None)], k=3), 0, 0)
    assert [b["count"] for b in kept["histogram"]["bins"]] == [0, 3, 3, 3, 0]
    for found in (merged, kept):
        assert [found[name] for name in STATISTICS] == [None] * len(STATISTICS)
        assert {found["not_estimable"][f"/{name}"] for name in STATISTICS} == {"suppressed"}


def test_under_k_the_mean_of_counts_does_not_give_a_count_that_finer_bins_hide(
    distributed: Distributed, summarised: Summarised
) -> None:
    orders = {"column": "orders.order_id", "aggregate": "count"}
    fine = distributed([{**orders, "bins": [0, 1, 2, 3]}], k=3)
    coarse = distributed([{**orders, "bins": [0, 10]}], k=3)
    nine_ones = [([made({1: 9, 2: 1})], None)]
    eight_ones = [([made({1: 8, 2: 2})], None)]
    hidden = column(summarised(fine, [10], nine_ones, k=3), 0, 0)
    assert [b["count"] for b in hidden["histogram"]["bins"]] == [0, 0, 10, 0]
    shown = column(summarised(coarse, [10], nine_ones, k=3), 0, 0)
    assert [shown[name] for name in STATISTICS] == [None] * len(STATISTICS)
    assert column(summarised(coarse, [10], eight_ones, k=3), 0, 0) == shown


def test_under_k_values_all_the_same_are_not_published(
    distributed: Distributed, summarised: Summarised
) -> None:
    view = distributed([AGE], k=3)
    found = column(summarised(view, [6], [([made({47: 6})], None)], k=3), 0, 0)
    assert found["mean"] is None
    assert found["not_estimable"]["/sd"] == "suppressed"
    assert "47" not in json.dumps(found)


def test_a_quartile_between_two_bins_is_not_given_a_bin(
    distributed: Distributed, summarised: Summarised
) -> None:
    view = distributed([{**AGE, "bins": [20, 30, 40]}], k=3)
    found = column(summarised(view, [6], [([made({25: 3, 35: 3})], None)], k=3), 0, 0)
    assert found["median_bin"] is None
    assert found["not_estimable"]["/median_bin"] == "suppressed"
    assert (found["q1_bin"], found["q3_bin"]) == (1, 2)


def test_a_position_whose_cohort_size_is_suppressed_shows_nothing_of_its_variables(
    distributed: Distributed, summarised: Summarised
) -> None:
    view = distributed([TIER], k=3)
    found = summarised(view, [2], [([made({"gold": 2})], None)], k=3)
    assert column(found, 0, 0)["categories"] is None
    assert found.population[0].n_true is None


def test_under_k_nothing_that_combines_variables_is_shown(
    distributed: Distributed, summarised: Summarised
) -> None:
    view = distributed([TIER, AGE], k=3)
    tiers, ages = made({"gold": 10}), made({30: 10})
    [analysed] = summarised(
        view, [10], [([tiers, ages], Joint(10, 0, tiers.excluded))], k=3
    ).analysed
    assert (analysed.n, analysed.excluded_units, analysed.excluded) == (None, None, None)
    assert analysed.variables is not None
    assert [(v.n, v.excluded_units) for v in analysed.variables] == [(10, 0), (10, 0)]


def test_under_k_the_shop_s_distribution_is_disclosed_as_its_parts_are(
    analyse: Analyse, shop: Shop
) -> None:
    [analysed] = analyse(
        shop_document(TIER, AGE, AMOUNT, ORDERS, WEB), shop(extended=True), floor=5
    )
    result = analysed.result
    assert result.derivation.disclosure.min_cell_count == 5
    assert {CaveatCode.SUPPRESSED, CaveatCode.UNKNOWN_EXCLUDED} <= {c.code for c in result.caveats}
    dumped = json.dumps(result.values.model_dump(mode="json"))
    assert '"min": null' in dumped
    for position in result.values.positions:
        for found in position["columns"]:
            for part in (found.get("histogram") or {}).get("bins", []):
                assert not 1 <= part["count"] < 5


# --- Phase 2 -----------------------------------------------------------------------------------


def _refusals(found: Any) -> list[tuple[str, str | None]]:
    return [(refusal.code, refusal.path) for refusal in found.refusals]


@pytest.mark.parametrize(
    ("given", "floor", "expected"),
    [
        ({"column": "orders.order_id"}, None, ("AGGREGATE_REQUIRED", "/column")),
        (
            {"column": "customers.customer_id"},
            None,
            ("NOT_SUPPORTED", "/column"),
        ),
        ({**TIER, "bins": [0, 1]}, None, ("INVALID_VALUE", "/bins")),
        ({"column": "orders.order_id", "aggregate": "count"}, 3, ("MISSING_MEMBER", "/bins")),
        ({**AGE, "bins": [3, 2]}, None, ("INVALID_VALUE", "/bins")),
        (
            {
                "column": "orders.order_id",
                "aggregate": "count",
                "where": [{"kind": "ids", "ids": ["d:o1"]}],
            },
            None,
            ("LEAF_NOT_ALLOWED", "/where/0"),
        ),
        (
            {
                "column": "orders.order_id",
                "aggregate": "count",
                "where": [{"kind": "shelves.stocked", "size": 2}],
            },
            None,
            ("NOT_SUPPORTED", "/where/0"),
        ),
    ],
)
def test_a_column_the_analysis_does_not_take_is_refused_where_it_is_written(
    check: Check,
    shop: Shop,
    given: dict[str, Any],
    floor: int | None,
    expected: tuple[str, str],
) -> None:
    found = check(shop_document(given), shop(extended=True), floor=floor)
    code, at = expected
    assert _refusals(found) == [(code, "/views/0/params/columns/0" + at)]
    assert found.views == []


@pytest.mark.parametrize(("low", "high"), [(0, 5e-324), (2.0**52, 2.0**52 + 1)])
def test_a_declared_range_too_narrow_for_ten_bins_has_its_repeated_edges_collapsed(
    distributed: Distributed, summarised: Summarised, low: float, high: float
) -> None:
    narrow = build.column("customers.narrow", "number", range={"min": low, "max": high})
    view = distributed([{"column": "customers.narrow"}], extras=(narrow,))
    found = column(summarised(view, [3], [([made({low: 3})], None)]), 0, 0)
    assert found["histogram"]["edges_from"] == "range"
    edges = [one["low"] for one in found["histogram"]["bins"][1:]]
    assert edges == sorted(set(edges))
    assert edges[0] == low


def test_a_pack_leaf_in_a_column_s_where_names_the_part_that_brings_it(
    check: Check, shop: Shop
) -> None:
    stocked = {
        "column": "orders.order_id",
        "aggregate": "count",
        "where": [{"kind": "shelves.stocked", "size": 2}],
    }
    [refusal] = check(shop_document(stocked), shop(extended=True)).refusals
    assert "M3.2d" in json.dumps([part.model_dump() for part in refusal.message])


def test_more_columns_than_a_view_may_have_are_refused(check: Check, shop: Shop) -> None:
    found = check(shop_document(*[TIER] * 9), shop(extended=True))
    [refusal] = found.refusals
    assert (refusal.code, refusal.path) == (RefusalCode.LIMIT_EXCEEDED, "/views/0/params/columns")
    assert refusal.limit is not None
    assert refusal.limit.name == "variables"


def test_a_view_s_canonical_parameters_write_each_variable_in_full(
    check: Check, shop: Shop
) -> None:
    found = check(shop_document(TIER, {**AGE, "bins": [20, 40]}, AMOUNT), shop(extended=True))
    assert found.refusals == []
    [view] = found.views
    tiers, ages, amounts = view.identity.params["columns"]
    assert tiers == {"column": "customers.tier"}
    assert ages == {"column": "customers.age", "bins": [20, 40]}
    assert (amounts["aggregate"], amounts["empty"], amounts["bins"]) == ("mean", "exclude", None)
    assert amounts["rows"]["via"] == [{"rel": "rel:orders.customer", "dir": "down"}]


def test_spellings_of_one_variable_give_one_id_and_other_bins_another(
    check: Check, shop: Shop
) -> None:
    release = shop(extended=True)

    def identity(*columns: Mapping[str, Any]) -> str:
        found = check(shop_document(*columns), release)
        assert found.refusals == []
        return found.views[0].identity.id

    plain = identity(AMOUNT)
    spelled = identity(
        {**AMOUNT, "via": [{"rel": "rel:orders.customer", "dir": "down"}], "empty": "exclude"}
    )
    assert plain == spelled
    assert identity({**AMOUNT, "bins": [0, 100, 200]}) != plain
    assert identity({**AMOUNT, "empty": 0}) != plain


def test_the_readback_states_each_column_from_the_descriptors(check: Check, shop: Shop) -> None:
    found = check(shop_document(TIER, AMOUNT, WEB, ORDERS), shop(extended=True))
    [view] = found.views
    text = "".join(
        segment.model_dump().get("text", "") or segment.model_dump().get("data", "")
        for segment in view.readback()
    )
    assert "For each of the 2 cohorts in view order" in text
    assert "Column 0: the customers.tier" in text
    assert "Column 1: the mean orders.amount over the orders rows linked through" in text
    assert "; a unit with no value there is left out" in text
    assert "Column 2: whether some orders row" in text
    assert "Column 3: the number of orders rows linked through" in text
    assert "young" not in text


def test_under_k_undeclared_values_are_one_row_that_names_none_of_them(
    distributed: Distributed, summarised: Summarised
) -> None:
    view = distributed([TIER], k=3)
    found = summarised(view, [11], [([made({"gold": 10, "Jane Q. Doe": 1})], None)], k=3)
    rows = column(found, 0, 0)["categories"]
    assert [([v["data"] for v in row["values"]], row.get("other_values")) for row in rows] == [
        (["gold", "silver", "bronze"], True)
    ]
    assert [row["proportion"]["numerator"] for row in rows] == [11]
    assert "Jane" not in json.dumps(found.values.model_dump(mode="json"))
    plain = column(
        summarised(distributed([TIER]), [11], [([made({"gold": 10, "Jane Q. Doe": 1})], None)]),
        0,
        0,
    )
    assert [row["values"][0]["data"] for row in plain["categories"]][-1] == "Jane Q. Doe"


def test_under_k_the_other_values_row_is_listed_when_empty(
    distributed: Distributed, summarised: Summarised
) -> None:
    view = distributed([TIER], k=3)
    rows = column(
        summarised(view, [9], [([made({"gold": 3, "silver": 3, "bronze": 3})], None)], k=3), 0, 0
    )["categories"]
    assert rows[-1] == {
        "values": [],
        "other_values": True,
        "proportion": rows[-1]["proportion"],
    }
    assert rows[-1]["proportion"]["numerator"] == 0


@pytest.mark.parametrize(
    ("given", "extras"),
    [(WEB, ()), ({"column": "customers.member"}, (build.column("customers.member", "boolean"),))],
)
def test_under_k_a_question_s_or_a_boolean_s_categories_are_false_and_true_with_no_other_row(
    distributed: Distributed,
    summarised: Summarised,
    given: dict[str, Any],
    extras: tuple[Any, ...],
) -> None:
    view = distributed([given], k=3, extras=extras)
    found = summarised(view, [8], [([made({False: 4, True: 4})], None)], k=3)
    rows = column(found, 0, 0)["categories"]
    assert [[value["data"] for value in row["values"]] for row in rows] == [["false"], ["true"]]
    assert [row.get("other_values") for row in rows] == [None, None]


def test_150_categories_are_listed_and_151_refuse_the_call(
    distributed: Distributed, summarised: Summarised
) -> None:
    view = distributed([TIER])
    listed = {f"v{n:03d}": 1 for n in range(147)}
    found = column(summarised(view, [147], [([made(listed)], None)]), 0, 0)
    assert len(found["categories"]) == 150
    with pytest.raises(TooManyCategories):
        summarised(view, [148], [([made({**listed, "v999": 1})], None)])


def test_a_position_whose_cohort_count_is_suppressed_shows_nothing_though_its_split_is_large(
    distributed: Distributed, summarised: Summarised
) -> None:
    view = distributed([TIER], k=3)
    found = summarised(view, [8], [([made({"gold": 4, "silver": 4})], None)], k=3, outside=2)
    assert found.population[0].n_true is None
    assert column(found, 0, 0)["categories"] is None
    [analysed] = found.analysed
    assert (analysed.n, analysed.excluded_units) == (None, None)


def test_without_k_a_value_beyond_the_magnitude_bound_is_not_summarised(
    distributed: Distributed, summarised: Summarised
) -> None:
    view = distributed([AGE])
    with pytest.raises(TooLarge):
        summarised(view, [2], [([made({2.0**53: 1, 0: 1})], None)])
    with pytest.raises(TooLarge):
        summarised(view, [2], [([made({-(2**53 - 1): 1, 2**53 - 1: 1})], None)])
    largest = 2**53 - 1
    edge = column(summarised(view, [2], [([made({largest: 1, largest - 1: 1})], None)]), 0, 0)
    assert (edge["min"], edge["max"]) == (largest - 1, largest)
    spread = column(summarised(view, [3], [([made({-largest: 1, 0: 1, largest: 1})], None)]), 0, 0)
    assert spread["sd"] == largest
    with pytest.raises(TooLarge):
        summarised(view, [2], [([made({largest + 1: 1, 0: 1})], None)])
    near = column(summarised(view, [2], [([made({-(2.0**51): 1, 2.0**51: 1})], None)]), 0, 0)
    assert (near["mean"], near["sd"]) == (0.0, math.sqrt(2) * 2.0**51)
    under_k = distributed([{**AGE, "bins": [0, 1]}], k=3)
    counted = column(summarised(under_k, [4], [([made({1e305: 4})], None)], k=3), 0, 0)
    assert counted["histogram"]["bins"][-1]["count"] == 4


def test_under_k_one_column_read_twice_with_other_bins_is_refused(check: Check, shop: Shop) -> None:
    found = check(
        shop_document({**AGE, "bins": [25, 35, 60]}, {**AGE, "bins": [25, 45, 60]}),
        shop(extended=True),
        floor=3,
    )
    assert _refusals(found) == [("CONFLICTING_MEMBERS", "/views/0/params/columns/1/bins")]
    again = check(shop_document(AGE, AGE), shop(extended=True), floor=3)
    assert again.refusals == []


def test_under_k_one_column_read_by_two_views_with_other_bins_is_refused(
    check: Check, shop: Shop
) -> None:
    document = shop_document({**AGE, "bins": [0, 45, 100]})
    document["views"].append(
        {**document["views"][0], "params": {"columns": [{**AGE, "bins": [0, 44, 100]}]}}
    )
    found = check(document, shop(extended=True), floor=3)
    assert _refusals(found) == [("CONFLICTING_MEMBERS", "/views/1/params/columns/0/bins")]
    assert check(document, shop(extended=True)).refusals == []
    document["views"][1]["params"] = {"columns": [{**AGE, "bins": [0, 45, 100]}]}
    assert check(document, shop(extended=True), floor=3).refusals == []


def test_under_k_a_column_s_range_edges_and_the_same_edges_written_are_one_set_of_bins(
    check: Check, shop: Shop
) -> None:
    written = [18 + 8 * step for step in range(11)]
    same = check(shop_document(AGE, {**AGE, "bins": written}), shop(extended=True), floor=3)
    assert same.refusals == []
    other = check(shop_document(AGE, {**AGE, "bins": [18, 58, 98]}), shop(extended=True), floor=3)
    assert _refusals(other) == [("CONFLICTING_MEMBERS", "/views/0/params/columns/1/bins")]


def test_under_k_an_aggregate_of_a_column_read_with_other_bins_than_the_column_is_refused(
    check: Check, shop: Shop
) -> None:
    through = [
        {"rel": "rel:orders.customer", "dir": "down"},
        {"rel": "rel:orders.customer", "dir": "up"},
    ]
    most = {**AGE, "via": through, "aggregate": "max"}
    found = check(
        shop_document({**AGE, "bins": [0, 45, 100]}, {**most, "bins": [0, 44, 100]}),
        shop(extended=True),
        floor=3,
    )
    assert _refusals(found) == [("CONFLICTING_MEMBERS", "/views/0/params/columns/1/bins")]


def test_under_k_counts_of_one_set_of_rows_are_one_quantity_whatever_column_they_name(
    check: Check, shop: Shop
) -> None:
    by_key = {"column": "orders.order_id", "aggregate": "count", "bins": [0, 2, 5]}
    by_channel = {"column": "orders.channel", "aggregate": "count", "bins": [0, 1, 5]}
    found = check(shop_document(by_key, by_channel), shop(extended=True), floor=3)
    assert _refusals(found) == [("CONFLICTING_MEMBERS", "/views/0/params/columns/1/bins")]
    web = {
        **by_channel,
        "where": [{"kind": "value", "column": "orders.channel", "values": ["web"]}],
    }
    assert check(shop_document(by_key, web), shop(extended=True), floor=3).refusals == []


def test_a_count_s_bins_are_its_own_beside_a_histogram_of_the_column_it_names(
    check: Check, shop: Shop
) -> None:
    amounts = {"column": "orders.amount", "aggregate": "max", "bins": [0, 50, 200]}
    counted = {"column": "orders.amount", "aggregate": "count", "bins": [0, 1, 2]}
    found = check(shop_document(amounts, counted), shop(extended=True), floor=3)
    assert found.refusals == []


def test_a_category_column_read_twice_once_with_bins_is_told_bins_divide_numbers(
    check: Check, shop: Shop
) -> None:
    found = check(shop_document(TIER, {**TIER, "bins": [0, 1]}), shop(extended=True))
    assert _refusals(found) == [("INVALID_VALUE", "/views/0/params/columns/1/bins")]


def test_counting_rows_is_not_supported_until_its_part(check: Check, shop: Shop) -> None:
    found = check(shop_document({"column": "orders.channel", "count": "rows"}), shop(extended=True))
    [refusal] = found.refusals
    assert (refusal.code, refusal.path) == (
        RefusalCode.NOT_SUPPORTED,
        "/views/0/params/columns/0/count",
    )
    assert "M3.2c" in json.dumps([s.model_dump() for s in refusal.message])


def test_under_k_whether_a_column_has_too_many_categories_depends_on_its_declaration_alone(
    distributed: Distributed, summarised: Summarised
) -> None:
    view = distributed([TIER], k=3)
    many = {f"v{n:03d}": 1 for n in range(200)}
    rows = column(summarised(view, [200], [([made(many)], None)], k=3), 0, 0)["categories"]
    assert [(row.get("other_values"), row["proportion"]["numerator"]) for row in rows] == [
        (None, 0),
        (None, 0),
        (None, 0),
        (True, 200),
    ]


def test_a_summary_stops_once_the_call_s_deadline_has_passed(
    distributed: Distributed, summarised: Summarised
) -> None:
    view = distributed([TIER, AGE])
    tiers, ages = made({"gold": 3}), made({30: 3})
    given = [([tiers, ages], Joint(3, 0, tiers.excluded))]
    with pytest.raises(CallerDeadline):
        summarised(view, [3], given, ends=time.monotonic() - 1)
    assert summarised(view, [3], given, ends=time.monotonic() + 60).values.positions

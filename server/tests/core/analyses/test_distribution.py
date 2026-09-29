"""``summary.distribution``: its values, estimability, caveats, disclosure and phase 2 (SPEC §7.6,
§8.1–§8.4, §9.2, §9.5; D325, D328, D329), over variables materialised as given and over the shop
evaluated by the reference evaluator."""

import json
import math
import time
from collections.abc import Callable, Mapping
from types import MappingProxyType
from typing import Any, cast

import pytest

from aibi.core.analyses import stats
from aibi.core.analyses.charts import distribution_charts
from aibi.core.analyses.distribution import TooLarge, TooManyCategories
from aibi.core.engine import build
from aibi.core.engine.variables import Joint, Materialised, RowCounts
from aibi.core.engine.worker import CallerDeadline
from aibi.core.schema.caveats import CaveatCode
from aibi.core.schema.refusals import Limit, RefusalCode
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
CHANNELS = {"column": "orders.channel", "count": "rows"}
AMOUNTS = {"column": "orders.amount", "count": "rows"}
ORDERED_TIERS = {
    "column": "customers.tier",
    "via": [
        {"rel": "rel:orders.customer", "dir": "down"},
        {"rel": "rel:orders.customer", "dir": "up"},
    ],
    "count": "rows",
}
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


def counted(
    units: Mapping[int, int],
    values: Mapping[Any, int],
    excluded_rows: Mapping[str, int] | None = None,
    excluded: Mapping[str, int] | None = None,
) -> Materialised:
    """A count of rows materialised over a cohort: its pooled units by the rows each reached,
    their rows' values and their rows excluded by reason, and its units excluded by reason."""
    found = made(units, excluded)
    by_reason = dict.fromkeys(ExclusionReason, 0)
    by_reason.update({ExclusionReason(reason): n for reason, n in (excluded_rows or {}).items()})
    rows = RowCounts(MappingProxyType(dict(values)), MappingProxyType(by_reason))
    return Materialised(found.values, found.excluded_units, found.excluded, found.marks, rows)


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
            ("UNKNOWN_KIND", "/where/0/kind"),
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


@pytest.mark.parametrize("refused", [{"column": "orders.channel", "each": "category"}, None])
def test_under_k_a_view_that_is_refused_reads_no_bins_that_a_later_view_conflicts_with(
    check: Check, shop: Shop, refused: dict[str, Any] | None
) -> None:
    """A view refused in phase 2, here for its memberships (D380), is not run, so its edges
    constrain no later view; a view that runs still does (§8.4)."""
    most = {"column": "orders.amount", "aggregate": "max", "bins": [0, 50, 200]}
    document = shop_document(most, *([refused] if refused else []))
    least = {"column": "orders.amount", "aggregate": "min", "bins": [0, 100, 200]}
    document["views"].append({**document["views"][0], "params": {"columns": [least]}})
    found = check(document, shop(extended=True), floor=3)
    if refused:
        assert _refusals(found) == [("NOT_SUPPORTED", "/views/0/params/columns/1/each")]
    else:
        assert _refusals(found) == [("CONFLICTING_MEMBERS", "/views/1/params/columns/0/bins")]


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


def test_a_histogram_takes_at_most_65_edges_under_the_limit_bin_edges(
    check: Check, shop: Shop
) -> None:
    assert (
        check(shop_document({**AGE, "bins": list(range(65))}), shop(extended=True)).refusals == []
    )
    found = check(shop_document({**AGE, "bins": list(range(66))}), shop(extended=True))
    [refusal] = found.refusals
    assert (refusal.code, refusal.path) == (
        RefusalCode.LIMIT_EXCEEDED,
        "/views/0/params/columns/0/bins",
    )
    assert refusal.limit == Limit(name="bin_edges", max=65)
    [short] = check(shop_document({**AGE, "bins": [1]}), shop(extended=True)).refusals
    assert short.code == RefusalCode.INVALID_VALUE


# --- Rows (D378, D379) -------------------------------------------------------------------------


def test_a_count_of_rows_gives_each_category_its_rows_over_the_rows_with_a_value(
    distributed: Distributed, summarised: Summarised
) -> None:
    view = distributed([CHANNELS])
    given = counted({2: 3, 0: 1}, {"web": 3, "shop": 2, "tram": 1}, {"NOT_ASSESSED": 1})
    found = column(summarised(view, [4], [([given], None)]), 0, 0)
    assert found["kind"] == "category_rows"
    shares = [(c["values"][0]["data"], c["proportion"]["numerator"]) for c in found["categories"]]
    assert shares == [("shop", 2), ("web", 3), ("phone", 0), ("tram", 1)]
    assert {c["proportion"]["denominator"] for c in found["categories"]} == {6}
    assert found["categories"][0]["proportion"]["denominator_definition"] == {
        "position": 0,
        "predicate": None,
        "counts": "rows",
    }
    assert found["excluded_rows"]["NOT_ASSESSED"] == 1
    assert set(found["excluded_rows"]) == {reason.value for reason in ExclusionReason}


def test_undeclared_categories_follow_the_declared_in_utf16_order_for_units_and_rows_alike(
    distributed: Distributed, summarised: Summarised
) -> None:
    """Without a disclosure setting (D329, D378): a value above the basic plane sorts before one
    of U+FF5E in UTF-16, and after it by code point."""
    undeclared = {"\uff5e": 1, "tram": 1, "\U0001f68b": 1, "bus": 1}
    others = ["bus", "tram", "\U0001f68b", "\uff5e"]
    for variable, one, declared in (
        (CHANNELS, counted({5: 1}, {**undeclared, "web": 1}), ["shop", "web", "phone"]),
        (TIER, made({**undeclared, "gold": 1}), ["gold", "silver", "bronze"]),
    ):
        found = column(summarised(distributed([variable]), [5], [([one], None)]), 0, 0)
        assert [c["values"][0]["data"] for c in found["categories"]] == [*declared, *others]


def test_a_count_of_rows_of_numbers_summarises_the_rows_values(
    distributed: Distributed, summarised: Summarised
) -> None:
    view = distributed([AMOUNTS])
    rows = {10.5: 2, 20.5: 1, 190.0: 1}
    given = counted({2: 2}, rows, {"NO_INFORMATION": 1, "NOT_APPLICABLE": 2})
    found = column(summarised(view, [2], [([given], None)]), 0, 0)
    weighted = sorted(rows.items())
    assert found["kind"] == "number_rows"
    assert found["n"] == 4
    assert found["mean"] == stats.mean(weighted)
    assert found["median"] == stats.quantile(weighted, 0.5)
    assert (found["min"], found["max"]) == (10.5, 190.0)
    assert found["histogram"]["edges_from"] == "range"
    assert sum(one["count"] for one in found["histogram"]["bins"]) == 4
    assert (found["excluded_rows"]["NO_INFORMATION"], found["excluded_rows"]["NOT_APPLICABLE"]) == (
        1,
        2,
    )


def test_a_count_of_rows_of_a_value_beyond_the_magnitude_bound_is_not_summarised(
    distributed: Distributed, summarised: Summarised
) -> None:
    """§8.2: an output holds no number beyond ±(2^53 − 1), a row's as a unit's."""
    largest = 2**53 - 1
    view = distributed([AMOUNTS])
    for beyond in (largest + 1, -(largest + 1), 2.0**60):
        with pytest.raises(TooLarge):
            summarised(view, [1], [([counted({2: 1}, {beyond: 1, 0: 1})], None)])
    edge = column(
        summarised(view, [1], [([counted({2: 1}, {largest: 1, largest - 1: 1})], None)]), 0, 0
    )
    assert (edge["min"], edge["max"]) == (largest - 1, largest)


def test_with_no_row_a_count_of_rows_estimates_nothing_but_the_histogram_of_a_range(
    distributed: Distributed, summarised: Summarised
) -> None:
    view = distributed([AMOUNTS, CHANNELS])
    none = counted({0: 3}, {})
    found = summarised(view, [3], [([none, none], Joint(3, 0, none.excluded))])
    amounts = column(found, 0, 0)
    assert amounts["n"] == 0
    assert set(amounts["not_estimable"]) == {f"/{name}" for name in STATISTICS}
    assert sum(one["count"] for one in amounts["histogram"]["bins"]) == 0
    channels = column(found, 0, 1)
    assert [c["proportion"]["estimate"] for c in channels["categories"]] == [None] * 3


def test_analysed_counts_the_units_a_count_of_rows_pools_those_with_no_rows_included(
    distributed: Distributed, summarised: Summarised
) -> None:
    view = distributed([CHANNELS])
    given = counted({0: 2, 3: 1}, {"web": 3}, excluded={"NOT_COVERED": 2})
    found = summarised(view, [5], [([given], None)])
    [analysed] = found.analysed
    assert (analysed.n, analysed.excluded_units) == (3, 2)
    assert analysed.excluded is not None
    assert analysed.excluded["NOT_COVERED"] == 2
    assert column(found, 0, 0)["categories"][1]["proportion"]["denominator"] == 3


def test_rows_left_out_for_what_is_unknown_raise_unknown_excluded_and_not_applicable_ones_do_not(
    distributed: Distributed, summarised: Summarised
) -> None:
    view = distributed([CHANNELS])
    applicable = summarised(view, [1], [([counted({1: 1}, {}, {"NOT_APPLICABLE": 1})], None)])
    assert CaveatCode.UNKNOWN_EXCLUDED not in codes(applicable)
    unknown = summarised(view, [1], [([counted({1: 1}, {}, {"NO_INFORMATION": 1})], None)])
    [caveat] = [c for c in unknown.caveats if c.code == CaveatCode.UNKNOWN_EXCLUDED]
    assert caveat.affects == ["/values"]
    assert "excluded_rows" in "".join(part.model_dump().get("text", "") for part in caveat.message)


def test_a_count_of_rows_is_never_summarised_under_k(
    distributed: Distributed, summarised: Summarised
) -> None:
    view = distributed([CHANNELS])
    with pytest.raises(ValueError, match="D379"):
        summarised(view, [1], [([counted({1: 1}, {"web": 1})], None)], k=3)


def test_the_shop_s_rows_are_counted_by_the_reference_evaluator_as_its_orders_are(
    analyse: Analyse, shop: Shop, rows: Callable[..., Any]
) -> None:
    [analysed] = analyse(shop_document(CHANNELS, AMOUNTS), shop(extended=True))
    given = rows(extended=True)
    ages = {c["customer_id"]: c["age"] for c in given["customers"]}
    for position, young in enumerate((True, False)):
        orders = [o for o in given["orders"] if (ages[o["customer_id"]] < 45) == young]
        channels = column(analysed.outcome, position, 0)
        by_channel = {
            c["values"][0]["data"]: c["proportion"]["numerator"] for c in channels["categories"]
        }
        assert by_channel == {
            channel: sum(o["channel"] == channel for o in orders)
            for channel in ("shop", "web", "phone")
        }
        amounts = column(analysed.outcome, position, 1)
        given_amounts = sorted(float(o["amount"]) for o in orders if o["amount"] is not None)
        assert amounts["n"] == len(given_amounts)
        assert amounts["excluded_rows"]["NO_INFORMATION"] == len(orders) - len(given_amounts)
        assert (amounts["min"], amounts["max"]) == (given_amounts[0], given_amounts[-1])
        customers = sum((age < 45) == young for age in ages.values())
        assert analysed.outcome.analysed[position].n == customers
    result = analysed.result
    assert [chart["description"] for chart in result.charts] == [
        "Column 0: the proportion of each cohort's rows in each category, among those with a value",
        "Column 1: each cohort's rows per histogram bin, the bins by their intervals from the "
        "lowest",
    ]
    encoding = cast(dict[str, Any], result.charts[1])["spec"]["encoding"]
    assert encoding["y"]["title"] == "Rows"
    assert [one["title"] for one in encoding["tooltip"]] == ["From", "To", "Rows"]
    assert CaveatCode.UNKNOWN_EXCLUDED in {caveat.code for caveat in result.caveats}


def test_a_row_is_counted_once_for_each_path_and_each_unit_that_reaches_it(
    analyse: Analyse, shop: Shop, rows: Callable[..., Any]
) -> None:
    given = rows()
    tiers = {c["customer_id"]: c["tier"] for c in given["customers"]}
    ages = {c["customer_id"]: c["age"] for c in given["customers"]}
    [by_order] = analyse(shop_document(ORDERED_TIERS), shop())
    young = [o for o in given["orders"] if ages[o["customer_id"]] < 45]
    categories = column(by_order.outcome, 0, 0)["categories"]
    assert {c["values"][0]["data"]: c["proportion"]["numerator"] for c in categories} == {
        tier: sum(tiers[o["customer_id"]] == tier for o in young)
        for tier in ("gold", "silver", "bronze")
    }
    assert column(by_order.outcome, 0, 0)["excluded_rows"]["NOT_ASSESSED"] == sum(
        tiers[o["customer_id"]] == "?" for o in young
    )
    siblings = {
        "aibi": "1",
        "dataset": "d",
        "unit": "orders",
        "cohorts": {"all": {"all": []}},
        "views": [
            {
                "analysis": "summary.distribution",
                "params": {
                    "columns": [
                        {
                            "column": "orders.channel",
                            "via": [
                                {"rel": "rel:orders.customer", "dir": "up"},
                                {"rel": "rel:orders.customer", "dir": "down"},
                            ],
                            "count": "rows",
                        }
                    ]
                },
            }
        ],
    }
    [reached] = analyse(siblings, shop())
    placed: dict[str, int] = {}
    for order in given["orders"]:
        placed[order["customer_id"]] = placed.get(order["customer_id"], 0) + 1
    expected = {
        channel: sum(placed[o["customer_id"]] for o in given["orders"] if o["channel"] == channel)
        for channel in ("shop", "web", "phone")
    }
    categories = column(reached.outcome, 0, 0)["categories"]
    assert {c["values"][0]["data"]: c["proportion"]["numerator"] for c in categories} == expected


def test_a_count_of_rows_takes_a_where_and_a_lift(analyse: Analyse, shop: Shop) -> None:
    web = [{"kind": "value", "column": "orders.channel", "values": ["web"]}]
    [analysed] = analyse(
        shop_document({**CHANNELS, "where": web, "lift": "assessed"}), shop(extended=True)
    )
    in_shop, on_web, by_phone = [
        c["proportion"] for c in column(analysed.outcome, 0, 0)["categories"]
    ]
    assert (in_shop["numerator"], by_phone["numerator"]) == (0, 0)
    assert on_web["numerator"] == on_web["denominator"] > 0


def test_a_count_of_rows_has_a_canonical_form_of_its_own(check: Check, shop: Shop) -> None:
    release = shop(extended=True)
    found = check(
        shop_document(CHANNELS, AMOUNTS, {**AMOUNTS, "bins": [0, 100, 200]}, ORDERED_TIERS),
        release,
    )
    assert found.refusals == []
    [view] = found.views
    channels, amounts, binned, tiers = view.identity.params["columns"]
    assert sorted(channels) == ["column", "count", "rows"]
    assert (channels["count"], channels["column"]) == ("rows", "orders.channel")
    assert channels["rows"]["via"] == [{"rel": "rel:orders.customer", "dir": "down"}]
    assert (amounts["bins"], binned["bins"]) == (None, [0, 100, 200])
    assert sorted(tiers) == ["column", "count", "lookup", "rows"]
    assert tiers["lookup"] == [{"rel": "rel:orders.customer", "dir": "up"}]
    assert tiers["rows"]["via"] == [{"rel": "rel:orders.customer", "dir": "down"}]
    counted_orders = check(
        shop_document({"column": "orders.amount", "aggregate": "count"}), release
    )
    rows = check(shop_document(AMOUNTS), release)
    assert counted_orders.views[0].identity.id != rows.views[0].identity.id
    spelled = check(
        shop_document({**AMOUNTS, "via": [{"rel": "rel:orders.customer", "dir": "down"}]}), release
    )
    assert spelled.views[0].identity.id == rows.views[0].identity.id


def test_the_readback_states_the_rows_a_count_of_rows_reaches(check: Check, shop: Shop) -> None:
    found = check(shop_document(TIER, CHANNELS, ORDERED_TIERS), shop(extended=True))
    [view] = found.views
    text = "".join(
        segment.model_dump().get("text", "") or segment.model_dump().get("data", "")
        for segment in view.readback()
    )
    assert "Column 1: the rows reached, by the orders.channel of each of the orders rows" in text
    assert (
        "Column 2: the rows reached, by the customers.tier of the customers row reached through "
        "rel:orders.customer of each of the orders rows"
    ) in text
    assert "a row counted once for each unit and each path that reaches it" in text
    assert "A column that counts rows gives the same over the rows its units reach" in text
    plain = check(shop_document(TIER), shop(extended=True)).views[0]
    said = "".join(segment.model_dump().get("text", "") or "" for segment in plain.readback())
    assert "counts rows" not in said


SETTINGS = {
    "the dataset's": ({"min_cell_count": 3}, None, None, "the dataset's min_cell_count, 3"),
    "the floor": (None, 4, None, "the deployment's floor, 4"),
    "a draft's published": (None, None, 5, "the latest published release's min_cell_count, 5"),
}


@pytest.mark.parametrize("setting", list(SETTINGS))
def test_under_any_disclosure_setting_a_count_of_rows_is_withheld_where_it_is_written(
    check: Check, shop: Shop, setting: str
) -> None:
    disclosure, floor, published, source = SETTINGS[setting]
    release = shop(extended=True, disclosure=disclosure)
    found = check(
        shop_document(TIER, {**AMOUNTS, "bins": [0, 100, 200]}),
        release,
        floor=floor,
        published=published,
    )
    [refusal] = found.refusals
    assert (refusal.code, refusal.path) == (
        RefusalCode.WITHHELD_UNDER_K,
        "/views/0/params/columns/1/count",
    )
    assert found.views == []
    said = "".join(part.model_dump().get("text", "") for part in refusal.message)
    assert f"({source})" in said
    assert "D379" in said
    assert [
        part.model_dump().get("data") for part in refusal.message if "data" in part.model_dump()
    ] == ["summary.distribution"]
    assert [part.model_dump()["text"] for part in refusal.alternatives] == [
        'aggregate: "count"',
        'aggregate: "max"',
        'aggregate: "min"',
        'aggregate: "mean"',
    ]
    assert check(shop_document(TIER, AMOUNTS), shop(extended=True)).refusals == []


def test_a_withheld_count_of_rows_is_refused_before_the_bins_it_would_need(
    check: Check, shop: Shop
) -> None:
    ages = {**ORDERED_TIERS, "column": "customers.age"}
    found = check(shop_document(ages), shop(), floor=3)
    assert _refusals(found) == [("WITHHELD_UNDER_K", "/views/0/params/columns/0/count")]
    without = {key: value for key, value in ages.items() if key != "count"}
    aggregate = {**without, "aggregate": "max"}
    assert _refusals(check(shop_document(aggregate), shop(), floor=3)) == [
        ("MISSING_MEMBER", "/views/0/params/columns/0/bins")
    ]


def test_a_withheld_count_of_rows_records_no_edges_that_a_later_view_would_conflict_with(
    check: Check, shop: Shop
) -> None:
    document = shop_document({**AMOUNTS, "bins": [0, 100, 200]})
    greatest = {"column": "orders.amount", "aggregate": "max", "bins": [0, 50, 200]}
    document["views"].append({**document["views"][0], "params": {"columns": [greatest]}})
    found = check(document, shop(extended=True), floor=3)
    assert _refusals(found) == [("WITHHELD_UNDER_K", "/views/0/params/columns/0/count")]
    assert [view.index for view in found.views] == [1]


@pytest.mark.parametrize(
    "given",
    [
        {**CHANNELS, "aggregate": "count"},
        {**CHANNELS, "values": ["web"]},
        {**AMOUNTS, "empty": 0},
    ],
    ids=["an aggregate", "values", "empty"],
)
def test_a_count_of_rows_takes_no_aggregate_values_or_empty(
    check: Check, shop: Shop, given: dict[str, Any]
) -> None:
    found = check(shop_document(given), shop(extended=True))
    assert _refusals(found) == [("CONFLICTING_MEMBERS", "/views/0/params/columns/0")]


def test_a_count_of_rows_of_a_column_with_one_value_per_unit_is_invalid(
    check: Check, shop: Shop
) -> None:
    found = check(shop_document({**TIER, "count": "rows"}), shop(extended=True))
    assert _refusals(found) == [("INVALID_VALUE", "/views/0/params/columns/0/count")]


def test_a_count_of_rows_of_a_list_waits_for_memberships(check: Check, shop: Shop) -> None:
    labels = build.column("customers.labels", "list<category>")
    found = check(
        shop_document({"column": "customers.labels", "count": "rows"}), shop(extras=[labels])
    )
    [refusal] = found.refusals
    assert (refusal.code, refusal.path) == (
        RefusalCode.NOT_SUPPORTED,
        "/views/0/params/columns/0/count",
    )
    assert "M3.2e-2a (#52)" in json.dumps([s.model_dump() for s in refusal.message])
    several = check(shop_document({"column": "orders.channel"}), shop())
    assert "M3.2e-2a (#52)" in json.dumps([s.model_dump() for s in several.refusals[0].message])


KINDS: dict[str, tuple[str, str, list[str], bool]] = {
    "an unordered category": ("orders.channel", "NOT_SUPPORTED", ["count", "some", "every"], True),
    "an ordered category": (
        "orders.grade",
        "NOT_SUPPORTED",
        ["count", "max", "min", "some", "every"],
        True,
    ),
    "a boolean": ("orders.paid", "NOT_SUPPORTED", ["count", "some", "every"], True),
    "a number": (
        "orders.amount",
        "AGGREGATE_REQUIRED",
        ["count", "max", "min", "mean", "some", "every"],
        True,
    ),
    "an integer": (
        "orders.items",
        "AGGREGATE_REQUIRED",
        ["count", "max", "min", "mean", "some", "every"],
        True,
    ),
    "a list": ("customers.labels", "NOT_SUPPORTED", ["some", "every"], False),
    "a string": ("orders.order_id", "AGGREGATE_REQUIRED", ["count", "some", "every"], False),
}
"""A column of each datatype below the unit, or a list on it, and what a view of it without an
aggregate is refused: the code, the aggregates it offers, and whether it offers ``count:
"rows"`` without a disclosure setting."""

VALUES: dict[str, list[Any]] = {
    "orders.channel": ["web"],
    "orders.grade": ["b"],
    "orders.paid": [True],
    "orders.amount": [10.5],
    "orders.items": [2],
    "customers.labels": ["new"],
    "orders.order_id": ["o1"],
}


UNIT_EXTRAS = [
    build.column("customers.flag", "boolean"),
    build.column("customers.spent", "number"),
    build.column("customers.waited", "time_offset", units="d"),
    build.column("customers.born", "date"),
    build.column("customers.seen", "datetime"),
    build.column("customers.nick", "string"),
]
"""A column of the customers of each datatype their tier and age are not."""


def _kinds(shop: Shop, disclosure: Mapping[str, Any] | None = None) -> Any:
    extras = [
        build.column("customers.labels", "list<category>"),
        build.column("orders.paid", "boolean"),
        build.column("orders.items", "integer"),
        build.column(
            "orders.grade",
            "category",
            permissible_values={"values": [{"value": v} for v in "abc"], "ordered": True},
        ),
        *UNIT_EXTRAS,
    ]
    return shop(extended=True, extras=extras, disclosure=disclosure)


def _followed(variable: Mapping[str, Any], refusal: Any) -> dict[str, Any] | None:
    """The variable the member a refusal names to give leads to, if it names one."""
    if refusal.code == RefusalCode.MISSING_MEMBER and refusal.path.endswith("/bins"):
        return {**variable, "bins": [0, 10, 100]}
    return None


def _alternative(variable: Mapping[str, Any], alternative: str) -> dict[str, Any]:
    """The variable a refusal's alternative leads to: an aggregate, with the ``values`` or
    ``bins`` it says, keeping the variable's ``where`` and ``bins`` unless it says without them,
    or a count of rows."""
    column = variable["column"]
    kept = {name: variable[name] for name in ("where", "bins") if name in variable}
    if alternative == 'count: "rows"':
        return {"column": column, "count": "rows", **kept}
    name = alternative.removeprefix('aggregate: "').split('"', 1)[0]
    found: dict[str, Any] = {"column": column, "aggregate": name, **kept}
    if name in ("some", "every"):
        found["values"] = VALUES[column]
    if alternative.endswith(" with bins"):
        found["bins"] = [0, 10, 100]
    if alternative.endswith(" without bins"):
        del found["bins"]
    return found


SINGLE: dict[str, tuple[str, list[str]]] = {
    "a category on the unit": ("customers.tier", ["leave each out"]),
    "a boolean on the unit": ("customers.flag", ["leave each out"]),
    "an integer on the unit": ("customers.age", ["leave each out"]),
    "a number on the unit": ("customers.spent", ["leave each out"]),
    "a time offset on the unit": ("customers.waited", ["leave each out"]),
    "a date on the unit": ("customers.born", []),
    "a datetime on the unit": ("customers.seen", []),
    "a string on the unit": ("customers.nick", []),
}
"""A column of one value per unit of each datatype, which has no memberships, and what a
refusal of its memberships offers: leaving ``each`` out where the analysis summarises the
column as it is, else nothing."""


@pytest.mark.parametrize("kind", [*KINDS, *SINGLE])
def test_memberships_offer_what_runs_in_their_place_until_their_part_reads_them(
    check: Check, shop: Shop, kind: str
) -> None:
    """``each`` is the engine's in M3.2e-2a-1 (D380): ``summary.distribution`` refuses the
    memberships it resolves at the member, naming the part that reads them, and resolution a
    column that has none; either offers what runs in its place, and a view of each runs: the
    aggregates the column takes (``resolve.aggregates_taken``) where it has several values per
    unit, whatever its datatype, else leaving ``each`` out, but for a column whose datatype the
    analysis does not summarise, which is offered nothing, since a view of it is refused."""
    release = _kinds(shop)
    if kind in SINGLE:
        column, offered = SINGLE[kind]
        code = RefusalCode.INVALID_VALUE
        if not offered:
            [bare] = check(shop_document({"column": column}), release).refusals
            assert (bare.code, bare.path) == (
                RefusalCode.NOT_SUPPORTED,
                "/views/0/params/columns/0/column",
            )
    else:
        column, several, offered, _ = KINDS[kind]
        categorical = several == "NOT_SUPPORTED"
        code = RefusalCode.NOT_SUPPORTED if categorical else RefusalCode.INVALID_VALUE
    found = check(shop_document({"column": column, "each": "category"}), release)
    [refusal] = found.refusals
    assert (refusal.code, refusal.path) == (code, "/views/0/params/columns/0/each")
    assert found.views == []
    said = json.dumps([s.model_dump() for s in refusal.message])
    assert ("M3.2e-2a-2 (#52)" in said) is (code == RefusalCode.NOT_SUPPORTED)
    assert [one.text for one in refusal.alternatives or []] == offered
    for alternative in offered:
        given = (
            {"column": column}
            if alternative == "leave each out"
            else _alternative({"column": column}, alternative)
        )
        assert check(shop_document(given), release).refusals == [], given


BELOW_SCOPE: dict[str, tuple[str, list[str], list[Any]]] = {
    "the scope column": ("visits.stall", ["some"], ["a"]),
    "another column of its rows": ("visits.paid", ["some", "every"], [True]),
}
"""A column of rows whose coverage is scoped by value, and what runs in place of its memberships
or of the bare column: the aggregates its path allows, and the values ``some`` and ``every``
ask about."""
REFUSED_BELOW_SCOPE = {"count": "OPEN_SCOPE", "every": "SCOPE_COLUMN_MENTION"}


@pytest.mark.parametrize("each", [True, False])
@pytest.mark.parametrize("kind", list(BELOW_SCOPE))
def test_below_scoped_coverage_only_the_aggregates_the_path_allows_are_offered_and_each_runs(
    check: Check, scoped: Shop, kind: str, each: bool
) -> None:
    """D377, D380: below a step whose coverage is scoped by value, an aggregate that pools rows
    restricts its scope columns in its ``where`` (``OPEN_SCOPE``), and ``every`` may not mention
    its scope column (``SCOPE_COLUMN_MENTION``), so the refusal of memberships and that of the
    bare column offer neither, nor ``count: "rows"`` (``resolve.aggregates_over``); a view of
    each alternative runs, and of each left out is refused."""
    column, offered, values = BELOW_SCOPE[kind]
    release = scoped()
    given: dict[str, Any] = {"column": column, **({"each": "category"} if each else {})}
    [refusal] = check(shop_document(given), release).refusals
    member = "each" if each else "column"
    assert (refusal.code, refusal.path) == (
        RefusalCode.NOT_SUPPORTED,
        f"/views/0/params/columns/0/{member}",
    )
    assert [one.text for one in refusal.alternatives or []] == offered
    for name in ("count", "some", "every"):
        asked: dict[str, Any] = {"column": column, "aggregate": name}
        if name != "count":
            asked["values"] = values
        found = check(shop_document(asked), release)
        if name in offered:
            assert found.refusals == [], name
        else:
            [left_out] = found.refusals
            assert left_out.code == REFUSED_BELOW_SCOPE[name], name


@pytest.mark.parametrize("kind", list(KINDS))
def test_a_column_of_several_values_per_unit_offers_the_aggregates_it_takes_and_a_count_of_its_rows(
    check: Check, shop: Shop, kind: str
) -> None:
    """D377, D378: the refusal of a column of several values per unit without an aggregate
    lists the aggregates its column takes (``max`` and ``min`` of numbers and ordered categories,
    ``mean`` of numbers, of a list's items ``some`` and ``every`` alone), each of which a view
    then runs, and ``count: "rows"`` for numbers, categories and booleans below the unit."""
    column, code, aggregates, offered = KINDS[kind]
    release = _kinds(shop)
    rows = 'count: "rows"'
    found = check(shop_document({"column": column}), release)
    [refusal] = found.refusals
    assert (refusal.code, refusal.path) == (code, "/views/0/params/columns/0/column")
    assert [one.text for one in refusal.alternatives or []] == [
        *aggregates,
        *([rows] if offered else []),
    ]
    said = "".join(part.model_dump().get("text") or "" for part in refusal.message)
    assert (rows in said) is offered
    if kind == "a list":
        assert "give an aggregate (some or every)" in said
    elif code == "NOT_SUPPORTED":
        assert "give an aggregate (count, some, every, or max or min of an ordered category)" in (
            said
        )
    for alternative in aggregates:
        given = _alternative({"column": column}, alternative)
        assert check(shop_document(given), release).refusals == [], given


@pytest.mark.parametrize("kind", [kind for kind in KINDS if "mean" not in KINDS[kind][2]])
def test_an_aggregate_a_column_does_not_take_offers_those_it_takes(
    check: Check, shop: Shop, kind: str
) -> None:
    column, _, aggregates, _ = KINDS[kind]
    release = _kinds(shop)
    for name in [name for name in ("count", "max", "min", "mean") if name not in aggregates]:
        [refusal] = check(shop_document({"column": column, "aggregate": name}), release).refusals
        assert (refusal.code, refusal.path) == (
            "AGGREGATE_NOT_ALLOWED",
            "/views/0/params/columns/0/aggregate",
        )
        offered = [one.text for one in refusal.alternatives or []]
        assert offered == [one for one in aggregates if one != name], name
        for alternative in offered:
            given = _alternative({"column": column}, alternative)
            assert check(shop_document(given), release).refusals == [], given


STARTS = {
    "an aggregate left out": {},
    "a count of rows": {"count": "rows"},
    "a count of rows with where": {"count": "rows", "where": True},
    "a count of rows with bins": {"count": "rows", "bins": [0, 50, 200]},
}


def _starts() -> list[tuple[str, str]]:
    """Each kind with each start its column takes: ``where`` and ``bins`` beside a count of
    rows where the column counts rows, ``bins`` of numbers alone."""
    return [
        (kind, start)
        for kind, (_, _, aggregates, rows) in KINDS.items()
        for start in STARTS
        if not ("where" in STARTS[start] and not rows)
        and not ("bins" in STARTS[start] and "mean" not in aggregates)
    ]


@pytest.mark.parametrize("setting", list(SETTINGS))
@pytest.mark.parametrize(("kind", "start"), _starts())
def test_under_a_disclosure_setting_what_a_refusal_offers_is_never_refused_for_the_same_reason(
    check: Check, shop: Shop, kind: str, start: str, setting: str
) -> None:
    """D379: under each source of *k*, following every alternative a refusal offers, and giving
    the member one names (``bins``), ends in a view that runs and never meets a refusal met on
    the way, whatever ``where`` and ``bins`` the variable gives: no ``count: "rows"`` is
    offered, and a withheld one offers the aggregates that run in its place, with the ``bins``
    and ``values`` they need, keeping its ``where`` and ``bins``."""
    column = KINDS[kind][0]
    disclosure, floor, published, _ = SETTINGS[setting]
    release = _kinds(shop, disclosure)
    given = STARTS[start]
    where = [{"kind": "value", "column": column, "values": VALUES[column]}]
    start_: dict[str, Any] = {
        "column": column,
        **{name: where if name == "where" else value for name, value in given.items()},
    }
    ends = 0
    walks: list[tuple[dict[str, Any], tuple[tuple[str, str | None], ...]]] = [(start_, ())]
    while walks:
        variable, met = walks.pop()
        refusals = check(
            shop_document(variable), release, floor=floor, published=published
        ).refusals
        if not refusals:
            ends += 1
            continue
        [refusal] = refusals
        reason = (refusal.code, refusal.path)
        assert reason not in met, (variable, met)
        assert len(met) < 3, (variable, met)
        texts = [one.text for one in refusal.alternatives or []]
        assert 'count: "rows"' not in texts, variable
        led = [_alternative(variable, one) for one in texts]
        named = _followed(variable, refusal)
        assert led or named is not None, (variable, refusal)
        walks += [(given, (*met, reason)) for given in [*led, *([named] if named else [])]]
    assert ends
    if "count" not in given:
        return
    withheld = check(shop_document(start_), release, floor=floor, published=published).refusals
    if withheld[0].code == RefusalCode.WITHHELD_UNDER_K:
        for alternative in withheld[0].alternatives or []:
            led = _alternative(start_, alternative.text)
            found = check(shop_document(led), release, floor=floor, published=published)
            assert found.refusals == [], led


@pytest.mark.parametrize("setting", list(SETTINGS))
@pytest.mark.parametrize(
    "kind", [*KINDS, *(kind for kind, (_, offered) in SINGLE.items() if offered)]
)
def test_under_a_disclosure_setting_what_a_refusal_of_memberships_offers_ends_in_a_view_that_runs(
    check: Check, shop: Shop, kind: str, setting: str
) -> None:
    """D379, D380: under each source of *k*, following every alternative a refusal of ``each``
    offers (leaving it out, or an aggregate with the values it needs), and giving the member a
    later refusal names (``bins``), ends in a view that runs and never meets a refusal met on
    the way, for a column of every datatype below the unit or on it."""
    column = KINDS[kind][0] if kind in KINDS else SINGLE[kind][0]
    disclosure, floor, published, _ = SETTINGS[setting]
    release = _kinds(shop, disclosure)
    ends = 0
    walks: list[tuple[dict[str, Any], tuple[tuple[str, str | None], ...]]] = [
        ({"column": column, "each": "category"}, ())
    ]
    while walks:
        variable, met = walks.pop()
        refusals = check(
            shop_document(variable), release, floor=floor, published=published
        ).refusals
        if not refusals:
            ends += 1
            continue
        [refusal] = refusals
        reason = (refusal.code, refusal.path)
        assert reason not in met, (variable, met)
        assert len(met) < 3, (variable, met)
        texts = [one.text for one in refusal.alternatives or []]
        led = [
            {"column": column} if one == "leave each out" else _alternative(variable, one)
            for one in texts
        ]
        named = _followed(variable, refusal)
        assert led or named is not None, (variable, refusal)
        walks += [(given, (*met, reason)) for given in [*led, *([named] if named else [])]]
    assert ends


def test_a_count_of_rows_of_what_is_neither_categories_nor_numbers_is_not_supported(
    check: Check, shop: Shop
) -> None:
    found = check(shop_document({"column": "orders.order_id", "count": "rows"}), shop())
    assert _refusals(found) == [("NOT_SUPPORTED", "/views/0/params/columns/0/column")]


def test_a_count_of_rows_of_categories_takes_no_bins(check: Check, shop: Shop) -> None:
    found = check(shop_document({**CHANNELS, "bins": [0, 1]}), shop())
    assert _refusals(found) == [("INVALID_VALUE", "/views/0/params/columns/0/bins")]


def test_150_categories_of_rows_are_listed_and_151_refuse_the_call(
    distributed: Distributed, summarised: Summarised
) -> None:
    view = distributed([CHANNELS])
    listed = {f"v{n:03d}": 1 for n in range(147)}
    found = column(summarised(view, [1], [([counted({147: 1}, listed)], None)]), 0, 0)
    assert len(found["categories"]) == 150
    with pytest.raises(TooManyCategories):
        summarised(view, [1], [([counted({148: 1}, {**listed, "v999": 1})], None)])


def test_an_aggregate_a_column_does_not_take_offers_only_those_its_other_members_allow(
    check: Check, shop: Shop
) -> None:
    """Beside a ``where`` no ``some`` or ``every``, beside an ``empty`` only ``max``, ``min``
    and ``mean``, where it is a value of theirs: each alternative then runs as written (§9.2),
    and one whose ``empty`` is not is refused."""
    release = _kinds(shop)
    where = [{"kind": "value", "column": "orders.channel", "values": ["web"]}]
    cases = [
        ({"column": "orders.channel", "aggregate": "mean", "where": where}, ["count"]),
        ({"column": "orders.channel", "aggregate": "max", "empty": "web"}, []),
        ({"column": "orders.grade", "aggregate": "mean", "empty": "b"}, ["max", "min"]),
        ({"column": "orders.grade", "aggregate": "mean", "empty": 0}, []),
    ]
    for variable, offered in cases:
        [refusal] = check(shop_document(variable), release).refusals
        assert refusal.code == "AGGREGATE_NOT_ALLOWED", variable
        assert [one.text for one in refusal.alternatives or []] == offered, variable
        for name in offered:
            led = {**variable, "aggregate": name}
            assert check(shop_document(led), release).refusals == [], led
    led = {"column": "orders.grade", "aggregate": "max", "empty": 0}
    assert _refusals(check(shop_document(led), release)) == [
        ("INVALID_CONSTANT", "/views/0/params/columns/0/empty")
    ]


STALL_A = {"kind": "value", "column": "visits.stall", "values": ["a"]}
PAID = {"kind": "value", "column": "visits.paid", "values": [True]}
NOT_TAKEN_BELOW_SCOPE: dict[str, tuple[dict[str, Any], list[str], dict[str, str]]] = {
    "mean of the scope column": (
        {"column": "visits.stall", "aggregate": "mean"},
        ["some"],
        {"count": "OPEN_SCOPE", "every": "SCOPE_COLUMN_MENTION"},
    ),
    "max of another column": (
        {"column": "visits.paid", "aggregate": "max"},
        ["some", "every"],
        {"count": "OPEN_SCOPE"},
    ),
    "mean of the scope column restricted in where": (
        {"column": "visits.stall", "aggregate": "mean", "where": [STALL_A]},
        ["count"],
        {},
    ),
    "max of another column, the scope column restricted in where": (
        {"column": "visits.paid", "aggregate": "max", "where": [STALL_A]},
        ["count"],
        {},
    ),
    "max of another column, the scope column open in where": (
        {"column": "visits.paid", "aggregate": "max", "where": [PAID]},
        [],
        {"count": "OPEN_SCOPE"},
    ),
}
"""An aggregate a column below coverage scoped by value does not take, with and without a
``where``, what its refusal offers, and the code of each aggregate it leaves out that the
variable could be written with."""


@pytest.mark.parametrize("kind", list(NOT_TAKEN_BELOW_SCOPE))
def test_below_scoped_coverage_an_aggregate_not_taken_offers_only_those_that_run_there(
    check: Check, scoped: Shop, kind: str
) -> None:
    """D377: the refusal of an aggregate a column does not take offers only those that run with
    the variable's path and ``where``: one that pools rows where every scope column is
    restricted in the ``where`` (``OPEN_SCOPE``), and without one ``some``, and ``every`` of no
    scope column (``SCOPE_COLUMN_MENTION``); beside a ``where``, which they do not take, neither.
    A view of each alternative runs, and of each left out is refused."""
    variable, offered, refused = NOT_TAKEN_BELOW_SCOPE[kind]
    values = ["a"] if variable["column"] == "visits.stall" else [True]
    release = scoped()
    [refusal] = check(shop_document(variable), release).refusals
    assert (refusal.code, refusal.path) == (
        RefusalCode.AGGREGATE_NOT_ALLOWED,
        "/views/0/params/columns/0/aggregate",
    )
    assert [one.text for one in refusal.alternatives or []] == offered
    for name in [*offered, *refused]:
        led = {**variable, "aggregate": name}
        if name in ("some", "every"):
            led["values"] = values
        found = check(shop_document(led), release)
        if name in offered:
            assert found.refusals == [], led
        else:
            [left_out] = found.refusals
            assert left_out.code == refused[name], led


@pytest.mark.parametrize("each", [True, False])
def test_every_of_a_column_looked_up_below_scoped_coverage_is_offered_whatever_its_name(
    check: Check, scoped: Shop, each: bool
) -> None:
    """D377, D380: ``every`` may not mention a scope column of the last step's child row, and a
    column its rows look up is none, though it be named as one is: ``booths.stall``, looked up
    from visits whose coverage is scoped by their own ``stall``, is offered ``some`` and
    ``every`` (``resolve.aggregates_over``'s ``trailing``, as resolution and phase 2 give it),
    and a view of each runs."""
    release = scoped()
    given: dict[str, Any] = {"column": "booths.stall", **({"each": "category"} if each else {})}
    [refusal] = check(shop_document(given), release).refusals
    assert (refusal.code, refusal.path) == (
        RefusalCode.NOT_SUPPORTED,
        f"/views/0/params/columns/0/{'each' if each else 'column'}",
    )
    assert [one.text for one in refusal.alternatives or []] == ["some", "every"]
    for name in ("some", "every"):
        led = {"column": "booths.stall", "aggregate": name, "values": ["a"]}
        assert check(shop_document(led), release).refusals == [], name


def test_a_list_on_the_unit_beside_a_where_is_offered_no_aggregate_since_it_has_no_rows_to_pool(
    check: Check, shop: Shop
) -> None:
    """A list column on the unit has no rows to pool, and its items are asked about by ``some``
    and ``every`` alone, which take no ``where`` (§9.2): beside a ``where`` its aggregate not
    taken is refused offering nothing (D377, D380)."""
    release = shop(extras=[build.column("customers.labels", "list<category>")])
    where = [{"kind": "value", "column": "customers.labels", "values": ["new"]}]
    for aggregate in ("mean", "count", "max"):
        variable = {"column": "customers.labels", "where": where, "aggregate": aggregate}
        refusals = check(shop_document(variable), release).refusals
        assert [(r.code, r.path, r.alternatives) for r in refusals] == [
            (RefusalCode.AGGREGATE_NOT_ALLOWED, "/views/0/params/columns/0/aggregate", [])
        ], aggregate

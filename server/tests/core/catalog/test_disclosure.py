"""The disclosure pass over catalogue statistics, and their references (SPEC §8.1, §8.4, D271,
D272)."""

from typing import Any

import pytest

from aibi.core.catalog.disclosure import References, column_output, disclose_table, effective_k
from aibi.core.schema.results import parse_stat_reference

MANIFEST = "sha256:" + "ab" * 32


def _states(present: int, unknown: int = 0, na: int = 0, nass: int = 0) -> dict[str, int]:
    return {"PRESENT": present, "NOT_APPLICABLE": na, "NOT_ASSESSED": nass, "UNKNOWN": unknown}


def _table(rows: int, **columns: dict[str, Any]) -> dict[str, Any]:
    return {"n_rows": rows, "columns": columns}


def _column(states: dict[str, int], distribution: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"states": states, "distribution": distribution or {"kind": "none", "reason": "text"}}


def _categories(*counts: tuple[str, int], listed: bool = False) -> dict[str, Any]:
    return {
        "kind": "categories",
        "categories": [[value, count] for value, count in counts],
        "multi_membership": listed,
    }


def _histogram(counts: list[int], source: str = "range") -> dict[str, Any]:
    edges = [float(n) for n in range(len(counts) - 1)]
    return {
        "kind": "histogram",
        "scale": "number",
        "from": source,
        "edges": edges,
        "counts": counts,
        "min": 0.0,
        "max": 1.0,
    }


def _d(found: Any) -> Any:
    return found.distributions["c"]


def test_the_effective_setting_is_the_largest_of_the_floor_and_the_dataset_s() -> None:
    assert effective_k(None, None) is None
    assert effective_k(3, None) == 3
    assert effective_k(None, 4) == 4
    assert effective_k(5, 4) == 5


def test_without_a_setting_every_count_is_shown_as_counted() -> None:
    raw = _table(3, c=_column(_states(2, unknown=1), _categories(("a", 1), ("b", 1))))
    found = disclose_table(raw, None)
    assert found.n_rows == 3
    assert found.states["c"] == _states(2, unknown=1)
    assert _d(found) == raw["columns"]["c"]["distribution"]
    assert not found.suppressed


def test_a_small_state_count_is_suppressed_and_so_is_the_smallest_other() -> None:
    raw = _table(20, c=_column(_states(10, unknown=2, na=8)))
    found = disclose_table(raw, 5)
    assert found.states["c"] == {
        "PRESENT": 10,
        "NOT_APPLICABLE": None,
        "NOT_ASSESSED": 0,
        "UNKNOWN": None,
    }
    assert found.n_rows == 20
    assert found.suppressed


def test_two_small_counts_hide_each_other_without_a_third() -> None:
    raw = _table(20, c=_column(_states(16, unknown=2, na=2)))
    found = disclose_table(raw, 5)
    assert found.states["c"]["PRESENT"] == 16
    assert found.states["c"]["UNKNOWN"] is None
    assert found.states["c"]["NOT_APPLICABLE"] is None


def test_a_count_of_zero_is_shown() -> None:
    found = disclose_table(_table(20, c=_column(_states(20))), 5)
    assert found.states["c"] == _states(20)


def test_a_small_table_hides_its_rows_and_every_state_count() -> None:
    raw = _table(3, c=_column(_states(3)), d=_column(_states(2, unknown=1)))
    found = disclose_table(raw, 5)
    assert found.n_rows is None
    assert all(count is None for states in found.states.values() for count in states.values())
    assert found.suppressed


def test_a_distribution_whose_present_count_is_suppressed_is_suppressed_whole() -> None:
    raw = _table(12, c=_column(_states(10, unknown=2), _categories(("a", 5), ("b", 5))))
    found = disclose_table(raw, 5)
    assert found.states["c"]["PRESENT"] is None
    assert _d(found) == {"kind": "none", "reason": "suppressed"}


def test_small_categories_are_pooled_into_one_row() -> None:
    raw = _table(30, c=_column(_states(30), _categories(("a", 20), ("b", 3), ("c", 3), ("d", 4))))
    found = disclose_table(raw, 5)
    assert _d(found) == {
        "kind": "categories",
        "categories": [["a", 20]],
        "multi_membership": False,
        "pooled": 10,
    }
    assert "c" in found.pooled
    assert found.suppressed


def test_a_small_pooled_row_suppresses_the_distribution() -> None:
    raw = _table(24, c=_column(_states(24), _categories(("a", 22), ("b", 2))))
    found = disclose_table(raw, 5)
    assert _d(found) == {"kind": "none", "reason": "suppressed"}


def test_a_list_column_s_pooled_row_has_no_count() -> None:
    raw = _table(
        30, c=_column(_states(30), _categories(("a", 20), ("b", 3), ("c", 4), listed=True))
    )
    found = disclose_table(raw, 5)
    assert _d(found)["pooled"] is None
    assert _d(found)["categories"] == [["a", 20]]


def test_small_bins_merge_the_smallest_first_toward_the_neighbour_with_fewer_values() -> None:
    raw = _table(40, c=_column(_states(40), _histogram([0, 10, 2, 3, 20, 0, 5, 0])))
    found = disclose_table(raw, 5)
    bins = _d(found)["bins"]
    assert [b["count"] for b in bins] == [0, 10, 5, 20, 0, 5, 0]
    merged = bins[2]
    assert (merged["low"], merged["high"]) == (1.0, 3.0)
    assert merged["includes_low"]
    assert not merged["includes_high"]


def test_a_small_bin_merges_with_empty_bins_between_it_and_its_neighbour() -> None:
    raw = _table(26, c=_column(_states(26), _histogram([0, 1, 0, 0, 25, 0])))
    found = disclose_table(raw, 5)
    bins = _d(found)["bins"]
    assert [b["count"] for b in bins] == [0, 26, 0]
    assert (bins[1]["low"], bins[1]["high"], bins[1]["includes_high"]) == (0.0, 4.0, True)


def test_merging_that_leaves_a_small_last_bin_suppresses_the_histogram() -> None:
    raw = _table(20, c=_column(_states(20), _histogram([0, 1, 1, 1, 0])))
    found = disclose_table(raw, 5)
    assert found.states["c"]["PRESENT"] == 20
    assert _d(found) == {"kind": "none", "reason": "suppressed"}


def test_a_histogram_whose_edges_came_from_the_data_is_not_reported_under_a_setting() -> None:
    raw = _table(20, c=_column(_states(20), _histogram([0, 10, 10, 0], source="data")))
    found = disclose_table(raw, 5)
    assert _d(found) == {"kind": "none", "reason": "no_declared_range"}


def test_minima_and_maxima_are_not_reported_under_a_setting() -> None:
    raw = _table(20, c=_column(_states(20), _histogram([0, 10, 10, 0])))
    found = disclose_table(raw, 5)
    assert "min" not in _d(found)
    assert "max" not in _d(found)


def test_the_open_bins_include_neither_edge() -> None:
    raw = _table(20, c=_column(_states(20), _histogram([5, 10, 5])))
    bins = _d(disclose_table(raw, 5))["bins"]
    assert (bins[0]["low"], bins[0]["high"], bins[0]["includes_high"]) == (None, 0.0, False)
    assert (bins[1]["includes_low"], bins[1]["includes_high"]) == (True, True)
    assert (bins[2]["low"], bins[2]["high"], bins[2]["includes_low"]) == (1.0, None, False)


def test_references_name_the_manifest_the_descriptor_and_the_count() -> None:
    references = References(MANIFEST, None)
    reference = references.of("trees.variety", "categories", "a/b c")
    assert reference == f"stat:{MANIFEST}/trees.variety/categories/a~1b%20c"
    assert parse_stat_reference(reference).pointer == "/categories/a~1b c"


@pytest.mark.parametrize("floor", [2, 7])
def test_references_carry_the_floor_whenever_the_deployment_sets_one(floor: int) -> None:
    reference = References(MANIFEST, floor).of("trees", "n_rows")
    assert reference == f"stat:{MANIFEST}/trees/n_rows?floor={floor}"


def test_a_suppressed_count_is_null_with_its_reason() -> None:
    found = References(MANIFEST, None).count(None, "trees", "n_rows")
    dumped = found.model_dump(mode="json")
    assert dumped["count"] is None
    assert dumped["not_estimable"] == {"/count": "suppressed"}


def test_the_smallest_bin_merges_first() -> None:
    raw = _table(40, c=_column(_states(16), _histogram([0, 1, 6, 3, 6])))
    bins = _d(disclose_table(raw, 5))["bins"]
    assert [b["count"] for b in bins] == [0, 7, 9]


def test_a_small_bin_between_neighbours_of_one_size_merges_to_the_left() -> None:
    raw = _table(40, c=_column(_states(14), _histogram([0, 6, 2, 6])))
    bins = _d(disclose_table(raw, 5))["bins"]
    assert [b["count"] for b in bins] == [0, 8, 6]
    assert (bins[1]["low"], bins[1]["high"]) == (0.0, 2.0)


def test_the_open_bin_above_the_last_edge_holds_neither_edge() -> None:
    raw = _table(20, c=_column(_states(20), _histogram([5, 10, 5])))
    above = _d(disclose_table(raw, 5))["bins"][2]
    assert (above["includes_low"], above["includes_high"]) == (False, False)
    assert _d(disclose_table(raw, None))["kind"] == "histogram"


def test_merging_bins_marks_the_table_suppressed_though_no_count_is_hidden() -> None:
    raw = _table(40, c=_column(_states(40), _histogram([0, 10, 2, 28, 0])))
    found = disclose_table(raw, 5)
    assert all(count is not None for count in found.states["c"].values())
    assert found.n_rows == 40
    assert found.suppressed
    assert found.pooled == frozenset({"c"})


def test_rows_are_shown_while_they_are_not_small_whatever_a_column_hides() -> None:
    raw = _table(9, c=_column(_states(0, unknown=1, na=8)), d=_column(_states(9)))
    found = disclose_table(raw, 5)
    assert found.n_rows == 9
    assert found.states["c"] == {
        "PRESENT": 0,
        "NOT_APPLICABLE": None,
        "NOT_ASSESSED": 0,
        "UNKNOWN": None,
    }
    assert found.states["d"] == _states(9)


@pytest.mark.parametrize("value", ["a\ufdd0", "v" * 10_001, "~/" * 5_000])
@pytest.mark.parametrize("k", [None, 2])
def test_categories_an_output_cannot_carry_are_not_reported(value: str, k: int | None) -> None:
    raw = _table(30, c=_column(_states(30), _categories((value, 20), ("b", 10))))
    found = disclose_table(raw, k)
    assert _d(found) == {"kind": "none", "reason": "unrepresentable"}
    output = column_output("t", "c", found, References(MANIFEST, None))
    assert output.distribution.kind == "none"


@pytest.mark.parametrize("value", ["v" * 10_000, "/" * 8_186, "é" * 10_000])
@pytest.mark.parametrize("k", [None, 2])
def test_categories_at_the_text_and_pointer_limits_are_reported(value: str, k: int | None) -> None:
    raw = _table(30, c=_column(_states(30), _categories((value, 20), ("b", 10))))
    found = disclose_table(raw, k)
    assert _d(found)["categories"] == [[value, 20], ["b", 10]]
    output = column_output("t", "c", found, References(MANIFEST, None))
    assert output.distribution.kind == "categories"


@pytest.mark.parametrize("value", ["/" * 8_186 + "a", "~" * 8_187])
def test_a_category_whose_reference_pointer_passes_its_limit_is_not_reported(value: str) -> None:
    raw = _table(30, c=_column(_states(30), _categories((value, 20), ("b", 10))))
    assert _d(disclose_table(raw, None)) == {"kind": "none", "reason": "unrepresentable"}


def test_a_value_an_output_cannot_carry_that_is_pooled_leaves_the_others_reported() -> None:
    raw = _table(30, c=_column(_states(30), _categories(("a\ufdd0", 2), ("b", 25), ("c", 3))))
    found = _d(disclose_table(raw, 5))
    assert (found["categories"], found["pooled"]) == ([["b", 25]], 5)

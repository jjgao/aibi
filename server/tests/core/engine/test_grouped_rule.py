"""The rule that admits grouped coverages to the one-group path (``grouped``, SPEC D449): each
term at the budget and one past it, points where only the sum crosses, a grid, the order the
coverages are taken in, a coverage that does not fit skipped rather than ending the pass, and the
clamp of a table's rows to the effective ``min_cell_count``."""

import itertools

import pytest

from aibi.core.engine import grouped
from aibi.core.engine.grouped import Sizes, admitted, clamped, term
from aibi.core.store.tables import TableSource

B = 1_000_003
"""A budget for these tests, prime, so that no boundary below is a round number."""


@pytest.fixture(autouse=True)
def budget(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(grouped, "ROW_BUDGET", B)


def fits(a: int, p: int, g: int, listings: int) -> bool:
    return admitted({"c": Sizes(a, p, g, listings)}) == {"c"}


def test_the_term_weighs_each_table_by_its_factor() -> None:
    assert term(Sizes(1, 0, 0, 5)) == 4
    assert term(Sizes(0, 1, 0, 5)) == 1
    assert term(Sizes(0, 0, 1, 5)) == 8
    assert term(Sizes(7, 11, 13, 2)) == 4 * 7 + 11 + 5 * 13
    assert grouped.ROW_BUDGET == B


@pytest.mark.parametrize("listings", [1, 2, 7, 64, 1408])
def test_each_term_alone_fits_at_the_budget_and_not_one_row_past_it(listings: int) -> None:
    """The assignment counts 4 to a row, the parent table 1 and the groups 3 + L: each alone, the
    others empty, fits exactly up to the budget."""
    a, g = B // 4, B // (3 + listings)
    assert fits(a, 0, 0, listings)
    assert not fits(a + 1, 0, 0, listings)
    assert fits(0, B, 0, listings)
    assert not fits(0, B + 1, 0, listings)
    assert fits(0, 0, g, listings)
    assert not fits(0, 0, g + 1, listings)


@pytest.mark.parametrize("listings", [1, 7, 64])
@pytest.mark.parametrize("share", [0.25, 0.5, 0.75])
def test_only_the_sum_crosses(listings: int, share: float) -> None:
    """Each table well within the budget alone, their sum at it and one past it: a rule that
    checks each table against the whole budget, or drops one, admits both."""
    a = int(B * share) // 4
    rest = B - 4 * a
    g = rest // (3 + listings)
    p = rest - (3 + listings) * g
    assert 4 * a + p + (3 + listings) * g == B
    assert max(4 * a, p, (3 + listings) * g) < B
    assert fits(a, p, g, listings)
    assert not fits(a, p + 1, g, listings)
    assert not fits(a + 1, p, g, listings)
    assert not fits(a, p, g + 1, listings)


def test_a_grid_of_sizes_is_admitted_exactly_where_the_sum_fits() -> None:
    steps = [0, 1, B // 97, B // 13, B // 8, B // 5, B // 4, B // 3, B]
    for a, p, g, listings in itertools.product(steps, steps, steps, (1, 3, 64)):
        assert fits(a, p, g, listings) == (4 * a + p + (3 + listings) * g <= B)


def test_unknown_sizes_are_never_admitted() -> None:
    assert admitted({"c": None}) == frozenset()
    assert admitted({"b": None, "c": Sizes(0, 0, 0, 1)}) == {"c"}


def test_coverages_are_taken_in_the_order_of_their_ids_not_as_given() -> None:
    half = Sizes(0, B // 2 + 1, 0, 1)
    for order in (["a", "b"], ["b", "a"]):
        assert admitted(dict.fromkeys(order, half)) == {"a"}


def test_a_coverage_that_does_not_fit_is_skipped_and_counts_nothing() -> None:
    """Skip, not stop: a coverage past the budget takes main's path, adds nothing to the sum,
    and the next ones are still taken."""
    big, small = Sizes(0, B + 1, 0, 1), Sizes(0, B // 2, 0, 1)
    assert admitted({"a": big, "b": small, "c": small}) == {"b", "c"}
    assert admitted({"a": small, "b": big, "c": small}) == {"a", "c"}
    assert admitted({"a": Sizes(0, B // 2 + 2, 0, 1), "b": small, "c": Sizes(0, 1, 0, 1)}) == {
        "a",
        "c",
    }


def test_the_budget_is_shared_by_the_coverages_of_one_compiler() -> None:
    """Each fits alone; the second does not fit what the first leaves (not all or nothing, not
    each against the whole budget)."""
    each = Sizes(0, B // 2 + 1, 0, 1)
    assert admitted({"a": each}) == {"a"}
    assert admitted({"a": each, "b": each}) == {"a"}
    assert admitted({"a": Sizes(0, B // 2, 0, 1), "b": Sizes(0, B - B // 2, 0, 1)}) == {"a", "b"}


def test_a_table_below_k_enters_the_rule_with_k_rows() -> None:
    """The clamp (§8.4, D271, D449): a count the catalogue withholds, none included, enters as
    the effective ``min_cell_count``; a count at or above it, or with no setting, as it is."""
    sources = {
        name: TableSource(f"/{name}", frozenset(), rows)
        for name, rows in (("zero", 0), ("below", 4), ("at", 5), ("above", 6), ("unknown", None))
    }
    found = clamped(sources, 5)
    assert {name: source.n_rows for name, source in found.items()} == {
        "zero": 5,
        "below": 5,
        "at": 5,
        "above": 6,
        "unknown": None,
    }
    assert all(found[name].path == f"/{name}" for name in found)
    assert clamped(sources, None) == sources
    assert clamped(sources, 2)["zero"].n_rows == 2
    assert clamped(sources, 2)["below"].n_rows == 4


@pytest.mark.parametrize("k", [2, 5, 7])
def test_no_hidden_count_changes_the_decision(k: int) -> None:
    """For every hidden count of each table (0 to k - 1) and every budget between the rule's
    terms at hidden counts 0 and k, the clamped rule decides one way."""
    for hidden in ("a", "p", "g"):
        for others, listings in itertools.product((k, 3 * k), (1, 3)):
            top = 4 * 3 * k + 3 * k + (3 + listings) * 3 * k
            for budget in range(0, top + 2):
                seen: set[bool] = set()
                for count in range(k):
                    sizes = {"a": others, "p": others, "g": others, hidden: count}
                    source = {
                        table: TableSource(table, frozenset(), sizes[table]) for table in sizes
                    }
                    rows = {table: s.n_rows for table, s in clamped(source, k).items()}
                    one = Sizes(
                        int(rows["a"] or 0), int(rows["p"] or 0), int(rows["g"] or 0), listings
                    )
                    seen.add(term(one) <= budget)
                assert len(seen) == 1, (hidden, others, listings, budget)

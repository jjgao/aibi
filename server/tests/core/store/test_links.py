"""The links a release's descriptors declare, for erasure (D408): coverage links, scope
mappings, the graph of relationships and keys, and what scope mappings compose with it. Read
from descriptors alone, over a library and an orchard."""

from typing import Any

import pytest
from tests.core.store import cover_fixtures as fx

from aibi.core.engine import build
from aibi.core.engine.data import Release
from aibi.core.store.links import (
    Cover,
    Link,
    Scope,
    columns_text,
    compose,
    covers,
    graph,
    link,
    relationships,
    scopes,
)


def test_a_link_keeps_its_pairs_in_order_of_the_child_s_columns() -> None:
    assert link("e", ["y", "x"], "m", ["b", "a"]) == Link("e", ("x", "y"), "m", ("a", "b"))
    assert link("e", ["x", "y"], "m", ["a", "b"]) == link("e", ["y", "x"], "m", ["b", "a"])
    assert columns_text(("x", "y")) == '["x","y"]'


def test_direct_and_grouped_coverage_give_links_into_the_parent() -> None:
    direct, _, _ = fx.covlib(fx.M2, [(1, "m-1")], [1])
    assert covers(direct) == {link("audited", ["loan_id"], "loans", ["loan_id"])}
    grouped, _, _ = fx.grouped("string", ["p-1"], [("t1", "p-1")], [("t1", "A")], [("A", "p-1")])
    assert covers(grouped) == {link("trees_teams", ["tree_id"], "trees", ["tree_id"])}
    assert scopes(grouped) == {Scope("team_pickers", "picker", "pickings", "picker")}


def test_a_composite_coverage_maps_each_column_to_its_parent_column() -> None:
    descriptors, _, _ = fx.ab([(1, 2)], [(1, 2)])
    assert covers(descriptors) == {link("enrolled", ["x", "y"], "members", ["a", "b"])}


def test_all_parents_and_a_coverage_without_its_relationship_give_no_link() -> None:
    descriptors, _, _ = fx.enrol(fx.M2, [(1, "m-1")], fx.M2, declare=False)
    every = [*descriptors, build.coverage("rel:visits.member_id", "all")]
    assert covers(every) == frozenset()
    orphaned = [build.coverage("rel:none.x", {"table": "e", "parent_columns": {"x": "y"}})]
    assert covers(orphaned) == frozenset()


def test_the_graph_holds_every_relationship_and_every_key_as_a_self_edge() -> None:
    descriptors, _, _ = fx.picked("string", ["p-1"], [("t1", "p-1")], [("t1", "p-1")])
    assert relationships(descriptors) == {
        link("pickings", ["tree_id"], "trees", ["tree_id"]),
        link("pickings", ["picker"], "members", ["member_id"]),
    }
    assert graph(descriptors) == relationships(descriptors) | {
        link("members", ["member_id"], "members", ["member_id"]),
        link("trees", ["tree_id"], "trees", ["tree_id"]),
        link("pickings", ["tree_id", "picker"], "pickings", ["tree_id", "picker"]),
    }


def test_a_scope_column_composes_with_a_foreign_key_and_with_a_key() -> None:
    edges = {
        link("pickings", ["picker"], "members", ["member_id"]),
        link("loans", ["loan_id"], "loans", ["loan_id"]),
    }
    found = {
        Scope("picked", "picker", "pickings", "picker"),
        Scope("assessed", "loan", "loans", "loan_id"),
    }
    assert compose(found, edges) == {
        Cover.of(link("picked", ["picker"], "members", ["member_id"])),
        Cover.of(link("assessed", ["loan"], "loans", ["loan_id"])),
    }


def test_a_composite_edge_composes_only_when_every_column_is_scope_mapped() -> None:
    edge = link("visits", ["a", "b"], "members", ["a", "b"])
    one = {Scope("s", "x", "visits", "a")}
    assert compose(one, {edge}) == frozenset()
    both = {*one, Scope("s", "y", "visits", "b")}
    assert compose(both, {edge}) == {Cover.of(link("s", ["x", "y"], "members", ["a", "b"]))}
    elsewhere = {*one, Scope("t", "y", "visits", "b")}
    assert compose(elsewhere, {edge}) == frozenset()


def test_two_scope_columns_standing_for_one_column_are_alternatives_of_one_cover() -> None:
    edge = link("pickings", ["picker"], "members", ["member_id"])
    found = {Scope("p", "a", "pickings", "picker"), Scope("p", "b", "pickings", "picker")}
    assert compose(found, {edge}) == {Cover("p", (("a", "b"),), "members", ("member_id",))}


def test_composition_never_enumerates_the_alternatives_product(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """M2 of PR #88's review: 16 key columns with 3 scope columns standing for each are one
    cover of 16 positions, not 3**16 links (which do not fit in memory): one cover is made."""
    keys = [f"k{i:02d}" for i in range(16)]
    edge = link("visits", keys, "members", keys)
    found = {
        Scope("e", f"s{i:02d}_{j}", "visits", key) for i, key in enumerate(keys) for j in range(3)
    }
    made: list[Cover] = []
    init = Cover.__init__

    def counting(self: Cover, *args: Any) -> None:
        init(self, *args)
        made.append(self)

    monkeypatch.setattr(Cover, "__init__", counting)
    [cover] = compose(found, {edge})
    assert made == [cover]
    assert [len(alternatives) for alternatives in cover.columns] == [3] * 16


def test_a_link_applies_where_the_child_table_has_its_columns() -> None:
    descriptors, _, _ = fx.enrol(fx.M2, [(1, "m-1")], fx.M2)
    release = Release("lib", "sha256:" + "0" * 64, tuple(descriptors), {})
    assert link("enrolled", ["member_id"], "members", ["member_id"]).applies(release)
    assert not link("enrolled", ["staff_ref"], "staff", ["staff_id"]).applies(release)
    assert not link("absent", ["member_id"], "members", ["member_id"]).applies(release)

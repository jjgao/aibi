"""The table graph and its paths (§6.1)."""

from collections.abc import Callable
from itertools import pairwise
from typing import Any

from aibi.core.engine.data import Release
from aibi.core.engine.graph import PATH_SEARCH_STEPS, Edge, Graph, Step, down_steps, render
from aibi.core.schema.limits import MAX_PATH_STEPS

UP = "up"
DOWN = "down"


def test_coverage_tables_are_not_nodes(city: Callable[..., Release]) -> None:
    graph = Graph.of(city())
    assert "checklist_items" not in graph.nodes
    assert "inspection_checklists" not in graph.nodes
    assert "checked_appliances" not in graph.nodes
    assert {"establishments", "inspections", "violations", "owners"} <= graph.nodes


def test_steps_reach_the_other_end_of_their_relationship(city: Callable[..., Release]) -> None:
    graph = Graph.of(city())
    down = Step("rel:inspections.establishment", DOWN)
    up = Step("rel:inspections.establishment", UP)
    assert graph.target("establishments", down) == "inspections"
    assert graph.target("inspections", up) == "establishments"
    assert graph.target("inspections", down) is None
    assert graph.target("establishments", Step("rel:nowhere", UP)) is None
    assert graph.follow("establishments", [down, up, Step("rel:owners.x", UP)]) == [
        "inspections",
        "establishments",
        None,
    ]


def test_the_implicit_path_is_every_simple_path_in_order(city: Callable[..., Release]) -> None:
    graph = Graph.of(city())
    search = graph.paths("establishments", "violations")
    assert search.paths == (
        (Step("rel:inspections.establishment", DOWN), Step("rel:violations.inspection", DOWN)),
    )
    assert not search.exhausted
    # Up then down: a sibling's table is reached through the shared parent.
    [path] = graph.paths("readings", "violations").paths
    assert [step.dir for step in path] == [UP, DOWN]
    assert down_steps(path) == 1


def test_a_table_reaches_itself_only_by_the_empty_path(city: Callable[..., Release]) -> None:
    assert Graph.of(city()).paths("inspections", "inspections").paths == ((),)


def _diamond() -> Graph:
    """a → b → d and a → c → d, as child to parent."""
    edges = [Edge("rel:a.b", "a", "b"), Edge("rel:a.c", "a", "c")]
    edges += [Edge("rel:b.d", "b", "d"), Edge("rel:c.d", "c", "d")]
    return Graph("abcd", edges)


def test_several_paths_are_all_found_sorted_up_to_the_limit() -> None:
    search = _diamond().paths("a", "d", limit=10)
    assert search.paths == (
        (Step("rel:a.b", UP), Step("rel:b.d", UP)),
        (Step("rel:a.c", UP), Step("rel:c.d", UP)),
    )
    assert len(_diamond().paths("a", "d", limit=1).paths) == 1


def test_a_long_chain_is_searched_to_the_step_limit() -> None:
    names = [f"t{index}" for index in range(MAX_PATH_STEPS + 2)]
    edges = [Edge(f"rel:{a}.x", a, b) for a, b in pairwise(names)]
    graph = Graph(names, edges)
    [path] = graph.paths(names[0], names[MAX_PATH_STEPS]).paths
    assert len(path) == MAX_PATH_STEPS
    # The one path to the last table is a step too long: it exists, but is no implicit path.
    beyond = graph.paths(names[0], names[MAX_PATH_STEPS + 1])
    assert (beyond.paths, beyond.too_long, beyond.exhausted) == ((), True, False)


def test_tables_no_path_joins_have_no_path_and_none_too_long() -> None:
    search = Graph(["a", "b", "apart"], [Edge("rel:a.b", "a", "b")]).paths("a", "apart")
    assert (search.paths, search.too_long, search.exhausted) == ((), False, False)
    assert Graph(["a"], []).paths("a", "not_a_node").paths == ()


def test_a_short_path_beside_a_long_one_is_the_only_implicit_path() -> None:
    """t0 → t1 → … → t17 and t17 → t0: the long way round has 17 steps."""
    names = [f"t{index:02}" for index in range(MAX_PATH_STEPS + 2)]
    edges = [Edge(f"rel:{a}.up", a, b) for a, b in pairwise(names)]
    edges.append(Edge("rel:shortcut", names[-1], names[0]))
    assert Graph(names, edges).paths(names[0], names[-1]).paths == ((Step("rel:shortcut", DOWN),),)


def _clique(size: int) -> list[Edge]:
    """Every pair of tables ``t00`` to ``t<size - 1>`` related, by relationships named
    ``rel:m…``, which sort between ``rel:a…`` and ``rel:z…``."""
    names = [f"t{index:02}" for index in range(size)]
    return [
        Edge(f"rel:m.{a}.{b}", a, b) for index, a in enumerate(names) for b in names[index + 1 :]
    ]


def test_the_search_leaves_out_relationships_on_no_path_to_the_target() -> None:
    """A table beside ten related to one another: the search follows the one step to it, and the
    million simple paths among the ten cost it nothing."""
    graph = Graph(
        [f"t{index:02}" for index in range(10)] + ["leaf"],
        [
            *_clique(10),
            Edge("rel:z.leaf", "leaf", "t00"),
        ],
    )
    search = graph.paths("t00", "leaf")
    assert search.paths == ((Step("rel:z.leaf", DOWN),),)
    assert not search.exhausted
    assert len(graph.paths("leaf", "t05", limit=100).paths) == 100


def _dense_block(*, shortcut: bool) -> Graph:
    """Twenty related tables, and a target ten steps from the last two of them, t18 and t19: the
    target shares their block, but a path to it must reach t18 or t19 within six steps, which
    the search, trying the tables in order, does only after all but a few of the others. With
    ``shortcut``, the target is also two steps from t00, by relationships the search tries
    first."""
    edges = _clique(20)
    for last in ("t18", "t19"):
        chain = [last, *(f"{last}.{index}" for index in range(1, 10)), "target"]
        edges += [Edge(f"rel:z.{child}", child, parent) for parent, child in pairwise(chain)]
    if shortcut:
        edges += [Edge("rel:a.one", "one", "t00"), Edge("rel:a.two", "target", "one")]
    return Graph({table for edge in edges for table in (edge.child, edge.parent)}, edges)


def test_the_search_gives_up_after_its_budget() -> None:
    search = _dense_block(shortcut=False).paths("t00", "target")
    assert search.exhausted
    assert search.paths == ()
    assert PATH_SEARCH_STEPS == 100_000


def test_a_search_that_gives_up_after_one_path_found_has_not_decided() -> None:
    search = _dense_block(shortcut=True).paths("t00", "target")
    assert search.exhausted
    assert search.paths == ((Step("rel:a.one", DOWN), Step("rel:a.two", DOWN)),)


def test_paths_render_as_a_via() -> None:
    path = (Step("rel:a.b", UP), Step("rel:b.d", DOWN))
    rendered: Any = render(path)
    assert rendered == '[{"rel":"rel:a.b","dir":"up"},{"rel":"rel:b.d","dir":"down"}]'

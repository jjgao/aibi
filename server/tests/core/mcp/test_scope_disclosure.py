"""Predicates by scope over both transports (SPEC §8.4; D424): round 1's coverage world of #73's
plan, where a category no row holds, beside ``group in [in]`` which a coverage's parent scope
reads, would pin the first predicate's suppressed UNKNOWN count through its map. Under a floor
``run_analysis`` gives the same result over MCP and over HTTP (but its issuance), and it shows
nothing of the scope; without one, the same call shows every count."""

import json
from collections.abc import Callable
from typing import Any

Served = Any
IN = {"kind": "value", "column": "trees.group", "values": ["in"]}
PROBE = {
    "kind": "exists",
    "table": "harvests",
    "where": [{"kind": "value", "column": "harvests.grade", "values": ["C"]}],
}


def _files() -> dict[str, bytes]:
    groups = ["in"] * 5 + ["out"] * 3 + ["?"] * 2
    held = [["A"], ["A"], ["B"], ["B"], ["A", "?"], ["A"], ["B"], [], [], ["A"]]
    trees = ["tree_id,variety,group"] + [
        f"tree{n},apple,{group}" for n, group in enumerate(groups, 1)
    ]
    crops = ["harvest_id,tree_id,kg,grade"] + [
        f"h{n}_{at},tree{n},10,{grade}"
        for n, grades in enumerate(held, 1)
        for at, grade in enumerate(grades)
    ]
    return {
        "trees.csv": ("\n".join(trees) + "\n").encode(),
        "harvests.csv": ("\n".join(crops) + "\n").encode(),
    }


def _set(descriptor: str, pointer: str, value: Any) -> dict[str, Any]:
    return {"op": "set", "descriptor": descriptor, "pointer": pointer, "value": value}


def _category(column: str, values: list[str], missing: dict[str, str]) -> list[dict[str, Any]]:
    return [
        _set(column, "/fields/datatype", "category"),
        _set(column, "/fields/permissible_values", {"values": [{"value": v} for v in values]}),
        _set(column, "/fields/missing_codes", missing),
    ]


EDITS = [
    *_category("trees.group", ["in", "out"], {"?": "NOT_ASSESSED"}),
    *_category("harvests.grade", ["A", "B", "C"], {"?": "NOT_ASSESSED"}),
    _set("harvests", "/fields/role", "event"),
    {
        "op": "put",
        "descriptor": {
            "kind": "coverage",
            "id": "cov:harvests.tree_id",
            "label": "cov:harvests.tree_id",
            "fields": {
                "relationship": "rel:harvests.tree_id",
                "parents": "all",
                "parent_scope": IN,
            },
        },
    },
]
DOCUMENT = {
    "aibi": "1",
    "dataset": "orchard",
    "unit": "trees",
    "cohorts": {
        "all": {"all": [{"kind": "value", "column": "trees.variety", "values": ["apple"]}]}
    },
    "views": [
        {"analysis": "compare.existence", "cohorts": ["all"], "params": {"predicates": [IN, PROBE]}}
    ],
}


def _both(served: Served) -> dict[str, Any]:
    """``run_analysis`` over MCP and over HTTP, which must agree; its one result."""
    over_mcp = served.rpc(
        "tools/call", {"name": "run_analysis", "arguments": {"document": DOCUMENT}}
    )
    result = over_mcp.json()["result"]
    assert not result["isError"], result
    over_http = served.api("run_analysis", {"document": DOCUMENT})
    assert over_http.status_code == 200, over_http.text
    found: dict[str, Any] = result["structuredContent"]
    [one] = found["results"]
    [other] = json.loads(over_http.text)["results"]
    assert {**one, "issuance": None} == {**other, "issuance": None}
    return one


def _proportions(result: dict[str, Any]) -> list[tuple[Any, Any, Any]]:
    [position] = result["values"]["positions"]
    return [
        (
            share["proportion"]["numerator"],
            share["proportion"]["denominator"],
            share["proportion"]["excluded"],
        )
        for share in position["predicates"]
    ]


def test_a_scope_of_dependent_predicates_is_withheld_whole_over_both_transports(
    make_served: Callable[..., Served],
) -> None:
    served = make_served(disclosure={"min_cell_count_floor": 3})
    served.import_("orchard", _files())
    served.curate("orchard", *EDITS)
    result = _both(served)
    assert _proportions(result) == [(None, None, None), (None, None, None)]
    [analysed] = result["analysed"]
    assert [variable["excluded"] for variable in analysed["variables"]] == [None, None]
    flags = {"COVERAGE_PROPOSED", "SCOPE_PARTIAL"}
    assert not [c for c in result["caveats"] if c["code"] in flags and "/values" in c["affects"]]


def test_without_a_floor_the_same_call_shows_every_count(
    make_served: Callable[..., Served],
) -> None:
    served = make_served()
    served.import_("orchard", _files())
    served.curate("orchard", *EDITS)
    group, probe = _proportions(_both(served))
    assert group[:2] == (5, 8)
    assert probe[2]["OUT_OF_SCOPE"] == 3

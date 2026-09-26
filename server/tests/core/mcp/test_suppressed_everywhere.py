"""No count the disclosure settings suppress is given back by anything public (SPEC §8.4, D271,
D276, D277, D279, D311): every tool over MCP and over HTTP, every resource, the curation queue and
the catalogue page, for the published release and the draft, are scanned for each suppressed
count.

The ledger is made so that its suppressed counts are numbers nothing else in an answer holds:
197 unparsed amounts (UNKNOWN), and so 4124 PRESENT ones, suppressed with them; categories of
123 and 198 rows, pooled into 321, which suppresses the distribution with its 3011 and 989; and
tables of 157 and 263 rows. The importer's evidence quotes each of them, and the unparsed note
counts 197, as a control without a floor shows. ``run_analysis`` asks of every account whether its
amount is known (4124 are, 197 not) and whether its kind is ``z`` (123) or not, and of the accounts
of low and high amounts whether their kind is ``w`` (D320).
"""

import json
import re
from collections.abc import Callable
from typing import Any

Served = Any
FLOOR = 500
SUPPRESSED = (197, 4124, 123, 198, 321, 3011, 989, 157, 263)
QUOTED = (197, 4124, 157, 263)
"""Suppressed counts the importer's evidence quotes."""


def ledger() -> dict[str, bytes]:
    kinds = ["x"] * 3011 + ["y"] * 989 + ["z"] * 123 + ["w"] * 198
    accounts = ["account_id,branch_id,amount,kind"]
    for n in range(4321):
        amount = "lots" if n % 22 == 0 else str(n % 50)
        accounts.append(f"a{n + 1},b{1 + n % 157},{amount},{kinds[n]}")
    branches = ["branch_id,city,staff"] + [f"b{n},c{n % 3},{n % 5}" for n in range(1, 158)]
    audits = ["audit_id,account_id,score"] + [
        f"u{n},a{1 + (n * 7) % 4321},{n % 7}" for n in range(1, 264)
    ]
    return {
        name: ("\n".join(lines) + "\n").encode()
        for name, lines in (
            ("accounts.csv", accounts),
            ("branches.csv", branches),
            ("audits.csv", audits),
        )
    }


KIND_Z = {"kind": "value", "column": "accounts.kind", "values": ["z"]}
AMOUNT = "accounts.amount"


def analysis(dataset: str) -> dict[str, Any]:
    return {
        "aibi": "1",
        "dataset": dataset,
        "unit": "accounts",
        "cohorts": {
            "every": {"all": []},
            "low": {"all": [{"kind": "value", "column": AMOUNT, "range": {"lt": 25}}]},
            "high": {"all": [{"kind": "value", "column": AMOUNT, "range": {"gte": 25}}]},
        },
        "views": [
            {
                "analysis": "compare.existence",
                "cohorts": ["every"],
                "params": {
                    "predicates": [
                        {"kind": "value", "column": AMOUNT, "range": {"gte": 0}},
                        KIND_Z,
                        {"not": KIND_Z},
                    ]
                },
            },
            {
                "analysis": "compare.existence",
                "cohorts": ["low", "high"],
                "params": {
                    "predicates": [{"kind": "value", "column": "accounts.kind", "values": ["w"]}]
                },
            },
        ],
    }


def _numbers(text: str) -> set[int]:
    return {int(found) for found in re.findall(r"(?<![0-9A-Za-z.])[0-9]+(?![0-9A-Za-z])", text)}


def _answers(served: Served, release: Any = None) -> list[str]:
    """Every answer an agent can get about the ledger, as text."""
    pinned = {} if release is None else {"release": release}
    found: list[str] = []

    def both(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        over_mcp = served.rpc("tools/call", {"name": name, "arguments": arguments})
        over_http = served.api(name, arguments)
        found.extend((over_mcp.text, over_http.text))
        result: dict[str, Any] = over_mcp.json()["result"]
        assert not result["isError"], result
        return result["structuredContent"]

    both("search_catalog", {})
    both("run_analysis", {"document": analysis("ledger@draft" if release == "draft" else "ledger")})
    described = both("describe_dataset", {"dataset": "ledger", **pinned})
    for table in described["tables"]:
        for column in table["columns"]:
            both("describe_column", {"dataset": "ledger", "column": column["id"], **pinned})
    if release is None:
        both("curation_queue", {"dataset": "ledger"})
        for path in ("/", "/datasets/ledger"):
            shown = served.client.get(path)
            assert shown.status_code == 200, shown.text
            found.append(shown.text)
    listed = served.rpc("resources/list")
    found.append(listed.text)
    label = "draft" if release == "draft" else described["release"]["label"]
    ids = [described["descriptor"]["id"]]
    ids += [t["descriptor"]["id"] for t in described["tables"]]
    ids += [c["id"] for t in described["tables"] for c in t["columns"]]
    ids += [d["id"] for d in (*described["relationships"], *described["coverage"])]
    for descriptor in ids:
        uri = f"aibi://dataset/ledger@{label}/{descriptor}"
        read = served.rpc("resources/read", {"uri": uri})
        assert "result" in read.json(), read.text
        found.append(read.text)
    return found


def test_no_suppressed_count_is_in_any_answer_or_resource(
    make_served: Callable[..., Served],
) -> None:
    served = make_served(disclosure={"min_cell_count_floor": FLOOR})
    served.import_("ledger", ledger())
    served.operator("/operator/datasets/ledger/session/open")
    answers = [*_answers(served), *_answers(served, "draft")]
    for number in SUPPRESSED:
        leaking = [answer for answer in answers if number in _numbers(answer)]
        assert not leaking, (number, leaking[0][:2000])


def test_without_a_floor_the_same_answers_quote_the_counts(
    make_served: Callable[..., Served],
) -> None:
    served = make_served()
    served.import_("ledger", ledger())
    quoted = set().union(*(_numbers(answer) for answer in _answers(served)))
    assert set(QUOTED) <= quoted
    queue = served.tool("curation_queue", {"dataset": "ledger"})["structuredContent"]
    assert [n["count"] for n in queue["queue"]["notes"] if n["kind"] == "unparsed"] == [197]
    analysed = served.tool("run_analysis", {"document": analysis("ledger")})
    assert {197, 4124, 123} <= _numbers(json.dumps(analysed["structuredContent"]))
    column = served.tool("describe_column", {"dataset": "ledger", "column": "accounts.amount"})
    evidence = column["structuredContent"]["descriptor"]["curation"]["/fields/datatype"]
    assert "4124 of 4321" in json.dumps(evidence)

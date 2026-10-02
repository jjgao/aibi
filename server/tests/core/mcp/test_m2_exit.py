"""The exit criterion of M2 (SPEC §15), end to end: the canonical-form tests pass for cohort ids
and cohort-count digests, through the public tools, with the counts computed by the SQL compiler
in a query worker and equal to the reference evaluator's.

An operator imports the non-biomedical lending library with ``aibi``, the CLI, over HTTP
(§11.2), and in one session declares the units of the members' ages and confirms their key and
the loans' relationship to them. The agent is the official MCP Python SDK's client over the
streamable HTTP transport at ``/mcp`` (§11.1): it validates a document and reads its readback,
counts its cohorts, counts documents that differ from it only by cohort names, notes, object key
order, parameters and equivalent syntax, and gets the same cohort ids and count digests; it cites
the ids to ``explain``, which gives the derivation and the SQL as run, and the operator reads the
issuance's document as written with ``aibi issuance`` and prunes the log with ``aibi prune``.
This is the cohort-query loop a person can drive by hand (the PR description gives the
commands).
"""

import json
import shutil
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import anyio
import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from aibi.core.engine.evaluate import evaluate
from aibi.core.engine.resolve import resolve
from aibi.core.schema.loading import load_document

Live = Any
FIXTURES = Path(__file__).resolve().parents[4] / "fixtures"
OLDER = {"kind": "value", "column": "members.age", "range": {"gte": 30}, "units": "a"}
MATHS = {"kind": "value", "column": "members.interests", "values": ["maths"]}
BORROWED = {"kind": "exists", "table": "loans_loans"}


def _cli(live: Live, *argv: str) -> Any:
    code, out, err = live.cli("--json", *argv)
    assert code == 0, (argv, out, err)
    return json.loads(out) if out.strip() else None


def _document(cohorts: Mapping[str, list[Any]], **extra: Any) -> dict[str, Any]:
    return {
        "aibi": "1",
        "dataset": "library",
        "unit": "members",
        "cohorts": {name: {"all": clauses} for name, clauses in cohorts.items()},
        **extra,
    }


def _reversed(value: Any) -> Any:
    """The same JSON value with every object's keys in reverse order."""
    if isinstance(value, dict):
        return {key: _reversed(value[key]) for key in reversed(list(value))}
    if isinstance(value, list):
        return [_reversed(item) for item in value]
    return value


async def _agent(url: str, *calls: tuple[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Connect as an MCP client, check the tools, and make the calls; their structured
    results."""
    async with (
        httpx.AsyncClient(trust_env=False, timeout=60) as http,
        streamable_http_client(f"{url}/mcp", http_client=http) as (read, write, _),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        listed = await session.list_tools()
        names = {tool.name for tool in listed.tools}
        assert {"validate_document", "count_cohort", "explain"} <= names
        found: list[dict[str, Any]] = []
        for name, arguments in calls:
            result = await session.call_tool(name, arguments)
            assert result.structuredContent is not None
            found.append({"isError": result.isError, **result.structuredContent})
        return found


def _counts(answer: dict[str, Any]) -> dict[str, tuple[str, str]]:
    assert not answer["isError"], answer
    return {
        named["cohort"]["data"]: (named["count"]["id"], named["count"]["digest"])
        for named in answer["counts"]
    }


def test_m2_cohort_ids_and_count_digests_hold_through_the_public_tools(
    live: Live, tmp_path: Path
) -> None:
    served = live.served
    library = served.imports / "library"
    shutil.copytree(FIXTURES / "library", library, ignore=shutil.ignore_patterns("formats", "*.md"))
    assert _cli(live, "import", "library", str(library))["label"] == 1
    _cli(live, "session", "open", "library")
    _cli(live, "session", "set", "library", "members.age", "/fields/units", '"a"')
    _cli(live, "session", "confirm", "library", "members", "/fields/primary_key")
    _cli(live, "session", "confirm", "library", "rel:loans_loans.member_id")
    assert _cli(live, "session", "publish", "library")["label"] == 2

    first = _document({"older": [OLDER, MATHS], "borrowers": [BORROWED]})
    renamed = _reversed(
        _document(
            {
                "readers": [
                    {"all": [MATHS]},
                    {"kind": "value", "column": "members.age", "op": ">=", "value": "$age"},
                    MATHS,
                ],
                "lenders": [{"kind": "exists", "table": "$loans", "where": []}],
            },
            params={"age": 30, "loans": "loans_loans"},
            notes="the same question, asked again",
            drafted_by="agent:tester",
        )
    )
    checked, one, again, wrong = anyio.run(
        _agent,
        live.url,
        ("validate_document", {"document": first}),
        ("count_cohort", {"document": first}),
        ("count_cohort", {"document": renamed}),
        (
            "count_cohort",
            {
                "document": _document(
                    {"c": [{"kind": "value", "column": "members.x", "values": [1]}]}
                )
            },
        ),
    )
    assert not checked["isError"]
    assert checked["valid"], checked["refusals"]
    assert [c["status"] for c in checked["cohorts"]] == ["not_issued", "not_issued"]
    ids = {c["cohort"]["data"]: c["id"] for c in checked["cohorts"]}

    counted = _counts(one)
    assert {name: found[0] for name, found in counted.items()} == ids
    repeated = _counts(again)
    assert repeated["readers"] == counted["older"]
    assert repeated["lenders"] == counted["borrowers"]
    assert wrong["isError"]
    assert wrong["refusals"][0]["code"] == "UNKNOWN_COLUMN"
    assert wrong["refusals"][0]["path"] == "/cohorts/c/all/0/column"

    manifest = served.store.latest("library").manifest
    loaded = load_document(json.dumps(first))
    assert loaded.document is not None
    resolution = resolve(
        loaded.document, {"library": served.store.load(manifest)}, loaded.positions
    )
    by_name = {named["cohort"]["data"]: named["count"] for named in one["counts"]}
    for name, cohort in resolution.cohorts.items():
        expected = evaluate(cohort)
        population = by_name[name]["population"]
        assert (population["n_true"], population["n_false"], population["n_unknown"]) == (
            expected.n_true,
            expected.n_false,
            expected.n_unknown,
        )
        assert by_name[name]["readback"][0] == {"text": "Rows of "}

    older = by_name["older"]
    derivation, issuance, validated_again = anyio.run(
        _agent,
        live.url,
        ("explain", {"id": older["id"]}),
        ("explain", {"id": older["issuance"]["id"]}),
        ("validate_document", {"document": first}),
    )
    assert derivation["status"] == "issued"
    assert derivation["derivation"]["object"]["semantics_version"] == 1
    assert derivation["derivation"]["releases"][0]["manifest"] == manifest
    assert "recorded_at" not in derivation["derivation"]
    assert issuance["issuance"]["sql"]["statements"]
    assert {"document", "params"}.isdisjoint(issuance["issuance"])
    assert [c["status"] for c in validated_again["cohorts"]] == ["issued", "issued"]
    logged = _cli(live, "issuance", older["issuance"]["id"])
    assert (logged["document"], logged["params"]) == (first, {})
    assert _cli(live, "prune", "--before", "2000-01-01T00:00:00Z")["pruned"] == 0

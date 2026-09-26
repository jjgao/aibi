"""The MCP transport and the tools over HTTP (SPEC §3.2 A4, A6, §11.1, §11.2, §14, D278–D280)."""

import json
import signal
import socket
import subprocess
import sys
import threading
import time
from collections.abc import AsyncIterator, Callable
from functools import partial
from pathlib import Path
from typing import Any

import anyio
import httpx
import pytest

from aibi.core.api.errors import status_of
from aibi.core.catalog.service import DEADLINE, Deadline
from aibi.core.catalog.tools import RULES, TOOLS
from aibi.core.mcp import server as transport_module
from aibi.core.mcp.calls import Calls, _Place  # pyright: ignore[reportPrivateUsage]
from aibi.core.mcp.server import McpTransport
from aibi.core.operator.auth import hash_token, new_token
from aibi.core.schema.catalog import TOOL_MODELS, CatalogHits
from aibi.core.store.store import StoreRefused

Served = Any
Orchard = Callable[..., dict[str, bytes]]
TOKEN = "aibi_" + "T" * 43
OPERATOR_WORDS = ("import", "withdraw", "erase", "session", "accept", "reject", "publish", "upload")


def _structured(result: dict[str, Any]) -> dict[str, Any]:
    assert result["structuredContent"] == json.loads(result["content"][0]["text"])
    found: dict[str, Any] = result["structuredContent"]
    return found


def test_the_server_lists_exactly_the_catalogue_and_query_tools(served: Served) -> None:
    answered = served.rpc("tools/list")
    assert answered.status_code == 200
    tools = answered.json()["result"]["tools"]
    assert [tool["name"] for tool in tools] == [
        "search_catalog",
        "describe_dataset",
        "describe_column",
        "curation_queue",
        "propose_descriptor",
        "validate_document",
        "count_cohort",
        "explain",
    ]
    assert list(TOOL_MODELS) == [tool.name for tool in TOOLS]
    for tool in tools:
        assert tool["inputSchema"]["type"] == "object"
        assert tool["outputSchema"]["type"] == "object"
        assert RULES in tool["description"]
        writes = tool["name"] in ("propose_descriptor", "count_cohort")
        assert tool["annotations"]["readOnlyHint"] is not writes
        assert tool["annotations"]["idempotentHint"] is tool["annotations"]["readOnlyHint"]


def test_no_tool_is_an_operator_operation(served: Served) -> None:
    names = [tool["name"] for tool in served.rpc("tools/list").json()["result"]["tools"]]
    assert not [name for name in names if any(word in name for word in OPERATOR_WORDS)]
    answered = served.rpc("tools/call", {"name": "import_dataset", "arguments": {}})
    result = answered.json()["result"]
    assert result["isError"]
    refusal = _structured(result)["refusals"][0]
    assert refusal["code"] == "NOT_FOUND"
    assert [a["text"] for a in refusal["alternatives"]] == [tool.name for tool in TOOLS]


def test_initialize_needs_no_session_and_gives_the_rules(served: Served) -> None:
    params = {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "a test", "version": "1"},
    }
    answered = served.rpc("initialize", params)
    assert answered.status_code == 200
    result = answered.json()["result"]
    assert RULES in result["instructions"]
    assert result["capabilities"]["tools"] is not None
    assert result["capabilities"]["resources"] is not None
    assert "mcp-session-id" not in answered.headers


def test_a_tool_s_output_is_its_structured_content_and_its_text(
    served: Served, orchard: Orchard
) -> None:
    served.import_("orchard", orchard())
    result = served.tool("search_catalog", {"text": "tree"})
    assert not result["isError"]
    found = _structured(result)
    assert [hit["dataset"] for hit in found["hits"]] == ["orchard"]
    over_http = served.api("search_catalog", {"text": "tree"})
    assert over_http.status_code == 200
    assert over_http.json() == found


def test_a_refused_call_is_a_tool_error_with_its_refusals(served: Served, orchard: Orchard) -> None:
    served.import_("orchard", orchard())
    result = served.tool("describe_column", {"dataset": "orchard", "column": "trees.colour"})
    assert result["isError"]
    refusal = _structured(result)["refusals"][0]
    assert (refusal["code"], refusal["path"]) == ("UNKNOWN_COLUMN", "/column")
    unknown = served.tool("describe_dataset", {"dataset": "orchard", "colour": "red"})
    assert _structured(unknown)["refusals"][0]["code"] == "UNKNOWN_MEMBER"
    over_http = served.api("describe_dataset", {"dataset": "nothing"})
    assert over_http.status_code == 404
    assert over_http.json()["refusals"][0]["code"] == "UNKNOWN_DATASET"


@pytest.mark.parametrize(
    ("body", "rpc", "code"),
    [
        (
            b'{"jsonrpc": "2.0", "jsonrpc": "2.0", "id": 1, "method": "tools/list"}',
            -32700,
            "DUPLICATE_KEY",
        ),
        (b"[" * 70 + b"]" * 70, -32700, "LIMIT_EXCEEDED"),
        (b'[{"jsonrpc": "2.0", "id": 1, "method": "tools/list"}]', -32600, "WRONG_TYPE"),
        (b'{"jsonrpc": "2.0", "id": NaN, "method": "tools/list"}', -32700, "NON_FINITE_NUMBER"),
        (b"\xff", -32700, "INVALID_JSON"),
    ],
)
def test_a_body_the_loader_refuses_never_reaches_the_sdk(
    served: Served, body: bytes, rpc: int, code: str
) -> None:
    answered = served.client.post(
        "/mcp", content=body, headers={"Content-Type": "application/json", **_accept()}
    )
    assert answered.status_code == 400
    error = answered.json()["error"]
    assert error["code"] == rpc
    assert error["data"]["refusals"][0]["code"] == code


def _accept() -> dict[str, str]:
    return {"Accept": "application/json, text/event-stream"}


def test_the_transport_answers_post_alone(served: Served) -> None:
    for method in ("GET", "DELETE", "PUT"):
        answered = served.client.request(method, "/mcp", headers=_accept())
        assert answered.status_code == 405
        assert answered.json()["refusals"][0]["code"] == "METHOD_NOT_ALLOWED"
    assert served.client.post("/mcp/", json={}).status_code == 404


def test_request_protection_guards_the_transport(served: Served) -> None:
    other = served.rpc("tools/list", headers={"Origin": "https://elsewhere.example"})
    assert other.status_code == 403
    assert other.json()["refusals"][0]["code"] == "ORIGIN_NOT_ALLOWED"
    rebound = served.rpc("tools/list", headers={"Host": "attacker.example"})
    assert rebound.status_code == 400
    assert rebound.json()["refusals"][0]["code"] == "HOST_NOT_ALLOWED"


def test_descriptors_are_resources(served: Served, orchard: Orchard) -> None:
    served.import_("orchard", orchard())
    listed = served.rpc("resources/list").json()["result"]["resources"]
    uris = [resource["uri"] for resource in listed]
    assert "aibi://dataset/orchard@1/dataset" in uris
    assert "aibi://concept/core:person" in uris
    templates = served.rpc("resources/templates/list").json()["result"]["resourceTemplates"]
    assert next(t["uriTemplate"] for t in templates) == (
        "aibi://dataset/{dataset}@{release}/{descriptor}"
    )
    read = served.rpc("resources/read", {"uri": "aibi://dataset/orchard@1/trees.variety"})
    contents = read.json()["result"]["contents"][0]
    assert contents["mimeType"] == "application/json"
    assert json.loads(contents["text"])["id"] == "trees.variety"


def test_a_resource_that_is_not_there_is_an_error_with_its_refusals(
    served: Served, orchard: Orchard
) -> None:
    served.import_("orchard", orchard())
    answered = served.rpc("resources/read", {"uri": "aibi://dataset/orchard@1/shrubs"})
    error = answered.json()["error"]
    assert error["code"] == -32002
    assert error["data"]["refusals"][0]["code"] == "NOT_FOUND"


def test_the_tools_over_http_take_json_alone(served: Served) -> None:
    answered = served.client.post(
        "/api/tools/search_catalog", content=b"{}", headers={"Content-Type": "text/plain"}
    )
    assert answered.status_code == 415
    assert answered.json()["refusals"][0]["code"] == "UNSUPPORTED_MEDIA_TYPE"
    assert served.api("search_catalog", {}).status_code == 200
    assert served.client.get("/api/tools/search_catalog").status_code == 405


def test_a_secret_given_to_a_tool_is_neither_stored_nor_repeated(
    served: Served, orchard: Orchard
) -> None:
    served.import_("orchard", orchard())
    arguments = {
        "dataset": "orchard",
        "agent": "bot",
        "descriptor": "trees",
        "pointer": "/definition",
        "value": "Trees",
        "evidence": f"see {TOKEN}",
    }
    answered = served.rpc("tools/call", {"name": "propose_descriptor", "arguments": arguments})
    assert TOKEN not in answered.text
    assert answered.status_code == 400
    error = answered.json()["error"]
    assert error["code"] == -32602
    assert error["data"]["refusals"][0]["path"] == "/params/arguments/evidence"
    column = {"dataset": "orchard", "column": f"trees.{TOKEN.lower()}"}
    refused = served.rpc("tools/call", {"name": "describe_column", "arguments": column})
    assert TOKEN.lower() not in refused.text
    over_http = served.api("propose_descriptor", arguments)
    assert over_http.status_code == 422
    assert TOKEN not in over_http.text
    assert served.store.db.connection.execute("SELECT count(*) FROM proposals").fetchone()[0] == 0


def test_a_failure_inside_a_tool_is_answered_without_its_message(
    served: Served, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(*args: Any) -> Any:
        raise ValueError(f"leaked {TOKEN}")

    monkeypatch.setattr(type(served.calls.catalog), "search_catalog", fail)
    answered = served.rpc("tools/call", {"name": "search_catalog", "arguments": {}})
    result = answered.json()["result"]
    assert result["isError"]
    assert _structured(result)["refusals"][0]["code"] == "INTERNAL_ERROR"
    assert "leaked" not in answered.text
    assert served.api("search_catalog", {}).json()["refusals"][0]["code"] == "INTERNAL_ERROR"


def test_a_call_past_its_wall_clock_is_refused(
    make_served: Callable[..., Served], monkeypatch: pytest.MonkeyPatch
) -> None:
    served = make_served(seconds=0.2)

    def slow(*args: Any) -> Any:
        time.sleep(1)
        raise AssertionError("never answered")

    monkeypatch.setattr(type(served.calls.catalog), "search_catalog", slow)
    result = served.tool("search_catalog", {})
    refusal = _structured(result)["refusals"][0]
    assert refusal["code"] == "LIMIT_EXCEEDED"
    assert refusal["limit"] == {"name": "tool_seconds", "max": 1}
    over_http = served.api("search_catalog", {})
    assert over_http.status_code == 503


def test_calls_run_at_most_so_many_at_once() -> None:
    running = 0
    most = 0

    def work() -> int:
        nonlocal running, most
        running += 1
        most = max(most, running)
        time.sleep(0.05)
        running -= 1
        return 1

    calls = Calls(None, concurrent=2)  # type: ignore[arg-type]

    async def main() -> list[object]:
        found: list[object] = []
        async with anyio.create_task_group() as group:
            for _ in range(6):

                async def one() -> None:
                    found.append(await calls.run(work))

                group.start_soon(one)
        return found

    assert anyio.run(main) == [1] * 6
    assert most == 2


def test_a_body_that_stalls_is_refused_with_an_upload_s_limit() -> None:
    async def stalled() -> AsyncIterator[bytes]:
        yield b"{"
        await anyio.sleep(5)
        yield b"}"

    calls = Calls(None, body_idle_seconds=0.05)  # type: ignore[arg-type]

    async def main() -> StoreRefused:
        with pytest.raises(StoreRefused) as slow:
            await calls.deadlines(100_000).read(stalled())
        return slow.value

    refusal = anyio.run(main).refusal
    assert refusal.code == "LIMIT_EXCEEDED"
    assert refusal.limit is not None
    assert refusal.limit.name == "tool_body_idle_seconds"
    assert status_of(refusal) == 408


def test_a_body_that_trickles_past_its_length_s_deadline_is_refused() -> None:
    async def trickle() -> AsyncIterator[bytes]:
        while True:
            await anyio.sleep(0.02)
            yield b" "

    calls = Calls(None, body_idle_seconds=0.1, body_min_bytes_per_second=1_000)  # type: ignore[arg-type]

    async def main() -> StoreRefused:
        with pytest.raises(StoreRefused) as slow:
            await calls.deadlines(100).read(trickle())
        return slow.value

    refusal = anyio.run(main).refusal
    assert refusal.limit is not None
    assert refusal.limit.name == "upload_seconds"


def test_a_body_that_keeps_sending_within_its_deadline_is_read() -> None:
    async def trickle() -> AsyncIterator[bytes]:
        for chunk in (b"{", b'"a"', b":1}"):
            await anyio.sleep(0.01)
            yield chunk

    calls = Calls(None, body_idle_seconds=1)  # type: ignore[arg-type]
    assert anyio.run(lambda: calls.deadlines(7).read(trickle())) == b'{"a":1}'


def test_the_server_s_tool_calls_take_the_configuration_s_body_deadlines(
    make_served: Callable[..., Served],
) -> None:
    served = make_served(
        imports_config={"upload_idle_seconds": 7, "upload_min_bytes_per_second": 2048},
    )
    assert (served.calls.body_idle_seconds, served.calls.body_min_bytes_per_second) == (10, 2048)
    assert served.calls.max_body_bytes == served.config.server.max_body_bytes
    shorter = make_served(name="shorter", server_config={"tool_body_idle_seconds": 3})
    assert shorter.calls.body_idle_seconds == 3
    assert served.calls.client_key("2001:db8::1") == served.calls.client_key("2001:db8::2")
    assert served.calls.client_key("2001:db8:0:1::1") != served.calls.client_key("2001:db8::1")


def test_a_body_that_is_not_json_or_a_client_that_takes_no_json_is_refused_first(
    served: Served,
) -> None:
    message = b'{"jsonrpc": "2.0", "id": 1, "method": "tools/list"}'
    typed = served.client.post(
        "/mcp", content=message, headers={"Content-Type": "text/plain", **_accept()}
    )
    assert typed.status_code == 415
    assert typed.json()["error"]["data"]["refusals"][0]["code"] == "UNSUPPORTED_MEDIA_TYPE"
    accepting = served.client.post(
        "/mcp", content=message, headers={"Content-Type": "application/json", "Accept": "text/html"}
    )
    assert accepting.status_code == 406
    assert accepting.json()["error"]["data"]["refusals"][0]["code"] == "INVALID_VALUE"


# --- The pool, the envelope, proposals' rate -----------------------------------------------------


def test_a_call_past_its_limit_keeps_its_place_until_its_thread_returns() -> None:
    running = 0
    most = 0
    lock = threading.Lock()
    release = threading.Event()

    def blocking() -> int:
        nonlocal running, most
        with lock:
            running += 1
            most = max(most, running)
        release.wait(10)
        with lock:
            running -= 1
        return 1

    calls = Calls(None, seconds=0.2, concurrent=2)  # type: ignore[arg-type]

    async def main() -> tuple[list[Any], Any]:
        answered = [await calls.run(blocking, client=f"192.0.2.{n}") for n in range(6)]
        release.set()
        with anyio.fail_after(10):
            while running:
                await anyio.sleep(0.01)
        return answered, await calls.run(lambda: 7)

    answered, after = anyio.run(main)
    limits = [found[0].limit.name for found in answered]
    assert limits == ["tool_seconds"] * 2 + ["tool_calls"] * 4
    assert most == 2
    assert after == 7
    busy = answered[-1][0]
    assert busy.code == "LIMIT_EXCEEDED"
    assert (busy.limit.max, status_of(busy)) == (2, 503)


def test_a_call_that_fails_gives_its_place_back() -> None:
    calls = Calls(None, concurrent=1)  # type: ignore[arg-type]

    def fail() -> int:
        raise ValueError(TOKEN)

    async def main() -> list[Any]:
        return [await calls.run(fail), await calls.run(lambda: 1)]

    failed, after = anyio.run(main)
    assert failed[0].code == "INTERNAL_ERROR"
    assert after == 1


def test_a_call_answered_before_its_thread_began_gives_its_place_back_and_never_runs() -> None:
    ran: list[str] = []
    calls = Calls(None, seconds=0.2, concurrent=1)  # type: ignore[arg-type]

    async def main() -> tuple[Any, Any]:
        _, threads = calls._places()  # pyright: ignore[reportPrivateUsage]
        borrowed = object()
        await threads.acquire_on_behalf_of(borrowed)
        try:
            unstarted = await calls.run(lambda: ran.append("late"))
        finally:
            threads.release_on_behalf_of(borrowed)
        return unstarted, await calls.run(lambda: 7)

    unstarted, after = anyio.run(main)
    assert unstarted[0].limit.name == "tool_seconds"
    assert after == 7
    assert ran == []


def test_a_place_given_back_before_its_thread_began_runs_nothing_and_is_not_given_twice() -> None:
    given: list[int] = []
    ran: list[str] = []
    place = _Place(lambda: given.append(1))

    async def main() -> object:
        assert place.unstarted()
        return await anyio.to_thread.run_sync(place.run, lambda: ran.append("late"))

    assert anyio.run(main) is None
    assert (ran, given) == ([], [])
    assert not place.unstarted()


def test_tool_calls_run_on_threads_of_their_own_while_the_shared_threads_are_taken() -> None:
    calls = Calls(None, seconds=2, concurrent=1)  # type: ignore[arg-type]

    async def main() -> object:
        shared = anyio.to_thread.current_default_thread_limiter()
        borrowed = [object() for _ in range(int(shared.total_tokens))]
        for holder in borrowed:
            await shared.acquire_on_behalf_of(holder)
        try:
            return await calls.run(lambda: 7)
        finally:
            for holder in borrowed:
                shared.release_on_behalf_of(holder)

    assert anyio.run(main) == 7


async def _all_given_back(calls: Calls) -> None:
    """Wait until every place is back, so that no thread outlives the event loop."""
    free, _ = calls._places()  # pyright: ignore[reportPrivateUsage]
    with anyio.fail_after(10):
        while free.value < calls.concurrent:
            await anyio.sleep(0.01)


def test_one_client_runs_at_most_its_share_of_the_places_and_others_still_get_one() -> None:
    release = threading.Event()
    calls = Calls(None, seconds=0.5, concurrent=4, per_client=2)  # type: ignore[arg-type]

    def blocking() -> int:
        release.wait(10)
        return 1

    async def main() -> tuple[list[Any], Any]:
        flood: list[Any] = []
        other: Any = None
        async with anyio.create_task_group() as group:
            for _ in range(3):

                async def one() -> None:
                    flood.append(await calls.run(blocking, client="192.0.2.1"))

                group.start_soon(one)
            await anyio.sleep(0.1)
            other = await calls.run(lambda: 2, client="198.51.100.7")
            await anyio.sleep(0.6)
            release.set()
        await _all_given_back(calls)
        return flood, other

    flood, other = anyio.run(main)
    assert other == 2
    refused = [found[0] for found in flood if isinstance(found, list)]
    assert len(refused) == 3
    assert sorted(r.limit.name for r in refused) == [
        "client_tool_calls",
        "tool_seconds",
        "tool_seconds",
    ]
    share = next(r for r in refused if r.limit.name == "client_tool_calls")
    assert (share.code, share.limit.max, status_of(share)) == ("LIMIT_EXCEEDED", 2, 503)


def test_clients_are_counted_by_the_key_of_their_address() -> None:
    release = threading.Event()
    calls = Calls(
        None,  # type: ignore[arg-type]
        seconds=0.3,
        concurrent=4,
        per_client=1,
        client_key=lambda address: address.rsplit(":", 1)[0],
    )

    async def main() -> Any:
        found: Any = None
        async with anyio.create_task_group() as group:
            group.start_soon(partial(calls.run, lambda: release.wait(10), client="2001:db8::1"))
            await anyio.sleep(0.05)
            found = await calls.run(lambda: 3, client="2001:db8::2")
            release.set()
        await _all_given_back(calls)
        return found

    refused = anyio.run(main)
    assert refused[0].limit.name == "client_tool_calls"


class _Blocking:
    """A catalogue whose searches without text wait until ``release`` is set."""

    token_digest = None

    def __init__(self) -> None:
        self.release = threading.Event()
        self.searching = 0

    def search_catalog(self, request: Any) -> CatalogHits:
        if request.text is None:
            self.searching += 1
            self.release.wait(10)
        return CatalogHits(hits=[], total=0, next_offset=None, caveats=[])


def _scope(client: str, length: int) -> dict[str, Any]:
    return {
        "type": "http",
        "method": "POST",
        "path": "/mcp",
        "raw_path": b"/mcp",
        "root_path": "",
        "scheme": "http",
        "http_version": "1.1",
        "query_string": b"",
        "server": ("testserver", 80),
        "client": (client, 50000),
        "headers": [
            (b"content-type", b"application/json"),
            (b"accept", b"application/json, text/event-stream"),
            (b"content-length", str(length).encode()),
        ],
    }


async def _asked(
    transport: McpTransport, client: str, message: dict[str, Any]
) -> tuple[int, dict[str, Any]]:
    body = json.dumps(message).encode()
    sent: list[dict[str, Any]] = []
    given = False

    async def receive() -> dict[str, Any]:
        nonlocal given
        if not given:
            given = True
            return {"type": "http.request", "body": body, "more_body": False}
        await anyio.sleep_forever()
        raise AssertionError

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    await transport(_scope(client, len(body)), receive, send)  # type: ignore[arg-type]
    status = next(m["status"] for m in sent if m["type"] == "http.response.start")
    written = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return status, json.loads(written)


def _call(id: int, **arguments: Any) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": id,
        "method": "tools/call",
        "params": {"name": "search_catalog", "arguments": arguments},
    }


def test_a_flood_of_one_client_s_calls_starves_no_other_client_s_pings_or_calls() -> None:
    catalog = _Blocking()
    calls = Calls(catalog, seconds=5, concurrent=4, per_client=2)  # type: ignore[arg-type]
    transport = McpTransport(calls)

    ping = {"jsonrpc": "2.0", "id": 1, "method": "ping"}

    async def main() -> tuple[float, int, Any, Any]:
        took, running, pinged, called = 0.0, 0, None, None
        async with transport.lifespan(), anyio.create_task_group() as group:
            for n in range(12):
                group.start_soon(_asked, transport, "192.0.2.1", _call(n))
            with anyio.fail_after(5):
                while catalog.searching < 2:
                    await anyio.sleep(0.01)
            began = anyio.current_time()
            pinged = await _asked(transport, "198.51.100.7", ping)
            took = anyio.current_time() - began
            running = catalog.searching
            called = await _asked(transport, "198.51.100.7", _call(99, text="x"))
            catalog.release.set()
        await _all_given_back(calls)
        return took, running, pinged, called

    took, running, pinged, called = anyio.run(main)
    assert pinged == (200, {"jsonrpc": "2.0", "id": 1, "result": {}})
    assert took < 1
    assert running == 2
    assert called[0] == 200
    assert called[1]["result"]["isError"] is False


def test_a_small_message_is_read_without_a_place_while_every_place_is_taken() -> None:
    catalog = _Blocking()
    calls = Calls(catalog, seconds=5, concurrent=2, per_client=2)  # type: ignore[arg-type]
    transport = McpTransport(calls)
    ping = {"jsonrpc": "2.0", "id": 1, "method": "ping"}

    async def main() -> tuple[float, Any]:
        took, pinged = 0.0, None
        async with transport.lifespan(), anyio.create_task_group() as group:
            for n in range(2):
                group.start_soon(_asked, transport, "192.0.2.1", _call(n))
            with anyio.fail_after(5):
                while catalog.searching < 2:
                    await anyio.sleep(0.01)
            began = anyio.current_time()
            pinged = await _asked(transport, "198.51.100.7", ping)
            took = anyio.current_time() - began
            catalog.release.set()
        await _all_given_back(calls)
        return took, pinged

    took, pinged = anyio.run(main)
    assert pinged == (200, {"jsonrpc": "2.0", "id": 1, "result": {}})
    assert took < 1


def test_the_envelope_check_and_the_call_share_one_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    catalog = _Blocking()
    catalog.release.set()
    checked = transport_module._checked

    def slow(body: bytes, digest: bytes | None) -> Any:
        time.sleep(0.4)
        return checked(body, digest)

    def slower(request: Any) -> CatalogHits:
        time.sleep(0.4)
        return CatalogHits(hits=[], total=0, next_offset=None, caveats=[])

    monkeypatch.setattr(transport_module, "_checked", slow)
    monkeypatch.setattr(catalog, "search_catalog", slower)
    calls = Calls(catalog, seconds=0.6)  # type: ignore[arg-type]
    transport = McpTransport(calls)
    large = _call(1)
    large["params"]["_meta"] = {"pad": "x" * transport_module.INLINE_BYTES}

    async def main() -> tuple[int, dict[str, Any]]:
        async with transport.lifespan():
            answered = await _asked(transport, "192.0.2.1", large)
        await _all_given_back(calls)
        return answered

    status, answered = anyio.run(main)
    assert status == 200
    result = answered["result"]
    assert result["isError"]
    assert result["structuredContent"]["refusals"][0]["limit"]["name"] == "tool_seconds"


def _post(served: Served, message: Any) -> httpx.Response:
    return served.client.post(
        "/mcp",
        content=json.dumps(message).encode(),
        headers={"Content-Type": "application/json", **_accept()},
    )


@pytest.mark.parametrize(
    ("message", "rpc", "code", "path", "id"),
    [
        ({"jsonrpc": "2.0", "id": 7, "method": "prompts/list"}, -32601, "NOT_FOUND", "/method", 7),
        (
            {"jsonrpc": "2.0", "method": "notifications/nothing"},
            -32601,
            "NOT_FOUND",
            "/method",
            None,
        ),
        ({"jsonrpc": "2.0", "id": {"a": 1}, "method": "ping"}, -32600, "WRONG_TYPE", "/id", None),
        ({"jsonrpc": "2.0", "id": True, "method": "ping"}, -32600, "WRONG_TYPE", "/id", None),
        ({"jsonrpc": "2.0", "id": None, "method": "ping"}, -32600, "WRONG_TYPE", "/id", None),
        ({"jsonrpc": "1.0", "id": 3, "method": "ping"}, -32600, "INVALID_VALUE", "/jsonrpc", 3),
        ({"jsonrpc": "2.0", "id": 3}, -32600, "WRONG_TYPE", "/method", 3),
        ({"jsonrpc": "2.0", "id": 3, "method": 5}, -32600, "WRONG_TYPE", "/method", 3),
        (
            {"jsonrpc": "2.0", "id": 3, "method": "tools/list", "params": []},
            -32602,
            "WRONG_TYPE",
            "/params",
            3,
        ),
        (
            {"jsonrpc": "2.0", "id": "x", "method": "tools/call", "params": {"arguments": {}}},
            -32602,
            "MISSING_MEMBER",
            "/params/name",
            "x",
        ),
        (
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "search_catalog", "arguments": "all"},
            },
            -32602,
            "WRONG_TYPE",
            "/params/arguments",
            3,
        ),
        (
            {"jsonrpc": "2.0", "id": 3, "method": "resources/read", "params": {"uri": 5}},
            -32602,
            "WRONG_TYPE",
            "/params/uri",
            3,
        ),
        (
            {"jsonrpc": "2.0", "id": 3, "method": "initialize", "params": {"capabilities": {}}},
            -32602,
            "MISSING_MEMBER",
            "/params/protocolVersion",
            3,
        ),
        (
            {
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {"name": "search_catalog", "arguments": {}},
            },
            -32601,
            "NOT_FOUND",
            "/method",
            None,
        ),
        (
            {"jsonrpc": "2.0", "id": 4, "method": "notifications/initialized"},
            -32601,
            "NOT_FOUND",
            "/method",
            4,
        ),
    ],
)
def test_a_message_that_is_not_a_request_the_server_answers_is_refused_before_the_sdk(
    served: Served, message: Any, rpc: int, code: str, path: str, id: Any
) -> None:
    answered = _post(served, message)
    assert answered.status_code == 400
    found = answered.json()
    assert (found["id"], found["error"]["code"]) == (id, rpc)
    refusal = found["error"]["data"]["refusals"][0]
    assert (refusal["code"], refusal["path"]) == (code, path)


@pytest.mark.parametrize(
    "message",
    [
        {"jsonrpc": TOKEN, "id": 1, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": TOKEN, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 1, "method": TOKEN},
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "x", TOKEN: 1}},
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "search_catalog", "arguments": TOKEN},
        },
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": TOKEN},
        },
        {"jsonrpc": "2.0", "id": 1, "method": "resources/read", "params": {"uri": TOKEN}},
    ],
)
def test_a_message_that_holds_a_secret_is_refused_without_it(served: Served, message: Any) -> None:
    answered = _post(served, message)
    assert answered.status_code == 400
    assert TOKEN not in answered.text
    assert TOKEN[5:] not in answered.text
    found = answered.json()
    assert found["id"] is None
    assert found["error"]["code"] in (-32600, -32602)
    assert found["error"]["data"]["refusals"][0]["code"] == "INVALID_VALUE"


def test_the_configured_token_is_refused_inside_a_longer_word(served: Served) -> None:
    arguments = {"text": f"x{served.token}y"}
    answered = served.rpc("tools/call", {"name": "search_catalog", "arguments": arguments})
    assert answered.status_code == 400
    assert served.token not in answered.text
    assert answered.json()["error"]["data"]["refusals"][0]["path"] == "/params/arguments/text"


def test_an_agent_s_proposals_are_admitted_at_their_own_rate_per_client(
    make_served: Callable[..., Served], orchard: Orchard
) -> None:
    served = make_served(server_config={"rates": {"proposals": {"per_minute": 1, "burst": 1}}})
    served.import_("orchard", orchard())
    arguments = {
        "dataset": "orchard",
        "agent": "bot",
        "descriptor": "trees.height_m",
        "pointer": "/fields/units",
        "value": "m",
    }
    assert served.api("propose_descriptor", arguments).status_code == 200
    again = served.api("propose_descriptor", {**arguments, "value": "cm"})
    assert again.status_code == 429
    assert again.json()["refusals"][0]["limit"]["name"] == "proposal_requests"
    result = served.tool("propose_descriptor", {**arguments, "value": "mm"})
    assert result["isError"]
    assert _structured(result)["refusals"][0]["limit"] == {"name": "proposal_requests", "max": 1}
    assert served.tool("search_catalog", {})["isError"] is False


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
        return port


def test_a_real_server_s_log_and_answers_hold_no_secret_a_message_carried(
    tmp_path: Path,
) -> None:
    token = new_token()
    port = _free_port()
    (tmp_path / "imports").mkdir()
    config = tmp_path / "aibi.toml"
    config.write_text(
        f'[server]\nport = {port}\n[curator]\ntoken_hash = "{hash_token(token)}"\n'
        '[storage]\ndata = "data"\nimports = ["imports"]\n'
    )
    config.chmod(0o600)
    probe = "import sys\nfrom aibi.core.api.serve import main\nsys.exit(main(sys.argv[1:]))\n"
    server = subprocess.Popen(
        [sys.executable, "-c", probe, "serve", "--config", str(config)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    url = f"http://127.0.0.1:{port}"
    messages: list[Any] = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "x", "arguments": token},
        },
        {"jsonrpc": token, "id": 1, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": token, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 1, "method": token},
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": token, "capabilities": {}, "clientInfo": {"name": 1}},
        },
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "describe_dataset", "arguments": {"dataset": f"d{token}"}},
        },
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "resources/read",
            "params": {"uri": f"aibi://{token}"},
        },
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": 5, "clientInfo": {}},
        },
    ]
    answers: list[str] = []
    try:
        with httpx.Client(base_url=url, trust_env=False, timeout=20) as client:
            deadline = time.monotonic() + 30
            while True:
                try:
                    if client.get("/api/health").status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                assert time.monotonic() < deadline, "the server did not start"
                time.sleep(0.05)
            headers = {"Content-Type": "application/json", **_accept()}
            for message in messages:
                answered = client.post("/mcp", content=json.dumps(message), headers=headers)
                answers.append(answered.text)
                assert answered.status_code in (200, 400), answered.text
            over_http = client.post("/api/tools/describe_dataset", json={"dataset": token})
            answers.append(over_http.text)
    finally:
        server.send_signal(signal.SIGINT)
        _, logged = server.communicate(timeout=60)
    written = logged.decode("utf-8", "replace")
    assert "Uvicorn running" in written or "Application startup complete" in written
    for secret in (token, token[5:]):
        assert secret not in written
        assert not [answer for answer in answers if secret in answer]


def test_a_call_s_thread_knows_its_deadline_on_its_own_clock() -> None:
    """What the thread starts can end by the call's deadline, and what it records be recorded
    only while the call can still be answered (D300, D301)."""
    calls = Calls(None, seconds=20)  # type: ignore[arg-type]

    def work() -> Deadline | None:
        return DEADLINE.get()

    async def main() -> tuple[float, object, float]:
        before = time.monotonic()
        found = await calls.run(work)
        return before, found, time.monotonic()

    before, found, after = anyio.run(main)
    assert isinstance(found, Deadline)
    assert found.seconds == 20
    assert before + 19 <= found.at <= after + 20
    assert DEADLINE.get() is None

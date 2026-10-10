"""The place policy (SPEC §11.1, §12.4, §14, D278, D412, D422): what a page load may spend of the
tool places and of the ``api`` rate, stated in D422, and what the server does that the statement
rests on. The server enforces none of the page's call counts (M5.2's and M5.3's call-count tests
do); here are the arithmetic of the bound from the defaults, every number and list of D422's place
paragraph rendered from the code's constants and asserted present, the tests D422 cites as pins
(by their assertions, as syntax trees), and, for JSON and MCP, a place held after ``tool_seconds``.
What every path answers is ``test_refused_paths.py``'s table."""

import json
import re
import socket
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
import uvicorn

from aibi.core.api import errors
from aibi.core.api.app import create_app
from aibi.core.api.config import Rates
from aibi.core.api.protection import Policy
from aibi.core.api.serve import uvicorn_config
from aibi.core.mcp.calls import CONCURRENT, PER_CLIENT, SECONDS, Calls
from aibi.core.mcp.server import INLINE_BYTES
from aibi.core.schema.catalog import (
    DEFAULT_DESCRIBED_COLUMNS,
    MAX_DESCRIBED_COLUMNS,
    CatalogHits,
)

Served = Any
ACCEPT = {"Accept": "application/json, text/event-stream"}
ROOT = Path(__file__).resolve().parents[4]
PAGE_LOAD = ("describe_dataset", "list_analyses", "list_leaf_kinds", "validate_document")
"""The calls of the dataset page's load that D422 states, each once."""
K_MOST = Rates().api.burst // Rates().page.burst
PAGES = K_MOST - (len(PAGE_LOAD) - 1)
TOOL_SECONDS = f"{int(SECONDS)} s"


def d422() -> str:
    """The row of Appendix A that is D422."""
    rows = [
        line
        for line in (ROOT / "SPEC.md").read_text(encoding="utf-8").splitlines()
        if line.startswith("| D422 |")
    ]
    assert len(rows) == 1
    return rows[0]


# --- The arithmetic ----------------------------------------------------------------------------


def test_k_is_at_most_the_api_burst_over_the_page_burst_by_the_defaults() -> None:
    rates = Rates()
    assert (rates.page.burst, rates.api.burst) == (30, 200)
    assert rates.api.burst // rates.page.burst == 6
    assert rates.page.burst * 6 <= rates.api.burst < rates.page.burst * 7


def test_the_dataset_page_load_is_four_calls_and_leaves_three_description_pages() -> None:
    assert len(PAGE_LOAD) == 4
    assert len(set(PAGE_LOAD)) == len(PAGE_LOAD)
    assert PAGES == 3
    assert PAGES * DEFAULT_DESCRIBED_COLUMNS == 6_000


# --- D422's place paragraph, rendered from the code ---------------------------------------------

PIN_NOTES = {
    "tests/core/mcp/test_transport.py::"
    "test_one_client_runs_at_most_its_share_of_the_places_and_others_still_get_one": (
        f"(`client_tool_calls`, at most {PER_CLIENT}, 503)"
    ),
    "tests/core/mcp/test_page.py::"
    "test_a_call_that_gets_no_place_is_a_page_with_retry_after_and_its_limit": (
        f"(a page: 503, `Retry-After: {errors.RETRY_IMPORT}`)"
    ),
}
"""The parenthesis after a pin D422 cites, from the constants it states."""
KEEP = (
    "After `tool_seconds` a place may still be held: a call's thread cannot be stopped and holds "
    "its place until it returns, so a client answered `tool_seconds` may be refused "
    "`client_tool_calls` at once after."
)


KIB = INLINE_BYTES // 1024
ANSWERED = (
    "**What is answered, as far as a pin holds:** to a client refused a place "
    "(`client_tool_calls`), over JSON `/api/tools` and in a page a 503 with "
    f"`Retry-After: {errors.RETRY_IMPORT}` (`RETRY_IMPORT`, {errors.RETRY_IMPORT} s); over MCP, "
    f"for an inline `tools/call` (a body of at most `INLINE_BYTES` ({KIB} KiB)), HTTP 200 with "
    "a tool error (`isError`) naming the limit and no `Retry-After`; and the server's own "
    "request limit (`max_connections`, uvicorn's `limit_concurrency`) is a 503 `text/plain` "
    "with no `Retry-After` on every path alike (a test with a real uvicorn pins it).",
    "**Not stated here:** every other path, among them an MCP body over `INLINE_BYTES`, "
    "`resources/list` and `resources/read` (which answer -32002 `RESOURCE_NOT_FOUND`: #105), "
    "`ping` and `resources/templates/list` (which take no place), a call past `tool_seconds` "
    "and the per-client connection cap (D255 closes the connection with no answer); to state "
    "and test each path is #106. The operator router, `/assets` and `/curate` take no tool "
    "place; their 429 and 503 are D259, D265 and D413.",
)
"""What D422 says is answered, and what it leaves to #106 (a table of every path)."""


def place_sentences() -> list[str]:
    """Every sentence of D422's place paragraph that holds a number, a limit's name or a list,
    from the constants the code has."""
    rates = Rates()
    return [
        "so *k*, the tool calls one page load makes, is the client's discipline, bounded by D412: "
        "a navigation burst spends at most `page`'s burst \u00d7 *k* of `api`'s burst of "
        f"{rates.api.burst}, so with the defaults page burst {rates.page.burst} \u00d7 k \u2264 "
        f"`api` burst {rates.api.burst}, so k \u2264 {K_MOST} (a test computes it from the "
        "defaults)",
        "The dataset page's load calls `describe_dataset` (it already carries "
        "`applicable_analyses`), `list_analyses` (without `dataset`), `list_leaf_kinds` and, "
        f"when a document is restored from the URL fragment, `validate_document`: k = "
        f"{len(PAGE_LOAD)}, under three requirements",
        "the page calls no `describe_column` (for a restored value leaf) and no `count_cohort` "
        "on load, both waiting for a user's action",
        f"(a release over `DEFAULT_DESCRIBED_COLUMNS` = {DEFAULT_DESCRIBED_COLUMNS:,} columns "
        f"pages, and each page is counted against k ≤ {K_MOST}: with the other three calls at "
        f"most {PAGES} `describe_dataset` pages ({PAGES * DEFAULT_DESCRIBED_COLUMNS:,} columns, "
        "at the default `columns_limit`: a page may hold up to `MAX_DESCRIBED_COLUMNS` = "
        f"{MAX_DESCRIBED_COLUMNS:,}, and a larger page is fewer pages) fit in a page load, and "
        "further pages wait for a user's action)",
        "The catalogue page calls `search_catalog`, once, and once more for each page of hits.",
        f"A client holds at most `client_tool_calls` ({PER_CLIENT}) of the `tool_calls` "
        f"({CONCURRENT}) places; a call that gets none within `tool_seconds` is refused "
        "`LIMIT_EXCEEDED` naming `client_tool_calls` or `tool_calls`, a call not ended by then "
        "`tool_seconds`",
        KEEP,
        f"which a page's author weighs against `tool_seconds` ({TOOL_SECONDS}) and *k* ≤ {K_MOST}",
        *(f"`{pin}` {note}" for pin, note in PIN_NOTES.items()),
        "the server enforces none of it",
        *ANSWERED,
    ]


def test_d422_holds_every_sentence_the_code_gives() -> None:
    row = d422()
    for sentence in place_sentences():
        assert sentence in row, sentence


def test_d422_says_nothing_of_an_every_503() -> None:
    row = d422()
    assert "all 503" not in row
    assert "on every 503" not in row
    # what is answered is stated for the paths a pin holds, never for every refused call
    assert not re.search(
        r"(?i)\b(every|all|any) (refused|refusal|call)", row.replace("every other path", "")
    ), "D422 states no universal answer: #106 does"


def section14() -> str:
    text = (ROOT / "SPEC.md").read_text(encoding="utf-8")
    [found] = re.findall(r"What a page load may spend of them is stated.*?\)\.", text, re.DOTALL)
    return " ".join(found.split())


def test_section_14_states_the_numbers_d422_and_the_code_do() -> None:
    assert (
        "What a page load may spend of them is stated, not enforced, by D422 "
        f"(*k* ≤ {K_MOST} calls, *k* = {len(PAGE_LOAD)} for the dataset page, "
        f"`Retry-After` {errors.RETRY_IMPORT} s on a place refused to a client over JSON and in "
        "a page, none on an inline MCP `tools/call`, the other paths #106's)."
    ) == section14()


def test_the_places_the_code_has_are_the_ones_the_pins_and_d422_state() -> None:
    assert (PER_CLIENT, CONCURRENT) == (2, 8)
    assert SECONDS == 30.0
    assert errors.RETRY_IMPORT == 30


def spec_row(identifier: str) -> str:
    [row] = [
        line
        for line in (ROOT / "SPEC.md").read_text(encoding="utf-8").splitlines()
        if line.startswith(f"| {identifier} |")
    ]
    return row


def test_the_rows_d422_refines_say_so_and_do_not_say_every_refusal_is_a_503() -> None:
    """D278 said a call refused a place or past ``tool_seconds`` is answered "(all 503)": it is
    not, over MCP; and D278 and D247 now say D422 refines them."""
    for identifier in ("D247", "D278"):
        title = spec_row(identifier).split(" | ")[1]
        assert "D422" in title, identifier
    assert "(all 503)" not in spec_row("D278")
    assert "the other paths are #106's" in spec_row("D278")
    assert "(all 503)" not in spec_row("D265")
    assert "the tool limits `tool_calls`, `client_tool_calls` and `tool_seconds`" in spec_row(
        "D265"
    )
    assert "D422" in spec_row("D265").split(" | ")[1]
    calls = (ROOT / "server" / "src" / "aibi" / "core" / "mcp" / "calls.py").read_text("utf-8")
    assert "all 503" not in calls
    assert "D422 and #106" in calls
    assert "refines" in spec_row("D422").split(" | ")[1]
    assert "D278" in spec_row("D422").split(" | ")[1]
    assert "D247" in spec_row("D422").split(" | ")[1]


# --- The places a client holds, over each transport -----------------------------------------


@contextmanager
def holding(
    served: Served, monkeypatch: pytest.MonkeyPatch, places: int = PER_CLIENT
) -> Iterator[list[Any]]:
    """``places`` JSON calls that hold the client's places (each is in its function: a
    semaphore says so, no sleep), answered when ``tool_seconds`` passes, their threads running
    until the block ends; what they were answered is the list."""
    release = threading.Event()
    entered = threading.Semaphore(0)
    inside: list[int] = []

    def search(*args: Any) -> CatalogHits:
        inside.append(1)
        entered.release()
        release.wait(10)
        return CatalogHits(hits=[], total=0, next_offset=None, caveats=[])

    monkeypatch.setattr(type(served.calls.catalog), "search_catalog", search)
    answers: list[Any] = []
    holders = [
        threading.Thread(target=lambda: answers.append(served.api("search_catalog", {})))
        for _ in range(places)
    ]
    try:
        for holder in holders:
            holder.start()
        taken = sum(1 for _ in range(places) if entered.acquire(timeout=10))
        assert taken == places, "a call did not take a place"
        assert len(inside) == places, "the places are not all held"
        yield answers
    finally:
        release.set()
        for holder in holders:
            holder.join(10)


def limit_of(answered: Any) -> tuple[int, str, int | None, str | None]:
    refusal = answered.json()["refusals"][0]
    return (
        answered.status_code,
        refusal["limit"]["name"],
        refusal["limit"]["max"],
        answered.headers.get("retry-after"),
    )


def test_a_json_tool_call_with_no_place_is_a_503_naming_client_tool_calls_with_retry_after(
    make_served: Callable[..., Served], monkeypatch: pytest.MonkeyPatch
) -> None:
    served = make_served(seconds=0.4)
    with holding(served, monkeypatch):
        third = served.api("search_catalog", {})
        assert limit_of(third) == (503, "client_tool_calls", PER_CLIENT, "30")
        assert third.headers["retry-after"] == str(errors.RETRY_IMPORT)


def test_after_tool_seconds_a_json_call_is_a_503_and_its_place_may_still_be_held(
    make_served: Callable[..., Served], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The call is answered at ``tool_seconds``, but its thread cannot be stopped and holds its
    place until it returns, so the client's next calls may be refused ``client_tool_calls``."""
    served = make_served(seconds=0.3)
    with holding(served, monkeypatch) as answers:
        deadline = time.monotonic() + 10
        while len(answers) < PER_CLIENT:
            assert time.monotonic() < deadline, "the calls were not answered"
            time.sleep(0.02)
        for answered in answers:
            assert limit_of(answered) == (503, "tool_seconds", 1, "30")
        # both calls were answered, and both threads still hold a place
        assert limit_of(served.api("search_catalog", {})) == (503, "client_tool_calls", 2, "30")
    deadline = time.monotonic() + 10
    while served.api("search_catalog", {}).status_code != 200:
        assert time.monotonic() < deadline, "the places were not given back"
        time.sleep(0.05)


def test_an_mcp_call_with_no_place_is_a_200_tool_error_without_retry_after(
    make_served: Callable[..., Served], monkeypatch: pytest.MonkeyPatch
) -> None:
    served = make_served(seconds=0.4)
    with holding(served, monkeypatch):
        answered = served.rpc("tools/call", {"name": "search_catalog", "arguments": {}})
    assert answered.status_code == 200
    assert "retry-after" not in answered.headers
    result = answered.json()["result"]
    assert result["isError"] is True
    refusal = result["structuredContent"]["refusals"][0]
    assert refusal["code"] == "LIMIT_EXCEEDED"
    assert refusal["limit"] == {"name": "client_tool_calls", "max": PER_CLIENT}


def mcp_call_of(size: int) -> bytes:
    """A valid ``tools/call`` body of exactly ``size`` bytes, padded in an argument."""

    def body(pad: int) -> bytes:
        message = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "search_catalog", "arguments": {"text": "a" * pad}},
        }
        return json.dumps(message, separators=(",", ":")).encode()

    found = body(size - len(body(0)))
    assert len(found) == size
    return found


def test_an_mcp_body_of_inline_bytes_is_inline_and_one_byte_more_is_not(
    make_served: Callable[..., Served], monkeypatch: pytest.MonkeyPatch
) -> None:
    """``INLINE_BYTES`` is the boundary D422 draws: a ``tools/call`` of at most that many bytes
    refused a place is a 200 tool error; one byte more takes the transport's own place first
    (what that is answered is #106's, here only that it is not the inline answer)."""
    served = make_served(seconds=0.4)
    headers = {**ACCEPT, "Content-Type": "application/json"}
    with holding(served, monkeypatch):
        inline = served.client.post("/mcp", content=mcp_call_of(INLINE_BYTES), headers=headers)
        beyond = served.client.post("/mcp", content=mcp_call_of(INLINE_BYTES + 1), headers=headers)
    assert inline.status_code == 200
    assert inline.json()["result"]["isError"] is True
    assert "retry-after" not in inline.headers
    assert inline.json()["result"]["structuredContent"]["refusals"][0]["limit"]["name"] == (
        "client_tool_calls"
    )
    assert beyond.status_code != 200
    assert "client_tool_calls" in beyond.text


# --- The server's own limit, with a real uvicorn ------------------------------------------------


def raw(port: int, source: str, path: str) -> tuple[int, dict[str, str], bytes]:
    """One request from ``source`` to a listener, by a socket: status, headers, body."""
    body = b'{"jsonrpc":"2.0","id":1,"method":"tools/list"}' if path == "/mcp" else b""
    if path.startswith("/api/tools"):
        body = b"{}"
    method = "POST" if body else "GET"
    head = (
        f"{method} {path} HTTP/1.1\r\nHost: 127.0.0.1\r\nAccept: application/json\r\n"
        f"Content-Type: application/json\r\nContent-Length: {len(body)}\r\n"
        "Connection: close\r\n\r\n"
    )
    with socket.socket() as client:
        client.bind((source, 0))
        client.settimeout(5)
        client.connect(("127.0.0.1", port))
        client.sendall(head.encode() + body)
        got = b""
        while chunk := client.recv(65536):
            got += chunk
    header, _, rest = got.partition(b"\r\n\r\n")
    lines = header.decode().split("\r\n")
    headers = {k.lower(): v for k, v in (line.split(": ", 1) for line in lines[1:])}
    return int(lines[0].split()[1]), headers, rest


def test_the_servers_own_request_limit_is_a_503_without_retry_after_on_every_path(
    make_served: Callable[..., Served],
) -> None:
    served = make_served(server_config={"max_connections": 2})
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    config = served.config.model_copy(
        update={"server": served.config.server.model_copy(update={"port": port})}
    )
    app = create_app(
        Policy.of(config, csrf_key=b"k" * 32), served.services, tools=Calls(served.calls.catalog)
    )
    server = uvicorn.Server(uvicorn_config(config, app))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    holder = socket.socket()
    try:
        deadline = time.monotonic() + 20
        while not server.started:
            assert time.monotonic() < deadline, "uvicorn did not start"
            time.sleep(0.01)
        # Linux routes all of 127.0.0.0/8; a host that does not cannot hold this pin, and the
        # pin is then failed, not skipped (``pins.pytest_runtest_makereport``).
        holder.bind(("127.0.0.2", 0))
        # One request under way (its head is in, its body is not) is the one task the limit of 2
        # counts with the next connection. Another address, since a client's own cap would close
        # its second connection, and a connection with no request under way is the idlest, which
        # the guard closes to make room instead.
        holder.connect(("127.0.0.1", port))
        holder.sendall(
            b"POST /api/tools/search_catalog HTTP/1.1\r\nHost: 127.0.0.1\r\n"
            b"Content-Type: application/json\r\nContent-Length: 100\r\n\r\n{"
        )
        while len(server.server_state.tasks) < 1:
            assert time.monotonic() < deadline, "the server did not begin the request"
            time.sleep(0.01)
        for path in ("/mcp", "/api/tools/search_catalog", "/"):
            status, headers, body = raw(port, "127.0.0.4", path)
            assert status == 503, path
            assert headers["content-type"].startswith("text/plain"), path
            assert "retry-after" not in headers, path
            assert body == b"Service Unavailable", path
    finally:
        holder.close()
        server.should_exit = True
        thread.join(30)
        listener.close()

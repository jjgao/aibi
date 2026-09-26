"""The MCP tests' server, and the catalogue page's: the whole application, with the tools,
over a store in ``tmp_path``.

- ``make_served`` builds a ``Store`` with a ticking clock, a curator token, a configuration whose
  import directory is under ``tmp_path``, and the application with the tool calls
  (``tools_of``), behind request protection, with a ``TestClient`` at
  ``http://127.0.0.1:8000``; ``served`` is one with the defaults. Rates are generous, and
  ``server_config`` adds or overrides ``[server]`` settings and rates.
- ``Served.rpc`` posts one JSON-RPC message to ``/mcp`` and returns the answer, ``Served.tool``
  calls a tool over MCP, and ``Served.api`` calls it over HTTP.
- ``Served.import_`` imports a directory through the operator router, and ``Served.curate``
  opens a session, applies edits and publishes it.
- ``live`` serves another application over the same store and services with uvicorn on a free
  loopback port, for the official MCP client and the operator CLI, which talk to it over real
  HTTP.
- ``orchard_files`` writes a small non-biomedical dataset (SPEC P8): trees and their harvests.

Test modules can't import one another (``--import-mode=importlib``), so the helpers are given as
fixtures, as in the other test directories.
"""

import io
import itertools
import socket
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
import uvicorn
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import JsonValue

from aibi.core.api.app import create_app
from aibi.core.api.config import BASE, ServerConfig
from aibi.core.api.protection import Policy
from aibi.core.api.serve import services_of, tools_of, uvicorn_config
from aibi.core.mcp.calls import Calls
from aibi.core.operator import cli
from aibi.core.operator.auth import encode_operator, hash_token, new_token
from aibi.core.operator.router import Services
from aibi.core.schema.pack_api import PackRegistry
from aibi.core.store.store import Store

ADA = "Ada Lovelace"
BASE_URL = "http://127.0.0.1:8000"
GENEROUS = {"per_minute": 1_000_000, "burst": 1_000_000}
EMPTY = PackRegistry((), core_version="0.0.1")
ACCEPT = {"Accept": "application/json, text/event-stream"}
FIXTURES = Path(__file__).resolve().parents[4] / "fixtures"


def _clock() -> Callable[[], datetime]:
    ticks = itertools.count()
    start = datetime(2026, 1, 1, tzinfo=UTC)
    return lambda: start + timedelta(seconds=next(ticks))


def orchard_files(rows: int = 24) -> dict[str, bytes]:
    trees = ["tree_id,variety,height_m"]
    for n in range(1, rows + 1):
        trees.append(f"tree{n},{('apple', 'pear', 'plum')[n % 3]},{1 + n * 0.25:g}")
    harvests = ["harvest_id,tree_id,kg"]
    for n in range(1, 31):
        harvests.append(f"h{n},tree{1 + n % rows},{10 + n % 9}")
    return {
        "trees.csv": ("\n".join(trees) + "\n").encode(),
        "harvests.csv": ("\n".join(harvests) + "\n").encode(),
    }


@dataclass
class Served:
    root: Path
    imports: Path
    store: Store
    token: str
    config: ServerConfig
    services: Services
    calls: Calls
    app: FastAPI
    client: TestClient

    def headers(self, **extra: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Aibi-Operator": encode_operator(ADA),
            **extra,
        }

    def operator(self, path: str, body: JsonValue = None) -> dict[str, Any]:
        answered = self.client.post(path, json={} if body is None else body, headers=self.headers())
        assert answered.status_code == 200, answered.text
        found: dict[str, Any] = answered.json()
        return found

    def write(self, dataset: str, files: dict[str, bytes]) -> Path:
        directory = self.imports / dataset
        directory.mkdir(parents=True, exist_ok=True)
        for name, content in files.items():
            (directory / name).write_bytes(content)
        return directory

    def import_(self, dataset: str, files: dict[str, bytes]) -> dict[str, Any]:
        source = self.write(dataset, files)
        return self.operator(
            f"/operator/datasets/{dataset}/import", {"source": {"path": str(source)}}
        )

    def curate(self, dataset: str, *edits: JsonValue) -> int:
        opened = self.operator(f"/operator/datasets/{dataset}/session/open")
        body = {"handle": opened["handle"], "expected": opened["draft"], "edits": list(edits)}
        draft = self.operator(f"/operator/datasets/{dataset}/session/change", body)["draft"]
        ended = {"handle": opened["handle"], "expected": draft}
        label: int = self.operator(f"/operator/datasets/{dataset}/session/publish", ended)["label"]
        return label

    def rpc(
        self, method: str, params: JsonValue = None, *, id: int = 1, **given: Any
    ) -> httpx.Response:
        message: dict[str, JsonValue] = {"jsonrpc": "2.0", "id": id, "method": method}
        if params is not None:
            message["params"] = params
        headers = {**ACCEPT, **given.pop("headers", {})}
        return self.client.post("/mcp", json=message, headers=headers, **given)

    def tool(self, name: str, arguments: JsonValue) -> dict[str, Any]:
        answered = self.rpc("tools/call", {"name": name, "arguments": arguments})
        assert answered.status_code == 200, answered.text
        result: dict[str, Any] = answered.json()["result"]
        return result

    def api(self, name: str, body: JsonValue, **given: Any) -> httpx.Response:
        return self.client.post(f"/api/tools/{name}", json=body, **given)


MakeServed = Callable[..., Served]


@pytest.fixture
def make_served(tmp_path: Path) -> Iterator[MakeServed]:
    made: list[Served] = []

    def make(
        *,
        name: str = "server",
        disclosure: dict[str, Any] | None = None,
        imports_config: dict[str, Any] | None = None,
        server_config: dict[str, Any] | None = None,
        seconds: float | None = None,
        **client: Any,
    ) -> Served:
        root = tmp_path / name
        imports = root / "imports"
        imports.mkdir(parents=True)
        token = new_token()
        given = dict(server_config or {})
        rates = {
            "operator": GENEROUS,
            "api": GENEROUS,
            "token_failures": GENEROUS,
            "proposals": GENEROUS,
            **given.pop("rates", {}),
        }
        written: dict[str, Any] = {
            "server": {"rates": rates, **given},
            "curator": {"token_hash": hash_token(token)},
            "storage": {"data": "data", "imports": ["imports"]},
            "disclosure": disclosure or {},
            "imports": imports_config or {},
        }
        config = ServerConfig.model_validate(written, context={BASE: root})
        store = Store(config.storage.data, clock=_clock())
        services = replace(services_of(config, store, EMPTY), registry=None)
        calls = tools_of(config, store, None)
        if seconds is not None:
            calls = Calls(calls.catalog, seconds=seconds, proposals=calls.proposals)
        app = create_app(Policy.of(config, csrf_key=b"k" * 32), services, tools=calls)
        test_client = TestClient(app, base_url=client.pop("base_url", BASE_URL), **client)
        test_client.__enter__()
        found = Served(root, imports, store, token, config, services, calls, app, test_client)
        made.append(found)
        return found

    yield make
    for found in made:
        found.client.__exit__(None, None, None)
        found.store.close()


@pytest.fixture
def served(make_served: MakeServed) -> Served:
    return make_served()


@pytest.fixture
def orchard() -> Callable[..., dict[str, bytes]]:
    return orchard_files


@dataclass
class Live:
    url: str
    served: Served

    def cli(self, *argv: str, stdin: str = "", state: Path | None = None) -> tuple[int, str, str]:
        """Run ``aibi`` against the live server, as the operator Ada."""
        environ = {
            "AIBI_SERVER": self.url,
            "AIBI_TOKEN": self.served.token,
            "AIBI_OPERATOR": ADA,
            "XDG_STATE_HOME": str(state or self.served.root / "state"),
        }
        out, err = io.StringIO(), io.StringIO()
        code = cli.main(
            list(argv), environ=environ, stdin=io.StringIO(stdin), stdout=out, stderr=err
        )
        return code, out.getvalue(), err.getvalue()


@pytest.fixture
def live(served: Served) -> Iterator[Live]:
    """The served application behind uvicorn on a free loopback port."""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    config = served.config.model_copy(
        update={"server": served.config.server.model_copy(update={"port": port})}
    )
    app = create_app(
        Policy.of(config, csrf_key=b"k" * 32),
        served.services,
        tools=Calls(served.calls.catalog),
    )
    server = uvicorn.Server(uvicorn_config(config, app))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 20
    while not server.started:
        assert time.monotonic() < deadline, "uvicorn did not start"
        time.sleep(0.01)
    try:
        yield Live(f"http://127.0.0.1:{port}", served)
    finally:
        server.should_exit = True
        thread.join(30)
        sock.close()

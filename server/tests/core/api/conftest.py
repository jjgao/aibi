"""The HTTP application tests' builders.

- ``make_app`` builds the application over a store in ``tmp_path`` from configuration sections
  (``server``, ``imports``), with a test mount at ``/mcp`` standing for #12's MCP transport and an
  injectable clock for the rate buckets, behind a ``TestClient`` at ``http://127.0.0.1:8000``.
  Rates are generous unless a test sets them.
- ``asgi`` sends one HTTP request straight to an ASGI application, with the scope and body messages
  a test gives, and records what it answers: for requests no HTTP client sends (no ``Host``, a body
  that stops half way) and for proving a body was never read.
- ``write_config`` writes a configuration file, private, beside an import directory.

Test modules can't import one another (``--import-mode=importlib``), so the helpers are given as
fixtures.
"""

import dataclasses
import os
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import anyio
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from aibi.core.api.app import create_app
from aibi.core.api.config import BASE, ServerConfig
from aibi.core.api.protection import Policy
from aibi.core.api.serve import services_of
from aibi.core.operator.auth import encode_operator, hash_token, new_token
from aibi.core.schema.pack_api import PackRegistry
from aibi.core.store.store import Store

BASE_URL = "http://127.0.0.1:8000"
GENEROUS = {"per_minute": 1_000_000, "burst": 1_000_000}


async def mcp(scope: Scope, receive: Receive, send: Send) -> None:
    """A mounted application that sets its own CORS and CSP headers, as a transport might."""
    if scope["type"] == "websocket":
        await receive()
        await send({"type": "websocket.accept"})
        await send({"type": "websocket.close", "code": 1000})
        return
    headers = [
        (b"content-type", b"application/json"),
        (b"access-control-allow-origin", b"*"),
        (b"content-security-policy", b"default-src 'self'"),
    ]
    await send({"type": "http.response.start", "status": 200, "headers": headers})
    await send({"type": "http.response.body", "body": b'{"mcp": true}'})


class Clock:
    """A clock the test moves."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@dataclass
class Built:
    app: FastAPI
    client: TestClient
    store: Store
    token: str
    config: ServerConfig
    root: Path
    clock: Clock
    clients: list[TestClient] = field(default_factory=list[TestClient])

    def headers(self, **extra: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Aibi-Operator": encode_operator("Ada Lovelace"),
            **extra,
        }

    def other(self, **given: Any) -> TestClient:
        """Another client of the application, closed with it."""
        client = TestClient(self.app, base_url=given.pop("base_url", BASE_URL), **given)
        client.__enter__()
        self.clients.append(client)
        return client

    def csrf(self) -> str:
        response = self.client.get(
            "/operator/csrf", headers=self.headers(Origin="http://127.0.0.1:8000")
        )
        token: str = response.json()["csrf"]
        return token


MakeApp = Callable[..., Built]


@pytest.fixture
def make_app(tmp_path: Path) -> Iterator[MakeApp]:
    made: list[Built] = []

    def make(
        *,
        name: str = "app",
        server: dict[str, Any] | None = None,
        imports: dict[str, Any] | None = None,
        mounts: dict[str, ASGIApp] | None = None,
        tls: bool = False,
        **client: Any,
    ) -> Built:
        root = tmp_path / name
        (root / "imports").mkdir(parents=True)
        token = new_token()
        rates = {"operator": GENEROUS, "api": GENEROUS, "token_failures": GENEROUS}
        given = dict(server or {})
        given["rates"] = {**rates, **given.get("rates", {})}
        written: dict[str, Any] = {
            "server": given,
            "curator": {"token_hash": hash_token(token)},
            "storage": {"data": "data", "imports": ["imports"]},
            "imports": imports or {},
        }
        config = ServerConfig.model_validate(written, context={BASE: root})
        store = Store(config.storage.data)
        services = services_of(config, store, PackRegistry((), core_version="0.0.1"))
        policy = Policy.of(config, csrf_key=b"k" * 32)
        if tls:
            policy = dataclasses.replace(policy, tls=True)
        clock = Clock()
        app = create_app(policy, services, mounts={"/mcp": mcp, **(mounts or {})}, clock=clock)
        test_client = TestClient(app, base_url=client.pop("base_url", BASE_URL), **client)
        test_client.__enter__()
        built = Built(app, test_client, store, token, config, root, clock)
        made.append(built)
        return built

    yield make
    for built in made:
        for other in built.clients:
            other.__exit__(None, None, None)
        built.client.__exit__(None, None, None)
        built.store.close()


@pytest.fixture
def built(make_app: MakeApp) -> Built:
    return make_app()


@dataclass
class Answer:
    status: int | None
    headers: dict[bytes, bytes]
    body: bytes
    received: int
    """How many messages the application took from ``receive``."""


def _asgi(
    app: ASGIApp,
    path: str,
    *,
    method: str = "GET",
    query: bytes = b"",
    headers: Sequence[tuple[bytes, bytes]] = ((b"host", b"127.0.0.1:8000"),),
    messages: Sequence[Message] = ({"type": "http.request", "body": b""},),
    client: tuple[str, int] = ("127.0.0.1", 50000),
) -> Answer:
    scope: Scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "root_path": "",
        "query_string": query,
        "headers": list(headers),
        "client": client,
        "server": ("127.0.0.1", 8000),
    }
    pending = list(messages)
    answer = Answer(None, {}, b"", 0)

    async def receive() -> Message:
        answer.received += 1
        if pending:
            return pending.pop(0)
        await anyio.sleep_forever()
        raise AssertionError("unreachable")  # pragma: no cover

    async def send(message: Message) -> None:
        if message["type"] == "http.response.start":
            answer.status = message["status"]
            answer.headers = {bytes(k): bytes(v) for k, v in message.get("headers", [])}
        elif message["type"] == "http.response.body":
            answer.body += message.get("body", b"")

    async def main() -> None:
        with anyio.fail_after(10):
            await app(scope, receive, send)

    anyio.run(main)
    return answer


@pytest.fixture
def asgi() -> Callable[..., Answer]:
    return _asgi


@pytest.fixture
def write_config(tmp_path: Path) -> Callable[..., Path]:
    def write(text: str, *, mode: int = 0o600, name: str = "aibi.toml") -> Path:
        (tmp_path / "imports").mkdir(exist_ok=True)
        path = tmp_path / name
        path.write_text(text, encoding="utf-8")
        os.chmod(path, mode)
        return path

    return write

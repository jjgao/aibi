"""The HTTP application tests' builders.

- ``make_app`` builds the application over a store in ``tmp_path`` from configuration sections
  (``server``, ``imports``), with a test mount at ``/mcp`` standing for #12's MCP transport and an
  injectable clock for the rate buckets, behind a ``TestClient`` at ``http://127.0.0.1:8000``.
  Rates are generous unless a test sets them.
- ``asgi`` sends one HTTP request straight to an ASGI application, with the scope and body messages
  a test gives, and records what it answers: for requests no HTTP client sends (no ``Host``, a body
  that stops half way) and for proving a body was never read.
- ``write_config`` writes a configuration file, private, beside an import directory.
- ``_process_state``, for every test here, turns the loader's DuckDB guard off (pytest's process
  may have loaded DuckDB, D404) and, after the test, restores the state the loader, or a logging
  configuration such as uvicorn's, changes for the whole process: the level, ``propagate``,
  ``disabled``, handlers and filters of the root logger and of every logger in
  ``logging.root.manager.loggerDict`` (``aibi``, ``aibi.*``, ``uvicorn*`` and any other; one made
  during the test is given a new logger's, but for the ``NullHandler`` a library adds to its own
  at import), ``logging.lastResort``, ``captureWarnings``, the warnings filters and
  ``showwarning``, ``sys.meta_path`` (removing ``NoDuckDB``) and ``sys.modules`` (removing the
  test packs' modules, named ``TEST_PACKS`` and on).

Test modules can't import one another (``--import-mode=importlib``), so the helpers are given as
fixtures.
"""

import dataclasses
import logging
import os
import sys
import warnings
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import anyio
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from aibi.core.api import packs
from aibi.core.api.app import create_app
from aibi.core.api.config import BASE, ServerConfig
from aibi.core.api.protection import Policy
from aibi.core.api.serve import services_of
from aibi.core.operator.auth import encode_operator, hash_token, new_token
from aibi.core.schema.pack_api import PackRegistry
from aibi.core.store.store import Store

TEST_PACKS = "aibi_test_pack_"
"""The prefix of every test pack module's name, which ``_process_state`` unloads."""
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

_Logger = tuple[int, bool, bool, list[logging.Handler], list[Any]]
"""A logger's level, ``propagate``, ``disabled``, handlers and filters."""


def _loggers() -> dict[str, logging.Logger]:
    found = {
        name: logger
        for name, logger in logging.root.manager.loggerDict.items()
        if isinstance(logger, logging.Logger)
    }
    return {"": logging.getLogger(), **found}


def _state(logger: logging.Logger) -> _Logger:
    return (
        logger.level,
        logger.propagate,
        logger.disabled,
        logger.handlers[:],
        logger.filters[:],
    )


def _restore(loggers: dict[str, _Logger]) -> None:
    """Every logger as ``loggers`` held it, a logger made since as a new one, keeping the
    ``NullHandler`` a library adds to its own; a handler the test added is closed, but pytest's,
    which it attaches to a logger that does not propagate."""
    for name, logger in _loggers().items():
        held = loggers.get(name)
        if held is None:
            kept = [one for one in logger.handlers if type(one) is logging.NullHandler]
            held = (logging.NOTSET, True, False, kept, [])
        level, propagate, disabled, handlers, filters = held
        if _state(logger) == held:
            continue
        for added in logger.handlers[:]:
            if added not in handlers:
                logger.removeHandler(added)
                if not type(added).__module__.startswith("_pytest"):
                    added.close()
        logger.handlers[:] = handlers
        logger.filters[:] = filters
        logger.setLevel(level)
        logger.propagate = propagate
        logger.disabled = disabled


@pytest.fixture(autouse=True)
def _process_state(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(packs, "GUARD_DUCKDB", False)
    loggers = {name: _state(logger) for name, logger in _loggers().items()}
    resort = logging.lastResort
    captured = logging.__dict__["_warnings_showwarning"]
    meta_path = [one for one in sys.meta_path if type(one) is not packs.NoDuckDB]
    try:
        with warnings.catch_warnings():
            yield
    finally:
        logging.__dict__["_warnings_showwarning"] = captured
        _restore(loggers)
        logging.lastResort = resort
        sys.meta_path[:] = meta_path
        for name in [name for name in sys.modules if name.startswith(TEST_PACKS)]:
            del sys.modules[name]


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

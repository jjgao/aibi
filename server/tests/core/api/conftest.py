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
- ``bundle_dir`` copies the checked-in web bundle (``fixtures/vite8``: the manifest, the build's
  own ``index.html`` and ``operator.html``, and its files, the scripts' code replaced by
  stand-ins, of a two-entry Vite 8.3.2 build with a shared chunk and its stylesheet, a lazy chunk
  with its own, an image a script imports and one a stylesheet does, and an icon) into
  ``tmp_path`` for a test to change; ``make_bundled`` builds the whole application, the tool
  calls and a web bundle with them, like ``make_app`` otherwise; ``drive`` sends many requests
  straight to an ASGI application in one event loop, for the generated tests. ``vite8`` and
  ``copy_bundle`` copy the checked-in bundle as ``bundle_dir`` does, to load unchanged and
  anywhere; each gives the loader modes it accepts (D410) whatever the umask of the checkout, and
  ``safe_umask`` holds one for the test, so the files it adds are accepted too.

Test modules can't import one another (``--import-mode=importlib``), so the helpers are given as
fixtures.
"""

import dataclasses
import logging
import os
import shutil
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
from aibi.core.api.bundle import Bundle, load_bundle
from aibi.core.api.config import BASE, ServerConfig
from aibi.core.api.protection import Policy
from aibi.core.api.serve import services_of, tools_of
from aibi.core.operator.auth import encode_operator, hash_token, new_token
from aibi.core.schema.pack_api import PackRegistry
from aibi.core.store.store import Store

TEST_PACKS = "aibi_test_pack_"
"""The prefix of every test pack module's name, which ``_process_state`` unloads."""
BASE_URL = "http://127.0.0.1:8000"
GENEROUS = {"per_minute": 1_000_000, "burst": 1_000_000}
FIXTURE_BUNDLE = Path(__file__).resolve().parent / "fixtures" / "vite8"
RATE_CLASSES = ("operator", "api", "token_failures", "proposals", "page", "assets")


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


@pytest.fixture
def safe_umask() -> Iterator[None]:
    """The umask a bundle's files are made under: the loader refuses a file or directory that
    group or others can write (D410), which a checkout under another umask would give."""
    before = os.umask(0o022)
    yield
    os.umask(before)


@pytest.fixture
def copy_bundle(safe_umask: None) -> Callable[[Path, Path], Path]:
    """Copy a bundle, its modes made ones the loader accepts (``0o755`` and ``0o644``, whatever
    the checkout's umask gave the original)."""

    def copy(source: Path, target: Path) -> Path:
        shutil.copytree(source, target, symlinks=True)
        for directory, _, names in os.walk(target):
            os.chmod(directory, 0o755)
            for name in names:
                if not Path(directory, name).is_symlink():
                    os.chmod(Path(directory, name), 0o644)
        return target

    return copy


@pytest.fixture
def vite8(tmp_path: Path, copy_bundle: Callable[[Path, Path], Path]) -> Path:
    """The checked-in bundle, copied beside ``tmp_path`` for a test to load unchanged."""
    return copy_bundle(FIXTURE_BUNDLE, tmp_path / "vite8")


@pytest.fixture
def bundle_dir(tmp_path: Path, copy_bundle: Callable[[Path, Path], Path]) -> Path:
    return copy_bundle(FIXTURE_BUNDLE, tmp_path / "bundle")


@dataclass
class Bundled:
    app: FastAPI
    client: TestClient
    store: Store
    token: str
    config: ServerConfig
    root: Path
    clock: Clock
    bundle: Bundle | None

    def headers(self, **extra: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Aibi-Operator": encode_operator("Ada Lovelace"),
            **extra,
        }


MakeBundled = Callable[..., Bundled]


@pytest.fixture
def make_bundled(tmp_path: Path) -> Iterator[MakeBundled]:
    made: list[Bundled] = []

    def make(
        bundle: Path | None,
        *,
        name: str = "bundled",
        server: dict[str, Any] | None = None,
        mounts: dict[str, ASGIApp] | None = None,
        **client: Any,
    ) -> Bundled:
        root = tmp_path / name
        (root / "imports").mkdir(parents=True)
        token = new_token()
        given = dict(server or {})
        given["rates"] = {
            **dict.fromkeys(RATE_CLASSES, GENEROUS),
            **given.get("rates", {}),
        }
        if bundle is not None:
            given["web_bundle"] = str(bundle)
        written: dict[str, Any] = {
            "server": given,
            "curator": {"token_hash": hash_token(token)},
            "storage": {"data": "data", "imports": ["imports"]},
        }
        config = ServerConfig.model_validate(written, context={BASE: root})
        store = Store(config.storage.data)
        services = services_of(config, store, PackRegistry((), core_version="0.0.1"))
        clock = Clock()
        loaded = None if config.server.web_bundle is None else load_bundle(config.server.web_bundle)
        app = create_app(
            Policy.of(config, csrf_key=b"k" * 32),
            services,
            tools=tools_of(config, store, None),
            mounts=mounts or {},
            clock=clock,
            bundle=loaded,
        )
        test_client = TestClient(app, base_url=client.pop("base_url", BASE_URL), **client)
        test_client.__enter__()
        found = Bundled(app, test_client, store, token, config, root, clock, loaded)
        made.append(found)
        return found

    yield make
    for found in made:
        found.client.__exit__(None, None, None)
        found.store.close()


@dataclass(frozen=True)
class Sent:
    """One request for ``drive``: its path as the scope holds it (decoded), and the rest."""

    path: str
    method: str = "GET"
    headers: tuple[tuple[bytes, bytes], ...] = ((b"host", b"127.0.0.1:8000"),)
    root_path: str = ""
    raw_path: bytes | None = None
    query: bytes = b""
    client: tuple[str, int] = ("127.0.0.1", 50000)
    websocket: bool = False


@dataclass
class Got:
    status: int | None
    headers: list[tuple[bytes, bytes]]
    body: bytes
    accepted: bool = False
    """For a WebSocket: whether the application accepted it."""

    def header(self, name: bytes) -> bytes | None:
        found = [value for key, value in self.headers if key == name]
        assert len(found) <= 1, (name, found)
        return found[0] if found else None


async def _one(app: ASGIApp, sent: Sent) -> Got:
    scope: Scope = {
        "type": "websocket" if sent.websocket else "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": sent.method,
        "scheme": "ws" if sent.websocket else "http",
        "path": sent.path,
        "raw_path": sent.raw_path if sent.raw_path is not None else sent.path.encode(),
        "root_path": sent.root_path,
        "query_string": sent.query,
        "headers": list(sent.headers),
        "client": sent.client,
        "server": ("127.0.0.1", 8000),
    }
    if sent.websocket:
        scope.pop("method")
        scope["subprotocols"] = []
    got = Got(None, [], b"")
    pending: list[Message] = (
        [{"type": "websocket.connect"}]
        if sent.websocket
        else [{"type": "http.request", "body": b"", "more_body": False}]
    )

    async def receive() -> Message:
        if pending:
            return pending.pop(0)
        return (
            {"type": "websocket.disconnect", "code": 1000}
            if sent.websocket
            else {"type": "http.disconnect"}
        )

    async def send(message: Message) -> None:
        if message["type"] == "http.response.start":
            got.status = message["status"]
            got.headers = [(bytes(k).lower(), bytes(v)) for k, v in message.get("headers", [])]
        elif message["type"] == "http.response.body":
            got.body += message.get("body", b"")
        elif message["type"] == "websocket.accept":
            got.accepted = True
        elif message["type"] == "websocket.close":
            got.status = got.status or 1000

    await app(scope, receive, send)
    return got


def _drive(app: ASGIApp, requests: Sequence[Sent]) -> list[Got]:
    answers: list[Got] = []

    async def main() -> None:
        with anyio.fail_after(600):
            for sent in requests:
                answers.append(await _one(app, sent))

    anyio.run(main)
    return answers


@pytest.fixture
def drive() -> Callable[[ASGIApp, Sequence[Sent]], list[Got]]:
    return _drive


@pytest.fixture
def sent() -> type[Sent]:
    """``Sent``, the requests ``drive`` sends."""
    return Sent

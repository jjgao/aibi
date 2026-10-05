"""The operator tests' server and CLI, as classes a module can import by name (a conftest is
pytest's, not a module to import): ``Server`` is the whole application in process over a store,
with the operator's headers and the requests most tests make, and ``Cli`` runs ``aibi`` in
process against it. ``conftest.py`` builds them (``make_server``, ``run_cli``)."""

import io
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import JsonValue

from aibi.core.api.config import ServerConfig
from aibi.core.operator import cli
from aibi.core.operator.auth import encode_operator
from aibi.core.operator.router import Services
from aibi.core.store.store import Store

ADA = "Ada Lovelace"
BASE_URL = "http://127.0.0.1:8000"


@dataclass
class Server:
    root: Path
    imports: Path
    store: Store
    token: str
    config: ServerConfig
    services: Services
    app: FastAPI
    client: TestClient
    others: list[TestClient] = field(default_factory=list[TestClient])

    def headers(self, operator: str = ADA, **extra: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Aibi-Operator": encode_operator(operator),
            **extra,
        }

    def get(self, path: str, *, operator: str = ADA, **given: Any) -> httpx.Response:
        headers = {**self.headers(operator), **given.pop("headers", {})}
        return self.client.get(path, headers=headers, **given)

    def post(
        self, path: str, body: JsonValue = None, *, operator: str = ADA, **given: Any
    ) -> httpx.Response:
        headers = {**self.headers(operator), **given.pop("headers", {})}
        return self.client.post(path, json={} if body is None else body, headers=headers, **given)

    def import_(self, dataset: str, source: Path, **given: JsonValue) -> dict[str, Any]:
        response = self.post(
            f"/operator/datasets/{dataset}/import", {"source": {"path": str(source)}, **given}
        )
        assert response.status_code == 200, response.text
        found: dict[str, Any] = response.json()
        return found

    def open(self, dataset: str, operator: str = ADA) -> dict[str, Any]:
        response = self.post(f"/operator/datasets/{dataset}/session/open", operator=operator)
        assert response.status_code == 200, response.text
        found: dict[str, Any] = response.json()
        return found

    def change(
        self,
        dataset: str,
        opened: dict[str, Any],
        *edits: dict[str, Any],
        expected: str | None = None,
    ) -> httpx.Response:
        body = {
            "handle": opened["handle"],
            "expected": expected or opened["draft"],
            "edits": list(edits),
        }
        return self.post(f"/operator/datasets/{dataset}/session/change", body)

    def end(self, dataset: str, opened: dict[str, Any], draft: str, action: str) -> httpx.Response:
        body = {"handle": opened["handle"], "expected": draft}
        return self.post(f"/operator/datasets/{dataset}/session/{action}", body)

    def curate(self, dataset: str, *edits: dict[str, Any]) -> int:
        """Open a session, apply one change and publish it; the label published."""
        opened = self.open(dataset)
        changed = self.change(dataset, opened, *edits)
        assert changed.status_code == 200, changed.text
        published = self.end(dataset, opened, changed.json()["draft"], "publish")
        assert published.status_code == 200, published.text
        label: int = published.json()["label"]
        return label

    def with_client(self, **given: Any) -> TestClient:
        """Another client of the application, closed with the server."""
        other = TestClient(self.app, base_url=given.pop("base_url", BASE_URL), **given)
        other.__enter__()
        self.others.append(other)
        return other


THE_SERVER: Any = object()
"""The CLI's client is the server's ``TestClient``."""


@dataclass(frozen=True)
class Ran:
    code: int
    out: str
    err: str


@dataclass
class Cli:
    server: Server
    state: Path
    operator: str = ADA

    def __call__(
        self,
        *argv: str,
        operator: str | None = "",
        token: str | None = None,
        stdin: str = "",
        client: Any = THE_SERVER,
        **environ: str,
    ) -> Ran:
        """Run ``aibi``; ``client=None`` lets it make its own HTTP client."""
        given = {"XDG_STATE_HOME": str(self.state), **environ}
        if operator is not None:
            given["AIBI_OPERATOR"] = operator or self.operator
        given["AIBI_TOKEN"] = self.server.token if token is None else token
        if token == "":
            del given["AIBI_TOKEN"]
        out, err = io.StringIO(), io.StringIO()
        code = cli.main(
            list(argv),
            client=self.server.client if client is THE_SERVER else client,
            environ=given,
            stdin=io.StringIO(stdin),
            stdout=out,
            stderr=err,
        )
        return Ran(code, out.getvalue(), err.getvalue())

    @property
    def state_file(self) -> Path:
        return self.state / "aibi" / "sessions.json"

"""The operator tests' server: the whole application in process, over a store in ``tmp_path``.

- ``make_server`` builds a ``Store`` with a ticking clock, a curator token from ``new_token``, a
  configuration whose import directory is under ``tmp_path``, and the application behind
  request protection, with a ``TestClient`` at ``http://127.0.0.1:8000``; ``server`` is one with
  the defaults. Rates are generous unless a test sets them, so that repeated requests are not
  limited.
- ``sites`` writes a small dataset of sites and their visits, as CSV files, into the import
  directory, so that most tests need no worker process; ``library`` copies the lending library
  of ``fixtures/library`` there, without ``formats/`` or its README.
- ``surveys`` is a test-only pack (SPEC §10.1): its importer is the core's file importer, which
  lists the pack in the dataset's ``packs``; its proposer proposes a definition for each table
  that has none.
- ``run_cli`` runs ``aibi`` in process against the application, with its state in ``tmp_path``;
  ``make_cli`` makes one for another server, operator or state directory.

Test modules can't import one another (``--import-mode=importlib``), so the helpers are given as
fixtures, as in the other test directories.
"""

import io
import itertools
import shutil
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import JsonValue

import aibi
from aibi.core.api.app import create_app
from aibi.core.api.config import BASE, ServerConfig
from aibi.core.api.protection import Policy
from aibi.core.api.serve import services_of
from aibi.core.importers.files import FileImporter
from aibi.core.operator import cli
from aibi.core.operator.auth import encode_operator, hash_token, new_token
from aibi.core.operator.router import Services
from aibi.core.schema.descriptors import DatasetDescriptor, Descriptor, TableDescriptor
from aibi.core.schema.pack_api import (
    ConfinedPath,
    ImportOptions,
    ImportResult,
    Pack,
    PackManifest,
    PackRegistry,
    Proposal,
    ReleaseView,
)
from aibi.core.store.store import Store

FIXTURES = Path(__file__).resolve().parents[4] / "fixtures"
ADA = "Ada Lovelace"
BASE_URL = "http://127.0.0.1:8000"
SITES = b"site_id,name\ns1,North\ns2,South\ns3,East\n"
VISITS = b"visit_id,site_id,count\nv1,s1,3\nv2,s2,5\nv3,s1,1\nv4,s3,2\n"
GENEROUS = {"per_minute": 1_000_000, "burst": 1_000_000}


def _clock() -> Callable[[], datetime]:
    ticks = itertools.count()
    start = datetime(2026, 1, 1, tzinfo=UTC)
    return lambda: start + timedelta(seconds=next(ticks))


# --- The surveys pack -----------------------------------------------------------------------------

SURVEYS_BY = "importer:surveys@1.0.0"


def _listing(descriptor: Descriptor, at: str) -> Descriptor:
    written: dict[str, Any] = descriptor.model_dump(mode="json")
    written["fields"]["packs"] = ["surveys"]
    written["curation"]["/fields/packs"] = {
        "status": "imported",
        "by": SURVEYS_BY,
        "at": at,
        "inferred": ["surveys"],
    }
    return DatasetDescriptor.model_validate(written)


class SurveyImporter:
    """The core's file importer, whose dataset lists the surveys pack."""

    def import_source(self, source: ConfinedPath, options: ImportOptions) -> ImportResult:
        result = FileImporter().import_source(source, options)
        descriptors = [
            _listing(d, options.at) if isinstance(d, DatasetDescriptor) else d
            for d in result.descriptors
        ]
        return replace(result, descriptors=descriptors)


def propose_definitions(release: ReleaseView) -> Sequence[Proposal]:
    return [
        Proposal(
            descriptor.id,
            "/definition",
            f"The survey's {descriptor.id}",
            evidence="The surveys pack defines every table",
        )
        for descriptor in release.descriptors.values()
        if isinstance(descriptor, TableDescriptor) and descriptor.definition is None
    ]


def surveys_registry() -> PackRegistry:
    pack = Pack(
        manifest=PackManifest(
            id="surveys", version="1.0.0", results_version=1, requires_core=">=0.0.1"
        ),
        importer=SurveyImporter(),
        proposer=propose_definitions,
    )
    return PackRegistry([pack], core_version=aibi.__version__)


@pytest.fixture
def surveys() -> PackRegistry:
    return surveys_registry()


# --- The server ---------------------------------------------------------------------------------


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


MakeServer = Callable[..., Server]


@pytest.fixture
def make_server(tmp_path: Path) -> Iterator[MakeServer]:
    made: list[Server] = []

    def make(
        *,
        name: str = "server",
        server: dict[str, Any] | None = None,
        imports: dict[str, Any] | None = None,
        registry: PackRegistry | None = None,
        **client: Any,
    ) -> Server:
        root = tmp_path / name
        imported = root / "imports"
        imported.mkdir(parents=True)
        token = new_token()
        rates = {"operator": GENEROUS, "api": GENEROUS, "token_failures": GENEROUS}
        written: dict[str, Any] = {
            "server": {"rates": rates, **(server or {})},
            "curator": {"token_hash": hash_token(token)},
            "storage": {"data": "data", "imports": ["imports"]},
            "imports": imports or {},
        }
        config = ServerConfig.model_validate(written, context={BASE: root})
        store = Store(config.storage.data, clock=_clock())
        services = services_of(config, store, registry or PackRegistry((), core_version="0.0.1"))
        if registry is None:
            services = replace(services, registry=None)
        policy = Policy.of(config, csrf_key=b"k" * 32)
        app = create_app(policy, services)
        test_client = TestClient(app, base_url=client.pop("base_url", BASE_URL), **client)
        test_client.__enter__()
        found = Server(root, imported, store, token, config, services, app, test_client)
        made.append(found)
        return found

    yield make
    for found in made:
        for other in found.others:
            other.__exit__(None, None, None)
        found.client.__exit__(None, None, None)
        found.store.close()


@pytest.fixture
def server(make_server: MakeServer) -> Server:
    return make_server()


@pytest.fixture
def sites(server: Server) -> Path:
    """A dataset of sites and their visits, as CSV files in the import directory."""
    directory = server.imports / "sites"
    directory.mkdir()
    (directory / "sites.csv").write_bytes(SITES)
    (directory / "visits.csv").write_bytes(VISITS)
    return directory


@pytest.fixture
def library(server: Server) -> Path:
    """The lending library of ``fixtures/library`` in the import directory."""
    target = server.imports / "library"
    shutil.copytree(FIXTURES / "library", target, ignore=shutil.ignore_patterns("formats", "*.md"))
    return target


# --- The CLI --------------------------------------------------------------------------------------


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


@pytest.fixture
def run_cli(server: Server, tmp_path: Path) -> Cli:
    return Cli(server, tmp_path / "state")


@pytest.fixture
def make_cli() -> Callable[..., Cli]:
    """A CLI for another server, operator or state directory."""
    return Cli

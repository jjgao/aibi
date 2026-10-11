"""The pack installed as a deployment installs it (SPEC §10.1, D253, D293, D404): named by its
module in ``[packs] modules``, loaded by the server's loader in a fresh interpreter, with nothing
on standard error and DuckDB not loaded; its concepts listed and read as MCP resources; and the
core's own non-biomedical fixtures imported and published the same with the pack as without it.
"""

import json
import shutil
import subprocess
import sys
import textwrap
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from aibi.core.operator.auth import hash_token, new_token
from aibi.core.schema.pack_api import PackRegistry

FIXTURES = Path(__file__).resolve().parents[4] / "fixtures"

PROBE = """
import contextlib, io, json, sys
from pathlib import Path
from aibi.core.api import packs, serve
from aibi.core.api.app import create_app
from aibi.core.api.config import load_config
from aibi.core.api.protection import Policy
from aibi.core.store.store import Store

config = load_config(Path(CONFIG))
out, err = io.StringIO(), io.StringIO()
checked = serve.main(["check", "--config", CONFIG], environ={}, stdin=io.StringIO(), stdout=out,
                     stderr=err)
loader_err = io.StringIO()
registry = packs.registry_of(config, loader_err)
found = {
    "check": [checked, out.getvalue(), err.getvalue()],
    "loader_err": loader_err.getvalue(),
    "listing": packs.listing(registry),
    "packs": sorted(name for name in sys.modules if name.startswith("aibi.packs")),
}
store = Store(config.storage.data)
try:
    app = create_app(
        Policy.of(config, csrf_key=b"k" * 32),
        serve.services_of(config, store, registry),
        tools=serve.tools_of(config, store, registry),
    )
    from fastapi.testclient import TestClient

    accept = {"Accept": "application/json, text/event-stream"}
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        def rpc(method, params=None):
            message = {"jsonrpc": "2.0", "id": 1, "method": method}
            if params is not None:
                message["params"] = params
            return client.post("/mcp", json=message, headers=accept).json()

        listed = rpc("resources/list")["result"]["resources"]
        found["listed"] = sorted(r["uri"] for r in listed if r["uri"].startswith("aibi://concept/onco:"))
        read = rpc("resources/read", {"uri": "aibi://concept/onco:origin.diagnosis"})
        found["read"] = json.loads(read["result"]["contents"][0]["text"])
        missing = rpc("resources/read", {"uri": "aibi://concept/onco:orgin.diagnosis"})
        found["missing"] = missing["error"]["data"]["refusals"][0]["code"]
finally:
    store.close()
found["duckdb"] = sorted(name for name in ("duckdb", "_duckdb") if name in sys.modules)
print(json.dumps(found))
"""


def _config(root: Path) -> Path:
    (root / "imports").mkdir(parents=True)
    path = root / "aibi.toml"
    path.write_text(
        f'[curator]\ntoken_hash = "{hash_token(new_token())}"\n'
        '[storage]\ndata = "data"\nimports = ["imports"]\n'
        '[packs]\nmodules = ["aibi.packs.onco"]\n',
        encoding="utf-8",
    )
    return path


def test_the_server_installs_the_pack_by_its_module_s_name(tmp_path: Path) -> None:
    path = _config(tmp_path / "server")
    script = f"CONFIG = {str(path)!r}\n" + textwrap.dedent(PROBE)
    ran = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=120, check=False
    )
    assert ran.returncode == 0, ran.stderr
    assert ran.stderr == ""
    found: dict[str, Any] = json.loads(ran.stdout)
    code, out, err = found["check"]
    assert (code, err) == (0, "")
    assert "pack: onco 0.1.0 (results 1)" in out.splitlines()
    assert found["loader_err"] == ""
    assert found["listing"] == ["onco 0.1.0 (results 1)"]
    assert found["duckdb"] == []
    assert "aibi.packs.onco" in found["packs"]
    assert len(found["listed"]) == 15
    assert "aibi://concept/onco:origin.diagnosis" in found["listed"]
    assert found["read"]["id"] == "onco:origin.diagnosis"
    assert found["read"]["kind"] == "concept"
    assert found["read"]["fields"] == {"sort": "time_origin"}
    assert found["missing"] == "NOT_FOUND"


@pytest.mark.parametrize("fixture", ["biai", "library"])
def test_the_core_s_fixtures_publish_the_same_with_the_pack(
    make_world: Callable[[str], Any], registry: PackRegistry, fixture: str
) -> None:
    published: list[tuple[Any, Any]] = []
    for name, given in (("alone", None), ("onco", registry)):
        world = make_world(name)
        directory = world.root / "imports" / fixture
        shutil.copytree(FIXTURES / fixture, directory)
        published.append((world, world.publish(fixture, directory, given)))
    (alone_world, alone), (onco_world, onco) = published
    assert (alone.label, alone.manifest, alone.notes) == (onco.label, onco.manifest, onco.notes)
    assert alone_world.store.manifest(alone.manifest) == onco_world.store.manifest(onco.manifest)
    assert alone_world.store.descriptors(alone.manifest) == onco_world.store.descriptors(
        onco.manifest
    )

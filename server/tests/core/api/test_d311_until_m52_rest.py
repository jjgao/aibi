"""D311's pages stay the server's default until M5.2-rest is built (D431, D410).

The web's M5.2-min shows no descriptor member: its dataset screen says so in fixed words and names
D311's page at ``/datasets/<id>``, which a server run without a web bundle serves. So, while any
row of the display table (SPEC.md's Appendix C, which the web's ``generate:check`` holds equal to
``web/src/display/rows.ts``) owned by ``M5.2-rest`` has its module absent under ``web/``, this test
holds, **by behaviour**, that ``aibi-server serve`` run from a configuration without ``web_bundle``
answers ``GET /`` and ``GET /datasets/<id>`` with D311's page: it drives ``serve``'s own entry
(``serve.main(["serve", "--config", ...])``) over a store holding one published dataset, with
uvicorn's server replaced by a probe that makes the requests against the app uvicorn was handed,
inside its ``run``, while the store is open. A packaged bundle made ``serve``'s default anywhere on
that path (``bundle_of``, ``run``, ``create_app``, the configuration's default or its reader) or
the page's router dropped fails it (the five mutants of D431's removal gate). Once every such
module is present, the test skips and says so: M5.2-rest's own pull request decides the default
then. A web gate cell (``web/tests/gates/d311-proxy.test.ts``) scans the three modules for the
spelling of a packaged bundle, a proxy and no more.
"""

import io
import re
from pathlib import Path
from typing import Any

import pytest
from starlette.testclient import TestClient
from tests.core.catalog.world import World, orchard_files

from aibi.core.api import serve
from aibi.core.store.store import Store

ROOT = Path(__file__).resolve().parents[4]
SPEC = ROOT / "SPEC.md"
WEB = ROOT / "web"
HASH = "sha256:" + "b" * 64
OWNER = "M5.2-rest"

_ROW = re.compile(r"^\| (R\d+[a-z]?) \| [^|]* \| .* \| ([^|]+) \| `([^`|]+)` \| `([^`|]+)` \|$")


def rest_modules(spec: str, owner: str = OWNER) -> list[str]:
    """The modules of the rows of Appendix C that ``owner`` owns, in the table's order; refused
    (``ValueError``) when the appendix holds none, so that a table this cannot read fails loudly."""
    begin = spec.index("## Appendix C.")
    modules = [
        found.group(3)
        for line in spec[begin:].splitlines()
        if (found := _ROW.match(line)) is not None and found.group(2).strip() == owner
    ]
    if not modules:
        raise ValueError(f"Appendix C names no row of {owner}")
    return modules


def absent(modules: list[str], web: Path = WEB) -> list[str]:
    """The modules not yet under ``web``."""
    return [module for module in modules if not (web / module).exists()]


def _published(root: Path) -> None:
    (root / "imports").mkdir()
    store = Store(root / "data")
    try:
        World(root, store).publish("orchard", orchard_files(rows=6, harvests=8))
    finally:
        store.close()


def _config(root: Path) -> Path:
    path = root / "aibi.toml"
    path.write_text(
        f'[curator]\ntoken_hash = "{HASH}"\n[storage]\ndata = "data"\nimports = ["imports"]\n',
        encoding="utf-8",
    )
    return path


def _served(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """``serve``'s own entry over a store with the orchard published and no ``web_bundle``; what
    the app uvicorn was handed answers, asked inside uvicorn's ``run``."""
    _published(tmp_path)
    seen: dict[str, Any] = {}

    class Probe:
        def __init__(self, config: Any) -> None:
            self.config = config

        def run(self) -> None:
            client = TestClient(self.config.app)
            headers = {"host": "127.0.0.1"}
            for name, target in (
                ("catalogue", "/"),
                ("dataset", "/datasets/orchard"),
                ("asset", "/assets/index.js"),
            ):
                seen[name] = client.get(target, headers=headers)

    monkeypatch.setattr(serve.uvicorn, "Server", Probe)
    err = io.StringIO()
    code = serve.main(
        ["serve", "--config", str(_config(tmp_path))], environ={}, stderr=err, stdout=io.StringIO()
    )
    assert code == 0, err.getvalue()
    return seen


def _is_d311(response: Any) -> bool:
    return (
        response.status_code == 200
        and response.headers["content-type"].startswith("text/html")
        and "<script" not in response.text
        and "<header>" in response.text
        and " · aibi, read-only" in response.text
    )


def test_d311_s_pages_are_serve_s_default_while_m5_2_rest_is_not_built(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    missing = absent(rest_modules(SPEC.read_text(encoding="utf-8")))
    if not missing:
        pytest.skip(
            f"{OWNER} is built: its rows' modules are present, and D311's default is its PR's"
        )
    seen = _served(tmp_path, monkeypatch)
    assert set(seen) == {"catalogue", "dataset", "asset"}
    assert _is_d311(seen["catalogue"]), seen["catalogue"].text[:300]
    assert "orchard" in seen["catalogue"].text
    dataset = seen["dataset"]
    assert _is_d311(dataset), dataset.text[:300]
    # The descriptor members the web does not show yet: the curation status and who set it.
    assert "importer:aibi.files" in dataset.text
    assert "rel:harvests.tree_id" in dataset.text
    # No bundle answers what a bundle would own.
    assert seen["asset"].status_code == 404
    assert not seen["asset"].headers["content-type"].startswith("text/javascript")


def test_the_rows_of_m5_2_rest_are_read_from_appendix_c() -> None:
    modules = rest_modules(SPEC.read_text(encoding="utf-8"))
    assert modules == [
        "src/catalogue/Paging.tsx",
        "src/dataset/Descriptor.tsx",
        "src/dataset/Descriptor.tsx",
        "src/dataset/Descriptor.tsx",
        "src/dataset/Relationships.tsx",
        "src/dataset/TableDescriptors.tsx",
        "src/dataset/Column.tsx",
        "src/dataset/Paging.tsx",
    ]
    assert absent(modules)


def test_the_controls_a_built_table_skips_and_a_table_without_the_owner_is_refused(
    tmp_path: Path,
) -> None:
    spec = (
        "## Appendix C. The display table (D427)\n\n"
        "| Row | Basis | What the page must show | Screens | Spec | Owner | Module | Test |\n"
        "|---|---|---|---|---|---|---|---|\n"
        "| R53b | spec statement | A control | catalogue | D311 | M5.2-rest | `src/a.tsx` | `t` |\n"
        "| R54 | derived | Words | catalogue | D311 | M5.2-min | `src/b.tsx` | `t.ts` |\n"
    )
    assert rest_modules(spec) == ["src/a.tsx"]
    assert absent(["src/a.tsx"], tmp_path) == ["src/a.tsx"]
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.tsx").write_text("export const a = 1;\n", encoding="utf-8")
    assert absent(["src/a.tsx"], tmp_path) == []
    with pytest.raises(ValueError, match=r"names no row of M5\.2-rest"):
        rest_modules(spec.replace("M5.2-rest", "M5.5"))

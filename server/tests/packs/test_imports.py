"""What a pack may import of the core (D438): the import-linter contract of ``pyproject.toml``
forbids, to ``aibi.packs``, every core module but the allow-list, and DuckDB.

- The contract's list is the complement of ``ALLOWED`` over the core's modules as they are on
  disk (a new core module is forbidden to the packs until it is allowed here and in D438).
- The packs' real import graph, read from their source, imports of the core only what is allowed,
  and each allowed module is imported (the list is no wider than the packs need).
- Loading the oncology pack, in a fresh interpreter, loads no DuckDB.

The contract holds direct imports (``allow_indirect_imports``): the modules allowed import more of
the store (the gate, Parquet, the statistics).
"""

import ast
import json
import subprocess
import sys
import tomllib
from pathlib import Path

SERVER = Path(__file__).resolve().parents[2]
CORE = SERVER / "src" / "aibi" / "core"
PACKS = SERVER / "src" / "aibi" / "packs"
ALLOWED = frozenset(
    {
        "aibi.core.schema",
        "aibi.core.importers.errors",
        "aibi.core.importers.infer",
        "aibi.core.store.sources",
        "aibi.core.store.build",
    }
)
"""What a pack may import of the core: the schema layer whole (the pack API among it) and four
modules."""
SPLIT = ("aibi.core.importers", "aibi.core.store")
"""The core packages part of which is allowed, so the complement names their modules."""


def contract() -> dict[str, object]:
    found = tomllib.loads((SERVER / "pyproject.toml").read_text(encoding="utf-8"))
    (packs,) = [
        c
        for c in found["tool"]["importlinter"]["contracts"]
        if c["name"].startswith("A pack imports the pack API")
    ]
    return packs


def _modules(directory: Path, name: str) -> list[str]:
    """The modules and packages directly in ``directory`` (the package ``name``)."""
    found: list[str] = []
    for entry in sorted(directory.iterdir()):
        if entry.name.startswith(("_", ".")):
            continue
        if entry.is_dir() and (entry / "__init__.py").exists():
            found.append(f"{name}.{entry.name}")
        elif entry.suffix == ".py":
            found.append(f"{name}.{entry.stem}")
    return found


def complement() -> set[str]:
    forbidden: set[str] = {"duckdb"}
    for module in _modules(CORE, "aibi.core"):
        if module in SPLIT:
            inner = CORE / module.rpartition(".")[2]
            forbidden |= {m for m in _modules(inner, module) if m not in ALLOWED}
        elif module not in ALLOWED:
            forbidden.add(module)
    return forbidden


def test_the_contract_forbids_the_complement_of_the_allow_list() -> None:
    packs = contract()
    assert packs["type"] == "forbidden"
    assert packs["source_modules"] == ["aibi.packs"]
    assert packs["allow_indirect_imports"] is True
    listed = packs["forbidden_modules"]
    assert isinstance(listed, list)
    assert len(listed) == len(set(listed))
    assert set(listed) == complement()
    assert "ignore_imports" not in packs


def imported(path: Path) -> set[str]:
    """The modules a source file imports, absolute, each ``from x import y`` as ``x``."""
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module is not None and node.level == 0:
            found.add(node.module)
    return found


def test_the_packs_import_of_the_core_only_what_is_allowed() -> None:
    reached: set[str] = set()
    for path in PACKS.rglob("*.py"):
        for module in imported(path):
            if module == "aibi.core" or module.startswith("aibi.core."):
                assert module.startswith("aibi.core.schema") or module in ALLOWED, (path, module)
                reached.add(
                    module if not module.startswith("aibi.core.schema") else "aibi.core.schema"
                )
            assert module.partition(".")[0] != "duckdb", path
    assert reached == ALLOWED


def test_loading_the_pack_loads_no_duckdb() -> None:
    script = (
        "import json, sys\n"
        "import aibi.packs.onco\n"
        "print(json.dumps(sorted(m for m in sys.modules if m.split('.')[0] == 'duckdb')))\n"
    )
    ran = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=120, check=False
    )
    assert ran.returncode == 0, ran.stderr
    assert json.loads(ran.stdout) == []

"""The pack's tests load the pack lazily (SPEC P8): ``tests/core`` holds that the core's suite
loads no pack (``importers/test_pack.py``), and a combined run collects every test before it runs
any. So no module here imports the pack while it is collected: the scan below finds each form it
lists (``LOADERS`` and the import statements), and the subprocess test finds the pack loaded in
whatever other form, and each test's teardown unloads it (``conftest._unload_packs``), whatever
order the tests run in."""

import ast
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SERVER = HERE.parents[2]
PACKAGE = "aibi.packs"
LOADERS = frozenset(
    {
        "import_module",
        "__import__",
        "find_spec",
        "spec_from_file_location",
        "importorskip",
        "run_module",
        "run_path",
        "exec",
    }
)
"""The calls that may load a module by name."""


def _evaluated_at_import(tree: ast.Module) -> list[ast.AST]:
    """Every node of ``tree`` that runs when the module is imported: all but the bodies of
    functions and lambdas, whose decorators, defaults and annotations do run."""
    found: list[ast.AST] = []
    pending: list[ast.AST] = [tree]
    while pending:
        node = pending.pop()
        found.append(node)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            pending.extend(node.decorator_list)
            pending.extend(node.args.defaults)
            pending.extend(default for default in node.args.kw_defaults if default is not None)
            pending.extend(
                argument.annotation
                for argument in (*node.args.args, *node.args.kwonlyargs, *node.args.posonlyargs)
                if argument.annotation is not None
            )
            if node.returns is not None:
                pending.append(node.returns)
        elif isinstance(node, ast.Lambda):
            pending.extend(node.args.defaults)
            pending.extend(default for default in node.args.kw_defaults if default is not None)
        else:
            pending.extend(ast.iter_child_nodes(node))
    return found


def _names_the_pack(name: str | None) -> bool:
    return name is not None and (name == PACKAGE or name.startswith(PACKAGE + "."))


def _loads_at_import(source: str) -> list[str]:
    """What in ``source`` would load a module of ``aibi.packs`` when it is imported."""
    found: list[str] = []
    for node in _evaluated_at_import(ast.parse(source)):
        if isinstance(node, ast.Import):
            found += [alias.name for alias in node.names if _names_the_pack(alias.name)]
        elif isinstance(node, ast.ImportFrom):
            if _names_the_pack(node.module) or (
                node.module == "aibi" and any(alias.name == "packs" for alias in node.names)
            ):
                found.append(f"from {node.module}")
        elif isinstance(node, ast.Call):
            called = node.func
            name = called.attr if isinstance(called, ast.Attribute) else getattr(called, "id", "")
            if name in LOADERS:
                found.append(f"{name}(…) at line {node.lineno}")
    return found


def test_no_module_here_loads_the_pack_when_it_is_imported() -> None:
    modules = sorted(HERE.glob("*.py"))
    assert HERE / "conftest.py" in modules
    assert len(modules) >= 8
    for module in modules:
        assert _loads_at_import(module.read_text(encoding="utf-8")) == [], module.name


def test_the_scan_finds_every_form() -> None:
    forms = [
        "import aibi.packs.onco",
        "import aibi.packs",
        "import aibi.packs.onco as onco",
        "from aibi.packs.onco import PACK",
        "from aibi.packs import onco",
        "from aibi import packs",
        "import importlib\nimportlib.import_module('aibi.packs.onco')",
        "from importlib import import_module\nimport_module(NAME)",
        "__import__('aibi.packs.onco')",
        "class Holder:\n    import aibi.packs.onco",
        "if True:\n    import aibi.packs.onco",
        "try:\n    import aibi.packs.onco\nexcept ImportError:\n    pass",
        "def f(pack=__import__('aibi.packs.onco')):\n    pass",
        "@decorate(__import__('aibi.packs.onco'))\ndef f():\n    pass",
        "def f(pack: __import__('aibi.packs.onco')):\n    pass",
        "f = lambda pack=__import__('aibi.packs.onco'): pack",
        "import pytest\npytest.importorskip('aibi.packs.onco')",
        "import runpy\nrunpy.run_module('aibi.packs.onco')",
        "import runpy\nrunpy.run_path(PATH)",
        "exec('import aibi.packs.onco')",
    ]
    for form in forms:
        assert _loads_at_import(form), form
    lazy = [
        "def f():\n    import aibi.packs.onco",
        "def f():\n    from aibi.packs.onco import PACK",
        "def f():\n    return importlib.import_module('aibi.packs.onco')",
        "f = lambda: __import__('aibi.packs.onco')",
        "def f():\n    exec('import aibi.packs.onco')",
        "import aibi.core.schema.pack_api",
        "MODULE = 'aibi.packs.onco'",
    ]
    for form in lazy:
        assert _loads_at_import(form) == [], form


PLUGIN = """
import sys


def pytest_collection_finish(session):
    loaded = sorted(m for m in sys.modules if m == "aibi.packs" or m.startswith("aibi.packs."))
    print("PACKS LOADED AT COLLECTION:", loaded)
"""


def test_a_combined_run_in_either_order_loads_no_pack_where_the_core_says_none(
    tmp_path: Path,
) -> None:
    (tmp_path / "collected_packs.py").write_text(PLUGIN, encoding="utf-8")
    core = "tests/core/importers/test_pack.py::test_the_core_suite_loads_no_pack"
    environ = {**os.environ, "PYTHONPATH": str(tmp_path)}
    for order in (
        [str(HERE.relative_to(SERVER)), core],
        [core, str(HERE.relative_to(SERVER) / "test_contract.py")],
    ):
        ran = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "-p",
                "no:cacheprovider",
                "-p",
                "collected_packs",
                "-k",
                "core_suite_loads_no_pack or test_contract",
                *order,
            ],
            cwd=SERVER,
            env=environ,
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        assert ran.returncode == 0, ran.stdout + ran.stderr
        assert "PACKS LOADED AT COLLECTION: []" in ran.stdout, ran.stdout

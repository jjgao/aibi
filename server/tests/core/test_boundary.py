"""The core must not load any pack, even indirectly (SPEC P8).

Two checks enforce the boundary, and each covers what the other misses:

- import-linter's contract in pyproject.toml reads every ``import`` statement in ``aibi.core``,
  including those inside functions, but it cannot see dynamic imports.
- The runtime check here imports every core module in a fresh interpreter and fails if a pack
  module was loaded. It sees what module-level code does, dynamic imports and ``aibi``'s own
  ``__init__`` included, but not an import inside a function that never ran.

import-linter does not look inside a directory without an ``__init__.py``, or below one, so
every directory on the way to a module must be a regular package. The tests below enforce that;
ruff's INP rule catches only directories that hold modules themselves. The runtime check finds
modules on disk and imports them either way.
"""

import subprocess
import sys
import textwrap
from pathlib import Path

import aibi

PACKAGE_DIR = Path(aibi.__file__).parent
SOURCE_ROOT = PACKAGE_DIR.parent

_LOAD_AND_REPORT = textwrap.dedent(
    """
    import importlib
    import sys

    source_root, forbidden, *modules = sys.argv[1:]
    sys.path.insert(0, source_root)
    for name in modules:
        importlib.import_module(name)
    leaked = sorted(m for m in sys.modules if m == forbidden or m.startswith(forbidden + "."))
    if leaked:
        raise SystemExit("importing the core loaded " + ", ".join(leaked))
    """
)


def modules_under(source_root: Path, package_dir: Path) -> list[str]:
    """Every module in a package directory, found on disk rather than through the import system."""
    names: list[str] = []
    for path in sorted(package_dir.rglob("*.py")):
        parts = path.relative_to(source_root).with_suffix("").parts
        names.append(".".join(parts[:-1] if parts[-1] == "__init__" else parts))
    return names


def directories_without_init(source_root: Path, package_dir: Path) -> list[str]:
    """Directories on the way to a module that are not regular packages.

    An intermediate directory with no modules of its own still needs an ``__init__.py``:
    import-linter does not look below a directory without one.
    """
    directories: set[Path] = set()
    for path in package_dir.rglob("*.py"):
        for directory in path.parents:
            directories.add(directory)
            if directory == package_dir:
                break
    return sorted(
        directory.relative_to(source_root).as_posix()
        for directory in directories
        if not (directory / "__init__.py").is_file()
    )


def load(source_root: Path, modules: list[str], forbidden: str) -> subprocess.CompletedProcess[str]:
    """Import the modules in a fresh interpreter; it fails if any ``forbidden`` module loaded."""
    return subprocess.run(
        [sys.executable, "-c", _LOAD_AND_REPORT, str(source_root), forbidden, *modules],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )


def test_importing_every_core_module_loads_no_pack() -> None:
    modules = modules_under(SOURCE_ROOT, PACKAGE_DIR / "core")
    assert "aibi.core" in modules
    result = load(SOURCE_ROOT, modules, "aibi.packs")
    assert result.returncode == 0, result.stderr or result.stdout


def test_every_directory_of_modules_is_a_regular_package() -> None:
    assert directories_without_init(SOURCE_ROOT, PACKAGE_DIR) == []


def _write_package(root: Path, files: dict[str, str]) -> None:
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


def test_the_runtime_check_catches_a_dynamic_import_in_a_nested_module(tmp_path: Path) -> None:
    leak = "import importlib\n\nimportlib.import_module('demo.' + 'packs')\n"
    _write_package(
        tmp_path,
        {
            "demo/__init__.py": "",
            "demo/core/__init__.py": "",
            "demo/core/sub/__init__.py": "",
            "demo/core/sub/leak.py": leak,
            "demo/packs/__init__.py": "",
        },
    )
    modules = modules_under(tmp_path, tmp_path / "demo" / "core")
    assert modules == ["demo.core", "demo.core.sub", "demo.core.sub.leak"]
    result = load(tmp_path, modules, "demo.packs")
    assert result.returncode != 0
    assert "importing the core loaded demo.packs" in result.stderr


def test_a_directory_of_modules_without_init_is_reported(tmp_path: Path) -> None:
    _write_package(
        tmp_path,
        {
            "demo/__init__.py": "",
            "demo/core/__init__.py": "",
            "demo/core/sub/leak.py": "",
            "demo/core/data/table.json": "{}",
        },
    )
    assert directories_without_init(tmp_path, tmp_path / "demo") == ["demo/core/sub"]


def test_an_intermediate_directory_without_init_is_reported(tmp_path: Path) -> None:
    _write_package(
        tmp_path,
        {
            "demo/__init__.py": "",
            "demo/core/__init__.py": "",
            "demo/core/engine/sql/__init__.py": "",
            "demo/core/engine/sql/compile.py": "",
        },
    )
    assert directories_without_init(tmp_path, tmp_path / "demo") == ["demo/core/engine"]

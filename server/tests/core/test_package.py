import ast
import re
import tomllib
from importlib.metadata import version
from pathlib import Path

import aibi

SERVER = Path(__file__).parents[2]


def test_version_comes_from_the_package_metadata() -> None:
    assert aibi.__version__ == version("aibi")


def _marked(module: Path) -> bool:
    """Whether a module assigns ``pytestmark = pytest.mark.million`` at its top level."""
    for node in ast.parse(module.read_text()).body:
        if isinstance(node, ast.Assign) and [ast.unparse(t) for t in node.targets] == [
            "pytestmark"
        ]:
            return ast.unparse(node.value) == "pytest.mark.million"
    return False


def _steps(ci: str, job: str) -> list[list[str]]:
    """The steps of a job of the CI workflow, each as its lines, and the job's own lines first."""
    lines = ci.splitlines()
    start = lines.index(f"  {job}:")
    end = next((i for i in range(start + 1, len(lines)) if re.match(r"  \S", lines[i])), len(lines))
    found: list[list[str]] = [[]]
    for line in lines[start + 1 : end]:
        if line.startswith("      - "):
            found.append([])
        found[-1].append(line)
    return found


def test_the_million_row_tests_are_deselected_by_default_and_run_by_a_ci_job_of_their_own() -> None:
    """D372: ``addopts`` deselects the ``million`` marker, which is registered; every module
    under ``tests/core/determinism`` but its conftests carries it at its top level (pytest
    collects ``*_test.py`` too, and subdirectories); CI's ``determinism`` job runs them with
    ``-m million`` in a step that always runs and fails the job, and a later step checks its
    report holds every test passed (``determinism_report.py``); and the core step passes no
    ``-m``, ``addopts`` or environment of its own, any of which could replace ``addopts``'
    ``-m``."""
    options = tomllib.loads((SERVER / "pyproject.toml").read_text())["tool"]["pytest"]
    given = options["ini_options"]["addopts"]
    assert given[given.index("-m") + 1] == "not million"
    assert any(marker.startswith("million:") for marker in options["ini_options"]["markers"])
    modules = sorted((SERVER / "tests" / "core" / "determinism").rglob("*.py"))
    tests = [module for module in modules if module.name not in ("conftest.py", "__init__.py")]
    assert tests
    assert [module.name for module in tests if not _marked(module)] == []
    ci = (SERVER.parent / ".github" / "workflows" / "ci.yml").read_text()
    [job, *steps] = _steps(ci, "determinism")
    assert not any(re.match(r"    (if|continue-on-error):", line) for line in job)
    [step] = [
        step
        for step in steps
        if any(
            line.strip().startswith("uv run pytest tests/core/determinism -m million")
            for line in step
        )
    ]
    assert not any(re.match(r"\s+-?\s*(if|continue-on-error|shell):", line) for line in step)
    assert not any("||" in line for line in step)
    after = steps[steps.index(step) + 1 :]
    [checked] = [
        step
        for step in after
        if any("python3 ../.github/scripts/determinism_report.py reports/" in line for line in step)
    ]
    assert not any(re.match(r"\s+-?\s*(if|continue-on-error|shell):", line) for line in checked)
    [core] = [
        step
        for step in _steps(ci, "server")[1:]
        if any("run: uv run pytest tests/core " in line for line in step)
    ]
    [run] = [line for line in core if "run: uv run pytest tests/core " in line]
    assert not re.search(r"\s-m|addopts", run)
    assert not any(re.match(r"\s+(env|shell):", line) for line in core)

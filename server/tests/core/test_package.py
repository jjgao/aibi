import ast
import re
import tomllib
from importlib.metadata import version
from pathlib import Path

import pytest
import yaml

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
    """D372: ``addopts`` deselects the ``million`` marker (and D422's ``measurement``, a run by
    hand), which is registered; every module
    under ``tests/core/determinism`` but its conftests carries it at its top level (pytest
    collects ``*_test.py`` too, and subdirectories); CI's ``determinism`` job runs them with
    ``-m million`` in a step that always runs and fails the job, and a later step checks its
    report holds every test passed (``determinism_report.py``); and the core step passes no
    ``-m``, ``addopts`` or environment of its own, any of which could replace ``addopts``'
    ``-m``."""
    options = tomllib.loads((SERVER / "pyproject.toml").read_text())["tool"]["pytest"]
    given = options["ini_options"]["addopts"]
    assert given[given.index("-m") + 1] == "not million and not measurement"
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


WORDING_RUN = {
    "mkdir": 'mkdir -p "${RUNNER_TEMP}/wording-base"',
    "verify": 'git rev-parse --verify "origin/${BASE_REF}^{commit}"',
    "exists": 'git cat-file -e "origin/${BASE_REF}:${path}"',
    "show": (
        'git show "origin/${BASE_REF}:${path}" '
        '> "${RUNNER_TEMP}/wording-base/$(basename "${path}")"'
    ),
    "export": 'echo "AIBI_WORDING_BASE=${RUNNER_TEMP}/wording-base" >> "${GITHUB_ENV}"',
}
"""The lines of the base's wording step, as the step's script must have them."""


def _wording_step_violations(ci: str) -> list[str]:
    """What the server job's step that hands the base branch's wording to the tests must still be,
    read from the workflow as YAML: where the base ref reaches the shell, what makes the step fail,
    and what carries its directory to the test step."""
    steps = yaml.safe_load(ci)["jobs"]["server"]["steps"]
    found: list[str] = []
    checkout = steps[0]
    if not str(checkout.get("uses", "")).startswith("actions/checkout@"):
        found.append("the first step is not the checkout")
    if (checkout.get("with") or {}).get("fetch-depth") != 0:
        found.append("the checkout does not fetch every branch (fetch-depth: 0)")
    named = [step for step in steps if "AIBI_WORDING_BASE" in str(step.get("run", ""))]
    if len(named) != 1:
        return [*found, f"{len(named)} steps hand on AIBI_WORDING_BASE, not one"]
    [step] = named
    if step.get("if") != "github.event_name == 'pull_request'":
        found.append("the step does not run exactly on a pull request")
    if set(step) - {"name", "if", "env", "working-directory", "run"}:
        found.append(f"the step has keys beyond the ones read: {sorted(step)}")
    if step.get("env") != {"BASE_REF": "${{ github.base_ref }}"}:
        found.append("the base ref does not reach the step by env BASE_REF alone")
    run = str(step.get("run", ""))
    lines = [line.strip() for line in run.splitlines() if line.strip()]
    if "${{" in run:
        found.append("an expression is interpolated into the script")
    if not lines or lines[0] != "set -eu":
        found.append("the script does not begin with set -eu")
    if any(token in run for token in ("||", "set +e", "; true", "&& true", "continue-on-error")):
        found.append("a failure of the script is tolerated")
    for line in WORDING_RUN.values():
        if run.count(line) != 1:
            found.append(f"the script lacks the line {line!r}")
    if "HEAD" in run or "git checkout" in run or "git merge" in run:
        found.append("the script reads something other than origin/<base>")
    for match in re.finditer(r"\$\{?BASE_REF\}?", run):
        if match.group() != "${BASE_REF}" or run[: match.start()].count('"') % 2 != 1:
            found.append("a use of the base ref is not ${BASE_REF} inside double quotes")
    if re.search(r"\bBASE_REF\b", re.sub(r"\$\{BASE_REF\}", "", run)):
        found.append("BASE_REF is named other than as ${BASE_REF}")
    for name in ("wording.json", "text_projection.json"):
        if name not in run:
            found.append(f"the script does not copy {name}")
    core = [i for i, other in enumerate(steps) if "pytest tests/core " in str(other.get("run", ""))]
    if len(core) != 1 or steps.index(step) > core[0]:
        found.append("the step does not come before the core tests")
    elif "env" in steps[core[0]] or "shell" in steps[core[0]]:
        found.append("the core tests step sets an environment of its own")
    return found


def test_ci_hands_the_base_branchs_wording_to_the_tests_of_a_pull_request() -> None:
    """D374, D399: the server job reads the base's history (the whole of it), writes the base's
    ``wording.json`` and ``text_projection.json`` to the directory ``AIBI_WORDING_BASE`` names
    (before the core tests that compare with them), and fails when the base cannot be found, so
    that the check is not skipped in silence on a pull request. The base ref reaches the shell by
    ``env`` alone, quoted: an expression in the script is a shell injection by a branch name."""
    ci = (SERVER.parent / ".github" / "workflows" / "ci.yml").read_text()
    assert _wording_step_violations(ci) == []


WORDING_STEP_MUTANTS = {
    "the head ref for the base ref": (
        "BASE_REF: ${{ github.base_ref }}",
        "BASE_REF: ${{ github.head_ref }}",
    ),
    "the base ref interpolated into the script": (
        WORDING_RUN["verify"],
        'git rev-parse --verify "origin/${{ github.base_ref }}^{commit}"',
    ),
    "no fetch-depth": ("          fetch-depth: 0\n", ""),
    "a missing base passes": (WORDING_RUN["verify"], WORDING_RUN["verify"] + " || true"),
    "a missing base passes, by :": (WORDING_RUN["verify"], WORDING_RUN["verify"] + " || :"),
    "not handed on": (WORDING_RUN["export"], WORDING_RUN["export"].split(" >> ")[0]),
    "handed on to another name": (
        "AIBI_WORDING_BASE=${RUNNER_TEMP}",
        "AIBI_WORDING_BASE_DIR=${RUNNER_TEMP}",
    ),
    "the copy is of HEAD": (
        WORDING_RUN["show"],
        WORDING_RUN["show"].replace("origin/${BASE_REF}", "HEAD"),
    ),
    "the ref is not quoted": (
        'git show "origin/${BASE_REF}:${path}"',
        "git show origin/${BASE_REF}:${path}",
    ),
    "the ref is without braces": (
        'git show "origin/${BASE_REF}:${path}"',
        'git show "origin/$BASE_REF:${path}"',
    ),
    "the step runs on every event": (
        "if: github.event_name == 'pull_request'\n        env:",
        "if: always()\n        env:",
    ),
    "the script does not stop on an error": ("          set -eu\n", "          set -u\n"),
    "a failure is continued": (
        "        working-directory: .\n        run: |",
        "        working-directory: .\n        continue-on-error: true\n        run: |",
    ),
    "a copy is dropped": (
        "server/tests/core/analyses/golden/text_projection.json; do",
        "; do",
    ),
}
"""Each way the step has been found to weaken, as a change of the workflow's text."""


@pytest.mark.parametrize(
    ("old", "new"), list(WORDING_STEP_MUTANTS.values()), ids=list(WORDING_STEP_MUTANTS)
)
def test_the_check_of_the_wording_step_notices_each_way_it_weakens(old: str, new: str) -> None:
    ci = (SERVER.parent / ".github" / "workflows" / "ci.yml").read_text()
    assert old in ci
    assert _wording_step_violations(ci.replace(old, new, 1))

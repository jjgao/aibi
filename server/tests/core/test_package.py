import ast
import os
import re
import subprocess
import tomllib
from importlib.metadata import version
from pathlib import Path

import pytest
import yaml

import aibi

SERVER = Path(__file__).parents[2]
CI = SERVER.parent / ".github" / "workflows" / "ci.yml"
CORE_RUN = "uv run pytest ${{ matrix.tests }} "
"""How the step of CI's ``core`` job that runs the tests begins: pytest on the shard's paths."""


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
    hand, and D438's ``mutation``, which a job of its own runs), which is registered; every module
    under ``tests/core/determinism`` but its conftests carries it at its top level (pytest
    collects ``*_test.py`` too, and subdirectories); CI's ``determinism`` job runs them with
    ``-m million`` in a step that always runs and fails the job, and a later step checks its
    report holds every test passed (``determinism_report.py``); and the step of the ``core`` job
    that runs a shard's tests passes no ``-m``, ``addopts`` or environment of its own, any of
    which could replace ``addopts``' ``-m`` (the shards' paths pass no option either:
    ``test_every_core_test_runs_in_exactly_one_shard``)."""
    options = tomllib.loads((SERVER / "pyproject.toml").read_text())["tool"]["pytest"]
    given = options["ini_options"]["addopts"]
    assert given[given.index("-m") + 1] == "not million and not measurement and not mutation"
    assert any(marker.startswith("million:") for marker in options["ini_options"]["markers"])
    modules = sorted((SERVER / "tests" / "core" / "determinism").rglob("*.py"))
    tests = [module for module in modules if module.name not in ("conftest.py", "__init__.py")]
    assert tests
    assert [module.name for module in tests if not _marked(module)] == []
    ci = CI.read_text()
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
        for step in yaml.safe_load(ci)["jobs"]["core"]["steps"]
        if str(step.get("run", "")).startswith(CORE_RUN)
    ]
    assert not re.search(r"\s-m|addopts", core["run"])
    assert not {"env", "shell"} & set(core)


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
    """What the core job's step that hands the base branch's wording to the tests must still be,
    read from the workflow as YAML: where the base ref reaches the shell, what makes the step fail,
    and what carries its directory to the test step (each shard's: the job is a matrix)."""
    steps = yaml.safe_load(ci)["jobs"]["core"]["steps"]
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
    core = [i for i, other in enumerate(steps) if CORE_RUN in str(other.get("run", ""))]
    if len(core) != 1 or steps.index(step) > core[0]:
        found.append("the step does not come before the core tests")
    elif "env" in steps[core[0]] or "shell" in steps[core[0]]:
        found.append("the core tests step sets an environment of its own")
    return found


def test_ci_hands_the_base_branchs_wording_to_the_tests_of_a_pull_request() -> None:
    """D374, D399: each shard of the core job reads the base's history (the whole of it), writes
    the base's ``wording.json`` and ``text_projection.json`` to the directory
    ``AIBI_WORDING_BASE`` names (before the core tests that compare with them), and fails when
    the base cannot be found, so that the check is not skipped in silence on a pull request. The
    base ref reaches the shell by ``env`` alone, quoted: an expression in the script is a shell
    injection by a branch name."""
    ci = CI.read_text()
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
    ci = CI.read_text()
    assert ci.count(old) == 1
    assert _wording_step_violations(ci.replace(old, new))


TOP_LEVEL = "tests/core/test_*.py"
"""The core's test modules outside its directories, as a shard names them (the shell's glob)."""


def _core_units() -> list[str]:
    """What the core shards must run between them, each once: every directory of ``tests/core``
    that holds a test module (but ``determinism``, the ``determinism`` job's), and the modules at
    its top level."""
    core = SERVER / "tests" / "core"

    def holds_tests(directory: Path) -> bool:
        return any(directory.rglob("test_*.py")) or any(directory.rglob("*_test.py"))

    units = [
        f"tests/core/{directory.name}"
        for directory in sorted(core.iterdir())
        if directory.is_dir() and directory.name != "determinism" and holds_tests(directory)
    ]
    return [*units, TOP_LEVEL] if any(core.glob("test_*.py")) else units


def test_every_core_test_runs_in_exactly_one_shard() -> None:
    """CI's ``core`` job is a matrix of shards ``1`` to ``n``, none of which cancels the others,
    each a list of paths given to pytest (no option: ``-k``, ``--ignore`` or a deselection would
    turn D422's pin guard off, or make it ask for another shard's pins, and P8's check of a
    session that loaded a pack needs every argument under ``tests/core``); every directory of
    ``tests/core`` that holds tests, and its top-level modules, is in exactly one shard, so that
    a new directory runs somewhere, and nothing else is."""
    job = yaml.safe_load(CI.read_text())["jobs"]["core"]
    assert set(job["strategy"]) == {"fail-fast", "matrix"}
    assert job["strategy"]["fail-fast"] is False
    assert set(job["strategy"]["matrix"]) == {"include"}
    shards = job["strategy"]["matrix"]["include"]
    assert [shard["shard"] for shard in shards] == list(range(1, len(shards) + 1))
    assert all(set(shard) == {"shard", "tests"} and shard["tests"].split() for shard in shards)
    given = [path for shard in shards for path in shard["tests"].split()]
    assert sorted(given) == sorted(_core_units())
    core = SERVER / "tests" / "core"
    assert not list(core.glob("*_test.py"))
    # A directory named test_* would be run by the top-level glob's shard and by its own unit.
    assert not [directory for directory in core.glob("test_*") if directory.is_dir()]


NEEDED = ("static", "core", "other")
"""The jobs the ``Server checks`` job stands for."""
RESULTS = ("success", "failure", "cancelled", "skipped")
"""What a job the ``Server checks`` job needs can end as (``needs.<job>.result``)."""


def _aggregate_violations(ci: str) -> list[str]:
    """What CI's ``server`` job (``Server checks``, one check for the static checks, the core
    shards and the other tests) must still be: run whatever the jobs it needs did, and fail
    unless each succeeded, read from the workflow and run by ``bash`` with and without ``-e``
    (GitHub's default shell has it, and a ``shell`` of the step, the job or the workflow, by
    ``defaults``, is refused), which annotates each job's result when it fails (D334)."""
    workflow = yaml.safe_load(ci)
    jobs = workflow["jobs"]
    job = jobs.get("server") or {}
    found: list[str] = []
    if "defaults" in workflow or "defaults" in job:
        found.append("a default of the workflow or the job reaches the step (a shell)")
    if job.get("name") != "Server checks":
        found.append("the job is not named Server checks")
    if sorted(job.get("needs") or []) != sorted(NEEDED) or not set(NEEDED) <= set(jobs):
        found.append(f"the job does not need exactly {', '.join(NEEDED)}")
    if job.get("if") != "always()":
        found.append("the job does not run whatever the jobs it needs did")
    if "continue-on-error" in job:
        found.append("the job's failure is tolerated")
    steps = job.get("steps") or []
    if len(steps) != 1:
        return [*found, f"the job has {len(steps)} steps, not one"]
    [step] = steps
    if set(step) - {"name", "env", "run"}:
        found.append(f"the step has keys beyond the ones read: {sorted(step)}")
    if step.get("env") != {name.upper(): f"${{{{ needs.{name}.result }}}}" for name in NEEDED}:
        found.append("the step does not read each needed job's result, by env alone")
    run = str(step.get("run", ""))
    if "${{" in run:
        found.append("an expression is interpolated into the script")
    for name in NEEDED:
        if run.count(f'test "${{{name.upper()}}}" = success') != 1:
            found.append(f"the script does not test that {name} succeeded")
    success = dict.fromkeys(NEEDED, "success")
    cases = [success] + [
        {**success, name: result} for name in NEEDED for result in RESULTS if result != "success"
    ]
    for results, errexit in [(results, e) for results in cases for e in (["-e"], [])]:
        ran = subprocess.run(
            ["bash", "--noprofile", "--norc", *errexit, "-c", run],
            env={"PATH": os.environ["PATH"], **{key.upper(): v for key, v in results.items()}},
            capture_output=True,
            check=False,
        )
        if (ran.returncode == 0) != (results == success):
            found.append(
                f"the script exits {ran.returncode} ({errexit}) when the jobs' results are "
                f"{results}"
            )
        said = "::error title=Server checks::" + "; ".join(
            f"{name} {results[name]}" for name in NEEDED
        )
        if (said in ran.stdout.decode().splitlines()) == (results == success):
            found.append(f"the script's annotation is wrong when the results are {results}")
    return found


def test_the_server_checks_stand_for_every_server_job() -> None:
    """``Server checks``, the check the single server job was, runs after the static checks, the
    core shards and the other tests whatever they did (a failed job skips a job that needs it,
    and a skipped check passes) and fails unless each succeeded: failed, cancelled (a job over
    its time limit, D340) or skipped fails it."""
    assert _aggregate_violations(CI.read_text()) == []


AGGREGATE_MUTANTS = {
    "the job is skipped when one it needs failed": ("    if: always()\n", ""),
    "the job runs on success alone": ("    if: always()\n", "    if: success()\n"),
    "a job is not needed": ("needs: [static, core, other]", "needs: [static, core]"),
    "a result is not tested": (' && test "${OTHER}" = success', ""),
    "a failure is tolerated": ('test "${CORE}" = success', 'test "${CORE}" = success || true'),
    "a cancelled shard passes": ('test "${CORE}" = success', 'test "${CORE}" != failure'),
    "another job's result is read": (
        "CORE: ${{ needs.core.result }}",
        "CORE: ${{ needs.static.result }}",
    ),
    "the check is renamed": ("    name: Server checks\n", "    name: Server\n"),
    "the step's failure is continued": (
        "      - name: Every server job succeeded\n",
        "      - name: Every server job succeeded\n        continue-on-error: true\n",
    ),
    "the failure branch passes": ("            exit 1\n", "            exit 0\n"),
    "the failure is not annotated": (
        '            echo "::error title=Server checks::static ${STATIC}; core ${CORE}; '
        'other ${OTHER}"\n',
        "",
    ),
    "the tests run one a line": (' && test "${CORE}"', '\n          test "${CORE}"'),
    "the job's default shell passes everything": (
        "    timeout-minutes: 5\n",
        "    timeout-minutes: 5\n    defaults:\n      run:\n        shell: true {0}\n",
    ),
    "the workflow's default shell passes everything": (
        "\njobs:\n",
        "\ndefaults:\n  run:\n    shell: true {0}\n\njobs:\n",
    ),
}
"""Each way the ``Server checks`` job could weaken, as a change of the workflow's text."""


@pytest.mark.parametrize(
    ("old", "new"), list(AGGREGATE_MUTANTS.values()), ids=list(AGGREGATE_MUTANTS)
)
def test_the_check_of_the_server_checks_notices_each_way_it_weakens(old: str, new: str) -> None:
    ci = CI.read_text()
    assert ci.count(old) == 1
    assert _aggregate_violations(ci.replace(old, new))


STATIC_RUNS = (
    "uv run ruff check .",
    "uv run ruff format --check .",
    "uv run pyright",
    "uv run lint-imports",
)
"""The static job's commands, each a step of its own."""
OTHER_EMPTY = " || test $? -eq 5"
"""A test step's one tolerance: pytest's exit 5 (no test collected), in the other tests' step."""
ANNOTATIONS = "python3 ../.github/scripts/annotations.py"


def _needed_job_violations(ci: str) -> list[str]:
    """What the jobs ``Server checks`` stands for must still be, as the determinism and mutation
    jobs are: no job or step whose failure is tolerated or skipped (``if`` but on the base's
    wording step and D334's annotations step, ``continue-on-error``, ``||`` in a script but the
    other tests' exit 5), no ``env``, ``defaults`` or ``shell`` of the workflow or a job but the
    working directory, and the static job's four commands."""
    workflow = yaml.safe_load(ci)
    found: list[str] = []
    if "env" in workflow or "defaults" in workflow:
        found.append("the workflow has an env or defaults of its own")
    for name in NEEDED:
        job = workflow["jobs"].get(name) or {}
        if {"if", "continue-on-error", "env"} & set(job):
            found.append(f"{name}: the job has an if, continue-on-error or env")
        if job.get("defaults") != {"run": {"working-directory": "server"}}:
            found.append(f"{name}: the job's defaults are other than its working directory")
        for step in job.get("steps") or []:
            run = str(step.get("run", ""))
            label = step.get("name") or step.get("uses")
            if {"continue-on-error", "shell"} & set(step):
                found.append(f"{name}: {label} has continue-on-error or a shell")
            allowed = (
                {"github.event_name == 'pull_request'"} if "AIBI_WORDING_BASE" in run else set()
            )
            if run == ANNOTATIONS:
                allowed = {"failure()"}
            if "if" in step and step["if"] not in allowed:
                found.append(f"{name}: {label} runs under a condition")
            if run.replace(OTHER_EMPTY, "", 1 if name == "other" else 0).count("||"):
                found.append(f"{name}: {label} tolerates a failure (||)")
        if name == "other" and not any(
            str(step.get("run", "")).endswith(OTHER_EMPTY) for step in job.get("steps") or []
        ):
            found.append("other: the tests' step does not run pytest as stated")
    runs = [str(step.get("run", "")) for step in workflow["jobs"]["static"].get("steps") or []]
    found += [f"static: no step runs {run}" for run in STATIC_RUNS if runs.count(run) != 1]
    return found


def test_the_jobs_the_server_checks_stand_for_fail_when_their_steps_do() -> None:
    """The static checks, the core shards and the other tests fail when a step does: their
    failure is what ``Server checks`` reads."""
    assert _needed_job_violations(CI.read_text()) == []


NEEDED_JOB_MUTANTS = {
    "the core job's failure is continued": (
        "    timeout-minutes: 40\n    strategy:",
        "    timeout-minutes: 40\n    continue-on-error: true\n    strategy:",
    ),
    "the static job's failure is continued": (
        "    timeout-minutes: 15\n    defaults:",
        "    timeout-minutes: 15\n    continue-on-error: true\n    defaults:",
    ),
    "the core step's failure is continued": (
        "        id: core\n",
        "        id: core\n        continue-on-error: true\n",
    ),
    "the core step does not run": ("        id: core\n", "        id: core\n        if: false\n"),
    "the core step tolerates a failure": (
        "--junitxml=reports/core-${{ matrix.shard }}.xml",
        "--junitxml=reports/core-${{ matrix.shard }}.xml || true",
    ),
    "the other step tolerates any failure": (
        "--junitxml=reports/other.xml || test $? -eq 5",
        "--junitxml=reports/other.xml || true",
    ),
    "pyright does not run": ("run: uv run pyright", 'run: "true"'),
    "the other job's default shell": (
        "the job has 25 (D340).\n    runs-on: ubuntu-latest\n    timeout-minutes: 25\n"
        "    defaults:\n      run:\n        working-directory: server\n",
        "the job has 25 (D340).\n    runs-on: ubuntu-latest\n    timeout-minutes: 25\n"
        "    defaults:\n      run:\n        working-directory: server\n        shell: true {0}\n",
    ),
    "the workflow's env": ("\njobs:\n", "\nenv:\n  PYTEST_ADDOPTS: -x\n\njobs:\n"),
    "the static job does not run": (
        "    name: Static checks\n",
        "    name: Static checks\n    if: false\n",
    ),
}
"""Each way a job ``Server checks`` stands for could pass with a failed step, as a change of the
workflow's text (the cold review's M6, M21, M7, M16, M12, M23 and M11, and others)."""


@pytest.mark.parametrize(
    ("old", "new"), list(NEEDED_JOB_MUTANTS.values()), ids=list(NEEDED_JOB_MUTANTS)
)
def test_the_check_of_the_needed_jobs_notices_each_way_they_weaken(old: str, new: str) -> None:
    ci = CI.read_text()
    assert ci.count(old) == 1
    assert _needed_job_violations(ci.replace(old, new))

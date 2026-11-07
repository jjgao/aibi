"""D438's mutation table for the retention guards (``retention_mutants/table.py``, issue #136): its
anchors still name the code they change, every mutant has a stated outcome, and, under ``-m
mutation`` (CI's ``mutation`` job, about 18 to 27 minutes: D438's figure), every mutant replayed
against a copy of the tree has that outcome (``retention_mutants/replay.py``)."""

import importlib.util
import re
import tomllib
from collections.abc import Mapping
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

HERE = Path(__file__).resolve().parent
SERVER = HERE.parents[2]


def _load(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


table = _load(HERE / "retention_mutants" / "table.py", "retention_table")
replay = _load(HERE / "retention_mutants" / "replay.py", "retention_replay")
MUTANTS: tuple[Any, ...] = table.MUTANTS
FILES = frozenset(
    {table.CLINICAL, table.IMPORTER, table.FACETS, table.ENDPOINTS, table.VALIDATORS, table.BUILD}
    | {table.SCAN, table.TEST, table.PINS, table.STATE, table.STATE_PINS, table.REPORT}
)
REQUIRED = frozenset(
    {f"NB{n:02}" for n in range(1, 18)} | {"NB04b", "NB04c", "NB04d", "NB11b"}
    | {"S1", "S2", "S2b", *(f"S{n}" for n in range(4, 14))}
    | {"C01", "C02", *(f"C{n:02}" for n in range(4, 11))}
    | {f"C{n}" for n in (11, 13, 18, 37, 38, 40, 41, 42, 43, 44, 45, 46, 47, 48, 52, 53, 54, 55)}
    | {"C11i", "C46b", "C56", "C57", "C58", "C59", "G1", "G5", "G14", "G43"}
    | {"R1", "R2", "R5", "R9", "H1", "H2", "H5", "H6", "W7", "W17", "W34"}
    | {f"T{n}" for n in range(1, 11)} | {f"F{n}" for n in range(1, 17)}
    | {"MR2", "MR2b", "MR6", "MR8"}
    | {f"X{n}" for n in range(1, 7)} | {"Y4", "Y5", "V1"}
    | {f"N{n:02}" for n in range(1, 19)} | {"P1", "P2", "P3"}
    | {f"A{n}" for n in range(1, 11)} | {f"D{n}" for n in range(1, 14)}
    | {f"RV{n}" for n in range(1, 7)} | {f"RX{n}" for n in range(1, 7)}
    | {f"RG{n}" for n in (*range(1, 14), 17, 18, *range(21, 35))}
    | {f"GX{n}" for n in (4, 6, 7, 10, 16, 17, 19, 20, *range(21, 28))}
    | {"Z1", "Z2", "Z17", "Z21", "Z22", "ZP3"}
)  # fmt: skip
"""The mutants the table must keep: round 4's of #134 (NB01 to NB17, S1 to S13), the ones of #136's
plan reviews that the plan names (a kill to assert, or a survivor D438 names), and those of its
code review's rounds 1 to 3 (C58, C59, G, R, F, H, W, T, MR, X, Y and V rows), and PR B's: its
plan reviews' new routes (N01 to N18, P1, P2), a mutant of each rule and leg of the state guards
(A1 to A10, the allow-list's; D1 to D13, the snapshot's), and its code reviews' rows (round 1's
RV1 to RV6, P3 and the RG rows; round 2's RX1 to RX6 and the GX rows; round 3's Z rows and
ZP3)."""
SPAN = r"\d+(-\d+)?"
SPANS = rf"{SPAN}(, {SPAN})*"
FOUND = (
    rf"(allow at import)?((; )?allow after {SPANS})?((; )?snapshot after {SPANS})?"
    rf"((; )?hits after {SPANS})?"
)
VERDICT = re.compile(rf"(clean|outcomes|walks|canary|stores|crash|(?!; )(?=.){FOUND})")


def test_every_anchor_of_the_table_is_found_exactly_once() -> None:
    """A refactor of the pack or of the guards moves the code a mutant changes: the failure names
    each mutant and file, and the anchor is cut again, exactly, from the file's new text. A
    mutant's edits are applied in their sequence, as the replay applies them (``replay.edited``),
    each anchor found once in what the edits before it left, and none replaces its anchor by
    itself."""
    problems = []
    for mutant in MUTANTS:
        paths = {edit.path for edit in mutant.edits}
        texts = {path: (SERVER / path).read_text(encoding="utf-8") for path in paths}
        _, wrong = replay.edited(texts, mutant.edits)
        problems += [
            f"{mutant.name}: {problem}; find the code the mutant changes ({mutant.route}) in "
            "that file's new text, and copy it, exactly, as the edit's anchor (and its "
            "replacement) in tests/packs/onco/retention_mutants/table.py"
            for problem in wrong
        ]
    assert problems == [], "\n".join(problems)


def test_the_replay_applies_edits_in_sequence_and_refuses_one_that_changes_nothing() -> None:
    """``replay.edited``: an edit reads what the one before it left, and an anchor missing, found
    twice or replaced by itself is reported."""
    edit = table.Edit
    texts = {"f": "a b a"}
    assert replay.edited(texts, [edit("f", "b", "c"), edit("f", "c", "d")]) == ({"f": "a d a"}, [])
    assert replay.edited(texts, [edit("f", "a", "x")])[1] != []
    assert replay.edited(texts, [edit("f", "z", "x")])[1] != []
    assert replay.edited(texts, [edit("f", "b", "b")])[1] != []


def test_the_replay_reads_a_junit_report_as_the_table_does(tmp_path: Path) -> None:
    """``replay.outcomes``: a test killed is one whose every case (every parametrisation) failed or
    errored; a skip is no kill, and a test with no case that ran did not run."""
    cases = {
        "dead": "<failure/>",
        "erred": "<error/>",
        "split[a]": "<failure/>",
        "split[b]": "",
        "every[a]": "<failure/>",
        "every[b]": "<error/>",
        "skipped": "<skipped/>",
        "alive": "",
    }
    junit = tmp_path / "report.xml"
    junit.write_text(
        "<testsuites><testsuite>"
        + "".join(f'<testcase name="{name}">{body}</testcase>' for name, body in cases.items())
        + "</testsuite></testsuites>"
    )
    tests = ["dead", "erred", "split", "every", "skipped", "alive", "absent"]
    failed, passed, missing = replay.outcomes(junit, tests)
    assert failed == {"dead", "erred", "every"}
    assert passed == {"split", "alive"}
    assert missing == {"skipped", "absent"}


def test_the_table_states_an_outcome_for_every_mutant() -> None:
    """Each mutant is killed (its scan not clean, the source guard refusing it, or a test of the
    guards failing) or names why it survives, never both; it edits the pack or the guards; the
    tests it names are tests of ``test_retention.py`` (or, so named, of this module); and the
    table keeps every mutant it must."""
    names = [mutant.name for mutant in MUTANTS]
    assert len(names) == len(set(names))
    assert REQUIRED - set(names) == set()
    tests = set(re.findall(r"^def (test_\w+)", (HERE / "test_retention.py").read_text(), re.M))
    own = re.findall(r"^def (test_\w+)", Path(__file__).read_text(encoding="utf-8"), re.M)
    tests |= {f"test_retention_mutants.py::{name}" for name in own}
    for mutant in MUTANTS:
        killed = mutant.scan != "clean" or mutant.banned or mutant.tests != ()
        assert killed != (mutant.survives != ""), mutant.name
        assert VERDICT.fullmatch(mutant.scan), mutant.name
        assert mutant.edits, mutant.name
        assert {edit.path for edit in mutant.edits} <= FILES, mutant.name
        assert set(mutant.tests) <= tests, mutant.name


def test_the_replay_reads_a_scan_s_report_as_the_table_does() -> None:
    """``replay.verdict``: how a report fails, in the order the table reads it, or which guard
    found the mutant after which calls, each named, in turn (``VERDICT`` reads every verdict)."""
    outcomes = ["ok", "EXCEL_DATE"]
    clean = {
        "outcomes": outcomes, "walks": 2, "canary_seen": True, "hits": [],
        "stores_ignored": True, "rejects": [], "differences": [],
    }  # fmt: skip
    assert replay.verdict(clean, outcomes) == "clean"
    hits = [{"after": after} for after in (1, 2, 4, 2, 7, 8, 9)]
    assert replay.verdict({**clean, "hits": hits}, outcomes) == "hits after 1-2, 4, 7-9"
    rejects = [{"after": after} for after in (0, 0, 3, 4)]
    changed = [{"after": after} for after in (1, 2, 9)]
    every = {**clean, "hits": hits, "rejects": rejects, "differences": changed}
    found = "allow at import; allow after 3-4; snapshot after 1-2, 9; hits after 1-2, 4, 7-9"
    assert replay.verdict(every, outcomes) == found
    assert replay.verdict({**clean, "rejects": rejects[:2]}, outcomes) == "allow at import"
    assert replay.verdict({**clean, "differences": changed}, outcomes) == "snapshot after 1-2, 9"
    for verdict in (found, "allow after 3", "snapshot after 9; hits after 1", "stores"):
        assert VERDICT.fullmatch(verdict), verdict
    for verdict in ("", "; hits after 1", "allow at import; ", "hits after 1; allow at import"):
        assert not VERDICT.fullmatch(verdict), verdict
    assert replay.verdict({**clean, "stores_ignored": False}, outcomes) == "stores"
    assert replay.verdict({**clean, "canary_seen": False}, outcomes) == "canary"
    assert replay.verdict({**clean, "walks": 1, "canary_seen": False}, outcomes) == "walks"
    assert replay.verdict({**clean, "outcomes": ["ok"], "walks": 1}, outcomes) == "outcomes"
    assert replay.spans([9]) == "9"


OUTCOMES = {
    "killed by the allow-list": lambda m: "allow " in m.scan,
    "killed by the snapshot": lambda m: "snapshot after" in m.scan,
    "killed by the walk": lambda m: (
        "hits after" in m.scan or m.scan in ("outcomes", "walks", "canary", "stores", "crash")
    ),
    "killed by the source guard": lambda m: m.banned,
    "killed by a test of the guards": lambda m: m.tests != (),
    "equivalents": lambda m: m.survives.startswith("equivalent"),
    "survivors no scan holds": lambda m: (
        bool(m.survives) and not m.survives.startswith("equivalent")
    ),
}
"""D438's lists of the table's outcomes, each by what makes a mutant one of it."""


def outcomes_of_d438() -> dict[str, tuple[int, list[str]]]:
    """D438's ``**The table's outcomes**`` lists: each label, its count and its names."""
    [row] = [
        line
        for line in (SERVER.parent / "SPEC.md").read_text().splitlines()
        if line.startswith("| D438 |")
    ]
    start = row.index("**The table's outcomes**")
    text = row[start + len("**The table's outcomes**") : row.index("**", start + 30)]
    found = {}
    for label, count, names in re.findall(r"([A-Za-z#0-9 -]+): (\d+) \(([^)]*)\)", text):
        found[label.strip()] = (int(count), [name.strip() for name in names.split(",")])
    return found


def test_d438_names_the_table_s_outcomes() -> None:
    """D438 lists, for each outcome, how many mutants of the table have it and which, in the
    table's order: a row added, removed or changed fails here until D438 says so."""
    stated = outcomes_of_d438()
    assert set(stated) == set(OUTCOMES)
    for label, has in OUTCOMES.items():
        names = [mutant.name for mutant in MUTANTS if has(mutant)]
        assert stated[label] == (len(names), names), label


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


def test_the_replay_runs_in_a_ci_job_of_its_own() -> None:
    """``addopts`` deselects the registered ``mutation`` marker, and CI's ``mutation`` job runs the
    replay with ``-m mutation`` in a step that always runs and fails the job, then checks its
    report holds every mutant replayed (``mutation_report.py``)."""
    options = tomllib.loads((SERVER / "pyproject.toml").read_text())["tool"]["pytest"]
    given = options["ini_options"]["addopts"]
    assert "not mutation" in given[given.index("-m") + 1]
    assert any(marker.startswith("mutation:") for marker in options["ini_options"]["markers"])
    ci = (SERVER.parent / ".github" / "workflows" / "ci.yml").read_text()
    [job, *steps] = _steps(ci, "mutation")
    assert not any(re.match(r"    (if|continue-on-error):", line) for line in job)
    command = "uv run pytest tests/packs/onco/test_retention_mutants.py -m mutation"
    [step] = [step for step in steps if any(line.strip().startswith(command) for line in step)]
    assert not any(re.match(r"\s+-?\s*(if|continue-on-error|shell):", line) for line in step)
    assert not any("||" in line for line in step)
    after = steps[steps.index(step) + 1 :]
    report = "python3 ../.github/scripts/mutation_report.py reports/mutation.xml"
    [checked] = [step for step in after if any(report in line for line in step)]
    assert not any(re.match(r"\s+-?\s*(if|continue-on-error|shell):", line) for line in checked)


def _junit(path: Path, names: list[str], bodies: Mapping[str, str] | None = None) -> Path:
    body = "".join(
        f'<testcase name="{name}">{(bodies or {}).get(name, "")}</testcase>' for name in names
    )
    path.write_text(f"<testsuites><testsuite>{body}</testsuite></testsuites>")
    return path


def test_the_job_s_report_check_refuses_an_incomplete_replay(tmp_path: Path) -> None:
    """``mutation_report.py``: a report passes with one passing case for each mutant of the table,
    the control and the coverage test, and fails (non-zero) without the control, without a
    mutant's case, with a case twice, with a case for no mutant of the table, with a case (a
    mutant's or a required one) failed, errored or skipped (pytest exits 0 on a skip), and
    against a table with no mutant."""
    script = _load(SERVER.parent / ".github" / "scripts" / "mutation_report.py", "report")
    path = HERE / "retention_mutants" / "table.py"
    cases = [f"test_the_mutant_has_its_recorded_outcome[{m.name}]" for m in MUTANTS]
    whole = [*cases, *script.REQUIRED]
    assert script.main(str(_junit(tmp_path / "whole.xml", whole)), path) == 0
    broken = {
        "no control": [n for n in whole if n != script.REQUIRED[0]],
        "a mutant missing": whole[1:],
        "a case twice": [*whole, cases[0]],
        "a case twice, another missing": [cases[1], *whole[1:]],
        "a case of no mutant": [*whole, "test_the_mutant_has_its_recorded_outcome[X99]"],
    }
    for name, names in broken.items():
        assert script.main(str(_junit(tmp_path / "broken.xml", names)), path) == 1, name
    for case in (cases[2], *script.REQUIRED):
        for body in ("<failure/>", "<error/>", "<skipped/>"):
            report = _junit(tmp_path / "bad.xml", whole, {case: body})
            assert script.main(str(report), path) == 1, (case, body)
    empty = tmp_path / "table.py"
    empty.write_text("MUTANTS = ()\n")
    required = _junit(tmp_path / "required.xml", list(script.REQUIRED))
    assert script.main(str(required), empty) == 1


CONTROL = table.Mutant(
    "control",
    "the tree unchanged, with every test the table names",
    (),
    "clean",
    tests=tuple(sorted({test for mutant in MUTANTS for test in mutant.tests})),
)
"""The replay of a copy with no edit: what a mutant's replay is read against."""


@pytest.fixture(scope="module")
def replayed(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    """Every mutant of the table, and the control, each replayed against a copy of its own."""
    guards = _load(HERE / "test_retention.py", "retention_guards")
    scan = _load(HERE / "retention_scan.py", "retention_scan_report")
    return replay.replay_all(
        [CONTROL, *MUTANTS],
        guards.banned,
        guards.OUTCOMES,
        scan.report_of,
        tmp_path_factory.mktemp("replay"),
    )


@pytest.mark.mutation
def test_the_replay_of_the_unchanged_tree_kills_nothing(replayed: dict[str, Any]) -> None:
    """The control: a copy with no edit scans clean, passes the source guard and every test the
    table names, so that a mutant's kill is the mutant's, not the copy's."""
    found = replayed[CONTROL.name]
    assert (found.scan, found.banned, found.failed, found.missing) == ("clean", False, (), ())
    assert found.passed == CONTROL.tests


@pytest.mark.mutation
@pytest.mark.parametrize("mutant", MUTANTS, ids=[mutant.name for mutant in MUTANTS])
def test_the_mutant_has_its_recorded_outcome(mutant: Any, replayed: dict[str, Any]) -> None:
    found = replayed[mutant.name]
    assert (found.scan, found.banned) == (mutant.scan, mutant.banned), mutant.route
    assert found.missing == (), f"{mutant.name}: these tests did not run: {found.missing}"
    assert found.passed == (), f"{mutant.name}: these tests did not fail: {found.passed}"
    assert set(found.failed) == set(mutant.tests)


@pytest.mark.mutation
def test_the_replay_covered_the_table(replayed: dict[str, Any]) -> None:
    assert set(replayed) == {CONTROL.name} | {mutant.name for mutant in MUTANTS}

"""The replay of D438's mutation table (``table.py``), against a copy of the tree, never in place.

For each mutant: ``src/aibi``, ``tests/packs/onco`` and ``pyproject.toml`` are copied into a
directory of its own, its edits applied there, and three things read of the copy:

- the source guard (``test_retention.banned``) over every module of the pack it edits;
- the marker scan, in a fresh interpreter (``python -I``) whose path has the copy's ``src`` first
  (the editable install is a path entry, which ``-I`` keeps: without it the child would import
  the installed tree), its report checked to come from a fresh start and from the copy's pack,
  and reduced to a verdict (``verdict``);
- the tests the mutant names, by pytest in a fresh interpreter whose ``aibi`` is the copy's,
  checked before pytest starts; a test that starts a scan of its own gives that child the same
  tree (``test_retention.RUN``).

Mutants run in parallel, one process pool's worth (the replay's children do the work, so a pool of
threads that waits on them is enough); each child's temporary files go in the mutant's directory.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, NamedTuple

SERVER = Path(__file__).resolve().parents[4]
COPIED = ("src/aibi", "tests/packs/onco", "pyproject.toml", "../.github/scripts")
SCAN = "tests/packs/onco/retention_scan.py"
PACK = "src/aibi/packs/onco"
RUN = (
    "import runpy, sys; sys.path.insert(0, sys.argv[1]); sys.argv = sys.argv[2:]; "
    "runpy.run_path(sys.argv[0], run_name='__main__')"
)
"""As ``test_retention.RUN``: a script (``argv[2]``) with a tree's ``src`` (``argv[1]``) first."""
PYTEST = (
    "import pathlib, sys; sys.path.insert(0, sys.argv[1]); import aibi; "
    "assert pathlib.Path(aibi.__file__).is_relative_to(sys.argv[1]), aibi.__file__; "
    "import pytest; raise SystemExit(pytest.main(sys.argv[2:]))"
)
"""pytest with a tree's ``src`` (``argv[1]``) first, checked to be the ``aibi`` it imports."""
VERDICTS = ("clean", "outcomes", "walks", "canary", "crash")


class Replayed(NamedTuple):
    """What the replay of one mutant found: the scan's verdict, whether the source guard refused
    the mutated pack, and which of the mutant's tests failed, which passed and which did not run
    (a test that did not run is not a kill: the replay reports it)."""

    name: str
    scan: str
    banned: bool
    failed: tuple[str, ...]
    passed: tuple[str, ...]
    missing: tuple[str, ...]


def spans(numbers: Iterable[int]) -> str:
    """``1-4, 7-9`` for 1, 2, 3, 4, 7, 8, 9."""
    found: list[list[int]] = []
    for number in sorted(set(numbers)):
        if found and found[-1][1] == number - 1:
            found[-1][1] = number
        else:
            found.append([number, number])
    return ", ".join(str(a) if a == b else f"{a}-{b}" for a, b in found)


def verdict(report: Mapping[str, Any], outcomes: Sequence[str]) -> str:
    """The scan's report reduced to what the table records: how it fails, or the calls after
    which a walk found a marker, or ``clean``."""
    if report["outcomes"] != list(outcomes):
        return "outcomes"
    if report["walks"] != len(outcomes):
        return "walks"
    if report["canary_seen"] is not True:
        return "canary"
    after = [hit["after"] for hit in report["hits"]]
    return f"hits after {spans(after)}" if after else "clean"


def copy(into: Path) -> Path:
    """A copy of the tree's parts the replay reads, in ``into``; its ``server`` directory."""
    server = into / "server"
    for part in COPIED:
        source, target = (SERVER / part).resolve(), (server / part).resolve()
        if source.is_dir():
            shutil.copytree(source, target, ignore=shutil.ignore_patterns("__pycache__"))
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    return server


def edited(texts: Mapping[str, str], edits: Iterable[Any]) -> tuple[dict[str, str], list[str]]:
    """The texts (by path) with the edits applied in their sequence, each to the text the ones
    before it left, and what is wrong with each edit that cannot be: an anchor not found exactly
    once at its turn, or a replacement that is its anchor (an edit that changes nothing)."""
    out = dict(texts)
    problems: list[str] = []
    for number, edit in enumerate(edits, 1):
        found = out[edit.path].count(edit.anchor)
        if found != 1:
            problems.append(f"edit {number}'s anchor is found {found} times in {edit.path}")
        elif edit.anchor == edit.replacement:
            problems.append(f"edit {number} of {edit.path} replaces its anchor by itself")
        else:
            out[edit.path] = out[edit.path].replace(edit.anchor, edit.replacement)
    return out, problems


def apply(server: Path, edits: Sequence[Any]) -> list[Path]:
    """The edits, applied to the copy as ``edited`` applies them; the files changed."""
    paths = sorted({edit.path for edit in edits})
    texts = {path: (server / path).read_text(encoding="utf-8") for path in paths}
    out, problems = edited(texts, edits)
    assert problems == [], problems
    for path in paths:
        (server / path).write_text(out[path], encoding="utf-8")
    return [server / path for path in paths]


def _environment(place: Path) -> dict[str, str]:
    return {**os.environ, "TMPDIR": str(place), "PYTHONDONTWRITEBYTECODE": "1"}


def scanned(
    server: Path, place: Path, outcomes: Sequence[str], report_of: Callable[..., Any]
) -> str:
    """The scan of the copy, as a verdict."""
    ran = subprocess.run(
        [sys.executable, "-I", "-c", RUN, str(server / "src"), str(server / SCAN)],
        capture_output=True,
        text=True,
        timeout=900,
        check=False,
        cwd=server,
        env=_environment(place),
    )
    if ran.returncode != 0:
        return "crash"
    report = report_of(ran.stdout)
    assert report["fresh_start"] is True, "the scan's child had loaded the pack"
    assert Path(report["pack"]) == server / PACK, f"the scan read {report['pack']}, not the copy"
    return verdict(report, outcomes)


MODULE = "test_retention.py"
"""The module of a test the table names by its name alone; another is named ``<module>::<name>``."""


def tested(server: Path, place: Path, tests: Sequence[str]) -> tuple[set[str], set[str], set[str]]:
    """The named tests run in the copy: those that failed, those that passed, and those that did
    not run."""
    if not tests:
        return set(), set(), set()
    junit = place / "tests.xml"
    here = server / "tests" / "packs" / "onco"
    nodes = [f"{here / (test if '::' in test else f'{MODULE}::{test}')}" for test in tests]
    options = ["-q", "-p", "no:cacheprovider", "-c", str(server / "pyproject.toml")]
    options += ["--rootdir", str(server), "-o", "junit_family=xunit1", "--junitxml", str(junit)]
    subprocess.run(
        [sys.executable, "-I", "-c", PYTEST, str(server / "src"), *options, *nodes],
        capture_output=True,
        text=True,
        timeout=900,
        check=False,
        cwd=server,
        env=_environment(place),
    )
    return outcomes(junit if junit.exists() else None, tests)


def outcomes(junit: Path | None, tests: Sequence[str]) -> tuple[set[str], set[str], set[str]]:
    """Of ``tests``, by a junit report: those that failed (every case of a parametrised test,
    named ``<test>[<id>]`` there, failed or errored), those that passed (a case of it passed), and
    those that did not run (no case, or every case skipped: a skip is no kill). A test named
    ``<module>::<name>`` is read by its name."""
    found: dict[str, list[str]] = {}
    for case in ET.parse(junit).iter("testcase") if junit is not None else ():
        status = "passed"
        if case.findall("skipped"):
            status = "skipped"
        elif case.findall("failure") or case.findall("error"):
            status = "failed"
        found.setdefault((case.get("name") or "").split("[")[0], []).append(status)
    failed: set[str] = set()
    passed: set[str] = set()
    missing: set[str] = set()
    for test in tests:
        ran = [status for status in found.get(test.split("::")[-1], []) if status != "skipped"]
        if not ran:
            missing.add(test)
        elif all(status == "failed" for status in ran):
            failed.add(test)
        else:
            passed.add(test)
    return failed, passed, missing


def replay(
    mutant: Any,
    banned: Callable[[str], list[str]],
    outcomes: Sequence[str],
    report_of: Callable[..., Any],
    base: Path,
) -> Replayed:
    """One mutant, in a directory of its own under ``base``, removed afterwards."""
    place = Path(tempfile.mkdtemp(prefix=f"{mutant.name}-", dir=base))
    try:
        server = copy(place)
        changed = apply(server, mutant.edits)
        pack = server / PACK
        refused = any(
            banned(path.read_text(encoding="utf-8"))
            for path in changed
            if path.is_relative_to(pack)
        )
        scan = scanned(server, place, outcomes, report_of)
        failed, passed, missing = (tuple(sorted(f)) for f in tested(server, place, mutant.tests))
        return Replayed(mutant.name, scan, refused, failed, passed, missing)
    finally:
        shutil.rmtree(place, ignore_errors=True)


def replay_all(
    mutants: Sequence[Any],
    banned: Callable[[str], list[str]],
    outcomes: Sequence[str],
    report_of: Callable[..., Any],
    base: Path,
    workers: int | None = None,
) -> dict[str, Replayed]:
    """Every mutant, ``workers`` at a time (one a processor by default)."""
    with ThreadPoolExecutor(max_workers=workers or os.cpu_count() or 2) as pool:
        done = pool.map(lambda m: replay(m, banned, outcomes, report_of, base), mutants)
        return {replayed.name: replayed for replayed in done}

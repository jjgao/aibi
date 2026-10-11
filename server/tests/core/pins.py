"""The tests D422 cites as pins, and the guard that keeps them pins (SPEC D422).

A pin is a test D422 names by ``path::function`` (``pin_table.PINS``, with the expressions each
must assert). It is a pin only while it runs and asserts what D422 says, and the guard does not
read that off the test's syntax, which any placement of an ``assert`` (after a ``return``, in an
``except`` that never runs, after the raise inside ``pytest.raises``, in a loop over nothing) can
fool: it watches the run. ``Guard``, which ``tests/core/conftest.py`` registers:

- *traces* each cited test's own code while it runs (``sys.monitoring``, ``LINE`` events on that
  one code object) and fails it unless an ``assert`` statement that holds each expression (as a
  syntax tree, as a conjunct: not a part of a disjunction, a string, a negation) ran. A pin that
  passes asserted what D422 says, and an assert in a function it calls is not its own;
- reports a cited test that was skipped or is expected to fail, however it came to be (a
  decorator, a condition held in a variable, a skip in a fixture or a helper), as failed;
- fails the session when a cited test that was collected, or that a path given to ``pytest`` asks
  for, did not run: a conftest hook or a marker (``addopts``' ``-m``) deselected it, or the
  module was ignored. A run narrowed by hand (``-k``, ``-m``, ``--deselect``, ``--ignore``,
  ``--lf``, ``--sw``, ``--collect-only``, or one that stopped at a first failure) is excused: those
  are the person's choice, and nothing in the repository makes it.

The syntax is read only to find which lines an expression is asserted on, and by
``reasons_not_a_pin`` for a cited test that does not assert an expression at all.
"""

import ast
import os
import re
import shlex
import sys
from collections.abc import Generator, Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from types import CodeType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[3]
"""Where the sources are read from (a test of this module points it elsewhere)."""
SPEC = ROOT / "SPEC.md"
TOOLS = (3, 4)
"""The ``sys.monitoring`` tool ids the guard may take: none of the debugger's (0), coverage's (1),
the profiler's (2) or the optimizer's (5)."""
LONG = (
    "--deselect",
    "--ignore",
    "--ignore-glob",
    "--lf",
    "--last-failed",
    "--sw",
    "--stepwise",
    "--sw-skip",
    "--stepwise-skip",
    "--co",
    "--collect-only",
    "--collectonly",
)
"""The options a person gives to narrow a run (``-k`` and ``-m`` are short ones: ``narrowed``)."""


def d422() -> str:
    [row] = [
        line
        for line in SPEC.read_text(encoding="utf-8").splitlines()
        if line.startswith("| D422 |")
    ]
    return row


def cited() -> set[str]:
    """Every ``path::function`` D422 names."""
    return set(re.findall(r"`(tests/[\w/]+\.py::\w+)`", d422()))


def function_node(path: str, name: str) -> ast.FunctionDef:
    source = (ROOT / "server" / path).read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{path} has no function {name}")


def function_at(code: CodeType) -> ast.FunctionDef | None:
    """The definition of the function whose code object this is, read from its file."""
    try:
        tree = ast.parse(Path(code.co_filename).read_text(encoding="utf-8"))
    except (OSError, SyntaxError, ValueError):
        return None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == code.co_name:
            first = min([node.lineno, *(each.lineno for each in node.decorator_list)])
            if first == code.co_firstlineno:
                return node
    return None


def conjuncts(test: ast.expr) -> list[ast.expr]:
    if isinstance(test, ast.BoolOp) and isinstance(test.op, ast.And):
        return [part for value in test.values for part in conjuncts(value)]
    return [test]


def needle(source: str) -> str:
    return ast.dump(ast.parse(source, mode="eval").body)


def asserting(node: ast.FunctionDef, expression: str) -> list[ast.Assert]:
    """The ``assert`` statements anywhere in the function whose test is the expression or has it
    as an ``and`` conjunct (a nested function's too: they are found, and never run as the
    function's own, so ``unrun`` fails them)."""
    wanted = needle(expression)
    return [
        each
        for each in ast.walk(node)
        if isinstance(each, ast.Assert)
        and wanted in [ast.dump(part) for part in conjuncts(each.test)]
    ]


def lines_of(found: list[ast.Assert]) -> set[int]:
    return {line for each in found for line in range(each.lineno, (each.end_lineno or 0) + 1)}


def reasons_not_a_pin(path: str, name: str, needles: tuple[str, ...]) -> list[str]:
    """The expressions the cited function does not assert anywhere (a syntactic check, which a test
    can pass without running: ``unrun`` is the run's)."""
    node = function_node(path, name)
    return [f"asserts nothing of {one}" for one in needles if not asserting(node, one)]


def unrun(code: CodeType, needles: tuple[str, ...], seen: set[int]) -> list[str]:
    """Why the function with this code object, which ran and passed with ``seen`` its lines, did
    not assert each expression: no assert of it, or none of its asserts ran."""
    node = function_at(code)
    if node is None:
        return [f"its source cannot be found from {code.co_filename}"]
    found: list[str] = []
    for one in needles:
        asserts = asserting(node, one)
        if not asserts:
            found.append(f"asserts nothing of {one}")
        elif not lines_of(asserts) & seen:
            found.append(f"no assert of {one} ran")
    return found


@contextmanager
def lines_run(code: CodeType) -> Iterator[set[int]]:
    """The lines of this code object that run in the block (``LINE`` events of it alone: the
    interpreter keeps no other code's cost). Raises when no tool id is free."""
    monitoring = sys.monitoring
    tool = next((one for one in TOOLS if monitoring.get_tool(one) is None), None)
    if tool is None:
        raise RuntimeError(f"no sys.monitoring tool id of {TOOLS} is free to trace a pin with")
    seen: set[int] = set()

    def on_line(_code: CodeType, line: int) -> None:
        seen.add(line)

    monitoring.use_tool_id(tool, "aibi-pins")
    try:
        monitoring.register_callback(tool, monitoring.events.LINE, on_line)
        monitoring.set_local_events(tool, code, monitoring.events.LINE)
        yield seen
    finally:
        monitoring.set_local_events(tool, code, 0)
        monitoring.register_callback(tool, monitoring.events.LINE, None)
        monitoring.free_tool_id(tool)


def skipped_pin(report: Any, nodeid: str, pins: Any) -> bool:
    """Whether a report is of a cited test that was skipped or is expected to fail."""
    base = nodeid.split("[", 1)[0]
    return base in pins and (bool(report.skipped) or hasattr(report, "wasxfail"))


def narrowed(config: pytest.Config) -> bool:
    """Whether the person who ran ``pytest`` narrowed the run (``-k``, ``-m``, ``--deselect``,
    ``--ignore``, ``--lf``, ``--sw``, ``--collect-only``; on the command line or in
    ``PYTEST_ADDOPTS``, never in the configuration file, which is the repository's)."""
    tokens = [*config.invocation_params.args, *shlex.split(os.environ.get("PYTEST_ADDOPTS", ""))]
    for token in map(str, tokens):
        if token.split("=", 1)[0] in LONG:
            return True
        if token.startswith("-") and not token.startswith("--") and set(token[1:]) & set("km"):
            return True
    return False


def requested(config: pytest.Config, pins: Mapping[str, Any]) -> set[str]:
    """The pins a path given to ``pytest`` asks for: a directory or file that holds the pin's file,
    or a node id of the pin itself."""
    found: set[str] = set()
    for arg in config.args:
        path, *rest = str(arg).split("::")
        target = (config.invocation_params.dir / path).resolve()
        for pin in pins:
            file, name = pin.split("::")
            asked = not rest or rest[0].split("[", 1)[0] == name
            if asked and (config.rootpath / file).resolve().is_relative_to(target):
                found.add(pin)
    return found


class Guard:
    """The guard of the cited tests (see the module's docstring), one per session. ``table`` is
    the pins with their expressions, ``pin_table.PINS`` unless a test of the guard gives its own."""

    def __init__(self, table: Mapping[str, tuple[str, ...]] | None = None) -> None:
        self._given = table
        self.collected: set[str] = set()
        self.asked: set[str] = set()
        self.ran: set[str] = set()
        self.missing: list[str] = []

    @property
    def table(self) -> Mapping[str, tuple[str, ...]]:
        if self._given is None:
            from tests.core.pin_table import PINS

            self._given = PINS
        return self._given

    @pytest.hookimpl(tryfirst=True)
    def pytest_collection_modifyitems(
        self, session: pytest.Session, config: pytest.Config, items: list[pytest.Item]
    ) -> None:
        """Before any hook or option deselects anything: the cited tests collected."""
        self.collected = {each.nodeid.split("[", 1)[0] for each in items} & set(self.table)
        self.asked = requested(config, self.table)

    @pytest.hookimpl(wrapper=True)
    def pytest_runtest_call(self, item: pytest.Item) -> Generator[None, Any, Any]:
        """Trace a cited test's own code and fail it unless each of its expressions was asserted
        by an ``assert`` that ran."""
        wanted = self.table.get(item.nodeid.split("[", 1)[0])
        if wanted is None:
            return (yield)
        code = getattr(getattr(item, "obj", None), "__code__", None)
        if code is None:
            raise AssertionError(
                f"{item.nodeid} is cited as a pin by D422 and has no code to trace"
            )
        with lines_run(code) as seen:
            result = yield
        reasons = unrun(code, wanted, seen)
        if reasons:
            raise AssertionError(
                f"{item.nodeid} is cited as a pin by D422 and does not assert what it says: "
                + "; ".join(reasons)
            )
        return result

    @pytest.hookimpl(wrapper=True)
    def pytest_runtest_makereport(
        self, item: pytest.Item, call: pytest.CallInfo[None]
    ) -> Generator[None, Any, Any]:
        """Report a cited test that was skipped, or expected to fail, as failed."""
        report = yield
        if skipped_pin(report, item.nodeid, self.table):
            report.outcome = "failed"
            report.longrepr = f"{item.nodeid} is cited as a pin by D422 and was skipped or xfailed"
        return report

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        self.ran.add(report.nodeid.split("[", 1)[0])

    def pytest_sessionfinish(self, session: pytest.Session) -> None:
        """Fail the session when a cited test that was asked for or collected did not run, unless
        a person narrowed the run or it stopped at a failure."""
        config = session.config
        if narrowed(config) or session.shouldstop or session.shouldfail:
            return
        self.missing = sorted((self.collected | self.asked) - self.ran)
        if self.missing:
            session.exitstatus = pytest.ExitCode.TESTS_FAILED

    def pytest_terminal_summary(self, terminalreporter: pytest.TerminalReporter) -> None:
        if self.missing:
            terminalreporter.section("a cited pin did not run (SPEC D422)", sep="=", red=True)
            for pin in self.missing:
                where = "deselected" if pin in self.collected else "not collected"
                terminalreporter.line(f"{pin}: {where}, though D422 cites it")

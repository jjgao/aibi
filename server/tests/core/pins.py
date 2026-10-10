"""The tests D422 cites as pins, and the guard that keeps them pins (SPEC D422).

A pin is a test D422 names by ``path::function``. It is a pin only while it runs and asserts what
D422 says, so three things are checked, none by trusting the test:

- *statically* (``reasons_not_a_pin``): the function is not decorated but by ``parametrize``, its
  module sets no ``pytestmark``, nothing in it calls ``pytest.skip``, ``importorskip`` or
  ``xfail``, and each needle (an expression, compared as a syntax tree) is the test of an
  ``assert`` that is a statement of the function itself or of a ``for``, ``while``, ``with`` or
  ``try`` in it: not under an ``if`` (``if False:``), not in a nested function that may never be
  called, and not a part of a disjunction;
- *at run time* (``pytest_runtest_makereport``, which ``tests/core/conftest.py`` takes up): a
  cited test that is skipped or expected to fail, however it came to be (a condition held in a
  variable, a skip in a fixture or a helper), is reported failed.
"""

import ast
import re
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[3]
"""Where the sources are read from (a test of this module points it elsewhere)."""
SPEC = ROOT / "SPEC.md"
_CITED: list[set[str]] = []
PINNED: set[str] | None = None
"""An override of the cited pins, for the guard's own test."""
SAFE_DECORATORS = ("pytest.mark.parametrize(",)
NESTED = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)
SKIPS = ("skip", "importorskip", "xfail")


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


def pinned() -> set[str]:
    """The pins the guard watches: those D422 cites, read once, or the override."""
    if PINNED is not None:
        return PINNED
    if not _CITED:
        _CITED.append(cited())
    return _CITED[0]


def function_node(path: str, name: str) -> ast.FunctionDef:
    source = (ROOT / "server" / path).read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{path} has no function {name}")


def statements(body: list[ast.stmt]) -> list[ast.stmt]:
    """The statements of a body and of the ``for``, ``while``, ``with`` and ``try`` blocks in it,
    never of an ``if``, a nested function, a class or a ``match``."""
    found: list[ast.stmt] = []
    for each in body:
        found.append(each)
        if isinstance(each, (ast.For, ast.AsyncFor, ast.While)):
            found += statements(each.body) + statements(each.orelse)
        elif isinstance(each, (ast.With, ast.AsyncWith)):
            found += statements(each.body)
        elif isinstance(each, ast.Try):
            found += statements(each.body + each.orelse + each.finalbody)
            for handler in each.handlers:
                found += statements(handler.body)
    return found


def conjuncts(test: ast.expr) -> list[ast.expr]:
    if isinstance(test, ast.BoolOp) and isinstance(test.op, ast.And):
        return [part for value in test.values for part in conjuncts(value)]
    return [test]


def asserted(node: ast.FunctionDef) -> list[str]:
    """The dumps of the test of every assert that runs whenever the function does, and of its
    ``and`` conjuncts."""
    found: list[str] = []
    for each in statements(node.body):
        if isinstance(each, ast.Assert):
            found += [ast.dump(part) for part in conjuncts(each.test)]
    return found


def needle(source: str) -> str:
    return ast.dump(ast.parse(source, mode="eval").body)


def skips(node: ast.FunctionDef) -> list[str]:
    """Every call in the function (nested ones too) of ``skip``, ``importorskip`` or ``xfail``."""
    return [
        ast.unparse(each.func)
        for each in ast.walk(node)
        if isinstance(each, ast.Call) and ast.unparse(each.func).split(".")[-1] in SKIPS
    ]


def marks(path: str, node: ast.FunctionDef) -> list[str]:
    """The decorators of the function that are not ``parametrize``, and the module's
    ``pytestmark`` assignments: any of them may skip or expect failure through a name."""
    found = [
        ast.unparse(decorator)
        for decorator in node.decorator_list
        if not ast.unparse(decorator).startswith(SAFE_DECORATORS)
    ]
    module = ast.parse((ROOT / "server" / path).read_text(encoding="utf-8"))
    return found + [
        ast.unparse(each)
        for each in module.body
        if isinstance(each, (ast.Assign, ast.AnnAssign)) and "pytestmark" in ast.unparse(each)
    ]


def reasons_not_a_pin(path: str, name: str, needles: tuple[str, ...]) -> list[str]:
    node = function_node(path, name)
    found = [f"decorated or marked: {one}" for one in marks(path, node)]
    found += [f"calls {one}" for one in skips(node)]
    asserts = asserted(node)
    found += [f"asserts nothing of {one}" for one in needles if needle(one) not in asserts]
    if not asserts:
        found.append("asserts nothing")
    return found


def skipped_pin(report: Any, nodeid: str, pins: set[str]) -> bool:
    """Whether a report is of a cited test that was skipped or is expected to fail."""
    base = nodeid.split("[", 1)[0]
    return base in pins and (bool(report.skipped) or hasattr(report, "wasxfail"))


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(
    item: pytest.Item, call: pytest.CallInfo[None]
) -> Generator[None, Any, Any]:
    """Report a cited test that was skipped, or expected to fail, as failed."""
    report = yield
    if skipped_pin(report, item.nodeid, pinned()):
        report.outcome = "failed"
        report.longrepr = f"{item.nodeid} is cited as a pin by D422 and was skipped or xfailed"
    return report

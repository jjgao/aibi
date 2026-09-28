"""Whether the determinism job ran every test of ``tests/core/determinism`` (D372): its junit
report, from ``server/``, holds one passing test per test function there, none skipped, failed or
in error, so that no step's edit (a shell that swallows the exit, a selection, a skip) can leave
the job green without them."""

import ast
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

TESTS = Path("tests/core/determinism")


def expected() -> int:
    """The test functions of the directory's modules, counted by name as pytest collects them."""
    found = 0
    for module in sorted(TESTS.rglob("*.py")):
        if module.name in ("conftest.py", "__init__.py"):
            continue
        tree = ast.parse(module.read_text())
        found += sum(
            isinstance(node, ast.FunctionDef) and node.name.startswith("test_")
            for node in tree.body
        )
    return found


def main(report: str) -> int:
    cases = list(ET.parse(report).iter("testcase"))
    passed = [
        case
        for case in cases
        if not any(case.findall(kind) for kind in ("failure", "error", "skipped"))
    ]
    want = expected()
    if want == 0 or len(cases) != want or len(passed) != want:
        print(
            f"::error title=Determinism tests incomplete::{len(passed)} of {len(cases)} tests "
            f"passed in {report}; {want} test functions are in {TESTS}"
        )
        return 1
    print(f"{want} determinism tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))

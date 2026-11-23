"""Whether the domain job ran every test of ``tests/core/domain`` (D449): its junit report, from
``server/``, holds at least one passing case of every test function there (they are
parametrised) and no case skipped, failed or in error, so that no step's edit (a shell that
swallows the exit, a selection, a skip) can leave the job green without them, and a run that
selects no test fails."""

import ast
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

TESTS = Path("tests/core/domain")


def expected() -> set[str]:
    """The test functions of the directory's modules, by name as pytest collects them."""
    found: set[str] = set()
    for module in sorted(TESTS.rglob("*.py")):
        if module.name in ("conftest.py", "__init__.py"):
            continue
        tree = ast.parse(module.read_text())
        found |= {
            node.name
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")
        }
    return found


def main(report: str) -> int:
    cases = list(ET.parse(report).iter("testcase"))
    passed = [
        case
        for case in cases
        if not any(case.findall(kind) for kind in ("failure", "error", "skipped"))
    ]
    want = expected()
    ran = {case.get("name", "").split("[", 1)[0] for case in passed}
    if not want or len(passed) != len(cases) or not want <= ran:
        missing = ", ".join(sorted(want - ran)) or "none"
        print(
            f"::error title=Domain tests incomplete::{len(passed)} of {len(cases)} cases passed "
            f"in {report}; {len(want)} test functions are in {TESTS}; not run: {missing}"
        )
        return 1
    print(f"{len(passed)} cases of {len(want)} domain tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))

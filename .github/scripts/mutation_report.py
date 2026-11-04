"""Whether the mutation job replayed every mutant of D438's table: its junit report, from
``server/``, holds exactly one passing case of ``test_the_mutant_has_its_recorded_outcome`` for each
``Mutant`` of ``tests/packs/onco/retention_mutants/table.py`` (by its name, the case's id), none
for anything else and none twice, a passing control
(``test_the_replay_of_the_unchanged_tree_kills_nothing``, which makes each kill the mutant's)
and a passing ``test_the_replay_covered_the_table``, and no case failed, errored or was skipped,
so that no step's edit (a shell that swallows the exit, a selection, a skip) can leave the job
green without them."""

import ast
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

TABLE = Path("tests/packs/onco/retention_mutants/table.py")
CASE = "test_the_mutant_has_its_recorded_outcome"
REQUIRED = (
    "test_the_replay_of_the_unchanged_tree_kills_nothing",
    "test_the_replay_covered_the_table",
)


def expected(table: Path = TABLE) -> list[str]:
    """The names of the ``Mutant(...)`` calls in the table's ``MUTANTS`` tuple, read from its
    source (each call's first argument)."""
    for node in ast.parse(table.read_text()).body:
        if isinstance(node, ast.AnnAssign | ast.Assign):
            targets = [node.target] if isinstance(node, ast.AnnAssign) else node.targets
            if [ast.unparse(t) for t in targets] == ["MUTANTS"] and isinstance(
                node.value, ast.Tuple
            ):
                calls = [call for call in node.value.elts if isinstance(call, ast.Call)]
                return [ast.literal_eval(call.args[0]) for call in calls]
    return []


def problems(report: str, table: Path = TABLE) -> list[str]:
    """What is wrong with the report, against the table."""
    cases = list(ET.parse(report).iter("testcase"))
    names = Counter(case.get("name") or "" for case in cases)
    found = [
        f"{case.get('name')} did not pass"
        for case in cases
        if any(case.findall(kind) for kind in ("failure", "error", "skipped"))
    ]
    found += [f"{name} ran {count} times" for name, count in names.items() if count > 1]
    found += [f"{name} is missing" for name in REQUIRED if name not in names]
    replayed = {name[len(CASE) + 1 : -1] for name in names if name.startswith(f"{CASE}[")}
    want = set(expected(table))
    if not want:
        found.append(f"no mutant is read from {table}")
    found += [f"{name} was not replayed" for name in sorted(want - replayed)]
    found += [f"{name} is not in the table" for name in sorted(replayed - want)]
    return found


def main(report: str, table: Path = TABLE) -> int:
    found = problems(report, table)
    if found:
        print(f"::error title=Mutation table incomplete::{'; '.join(found)} in {report}")
        return 1
    print(f"{len(expected(table))} mutants replayed, each with its recorded outcome")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))

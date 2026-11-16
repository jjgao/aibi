"""Whether a leg of the mutation job replayed its share of D438's table: its junit report, from
``server/``, holds exactly one passing case of ``test_the_mutant_has_its_recorded_outcome`` for each
``Mutant`` of ``tests/packs/onco/retention_mutants/table.py`` dealt to the leg (by its name, the
case's id), none for anything else and none twice, a passing control
(``test_the_replay_of_the_unchanged_tree_kills_nothing``, which makes each kill the mutant's)
and a passing ``test_the_replay_covered_the_table``, and no case failed, errored or was skipped,
so that no step's edit (a shell that swallows the exit, a selection, a skip) can leave the job
green without them. ``--shard i/n`` names the leg (``1/1``, the whole table, when it is not
given): ``dealt`` gives each of ``n`` legs its share, which the test module replays by
``AIBI_MUTANT_SHARD`` and this script reads from the table's source the same way, so that the
``n`` legs together replay every mutant once."""

import ast
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from collections.abc import Sequence
from pathlib import Path

TABLE = Path("tests/packs/onco/retention_mutants/table.py")
CASE = "test_the_mutant_has_its_recorded_outcome"
REQUIRED = (
    "test_the_replay_of_the_unchanged_tree_kills_nothing",
    "test_the_replay_covered_the_table",
)
WHOLE = (1, 1)
"""The one leg that replays the whole table."""


def shard(given: str) -> tuple[int, int]:
    """``i/n``: leg ``i`` of ``n`` (``1 <= i <= n``, in ASCII digits), or ``ValueError``."""
    leg, slash, legs = given.partition("/")
    if not (slash and leg.isascii() and leg.isdigit() and legs.isascii() and legs.isdigit()):
        raise ValueError(f"a shard is i/n, not {given!r}")
    if not 1 <= int(leg) <= int(legs):
        raise ValueError(f"a shard is i/n with 1 <= i <= n, not {given!r}")
    return int(leg), int(legs)


def dealt(mutants: Sequence[tuple[str, int]], given: tuple[int, int]) -> list[str]:
    """The names of leg ``i`` of ``n``'s share of ``mutants`` (each a name and how many tests it
    names): ordered by those tests, most first (a mutant's tests run after its scan, so they are
    its cost beyond the scan), then by name, and dealt to the legs in turn, so that every mutant
    is in exactly one leg's share and the shares' costs are close."""
    leg, legs = given
    ordered = sorted(mutants, key=lambda mutant: (-mutant[1], mutant[0]))
    return [name for name, _ in ordered[leg - 1 :: legs]]


def table_mutants(table: Path = TABLE) -> list[tuple[str, int]]:
    """Each ``Mutant(...)`` call of the table's ``MUTANTS`` tuple, read from its source: its first
    argument (the name) and how many tests its ``tests`` keyword names (a literal tuple: anything
    else raises, so that this script never deals a table other than the test module's)."""
    for node in ast.parse(table.read_text()).body:
        if isinstance(node, ast.AnnAssign | ast.Assign):
            targets = [node.target] if isinstance(node, ast.AnnAssign) else node.targets
            if [ast.unparse(t) for t in targets] == ["MUTANTS"] and isinstance(
                node.value, ast.Tuple
            ):
                calls = [call for call in node.value.elts if isinstance(call, ast.Call)]
                return [
                    (
                        ast.literal_eval(call.args[0]),
                        sum(
                            len(ast.literal_eval(keyword.value))
                            for keyword in call.keywords
                            if keyword.arg == "tests"
                        ),
                    )
                    for call in calls
                ]
    return []


def expected(table: Path = TABLE, given: tuple[int, int] = WHOLE) -> list[str]:
    """The names of the mutants dealt to leg ``given`` (every mutant, for the whole table)."""
    return dealt(table_mutants(table), given)


def problems(report: str, table: Path = TABLE, given: tuple[int, int] = WHOLE) -> list[str]:
    """What is wrong with the report, against the leg's share of the table."""
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
    want = set(expected(table, given))
    if not want:
        found.append(f"no mutant is read from {table}")
    every = set(expected(table))
    found += [f"{name} was not replayed" for name in sorted(want - replayed)]
    found += [
        f"{name} is another leg's, not shard {given[0]}/{given[1]}'s"
        for name in sorted((replayed - want) & every)
    ]
    found += [f"{name} is not in the table" for name in sorted(replayed - every)]
    return found


def main(report: str, table: Path = TABLE, given: tuple[int, int] = WHOLE) -> int:
    found = problems(report, table, given)
    if found:
        print(f"::error title=Mutation table incomplete::{'; '.join(found)} in {report}")
        return 1
    print(
        f"{len(expected(table, given))} of the table's {len(expected(table))} mutants replayed "
        f"(shard {given[0]}/{given[1]}), each with its recorded outcome"
    )
    return 0


def arguments(argv: Sequence[str]) -> tuple[str, tuple[int, int]]:
    """``<report> [--shard i/n]``, or ``ValueError``."""
    match list(argv):
        case [report]:
            return report, WHOLE
        case [report, "--shard", given]:
            return report, shard(given)
    raise ValueError(f"usage: mutation_report.py <report> [--shard i/n], not {list(argv)!r}")


if __name__ == "__main__":
    try:
        named, leg = arguments(sys.argv[1:])
    except ValueError as refused:
        print(f"::error title=Mutation report not read::{refused}")
        sys.exit(1)
    sys.exit(main(named, given=leg))

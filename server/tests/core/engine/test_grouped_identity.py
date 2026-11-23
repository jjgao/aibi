"""Main's SQL for grouped coverage, byte for byte (SPEC D449): the digests of the statements the
compiler gave before the one-group path existed, recorded on that tree, for a value leaf, an
``every`` question, ``covered``, a membership, a materialisation, a listing of inputs, a crossing,
a members query and two coverages over one assignment and one groups table. With sizes outside
the bound (``grouped.ROW_BUDGET``), with sizes unknown, and with main's path forced, the compiler
emits exactly those statements; the inputs path emits them whatever the sizes (D342).

The digests depend on SQLGlot's rendering, which ``pyproject.toml`` pins: a bump of SQLGlot, or a
deliberate change to main's SQL, writes them again (this module's ``record``) in a
change that says why. The statements hold no blob path (each is a bound parameter), so the
digests do not depend on where the blobs are written."""

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import replace
from importlib.metadata import version
from pathlib import Path
from typing import Any

import pytest

from aibi.core.engine import build, grouped, sql
from aibi.core.engine.data import Release
from aibi.core.engine.resolve import Resolution
from aibi.core.store.tables import TableSource

Sources = Mapping[str, TableSource]


def release() -> Release:
    """Sites have visits and notes; both are listed by one assignment of sites to groups, through
    two group columns, and one groups table (D447's shape); a site may sit in two groups."""
    column, table = build.column, build.table
    groups = {"table": "groups", "group_column": "grp", "scope_columns": {"s": "s"}}
    descriptors = [
        build.dataset(disclosure={"allow_row_ids": True}),
        table("sites", ["site"]),
        column("sites.site", "string"),
        table("visits", ["visit"], role="event"),
        column("visits.visit", "string"),
        column("visits.site", "string"),
        column("visits.s", "category"),
        column("visits.flag", "category"),
        build.relationship("visits", ["site"], "sites", role="place"),
        build.coverage(
            "rel:visits.place",
            {
                "assignment": {
                    "table": "assign",
                    "parent_columns": {"site": "site"},
                    "group_column": "grp",
                },
                "groups": {**groups, "covers_all_column": "all"},
            },
        ),
        table("notes", ["note"], role="event"),
        column("notes.note", "string"),
        column("notes.site", "string"),
        column("notes.s", "category"),
        build.relationship("notes", ["site"], "sites", role="place"),
        build.coverage(
            "rel:notes.place",
            {
                "assignment": {
                    "table": "assign",
                    "parent_columns": {"site": "site"},
                    "group_column": "grp2",
                },
                "groups": groups,
            },
        ),
        table("assign", None, role="coverage"),
        column("assign.site", "string"),
        column("assign.grp", "string"),
        column("assign.grp2", "string"),
        table("groups", None, role="coverage"),
        column("groups.grp", "string"),
        column("groups.s", "category"),
        column("groups.all", "boolean"),
    ]
    rows: dict[str, list[dict[str, object]]] = {
        "sites": [{"site": f"t{n}"} for n in range(4)],
        "visits": [
            {"visit": "v0", "site": "t0", "s": "a", "flag": "x"},
            {"visit": "v1", "site": "t1", "s": "b", "flag": "y"},
            {"visit": "v2", "site": "t3", "s": "a", "flag": "y"},
        ],
        "notes": [{"note": "n0", "site": "t1", "s": "b"}],
        "assign": [
            {"site": "t0", "grp": "g0", "grp2": "g1"},
            {"site": "t1", "grp": "g0", "grp2": "g1"},
            {"site": "t1", "grp": "g1", "grp2": "g0"},
            {"site": "t2", "grp": "g2", "grp2": "g2"},
        ],
        "groups": [
            {"grp": "g0", "s": "a", "all": False},
            {"grp": "g1", "s": "b", "all": False},
            {"grp": "g2", "s": "a", "all": True},
        ],
    }
    return build.release(descriptors, rows)


def _value(column: str, *values: str) -> dict[str, Any]:
    return {"kind": "value", "column": column, "values": list(values)}


COHORTS: dict[str, list[Any]] = {
    "value": [{"kind": "exists", "table": "visits", "where": [_value("visits.s", "a")]}],
    "every": [{"kind": "exists", "table": "visits", "quantifier": "every"}],
    "covered": [{"kind": "covered", "table": "visits", "scope": {"s": ["a"]}}],
    "two": [
        {"kind": "exists", "table": "visits", "where": [_value("visits.s", "b")]},
        {"not": {"kind": "exists", "table": "notes", "where": [_value("notes.s", "b")]}},
    ],
    "flagged": [{"kind": "exists", "table": "visits", "where": [_value("visits.flag", "x")]}],
}
VARIABLES: list[dict[str, Any]] = [
    {"column": "visits.s", "each": "category"},
    {"column": "visits.s", "aggregate": "some", "values": ["a"]},
    {"column": "visits.flag", "aggregate": "count", "where": [_value("visits.s", "a", "b")]},
]


def resolved(resolve: Callable[..., Resolution]) -> Resolution:
    written = {
        "aibi": "1",
        "dataset": "d",
        "unit": "sites",
        "cohorts": {name: {"all": clauses} for name, clauses in COHORTS.items()},
    }
    found = resolve(written, release(), VARIABLES)
    assert found.refusals == [], found.refusals
    return found


def texts(resolution: Resolution, sources: Sources) -> dict[str, list[str]]:
    """Every case's statements over ``sources``."""
    cohorts = resolution.cohorts
    memberships, some, count = (resolution.variables[f"0/{index}"] for index in range(3))
    found: dict[str, list[str]] = {}
    for name in ("value", "every", "covered", "two"):
        compiled = sql.compile_cohort(cohorts[name], sources)
        found[name] = [compiled.counts_sql, compiled.values_sql]
    found["membership"] = list(
        sql.compile_materialised([cohorts["value"]], [memberships], sources).statements
    )
    found["materialised"] = list(
        sql.compile_materialised(
            [cohorts["value"], cohorts["two"]], [some, count], sources
        ).statements
    )
    found["inputs"] = list(
        sql.compile_inputs([cohorts["value"], cohorts["every"]], [some, count], sources).statements
    )
    found["crossing"] = list(
        sql.compile_crossing(
            [cohorts["value"], cohorts["two"]], [cohorts["covered"], cohorts["flagged"]], sources
        ).statements
    )
    found["members"] = [sql.compile_members(cohorts["covered"], sources).statement]
    return found


def digests(found: Mapping[str, list[str]]) -> dict[str, list[str]]:
    return {
        name: [hashlib.sha256(text.encode()).hexdigest()[:16] for text in statements]
        for name, statements in sorted(found.items())
    }


DIGESTS: dict[str, list[str]] = {
    "covered": ["5bdaea839af9386e", "7197843ae44d8b07"],
    "crossing": ["860549a57b0c8be0", "9bd6e9470dcdb510", "654dc285bc27b005"],
    "every": ["11b72aacc638e2be", "fb91c380c9fad280"],
    "inputs": [
        "99a09f02ed9b4770",
        "7ae90483f369f1d8",
        "4fe5dc8a427a6088",
        "53265042eaa4e4bc",
        "6246e583781f517c",
        "bc19468bc3f9094b",
    ],
    "materialised": [
        "f0d8a505b3e1ba48",
        "71a7707e403ddbbf",
        "2c2bdbe72db1616e",
        "a1f00e772f62ba81",
        "9daa7b4eb652cd53",
        "88557ceb25347ab3",
    ],
    "members": ["3f01d988d5b1e4fb"],
    "membership": ["77d3526b5632b1dd"],
    "two": ["b86e879c09618ab3", "e04c2a62db7b03cc"],
    "value": ["a854dd700674b00e", "7450fba52412a60b"],
}
"""Recorded on the tree before the one-group path, with SQLGlot 30.19.0."""
SQLGLOT = "30.19.0"


def sized(sources: Sources, rows: int | None) -> dict[str, TableSource]:
    return {table: replace(source, n_rows=rows) for table, source in sources.items()}


@pytest.fixture(scope="module")
def cases(
    variables_of: Callable[..., Resolution], blobs: Callable[[Release], dict[str, Any]]
) -> tuple[Resolution, dict[str, TableSource]]:
    found = resolved(variables_of)
    return found, blobs(found.cohorts["value"].release)


def test_sqlglot_is_the_version_the_digests_were_recorded_with() -> None:
    assert version("sqlglot") == SQLGLOT


@pytest.mark.parametrize("rows", [None, grouped.ROW_BUDGET, 10**12])
def test_outside_the_bound_the_compiler_emits_mains_statements(
    cases: tuple[Resolution, dict[str, TableSource]], rows: int | None
) -> None:
    """Sizes unknown, or each table alone past the budget: main's statements, byte for byte."""
    resolution, sources = cases
    assert digests(texts(resolution, sized(sources, rows))) == DIGESTS


def test_mains_path_forced_emits_mains_statements(
    cases: tuple[Resolution, dict[str, TableSource]], force: Callable[[str], None]
) -> None:
    resolution, sources = cases
    force("main")
    assert digests(texts(resolution, sources)) == DIGESTS


def test_inside_the_bound_only_the_inputs_statements_are_mains(
    cases: tuple[Resolution, dict[str, TableSource]],
) -> None:
    """The fixture's own sizes are inside the bound: every case but the inputs takes the
    one-group path; the inputs path never does (D342, D449)."""
    resolution, sources = cases
    assert all(source.n_rows is not None for source in sources.values())
    found = digests(texts(resolution, sources))
    assert found["inputs"] == DIGESTS["inputs"]
    assert all(found[name] != DIGESTS[name] for name in DIGESTS if name != "inputs")


def record() -> None:
    """The digests of the statements this tree gives with sizes unknown, to be written into
    ``DIGESTS`` (``uv run python tests/core/engine/test_grouped_identity.py``)."""
    import runpy
    import tempfile

    conftest = runpy.run_path(str(Path(__file__).parent / "conftest.py"))
    found = resolved(conftest["resolve_variables"])
    sources = conftest["release_blobs"](found.cohorts["value"].release, Path(tempfile.mkdtemp()))
    print(json.dumps(digests(texts(found, sized(sources, None))), indent=4))


if __name__ == "__main__":
    record()

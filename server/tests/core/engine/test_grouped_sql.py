"""The one-group path in the compiler (SPEC D449): what decides it (the sizes of a coverage's
assignment, groups and parent tables, its listings over every part a compiler holds, one
decision per compiler, nothing kept between compilers), what it reads (``NOT MATERIALIZED``
relations of exactly the admitted coverages' tables, one count pass per coverage, ``MIN`` and
``MAX`` of row numbers alone), and that its answers are the evaluator's.

Regions hold sites; sites have visits and notes, both listed by one assignment of sites to groups
(two group columns) and one groups table; a site may sit in no group, one or two."""

import ast
import itertools
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest
from sqlglot import exp, parse_one
from tests.core.engine.forcing import GroupedPath, forced, forced_compiler, listings_checked

import aibi
from aibi.core.engine import build, grouped
from aibi.core.engine import sql as compiler
from aibi.core.engine.data import Release
from aibi.core.engine.resolve import Resolution, ResolvedCohort
from aibi.core.store.tables import TableSource

VISITS = "rel:visits.place"
NOTES = "rel:notes.place"
SITES = "rel:sites.area"
Sources = dict[str, TableSource]


ITEMS = "rel:items.call"
MARKS = "rel:marks.place"


def release(
    assign: Sequence[tuple[str, str | None, str | None]] | None = None, *, extended: bool = False
) -> Release:
    """With ``extended``, visits have items, listed by groups scoped by a column named as the
    visits' scope column is, and sites have marks, a category whose groups (codes) hold its
    scope values as integers."""
    column, table = build.column, build.table
    groups = {"table": "groups", "group_column": "grp", "scope_columns": {"s": "s"}}
    descriptors = [
        build.dataset(disclosure={"allow_row_ids": True}),
        table("regions", ["region"]),
        column("regions.region", "string"),
        table("sites", ["site"]),
        column("sites.site", "string"),
        column("sites.region", "string"),
        build.relationship("sites", ["region"], "regions", role="area"),
        build.coverage(SITES, "all"),
        table("visits", ["visit"], role="event"),
        column("visits.visit", "string"),
        column("visits.site", "string"),
        column("visits.s", "category"),
        column("visits.flag", "category"),
        build.relationship("visits", ["site"], "sites", role="place"),
        build.coverage(
            VISITS,
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
            NOTES,
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
    given = assign or [
        ("t0", "g0", "g1"),
        ("t1", "g0", "g1"),
        ("t1", "g1", "g0"),
        ("t2", "g2", "g2"),
        ("t3", "g1", None),
        ("t3", "g1", "dangling"),
        ("t5", "g0", "g0"),
        ("t5", "g2", "g0"),
    ]
    rows: dict[str, list[dict[str, object]]] = {
        "regions": [{"region": "r0"}, {"region": "r1"}],
        "sites": [{"site": f"t{n}", "region": f"r{n % 2}"} for n in range(6)],
        "visits": [
            {"visit": "v0", "site": "t0", "s": "a", "flag": "x"},
            {"visit": "v1", "site": "t1", "s": "b", "flag": "y"},
            {"visit": "v2", "site": "t3", "s": "b", "flag": "y"},
            {"visit": "v3", "site": "t5", "s": "c", "flag": "x"},
            {"visit": "v4", "site": "t4", "s": "a", "flag": "x"},
        ],
        "notes": [{"note": "n0", "site": "t1", "s": "b"}, {"note": "n1", "site": "t5", "s": "a"}],
        "assign": [{"site": site, "grp": one, "grp2": two} for site, one, two in given],
        "groups": [
            {"grp": "g0", "s": "a", "all": False},
            {"grp": "g0", "s": "c", "all": False},
            {"grp": "g1", "s": "b", "all": False},
            {"grp": "g2", "s": None, "all": True},
            {"grp": "unused", "s": "a", "all": False},
        ],
    }
    if extended:
        descriptors += [
            table("items", ["item"], role="event"),
            column("items.item", "string"),
            column("items.visit", "string"),
            column("items.s", "category"),
            build.relationship("items", ["visit"], "visits", role="call"),
            build.coverage(
                ITEMS,
                {
                    "assignment": {
                        "table": "listed",
                        "parent_columns": {"visit": "visit"},
                        "group_column": "grp",
                    },
                    "groups": groups,
                },
            ),
            table("listed", None, role="coverage"),
            column("listed.visit", "string"),
            column("listed.grp", "string"),
            table("marks", ["mark"], role="event"),
            column("marks.mark", "string"),
            column("marks.site", "string"),
            column("marks.s", "category"),
            build.relationship("marks", ["site"], "sites", role="place"),
            build.coverage(
                MARKS,
                {
                    "assignment": {
                        "table": "assign",
                        "parent_columns": {"site": "site"},
                        "group_column": "grp",
                    },
                    "groups": {
                        "table": "codes",
                        "group_column": "grp",
                        "scope_columns": {"s": "s"},
                    },
                },
            ),
            table("codes", None, role="coverage"),
            column("codes.grp", "string"),
            column("codes.s", "integer", units="1"),
        ]
        rows["items"] = [
            {"item": "i0", "visit": "v0", "s": "a"},
            {"item": "i1", "visit": "v3", "s": "c"},
        ]
        rows["marks"] = [{"mark": "m0", "site": "t1", "s": "1"}]
        rows["codes"] = [{"grp": "g0", "s": 1}, {"grp": "g1", "s": 2}]
        rows["listed"] = [
            {"visit": "v0", "grp": "g0"},
            {"visit": "v3", "grp": "g1"},
            {"visit": "v3", "grp": "g0"},
        ]
    return build.release(descriptors, rows)


def value(column: str, *values: str) -> dict[str, Any]:
    return {"kind": "value", "column": column, "values": list(values)}


def visits(*where: Any, **extra: Any) -> dict[str, Any]:
    return {"kind": "exists", "table": "visits", "where": list(where), **extra}


def notes(*where: Any, **extra: Any) -> dict[str, Any]:
    return {"kind": "exists", "table": "notes", "where": list(where), **extra}


def written(cohorts: Mapping[str, Sequence[Any]], unit: str = "sites") -> dict[str, Any]:
    return {
        "aibi": "1",
        "dataset": "d",
        "unit": unit,
        "cohorts": {name: {"all": list(clauses)} for name, clauses in cohorts.items()},
    }


@pytest.fixture(scope="module")
def made() -> Release:
    return release()


@pytest.fixture(scope="module")
def sources(made: Release, blobs: Callable[[Release], Sources]) -> Sources:
    return blobs(made)


def resolved(
    variables_of: Callable[..., Resolution],
    made: Release,
    cohorts: Mapping[str, Sequence[Any]],
    unit: str = "sites",
    variables: Sequence[Mapping[str, Any]] = (),
) -> Resolution:
    found = variables_of(written(cohorts, unit), made, list(variables))
    assert found.refusals == [], found.refusals
    return found


def compiled(
    cohort: ResolvedCohort,
    sources: Mapping[str, TableSource],
    path: GroupedPath = "auto",
) -> Any:
    found = forced(path, cohort, sources)
    found.compile()
    return found


def sized(sources: Sources, **rows: int | None) -> Sources:
    return {
        table: replace(source, n_rows=rows.get(table, source.n_rows))
        for table, source in sources.items()
    }


# --- The decision ---------------------------------------------------------------------------


def test_the_fixtures_coverages_are_inside_the_bound(
    variables_of: Callable[..., Resolution], made: Release, sources: Sources
) -> None:
    cohort = resolved(variables_of, made, {"c": [visits(value("visits.s", "a")), notes()]})
    assert compiled(cohort.cohorts["c"], sources).admitted == {VISITS, NOTES}
    assert all(source.n_rows is not None for source in sources.values())


@pytest.mark.parametrize("listings", [1, 3])
def test_the_rule_reads_the_assignment_groups_and_parent_tables_alone(
    variables_of: Callable[..., Resolution],
    made: Release,
    sources: Sources,
    monkeypatch: pytest.MonkeyPatch,
    listings: int,
) -> None:
    """The parent table (sites) is neither the unit (regions) nor the child (visits): each
    table's rows move the decision by its own factor, and no other table's do."""
    letters = "abc"[:listings]
    clauses = [visits(value("visits.s", letter)) for letter in letters]
    cohort = resolved(
        variables_of,
        made,
        {"c": [{"kind": "exists", "table": "sites", "where": clauses}]},
        "regions",
    ).cohorts["c"]
    a, p, g = 101, 37, 59
    term = 4 * a + p + (3 + listings) * g
    others = {"regions": 10**9, "visits": 10**9, "notes": 10**9}
    given = sized(sources, assign=a, sites=p, groups=g, **others)
    for budget, admitted in ((term, {VISITS}), (term - 1, set())):
        monkeypatch.setattr(grouped, "ROW_BUDGET", budget)
        assert compiled(cohort, given).admitted == admitted
    monkeypatch.setattr(grouped, "ROW_BUDGET", term)
    swapped = 4 * g + p + (3 + listings) * a
    assert (compiled(cohort, sized(given, assign=g, groups=a)).admitted == {VISITS}) == (
        swapped <= term
    )
    assert compiled(cohort, sized(given, sites=p + 1)).admitted == set()
    assert compiled(cohort, sized(given, regions=0, visits=0, notes=0)).admitted == {VISITS}
    for table in ("assign", "sites", "groups"):
        assert compiled(cohort, sized(given, **{table: None})).admitted == set()
    unknown = {name: TableSource(source.path, source.columns) for name, source in given.items()}
    assert compiled(cohort, unknown).admitted == set()


def test_the_listings_of_a_coverage_are_counted_over_every_part_of_the_compiler(
    variables_of: Callable[..., Resolution],
    made: Release,
    sources: Sources,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two cohorts that ask one coverage with different values: two listings for the
    materialisation that holds both, though each holds one; the decision is the compiler's,
    shared by every statement it gives."""
    found = resolved(
        variables_of,
        made,
        {"c": [visits(value("visits.s", "a"))], "d": [visits(value("visits.s", "b"))]},
        variables=[
            {"column": "visits.flag", "aggregate": "count", "where": [value("visits.s", "c")]}
        ],
    )
    c, d = found.cohorts["c"], found.cohorts["d"]
    variable = found.variables["0/0"]
    rows = {table: source.n_rows for table, source in sources.items()}
    a, p, g = (cast(int, rows[name]) for name in ("assign", "sites", "groups"))

    def admitted(cohorts: list[ResolvedCohort], variables: list[Any]) -> frozenset[str]:
        made_compiler = compiler._Compiler(cohorts[0], sources)  # pyright: ignore[reportPrivateUsage]
        made_compiler.materialised(cohorts, variables)
        return made_compiler.admitted

    for listings, cohorts, variables in ((2, [c, d], []), (3, [c, d], [variable]), (1, [c], [])):
        monkeypatch.setattr(grouped, "ROW_BUDGET", 4 * a + p + (3 + listings) * g)
        assert admitted(cohorts, variables) == {VISITS}
        monkeypatch.setattr(grouped, "ROW_BUDGET", 4 * a + p + (3 + listings) * g - 1)
        assert admitted(cohorts, variables) == frozenset()


def test_nothing_is_kept_between_compilers(
    variables_of: Callable[..., Resolution],
    made: Release,
    sources: Sources,
    monkeypatch: pytest.MonkeyPatch,
    inputs_compiled: Callable[..., Any],
) -> None:
    """Two compiles that differ only in the sizes, or only in the listings, decide apart; and a
    listing of inputs, which never admits a coverage, leaves the next compiler's decision alone."""
    found = resolved(
        variables_of,
        made,
        {
            "one": [visits(value("visits.s", "a"))],
            "two": [visits(value("visits.s", "a", "b")), visits(value("visits.s", "b"))],
        },
        variables=[
            {"column": "visits.flag", "aggregate": "count", "where": [value("visits.s", "c")]}
        ],
    )
    one, two = found.cohorts["one"], found.cohorts["two"]
    rows = {table: source.n_rows for table, source in sources.items()}
    a, p, g = (cast(int, rows[name]) for name in ("assign", "sites", "groups"))
    monkeypatch.setattr(grouped, "ROW_BUDGET", 4 * a + p + 4 * g)
    assert compiled(one, sources).admitted == {VISITS}
    assert compiled(two, sources).admitted == set()
    assert compiled(one, sources).admitted == {VISITS}
    assert compiled(one, sized(sources, groups=g + 1)).admitted == set()
    assert compiled(one, sources).admitted == {VISITS}
    listed = inputs_compiled([one], [found.variables["0/0"]])
    assert all("NOT MATERIALIZED" not in statement for statement in listed.statements)
    assert compiled(one, sources).admitted == {VISITS}


def test_one_coverage_inside_and_one_skipped_in_one_statement_answer_as_the_evaluator(
    run: Callable[..., Any],
    made: Release,
    sources: Sources,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Notes sort first and are asked three ways, visits once: with the budget at visits' term
    alone, notes are skipped and visits admitted; the statement holds both paths."""
    clauses = [
        notes(value("notes.s", "a")),
        notes(value("notes.s", "b")),
        {"not": notes(value("notes.s", "c"))},
        visits(value("visits.s", "b")),
    ]
    rows = {table: source.n_rows for table, source in sources.items()}
    a, p, g = (cast(int, rows[name]) for name in ("assign", "sites", "groups"))
    monkeypatch.setattr(grouped, "ROW_BUDGET", 4 * a + p + 4 * g)
    checked = run(written({"c": clauses}), made)
    cohort = checked.resolution.cohorts["c"]
    made_compiler = compiled(cohort, sources)
    assert made_compiler.admitted == {VISITS}
    assert set(made_compiler.counted) == {VISITS}


# --- The listings ---------------------------------------------------------------------------


def _spied(monkeypatch: pytest.MonkeyPatch) -> dict[str, set[object]]:
    found: dict[str, set[object]] = {}
    given = compiler._Compiler  # pyright: ignore[reportPrivateUsage]
    listing, values = given.listing, given.scope_values

    def spy_listing(self: Any, coverage: Any, columns: Any, admitted: Any) -> str:
        keys = found.setdefault(coverage.relationship, set())
        keys.add(compiler._admitted_key(columns, admitted))  # pyright: ignore[reportPrivateUsage]
        return listing(self, coverage, columns, admitted)

    def spy_values(self: Any, coverage: Any, column: Any, kind: Any) -> str | None:
        made_values = values(self, coverage, column, kind)
        if made_values is not None:
            keys = found.setdefault(coverage.relationship, set())
            keys.add(("values", made_values))
        return made_values

    monkeypatch.setattr(given, "listing", spy_listing)
    monkeypatch.setattr(given, "scope_values", spy_values)
    return found


def _counted(found: Mapping[str, set[object]]) -> dict[str, int]:
    return {relationship: len(keys) for relationship, keys in sorted(found.items())}


LISTED: list[tuple[str, dict[str, list[Any]], list[dict[str, Any]]]] = [
    ("leaf", {"c": [visits(value("visits.s", "a"))]}, []),
    (
        "nested",
        {
            "c": [
                {
                    "any": [
                        visits(value("visits.s", "a")),
                        {"not": visits(value("visits.s", "a"), value("visits.flag", "x"))},
                    ]
                },
                visits(quantifier="every"),
            ]
        },
        [],
    ),
    (
        "where",
        {
            "c": [
                visits(value("visits.s", "a", "b"), value("visits.s", "b", "c")),
                visits(value("visits.s", "b")),
            ]
        },
        [],
    ),
    (
        "lift",
        {
            "c": [
                {
                    "kind": "exists",
                    "table": "sites",
                    "where": [visits(value("visits.s", "a"), lift="assessed")],
                    "lift": "strict",
                }
            ]
        },
        [],
    ),
    (
        "covered",
        {
            "c": [
                {"kind": "covered", "table": "visits", "scope": {"s": ["a"]}},
                {"kind": "covered", "table": "visits"},
                {"kind": "covered", "table": "notes", "scope": {"s": ["b", "a"]}},
            ]
        },
        [],
    ),
    (
        "steps",
        {
            "c": [
                {
                    "kind": "covered",
                    "table": "visits",
                    "via": [
                        {"rel": SITES, "dir": "down"},
                        {"rel": VISITS, "dir": "down"},
                    ],
                    "scope": {"s": ["c"]},
                }
            ]
        },
        [],
    ),
    (
        "variables",
        {"c": [visits()]},
        [
            {"column": "visits.s", "each": "category"},
            {"column": "notes.s", "each": "category"},
            {"column": "visits.flag", "aggregate": "count", "where": [value("visits.s", "a")]},
            {"column": "visits.s", "aggregate": "some", "values": ["b"]},
        ],
    ),
]


@pytest.mark.parametrize(("name", "cohorts", "variables"), LISTED, ids=[case[0] for case in LISTED])
def test_the_listings_counted_are_the_listings_the_compiler_builds(
    variables_of: Callable[..., Resolution],
    made: Release,
    sources: Sources,
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    cohorts: dict[str, list[Any]],
    variables: list[dict[str, Any]],
) -> None:
    unit = "regions" if name in ("lift", "steps") else "sites"
    found = resolved(variables_of, made, cohorts, unit, variables)
    parts = list(found.cohorts.values())
    given = [found.variables[f"0/{index}"] for index in range(len(variables))]
    coverage = {
        relationship: one
        for part in (*parts, *given)
        for relationship, one in part.coverage.items()
    }
    merged = replace(parts[0], coverage=dict(sorted(coverage.items())))
    spied = _spied(monkeypatch)
    for path in ("one_group", "main"):
        spied.clear()
        made_compiler = forced(path, merged, sources)
        counted = compiler._Listings(made_compiler).of(parts, given)  # pyright: ignore[reportPrivateUsage]
        if given:
            made_compiler.materialised(parts, given)
        else:
            made_compiler.compile()
        if path == "one_group":
            assert counted == _counted(spied)
        assert set(counted) <= set(_counted(spied))


def test_a_crossing_counts_the_listings_of_all_its_parts(
    variables_of: Callable[..., Resolution],
    made: Release,
    sources: Sources,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Six cohorts and sixteen predicates of eight leaves each, every leaf asking the coverage
    for a value of its own: 176 listings of one coverage, in one compiler."""
    names = itertools.count()

    def part() -> list[Any]:
        return [visits(value("visits.s", f"v{next(names)}")) for _ in range(8)]

    def parts(count: int) -> list[ResolvedCohort]:
        found: list[ResolvedCohort] = []
        while len(found) < count:
            batch = {f"c{index}": part() for index in range(min(6, count - len(found)))}
            resolution = resolved(variables_of, made, batch)
            found += [resolution.cohorts[name] for name in batch]
        return found

    first, second = parts(6), parts(16)
    spied = _spied(monkeypatch)
    made_compiler = forced(
        "one_group",  # pyright: ignore[reportPrivateUsage]
        first[0],
        sources,
    )
    assert compiler._Listings(made_compiler).of([*first, *second], []) == {VISITS: 176}  # pyright: ignore[reportPrivateUsage]
    made_compiler.crossing(first, second)
    assert _counted(spied) == {VISITS: 176}
    assert made_compiler.admitted == {VISITS}
    assert len(made_compiler.counted) == 1


# --- What the one-group path reads ----------------------------------------------------------


def _bases(statement: str, parameters: Mapping[str, Any], sources: Sources) -> dict[str, bool]:
    """Each table a statement reads a blob of, and whether its relation is NOT MATERIALIZED."""
    tables = {source.path: table for table, source in sources.items()}
    found: dict[str, bool] = {}
    for cte in parse_one(statement, dialect="duckdb").find_all(exp.CTE):
        reads = [
            function
            for function in cte.this.find_all(exp.Anonymous, exp.ReadParquet)
            if function.sql(dialect="duckdb").upper().startswith("READ_PARQUET(")
        ]
        if not reads:
            continue
        [placeholder] = list(reads[0].find_all(exp.Placeholder))
        found[tables[cast(str, parameters[placeholder.name])]] = (
            cte.args.get("materialized") is False
        )
    return found


@pytest.mark.parametrize("admitted", [VISITS, NOTES])
def test_exactly_the_admitted_coverages_tables_are_not_materialized(
    variables_of: Callable[..., Resolution],
    made: Release,
    sources: Sources,
    monkeypatch: pytest.MonkeyPatch,
    admitted: str,
) -> None:
    """A relation is NOT MATERIALIZED exactly where its table is the assignment, groups or parent
    table of an admitted coverage: here one coverage is admitted and the other, over the same
    assignment and groups tables, skipped, in either order of their ids; the child tables and
    the unit's are main's."""
    asked, skipped = (visits, notes) if admitted == VISITS else (notes, visits)
    table = "visits" if admitted == VISITS else "notes"
    other = "notes" if admitted == VISITS else "visits"
    clauses = [
        asked(value(f"{table}.s", "a")),
        skipped(value(f"{other}.s", "a")),
        skipped(value(f"{other}.s", "b")),
        {"not": skipped(value(f"{other}.s", "c"))},
    ]
    cohort = resolved(
        variables_of,
        made,
        {"c": [{"kind": "exists", "table": "sites", "where": clauses}]},
        "regions",
    ).cohorts["c"]
    rows = {name: source.n_rows for name, source in sources.items()}
    a, p, g = (cast(int, rows[name]) for name in ("assign", "sites", "groups"))
    monkeypatch.setattr(grouped, "ROW_BUDGET", 4 * a + p + 4 * g)
    made_compiler = compiled(cohort, sources)
    assert made_compiler.admitted == {admitted}
    statements = compiler.compile_cohort(cohort, sources)
    for statement in (statements.counts_sql, statements.values_sql):
        bases = _bases(statement, statements.parameters, sources)
        assert bases == {
            "regions": False,
            "sites": True,
            "visits": False,
            "notes": False,
            "assign": True,
            "groups": True,
        }
    monkeypatch.setattr(grouped, "ROW_BUDGET", 0)
    main = compiler.compile_cohort(cohort, sources)
    assert "NOT MATERIALIZED" not in main.counts_sql + main.values_sql


def test_a_listing_of_inputs_takes_mains_path_and_materialises_every_relation(
    variables_of: Callable[..., Resolution], made: Release, sources: Sources
) -> None:
    found = resolved(
        variables_of,
        made,
        {"c": [visits(value("visits.s", "a")), {"kind": "covered", "table": "notes"}]},
        variables=[
            {"column": "visits.flag", "aggregate": "count", "where": [value("visits.s", "c")]}
        ],
    )
    listed = compiler.compile_inputs([found.cohorts["c"]], [found.variables["0/0"]], sources)
    for statement in listed.statements:
        ctes = list(parse_one(statement, dialect="duckdb").find_all(exp.CTE))
        assert ctes
        assert all(cte.args.get("materialized") is True for cte in ctes)
    for path in ("one_group", "main"):
        made_compiler = forced(path, found.cohorts["c"], sources)
        assert (
            made_compiler.inputs([found.cohorts["c"]], [found.variables["0/0"]]).statements
            == listed.statements
        )
        assert made_compiler.admitted == frozenset()


_COUNT_PASS = re.compile(r'CASE WHEN MIN\("n"\."rid"\) = MAX\("n"\."rid"\) THEN 1 ELSE 2 END')


def test_one_count_pass_per_coverage_per_compiler(
    variables_of: Callable[..., Resolution], made: Release, sources: Sources
) -> None:
    """However many listings of a coverage a compiler builds (here four of visits and two of
    notes, under several quantifiers), each admitted coverage is counted once."""
    clauses = [
        visits(value("visits.s", "a")),
        visits(value("visits.flag", "y"), quantifier="every"),
        {"not": visits(value("visits.s", "a", "c"))},
        {"kind": "covered", "table": "visits"},
        notes(value("notes.s", "a")),
        {"kind": "covered", "table": "notes", "scope": {"s": ["b"]}},
    ]
    cohort = resolved(variables_of, made, {"c": clauses}).cohorts["c"]
    statements = compiler.compile_cohort(cohort, sources)
    for statement in (statements.counts_sql, statements.values_sql):
        assert len(_COUNT_PASS.findall(statement)) == 2
    made_compiler = compiled(cohort, sources)
    assert set(made_compiler.counted) == {VISITS, NOTES}
    # The two halves are stacked with UNION ALL: no parent row is in both, and a UNION that
    # removed duplicates would hide one that was (D449).
    bodies = dict(made_compiler.ctes)
    stacked = [
        union
        for key, name in made_compiler.memo.items()
        if isinstance(key, tuple) and key[0] == "one group"
        for union in bodies[name].find_all(exp.Union)
    ]
    assert stacked
    assert all(union.args.get("distinct") is False for union in stacked)


def test_the_one_group_path_aggregates_integers_alone(
    variables_of: Callable[..., Resolution], made: Release, sources: Sources
) -> None:
    """§9.3: COUNT, BOOL_OR and MIN and MAX of row numbers (``rid``); the groups' row numbers are
    a window's, over the distinct keys."""
    cohort = resolved(
        variables_of,
        made,
        {
            "c": [
                visits(value("visits.s", "a")),
                notes(),
                {"kind": "covered", "table": "visits", "scope": {"s": ["b"]}},
            ]
        },
    ).cohorts["c"]
    statements = compiler.compile_cohort(cohort, sources)
    assert compiled(cohort, sources).admitted == {VISITS, NOTES}
    tree = parse_one(statements.counts_sql, dialect="duckdb")
    seen: set[str] = set()
    for node in tree.find_all(exp.AggFunc):
        seen.add(node.sql_name())
        assert node.sql_name() in {"COUNT", "BITWISE_OR_AGG", "LOGICAL_OR", "MIN", "MAX"}
        if isinstance(node, exp.Min | exp.Max):
            assert isinstance(node.this, exp.Column)
            assert node.this.name in {"v", "rid"}
    assert {"MIN", "MAX"} <= seen
    windows = list(tree.find_all(exp.Window))
    assert windows
    assert all(isinstance(window.this, exp.RowNumber) for window in windows)


def test_the_count_pass_finds_exactly_the_parents_of_one_group(
    variables_of: Callable[..., Resolution], sessions: Any, blobs: Callable[[Release], Sources]
) -> None:
    """The count pass compiled alone, against the fixture's own groups: a site whose assignment
    rows reach one group (once or more) counts 1, more than one 2, and one whose keys are null
    or dangling, or that has none, is not counted."""
    assign = [
        ("t0", "g0", "g0"),
        ("t1", "g0", "g0"),
        ("t1", "g1", "g0"),
        ("t2", "g2", "g2"),
        ("t2", "g2", "g1"),
        ("t3", None, None),
        ("t4", "dangling", "dangling"),
        ("t4", "g1", "g1"),
        ("t5", "unused", "dangling"),
    ]
    made = release(assign)
    sources = blobs(made)
    cohort = resolved(variables_of, made, {"c": [visits(), notes()]}).cohorts["c"]
    made_compiler = compiled(cohort, sources, "one_group")
    known = {"g0", "g1", "g2", "unused"}
    for relationship, column in ((VISITS, 1), (NOTES, 2)):
        reached: dict[str, set[str]] = {}
        for row in assign:
            key = row[column]
            if key is not None and key in known:
                reached.setdefault(row[0], set()).add(key)
        site = {f"t{n}": n for n in range(6)}
        oracle = sorted((site[name], 1 if len(keys) == 1 else 2) for name, keys in reached.items())
        _, counts = made_compiler.counted[relationship]
        bases = [made_compiler.base_cte(base) for base in made_compiler.bases.values()]
        ctes = bases + [
            exp.CTE(this=body, alias=exp.TableAlias(this=exp.to_identifier(name)))
            for name, body in made_compiler.ctes
        ]
        select = exp.select("c.rid", "c.c").from_(f"{counts} AS c").order_by("c.rid")
        statement = made_compiler.with_ctes(select, ctes).sql(dialect="duckdb")
        used = {
            node.name for node in parse_one(statement, dialect="duckdb").find_all(exp.Placeholder)
        }
        parameters = {name: made_compiler.parameters[name] for name in used}
        [(_, rows)] = sessions.run(
            sorted(source.path for source in sources.values()), [(statement, parameters)]
        )
        assert [tuple(row) for row in rows] == oracle, relationship


# --- No switch -------------------------------------------------------------------------------


def test_the_compiler_has_no_switch_and_no_caller_reaches_its_decision() -> None:
    """The path is the rule's alone (D449): the compiler's class takes no parameter beyond its
    cohort, sources and ``declared``, reads no environment, and holds no attribute but what
    ``decide`` sets at every entry; no module of ``aibi`` but ``engine/sql.py`` names the class or
    its listings' pass, so only the compiler's own entries construct one. (A scan cannot see code
    written to evade it, by names assembled at run time: what it guards against is accidental
    use, and what the class offers such code is ``decide`` and ``admit``, which every entry
    overwrites before it builds: the next test.)"""
    import inspect

    assert list(inspect.signature(compiler._Compiler).parameters) == [  # pyright: ignore[reportPrivateUsage]
        "cohort",
        "sources",
        "declared",
    ]
    for entry in (
        compiler.compile_cohort,
        compiler.compile_crossing,
        compiler.compile_materialised,
        compiler.compile_members,
        compiler.compile_inputs,
    ):
        assert not any("path" in name for name in inspect.signature(entry).parameters)
    root = Path(cast(str, aibi.__file__)).parent
    for path in sorted(root.rglob("*.py")):
        if path.name == "sql.py" and path.parent.name == "engine":
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Name | ast.Attribute):
                name = node.id if isinstance(node, ast.Name) else node.attr
                assert name not in ("_Compiler", "_Listings"), (path, name)
            if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Store | ast.Del):
                assert node.attr not in ("admitted", "split_tables"), path
    for name in ("sql.py", "grouped.py"):
        source = (root / "core" / "engine" / name).read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported = {
            alias.name.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import | ast.ImportFrom)
            for alias in node.names
        } | {
            cast(str, node.module).split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        }
        assert not imported & {"os", "sys", "dotenv"}, name
        assert "environ" not in source, name
        assert "getenv" not in source, name
        assert "grouped_path" not in source, name


def test_what_an_instance_is_given_before_an_entry_is_decided_again(
    variables_of: Callable[..., Resolution], made: Release, sources: Sources
) -> None:
    """Attributes set on a compiler before an entry (as code that evaded the scan might set
    them) do not survive it: the entry decides again before it builds anything."""
    cohort = resolved(variables_of, made, {"c": [visits(value("visits.s", "a"))]}).cohorts["c"]
    expected = compiler.compile_cohort(cohort, sources)
    for admitted in (frozenset(), frozenset({VISITS, NOTES})):
        made_compiler = compiler._Compiler(cohort, sources)  # pyright: ignore[reportPrivateUsage]
        made_compiler.admitted = admitted
        made_compiler.split_tables = frozenset({"regions", "visits"})
        made_compiler.grouped_path = "main"  # pyright: ignore[reportAttributeAccessIssue]
        found = made_compiler.compile()
        assert (found.counts_sql, found.values_sql) == (expected.counts_sql, expected.values_sql)
        assert made_compiler.admitted == {VISITS}


EXTENDED: list[tuple[str, str, dict[str, list[Any]]]] = [
    (
        "two grouped steps",
        "sites",
        {
            "c": [
                {
                    "kind": "covered",
                    "table": "items",
                    "via": [{"rel": VISITS, "dir": "down"}, {"rel": ITEMS, "dir": "down"}],
                    "scope": {"s": ["a"]},
                },
                # The visits' own listing of ``a``: the covered's first step lists them for
                # no values, a second listing, which a scope read at every step would merge.
                visits(value("visits.s", "a")),
            ]
        },
    ),
    (
        "up then down",
        "visits",
        {
            "c": [
                {
                    "kind": "covered",
                    "table": "notes",
                    "via": [{"rel": VISITS, "dir": "up"}, {"rel": NOTES, "dir": "down"}],
                    "scope": {"s": ["b"]},
                }
            ]
        },
    ),
]


@pytest.mark.parametrize(("name", "unit", "cohorts"), EXTENDED, ids=[case[0] for case in EXTENDED])
def test_the_listings_of_several_steps_are_counted(
    variables_of: Callable[..., Resolution],
    blobs: Callable[[Release], Sources],
    run: Callable[..., Any],
    name: str,
    unit: str,
    cohorts: dict[str, list[Any]],
) -> None:
    """A ``covered`` over two grouped down steps whose scope columns share a name (only the last
    step's are admitted), and one that steps up before its down step (an up step lists
    nothing): counted as built, and answered as the evaluator answers. A parent scope asks no
    question in v1 (D207: the release check and resolution refuse one), so the walk of parent
    scopes finds no listing in any release."""
    made = release(extended=True)
    sources = blobs(made)
    checked = run(written(cohorts, unit), made)
    cohort = checked.resolution.cohorts["c"]
    for path in ("one_group", "main"):
        built = _built(cohort, sources, path)
        assert compiler._Listings(_bare(cohort, sources)).of([cohort], []) == built, (name, path)  # pyright: ignore[reportPrivateUsage]


def _bare(cohort: ResolvedCohort, sources: Sources) -> Any:
    return forced("main", cohort, sources)


def _built(cohort: ResolvedCohort, sources: Sources, path: GroupedPath) -> dict[str, int]:
    """The listings of each grouped coverage a compile of ``cohort`` builds by ``path``."""
    given = forced_compiler(path)
    found: dict[str, set[object]] = {}

    class Spy(given):  # type: ignore[misc, valid-type]
        def listing(self, coverage: Any, columns: Any, admitted: Any) -> str:
            if compiler._is_grouped(coverage):  # pyright: ignore[reportPrivateUsage]
                keys = found.setdefault(coverage.relationship, set())
                keys.add(compiler._admitted_key(columns, admitted))  # pyright: ignore[reportPrivateUsage]
            return super().listing(coverage, columns, admitted)

    Spy(cohort, sources).compile()
    return {relationship: len(keys) for relationship, keys in sorted(found.items())}


@pytest.mark.parametrize("entry", ["compile", "crossing", "members", "materialised"])
def test_every_entry_decides_over_the_listings_of_every_part(
    variables_of: Callable[..., Resolution],
    made: Release,
    sources: Sources,
    monkeypatch: pytest.MonkeyPatch,
    entry: str,
) -> None:
    """Each compile entry's decision moves with the listings of every part it is given: here
    three (a cohort's own two, and a value its predicates, or its variable, ask that it does
    not), so the coverage is admitted at the term of three listings and not one row below it,
    where one that counted fewer parts would admit it."""
    found = resolved(
        variables_of,
        made,
        {
            "c": [visits(value("visits.s", "a")), visits(value("visits.s", "a", "b"))],
            "p": [visits(value("visits.s", "c"))],
        },
        variables=[
            {"column": "visits.flag", "aggregate": "count", "where": [value("visits.s", "c")]}
        ],
    )
    cohort, predicate, variable = found.cohorts["c"], found.cohorts["p"], found.variables["0/0"]
    rows = {table: source.n_rows for table, source in sources.items()}
    a, p, g = (cast(int, rows[name]) for name in ("assign", "sites", "groups"))
    listings = 2 if entry in ("compile", "members") else 3
    coverage = {**cohort.coverage, **predicate.coverage, **variable.coverage}
    merged = replace(cohort, coverage=dict(sorted(coverage.items())))

    def admitted() -> frozenset[str]:
        made_compiler = compiler._Compiler(merged, sources)  # pyright: ignore[reportPrivateUsage]
        if entry == "compile":
            made_compiler.compile()
        elif entry == "members":
            made_compiler.members()
        elif entry == "crossing":
            made_compiler.crossing([cohort], [predicate])
        else:
            made_compiler.materialised([cohort], [variable])
        return made_compiler.admitted

    term = 4 * a + p + (3 + listings) * g
    monkeypatch.setattr(grouped, "ROW_BUDGET", term)
    assert admitted() == {VISITS}
    monkeypatch.setattr(grouped, "ROW_BUDGET", term - 1)
    assert admitted() == frozenset()


def test_the_reached_keys_are_a_semi_join(
    variables_of: Callable[..., Resolution], made: Release, sources: Sources
) -> None:
    """The groups the one-group path numbers are those some assignment row reaches: the groups
    table semi-joined to the assignment (D449), so unused groups cost nothing; answers alone
    cannot see it."""
    cohort = resolved(variables_of, made, {"c": [visits(value("visits.s", "a"))]}).cohorts["c"]
    made_compiler = compiled(cohort, sources)
    numbered, _ = made_compiler.counted[VISITS]
    bodies = dict(made_compiler.ctes)
    [keys] = [table.name for table in bodies[numbered].find_all(exp.Table)]
    joins = list(bodies[keys].find_all(exp.Join))
    assert [join.args.get("kind") or join.args.get("side") for join in joins] == ["SEMI"]


# --- The listings counted, for every branch of the pass, place of the coverage and entry ------

PLACES = [
    ("sites", "visits", "first"),
    ("regions", "visits", "middle"),
    ("regions", "items", "last"),
]
"""Where the grouped coverage sits on the path from the unit: its first down step, a middle one
(regions, then sites, then visits), or the last of three (then items)."""


def _branches(
    table: str, unit: str
) -> dict[str, tuple[list[Any], list[Any], list[dict[str, Any]]]]:
    """Each branch of the listings' pass (``_Listings``), as a cohort's clauses, a crossing's
    predicate and a materialisation's variables over ``table``."""
    asked = {"kind": "exists", "table": table, "where": [value(f"{table}.s", "a")]}
    other = {"kind": "exists", "table": table, "where": [value(f"{table}.s", "b")]}
    counted = f"{table}.flag" if table == "visits" else f"{table}.s"
    found: dict[str, tuple[list[Any], list[Any], list[dict[str, Any]]]] = {
        "exists": ([asked], [other], []),
        "every": (
            [{"kind": "exists", "table": table, "where": [], "quantifier": "every"}],
            [{"not": other}],
            [],
        ),
        "combinators": ([{"any": [asked, {"not": other}]}, {"all": [asked]}], [other], []),
        "known": ([{"known": asked}, {"unknown": other}], [{"known": other}], []),
        "covered": ([{"kind": "covered", "table": table, "scope": {"s": ["a"]}}], [other], []),
        "covered unscoped": ([{"kind": "covered", "table": table}], [other], []),
        "aggregate": (
            [asked],
            [other],
            [{"column": counted, "aggregate": "count", "where": [value(f"{table}.s", "c")]}],
        ),
        "some": (
            [asked],
            [other],
            [{"column": f"{table}.s", "aggregate": "some", "values": ["c"]}],
        ),
        "membership": ([asked], [other], [{"column": f"{table}.s", "each": "category"}]),
        "second variable": (
            [asked],
            [other],
            [{"column": f"{unit}.{unit[:-1]}"}, {"column": f"{table}.s", "each": "category"}],
        ),
    }
    if table == "items":
        # An aggregate's rows through two scoped coverages are open (§9.2): refused, so no part.
        del found["aggregate"], found["some"]
    if table == "visits":
        nested = {"kind": "exists", "table": "items", "where": [value("items.s", "b")]}
        found["aggregate's last where"] = (
            [asked],
            [other],
            [
                {
                    "column": "visits.flag",
                    "aggregate": "count",
                    "where": [value("visits.s", "a"), nested],
                }
            ],
        )
    return found


def _parts(
    variables_of: Callable[..., Resolution],
    made: Release,
    unit: str,
    cohort: list[Any],
    predicate: list[Any],
    variables: list[dict[str, Any]],
) -> tuple[ResolvedCohort, ResolvedCohort, list[Any], ResolvedCohort]:
    """The cohort, the predicate, the variables, and the cohort holding all their coverage."""
    found = resolved(variables_of, made, {"c": cohort, "p": predicate}, unit, variables)
    one, other = found.cohorts["c"], found.cohorts["p"]
    given = [found.variables[f"0/{index}"] for index in range(len(variables))]
    coverage = {**one.coverage, **other.coverage}
    for variable in given:
        coverage.update(variable.coverage)
    return one, other, given, replace(one, coverage=dict(sorted(coverage.items())))


@pytest.mark.parametrize("path", ["one_group", "auto", "main"])
@pytest.mark.parametrize(("unit", "table", "place"), PLACES, ids=[place[2] for place in PLACES])
def test_every_branch_counts_the_listings_built_at_every_place_and_entry(
    variables_of: Callable[..., Resolution],
    blobs: Callable[[Release], Sources],
    unit: str,
    table: str,
    place: str,
    path: GroupedPath,
) -> None:
    """The product of the listings' pass's branches, the place of the grouped coverage on the
    path from the unit and the four entries that decide, each compiled by the checking compiler
    (the decision before the first relation, each listing by its coverage's path, and the
    listings counted those built)."""
    made = release(extended=True)
    sources = blobs(made)
    checked = listings_checked(path)
    for cohort, predicate, variables in _branches(table, unit).values():
        one, other, given, merged = _parts(variables_of, made, unit, cohort, predicate, variables)
        checked(one, sources).compile()
        checked(one, sources).members()
        checked(merged, sources).crossing([one], [other])
        if given:
            checked(merged, sources).materialised([one], given)
    del place  # it names the case


@pytest.mark.parametrize("path", ["one_group", "auto", "main"])
def test_memberships_of_two_steps_and_of_another_kind_count_what_they_build(
    variables_of: Callable[..., Resolution], blobs: Callable[[Release], Sources], path: GroupedPath
) -> None:
    """A membership through two grouped down steps (its scope values are the last step's alone),
    and one whose scope column holds its categories in another type than the groups' (no scope
    values: its categories are no value of theirs)."""
    made = release(extended=True)
    sources = blobs(made)
    for variable in (
        {"column": "items.s", "each": "category"},
        {"column": "marks.s", "each": "category"},
    ):
        one, _, given, merged = _parts(
            variables_of, made, "sites", [visits(value("visits.s", "a"))], [visits()], [variable]
        )
        listings_checked(path)(merged, sources).materialised([one], given)


def test_the_budget_is_the_one_d449_states() -> None:
    """``ROW_BUDGET`` is D449's constant: the domain job's cells pin the costs it allows, and a
    change of it is a change of D449 (its figures were measured at this budget)."""
    assert grouped.ROW_BUDGET == 1_500_000


def test_a_compiler_decides_once_before_it_builds_anything(
    variables_of: Callable[..., Resolution], made: Release, sources: Sources
) -> None:
    """One decision per compiler (D449): a second decision, or one after anything was built, is
    an internal error rather than a statement holding two decisions: an entry that decided over
    the cohorts, built them and decided again over its predicates too (a crossing whose
    cohorts took the one-group path for a coverage its predicates then pushed past the budget)
    raises."""
    found = resolved(
        variables_of,
        made,
        {"c": [visits(value("visits.s", "a"))], "p": [visits(value("visits.s", "c"))]},
    )
    cohort, predicate = found.cohorts["c"], found.cohorts["p"]
    made_compiler = compiler._Compiler(cohort, sources)  # pyright: ignore[reportPrivateUsage]
    made_compiler.compile()
    with pytest.raises(RuntimeError, match="decides once"):
        made_compiler.compile()
    with pytest.raises(RuntimeError, match="decides once"):
        made_compiler.decide([cohort])
    given = compiler._Compiler  # pyright: ignore[reportPrivateUsage]

    class Twice(given):  # type: ignore[misc, valid-type]
        def part(self, cohort: Any, *, lifted: bool = False) -> Any:
            found = super().part(cohort, lifted=lifted)
            if cohort is predicate and not lifted:
                self.decide([cohort, predicate])
            return found

    with pytest.raises(RuntimeError, match="decides once"):
        Twice(cohort, sources).crossing([cohort], [predicate])
    built = compiler._Compiler(cohort, sources)  # pyright: ignore[reportPrivateUsage]
    built.base("sites")
    with pytest.raises(RuntimeError, match="decides once"):
        built.decide([cohort])


def test_the_listing_of_inputs_admits_nothing_and_keeps_no_preset_admission(
    variables_of: Callable[..., Resolution], made: Release, sources: Sources
) -> None:
    """The listing of inputs is main's SQL whatever the sizes: it admits nothing, once, so an
    admission an instance was given before it does not survive: the entry raises, even for
    an empty one."""
    found = resolved(
        variables_of,
        made,
        {"c": [visits(value("visits.s", "a"))]},
        variables=[
            {"column": "visits.flag", "aggregate": "count", "where": [value("visits.s", "a")]}
        ],
    )
    cohort, variable = found.cohorts["c"], found.variables["0/0"]
    merged = replace(
        cohort, coverage=dict(sorted({**cohort.coverage, **variable.coverage}.items()))
    )
    expected = compiler.compile_inputs([cohort], [variable], sources).statements
    fresh = compiler._Compiler(merged, sources)  # pyright: ignore[reportPrivateUsage]
    assert fresh.inputs([cohort], [variable]).statements == expected
    assert fresh.admitted == frozenset()
    for admitted in (frozenset({VISITS}), frozenset()):
        preset = compiler._Compiler(merged, sources)  # pyright: ignore[reportPrivateUsage]
        preset.admit(admitted)
        with pytest.raises(RuntimeError, match="decides once"):
            preset.inputs([cohort], [variable])


def test_a_parent_of_one_group_reads_its_groups_listing_and_only_the_others_take_the_join(
    variables_of: Callable[..., Resolution], made: Release, sources: Sources, sessions: Any
) -> None:
    """The one-group path's two halves: the per-group listing for the parents the count pass
    gives one group (``c = 1``), main's join restricted to those of two or more (``c >= 2``),
    each read from the count pass; a path that sent every parent through the join would answer
    alike, at main's cost. Held by the halves' exact conditions and by the rows each half gives
    on the fixture: the one half exactly the parents of one group (sites t0, t2 and t3, whose
    assignment repeats one group), the other exactly those of two (t1 and t5)."""
    cohort = resolved(variables_of, made, {"c": [visits(value("visits.s", "a"))]}).cohorts["c"]
    made_compiler = compiled(cohort, sources)
    assert made_compiler.admitted == {VISITS}
    _, counts = made_compiler.counted[VISITS]
    bodies = dict(made_compiler.ctes)
    [name] = [
        name
        for key, name in made_compiler.memo.items()
        if isinstance(key, tuple) and key[0] == "one group"
    ]
    [union] = list(bodies[name].find_all(exp.Union))
    one, many = union.this, union.expression
    by_group = _by_group(bodies, one)
    assert one.args["where"].this == exp.condition('"c"."c" = 1')
    [join] = one.args["joins"]
    assert join.args["on"] == exp.condition('"l"."rid" = "c"."gid"')
    assert {table.name for table in one.find_all(exp.Table)} == {counts, by_group}
    [condition] = [join.args["on"] for join in many.args["joins"] if join.this.name == counts]
    assert condition == exp.condition('("c"."rid" = "p"."rid" AND "c"."c" >= 2)')
    bases = [made_compiler.base_cte(base) for base in made_compiler.bases.values()]
    ctes = bases + [
        exp.CTE(this=body, alias=exp.TableAlias(this=exp.to_identifier(cte)))
        for cte, body in made_compiler.ctes
    ]
    found: list[list[int]] = []
    for half in (one, many):
        select = exp.select("h.rid").from_(half.copy().subquery("h")).order_by("h.rid")
        statement = made_compiler.with_ctes(select, ctes).sql(dialect="duckdb")
        used = {
            node.name for node in parse_one(statement, dialect="duckdb").find_all(exp.Placeholder)
        }
        [(_, rows)] = sessions.run(
            sorted(source.path for source in sources.values()),
            [(statement, {key: made_compiler.parameters[key] for key in used})],
        )
        found.append([row[0] for row in rows])
    assert found == [[0, 2, 3], [1, 5]]


def _by_group(bodies: Mapping[str, exp.Select], one: exp.Select) -> str:
    """The relation the one-group half joins the count pass to: the per-group listing, grouped
    by the groups' row number alone."""
    [join] = one.args["joins"]
    name = join.this.name
    grouped_by = bodies[name].args["group"].expressions
    assert [column.sql() for column in grouped_by] == ['"n"."rid"']
    return name

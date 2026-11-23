"""What the compiler is told of a release's tables, through the tools (SPEC D449): each table's
rows from its blob's footer, clamped up to the effective ``min_cell_count`` where the two tools
that compile build their sources, so that no count the catalogue withholds changes a statement.

A test-only pack imports plots: sites, their visits and notes, each listed by an assignment of
sites to plans (one table each) and one table of the plans' areas (grouped coverage). The query
workers record each statement and refuse to run it, so no worker is started; the overlap path,
which runs only after a first run answers, runs for real."""

import itertools
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import pyarrow.parquet as pq
import pytest
from pydantic import JsonValue, TypeAdapter

import aibi
from aibi.core.catalog.service import Catalog
from aibi.core.catalog.tools import BY_NAME, call
from aibi.core.engine import grouped
from aibi.core.engine.worker import Query, QueryRefused, Rows, Workers
from aibi.core.schema.descriptors import Descriptor
from aibi.core.schema.limits import QueryLimits
from aibi.core.schema.output import text
from aibi.core.schema.pack_api import (
    ConfinedPath,
    ImportOptions,
    ImportResult,
    Pack,
    PackManifest,
    PackRegistry,
)
from aibi.core.schema.refusals import Refusal, RefusalCode
from aibi.core.store import build as store_build
from aibi.core.store import parquet, statistics
from aibi.core.store.build import Layout
from aibi.core.store.erasure import erase
from aibi.core.store.sources import TypedSource

World = Any
BY = "importer:plots@1.0.0"
AT = "2026-01-01T00:00:00Z"
VISITS = "rel:visits.site_id"
NOTES = "rel:notes.site_id"
_DESCRIPTORS: TypeAdapter[Descriptor] = TypeAdapter(Descriptor)

TABLES: dict[str, tuple[list[str], list[str] | None, str, dict[str, str]]] = {
    "sites": (["site_id"], ["site_id"], "entity", {}),
    "visits": (["visit_id", "site_id", "area"], ["visit_id"], "event", {"area": "category"}),
    "notes": (["note_id", "site_id", "area"], ["note_id"], "event", {"area": "category"}),
    "assign": (["site_id", "plan"], None, "coverage", {}),
    "assign2": (["site_id", "plan"], None, "coverage", {}),
    "plans": (["plan", "area", "every"], None, "coverage", {"every": "boolean"}),
}


def _declared(kind: str, id: str, fields: Mapping[str, JsonValue]) -> Descriptor:
    entry: dict[str, JsonValue] = {"status": "imported", "by": BY, "at": AT}
    curation: dict[str, JsonValue] = {"/label": entry}
    curation.update({f"/fields/{name}": entry for name in fields})
    written = {
        "kind": kind,
        "id": id,
        "version": 1,
        "label": id,
        "fields": dict(fields),
        "curation": curation,
    }
    return _DESCRIPTORS.validate_python(written)


def _coverage(relationship: str, assignment: str) -> Descriptor:
    return _declared(
        "coverage",
        "cov:" + relationship.removeprefix("rel:"),
        {
            "relationship": relationship,
            "parents": {
                "assignment": {
                    "table": assignment,
                    "parent_columns": {"site_id": "site_id"},
                    "group_column": "plan",
                },
                "groups": {
                    "table": "plans",
                    "group_column": "plan",
                    "scope_columns": {"area": "area"},
                    "covers_all_column": "every",
                },
            },
        },
    )


class PlotsImporter:
    """Reads ``plots.json``: each table's rows."""

    def import_source(self, source: ConfinedPath, options: ImportOptions) -> ImportResult:
        [entry] = [entry for entry in options.reader.files(source) if entry.name == "plots.json"]
        assert entry.path is not None
        given = json.loads(options.reader.read(entry.path, options.limits.import_bytes))
        descriptors = [_declared("dataset", "dataset", {"name": "Plots"})]
        sources: dict[str, TypedSource] = {}
        layouts: dict[str, Layout] = {}
        for table, (columns, key, role, datatypes) in TABLES.items():
            fields: dict[str, JsonValue] = {"role": role}
            if key is not None:
                fields["primary_key"] = list(key)
            descriptors.append(_declared("table", table, fields))
            for column in columns:
                datatype = datatypes.get(column, "string")
                descriptors.append(_declared("column", f"{table}.{column}", {"datatype": datatype}))
            sources[table] = TypedSource(tuple(columns), tuple(tuple(row) for row in given[table]))
            layouts[table] = Layout(table, tuple((c, c) for c in columns))
        for child in ("visits", "notes"):
            descriptors.append(
                _declared(
                    "relationship",
                    f"rel:{child}.site_id",
                    {
                        "child_table": child,
                        "child_columns": ["site_id"],
                        "parent_table": "sites",
                        "parent_columns": ["site_id"],
                        "cardinality": "many-to-one",
                    },
                )
            )
        descriptors += [_coverage(VISITS, "assign"), _coverage(NOTES, "assign2")]
        return ImportResult(sources, layouts, descriptors)


REGISTRY = PackRegistry(
    [
        Pack(
            manifest=PackManifest(
                id="plots", version="1.0.0", results_version=1, requires_core=">=0.0.1"
            ),
            importer=PlotsImporter(),
        )
    ],
    core_version=aibi.__version__,
)


def plots(sites: int, assign: int, plans: int, assign2: int = 5) -> dict[str, Any]:
    """A plots release of these sizes: plans p0, p1, … cover areas a and b in turn; assignment
    rows name the sites and plans in turn (the gate refuses a site or plan that is not there, so
    there are none without a site or a plan)."""

    def assigned(rows: int) -> list[list[str]]:
        if not sites or not plans:
            return []
        return [[f"s{n % sites}", f"p{n % plans}"] for n in range(rows)]

    return {
        "sites": [[f"s{n}"] for n in range(sites)],
        "visits": [[f"v{n}", f"s{n % sites}", "ab"[n % 2]] for n in range(4 if sites else 0)],
        "notes": [[f"n{n}", f"s{n % sites}", "ab"[n % 2]] for n in range(4 if sites else 0)],
        "assign": assigned(assign),
        "assign2": assigned(assign2),
        "plans": [[f"p{n}", "ab"[n % 2], n == 4] for n in range(plans)],
    }


def publish(world: World, dataset: str, given: Mapping[str, Any], setting: int | None) -> None:
    world.publish(
        dataset, {"plots.json": json.dumps(given).encode()}, registry=REGISTRY, pack="plots"
    )
    if setting is not None:
        disclosed(world, dataset, setting)


def disclosed(world: World, dataset: str, setting: int) -> None:
    world.curate(dataset, _setting(setting))


def _setting(setting: int) -> dict[str, JsonValue]:
    return {
        "op": "set",
        "descriptor": "dataset",
        "pointer": "/fields/disclosure",
        "value": {"min_cell_count": setting, "allow_row_ids": True},
    }


class Recording(Workers):
    """Workers that record every statement they are given and refuse to run it; with ``real``,
    that run it."""

    def __init__(self, *, real: bool = False) -> None:
        super().__init__(QueryLimits())
        self.statements: list[str] = []
        self.real = real

    def run(
        self, paths: Sequence[str], queries: Sequence[Query], *, ends: float | None = None
    ) -> list[Rows]:
        self.statements += [query.sql for query in queries]
        if self.real:
            return super().run(paths, queries, ends=ends)
        raise QueryRefused(
            Refusal(code=RefusalCode.LIMIT_EXCEEDED, path=None, message=[text("Recorded")])
        )


def statements(
    world: World, tool: str, document: Mapping[str, Any], *, floor: int | None, real: bool = False
) -> tuple[str, ...]:
    workers = Recording(real=real)
    catalog = Catalog(world.store, workers=workers, floor=floor, registry=REGISTRY)
    call(catalog, BY_NAME[tool], json.dumps({"document": dict(document)}).encode())
    assert workers.statements
    return tuple(workers.statements)


def document(
    dataset: str, cohorts: Mapping[str, Sequence[Any]] | None = None, **extra: Any
) -> dict[str, Any]:
    given = cohorts if cohorts is not None else {"c": [EXISTS]}
    return {
        "aibi": "1",
        "dataset": dataset,
        "unit": "sites",
        "cohorts": {name: {"all": list(clauses)} for name, clauses in given.items()},
        **extra,
    }


EXISTS = {
    "kind": "exists",
    "table": "visits",
    "where": [{"kind": "value", "column": "visits.area", "values": ["a"]}],
}
NOTED = {"kind": "exists", "table": "notes"}
VIEW = {
    "analysis": "compare.existence",
    "cohorts": ["c", "d"],
    "params": {"predicates": [EXISTS, NOTED]},
}


@dataclass(frozen=True)
class Case:
    name: str
    floor: int | None
    setting: int | None
    published: int | None = None
    """For a draft: the setting of its latest published release (D275)."""

    @property
    def k(self) -> int:
        return max(v for v in (self.floor, self.setting, self.published) if v is not None)


CASES = [
    Case("floor above the setting", floor=3, setting=2),
    Case("setting above the floor", floor=2, setting=3),
    Case("a draft under its published setting", floor=2, setting=2, published=3),
]
OTHERS = 5
"""The rows of every table but the hidden one: at or above every case's k, so served."""


def terms(k: int, weight: int, base: int) -> list[int]:
    """The budgets at which a hidden count would decide (m3): the term at each count from 0 to
    k, where an unclamped count would flip the path, and one below the term at k."""
    return sorted({*(base + weight * count for count in range(k + 1)), base + weight * k - 1})


def _release(world: World, case: Case, dataset: str, given: Mapping[str, Any]) -> str:
    if case.published is None:
        publish(world, dataset, given, case.setting)
        return dataset
    publish(world, dataset, given, case.published)
    opened = world.open(dataset)
    world.change(dataset, opened, opened.draft, _setting(cast_int(case.setting)))
    return f"{dataset}@draft"


def cast_int(value: int | None) -> int:
    assert value is not None
    return value


@pytest.mark.parametrize("case", CASES, ids=[case.name for case in CASES])
def test_no_hidden_count_changes_a_statement(
    world: World, monkeypatch: pytest.MonkeyPatch, case: Case
) -> None:
    """For each of the assignment, groups and parent tables, every count below k (none
    included) gives one statement text at every budget where an unclamped count would decide,
    through both tools that compile; at k the budget decides."""
    k = case.k
    weights = {"assign": 4, "plans": 3 + 1, "sites": 1}
    for hidden, weight in weights.items():
        texts: dict[tuple[str, int], dict[int, tuple[str, ...]]] = {}
        # With no site or no plan there is no assignment row (the gate refuses one that names
        # neither), so with either hidden both assignment tables are hidden too, at k - 1 rows.
        assigned = OTHERS if hidden == "assign" else k - 1
        clamped_assigned = OTHERS if hidden == "assign" else k
        base = 4 * clamped_assigned + OTHERS + 4 * OTHERS - weight * OTHERS
        if hidden == "assign":
            base = OTHERS + 4 * OTHERS
        for count in range(k):
            sizes = {"sites": OTHERS, "assign": assigned, "plans": OTHERS, "assign2": assigned}
            sizes[hidden] = count
            dataset = _release(world, case, f"d_{hidden}_{count}", plots(**sizes))
            asked = {
                "count_cohort": document(dataset),
                "run_analysis": document(dataset, {"c": [EXISTS], "d": [NOTED]}, views=[VIEW]),
            }
            for budget in terms(k, weight, base):
                monkeypatch.setattr(grouped, "ROW_BUDGET", budget)
                for tool, written in asked.items():
                    texts.setdefault((tool, budget), {})[count] = statements(
                        world, tool, written, floor=case.floor
                    )
        for (tool, budget), by_count in texts.items():
            assert len(set(by_count.values())) == 1, (hidden, tool, budget)
        clamped = base + weight * k
        for tool in ("count_cohort", "run_analysis"):
            assert texts[(tool, clamped)][0] != texts[(tool, clamped - 1)][0], (hidden, tool)


def test_two_datasets_of_different_k_clamp_each_by_its_own(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A document over two datasets, of k 2 and 4: the hidden table is the second's, whose
    manifest sorts first in one round and last in the other (the releases are published again
    until it does), so neither the first manifest's k nor the least serves."""
    for small_first in (True, False):
        found: dict[int, set[tuple[str, ...]]] = {}
        for count in range(4):
            small = large = ""
            for attempt in itertools.count():
                small, large = (
                    f"s{small_first:d}{count}x{attempt}",
                    f"l{small_first:d}{count}x{attempt}",
                )
                publish(world, small, plots(OTHERS, OTHERS, OTHERS), 2)
                publish(world, large, plots(OTHERS, count, OTHERS), 4)
                manifests = [world.store.latest(name).manifest for name in (small, large)]
                if (manifests[0] < manifests[1]) == small_first:
                    break
            asked = {
                "aibi": "1",
                "unit": "sites",
                "cohorts": {
                    "c": {"dataset": small, "all": [EXISTS]},
                    "d": {"dataset": large, "all": [EXISTS]},
                },
            }
            base = OTHERS + 4 * OTHERS
            for budget in terms(4, 4, base):
                monkeypatch.setattr(grouped, "ROW_BUDGET", budget)
                found.setdefault(budget, set()).add(
                    statements(world, "count_cohort", asked, floor=None)
                )
        assert all(len(texts) == 1 for texts in found.values()), small_first


def test_a_hidden_first_coverage_counts_as_k_toward_the_second(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two coverages in one compiler, the first (by id: notes) over a hidden assignment table:
    what it adds to the sum is its clamped term, so the second's path does not move with the
    hidden count either."""
    k = 3
    found: dict[int, set[tuple[str, ...]]] = {}
    for count in range(k):
        publish(world, f"two{count}", plots(OTHERS, OTHERS, OTHERS, assign2=count), k)
        asked = document(f"two{count}", {"c": [EXISTS, NOTED]})
        first = 4 * k + OTHERS + 4 * OTHERS
        second = 4 * OTHERS + OTHERS + 4 * OTHERS
        budgets = {first + second - 1, first + second, first - 1, first}
        budgets |= {4 * hidden + OTHERS + 4 * OTHERS + second for hidden in range(k)}
        for budget in sorted(budgets):
            monkeypatch.setattr(grouped, "ROW_BUDGET", budget)
            found.setdefault(budget, set()).add(
                statements(world, "count_cohort", asked, floor=None)
            )
    assert all(len(texts) == 1 for texts in found.values())


def test_a_release_without_statistics_is_clamped_alike(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A release built before statistics were kept (D270) serves no counts; its rows are clamped
    as any other release's, so its hidden counts change nothing either."""
    k = 3
    with monkeypatch.context() as patch:
        patch.setattr(store_build._Builder, "_statistics", lambda self: None)  # pyright: ignore[reportPrivateUsage]
        for count in range(k):
            publish(world, f"bare{count}", plots(OTHERS, count, OTHERS), k)
    found: dict[int, set[tuple[str, ...]]] = {}
    for count in range(k):
        assert world.store.manifest(world.store.latest(f"bare{count}").manifest).statistics is None
        base = OTHERS + 4 * OTHERS
        for budget in terms(k, 4, base):
            monkeypatch.setattr(grouped, "ROW_BUDGET", budget)
            found.setdefault(budget, set()).add(
                statements(world, "count_cohort", document(f"bare{count}"), floor=None)
            )
    assert all(len(texts) == 1 for texts in found.values())


def test_the_overlap_path_is_clamped_alike(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    """A view whose cohorts share units is refused with each shared part counted (§7.4), in a
    second run over the same sources: its statements do not move with a hidden count either."""
    k = 3
    found: dict[int, set[tuple[str, ...]]] = {}
    for count in range(k):
        publish(world, f"shared{count}", plots(OTHERS, count, OTHERS), k)
        asked = document(
            f"shared{count}",
            {"c": [EXISTS], "d": [EXISTS]},
            views=[{**VIEW, "params": {"predicates": [NOTED]}}],
        )
        base = OTHERS + 4 * OTHERS
        for budget in terms(k, 4, base):
            monkeypatch.setattr(grouped, "ROW_BUDGET", budget)
            texts = statements(world, "run_analysis", asked, floor=None, real=True)
            found.setdefault(budget, set()).add(texts[-1:])
    assert all(len(texts) == 1 for texts in found.values())


# --- The footer -----------------------------------------------------------------------------


def test_the_footer_counts_every_row_group(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A blob the store writes in several row groups: its rows are all of theirs, not the
    first's."""
    monkeypatch.setattr(parquet, "ROW_GROUP", 3)
    path = tmp_path / "t.parquet"
    path.write_bytes(parquet.write([parquet.Column("x", "int64", list(range(10)))]))
    assert pq.ParquetFile(path).metadata.num_row_groups == 4
    assert pq.ParquetFile(path).metadata.row_group(0).num_rows == 3
    assert parquet.footer(path) == (("x",), 10)


def _statistics_rows(world: World, manifest: str) -> dict[str, int]:
    found = world.store.manifest(manifest)
    assert found.statistics is not None
    decoded = statistics.decode(world.store.blobs.read(found.statistics))
    return {table: cast(int, entry["n_rows"]) for table, entry in decoded.items()}


def _footer_rows(world: World, manifest: str) -> dict[str, int | None]:
    return {table: source.n_rows for table, source in world.store.sources(manifest).items()}


def test_the_footer_rows_are_the_statistics_rows_of_every_table(world: World, orchard: Any) -> None:
    """Every table's footer rows equal its catalogue statistics' (D270): a pack's tables, a
    re-import's, whose unchanged table's statistics are carried (a reshaped table's:
    ``importers/test_reshaped.py``). An erasure writes no release: it withdraws those that
    hold the row, so no release has rows its footer and its statistics count apart."""
    publish(world, "plots", plots(7, 11, 5, assign2=2), None)
    manifest = world.store.latest("plots").manifest
    assert _footer_rows(world, manifest) == _statistics_rows(world, manifest)
    assert _footer_rows(world, manifest)["assign"] == 11
    world.publish("orchard", orchard(rows=30))
    first = world.store.latest("orchard").manifest
    assert _footer_rows(world, first) == _statistics_rows(world, first)
    world.reimport("orchard", orchard(rows=29, harvests=45))
    second = world.store.latest("orchard").manifest
    assert second != first
    assert _footer_rows(world, second) == _statistics_rows(world, second)
    assert _footer_rows(world, second)["harvests"] == 45
    erase(world.store, "orchard", "trees", ["tree30"], "operator:Ada")
    labels = {label.manifest: label.withdrawn for label in world.store.labels("orchard")}
    assert labels == {first: True, second: False}

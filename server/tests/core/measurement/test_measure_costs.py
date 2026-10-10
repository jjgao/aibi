"""Measures what a page load's calls cost (SPEC §11.1, §12.4, D422); **by hand**, never in the
suite or in CI (the ``measurement`` marker, which ``addopts`` deselects), from ``server/``:

    uv run pytest -m measurement tests/core/measurement -s --no-header -p no:cacheprovider

It records, and asserts nothing of, seconds; the only assertions are that every call answered
(a count of refusals is not a count) and, in the unmarked smoke test, that the recording works on
a tiny store. ``AIBI_MEASUREMENT_OUT``, a file, receives the lines as well as the output.

**Live counts** (``validate_document`` and ``count_cohort``, through ``catalog.tools.call`` with a
JSON body, as both transports call them: the body's parse and validation are in the time) for the
named documents, each on two stores of the non-biomedical orchard (SPEC P8: trees, which are the
units, and their harvests, the largest table, imported by the core's file importer):

- ``small``: 24 trees and 30 harvests (the fixture the catalogue tests use);
- ``large``: 100,000 trees and 200,000 harvests.

The documents: ``value`` (one cohort of one value leaf on a tree's height), ``exists`` (one cohort
of ``exists`` over the harvests, the largest table, with a value leaf on the weight) and ``six``
(six cohorts of both kinds and the whole table). Each is measured in three series of 20 calls:

- ``cold``: each call is the first on a store opened for it (a new ``Store`` and ``Catalog``: no
  descriptor, manifest or query in memory) and on a document nobody asked before (each trial's
  thresholds differ);
- ``distinct``: 20 documents, each new (its request and its SQL are new texts in the derivation
  log, D300), on one warm catalogue;
- ``repeated``: one document 20 times after one call of it (the log stores the texts once).

Each query runs in a child process of its own (``Workers``), so ``count_cohort`` always starts
one; the system's file cache is not cleared, so *cold* means the server's own state, not the
disk's. ``validate_document`` reads no row.

**Listings** (``describe_dataset``, ``list_analyses`` with a dataset, ``resources/list``) on
releases of 50, 200 and 1,000 tables (1,000 is ``import_tables``) of 40 columns, imported from CSV
files of a few rows, for the core's registry alone and for the registry of packs at the caps
(1,000 analyses, 4,096 requirements, each of its own ``min`` over every requirement shape, with
predicates that hold, the adversary of D420's index), five calls each; ``describe_dataset`` gives
its first page of ``DEFAULT_DESCRIBED_COLUMNS`` columns. ``list_analyses`` without a dataset and
``list_leaf_kinds`` (at 1,000 leaf kinds) are added; the time is the catalogue method's, in the
calling thread: the loop's time to write the body is ``tests/core/api/measure_served.py``'s.

No domain: trees, harvests, tables and columns are letters and numbers."""

import json
import os
import re
import statistics
import time
import tomllib
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from tests.core.analyses.measure_applicable import at_cap, distinct
from tests.core.api.served_builders import pack_of, spread

import aibi
from aibi.core.catalog.service import Catalog
from aibi.core.catalog.tools import BY_NAME, call
from aibi.core.engine.worker import Workers
from aibi.core.importers.confine import Confinement
from aibi.core.importers.run import import_dataset
from aibi.core.schema.catalog import (
    DEFAULT_DESCRIBED_COLUMNS,
    DescribeDataset,
    ListAnalyses,
    ListLeafKinds,
)
from aibi.core.schema.cohorts import CohortCounts
from aibi.core.schema.limits import ImportLimits, QueryLimits
from aibi.core.schema.output import Output
from aibi.core.schema.pack_api import ImportOptions, PackRegistry
from aibi.core.store.store import Store

ADA = "operator:Ada"
REPEATS = 20
VARIETIES = ("apple", "pear", "plum")


# --- The stores --------------------------------------------------------------------------------


def orchard_files(trees: int, harvests: int) -> dict[str, bytes]:
    """Trees (a variety, a planting date, a height in metres, tags) and harvests (a weight in
    kilograms, a decimal, and a grade), written from each row's number."""
    rows = ["tree_id,variety,planted,height_m,tags"]
    for n in range(1, trees + 1):
        height = f"{1 + (n % 400) * 0.05:g}"
        tags = ("old;tall", "old", "young;tall", "young")[n % 4]
        planted = f"2010-{1 + n % 12:02d}-{1 + n % 28:02d}"
        rows.append(f"tree{n},{VARIETIES[n % 3]},{planted},{height},{tags}")
    crops = ["harvest_id,tree_id,kg,grade"]
    for n in range(1, harvests + 1):
        crops.append(f"h{n},tree{1 + n % trees},{10 + n % 9 + (n % 7) / 10:g},{'AB'[n % 2]}")
    return {
        "trees.csv": ("\n".join(rows) + "\n").encode(),
        "harvests.csv": ("\n".join(crops) + "\n").encode(),
    }


def wide_files(tables: int, columns: int = 40, rows: int = 4) -> dict[str, bytes]:
    """``tables`` tables of ``columns`` columns of ``rows`` rows: a key and then, in turn, an
    integer, a decimal, text, a date, a category of three values and a yes or no."""
    found: dict[str, bytes] = {}
    for t in range(tables):
        lines = [",".join(f"c{c:02d}" for c in range(columns))]
        for r in range(rows):
            cells = [f"k{t}-{r}"]
            for c in range(1, columns):
                cells.append(
                    (
                        str(r + c),
                        f"{r + c / 8:g}",
                        f"text {t} {r} {c}",
                        f"2020-{1 + c % 12:02d}-{1 + r % 28:02d}",
                        ("a", "b", "c")[(r + c) % 3],
                        ("yes", "no")[(r + c) % 2],
                    )[c % 6]
                )
            lines.append(",".join(cells))
        found[f"t{t:05d}.csv"] = ("\n".join(lines) + "\n").encode()
    return found


def clock() -> Callable[[], datetime]:
    ticks = iter(range(10**9))
    start = datetime(2026, 1, 1, tzinfo=UTC)
    return lambda: start + timedelta(seconds=next(ticks))


def published(root: Path, dataset: str, files: dict[str, bytes]) -> Path:
    """A store in ``root / "store"`` holding ``dataset``, imported by the core's file importer
    from ``files``; the store is closed again."""
    imports = root / "imports" / dataset
    imports.mkdir(parents=True)
    for name, content in files.items():
        (imports / name).write_bytes(content)
    confinement = Confinement.of(root / "imports")
    store = Store(root / "store", clock=clock())
    try:
        options = ImportOptions(
            dataset=dataset, reader=confinement, limits=ImportLimits(), at=store.now()
        )
        import_dataset(store, confinement.confine(imports), options, ADA)
    finally:
        store.close()
    return root / "store"


# --- The recording -------------------------------------------------------------------------------


@dataclass(frozen=True)
class Series:
    what: str
    seconds: tuple[float, ...]

    def line(self) -> str:
        found = sorted(self.seconds)
        return (
            f"{self.what}: median {statistics.median(found) * 1000:.1f} ms, "
            f"range {found[0] * 1000:.1f}-{found[-1] * 1000:.1f} ms, n={len(found)}"
        )


Lines = list[str]


def record(lines: Lines, series: Series) -> Series:
    lines.append(series.line())
    print(series.line(), flush=True)
    return series


def timed(run: Callable[[], object]) -> float:
    started = time.perf_counter()
    run()
    return time.perf_counter() - started


# --- Live counts --------------------------------------------------------------------------------


def _value(i: int, op: str, base: float) -> dict[str, Any]:
    return {
        "kind": "value",
        "column": "trees.height_m",
        "range": {op: round(base + i / 1000, 3)},
    }


def _heavy(i: int, op: str, base: float) -> dict[str, Any]:
    return {
        "kind": "exists",
        "table": "harvests",
        "where": [
            {
                "kind": "value",
                "column": "harvests.kg",
                "range": {op: round(base + i / 1000, 3)},
            }
        ],
    }


def document(name: str, i: int) -> dict[str, Any]:
    """The named document, its thresholds moved by ``i`` so that no two are alike."""
    cohorts: dict[str, list[dict[str, Any]]]
    if name == "value":
        cohorts = {"tall": [_value(i, "gte", 5)]}
    elif name == "exists":
        cohorts = {"heavy": [_heavy(i, "gte", 15)]}
    elif name == "six":
        cohorts = {
            "tall": [_value(i, "gte", 5)],
            "short": [_value(i, "lt", 3)],
            "heavy": [_heavy(i, "gte", 15)],
            "light": [_heavy(i, "lt", 12)],
            "apples": [{"kind": "value", "column": "trees.variety", "values": ["apple"]}],
            "all": [],
        }
    else:
        raise ValueError(name)
    return {
        "aibi": "1",
        "dataset": "orchard",
        "unit": "trees",
        "cohorts": {key: {"all": clauses} for key, clauses in cohorts.items()},
    }


DOCUMENTS = ("value", "exists", "six")
TOOLS = ("validate_document", "count_cohort")


def asked(catalog: Catalog, tool: str, written: dict[str, Any]) -> Output:
    found = call(catalog, BY_NAME[tool], json.dumps({"document": written}).encode())
    assert not isinstance(found, list), found
    return found


def _cold(path: Path, workers: Workers, tool: str, name: str, i: int) -> float:
    """One call on a store opened for it, of a document nobody asked."""
    store = Store(path, clock=clock())
    try:
        written = document(name, i)
        catalog = Catalog(store, workers=workers)
        return timed(lambda: asked(catalog, tool, written))
    finally:
        store.close()


def _new(catalog: Catalog, tool: str, name: str, i: int) -> float:
    """One call of a document nobody asked, on a catalogue."""
    written = document(name, i)
    return timed(lambda: asked(catalog, tool, written))


def _warm(
    path: Path, workers: Workers, tool: str, name: str, repeats: int
) -> tuple[Series, Series]:
    """Distinct documents, and one document again and again, on one warm catalogue."""
    store = Store(path, clock=clock())
    try:
        catalog = Catalog(store, workers=workers)
        asked(catalog, tool, document(name, 10_000))  # warms the catalogue
        fresh = tuple(_new(catalog, tool, name, 20_000 + i) for i in range(repeats))
        same = document(name, 30_000)
        asked(catalog, tool, same)
        again = tuple(timed(lambda: asked(catalog, tool, same)) for _ in range(repeats))
        return Series("warm, distinct documents", fresh), Series("warm, repeated document", again)
    finally:
        store.close()


def units_of(path: Path, workers: Workers) -> int:
    """The units the store at ``path`` counts: the whole table's count of the ``six`` document."""
    store = Store(path, clock=clock())
    try:
        found = asked(Catalog(store, workers=workers), "count_cohort", document("six", 0))
        assert isinstance(found, CohortCounts)
        everyone = next(n for n in found.counts if n.cohort.data == "all")
        denominator = everyone.count.size.denominator
        assert denominator is not None
        return int(denominator)
    finally:
        store.close()


def counts_on(path: Path, shape: str, units: int, repeats: int = REPEATS) -> Lines:
    """The three series of every tool and document on the store at ``path``, which has
    ``units`` units (checked: the shape stated is the shape measured)."""
    lines: Lines = []
    workers = Workers(QueryLimits())
    assert units_of(path, workers) == units
    for tool in TOOLS:
        for name in DOCUMENTS:
            label = f"{tool}, {name}, {shape}"
            cold = tuple(_cold(path, workers, tool, name, i) for i in range(repeats))
            record(lines, Series(f"{label}, cold", cold))
            for series in _warm(path, workers, tool, name, repeats):
                record(lines, Series(f"{label}, {series.what}", series.seconds))
    return lines


# --- Listings at the large shapes ---------------------------------------------------------------


def _registry(catalog_of: str) -> PackRegistry | None:
    if catalog_of == "core only":
        return None
    if catalog_of == "packs at the caps":
        return at_cap(distinct, True)
    return PackRegistry(
        [pack_of(spec) for spec in spread(1000, kinds=True)], core_version=aibi.__version__
    )


def _series(name: str, run: Callable[[], object], repeats: int) -> Series:
    run()
    return Series(name, tuple(timed(run) for _ in range(repeats)))


def _on_release(path: Path, tables: int, registry_name: str, repeats: int) -> list[Series]:
    store = Store(path, clock=clock())
    try:
        catalog = Catalog(store, registry=_registry(registry_name))
        shape = f"{tables} tables x 40 columns, {registry_name}"
        described = DescribeDataset(dataset="wide")
        listed = ListAnalyses(dataset="wide")
        return [
            _series(
                f"describe_dataset (first {DEFAULT_DESCRIBED_COLUMNS:,} columns), {shape}",
                lambda: catalog.describe_dataset(described),
                repeats,
            ),
            _series(
                f"list_analyses with a dataset, {shape}",
                lambda: catalog.list_analyses(listed),
                repeats,
            ),
            _series(f"resources/list, {shape}", catalog.resources, repeats),
        ]
    finally:
        store.close()


def _of_registry(path: Path, repeats: int) -> list[Series]:
    """The listings that read no release."""
    store = Store(path, clock=clock())
    try:
        analyses = Catalog(store, registry=_registry("packs at the caps"))
        kinds = Catalog(store, registry=_registry("1,000 leaf kinds"))
        return [
            _series(
                "list_analyses, no dataset, packs at the caps",
                lambda: analyses.list_analyses(ListAnalyses()),
                repeats,
            ),
            _series(
                "list_leaf_kinds, 1,000 leaf kinds",
                lambda: kinds.list_leaf_kinds(ListLeafKinds()),
                repeats,
            ),
        ]
    finally:
        store.close()


def listings_on(path: Path, tables: int, repeats: int = 5) -> Lines:
    """The listings on the release at ``path`` (of ``tables`` tables) for the core's registry
    alone and for the packs at the caps; ``list_analyses`` without a dataset and
    ``list_leaf_kinds`` read no release, so they are measured beside, once for each release the
    run builds (three times in all), and their figures are of the registry alone."""
    lines: Lines = []
    found = [
        *_on_release(path, tables, "core only", repeats),
        *_on_release(path, tables, "packs at the caps", repeats),
        *_of_registry(path, repeats),
    ]
    for series in found:
        record(lines, series)
    return lines


# --- Running -----------------------------------------------------------------------------------


def shape_of(trees: int, harvests: int) -> str:
    return f"{trees:,} trees ({trees:,} units) and {harvests:,} harvests"


def finish(lines: Sequence[str]) -> None:
    target = os.environ.get("AIBI_MEASUREMENT_OUT")
    if target:
        Path(target).write_text("\n".join(lines) + "\n", encoding="utf-8")


@pytest.mark.measurement
def test_measure_live_counts(tmp_path: Path) -> None:
    lines: Lines = []
    for trees, harvests, shape in ((24, 30, "small"), (100_000, 200_000, "large")):
        print(f"-- {shape} store: {shape_of(trees, harvests)}", flush=True)
        root = tmp_path / shape
        root.mkdir()
        path = published(root, "orchard", orchard_files(trees, harvests))
        lines += counts_on(path, f"{shape} store", trees)
    finish(lines)


@pytest.mark.measurement
@pytest.mark.parametrize("tables", [50, 200, 1_000])
def test_measure_listings_at_the_large_shapes(tmp_path: Path, tables: int) -> None:
    path = published(tmp_path, "wide", wide_files(tables))
    finish(listings_on(path, tables))


# --- Not measured: that the recording works -----------------------------------------------------


def test_the_recording_runs_on_tiny_stores(tmp_path: Path) -> None:
    """Every call of the measurement answers (a refusal would fail ``asked``) on tiny stores,
    with few repeats, so that the code does not rot between runs by hand."""
    (tmp_path / "a").mkdir()
    lines = counts_on(published(tmp_path / "a", "orchard", orchard_files(24, 30)), "tiny", 24, 1)
    assert len(lines) == len(TOOLS) * len(DOCUMENTS) * 3
    assert all("median" in line and "n=1" in line for line in lines)
    (tmp_path / "b").mkdir()
    found = listings_on(published(tmp_path / "b", "wide", wide_files(3)), 3, repeats=1)
    assert len(found) == 2 * 3 + 2


def test_the_measurement_is_marked_and_addopts_deselects_it() -> None:
    """The marker is declared (``--strict-markers``), ``addopts`` deselects it and the
    determinism tests' marker alike, and each measuring test carries it: by hand only (D422)."""
    configured = tomllib.loads((Path(__file__).parents[3] / "pyproject.toml").read_text("utf-8"))
    options = configured["tool"]["pytest"]["ini_options"]
    assert any(line.startswith("measurement:") for line in options["markers"])
    given = options["addopts"]
    expression = given[given.index("-m") + 1]
    found = re.fullmatch(r"not (\w+)((?: and not \w+)*)", expression)
    assert found, expression
    excluded = {found.group(1), *re.findall(r"and not (\w+)", found.group(2))}
    assert {"million", "measurement"} <= excluded
    assert "databases" not in excluded
    for measuring in (test_measure_live_counts, test_measure_listings_at_the_large_shapes):
        marks = [mark.name for mark in getattr(measuring, "pytestmark")]  # noqa: B009
        assert "measurement" in marks


def test_a_call_the_server_refuses_is_not_recorded(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    path = published(tmp_path / "a", "orchard", orchard_files(24, 30))
    store = Store(path, clock=clock())
    try:
        catalog = Catalog(store, workers=Workers(QueryLimits()))
        written = document("value", 0)
        written["unit"] = "nowhere"
        with pytest.raises(AssertionError):
            asked(catalog, "count_cohort", written)
    finally:
        store.close()
    with pytest.raises(AssertionError):
        counts_on(path, "wrong", 25, 1)


def test_the_named_documents_are_what_they_say_and_each_trial_is_new() -> None:
    sizes = {"value": 1, "exists": 1, "six": 6}
    for name, cohorts in sizes.items():
        found = document(name, 0)
        assert len(found["cohorts"]) == cohorts
        assert found["dataset"] == "orchard"
        assert found["unit"] == "trees"
        texts = {json.dumps(document(name, i), sort_keys=True) for i in range(REPEATS * 3)}
        assert len(texts) == REPEATS * 3, "every trial's document is its own"
    assert "harvests" in json.dumps(document("exists", 0))
    assert "exists" not in json.dumps(document("value", 0))
    assert json.dumps(document("six", 0)).count('"exists"') == 2
    assert set(DOCUMENTS) == set(sizes)
    assert TOOLS == ("validate_document", "count_cohort")
    assert REPEATS == 20
    with pytest.raises(ValueError, match="other"):
        document("other", 0)

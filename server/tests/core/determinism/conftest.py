"""The thread-count determinism tests' world (SPEC §9.3, D372): an orchard of a million trees, the
harvests of most of them and the weighings of a third of those, published once for the module,
and catalogues over it whose query workers differ only in their DuckDB threads.

- The rows are written from each row's number, so every run builds the same release. One tree in
  seven has no harvest (a closed relation with no rows, §6.5), one in eleven has three more, written
  after all the others and weighing 0.1, 0.2 and 0.3, whose sum depends on its order, one harvest
  in thirteen has no weight, and ``ring`` holds about 200,000 distinct numbers and ``block``
  127 categories it does not declare. The weighings'
  coverage lists every other harvest (``weighed_harvests``) and is scoped to grades A and B, so a
  harvest outside the list is ``NOT_COVERED``, which only the ``assessed`` lift drops: a question
  from a tree through its harvests to their weighings depends on its lift (two down steps, §6.5).
- It is published by a test-only pack's importer: the core's file importer reads a sample of the
  first 3,000 trees, the curation is written into its descriptors as ``imported`` entries (the
  fields it inferred keep their ``proposed`` statuses), and the full files are the raw snapshots,
  so the million rows are never inferred cell by cell. No catalogue that queries it registers that
  pack; the one that runs the pack analysis registers only ``echoes``.
- Table blobs are written with row groups of 2^17 rows, not the store's 2^20: DuckDB scans a
  Parquet file's row groups in parallel, and a table of fewer rows than a row group is scanned by
  one thread whatever the count (D373). ``row_groups`` gives each table blob's.

Test modules can't import one another (``--import-mode=importlib``), so the helpers are given as
fixtures."""

import itertools
import json
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq
import pytest
from pydantic import JsonValue, TypeAdapter

import aibi
from aibi.core.catalog.service import Catalog
from aibi.core.catalog.tools import BY_NAME, call
from aibi.core.engine.worker import Workers
from aibi.core.importers.confine import Confinement
from aibi.core.importers.files import FileImporter
from aibi.core.importers.run import import_dataset
from aibi.core.schema.descriptors import AnalysisDescriptor, Descriptor
from aibi.core.schema.limits import ImportLimits, QueryLimits
from aibi.core.schema.output import Output
from aibi.core.schema.pack_api import (
    AnalysisInputs,
    ConfinedPath,
    ImportOptions,
    ImportResult,
    Pack,
    PackManifest,
    PackRegistry,
)
from aibi.core.schema.refusals import Refusal
from aibi.core.store import parquet
from aibi.core.store.sources import TextSource
from aibi.core.store.store import Store

TREES = 1_000_000
SAMPLE = 3_000
ROW_GROUP = 1 << 17
ADA = "operator:Ada"
BY = "importer:fixture@1.0.0"
SOILS = ("sand", "loam", "clay")
GRADES = ("C", "B", "A")
MEMORY = 8 << 30
"""The workers' ``query_memory``: the crossing of sixteen predicates needs 4 GiB at one thread and
more than 4 GiB at four (6 GiB ran it), so the tests give room above the 2 GiB default (D373)."""
SECONDS = 300
LIMITS = ImportLimits(import_cells=100_000_000)
"""The import's limits: the full files hold more cells than the default ``import_cells``
(20,000,000), which bounds a pack's result as it does the core's importers' (D400)."""


BLOCKS = 127
"""How many values ``block``, a category with no declared values, holds: many enough that DuckDB
gives its groups in an order that differs across threads, which the server must sort (§9.3)."""
LATE = ("0.1", "0.2", "0.3")
"""The late harvests' weights, whose sum as doubles depends on its order (0.1 + 0.2 + 0.3 is not
0.3 + 0.2 + 0.1), so a pooled sum in the order the rows arrive would differ across threads."""


def late_harvests(trees: int) -> range:
    """The trees, one in eleven, that have three harvests more, written after all the others (so
    in other row groups than their first ones, which the order of a sum's terms does not need)."""
    return range(11, trees + 1, 11)


def orchard_files(trees: int) -> dict[str, bytes]:
    """The orchard's CSV files, written from each row's number; the trees of the late harvests
    have three more, written after every other harvest."""
    rows = ["tree_id,variety,months,fell,since,girth,ring,grafted,soil,tags,block"]
    crops = ["harvest_id,tree_id,kg,grade"]
    weighed = ["weighing_id,harvest_id,grams,scale"]
    listed = ["harvest_id"]
    harvest = 0
    for n in range(1, trees + 1):
        felled = (n * 11) % 37 + 1
        fell = "NA" if n % 11 == 0 else ("yes" if n % 4 else "no")
        since = 0 if n % 3 == 0 else min(n % 5, felled - 1)
        girth = "NA" if n % 9 == 0 else f"{20 + (n * 13) % 40}.{n % 7}"
        ring = f"{(n * 7919) % 200_003 / 1000:.3f}"
        grafted = "yes" if n % 5 < 2 else "no"
        tags = ("old;tall", "old", "young;tall", "young")[n % 4]
        rows.append(
            f"tree{n},{('apple', 'pear', 'plum')[n % 3]},{felled},{fell},{since},{girth},{ring},"
            f"{grafted},{SOILS[(n // 3) % 3]},{tags},b{(n * 31) % BLOCKS}"
        )
        for k in range(0 if n % 7 == 0 else 1 + n % 2):
            harvest += 1
            kg = "NA" if harvest % 13 == 0 else f"{10 + (n + k) % 9}.{(n * 7 + k) % 10}"
            crops.append(f"h{harvest},tree{n},{kg},{GRADES[(n + 2 * k) % 3]}")
            if harvest % 2:
                listed.append(f"h{harvest}")
            if harvest % 3 == 0:
                weighed.append(f"w{harvest},h{harvest},{100 + (harvest * 37) % 900},{'ab'[k]}")
    for n in late_harvests(trees):
        for k in range(3):
            harvest += 1
            kg = LATE[(n + k) % 3]
            crops.append(f"h{harvest},tree{n},{kg},{GRADES[(n + k) % 3]}")
            if harvest % 2:
                listed.append(f"h{harvest}")
            if harvest % 3 == 0:
                weighed.append(f"w{harvest},h{harvest},{100 + (harvest * 37) % 900},{'ab'[k % 2]}")
    return {
        name: ("\n".join(lines) + "\n").encode()
        for name, lines in (
            ("trees.csv", rows),
            ("harvests.csv", crops),
            ("weighings.csv", weighed),
            ("weighed_harvests.csv", listed),
        )
    }


def _ordered(values: tuple[str, ...]) -> dict[str, JsonValue]:
    """Permissible values in their declared order."""
    return {"values": [{"value": value} for value in values], "ordered": True}


SET: list[tuple[str, str, JsonValue]] = [
    *(
        (f"trees.{name}", pointer, value)
        for name in ("months", "since")
        for pointer, value in (("/fields/datatype", "time_offset"), ("/fields/units", "mo"))
    ),
    ("trees.soil", "/fields/datatype", "category"),
    ("trees.block", "/fields/datatype", "category"),
    ("trees.soil", "/fields/permissible_values", _ordered(SOILS)),
    ("harvests.grade", "/fields/datatype", "category"),
    ("harvests.grade", "/fields/permissible_values", _ordered(GRADES)),
    ("weighed_harvests", "/fields/role", "coverage"),
]
PUT: list[dict[str, Any]] = [
    {
        "kind": "endpoint",
        "id": "ep:felled",
        "label": "Felled",
        "fields": {
            "table": "trees",
            "time_column": "months",
            "status_column": "fell",
            "event_coding": {"event": [True], "censored": [False]},
            "entry": {"column": "since"},
        },
    },
    {
        "kind": "coverage",
        "id": "cov:harvests.tree_id",
        "label": "Every tree's harvests are recorded",
        "fields": {"relationship": "rel:harvests.tree_id", "parents": "all"},
    },
    {
        "kind": "coverage",
        "id": "cov:weighings.harvest_id",
        "label": "Listed harvests of grades A and B are weighed",
        "fields": {
            "relationship": "rel:weighings.harvest_id",
            "parents": {
                "table": "weighed_harvests",
                "parent_columns": {"harvest_id": "harvest_id"},
            },
            "parent_scope": {"kind": "value", "column": "harvests.grade", "values": ["A", "B"]},
        },
    },
]
_DESCRIPTOR: TypeAdapter[Descriptor] = TypeAdapter(Descriptor)


class SampledImporter:
    """The core's file importer over a sample of the files, its descriptors curated as
    ``imported`` entries, and the full files as the raw snapshots."""

    def __init__(self, full: Mapping[str, bytes], sample: Path) -> None:
        self._full = full
        self._sample = Confinement.of(sample.parent)
        self._path = sample

    def import_source(self, source: ConfinedPath, options: ImportOptions) -> ImportResult:
        sampled = replace(options, reader=self._sample)
        found = FileImporter().import_source(self._sample.confine(self._path), sampled)
        entry = {"status": "imported", "by": BY, "at": options.at}
        written = {d.id: d.model_dump(mode="json") for d in found.descriptors}
        for descriptor, pointer, value in SET:
            written[descriptor]["fields"][pointer.removeprefix("/fields/")] = value
            written[descriptor]["curation"][pointer] = {**entry, "inferred": value}
        for given in PUT:
            curation = {"/label": {**entry, "inferred": given["label"]}} | {
                f"/fields/{name}": {**entry, "inferred": value}
                for name, value in given["fields"].items()
            }
            written[given["id"]] = {**given, "version": 1, "curation": curation}
        descriptors = [_DESCRIPTOR.validate_python(d) for d in written.values()]
        sources = {table: TextSource(self._full[f"{table}.csv"]) for table in found.sources}
        return replace(found, sources=sources, descriptors=descriptors)


def _clock() -> Callable[[], datetime]:
    """A clock that ticks a second at each reading, from 2026-01-01."""
    ticks = itertools.count()
    start = datetime(2026, 1, 1, tzinfo=UTC)
    return lambda: start + timedelta(seconds=next(ticks))


@dataclass(frozen=True)
class Orchard:
    store: Store
    manifest: str

    def catalog(self, threads: int, registry: PackRegistry | None = None) -> Catalog:
        """A catalogue whose query workers run DuckDB on ``threads`` threads, and which neither
        gives nor fills the result cache (D376), so that each call runs its queries: a hit would
        compare one answer with another's copy."""
        limits = QueryLimits(query_seconds=SECONDS, query_memory=MEMORY, query_threads=threads)
        return Catalog(self.store, workers=Workers(limits), registry=registry, cache=False)

    def rows(self) -> dict[str, int]:
        """Each table blob's rows."""
        return {
            table: pq.ParquetFile(source.path).metadata.num_rows
            for table, source in self.store.sources(self.manifest).items()
        }

    def categories(self, column: str) -> tuple[bool, int]:
        """Whether a category column declares its values, and how many distinct ones its table
        blob holds."""
        table, name = column.split(".")
        [found] = [d for d in self.store.descriptors(self.manifest) if d.id == column]
        declared = getattr(found.fields, "permissible_values", None) is not None
        values = pq.read_table(self.store.sources(self.manifest)[table].path, columns=[name])
        return declared, len(set(values.column(name).to_pylist()))

    def row_groups(self) -> dict[str, int]:
        """Each table blob's Parquet row groups."""
        return {
            table: pq.ParquetFile(source.path).metadata.num_row_groups
            for table, source in self.store.sources(self.manifest).items()
        }


@pytest.fixture(scope="module")
def orchard(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Orchard]:
    """The orchard's release, published once for the module (module docstring), its store
    closed when the module is done."""
    root = tmp_path_factory.mktemp("million")
    for folder in ("imports/orchard", "sample/orchard"):
        (root / folder).mkdir(parents=True)
    for name, content in orchard_files(SAMPLE).items():
        (root / "sample" / "orchard" / name).write_bytes(content)
    (root / "imports" / "orchard" / "trees.csv").write_bytes(b"tree_id\ntree1\n")
    importer = SampledImporter(orchard_files(TREES), root / "sample" / "orchard")
    registry = PackRegistry(
        [
            Pack(
                manifest=PackManifest(
                    id="fixture", version="1.0.0", results_version=1, requires_core=">=0.0.1"
                ),
                importer=importer,
            )
        ],
        core_version=aibi.__version__,
    )
    store = Store(root / "data", clock=_clock())
    try:
        confinement = Confinement.of(root / "imports")
        options = ImportOptions(
            dataset="orchard", reader=confinement, limits=LIMITS, at=store.now()
        )
        with pytest.MonkeyPatch.context() as patched:
            patched.setattr(parquet, "ROW_GROUP", ROW_GROUP)
            published = import_dataset(
                store,
                confinement.confine(root / "imports" / "orchard"),
                options,
                ADA,
                registry=registry,
                pack="fixture",
            )
        yield Orchard(store, published.manifest)
    finally:
        store.close()


# --- The echoes pack: an analysis handed its inputs --------------------------------------------


def echo_entry() -> AnalysisDescriptor:
    """``echoes.echo``'s entry: cohorts, a column role and the unit's endpoint (D341, D352)."""
    return AnalysisDescriptor.model_validate(
        {
            "kind": "analysis",
            "id": "echoes.echo",
            "version": "1.0.0",
            "label": "Echo",
            "fields": {
                "requires": [
                    {"role": "cohorts", "min": 1, "max": 6},
                    {"role": "measure", "kind": "column", "min": 1},
                    {"role": "time", "kind": "endpoint", "on": "unit"},
                ],
                "params": {"type": "object"},
                "returns": {"type": "object"},
                "methods": {},
                "assumptions": [],
                "uses_reference": False,
                "assumes_independent_groups": False,
                "cross_dataset": None,
                "caveats": [],
            },
        }
    )


class Echo:
    """An analysis whose values are its inputs: each position's values, exclusions and endpoint
    rows, as listed."""

    @property
    def entry(self) -> AnalysisDescriptor:
        return echo_entry()

    def run(self, inputs: AnalysisInputs) -> Mapping[str, JsonValue]:
        return {
            "positions": [
                {
                    "units": position.units,
                    "values": [list(column) for column in position.values],
                    "excluded": [[list(r) for r in column] for column in position.excluded],
                    "endpoints": [
                        [None if row is None else list(row) for row in rows]
                        for rows in position.endpoints
                    ],
                }
                for position in inputs.positions
            ],
            "view": {"overlapping": inputs.overlapping},
        }


@pytest.fixture(scope="module")
def echoes() -> PackRegistry:
    """A registry of the ``echoes`` pack alone, whose analysis is ``Echo``."""
    pack = Pack(
        manifest=PackManifest(
            id="echoes", version="1.0.0", results_version=1, requires_core=">=0.0.1"
        ),
        analyses=[Echo()],
    )
    return PackRegistry([pack], core_version=aibi.__version__)


Answer = Callable[[Catalog, str, Mapping[str, Any]], Output | list[Refusal]]


@pytest.fixture(scope="module")
def answer() -> Answer:
    """A tool's answer to a document, as both transports give it."""

    def answered(
        catalog: Catalog, name: str, document: Mapping[str, Any]
    ) -> Output | list[Refusal]:
        body = json.dumps({"document": dict(document)}).encode()
        return call(catalog, BY_NAME[name], body)

    return answered

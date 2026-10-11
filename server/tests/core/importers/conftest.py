"""The importer tests' builders and a test-only pack.

- ``make_xlsx`` writes a minimal SpreadsheetML workbook with inline strings, and ``make_ods`` a
  minimal OpenDocument one, so that edge cases and bombs need no binary fixture; ``error_cell``
  is an error cell in one.
- ``make_zip`` writes a zip archive of ``entry`` values, which can be symbolic links or
  encrypted; ``declare_size`` makes one lie about its size.
- ``roots`` gives an import directory and a directory outside it; ``importing`` builds a release
  of a path from them in a fresh store and publishes it with the store's own step, and
  ``lifecycle`` imports and re-imports through the operator's functions, which publish (§12.3).
- ``library_variant`` copies the lending library of ``fixtures/library`` (or its next export,
  ``fixtures/library_next``) into the import directory, changed as a test needs.
- ``birds`` is a pack of bird surveys (SPEC §10.1: extension points are tested with a
  non-biomedical pack): a survey file gives sites, checklists and the counts of species on each
  checklist, with a grouped coverage (a checklist follows a protocol, which lists the species it
  counts, or counts them all). Its dataset extension names the survey's protocol, from a fixed
  list; its validator refuses a release without one; its proposer proposes a definition for each
  table that has none.

Test modules can't import one another (``--import-mode=importlib``), so the helpers are given as
fixtures, as in the engine and store tests.
"""

import itertools
import json
import shutil
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from pydantic import JsonValue, TypeAdapter
from tests.core.importers.builders import (
    Entry,
    Error,
    Sheets,
    build_lie,
    build_ods,
    build_xlsx,
    build_zip,
)

import aibi
from aibi.core.importers.confine import Confinement
from aibi.core.importers.run import (
    Imported,
    Published,
    build_import,
    import_dataset,
    reimport_dataset,
)
from aibi.core.schema.descriptors import Descriptor, TableDescriptor
from aibi.core.schema.limits import ImportLimits
from aibi.core.schema.output import Segment, text
from aibi.core.schema.pack_api import (
    ConfinedPath,
    ImportOptions,
    ImportResult,
    ImportSource,
    Pack,
    PackManifest,
    PackRegistry,
    Proposal,
    ReleaseView,
    Reshaped,
    cell_digest,
)
from aibi.core.schema.refusals import Refusal
from aibi.core.store.build import Layout
from aibi.core.store.sources import SourceValue, TypedSource
from aibi.core.store.store import Store

AT = "2026-01-01T00:00:00Z"
FIXTURES = Path(__file__).resolve().parents[4] / "fixtures"


@pytest.fixture(name="make_xlsx")
def make_xlsx_fixture() -> Callable[[Sheets], bytes]:
    return build_xlsx


@pytest.fixture(name="make_ods")
def make_ods_fixture() -> Callable[[Sheets], bytes]:
    return build_ods


@pytest.fixture(name="make_zip")
def make_zip_fixture() -> Callable[[Sequence[Entry]], bytes]:
    return build_zip


@pytest.fixture(name="declare_size")
def declare_size_fixture() -> Callable[[bytes, str, int], bytes]:
    return build_lie


@pytest.fixture(name="entry")
def entry_fixture() -> type[Entry]:
    return Entry


@pytest.fixture(name="error_cell")
def error_cell_fixture() -> type[Error]:
    return Error


# --- Importing ---------------------------------------------------------------------------------


@dataclass
class Roots:
    inside: Path
    outside: Path
    confinement: Confinement
    stores: Path

    def write(self, name: str, content: bytes) -> Path:
        path = self.inside / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def options(self, dataset: str = "d", **given: Any) -> ImportOptions:
        return ImportOptions(
            dataset=dataset,
            reader=given.pop("reader", self.confinement),
            limits=given.pop("limits", ImportLimits()),
            at=AT,
            **given,
        )


@pytest.fixture
def roots(tmp_path: Path) -> Roots:
    inside, outside, stores = tmp_path / "imports", tmp_path / "elsewhere", tmp_path / "stores"
    for directory in (inside, outside, stores):
        directory.mkdir()
    return Roots(inside, outside, Confinement.of(inside), stores)


def _clock() -> Callable[[], datetime]:
    ticks = itertools.count()
    start = datetime(2026, 1, 1, tzinfo=UTC)
    return lambda: start + timedelta(seconds=next(ticks))


@pytest.fixture
def store(roots: Roots) -> Iterator[Store]:
    opened = Store(roots.stores / "data", clock=_clock())
    yield opened
    opened.close()


Importing = Callable[..., Imported]


@pytest.fixture
def importing(roots: Roots, store: Store) -> Importing:
    """Import a path of the import directory into the store, published as ``@1``."""

    def run(
        path: Path,
        *,
        dataset: str = "d",
        registry: PackRegistry | None = None,
        pack: str | None = None,
        publish: bool = True,
        **options: Any,
    ) -> Imported:
        with store.pin() as pin:
            imported = build_import(
                store,
                pin,
                roots.confinement.confine(path),
                roots.options(dataset, **options),
                registry=registry,
                pack=pack,
            )
            if publish:
                store.publish(dataset, imported.built.manifest.hash, "operator:ada")
        return imported

    return run


@dataclass
class Lifecycle:
    roots: Roots
    store: Store

    def run(
        self,
        operation: Callable[..., Published],
        path: Path,
        dataset: str,
        by: str,
        registry: PackRegistry | None,
        pack: str | None,
        options: dict[str, Any],
    ) -> Published:
        return operation(
            self.store,
            self.roots.confinement.confine(path),
            self.roots.options(dataset, **options),
            by,
            registry=registry,
            pack=pack,
        )

    def import_(
        self,
        path: Path,
        *,
        dataset: str = "d",
        by: str = "operator:ada",
        registry: PackRegistry | None = None,
        pack: str | None = None,
        **options: Any,
    ) -> Published:
        return self.run(import_dataset, path, dataset, by, registry, pack, options)

    def reimport(
        self,
        path: Path,
        *,
        dataset: str = "d",
        by: str = "operator:ada",
        registry: PackRegistry | None = None,
        pack: str | None = None,
        **options: Any,
    ) -> Published:
        return self.run(reimport_dataset, path, dataset, by, registry, pack, options)


@pytest.fixture
def lifecycle(roots: Roots, store: Store) -> Lifecycle:
    return Lifecycle(roots, store)


# --- The lending library -------------------------------------------------------------------------


def copy_library(
    roots: Roots,
    name: str,
    *,
    source: str = "library",
    without: Sequence[str] = (),
    files: Mapping[str, bytes] | None = None,
) -> Path:
    """The library of ``fixtures/<source>`` in the import directory as ``name``, without the
    files ``without`` and with ``files`` written over its own; ``formats/`` left out."""
    target = roots.inside / name
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(FIXTURES / source, target, ignore=shutil.ignore_patterns("formats", "*.md"))
    for gone in without:
        (target / gone).unlink()
    for file, content in (files or {}).items():
        (target / file).write_bytes(content)
    return target


@pytest.fixture
def library_variant(roots: Roots) -> Callable[..., Path]:
    def make(name: str = "library", **given: Any) -> Path:
        return copy_library(roots, name, **given)

    return make


# --- The birds pack ----------------------------------------------------------------------------

_DESCRIPTORS: TypeAdapter[Descriptor] = TypeAdapter(Descriptor)
BY = "importer:birds@1.0.0"


def _declared(
    kind: str,
    id: str,
    fields: Mapping[str, JsonValue],
    extensions: Mapping[str, Any] | None = None,
    *,
    proposed: Sequence[str] = (),
    by: str = BY,
) -> Descriptor:
    """A descriptor whose fields the importer declares as ``imported``, but for those named in
    ``proposed``."""
    entry: dict[str, JsonValue] = {"status": "imported", "by": by, "at": AT}
    guess: dict[str, JsonValue] = {"status": "proposed", "by": by, "at": AT}
    curation: dict[str, JsonValue] = {"/label": entry}
    curation.update({f"/fields/{name}": guess if name in proposed else entry for name in fields})
    for pack, members in (extensions or {}).items():
        curation.update({f"/extensions/{pack}/{member}": entry for member in members})
    written: dict[str, Any] = {
        "kind": kind,
        "id": id,
        "version": 1,
        "label": id,
        "fields": dict(fields),
        "curation": curation,
    }
    if extensions:
        written["extensions"] = dict(extensions)
    return _DESCRIPTORS.validate_python(written)


_TABLES: dict[str, tuple[list[str], list[str] | None, str, dict[str, str]]] = {
    "sites": (["site_id", "habitat"], ["site_id"], "entity", {"habitat": "category"}),
    "checklists": (
        ["checklist_id", "site_id", "protocol", "observed"],
        ["checklist_id"],
        "event",
        {"protocol": "category", "observed": "date"},
    ),
    "counts": (
        ["checklist_id", "species", "count"],
        ["checklist_id", "species"],
        "measurement",
        {"species": "category", "count": "integer"},
    ),
    "assignments": (["checklist_id", "protocol"], None, "coverage", {"protocol": "category"}),
    "protocol_species": (
        ["protocol", "species", "all_species"],
        None,
        "coverage",
        {"protocol": "category", "species": "category", "all_species": "boolean"},
    ),
}


def _survey_rows(survey: Mapping[str, Any]) -> dict[str, list[tuple[SourceValue, ...]]]:
    return {
        "sites": [(s["site_id"], s["habitat"]) for s in survey["sites"]],
        "checklists": [
            (c["checklist_id"], c["site_id"], c["protocol"], date.fromisoformat(c["observed"]))
            for c in survey["checklists"]
        ],
        "counts": [(n["checklist_id"], n["species"], n["count"]) for n in survey["counts"]],
        "assignments": [(c["checklist_id"], c["protocol"]) for c in survey["checklists"]],
        "protocol_species": [
            (p["protocol"], p["species"], p.get("all_species", False)) for p in survey["protocols"]
        ],
    }


@dataclass
class BirdImporter:
    """Reads a survey file, only through ``options.reader`` (SPEC §14). Its keys are declared,
    or proposed when the survey says ``keys_proposed``."""

    reads: list[str] = field(default_factory=list[str])

    def import_source(self, source: ConfinedPath, options: ImportOptions) -> ImportResult:
        survey = json.loads(options.reader.read(source, options.limits.import_bytes))
        self.reads.append(str(source))
        extensions = {"birds": {"protocol": survey["protocol"]}} if "protocol" in survey else None
        descriptors: list[Descriptor] = [
            _declared(
                "dataset",
                "dataset",
                {"name": "Bird survey", "packs": survey.get("packs", ["birds"])},
                extensions,
            )
        ]
        sources: dict[str, TypedSource] = {}
        layouts: dict[str, Layout] = {}
        rows = _survey_rows(survey)
        for table, (columns, key, role, datatypes) in _TABLES.items():
            fields: dict[str, JsonValue] = {"role": role}
            if key is not None:
                fields["primary_key"] = list(key)
            proposed = ["primary_key"] if survey.get("keys_proposed") else []
            descriptors.append(_declared("table", table, fields, proposed=proposed))
            for column in columns:
                datatype = datatypes.get(column, "string")
                descriptors.append(_declared("column", f"{table}.{column}", {"datatype": datatype}))
            sources[table] = TypedSource(tuple(columns), tuple(rows[table]))
            layouts[table] = Layout(table, tuple((c, c) for c in columns))
        for child, column, parent in (
            ("checklists", "site_id", "sites"),
            ("counts", "checklist_id", "checklists"),
        ):
            descriptors.append(
                _declared(
                    "relationship",
                    f"rel:{child}.{column}",
                    {
                        "child_table": child,
                        "child_columns": [column],
                        "parent_table": parent,
                        "parent_columns": [column],
                        "cardinality": "many-to-one",
                    },
                )
            )
        descriptors.append(
            _declared(
                "coverage",
                "cov:counts.checklist_id",
                {
                    "relationship": "rel:counts.checklist_id",
                    "parents": {
                        "assignment": {
                            "table": "assignments",
                            "parent_columns": {"checklist_id": "checklist_id"},
                            "group_column": "protocol",
                        },
                        "groups": {
                            "table": "protocol_species",
                            "group_column": "protocol",
                            "scope_columns": {"species": "species"},
                            "covers_all_column": "all_species",
                        },
                    },
                },
            )
        )
        return ImportResult(sources, layouts, descriptors)


class BirdValidator:
    def validate_source(self, source: ImportSource, result: ImportResult) -> Sequence[Refusal]:
        counts = result.sources["counts"]
        assert isinstance(counts, TypedSource)
        negative = sum(1 for row in counts.rows if isinstance(row[2], int) and row[2] < 0)
        if not negative:
            return []
        message: list[Segment] = [text(f"{negative} counts are negative")]
        return [Refusal(code="birds.NEGATIVE_COUNT", path=None, message=message)]

    def validate_descriptors(self, release: ReleaseView) -> Sequence[Refusal]:
        dataset = release.descriptors["dataset"]
        if "birds" in dataset.extensions:
            return []
        message: list[Segment] = [text(f"Release @{release.label} has no survey protocol")]
        return [Refusal(code="birds.NO_PROTOCOL", path="/dataset/extensions", message=message)]


def propose_definitions(release: ReleaseView) -> Sequence[Proposal]:
    """A definition for each table that has none."""
    return [
        Proposal(
            descriptor.id,
            "/definition",
            f"The survey's {descriptor.id.replace('_', ' ')}",
            evidence="The birds pack defines every table it knows",
        )
        for descriptor in release.descriptors.values()
        if isinstance(descriptor, TableDescriptor) and descriptor.definition is None
    ]


@dataclass
class Birds:
    importer: BirdImporter
    registry: PackRegistry
    survey: Callable[..., dict[str, Any]]
    pack: Pack


def survey(**changes: Any) -> dict[str, Any]:
    written: dict[str, Any] = {
        "protocol": "stationary",
        "sites": [{"site_id": "s1", "habitat": "wood"}, {"site_id": "s2", "habitat": "marsh"}],
        "checklists": [
            {"checklist_id": "c1", "site_id": "s1", "protocol": "all", "observed": "2026-05-01"},
            {"checklist_id": "c2", "site_id": "s2", "protocol": "waders", "observed": "2026-05-02"},
        ],
        "counts": [
            {"checklist_id": "c1", "species": "wren", "count": 3},
            {"checklist_id": "c2", "species": "curlew", "count": 2},
            {"checklist_id": "c2", "species": "wren", "count": 1},
        ],
        "protocols": [
            {"protocol": "all", "species": "wren", "all_species": True},
            {"protocol": "waders", "species": "curlew"},
            {"protocol": "waders", "species": "redshank"},
        ],
    }
    written.update(changes)
    return written


@pytest.fixture
def birds() -> Birds:
    importer = BirdImporter()
    pack = Pack(
        manifest=PackManifest(
            id="birds", version="1.0.0", results_version=1, requires_core=">=0.0.1"
        ),
        extension_schemas={
            "dataset": {
                "type": "object",
                "properties": {"protocol": {"enum": ["stationary", "travelling"]}},
                "additionalProperties": False,
            }
        },
        importer=importer,
        validator=BirdValidator(),
        proposer=propose_definitions,
    )
    return Birds(importer, PackRegistry([pack], core_version=aibi.__version__), survey, pack)


# --- The grid pack -------------------------------------------------------------------------------

GRID_BY = "importer:grid@1.0.0"
GRADES = ["A", "B", "C"]


def _grid_declared(kind: str, id: str, fields: Mapping[str, JsonValue]) -> Descriptor:
    return _declared(kind, id, fields, by=GRID_BY)


def _tsv(content: bytes) -> list[list[str]]:
    lines = content.decode().split("\n")
    return [line.split("\t") for line in lines if line]


@dataclass
class GridImporter:
    """A pack of orchard harvests (SPEC §10.1, D401): ``trees.tsv``; ``grades.tsv``, a matrix of
    one row per season and one column per tree, whose cells are a grade, ``0`` (no harvest) or
    empty (not assessed), unpivoted into ``yields(tree_id, season, grade)``; and ``pickings.tsv``,
    a tree's pickers in two slots, exploded into ``pickings(tree_id, picker)``, whose coverage
    lists the trees the file held, so that a tree it never named is not covered (round 1 of
    #77's review, m6: an explode declares nothing, but a unit its source never held is no
    observation either).

    The unpivot keeps a row for each grade and drops ``0`` as absent and empty cells, declaring
    both (``Reshaped``). Its coverage lists the cells the matrix held, a grade or ``0``: each tree
    is assigned the group of the seasons its column held, and a tree whose column held none is
    not assigned. ``change`` may alter the result, so a test can play a dishonest pack."""

    change: Callable[[ImportResult], ImportResult] | None = None

    def import_source(self, source: ConfinedPath, options: ImportOptions) -> ImportResult:
        files = {entry.name: entry for entry in options.reader.files(source)}
        read = {
            name: options.reader.read(entry.path, options.limits.import_bytes)
            for name, entry in files.items()
            if entry.path is not None
        }
        trees = _tsv(read["trees.tsv"])[1:]
        grid = _tsv(read["grades.tsv"])
        header, rows = grid[0][1:], grid[1:]
        yields: list[tuple[SourceValue, ...]] = []
        held: dict[str, list[str]] = {tree: [] for tree in header}
        dropped: list[tuple[str, str]] = []
        empty = 0
        for row in rows:
            season, cells = row[0], row[1:] + [""] * (len(header) - len(row) + 1)
            for tree, cell in zip(header, cells, strict=False):
                if cell == "":
                    empty += 1
                    continue
                held[tree].append(season)
                if cell == "0":
                    dropped.append((tree, season))
                else:
                    yields.append((tree, season, cell))
        groups: dict[tuple[str, ...], str] = {}
        for tree in header:
            seasons = tuple(sorted(held[tree]))
            if seasons and seasons not in groups:
                groups[seasons] = f"g{len(groups) + 1}"
        assigned = [(tree, groups[tuple(sorted(held[tree]))]) for tree in header if held[tree]]
        group_seasons = [(name, season) for seasons, name in groups.items() for season in seasons]
        pickings: list[tuple[SourceValue, ...]] = []
        for row in _tsv(read["pickings.tsv"])[1:]:
            tree, *slots = row
            pickings.extend((tree, picker) for picker in slots if picker)
        sources: dict[str, Any] = {
            "trees": TypedSource(("tree_id", "variety"), tuple(tuple(t) for t in trees)),
            "yields": TypedSource(("tree_id", "season", "grade"), tuple(yields)),
            "tree_groups": TypedSource(("tree_id", "group"), tuple(assigned)),
            "group_seasons": TypedSource(("group", "season"), tuple(group_seasons)),
            "pickings": TypedSource(("tree_id", "picker"), tuple(pickings)),
            "picked": TypedSource(
                ("tree_id",), tuple(sorted({(str(row[0]),) for row in pickings}))
            ),
        }
        layouts = {
            name: Layout(name, tuple((c, c) for c in source.columns))
            for name, source in sources.items()
        }
        pack_source: JsonValue = {"kind": "pack", "name": "yields", "original_name": "grades.tsv"}
        descriptors: list[Descriptor] = [
            _grid_declared("dataset", "dataset", {"name": "Orchard", "packs": []}),
            _grid_declared("table", "trees", {"role": "entity", "primary_key": ["tree_id"]}),
            _grid_declared("column", "trees.tree_id", {"datatype": "string"}),
            _grid_declared("column", "trees.variety", {"datatype": "category"}),
            _grid_declared(
                "table",
                "yields",
                {
                    "role": "measurement",
                    "primary_key": ["tree_id", "season"],
                    "source": pack_source,
                },
            ),
            _grid_declared("column", "yields.tree_id", {"datatype": "string"}),
            _grid_declared("column", "yields.season", {"datatype": "string"}),
            _grid_declared(
                "column",
                "yields.grade",
                {
                    "datatype": "category",
                    "permissible_values": {
                        "values": [{"value": grade} for grade in GRADES],
                        "ordered": True,
                    },
                },
            ),
            _grid_declared("table", "tree_groups", {"role": "coverage"}),
            _grid_declared("column", "tree_groups.tree_id", {"datatype": "string"}),
            _grid_declared("column", "tree_groups.group", {"datatype": "string"}),
            _grid_declared("table", "group_seasons", {"role": "coverage"}),
            _grid_declared("column", "group_seasons.group", {"datatype": "string"}),
            _grid_declared("column", "group_seasons.season", {"datatype": "string"}),
            _grid_declared("table", "pickings", {"role": "measurement"}),
            _grid_declared("column", "pickings.tree_id", {"datatype": "string"}),
            _grid_declared("column", "pickings.picker", {"datatype": "category"}),
            _grid_declared("table", "picked", {"role": "coverage"}),
            _grid_declared("column", "picked.tree_id", {"datatype": "string"}),
        ]
        for child in ("yields", "pickings"):
            descriptors.append(
                _grid_declared(
                    "relationship",
                    f"rel:{child}.tree_id",
                    {
                        "child_table": child,
                        "child_columns": ["tree_id"],
                        "parent_table": "trees",
                        "parent_columns": ["tree_id"],
                        "cardinality": "many-to-one",
                    },
                )
            )
        descriptors.append(
            _grid_declared(
                "coverage",
                "cov:yields.tree_id",
                {
                    "relationship": "rel:yields.tree_id",
                    "parents": {
                        "assignment": {
                            "table": "tree_groups",
                            "parent_columns": {"tree_id": "tree_id"},
                            "group_column": "group",
                        },
                        "groups": {
                            "table": "group_seasons",
                            "group_column": "group",
                            "scope_columns": {"season": "season"},
                        },
                    },
                },
            )
        )
        descriptors.append(
            _grid_declared(
                "coverage",
                "cov:pickings.tree_id",
                {
                    "relationship": "rel:pickings.tree_id",
                    "parents": {"table": "picked", "parent_columns": {"tree_id": "tree_id"}},
                },
            )
        )
        reshaped = {
            "yields": Reshaped(
                column="grade",
                absent=("0",),
                dropped=len(dropped),
                digest=cell_digest(dropped),
                empty=empty,
            )
        }
        result = ImportResult(sources, layouts, descriptors, reshaped=reshaped)
        return result if self.change is None else self.change(result)


@dataclass
class Grid:
    importer: GridImporter
    registry: PackRegistry
    pack: Pack
    write: Callable[..., Path]

    def with_change(self, change: Callable[[ImportResult], ImportResult]) -> PackRegistry:
        """A registry whose grid importer changes its result with ``change``."""
        pack = Pack(manifest=self.pack.manifest, importer=GridImporter(change))
        return PackRegistry([pack], core_version=aibi.__version__)


def write_orchard(
    roots: Roots,
    *,
    trees: Sequence[tuple[str, str]] = (("t1", "apple"), ("t2", "pear"), ("t3", "plum")),
    grades: Sequence[Sequence[str]] = (("autumn", "A", "0", ""), ("spring", "B", "0", "A")),
    header: Sequence[str] = ("t1", "t2", "t3"),
    pickings: Sequence[Sequence[str]] = (("t1", "ann", "bob"), ("t2", "ann", "")),
    name: str = "orchard",
) -> Path:
    """An orchard directory: by default t1 graded in both seasons, t2 ``0`` in both, and t3
    empty in autumn and graded A in spring."""
    folder = roots.inside / name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "trees.tsv").write_bytes(
        ("tree_id\tvariety\n" + "".join(f"{t}\t{v}\n" for t, v in trees)).encode()
    )
    lines = ["season\t" + "\t".join(header)] + ["\t".join(row) for row in grades]
    (folder / "grades.tsv").write_bytes(("\n".join(lines) + "\n").encode())
    (folder / "pickings.tsv").write_bytes(
        ("tree_id\tpicker_1\tpicker_2\n" + "".join("\t".join(r) + "\n" for r in pickings)).encode()
    )
    return folder


@pytest.fixture
def grid(roots: Roots) -> Grid:
    importer = GridImporter()
    pack = Pack(
        manifest=PackManifest(
            id="grid", version="1.0.0", results_version=1, requires_core=">=0.0.1"
        ),
        importer=importer,
    )
    registry = PackRegistry([pack], core_version=aibi.__version__)
    return Grid(importer, registry, pack, lambda **given: write_orchard(roots, **given))

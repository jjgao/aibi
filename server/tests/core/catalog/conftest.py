"""The catalogue tests' world: a store in ``tmp_path``, datasets imported into it by the core's
file importer, curated through sessions, and catalogues over it.

- ``orchard`` is a non-biomedical dataset (SPEC P8): trees, with a variety (a category), a
  planting date, a height in metres and a list of tags, and their harvests, with a weight and a
  grade. ``orchard_files`` writes it as CSV files; ``rows`` sets how many trees it has.
- ``world`` publishes datasets (``publish``), curates them through a session (``curate``, which
  opens one, applies edits and publishes), and makes catalogues (``catalog``) with a floor, a
  registry and model cards; ``tool`` runs a tool on a JSON body as both transports do.
- ``orchards`` is a test-only pack whose facet names the dataset's orchard region, from the
  dataset descriptor's extension, so that the catalogue facet extension point is exercised
  without a domain pack (§10.1).

Test modules can't import one another (``--import-mode=importlib``), so the helpers are given as
fixtures, as in the other test directories.
"""

import itertools
import json
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from pydantic import JsonValue

import aibi
from aibi.core.catalog.service import Catalog
from aibi.core.catalog.tools import BY_NAME, call
from aibi.core.importers.confine import Confinement
from aibi.core.importers.files import FileImporter
from aibi.core.importers.run import Published, import_dataset, reimport_dataset
from aibi.core.schema.curation import ChangeRequest
from aibi.core.schema.descriptors import DatasetDescriptor, ModelCardDescriptor
from aibi.core.schema.limits import ImportLimits
from aibi.core.schema.output import Output
from aibi.core.schema.pack_api import (
    ConfinedPath,
    ImportOptions,
    ImportResult,
    Pack,
    PackManifest,
    PackRegistry,
    ReleaseView,
)
from aibi.core.schema.refusals import Refusal
from aibi.core.store import sessions
from aibi.core.store.store import Store

ADA = "operator:Ada"
VARIETIES = ("apple", "pear", "plum")


def _clock() -> Callable[[], datetime]:
    ticks = itertools.count()
    start = datetime(2026, 1, 1, tzinfo=UTC)
    return lambda: start + timedelta(seconds=next(ticks))


def orchard_files(
    rows: int = 24, *, harvests: int = 30, unparsed: int = 0, sites: bool = False
) -> dict[str, bytes]:
    """Trees and their harvests, as CSV files; a tree's height is missing (``NA``) for every
    seventh tree, and the first ``unparsed`` harvests weigh ``heavy``, which does not parse.
    With ``sites``, trees stand on sites, which makes them an entity below another (D229), whose
    relationship's coverage is proposed."""
    trees = ["tree_id,variety,planted,height_m,tags" + (",site_id" if sites else "")]
    for n in range(1, rows + 1):
        height = "NA" if n % 7 == 0 else f"{1 + n * 0.25:g}"
        tags = ("old;tall", "old", "young;tall", "young")[n % 4]
        planted = f"2010-{1 + n % 12:02d}-{1 + n % 28:02d}"
        site = f",site{1 + n % 2}" if sites else ""
        trees.append(f"tree{n},{VARIETIES[n % 3]},{planted},{height},{tags}{site}")
    crops = ["harvest_id,tree_id,kg,grade"]
    for n in range(1, harvests + 1):
        weight = "heavy" if n <= unparsed else str(10 + n % 9)
        crops.append(f"h{n},tree{1 + n % rows},{weight},{'AB'[n % 2]}")
    files = {
        "trees.csv": ("\n".join(trees) + "\n").encode(),
        "harvests.csv": ("\n".join(crops) + "\n").encode(),
    }
    if sites:
        files["sites.csv"] = b"site_id,name\nsite1,North field\nsite2,South field\n"
    return files


@dataclass
class World:
    root: Path
    store: Store

    def write(self, dataset: str, files: Mapping[str, bytes]) -> Path:
        directory = self.root / "imports" / dataset
        directory.mkdir(parents=True, exist_ok=True)
        for existing in directory.iterdir():
            existing.unlink()
        for name, content in files.items():
            (directory / name).write_bytes(content)
        return directory

    def _source(self, dataset: str, files: Mapping[str, bytes]) -> tuple[ConfinedPath, Any]:
        directory = self.write(dataset, files)
        confinement = Confinement.of(self.root / "imports")
        return confinement.confine(directory), confinement

    def publish(
        self,
        dataset: str,
        files: Mapping[str, bytes],
        *,
        registry: PackRegistry | None = None,
        pack: str | None = None,
    ) -> Published:
        source, confinement = self._source(dataset, files)
        options = ImportOptions(
            dataset=dataset, reader=confinement, limits=ImportLimits(), at=self.store.now()
        )
        return import_dataset(self.store, source, options, ADA, registry=registry, pack=pack)

    def reimport(self, dataset: str, files: Mapping[str, bytes]) -> Published:
        source, confinement = self._source(dataset, files)
        options = ImportOptions(
            dataset=dataset, reader=confinement, limits=ImportLimits(), at=self.store.now()
        )
        return reimport_dataset(self.store, source, options, ADA)

    def open(self, dataset: str) -> sessions.Opened:
        return sessions.open_session(self.store, dataset, ADA)

    def change(
        self, dataset: str, opened: sessions.Opened, draft: str, *edits: Mapping[str, JsonValue]
    ) -> str:
        request = ChangeRequest.model_validate({"edits": list(edits)})
        return sessions.change(self.store, dataset, opened.handle, draft, request, ADA)

    def curate(self, dataset: str, *edits: Mapping[str, JsonValue]) -> int:
        """Open a session, apply ``edits`` as one change and publish; the label published."""
        opened = self.open(dataset)
        draft = self.change(dataset, opened, opened.draft, *edits)
        return sessions.publish(self.store, dataset, opened.handle, draft, ADA).label

    def catalog(
        self,
        *,
        floor: int | None = None,
        registry: PackRegistry | None = None,
        models: Sequence[ModelCardDescriptor] = (),
    ) -> Catalog:
        return Catalog(self.store, registry=registry, floor=floor, models=tuple(models))

    def tool(self, catalog: Catalog, name: str, body: JsonValue) -> Output | list[Refusal]:
        return call(catalog, BY_NAME[name], json.dumps(body).encode())


@pytest.fixture
def world(tmp_path: Path) -> Iterator[World]:
    (tmp_path / "imports").mkdir()
    found = World(tmp_path, Store(tmp_path / "data", clock=_clock()))
    yield found
    found.store.close()


@pytest.fixture
def orchard() -> Callable[..., dict[str, bytes]]:
    return orchard_files


# --- The orchards pack ----------------------------------------------------------------------------

ORCHARDS_BY = "importer:orchards@1.0.0"


class OrchardImporter:
    """The core's file importer, whose dataset lists the orchards pack and names its region."""

    def import_source(self, source: ConfinedPath, options: ImportOptions) -> ImportResult:
        result = FileImporter().import_source(source, options)
        found: list[Any] = []
        for descriptor in result.descriptors:
            if isinstance(descriptor, DatasetDescriptor):
                written: dict[str, Any] = descriptor.model_dump(mode="json")
                written["fields"]["packs"] = ["orchards"]
                written["extensions"] = {"orchards": {"region": "north"}}
                entry = {"status": "imported", "by": ORCHARDS_BY, "at": options.at}
                written["curation"]["/fields/packs"] = {**entry, "inferred": ["orchards"]}
                written["curation"]["/extensions/orchards/region"] = {**entry, "inferred": "north"}
                descriptor = DatasetDescriptor.model_validate(written)
            found.append(descriptor)
        return replace(result, descriptors=found)


def region(release: ReleaseView) -> Mapping[str, Sequence[str]]:
    dataset = release.descriptors["dataset"]
    named = dataset.extensions.get("orchards", {}).get("region")
    return {"region": [named]} if isinstance(named, str) else {}


def orchards_registry(facet: Callable[[ReleaseView], Any] = region) -> PackRegistry:
    pack = Pack(
        manifest=PackManifest(
            id="orchards", version="1.0.0", results_version=1, requires_core=">=0.0.1"
        ),
        extension_schemas={
            "dataset": {
                "type": "object",
                "properties": {"region": {"type": "string"}},
                "additionalProperties": False,
            }
        },
        importer=OrchardImporter(),
        facet=facet,
    )
    return PackRegistry([pack], core_version=aibi.__version__)


@pytest.fixture
def orchards() -> Callable[..., PackRegistry]:
    return orchards_registry

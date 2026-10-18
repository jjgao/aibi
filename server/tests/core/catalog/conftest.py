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

Test modules can't import one another (``--import-mode=importlib``) by their directory, so the
helpers are given as fixtures, as in the other test directories; ``world.py`` holds the orchard's
files and the ``World`` as a library, for a test of another directory (``tests.core.catalog.world``,
as ``tests.core.api.served_builders`` is imported).
"""

from collections.abc import Callable, Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from tests.core.catalog.world import World, orchard_files, ticking_clock

import aibi
from aibi.core.importers.files import FileImporter
from aibi.core.schema.descriptors import DatasetDescriptor
from aibi.core.schema.pack_api import (
    ConfinedPath,
    ImportOptions,
    ImportResult,
    Pack,
    PackManifest,
    PackRegistry,
    ReleaseView,
)
from aibi.core.store.store import Store


@pytest.fixture
def world(tmp_path: Path) -> Iterator[World]:
    (tmp_path / "imports").mkdir()
    found = World(tmp_path, Store(tmp_path / "data", clock=ticking_clock()))
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


def region(release: ReleaseView) -> dict[str, list[str] | tuple[str, ...]]:
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

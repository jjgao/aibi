"""The catalogue tests' world as a library: the orchard's CSV files and the ``World`` that
publishes them into a store in a directory, imported by the file importer and curated through
sessions. The fixtures of ``conftest.py`` give them to the tests of this directory; a test of
another directory that needs the world itself (the web client's orchard answers,
``analyses/test_openapi_results.py``) imports this module, which a conftest is not for (a conftest
is the framework's, and is not imported by a test).
"""

import itertools
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from pydantic import JsonValue

from aibi.core.catalog.service import Catalog
from aibi.core.catalog.tools import BY_NAME, call
from aibi.core.importers.confine import Confinement
from aibi.core.importers.run import Published, import_dataset, reimport_dataset
from aibi.core.schema.curation import ChangeRequest
from aibi.core.schema.descriptors import ModelCardDescriptor
from aibi.core.schema.limits import ImportLimits
from aibi.core.schema.output import Output
from aibi.core.schema.pack_api import ConfinedPath, ImportOptions, PackRegistry
from aibi.core.schema.refusals import Refusal
from aibi.core.store import sessions
from aibi.core.store.store import Store

ADA = "operator:Ada"
VARIETIES = ("apple", "pear", "plum")


def ticking_clock() -> Callable[[], datetime]:
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

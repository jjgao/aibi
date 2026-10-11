"""The oncology pack's tests' helpers.

The pack is imported lazily, inside fixtures and test bodies alone: a module-level import would
load it while pytest collects, before ``tests/core``'s own tests run in a combined run, and one
of those holds that the core's suite loads no pack. ``_unload_packs`` removes every
``aibi.packs`` module after each test, so that the order the tests run in changes nothing
(``test_lazy.py`` holds both rules).

Test modules can't import one another (``--import-mode=importlib``), so the helpers are given as
fixtures; they are small copies of the core tests' own, made from the core's public functions:

- ``onco`` is the pack's module, ``registry`` a registry of the pack alone, ``validators`` its
  ontology validators by system;
- ``extended`` gives a descriptor ``onco`` extension members, each with its curation entry, and
  ``refused`` the ``(code, path)`` of each refusal of the pack checks on a descriptor write;
- ``world`` is a store in ``tmp_path`` into which a directory is imported (``publish``), curated
  through a session (``curate``), and read through a catalogue (``catalog``); ``make_world``
  makes more, each with its own store and a clock that starts at the same time.
"""

import importlib
import itertools
import sys
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType

import pytest
from pydantic import JsonValue, TypeAdapter

import aibi
from aibi.core.catalog.service import Catalog
from aibi.core.importers.confine import Confinement
from aibi.core.importers.run import Published, import_dataset
from aibi.core.schema.curation import ChangeRequest
from aibi.core.schema.descriptors import Descriptor
from aibi.core.schema.limits import ImportLimits
from aibi.core.schema.pack_api import ImportOptions, PackRegistry
from aibi.core.store import sessions
from aibi.core.store.store import Store
from aibi.core.store.writes import check_writes

PACKAGE = "aibi.packs"
MODULE = "aibi.packs.onco"
ADA = "operator:Ada"
AT = "2026-01-01T00:00:00Z"
_ADAPTER: TypeAdapter[Descriptor] = TypeAdapter(Descriptor)


def packs_loaded() -> list[str]:
    return sorted(name for name in sys.modules if name == PACKAGE or name.startswith(PACKAGE + "."))


@pytest.fixture(autouse=True)
def _unload_packs() -> Iterator[None]:
    yield
    for name in packs_loaded():
        del sys.modules[name]


@pytest.fixture
def onco() -> ModuleType:
    return importlib.import_module(MODULE)


@pytest.fixture
def registry(onco: ModuleType) -> PackRegistry:
    return PackRegistry([onco.PACK], core_version=aibi.__version__)


@pytest.fixture
def validators() -> dict[str, Callable[[str], bool]]:
    ontology = importlib.import_module(MODULE + ".ontology")
    return dict(ontology.SYSTEMS)


Extended = Callable[[Descriptor, Mapping[str, JsonValue]], Descriptor]
Refused = Callable[[Sequence[Descriptor]], list[tuple[str, str | None]]]


@pytest.fixture
def extended() -> Extended:
    def extend(descriptor: Descriptor, members: Mapping[str, JsonValue]) -> Descriptor:
        written = descriptor.model_dump(mode="json")
        written["extensions"] = {"onco": dict(members)}
        for name in members:
            written["curation"][f"/extensions/onco/{name}"] = {
                "status": "asserted",
                "by": "operator:Ada",
                "at": AT,
            }
        return _ADAPTER.validate_python(written)

    return extend


@pytest.fixture
def refused(registry: PackRegistry) -> Refused:
    def found(descriptors: Sequence[Descriptor]) -> list[tuple[str, str | None]]:
        return [
            (str(refusal.code), refusal.path) for refusal in check_writes(descriptors, registry)
        ]

    return found


def _clock() -> Callable[[], datetime]:
    ticks = itertools.count()
    start = datetime(2026, 1, 1, tzinfo=UTC)
    return lambda: start + timedelta(seconds=next(ticks))


@dataclass
class World:
    root: Path
    store: Store

    def publish(
        self, dataset: str, directory: Path, registry: PackRegistry | None = None
    ) -> Published:
        confinement = Confinement.of(directory.parent)
        options = ImportOptions(
            dataset=dataset, reader=confinement, limits=ImportLimits(), at=self.store.now()
        )
        source = confinement.confine(directory)
        return import_dataset(self.store, source, options, ADA, registry=registry)

    def write(self, dataset: str, files: Mapping[str, bytes]) -> Path:
        directory = self.root / "imports" / dataset
        directory.mkdir(parents=True)
        for name, content in files.items():
            (directory / name).write_bytes(content)
        return directory

    def curate(self, dataset: str, registry: PackRegistry, *edits: Mapping[str, JsonValue]) -> int:
        opened = sessions.open_session(self.store, dataset, ADA)
        request = ChangeRequest.model_validate({"edits": list(edits)})
        draft = sessions.change(
            self.store, dataset, opened.handle, opened.draft, request, ADA, registry=registry
        )
        published = sessions.publish(
            self.store, dataset, opened.handle, draft, ADA, registry=registry
        )
        return published.label

    def catalog(self, registry: PackRegistry | None) -> Catalog:
        return Catalog(self.store, registry=registry)


MakeWorld = Callable[[str], World]


@pytest.fixture
def make_world(tmp_path: Path) -> Iterator[MakeWorld]:
    """A world in ``tmp_path / name``; each one's clock starts at the same time."""
    made: list[World] = []

    def make(name: str) -> World:
        root = tmp_path / name
        (root / "imports").mkdir(parents=True)
        found = World(root, Store(root / "data", clock=_clock()))
        made.append(found)
        return found

    yield make
    for found in made:
        found.store.close()


@pytest.fixture
def world(make_world: MakeWorld) -> World:
    return make_world("world")

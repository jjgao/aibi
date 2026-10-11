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
  makes more, each with its own store and a clock that starts at the same time;
- for study discovery (M4.2a-1a): ``make_study`` builds a directory from a tree whose entries are
  files (``bytes``), subdirectories (a mapping), and the ``kinds`` (symlinks, hard links, FIFOs,
  files of a given mode); ``base`` is the small study ``clinical_grid.BASE`` holds;
  ``discovery`` runs the pack's ``discover`` on a directory through a ``RecordingReader``, which
  records every listing and read and can stand a prepared refusal in for a read, or through the
  core's own importer checks (``through_core``).
"""

import importlib
import importlib.util
import itertools
import os
import sys
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import pytest
from pydantic import JsonValue, TypeAdapter

import aibi
from aibi.core.catalog.service import Catalog
from aibi.core.importers.checks import run_importer
from aibi.core.importers.confine import Confinement
from aibi.core.importers.errors import ImportRefused
from aibi.core.importers.run import Published, import_dataset
from aibi.core.schema.curation import ChangeRequest
from aibi.core.schema.descriptors import Descriptor
from aibi.core.schema.limits import ImportLimits
from aibi.core.schema.output import DataSegment, Segment, TextSegment
from aibi.core.schema.pack_api import (
    ConfinedPath,
    DirectoryEntry,
    ImportOptions,
    ImportResult,
    PackRegistry,
)
from aibi.core.schema.pack_api import Refused as PackRefused
from aibi.core.schema.refusals import Refusal
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


# --- Study discovery (M4.2a-1a) -------------------------------------------------------------------

HERE = Path(__file__).resolve().parent


@pytest.fixture(scope="session")
def grid() -> ModuleType:
    """``clinical_grid.py``, loaded by its path (test modules cannot import one another)."""
    spec = importlib.util.spec_from_file_location("clinical_grid", HERE / "clinical_grid.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def base(grid: ModuleType) -> dict[str, bytes]:
    """The small study that imports, by entry name: a copy each test may change."""
    return dict(grid.BASE)


@dataclass(frozen=True)
class Symlink:
    target: str


@dataclass(frozen=True)
class HardLink:
    of: str
    """The name of a file of the same directory."""


@dataclass(frozen=True)
class Fifo:
    pass


@dataclass(frozen=True)
class Mode:
    content: bytes
    mode: int


class Kinds:
    """The entries of a tree that are neither files (``bytes``) nor directories (a mapping)."""

    symlink = Symlink
    hard_link = HardLink
    fifo = Fifo
    mode = Mode


@pytest.fixture
def kinds() -> type[Kinds]:
    return Kinds


Tree = Mapping[str | bytes, object]


def _built(directory: Path, tree: Tree) -> None:
    """``tree`` written into ``directory``, hard links last; a name given as ``bytes`` is written
    as those bytes (a name that is not UTF-8)."""
    os.mkdir(directory)
    links: list[tuple[bytes, HardLink]] = []
    for given, node in tree.items():
        name = given if isinstance(given, bytes) else os.fsencode(given)
        path = os.path.join(os.fsencode(directory), name)
        if isinstance(node, bytes):
            with open(path, "wb") as file:
                file.write(node)
        elif isinstance(node, Mapping):
            _built(Path(os.fsdecode(path)), cast(Tree, node))
        elif isinstance(node, Symlink):
            os.symlink(os.fsencode(node.target), path)
        elif isinstance(node, HardLink):
            links.append((path, node))
        elif isinstance(node, Fifo):
            os.mkfifo(path)
        elif isinstance(node, Mode):
            with open(path, "wb") as file:
                file.write(node.content)
            os.chmod(path, node.mode)
        else:
            raise TypeError(f"not an entry of a tree: {node!r}")
    for path, link in links:
        os.link(os.path.join(os.fsencode(directory), os.fsencode(link.of)), path)


MakeStudy = Callable[..., Path]


@pytest.fixture
def make_study(tmp_path: Path) -> Iterator[MakeStudy]:
    """A directory built from a tree, in ``tmp_path / "imports"``; modes are restored after the
    test, so that its directory can be removed."""
    made: list[Path] = []
    counter = itertools.count()

    def make(tree: Tree, name: str | None = None) -> Path:
        directory = tmp_path / "imports" / (name or f"study{next(counter)}")
        directory.parent.mkdir(exist_ok=True)
        _built(directory, tree)
        made.append(directory)
        return directory

    yield make
    for directory in made:
        for path, _, files in os.walk(directory):
            for name in files:
                if not os.path.islink(os.path.join(path, name)):
                    os.chmod(os.path.join(path, name), 0o644)


class RecordingReader:
    """The import's reader, recording each listing (``listed``) and each read, by the entry's
    name and the limit asked (``reads``). ``most`` caps a read's limit as the core's reader caps
    it at the operator's ``import_bytes``; ``fail`` stands a prepared exception in for the read
    of a name, raised as that instance; ``swap`` replaces each named file by a new one after it
    is listed, so that the reader's own read refuses it (a real refusal that does not need a mode
    the root user reads anyway)."""

    def __init__(
        self,
        inner: Confinement,
        most: int | None = None,
        fail: Mapping[str, BaseException] | None = None,
        swap: Sequence[str] = (),
    ) -> None:
        self.inner = inner
        self.most = most
        self.fail = dict(fail or {})
        self.swap = tuple(swap)
        self.listed: list[str] = []
        self.reads: list[tuple[str, int]] = []
        self.raised: list[BaseException] = []

    def files(self, directory: ConfinedPath) -> Sequence[DirectoryEntry]:
        self.listed.append(directory.name)
        entries = self.inner.files(directory)
        for name in self.swap:
            path = Path(directory) / name
            swapped = path.with_name(name + ".swapped")
            swapped.write_bytes(path.read_bytes())
            swapped.replace(path)
        return entries

    def read(self, path: ConfinedPath, limit: int) -> bytes:
        self.reads.append((path.name, limit))
        if path.name in self.fail:
            raise self.fail[path.name]
        try:
            return self.inner.read(path, limit if self.most is None else min(limit, self.most))
        except ImportRefused as error:
            self.raised.append(error)
            raise

    def location(self, path: ConfinedPath) -> str:
        return self.inner.location(path)


def said(segments: Sequence[Segment]) -> str:
    """A message as text, each ``data`` segment between ``«`` and ``»``."""
    return "".join(
        segment.text if isinstance(segment, TextSegment) else f"«{segment.data}»"
        for segment in segments
    )


class Discovery:
    """The pack's ``discover``, run on a directory."""

    def __init__(self, study: ModuleType) -> None:
        self.study = study

    def reader(
        self,
        directory: Path,
        most: int | None = None,
        fail: Mapping[str, BaseException] | None = None,
        swap: Sequence[str] = (),
    ) -> RecordingReader:
        return RecordingReader(Confinement.of(directory.parent), most, fail, swap)

    def run(
        self,
        directory: Path,
        limits: ImportLimits | None = None,
        reader: RecordingReader | None = None,
    ) -> Any:
        """The ``Study`` ``discover`` finds; it raises what ``discover`` raises."""
        reader = reader or self.reader(directory)
        source = reader.inner.confine(directory)
        return self.study.discover(reader, source, limits or ImportLimits())

    def refusal(
        self,
        directory: Path,
        limits: ImportLimits | None = None,
        reader: RecordingReader | None = None,
    ) -> Refusal:
        """The one refusal ``discover`` refuses the study with, a ``Refused`` itself."""
        with pytest.raises(PackRefused) as raised:
            self.run(directory, limits, reader)
        assert type(raised.value) is PackRefused
        (refusal,) = raised.value.refusals
        return refusal

    def outcome(self, directory: Path, limits: ImportLimits | None = None) -> tuple[str, ...]:
        """``("ok", sample data, patient data)``, or ``(code, named entry, message)``."""
        try:
            found = self.run(directory, limits)
        except PackRefused as error:
            (refusal,) = error.refusals
            named = next((s.data for s in refusal.message if isinstance(s, DataSegment)), "")
            return (str(refusal.code), named, said(refusal.message))
        patient = found.patient.data.name if found.patient is not None else ""
        return ("ok", found.sample.data.name, patient)

    def through_core(self, directory: Path, import_bytes: int | None = None) -> ImportResult:
        """``discover`` run by the core's importer checks (``run_importer``), as an importer that
        returns no table and discover's notes: the reader is the core's, which caps a read at
        ``import_bytes``, and what the pack raises is read back as the core reads it. Raises
        ``ImportRefused``."""
        study = self.study
        reader = Confinement.of(directory.parent)
        limits = ImportLimits() if import_bytes is None else ImportLimits(import_bytes=import_bytes)

        class Importer:
            def import_source(self, source: ConfinedPath, options: ImportOptions) -> ImportResult:
                found = study.discover(options.reader, source, options.limits)
                return ImportResult({}, {}, [], notes=found.notes)

        options = ImportOptions(dataset="study", reader=reader, limits=limits, at=AT)
        return run_importer(Importer(), "onco", reader.confine(directory), options)


@pytest.fixture
def discovery() -> Discovery:
    return Discovery(importlib.import_module(MODULE + ".study"))


@pytest.fixture
def notes_of() -> Callable[[Any], list[str]]:
    """A study's notes, each as its kind, its text and its count."""

    def notes(found: Any) -> list[str]:
        shown = []
        for note in found.notes:
            count = "" if note.count is None else f" ({note.count})"
            shown.append(f"{note.kind}: {said(note.message)}{count}")
        return shown

    return notes

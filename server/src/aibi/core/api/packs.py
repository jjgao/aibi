"""The packs the server installs: the modules its configuration names (SPEC §10.1, D253, D293,
D387, D388, D389).

``registry_of(config, stderr)`` is the loader, which ``aibi-server serve`` and ``check`` call
before anything else of theirs (``serve`` before it sets the umask and opens the store), so that
a failure takes no lock and writes nothing:

1. **DuckDB.** If ``duckdb``, ``_duckdb`` or a submodule of either is already loaded (in
   ``sys.modules``, as anything but ``None``, the entry by which a deployment blocks its import),
   the start fails: the server's process loaded DuckDB before its packs (a core bug, or a
   ``sitecustomize`` or ``.pth`` file), and no finder could keep it out. Otherwise ``NoDuckDB``
   goes first on ``sys.meta_path``, for the process's life: any import of ``duckdb``, ``_duckdb``
   or a submodule of either is refused (``ImportError``), at start-up and at run time, so a
   pack's hook that imports DuckDB fails inside its guard (D388). The query and import workers
   are fresh interpreters (``subprocess.Popen``) and do not inherit it. ``GUARD_DUCKDB``,
   ``True`` in production, turns both off for in-process tests, where pytest has loaded DuckDB.
2. **Quiet.** Every phase that may run a pack's code, each module's import, its ``PACK``'s read
   and the registration, runs inside ``_quiet``: the filtered root handler and the filtered
   ``logging.lastResort`` on ``stderr`` (``logs.install_handler``), and one
   ``warnings.catch_warnings`` with ``"always"``, whose ``showwarning`` counts each warning and
   keeps nothing of it; only the count is written, once, when every phase is done. The count is
   of every warning raised while the packs were loaded, the core's own and its libraries' among
   them, since a warning's frame does not say whose code raised it.
3. **For each module, in its own guard:** its import (``importlib.import_module``), then its
   pack: the module exactly a ``ModuleType``, its ``vars()`` a plain ``dict`` of exact ``str``
   keys, ``PACK`` read from it, and exactly a ``Pack``. A module that fails either is left out
   and named in a problem, with a built-in exception's type found by identity
   (``guards.builtin_name``), never an exception's text. ``MemoryError`` and
   ``KeyboardInterrupt`` themselves pass, as new instances; a ``SystemExit`` at import is the
   module's failure, not an exit.
4. **One registry** of the packs of every module that passed, labelled by their modules
   (``PackRegistry(packs, core_version=…, labels=…)``): its ``PackError`` is the core's, and its
   ``SystemExit`` (a pack's code asked to exit while it was read) is the start's failure, naming
   the module being registered. Nothing else is caught: any other exception is a core bug.

Every problem is written to ``stderr`` at once and the start fails (``None``), so that a module
left out never makes a registry that looks whole.
"""

import importlib
import importlib.abc
import importlib.machinery
import sys
import warnings
from collections.abc import Generator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from types import ModuleType
from typing import NoReturn, TextIO

import aibi
from aibi.core.api import logs
from aibi.core.api.config import ServerConfig
from aibi.core.schema.guards import builtin_name, passed
from aibi.core.schema.pack_api import (
    Pack,
    PackError,
    PackRegistry,
    _shown,  # pyright: ignore[reportPrivateUsage]
)

GUARD_DUCKDB = True
"""Whether the loader refuses DuckDB (D293): ``True`` but in in-process tests, whose process has
loaded DuckDB, which a fixture of theirs sets to ``False``."""
DUCKDB = ("duckdb", "_duckdb")
"""The top-level modules of DuckDB: its package and its native module."""
REFUSED_DUCKDB = "the server's process does not load duckdb (D293)"
"""What ``NoDuckDB`` raises."""
LOADED_DUCKDB = (
    "the server's process loaded duckdb before its packs (a core bug, or a sitecustomize or .pth "
    "file)"
)
"""The start's failure when DuckDB is loaded before the finder can be installed."""


class NoDuckDB(importlib.abc.MetaPathFinder):
    """The finder that refuses DuckDB in the server's process (D293, D389): ``duckdb``,
    ``_duckdb`` and their submodules, by exact name or prefix, each refusal counted. It refuses
    imports through ``sys.meta_path`` only: a module that loads DuckDB's native library by path,
    edits ``sys.meta_path`` or uses ``ctypes`` defeats it, deliberately, which is out of scope
    (D285)."""

    def __init__(self) -> None:
        self.refused = 0

    def find_spec(
        self,
        fullname: str,
        path: Sequence[str] | None,
        target: ModuleType | None = None,
        /,
    ) -> importlib.machinery.ModuleSpec | None:
        if fullname in DUCKDB or fullname.startswith(("duckdb.", "_duckdb.")):
            self.refused += 1
            raise ImportError(REFUSED_DUCKDB, name=fullname)
        return None


def duckdb_loaded() -> bool:
    """Whether ``duckdb`` or ``_duckdb``, or a submodule of either, is in ``sys.modules`` as
    anything but ``None``: a ``None`` entry blocks an import and loads nothing."""
    entries: dict[str, ModuleType | None] = dict(sys.modules)  # a ``None`` entry is a block
    return any(
        module is not None
        for name, module in entries.items()
        if name in DUCKDB or name.startswith(("duckdb.", "_duckdb."))
    )


def install_finder() -> NoDuckDB:
    """``NoDuckDB`` first on ``sys.meta_path``, once."""
    for found in sys.meta_path:
        if type(found) is NoDuckDB:
            return found
    made = NoDuckDB()
    sys.meta_path.insert(0, made)
    return made


@dataclass
class _Warned:
    """How many warnings were raised while the packs were loaded; nothing of each is kept."""

    count: int = 0

    def show(self, *given: object, **named: object) -> None:
        self.count += 1


@contextmanager
def _quiet(stream: TextIO) -> Generator[_Warned]:
    """Every phase of the start that may run a pack's code (D389): the filtered handlers on
    ``stream``, installed if missing, and every warning counted, never shown. On exit the
    warnings filters and ``showwarning`` are as they were, so a filter a module set at import
    does not outlast its import."""
    logs.install_handler(stream)
    warned = _Warned()
    with warnings.catch_warnings():
        warnings.simplefilter("always")
        warnings.showwarning = warned.show
        yield warned


def _raise_passed(kind: type[BaseException]) -> NoReturn:
    """A passed ``MemoryError`` or ``KeyboardInterrupt``, raised anew outside every ``except``."""
    raise kind()


def _imported(name: str) -> tuple[object, str | None]:
    """Step 1: the module ``name``, or the problem that it failed to import."""
    passing: type[BaseException] | None = None
    raised = ""
    try:
        return importlib.import_module(name), None
    except BaseException as error:  # whatever the module raises is its failure (D389)
        passing = passed(error)
        raised = builtin_name(error)
    shown = _shown(name)
    if passing is SystemExit:
        return None, f"the module {shown} asked to exit while it was imported"
    if passing is not None:
        _raise_passed(passing)
    return None, (
        f"the module {shown} failed to import: it raised {raised} "
        f"(python -c 'import {shown}' shows why)"
    )


def _read_pack(module: object) -> tuple[Pack | None, str]:
    """Step 2's reads, which run no code of the module's: its type by identity, its namespace
    through ``vars`` of an exact ``ModuleType``, its keys' types by identity, and ``PACK`` looked
    up among exact ``str`` keys."""
    if type(module) is not ModuleType:
        return None, "has no pack: it is not a plain module"
    namespace = vars(module)
    if type(namespace) is not dict or any(type(key) is not str for key in namespace):
        return None, "has no pack: its namespace is not a plain mapping of names"
    if "PACK" not in namespace:
        return None, "has no pack: it defines no PACK"
    found: object = namespace["PACK"]
    if type(found) is not Pack:
        return None, "is not a pack: its PACK is not exactly a Pack"
    return found, ""


def _pack_of(name: str, module: object) -> tuple[Pack | None, str | None]:
    """Step 2: the module's pack, or the problem that it has none."""
    passing: type[BaseException] | None = None
    raised = ""
    try:
        pack, reason = _read_pack(module)
    except BaseException as error:  # no code of the module's runs here, but guard it all the same
        passing = passed(error)
        raised = builtin_name(error)
    else:
        return pack, None if pack is not None else f"the module {_shown(name)} {reason}"
    if passing is not None and passing is not SystemExit:
        _raise_passed(passing)
    return None, f"the module {_shown(name)} has no pack: reading it raised {raised}"


def _registry(packs: list[Pack], names: list[str]) -> tuple[PackRegistry | None, list[str]]:
    """Step 3: one registry of every pack, labelled by its module; its own ``PackError``'s
    problems, or the one problem of a pack's code asking to exit, otherwise."""
    stops: list[str] = []
    problems: list[str] = []
    exited = False
    try:
        return PackRegistry(
            packs, core_version=aibi.__version__, labels=names, on_stop=stops.append
        ), []
    except PackError as error:  # the registry's own, in the core's words (D387)
        problems = list(error.problems)
    except SystemExit:  # the registry's own, when a pack's code asked to exit (D387)
        exited = True
    if exited:
        stopped = stops[-1] if stops else "a module"
        return None, [
            f"{stopped}: its code asked to exit while the packs were registered, so the "
            "registration's other problems are not known"
        ]
    return None, problems


@dataclass(frozen=True)
class Loaded:
    """What the loader found: the registry, or ``None`` and every problem; and how many
    warnings were raised while the packs were loaded."""

    registry: PackRegistry | None
    problems: tuple[str, ...]
    warnings: int


def load(config: ServerConfig, stderr: TextIO) -> Loaded:
    """The loader of the module docstring, without writing its outcome."""
    finder: NoDuckDB | None = None
    if GUARD_DUCKDB:
        if duckdb_loaded():
            return Loaded(None, (LOADED_DUCKDB,), 0)
        finder = install_finder()
    problems: list[str] = []
    packs: list[Pack] = []
    names: list[str] = []
    with _quiet(stderr) as warned:
        for name in config.packs.modules:
            before = 0 if finder is None else finder.refused
            module, problem = _imported(name)
            pack: Pack | None = None
            if problem is None:
                pack, problem = _pack_of(name, module)
            if finder is not None and finder.refused > before and problem is not None:
                problem += f"; it tried to load duckdb, and {REFUSED_DUCKDB}"
            if problem is not None or pack is None:
                problems.append(problem or f"the module {_shown(name)} has no pack")
                continue
            packs.append(pack)
            names.append(name)
        registry, found = _registry(packs, names)
    problems += found
    if problems:
        return Loaded(None, tuple(problems), warned.count)
    return Loaded(registry, (), warned.count)


def listing(registry: PackRegistry) -> list[str]:
    """The installed packs, in id order, from the registry's copies: ``id version (results
    N)``, escaped."""
    lines: list[str] = []
    for pack_id in registry.ids:
        manifest = registry.pack(pack_id).manifest
        lines.append(
            f"{_shown(manifest.id)} {_shown(manifest.version)} "
            f"(results {int(manifest.results_version)})"
        )
    return lines


def registry_of(config: ServerConfig, stderr: TextIO) -> PackRegistry | None:
    """The registry of the packs ``config`` names, or ``None`` once every problem is written to
    ``stderr``; the warnings raised while the packs were loaded are counted there too. A
    ``MemoryError`` or a ``KeyboardInterrupt`` passes, as a new instance."""
    loaded = load(config, stderr)
    if loaded.warnings:
        counted = "1 warning" if loaded.warnings == 1 else f"{loaded.warnings} warnings"
        stderr.write(f"aibi-server: {counted} while the packs were loaded, not shown\n")
    if loaded.registry is None:
        stderr.write("aibi-server: the packs are refused:\n")
        stderr.writelines(f"  {problem}\n" for problem in loaded.problems)
    return loaded.registry


__all__ = [
    "DUCKDB",
    "GUARD_DUCKDB",
    "LOADED_DUCKDB",
    "REFUSED_DUCKDB",
    "Loaded",
    "NoDuckDB",
    "duckdb_loaded",
    "install_finder",
    "listing",
    "load",
    "registry_of",
]

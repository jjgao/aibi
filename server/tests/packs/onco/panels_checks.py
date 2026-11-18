"""The refusals of the gene panel matrix reader, found in its source (M4.2b-2a, D447).

This is not a test: pytest does not collect it, and it imports nothing of the pack. It loads the
walker of slice 1b, ``mutation_checks.py``, by its path (a stable helper: its ``checks``,
``sites``, ``templates``, ``dropped`` and ``reason_of`` take a module's name) and walks its own
literal list of modules, ``MODULES``: ``panels`` alone, whose every ``if`` that holds a refusal
site is a check of D447. The walkers of slices 1b, 4 and 3 keep their own lists, to which
``panels`` is never added: their corpora, inventories and drop tests are unchanged (D447).
"""

import importlib.util
from pathlib import Path
from types import ModuleType


def _walker() -> ModuleType:
    path = Path(__file__).resolve().parent / "mutation_checks.py"
    spec = importlib.util.spec_from_file_location("mutation_checks_for_panels", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


WALKER = _walker()
MODULES = ("panels",)
"""The modules whose checks the gene panel matrix corpus holds one by one."""
SHARED = ("study", "meta", "importer", "caselists")
"""The modules whose sites a matrix cell reaches besides ``panels``: D409's (``study``, ``meta``,
``importer``) and D444's case lists, to which slice 2a adds no site."""
READERS = ("mutation", "structural", "cna")
"""The other readers a cell beside the matrix reaches (their own maps hold their checks)."""
checks = WALKER.checks
sites = WALKER.sites
templates = WALKER.templates
dropped = WALKER.dropped
reason_of = WALKER.reason_of
pattern = WALKER.pattern


def every_check() -> list[object]:
    return [check for module in MODULES for check in checks(module)]


__all__ = [
    "MODULES",
    "READERS",
    "SHARED",
    "checks",
    "dropped",
    "every_check",
    "pattern",
    "reason_of",
    "sites",
    "templates",
]

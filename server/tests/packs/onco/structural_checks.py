"""The refusals of the structural variant reader, found in its source (M4.2b-4, D445).

This is not a test: pytest does not collect it, and it imports nothing of the pack. It loads the
walker of slice 1b, ``mutation_checks.py``, by its path (a stable helper: its ``checks``,
``sites``, ``templates``, ``dropped`` and ``reason_of`` take a module's name) and walks its own
literal list of modules, ``MODULES``: ``structural`` alone, whose every ``if`` that holds a refusal
site is a check of D445. Slice 1b's walker keeps its own lists (``READERS`` and ``OTHERS``), to
which ``structural`` is never added: its corpus, inventory and drop test are unchanged (D445).
"""

import importlib.util
from pathlib import Path
from types import ModuleType


def _walker() -> ModuleType:
    path = Path(__file__).resolve().parent / "mutation_checks.py"
    spec = importlib.util.spec_from_file_location("mutation_checks_for_sv", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


WALKER = _walker()
MODULES = ("structural",)
"""The modules whose checks the structural variant corpus holds one by one."""
SHARED = ("study", "meta", "importer")
"""The modules slice 1b's walker holds (its ``OTHERS``), which slice 4 adds no site to."""
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
    "SHARED",
    "checks",
    "dropped",
    "every_check",
    "pattern",
    "reason_of",
    "sites",
    "templates",
]

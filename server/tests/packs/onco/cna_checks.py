"""The refusals of the discrete copy-number reader, found in its source (M4.2b-3, D446).

This is not a test: pytest does not collect it, and it imports nothing of the pack. It loads the
walker of slice 1b, ``mutation_checks.py``, by its path (a stable helper: its ``checks``,
``sites``, ``templates``, ``dropped`` and ``reason_of`` take a module's name) and walks its own
literal list of modules, ``MODULES``: ``cna`` alone, whose every ``if`` that holds a refusal site
is a check of D446. Slice 1b's walker keeps its own lists (``READERS`` and ``OTHERS``) and slice 4's
its own (``structural_checks.MODULES``), to which ``cna`` is never added: their corpora,
inventories and drop tests are unchanged (D446).
"""

import importlib.util
from pathlib import Path
from types import ModuleType


def _walker() -> ModuleType:
    path = Path(__file__).resolve().parent / "mutation_checks.py"
    spec = importlib.util.spec_from_file_location("mutation_checks_for_cna", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


WALKER = _walker()
MODULES = ("cna",)
"""The modules whose checks the discrete copy-number corpus holds one by one."""
SHARED = ("study", "meta", "importer", "caselists", "panels")
"""The modules whose sites a CNA cell reaches besides ``cna``: D409's (``study``, ``meta``,
``importer``) and D444's case lists, to which slice 3 adds no site, and from M4.2b-2a the gene
panel matrix's (D447), which a CNA cell beside a matrix meta reaches before the CNA file."""
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

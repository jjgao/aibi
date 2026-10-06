"""No import reaches ``discover`` before M4.2a-1a-paths (D409's open exception, its guard).

``discover`` resolves no path value outside the clinical role. Where cBioPortal, through such a
value, reads a file aibi imports or fails on the tags file's name, it would accept a study
cBioPortal refuses. That is acceptable only while nothing imports through it: the pack's ``PACK``
has no importer until M4.2a-1b wires one, and 1b must land after M4.2a-1a-paths, which adds
``study.PATH_SITES`` with the closure. The guard fails if that order is broken.
"""

import importlib
from types import ModuleType, SimpleNamespace

MODULE = "aibi.packs.onco"

SEED_KEYS = frozenset(
    {
        "data_filename",
        "pd_annotations_filename",
        "tags_file",
        "listing:meta",
        "listing:case_lists",
        "isdir:case_lists",
    }
)
"""The keys of the seed rows of M4.2a-1a-paths' ``PATH_SITES`` (its outline 2): the three path
values outside the clinical role and the three listings of study content. Each row of
``PATH_SITES`` is its key, or carries it as ``key``."""


def closed(pack: object, study: object) -> bool:
    """Whether the guard holds: the pack has no importer, or ``study.PATH_SITES`` holds at least
    the seed rows' keys."""
    if getattr(pack, "importer", None) is None:
        return True
    sites = getattr(study, "PATH_SITES", None)
    if sites is None:
        return False
    return {getattr(row, "key", row) for row in sites} >= SEED_KEYS


def test_discover_not_reachable_before_paths(onco: ModuleType) -> None:
    """D409, the open exception: until M4.2a-1a-paths, ``discover`` resolves no path value outside
    the clinical role (a non-clinical meta file's ``data_filename``, ``pd_annotations_filename``,
    ``tags_file``), so no import may reach it. Its landing condition: 1a-paths merges before
    M4.2a-1b wires ``discover`` into ``PACK``. This fails if ``PACK`` gains an importer while
    ``aibi.packs.onco.study`` has no ``PATH_SITES`` holding the seed rows' keys."""
    study = importlib.import_module(MODULE + ".study")
    assert closed(onco.PACK, study)


def test_the_guard_refuses_what_would_open_the_exception() -> None:
    """The guard's self-check: a wired importer with no ``PATH_SITES``, or a stub of it, fails;
    the seed rows pass, as strings or as rows with a ``key``."""
    wired, unwired = SimpleNamespace(importer=object()), SimpleNamespace(importer=None)
    assert closed(unwired, SimpleNamespace())
    assert not closed(wired, SimpleNamespace())
    assert not closed(wired, SimpleNamespace(PATH_SITES=()))
    assert not closed(wired, SimpleNamespace(PATH_SITES=sorted(SEED_KEYS)[1:]))
    assert closed(wired, SimpleNamespace(PATH_SITES=tuple(SEED_KEYS)))
    rows = [SimpleNamespace(key=key, sites=("V:0",)) for key in SEED_KEYS]
    assert closed(wired, SimpleNamespace(PATH_SITES=rows))


def test_the_pack_imports_nothing_yet(onco: ModuleType) -> None:
    """At M4.2a-1a, ``PACK`` has no importer: discovery is reachable from tests alone."""
    assert onco.PACK.importer is None

"""The census of the sites where a string reaches server text (``scripts/census_text_sites.py``,
SPEC §8.1, §14, D397) is current: the residue it generates now is the committed one, and no review
is of a site that is gone. A new site, a reverted conversion or a moved review fails here until it
is converted or reviewed (M4.0f-A1; M4.0f-A2 replaces the census with a rule on the type)."""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

SERVER = Path(__file__).parents[2]
SCRIPT = SERVER / "scripts" / "census_text_sites.py"
RESIDUE = SERVER / "scripts" / "census_text_sites.residue.md"
BEGIN, END = "<!-- census:begin -->", "<!-- census:end -->"


def _script() -> ModuleType:
    """The script as a module, by its path: it is not a package, and nothing else imports it."""
    spec = importlib.util.spec_from_file_location("census_text_sites", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses look their module up here
    try:
        spec.loader.exec_module(module)
    except BaseException:
        del sys.modules[spec.name]
        raise
    return module


@pytest.fixture(scope="module")
def census() -> tuple[str, list[tuple[str, str, str, str]]]:
    """The residue table the census generates now, and the reviewed keys that match no site."""
    script = _script()
    try:
        found, sites = script.census()
        known = script.reviewed()
        return script.report(found, sites, known, False), script.stale(sites, known)
    finally:
        del sys.modules[script.__name__]


def test_the_committed_residue_is_what_the_census_generates_now(
    census: tuple[str, list[tuple[str, str, str, str]]],
) -> None:
    committed = RESIDUE.read_text().split(BEGIN)[1].split(END)[0].strip()
    assert committed == census[0]


def test_no_review_is_of_a_site_that_is_gone(
    census: tuple[str, list[tuple[str, str, str, str]]],
) -> None:
    assert census[1] == []

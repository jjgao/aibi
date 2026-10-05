"""The server's wording and its classification of server text, checked in (SPEC §8.1, D374,
D231, D399).

- ``CACHE_WORDING`` is raised with any change to a template of the server's text: ``wording.json``
  holds the digest of every template (``census_text_sites.templates``, a sorted multiset, so that
  moving code changes nothing), and a change fails here until the wording is raised, so that a
  template no golden result renders cannot change the text of cached results silently.
- ``REPORT_CLASSIFICATION`` changes only with what counts as server text, and the wording is
  raised with it: ``wording.json``'s history holds each pair the server has had.

``AIBI_WRITE_GOLDEN=1`` writes the file again, and refuses, naming the number to bump, when the
templates or the classification changed and ``CACHE_WORDING`` was not raised past the history's
last entry; its history gains the current pair if it is new.
"""

import hashlib
import importlib.util
import json
import os
import sys
from itertools import pairwise
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from tests.core import wording_base

from aibi.core.schema.output import CACHE_WORDING, REPORT_CLASSIFICATION
from aibi.core.store import build

SERVER = Path(__file__).parents[2]
SCRIPT = SERVER / "scripts" / "census_text_sites.py"
WORDING = Path(__file__).with_name("wording.json")
WORDING_CHECKED_IN = WORDING


def _script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("census_text_sites_wording", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        del sys.modules[spec.name]
        raise
    return module


@pytest.fixture(scope="module")
def templates() -> list[str]:
    script = _script()
    try:
        return script.templates()
    finally:
        del sys.modules[script.__name__]


def _digest(found: list[str]) -> str:
    written = json.dumps(found, ensure_ascii=False, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(written).hexdigest()


def _written(golden: dict[str, Any], templates: list[str]) -> dict[str, Any]:
    """What ``AIBI_WRITE_GOLDEN=1`` writes: refused, naming the number to bump, unless the wording
    was raised past the history's last entry whenever the templates or the classification
    changed, and never under a lower classification (a write that did not check would turn the
    gate off: the stale cache would be served in the new wording)."""
    last_classification, last_wording = golden["history"][-1]
    digest = _digest(templates)
    if (golden["templates"], golden["count"]) != (digest, len(templates)) and (
        last_wording >= CACHE_WORDING
    ):
        pytest.fail(
            f"a template of the server's text changed: raise CACHE_WORDING to {last_wording + 1} "
            "(schema/output.py) before writing wording.json again (D399)",
            pytrace=False,
        )
    if last_classification > REPORT_CLASSIFICATION:
        pytest.fail(
            f"REPORT_CLASSIFICATION is {REPORT_CLASSIFICATION}, below the {last_classification} "
            "of the history: it is never lowered (D399)",
            pytrace=False,
        )
    if last_classification != REPORT_CLASSIFICATION and last_wording >= CACHE_WORDING:
        pytest.fail(
            f"REPORT_CLASSIFICATION changed: raise CACHE_WORDING to {last_wording + 1} "
            "(schema/output.py) with it, before writing wording.json again (D399)",
            pytrace=False,
        )
    pair = [REPORT_CLASSIFICATION, CACHE_WORDING]
    history = golden["history"] if pair in golden["history"] else [*golden["history"], pair]
    return {
        "cache_wording": CACHE_WORDING,
        "report_classification": REPORT_CLASSIFICATION,
        "templates": digest,
        "count": len(templates),
        "history": history,
    }


def test_the_templates_are_the_wording_checked_in(templates: list[str]) -> None:
    pair = [REPORT_CLASSIFICATION, CACHE_WORDING]
    golden = json.loads(WORDING.read_text(encoding="utf-8"))
    if os.environ.get("AIBI_WRITE_GOLDEN") == "1":
        WORDING.write_text(json.dumps(_written(golden, templates), indent=1) + "\n")
        pytest.skip("written")
    assert [golden["report_classification"], golden["cache_wording"]] == pair, (
        "write wording.json again"
    )
    assert golden["templates"] == _digest(templates), (
        "a template of the server's text changed: raise CACHE_WORDING (schema/output.py) and "
        "write tests/core/wording.json again (D399)"
    )
    assert golden["count"] == len(templates)


@pytest.fixture
def writing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The checked-in file copied, to be written by the test that writes golden files."""
    copied = tmp_path / "wording.json"
    copied.write_text(WORDING.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setattr(sys.modules[__name__], "WORDING", copied)
    monkeypatch.setenv("AIBI_WRITE_GOLDEN", "1")
    return copied


def _wrote(templates: list[str]) -> bool:
    """Whether the writer wrote (the test skips with "written" when it does); a refusal raises.
    A skip must not stand for a pass: a writer that wrote when it should not is a failure."""
    try:
        test_the_templates_are_the_wording_checked_in(templates)
    except pytest.skip.Exception:
        return True
    return False


def test_the_writer_refuses_a_changed_template_unless_the_wording_is_raised(
    writing: Path, templates: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    before = writing.read_text(encoding="utf-8")
    wording = CACHE_WORDING
    changed = [*templates[:-1], "A template that was reworded {}"]
    with pytest.raises(pytest.fail.Exception, match=f"raise CACHE_WORDING to {wording + 1}"):
        _wrote(changed)
    assert writing.read_text(encoding="utf-8") == before, "nothing is written when it refuses"
    monkeypatch.setattr(sys.modules[__name__], "CACHE_WORDING", wording + 1)
    assert _wrote(changed)
    written = json.loads(writing.read_text(encoding="utf-8"))
    assert written["cache_wording"] == wording + 1
    assert written["history"][-1] == [REPORT_CLASSIFICATION, wording + 1]
    assert written["templates"] == _digest(changed)


def test_the_writer_refuses_a_changed_classification_unless_the_wording_is_raised(
    writing: Path, templates: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    here = sys.modules[__name__]
    wording, classification = CACHE_WORDING, REPORT_CLASSIFICATION
    monkeypatch.setattr(here, "REPORT_CLASSIFICATION", classification + 1)
    with pytest.raises(pytest.fail.Exception, match=f"raise CACHE_WORDING to {wording + 1}"):
        _wrote(templates)
    monkeypatch.setattr(here, "CACHE_WORDING", wording + 1)
    assert _wrote(templates)
    assert json.loads(writing.read_text(encoding="utf-8"))["history"][-1] == [
        classification + 1,
        wording + 1,
    ]


def test_the_writer_never_lowers_the_classification(
    writing: Path, templates: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys.modules[__name__], "REPORT_CLASSIFICATION", REPORT_CLASSIFICATION - 1)
    monkeypatch.setattr(sys.modules[__name__], "CACHE_WORDING", CACHE_WORDING + 1)
    with pytest.raises(pytest.fail.Exception, match="never lowered"):
        _wrote(templates)


def test_the_writer_of_an_unchanged_wording_writes_the_same_file(
    writing: Path, templates: list[str]
) -> None:
    assert _wrote(templates)
    assert json.loads(writing.read_text(encoding="utf-8")) == json.loads(
        WORDING_CHECKED_IN.read_text(encoding="utf-8")
    )


def test_the_wording_is_raised_with_every_classification() -> None:
    history: list[list[int]] = json.loads(WORDING.read_text(encoding="utf-8"))["history"]
    assert history[-1] == [REPORT_CLASSIFICATION, CACHE_WORDING]
    for (classification, wording), (later_classification, later_wording) in pairwise(history):
        assert later_wording > wording
        assert later_classification >= classification


def test_a_report_of_format_2_is_of_the_first_classification() -> None:
    assert build.REPORT_2_CLASSIFICATION == 1
    assert build.REPORT_FORMAT == "aibi.import-report/2"


# --- against the base branch's (a hand edit of the file) ----------------------------------------


def _wording(digest: str = "sha256:d1", wording: int = 1, **more: Any) -> dict[str, Any]:
    history = more.pop("history", [[1, wording]])
    return {
        "cache_wording": wording,
        "report_classification": 1,
        "templates": digest,
        "count": 10,
        "history": history,
        **more,
    }


def test_the_check_against_the_base_fails_a_digest_changed_under_the_same_wording() -> None:
    base = _wording("sha256:d1", 1)
    found = wording_base.wording_violations(base, _wording("sha256:d2", 1))
    assert len(found) == 1
    assert "raise CACHE_WORDING" in found[0]


def test_the_check_against_the_base_passes_a_raised_wording() -> None:
    base = _wording("sha256:d1", 1)
    head = _wording("sha256:d2", 2, history=[[1, 1], [1, 2]])
    assert wording_base.wording_violations(base, head) == []
    assert wording_base.wording_violations(base, base) == []


def test_the_check_against_the_base_fails_a_changed_count_or_classification() -> None:
    base = _wording()
    assert wording_base.wording_violations(base, _wording(count=11))
    assert wording_base.wording_violations(base, _wording(report_classification=2))
    raised = _wording(report_classification=2, wording=2, history=[[1, 1], [2, 2]])
    assert wording_base.wording_violations(base, raised) == []


def test_the_check_against_the_base_fails_a_lowered_number_or_a_truncated_history() -> None:
    base = _wording("sha256:d1", 3, history=[[1, 1], [1, 2], [1, 3]])
    lowered = _wording("sha256:d1", 2, history=[[1, 1], [1, 2], [1, 3]])
    assert wording_base.wording_violations(base, lowered)
    truncated = _wording("sha256:d1", 3, history=[[1, 3]])
    assert wording_base.wording_violations(base, truncated)
    assert wording_base.wording_violations(base, _wording("sha256:d1", 3, report_classification=0))


def test_the_check_against_the_base_fails_a_changed_projection_under_the_same_wording() -> None:
    base = {"cache_wording": 1, "cases": {"a": [["/m/0", "text", "x"]], "b": []}}
    same = {"cache_wording": 1, "cases": {"a": [["/m/0", "text", "x"]], "b": []}}
    changed = {"cache_wording": 1, "cases": {"a": [["/m/0", "text", "y"]], "b": []}}
    raised = {"cache_wording": 2, "cases": {"a": [["/m/0", "text", "y"]], "b": []}}
    added = {"cache_wording": 1, "cases": {**same["cases"], "c": [["/m/0", "text", "z"]]}}
    assert wording_base.projection_violations(base, same) == []
    assert wording_base.projection_violations(base, added) == []
    assert wording_base.projection_violations(base, raised) == []
    found = wording_base.projection_violations(base, changed)
    assert len(found) == 1
    assert "a changed or is gone since the base" in found[0]
    assert wording_base.projection_violations(raised, base)


def test_the_check_against_the_base_fails_a_result_gone_or_renamed_under_the_same_wording() -> None:
    base = {"cache_wording": 1, "cases": {"a": [["/m/0", "text", "x"]], "b": []}}
    renamed = {"cache_wording": 1, "cases": {"a": [["/m/0", "text", "x"]], "c": []}}
    dropped = {"cache_wording": 1, "cases": {"a": [["/m/0", "text", "x"]]}}
    raised = {"cache_wording": 2, "cases": {"a": [["/m/0", "text", "x"]]}}
    for head in (renamed, dropped):
        [found] = wording_base.projection_violations(base, head)
        assert "b changed or is gone since the base" in found
    assert wording_base.projection_violations(base, raised) == []


def test_each_check_against_the_base_fails_alone_on_its_own_fixture() -> None:
    """Every violation of ``wording_violations`` has a fixture that trips it and no other, so that
    removing one is a failure of its own."""
    base = _wording("sha256:d1", 2, report_classification=2, history=[[1, 1], [2, 2]])
    cases = {
        "CACHE_WORDING is lower": _wording(
            "sha256:d1", 1, report_classification=2, history=[[1, 1], [2, 2]]
        ),
        "REPORT_CLASSIFICATION is lower": _wording(
            "sha256:d1", 3, report_classification=1, history=[[1, 1], [2, 2], [1, 3]]
        ),
        "a template of the server's text changed": _wording(
            "sha256:d2", 2, report_classification=2, history=[[1, 1], [2, 2]]
        ),
        "REPORT_CLASSIFICATION changed": _wording(
            "sha256:d1", 2, report_classification=3, history=[[1, 1], [2, 2]]
        ),
        "the history is not the base's history": _wording(
            "sha256:d1", 3, report_classification=2, history=[[2, 2], [2, 3]]
        ),
    }
    for expected, head in cases.items():
        [found] = wording_base.wording_violations(base, head)
        assert expected in found, (expected, found)


def test_the_base_directory_is_required_where_a_base_is_due(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A check that cannot run fails and does not skip: a set variable names a directory that is
    there, and a pull request's run has one; elsewhere none is due."""
    monkeypatch.delenv(wording_base.ENV, raising=False)
    monkeypatch.delenv(wording_base.EVENT, raising=False)
    assert wording_base.directory() is None
    monkeypatch.setenv(wording_base.EVENT, "push")
    assert wording_base.directory() is None
    monkeypatch.setenv(wording_base.EVENT, "pull_request")
    with pytest.raises(wording_base.BaseMissingError):
        wording_base.directory()
    monkeypatch.setenv(wording_base.ENV, str(tmp_path / "missing"))
    with pytest.raises(wording_base.BaseMissingError):
        wording_base.directory()
    monkeypatch.setenv(wording_base.ENV, str(tmp_path))
    assert wording_base.directory() == tmp_path
    monkeypatch.delenv(wording_base.EVENT)
    assert wording_base.directory() == tmp_path


def _base_copy(name: str) -> dict[str, Any] | None:
    directory = wording_base.directory()
    assert directory is not None
    copied = directory / name
    return json.loads(copied.read_text(encoding="utf-8")) if copied.exists() else None


def test_the_wording_is_raised_whenever_it_changed_since_the_base_branch() -> None:
    if wording_base.directory() is None:
        pytest.skip(
            f"{wording_base.ENV} names the directory of the base branch's wording.json (CI sets it "
            "on a pull request); not set, so the hand edit of the file is not checked"
        )
    base = _base_copy("wording.json")
    if base is None:  # the base has none: this change introduces the file
        return
    head = json.loads(WORDING_CHECKED_IN.read_text(encoding="utf-8"))
    assert wording_base.wording_violations(base, head) == []

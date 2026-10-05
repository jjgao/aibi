"""The server's wording in results, held to ``CACHE_WORDING`` (SPEC §8.1, D374, D399).

A cached result is read back only under the wording it was written with. ``golden/
text_projection.json`` holds, for each golden document, the text projection of its result: the
JSON pointer, kind and text of every segment, and every chart's title and description; numbers
are left out, so that a change below a digest's resolution does not touch it. A change to it
fails here until ``CACHE_WORDING`` is raised and the file is written again (``AIBI_WRITE_GOLDEN=1``
writes it), so that no cached result is served in the old wording.
"""

import json
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from tests.core import wording_base

from aibi.core.analyses.results import Digested, OtherWording, digested
from aibi.core.schema.jsonio import escape_token
from aibi.core.schema.output import CACHE_WORDING

GOLDEN = Path(__file__).parent / "golden"
CASES = GOLDEN / "results.json"
PROJECTION = GOLDEN / "text_projection.json"


def _cases() -> dict[str, dict[str, Any]]:
    return json.loads(CASES.read_text(encoding="utf-8"))


def project(value: Any, pointer: str = "") -> list[list[str]]:
    """The text projection of a dumped output: ``[pointer, kind, text]`` for each segment, and
    each chart's title and description."""
    found: list[list[str]] = []
    if isinstance(value, dict):
        members: dict[str, Any] = value
        if set(members) == {"text"}:
            return [[pointer, "text", members["text"]]]
        if set(members) in ({"data"}, {"data", "truncated"}):
            return [[pointer, "data", members["data"]]]
        for key in sorted(members):
            below = f"{pointer}/{escape_token(key)}"
            if key in ("title", "description") and isinstance(members[key], str):
                found.append([below, key, members[key]])
            else:
                found += project(members[key], below)
    elif isinstance(value, list):
        for at, item in enumerate(value):
            found += project(item, f"{pointer}/{at}")
    return found


def _projected(analyse: Callable[..., list[Any]], shop: Callable[..., Any]) -> dict[str, Any]:
    found: dict[str, Any] = {}
    for name, case in sorted(_cases().items()):
        [analysed] = analyse(
            case["document"], shop(**case.get("shop", {})), floor=case.get("floor")
        )
        found[name] = project(analysed.result.model_dump(mode="json"))
    return found


def _written(found: dict[str, Any]) -> str:
    """The projection as the file holds it: one segment to a line."""
    cases = ",\n".join(
        f" {json.dumps(name)}: [\n"
        + ",\n".join(f"  {json.dumps(row, ensure_ascii=False)}" for row in rows)
        + "\n ]"
        for name, rows in found["cases"].items()
    )
    return f'{{"cache_wording": {found["cache_wording"]}, "cases": {{\n{cases}\n}}}}\n'


def _refuse_unraised(golden: dict[str, Any], found: dict[str, Any]) -> None:
    """``AIBI_WRITE_GOLDEN=1`` writes a changed projection only under a raised wording: one
    written under the file's own ``cache_wording`` would serve cached results in the old wording."""
    if found["cases"] != golden["cases"] and golden["cache_wording"] >= CACHE_WORDING:
        pytest.fail(
            f"the server's wording changed: raise CACHE_WORDING to {golden['cache_wording'] + 1} "
            "(schema/output.py) before writing golden/text_projection.json again (D399)",
            pytrace=False,
        )


def test_the_wording_of_every_golden_result_is_the_one_checked_in(
    analyse: Callable[..., list[Any]], shop: Callable[..., Any]
) -> None:
    found = {"cache_wording": CACHE_WORDING, "cases": _projected(analyse, shop)}
    golden = json.loads(PROJECTION.read_text(encoding="utf-8"))
    if os.environ.get("AIBI_WRITE_GOLDEN") == "1":
        _refuse_unraised(golden, found)
        PROJECTION.write_text(_written(found))
        pytest.skip("written")
    assert golden["cache_wording"] == CACHE_WORDING, "write the projection again"
    for name, projected in found["cases"].items():
        assert projected == golden["cases"][name], (
            f"{name}: the server's wording changed: raise CACHE_WORDING (schema/output.py) and "
            "write golden/text_projection.json again (D399)"
        )
    assert set(golden["cases"]) == set(found["cases"])


def _wrote(analyse: Callable[..., list[Any]], shop: Callable[..., Any]) -> bool:
    """Whether the writer wrote (the test skips with "written" when it does); a refusal raises.
    A skip must not stand for a pass: a writer that wrote when it should not is a failure."""
    try:
        test_the_wording_of_every_golden_result_is_the_one_checked_in(analyse, shop)
    except pytest.skip.Exception:
        return True
    return False


def test_the_writer_refuses_a_changed_projection_unless_the_wording_is_raised(
    analyse: Callable[..., list[Any]],
    shop: Callable[..., Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    here = sys.modules[__name__]
    wording = CACHE_WORDING
    copied = tmp_path / "text_projection.json"
    copied.write_text(PROJECTION.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setattr(here, "PROJECTION", copied)
    monkeypatch.setenv("AIBI_WRITE_GOLDEN", "1")
    monkeypatch.setattr(here, "project", lambda value, pointer="": [["/m/0", "text", "Reworded "]])
    before = copied.read_text(encoding="utf-8")
    with pytest.raises(pytest.fail.Exception, match=f"raise CACHE_WORDING to {wording + 1}"):
        _wrote(analyse, shop)
    assert copied.read_text(encoding="utf-8") == before, "nothing is written when it refuses"
    monkeypatch.setattr(here, "CACHE_WORDING", wording + 1)
    assert _wrote(analyse, shop)
    assert json.loads(copied.read_text(encoding="utf-8"))["cache_wording"] == wording + 1


def test_the_projection_holds_text_and_data_and_leaves_numbers_out() -> None:
    dumped = {
        "message": [{"text": "a "}, {"data": "b", "truncated": True}],
        "estimate": 0.5,
        "charts": [{"title": "t", "description": "d", "values": [1, 2]}],
    }
    assert project(dumped) == [
        ["/charts/0/description", "description", "d"],
        ["/charts/0/title", "title", "t"],
        ["/message/0", "text", "a "],
        ["/message/1", "data", "b"],
    ]


@pytest.mark.parametrize("written", [CACHE_WORDING + 1, 0, None], ids=["later", "0", "none"])
def test_content_of_another_wording_is_refused_before_anything_else(
    analyse: Callable[..., list[Any]], shop: Callable[..., Any], written: int | None
) -> None:
    """``OtherWording``, a quiet miss, whatever else the content holds (its hash is not looked
    at); content written before D399 has no wording, which reads as 0."""
    case = _cases()["ages_compared"]
    [analysed] = analyse(case["document"], shop(**case.get("shop", {})), floor=case.get("floor"))
    content = digested(analysed.view, analysed.outcome)
    found: dict[str, Any] = json.loads(content.content())
    assert found["text_format"] == CACHE_WORDING
    found["hashed"] = "sha256:" + "0" * 64
    if written is None:
        del found["text_format"]
    else:
        found["text_format"] = written
    with pytest.raises(OtherWording):
        Digested.read(analysed.view, json.dumps(found).encode())


def test_the_projection_changed_only_under_a_raised_wording_since_the_base_branch() -> None:
    if wording_base.directory() is None:
        pytest.skip(
            f"{wording_base.ENV} names the directory of the base branch's text_projection.json "
            "(CI sets it on a pull request); not set, so a hand edit of the file is not checked"
        )
    directory = wording_base.directory()
    assert directory is not None
    copied = directory / "text_projection.json"
    if not copied.exists():  # the base has none: this change introduces the file
        return
    base = json.loads(copied.read_text(encoding="utf-8"))
    head = json.loads(PROJECTION.read_text(encoding="utf-8"))
    assert wording_base.projection_violations(base, head) == []

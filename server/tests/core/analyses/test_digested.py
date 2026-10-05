"""A result's digested content apart from its rendering (SPEC §7.6, §8.1; D374): over every golden
document, the content a view's outcome gives is read back as it was, and rendered for another
issuance it gives the same envelope but its issuance, whose values come from the first."""

import copy
import json
import math
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from aibi.core.analyses.results import Digested, digested, render
from aibi.core.schema.digests import hashed
from aibi.core.schema.results import ResultEnvelope

GOLDEN = Path(__file__).parent / "golden" / "results.json"
Analyse = Callable[..., list[Any]]
Shop = Callable[..., Any]


def _cases() -> dict[str, dict[str, Any]]:
    return json.loads(GOLDEN.read_text(encoding="utf-8"))


def rendered(analysed: Any, content: Digested, *, issuance: str, values_from: str) -> Any:
    result: ResultEnvelope = analysed.result
    return render(
        analysed.view,
        content,
        issuance=issuance,
        values_from=values_from,
        written=result.source.document,
        params=result.source.params,
        engine=result.derivation.engine,
    )


def without_issuance(result: ResultEnvelope) -> dict[str, Any]:
    dumped = result.model_dump(mode="json")
    del dumped["issuance"]
    return dumped


@pytest.mark.parametrize("name", sorted(_cases()))
def test_a_view_s_digested_content_reads_back_as_it_was(
    analyse: Analyse, shop: Shop, name: str
) -> None:
    case = _cases()[name]
    [analysed] = analyse(case["document"], shop(**case.get("shop", {})), floor=case.get("floor"))
    content = digested(analysed.view, analysed.outcome)
    back = Digested.read(analysed.view, content.content())
    assert back == content
    assert back.digest == analysed.result.digest == case["digest"]
    assert back.values.model_dump_json() == analysed.result.values.model_dump_json()


@pytest.mark.parametrize("name", sorted(_cases()))
def test_content_read_back_renders_the_envelope_but_its_issuance(
    analyse: Analyse, shop: Shop, name: str
) -> None:
    case = _cases()[name]
    [analysed] = analyse(case["document"], shop(**case.get("shop", {})), floor=case.get("floor"))
    first = analysed.result.issuance.id
    back = Digested.read(analysed.view, digested(analysed.view, analysed.outcome).content())
    again = rendered(analysed, back, issuance="iss:01J0000000000000000000000B", values_from=first)
    assert without_issuance(again) == without_issuance(analysed.result)
    assert again.model_dump_json(exclude={"issuance"}) == analysed.result.model_dump_json(
        exclude={"issuance"}
    )
    assert again.issuance.cache_hit
    assert again.issuance.values_from == first
    same = rendered(analysed, back, issuance=first, values_from=first)
    assert same == analysed.result


def test_content_rendered_for_a_document_that_names_its_cohorts_otherwise_takes_its_names(
    analyse: Analyse, shop: Shop
) -> None:
    """A result's id holds no name (§7.6), so content filled under one document's names is
    rendered with another's: its labels, readback and charts are the second's (D84, D120)."""
    case = _cases()["cox_tiers_and_ages"]
    written = case["document"]
    renamed = copy.deepcopy(written)
    names = {name: f"{name}_again" for name in written["cohorts"]}
    renamed["cohorts"] = {names[name]: clause for name, clause in written["cohorts"].items()}
    for view in renamed["views"]:
        view["cohorts"] = [names[name] for name in view["cohorts"]]
    [first] = analyse(written, shop(**case.get("shop", {})))
    [second] = analyse(renamed, shop(**case.get("shop", {})))
    assert first.result.derivation.id == second.result.derivation.id
    back = Digested.read(second.view, digested(first.view, first.outcome).content())
    again = rendered(
        second,
        back,
        issuance=second.result.issuance.id,
        values_from="iss:01J0000000000000000000000C",
    )
    assert without_issuance(again) == without_issuance(second.result)
    assert [label.data for label in again.labels] == [
        names[n] for n in written["views"][0]["cohorts"]
    ]


ACROSS = [
    (one, other)
    for one in sorted(_cases())
    for other in sorted(_cases())
    if _cases()[one]["document"]["views"][0]["analysis"]
    != _cases()[other]["document"]["views"][0]["analysis"]
]


@pytest.mark.parametrize(("one", "other"), ACROSS)
def test_content_of_one_analysis_is_never_read_back_for_another(
    analyse: Analyse, shop: Shop, one: str, other: str
) -> None:
    first, second = _analysed(analyse, shop, one), _analysed(analyse, shop, other)
    with pytest.raises(ValueError, match="result it is of"):
        Digested.read(second.view, digested(first.view, first.outcome).content())


def _analysed(analyse: Analyse, shop: Shop, name: str) -> Any:
    case = _cases()[name]
    [found] = analyse(case["document"], shop(**case.get("shop", {})), floor=case.get("floor"))
    return found


PAIRS = [
    (one, other)
    for one in sorted(_cases())
    for other in sorted(_cases())
    if one != other
    and _cases()[one]["document"]["views"][0]["analysis"]
    == _cases()[other]["document"]["views"][0]["analysis"]
]


@pytest.mark.parametrize(("one", "other"), PAIRS)
def test_content_is_read_back_only_for_the_result_it_is_of(
    analyse: Analyse, shop: Shop, one: str, other: str
) -> None:
    """Another view of the same analysis (other cohorts, parameters or *k*) never reads it."""
    first, second = _analysed(analyse, shop, one), _analysed(analyse, shop, other)
    content = digested(first.view, first.outcome).content()
    with pytest.raises(ValueError, match="result it is of"):
        Digested.read(second.view, content)


def _rehashed(content: dict[str, Any]) -> bytes:
    rest = {key: value for key, value in content.items() if key != "hashed"}
    return json.dumps({**rest, "hashed": "sha256:" + hashed(rest)}).encode()


def test_content_whose_digest_changed_is_not_read_back(analyse: Analyse, shop: Shop) -> None:
    """Even with its hash taken again, content whose digested members changed is refused."""
    analysed = _analysed(analyse, shop, "cox_tiers_and_ages")
    content = json.loads(digested(analysed.view, analysed.outcome).content())
    assert Digested.read(analysed.view, _rehashed(content)).digest == content["digest"]
    dropped = {**content, "caveats": content["caveats"][1:]}
    path = next(
        path
        for path in _leaves(content["values"])
        if isinstance(_at(content["values"], path), float) and _at(content["values"], path)
    )
    doubled = _set(content, ("values", *path), 2 * _at(content["values"], path))
    for changed in (dropped, doubled):
        with pytest.raises(ValueError, match="digest it was written with"):
            Digested.read(analysed.view, _rehashed(changed))


def test_digested_content_whose_values_are_not_its_typed_values_is_refused(
    analyse: Analyse, shop: Shop
) -> None:
    first = digested(*_view_outcome(_analysed(analyse, shop, "cox_tiers_and_ages")))
    other = digested(*_view_outcome(_analysed(analyse, shop, "cox_stratified_at_the_origin")))
    with pytest.raises(ValueError, match="typed values give its values"):
        replace(first, typed=other.typed)


def _view_outcome(analysed: Any) -> tuple[Any, Any]:
    return analysed.view, analysed.outcome


def test_a_pack_s_digested_content_is_not_read_back(analyse: Analyse, shop: Shop) -> None:
    case = _cases()["cox_tiers_and_ages"]
    [analysed] = analyse(case["document"], shop(**case.get("shop", {})))
    content = digested(analysed.view, analysed.outcome).content()
    packed = type(analysed.view.analysis)(**{**vars(analysed.view.analysis), "pack": "tallies"})
    view = type(analysed.view)(**{**vars(analysed.view), "analysis": packed})
    with pytest.raises(ValueError, match="core analysis"):
        Digested.read(view, content)


def _leaves(value: Any, path: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
    if isinstance(value, dict):
        return [leaf for key, item in value.items() for leaf in _leaves(item, (*path, key))]
    if isinstance(value, list):
        return [leaf for index, item in enumerate(value) for leaf in _leaves(item, (*path, index))]
    return [path] if value is not None else []


def _changed(value: Any) -> Any:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value + 1
    if isinstance(value, float):
        return math.nextafter(value, math.inf)
    return f"{value} "


def _at(content: Any, path: tuple[Any, ...]) -> Any:
    for step in path:
        content = content[step]
    return content


def _set(content: Any, path: tuple[Any, ...], value: Any) -> Any:
    changed = copy.deepcopy(content)
    target = changed
    for step in path[:-1]:
        target = target[step]
    target[path[-1]] = value
    return changed


@pytest.mark.parametrize("name", sorted(_cases()))
def test_content_changed_anywhere_below_the_digest_s_resolution_is_not_read_back(
    analyse: Analyse, shop: Shop, name: str
) -> None:
    """The digest rounds numbers and reduces caveats, so a number changed in its last bit, a
    caveat's message or a true written as 1 keeps it; the content's hash does not, and a changed
    wording is refused before it (D399)."""
    analysed = _analysed(analyse, shop, name)
    content = json.loads(digested(analysed.view, analysed.outcome).content())
    paths = [path for path in _leaves(content) if path != ("hashed",)]
    assert paths
    for path in paths:
        changed = json.dumps(_set(content, path, _changed(_at(content, path)))).encode()
        with pytest.raises(ValueError, match=r"D374|D399|validation error"):
            Digested.read(analysed.view, changed)


def test_content_whose_hash_changed_is_not_read_back(analyse: Analyse, shop: Shop) -> None:
    analysed = _analysed(analyse, shop, "cox_tiers_and_ages")
    content = json.loads(digested(analysed.view, analysed.outcome).content())
    content["hashed"] = "sha256:" + "0" * 64
    with pytest.raises(ValueError, match="as it was written"):
        Digested.read(analysed.view, json.dumps(content).encode())


@pytest.mark.parametrize("name", sorted(_cases()))
def test_digested_content_whose_values_are_not_its_typed_values_as_json_is_refused(
    analyse: Analyse, shop: Shop, name: str
) -> None:
    """``True == 1`` in Python, but not as JSON text, nor in the envelope."""
    first = digested(*_view_outcome(_analysed(analyse, shop, name)))
    values = first.values.model_dump(mode="json")
    for path in _leaves(values):
        target = _at(values, path)
        if isinstance(target, bool) or (isinstance(target, int) and target in (0, 1)):
            other = int(target) if isinstance(target, bool) else bool(target)
            changed = type(first.values).model_validate(_set(values, path, other))
            with pytest.raises(ValueError, match="typed values give its values"):
                replace(first, values=changed)

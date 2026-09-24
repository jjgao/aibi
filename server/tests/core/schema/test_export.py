"""The checked-in JSON Schemas are current, valid, and describe real documents."""

import copy
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from aibi.core.schema.export import SCHEMAS, render
from aibi.core.schema.loading import load_document

SCHEMA_DIR = Path(__file__).resolve().parents[4] / "schemas"
DOCUMENTS = Path(__file__).parent / "documents"


def example(name: str) -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads((DOCUMENTS / f"{name}.json").read_text(encoding="utf-8"))
    return loaded


@pytest.mark.parametrize("name", sorted(SCHEMAS))
def test_checked_in_schema_is_current(name: str) -> None:
    checked_in = (SCHEMA_DIR / name).read_text(encoding="utf-8")
    assert checked_in == render(SCHEMAS[name]()), (
        f"schemas/{name} is stale: run `uv run python -m aibi.core.schema.export ../schemas`"
    )


@pytest.mark.parametrize("name", sorted(SCHEMAS))
def test_schema_is_valid_draft_2020_12(name: str) -> None:
    jsonschema.Draft202012Validator.check_schema(SCHEMAS[name]())


def test_schemas_never_allow_null() -> None:
    for build in SCHEMAS.values():
        assert '"null"' not in json.dumps(build())


def test_documents_validate_against_the_as_written_schema() -> None:
    as_written = SCHEMAS["document.as-written.schema.json"]()
    substituted = SCHEMAS["document.schema.json"]()
    for name in ("sites", "library"):
        jsonschema.validate(example(name), as_written)
    jsonschema.validate(example("library"), substituted)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(example("sites"), substituted)  # "$min_score" is not a number


def test_a_whole_cohort_may_come_from_a_parameter() -> None:
    document = {
        "aibi": "1",
        "dataset": "d",
        "unit": "t",
        "params": {"c": {"all": []}},
        "cohorts": {"everyone": "$c"},
    }
    jsonschema.validate(document, SCHEMAS["document.as-written.schema.json"]())


def _positions(value: Any, path: list[str | int]) -> Iterator[list[str | int]]:
    yield path
    if isinstance(value, dict):
        for key, member in value.items():
            if not (path == [] and key == "params"):
                yield from _positions(member, [*path, key])
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _positions(item, [*path, index])


def _put(document: Any, path: list[str | int], value: Any) -> None:
    for token in path[:-1]:
        document = document[token]
    document[path[-1]] = value


def _get(document: Any, path: list[str | int]) -> Any:
    for token in path:
        document = document[token]
    return document


TEXT_MEMBERS = {"notes", "note", "drafted_by"}


@pytest.mark.parametrize("name", ["sites", "library"])
def test_any_position_may_take_a_parameter(name: str) -> None:
    """Replacing any value with a reference to it keeps a document valid, for both judges."""
    result = load_document(json.dumps(example(name)))
    assert result.document is not None
    substituted = result.document.model_dump(mode="json")
    validator = jsonschema.Draft202012Validator(SCHEMAS["document.as-written.schema.json"]())
    checked = 0
    for path in _positions(substituted, []):
        if not path or path[-1] in TEXT_MEMBERS:
            continue
        document = copy.deepcopy(substituted)
        document["params"] = {"p": copy.deepcopy(_get(document, path))}
        _put(document, path, "$p")
        assert load_document(json.dumps(document)).refusals == [], path
        assert validator.is_valid(document), path
        checked += 1
    assert checked > 20


BASE: dict[str, Any] = {"aibi": "1", "dataset": "d", "unit": "t"}


def _with_clause(clause: Any) -> dict[str, Any]:
    return {**BASE, "cohorts": {"c": {"all": [clause]}}}


INVALID = [
    {**BASE, "cohorts": {"bad-name": {"all": []}}},
    {**BASE, "packs": {"Bad Pack": ">=1"}, "cohorts": {"c": {"all": []}}},
    {**BASE, "packs": {"core": ">=1"}, "cohorts": {"c": {"all": []}}},
    {**BASE, "params": {"bad-name": 1}, "cohorts": {"c": {"all": []}}},
    {**BASE, "cohorts": {"c": {"all": [], "dataset": "a", "datasets": ["a", "b"]}}},
    {**BASE, "cohorts": {"c": {"all": []}}, "views": [{"analysis": "a.b", "params": {"x": None}}]},
    {**BASE, "cohorts": {"c": {"all": []}}, "views": [{"analysis": "a.b", "cohorts": ["c", "c"]}]},
    _with_clause({"kind": "covered", "table": "m", "scope": {"Branch!": ["x"]}}),
    _with_clause({"kind": "value", "column": "t.c", "values": [1], "via": {"D-1": []}}),
    _with_clause({"kind": "value", "column": "t.c", "values": ["x" * 5000]}),
    _with_clause({"kind": "value", "column": "t.c", "range": {"gt": "x" * 5000}}),
    _with_clause({"kind": "value", "column": "t.c", "range": {}}),
    _with_clause({"kind": "value", "column": "t.c", "range": {"gt": 1, "gte": 2}}),
    _with_clause({"kind": "value", "column": "t.c", "values": [1], "range": {"gt": 1}}),
    _with_clause({"kind": "value", "column": "t.c", "op": "="}),
    _with_clause({"kind": "value", "column": "t.c"}),
    _with_clause({"kind": "value.x"}),
    _with_clause({"kind": "exists", "table": "s", "quantifier": "every", "min_count": 2}),
    _with_clause({"kind": "value", "column": "t.c", "values": ["$5"]}),
    _with_clause({"kind": "testpack.flag", "q": "$5"}),
    _with_clause({"kind": "value", "column": "t.c", "values": [6.022e23]}),
    _with_clause({"kind": "value", "column": "t.c", "range": {"gt": 1e16}}),
    _with_clause({"kind": "value", "column": "t__x.c", "values": [1]}),
    _with_clause({"kind": "value", "column": "t" * 65 + ".c", "values": [1]}),
    {**BASE, "unit": "t__state", "cohorts": {"c": {"all": []}}},
    {**BASE, "unit": "core:" + "a." * 130 + "a", "cohorts": {"c": {"all": []}}},
    {**BASE, "packs": {"a__b": ">=1"}, "cohorts": {"c": {"all": []}}},
    _with_clause({"kind": "ids", "ids": ["d__x:1"]}),
    _with_clause({"kind": "p." + "x" * 65}),
    {**BASE, "drafted_by": "model:a__b", "cohorts": {"c": {"all": []}}},
    {**BASE, "unit": "summary:x", "cohorts": {"c": {"all": []}}},
    {**BASE, "cohorts": {"c": {"all": []}}, "views": [{"analysis": "core.x"}]},
    _with_clause({"kind": "value", "column": "value:x", "values": [1]}),
]


@pytest.mark.parametrize("document", INVALID)
def test_the_schemas_refuse_what_the_loader_refuses(document: dict[str, Any]) -> None:
    assert load_document(json.dumps(document)).refusals
    as_written = jsonschema.Draft202012Validator(SCHEMAS["document.as-written.schema.json"]())
    assert not as_written.is_valid(document)


VALID = [
    {**BASE, "cohorts": {"c": {"all": []}}, "notes": "$100 grant"},
    {**BASE, "cohorts": {"c": {"all": []}}, "drafted_by": "agent:$bot"},
    _with_clause({"kind": "value", "column": "t.c", "values": ["$$5"]}),
    _with_clause({"kind": "exists", "table": "s", "min_count": 2, "quantifier": "some"}),
    {**BASE, "params": {"p": ["$5"], "q": {"k": "$5"}}, "cohorts": {"c": {"all": []}}},
    _with_clause({"kind": "testpack.flag", "q": "$$5", "n": [1, {"x": True}]}),
    _with_clause({"kind": "ids", "ids": ["d:x__y", "d:a:b"]}),
    {**BASE, "unit": "testpack:member", "cohorts": {"c": {"all": []}}},
    {**BASE, "cohorts": {"c": {"all": []}}, "views": [{"analysis": "testpack.enrichment"}]},
    _with_clause(
        {
            "kind": "value",
            "column": "t.c",
            "values": [1],
            "via": [{"rel": "rel:t." + "+".join(["c" * 64] * 16), "dir": "up"}],
        }
    ),
]


@pytest.mark.parametrize("document", VALID)
def test_the_schemas_accept_what_the_loader_accepts(document: dict[str, Any]) -> None:
    assert load_document(json.dumps(document)).refusals == []
    as_written = jsonschema.Draft202012Validator(SCHEMAS["document.as-written.schema.json"]())
    assert as_written.is_valid(document)


def test_objects_keyed_by_pattern_are_closed() -> None:
    def walk(node: Any) -> Iterator[dict[str, Any]]:
        if isinstance(node, dict):
            yield node
            for value in node.values():
                yield from walk(value)
        elif isinstance(node, list):
            for item in node:
                yield from walk(item)

    for build in SCHEMAS.values():
        for node in walk(build()):
            if "patternProperties" in node:
                assert node.get("additionalProperties") is False

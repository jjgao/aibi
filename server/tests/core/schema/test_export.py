"""The checked-in JSON Schemas are current, valid, and describe real documents."""

import copy
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from aibi.core.schema.export import SCHEMAS, render
from aibi.core.schema.loading import load_descriptor, load_document

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


@pytest.mark.parametrize("name", ["document.schema.json", "document.as-written.schema.json"])
def test_document_schemas_never_allow_null(name: str) -> None:
    assert '"null"' not in json.dumps(SCHEMAS[name]())


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


# --- The descriptor schema ------------------------------------------------------------------


def test_the_descriptor_schema_allows_null_only_where_it_is_declared() -> None:
    schema: dict[str, Any] = SCHEMAS["descriptor.schema.json"]()
    nullable = {
        (name, member)
        for name, definition in schema["$defs"].items()
        for member, value in definition.get("properties", {}).items()
        if {"type": "null"} in value.get("anyOf", [])
    }
    assert nullable == {
        ("TableFields", "primary_key"),
        ("Disclosure", "min_cell_count"),
        ("ConceptMapping", "transform"),
        ("AnalysisFields", "cross_dataset"),
    }
    assert '"default": null' not in json.dumps(schema)


def _descriptor(kind: str, id: str, fields: dict[str, Any]) -> dict[str, Any]:
    status = {"status": "imported", "by": "importer:files@0.1.0", "at": "2026-09-24T06:00:00Z"}
    pointers = ["/label", *(f"/fields/{name}" for name in fields)]
    curation = {pointer: dict(status) for pointer in pointers}
    return {
        "kind": kind,
        "id": id,
        "version": 1,
        "label": id,
        "fields": fields,
        "curation": curation,
    }


def _column(**fields: Any) -> dict[str, Any]:
    return _descriptor("column", "t.c", fields)


def _table(**fields: Any) -> dict[str, Any]:
    return _descriptor("table", "t", fields)


_PARSE = {"format": "csv", "delimiter": ",", "quote": '"', "header_row": 0, "skip_rows": 0}
_PARSE["encoding"] = "utf-8"
_ASSERTED_BY_AGENT = _table(role="entity")
_ASSERTED_BY_AGENT["curation"]["/fields/role"]["status"] = "asserted"
_ASSERTED_BY_AGENT["curation"]["/fields/role"]["by"] = "agent:helper"
_BAD_TIMESTAMP = _table(role="entity")
_BAD_TIMESTAMP["curation"]["/label"]["at"] = "2026-13-01T00:00:00Z"
_YEAR_ZERO = _table(role="entity")
_YEAR_ZERO["curation"]["/label"]["at"] = "0000-01-01T00:00:00Z"
_UNDECLARED_WITH_VALUE = _table(role="entity")
_UNDECLARED_WITH_VALUE["curation"]["/fields/role"]["status"] = "undeclared"
_LARGE_INFERRED = _table(role="entity")
_LARGE_INFERRED["curation"]["/fields/role"]["inferred"] = {"n": [2**60]}
_LARGE_EXTENSION = _table()
_LARGE_EXTENSION["extensions"] = {"p": {"x": {"y": 2**60}}}
_LARGE_EXTENSION["curation"]["/extensions/p/x"] = dict(_LARGE_EXTENSION["curation"]["/label"])

INVALID_DESCRIPTORS = [
    _table(grain=None),
    _table(observation_window={"start": "x"}),
    _table(primary_key=["a", "a"]),
    _descriptor("table", "dataset", {}),
    {**_table(), "version": 2**60},
    _table(source={"kind": "database", "name": "t", "original_name": "T", "parse": _PARSE}),
    _table(
        source={
            "kind": "file",
            "name": "t",
            "original_name": "T",
            "parse": {**_PARSE, "format": "tsv"},
        }
    ),
    _table(
        source={
            "kind": "file",
            "name": "t",
            "original_name": "T",
            "parse": {**_PARSE, "delimiter": ",,"},
        }
    ),
    _column(datatype="string", units="a"),
    _column(units="a"),
    _column(datatype="category", range={"min": 0, "max": 1}),
    _column(datatype="integer", permissible_values={"values": [{"value": "1"}]}),
    _column(datatype="category", permissible_values={"values": [{"value": 1}]}),
    _column(datatype="list<category>"),
    _column(datatype="string", list_syntax={"format": "json"}),
    _column(datatype="list<category>", list_syntax={"format": "delimited"}),
    _column(datatype="list<category>", list_syntax={"format": "json", "delimiter": ";"}),
    _column(derived={"op": "arith", "operator": "+", "args": ["a", 1, 2]}),
    _descriptor("dataset", "dataset", {"disclosure": {"allow_row_ids": False}}),
    _descriptor(
        "dataset", "dataset", {"disclosure": {"allow_row_ids": False, "min_cell_count": None}}
    ),
    _descriptor("coverage", "cov:m.s", {"relationship": "rel:m.s", "parents": "undeclared"}),
    _descriptor(
        "coverage",
        "cov:m.s",
        {
            "relationship": "rel:m.s",
            "parents": {
                "assignment": {"table": "a", "parent_columns": {"k": "k"}, "group_column": "g"},
                "groups": {"table": "g", "group_column": "g", "covers_all_column": "all"},
            },
        },
    ),
    _descriptor("endpoint", "ep:x", {"entry": "undeclared"}),
    {
        "kind": "concept",
        "id": "core:thing",
        "version": 1,
        "label": "Thing",
        "fields": {"sort": "table", "units": "a"},
    },
    _ASSERTED_BY_AGENT,
    _BAD_TIMESTAMP,
    _YEAR_ZERO,
    _UNDECLARED_WITH_VALUE,
    _LARGE_INFERRED,
    _LARGE_EXTENSION,
    _table(maps_to={"concept": "core:person", "transform": {"unit_from": "d", "unit_to": "a"}}),
    _descriptor("dataset", "dataset", {"domain_tags": [""]}),
    _descriptor("coverage", "cov:m.s", {"relationship": "rel:m.s", "record_filter": {"k": [1]}}),
    _descriptor(
        "coverage", "cov:m.s", {"relationship": "rel:m.s", "record_filter": {"k": ["a", "a"]}}
    ),
    _descriptor("endpoint", "ep:x", {"event_coding": {"event": [1, 1.0], "censored": []}}),
]


@pytest.mark.parametrize("descriptor", INVALID_DESCRIPTORS)
def test_the_descriptor_schema_refuses_what_the_loader_refuses(descriptor: dict[str, Any]) -> None:
    assert load_descriptor(json.dumps(descriptor)).refusals
    validator = jsonschema.Draft202012Validator(SCHEMAS["descriptor.schema.json"]())
    assert not validator.is_valid(descriptor)


VALID_DESCRIPTORS = [
    _table(primary_key=None),
    _table(maps_to={"concept": "core:person", "transform": None}),
    _descriptor("dataset", "dataset", {"disclosure": {"min_cell_count": None}}),
    _descriptor(
        "dataset", "dataset", {"disclosure": {"allow_row_ids": False, "min_cell_count": 5}}
    ),
    _table(
        source={
            "kind": "file",
            "name": "t",
            "original_name": "",
            "parse": {**_PARSE, "format": "tsv", "delimiter": "\t"},
        }
    ),
    _column(datatype="list<category>", list_syntax={"format": "json"}),
    _column(datatype="number", units="a", range={"min": 0, "max": 1}),
]


@pytest.mark.parametrize("descriptor", VALID_DESCRIPTORS)
def test_the_descriptor_schema_accepts_what_the_loader_accepts(descriptor: dict[str, Any]) -> None:
    assert load_descriptor(json.dumps(descriptor)).refusals == []
    validator = jsonschema.Draft202012Validator(SCHEMAS["descriptor.schema.json"]())
    assert validator.is_valid(descriptor)


_KEYWORDS = frozenset(
    [
        "$schema",
        "$id",
        "$ref",
        "$defs",
        "$comment",
        "$anchor",
        "$dynamicRef",
        "$dynamicAnchor",
        "$vocabulary",
        "type",
        "enum",
        "const",
        "multipleOf",
        "maximum",
        "exclusiveMaximum",
        "minimum",
        "exclusiveMinimum",
        "maxLength",
        "minLength",
        "pattern",
        "maxItems",
        "minItems",
        "uniqueItems",
        "maxContains",
        "minContains",
        "maxProperties",
        "minProperties",
        "required",
        "dependentRequired",
        "properties",
        "patternProperties",
        "additionalProperties",
        "propertyNames",
        "items",
        "prefixItems",
        "contains",
        "allOf",
        "anyOf",
        "oneOf",
        "not",
        "if",
        "then",
        "else",
        "dependentSchemas",
        "unevaluatedItems",
        "unevaluatedProperties",
        "format",
        "contentEncoding",
        "contentMediaType",
        "contentSchema",
        "title",
        "description",
        "default",
        "deprecated",
        "readOnly",
        "writeOnly",
        "examples",
        "discriminator",
    ]
)
"""The keywords of JSON Schema 2020-12, and the ``discriminator`` annotation Pydantic writes."""
_SCHEMA_MAPS = ("properties", "patternProperties", "$defs", "dependentSchemas")
_SCHEMA_LISTS = ("allOf", "anyOf", "oneOf", "prefixItems")
_SCHEMA_VALUES = (
    "items",
    "additionalProperties",
    "propertyNames",
    "contains",
    "not",
    "if",
    "then",
    "else",
    "unevaluatedItems",
    "unevaluatedProperties",
    "contentSchema",
)


def _unknown_keywords(schema: Any, at: str = "#") -> Iterator[str]:
    """Every member of a schema object that is neither a keyword nor an ``x-`` extension."""
    if not isinstance(schema, dict):
        return
    for key, value in schema.items():
        if key not in _KEYWORDS and not key.startswith("x-"):
            yield f"{at}/{key}"
        if key in _SCHEMA_MAPS:
            for name, member in value.items():
                yield from _unknown_keywords(member, f"{at}/{key}/{name}")
        elif key in _SCHEMA_LISTS:
            for index, member in enumerate(value):
                yield from _unknown_keywords(member, f"{at}/{key}/{index}")
        elif key in _SCHEMA_VALUES:
            yield from _unknown_keywords(value, f"{at}/{key}")


@pytest.mark.parametrize("name", sorted(SCHEMAS))
def test_schemas_use_only_json_schema_keywords(name: str) -> None:
    """Pydantic writes a constraint it cannot translate under its own name (``gt``), which a
    validator ignores."""
    assert list(_unknown_keywords(SCHEMAS[name]())) == []


def test_the_keyword_walker_finds_unknown_members() -> None:
    schema = {"properties": {"gt": {"type": "number", "gt": 0}}, "allOf": [{"lt": 1}]}
    assert list(_unknown_keywords(schema)) == ["#/properties/gt/gt", "#/allOf/0/lt"]

"""JSON Schema export (SPEC §12.4): ``uv run python -m aibi.core.schema.export <directory>``.

Two schemas describe documents: the document after substitution, and the document as written,
in which any value may instead be a ``"$name"`` parameter reference (SPEC §7.1). Documents never
contain ``null``, so the ``null`` alternatives Pydantic adds for optional members are removed.
Objects whose keys follow a pattern are closed to other keys, as the models are.

The loader is the authority: the schemas describe what it accepts as closely as JSON Schema can,
and a test checks that they agree on a set of documents. The descriptor schema does not say which
clauses a coverage's ``parent_scope`` may hold (no pack, ids or cohort leaves, and no ``via`` by
dataset); the loader refuses the others.
"""

import json
import sys
from pathlib import Path
from typing import cast

from pydantic import JsonValue, TypeAdapter

from aibi.core.schema.descriptors import DESCRIPTOR_JSON_MARK, DescModel, Descriptor
from aibi.core.schema.document import DOCUMENT_JSON_MARK, Document
from aibi.core.schema.ids import MAX_SAFE_INTEGER, NAME

SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"
PARAMETER_REFERENCE: dict[str, JsonValue] = {
    "type": "string",
    "pattern": f"^\\$(?:{NAME}|\\$[\\s\\S]*)$",
    "description": 'A parameter reference, "$name", or a literal string written "$$…"',
}


def _json_value(name: str, *, null: bool = False) -> dict[str, JsonValue]:
    """A definition of any JSON value, ``null`` only if asked, named ``name`` for its recursion.

    Numbers are bounded as the models bound them (SPEC §7.1).
    """
    return {
        "description": "Any JSON value" if null else "Any JSON value but null",
        "anyOf": [
            {"type": ["string", "boolean", "null"] if null else ["string", "boolean"]},
            {"type": "number", "minimum": -MAX_SAFE_INTEGER, "maximum": MAX_SAFE_INTEGER},
            {"type": "array", "items": {"$ref": f"#/$defs/{name}"}},
            {"type": "object", "additionalProperties": {"$ref": f"#/$defs/{name}"}},
        ],
    }


DOCUMENT_JSON = _json_value("DocumentJson")
DESCRIPTOR_JSON = _json_value("DescriptorJson", null=True)
TEXT_MEMBERS = frozenset({"notes", "note", "drafted_by"})
"""Plain-text members, never substituted (SPEC §7.1)."""

JsonObject = dict[str, JsonValue]


def _without_null(node: JsonValue) -> JsonValue:
    if isinstance(node, list):
        return [_without_null(item) for item in node]
    if not isinstance(node, dict):
        return node
    result: JsonObject = {key: _without_null(value) for key, value in node.items()}
    any_of = result.get("anyOf")
    if isinstance(any_of, list):
        kept = [member for member in any_of if member != {"type": "null"}]
        if len(kept) != len(any_of):
            del result["anyOf"]
            if len(kept) == 1 and isinstance(kept[0], dict):
                result = {**kept[0], **result}
            else:
                result["anyOf"] = kept
    if result.get("default", 0) is None:
        del result["default"]
    return result


def _closed(node: JsonValue) -> JsonValue:
    """Add ``additionalProperties: false`` beside ``patternProperties``, and define JSON values."""
    if isinstance(node, list):
        return [_closed(item) for item in node]
    if not isinstance(node, dict):
        return node
    if node == {DOCUMENT_JSON_MARK: True}:
        return {"$ref": "#/$defs/DocumentJson"}
    if node.get(DESCRIPTOR_JSON_MARK) is True:
        rest = {key: _closed(value) for key, value in node.items() if key != DESCRIPTOR_JSON_MARK}
        return {"$ref": "#/$defs/DescriptorJson", **rest}
    result: JsonObject = {key: _closed(value) for key, value in node.items()}
    if "patternProperties" in result and "additionalProperties" not in result:
        result["additionalProperties"] = False
    return result


def document_schema() -> JsonObject:
    schema = cast(JsonObject, _without_null(cast(JsonValue, Document.model_json_schema())))
    schema = cast(JsonObject, _closed(schema))
    defs = cast(JsonObject, schema.setdefault("$defs", {}))
    defs["DocumentJson"] = DOCUMENT_JSON
    return {"$schema": SCHEMA_DIALECT, "$id": "document.schema.json", **schema}


def _allow_references(node: JsonValue, *, skip: bool = False) -> JsonValue:
    """Let every value position accept a parameter reference, except inside ``params``."""
    if isinstance(node, list):
        return [_allow_references(item) for item in node]
    if not isinstance(node, dict):
        return node
    result: JsonObject = {}
    for key, value in node.items():
        if key == "properties" and isinstance(value, dict):
            result[key] = {
                name: member
                if (name == "params" and skip) or name in TEXT_MEMBERS
                else _wrap(member)
                for name, member in value.items()
            }
        elif key == "oneOf":
            # A "$name" in a union's tag member can match several members: the loader decides.
            converted = _allow_references(value)
            if "anyOf" in node:
                result["allOf"] = [{"anyOf": converted}]
            else:
                result["anyOf"] = converted
        elif key in ("items", "additionalProperties") and isinstance(value, dict):
            result[key] = _wrap(value)
        elif key == "patternProperties" and isinstance(value, dict):
            result[key] = {pattern: _wrap(member) for pattern, member in value.items()}
        elif key == "$defs" and isinstance(value, dict):
            result[key] = {name: _allow_references(member) for name, member in value.items()}
        else:
            result[key] = _allow_references(value)
    return result


def _wrap(member: JsonValue) -> JsonValue:
    """A position that also takes a reference; any other string starting with ``$`` is refused."""
    inner = _allow_references(member)
    return {
        "anyOf": [inner, {"$ref": "#/$defs/ParameterReference"}],
        "if": {"type": "string", "pattern": "^\\$"},
        "then": {"$ref": "#/$defs/ParameterReference"},
    }


def _renamed(node: JsonValue, old: str, new: str) -> JsonValue:
    if isinstance(node, list):
        return [_renamed(item, old, new) for item in node]
    if not isinstance(node, dict):
        return node
    return {
        key: new if key == "$ref" and value == old else _renamed(value, old, new)
        for key, value in node.items()
    }


def document_as_written_schema() -> JsonObject:
    schema = document_schema()
    transformed = cast(JsonObject, _allow_references(schema, skip=True))
    defs = cast(JsonObject, transformed.setdefault("$defs", {}))
    defs["ParameterReference"] = PARAMETER_REFERENCE
    # Parameter values are taken verbatim: nothing in them is a reference (SPEC §7.1).
    defs["ParameterValue"] = _json_value("ParameterValue")
    properties = cast(JsonObject, transformed["properties"])
    properties["params"] = _renamed(
        properties["params"], "#/$defs/DocumentJson", "#/$defs/ParameterValue"
    )
    transformed["$id"] = "document.as-written.schema.json"
    return transformed


def _descriptor_models() -> dict[str, type[DescModel]]:
    models: dict[str, type[DescModel]] = {}
    pending: list[type[DescModel]] = [DescModel]
    while pending:
        model = pending.pop()
        models[model.__name__] = model
        pending.extend(model.__subclasses__())
    return models


def _declared_nulls(schema: JsonObject) -> JsonObject:
    """Keep ``null`` only for the members of models that declare it (SPEC §5.1, §7.1).

    Absent and ``null`` differ for those members, so none of them has a default.
    """
    models = _descriptor_models()
    for name, definition in cast(JsonObject, schema.get("$defs", {})).items():
        properties = definition.get("properties") if isinstance(definition, dict) else None
        if not isinstance(properties, dict):
            continue
        # Other models are those of documents (a coverage's parent_scope), which never hold null.
        nullable = models[name].nullable() if name in models else frozenset[str]()
        for member, value in list(properties.items()):
            if member not in nullable:
                properties[member] = _without_null(value)
            elif isinstance(value, dict) and value.get("default", 0) is None:
                del value["default"]
    return schema


def descriptor_schema() -> JsonObject:
    """Any descriptor, chosen by ``kind`` (SPEC §5). ``null`` appears only where it is declared."""
    generated = cast(JsonObject, TypeAdapter(Descriptor).json_schema())
    schema = cast(JsonObject, _closed(cast(JsonValue, _declared_nulls(generated))))
    defs = cast(JsonObject, schema.setdefault("$defs", {}))
    defs["DescriptorJson"] = DESCRIPTOR_JSON
    if '"#/$defs/DocumentJson"' in json.dumps(schema):
        defs["DocumentJson"] = DOCUMENT_JSON
    return {"$schema": SCHEMA_DIALECT, "$id": "descriptor.schema.json", **schema}


SCHEMAS = {
    "document.schema.json": document_schema,
    "document.as-written.schema.json": document_as_written_schema,
    "descriptor.schema.json": descriptor_schema,
}


def render(schema: JsonObject) -> str:
    return json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def write(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for name, build in SCHEMAS.items():
        (directory / name).write_text(render(build()), encoding="utf-8")


if __name__ == "__main__":  # pragma: no cover
    write(Path(sys.argv[1]) if len(sys.argv) > 1 else Path("../schemas"))

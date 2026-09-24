"""JSON Schema export (SPEC §12.4): ``uv run python -m aibi.core.schema.export <directory>``.

Two schemas describe documents: the document after substitution, and the document as written,
in which any value may instead be a ``"$name"`` parameter reference (SPEC §7.1). Documents never
contain ``null``, so the ``null`` alternatives Pydantic adds for optional members are removed.
"""

import json
import sys
from pathlib import Path
from typing import cast

from pydantic import JsonValue

from aibi.core.schema.document import Document
from aibi.core.schema.ids import NAME

SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"
PARAMETER_REFERENCE: dict[str, JsonValue] = {
    "type": "string",
    "pattern": f"^\\$(?:{NAME}|\\$.*)$",
    "description": 'A parameter reference, "$name", or a literal string written "$$…"',
}

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


def document_schema() -> JsonObject:
    schema = cast(JsonObject, _without_null(cast(JsonValue, Document.model_json_schema())))
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
                name: member if name == "params" and skip else _wrap(member)
                for name, member in value.items()
            }
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
    inner = _allow_references(member)
    return {"anyOf": [inner, {"$ref": "#/$defs/ParameterReference"}]}


def document_as_written_schema() -> JsonObject:
    schema = document_schema()
    transformed = cast(JsonObject, _allow_references(schema, skip=True))
    defs = cast(JsonObject, transformed.setdefault("$defs", {}))
    defs["ParameterReference"] = PARAMETER_REFERENCE
    transformed["$id"] = "document.as-written.schema.json"
    return transformed


SCHEMAS = {
    "document.schema.json": document_schema,
    "document.as-written.schema.json": document_as_written_schema,
}


def render(schema: JsonObject) -> str:
    return json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def write(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for name, build in SCHEMAS.items():
        (directory / name).write_text(render(build()), encoding="utf-8")


if __name__ == "__main__":  # pragma: no cover
    write(Path(sys.argv[1]) if len(sys.argv) > 1 else Path("../schemas"))

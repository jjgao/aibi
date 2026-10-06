"""The keywords the OpenAPI document and the tool schemas may use, and where (SPEC §12.4, D416).

A tripwire, not a claim about rendering: how a code generator renders a schema depends on the
whole set of keywords at a node, which a list by keyword cannot decide (the web client's own
build checks the types it gets, M5.1c-1). The list makes a new keyword, or a keyword in a new
place, a deliberate edit that a reviewer sees: ``api.openapi`` refuses to write a document, and
lints ``schemas/tool.*``, holding a (placement, keyword) pair that is not listed.

A placement is where a schema sits: ``component`` (a component's root), ``media`` (a request's
or a response's body), ``parameter``, ``header``, ``file`` and ``definition`` (a tool schema's
root and its ``$defs``), or the keyword it sits below (``property``, ``items``,
``anyOf-member`` and so on).
"""

from collections.abc import Iterator
from typing import cast

from pydantic import JsonValue

from aibi.core.api.openapi_normalise import children

JsonObject = dict[str, JsonValue]

_PLACEMENTS = {
    "properties": "property",
    "patternProperties": "patternProperty",
    "$defs": "definition",
    "dependentSchemas": "dependentSchema",
    "allOf": "allOf-member",
    "anyOf": "anyOf-member",
    "oneOf": "oneOf-member",
    "prefixItems": "prefixItems-member",
}

_LISTED: dict[str, tuple[str, ...]] = {
    "additionalProperties": (
        "$ref",
        "anyOf",
        "if",
        "maximum",
        "minimum",
        "then",
        "type",
        "x-aibi-server-number",
    ),
    "allOf-member": (
        "$ref",
        "allOf",
        "anyOf",
        "else",
        "if",
        "items",
        "not",
        "prefixItems",
        "properties",
        "then",
    ),
    "anyOf-member": (
        "$ref",
        "additionalProperties",
        "anyOf",
        "const",
        "default",
        "enum",
        "items",
        "maxItems",
        "maxLength",
        "maxProperties",
        "maximum",
        "minItems",
        "minProperties",
        "minimum",
        "not",
        "pattern",
        "patternProperties",
        "propertyNames",
        "required",
        "title",
        "type",
        "uniqueItems",
        "x-aibi-data",
        "x-aibi-server-number",
    ),
    "component": (
        "additionalProperties",
        "allOf",
        "anyOf",
        "dependentRequired",
        "description",
        "else",
        "enum",
        "if",
        "minProperties",
        "pattern",
        "properties",
        "required",
        "then",
        "title",
        "type",
        "x-aibi-data",
        "x-aibi-json",
    ),
    "contains": (
        "const",
        "properties",
    ),
    "definition": (
        "additionalProperties",
        "allOf",
        "anyOf",
        "dependentRequired",
        "description",
        "else",
        "enum",
        "if",
        "minProperties",
        "not",
        "pattern",
        "properties",
        "required",
        "then",
        "title",
        "type",
        "x-aibi-data",
    ),
    "else": (
        "properties",
        "required",
    ),
    "file": (
        "$defs",
        "$id",
        "$schema",
        "additionalProperties",
        "description",
        "properties",
        "required",
        "title",
        "type",
    ),
    "header": (
        "pattern",
        "type",
    ),
    "if": (
        "pattern",
        "properties",
        "required",
        "type",
    ),
    "items": (
        "$ref",
        "additionalProperties",
        "allOf",
        "anyOf",
        "enum",
        "if",
        "items",
        "maxLength",
        "maximum",
        "minimum",
        "not",
        "oneOf",
        "pattern",
        "properties",
        "then",
        "type",
        "x-aibi-data",
        "x-aibi-server-number",
    ),
    "media": (
        "$ref",
        "format",
        "type",
    ),
    "not": (
        "anyOf",
        "const",
        "pattern",
        "properties",
        "required",
    ),
    "oneOf-member": (
        "$ref",
        "additionalProperties",
        "const",
        "enum",
        "items",
        "maxItems",
        "maxLength",
        "maxProperties",
        "maximum",
        "minItems",
        "minimum",
        "not",
        "oneOf",
        "pattern",
        "patternProperties",
        "propertyNames",
        "type",
    ),
    "parameter": (
        "maxLength",
        "maximum",
        "minLength",
        "minimum",
        "pattern",
        "type",
    ),
    "patternProperty": (
        "$ref",
        "anyOf",
        "const",
        "if",
        "items",
        "maxItems",
        "maxLength",
        "maximum",
        "minItems",
        "minimum",
        "then",
        "type",
        "x-aibi-server-number",
    ),
    "prefixItems-member": ("properties",),
    "property": (
        "$ref",
        "additionalProperties",
        "allOf",
        "anyOf",
        "const",
        "contains",
        "default",
        "description",
        "enum",
        "exclusiveMaximum",
        "exclusiveMinimum",
        "if",
        "items",
        "maxContains",
        "maxItems",
        "maxLength",
        "maxProperties",
        "maximum",
        "minContains",
        "minItems",
        "minLength",
        "minProperties",
        "minimum",
        "not",
        "oneOf",
        "pattern",
        "patternProperties",
        "properties",
        "propertyNames",
        "required",
        "then",
        "title",
        "type",
        "uniqueItems",
        "x-aibi-computed",
        "x-aibi-data",
        "x-aibi-server-number",
    ),
    "propertyNames": (
        "$ref",
        "enum",
        "maxLength",
        "minLength",
        "not",
        "x-aibi-data",
    ),
    "then": (
        "$ref",
        "properties",
        "required",
    ),
}
VOCABULARY: frozenset[tuple[str, str]] = frozenset(
    (placement, keyword) for placement, keywords in _LISTED.items() for keyword in keywords
)
"""Every (placement, keyword) pair the document and the tool schemas may use."""


def census(schema: JsonValue, placement: str) -> Iterator[tuple[str, str]]:
    """Every (placement, keyword) pair of ``schema`` and the schemas below it."""
    if not isinstance(schema, dict):
        return
    for key in schema:
        yield placement, key
    for key, _, member in children(schema):
        yield from census(member, _PLACEMENTS.get(key, key))


def document_census(document: JsonObject) -> Iterator[tuple[str, str]]:
    """Every (placement, keyword) pair of an OpenAPI document's schemas."""
    components = cast(JsonObject, document["components"])
    for schema in cast(JsonObject, components["schemas"]).values():
        yield from census(schema, "component")
    for operations in cast(JsonObject, document["paths"]).values():
        for operation in cast(dict[str, JsonObject], operations).values():
            for parameter in cast(list[JsonObject], operation.get("parameters", [])):
                yield from census(parameter["schema"], "parameter")
            body = cast(JsonObject, operation.get("requestBody", {}))
            for media in cast(dict[str, JsonObject], body.get("content", {})).values():
                yield from census(media["schema"], "media")
            for response in cast(dict[str, JsonObject], operation["responses"]).values():
                for media in cast(dict[str, JsonObject], response.get("content", {})).values():
                    yield from census(media["schema"], "media")
                for header in cast(dict[str, JsonObject], response.get("headers", {})).values():
                    yield from census(header["schema"], "header")


def unlisted(document: JsonObject, tool_schemas: dict[str, JsonObject]) -> list[str]:
    """The pairs of the document and the tool schemas the vocabulary does not list."""
    found = {("openapi.json", pair) for pair in document_census(document)}
    for name, schema in tool_schemas.items():
        found |= {(name, pair) for pair in census(schema, "file")}
    return sorted(
        f"{source}: {keyword} at {placement}"
        for source, (placement, keyword) in found
        if (placement, keyword) not in VOCABULARY
    )


__all__ = ["VOCABULARY", "census", "document_census", "unlisted"]

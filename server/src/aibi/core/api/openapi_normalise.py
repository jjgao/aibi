"""The OpenAPI document's rewrites (SPEC §12.4, D416): a named class of local equivalences.

The document's schemas are the builders' (``schema.export``), rewritten only where a rewrite keeps
the instances a schema accepts, by a rule that looks at one node and never at what its siblings
mean:

- **R1** moves a constraint-only ``anyOf``, ``oneOf`` or ``not`` (one whose members say nothing
  of a type: no ``type``, ``$ref``, ``properties``, ``items``, ``const``, ``enum`` or composition)
  that sits beside ``properties`` into an ``allOf`` member of its own: the same constraint, as a
  conjunct.
- **R2** gives a typeless member of a union (no ``type``, ``$ref``, ``const``, ``enum`` or
  composition) its parent's own ``type``, which already restricts every instance the member could
  accept; not inside a ``not``, ``if``, ``then``, ``else``, ``contains`` or ``propertyNames``,
  which only constrain.
- **R3** is the builders' own: ``export.request_schema`` names a request's JSON values
  ``RequestJson``, so the tool schemas, MCP's ``inputSchema`` and the document agree.
- **R4** gives each parameter's schema without the ``null`` Pydantic adds for an optional one (an
  absent parameter is omitted) and without its ``title``, then R1 and R2.
- **R5** declares every response header a string (``Retry-After`` as its digits).

Keywords whose meaning depends on their siblings are never split or moved (``NEVER_SPLIT``):
``prefixItems`` and ``items``; ``contains``, ``minContains`` and ``maxContains``; ``if``, ``then``
and ``else``; ``properties``, ``patternProperties`` and ``additionalProperties``; the
``unevaluated*`` and ``dependent*`` keywords. A test compares every node of the builders' schemas
with the document's for these.
"""

from typing import cast

from pydantic import JsonValue

from aibi.core.schema.export import without_null

JsonObject = dict[str, JsonValue]

NEVER_SPLIT = frozenset(
    {
        "prefixItems",
        "items",
        "contains",
        "minContains",
        "maxContains",
        "if",
        "then",
        "else",
        "properties",
        "patternProperties",
        "additionalProperties",
        "unevaluatedItems",
        "unevaluatedProperties",
        "dependentRequired",
        "dependentSchemas",
    }
)
"""Keywords no rewrite splits or moves: each is read together with a sibling."""
CONSTRAINT_CONTEXTS = frozenset({"not", "if", "then", "else", "contains", "propertyNames"})
"""Keywords whose schemas only constrain: R2 leaves the unions below them alone."""
UNIONS = ("anyOf", "oneOf")
_TYPING = frozenset(
    {"type", "$ref", "properties", "items", "enum", "const", "anyOf", "oneOf", "allOf"}
)
_TYPED = frozenset({"type", "$ref", "const", "enum", "allOf", "anyOf", "oneOf", "not"})
RETRY_AFTER: JsonObject = {"type": "string", "pattern": "^[0-9]+$"}
"""R5: ``Retry-After``, in seconds."""
HEADER_TEXT: JsonObject = {"type": "string"}
"""R5: any other response header."""


def constraint_only(member: JsonValue) -> bool:
    """Whether a schema says nothing of its instances' type: a constraint alone."""
    return isinstance(member, dict) and not (_TYPING & member.keys())


def typeless(member: JsonValue) -> bool:
    """Whether a union member has no ``type``, ``$ref``, ``const``, ``enum`` or composition."""
    return isinstance(member, dict) and not (_TYPED & member.keys())


def movable(node: JsonObject, key: str) -> bool:
    """Whether R1 moves ``node[key]`` (``anyOf``, ``oneOf`` or ``not``) beside ``properties``."""
    if "properties" not in node or key not in node:
        return False
    value = node[key]
    if key == "not":
        inner = value.get("anyOf") if isinstance(value, dict) else None
        return constraint_only(value) or (
            isinstance(inner, list) and all(constraint_only(member) for member in inner)
        )
    return isinstance(value, list) and all(constraint_only(member) for member in value)


SCHEMA_MAPS = ("properties", "patternProperties", "$defs", "dependentSchemas")
"""Keywords whose value maps names to schemas."""
SCHEMA_LISTS = ("allOf", "anyOf", "oneOf", "prefixItems")
"""Keywords whose value is a list of schemas."""
SCHEMA_VALUES = (
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
"""Keywords whose value is one schema; any other keyword's value is data, never a schema."""


def children(node: JsonObject) -> list[tuple[str, str, JsonValue]]:
    """The schemas directly below ``node``: (keyword, name or index, schema), never data."""
    found: list[tuple[str, str, JsonValue]] = []
    for key, value in node.items():
        if key in SCHEMA_MAPS and isinstance(value, dict):
            found.extend((key, name, member) for name, member in value.items())
        elif key in SCHEMA_LISTS and isinstance(value, list):
            found.extend((key, str(index), member) for index, member in enumerate(value))
        elif key in SCHEMA_VALUES:
            found.append((key, "", value))
    return found


def conjunct(node: JsonObject, keyword: str | None) -> bool:
    """Whether ``node``, below ``keyword``, is a union alone in an ``allOf`` member (R1's own
    conjuncts)."""
    return keyword == "allOf" and len(node) == 1 and node.keys() <= set(UNIONS)


def _rewritten(node: JsonValue, constraint: bool) -> JsonValue:
    if not isinstance(node, dict):
        return node
    result: JsonObject = dict(node)
    for key, value in node.items():
        below = constraint or key in CONSTRAINT_CONTEXTS
        if key in SCHEMA_MAPS and isinstance(value, dict):
            result[key] = {name: _rewritten(member, below) for name, member in value.items()}
        elif key in SCHEMA_LISTS and isinstance(value, list):
            result[key] = [_rewritten(member, below) for member in value]
        elif key in SCHEMA_VALUES:
            result[key] = _rewritten(value, below)
    moved: list[JsonValue] = [
        {key: result.pop(key)} for key in (*UNIONS, "not") if movable(result, key)
    ]
    if moved:
        result["allOf"] = [*cast(list[JsonValue], result.get("allOf", [])), *moved]
    parent = result.get("type")
    if parent is not None and not constraint:
        for key in UNIONS:
            members = result.get(key)
            if isinstance(members, list):
                result[key] = [
                    {**member, "type": parent} if typeless(member) else member
                    for member in cast(list[JsonObject], members)
                ]
    return result


def normalised(schema: JsonValue) -> JsonValue:
    """``schema`` with R1 and R2 applied at every node; ``schema`` itself is left as it was."""
    return _rewritten(schema, False)


def untyped(
    schema: JsonValue, at: str = "#", *, keyword: str | None = None, constraint: bool = False
) -> list[str]:
    """The typeless union members R2 could not type (their parent has no ``type``), outside the
    constraint contexts and R1's conjuncts: the document refuses any."""
    found: list[str] = []
    if not isinstance(schema, dict):
        return found
    if not constraint and not conjunct(schema, keyword):
        for key in UNIONS:
            members = schema.get(key)
            if isinstance(members, list):
                found.extend(
                    f"{at}/{key}/{index}"
                    for index, member in enumerate(members)
                    if typeless(member)
                )
    for key, name, member in children(schema):
        where = f"{at}/{key}/{name}" if name else f"{at}/{key}"
        below = constraint or key in CONSTRAINT_CONTEXTS
        found.extend(untyped(member, where, keyword=key, constraint=below))
    return found


def parameter(schema: JsonValue) -> JsonValue:
    """R4: a parameter's schema without ``null`` and its ``title``, then R1 and R2."""
    plain = without_null(schema)
    if isinstance(plain, dict):
        plain = {key: value for key, value in plain.items() if key != "title"}
    return normalised(plain)


__all__ = [
    "CONSTRAINT_CONTEXTS",
    "HEADER_TEXT",
    "NEVER_SPLIT",
    "RETRY_AFTER",
    "SCHEMA_LISTS",
    "SCHEMA_MAPS",
    "SCHEMA_VALUES",
    "UNIONS",
    "children",
    "conjunct",
    "constraint_only",
    "movable",
    "normalised",
    "parameter",
    "typeless",
    "untyped",
]

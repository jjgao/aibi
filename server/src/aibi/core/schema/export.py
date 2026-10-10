"""JSON Schema export (SPEC §12.4): ``uv run python -m aibi.core.schema.export <directory>``.

Two schemas describe documents: the document after substitution, and the document as written,
in which any value may instead be a ``"$name"`` parameter reference (SPEC §7.1). Documents never
contain ``null``, so the ``null`` alternatives Pydantic adds for optional members are removed.
Objects whose keys follow a pattern are closed to other keys, as the models are.

The loader is the authority: the schemas describe what it accepts as closely as JSON Schema can,
and a test checks that they agree on a set of documents. The descriptor schema does not say which
clauses a coverage's ``parent_scope`` may hold (no pack, ids or cohort leaves, and no ``via`` by
dataset); the loader refuses the others.

Names (D416): a definition is written only through ``put`` (``_define``), which refuses a name
already holding another body, compared as canonical text (``same``), never with ``==``, where
``true``, ``1`` and ``1.0`` are equal. Canonical text refuses rather than equates what ``json``
would write alike (a key that is not a string, a tuple, a subclass of a string or a number, a
circular structure, a number that is not finite, a dict forging a marker); a value that is no JSON
at all (a route, a partial, a class) compares by identity. The builders' own definitions are
``RESERVED``, written by ``_define_reserved`` alone; a class Pydantic names like one is refused
(``Strict``), as are two classes Pydantic gives one name with different bodies (compared as
Pydantic writes them, after its own renaming). A definition of any JSON value carries a marker
whose value is an object of this module's (``_json_value``), so the OpenAPI document finds those
definitions by identity, never by name; every public function strips it (``unmarked``), and the
generator reads it through the ``*_marked`` builders.

``vocabulary.json`` is not a schema: it is the form renderer's partition of the 2020-12 keywords
(``schema.vocabulary``, D422), written beside them.
"""

import json
import math
import sys
from collections import defaultdict
from collections.abc import Callable, Collection, Iterator
from copy import deepcopy
from functools import partial
from pathlib import Path
from typing import NewType, Self, cast

from pydantic import BaseModel, JsonValue, TypeAdapter
from pydantic.json_schema import GenerateJsonSchema, JsonSchemaValue
from pydantic_core import core_schema

from aibi.core.schema import vocabulary
from aibi.core.schema.catalog import TOOL_MODELS
from aibi.core.schema.cohorts import DOCUMENT_MARK
from aibi.core.schema.curation import ChangeRequest, CurationQueue, ProposalInput
from aibi.core.schema.descriptors import DESCRIPTOR_JSON_MARK, DescModel, Descriptor
from aibi.core.schema.document import DOCUMENT_JSON_MARK, Document
from aibi.core.schema.ids import MAX_SAFE_INTEGER, NAME
from aibi.core.schema.output import COMPUTED_MARK, OUTPUT_JSON_MARK
from aibi.core.schema.pack_api import PackManifest
from aibi.core.schema.refusals import Refusal
from aibi.core.schema.results import CohortCount, ResultEnvelope

SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"
PARAMETER_REFERENCE: dict[str, JsonValue] = {
    "type": "string",
    "pattern": f"^\\$(?:{NAME}|\\$[\\s\\S]*)$",
    "description": 'A parameter reference, "$name", or a literal string written "$$…"',
}

JsonObject = dict[str, JsonValue]
MarkedSchema = NewType("MarkedSchema", JsonObject)
"""A builder's schema whose JSON-value definitions still carry their marker (``unmarked``)."""

RESERVED = frozenset(
    {
        "DescriptorJson",
        "DocumentJson",
        "OutputJson",
        "ParameterReference",
        "ParameterValue",
        "RequestJson",
    }
)
"""Every name a builder defines itself (``_define_reserved``): a class Pydantic names so is
refused (``Strict``), and the OpenAPI document refuses a route model so named."""
_JSON_NULL: dict[str, bool] = {
    "DescriptorJson": True,
    "DocumentJson": False,
    "OutputJson": True,
    "ParameterValue": False,
    "RequestJson": True,
}
"""The reserved names that are any JSON value, each with whether it takes ``null``."""
MARKER_KEY = "\u0000made"
"""The key ``canonical`` writes a marker as: a dict holding it is refused (it would be equal)."""
JSON_VALUE_MARK = "x-aibi-json-made"
"""The marker's key, at the top level of a definition ``_json_value`` made; never public."""
_NULLABLE = "x-aibi-nullable"
"""``_Nulls``' mark of a descriptor model's nullable members, which ``_declared_nulls`` takes."""


class SchemaNameError(ValueError):
    """Two bodies for one name, a reserved name taken, or a marker this module did not give."""


class _Made:
    """The marker's value: one object for JSON values with ``null`` and one without, compared
    with ``is``; a copy is the object itself, and it is no JSON (``json.dumps`` fails on it)."""

    __slots__ = ("null",)

    def __init__(self, null: bool) -> None:
        self.null = null

    def __repr__(self) -> str:
        return "<a JSON value's marker>"

    def __copy__(self) -> Self:
        return self

    def __deepcopy__(self, memo: dict[int, object]) -> Self:
        return self


_MADE = (_Made(False), _Made(True))


def made(value: object) -> bool:
    """Whether the JSON value whose marker is ``value`` takes ``null``; a value that is not one of
    this module's markers (a model's own ``json_schema_extra``, say) is refused."""
    for found in _MADE:
        if value is found:
            return found.null
    raise SchemaNameError(f"{JSON_VALUE_MARK} holds what export._json_value did not give")


def _plain(value: object) -> JsonValue:
    """``json.dumps``' ``default``: a marker as text no other marker has; anything else no JSON."""
    for found in _MADE:
        if value is found:
            return {MARKER_KEY: found.null}
    raise TypeError(f"{type(value).__name__} is no JSON")


class UncomparableError(SchemaNameError):
    """A value ``canonical`` refuses to compare as text, for what it holds (no value, never)."""


def _where(path: tuple[object, ...]) -> str:
    return "".join(f"[{part!r}]" for part in path) or "the value"


def _vet(value: object, path: tuple[object, ...], open_: set[int]) -> None:
    """Refuse what ``json.dumps`` would write like something else (a key that is not a string,
    a tuple, a subclass of ``str``, ``int`` or ``float``, a dict that forges a marker), a circular
    structure and a number that is not finite, naming where and never the value; an object that
    is no JSON at all is left for ``_plain`` (``TypeError``)."""
    kind = type(value)
    if kind is float and not math.isfinite(cast(float, value)):
        raise UncomparableError(f"a number that is not finite at {_where(path)}")
    if value is None or kind in (str, int, bool, float) or kind is _Made:
        return
    if kind is dict or kind is list:
        if id(value) in open_:
            raise UncomparableError(f"a circular structure at {_where(path)}")
        open_.add(id(value))
        members: list[tuple[object, object]] = (
            list(cast(dict[object, object], value).items())
            if kind is dict
            else list(enumerate(cast(list[object], value)))
        )
        for key, member in members:
            if kind is dict and type(key) is not str:
                raise UncomparableError(f"a key that is not a string at {_where(path)}")
            if key == MARKER_KEY:
                raise UncomparableError(f"a marker's own key at {_where(path)}")
            _vet(member, (*path, key), open_)
        open_.discard(id(value))
    elif isinstance(value, (Collection, int, float)) and not isinstance(value, type):
        raise UncomparableError(f"a {kind.__name__}, which is no JSON, at {_where(path)}")


def canonical(value: object) -> str:
    """``value`` as type-preserving text: keys sorted, ``true``, ``1`` and ``1.0`` told apart, a
    marker by which it is. What ``json`` would write like something else is refused, naming where
    (``UncomparableError``, ``_vet``), and an object that is no JSON at all is a ``TypeError``."""
    _vet(value, (), set())
    return json.dumps(value, sort_keys=True, allow_nan=False, default=_plain)


def same(first: object, second: object) -> bool:
    """Whether two schemas are one, as canonical text (never ``==``: ``True == 1 == 1.0``)."""
    return canonical(first) == canonical(second)


def _equal(first: object, second: object) -> bool:
    try:
        return same(first, second)
    except TypeError:
        return first is second


def put[K, V](mapping: dict[K, V], key: K, value: V, refusal: str) -> None:
    """Write ``value`` at ``key``; a key that holds another value is refused (``refusal``, its
    ``{key}`` filled), never overwritten. Values that are JSON compare as canonical text; one that
    ``canonical`` refuses (``UncomparableError``) is refused too, naming the key and where in the
    value, never what it holds; a value that is no JSON (a route, a partial, a class) compares by
    identity. The one primitive every name-keyed map of the builders and the OpenAPI document is
    written through (a test lists every other write)."""
    if key in mapping:
        try:
            equal = _equal(mapping[key], value)
        except UncomparableError as error:
            raise SchemaNameError(f"{refusal.format(key=key)}: holds {error}") from None
        if not equal:
            raise SchemaNameError(refusal.format(key=key))
    mapping[key] = value


def _define(defs: JsonObject, name: str, body: JsonValue) -> None:
    put(defs, name, body, "{key} is defined twice, differently")


def json_value(name: str) -> JsonObject:
    """The definition of any JSON value named ``name`` (one of ``RESERVED``), ``null`` only if the
    name takes it, its numbers bounded as the models bound them (SPEC §7.1), without its marker."""
    if name not in RESERVED or name not in _JSON_NULL:
        raise SchemaNameError(f"{name} is not a reserved JSON value")
    null = _JSON_NULL[name]
    return {
        "description": "Any JSON value" if null else "Any JSON value but null",
        "anyOf": [
            {"type": ["string", "boolean", "null"] if null else ["string", "boolean"]},
            {"type": "number", "minimum": -MAX_SAFE_INTEGER, "maximum": MAX_SAFE_INTEGER},
            {"type": "array", "items": {"$ref": f"#/$defs/{name}"}},
            {"type": "object", "additionalProperties": {"$ref": f"#/$defs/{name}"}},
        ],
    }


def _json_value(name: str) -> JsonObject:
    """``json_value(name)`` with its marker: the one place a marker enters a schema (an object
    typed as JSON once, here)."""
    body = json_value(name)
    return {**body, JSON_VALUE_MARK: cast(JsonValue, _MADE[_JSON_NULL[name]])}


def _define_reserved(defs: JsonObject, name: str) -> None:
    """Define one of the builders' own names in ``defs``, its body taken from the name alone."""
    body = PARAMETER_REFERENCE if name == "ParameterReference" else _json_value(name)
    _define(defs, name, body)


def _nodes(node: JsonValue) -> Iterator[JsonObject]:
    if isinstance(node, list):
        for item in node:
            yield from _nodes(item)
    elif isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _nodes(value)


def _refers(schema: JsonValue, name: str) -> bool:
    """Whether a ``$ref`` of ``schema`` points at its definition ``name``."""
    return any(node.get("$ref") == f"#/$defs/{name}" for node in _nodes(schema))


def _holds(schema: JsonValue, mark: str) -> bool:
    """Whether a node of ``schema`` carries ``mark``."""
    return any(node.get(mark) is True for node in _nodes(schema))


def _holds_marker(node: JsonValue) -> bool:
    """Whether a marker is anywhere in ``node``, as a member's value or a list's item."""
    if isinstance(node, _Made):
        return True
    if isinstance(node, list):
        return any(_holds_marker(item) for item in node)
    return isinstance(node, dict) and any(_holds_marker(value) for value in node.values())


def unmarked(schema: MarkedSchema) -> JsonObject:
    """``schema`` without the markers at its root's and its definitions' top level (refusing one
    this module did not give), as every public builder returns it; a marker anywhere else is a
    builder's error, refused here rather than left for ``json.dumps``."""

    def plain(node: JsonValue) -> JsonValue:
        if isinstance(node, dict) and JSON_VALUE_MARK in node:
            made(node[JSON_VALUE_MARK])
            return {key: value for key, value in node.items() if key != JSON_VALUE_MARK}
        return node

    found = cast(JsonObject, plain(schema))
    defs = found.get("$defs")
    if isinstance(defs, dict):
        found: JsonObject = {
            key: {name: plain(body) for name, body in defs.items()} if key == "$defs" else value
            for key, value in found.items()
        }
    if _holds_marker(found):
        raise SchemaNameError(f"a {JSON_VALUE_MARK} below a definition's top level")
    return found


class Strict(GenerateJsonSchema):
    """Every JSON Schema this module generates. Pydantic merges two definitions that share their
    final name when they compare equal with ``==`` (where ``true == 1 == 1.0``): a field would be
    described by another class's default. This refuses two that differ as canonical text, each as
    Pydantic writes it after its own renaming (``remap_json_schema``, on a copy: it rewrites in
    place; two classes whose bodies differ only by the names of nested twins Pydantic merges are
    one), and a final name in
    ``RESERVED`` (``_refuse_reserved``). ``_build_definitions_remapping`` is Pydantic's private
    hook: a test pins it, and fails on an upgrade that changes it."""

    def _build_definitions_remapping(self):  # Pydantic's private return type
        remapping = super()._build_definitions_remapping()
        finals: dict[str, set[str]] = defaultdict(set)
        originals: dict[str, str] = {}
        for original, body in self.definitions.items():
            final = remapping.defs_remapping.get(original, original)
            try:
                finals[final].add(canonical(remapping.remap_json_schema(deepcopy(body))))
            except UncomparableError as error:
                raise SchemaNameError(f"{final} holds {error}") from None
            originals.setdefault(final, original)
        if clashes := sorted(name for name, bodies in finals.items() if len(bodies) > 1):
            raise SchemaNameError(f"two classes Pydantic names {clashes[0]} have different schemas")
        _refuse_reserved(originals)
        return remapping


def _refuse_reserved(originals: dict[str, str]) -> None:
    """Refuse a class whose definition Pydantic names like one the builders define themselves,
    naming the class by Pydantic's own reference to it (its module and qualified name)."""
    if taken := sorted(name for name in originals if name in RESERVED):
        name = taken[0]
        raise SchemaNameError(
            f"a class Pydantic names {name}, a name the builders reserve: {originals[name]}"
        )


class _Nulls(Strict):
    """The descriptor schema's generation: a descriptor model's definition carries the members it
    declares nullable, read from the class itself (``schema['cls']``), never from its name."""

    def model_schema(self, schema: core_schema.ModelSchema) -> JsonSchemaValue:
        found = super().model_schema(schema)
        model = schema["cls"]
        if issubclass(model, DescModel):
            return {**found, _NULLABLE: sorted(model.nullable())}
        return found


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
                result = _merged(kept[0], result)
            else:
                result["anyOf"] = kept
    if result.get("default", 0) is None:
        del result["default"]
    return result


ANNOTATIONS = frozenset(
    {
        "$comment",
        "default",
        "deprecated",
        "description",
        "examples",
        "readOnly",
        "title",
        "writeOnly",
    }
)
"""The keywords that say nothing of what a schema accepts (``_merged``)."""


def _merged(member: JsonObject, parent: JsonObject) -> JsonObject:
    """A union's one member left in its place: their keywords together, the parent's annotation
    where both have one (its ``title`` and ``description`` are the member's own); any other
    keyword both have differently is refused, never overwritten."""
    if clash := sorted(
        key
        for key in member.keys() & parent.keys()
        if key not in ANNOTATIONS and not same(member[key], parent[key])
    ):
        raise SchemaNameError(f"a union's member and its parent both give {clash}")
    return {**member, **parent}


def without_null(node: JsonValue) -> JsonValue:
    """``node`` without the ``null`` alternatives and ``null`` defaults Pydantic adds for optional
    members (the OpenAPI document's parameters, which are never ``null``, ``api.openapi``)."""
    return _without_null(node)


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
        if "$ref" in rest:
            raise SchemaNameError("a descriptor's JSON value has a $ref of its own")
        return {"$ref": "#/$defs/DescriptorJson", **rest}
    result: JsonObject = {key: _closed(value) for key, value in node.items()}
    if "patternProperties" in result and "additionalProperties" not in result:
        result["additionalProperties"] = False
    return result


def _document_schema() -> MarkedSchema:
    generated = Document.model_json_schema(schema_generator=Strict)
    schema = cast(JsonObject, _without_null(cast(JsonValue, generated)))
    schema = cast(JsonObject, _closed(schema))
    _define_reserved(cast(JsonObject, schema.setdefault("$defs", {})), "DocumentJson")
    return MarkedSchema({"$schema": SCHEMA_DIALECT, "$id": "document.schema.json", **schema})


def document_schema_marked() -> MarkedSchema:
    """``document_schema`` with its markers, for the OpenAPI document's ``Substituted*``."""
    return _document_schema()


def document_schema() -> JsonObject:
    return unmarked(_document_schema())


def params_schema(model: type[BaseModel]) -> JsonObject:
    """The JSON Schema of an analysis's parameters, as its registry entry carries it (§9.1,
    D316): a view's ``params`` after substitution, whose clauses are the document's."""
    generated = model.model_json_schema(schema_generator=Strict)
    schema = cast(JsonObject, _without_null(cast(JsonValue, generated)))
    schema = cast(JsonObject, _closed(schema))
    if _refers(schema, "DocumentJson"):
        _define_reserved(cast(JsonObject, schema.setdefault("$defs", {})), "DocumentJson")
    return unmarked(MarkedSchema({"$schema": SCHEMA_DIALECT, **schema}))


def values_schema(model: type[BaseModel]) -> JsonObject:
    """The JSON Schema of an analysis's ``values``, as its registry entry carries it (§9.1,
    D316)."""
    schema = cast(JsonValue, TypeAdapter(model).json_schema(schema_generator=Strict))
    closed = cast(JsonObject, _output_json(_closed(_optional_without_null(schema))))
    if _refers(closed, "OutputJson"):
        _define_reserved(cast(JsonObject, closed.setdefault("$defs", {})), "OutputJson")
    return unmarked(MarkedSchema({"$schema": SCHEMA_DIALECT, **closed}))


def _allow_references(node: JsonValue, *, skip: bool = False) -> JsonValue:
    """Let every value position accept a parameter reference, except inside ``params``. A union
    (``oneOf``) beside an ``anyOf`` becomes a conjunct after the node's own ``allOf`` members."""
    if isinstance(node, list):
        return [_allow_references(item) for item in node]
    if not isinstance(node, dict):
        return node
    beside = "oneOf" in node and "anyOf" in node
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
            if beside:
                conjuncts = _allow_references(node.get("allOf", []))
                result["allOf"] = [*cast(list[JsonValue], conjuncts), {"anyOf": converted}]
            else:
                result["anyOf"] = converted
        elif key == "allOf" and beside:
            continue  # the union's conjunct joins them at "oneOf"
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


TEXT_MEMBERS = frozenset({"notes", "note", "drafted_by"})
"""Plain-text members, never substituted (SPEC §7.1)."""


def _document_as_written_schema() -> MarkedSchema:
    transformed = cast(JsonObject, _allow_references(_document_schema(), skip=True))
    defs = cast(JsonObject, transformed.setdefault("$defs", {}))
    _define_reserved(defs, "ParameterReference")
    # Parameter values are taken verbatim: nothing in them is a reference (SPEC §7.1).
    _define_reserved(defs, "ParameterValue")
    properties = cast(JsonObject, transformed["properties"])
    properties["params"] = _renamed(
        properties["params"], "#/$defs/DocumentJson", "#/$defs/ParameterValue"
    )
    transformed["$id"] = "document.as-written.schema.json"
    return MarkedSchema(transformed)


def document_as_written_schema() -> JsonObject:
    return unmarked(_document_as_written_schema())


def _declared_nulls(schema: JsonObject) -> JsonObject:
    """Keep ``null`` only for the members a descriptor model declares nullable (SPEC §5.1, §7.1),
    by ``_Nulls``' mark, which goes.

    Absent and ``null`` differ for those members, so none of them has a default. Other models are
    those of documents (a coverage's parent_scope), which never hold null.
    """
    for definition in (schema, *cast(JsonObject, schema.get("$defs", {})).values()):
        if not isinstance(definition, dict):
            continue
        nullable = frozenset(cast(list[str], definition.pop(_NULLABLE, [])))
        properties = definition.get("properties")
        if not isinstance(properties, dict):
            continue
        for member, value in list(properties.items()):
            if member not in nullable:
                properties[member] = _without_null(value)
            elif isinstance(value, dict) and value.get("default", 0) is None:
                del value["default"]
    return schema


def descriptor_schema() -> JsonObject:
    """Any descriptor, chosen by ``kind`` (SPEC §5). ``null`` appears only where it is declared."""
    generated = cast(JsonObject, TypeAdapter(Descriptor).json_schema(schema_generator=_Nulls))
    schema = cast(JsonObject, _closed(cast(JsonValue, _declared_nulls(generated))))
    defs = cast(JsonObject, schema.setdefault("$defs", {}))
    _define_reserved(defs, "DescriptorJson")
    if _refers(schema, "DocumentJson"):
        _define_reserved(defs, "DocumentJson")
    found = {"$schema": SCHEMA_DIALECT, "$id": "descriptor.schema.json", **schema}
    return unmarked(MarkedSchema(found))


def _no_default(member: JsonValue) -> JsonValue:
    """A computed member's ``null`` means not estimable, so it is never its default: an absent
    optional member is omitted (§8.1)."""
    if isinstance(member, dict) and member.get("default", 0) is None:
        return {key: value for key, value in member.items() if key != "default"}
    return member


def _optional_without_null(node: JsonValue) -> JsonValue:
    """Outputs omit an absent optional member rather than writing ``null`` (SPEC §8.1), so the
    ``null`` Pydantic allows for optional members goes; required members, and computed ones
    (whose ``null`` means not estimable), keep theirs."""
    if isinstance(node, list):
        return [_optional_without_null(item) for item in node]
    if not isinstance(node, dict):
        return node
    result: JsonObject = {key: _optional_without_null(value) for key, value in node.items()}
    properties = result.get("properties")
    required = result.get("required", [])
    if isinstance(properties, dict) and isinstance(required, list):
        result["properties"] = {
            name: _no_default(member)
            if name in required or (isinstance(member, dict) and member.get(COMPUTED_MARK))
            else _without_null(member)
            for name, member in properties.items()
        }
    return result


def _output_json(node: JsonValue, definition: str = "OutputJson") -> JsonValue:
    """Replace the output JSON mark with a reference to its bounded definition."""
    if isinstance(node, list):
        return [_output_json(item, definition) for item in node]
    if not isinstance(node, dict):
        return node
    if node.get(OUTPUT_JSON_MARK) is True:
        rest = {
            key: _output_json(value, definition)
            for key, value in node.items()
            if key != OUTPUT_JSON_MARK
        }
        if "$ref" in rest:
            raise SchemaNameError("an output's JSON value has a $ref of its own")
        return {"$ref": f"#/$defs/{definition}", **rest}
    return {key: _output_json(value, definition) for key, value in node.items()}


def _output_schema(output: object, schema_id: str) -> MarkedSchema:
    """The schema of an output. Strings from data or documents are marked ``x-aibi-data``."""
    schema = cast(JsonValue, TypeAdapter(output).json_schema(schema_generator=Strict))
    closed = cast(JsonObject, _output_json(_closed(_optional_without_null(schema))))
    if _refers(closed, "OutputJson"):
        _define_reserved(cast(JsonObject, closed.setdefault("$defs", {})), "OutputJson")
    return MarkedSchema({"$schema": SCHEMA_DIALECT, "$id": schema_id, **closed})


def result_schema() -> JsonObject:
    return output_schema(ResultEnvelope, "result.schema.json")


def cohort_count_schema() -> JsonObject:
    return output_schema(CohortCount, "cohort-count.schema.json")


def refusal_schema() -> JsonObject:
    return output_schema(Refusal, "refusal.schema.json")


def pack_manifest_schema() -> JsonObject:
    return output_schema(PackManifest, "pack-manifest.schema.json")


def _request_schema(request: object, schema_id: str) -> MarkedSchema:
    """The schema of an operator or tool request (§11.1, §11.2): an optional member is omitted
    rather than ``null``, and JSON values are descriptors' (``null`` allowed)."""
    schema = cast(JsonValue, TypeAdapter(request).json_schema(schema_generator=Strict))
    closed = cast(JsonObject, _closed(_optional_without_null(schema)))
    if _refers(closed, "DescriptorJson"):
        _define_reserved(cast(JsonObject, closed.setdefault("$defs", {})), "DescriptorJson")
    return MarkedSchema({"$schema": SCHEMA_DIALECT, "$id": schema_id, **closed})


def change_request_schema() -> JsonObject:
    return unmarked(_request_schema(ChangeRequest, "change-request.schema.json"))


def proposal_schema() -> JsonObject:
    return unmarked(_request_schema(ProposalInput, "proposal.schema.json"))


def curation_queue_schema() -> JsonObject:
    return output_schema(CurationQueue, "curation-queue.schema.json")


WRITTEN_TEXT = 'An analysis document as written, "$name" parameter references allowed (SPEC §7.1)'


def _documents(schema: MarkedSchema) -> MarkedSchema:
    """A request's ``document`` members as the schema of a document as written, whose
    definitions join the request's (§11.1, D299)."""
    if not _holds(schema, DOCUMENT_MARK):
        return schema
    written = _document_as_written_schema()
    definitions = cast(JsonObject, written["$defs"])
    root = {key: value for key, value in written.items() if key not in ("$schema", "$id", "$defs")}

    def replaced(node: JsonValue) -> JsonValue:
        if isinstance(node, list):
            return [replaced(item) for item in node]
        if not isinstance(node, dict):
            return node
        if node.get(DOCUMENT_MARK) is True:
            return {**root, "description": WRITTEN_TEXT}
        return {key: replaced(value) for key, value in node.items()}

    found = cast(JsonObject, replaced(schema))
    own = cast(JsonObject, found.setdefault("$defs", {}))
    for name, definition in definitions.items():
        _define(own, name, definition)
    return MarkedSchema(found)


def _request_json(schema: MarkedSchema) -> MarkedSchema:
    """A request's bounded JSON values (a model's output JSON mark, which a request may share
    with outputs) as ``RequestJson``: any JSON value, ``null`` included, its numbers bounded."""
    found = cast(JsonObject, _output_json(schema, "RequestJson"))
    if _refers(found, "RequestJson"):
        _define_reserved(cast(JsonObject, found.setdefault("$defs", {})), "RequestJson")
    return MarkedSchema(found)


def request_schema_marked(request: object, schema_id: str) -> MarkedSchema:
    """``request_schema`` with its markers, for the OpenAPI document (``api.openapi``)."""
    return _request_json(_documents(_request_schema(request, schema_id)))


def request_schema(request: object, schema_id: str) -> JsonObject:
    """A tool's or an operator route's request schema, as the MCP server and the HTTP API give
    it (§11.1): a document it takes is described as a document as written, and a JSON value it
    takes as ``RequestJson``. The loader stays the judge of how deep a value nests."""
    return unmarked(request_schema_marked(request, schema_id))


def output_schema_marked(output: object, schema_id: str) -> MarkedSchema:
    """``output_schema`` with its markers, for the OpenAPI document (``api.openapi``)."""
    return _output_schema(output, schema_id)


def output_schema(output: object, schema_id: str) -> JsonObject:
    """A tool's output schema, as the MCP server and the HTTP API give it (§11.1)."""
    return unmarked(_output_schema(output, schema_id))


def _tool_schemas() -> dict[str, Callable[[], JsonObject]]:
    found: dict[str, Callable[[], JsonObject]] = {}
    for name, (request, output) in TOOL_MODELS.items():
        stem = name.replace("_", "-")
        given = f"tool.{stem}.request.schema.json"
        put(found, given, partial(request_schema, request, given), "two tools write {key}")
        given = f"tool.{stem}.output.schema.json"
        put(found, given, partial(output_schema, output, given), "two tools write {key}")
    return found


SCHEMAS = {
    "document.schema.json": document_schema,
    "document.as-written.schema.json": document_as_written_schema,
    "descriptor.schema.json": descriptor_schema,
    "result.schema.json": result_schema,
    "cohort-count.schema.json": cohort_count_schema,
    "refusal.schema.json": refusal_schema,
    "pack-manifest.schema.json": pack_manifest_schema,
    "change-request.schema.json": change_request_schema,
    "proposal.schema.json": proposal_schema,
    "curation-queue.schema.json": curation_queue_schema,
    **_tool_schemas(),
}
OUTPUT_SCHEMAS = (
    "result.schema.json",
    "cohort-count.schema.json",
    "refusal.schema.json",
    "pack-manifest.schema.json",
    "curation-queue.schema.json",
    *(name for name in SCHEMAS if name.startswith("tool.") and ".output." in name),
)


def render(schema: JsonObject) -> str:
    return json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def write(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for name, build in SCHEMAS.items():
        (directory / name).write_text(render(build()), encoding="utf-8")
    (directory / vocabulary.VOCABULARY_FILE).write_text(
        render(vocabulary.document()), encoding="utf-8"
    )


if __name__ == "__main__":  # pragma: no cover
    write(Path(sys.argv[1]) if len(sys.argv) > 1 else Path("../schemas"))

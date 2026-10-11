"""The OpenAPI document (SPEC §12.4, D416): ``uv run python -m aibi.core.api.openapi <directory>``.

The document is generated and checked in (``schemas/openapi.json``), for the web client's types,
and never served (``api.app``). It describes the JSON API ``create_app`` serves: the tools at
``/api/tools`` and the health check, and the operator router; not the MCP transport, the
catalogue pages or the web bundle's routes (D410, D411: none is in FastAPI's own schema). Its
paths, operations and parameters are FastAPI's, for an application built on stub services (nothing
is called: no store, no configuration, no pack and no DuckDB are read), and its operation ids are
the routes' own (a tool's name, an operator body's ``OperatorBody``, a handler's name). Its
schemas come only from ``schema.export``'s builders, as the tool schemas and MCP do, FastAPI's
own being discarded: each request body through ``export.request_schema``, each
response through ``export.output_schema``, each builder's definitions becoming components (two
builders defining one name differently, two route models of one name, a route model named like a
name the builders reserve (``export.RESERVED``) or ``Substituted*``, a definition named like its
own root or ``Substituted*``, or a name OpenAPI 3.1 gives no component key, refuse the document).
Every name-keyed map is written through ``export.put``, which refuses a key holding another
value; two routes of one method and path, as FastAPI's own inclusions give them (their prefix,
``include_in_schema`` and dependencies), are refused whatever they hold (the server runs the
first), and a route an inclusion leaves out of FastAPI's document is left out of this one. The
components are ``components_of``'s, from the routes' models alone. The rewrites are
``api.openapi_normalise``'s named class of local equivalences.

What the document says beside the builders is computed, never listed:

- every status a refusal is answered with (``errors.refusal_statuses``) is declared on every
  operation, with ``Refusals`` (an over-approximation: which route answers which status would be
  a hand-kept list), and ``default`` covers the rest; the headers the server's own table gives a
  status (``errors.RESPONSE_HEADERS``, which its answers are checked against: ``WWW-Authenticate``
  on 401, ``Allow`` on 405 and ``Retry-After`` on 429 and 503) are declared, each optional (a
  proposal's 429 has none);
- every operator operation takes ``Aibi-Operator`` (D262's pattern, ``auth.ENCODED_NAME``) and,
  but ``GET /operator/csrf`` (``protection.CSRF_PATH``), ``Aibi-CSRF`` (D263), and the curator
  token as a bearer scheme;
- a component a response reaches is a response's, any other a request's, and none is both;
  components nothing reaches are dropped but the document after substitution (``Substituted*``:
  ``export.document_schema``'s definitions and root, renamed so, which no route takes);
- ``x-aibi-json`` marks the components that are any JSON value, those ``export._json_value``
  made (whatever ``_allow_references`` did to them after), found by the marker it gives them (an
  object of ``export``'s, by identity, never by name), with their ``direction`` (whether a
  response reaches them) and whether they take ``null``;
- ``x-aibi-server-number`` marks every ``number`` or ``integer`` of a response's JSON body, but
  in a condition (``if``, ``then``, ``else``, ``not``) and in an ``x-aibi-json`` component, which
  is its own typed value: numbers the server computed, which a client may not compute with;
- ``info.version`` is ``aibi-http``, so that the document changes with the API, not the package.

It refuses a document a check fails: a typeless union member R2 could not type, a numeric
``const`` or ``enum`` of a response that is neither beside a numeric ``type`` nor in a constraint
(an ``allOf`` conjunct or a condition), a keyword ``api.openapi_vocabulary`` does not list (in the
document or a tool schema; a parameter's ``$ref`` among them: parameters never reference
components), an operation without its own id, two operations with one, or a route whose JSON body
is neither a tool's nor an operator body's.
"""

import re
import sys
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Literal, cast

from fastapi import FastAPI
from fastapi.routing import APIRoute, RouteContext, iter_route_contexts
from pydantic import BaseModel, JsonValue

from aibi.core.api import errors, openapi_vocabulary
from aibi.core.api.app import create_app
from aibi.core.api.openapi_normalise import (
    HEADER_TEXT,
    RETRY_AFTER,
    SCHEMA_LISTS,
    SCHEMA_MAPS,
    SCHEMA_VALUES,
    children,
    constraint_only,
    normalised,
    parameter,
    untyped,
)
from aibi.core.api.protection import CSRF_PATH, Policy
from aibi.core.api.tools import TOOLS_PREFIX
from aibi.core.importers.uploads import UploadArea
from aibi.core.mcp.calls import Calls
from aibi.core.operator.auth import ENCODED_NAME, OPERATOR_PREFIX
from aibi.core.operator.router import OPERATOR_BODIES, UPLOAD_OPERATION, Services
from aibi.core.schema import export
from aibi.core.schema.catalog import TOOL_MODELS
from aibi.core.schema.export import MarkedSchema, SchemaNameError, put
from aibi.core.schema.limits import MAX_BODY_BYTES, ImportLimits
from aibi.core.schema.operator import Refusals
from aibi.core.store.store import Store

JsonObject = dict[str, JsonValue]
Side = Literal["request", "response"]
Models = tuple[tuple[Side, type[BaseModel]], ...]

DOCUMENT = "openapi.json"
VERSION = "aibi-http"
SERVER_NUMBER = "x-aibi-server-number"
JSON_MARK = "x-aibi-json"
SUBSTITUTED = "Substituted"
SCHEME = "curator"
OPERATOR_NAME = "Aibi-Operator"
CSRF_NAME = "Aibi-CSRF"
REFUSALS = "Refusals"
COMPONENTS = "#/components/schemas/"
_DEFINITIONS = "#/$defs/"
_CONDITIONS = frozenset({"if", "then", "else", "not"})
_NUMBERS = frozenset({"number", "integer"})
_COMPONENT_KEY = re.compile(r"[a-zA-Z0-9._-]+")
_OPERATION_ID = re.compile(r"^[a-z](?:[a-z0-9_]*[a-z0-9])?$")
_REFUSED = "Refused (D265): the refusals, the status the first one's"


def _app() -> FastAPI:
    """The application's routes, on stub services that nothing calls."""
    policy = Policy(
        hosts=frozenset({"localhost"}),
        origins=frozenset(),
        cors_origins=frozenset(),
        token_digest=bytes(32),
        csrf_key=bytes(32),
        rates={},
        max_body_bytes=MAX_BODY_BYTES,
        upload_bytes=0,
        tls=False,
    )
    services = Services(
        store=cast(Store, None),
        uploads=cast(UploadArea, None),
        import_directories=(),
        limits=ImportLimits(),
    )
    return create_app(policy, services, tools=cast(Calls, object()))


def _routes(app: FastAPI) -> dict[tuple[str, str], RouteContext]:
    """The routes the JSON API describes, by method and path (those FastAPI's own document
    lists, the catalogue pages, the web bundle's routes and the MCP transport left out), each as
    FastAPI's effective context holds it: the path after an inclusion's prefix, and ``include_in_
    schema``, dependencies and the rest after the inclusion. A second context of one key is
    refused whatever it holds (a router included twice, with other dependencies or not): the
    server runs the first and FastAPI's document describes the last. ``put`` compares the contexts
    by identity, and each is its own."""
    found: dict[tuple[str, str], RouteContext] = {}
    for context in iter_route_contexts(app.routes):
        if isinstance(context.original_route, APIRoute) and context.include_in_schema:
            for method in context.methods or ():
                key = (method.lower(), cast(str, context.path_format))
                put(found, key, context, "two routes serve {key}")
    return found


def _bodies() -> dict[str, type[BaseModel]]:
    """The operator routes' body models, by operation id."""
    found: dict[str, type[BaseModel]] = {}
    for body in OPERATOR_BODIES:
        put(found, body.operation, body.model, "two operator bodies are {key}'s")
    return found


def _request_model(route: RouteContext) -> type[BaseModel] | None:
    """The model of a route's JSON body, if it takes one."""
    path = cast(str, route.path)
    if path.startswith(f"{TOOLS_PREFIX}/"):
        return TOOL_MODELS[path.removeprefix(f"{TOOLS_PREFIX}/")][0]
    return _bodies().get(route.operation_id or "")


def _response_model(route: RouteContext) -> type[BaseModel]:
    model = route.response_model
    if not (isinstance(model, type) and issubclass(model, BaseModel)):
        raise ValueError(f"{route.path} has no response model")
    return model


def _named(classes: dict[str, type[BaseModel]], model: type[BaseModel]) -> str:
    """``model``'s name as its component, recording its class: two classes of one name would
    describe a route by the other's schema, and a name the builders reserve (``export.RESERVED``)
    or ``Substituted*`` would be taken for theirs, so the document refuses them."""
    name = model.__name__
    where = f"{model.__module__}.{model.__qualname__}"
    if name in export.RESERVED or name.startswith(SUBSTITUTED):
        raise SchemaNameError(
            f"a route model is named {name}, a name the builders reserve: {where}"
        )
    if (known := classes.setdefault(name, model)) is not model:
        first = f"{known.__module__}.{known.__qualname__}"
        raise SchemaNameError(f"two models are named {name}: {first} and {where}")
    return name


def route_models(app: FastAPI) -> Models:
    """The models of the routes the document describes, each with its side, in the routes'
    order: ``components_of``'s input."""
    found: list[tuple[Side, type[BaseModel]]] = []
    for route in _routes(app).values():
        request = _request_model(route)
        if request is not None:
            found.append(("request", request))
        found.append(("response", _response_model(route)))
    return tuple(found)


def _builders(models: Models) -> dict[str, MarkedSchema]:
    """The builders' schemas, markers kept, by the name of the component each root becomes;
    ``Refusals`` first. A name is one class's (``_named``)."""
    classes: dict[str, type[BaseModel]] = {}
    found: dict[str, MarkedSchema] = {}
    refusals = export.output_schema_marked(Refusals, REFUSALS)
    put(found, _named(classes, Refusals), refusals, "two builders define {key} differently")
    for side, model in models:
        name = _named(classes, model)
        if side == "request":
            built = export.request_schema_marked(model, name)
        else:
            built = export.output_schema_marked(model, name)
        put(found, name, built, "two builders define {key} differently")
    return found


def builder_schemas(app: FastAPI | None = None) -> dict[str, JsonObject]:
    """The builders' schemas the document's components come from, as the builders give them (no
    marker), by the name of the component each root becomes; ``Substituted*`` aside."""
    built = _builders(route_models(app or _app()))
    return {name: export.unmarked(schema) for name, schema in built.items()}


def _referenced(node: JsonValue, names: dict[str, str]) -> JsonValue:
    if isinstance(node, list):
        return [_referenced(item, names) for item in node]
    if not isinstance(node, dict):
        return node
    result: JsonObject = {}
    for key, value in node.items():
        if key == "$ref" and isinstance(value, str) and value.startswith(_DEFINITIONS):
            target = value.removeprefix(_DEFINITIONS)
            if target not in names:
                raise SchemaNameError(f"{value} refers to no definition of its schema")
            result[key] = COMPONENTS + names[target]
        else:
            result[key] = _referenced(value, names)
    return result


def hoisted(name: str, schema: JsonObject, prefix: str = "") -> dict[str, JsonObject]:
    """A builder's schema as components: its root as ``name`` and each definition by its own
    name, every name after ``prefix``, and its references to them pointing there. A definition
    named like the root (another class Pydantic names so: a model, an enum, a dataclass) is
    refused, for the root would replace it and every reference to it would point at the root; so
    is a definition named ``Substituted*`` but ``Substituted*``'s own (``prefix``)."""
    definitions = cast(JsonObject, schema.get("$defs", {}))
    if name in definitions:
        raise SchemaNameError(f"{name} is both a root and a definition of its own schema")
    if not prefix and (taken := sorted(n for n in definitions if n.startswith(SUBSTITUTED))):
        raise SchemaNameError(f"a definition is named {taken[0]}, a name the document reserves")
    root = {key: value for key, value in schema.items() if key not in ("$schema", "$id", "$defs")}
    names = {definition: prefix + definition for definition, _ in definitions.items()}
    found: dict[str, JsonObject] = {}
    for definition, body in definitions.items():
        hoist = cast(JsonObject, _referenced(body, names))
        put(found, prefix + definition, hoist, "a schema defines {key} twice")
    put(found, prefix + name, cast(JsonObject, _referenced(root, names)), "{key} twice")
    return found


def _references(node: JsonValue) -> Iterator[str]:
    if isinstance(node, list):
        for item in node:
            yield from _references(item)
    elif isinstance(node, dict):
        reference = node.get("$ref")
        if isinstance(reference, str) and reference.startswith(COMPONENTS):
            yield reference.removeprefix(COMPONENTS)
        for value in node.values():
            yield from _references(value)


def reached(components: dict[str, JsonObject], roots: Iterable[JsonValue]) -> frozenset[str]:
    """The components ``roots`` refer to, directly or through other components."""
    found: set[str] = set()
    pending = [name for root in roots for name in _references(root)]
    while pending:
        name = pending.pop()
        if name not in found:
            if name not in components:
                raise SchemaNameError(f"a reference to {name}, which no component is")
            found.add(name)
            pending.extend(_references(components[name]))
    return frozenset(found)


def directions(generated: JsonObject) -> tuple[frozenset[str], frozenset[str]]:
    """The components requests reach (bodies and parameters) and those responses reach."""
    components = cast(dict[str, JsonObject], cast(JsonObject, generated["components"])["schemas"])
    operations = [
        cast(JsonObject, operation)
        for methods in cast(dict[str, JsonObject], generated["paths"]).values()
        for operation in methods.values()
    ]
    requests = reached(
        components,
        [
            part
            for operation in operations
            for part in (operation.get("requestBody", {}), operation.get("parameters", []))
        ],
    )
    responses = reached(components, [operation["responses"] for operation in operations])
    return requests, responses


def _numeric(node: JsonObject) -> bool:
    kind = node.get("type")
    kinds = kind if isinstance(kind, list) else [kind]
    return any(each in _NUMBERS for each in kinds)


def _marked(node: JsonValue, condition: bool = False) -> JsonValue:
    """``node`` with ``x-aibi-server-number`` on its numbers outside conditions."""
    if not isinstance(node, dict):
        return node
    result: JsonObject = dict(node)
    if not condition and _numeric(node):
        result[SERVER_NUMBER] = True
    for key, value in node.items():
        below = condition or key in _CONDITIONS
        if key in SCHEMA_MAPS and isinstance(value, dict):
            result[key] = {name: _marked(member, below) for name, member in value.items()}
        elif key in SCHEMA_LISTS and isinstance(value, list):
            result[key] = [_marked(member, below) for member in value]
        elif key in SCHEMA_VALUES:
            result[key] = _marked(value, below)
    return result


def unconstrained_numbers(node: JsonValue, at: str = "#", constraint: bool = False) -> list[str]:
    """The numeric ``const`` or ``enum`` values of a schema that sit neither beside a numeric
    ``type`` nor in a constraint (an ``allOf`` conjunct that says nothing of a type, or a
    condition)."""
    found: list[str] = []
    if not isinstance(node, dict):
        return found
    values = [node["const"]] if "const" in node else []
    listed = node.get("enum")
    values += listed if isinstance(listed, list) else []
    numbers = [v for v in values if isinstance(v, int | float) and not isinstance(v, bool)]
    if numbers and not constraint and not _numeric(node):
        found.append(at)
    for key, name, member in children(node):
        below = constraint or key in _CONDITIONS or (key == "allOf" and constraint_only(member))
        where = f"{at}/{key}/{name}" if name else f"{at}/{key}"
        found.extend(unconstrained_numbers(member, where, below))
    return found


def _json(schema: JsonValue) -> JsonObject:
    return {"application/json": {"schema": schema}}


def _reference(name: str) -> JsonObject:
    return {"$ref": COMPONENTS + name}


def _header_key(name: str) -> str:
    """A header's name as it is compared, in every place the document holds one (a header
    parameter, a response header): without regard to case (HTTP)."""
    return name.lower()


def _header_texts() -> dict[str, str]:
    """What each response header is, by its name as ``_header_key`` compares it."""
    found: dict[str, str] = {}
    for name, text in (
        (errors.WWW_AUTHENTICATE, "The curator token's scheme"),
        (errors.ALLOW, "The methods the path takes"),
        (errors.RETRY_AFTER, "Seconds to wait"),
    ):
        put(found, _header_key(name), text, "the header {key} is described twice")
    return found


_HEADER_TEXT = _header_texts()


def _headers(status: int) -> JsonObject:
    """R5: the response headers a client reads, as strings, each optional: those the server's own
    table gives the status (``errors.RESPONSE_HEADERS``), under the names it gives them; two names
    of one header, whatever the case, are refused."""
    found: JsonObject = {}
    seen: dict[str, str] = {}
    for name in errors.RESPONSE_HEADERS.get(status, ()):
        key = _header_key(name)
        put(seen, key, name, "the header {key} twice, as another name")
        header: JsonObject = {
            "description": _HEADER_TEXT[key],
            "schema": RETRY_AFTER if key == _header_key(errors.RETRY_AFTER) else HEADER_TEXT,
        }
        put(found, name, header, "the header {key} twice")
    return found


def _responses(response: str) -> JsonObject:
    refused: JsonObject = {"description": _REFUSED, "content": _json(_reference(REFUSALS))}
    found: JsonObject = {}
    answer: JsonObject = {"description": "The answer", "content": _json(_reference(response))}
    put(found, "200", answer, "two responses at {key}")
    for status in sorted(errors.refusal_statuses()):
        if status < 400:
            raise SchemaNameError(f"a refusal is answered with {status}, which is no error status")
        headers = _headers(status)
        declared: JsonObject = {**refused, "headers": headers} if headers else dict(refused)
        put(found, str(status), declared, "two responses at {key}")
    put(found, "default", dict(refused), "two responses at {key}")
    return found


def _operator_parameters(method: str, path: str) -> list[JsonObject]:
    found: list[JsonObject] = [
        {
            "name": OPERATOR_NAME,
            "in": "header",
            "required": True,
            "description": "The operator's name, percent-encoded (D262)",
            "schema": {"type": "string", "pattern": ENCODED_NAME.pattern},
        }
    ]
    if not (method == "get" and path == CSRF_PATH):
        found.append(
            {
                "name": CSRF_NAME,
                "in": "header",
                "required": False,
                "description": (
                    f"Required on a browser's request, one with an Origin or a Sec-Fetch-* "
                    f"header: the token GET {CSRF_PATH} returns (D263)"
                ),
                "schema": {"type": "string"},
            }
        )
    return found


def _operation(method: str, path: str, route: RouteContext, generated: JsonObject) -> JsonObject:
    """One operation: FastAPI's, with the builders' bodies and the computed facts."""
    operation_id = route.operation_id
    if operation_id is None or generated.get("operationId") != operation_id:
        raise ValueError(f"{method.upper()} {path} has no operation id of its own")
    request = _request_model(route)
    if request is None and "requestBody" in generated:
        raise ValueError(
            f"{method.upper()} {path} takes a body that is neither a tool's nor an operator "
            f"body's (a route's JSON body is described by a builder or not at all)"
        )
    response = _response_model(route).__name__
    operation: JsonObject = {
        key: value
        for key, value in generated.items()
        if key not in ("parameters", "requestBody", "responses")
    }
    given_parameters = cast(list[JsonObject], generated.get("parameters", []))
    parameters = [{**given, "schema": parameter(given["schema"])} for given in given_parameters]
    if path.startswith(f"{OPERATOR_PREFIX}/"):
        parameters += _operator_parameters(method, path)
        operation["security"] = [{SCHEME: []}]
    # A parameter is its name and its place, never two of one; a header's name is read without
    # regard to case (``_header_key``), the name of any other parameter (a cookie's too) exactly.
    unique: dict[tuple[str, str], JsonValue] = {}
    for given in parameters:
        place = str(given["in"])
        name = str(given["name"])
        put(
            unique,
            (_header_key(name) if place == "header" else name, place),
            given,
            "two parameters are {key}",
        )
    if unique:
        operation["parameters"] = list(unique.values())
    if request is not None:
        operation["requestBody"] = {
            "required": True,
            "content": _json(_reference(request.__name__)),
        }
    elif operation_id == UPLOAD_OPERATION:
        binary: JsonObject = {"type": "string", "format": "binary"}
        operation["requestBody"] = {
            "required": True,
            "content": {"application/octet-stream": {"schema": binary}},
        }
    operation["responses"] = _responses(response)
    return operation


def components_of(models: Models) -> dict[str, JsonObject]:
    """The document's components for ``models`` (``route_models``), the document's single code
    path for them: every builder's components and ``Substituted*``, rewritten; which a request
    and which a response reaches, from the models' sides (``Refusals`` a response's), none both;
    those nothing reaches dropped but ``Substituted*``; the marks; and the checks of a component
    (a typeless union member R2 could not type, a response's numeric ``const`` or ``enum``)."""
    components: dict[str, JsonObject] = {}
    sources = [hoisted(name, schema) for name, schema in _builders(models).items()]
    replaced = hoisted("Document", export.document_schema_marked(), SUBSTITUTED)
    for source in (*sources, replaced):
        for name, body in source.items():
            if not _COMPONENT_KEY.fullmatch(name):
                raise SchemaNameError(
                    f"{name!r} is no component key: {_COMPONENT_KEY.pattern} (OAS 3.1)"
                )
            put(components, name, body, "two builders define {key} differently")
    rewritten = {name: cast(JsonObject, normalised(body)) for name, body in components.items()}
    sides: dict[Side, list[JsonValue]] = {"request": [], "response": [_reference(REFUSALS)]}
    for side, model in models:
        sides[side].append(_reference(model.__name__))
    requests = reached(rewritten, sides["request"])
    responses = reached(rewritten, sides["response"])
    if both := sorted(requests & responses):
        raise SchemaNameError(f"components a request and a response both reach: {both}")
    kept: dict[str, JsonObject] = {}
    for name, body in rewritten.items():
        if name not in requests and name not in responses and name not in replaced:
            continue
        # The marker, read at a component's top level alone, before any check or mark.
        plain = export.unmarked(MarkedSchema(body))
        if export.JSON_VALUE_MARK in body:
            direction = "response" if name in responses else "request"
            null = export.made(body[export.JSON_VALUE_MARK])
            mark: JsonObject = {"direction": direction, "null": null}
            put(kept, name, {**plain, JSON_MARK: mark}, "{key} twice")
        elif name in responses:
            put(kept, name, cast(JsonObject, _marked(plain)), "{key} twice")
        else:
            put(kept, name, plain, "{key} twice")
    problems = [
        f"{name}: a typeless union member at {at}"
        for name, body in kept.items()
        for at in untyped(body)
    ]
    problems += [
        f"{name}: a numeric const or enum outside a numeric type at {at}"
        for name in sorted(responses)
        for at in unconstrained_numbers(kept[name])
    ]
    if problems:
        raise SchemaNameError("the document is refused:\n" + "\n".join(problems))
    return dict(sorted(kept.items()))


def document() -> JsonObject:
    """The OpenAPI document of the JSON API."""
    app = _app()
    generated = app.openapi()
    routes = _routes(app)
    components = components_of(route_models(app))
    paths: dict[str, JsonObject] = {}
    identifiers: dict[str, str] = {}
    for path, methods in cast(dict[str, dict[str, JsonObject]], generated["paths"]).items():
        for method, given in methods.items():
            operation = _operation(method, path, routes[(method, path)], given)
            put(paths.setdefault(path, {}), method, operation, "two operations of a path are {key}")
            given_id = cast(str, operation["operationId"])
            put(identifiers, given_id, f"{method} {path}", "two operations have one id: {key}")
    if bad := [found for found in identifiers if not _OPERATION_ID.fullmatch(found)]:
        raise SchemaNameError(f"operation ids that are no plain names: {bad}")
    found: JsonObject = {
        "openapi": generated["openapi"],
        "info": {"title": generated["info"]["title"], "version": VERSION},
        "paths": cast(JsonValue, paths),
        "components": {
            "schemas": cast(JsonValue, components),
            "securitySchemes": {
                SCHEME: {
                    "type": "http",
                    "scheme": "bearer",
                    "description": "The curator token (D261), on the operator router alone",
                }
            },
        },
    }
    problems: list[str] = []
    for methods in paths.values():
        for operation in cast(dict[str, JsonObject], methods).values():
            for given in cast(list[JsonObject], operation.get("parameters", [])):
                problems += [
                    f"parameter {given['name']}: at {at}" for at in untyped(given["schema"])
                ]
    tools = {name: build() for name, build in export.SCHEMAS.items() if name.startswith("tool.")}
    problems += openapi_vocabulary.unlisted(found, tools)
    if problems:
        raise SchemaNameError("the document is refused:\n" + "\n".join(problems))
    return found


def write(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / DOCUMENT).write_text(export.render(document()), encoding="utf-8")


if __name__ == "__main__":  # pragma: no cover
    write(Path(sys.argv[1]) if len(sys.argv) > 1 else Path("../schemas"))


__all__ = [
    "COMPONENTS",
    "CSRF_NAME",
    "DOCUMENT",
    "JSON_MARK",
    "OPERATOR_NAME",
    "REFUSALS",
    "SCHEME",
    "SERVER_NUMBER",
    "SUBSTITUTED",
    "VERSION",
    "Models",
    "builder_schemas",
    "components_of",
    "directions",
    "document",
    "hoisted",
    "reached",
    "route_models",
    "unconstrained_numbers",
    "write",
]

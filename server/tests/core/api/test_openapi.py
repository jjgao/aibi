"""The OpenAPI document (SPEC §12.4, D416): current, made only of the builders' schemas, rewritten
only by local equivalences, its facts computed from the server, and nothing secret in it.

Each class of test is tied to what must not happen: the document saying less than the wire (a
``null`` dropped, a rewrite that changes what a schema accepts, a status or header left out), a
response number left unmarked or a request number marked, a secret in it, and a route, body,
header or status the server has that it lacks. The tests over response instances are beside their
fixtures (``analyses/test_openapi_results.py``, ``catalog/test_openapi_counts.py``), and the
request corpus's in ``schema/test_export.py``. Whether a code generator renders the document
faithfully is the web client's build's to check (M5.1c-1).
"""

import ast
import asyncio
import enum
import json
import re
import subprocess
import sys
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Generic, TypeVar, cast

import jsonschema
import pytest
from fastapi.routing import APIRoute, RouteContext, iter_route_contexts
from fastapi.testclient import TestClient
from pydantic import BaseModel, create_model
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import Request

import aibi
from aibi.core.api import errors, openapi, openapi_normalise, openapi_vocabulary
from aibi.core.api.app import create_app
from aibi.core.api.chrome import PAGE_HEADERS
from aibi.core.api.config import BASE, ServerConfig
from aibi.core.api.protection import CSRF_PATH, SECURITY_HEADERS, Policy
from aibi.core.api.serve import services_of, tools_of
from aibi.core.api.tools import TOOLS_PREFIX
from aibi.core.operator import auth, router
from aibi.core.operator.auth import encode_operator, hash_token, new_token
from aibi.core.operator.router import OPERATOR_BODIES, UPLOAD_OPERATION, OperatorBody
from aibi.core.schema import export
from aibi.core.schema.catalog import TOOL_MODELS
from aibi.core.schema.limits import MAX_BODY_BYTES
from aibi.core.schema.operator import Refusals
from aibi.core.schema.pack_api import PackRegistry
from aibi.core.schema.refusals import SECRET_ALONE_RE, SECRET_RE, Limit, Refusal, RefusalCode
from aibi.core.store.store import Store

SCHEMA_DIR = Path(__file__).resolve().parents[4] / "schemas"
DOCUMENT = SCHEMA_DIR / "openapi.json"
OWN = "http://127.0.0.1:8000"
GENEROUS = {"per_minute": 1_000_000, "burst": 1_000_000}
MARK = "x-aibi-server-number"
JSON_MARK = "x-aibi-json"
COMPONENTS = "#/components/schemas/"
MAPS = ("properties", "patternProperties", "$defs", "dependentSchemas")
LISTS = ("allOf", "anyOf", "oneOf", "prefixItems")
VALUES = (
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
CONDITIONS = {"if", "then", "else", "not"}
CONSTRAINTS = {"not", "if", "then", "else", "contains", "propertyNames"}
TYPING = {"type", "$ref", "properties", "items", "enum", "const", "anyOf", "oneOf", "allOf"}
TYPED = {"type", "$ref", "const", "enum", "allOf", "anyOf", "oneOf", "not"}
NEVER_SPLIT = {
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

Node = tuple[tuple[str, ...], tuple[str, ...], dict[str, Any]]


@pytest.fixture(scope="module")
def document() -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads(DOCUMENT.read_text(encoding="utf-8"))
    return loaded


def components(document: dict[str, Any]) -> dict[str, dict[str, Any]]:
    found: dict[str, dict[str, Any]] = document["components"]["schemas"]
    return found


def operations(document: dict[str, Any]) -> Iterator[tuple[str, str, dict[str, Any]]]:
    for path, methods in document["paths"].items():
        for method, operation in methods.items():
            yield method, path, operation


def nodes(schema: Any, at: tuple[str, ...] = (), via: tuple[str, ...] = ()) -> Iterator[Node]:
    """Every schema at and below ``schema``: its path, the keywords it lies below, the node."""
    if not isinstance(schema, dict):
        return
    yield at, via, schema
    for key, value in schema.items():
        if key in MAPS and isinstance(value, dict):
            for name, member in value.items():
                yield from nodes(member, (*at, key, name), (*via, key))
        elif key in LISTS and isinstance(value, list):
            for index, member in enumerate(value):
                yield from nodes(member, (*at, key, str(index)), (*via, key))
        elif key in VALUES:
            yield from nodes(value, (*at, key), (*via, key))


def numeric(node: dict[str, Any]) -> bool:
    kind = node.get("type")
    return bool({"number", "integer"} & set(kind if isinstance(kind, list) else [kind]))


def references(node: Any) -> Iterator[str]:
    if isinstance(node, dict):
        reference = node.get("$ref")
        if isinstance(reference, str) and reference.startswith(COMPONENTS):
            yield reference.removeprefix(COMPONENTS)
        for value in node.values():
            yield from references(value)
    elif isinstance(node, list):
        for item in node:
            yield from references(item)


def closure(document: dict[str, Any], roots: list[Any]) -> set[str]:
    found: set[str] = set()
    pending = [name for root in roots for name in references(root)]
    while pending:
        name = pending.pop()
        if name not in found:
            found.add(name)
            pending.extend(references(components(document)[name]))
    return found


def reach(document: dict[str, Any]) -> tuple[set[str], set[str]]:
    """The components requests reach and those responses reach, computed here."""
    requests: list[Any] = []
    responses: list[Any] = []
    for _, _, operation in operations(document):
        requests += [operation.get("requestBody", {}), operation.get("parameters", [])]
        responses.append(operation["responses"])
    return closure(document, requests), closure(document, responses)


def without_marks(node: Any) -> Any:
    if isinstance(node, list):
        return [without_marks(item) for item in node]
    if not isinstance(node, dict):
        return node
    return {
        key: without_marks(value) for key, value in node.items() if key not in (MARK, JSON_MARK)
    }


def renamed(node: Any, prefix: str = "") -> Any:
    text = json.dumps(node).replace('"#/$defs/', f'"{COMPONENTS}{prefix}')
    return json.loads(text)


def hoist(name: str, schema: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    """A builder's schema as components, by its own rule (not the generator's): a definition
    named like the root is refused, not overwritten by it."""
    definitions = schema.get("$defs", {})
    if name in definitions:
        raise ValueError(f"{name} is both a root and a definition of its own schema")
    found = {prefix + key: renamed(body, prefix) for key, body in definitions.items()}
    root = {k: v for k, v in schema.items() if k not in ("$schema", "$id", "$defs")}
    found[prefix + name] = renamed(root, prefix)
    return found


def canonical(body: Any) -> str:
    """Type-preserving text (``true``, ``1`` and ``1.0`` apart, which ``==`` takes for one)."""
    return json.dumps(body, sort_keys=True, allow_nan=False)


def merge(found: dict[str, Any], part: dict[str, Any]) -> None:
    """``part``'s components into ``found``: a name two builders give differently, as canonical
    text, is refused, never overwritten."""
    for key, body in part.items():
        if canonical(found.setdefault(key, body)) != canonical(body):
            raise ValueError(f"two builders define {key} differently")


@pytest.fixture(scope="module")
def builders() -> dict[str, Any]:
    """The components as the builders give them, before any rewrite."""
    found: dict[str, Any] = {}
    for name, schema in openapi.builder_schemas().items():
        merge(found, hoist(name, schema))
    merge(found, hoist("Document", export.document_schema(), "Substituted"))
    return found


# --- Current, and where the files come from ------------------------------------------------------


def test_the_checked_in_document_is_current() -> None:
    assert DOCUMENT.read_text(encoding="utf-8") == export.render(openapi.document()), (
        "schemas/openapi.json is stale: run `uv run python -m aibi.core.api.openapi ../schemas`"
    )


def test_schemas_holds_exactly_what_the_two_commands_write() -> None:
    written = {path.name for path in SCHEMA_DIR.iterdir()}
    assert written == {*export.SCHEMAS, openapi.DOCUMENT}


def test_the_document_is_openapi_3_1_whose_schemas_are_valid(document: dict[str, Any]) -> None:
    assert document["openapi"].startswith("3.1.")
    assert document["info"] == {"title": "aibi", "version": "aibi-http"}
    assert aibi.__version__ != "aibi-http"
    for body in components(document).values():
        jsonschema.Draft202012Validator.check_schema(body)


def test_the_tool_components_are_the_tool_schemas_rewritten(document: dict[str, Any]) -> None:
    """A round trip, and only that: it says which builder each tool's components come from (the
    files ``schema.export`` writes, MCP's schemas), not that a rewrite keeps the meaning, which
    the agreement and structural tests check."""
    checked = 0
    for name, (request, output) in TOOL_MODELS.items():
        stem = name.replace("_", "-")
        for model, kind in ((request, "request"), (output, "output")):
            built = export.SCHEMAS[f"tool.{stem}.{kind}.schema.json"]()
            for component, body in hoist(model.__name__, built).items():
                expected = openapi_normalise.normalised(body)
                assert without_marks(components(document)[component]) == expected, component
                checked += 1
    assert checked > 60


# --- (1) The document never says less than the wire ----------------------------------------------


def admits_null(node: Any) -> bool:
    if not isinstance(node, dict):
        return False
    kind = node.get("type")
    if kind == "null" or (isinstance(kind, list) and "null" in kind):
        return True
    return any(admits_null(member) for key in ("anyOf", "oneOf") for member in node.get(key, []))


def test_computed_members_keep_their_null(document: dict[str, Any]) -> None:
    """A computed member's ``null`` means not estimable (§8.1): the document keeps it."""
    found = 0
    for name, body in components(document).items():
        for at, _, node in nodes(body):
            if node.get("x-aibi-computed"):
                found += 1
                assert admits_null(node), (name, at)
    assert found > 10


def constraint_only(node: Any) -> bool:
    return isinstance(node, dict) and not (TYPING & node.keys())


def test_r1_no_constraint_only_union_or_not_sits_beside_properties(
    document: dict[str, Any],
) -> None:
    """R1's structural test (a ``not`` beside ``type``, or a union with its own ``type``, is
    legal and not matched)."""
    for name, body in components(document).items():
        for at, _, node in nodes(body):
            if "properties" not in node:
                continue
            for key in ("anyOf", "oneOf"):
                members = node.get(key)
                assert not (members and all(constraint_only(m) for m in members)), (name, at, key)
            denied = node.get("not")
            if isinstance(denied, dict):
                inner = denied.get("anyOf")
                union = isinstance(inner, list) and all(constraint_only(m) for m in inner)
                assert not constraint_only(denied), (name, at, "not")
                assert not union, (name, at, "not")


def typeless_members(schema: Any) -> list[tuple[str, ...]]:
    found: list[tuple[str, ...]] = []
    for at, via, node in nodes(schema):
        if CONSTRAINTS & set(via):
            continue
        if via and via[-1] == "allOf" and len(node) == 1 and set(node) <= {"anyOf", "oneOf"}:
            continue
        for key in ("anyOf", "oneOf"):
            for index, member in enumerate(node.get(key, [])):
                if isinstance(member, dict) and not (TYPED & member.keys()):
                    found.append((*at, key, str(index)))
    return found


def test_r2_no_typeless_union_member_outside_the_constraints(document: dict[str, Any]) -> None:
    """R2's structural test: no typeless union member outside ``not``, ``if``, ``then``,
    ``else``, ``contains`` or ``propertyNames``, but a union alone in an ``allOf`` member."""
    for name, body in components(document).items():
        assert typeless_members(body) == [], name
    for method, path, operation in operations(document):
        for given in operation.get("parameters", []):
            assert typeless_members(given["schema"]) == [], (method, path, given["name"])


def compared(builder: Any, ours: Any, at: tuple[str, ...], found: list[str]) -> None:
    """The never-split keywords alike at every node, and R2's types its parent's."""
    if not (isinstance(builder, dict) and isinstance(ours, dict)):
        return
    for key in NEVER_SPLIT & (builder.keys() | ours.keys()):
        if (key in builder) != (key in ours):
            found.append(f"{'/'.join(at)}: {key} moved or split")
        elif key in MAPS and set(builder[key]) != set(ours[key]):
            found.append(f"{'/'.join(at)}: {key} changed its names")
    for key in ("anyOf", "oneOf"):
        if key in builder and key in ours:
            for index, (theirs, mine) in enumerate(zip(builder[key], ours[key], strict=False)):
                if not (isinstance(theirs, dict) and isinstance(mine, dict)):
                    continue
                if "type" in theirs and mine.get("type") != theirs["type"]:
                    found.append(f"{'/'.join(at)}/{key}/{index}: a type changed")
                if "type" not in theirs and "type" in mine:
                    parent = (ours.get("type"), builder.get("type"))
                    if parent != (mine["type"], mine["type"]):
                        found.append(f"{'/'.join(at)}/{key}/{index}: R2 gave another type")
    for key in builder.keys() & ours.keys():
        if key in MAPS and isinstance(builder[key], dict):
            for name in builder[key].keys() & ours[key].keys():
                compared(builder[key][name], ours[key][name], (*at, key, name), found)
        elif key in LISTS and isinstance(builder[key], list):
            for index, pair in enumerate(zip(builder[key], ours[key], strict=False)):
                compared(*pair, (*at, key, str(index)), found)
        elif key in VALUES:
            compared(builder[key], ours[key], (*at, key), found)


def test_no_rewrite_splits_or_moves_a_never_split_keyword(
    document: dict[str, Any], builders: dict[str, Any]
) -> None:
    """Every node of every builder component against the document's: the keywords read with a
    sibling are where the builder put them, and every type R2 added is its parent's."""
    found: list[str] = []
    for name, body in components(document).items():
        compared(builders[name], without_marks(body), (name,), found)
    assert found == []
    assert set(openapi_normalise.NEVER_SPLIT) == NEVER_SPLIT


def test_the_components_are_the_builders_and_nothing_else(
    document: dict[str, Any], builders: dict[str, Any]
) -> None:
    """Every component is a builder's, rewritten; those nothing reaches are dropped but
    ``Substituted*``."""
    for name, body in components(document).items():
        assert without_marks(body) == openapi_normalise.normalised(builders[name]), name


# --- Every difference between a builder and the document is R1 or R2, by a predicate of its own --

MOVABLE = ("anyOf", "oneOf", "not")


def says_nothing_of_type(node: Any) -> bool:
    return isinstance(node, dict) and not (TYPING & node.keys())


def r1_moves(built: dict[str, Any], key: str) -> bool:
    """D416's R1: a ``properties`` node's ``anyOf``, ``oneOf`` or ``not`` whose members are
    constraints alone (a ``not`` of a constraint, or of a union of them)."""
    if "properties" not in built:
        return False
    value = built[key]
    if key == "not":
        inner = value.get("anyOf") if isinstance(value, dict) else None
        return says_nothing_of_type(value) or (
            isinstance(inner, list) and all(says_nothing_of_type(member) for member in inner)
        )
    return isinstance(value, list) and all(says_nothing_of_type(member) for member in value)


def judge(
    built: Any, ours: Any, at: str, constraint: bool, tally: dict[str, int], problems: list[str]
) -> None:
    """Record in ``problems`` each difference between a builder's schema and the document's that
    is not R1 (a constraint-only union or ``not`` beside ``properties`` moved into an ``allOf``
    member) or R2 (a ``type``, the parent's, added to a union member that had none of ``type``,
    ``$ref``, ``const``, ``enum`` or a composition, outside the constraint contexts), counting
    the R1 and R2 it finds in ``tally``."""
    if not (isinstance(built, dict) and isinstance(ours, dict)):
        if built != ours:
            problems.append(f"{at}: {built!r} became {ours!r}")
        return
    moved = [key for key in MOVABLE if key in built and key not in ours]
    for key in moved:
        if not r1_moves(built, key):
            problems.append(f"{at}: {key} moved and is not R1's")
    kept = built.get("allOf", [])
    if set(ours) != (set(built) - set(moved)) | ({"allOf"} if moved else set()):
        problems.append(f"{at}: the keywords are {sorted(ours)}, the builder's {sorted(built)}")
        return
    for key, value in ours.items():
        below = constraint or key in CONSTRAINTS
        if key == "allOf" and moved:
            if not (isinstance(value, list) and len(value) == len(kept) + len(moved)):
                problems.append(f"{at}: allOf is not the builder's and the moved")
                continue
            for index, member in enumerate(kept):
                judge(member, value[index], f"{at}/allOf/{index}", below, tally, problems)
            for key_moved, member in zip(moved, value[len(kept) :], strict=True):
                if set(member) != {key_moved}:
                    problems.append(f"{at}: {key_moved} moved with another keyword")
                    continue
                tally["R1"] += 1
                judge(
                    built[key_moved], member[key_moved], f"{at}/{key_moved}", True, tally, problems
                )
        elif key in MAPS and isinstance(value, dict) and isinstance(built[key], dict):
            if set(value) != set(built[key]):
                problems.append(f"{at}/{key}: other names")
                continue
            for name, member in value.items():
                judge(built[key][name], member, f"{at}/{key}/{name}", below, tally, problems)
        elif key in LISTS and isinstance(value, list) and isinstance(built[key], list):
            if len(value) != len(built[key]):
                problems.append(f"{at}/{key}: another length")
                continue
            for index, (theirs, mine) in enumerate(zip(built[key], value, strict=True)):
                place = f"{at}/{key}/{index}"
                typed = (
                    key in ("anyOf", "oneOf")
                    and isinstance(theirs, dict)
                    and isinstance(mine, dict)
                    and "type" in mine
                    and "type" not in theirs
                    and not (TYPED & theirs.keys())
                )
                if typed:
                    if constraint or built.get("type") is None or mine["type"] != built["type"]:
                        problems.append(f"{place}: a type that is not R2's")
                    tally["R2"] += 1
                    mine = {name: member for name, member in mine.items() if name != "type"}
                judge(theirs, mine, place, below, tally, problems)
        elif key in VALUES:
            judge(built[key], value, f"{at}/{key}", below, tally, problems)
        elif built[key] != value:
            problems.append(f"{at}/{key}: changed")


def test_every_difference_between_a_builder_and_the_document_is_r1_or_r2(
    document: dict[str, Any], builders: dict[str, Any]
) -> None:
    """The domain of the rewrites, held to D416's by a classifier that does not call them: no
    keyword is added, dropped or changed, no type is given but R2's, no union moved but R1's."""
    tally = {"R1": 0, "R2": 0}
    problems: list[str] = []
    for name, body in components(document).items():
        judge(builders[name], without_marks(body), name, False, tally, problems)
    assert problems == []
    assert tally["R1"] > 0, tally
    assert tally["R2"] > 0, tally


def test_the_classifier_refuses_what_is_neither_r1_nor_r2() -> None:
    def judged(built: Any, ours: Any) -> tuple[list[str], dict[str, int]]:
        tally, problems = {"R1": 0, "R2": 0}, []
        judge(built, ours, "#", False, tally, problems)
        return problems, tally

    parent = {"type": "string"}
    assert judged(
        {**parent, "anyOf": [{"pattern": "a"}, {"type": "string"}]},
        {**parent, "anyOf": [{"type": "string", "pattern": "a"}, {"type": "string"}]},
    ) == ([], {"R1": 0, "R2": 1})
    for typed in ({"const": "a"}, {"enum": ["a"]}, {"not": {"const": "b"}}, {"$ref": "#/x"}):
        assert judged(
            {**parent, "anyOf": [typed]}, {**parent, "anyOf": [{**typed, "type": "string"}]}
        )[0]
    assert judged(
        {"type": "string", "anyOf": [{"pattern": "a"}]},
        {"type": "number", "anyOf": [{"type": "number", "pattern": "a"}]},
    )[0]
    assert judged(
        {"type": "string", "anyOf": [{"pattern": "a"}]},
        {"type": "string", "anyOf": [{"type": "number", "pattern": "a"}]},
    )[0], "R2 gives the parent's type, not another"
    assert judged(
        {"not": {"anyOf": [{"pattern": "a"}]}},
        {"not": {"anyOf": [{"type": "string", "pattern": "a"}]}},
    )[0]
    assert judged({"anyOf": [{"pattern": "a"}]}, {"anyOf": [{"type": "string", "pattern": "a"}]})[0]
    shape = {"type": "object", "properties": {}}
    assert judged(
        {**shape, "oneOf": [{"required": ["a"]}]},
        {**shape, "allOf": [{"oneOf": [{"required": ["a"]}]}]},
    ) == ([], {"R1": 1, "R2": 0})
    assert judged(
        {**shape, "oneOf": [{"type": "string"}]},
        {**shape, "allOf": [{"oneOf": [{"type": "string"}]}]},
    )[0]
    assert judged({**shape, "not": {"const": 1}}, {**shape, "not": {"const": 1}})[0] == []
    assert judged({"type": "string"}, {"type": "string", "title": "x"})[0]
    assert judged({"enum": [1]}, {"enum": [2]})[0]


# --- Statuses, headers and operations ------------------------------------------------------------


def test_every_operation_declares_every_refusal_status_and_its_headers(
    document: dict[str, Any],
) -> None:
    statuses = frozenset({*errors._STATUS.values(), *errors._LIMITS.values(), 422, 401})
    assert errors.refusal_statuses() == statuses
    refusals = {"$ref": f"{COMPONENTS}Refusals"}
    for method, path, operation in operations(document):
        responses = operation["responses"]
        assert set(responses) == {"200", "default", *map(str, statuses)}, (method, path)
        for status, response in responses.items():
            if status != "200":
                assert response["content"] == {"application/json": {"schema": refusals}}
            declared = response.get("headers", {})
            want = errors.RESPONSE_HEADERS.get(int(status), ()) if status.isdigit() else ()
            assert set(declared) == set(want), (method, path, status)
            for given, header in declared.items():
                assert not header.get("required", False)
                digits = {"type": "string", "pattern": "^[0-9]+$"}
                assert header["schema"] == (
                    digits if given == errors.RETRY_AFTER else {"type": "string"}
                )
        retry = responses["429"]["headers"]["Retry-After"]["schema"]
        assert retry == {"type": "string", "pattern": "^[0-9]+$"}


def test_operator_headers_and_the_bearer_scheme(document: dict[str, Any]) -> None:
    assert openapi.OPERATOR_NAME.lower() == auth.OPERATOR_HEADER
    assert openapi.CSRF_NAME.lower() == auth.CSRF_HEADER
    assert document["components"]["securitySchemes"]["curator"]["scheme"] == "bearer"
    assert document["components"]["securitySchemes"]["curator"]["type"] == "http"
    for method, path, operation in operations(document):
        headers = {p["name"]: p for p in operation.get("parameters", []) if p["in"] == "header"}
        names = {p["name"].lower() for p in operation.get("parameters", [])}
        assert not names & {"authorization", "content-type", "cookie"}, (method, path)
        if path.startswith("/operator/"):
            assert operation["security"] == [{"curator": []}]
            operator = headers.pop("Aibi-Operator")
            assert operator["required"] is True
            assert operator["schema"] == {"type": "string", "pattern": auth.ENCODED_NAME.pattern}
            csrf = headers.pop("Aibi-CSRF", None)
            assert (csrf is None) == (method == "get" and path == CSRF_PATH), (method, path)
            assert csrf is None or csrf["required"] is False
        else:
            assert "security" not in operation
        assert headers == {}, (method, path)


def test_operation_ids_are_the_routes_own(document: dict[str, Any]) -> None:
    identifiers = [operation["operationId"] for _, _, operation in operations(document)]
    assert len(identifiers) == len(set(identifiers)) == 31
    assert all(re.fullmatch(r"[a-z](?:[a-z0-9_]*[a-z0-9])?", found) for found in identifiers)
    by_body = {body.operation: body for body in OPERATOR_BODIES}
    for method, path, operation in operations(document):
        found = operation["operationId"]
        if path.startswith(f"{TOOLS_PREFIX}/"):
            assert found == path.removeprefix(f"{TOOLS_PREFIX}/")
            body = TOOL_MODELS[found][0].__name__
        elif found in by_body:
            body = by_body[found].model.__name__
        else:
            body = None
        given = operation.get("requestBody", {}).get("content", {})
        if body is not None:
            assert given == {"application/json": {"schema": {"$ref": f"{COMPONENTS}{body}"}}}
        elif found == UPLOAD_OPERATION:
            assert set(given) == {"application/octet-stream"}
        else:
            assert method == "get", (method, path)
            assert given == {}, (method, path)


def test_every_operator_body_is_one_constant_that_the_route_reads() -> None:
    """Every ``_body`` call of the router's source takes a constant of ``OPERATOR_BODIES``
    whose name is its handler's, and the handler's route takes its name and operation id."""
    source = ast.parse(Path(router.__file__).read_text(encoding="utf-8"))
    constants = {
        name: value for name, value in vars(router).items() if isinstance(value, OperatorBody)
    }
    used: list[str] = []
    calls = [
        node
        for node in ast.walk(source)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_body"
    ]
    for handler in ast.walk(source):
        if not isinstance(handler, ast.AsyncFunctionDef | ast.FunctionDef):
            continue
        inside = [
            node
            for node in ast.walk(handler)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_body"
        ]
        if not inside or handler.name == "operator_router":
            continue
        [call] = inside
        argument = call.args[2]
        assert isinstance(argument, ast.Name), handler.name
        body = constants[argument.id]
        assert body.name == handler.name
        [decorator] = handler.decorator_list
        assert isinstance(decorator, ast.Call)
        keywords = {word.arg: ast.unparse(word.value) for word in decorator.keywords if word.arg}
        assert keywords["name"] == f"{argument.id}.name", handler.name
        assert keywords["operation_id"] == f"{argument.id}.operation", handler.name
        used.append(argument.id)
    assert len(calls) == len(used) == len(OPERATOR_BODIES) == 14
    assert {id(constants[name]) for name in used} == {id(body) for body in OPERATOR_BODIES}


@contextmanager
def serving(root: Path, rates: dict[str, dict[str, int]]) -> Iterator[tuple[Any, str]]:
    """The application over a real store, with the tools, at ``rates``."""
    root.mkdir(exist_ok=True)
    (root / "imports").mkdir()
    token = new_token()
    config = ServerConfig.model_validate(
        {
            "server": {"rates": rates},
            "curator": {"token_hash": hash_token(token)},
            "storage": {"data": "data", "imports": ["imports"]},
        },
        context={BASE: root},
    )
    store = Store(config.storage.data)
    registry = PackRegistry((), core_version=aibi.__version__)
    app = create_app(
        Policy.of(config, csrf_key=b"k" * 32),
        services_of(config, store, registry),
        tools=tools_of(config, store, registry),
    )
    try:
        yield app, token
    finally:
        store.close()


@pytest.fixture
def served(tmp_path: Path) -> Iterator[tuple[Any, str]]:
    """The application over a real store, with the tools, and generous rates."""
    rates = {"operator": GENEROUS, "api": GENEROUS, "token_failures": GENEROUS}
    with serving(tmp_path, rates) as found:
        yield found


def test_the_operations_are_those_of_the_server_over_a_store(
    document: dict[str, Any], served: tuple[Any, str]
) -> None:
    """Against an application over a real store (the document's own is built on stubs, whose
    FastAPI document is the same)."""
    app, _ = served
    real = app.openapi()
    assert openapi._app().openapi() == real
    theirs = {(m, p): o for p, ms in real["paths"].items() for m, o in ms.items()}
    ours = {(m, p): o for m, p, o in operations(document)}
    assert set(ours) == set(theirs)
    for key, operation in ours.items():
        given = theirs[key]
        assert operation["operationId"] == given["operationId"]
        assert operation["summary"] == given["summary"]
        mine = [
            (p["name"], p["in"], p["required"])
            for p in operation.get("parameters", [])
            if p["name"] not in ("Aibi-Operator", "Aibi-CSRF")
        ]
        assert mine == [(p["name"], p["in"], p["required"]) for p in given.get("parameters", [])]
        if "requestBody" in given:
            assert "requestBody" in operation


def test_the_web_bundles_routes_are_not_in_the_document(
    document: dict[str, Any], bundle_dir: Path, make_bundled: Callable[..., Any]
) -> None:
    """The served bundle's routes (D410, D411: ``/``, ``/datasets/<id>``, ``/curate`` and
    ``/assets/<name>``) are outside the JSON API: an application serving a bundle has the document's
    own operations, and the stubs' FastAPI document is still the document's."""
    bundled = make_bundled(bundle_dir)
    paths = {
        context.original_route.path
        for context in iter_route_contexts(bundled.app.routes)
        if isinstance(context.original_route, APIRoute)
    }
    assert {"/", "/datasets/{dataset}", "/curate", "/assets/{name}"} <= paths
    real = bundled.app.openapi()
    assert real == openapi._app().openapi()
    assert set(real["paths"]) == set(document["paths"])


def codes(response: Any) -> list[str]:
    try:
        return [found["code"] for found in response.json()["refusals"]]
    except (ValueError, KeyError, TypeError):
        return []


def sent(client: TestClient, method: str, path: str, upload: bool, headers: dict[str, str]) -> Any:
    """A request a browser could send to an operation: a JSON body ``{}``, or an upload's
    bytes."""
    if method == "get":
        return client.get(path, headers=headers)
    if upload:
        kind = {"Content-Type": "application/octet-stream"}
        return client.post(path, params={"extension": "csv"}, content=b"x", headers=headers | kind)
    return client.post(path, json={}, headers=headers)


FETCH = {"Sec-Fetch-Site": "same-origin", "Sec-Fetch-Mode": "cors"}
BROWSERS = {"origin": {"Origin": OWN}, "fetch metadata": FETCH, "both": {"Origin": OWN, **FETCH}}
"""What marks a request a browser's (D263): an ``Origin``, or ``Sec-Fetch-*`` headers, or both."""


@pytest.mark.parametrize("browser", BROWSERS)
def test_aibi_csrf_is_declared_where_the_server_requires_it(
    document: dict[str, Any], served: tuple[Any, str], browser: str
) -> None:
    """Driven from the document's operations: a browser's request (from the server's own
    origin, by its ``Origin``, its ``Sec-Fetch-*`` headers or both) without the header is
    ``CSRF_REQUIRED`` exactly where the document declares it, and the token ``GET /operator/csrf``
    gives admits it."""
    app, token = served
    base = {
        "Authorization": f"Bearer {token}",
        "Aibi-Operator": encode_operator("Probe"),
        **BROWSERS[browser],
    }
    with TestClient(app, base_url=OWN, raise_server_exceptions=False) as client:
        answered = client.get(CSRF_PATH, headers=base)
        assert answered.status_code == 200
        csrf = answered.json()["csrf"]
        checked = 0
        for method, path, operation in operations(document):
            filled = (
                path.replace("{dataset}", "d")
                .replace("{descriptor}", "sites")
                .replace("{proposal}", "1")
            )
            declared = any(p["name"] == "Aibi-CSRF" for p in operation.get("parameters", []))
            upload = operation.get("operationId") == UPLOAD_OPERATION
            without = codes(sent(client, method, filled, upload, base))
            assert "ORIGIN_NOT_ALLOWED" not in without, (method, path)
            refused = "CSRF_REQUIRED" in without
            if path.startswith("/operator/"):
                assert refused == declared, (method, path, without)
            else:
                assert not refused, (method, path)
            if declared:
                admitted = codes(sent(client, method, filled, upload, {**base, "Aibi-CSRF": csrf}))
                assert not {"CSRF_REQUIRED", "ORIGIN_NOT_ALLOWED"} & set(admitted), (method, path)
            checked += 1
        assert checked == 31


# --- The headers a response carries: one table, read by the server and by the document ----------

JSON_BODY = {"Content-Type": "application/json"}
TRANSPORT = {
    "content-type",
    "content-length",
    "content-security-policy",
    "cache-control",
    "strict-transport-security",
    "vary",
    "date",
    "server",
}
"""The headers every response carries and the document does not declare: the security headers
(``protection.SECURITY_HEADERS`` and the policy's), the page and transport ones."""


def declared_headers(document: dict[str, Any], path: str, method: str, status: int) -> set[str]:
    responses = document["paths"][path][method]["responses"]
    response = responses.get(str(status), responses["default"])
    return {name.lower() for name in response.get("headers", {})}


def sent_headers(response: Any) -> set[str]:
    ignored = TRANSPORT | {name.decode() for name, _ in SECURITY_HEADERS}
    return {name.lower() for name in response.headers} - ignored


def test_the_headers_the_server_sends_are_those_the_document_declares_at_each_status(
    document: dict[str, Any], tmp_path: Path
) -> None:
    """Every status a client can drive on an application over a real store (the 408, 409, 411 and
    503 of timing and of the imports the unit test below covers): the headers sent beside the
    security and transport ones are exactly those the document declares for that status."""
    tool, datasets = "/api/tools/search_catalog", "/operator/datasets"
    seen: dict[int, set[str]] = {}

    def check(response: Any, path: str, method: str) -> None:
        status = response.status_code
        sent = sent_headers(response)
        assert sent == declared_headers(document, path, method, status), (path, method, status)
        seen.setdefault(status, set()).update(sent)

    rates = {"operator": GENEROUS, "api": GENEROUS, "token_failures": GENEROUS}
    with (
        serving(tmp_path / "open", rates) as (app, token),
        TestClient(app, base_url=OWN, raise_server_exceptions=False) as client,
    ):
        named = {"Authorization": f"Bearer {token}", "Aibi-Operator": encode_operator("Probe")}
        from_a_browser = {**named, "Origin": OWN}
        from_elsewhere = {**named, "Origin": "http://evil"}
        by_dataset = f"{datasets}/{{dataset}}"
        check(client.get(datasets, headers={"Authorization": f"Bearer {token}"}), datasets, "get")
        check(client.get(datasets), datasets, "get")
        check(client.get(datasets, headers=from_a_browser), datasets, "get")
        check(client.get(datasets, headers=from_elsewhere), datasets, "get")
        check(client.get(f"{datasets}/nope", headers=named), by_dataset, "get")
        check(client.get("/nowhere", headers=named), datasets, "get")
        check(client.get(tool), tool, "post")
        text = {"Content-Type": "text/plain"}
        check(client.post(tool, content=b"{}", headers=text), tool, "post")
        check(client.post(tool, json={"zz": 1}), tool, "post")
        big = b" " * (MAX_BODY_BYTES + 1)
        check(client.post(tool, content=big, headers=JSON_BODY), tool, "post")
        withdrawn = client.post(f"{datasets}/nope/withdraw", json={"release": 1}, headers=named)
        check(withdrawn, f"{by_dataset}/withdraw", "post")
    tight = {"operator": {"per_minute": 1, "burst": 1}, "api": {"per_minute": 1, "burst": 1}}
    with (
        serving(tmp_path / "tight", tight) as (app, token),
        TestClient(app, base_url=OWN, raise_server_exceptions=False) as client,
    ):
        named = {"Authorization": f"Bearer {token}", "Aibi-Operator": encode_operator("Probe")}
        answers = [client.post(tool, json={}) for _ in range(3)]
        check(answers[-1], tool, "post")
        answers = [client.get(datasets, headers=named) for _ in range(3)]
        check(answers[-1], datasets, "get")
    assert {400, 401, 403, 404, 405, 413, 415, 422, 429} <= set(seen), sorted(seen)
    assert {status: names for status, names in seen.items() if names} == {
        401: {"www-authenticate"},
        405: {"allow"},
        429: {"retry-after"},
    }


def a_refusal_at(status: int) -> Refusal:
    """A refusal whose status is ``status``, by its code or its limit."""
    for code, answered in errors._STATUS.items():
        if answered == status:
            return errors.refusal(RefusalCode(code), "m")
    for name, answered in errors._LIMITS.items():
        if answered == status:
            found = errors.refusal(RefusalCode.LIMIT_EXCEEDED, "m")
            return found.model_copy(update={"limit": Limit(name=name, max=1)})
    return errors.refusal(RefusalCode.INVALID_VALUE, "m")


def test_a_refusal_carries_only_the_headers_its_status_does_in_the_table() -> None:
    """Every status the document declares, at the server's own answer to a refusal of it (a
    JSON refusal and a page), and the refusal of a header the table does not give the status."""
    ignored = TRANSPORT | {name.lower() for name in PAGE_HEADERS}
    ignored |= {name.decode() for name, _ in SECURITY_HEADERS}
    statuses = errors.refusal_statuses() - {401}
    assert statuses >= {408, 409, 411, 503}
    for status in sorted(statuses):
        found = a_refusal_at(status)
        assert errors.status_of(found) == status
        by_default = {"retry-after"} if status == 503 else set()
        assert by_default <= {name.lower() for name in errors.RESPONSE_HEADERS.get(status, ())}
        for response in (errors.refused([found]), errors.refused_page("", [found])):
            assert response.status_code == status
            assert {name.lower() for name in response.headers} - ignored == by_default, status
    assert errors.refused([a_refusal_at(503)]).headers["retry-after"] == str(errors.RETRY_IMPORT)
    for status, name in ((408, "Retry-After"), (401, "Location"), (404, "Allow"), (429, "Allow")):
        with pytest.raises(ValueError, match="does not declare"):
            errors.refused([a_refusal_at(status)], headers={name: "1"})
        with pytest.raises(ValueError, match="does not declare"):
            errors.refused_page("", [a_refusal_at(status)], headers={name: "1"})
    errors.refused([a_refusal_at(405)], headers={"allow": "GET"})
    assert errors.RESPONSE_HEADERS[401] == (errors.WWW_AUTHENTICATE,)


def test_the_http_error_handler_answers_with_the_headers_of_the_table_alone() -> None:
    """The handler of Starlette's own errors (a 401 or 405 an application raises behind request
    protection, which answers its own): the challenge on 401, ``Allow`` on 405, none elsewhere."""
    request = Request({"type": "http", "method": "GET", "path": "/", "headers": []})
    handlers = errors._Handlers(pages=False)

    def answered(error: StarletteHTTPException) -> tuple[int, dict[str, str]]:
        response = asyncio.run(handlers.http(request, error))
        return response.status_code, {
            name: value
            for name, value in response.headers.items()
            if name not in TRANSPORT | {n.decode() for n, _ in SECURITY_HEADERS}
        }

    assert answered(StarletteHTTPException(401)) == (401, {"www-authenticate": auth.AUTHORIZATION})
    assert answered(StarletteHTTPException(405, headers={"Allow": "POST"})) == (
        405,
        {"allow": "POST"},
    )
    assert answered(StarletteHTTPException(405)) == (405, {})
    assert answered(StarletteHTTPException(404)) == (404, {})
    assert answered(StarletteHTTPException(418)) == (418, {})
    assert answered(StarletteHTTPException(500)) == (500, {})


def test_every_operation_summary_is_plain_text_without_edge_spaces(
    document: dict[str, Any],
) -> None:
    for method, path, operation in operations(document):
        summary = operation["summary"]
        assert summary, (method, path)
        assert summary == summary.strip(), (method, path, summary)
    by_name = {
        operation["operationId"]: operation["summary"] for *_, operation in operations(document)
    }
    assert (by_name["import_dataset"], by_name["erase_dataset"], by_name["open_session"]) == (
        "Import",
        "Erase",
        "Open",
    )


# --- (2) Marks ----------------------------------------------------------------------------------


def test_response_numbers_are_marked_and_no_other_number_is(document: dict[str, Any]) -> None:
    requests, responses = reach(document)
    assert not requests & responses
    marked = 0
    for name, body in components(document).items():
        inside_json = JSON_MARK in body
        for at, via, node in nodes(body):
            conditional = bool(CONDITIONS & set(via))
            if conditional or inside_json:
                assert MARK not in node, (name, at)
            elif numeric(node):
                assert (MARK in node) == (name in responses), (name, at)
                marked += MARK in node
            else:
                assert MARK not in node, (name, at)
    assert marked > 30
    for method, path, operation in operations(document):
        assert MARK not in json.dumps(operation), (method, path)


def json_value(name: str, null: bool) -> dict[str, Any]:
    """``export.json_value`` of a name with ``null`` or without, as the component ``name``."""
    made = "OutputJson" if null else "DocumentJson"
    text = json.dumps(export.json_value(made))
    return json.loads(text.replace(f'"#/$defs/{made}"', f'"{COMPONENTS}{name}"'))


def unwrapped(node: Any) -> Any:
    """``node`` with ``export._wrap`` undone wherever it was applied."""
    wrap = renamed(export._wrap({}))
    if isinstance(node, list):
        return [unwrapped(item) for item in node]
    if not isinstance(node, dict):
        return node
    if (
        set(node) == set(wrap)
        and len(node["anyOf"]) == 2
        and node["anyOf"][1] == wrap["anyOf"][1]
        and (node["if"], node["then"]) == (wrap["if"], wrap["then"])
    ):
        return unwrapped(node["anyOf"][0])
    return {key: unwrapped(value) for key, value in node.items()}


def test_x_aibi_json_marks_exactly_the_json_value_components(document: dict[str, Any]) -> None:
    """A component carries the mark exactly when, unwrapped and its marks stripped, it is
    ``_json_value``'s definition of its name, with ``null`` or without."""
    marked = set()
    for name, body in components(document).items():
        plain = unwrapped(without_marks(body))
        shaped = plain in (json_value(name, True), json_value(name, False))
        assert shaped == (JSON_MARK in body), name
        if shaped:
            marked.add(name)
    assert marked == {
        "DescriptorJson",
        "DocumentJson",
        "OutputJson",
        "ParameterValue",
        "RequestJson",
        "SubstitutedDocumentJson",
    }


def validator_of(document: dict[str, Any], name: str) -> jsonschema.Draft202012Validator:
    definitions = json.loads(json.dumps(components(document)).replace(COMPONENTS, "#/$defs/"))
    return jsonschema.Draft202012Validator({"$ref": f"#/$defs/{name}", "$defs": definitions})


def test_x_aibi_json_direction_and_null_are_computed(document: dict[str, Any]) -> None:
    _, responses = reach(document)
    for name, body in components(document).items():
        if JSON_MARK in body:
            direction = "response" if name in responses else "request"
            null = validator_of(document, name).is_valid(None)
            assert body[JSON_MARK] == {"direction": direction, "null": null}, name


def test_only_substituted_components_are_unreached(document: dict[str, Any]) -> None:
    requests, responses = reach(document)
    assert not requests & responses
    unreached = set(components(document)) - requests - responses
    definitions = cast(dict[str, Any], export.document_schema()["$defs"])
    expected = {f"Substituted{name}" for name in definitions}
    assert unreached == expected | {"SubstitutedDocument"}
    for name in unreached:
        assert MARK not in json.dumps(components(document)[name])
    assert closure(document, [{"$ref": f"{COMPONENTS}SubstitutedDocument"}]) <= unreached


def test_a_response_s_numeric_const_or_enum_is_typed_or_a_constraint(
    document: dict[str, Any],
) -> None:
    """Rendering-free: a numeric ``const`` or ``enum`` of a response sits beside a numeric
    ``type`` (so it is marked), in an ``allOf`` conjunct that types nothing, or in a condition;
    today the constraints are the cohorts' positions alone."""
    _, responses = reach(document)
    constrained = set()

    def number(value: Any) -> bool:
        return isinstance(value, int | float) and not isinstance(value, bool)

    for name in responses:
        body = components(document)[name]
        for at, via, node in nodes(body):
            values = [node["const"]] if "const" in node else list(node.get("enum", []))
            if not any(number(value) for value in values) or numeric(node):
                continue
            parents = [part for part in nodes(body) if at[: len(part[0])] == part[0]]
            conjunct = any(
                via_ and via_[-1] == "allOf" and constraint_only(parent)
                for _, via_, parent in parents
            )
            assert conjunct or CONDITIONS & set(via), (name, at)
            constrained.add(name)
    assert constrained == {"ResultEnvelope"}
    assert openapi.unconstrained_numbers({"items": {"enum": [1, 2]}}) == ["#/items"]


def test_only_known_extension_keys(document: dict[str, Any]) -> None:
    allowed = {"x-aibi-data", "x-aibi-computed", MARK, JSON_MARK}

    def keys(node: Any) -> Iterator[str]:
        if isinstance(node, dict):
            for key, value in node.items():
                if key.startswith("x-"):
                    yield key
                yield from keys(value)
        elif isinstance(node, list):
            for item in node:
                yield from keys(item)

    assert set(keys(document)) == allowed
    for name, build in export.SCHEMAS.items():
        assert set(keys(build())) <= {"x-aibi-data", "x-aibi-computed"}, name


def test_parameters_carry_no_null_and_no_title(document: dict[str, Any]) -> None:
    for method, path, operation in operations(document):
        for given in operation.get("parameters", []):
            text = json.dumps(given["schema"])
            assert '"null"' not in text, (method, path, given["name"])
            assert '"title"' not in text, (method, path, given["name"])


SAYS_NOTHING = {"title", "description", "default", "examples", "deprecated", "$comment"}
"""Annotations: where a union's one member and its parent both give one, the parent's stands."""


def r4_of(schema: dict[str, Any]) -> dict[str, Any]:
    """What FastAPI's parameter schema becomes by R4 alone, by this test's own rule: its ``title``
    and the ``null`` branch of a union gone (the one member left taking the union's place, key by
    key: a keyword only one of them gives is kept, an annotation both give is the parent's, and
    any other keyword both give must be one value), and a ``null`` default; nothing else."""
    rest = {key: value for key, value in schema.items() if key != "title"}
    union = rest.get("anyOf")
    if isinstance(union, list) and {"type": "null"} in union:
        left = [member for member in union if member != {"type": "null"}]
        del rest["anyOf"]
        if len(left) == 1:
            [member] = left
            for key in member.keys() & rest.keys() - SAYS_NOTHING:
                assert json.dumps(member[key]) == json.dumps(rest[key]), key
            rest = {
                key: rest[key] if key in rest else member[key]
                for key in sorted(member.keys() | rest.keys())
            }
        else:
            rest["anyOf"] = left
    if rest.get("default", 0) is None:
        del rest["default"]
    return rest


OPERATOR_HEADERS = ("Aibi-Operator", "Aibi-CSRF")


def fastapi_parameters(operation: dict[str, Any]) -> list[dict[str, Any]]:
    """An operation's parameters but the operator's two headers, which FastAPI does not have."""
    found: list[dict[str, Any]] = operation.get("parameters", [])
    return [given for given in found if given["name"] not in OPERATOR_HEADERS]


def parameter_problems(document: dict[str, Any], generated: dict[str, Any]) -> list[str]:
    """Every parameter of ``document`` against FastAPI's own for the same method, path, name and
    place: the schema differs by R4's rewrite and then R1's and R2's alone (``judge``), and the
    operator's two headers are the only parameters FastAPI does not have."""
    theirs = {
        (method, path, given["name"], given["in"]): given["schema"]
        for path, methods in generated["paths"].items()
        for method, operation in methods.items()
        for given in operation.get("parameters", [])
    }
    problems: list[str] = []
    seen: set[tuple[str, str, str, str]] = set()
    tally = {"R1": 0, "R2": 0}
    for method, path, operation in operations(document):
        for given in fastapi_parameters(operation):
            key = (method, path, given["name"], given["in"])
            if key in theirs:
                seen.add(key)
                judge(r4_of(theirs[key]), given["schema"], "/".join(key), False, tally, problems)
            else:
                problems.append(f"{key}: not a parameter of FastAPI's")
    problems += [f"{key}: FastAPI's, and not the document's" for key in sorted(set(theirs) - seen)]
    return problems


def test_every_parameter_differs_from_fastapi_s_by_r4_r1_and_r2_alone(
    document: dict[str, Any],
) -> None:
    """The parameters' rewrites held to their domain, as the components' are: a ``pattern``, a
    ``maxLength`` or any other keyword dropped, changed or added fails (the document would say
    less than the wire), beside the absence of ``null`` and ``title``."""
    generated = openapi._app().openapi()
    assert parameter_problems(document, generated) == []
    assert sum(len(fastapi_parameters(o)) for *_, o in operations(document)) > 10
    for keyword in ("pattern", "maxLength", "minLength", "maximum", "type"):
        changed = json.loads(json.dumps(document))
        victims = [
            found
            for *_, operation in operations(changed)
            for found in fastapi_parameters(operation)
            if keyword in found["schema"]
        ]
        assert victims, keyword
        del victims[0]["schema"][keyword]
        assert parameter_problems(changed, generated), keyword
    retitled = json.loads(json.dumps(document))
    for *_, operation in operations(retitled):
        for found in fastapi_parameters(operation):
            found["schema"]["title"] = "T"
    assert parameter_problems(retitled, generated)


def test_every_request_body_is_required_where_a_route_takes_one(document: dict[str, Any]) -> None:
    """From the routes: an operation with a request model, or the upload, has a required body
    (a client typed ``requestBody?`` could omit one the server answers 422), and any other has
    none."""
    routes = openapi._routes(openapi._app())
    with_body = 0
    for method, path, operation in operations(document):
        route = routes[(method, path)]
        takes = openapi._request_model(route) is not None or route.operation_id == UPLOAD_OPERATION
        if takes:
            assert operation["requestBody"]["required"] is True, (method, path)
            with_body += 1
        else:
            assert "requestBody" not in operation, (method, path)
    assert with_body == sum(1 for _, _, o in operations(document) if "requestBody" in o) > 20


# --- (3) Nothing secret --------------------------------------------------------------------------


def test_the_document_holds_no_secret_or_configured_value(document: dict[str, Any]) -> None:
    def strings(node: Any) -> Iterator[str]:
        if isinstance(node, dict):
            for key, value in node.items():
                yield key
                yield from strings(value)
        elif isinstance(node, list):
            for item in node:
                yield from strings(item)
        elif isinstance(node, str):
            yield node

    found = list(strings(document))
    assert len(found) > 1000
    hits = {s for s in found if SECRET_RE.search(s) or SECRET_ALONE_RE.search(s)}
    assert hits <= {"^ses_[A-Za-z0-9_-]{43}$"}
    assert not [s for s in found if auth.TOKEN_HASH_RE.fullmatch(s)]
    assert not [s for s in found if re.search(r"[0-9a-f]{32}", s)]
    assert not [s for s in found if "/tmp" in s or "/home/" in s or tempfile.gettempdir() in s]


# --- (5) The generator's own refusals and surroundings -------------------------------------------


def fresh(code: str) -> bytes:
    """What ``code`` writes to stdout in a process of its own (no test has run in it)."""
    done = subprocess.run(
        [sys.executable, "-c", code], check=True, timeout=120, capture_output=True
    )
    return done.stdout


def test_the_generator_loads_no_pack_and_no_duckdb_and_writes_the_checked_in_file() -> None:
    """In a process of its own, as ``python -m aibi.core.api.openapi`` runs it: no DuckDB and no
    pack is loaded, and its output is the checked-in document byte for byte."""
    probe = (
        "import sys\n"
        "from aibi.core.api.openapi import document\n"
        "from aibi.core.schema.export import render\n"
        "text = render(document())\n"
        "assert 'duckdb' not in sys.modules\n"
        "assert not [m for m in sys.modules if m.startswith('aibi.packs')]\n"
        "sys.stdout.buffer.write(text.encode('utf-8'))\n"
    )
    assert fresh(probe) == DOCUMENT.read_bytes()


def test_the_generator_refuses_a_component_both_directions_reach(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    given = openapi._request_model

    def shared(route: Any) -> Any:
        return Refusals if route.path == "/api/health" else given(route)

    monkeypatch.setattr(openapi, "_request_model", shared)
    with pytest.raises(ValueError, match="both reach"):
        openapi.document()


def test_the_generator_refuses_two_builders_defining_one_name_with_the_same_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The two definitions have the same keys and differ in a value (one ``required`` member
    fewer), so a comparison of keys alone would take them for one."""
    given = openapi._builders

    def clashing(models: Any) -> dict[str, Any]:
        found = cast(dict[str, Any], given(models))
        clash = json.loads(json.dumps(export.unmarked(found["Refusals"])))
        refusal = clash["$defs"]["Refusal"]
        assert refusal["required"]
        refusal["required"] = refusal["required"][:-1]
        assert refusal.keys() == found["Refusals"]["$defs"]["Refusal"].keys()
        return {**found, "Clash": clash}

    monkeypatch.setattr(openapi, "_builders", clashing)
    with pytest.raises(ValueError, match="define Refusal differently"):
        openapi.document()
    with pytest.raises(ValueError, match="define Refusal differently"):
        merge(
            hoist("Refusals", openapi.builder_schemas()["Refusals"]),
            hoist("Clash", clashing(())["Clash"]),
        )


def test_the_fixture_s_merge_compares_as_canonical_text() -> None:
    merge(found := {"A": {"default": 1}}, {"A": {"default": 1}})
    with pytest.raises(ValueError, match="define A differently"):
        merge(found, {"A": {"default": True}})
    with pytest.raises(ValueError, match="not JSON compliant"):
        merge({"N": {"default": float("nan")}}, {"N": {"default": float("nan")}})


def test_the_vocabulary_refuses_an_unlisted_keyword(document: dict[str, Any]) -> None:
    tools = {name: build() for name, build in export.SCHEMAS.items() if name.startswith("tool.")}
    assert openapi_vocabulary.unlisted(document, tools) == []
    changed = json.loads(json.dumps(document))
    components(changed)["Refusal"]["unevaluatedProperties"] = False
    assert openapi_vocabulary.unlisted(changed, {}) == [
        "openapi.json: unevaluatedProperties at component"
    ]
    moved = json.loads(json.dumps(document))
    components(moved)["Refusal"]["properties"]["code"]["prefixItems"] = []
    assert openapi_vocabulary.unlisted(moved, {}) == ["openapi.json: prefixItems at property"]


def test_the_rewrites_refuse_what_they_cannot_type() -> None:
    assert openapi_normalise.untyped({"anyOf": [{"pattern": "a"}, {"type": "string"}]}) == [
        "#/anyOf/0"
    ]
    typed = openapi_normalise.normalised({"type": "string", "anyOf": [{"pattern": "a"}]})
    assert typed == {"type": "string", "anyOf": [{"type": "string", "pattern": "a"}]}
    moved = openapi_normalise.normalised(
        {"type": "object", "properties": {"a": {}}, "oneOf": [{"required": ["a"]}]}
    )
    assert moved == {
        "type": "object",
        "properties": {"a": {}},
        "allOf": [{"oneOf": [{"required": ["a"]}]}],
    }


def test_the_generator_refuses_two_route_models_of_one_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A second class named ``Csrf`` as a route's response would give ``GET /operator/csrf`` its
    schema, one named ``WithdrawRequest`` as a tool's request would give the withdrawal's body its
    own, and one named ``Refusals`` would give every refusal of every operation its own: the
    generator refuses each, and the same class twice is fine."""

    class Csrf(BaseModel):
        something_else: int

    class WithdrawRequest(BaseModel):
        something_else: int

    class Refusals(BaseModel):
        something_else: int

    response, request = openapi._response_model, openapi._request_model

    def swapped_response(route: RouteContext) -> type[BaseModel]:
        return Csrf if route.path == "/operator/datasets" else response(route)

    def refusals_response(route: RouteContext) -> type[BaseModel]:
        return Refusals if route.path == "/operator/datasets" else response(route)

    def swapped_request(route: RouteContext) -> type[BaseModel] | None:
        return WithdrawRequest if route.path == f"{TOOLS_PREFIX}/search_catalog" else request(route)

    monkeypatch.setattr(openapi, "_response_model", swapped_response)
    with pytest.raises(ValueError, match="two models are named Csrf"):
        openapi.document()
    monkeypatch.undo()
    monkeypatch.setattr(openapi, "_request_model", swapped_request)
    with pytest.raises(ValueError, match="two models are named WithdrawRequest"):
        openapi.document()
    monkeypatch.undo()
    monkeypatch.setattr(openapi, "_response_model", refusals_response)
    with pytest.raises(ValueError, match="two models are named Refusals"):
        openapi.document()
    monkeypatch.undo()
    assert openapi.builder_schemas()["Csrf"] == openapi.builder_schemas()["Csrf"]


def an_enum_of_the_name(name: str) -> tuple[type[BaseModel], dict[str, Any]]:
    """A route model named ``name``, and the builder's schema of it, one of whose fields is an
    enum of the same name."""
    mode = enum.Enum(name, {"a": "a", "b": "b"})
    model = create_model(name, mode=(mode, ...), other=(str, ...))
    return model, export.output_schema(model, name)


def a_model_of_the_name(name: str) -> tuple[type[BaseModel], dict[str, Any]]:
    """The same, with a model of the same name in the enum's place."""
    inner = create_model(name, shape=(int, ...))
    model = create_model(name, inner=(inner, ...), other=(str, ...))
    return model, export.output_schema(model, name)


@pytest.mark.parametrize("make", [an_enum_of_the_name, a_model_of_the_name])
def test_a_definition_named_like_its_own_root_is_refused(
    monkeypatch: pytest.MonkeyPatch, make: Callable[[str], tuple[type[BaseModel], dict[str, Any]]]
) -> None:
    """A route model ``Csrf`` that reaches another class Pydantic names ``Csrf`` (an enum, or a
    model of another module) has it as a definition of its own name, which the root would replace
    in the components, pointing every reference to it at the root: refused by the generator's
    ``hoisted`` and by this module's ``hoist``, so that the fixture cannot agree with a generator
    that overwrites; through the whole document too."""
    model, built = make("Csrf")
    assert "Csrf" in built["$defs"]
    with pytest.raises(ValueError, match="both a root and a definition of its own schema"):
        openapi.hoisted("Csrf", built)
    with pytest.raises(ValueError, match="both a root and a definition of its own schema"):
        hoist("Csrf", built)
    response = openapi._response_model
    monkeypatch.setattr(
        openapi,
        "_response_model",
        lambda route: model if route.path == "/operator/csrf" else response(route),
    )
    with pytest.raises(ValueError, match="both a root and a definition of its own schema"):
        openapi.document()


def test_a_definition_name_is_one_definition_over_every_builder_s_output() -> None:
    """Brute force over what the builders output for the route models (so the enums and any
    other class Pydantic defines are reached, not the models' fields alone): no schema defines its
    own root's name, and a name two builders define has one body, by this module's own merge
    (``hoist`` and ``merge`` refuse where they would overwrite)."""
    schemas = cast(dict[str, dict[str, Any]], openapi.builder_schemas())
    found: dict[str, Any] = {}
    for name, schema in schemas.items():
        assert name not in schema.get("$defs", {}), name
        merge(found, hoist(name, schema))
    merge(found, hoist("Document", export.document_schema(), "Substituted"))
    assert len(found) > 100
    assert [name for name, body in found.items() if "enum" in body]
    roots = {openapi.REFUSALS}
    for route in openapi._routes(openapi._app()).values():
        roots.add(openapi._response_model(route).__name__)
        if (request := openapi._request_model(route)) is not None:
            roots.add(request.__name__)
    assert set(schemas) == roots
    assert roots <= set(found)


def test_the_generator_refuses_a_component_key_openapi_3_1_does_not_allow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A generic route model is named ``Page[str]``, which is no component key (OpenAPI 3.1 allows
    ``^[a-zA-Z0-9._-]+$``), where Pydantic names the same class ``Page_str_`` when it is nested."""
    item = TypeVar("item")

    class Page(BaseModel, Generic[item]):
        items: list[item]

    assert Page[str].__name__ == "Page[str]"
    response = openapi._response_model
    monkeypatch.setattr(
        openapi,
        "_response_model",
        lambda route: Page[str] if route.path == "/operator/datasets" else response(route),
    )
    with pytest.raises(ValueError, match=r"'Page\[str\]' is no component key"):
        openapi.document()


def test_the_generator_refuses_a_route_without_a_response_model_or_an_id_of_its_own(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def bare() -> None: ...

    with pytest.raises(ValueError, match="has no response model"):
        openapi._response_model(RouteContext(APIRoute("/x", bare, response_model=None)))
    given = openapi._app

    def renamed(operation_id: str | None) -> Callable[[], Any]:
        """The application with ``/api/health``'s operation id changed, on the route and context the
        generator reads and in the document FastAPI describes it by."""

        def make() -> Any:
            app = given()
            for context in iter_route_contexts(app.routes):
                if isinstance(context.original_route, APIRoute) and context.path == "/api/health":
                    # The route's own, and the effective context FastAPI copies it into (the
                    # generator reads the context: an inclusion may change what the route holds).
                    context.original_route.operation_id = operation_id
                    if context._route_context is not None:
                        context._route_context.operation_id = operation_id
            described = app.openapi

            def openapi_() -> dict[str, Any]:
                found = described()
                if operation_id is not None:
                    found["paths"]["/api/health"]["get"]["operationId"] = operation_id
                return found

            setattr(app, "openapi", openapi_)  # noqa: B010 - a method of one instance
            return app

        return make

    monkeypatch.setattr(openapi, "_app", renamed(None))
    with pytest.raises(ValueError, match="no operation id of its own"):
        openapi.document()
    monkeypatch.setattr(openapi, "_app", renamed("Not-Plain"))
    with pytest.raises(ValueError, match="no plain names"):
        openapi.document()
    monkeypatch.setattr(openapi, "_app", renamed("csrf"))
    duplicated = pytest.warns(UserWarning, match="Duplicate Operation ID csrf")  # FastAPI's own
    with duplicated, pytest.raises(ValueError, match="two operations have one id"):
        openapi.document()


def test_the_generator_s_own_checks_each_catch_a_case_of_their_own() -> None:
    flagged = openapi.unconstrained_numbers({"allOf": [{"type": "string", "enum": [1]}]})
    assert flagged == ["#/allOf/0"]
    assert openapi.unconstrained_numbers({"allOf": [{"not": {"const": 1}}]}) == []
    assert openapi.unconstrained_numbers({"type": "integer", "enum": [1]}) == []
    refs = {"$ref": f"{COMPONENTS}A"}
    shape: dict[str, Any] = {
        "components": {"schemas": {"A": {}, "B": {}, "C": {}}},
        "paths": {
            "/x": {
                "get": {
                    "parameters": [{"name": "p", "in": "query", "schema": refs}],
                    "requestBody": {
                        "content": {"application/json": {"schema": {"$ref": f"{COMPONENTS}C"}}}
                    },
                    "responses": {
                        "200": {
                            "content": {"application/json": {"schema": {"$ref": f"{COMPONENTS}B"}}}
                        }
                    },
                }
            }
        },
    }
    assert openapi.directions(shape) == ({"A", "C"}, {"B"})


def test_a_json_value_component_is_marked_with_the_direction_its_operations_give_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The mark's direction is computed from what the operations reach, not from the name: the
    markers of a JSON value without ``null`` on ``Refusal`` (a response's) and of one with
    ``null`` on ``Accept`` (a request's root)."""
    given = openapi._builders
    without = export._json_value("DocumentJson")[export.JSON_VALUE_MARK]
    with_null = export._json_value("RequestJson")[export.JSON_VALUE_MARK]

    def marking(models: Any) -> dict[str, Any]:
        found = cast(dict[str, Any], given(models))
        for schema in found.values():
            for name, marker in (("Refusal", without), ("Accept", with_null)):
                if name in schema.get("$defs", {}):
                    body = schema["$defs"][name]
                    schema["$defs"][name] = {**body, export.JSON_VALUE_MARK: marker}
        return found

    monkeypatch.setattr(openapi, "_builders", marking)
    marked = cast(dict[str, Any], openapi.document()["components"])["schemas"]
    assert marked["Refusal"][JSON_MARK] == {"direction": "response", "null": False}
    assert marked["Accept"][JSON_MARK] == {"direction": "request", "null": True}


def test_r2_leaves_the_constraint_contexts_alone() -> None:
    """A union below ``not``, ``if``, ``then``, ``else``, ``contains`` or ``propertyNames`` only
    constrains: R2 types no member there, while it does anywhere else."""
    union: openapi_normalise.JsonObject = {"type": "string", "anyOf": [{"pattern": "a"}]}
    for keyword in sorted(CONSTRAINTS):
        schema: openapi_normalise.JsonObject = {keyword: union}
        assert openapi_normalise.normalised(schema) == schema, keyword
        deeper: openapi_normalise.JsonObject = {keyword: {"properties": {"a": union}}}
        assert openapi_normalise.normalised(deeper) == deeper, keyword
    typed = openapi_normalise.normalised({"properties": {"a": union}})
    assert typed == {
        "properties": {"a": {"type": "string", "anyOf": [{"type": "string", "pattern": "a"}]}}
    }


# --- The request side's numbers (the input of M5.1c-1's adapters) --------------------------------

ADAPTER_FED = {
    "SearchCatalog.offset",
    "DescribeDataset.columns_offset",
    "Accept.proposal",
    "path:proposal",
    "TakeOver.session",
    "DescribeColumn.release",
    "DescribeDataset.release",
    "ListAnalyses.release",
    "QueueRequest.release",
    "WithdrawRequest.release",
    "query:release",
}
"""A number a response gave, which the person's next request takes back: an offset (a page's
``next_offset``, ``columns_next``), a proposal's id, a release's label, the open session's id (a
take-over's)."""
TYPED_BY_THE_PERSON = {
    "SearchCatalog.limit",
    "SearchCatalog.min_rows",
    "Completeness.min_present",
    "DescribeDataset.columns_limit",
    "ExistsLeaf.min_count",
    "SomeAtLeast.some",
}
"""A number the person types (or the client's constant, for a page's size)."""
BOTH = {
    "Range.gt",
    "Range.gte",
    "Range.lt",
    "Range.lte",
    "ValueLeaf.value",
    "ValueLeaf.values[]",
    "CoveredLeaf.scope.*[]",
    "UnitKey.key[]",
    "DocumentJson",
    "ParameterValue",
    "RequestJson",
    "DescriptorJson",
}
"""Typed by the person, or taken back from a response: a bin's edge, a column's statistic, a
member's key or value, a document or a descriptor read back."""
NEVER_IN_THE_CLIENT = {"EraseRequest.key[]"}
"""The data subject's key, which no screen takes (D269)."""


def request_numbers(document: dict[str, Any]) -> set[str]:
    found: set[str] = set()

    def walk(node: Any, at: str, seen: frozenset[str]) -> None:
        if not isinstance(node, dict):
            return
        if numeric(node):
            found.add(at)
        for key, value in node.items():
            if key in CONDITIONS:
                continue
            if key == "$ref":
                name = value.removeprefix(COMPONENTS)
                if name not in seen:
                    walk(components(document)[name], name, seen | {name})
            elif key == "properties":
                for member, schema in value.items():
                    walk(schema, f"{at}.{member}", seen)
            elif key == "patternProperties":
                for schema in value.values():
                    walk(schema, f"{at}.*", seen)
            elif key in ("items", "prefixItems"):
                for schema in value if isinstance(value, list) else [value]:
                    walk(schema, f"{at}[]", seen)
            elif key == "additionalProperties":
                walk(value, f"{at}{{}}", seen)
            elif key in LISTS:
                for schema in value:
                    walk(schema, at, seen)

    for _, _, operation in operations(document):
        for given in operation.get("parameters", []):
            walk(given["schema"], f"{given['in']}:{given['name']}", frozenset())
        for media in operation.get("requestBody", {}).get("content", {}).values():
            walk(media["schema"], "body", frozenset())
    return found


def test_every_request_number_is_classified(document: dict[str, Any]) -> None:
    classes = [ADAPTER_FED, TYPED_BY_THE_PERSON, BOTH, NEVER_IN_THE_CLIENT]
    assert sum(len(found) for found in classes) == len(set().union(*classes)) == 30
    assert request_numbers(document) == set().union(*classes)

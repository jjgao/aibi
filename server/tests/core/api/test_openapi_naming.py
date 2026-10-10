"""The OpenAPI generator's naming layer (SPEC §12.4, D416): every name-keyed map written through
``export.put``, route roots refused when named like the builders' own names or ``Substituted*``,
two routes of one method and path (as FastAPI's inclusions give them) refused, ``x-aibi-json`` by
identity, the components ``components_of``'s alone, and no call, store or merge in ``schema.export``
or ``api.openapi`` but those a reviewed allowlist holds (``ALLOWED``).

A document-level property test runs ``components_of`` (the document's single code path for its
components) over random subsets of the real routes' models with random models swapped in, whose
roots and nested classes are named like the reserved names, ``Substituted*``, real components and
each other, with an oracle of its own (unique ``Literal`` marks, the declared defaults as JSON, and
the checked-in document's ``x-aibi-json`` components). A refusal counts as success there, so a
second test runs the trials that must be accepted (``safe``). The builders' side is
``schema/test_naming.py``.
"""

import ast
import enum
import json
import random
import sys
import types
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any, Literal

import pytest
from fastapi import APIRouter, Cookie, Depends, FastAPI, Header, Query, Response
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, create_model
from starlette.responses import PlainTextResponse
from starlette.routing import Match, Route

from aibi.core.api import errors, openapi, openapi_normalise
from aibi.core.schema import export
from aibi.core.schema.operator import Refusals

SCHEMA_DIR = Path(__file__).resolve().parents[4] / "schemas"
CHECKED: dict[str, Any] = json.loads((SCHEMA_DIR / "openapi.json").read_text(encoding="utf-8"))
JSON_MARK = openapi.JSON_MARK
MODULES = ("aibi_openapi_probe_one", "aibi_openapi_probe_two")


@pytest.fixture(autouse=True)
def probe_modules(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in MODULES:
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))


def swapped(
    monkeypatch: pytest.MonkeyPatch,
    response: dict[str, type[BaseModel]] | None = None,
    request: dict[str, type[BaseModel]] | None = None,
) -> None:
    """The routes at the paths given answer, or take, the models given."""
    responses, requests = openapi._response_model, openapi._request_model
    monkeypatch.setattr(
        openapi,
        "_response_model",
        lambda route: (response or {}).get(route.path) or responses(route),
    )
    monkeypatch.setattr(
        openapi,
        "_request_model",
        lambda route: (request or {}).get(route.path) or requests(route),
    )


# --- One code path ------------------------------------------------------------------------------


def test_the_document_s_components_are_components_of_the_routes_models() -> None:
    models = openapi.route_models(openapi._app())
    found: Any = openapi.document()
    assert found["components"]["schemas"] == openapi.components_of(models)
    sides = Counter(side for side, _ in models)
    assert sides["response"] == 32
    assert sides["request"] > 20


# --- Roots and routes ----------------------------------------------------------------------------


@pytest.mark.parametrize("name", [*sorted(export.RESERVED), "SubstitutedDocument", "SubstitutedX"])
@pytest.mark.parametrize("side", ["request", "response"])
def test_a_route_model_named_like_a_reserved_name_is_refused(
    monkeypatch: pytest.MonkeyPatch, name: str, side: str
) -> None:
    """``p_root_reserved.py``: a root is in no ``$defs``, so the builders' pre-check cannot see
    it; dropping the route that defines ``RequestJson`` once let a response root of that name be
    marked ``x-aibi-json``. Defence in depth (the marks are by identity): ``_named`` refuses."""
    root = create_model(name, __module__=MODULES[0], secret_shape=(int, ...))
    if side == "response":
        swapped(monkeypatch, response={"/api/health": root})
    else:
        swapped(monkeypatch, request={"/operator/log/prune": root})
    with pytest.raises(ValueError, match=f"a route model is named {name}, a name the builders"):
        openapi.document()
    with pytest.raises(ValueError, match=f"{MODULES[0]}.{name}"):
        openapi.components_of(((side, root),))  # type: ignore[arg-type]


def test_a_root_is_named_by_its_class_s_name_now() -> None:
    """``_named`` reads ``__name__`` (a class renamed after its creation is named so), where the
    builders' pre-check reads Pydantic's keys."""
    renamed = create_model("Plain", __module__=MODULES[0], shape=(int, ...))
    renamed.__name__ = "RequestJson"
    with pytest.raises(ValueError, match="a route model is named RequestJson"):
        openapi._named({}, renamed)
    plain = create_model("RequestJson", __module__=MODULES[0], shape=(int, ...))
    plain.__name__ = "Plainly"
    assert openapi._named({}, plain) == "Plainly"


def test_two_models_of_one_module_and_name_are_two_models() -> None:
    """``named_by_name_equality`` (mutant): ``create_model`` twice with one name and module gives
    two classes of one qualified name (as two classes of a reloaded module are); the document
    knows a class by the class itself, never by its name."""
    one = create_model("Twin", __module__=MODULES[0], a=(int, ...))
    two = create_model("Twin", __module__=MODULES[0], a=(str, ...))
    assert (one.__module__, one.__qualname__) == (two.__module__, two.__qualname__)
    classes: dict[str, type[BaseModel]] = {}
    assert openapi._named(classes, one) == "Twin"
    assert openapi._named(classes, one) == "Twin"
    with pytest.raises(ValueError, match=f"two models are named Twin: {MODULES[0]}.Twin and "):
        openapi._named(classes, two)
    with pytest.raises(ValueError, match="two models are named Twin"):
        openapi.components_of((("response", one), ("response", two)))


class Shadow(BaseModel):
    shadow: int


def test_two_routes_of_one_method_and_path_are_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """``p_routes.py``: the server runs the first, FastAPI's document and a last-wins map
    describe the second."""
    given = openapi._app

    def with_shadow() -> FastAPI:
        app = given()

        @app.get("/api/health", response_model=Shadow, operation_id="shadow")
        def shadow() -> Response:  # pragma: no cover - never called
            return Response()

        return app

    monkeypatch.setattr(openapi, "_app", with_shadow)
    with pytest.raises(ValueError, match=r"two routes serve \('get', '/api/health'\)"):
        openapi.document()


class Thing(BaseModel):
    who: str


def first(x_first: Annotated[str, Header()]) -> str:  # pragma: no cover - never called
    return "first"


def second(x_second: Annotated[str, Header()]) -> str:  # pragma: no cover - never called
    return "second"


def thing_router(operation_id: str = "thing_probe") -> APIRouter:
    router = APIRouter()

    @router.get("/thing", response_model=Thing, operation_id=operation_id)
    def thing() -> Thing:  # pragma: no cover - never called
        return Thing(who="handler")

    return router


def with_inclusions(
    monkeypatch: pytest.MonkeyPatch, *inclusions: dict[str, Any], router: APIRouter | None = None
) -> None:
    """``openapi._app`` gains ``router`` (one router object) included once for each of
    ``inclusions`` (the arguments of ``include_router``)."""
    given = openapi._app
    shared = router or thing_router()

    def app() -> FastAPI:
        found = given()
        for each in inclusions:
            found.include_router(shared, **each)
        return found

    monkeypatch.setattr(openapi, "_app", app)


@pytest.mark.parametrize(
    "second_inclusion",
    [
        {"prefix": "/p", "dependencies": [Depends(second)]},
        {"prefix": "/p", "dependencies": [Depends(first)]},
        {"prefix": "/p"},
    ],
    ids=["other dependencies", "the same dependencies", "none"],
)
def test_one_router_included_twice_at_one_path_is_refused_whatever_it_holds(
    monkeypatch: pytest.MonkeyPatch, second_inclusion: dict[str, Any]
) -> None:
    """``p_routes.py`` (review round 1): FastAPI documents the last inclusion's dependencies (a
    header) and the server runs the first; the one router object, its original route, is the
    same in both contexts, so the contexts, not the routes, are what differ."""
    with_inclusions(
        monkeypatch, {"prefix": "/p", "dependencies": [Depends(first)]}, second_inclusion
    )
    with pytest.raises(ValueError, match=r"two routes serve \('get', '/p/thing'\)"):
        openapi._routes(openapi._app())
    refused = pytest.raises(ValueError, match=r"two routes serve \('get', '/p/thing'\)")
    with pytest.warns(UserWarning, match="Duplicate Operation ID thing_probe"), refused:
        openapi.document()


def test_an_inclusion_left_out_of_fastapi_s_document_is_left_out_of_this_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``p_ctx2.py``: ``include_router(include_in_schema=False)`` leaves the route out of FastAPI's
    document; its model must not become a component either (the original route says it is in)."""
    hidden = create_model("Hidden", __module__=MODULES[0], secret=(int, ...))
    router = APIRouter()

    @router.get("/hidden", response_model=hidden, operation_id="hidden_probe")
    def hidden_route() -> None:  # pragma: no cover - never called
        return None

    with_inclusions(monkeypatch, {"prefix": "/h", "include_in_schema": False}, router=router)
    app = openapi._app()
    assert ("get", "/h/hidden") not in openapi._routes(app)
    assert hidden not in [model for _, model in openapi.route_models(app)]
    found: Any = openapi.document()
    assert "Hidden" not in found["components"]["schemas"]
    assert "/h/hidden" not in found["paths"]
    # The same router included a second time, in the document, is no second route of the key.
    with_inclusions(
        monkeypatch,
        {"prefix": "/h", "include_in_schema": False},
        {"prefix": "/h"},
        router=router,
    )
    assert ("get", "/h/hidden") in openapi._routes(openapi._app())


class ProbeBody(BaseModel):
    what: int


def test_a_prefix_at_inclusion_is_the_path_a_request_body_is_read_by(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``request_model_original_path`` (mutant): a tool's body model is found by the route's
    effective path, ``/api/tools/<name>``, which a router's own path (``/<name>``) is not."""
    monkeypatch.setitem(openapi.TOOL_MODELS, "probe_tool", (ProbeBody, Thing))
    router = APIRouter()

    @router.post("/probe_tool", response_model=Thing, operation_id="probe_tool")
    def probe_tool(body: ProbeBody) -> Thing:  # pragma: no cover - never called
        return Thing(who=str(body.what))

    given = openapi._app

    def with_tool() -> FastAPI:
        app = given()
        app.include_router(router, prefix="/api/tools")
        return app

    monkeypatch.setattr(openapi, "_app", with_tool)
    [context] = [c for k, c in openapi._routes(with_tool()).items() if k[1].endswith("probe_tool")]
    assert context.path == "/api/tools/probe_tool"
    assert openapi._request_model(context) is ProbeBody
    found: Any = openapi.document()
    body = found["paths"]["/api/tools/probe_tool"]["post"]["requestBody"]
    assert body["content"]["application/json"]["schema"] == {
        "$ref": openapi.COMPONENTS + "ProbeBody"
    }
    assert "ProbeBody" in found["components"]["schemas"]


def test_a_route_with_a_json_body_that_is_neither_a_tool_s_nor_an_operator_body_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The generator would document the route without its ``requestBody``; it refuses (the real
    application's are each a tool's or an ``OperatorBody``'s)."""
    given = openapi._app

    def with_body() -> FastAPI:
        app = given()

        @app.post("/api/probe", operation_id="probe", response_model=Refusals)
        def probe(body: ProbeBody) -> None:  # pragma: no cover - never called
            return None

        return app

    monkeypatch.setattr(openapi, "_app", with_body)
    with pytest.raises(ValueError, match=r"POST /api/probe takes a body that is neither a tool's"):
        openapi.document()


def test_a_prefix_at_inclusion_is_the_path_the_document_gives(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``p_routes.py``: routes were keyed by the original route's path, so a prefix given at
    inclusion made ``document()`` raise a ``KeyError`` for the path FastAPI wrote."""
    with_inclusions(monkeypatch, {"prefix": "/p"})
    app = openapi._app()
    assert ("get", "/p/thing") in openapi._routes(app)
    found: Any = openapi.document()
    assert found["paths"]["/p/thing"]["get"]["operationId"] == "thing_probe"
    assert "Thing" in found["components"]["schemas"]


def fastapi_route(scope: dict[str, Any], routes: list[Any]) -> Any:
    """The route Starlette and FastAPI run for ``scope``: the first that matches fully, looked
    for inside an included router as FastAPI does. ``_match`` is FastAPI's private
    ``_IncludedRouter`` method (0.141: ``uv.lock`` pins 0.141.1 and ``pyproject`` allows
    ``>=0.141``, so a newer release can change it). Expect a loud failure, never a pass: a
    release that renames or removes it makes ``getattr`` give ``None`` and ``route.matches`` of
    the included router (which calls ``_match`` itself) raise ``AttributeError: '_IncludedRouter'
    object has no attribute '_match'`` in every shadow test, and
    ``test_the_included_router_is_looked_into`` fail by name; one that changes what it returns
    fails at the unpacking below (``ValueError`` or ``TypeError``). Rewrite this walk then, never
    skip it."""
    for route in routes:
        inside = getattr(route, "_match", None)
        if inside is not None:
            match, _, found, _ = inside(scope)
            if match == Match.FULL:
                return fastapi_route(scope, [found]) if hasattr(found, "_match") else found
        elif route.matches(scope)[0] == Match.FULL:
            return route
    return None


def shadowed(app: FastAPI, document: dict[str, Any]) -> tuple[int, list[str]]:
    """For every documented operation and concrete path of its template (each parameter a
    plain value, then each literal segment of the document's), the route the server runs is the
    operation's own; the number of requests checked, and those it is not."""
    literals = sorted(
        {part for path in document["paths"] for part in path.split("/") if part and "{" not in part}
    )
    problems: list[str] = []
    checked = 0
    for path, methods in document["paths"].items():
        names = [part[1:-1] for part in path.split("/") if part.startswith("{")]
        choices = [dict.fromkeys(names, "ds_1")]
        choices += [
            {**dict.fromkeys(names, "ds_1"), name: lit} for lit in literals for name in names
        ]
        for method, operation in methods.items():
            for values in choices:
                concrete = path
                for name, value in values.items():
                    concrete = concrete.replace("{" + name + "}", value)
                scope = {
                    "type": "http",
                    "method": method.upper(),
                    "path": concrete,
                    "root_path": "",
                }
                served = fastapi_route(scope, list(app.router.routes))
                checked += 1
                if getattr(served, "operation_id", None) != operation["operationId"]:
                    problems.append(f"{method} {concrete}: {served!r}")
    return checked, problems


def test_no_route_shadows_a_documented_operation() -> None:
    """Starlette's own matching over the application's routes (pages, the MCP mount and the
    routers in the order the server serves them), every request of every documented operation."""
    checked, problems = shadowed(openapi._app(), CHECKED)
    assert problems == []
    assert checked > 600


def test_no_bundle_route_shadows_a_documented_operation(
    bundle_dir: Path, make_bundled: Callable[..., Any]
) -> None:
    """The bundle's router comes before the operator router (``app.create_app``)."""
    bundled = make_bundled(bundle_dir)
    checked, problems = shadowed(bundled.app, CHECKED)
    assert problems == []
    assert checked > 600


def test_the_shadow_check_sees_a_plain_route_ahead_of_a_router() -> None:
    """``p_shadow_plain.py``: a plain Starlette route (as ``/mcp`` is) ahead of the operator
    router would serve ``GET /operator/datasets``; the check reports it."""
    app = openapi._app()
    app.router.routes.insert(
        0,
        Route("/operator/datasets", lambda _: PlainTextResponse("shadow"), methods=["GET"]),
    )
    _, problems = shadowed(app, CHECKED)
    assert problems
    assert all(problem.startswith("get /operator/datasets:") for problem in problems)


# --- Definitions -----------------------------------------------------------------------------


def opt(value: object, module: str) -> type[BaseModel]:
    return create_model("Opt", __module__=module, x=(int | bool, value))


def test_two_routes_classes_of_one_name_differing_as_json_are_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``p_same_body.py``: two classes named ``Opt`` whose defaults are ``1`` and ``true`` (equal
    with ``==``) in two routes' builders; one route would be described by the other's."""
    one = create_model("ProbeOne", __module__=MODULES[0], opt=(opt(1, MODULES[0]), ...))
    two = create_model("ProbeTwo", __module__=MODULES[0], opt=(opt(True, MODULES[1]), ...))
    swapped(monkeypatch, response={"/operator/datasets": one, "/api/health": two})
    with pytest.raises(ValueError, match="two builders define Opt differently"):
        openapi.document()


def test_two_classes_pydantic_would_merge_in_one_builder_are_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``p_pydantic_collapse.py``, through the document."""
    both = create_model(
        "ProbeBoth",
        __module__=MODULES[0],
        one=(opt(1, MODULES[0]), ...),
        two=(opt(True, MODULES[1]), ...),
    )
    swapped(monkeypatch, response={"/api/health": both})
    with pytest.raises(ValueError, match="two classes Pydantic names Opt"):
        openapi.document()


def test_a_definition_named_substituted_is_refused() -> None:
    built = export.output_schema(
        create_model(
            "Probe",
            __module__=MODULES[0],
            inner=(create_model("SubstitutedCohort", __module__=MODULES[0], a=(int, ...)), ...),
        ),
        "Probe",
    )
    with pytest.raises(ValueError, match="a definition is named SubstitutedCohort"):
        openapi.hoisted("Probe", built)
    prefixed: Any = openapi.hoisted("Probe", built, "Substitutedx")
    assert prefixed["SubstitutedxProbe"]["properties"]["inner"]["$ref"].endswith(
        "SubstitutedCohort"
    )


def test_substituted_components_go_through_the_primitive(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolation (``_named``'s refusal patched out, ``components_of`` unchanged): a route root
    named ``SubstitutedDocument`` meets the document after substitution's root in
    ``components_of``, which refuses rather than letting one overwrite the other."""
    given = openapi._named

    def plain(classes: dict[str, type[BaseModel]], model: type[BaseModel]) -> str:
        return given(classes, model) if model.__name__ != "SubstitutedDocument" else model.__name__

    monkeypatch.setattr(openapi, "_named", plain)
    root = create_model("SubstitutedDocument", __module__=MODULES[0], other=(int, ...))
    with pytest.raises(ValueError, match="two builders define SubstitutedDocument differently"):
        openapi.components_of((("response", root),))


def test_an_unknown_reference_is_refused_naming_its_cause() -> None:
    """``reached`` and ``_referenced``: a reference to nothing is a refusal, never a
    ``KeyError``."""
    with pytest.raises(ValueError, match="a reference to Nowhere, which no component is"):
        openapi.reached({}, [{"$ref": openapi.COMPONENTS + "Nowhere"}])
    with pytest.raises(ValueError, match="refers to no definition of its schema"):
        openapi.hoisted("Root", {"properties": {"a": {"$ref": "#/$defs/Nowhere"}}})


@pytest.mark.parametrize(
    ("side", "path", "name"),
    [
        ("request", "/operator/log/prune", "RequestJson"),
        ("request", "/operator/log/prune", "DescriptorJson"),
        ("response", "/operator/datasets", "OutputJson"),
        ("response", "/api/health", "OutputJson"),
    ],
)
@pytest.mark.parametrize("kind", ["model", "enum"])
def test_the_round_3_probe_s_injected_names_are_refused_by_the_document(
    monkeypatch: pytest.MonkeyPatch, side: str, path: str, name: str, kind: str
) -> None:
    """``p_injected.py`` and ``p_differential.py``'s accepted-and-wrong cases, through the
    document: a route's model whose member is a class named like the JSON value the builder
    defines beside it (``nested``), which the builder's definition used to replace."""
    if kind == "enum":
        nested: Any = enum.Enum(name, {"a": "a", "b": "b"}, module=MODULES[1])
    else:
        nested = create_model(name, __module__=MODULES[1], secret_shape=(int, ...))
    from aibi.core.schema.descriptors import AnyJson
    from aibi.core.schema.output import FiniteJsonObject

    value = AnyJson if name == "DescriptorJson" else FiniteJsonObject
    probe = create_model(
        "ProbeModel", __module__=MODULES[0], values=(value, ...), nested=(nested, ...)
    )
    swapped(monkeypatch, **{side: {path: probe}})
    with pytest.raises(ValueError, match=f"Pydantic names {name}, a name the builders reserve"):
        openapi.document()


def test_the_tool_schemas_are_held_to_the_vocabulary(monkeypatch: pytest.MonkeyPatch) -> None:
    """n16: the generator lints the tool schemas it is given, as well as the document."""
    probe = {"$schema": export.SCHEMA_DIALECT, "type": "object", "unevaluatedProperties": False}
    monkeypatch.setitem(export.SCHEMAS, "tool.probe.request.schema.json", lambda: probe)
    refused = r"tool\.probe\.request\.schema\.json: unevaluatedProperties at file"
    with pytest.raises(ValueError, match=refused):
        openapi.document()


# --- Marks by identity ---------------------------------------------------------------------------


def test_a_forged_marker_is_refused_by_the_document(monkeypatch: pytest.MonkeyPatch) -> None:
    """``p_marker_forge.py``."""
    forged = create_model(
        "HealthLike",
        __module__=MODULES[0],
        __config__=ConfigDict(json_schema_extra={export.JSON_VALUE_MARK: {"null": True}}),
        status=(str, ...),
    )
    swapped(monkeypatch, response={"/api/health": forged})
    with pytest.raises(ValueError, match="did not give"):
        openapi.document()


def test_a_member_named_like_the_marker_is_no_json_value(monkeypatch: pytest.MonkeyPatch) -> None:
    from pydantic import Field

    named = create_model(
        "Named", __module__=MODULES[0], member=(int, Field(alias=export.JSON_VALUE_MARK))
    )
    swapped(monkeypatch, response={"/api/health": named})
    models = openapi.route_models(openapi._app())
    found: Any = openapi.components_of(models)
    assert JSON_MARK not in found["Named"]
    assert export.JSON_VALUE_MARK in found["Named"]["properties"]


# --- Operations, statuses, headers and parameters ------------------------------------------------


def test_every_refusal_status_is_an_error_status(monkeypatch: pytest.MonkeyPatch) -> None:
    """n15: a refusal answered with 200 would replace the answer's declaration."""
    statuses = errors.refusal_statuses()
    monkeypatch.setattr(errors, "refusal_statuses", lambda: statuses | {200})
    with pytest.raises(ValueError, match="answered with 200, which is no error status"):
        openapi.document()
    monkeypatch.setattr(errors, "refusal_statuses", lambda: statuses | {399})
    with pytest.raises(ValueError, match="answered with 399"):
        openapi.document()


def test_two_header_parameters_of_one_name_are_refused_whatever_the_case(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A header's name is read without regard to case (HTTP): ``Aibi-Operator`` twice is one."""
    given = openapi._operator_parameters

    def doubled(method: str, path: str) -> list[dict[str, Any]]:
        found = given(method, path)
        return [*found, {**found[0], "name": "aibi-operator", "required": False}]

    monkeypatch.setattr(openapi, "_operator_parameters", doubled)
    with pytest.raises(ValueError, match=r"two parameters are \('aibi-operator', 'header'\)"):
        openapi.document()


def test_two_query_parameters_whose_names_differ_by_case_are_two_parameters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only a header's name is case-insensitive: ``Offset`` and ``offset`` of a query are two
    parameters (a query string is read exactly), both documented, and ``a`` as a query and as a
    header (another place) are two as well."""
    given = openapi._app

    def with_parameters() -> FastAPI:
        app = given()

        @app.get("/api/probe", operation_id="probe", response_model=Refusals)
        def probe(  # pragma: no cover - never called
            Offset: Annotated[int, Query()],  # noqa: N803
            offset: Annotated[int, Query()],
            Probe: Annotated[str, Header()],  # noqa: N803
            probe: Annotated[str, Query()],
        ) -> None:
            return None

        return app

    monkeypatch.setattr(openapi, "_app", with_parameters)
    found: Any = openapi.document()
    parameters = found["paths"]["/api/probe"]["get"]["parameters"]
    assert sorted((p["in"], p["name"]) for p in parameters) == [
        ("header", "Probe"),
        ("query", "Offset"),
        ("query", "offset"),
        ("query", "probe"),
    ]


def test_two_operations_with_one_id_are_refused_naming_it() -> None:
    found: dict[str, Any] = {}
    export.put(found, "a", "get /a", "two operations have one id: {key}")
    with pytest.raises(ValueError, match=r"two operations have one id: a$"):
        export.put(found, "a", "get /b", "two operations have one id: {key}")


def test_one_route_of_two_methods_and_one_operation_id_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``identifiers_value_path_only`` (mutant): the id's value is the method and the path, so two
    operations of one path, with one id, are two."""
    given = openapi._app

    def with_two_methods() -> FastAPI:
        app = given()

        @app.api_route(
            "/api/probe", methods=["GET", "POST"], operation_id="probe", response_model=Refusals
        )
        def probe() -> None:  # pragma: no cover - never called
            return None

        return app

    monkeypatch.setattr(openapi, "_app", with_two_methods)
    refused = pytest.raises(ValueError, match=r"two operations have one id: probe$")
    with pytest.warns(UserWarning, match="Duplicate Operation ID probe"), refused:
        openapi.document()


def test_a_header_is_keyed_without_regard_to_case_by_one_helper() -> None:
    assert openapi._header_key("Retry-After") == "retry-after"
    assert openapi._header_key("retry-after") == "retry-after"
    assert set(openapi._HEADER_TEXT) == {"www-authenticate", "allow", "retry-after"}


def test_two_response_headers_of_one_name_are_refused_whatever_the_case(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Round 2, minor 3: a response header's name is read without regard to case as a header
    parameter's is, so ``Retry-After`` and ``retry-after`` of one status are one header, declared
    twice. The same name twice is the table's own repetition and one declaration."""
    monkeypatch.setattr(
        errors, "RESPONSE_HEADERS", {**errors.RESPONSE_HEADERS, 429: ("Retry-After", "retry-after")}
    )
    with pytest.raises(ValueError, match=r"the header retry-after twice, as another name"):
        openapi.document()
    monkeypatch.setattr(errors, "RESPONSE_HEADERS", {429: ("Retry-After", "Retry-After")})
    found: Any = openapi.document()
    declared = found["paths"]["/api/health"]["get"]["responses"]["429"]["headers"]
    assert list(declared) == ["Retry-After"]


def test_a_response_header_the_table_names_in_other_case_is_described_and_typed_as_its_own(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The text and the type of a header are looked up by ``_header_key``: ``retry-after`` has the
    digits ``Retry-After`` has, and the name is the table's own, as written."""
    table = {**errors.RESPONSE_HEADERS, 429: ("retry-after",), 401: ("WWW-AUTHENTICATE",)}
    monkeypatch.setattr(errors, "RESPONSE_HEADERS", table)
    found: Any = openapi.document()
    responses = found["paths"]["/api/health"]["get"]["responses"]
    assert responses["429"]["headers"] == {
        "retry-after": {"description": "Seconds to wait", "schema": openapi_normalise.RETRY_AFTER}
    }
    assert responses["401"]["headers"] == {
        "WWW-AUTHENTICATE": {
            "description": "The curator token's scheme",
            "schema": openapi_normalise.HEADER_TEXT,
        }
    }


def test_cookie_parameters_are_named_exactly_as_query_parameters_are(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``params_cookie_folded`` (mutant): a cookie's name is case-sensitive (RFC 6265), so
    ``Session`` and ``session`` are two cookies, as they are two of a query; a header of the same
    name (``Session``, ``session``) is another place."""
    given = openapi._app

    def with_cookies() -> FastAPI:
        app = given()

        @app.get("/api/probe", operation_id="probe", response_model=Refusals)
        def probe(  # pragma: no cover - never called
            Session: Annotated[str, Cookie()],  # noqa: N803
            session: Annotated[str, Cookie()],
            Probe: Annotated[str, Header()],  # noqa: N803
        ) -> None:
            return None

        return app

    monkeypatch.setattr(openapi, "_app", with_cookies)
    found: Any = openapi.document()
    parameters = found["paths"]["/api/probe"]["get"]["parameters"]
    assert sorted((p["in"], p["name"]) for p in parameters) == [
        ("cookie", "Session"),
        ("cookie", "session"),
        ("header", "Probe"),
    ]


class Level(enum.StrEnum):
    a = "a"
    b = "b"


@pytest.mark.parametrize("name", ["Level", "ParameterReference", "Severity"])
def test_a_parameter_never_references_a_component(
    monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    """``p_param_ref.py``: FastAPI gives an enum parameter a ``$ref`` to its own component, which
    would resolve by name to a builder's (``Severity``, ``ParameterReference``) or to nothing;
    the vocabulary refuses a ``$ref`` at a parameter, and D416 says parameters never reference
    components."""
    kind = enum.Enum(name, {"a": "a", "b": "b"}, type=str, module=MODULES[0])  # type: ignore[misc]
    given = openapi._app

    def with_parameter() -> FastAPI:
        app = given()

        @app.get("/api/probe", operation_id="probe", response_model=Refusals)
        def probe(level: Annotated[kind, Query()] = kind.a) -> None:  # type: ignore[valid-type]  # pragma: no cover
            return None

        return app

    monkeypatch.setattr(openapi, "_app", with_parameter)
    with pytest.raises(ValueError, match=r"openapi.json: \$ref at parameter"):
        openapi.document()


def test_the_untyped_check_covers_parameters(monkeypatch: pytest.MonkeyPatch) -> None:
    """n14: a parameter's typeless union member R2 could not type refuses the document."""
    monkeypatch.setattr(openapi, "parameter", lambda schema: {"anyOf": [{"pattern": "a"}]})
    with pytest.raises(ValueError, match=r"parameter [^:]+: at #/anyOf/0"):
        openapi.document()


def test_r4_is_then_r1_and_r2() -> None:
    """n14: R4 drops ``null`` and ``title``, then R1 and R2 apply."""
    typed = openapi_normalise.parameter(
        {"title": "T", "anyOf": [{"type": "string", "anyOf": [{"pattern": "a"}]}, {"type": "null"}]}
    )
    assert typed == {"type": "string", "anyOf": [{"type": "string", "pattern": "a"}]}
    moved = openapi_normalise.parameter(
        {"type": "object", "properties": {"a": {}}, "oneOf": [{"required": ["a"]}], "title": "T"}
    )
    assert moved == {
        "type": "object",
        "properties": {"a": {}},
        "allOf": [{"oneOf": [{"required": ["a"]}]}],
    }


# OAS 3.1, §4.8.7.1 (Components Object): every key matches this.
OAS_COMPONENT_KEY = r"^[a-zA-Z0-9\.\-_]+$"


@pytest.mark.parametrize(
    ("key", "allowed"),
    [
        ("Refusal", True),
        ("Page.v1", True),
        ("a-b_c", True),
        ("9", True),
        ("Page[str]", False),
        ("a b", False),
        ("", False),
        ("a/b", False),
    ],
)
def test_the_component_key_is_oas_3_1_s_pattern(key: str, allowed: bool) -> None:
    import re

    assert bool(re.match(OAS_COMPONENT_KEY, key)) is allowed
    assert bool(openapi._COMPONENT_KEY.fullmatch(key)) is allowed


@pytest.mark.parametrize(
    ("given", "plain"),
    [
        ("search_catalog", True),
        ("a", True),
        ("searchCatalog", False),
        ("Search", False),
        ("a_", False),
        ("_a", False),
        ("a-b", False),
        ("1a", False),
    ],
)
def test_an_operation_id_is_a_plain_lower_case_name(given: str, plain: bool) -> None:
    assert bool(openapi._OPERATION_ID.fullmatch(given)) is plain


# --- The document-level property ----------------------------------------------------------------

JSON_COMPONENTS = {
    name for name, body in CHECKED["components"]["schemas"].items() if JSON_MARK in body
}
ROOTS = [
    *sorted(export.RESERVED),
    "SubstitutedDocument",
    "SubstitutedX",
    "Health",
    "Csrf",
    "Datasets",
    "Refusals",
    "P1",
    "P2",
    "P3",
]
NESTED = ["Opt", "Opt", "Inner", "Cohort", "Range", *sorted(export.RESERVED)]
DOCUMENT_TRIALS = 600


class Maker:
    """``safe`` makes the trials that must be accepted: unique names for every class but the
    twins, whose schemas are one (``Opt`` as ``1`` in both modules; ``Outer(i: Inner)``, ``Inner.x``
    ``1``, nested twins Pydantic merges), none of the real components' or reserved names."""

    def __init__(self, rng: random.Random, safe: bool = False) -> None:
        self.rng = rng
        self.safe = safe
        self.count = 0

    def uid(self) -> str:
        self.count += 1
        return f"u{self.count}"

    def make(self, depth: int, name: str) -> tuple[type[BaseModel], str | None, dict[str, Any]]:
        rng = self.rng
        if name == "Opt":
            value = 1 if self.safe else rng.choice([1, True, 0, False])
            cls = create_model("Opt", __module__=rng.choice(MODULES), x=(int | bool, value))
            return cls, None, {"x": ("default", value)}
        mark = self.uid()
        fields: dict[str, Any] = {"mark_": (Literal[mark], ...)}
        spec: dict[str, Any] = {}
        if rng.random() < (0.5 if self.safe else 0.2) and depth < 2:
            one = 1 if self.safe else rng.choice([1, True])
            two = one if self.safe or rng.random() < 0.5 else rng.choice([2, False])
            for tag, value, where in (("n0", one, MODULES[0]), ("n1", two, MODULES[1])):
                inner = create_model("Inner", __module__=where, x=(int | bool, value))
                fields[tag] = (create_model("Outer", __module__=where, i=(inner, ...)), ...)
                spec[tag] = ("nested", value)
        for index in range(rng.randint(1, 3)):
            field = f"f{index}"
            if rng.random() < 0.4 and depth < 2:
                roll = rng.random()
                child_name = (
                    "Opt"
                    if roll < 0.45
                    else rng.choice(NESTED)
                    if roll < 0.6 and not self.safe
                    else "N" + self.uid()
                )
                child = self.make(depth + 1, child_name)
                fields[field] = (child[0], ...)
                spec[field] = child
            else:
                value = rng.choice([1, True, 0, False])
                fields[field] = (int | bool, value)
                spec[field] = ("default", value)
        return create_model(name, __module__=rng.choice(MODULES), **fields), mark, spec

    def root_name(self) -> str:
        if self.safe:
            return "P" + self.uid()
        return self.rng.choice(ROOTS) if self.rng.random() < 0.35 else "P" + self.uid()


def check(
    made: tuple[type[BaseModel], str | None, dict[str, Any]],
    node: Any,
    components: dict[str, Any],
    at: str,
    wrong: list[str],
    mine: set[str],
) -> None:
    cls, mark, spec = made
    while isinstance(node, dict) and "$ref" in node and set(node) <= {"$ref", "title"}:
        name = node["$ref"].removeprefix(openapi.COMPONENTS)
        mine.add(name)
        node = components[name]
    properties = node.get("properties", {})
    if mark is not None and properties.get("mark_", {}).get("const") != mark:
        wrong.append(f"{at}: {cls.__name__} -> {json.dumps(node)[:100]}")
        return
    for field, expected in spec.items():
        member = properties.get(field, {})
        if expected[0] == "default":
            if json.dumps(member.get("default")) != json.dumps(expected[1]):
                wrong.append(f"{at}.{field}: default {member.get('default')!r} for {expected[1]!r}")
        elif expected[0] == "nested":
            outer = member
            while isinstance(outer, dict) and "$ref" in outer:
                name = outer["$ref"].removeprefix(openapi.COMPONENTS)
                mine.add(name)
                outer = components[name]
            inner = outer.get("properties", {}).get("i", {})
            while isinstance(inner, dict) and "$ref" in inner:
                name = inner["$ref"].removeprefix(openapi.COMPONENTS)
                mine.add(name)
                inner = components[name]
            got = inner.get("properties", {}).get("x", {}).get("default", "MISSING")
            if json.dumps(got) != json.dumps(expected[1]):
                wrong.append(f"{at}.{field}: nested default {got!r} for {expected[1]!r}")
        else:
            check(expected, member, components, f"{at}.{field}", wrong, mine)


def document_trial(maker: Maker, real: openapi.Models) -> tuple[str, str]:
    rng = maker.rng
    responses = [index for index, (side, _) in enumerate(real) if side == "response"]
    requests = [index for index, (side, _) in enumerate(real) if side == "request"]
    swaps: dict[int, tuple[type[BaseModel], str | None, dict[str, Any]]] = {}
    for index in rng.sample(responses, rng.randint(1, 2)):
        swaps[index] = maker.make(0, maker.root_name())
    if (
        not maker.safe and rng.random() < 0.4
    ):  # a twin shared by a request and a response is refused
        swaps[rng.choice(requests)] = maker.make(0, maker.root_name())
    others = [index for index in range(len(real)) if index not in swaps]
    dropped = set(rng.sample(others, rng.randint(0, 8))) if rng.random() < 0.7 else set()
    models: Any = tuple(
        (side, swaps[index][0] if index in swaps else model)
        for index, (side, model) in enumerate(real)
        if index not in dropped
    )
    try:
        components = openapi.components_of(models)
    except ValueError as error:
        return ("OVERREFUSED" if maker.safe else "refused"), str(error).splitlines()[0][:50]
    wrong: list[str] = []
    mine: set[str] = set()
    for index, made in swaps.items():
        check(
            made,
            {"$ref": openapi.COMPONENTS + made[0].__name__},
            components,
            str(index),
            wrong,
            mine,
        )
    for name in mine:
        if JSON_MARK in components.get(name, {}):
            wrong.append(f"{name}: a synthesised component marked x-aibi-json")
    for name in JSON_COMPONENTS & set(components) - mine:
        if JSON_MARK not in components[name]:
            wrong.append(f"{name}: lost its x-aibi-json")
    for name in set(components) - JSON_COMPONENTS - mine:
        if JSON_MARK in components[name]:
            wrong.append(f"{name}: marked x-aibi-json, checked in unmarked")
    return ("WRONG", " | ".join(wrong)[:240]) if wrong else ("ok", "")


@pytest.fixture
def cached_builders(monkeypatch: pytest.MonkeyPatch) -> openapi.Models:
    """The real routes' models, their builders' schemas computed once (the builders are pure;
    ``components_of`` reads what they give and never changes it)."""
    real = openapi.route_models(openapi._app())
    known = {model for _, model in real} | {Refusals}
    memo: dict[tuple[object, ...], Any] = {}

    def cached(build: Callable[..., Any]) -> Callable[..., Any]:
        def built(*given: Any) -> Any:
            if given and given[0] not in known:
                return build(*given)
            key = (build, *given)
            if key not in memo:
                memo[key] = build(*given)
            return memo[key]

        return built

    for name in ("request_schema_marked", "output_schema_marked", "document_schema_marked"):
        monkeypatch.setattr(export, name, cached(getattr(export, name)))
    return real


def test_every_route_is_described_by_its_own_classes_or_the_document_is_refused(
    cached_builders: openapi.Models,
) -> None:
    """600 seeded trials: one or two responses and maybe a request swapped for random models,
    up to eight other routes dropped (a reserved root is otherwise refused only because another
    route defines the name). No accepted document describes a route by another class, a field by
    another default, or marks ``x-aibi-json`` other than the JSON values. A refusal counts as
    success here, so this test cannot see a document that refuses too much:
    ``test_a_document_of_routes_that_names_cannot_confuse_is_accepted`` does."""
    maker = Maker(random.Random(7))
    tally: Counter[str] = Counter()
    examples: list[str] = []
    for _ in range(DOCUMENT_TRIALS):
        outcome, why = document_trial(maker, cached_builders)
        tally[outcome] += 1
        if outcome == "WRONG" and len(examples) < 3:
            examples.append(why)
    assert tally["WRONG"] == 0, examples
    assert tally["ok"] > 150, dict(tally)
    assert tally["refused"] > 150, dict(tally)


SAFE_TRIALS = 200


def test_a_document_of_routes_that_names_cannot_confuse_is_accepted(
    cached_builders: openapi.Models,
) -> None:
    """The expected-accepted subset: swapped roots with unique names, nested classes that are
    twins whose schemas are one (``Opt``, and ``Outer(i: Inner)`` in each of two modules, which
    Pydantic merges and ``Strict`` must too), in responses alone (one shared with a request would
    be both's), routes dropped as above: no trial may be refused,
    and each is described by its own classes. Over-refusal fails here."""
    maker = Maker(random.Random(11), safe=True)
    tally: Counter[str] = Counter()
    examples: list[str] = []
    for _ in range(SAFE_TRIALS):
        outcome, why = document_trial(maker, cached_builders)
        tally[outcome] += 1
        if outcome != "ok" and len(examples) < 3:
            examples.append(why)
    assert tally["ok"] == SAFE_TRIALS, (dict(tally), examples)


# --- The writes into a map ------------------------------------------------------------------------

LINTED = {
    "export": Path(export.__file__),
    "openapi": Path(openapi.__file__),
}

SAFE_FUNCTIONS = frozenset(
    {
        # What reads, tests, orders or converts, and makes no map from two (``dict`` is not here).
        *("len", "isinstance", "issubclass", "str", "bool", "int", "tuple", "list", "set"),
        *("frozenset", "sorted", "any", "all", "enumerate", "zip", "range", "min", "max"),
        *("repr", "bytes", "object", "super", "cast", "type", "id", "MarkedSchema"),
        "put",  # the primitive itself: a call of it is the sanctioned write
    }
)
"""Bare names a call may use without being listed. An exception class (``*Error``) and a function
or class the linted module defines itself (its body is linted) are safe by rule as well."""
SAFE_METHODS = frozenset(
    {
        # Read-only views and lookups, str and list and set methods: none writes a map by a key.
        *("get", "items", "keys", "values", "startswith", "endswith", "removeprefix"),
        *("removesuffix", "fullmatch", "match", "join", "lower", "upper", "format", "split"),
        *("append", "extend", "add", "discard", "replace", "mkdir", "write_text"),
    }
)
"""Methods a call may use whatever its receiver (``update``, ``setdefault``, ``pop``, ``copy``,
``__setitem__``, ``__ior__``, ``fromkeys`` and every other are not here). A receiver that is a
multimap (``append``, ``add``) or a dataclass (``replace``) is not seen: the lint is a tripwire."""
WRITING_ATTRIBUTES = frozenset(
    {
        *("update", "setdefault", "pop", "popitem", "clear", "copy", "fromkeys"),
        *("__setitem__", "__delitem__", "__ior__", "__or__", "__ror__", "__init__", "__dict__"),
    }
)
"""Attributes that write or rebuild a map; a reference to one that is no call is a write too (an
alias, ``functools.partial(own.__setitem__, …)``, ``map(own.setdefault, …)``)."""
NON_MAPPINGS = frozenset(
    {"object", "BaseModel", "GenerateJsonSchema", "Enum", "StrEnum", "IntEnum", "NamedTuple"}
)
"""Classes a class of the module may have as a base besides its own others (and exception
classes): none is a mapping. ``dict``, ``Mapping``, ``TypedDict``, ``UserDict`` and the like are
not here, for ``Defs(own, **definitions)`` of such a class is a merge no call shows."""


def _plain_comprehension(comprehension: ast.DictComp) -> bool:
    """A comprehension that rebuilds one map, key by key, from that map's own members: its key is
    the iteration's key, and its value uses only the iteration's targets (a function called, but
    no other name, whichever map it would read): it cannot merge a second source."""
    if len(comprehension.generators) != 1:
        return False
    [generator] = comprehension.generators
    iterated = generator.iter
    items = (
        isinstance(iterated, ast.Call)
        and isinstance(iterated.func, ast.Attribute)
        and iterated.func.attr == "items"
        and not iterated.args
        and not iterated.keywords
    )
    target = generator.target
    if not (items and isinstance(target, ast.Tuple) and len(target.elts) == 2):
        return False
    key, _ = target.elts
    targets = {n.id for n in ast.walk(target) if isinstance(n, ast.Name)}
    functions = {
        id(call.func)
        for call in ast.walk(comprehension.value)
        if isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
    }
    used = {
        n.id
        for n in ast.walk(comprehension.value)
        if isinstance(n, ast.Name) and id(n) not in functions
    }
    return ast.unparse(key) == ast.unparse(comprehension.key) and used <= targets


def _base_name(base: ast.expr) -> str:
    """The name a class's base is known by: ``Generic[T]`` is ``Generic``, ``a.b.C`` is ``C``, and
    anything else (a call, a lambda) is no name."""
    if isinstance(base, ast.Subscript):
        return _base_name(base.value)
    if isinstance(base, ast.Attribute):
        return base.attr
    return base.id if isinstance(base, ast.Name) else ""


def _bindings(node: ast.AST) -> list[str]:
    """The names ``node`` binds (or unbinds), but a definition's own: an assignment, walrus, ``for``
    or ``with`` target, argument, ``except … as``, ``match`` capture or import."""
    if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store | ast.Del):
        return [node.id]
    if isinstance(node, ast.arg):
        return [node.arg]
    if isinstance(node, ast.alias):
        return [node.asname or node.name.split(".")[0]]
    if isinstance(node, ast.ExceptHandler) and node.name:
        return [node.name]
    if isinstance(node, ast.MatchAs | ast.MatchStar) and node.name:
        return [node.name]
    if isinstance(node, ast.MatchMapping) and node.rest:
        return [node.rest]
    return []


def _writes_of(
    node: ast.AST,
    local: frozenset[str],
    called: frozenset[int],
    defined: Counter[str],
    annotation: bool = False,
) -> tuple[str, str] | None:
    """The write ``node`` is, if it is one: (kind, target). In an annotation (evaluated when the
    function or class is defined) only a call or a reference to a writing attribute is one."""
    unparse = ast.unparse
    if isinstance(node, ast.Call):
        func = node.func
        if isinstance(func, ast.Name):
            safe = func.id in SAFE_FUNCTIONS or func.id in local or func.id.endswith("Error")
        else:
            safe = isinstance(func, ast.Attribute) and func.attr in SAFE_METHODS
        return None if safe else ("call", unparse(func))
    if isinstance(node, ast.Attribute) and node.attr in WRITING_ATTRIBUTES:
        return None if id(node) in called else ("attribute", unparse(node))
    if annotation:
        return None
    if isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Store | ast.Del):
        # A literal key is named in the target: ``defs["DocumentJson"] = …`` is a write too.
        literal = isinstance(node.slice, ast.Constant)
        return "subscript", unparse(node if literal else node.value)
    if isinstance(node, ast.AugAssign) and isinstance(node.op, ast.BitOr):
        return "|=", unparse(node.target)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        return "|", unparse(node)
    if isinstance(node, ast.DictComp) and not _plain_comprehension(node):
        return "dictcomp", f"{unparse(node.key)}: {unparse(node.value)}"
    if isinstance(node, ast.Dict) and len(node.keys) >= 2:
        if None in node.keys:
            merged = [v for key, v in zip(node.keys, node.values, strict=True) if key is None]
            return "**", ", ".join(unparse(value) for value in merged)
        if not all(isinstance(key, ast.Constant) for key in node.keys):
            # Computed keys of two entries or more: what a name-keyed map is written as, by hand.
            return "dict display", ", ".join(unparse(key) for key in node.keys if key)
    if isinstance(node, ast.alias) and node.asname:
        return "import as", f"{node.name} as {node.asname}"
    if isinstance(node, ast.ClassDef):
        names = [_base_name(base) for base in node.bases]
        if not all(
            name in local or name in NON_MAPPINGS or name.endswith(("Error", "Exception"))
            for name in names
        ):
            return "class", f"{node.name}({', '.join(unparse(base) for base in node.bases)})"
    if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) and (
        node.name in SAFE_FUNCTIONS or defined[node.name] > 1
    ):
        return "binding", node.name
    for name in _bindings(node):
        if name in SAFE_FUNCTIONS or name in local or name.endswith("Error"):
            return "binding", name
    return None


def writes(source: str) -> Counter[tuple[str, str, str]]:
    """Every write of ``_writes_of``'s forms in ``source``, and every call of it that is not safe
    by rule, by (qualified name, kind, target); annotations only for a call and a reference to a
    writing attribute."""
    found: Counter[tuple[str, str, str]] = Counter()
    tree = ast.parse(source)
    defs = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
    ]
    local = frozenset(node.name for node in defs)
    defined = Counter(node.name for node in defs)
    called = frozenset(id(node.func) for node in ast.walk(tree) if isinstance(node, ast.Call))

    def visit(node: ast.AST, scope: str, annotation: bool) -> None:
        for field, value in ast.iter_fields(node):
            below = annotation or field in ("annotation", "returns", "type_params")
            for child in value if isinstance(value, list) else [value]:
                if not isinstance(child, ast.AST):
                    continue
                inner = scope
                if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                    inner = f"{scope}.{child.name}" if scope else child.name
                if (kind := _writes_of(child, local, called, defined, below)) is not None:
                    found[(inner, *kind)] += 1
                visit(child, inner, below)

    visit(tree, "", False)
    return found


ROOT_KEYS = "a root's own keys, after $schema (and $id), beside literal keys"
SELF_KEYS = "a node's own keys rewritten in place, each once: no name from outside"
TYPED = "Pydantic's JSON Schema of a model, with a Strict generator (test_naming)"
FILLED = "the one literal key ($defs), then through put"
KEPT = "the builder's own typing (an identity) or Pydantic's own call"
ONE_MAP = "rebuilds one map's own members, key by key; the value reads a parameter"
LITERAL = "a fixed member of the JSON Schema or OpenAPI vocabulary, no name of a class"

ALLOWED: dict[str, dict[tuple[str, str, str], tuple[int, str]]] = {
    "export": {
        ("put", "subscript", "mapping"): (1, "the primitive itself"),
        ("put", "binding", "put"): (1, "the primitive's own definition"),
        ("", "binding", "MarkedSchema"): (1, "its declaration"),
        ("", "binding", "cast"): (1, "imported from typing"),
        ("", "**", "_tool_schemas()"): (1, "tool files are tool.*; no literal name is"),
        ("", "call", "NewType"): (1, "MarkedSchema's declaration"),
        ("", "call", "Path"): (2, "where the schemas are written"),
        ("write", "call", "build"): (1, "a schema builder, written to its own file"),
        ("write", "call", "vocabulary.document"): (
            1,
            "the form vocabulary's lists, written to its own file (D422): text, no map",
        ),
        ("render", "call", "json.dumps"): (1, "text, no map"),
        ("canonical", "call", "json.dumps"): (1, "text, no map"),
        ("_vet", "call", "math.isfinite"): (1, "a test of a number"),
        ("_tool_schemas", "call", "partial"): (
            2,
            "a builder with its arguments, put under its file",
        ),
        ("Strict._build_definitions_remapping", "call", "deepcopy"): (
            1,
            "a body, copied before Pydantic's remap rewrites it",
        ),
        ("Strict._build_definitions_remapping", "call", "defaultdict"): (
            1,
            "each final name's set of bodies, compared",
        ),
        ("Strict._build_definitions_remapping", "call", "originals.setdefault"): (
            1,
            "the first original of a final name, for the refusal's text alone",
        ),
        ("Strict._build_definitions_remapping", "call", "remapping.remap_json_schema"): (
            1,
            "Pydantic's own renaming, of a copy",
        ),
        ("Strict._build_definitions_remapping", "call", "super()._build_definitions_remapping"): (
            1,
            "Pydantic's own",
        ),
        ("_Nulls.model_schema", "**", "found"): (1, "one literal key Pydantic never writes"),
        ("_Nulls.model_schema", "call", "model.nullable"): (1, "the class's own declaration"),
        ("_Nulls.model_schema", "call", "super().model_schema"): (1, "Pydantic's own"),
        (
            "_allow_references",
            "dictcomp",
            "name: member if name == 'params' and skip or name in TEXT_MEMBERS else _wrap(member)",
        ): (1, ONE_MAP),
        ("_allow_references", "subscript", "result"): (5, SELF_KEYS),
        ("_allow_references", "subscript", "result['allOf']"): (1, LITERAL),
        ("_allow_references", "subscript", "result['anyOf']"): (1, LITERAL),
        ("_closed", "**", "rest"): (1, "rest is checked to hold no $ref just before"),
        ("_closed", "subscript", "result['additionalProperties']"): (1, LITERAL),
        ("_declared_nulls", "call", "definition.pop"): (1, "the one literal mark, taken off"),
        ("_declared_nulls", "subscript", "properties"): (1, "a member replaced in place"),
        ("_declared_nulls", "subscript", "value['default']"): (1, LITERAL),
        ("_document_as_written_schema", "call", "transformed.setdefault"): (1, FILLED),
        ("_document_as_written_schema", "subscript", "properties['params']"): (1, LITERAL),
        ("_document_as_written_schema", "subscript", "transformed['$id']"): (1, LITERAL),
        ("_document_schema", "**", "schema"): (1, ROOT_KEYS),
        ("_document_schema", "call", "Document.model_json_schema"): (1, TYPED),
        ("_document_schema", "call", "schema.setdefault"): (1, FILLED),
        ("_documents", "call", "found.setdefault"): (1, FILLED),
        ("_documents.replaced", "**", "root"): (1, "the description of a document as written"),
        ("_json_value", "**", "body"): (1, "the marker's key, which json_value never writes"),
        ("_merged", "**", "member, parent"): (1, "the shared keys are checked just before"),
        (
            "_optional_without_null",
            "dictcomp",
            "name: _no_default(member) if name in required or (isinstance(member, dict) and "
            "member.get(COMPUTED_MARK)) else _without_null(member)",
        ): (1, ONE_MAP),
        ("_optional_without_null", "subscript", "result['properties']"): (1, LITERAL),
        ("_output_json", "**", "rest"): (1, "rest is checked to hold no $ref just before"),
        ("_output_json", "dictcomp", "key: _output_json(value, definition)"): (2, ONE_MAP),
        ("_output_schema", "**", "closed"): (1, ROOT_KEYS),
        ("_output_schema", "call", "TypeAdapter"): (1, KEPT),
        ("_output_schema", "call", "TypeAdapter(output).json_schema"): (1, TYPED),
        ("_output_schema", "call", "closed.setdefault"): (1, FILLED),
        (
            "_renamed",
            "dictcomp",
            "key: new if key == '$ref' and value == old else _renamed(value, old, new)",
        ): (1, ONE_MAP),
        ("_request_json", "call", "found.setdefault"): (1, FILLED),
        ("_request_schema", "**", "closed"): (1, ROOT_KEYS),
        ("_request_schema", "call", "TypeAdapter"): (1, KEPT),
        ("_request_schema", "call", "TypeAdapter(request).json_schema"): (1, TYPED),
        ("_request_schema", "call", "closed.setdefault"): (1, FILLED),
        ("_without_null", "subscript", "result['anyOf']"): (2, LITERAL),
        ("_without_null", "subscript", "result['default']"): (1, LITERAL),
        ("descriptor_schema", "**", "schema"): (1, ROOT_KEYS),
        ("descriptor_schema", "call", "TypeAdapter"): (1, KEPT),
        ("descriptor_schema", "call", "TypeAdapter(Descriptor).json_schema"): (1, TYPED),
        ("descriptor_schema", "call", "schema.setdefault"): (1, FILLED),
        ("params_schema", "**", "schema"): (1, ROOT_KEYS),
        ("params_schema", "call", "model.model_json_schema"): (1, TYPED),
        ("params_schema", "call", "schema.setdefault"): (1, FILLED),
        (
            "unmarked",
            "dictcomp",
            "key: {name: plain(body) for name, body in defs.items()} if key == '$defs' else value",
        ): (1, "the $defs of one schema, each body unmarked, no name added"),
        ("values_schema", "**", "closed"): (1, ROOT_KEYS),
        ("values_schema", "call", "TypeAdapter"): (1, KEPT),
        ("values_schema", "call", "TypeAdapter(model).json_schema"): (1, TYPED),
        ("values_schema", "call", "closed.setdefault"): (1, FILLED),
    },
    "openapi": {
        ("", "binding", "MarkedSchema"): (1, "imported from export"),
        ("", "binding", "SchemaNameError"): (1, "imported from export"),
        ("", "binding", "cast"): (1, "imported from typing"),
        ("", "binding", "put"): (1, "the primitive, imported from export"),
        ("", "call", "Path"): (2, "where the document is written"),
        ("", "call", "re.compile"): (2, "a pattern"),
        ("_app", "call", "ImportLimits"): (1, "the stub application's own"),
        ("_app", "call", "Policy"): (1, "the stub application's own"),
        ("_app", "call", "Services"): (1, "the stub application's own"),
        ("_app", "call", "create_app"): (1, "the application whose routes are described"),
        ("_builders", "call", "export.output_schema_marked"): (2, "a builder, put under its name"),
        ("_builders", "call", "export.request_schema_marked"): (1, "a builder, put under its name"),
        ("_marked", "call", "dict"): (1, "a copy of one node"),
        ("_marked", "dictcomp", "name: _marked(member, below)"): (1, ONE_MAP),
        ("_marked", "subscript", "result"): (4, SELF_KEYS),
        ("_named", "call", "classes.setdefault"): (1, "a refusal check: the first class of a name"),
        ("_operation", "**", "given"): (1, "a parameter's schema replaced by R4's"),
        ("_operation", "call", "parameter"): (1, "R4's rewrite of a parameter's schema"),
        ("_operation", "subscript", "operation['parameters']"): (1, LITERAL),
        ("_operation", "subscript", "operation['requestBody']"): (2, LITERAL),
        ("_operation", "subscript", "operation['responses']"): (1, LITERAL),
        ("_operation", "subscript", "operation['security']"): (1, LITERAL),
        ("_referenced", "subscript", "result"): (2, SELF_KEYS),
        ("_responses", "**", "refused"): (1, "the literal headers key, refused has none"),
        ("_responses", "call", "dict"): (2, "a copy of one response"),
        ("_responses", "call", "errors.refusal_statuses"): (1, "the statuses, computed"),
        ("_routes", "call", "iter_route_contexts"): (1, "FastAPI's own, with the inclusions"),
        ("builder_schemas", "call", "export.unmarked"): (1, "a builder's schema, unmarked"),
        ("builder_schemas", "dictcomp", "name: export.unmarked(schema)"): (1, ONE_MAP),
        ("components_of", "**", "plain"): (1, "the mark's literal key, a builder's never is"),
        ("components_of", "call", "dict"): (1, "one map, sorted"),
        ("components_of", "call", "export.document_schema_marked"): (1, "hoisted as Substituted*"),
        ("components_of", "call", "export.made"): (1, "reads a marker by identity"),
        ("components_of", "call", "export.unmarked"): (1, "a component without its marker"),
        ("components_of", "call", "normalised"): (1, "the named local equivalences, per body"),
        ("components_of", "call", "untyped"): (1, "a check"),
        ("components_of", "dictcomp", "name: cast(JsonObject, normalised(body))"): (1, ONE_MAP),
        ("document", "call", "app.openapi"): (1, "FastAPI's own: paths, operations, parameters"),
        ("document", "call", "build"): (1, "a tool schema builder, checked by the vocabulary"),
        ("document", "call", "openapi_vocabulary.unlisted"): (1, "a check"),
        ("document", "call", "paths.setdefault"): (1, "a path's operations, written through put"),
        ("document", "call", "untyped"): (1, "a check"),
        ("hoisted", "dictcomp", "definition: prefix + definition"): (
            1,
            "each definition's component name; put checks the bodies",
        ),
        ("reached", "call", "pending.pop"): (1, "a worklist (a list)"),
        ("unconstrained_numbers", "call", "children"): (1, "a schema's subschemas, read"),
        ("unconstrained_numbers", "call", "constraint_only"): (1, "a check"),
        ("unconstrained_numbers", "|", "int | float"): (1, "a type union, no map"),
        ("write", "call", "export.render"): (1, "text, no map"),
    },
}


def deviations(source: str, allowed: dict[tuple[str, str, str], tuple[int, str]]) -> list[str]:
    """Where ``source``'s writes are not ``allowed`` as listed: a write unlisted, listed more or
    fewer times than it is found (a second use of a listed call changes a count), or listed and
    absent (stale), each with its reason empty or not."""
    found = writes(source)
    listed = {key: count for key, (count, _) in allowed.items()}
    wrong = [
        f"{key}: found {found.get(key, 0)}, listed {listed.get(key, 0)}"
        for key in sorted(set(found) | set(listed))
        if found.get(key, 0) != listed.get(key, 0)
    ]
    wrong += [f"{key}: no reason" for key, (_, reason) in sorted(allowed.items()) if not reason]
    return wrong


@pytest.mark.parametrize("module", list(LINTED))
def test_every_write_into_a_map_is_the_primitive_s_or_listed(module: str) -> None:
    """m1 (settlement, review rounds 1 and 2): every call in ``export`` and ``api.openapi`` that
    is not safe by rule (``SAFE_FUNCTIONS``, ``SAFE_METHODS``, an exception class, a function the
    module defines), every store into a subscript (a literal key too: ``defs["DocumentJson"] =
    …``) or deletion from one, every reference to an attribute that writes or rebuilds a map
    (``WRITING_ATTRIBUTES``: an alias too), ``|`` and ``|=``, a ``**`` beside another entry, a
    dict display of two entries or more whose keys are not all literals, a comprehension that is
    not one map's own members key by key, a class whose bases are not the module's own or known
    non-mappings (``NON_MAPPINGS``), every binding of a safe name, an exception class's or a name
    the module defines (assignment, walrus, ``for``, ``with``, argument, import, ``except``,
    ``match``), a call in an annotation (evaluated at definition) and every ``import … as`` is
    listed with its reason, keyed by its function, kind and target, with a count: a new callee,
    or a second use alike, fails until it is reviewed and listed, or goes through ``export.put``.
    A tripwire for these forms and no proof (``test_the_lint_knows_what_it_does_not_cover``)."""
    assert deviations(LINTED[module].read_text(encoding="utf-8"), ALLOWED[module]) == []


@pytest.mark.parametrize("module", list(LINTED))
def test_a_changed_count_a_stale_entry_and_an_unlisted_write_each_fail(module: str) -> None:
    """The allowlist's own check (``lint_count_ignored``, mutant): one more use of a listed write,
    one fewer, an entry listed that the source does not hold, a write the source holds that is not
    listed, and an entry without its reason each fail."""
    source = LINTED[module].read_text(encoding="utf-8")
    allowed = ALLOWED[module]
    assert deviations(source, allowed) == []
    key = next(key for key, (count, _) in allowed.items() if count == 1 and key[1] == "call")
    count, reason = allowed[key]
    for changed in (count + 1, count - 1):
        assert deviations(source, {**allowed, key: (changed, reason)}), (key, changed)
    stale = {**allowed, ("gone", "call", "nothing"): (1, "a reason")}
    assert deviations(source, stale) == ["('gone', 'call', 'nothing'): found 0, listed 1"]
    unlisted = {name: value for name, value in allowed.items() if name != key}
    assert deviations(source, unlisted) == [f"{key}: found {count}, listed 0"]
    assert deviations(source, {**allowed, key: (count, "")}) == [f"{key}: no reason"]


MERGES = {
    "subscript": "own[name] = definition",
    "literal subscript of a reserved name": "defs['DocumentJson'] = body",
    "delete": "del own[name]",
    "update": "own.update(definitions)",
    "two **": "return {**own, **definitions}",
    "|": "return own | definitions",
    "|=": "own |= definitions",
    "dict(**)": "return dict(own, **definitions)",
    "dict(list)": "return dict([*own.items(), *definitions.items()])",
    "dict(zip)": "return dict(zip(names, bodies))",
    "setitem": "operator.setitem(own, name, body)",
    "comprehension": "return {k: v for k, v in [*own.items(), *definitions.items()]}",
    "** and a key": "return {**own, name: body}",
    "ChainMap": "return dict(ChainMap(definitions, own))",
    "setdefault": "own.setdefault(name, body)",
    "literal setdefault": "defs.setdefault('RequestJson', body)",
    "__setitem__": "own.__setitem__(name, body)",
    "walrus alias then call": "(s := own.__setitem__)(name, body)",
    "bound method alias": "w = own.__setitem__\n    w(name, body)",
    "dict.fromkeys": "return dict.fromkeys(names, body)",
    "functools.reduce(or_)": "return functools.reduce(operator.or_, [own, definitions])",
    "collections.OrderedDict": "return collections.OrderedDict(own, **definitions)",
    "ChainMap in an items comprehension": (
        "return {k: v for k, v in collections.ChainMap(definitions, own).items()}"
    ),
    "__ior__": "own.__ior__(definitions)",
    "__or__": "return own.__or__(definitions)",
    "type(own)(...)": "return type(own)(own, **definitions)",
    "dict.__init__": "dict.__init__(own, definitions)",
    "from operator import setitem as s": "s(own, name, body)",
    "itertools in an items comprehension": (
        "return {k: v for k, v in itertools.chain(own, definitions).items()}"
    ),
    "setattr on a namespace": "setattr(ns, name, body)",
    "vars()": "vars(ns).update(definitions)",
    "a json round trip": "return json.loads(json.dumps(own) + json.dumps(definitions))",
    "MappingProxyType": "return types.MappingProxyType(own | definitions)",
    "model_copy(update=)": "found = found.model_copy(update=definitions)",
    "pop": "own.pop(name)",
    "starred dict()": "return dict(*[own], **definitions)",
    "locals() subscript": "locals()[name] = body",
    "a class body": "class K:\n        own[name] = body",
    "a nested function": "def inner():\n        own[name] = body\n    inner()",
    "a copy": "return own.copy()",
    "an items comprehension whose value reads another map": (
        "return {k: definitions.get(k, v) for k, v in own.items()}"
    ),
    "an items comprehension whose value calls with another map": (
        "return {k: fix(v, definitions) for k, v in own.items()}"
    ),
    "getattr(own, 'update')": "getattr(own, 'update')(definitions)",
    "MutableMapping.update": "MutableMapping.update(own, definitions)",
    "UserDict": "return collections.UserDict(own, **definitions)",
    "map over a bound method": "list(map(own.__setitem__, names, bodies))",
    "a partial of a bound method": "functools.partial(own.setdefault, name)(body)",
    "a bound method handed to a safe function": "sorted(names, key=own.pop)",
    "a bound method kept": "handler = own.__setitem__",
    "a lambda": "(lambda m: m.update(definitions))(own)",
    "a generator expression of calls": "any(own.__setitem__(n, b) for n, b in zip(names, bodies))",
    "a literal key store": "own['x'] = body",
    # One form for each rule of ``_plain_comprehension`` and of ``WRITING_ATTRIBUTES`` (round 2).
    "two generators": "return {k: v for d in (own, definitions) for k, v in d.items()}",
    "a transformed key": "return {str(k): v for k, v in own.items()}",
    "values() is no items": "return {k: v for k, v in own.values()}",
    "an uncalled __init__ handed to a safe function": "sorted(names, key=own.__init__)",
}

ROUND_2 = {
    "put rebound to operator.setitem": ("put = operator.setitem\n    put(own, name, body)"),
    "len rebound to operator.setitem": ("len = operator.setitem\n    len(own, name, body)"),
    "from-import then rebind to put": (
        "from operator import setitem\n    put = setitem\n    put(own, name, body)"
    ),
    "local function name rebound": ("_vet = operator.setitem\n    _vet(own, name, body)"),
    "parameter named put called": (
        "def inner(put):\n        put(own, name, body)\n    inner(operator.setitem)"
    ),
    "walrus into a safe name": ("(str := operator.setitem)\n    str(own, name, body)"),
    "for-loop target named put": ("for put in [operator.setitem]:\n        put(own, name, body)"),
    "with-as named put": (
        "with contextlib.nullcontext(operator.setitem) as put:\n        put(own, name, body)"
    ),
    "local dict subclass merge": (
        "class Defs(dict):\n        pass\n    return Defs(own, **definitions)"
    ),
    "local dict subclass via list of pairs": (
        "class Defs(dict):\n        pass\n    return Defs([*own.items(), *definitions.items()])"
    ),
    "dict display, two computed keys": "return {name: body, other: definitions}",
    "dict display, computed key twice": "return {name: body, name: definitions}",
    "a parameter annotation": "def inner(x: own.update(definitions) or int): ...",
    "a return annotation": "def inner() -> own.update(definitions) or int: ...",
    "a class-body annotated assignment's annotation": (
        "class K:\n        x: own.update(definitions) or int = 1"
    ),
    "plain comprehension, value through a safe-named reader": (
        "put = definitions.get\n    return {k: put(k, v) for k, v in own.items()}"
    ),
    "set comprehension then dict? (safe set)": "return dict.__call__(pairs)",
    "exec": "exec('own[name] = body')",
    "__import__ attribute call": "__import__('operator').setitem(own, name, body)",
    "type() 3-arg namespace merge": "K = type('K', (), {**own, **definitions})",
    "super().__setitem__": "super().__setitem__(name, body)",
    "object.__setattr__ __dict__": "object.__setattr__(ns, name, body)",
    "a decorator registering (attr not called)": ("@own.setdefault\n    def f(): ..."),
    "a decorator call (put)": ("@functools.partial(put, own, name)\n    def f(): ..."),
    "match mapping capture then merge via **": (
        "match own:\n        case {**rest}:\n            return {**rest, **definitions}"
    ),
    "global map rebinding through |": ("global DEFS\n    DEFS = DEFS | definitions"),
    "lambda default arg alias": ("w = (lambda f=own.__setitem__: f)()\n    w(name, body)"),
    "nested comprehension two generators": (
        "return {k: v for d in (own, definitions) for k, v in d.items()}"
    ),
    "itertools.starmap of setitem": (
        "list(itertools.starmap(operator.setitem, [(own, name, body)]))"
    ),
    "max(key=) with a writer": "max([definitions], key=own.__ior__)",
    "zip into a safe dict alias": ("d = dict\n    return d(zip(names, bodies))"),
    "str.maketrans": "return str.maketrans(own)",
    "a generator send": ("g = (own.update(x) for x in [definitions])\n    next(g)"),
    "json round trip via loads only": "return json.loads(text)",
    "a model_validate rebuild": "return Model.model_validate({**found, '$defs': definitions})",
    "OrderedDict.fromkeys alias": "fk = collections.OrderedDict.fromkeys",
}

NOT_COVERED = {
    "plain comprehension over a multidict's items": (
        "return {k: v for k, v in headers.items()}",
        (
            "a comprehension that rebuilds a receiver from its own `.items()`, "
            "key by key, is the form the allowlist accepts, and the syntax "
            "does not tell a multimap from a map"
        ),
    ),
    "plain comprehension, filter reads another map": (
        "return {k: v for k, v in own.items() if k not in definitions}",
        "a filter only removes members; no name of another map is ever added",
    ),
    "MutableHeaders.append (safe method)": (
        "headers.append(name, body)",
        (
            "`append`, `add` and `extend` are the methods of lists and sets in "
            "every other use; a multimap is told from them by its type, which "
            "the syntax does not give"
        ),
    ),
    "multidict add (safe method)": (
        "own.add(name, body)",
        "as `append`",
    ),
    "dataclasses.replace (safe method name)": (
        "return dataclasses.replace(found, defs=definitions)",
        "`replace` is `str.replace` everywhere else",
    ),
    "copy.replace (safe method name)": (
        "return copy.replace(found, **definitions)",
        "as `dataclasses.replace`",
    ),
    "Path.write_text keyed by a name": (
        "(root / f'{name}.json').write_text(body)",
        (
            "`write_text` is how `write` puts a file, named by the SCHEMAS key "
            "it is given, which is written through `put`"
        ),
    ),
    "defaultdict load writes a key": (
        "finals[name].add(body)",
        (
            "a load is no write form; a `defaultdict` is the one map that "
            "writes on a read, and making one (`defaultdict(...)`) is listed, "
            "but a read of the one `Strict` holds is not seen"
        ),
    ),
}


MERGE_HEAD = (
    "def merge(own, definitions, found, name, body, names, bodies, ns, pairs, other, headers, "
    "text, finals, root):\n"
)


@pytest.mark.parametrize("form", [*MERGES, *ROUND_2])
def test_the_lint_catches_every_form_of_a_merge(form: str) -> None:
    """The 33 forms the review round 1 probed (``naming-c1/p_lint.py``: 20 slipped past the first
    lint), the 14 before them, the 37 of the 45 of round 2 (``naming-c2/p_lint.py``: 20 slipped
    past the second) the lint now catches and one for each of the lint's own rules, each in a
    function ``merge`` that nothing lists."""
    body = {**MERGES, **ROUND_2}[form]
    source = f"{MERGE_HEAD}    {body}\n"
    assert any(scope.startswith("merge") for scope, _, _ in writes(source)), form


@pytest.mark.parametrize("attribute", sorted(WRITING_ATTRIBUTES))
def test_every_writing_attribute_is_a_write_where_no_call_shows_it(attribute: str) -> None:
    """A reference handed to a safe function (``sorted(names, key=own.pop)``): no call of a name or
    a method the lint judges shows it, so each of ``WRITING_ATTRIBUTES`` is a rule of its own."""
    found = writes(f"{MERGE_HEAD}    sorted(names, key=own.{attribute})\n")
    assert {kind for _, kind, _ in found} == {"attribute"}, attribute


@pytest.mark.parametrize("form", list(NOT_COVERED))
def test_the_lint_knows_what_it_does_not_cover(form: str) -> None:
    """The lint is a tripwire for the forms it lists, no proof (D416): these forms of round 2
    slip past it, each for the reason stated (a mapping that is no ``dict``, a method name it
    cannot tell by its receiver, a read that writes), and this test fails the day one is caught,
    so that D416 and this table are stated again. Every map is protected by more than the lint:
    definitions and components by the property tests over random class trees, routes, parameters,
    operation ids, responses and headers by their own refusal tests."""
    body, reason = NOT_COVERED[form]
    assert reason
    source = f"{MERGE_HEAD}    {body}\n"
    assert not [scope for scope, _, _ in writes(source) if scope.startswith("merge")], form


def test_the_lint_lets_pass_what_cannot_write_a_name() -> None:
    clean = (
        "def _closed(value):\n"
        "    return value\n\n\n"
        "def keep(found, name):\n"
        "    kept = {key: value for key, value in found.items()}\n"
        "    again = {key: _closed(value) for key, value in found.items() if key != 'x'}\n"
        "    return len(found), sorted(kept), name.startswith('a'), found.get(name, 1), again\n"
    )
    assert writes(clean) == Counter()
    assert writes("def f(a: int | None) -> dict[str, int] | None:\n    return None\n") == Counter()
    assert not writes("def f():\n    return Boom('x')\n\nclass Boom(Exception): ...\n")
    literals = "def f(a, b):\n    return {'x': a, 'y': b, 'z': 1}, {a: b}, {**a}\n"
    assert writes(literals) == Counter(), "literal keys, one entry and a lone ** are no merge"
    classes = (
        "class Base(BaseModel): ...\n\n\n"
        "class Child(Base): ...\n\n\n"
        "class Failure(ValueError): ...\n\n\n"
        "class Shape(Generic[T], metaclass=Meta): ...\n"
    )
    assert writes(classes) == Counter({("Shape", "class", "Shape(Generic[T])"): 1})
    annotated = (
        "def f(a: dict[str, int] | None = None, *, b: Literal['x'] = 'x') -> int | None: ...\n"
    )
    assert writes(annotated) == Counter(), "a union in an annotation is no merge"
    names = (
        "def f(found, name):\n    for key in found:\n        seen = key\n    return seen, name\n"
    )
    assert writes(names) == Counter(), "bindings of other names are no write"


@pytest.mark.parametrize(
    ("source", "kind"),
    [
        ("def f(a, b, c, d):\n    return {a: b, c: d}\n", "dict display"),
        ("def f(a, b):\n    return {'x': a, b: a}\n", "dict display"),
        ("class K(dict): ...\n", "class"),
        ("class K(collections.UserDict): ...\n", "class"),
        ("class K(Mapping[str, int]): ...\n", "class"),
        ("class K(make()): ...\n", "class"),
        ("def f(x: g(1)) -> h(2): ...\n", "call"),
        ("def f(put): ...\n", "binding"),
        ("def put(): ...\n", "binding"),
        ("def helper(): ...\n\n\ndef helper(): ...\n", "binding"),
        ("def f():\n    try:\n        pass\n    except E as str:\n        pass\n", "binding"),
        ("import operator as o\nimport os.path\nfrom m import len\n", "binding"),
    ],
)
def test_each_rule_of_the_lint_reports_its_own_kind(source: str, kind: str) -> None:
    """One source for each of the rules round 2 added, reported as the kind it is (the annotation's
    call as a ``call``; ``from m import len`` as a ``binding`` of a safe name)."""
    assert kind in {found for _, found, _ in writes(source)}, source


def test_an_import_as_is_a_write() -> None:
    """``from operator import setitem as s`` hides what ``s(…)`` is: no alias is allowed."""
    found = writes("from operator import setitem as s\n\nfrom json import dumps\n")
    assert found == Counter({("", "import as", "setitem as s"): 1})


# --- A sanity check of the fixtures' helper ------------------------------------------------------


def test_the_included_router_is_looked_into() -> None:
    """The walk above depends on FastAPI's private ``_match`` of an included router: every
    router the application includes has one (a rename fails here, loudly, naming it), and an
    operator route is found through it."""
    app = openapi._app()
    included = [route for route in app.router.routes if type(route).__name__ == "_IncludedRouter"]
    assert len(included) == 4
    assert all(callable(getattr(route, "_match", None)) for route in included), (
        "FastAPI's _IncludedRouter._match is gone or renamed: rewrite fastapi_route"
    )
    scope = {"type": "http", "method": "GET", "path": "/operator/datasets", "root_path": ""}
    found = fastapi_route(scope, list(app.router.routes))
    assert isinstance(found, APIRoute)
    assert found.operation_id == "datasets"


def test_fastapi_route_finds_the_operation_s_own_route() -> None:
    app = openapi._app()
    scope = {"type": "http", "method": "GET", "path": "/api/health", "root_path": ""}
    found = fastapi_route(scope, list(app.router.routes))
    assert isinstance(found, APIRoute)
    assert found.operation_id == "health"

"""The MCP server and its transport (SPEC §11.1, §11.2, §14, D278, D279).

``McpTransport`` is the ASGI application the server serves at ``/mcp``, behind request
protection like every route (D255): the official MCP Python SDK's low-level server over its
streamable HTTP transport, **stateless** and answering in JSON, so that no session outlives its
request and a client holds nothing on the server between calls. The SDK's own DNS-rebinding
check is off, since request protection has made it already; its body limit is the server's
``request_bytes``, which request protection enforces first.

Before the SDK reads a body, the transport reads it as the operator router reads its own (§7.1,
D260), on the event loop when it has at most ``INLINE_BYTES`` (an ``initialize``, a ``ping``, a
notification or a list takes about a millisecond), so that small messages never wait behind tool
calls, and otherwise on the tools' own threads, as a call of the client's (``Calls.run``): UTF-8, no
duplicate keys, no non-finite numbers, lone surrogates or nesting past 64, at most 200,000 JSON
values (``PARSE_ERROR``, -32700); then as one JSON-RPC message (``_envelope``): an object whose
``jsonrpc`` is ``"2.0"``, whose ``id``, if it has one, is an integer or a string, and whose
``method`` is a string (``INVALID_REQUEST``, -32600), one this server answers (``METHOD_NOT_FOUND``,
-32601), with ``params`` an object shaped as its method takes them (``INVALID_PARAMS``, -32602); and
no string or member name anywhere in it holds a token's or a handle's shape standing alone, or the
configured curator token anywhere (-32600, or -32602 within ``params``). Each is answered 400 with a
JSON-RPC error whose ``data`` holds the refusal, blanked, and whose ``id`` is the message's when
that is one and holds no secret, ``null`` otherwise, quoting nothing else of it: the SDK validates
messages with Pydantic, whose errors, which it logs and answers, quote their input. The body is
received within its deadlines (``Calls.deadlines``, D266, D278): a client that sends it slowly, or
half of it, is answered 408 (``LIMIT_EXCEEDED`` naming ``tool_body_idle_seconds`` or
``upload_seconds``) rather than holding its connection. Once it is received, the message has one
``tool_seconds``, which its reading and the call or the resource read it makes share (``ENDS``, in
the request's scope). A body that is not ``application/json``, or a client that does not accept
JSON, is refused first (415, 406), in the same shape, before anything is read.

The server lists exactly the tools of ``catalog.tools`` (``TOOLS``), with schemas generated from
their models and the descriptions that carry §11.1's rules, and never an operator operation
(§11.2): the transport never mounts the operator router, and imports none of its modules. A
call's arguments are written back as JSON and read by the tool's model (``load_request``); its
output is the tool result's structured content, and the same JSON its text; a refusal is a tool
error (``isError``) whose structured content is ``{"refusals": […]}``, blanked of token and
handle shapes (D265). No handler raises: the SDK would quote an exception's message. The read-only
tools are idempotent; ``propose_descriptor`` is not, since a proposal accepted or rejected meanwhile
is made again, and neither are ``count_cohort`` and ``run_analysis``, which record new issuances
every time (D300, D318).

**Resources** (D279): every descriptor is a resource, ``aibi://dataset/<id>@<n | sha256:hex |
draft>/<descriptor id>``, ``aibi://concept/<id>``, ``aibi://analysis/<id>@<version>`` (the
registry's entries, D316) and ``aibi://model/<id>``, read as its RFC 8785 JSON; the list names
each dataset's descriptor in its latest release, the concepts, the analyses and the model cards,
and the templates name the rest. A resource that cannot be read is a JSON-RPC error
(``RESOURCE_NOT_FOUND``, -32002, or ``INTERNAL_ERROR``) with its refusals as data.
"""

import json
from collections.abc import AsyncGenerator, AsyncIterator, Iterable, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from functools import partial
from typing import Any, cast

import mcp.types as types
from mcp.server.lowlevel import Server
from mcp.server.lowlevel.helper_types import ReadResourceContents
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from mcp.shared.exceptions import McpError
from pydantic import AnyUrl, JsonValue
from starlette.types import Message, Receive, Scope, Send

import aibi
from aibi.core.bodies import declared_length
from aibi.core.catalog.service import RESOURCE_TEMPLATES, refusals_of
from aibi.core.catalog.tools import BY_NAME, INSTRUCTIONS, TOOLS, Tool
from aibi.core.mcp.calls import Calls, failed
from aibi.core.schema.export import output_schema, request_schema
from aibi.core.schema.jsonio import JsonError, parse_json, pointer
from aibi.core.schema.limits import MAX_BODY_BYTES
from aibi.core.schema.operator import Refusals
from aibi.core.schema.output import Output, TextSegment, text
from aibi.core.schema.refusals import (
    SECRET_BLANK,
    Limit,
    Refusal,
    RefusalCode,
    blank_secrets,
    holds_secret,
    holds_token_of,
)
from aibi.core.store.store import StoreRefused

RESOURCE_NOT_FOUND = -32002
"""The JSON-RPC code of a resource that is not there (MCP)."""
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
SERVER_ERROR = -32000
"""A limit the server reached: no place for a call freed in time (JSON-RPC's server errors)."""
JSON = "application/json"
INLINE_BYTES = 4 * 1024
"""Bodies of at most this many bytes are read on the event loop, not as a call (module
docstring)."""
ENDS = "aibi.ends"
"""The member of a request's ASGI scope that holds when its call must be answered."""
REQUESTS = frozenset(
    {
        "initialize",
        "ping",
        "tools/list",
        "tools/call",
        "resources/list",
        "resources/templates/list",
        "resources/read",
    }
)
"""The requests the server answers."""
NOTIFICATIONS = frozenset(
    {
        "notifications/initialized",
        "notifications/cancelled",
        "notifications/progress",
        "notifications/roots/list_changed",
    }
)
"""The notifications the server takes."""


def _schema(found: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in found.items() if key != "$id"}


def _definition(tool: Tool) -> types.Tool:
    stem = tool.name.replace("_", "-")
    return types.Tool(
        name=tool.name,
        description=tool.description,
        inputSchema=_schema(request_schema(tool.request, f"tool.{stem}.request.schema.json")),
        outputSchema=_schema(output_schema(tool.output, f"tool.{stem}.output.schema.json")),
        annotations=types.ToolAnnotations(
            readOnlyHint=tool.read_only,
            destructiveHint=False,
            idempotentHint=tool.read_only,
            openWorldHint=False,
        ),
    )


def _refusals(refusals: Iterable[Refusal]) -> str:
    return Refusals(refusals=[blank_secrets(refusal) for refusal in refusals]).model_dump_json()


def _result(found: Output | list[Refusal]) -> types.CallToolResult:
    written = _refusals(found) if isinstance(found, list) else found.model_dump_json()
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=written)],
        structuredContent=cast(dict[str, Any], json.loads(written)),
        isError=isinstance(found, list),
    )


def _unknown_tool() -> Refusal:
    return Refusal(
        code=RefusalCode.NOT_FOUND,
        path=None,
        message=[text("No tool has that name; these are the tools")],
        alternatives=[text(tool.name) for tool in TOOLS],
    )


def _arguments(arguments: object) -> bytes | Refusal:
    try:
        return json.dumps(arguments, ensure_ascii=False, allow_nan=False).encode(
            "utf-8", "surrogatepass"
        )
    except (TypeError, ValueError):
        return Refusal(
            code=RefusalCode.INVALID_JSON,
            path=None,
            message=[text("The arguments are not JSON text carries unchanged")],
        )


def _error(refusals: Iterable[Refusal]) -> McpError:
    listed = [blank_secrets(refusal) for refusal in refusals]
    code = RESOURCE_NOT_FOUND
    if any(refusal.code == RefusalCode.INTERNAL_ERROR for refusal in listed):
        code = types.INTERNAL_ERROR
    written = cast(dict[str, Any], json.loads(Refusals(refusals=listed).model_dump_json()))
    message = " ".join(
        segment.text for segment in listed[0].message if isinstance(segment, TextSegment)
    )
    return McpError(types.ErrorData(code=code, message=message or "Refused", data=written))


def mcp_server(calls: Calls) -> Server[Any, Any]:
    """The MCP server over ``calls``' catalogue (module docstring)."""
    server: Server[Any, Any] = Server("aibi", version=aibi.__version__, instructions=INSTRUCTIONS)
    definitions = [_definition(tool) for tool in TOOLS]

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        return list(definitions)

    @server.call_tool(validate_input=False)
    async def call_tool(name: str, arguments: dict[str, Any]) -> types.CallToolResult:
        tool = BY_NAME.get(name)
        if tool is None:
            return _result([_unknown_tool()])
        body = _arguments(arguments)
        if isinstance(body, Refusal):
            return _result([body])
        client, ends = _asked(server)
        return _result(await calls.tool(tool, body, client, ends=ends))

    @server.list_resources()
    async def list_resources() -> list[types.Resource]:
        client, ends = _asked(server)
        found = await calls.run(lambda: tuple(calls.catalog.resources()), client=client, ends=ends)
        if isinstance(found, list):
            raise _error(found)
        return [types.Resource(uri=AnyUrl(uri), name=name, mimeType=JSON) for uri, name in found]

    @server.list_resource_templates()
    async def list_resource_templates() -> list[types.ResourceTemplate]:
        return [
            types.ResourceTemplate(uriTemplate=template, name=name, mimeType=JSON)
            for template, name in RESOURCE_TEMPLATES
        ]

    @server.read_resource()
    async def read_resource(uri: AnyUrl) -> Iterable[ReadResourceContents]:
        client, ends = _asked(server)
        found = await calls.run(partial(_read, calls, str(uri)), client=client, ends=ends)
        if not isinstance(found, str):
            raise _error(found)
        return [ReadResourceContents(content=found, mime_type=JSON)]

    return server


def _read(calls: Calls, uri: str) -> str | tuple[Refusal, ...]:
    try:
        return calls.catalog.read_resource(uri)
    except Exception as error:
        found = refusals_of(error)
        if found is None:
            raise
        return found


def _asked(server: Server[Any, Any]) -> tuple[str, float | None]:
    """The address of the client whose request the server is answering, ``""`` without one, and
    when its call must be answered (``ENDS``), as the streamable HTTP transport gives its request
    to the handlers."""
    try:
        request: object = server.request_context.request
    except LookupError:
        return "", None
    client = getattr(request, "client", None)
    host = getattr(client, "host", None)
    scope = cast(object, getattr(request, "scope", None))
    ends = cast(Mapping[str, object], scope).get(ENDS) if isinstance(scope, dict) else None
    return (
        host if isinstance(host, str) else "",
        ends if isinstance(ends, float) else None,
    )


@dataclass(frozen=True)
class _Refused:
    """A message refused before the SDK reads it: its JSON-RPC error code, the ``id`` to answer
    with, and the refusal."""

    code: int
    id: int | str | None
    refusal: Refusal


def _rpc_error(code: int, refusal: Refusal, id: int | str | None = None) -> bytes:
    written = cast(
        dict[str, Any], json.loads(Refusals(refusals=[blank_secrets(refusal)]).model_dump_json())
    )
    error = {"code": code, "message": "The request is refused", "data": written}
    return json.dumps({"jsonrpc": "2.0", "id": id, "error": error}).encode("utf-8")


def _refusal(
    code: RefusalCode, path: str, message: str, *, available: Iterable[str] = ()
) -> Refusal:
    return Refusal(
        code=code,
        path=path,
        message=[text(message)],
        alternatives=[text(item) for item in available],
    )


def _secret(value: JsonValue, at: list[str | int], digest: bytes | None) -> str | None:
    """The pointer of the first string or member name in ``value`` that holds a secret."""

    def held(found: str) -> bool:
        return holds_secret(found) or (digest is not None and holds_token_of(found, digest))

    if isinstance(value, str):
        return pointer(at) if held(value) else None
    if isinstance(value, list):
        for index, member in enumerate(value):
            if (found := _secret(member, [*at, index], digest)) is not None:
                return found
    elif isinstance(value, dict):
        for key, member in value.items():
            if held(key):
                return pointer([*at, SECRET_BLANK])
            if (found := _secret(member, [*at, key], digest)) is not None:
                return found
    return None


_PARAMS: Mapping[str, tuple[tuple[str, type, bool], ...]] = {
    "initialize": (
        ("protocolVersion", str, True),
        ("capabilities", dict, True),
        ("clientInfo", dict, True),
    ),
    "tools/call": (("name", str, True), ("arguments", dict, False)),
    "resources/read": (("uri", str, True),),
    "tools/list": (("cursor", str, False),),
    "resources/list": (("cursor", str, False),),
    "resources/templates/list": (("cursor", str, False),),
}
"""The members of each method's ``params`` that the transport checks: name, type, required."""


def _envelope(value: JsonValue, digest: bytes | None) -> _Refused | None:
    """The refusal of a message that is not one JSON-RPC request or notification this server
    takes, or that holds a secret (module docstring)."""
    if not isinstance(value, dict):
        message = "A request body is one JSON-RPC message, an object"
        return _Refused(INVALID_REQUEST, None, _refusal(RefusalCode.WRONG_TYPE, "", message))
    secret = _secret(value, [], digest)
    if secret is not None:
        code = INVALID_PARAMS if secret.startswith("/params/") else INVALID_REQUEST
        message = "A message holds no token's or session handle's shape"
        return _Refused(code, None, _refusal(RefusalCode.INVALID_VALUE, secret, message))
    given = value.get("id")
    has_id = "id" in value
    id: int | str | None = None
    if isinstance(given, str) or (isinstance(given, int) and not isinstance(given, bool)):
        id = given
    if value.get("jsonrpc") != "2.0":
        message = 'A JSON-RPC message has "jsonrpc": "2.0"'
        return _Refused(
            INVALID_REQUEST, id, _refusal(RefusalCode.INVALID_VALUE, "/jsonrpc", message)
        )
    if has_id and id is None:
        message = "A request's id is an integer or a string"
        return _Refused(INVALID_REQUEST, None, _refusal(RefusalCode.WRONG_TYPE, "/id", message))
    method = value.get("method")
    if not isinstance(method, str):
        message = "The server takes requests and notifications, whose method is a string"
        return _Refused(INVALID_REQUEST, id, _refusal(RefusalCode.WRONG_TYPE, "/method", message))
    known = REQUESTS if has_id else NOTIFICATIONS
    if method not in known:
        message = "The server does not answer that method; these are the ones it answers"
        refusal = _refusal(RefusalCode.NOT_FOUND, "/method", message, available=sorted(known))
        return _Refused(METHOD_NOT_FOUND, id, refusal)
    params = value.get("params", {})
    if not isinstance(params, dict):
        message = "A message's params are an object"
        return _Refused(INVALID_PARAMS, id, _refusal(RefusalCode.WRONG_TYPE, "/params", message))
    for name, kind, required in _PARAMS.get(method, ()):
        at = f"/params/{name}"
        if name not in params:
            if required:
                message = f"The method's params have {name}"
                return _Refused(
                    INVALID_PARAMS, id, _refusal(RefusalCode.MISSING_MEMBER, at, message)
                )
            continue
        member = params[name]
        if not isinstance(member, kind):
            message = f"The method's {name} is {'a string' if kind is str else 'an object'}"
            return _Refused(INVALID_PARAMS, id, _refusal(RefusalCode.WRONG_TYPE, at, message))
    return None


def _checked(body: bytes, digest: bytes | None) -> _Refused | None:
    """The refusal of a body that is not one JSON-RPC message the loader and ``_envelope``
    accept (D260, D278)."""
    try:
        value = parse_json(body)
    except JsonError as problem:
        limit = Limit(name=problem.limit[0], max=problem.limit[1]) if problem.limit else None
        refusal = Refusal(
            code=RefusalCode(problem.code),
            path=problem.pointer,
            message=[text(problem.message)],
            limit=limit,
        )
        return _Refused(PARSE_ERROR, None, refusal)
    return _envelope(value, digest)


class McpTransport:
    """The ASGI application at ``/mcp`` (module docstring). ``lifespan`` runs the SDK's session
    manager, which the application's lifespan enters."""

    def __init__(self, calls: Calls, *, max_body_bytes: int = MAX_BODY_BYTES) -> None:
        self.calls = calls
        self.server = mcp_server(calls)
        self.max_body_bytes = max_body_bytes
        self._running: StreamableHTTPSessionManager | None = None

    @asynccontextmanager
    async def lifespan(self) -> AsyncGenerator[None]:
        if self._running is not None:
            yield
            return
        manager = StreamableHTTPSessionManager(
            app=self.server,
            json_response=True,
            stateless=True,
            security_settings=TransportSecuritySettings(enable_dns_rebinding_protection=False),
            max_request_body_size=self.max_body_bytes,
        )
        async with manager.run():
            self._running = manager
            try:
                yield
            finally:
                self._running = None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        manager = self._running
        if scope["type"] != "http":
            return
        if manager is None:
            await _answer(send, 503, _rpc_error(types.INTERNAL_ERROR, failed()))
            return
        media = _media(scope)
        if media is not None:
            await _answer(send, media[0], _rpc_error(INVALID_REQUEST, media[1]))
            return
        deadlines = self.calls.deadlines(declared_length(scope.get("headers", [])))
        try:
            body = await deadlines.read(_chunks(receive))
        except StoreRefused as slow:
            await _answer(send, 408, _rpc_error(INVALID_REQUEST, slow.refusal))
            return
        except _Gone:
            return
        ends = self.calls.ends()
        digest = self.calls.catalog.token_digest
        if len(body) <= INLINE_BYTES:
            refused: _Refused | list[Refusal] | None = _checked(body, digest)
        else:
            refused = await self.calls.run(
                partial(_checked, body, digest), client=_host(scope), ends=ends
            )
        if isinstance(refused, list):
            failing = refused[0].code == RefusalCode.INTERNAL_ERROR
            code = types.INTERNAL_ERROR if failing else SERVER_ERROR
            await _answer(send, 500 if failing else 503, _rpc_error(code, refused[0]))
            return
        if refused is not None:
            answered = _rpc_error(refused.code, refused.refusal, refused.id)
            await _answer(send, 400, answered)
            return
        sent = False

        async def replay() -> Message:
            nonlocal sent
            if sent:
                return await receive()
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}

        await manager.handle_request({**scope, ENDS: ends}, replay, send)


class _Gone(Exception):  # noqa: N818 - the client left
    """The client left before its body arrived."""


async def _chunks(receive: Receive) -> AsyncIterator[bytes]:
    while True:
        message = await receive()
        if message["type"] == "http.disconnect":
            raise _Gone
        yield cast(bytes, message.get("body", b""))
        if not message.get("more_body", False):
            return


def _host(scope: Scope) -> str:
    """The address of the connection's client, ``""`` without one."""
    client = cast(object, scope.get("client"))
    if isinstance(client, tuple | list) and client:
        host = cast(Sequence[object], client)[0]
        return host if isinstance(host, str) else ""
    return ""


def _header(scope: Scope, name: bytes) -> list[str]:
    return [v.decode("latin-1") for n, v in scope.get("headers", []) if n == name]


def _media(scope: Scope) -> tuple[int, Refusal] | None:
    """The status and refusal of a request whose body is not JSON, or whose client does not
    accept JSON, which the SDK would answer in its own words."""
    given = _header(scope, b"content-type")
    if len(given) != 1 or given[0].split(";", 1)[0].strip().lower() != JSON:
        message = "A JSON-RPC message is JSON: Content-Type: application/json"
        return 415, Refusal(
            code=RefusalCode.UNSUPPORTED_MEDIA_TYPE, path=None, message=[text(message)]
        )
    accepted = [
        part.split(";", 1)[0].strip() for part in ",".join(_header(scope, b"accept")).split(",")
    ]
    if not any(part.startswith(JSON) for part in accepted):
        message = "The transport answers in JSON: Accept: application/json"
        return 406, Refusal(code=RefusalCode.INVALID_VALUE, path=None, message=[text(message)])
    return None


async def _answer(send: Send, status: int, body: bytes) -> None:
    headers = [(b"content-type", JSON.encode()), (b"content-length", str(len(body)).encode())]
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})


__all__ = [
    "ENDS",
    "INLINE_BYTES",
    "INVALID_PARAMS",
    "INVALID_REQUEST",
    "METHOD_NOT_FOUND",
    "NOTIFICATIONS",
    "PARSE_ERROR",
    "REQUESTS",
    "RESOURCE_NOT_FOUND",
    "SERVER_ERROR",
    "McpTransport",
    "mcp_server",
]

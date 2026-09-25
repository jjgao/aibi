"""The tools over HTTP (SPEC §11.1, D280).

Each tool of ``catalog.tools`` is ``POST /api/tools/<name>``, whose body is a JSON object of the
tool's arguments with ``Content-Type: application/json`` (``UNSUPPORTED_MEDIA_TYPE``, 415,
otherwise), received within its deadlines as the MCP transport receives its own
(``Calls.deadlines``, 408 naming ``tool_body_idle_seconds`` or ``upload_seconds``) and read by
``load_request`` in a worker thread (D260), and whose answer is the tool's
output or its refusals, with the status of the first (D265). The routes run the calls the MCP
server runs (``mcp.calls``): the same service functions, limits and places, so that an agent
gets the same answers from either transport. The HTTP API is public, as the MCP transport is:
request protection guards it (D255), and it takes no curator token.
"""

from collections.abc import Awaitable, Callable

from fastapi import APIRouter, Request
from starlette.responses import Response

from aibi.core.api.errors import refused
from aibi.core.api.routes import API_PREFIX
from aibi.core.bodies import declared_length
from aibi.core.catalog.tools import TOOLS, Tool
from aibi.core.mcp.calls import Calls
from aibi.core.schema.output import text
from aibi.core.schema.refusals import Refusal, RefusalCode
from aibi.core.store.store import StoreRefused

TOOLS_PREFIX = f"{API_PREFIX}/tools"


def _json(request: Request) -> bool:
    given = request.headers.getlist("content-type")
    return len(given) == 1 and given[0].split(";", 1)[0].strip().lower() == "application/json"


def _endpoint(calls: Calls, tool: Tool) -> Callable[[Request], Awaitable[Response]]:
    async def endpoint(request: Request) -> Response:
        if not _json(request):
            refusal = Refusal(
                code=RefusalCode.UNSUPPORTED_MEDIA_TYPE,
                path=None,
                message=[text("A tool's body is JSON: Content-Type: application/json")],
            )
            return refused([refusal])
        deadlines = calls.deadlines(declared_length(request.headers.raw))
        try:
            body = await deadlines.read(request.stream())
        except StoreRefused as slow:
            return refused([slow.refusal])
        client = "" if request.client is None else request.client.host
        found = await calls.tool(tool, body, client)
        if isinstance(found, list):
            return refused(found)
        return Response(found.model_dump_json(), media_type="application/json")

    return endpoint


def tools_router(calls: Calls) -> APIRouter:
    """``POST /api/tools/<name>`` for every tool."""
    router = APIRouter(prefix=TOOLS_PREFIX)
    for tool in TOOLS:
        router.add_api_route(
            f"/{tool.name}",
            _endpoint(calls, tool),
            methods=["POST"],
            response_model=tool.output,
            name=tool.name,
        )
    return router


__all__ = ["TOOLS_PREFIX", "tools_router"]

"""The application (SPEC §11, §12.1, D255, D264, D278, D280).

``create_app`` builds one FastAPI application: the public API's routes, the operator router at
``/operator``, and, given the server's tool calls, the tools over HTTP at ``/api/tools``, the
MCP transport at ``/mcp``, whose session manager the application's lifespan runs, and the
read-only catalogue page at ``/`` and ``/datasets/<dataset>`` (``api.page``, D311), whose paths
request protection and the error handlers then answer with pages (``pages``, D311, D314); and the
mounts it is given. All of it is behind one ``RequestProtection``, which the application adds as its
outermost middleware. It serves no documentation pages and no OpenAPI document (the pages load
scripts from a CDN; the document is generated, for types, but not served), and redirects no
trailing slash. A mount at ``/``, or at or below ``/api``, ``/operator`` or, with the tools,
``/mcp`` or ``/datasets``, is refused: it would take paths from the routers, or put a path under
the operator router's protection that its routes do not serve. The operator router is included
once, and never by a mount; the MCP transport answers ``POST`` at ``/mcp`` alone.
"""

import time
from collections.abc import AsyncGenerator, Callable, Mapping
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from types import MappingProxyType

from fastapi import FastAPI
from starlette.routing import Route
from starlette.types import ASGIApp

import aibi
from aibi.core.api.chrome import DATASETS_PREFIX
from aibi.core.api.errors import install
from aibi.core.api.page import page_router
from aibi.core.api.protection import Policy, RequestProtection
from aibi.core.api.routes import API_PREFIX, api_router
from aibi.core.api.tools import tools_router
from aibi.core.mcp.calls import Calls
from aibi.core.mcp.server import McpTransport
from aibi.core.operator.auth import OPERATOR_PREFIX
from aibi.core.operator.router import Services, operator_router

MCP_PATH = "/mcp"


def _mount_path(path: str, reserved: tuple[str, ...]) -> str:
    if not path.startswith("/") or path == "/" or path.endswith("/"):
        raise ValueError(f"a mount is at a path below /, without a trailing slash: {path!r}")
    if any(path == taken or path.startswith(taken + "/") for taken in reserved):
        raise ValueError(f"a mount at {path!r} would lie under a path the application serves")
    return path


def _lifespan(
    transport: McpTransport | None,
) -> Callable[[FastAPI], AbstractAsyncContextManager[None]]:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        if transport is None:
            yield
            return
        async with transport.lifespan():
            yield

    return lifespan


def create_app(
    policy: Policy,
    services: Services,
    *,
    tools: Calls | None = None,
    mounts: Mapping[str, ASGIApp] = MappingProxyType({}),
    clock: Callable[[], float] = time.monotonic,
) -> FastAPI:
    """The application, every route and mount behind request protection under ``policy``;
    with ``tools``, the tools over HTTP and MCP and the catalogue page."""
    reserved = (
        API_PREFIX,
        OPERATOR_PREFIX,
        *((MCP_PATH, DATASETS_PREFIX) if tools is not None else ()),
    )
    paths = [_mount_path(path, reserved) for path in mounts]
    transport = None if tools is None else McpTransport(tools, max_body_bytes=policy.max_body_bytes)
    app = FastAPI(
        title="aibi",
        version=aibi.__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        redirect_slashes=False,
        lifespan=_lifespan(transport),
    )
    install(app, pages=tools is not None)
    app.include_router(api_router())
    if tools is not None and transport is not None:
        app.include_router(tools_router(tools))
        app.router.routes.append(Route(MCP_PATH, transport, methods=["POST"]))
        app.include_router(page_router(tools))
    app.include_router(operator_router(services))
    for path in paths:
        app.mount(path, mounts[path])
    app.add_middleware(RequestProtection, policy=policy, clock=clock, pages=tools is not None)
    return app


__all__ = ["MCP_PATH", "create_app"]

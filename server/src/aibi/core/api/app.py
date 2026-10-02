"""The application (SPEC §11, §12.1, D255, D264).

``create_app`` builds one FastAPI application: the public API's routes, the operator router at
``/operator``, and the mounts it is given (#12's MCP transport at ``/mcp``), all behind one
``RequestProtection``, which the application adds as its outermost middleware. It serves no
documentation pages and no OpenAPI document (the pages load scripts from a CDN; the document is
generated, for types, but not served), and redirects no trailing slash. A mount at ``/``, or at or
below ``/api`` or ``/operator``, is refused: it would take paths from the routers, or put a path
under the operator router's protection that its routes do not serve. The operator router is
included once, and never by a mount.
"""

import time
from collections.abc import Callable, Mapping
from types import MappingProxyType

from fastapi import FastAPI
from starlette.types import ASGIApp

import aibi
from aibi.core.api.errors import install
from aibi.core.api.protection import Policy, RequestProtection
from aibi.core.api.routes import API_PREFIX, api_router
from aibi.core.operator.auth import OPERATOR_PREFIX
from aibi.core.operator.router import Services, operator_router

_RESERVED = (API_PREFIX, OPERATOR_PREFIX)


def _mount_path(path: str) -> str:
    if not path.startswith("/") or path == "/" or path.endswith("/"):
        raise ValueError(f"a mount is at a path below /, without a trailing slash: {path!r}")
    if any(path == reserved or path.startswith(reserved + "/") for reserved in _RESERVED):
        raise ValueError(f"a mount at {path!r} would lie under the API or the operator router")
    return path


def create_app(
    policy: Policy,
    services: Services,
    *,
    mounts: Mapping[str, ASGIApp] = MappingProxyType({}),
    clock: Callable[[], float] = time.monotonic,
) -> FastAPI:
    """The application, every route and mount behind request protection under ``policy``."""
    paths = [_mount_path(path) for path in mounts]
    app = FastAPI(
        title="aibi",
        version=aibi.__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        redirect_slashes=False,
    )
    install(app)
    app.include_router(api_router())
    app.include_router(operator_router(services))
    for path in paths:
        app.mount(path, mounts[path])
    app.add_middleware(RequestProtection, policy=policy, clock=clock)
    return app


__all__ = ["create_app"]

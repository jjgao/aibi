"""The public API's own routes (SPEC §11.1): a health check, which request protection guards like
every other route. The tools are ``api.tools``'s."""

from fastapi import APIRouter
from starlette.responses import Response

from aibi.core.schema.operator import Health

API_PREFIX = "/api"


def api_router() -> APIRouter:
    router = APIRouter(prefix=API_PREFIX)

    @router.get("/health", response_model=Health)
    def health() -> Response:
        return Response(Health(status="ok").model_dump_json(), media_type="application/json")

    return router


__all__ = ["API_PREFIX", "api_router"]

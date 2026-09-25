"""Refusals over HTTP (SPEC §8.6, D265).

Every error response on every router is ``{"refusals": [Refusal, …]}``: at least one refusal, and
all of a change's failing stage (D246) or of an import. Its status is the first refusal's
(``status_of``):

- 400 ``HOST_NOT_ALLOWED`` and ``OPERATOR_REQUIRED``; 401 ``TOKEN_REQUIRED``; 403
  ``ORIGIN_NOT_ALLOWED`` and ``CSRF_REQUIRED``;
- 404 ``NOT_FOUND``, ``UNKNOWN_RELEASE``, ``UNKNOWN_PROPOSAL`` and ``UNKNOWN_DATASET``; 405
  ``METHOD_NOT_ALLOWED``;
- 409 ``DATASET_BUSY``, ``CONFLICT``, ``NO_SESSION``, ``NO_CHANGE``, ``DATASET_EXISTS``,
  ``RELEASE_WITHDRAWN`` and ``ERASURE_BLOCKED``; 411 ``LENGTH_REQUIRED``;
- ``LIMIT_EXCEEDED``: 408 naming ``upload_idle_seconds`` or ``upload_seconds``, 413 naming
  ``request_bytes``, 429 naming a rate and 503 naming ``concurrent_imports`` (both with
  ``Retry-After``), and 422 naming any other limit;
- 415 ``UNSUPPORTED_MEDIA_TYPE``; 500 ``INTERNAL_ERROR``, which says nothing more;
- 422 every other code, a pack's included.

``install`` registers the handlers that turn the core's refusals, FastAPI's validation errors
(of path and query parameters: bodies are read by ``load_request``), Starlette's own 404 and 405
and any other exception into refusals. No response quotes the request: a parameter is named, not
echoed, and ``refused``, which every handler answers through, writes each refusal with anything
of a token's or a handle's shape blanked (``blank_secrets``), whichever service raised it.
"""

from collections.abc import Mapping, Sequence

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response

from aibi.core.importers.errors import ImportRefused
from aibi.core.operator.auth import AUTHORIZATION
from aibi.core.schema.limits import (
    API_REQUESTS,
    CONCURRENT_IMPORTS,
    OPERATOR_REQUESTS,
    REQUEST_BYTES,
    TOKEN_FAILURES,
    UPLOAD_IDLE_SECONDS,
    UPLOAD_SECONDS,
)
from aibi.core.schema.operator import Refusals
from aibi.core.schema.output import text
from aibi.core.schema.refusals import Refusal, RefusalCode, blank_secrets
from aibi.core.store.build import BuildRefused
from aibi.core.store.carry import CarryRefused
from aibi.core.store.edits import EditRefused
from aibi.core.store.store import StoreRefused

R = RefusalCode
_STATUS: Mapping[str, int] = {
    R.HOST_NOT_ALLOWED: 400,
    R.OPERATOR_REQUIRED: 400,
    R.TOKEN_REQUIRED: 401,
    R.ORIGIN_NOT_ALLOWED: 403,
    R.CSRF_REQUIRED: 403,
    R.NOT_FOUND: 404,
    R.UNKNOWN_RELEASE: 404,
    R.UNKNOWN_PROPOSAL: 404,
    R.UNKNOWN_DATASET: 404,
    R.METHOD_NOT_ALLOWED: 405,
    R.DATASET_BUSY: 409,
    R.CONFLICT: 409,
    R.NO_SESSION: 409,
    R.NO_CHANGE: 409,
    R.DATASET_EXISTS: 409,
    R.RELEASE_WITHDRAWN: 409,
    R.ERASURE_BLOCKED: 409,
    R.LENGTH_REQUIRED: 411,
    R.UNSUPPORTED_MEDIA_TYPE: 415,
    R.INTERNAL_ERROR: 500,
}
_LIMITS: Mapping[str, int] = {
    UPLOAD_IDLE_SECONDS: 408,
    UPLOAD_SECONDS: 408,
    REQUEST_BYTES: 413,
    OPERATOR_REQUESTS: 429,
    API_REQUESTS: 429,
    TOKEN_FAILURES: 429,
    CONCURRENT_IMPORTS: 503,
}
RETRY_IMPORT = 30
"""The ``Retry-After`` of an import refused while ``concurrent_imports`` run, in seconds."""


def status_of(refusal: Refusal) -> int:
    """The HTTP status of a refusal, by its code and, for ``LIMIT_EXCEEDED``, its limit."""
    code = str(refusal.code)
    if code == R.LIMIT_EXCEEDED:
        return 422 if refusal.limit is None else _LIMITS.get(refusal.limit.name, 422)
    return _STATUS.get(code, 422)


def refused(
    refusals: Sequence[Refusal],
    *,
    status: int | None = None,
    headers: Mapping[str, str] | None = None,
) -> Response:
    """The response that refuses a request: the first refusal's status unless ``status`` is
    given, with ``Retry-After`` for an import refused while others run, and every token or
    handle shape blanked (D265)."""
    listed = [blank_secrets(found) for found in refusals]
    answer = status if status is not None else status_of(listed[0])
    extra = dict(headers or {})
    if answer == 503:
        extra.setdefault("Retry-After", str(RETRY_IMPORT))
    body = Refusals(refusals=listed).model_dump_json()
    return Response(body, status_code=answer, media_type="application/json", headers=extra)


def refusal(code: RefusalCode, message: str, *, alternatives: Sequence[str] = ()) -> Refusal:
    """A refusal without a path, in the server's own words."""
    return Refusal(
        code=code,
        path=None,
        message=[text(message)],
        alternatives=[text(alternative) for alternative in alternatives],
    )


async def _store(request: Request, error: Exception) -> Response:
    assert isinstance(error, StoreRefused)
    return refused([error.refusal])


async def _refusals(request: Request, error: Exception) -> Response:
    assert isinstance(error, ImportRefused | EditRefused | BuildRefused | CarryRefused)
    return refused(error.refusals)


async def _parameters(request: Request, error: Exception) -> Response:
    assert isinstance(error, RequestValidationError)
    found: list[Refusal] = []
    for details in error.errors():
        where, *rest = (str(element) for element in details.get("loc", ()))
        name = rest[0] if rest else where
        problem = "is missing" if details.get("type") == "missing" else "is not valid"
        message = f"The {where} parameter {name} {problem}: {details.get('msg', '')}"
        found.append(refusal(RefusalCode.INVALID_VALUE, message))
    return refused(found or [refusal(RefusalCode.INVALID_VALUE, "A parameter is not valid")])


async def _http(request: Request, error: Exception) -> Response:
    assert isinstance(error, StarletteHTTPException)
    if error.status_code == 404:
        return refused([refusal(RefusalCode.NOT_FOUND, "Nothing is at this path")])
    if error.status_code == 405:
        allowed = (error.headers or {}).get("Allow", "")
        methods = [method.strip() for method in allowed.split(",") if method.strip()]
        found = refusal(
            RefusalCode.METHOD_NOT_ALLOWED,
            "This path does not take this method",
            alternatives=methods,
        )
        return refused([found], headers={"Allow": allowed} if allowed else None)
    if error.status_code == 401:
        found = refusal(
            RefusalCode.TOKEN_REQUIRED,
            "Operator requests carry the curator token: Authorization: Bearer <token>",
        )
        return refused([found], headers={"WWW-Authenticate": AUTHORIZATION})
    if 400 <= error.status_code < 500:
        found = refusal(RefusalCode.INVALID_VALUE, "The request is not one this path takes")
        return refused([found], status=error.status_code)
    return refused([refusal(RefusalCode.INTERNAL_ERROR, "The server failed")], status=500)


async def _unexpected(request: Request, error: Exception) -> Response:
    return refused([refusal(RefusalCode.INTERNAL_ERROR, "The server failed")])


def install(app: FastAPI) -> None:
    """Register the handlers that answer every error with refusals."""
    app.add_exception_handler(StoreRefused, _store)
    for kind in (ImportRefused, EditRefused, BuildRefused, CarryRefused):
        app.add_exception_handler(kind, _refusals)
    app.add_exception_handler(RequestValidationError, _parameters)
    app.add_exception_handler(StarletteHTTPException, _http)
    app.add_exception_handler(Exception, _unexpected)


__all__ = ["RETRY_IMPORT", "install", "refusal", "refused", "status_of"]

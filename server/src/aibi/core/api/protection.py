"""Request protection: one middleware in front of every router and mount (SPEC §11.2, §14,
D255–D263).

``RequestProtection`` is a pure ASGI middleware that the application adds outermost, so that every
request reaches it first: every router, every mount (such as #12's MCP transport) and every path,
unknown ones included. It checks, in this order, and refuses at the first check that fails:

1. **Host** (D256): exactly one ``Host`` header, whose host, in lower case and without its port,
   is a loopback name, the bound address or a configured hostname (``HOST_NOT_ALLOWED``, 400).
2. **Origin and fetch metadata** (D257): an ``Origin``, if given, is given once, is not ``null``
   and is one of the server's own origins or a public origin, or on the API and mounts a CORS
   origin; ``Sec-Fetch-Site: same-site`` or ``cross-site`` needs an allowed ``Origin``
   (``ORIGIN_NOT_ALLOWED``, 403). A request without either passes: clients that are not browsers
   send neither, and the curator token is what admits them to the operator router.
3. **CORS preflights** (D258): each takes a token from the client's ``api`` bucket (429 when it
   is empty), then is answered for a CORS origin on the API and mounts, for ``GET`` and ``POST``
   and the ``Content-Type`` header, without credentials; any other preflight is refused. With
   CORS configured, responses there carry ``Vary: Origin``, and a CORS origin's requests
   ``Access-Control-Allow-Origin``.
4. **Off the operator router, the client's rate** (D259): one token from the client's bucket of
   the path's class (``LIMIT_EXCEEDED`` naming the rate, 429, with ``Retry-After``). Only
   requests that passed the Host and Origin checks count, so a web page cannot drain the bucket
   local clients share.
5. **At /operator and below** (D259, D261–D263): ``Authorization: Bearer <curator token>``, the
   only place a token is taken, and none in the URL or a cookie, as written or percent-decoded,
   is verified first. A request that fails takes a token from the client's ``token_failures``
   bucket and is ``TOKEN_REQUIRED`` (401), or ``LIMIT_EXCEEDED`` naming ``token_failures`` (429)
   once that bucket is empty; it takes nothing from the ``operator`` bucket. A request with the
   token is never refused for failures, anyone's: it takes a token from the client's
   ``operator`` bucket (429 naming ``operator_requests``), then needs one ``Aibi-Operator`` that
   decodes to an operator's name (``OPERATOR_REQUIRED``, 400); a browser's request (one with an
   ``Origin`` or a ``Sec-Fetch-*`` header), unless it gets ``/operator/csrf``, carries the CSRF
   token (``CSRF_REQUIRED``, 403); and a request whose method is not ``GET``, ``HEAD`` or
   ``OPTIONS`` has ``Content-Type: application/json``, ``application/octet-stream`` for an upload
   (``UNSUPPORTED_MEDIA_TYPE``, 415). The request then carries its attribution,
   ``operator:<name>``, and the CSRF token, in its scope (``OPERATOR_KEY``, ``CSRF_KEY``).
6. **The body's size** (D260): a ``Content-Length`` over the limit is refused before anything is
   read, and a body is counted as it arrives, the request refused once it passes the limit
   (``LIMIT_EXCEEDED`` naming ``request_bytes``, 413): ``max_body_bytes``, or ``import_bytes``
   for an upload.

Every response gets ``X-Content-Type-Options: nosniff``, ``Referrer-Policy: no-referrer``,
``Cross-Origin-Resource-Policy: same-origin`` and, unless it set its own, ``Content-Security-Policy:
default-src 'none'; frame-ancestors 'none'``; the operator router's get ``Cache-Control:
no-store``, and with TLS every response gets ``Strict-Transport-Security``. No response carries an
``Access-Control-*`` header but the CORS headers this middleware adds. An exception that the
application leaves unanswered is answered here with ``INTERNAL_ERROR`` and logged, never quoted,
its path with any segment of a token's or a handle's shape blanked (``blank_path``); a client
that left before its body arrived gets no answer. WebSocket connections get the Host, Origin and
rate checks, and none reaches the operator router; lifespan events pass through. Refusals have
the one error shape (D265), and quote nothing of the request.
"""

import logging
import math
import re
import secrets
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal, cast

from starlette.requests import ClientDisconnect
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from aibi.core.api.config import ServerConfig
from aibi.core.api.errors import status_of
from aibi.core.api.logs import WithoutSecrets
from aibi.core.api.origins import host_name, origin, own_origins
from aibi.core.api.rates import Buckets, Rate, client_key
from aibi.core.operator.auth import (
    AUTHORIZATION,
    CSRF_HEADER,
    CSRF_KEY,
    OPERATOR_HEADER,
    OPERATOR_KEY,
    OPERATOR_PREFIX,
    attribution,
    bearer,
    blank_path,
    csrf_ok,
    csrf_token,
    holds_token,
    token_digest,
    verify,
)
from aibi.core.schema.limits import (
    API_REQUESTS,
    OPERATOR_REQUESTS,
    REQUEST_BYTES,
    TOKEN_FAILURES,
)
from aibi.core.schema.operator import Refusals
from aibi.core.schema.output import text
from aibi.core.schema.refusals import Limit, Refusal, RefusalCode, blank_secrets

RateClass = Literal["operator", "api", "token_failures"]
UPLOAD_PATH = re.compile(r"^/operator/datasets/[^/]+/uploads$")
CSRF_PATH = f"{OPERATOR_PREFIX}/csrf"
CORS_PREFIXES = ("/api", "/mcp")
"""The paths a CORS origin may reach: the public API and the MCP transport (#12)."""
CORS_METHODS = ("GET", "POST")
CORS_HEADERS = ("content-type",)
SECURITY_HEADERS: tuple[tuple[bytes, bytes], ...] = (
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"no-referrer"),
    (b"cross-origin-resource-policy", b"same-origin"),
)
CONTENT_SECURITY_POLICY = b"default-src 'none'; frame-ancestors 'none'"
STRICT_TRANSPORT_SECURITY = b"max-age=31536000"

_LIMIT_NAMES: Mapping[RateClass, str] = {
    "operator": OPERATOR_REQUESTS,
    "api": API_REQUESTS,
    "token_failures": TOKEN_FAILURES,
}
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_CROSS_SITE = frozenset({b"same-site", b"cross-site"})
_TOKEN_MESSAGE = "Operator requests carry the curator token: Authorization: Bearer <token>"
_logger = logging.getLogger(__name__)
_logger.addFilter(WithoutSecrets())


@dataclass(frozen=True)
class Policy:
    """What request protection allows, from the configuration (D255–D263). The token's digest
    and the CSRF key are kept out of its ``repr``."""

    hosts: frozenset[str]
    origins: frozenset[str]
    """The server's own origins and the public origins: allowed on every router."""
    cors_origins: frozenset[str]
    """Allowed besides on the API and the mounts, which answer their preflights (D258)."""
    token_digest: bytes = field(repr=False)
    csrf_key: bytes = field(repr=False)
    rates: Mapping[RateClass, Rate]
    classes: Sequence[tuple[str, RateClass]]
    """The rate class of each path prefix; any other path is ``api``. #12 adds ``/mcp``."""
    max_body_bytes: int
    upload_bytes: int
    tls: bool

    @classmethod
    def of(cls, config: ServerConfig, *, csrf_key: bytes | None = None) -> "Policy":
        """The policy of a configuration, with a CSRF key drawn now unless one is given."""
        hosts = config.hosts()
        own = own_origins(hosts, tls=config.tls, port=config.server.port)
        rates = config.server.rates
        return cls(
            hosts=hosts,
            origins=own | frozenset(config.server.public_origins),
            cors_origins=frozenset(config.server.cors_origins),
            token_digest=token_digest(config.curator.token_hash),
            csrf_key=secrets.token_bytes(32) if csrf_key is None else csrf_key,
            rates={
                "operator": Rate(rates.operator.per_minute, rates.operator.burst),
                "api": Rate(rates.api.per_minute, rates.api.burst),
                "token_failures": Rate(rates.token_failures.per_minute, rates.token_failures.burst),
            },
            classes=((OPERATOR_PREFIX, "operator"),),
            max_body_bytes=config.server.max_body_bytes,
            upload_bytes=config.imports.import_bytes,
            tls=config.tls,
        )


class BodyTooLarge(Exception):  # noqa: N818 - raised into the application by ``receive``
    """A request body counted past its limit as it arrived."""

    def __init__(self, limit: int) -> None:
        super().__init__(f"the request body has more than {limit} bytes")
        self.limit = limit


@dataclass(frozen=True)
class _Refused:
    refusal: Refusal
    headers: tuple[tuple[bytes, bytes], ...] = ()

    @property
    def status(self) -> int:
        return status_of(self.refusal)


def _refused(
    code: RefusalCode,
    message: str,
    *,
    limit: Limit | None = None,
    alternatives: Sequence[str] = (),
    headers: tuple[tuple[bytes, bytes], ...] = (),
) -> _Refused:
    found = Refusal(
        code=code,
        path=None,
        message=[text(message)],
        alternatives=[text(alternative) for alternative in alternatives],
        limit=limit,
    )
    return _Refused(found, headers)


def _route_path(scope: Scope) -> str:
    """The path the routes match, as Starlette finds it: without the server's root path."""
    path = cast(str, scope.get("path", ""))
    root = cast(str, scope.get("root_path", ""))
    if not root or not path.startswith(root):
        return path
    if path == root:
        return ""
    return path[len(root) :] if path[len(root)] == "/" else path


def _under(path: str, prefix: str) -> bool:
    return path == prefix or path.startswith(prefix + "/")


@dataclass(frozen=True)
class _Seen:
    """What the checks read of a request."""

    websocket: bool
    method: str
    path: str
    target: bytes
    """The raw path and query string, where a token is looked for."""
    headers: Mapping[bytes, tuple[bytes, ...]]
    client: str
    """The client's rate-limit key (``client_key``)."""

    @classmethod
    def of(cls, scope: Scope) -> "_Seen":
        headers: dict[bytes, list[bytes]] = {}
        for name, value in cast(Sequence[tuple[bytes, bytes]], scope.get("headers", ())):
            headers.setdefault(bytes(name).lower(), []).append(bytes(value))
        path = _route_path(scope)
        raw = cast(bytes | None, scope.get("raw_path")) or path.encode("utf-8", "surrogateescape")
        query = cast(bytes, scope.get("query_string", b""))
        client = cast(tuple[str, int] | None, scope.get("client"))
        return cls(
            websocket=scope["type"] == "websocket",
            method=cast(str, scope.get("method", "GET")).upper(),
            path=path,
            target=bytes(raw) + b"?" + bytes(query),
            headers={name: tuple(values) for name, values in headers.items()},
            client="" if client is None else client_key(str(client[0])),
        )

    def one(self, name: bytes) -> str | None:
        """The header's value if it is given exactly once, as Latin-1 text."""
        values = self.headers.get(name, ())
        return values[0].decode("latin-1") if len(values) == 1 else None

    @property
    def operator(self) -> bool:
        return _under(self.path, OPERATOR_PREFIX)

    @property
    def cors_path(self) -> bool:
        return any(_under(self.path, prefix) for prefix in CORS_PREFIXES)

    @property
    def upload(self) -> bool:
        return self.method == "POST" and UPLOAD_PATH.fullmatch(self.path) is not None

    @property
    def browser(self) -> bool:
        """Whether a browser sent it: it has an ``Origin`` or a ``Sec-Fetch-*`` header (D263)."""
        return b"origin" in self.headers or any(
            name.startswith(b"sec-fetch-") for name in self.headers
        )

    @property
    def preflight(self) -> bool:
        return (
            self.method == "OPTIONS"
            and b"origin" in self.headers
            and b"access-control-request-method" in self.headers
        )

    @property
    def origin(self) -> str | None:
        given = self.one(b"origin")
        return None if given is None else origin(given)


class RequestProtection:
    """The middleware (module docstring). ``clock`` drives the rate buckets."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        policy: Policy,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.app = app
        self.policy = policy
        self._buckets = {name: Buckets(rate, clock=clock) for name, rate in policy.rates.items()}
        self._csrf = csrf_token(policy.csrf_key, policy.token_digest)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            await self.app(scope, receive, send)
            return
        if scope["type"] not in ("http", "websocket"):
            raise RuntimeError("request protection passes lifespan, HTTP and WebSocket alone")
        scope = {key: value for key, value in scope.items() if key not in (OPERATOR_KEY, CSRF_KEY)}
        seen = _Seen.of(scope)
        found = self._host(seen) or self._origin(seen)
        if found is None and seen.preflight and not seen.websocket:
            found = self._rate(seen, "api")
            if found is None:
                await self._preflight(seen, send)
                return
        by: str | None = None
        if found is None and not seen.operator:
            found = self._rate(seen, self._class(seen))
        elif found is None and seen.websocket:
            found = _refused(RefusalCode.NOT_FOUND, "The operator router serves no WebSocket")
        elif found is None:
            found, by = self._operator(seen)
        if found is None and not seen.websocket:
            found = self._declared(seen)
        if found is not None:
            if seen.websocket:
                await _close(receive, send)
            else:
                await self._refuse(seen, send, found)
            return
        if by is not None:
            scope = {**scope, OPERATOR_KEY: by, CSRF_KEY: self._csrf}
        if seen.websocket:
            await self.app(scope, receive, send)
            return
        await self._forward(scope, seen, receive, send)

    # --- The checks, in order --------------------------------------------------------------

    def _host(self, seen: _Seen) -> _Refused | None:
        given = seen.one(b"host")
        host = None if given is None else host_name(given)
        if host is None or host not in self.policy.hosts:
            return _refused(
                RefusalCode.HOST_NOT_ALLOWED,
                "The Host is not one this server answers to; a DNS-rebinding page cannot make "
                "it one",
            )
        return None

    def _allowed_origins(self, seen: _Seen) -> frozenset[str]:
        if seen.operator or not seen.cors_path:
            return self.policy.origins
        return self.policy.origins | self.policy.cors_origins

    def _origin(self, seen: _Seen) -> _Refused | None:
        given = seen.headers.get(b"origin", ())
        allowed = self._allowed_origins(seen)
        if given and (len(given) != 1 or seen.origin not in allowed):
            return _refused(RefusalCode.ORIGIN_NOT_ALLOWED, "Requests from this origin are refused")
        site = seen.headers.get(b"sec-fetch-site", ())
        if not given and any(value.strip().lower() in _CROSS_SITE for value in site):
            return _refused(
                RefusalCode.ORIGIN_NOT_ALLOWED,
                "A request from another site needs an Origin this server allows",
            )
        return None

    async def _preflight(self, seen: _Seen, send: Send) -> None:
        requested = (seen.one(b"access-control-request-method") or "").strip().upper()
        asked = seen.one(b"access-control-request-headers") or ""
        headers = {name.strip().lower() for name in asked.split(",") if name.strip()}
        cors = seen.origin in self.policy.cors_origins and seen.cors_path and not seen.operator
        if not cors or requested not in CORS_METHODS or not headers <= set(CORS_HEADERS):
            refusal = _refused(
                RefusalCode.ORIGIN_NOT_ALLOWED,
                "Cross-origin requests are not allowed here",
                alternatives=sorted(self.policy.cors_origins) if cors else (),
            )
            await self._refuse(seen, send, refusal)
            return
        answer = [
            (b"access-control-allow-methods", ", ".join(CORS_METHODS).encode("ascii")),
            (b"access-control-allow-headers", ", ".join(CORS_HEADERS).encode("ascii")),
            (b"access-control-max-age", b"600"),
            (b"content-length", b"0"),
        ]
        wrapped = self._headers(seen, send, _Started(), preflight=True)
        await wrapped({"type": "http.response.start", "status": 204, "headers": answer})
        await wrapped({"type": "http.response.body", "body": b""})

    def _limited(self, name: RateClass, wait: float) -> _Refused:
        rate = self.policy.rates[name]
        return _refused(
            RefusalCode.LIMIT_EXCEEDED,
            "Too many requests from this client; try again later",
            limit=Limit(name=_LIMIT_NAMES[name], max=rate.per_minute),
            headers=((b"retry-after", str(max(1, math.ceil(wait))).encode("ascii")),),
        )

    def _class(self, seen: _Seen) -> RateClass:
        for prefix, name in self.policy.classes:
            if _under(seen.path, prefix):
                return name
        return "api"

    def _rate(self, seen: _Seen, name: RateClass) -> _Refused | None:
        wait = self._buckets[name].take(seen.client)
        return None if wait is None else self._limited(name, wait)

    def _token(self, seen: _Seen) -> bool:
        digest = self.policy.token_digest
        if holds_token(seen.target, digest) or any(
            holds_token(cookie, digest) for cookie in seen.headers.get(b"cookie", ())
        ):
            return False
        given = seen.one(b"authorization")
        token = None if given is None else bearer(given)
        return token is not None and verify(token, self.policy.token_digest)

    def _operator(self, seen: _Seen) -> tuple[_Refused | None, str | None]:
        if not self._token(seen):
            limited = self._rate(seen, "token_failures")
            if limited is not None:
                return limited, None
            challenge = ((b"www-authenticate", AUTHORIZATION.encode("ascii")),)
            return _refused(RefusalCode.TOKEN_REQUIRED, _TOKEN_MESSAGE, headers=challenge), None
        limited = self._rate(seen, "operator")
        if limited is not None:
            return limited, None
        named = seen.one(OPERATOR_HEADER.encode("ascii"))
        by = None if named is None else attribution(named)
        if by is None:
            return _refused(
                RefusalCode.OPERATOR_REQUIRED,
                "Operator requests name their operator in one Aibi-Operator header: the name, "
                "percent-encoded as UTF-8, 1 to 200 characters without control or bidi formatting "
                "characters, and holding no token or handle",
            ), None
        exempt = seen.method == "GET" and seen.path == CSRF_PATH
        if seen.browser and not exempt:
            given = seen.one(CSRF_HEADER.encode("ascii"))
            if not csrf_ok(given, self.policy.csrf_key, self.policy.token_digest):
                return _refused(
                    RefusalCode.CSRF_REQUIRED,
                    f"A browser's operator requests carry the Aibi-CSRF header that GET "
                    f"{CSRF_PATH} gives",
                ), None
        if seen.method not in _SAFE_METHODS:
            expected = "application/octet-stream" if seen.upload else "application/json"
            given_type = seen.one(b"content-type")
            media = None if given_type is None else given_type.partition(";")[0].strip().lower()
            if media != expected:
                return _refused(
                    RefusalCode.UNSUPPORTED_MEDIA_TYPE,
                    "This request's body is not of the content type the path reads",
                    alternatives=[expected],
                ), None
        return None, by

    def _limit(self, seen: _Seen) -> int:
        return self.policy.upload_bytes if seen.upload else self.policy.max_body_bytes

    def _too_large(self, limit: int) -> _Refused:
        return _refused(
            RefusalCode.LIMIT_EXCEEDED,
            f"The request body has more than {limit} bytes",
            limit=Limit(name=REQUEST_BYTES, max=limit),
        )

    def _declared(self, seen: _Seen) -> _Refused | None:
        limit = self._limit(seen)
        for value in seen.headers.get(b"content-length", ()):
            digits = value.strip()
            if digits.isdigit() and (len(digits) > 18 or int(digits) > limit):
                return self._too_large(limit)
        return None

    # --- Answering -------------------------------------------------------------------------

    def _headers(
        self, seen: _Seen, send: Send, started: "_Started", *, preflight: bool = False
    ) -> Send:
        """``send``, adding the security headers, and the CORS ones where they apply, to a
        response's start; an ``Access-Control-*`` header is kept only in a preflight's answer,
        which this middleware writes."""
        reachable = bool(self.policy.cors_origins) and seen.cors_path and not seen.operator
        cors = seen.origin if reachable and seen.origin in self.policy.cors_origins else None

        async def sending(message: Message) -> None:
            if message["type"] == "http.response.start":
                started.started = True
                given = cast(Sequence[tuple[bytes, bytes]], message.get("headers", ()))
                headers = [
                    (bytes(name).lower(), bytes(value))
                    for name, value in given
                    if preflight or not bytes(name).lower().startswith(b"access-control-")
                ]
                names = {name for name, _ in headers}
                headers.extend(pair for pair in SECURITY_HEADERS if pair[0] not in names)
                if b"content-security-policy" not in names:
                    headers.append((b"content-security-policy", CONTENT_SECURITY_POLICY))
                if seen.operator:
                    headers = [pair for pair in headers if pair[0] != b"cache-control"]
                    headers.append((b"cache-control", b"no-store"))
                if self.policy.tls:
                    headers.append((b"strict-transport-security", STRICT_TRANSPORT_SECURITY))
                if cors is not None:
                    headers.append((b"access-control-allow-origin", cors.encode("latin-1")))
                if reachable:
                    headers.append((b"vary", b"Origin"))
                message = {**message, "headers": headers}
            await send(message)

        return sending

    async def _refuse(self, seen: _Seen, send: Send, found: _Refused) -> None:
        refusal = blank_secrets(found.refusal)
        body = Refusals(refusals=[refusal]).model_dump_json().encode("utf-8")
        headers = [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode("ascii")),
            *found.headers,
        ]
        wrapped = self._headers(seen, send, _Started())
        await wrapped({"type": "http.response.start", "status": found.status, "headers": headers})
        await wrapped({"type": "http.response.body", "body": body})

    async def _forward(self, scope: Scope, seen: _Seen, receive: Receive, send: Send) -> None:
        started = _Started()
        wrapped = self._headers(seen, send, started)
        limit = self._limit(seen)
        received = 0

        async def counted() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(cast(bytes, message.get("body", b"")))
                if received > limit:
                    raise BodyTooLarge(limit)
            return message

        try:
            await self.app(scope, counted, wrapped)
        except BodyTooLarge:
            if started.started:
                raise
            await self._refuse(seen, send, self._too_large(limit))
        except ClientDisconnect:
            if started.started:
                raise
        except Exception:
            if started.started:
                raise
            _logger.exception("An error answering %s %s", seen.method, blank_path(seen.path))
            failed = _refused(RefusalCode.INTERNAL_ERROR, "The server failed")
            await self._refuse(seen, send, failed)


class _Started:
    """Whether a response has started, so that nothing is sent twice."""

    def __init__(self) -> None:
        self.started = False


async def _close(receive: Receive, send: Send) -> None:
    """Refuse a WebSocket connection before it is accepted (a policy violation, 1008)."""
    await receive()
    await send({"type": "websocket.close", "code": 1008})


__all__ = [
    "CORS_PREFIXES",
    "CSRF_PATH",
    "UPLOAD_PATH",
    "BodyTooLarge",
    "Policy",
    "RateClass",
    "RequestProtection",
]

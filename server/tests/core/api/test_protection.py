"""Request protection on every router (SPEC §13.4, §14, D255–D260): Host and Origin checks against
rebinding, CORS off unless configured, rate limits per client, body limits, security headers, and
nothing that reaches the application around them."""

from collections.abc import Callable
from typing import Any

import anyio
import pytest
from starlette.types import Message, Receive, Scope, Send
from starlette.websockets import WebSocketDisconnect

from aibi.core.api.app import create_app
from aibi.core.api.protection import Policy, RequestProtection
from aibi.core.api.serve import services_of
from aibi.core.schema.pack_api import PackRegistry

Built = Any
MakeApp = Any
Asgi = Callable[..., Any]
OWN = "http://127.0.0.1:8000"
WS = "ws://127.0.0.1:8000"
EVERY_ROUTER = ["/api/health", "/operator/datasets", "/operator/csrf", "/mcp/x", "/nope"]


def codes(response: Any) -> list[str]:
    return [refusal["code"] for refusal in response.json()["refusals"]]


def valid(built: Built, **extra: str) -> dict[str, str]:
    """A token, a name and the CSRF token: all an operator request needs."""
    return built.headers(**{"Aibi-CSRF": built.csrf(), **extra})


# --- Host and Origin (D256, D257) ------------------------------------------------------------


@pytest.mark.parametrize("path", EVERY_ROUTER)
@pytest.mark.parametrize(
    "headers",
    [
        {"Host": "attacker.example:8000"},
        {"Host": ""},
        {"Host": "localhost."},
        {"Host": "127.0.0.1.attacker.example"},
        {"Host": "ada@127.0.0.1:8000"},
    ],
    ids=["attacker", "empty", "trailing dot", "suffix", "userinfo"],
)
def test_a_host_this_server_does_not_answer_to_is_refused_on_every_router(
    built: Built, path: str, headers: dict[str, str]
) -> None:
    response = built.client.get(path, headers=valid(built, **headers))
    assert response.status_code == 400
    assert codes(response) == ["HOST_NOT_ALLOWED"]


@pytest.mark.parametrize("path", EVERY_ROUTER)
def test_a_request_without_a_host_is_refused(built: Built, asgi: Asgi, path: str) -> None:
    answer = asgi(built.app, path, headers=[])
    assert answer.status == 400
    assert b"HOST_NOT_ALLOWED" in answer.body
    repeated = [(b"host", b"127.0.0.1:8000"), (b"host", b"127.0.0.1:8000")]
    assert asgi(built.app, path, headers=repeated).status == 400


@pytest.mark.parametrize("path", EVERY_ROUTER)
@pytest.mark.parametrize(
    "origin",
    [
        "http://attacker.example:8000",
        "null",
        "http://127.0.0.1:9000",
        "https://127.0.0.1:8000",
        "http://127.0.0.1:8000/",
        "http://localhost.:8000",
    ],
)
def test_an_origin_this_server_does_not_allow_is_refused_on_every_router(
    built: Built, path: str, origin: str
) -> None:
    response = built.client.get(path, headers=valid(built, Origin=origin))
    assert response.status_code == 403
    assert codes(response) == ["ORIGIN_NOT_ALLOWED"]


@pytest.mark.parametrize("path", EVERY_ROUTER)
def test_a_repeated_origin_or_a_cross_site_request_without_one_is_refused(
    built: Built, path: str
) -> None:
    headers = [(name, value) for name, value in valid(built).items()]
    repeated = built.client.get(path, headers=[*headers, ("Origin", OWN), ("Origin", OWN)])
    assert codes(repeated) == ["ORIGIN_NOT_ALLOWED"]
    for site in ("cross-site", "same-site"):
        response = built.client.get(path, headers=valid(built, **{"Sec-Fetch-Site": site}))
        assert response.status_code == 403
        assert codes(response) == ["ORIGIN_NOT_ALLOWED"]


@pytest.mark.parametrize(
    ("host", "origin"),
    [
        ("127.0.0.1:8000", OWN),
        ("[::1]:8000", "http://[::1]:8000"),
        ("LOCALHOST:8000", "http://localhost:8000"),
        ("aibi.lab.example.org", "https://aibi.lab.example.org"),
        ("127.0.0.1:1234", OWN),
    ],
)
def test_the_loopback_names_and_configured_hosts_are_allowed(
    make_app: MakeApp, host: str, origin: str
) -> None:
    built = make_app(
        server={
            "hostnames": ["aibi.lab.example.org"],
            "public_origins": ["https://aibi.lab.example.org"],
        }
    )
    for path in ("/api/health", "/operator/datasets", "/mcp/x"):
        response = built.client.get(path, headers=valid(built, Host=host, Origin=origin))
        assert response.status_code == 200, (path, response.text)


def test_a_websocket_with_a_bad_host_or_origin_is_refused(built: Built) -> None:
    for headers in ({"Host": "attacker.example"}, {"Origin": "http://attacker.example"}):
        with (
            pytest.raises(WebSocketDisconnect) as refused,
            built.client.websocket_connect(WS + "/mcp/ws", headers=headers),
        ):
            pass
        assert refused.value.code == 1008
    with (
        built.client.websocket_connect(WS + "/mcp/ws") as accepted,
        pytest.raises(WebSocketDisconnect),
    ):
        accepted.receive_text()


def test_no_websocket_reaches_the_operator_router(built: Built) -> None:
    with (
        pytest.raises(WebSocketDisconnect) as refused,
        built.client.websocket_connect(WS + "/operator/datasets", headers=built.headers()),
    ):
        pass
    assert refused.value.code == 1008


# --- CORS (D258) --------------------------------------------------------------------------------

PREFLIGHT = {
    "Access-Control-Request-Method": "POST",
    "Access-Control-Request-Headers": "content-type",
}


def test_cors_is_off_by_default(built: Built) -> None:
    for path in EVERY_ROUTER:
        response = built.client.get(path, headers=valid(built, Origin=OWN))
        assert not any(name.startswith("access-control-") for name in response.headers), path
        preflight = built.client.options(path, headers={"Origin": OWN, **PREFLIGHT})
        assert preflight.status_code == 403, path
        assert not any(name.startswith("access-control-") for name in preflight.headers)


def test_a_preflight_for_a_method_but_get_or_post_is_refused(make_app: MakeApp) -> None:
    other = "https://notebook.example.org"
    built = make_app(server={"cors_origins": [other]})
    for method in ("PUT", "DELETE", "PATCH"):
        asked = {**PREFLIGHT, "Access-Control-Request-Method": method}
        refused = built.client.options("/api/health", headers={"Origin": other, **asked})
        assert refused.status_code == 403, method
        assert "access-control-allow-methods" not in refused.headers


def test_a_cors_origin_s_cross_site_request_is_answered(make_app: MakeApp) -> None:
    other = "https://notebook.example.org"
    built = make_app(server={"cors_origins": [other]})
    fetch = {"Origin": other, "Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "cors"}
    for path in ("/api/health", "/mcp/x"):
        answered = built.client.get(path, headers=fetch)
        assert answered.status_code == 200, (path, answered.text)
        assert answered.headers["access-control-allow-origin"] == other


def test_configured_cors_origins_reach_the_api_and_mounts_but_never_the_operator_router(
    make_app: MakeApp,
) -> None:
    other = "https://notebook.example.org"
    built = make_app(server={"cors_origins": [other]})
    preflight = built.client.options("/api/health", headers={"Origin": other, **PREFLIGHT})
    assert preflight.status_code == 204
    assert preflight.headers["access-control-allow-origin"] == other
    assert preflight.headers["vary"] == "Origin"
    assert "access-control-allow-credentials" not in preflight.headers
    asked = {**PREFLIGHT, "Access-Control-Request-Headers": "authorization"}
    assert (
        built.client.options("/api/health", headers={"Origin": other, **asked}).status_code == 403
    )
    answered = built.client.get("/mcp/x", headers={"Origin": other})
    assert answered.headers["access-control-allow-origin"] == other
    plain = built.client.get("/api/health")
    assert plain.headers["vary"] == "Origin"
    assert "access-control-allow-origin" not in plain.headers
    for path in ("/operator/datasets", "/operator/csrf"):
        refused = built.client.options(path, headers={"Origin": other, **PREFLIGHT})
        assert refused.status_code == 403
        assert not any(name.startswith("access-control-") for name in refused.headers)
        direct = built.client.get(path, headers=valid(built, Origin=other))
        assert codes(direct) == ["ORIGIN_NOT_ALLOWED"]


# --- Rate limits (D259) -------------------------------------------------------------------------


def test_rate_limits_apply_per_client_on_every_router(make_app: MakeApp) -> None:
    slow = {"per_minute": 60, "burst": 2}
    built = make_app(server={"rates": {"api": slow, "operator": slow}})
    for path, name in (
        ("/api/health", "api_requests"),
        ("/operator/datasets", "operator_requests"),
    ):
        for _ in range(2):
            assert built.client.get(path, headers=built.headers()).status_code == 200
        limited = built.client.get(path, headers=built.headers())
        assert limited.status_code == 429
        assert codes(limited) == ["LIMIT_EXCEEDED"]
        assert limited.json()["refusals"][0]["limit"] == {"name": name, "max": 60}
        assert limited.headers["retry-after"] == "1"
        another = built.other(client=("10.0.0.2", 1))
        assert another.get(path, headers=built.headers()).status_code == 200
    built.clock.now += 1
    assert built.client.get("/api/health").status_code == 200


def test_an_ipv6_client_is_limited_by_its_slash_64(make_app: MakeApp) -> None:
    slow = {"per_minute": 60, "burst": 1}
    built = make_app(server={"rates": {"api": slow}})
    first = built.other(client=("2001:db8:1:2::1", 1))
    assert first.get("/api/health").status_code == 200
    same = built.other(client=("2001:db8:1:2:ffff:ffff:ffff:ffff", 1))
    assert same.get("/api/health").status_code == 429
    other = built.other(client=("2001:db8:1:3::1", 1))
    assert other.get("/api/health").status_code == 200
    mapped = built.other(client=("::ffff:10.0.0.9", 1))
    assert mapped.get("/api/health").status_code == 200
    plain = built.other(client=("10.0.0.9", 1))
    assert plain.get("/api/health").status_code == 429


def test_refused_hosts_and_origins_consume_no_rate(make_app: MakeApp) -> None:
    built = make_app(server={"rates": {"api": {"per_minute": 60, "burst": 2}}})
    for _ in range(5):
        assert built.client.get("/api/health", headers={"Host": "evil.example"}).status_code == 400
        assert built.client.get("/api/health", headers={"Origin": "http://e.x"}).status_code == 403
    assert [built.client.get("/api/health").status_code for _ in range(3)] == [200, 200, 429]


def test_repeated_bad_tokens_are_limited_per_client_until_the_bucket_refills(
    make_app: MakeApp,
) -> None:
    built = make_app(server={"rates": {"token_failures": {"per_minute": 60, "burst": 3}}})
    wrong = {**built.headers(), "Authorization": "Bearer nothing"}
    answered = [built.client.get("/operator/datasets", headers=wrong) for _ in range(5)]
    assert [response.status_code for response in answered] == [401, 401, 401, 429, 429]
    assert answered[-1].json()["refusals"][0]["limit"] == {"name": "token_failures", "max": 60}
    assert answered[-1].headers["retry-after"] == "1"
    another = built.other(client=("10.0.0.3", 1))
    assert another.get("/operator/datasets", headers=wrong).status_code == 401
    built.clock.now += 1
    assert built.client.get("/operator/datasets", headers=wrong).status_code == 401


def test_no_failure_of_any_client_refuses_the_valid_token(make_app: MakeApp) -> None:
    built = make_app(server={"rates": {"token_failures": {"per_minute": 1, "burst": 1}}})
    for headers in ({}, {"Authorization": "Bearer nothing"}, {"Authorization": "Basic x"}):
        for _ in range(10):
            assert built.client.get("/operator/datasets", headers=headers).status_code in (401, 429)
    assert codes(built.client.get("/operator/datasets")) == ["LIMIT_EXCEEDED"]
    for _ in range(3):
        assert built.client.get("/operator/datasets", headers=built.headers()).status_code == 200


def test_requests_without_the_token_take_nothing_from_the_operator_bucket(
    make_app: MakeApp,
) -> None:
    built = make_app(server={"rates": {"operator": {"per_minute": 60, "burst": 2}}})
    for _ in range(5):
        assert built.client.get("/operator/datasets").status_code == 401
    answered = [built.client.get("/operator/datasets", headers=built.headers()) for _ in range(3)]
    assert [response.status_code for response in answered] == [200, 200, 429]
    assert answered[-1].json()["refusals"][0]["limit"] == {"name": "operator_requests", "max": 60}


def test_preflights_take_from_the_api_bucket(make_app: MakeApp) -> None:
    other = "https://notebook.example.org"
    built = make_app(
        server={"cors_origins": [other], "rates": {"api": {"per_minute": 60, "burst": 2}}}
    )
    answered = [
        built.client.options("/api/health", headers={"Origin": other, **PREFLIGHT})
        for _ in range(3)
    ]
    assert [response.status_code for response in answered] == [204, 204, 429]
    assert answered[-1].json()["refusals"][0]["limit"] == {"name": "api_requests", "max": 60}


# --- Bodies (D260) --------------------------------------------------------------------------------


def test_a_declared_length_over_the_limit_is_refused_before_the_body_is_read(
    make_app: MakeApp, asgi: Asgi
) -> None:
    built = make_app(server={"max_body_bytes": 64})
    headers = [
        (b"host", b"127.0.0.1:8000"),
        (b"authorization", f"Bearer {built.token}".encode()),
        (b"aibi-operator", b"ada"),
        (b"content-type", b"application/json"),
        (b"content-length", b"65"),
    ]
    answer = asgi(built.app, "/operator/datasets/d/session/open", method="POST", headers=headers)
    assert answer.status == 413
    assert answer.received == 0
    assert b'"request_bytes"' in answer.body
    huge = [*headers[:-1], (b"content-length", b"9" * 40)]
    assert asgi(built.app, "/api/health", headers=huge).status == 413


def test_a_streamed_body_over_the_limit_is_refused(make_app: MakeApp) -> None:
    built = make_app(server={"max_body_bytes": 64})

    def chunks() -> Any:
        yield b'{"release": 1, "padding": "'
        yield b"x" * 100
        yield b'"}'

    response = built.client.post(
        "/operator/datasets/d/withdraw",
        content=chunks(),
        headers={**built.headers(), "Content-Type": "application/json"},
    )
    assert response.status_code == 413
    assert response.json()["refusals"][0]["limit"] == {"name": "request_bytes", "max": 64}


def test_an_upload_may_reach_import_bytes_and_no_more(make_app: MakeApp) -> None:
    built = make_app(server={"max_body_bytes": 16}, imports={"import_bytes": 40})
    headers = {**built.headers(), "Content-Type": "application/octet-stream"}
    fits = built.client.post(
        "/operator/datasets/d/uploads",
        params={"extension": "csv"},
        content=b"a\n" * 20,
        headers=headers,
    )
    assert fits.status_code == 200, fits.text

    def chunks() -> Any:
        yield b"a\n" * 15
        yield b"a\n" * 15

    over = built.client.post(
        "/operator/datasets/d/uploads",
        params={"extension": "csv"},
        content=chunks(),
        headers={**headers, "Content-Length": "30"},
    )
    assert over.status_code == 413
    assert over.json()["refusals"][0]["limit"] == {"name": "request_bytes", "max": 40}
    temporary = built.root / "data" / "uploads.tmp"
    assert list(temporary.iterdir()) == []
    assert len(list((built.root / "data" / "uploads" / "d").iterdir())) == 1


def test_an_upload_cut_short_leaves_nothing_behind(built: Built, asgi: Asgi) -> None:
    headers = [
        (b"host", b"127.0.0.1:8000"),
        (b"authorization", f"Bearer {built.token}".encode()),
        (b"aibi-operator", b"ada"),
        (b"content-type", b"application/octet-stream"),
        (b"content-length", b"100"),
    ]
    messages: list[Message] = [
        {"type": "http.request", "body": b"a,b\n1,2\n", "more_body": True},
        {"type": "http.disconnect"},
    ]
    scope_path = "/operator/datasets/d/uploads"
    answer = asgi(
        built.app,
        scope_path,
        method="POST",
        query=b"extension=csv",
        headers=headers,
        messages=messages,
    )
    assert answer.status is None
    assert list((built.root / "data" / "uploads.tmp").iterdir()) == []
    assert not (built.root / "data" / "uploads" / "d").exists()


# --- Headers, lifespan and mounts (D255) ----------------------------------------------------------


def test_every_response_carries_the_security_headers(make_app: MakeApp) -> None:
    built = make_app()
    for path in EVERY_ROUTER:
        response = built.client.get(path, headers=built.headers())
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["referrer-policy"] == "no-referrer"
        assert response.headers["cross-origin-resource-policy"] == "same-origin"
        assert "strict-transport-security" not in response.headers
        if path == "/mcp/x":
            assert response.headers["content-security-policy"] == "default-src 'self'"
        else:
            assert response.headers["content-security-policy"] == (
                "default-src 'none'; frame-ancestors 'none'"
            )
        operator = path.startswith("/operator")
        assert (response.headers.get("cache-control") == "no-store") == operator
    refused = built.client.get("/operator/datasets")
    assert refused.headers["cache-control"] == "no-store"
    secured = make_app(name="tls", tls=True)
    assert secured.client.get("/api/health").headers["strict-transport-security"] == (
        "max-age=31536000"
    )


def test_lifespan_passes_through_and_other_scopes_are_refused(built: Built) -> None:
    seen: list[str] = []

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        seen.append(scope["type"])

    policy = Policy.of(built.config)
    middleware = RequestProtection(app, policy=policy)

    async def nothing() -> Message:
        return {"type": "lifespan.startup"}

    async def ignore(message: Message) -> None:
        return None

    anyio.run(middleware, {"type": "lifespan"}, nothing, ignore)
    assert seen == ["lifespan"]
    with pytest.raises(RuntimeError, match="lifespan, HTTP and WebSocket"):
        anyio.run(middleware, {"type": "telepathy"}, nothing, ignore)
    assert seen == ["lifespan"]


def test_an_attribution_smuggled_into_the_scope_is_dropped(built: Built, asgi: Asgi) -> None:
    seen: list[Scope] = []

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        seen.append(scope)
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    middleware = RequestProtection(app, policy=Policy.of(built.config))

    async def smuggling(scope: Scope, receive: Receive, send: Send) -> None:
        await middleware({**scope, "aibi.operator": "operator:mallory"}, receive, send)

    assert asgi(smuggling, "/api/health").status == 204
    assert "aibi.operator" not in seen[0]


async def _mounted(scope: Scope, receive: Receive, send: Send) -> None:
    raise AssertionError("never called")  # pragma: no cover


def test_mounts_cannot_shadow_the_routers(built: Built) -> None:
    policy = Policy.of(built.config)
    services = services_of(built.config, built.store, PackRegistry((), core_version="0.0.1"))
    for path in ("/", "/api", "/api/x", "/operator", "/operator/x", "mcp", "/mcp/"):
        with pytest.raises(ValueError, match="mount"):
            create_app(policy, services, mounts={path: _mounted})


def test_an_unexpected_error_is_answered_by_the_middleware_and_logged(
    built: Built, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def failing() -> list[str]:
        raise RuntimeError("a detail the client must not see")

    monkeypatch.setattr(built.store, "datasets", failing)
    response = built.client.get("/operator/datasets", headers=built.headers())
    assert response.status_code == 500
    assert codes(response) == ["INTERNAL_ERROR"]
    assert "detail" not in response.text
    assert "Traceback" not in response.text
    assert response.headers["cache-control"] == "no-store"
    assert any("a detail" in (record.exc_text or "") for record in caplog.records)


def test_an_unexpected_error_s_log_line_blanks_a_path_segment_of_a_secret_s_shape(
    built: Built, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    handle = "ses_" + "h" * 43

    def failing(dataset: str) -> list[Any]:
        raise RuntimeError("a detail the client must not see")

    monkeypatch.setattr(built.store, "labels", failing)
    response = built.client.get(
        f"/operator/datasets/d/descriptors/{handle}", headers=built.headers()
    )
    assert response.status_code == 500
    logged = [record.getMessage() for record in caplog.records]
    assert "An error answering GET /operator/datasets/d/descriptors/<secret>" in logged
    assert not any(handle in line for line in logged)


def test_an_unexpected_error_s_log_blanks_a_secret_its_message_quotes(
    built: Built, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def failing() -> list[str]:
        raise RuntimeError(f"quoting {built.token} and {built.token.replace('_', '%5F')}")

    monkeypatch.setattr(built.store, "datasets", failing)
    response = built.client.get("/operator/datasets", headers=built.headers())
    assert response.status_code == 500
    assert "RuntimeError: quoting <secret> and <secret>" in caplog.text
    assert built.token not in caplog.text
    assert built.token[5:] not in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


@pytest.mark.parametrize(
    "path",
    [
        "//operator/datasets",
        "/./operator/datasets",
        "/api/../operator/datasets",
        "/OPERATOR/datasets",
        "/operator/datasets/",
        "/operator//datasets",
        "/operatordatasets",
        "operator/datasets",
        "http://127.0.0.1:8000/operator/datasets",
    ],
)
def test_no_spelling_of_a_path_reaches_an_operator_route_without_the_token(
    built: Built, asgi: Asgi, path: str
) -> None:
    answer = asgi(built.app, path)
    assert answer.status in (401, 404), (path, answer.status, answer.body)
    assert answer.headers[b"x-content-type-options"] == b"nosniff"

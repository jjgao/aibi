"""Taking over a session over HTTP (SPEC §11.2; D263, D267, D415): a take-over names the session
and the draft the operator was shown, which ``GET …/datasets/{d}`` shows, and a browser's must,
as request protection's record of the request says; a mismatch is a ``CONFLICT`` that issues no
handle and leaves the holder's working. Also: the dataset's state shows a draft that differs from
its base (R2), and the router's own guard on ``GET /operator/csrf`` refuses a request that
carries an attribution and no CSRF token (N11), which request protection never lets through."""

from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.types import Receive, Scope, Send

from aibi.core.api.errors import install
from aibi.core.classify import CLASSIFIED_KEY, Classified
from aibi.core.operator.auth import OPERATOR_KEY
from aibi.core.operator.router import operator_router

Server = Any
OWN = "http://127.0.0.1:8000"
RELABEL = {"op": "set", "descriptor": "sites", "pointer": "/label", "value": "Field sites"}
TAKE_OVER = "/operator/datasets/d/session/take-over"


def codes(response: Any) -> list[str]:
    return [refusal["code"] for refusal in response.json()["refusals"]]


def browser(server: Server, **extra: str) -> dict[str, str]:
    csrf = server.get("/operator/csrf", headers={"Origin": OWN}).json()["csrf"]
    return {"Origin": OWN, "Aibi-CSRF": csrf, **extra}


def shown(server: Server) -> dict[str, Any]:
    state = server.get("/operator/datasets/d").json()["session"]
    return {"session": state["session"], "expected": state["draft"]}


def test_a_browsers_take_over_names_the_session_and_the_draft_it_was_shown(
    server: Server, sites: Path
) -> None:
    server.import_("d", sites)
    held = server.open("d")
    headers = browser(server)
    for body in ({}, {"session": held["session"]}, {"expected": held["draft"]}):
        refused = server.post(TAKE_OVER, body, operator="Grace Hopper", headers=headers)
        assert refused.status_code == 422, body
        assert codes(refused) == ["INVALID_VALUE"]
    metadata = {"Sec-Fetch-Site": "same-origin", "Aibi-CSRF": headers["Aibi-CSRF"]}
    assert server.post(TAKE_OVER, {}, headers=metadata).status_code == 422
    taken = server.post(TAKE_OVER, shown(server), operator="Grace Hopper", headers=headers)
    assert taken.status_code == 200, taken.text
    assert taken.json()["handle"] != held["handle"]
    assert server.change("d", held, RELABEL).status_code == 409


def test_a_take_over_of_a_draft_changed_since_it_was_shown_issues_no_handle(
    server: Server, sites: Path
) -> None:
    server.import_("d", sites)
    held = server.open("d")
    seen = shown(server)
    changed = server.change("d", held, RELABEL)
    assert changed.status_code == 200
    headers = browser(server)
    refused = server.post(TAKE_OVER, seen, operator="Grace Hopper", headers=headers)
    assert refused.status_code == 409
    assert codes(refused) == ["CONFLICT"]
    assert "handle" not in refused.text
    assert held["handle"] not in refused.text
    again = server.change("d", held, {**RELABEL, "value": "x"}, expected=changed.json()["draft"])
    assert again.status_code == 200, again.text
    other = {"session": seen["session"] + 1, "expected": again.json()["draft"]}
    assert server.post(TAKE_OVER, other, headers=headers).status_code == 409


def test_a_client_that_is_not_a_browser_may_take_over_without_them(
    server: Server, sites: Path
) -> None:
    server.import_("d", sites)
    held = server.open("d")
    assert server.post(TAKE_OVER, {"session": held["session"]}).status_code == 422
    stale = {"session": held["session"], "expected": "sha256:" + "0" * 64}
    assert server.post(TAKE_OVER, stale).status_code == 409
    assert server.post(TAKE_OVER, shown(server)).status_code == 200
    assert server.post(TAKE_OVER, {}).status_code == 200


def test_a_record_that_says_not_a_browser_is_dropped_before_a_take_over(
    server: Server, sites: Path
) -> None:
    server.import_("d", sites)
    server.open("d")
    headers = browser(server)

    async def claiming(scope: Scope, receive: Receive, send: Send) -> None:
        record = Classified(kind="operator", root="", browser=False)
        await server.app({**scope, CLASSIFIED_KEY: record}, receive, send)

    with TestClient(claiming, base_url=OWN) as client:
        refused = client.post(TAKE_OVER, json={}, headers={**server.headers(), **headers})
        assert refused.status_code == 422, refused.text


def test_a_request_without_the_protection_record_is_taken_for_a_browser(
    server: Server, sites: Path
) -> None:
    """The router fails closed: no record, no claim that the caller is not a browser, so a
    take-over that names nothing is refused as a browser's is (D415)."""
    server.import_("d", sites)
    held = server.open("d")
    app = FastAPI()
    install(app)
    app.include_router(operator_router(server.services))

    async def attributed(scope: Scope, receive: Receive, send: Send) -> None:
        await app({**scope, OPERATOR_KEY: "operator:Ada"}, receive, send)

    with TestClient(attributed, base_url=OWN) as client:
        refused = client.post(TAKE_OVER, json={})
        assert refused.status_code == 422, refused.text
        assert codes(refused) == ["INVALID_VALUE"]
        alone = client.post(TAKE_OVER, json={"session": held["session"]})
        assert alone.status_code == 422, alone.text
    assert server.change("d", held, RELABEL).status_code == 200, "the handle was not voided"


def test_the_state_shows_a_draft_that_differs_from_its_base(server: Server, sites: Path) -> None:
    server.import_("d", sites)
    held = server.open("d")
    changed = server.change("d", held, RELABEL).json()["draft"]
    session = server.get("/operator/datasets/d").json()["session"]
    assert session["base"] == held["base"]
    assert session["draft"] == changed
    assert session["draft"] != session["base"]
    assert session["session"] == held["session"]
    assert "handle" not in session


def test_the_csrf_route_refuses_an_attributed_request_without_a_csrf_token(
    server: Server,
) -> None:
    app = FastAPI()
    install(app)
    app.include_router(operator_router(server.services))

    async def attributed(scope: Scope, receive: Receive, send: Send) -> None:
        await app({**scope, OPERATOR_KEY: "operator:Ada"}, receive, send)

    with TestClient(attributed, base_url=OWN) as client:
        answered = client.get("/operator/csrf")
        assert answered.status_code == 401, answered.text
        assert codes(answered) == ["TOKEN_REQUIRED"]

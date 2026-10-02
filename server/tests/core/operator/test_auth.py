"""The curator token, operator names and CSRF tokens (SPEC §11.2, D261–D263): every operator route
refuses a request without the token, names are recorded as the operator gave them, and a
browser's requests carry the CSRF token."""

import base64
import dataclasses
import hashlib
from typing import Any

import pytest
from fastapi.routing import iter_route_contexts
from fastapi.testclient import TestClient
from hypothesis import given
from hypothesis import strategies as st

from aibi.core.api.app import create_app
from aibi.core.api.protection import Policy
from aibi.core.operator.auth import (
    TOKEN_RE,
    attribution,
    bearer,
    blank_path,
    csrf_ok,
    csrf_token,
    encode_operator,
    hash_token,
    holds_token,
    is_loopback_host,
    new_token,
    token_digest,
    valid_name,
    verify,
)

Server = Any
OWN = "http://127.0.0.1:8000"


def operator_routes(server: Server) -> list[tuple[str, str]]:
    """(method, path) of every operator route, its parameters filled in."""
    found: list[tuple[str, str]] = []
    for route in iter_route_contexts(server.app.routes):
        if route.path and route.methods and route.path.startswith("/operator"):
            path = (
                route.path.replace("{dataset}", "d")
                .replace("{descriptor}", "sites")
                .replace("{proposal}", "1")
            )
            found.extend((method, path) for method in sorted(route.methods))
    return found


def codes(response: Any) -> list[str]:
    return [refusal["code"] for refusal in response.json()["refusals"]]


# --- The curator token (D261) -----------------------------------------------------------------


def test_a_token_is_256_bits_in_its_form_and_its_hash_is_sha256() -> None:
    token = new_token()
    assert TOKEN_RE.fullmatch(token)
    assert token != new_token()
    assert hash_token(token) == "sha256:" + hashlib.sha256(token.encode()).hexdigest()
    assert token_digest(hash_token(token)) == hashlib.sha256(token.encode()).digest()


def test_only_the_matching_token_verifies() -> None:
    token = new_token()
    digest = token_digest(hash_token(token))
    assert verify(token, digest)
    assert not verify(new_token(), digest)
    assert not verify(token[:-1] + ("A" if token[-1] != "A" else "B"), digest)


def test_a_value_outside_the_token_s_form_is_refused_whatever_its_hash() -> None:
    password = "correct horse battery staple"
    digest = hashlib.sha256(password.encode()).digest()
    assert not verify(password, digest)
    assert bearer(f"Bearer {password}") is None
    with pytest.raises(ValueError, match="not a curator token") as refused:
        hash_token(password)
    assert password not in str(refused.value)


def test_the_bearer_scheme_is_read_in_any_case_and_nothing_else_is() -> None:
    token = new_token()
    assert bearer(f"Bearer {token}") == token
    assert bearer(f"bearer  {token}") == token
    assert bearer(f"Basic {token}") is None
    assert bearer(token) is None
    assert bearer(f"Bearer {token} extra") is None


def test_the_server_refuses_a_configured_hash_of_a_value_outside_the_form(server: Server) -> None:
    password = "hunter2"
    digest = hashlib.sha256(password.encode()).digest()
    policy = dataclasses.replace(Policy.of(server.config), token_digest=digest)
    with TestClient(create_app(policy, server.services), base_url=OWN) as client:
        response = client.get(
            "/operator/datasets",
            headers={"Authorization": f"Bearer {password}", "Aibi-Operator": "ada"},
        )
    assert response.status_code == 401
    assert codes(response) == ["TOKEN_REQUIRED"]


def _without_token(server: Server) -> dict[str, dict[str, Any]]:
    basic = base64.b64encode(f"ada:{server.token}".encode()).decode()
    name = {"Aibi-Operator": "ada"}
    return {
        "no header": {"headers": name},
        "a wrong token": {"headers": {**name, "Authorization": f"Bearer {new_token()}"}},
        "a malformed one": {"headers": {**name, "Authorization": "Bearer aibi_short"}},
        "basic credentials": {"headers": {**name, "Authorization": f"Basic {basic}"}},
        "a query parameter": {"headers": name, "params": {"token": server.token}},
        "a cookie": {"headers": {**name, "Cookie": f"token={server.token}"}},
        "the token in the query as well": {
            "headers": {**name, "Authorization": f"Bearer {server.token}"},
            "params": {"token": server.token},
        },
        "the token percent-encoded in the query as well": {
            "headers": {**name, "Authorization": f"Bearer {server.token}"},
            "params": {"t": server.token.replace("aibi_", "aibi%5F")},
        },
        "the token in a cookie as well": {
            "headers": {
                **name,
                "Authorization": f"Bearer {server.token}",
                "Cookie": f"token={server.token}",
            }
        },
        "the token percent-encoded in a cookie as well": {
            "headers": {
                **name,
                "Authorization": f"Bearer {server.token}",
                "Cookie": "token=" + server.token.replace("aibi_", "%61ibi%5F"),
            }
        },
        "two authorizations": {
            "headers": [
                ("Aibi-Operator", "ada"),
                ("Authorization", f"Bearer {server.token}"),
                ("Authorization", f"Bearer {server.token}"),
            ]
        },
    }


def test_operator_routes_refuse_requests_without_the_token(server: Server) -> None:
    routes = [*operator_routes(server), ("GET", "/operator/x"), ("POST", "/operator")]
    assert len(routes) > 15
    for method, path in routes:
        for case, request in _without_token(server).items():
            response = server.client.request(method, path, **request)
            assert response.status_code == 401, (method, path, case)
            assert codes(response) == ["TOKEN_REQUIRED"], (method, path, case)
            assert response.headers["www-authenticate"] == 'Bearer realm="aibi-operator"'
            assert server.token not in response.text


def test_a_token_in_the_path_is_refused_as_written_or_percent_encoded(server: Server) -> None:
    for written in (
        server.token,
        server.token.replace("_", "%5F"),
        server.token.replace("_", "%255F"),
    ):
        response = server.get(f"/operator/datasets/{written}")
        assert response.status_code == 401, written
        assert codes(response) == ["TOKEN_REQUIRED"]


def test_a_token_is_found_in_a_url_as_written_or_decoded_up_to_three_times() -> None:
    token = new_token().encode()
    assert holds_token(b"/x/" + token)
    assert holds_token(b"/x?t=" + token.replace(b"_", b"%5F"))
    assert holds_token(token.replace(b"_", b"%25255F"))
    assert not holds_token(token.replace(b"_", b"%2525255F"))
    assert not holds_token(b"/operator/datasets/d?release=1")


def test_a_longer_word_holding_a_token_s_shape_in_a_url_is_a_name() -> None:
    token = b"aibi_" + b"t" * 43
    assert holds_token(b"/x/" + token + b"/y")
    assert holds_token(b"/x?q=" + token + b"&r=1")
    assert not holds_token(b"/operator/datasets/d/descriptors/t.my" + token)
    assert not holds_token(b"/operator/datasets/d/descriptors/t." + token + b"_2024")
    assert not holds_token(b"/x?q=aaa_" + token)
    assert not holds_token(b"/x?q=" + token + b"-")
    assert not holds_token(b"/x?q=" + token + b"_v2")


def test_the_configured_token_is_found_in_a_url_wherever_it_sits() -> None:
    token = new_token()
    digest = token_digest(hash_token(token))
    for written in (f"/x?q=aaa_{token}", f"/x?q={token}-", f"s=x_{token}_y", f"/x/a%5F{token}"):
        assert holds_token(written.encode(), digest), written
        assert not holds_token(written.encode())
    other = new_token()
    assert not holds_token(f"/x?q=aaa_{other}".encode(), digest)
    assert not holds_token(b"/x/aibi_aibi_" + b"t" * 43, digest)


def test_the_configured_token_touching_a_base64url_character_is_refused(server: Server) -> None:
    for path in (
        f"/operator/datasets?x=aaa_{server.token}",
        f"/operator/datasets?x={server.token}-",
    ):
        response = server.get(path)
        assert response.status_code == 401, path
        assert codes(response) == ["TOKEN_REQUIRED"]
    cookie = server.get("/operator/datasets", headers={"Cookie": f"s=x_{server.token}"})
    assert cookie.status_code == 401
    other = "aaa_aibi_" + "t" * 43
    assert server.get(f"/operator/datasets?x={other}").status_code == 200


def test_a_logged_path_blanks_each_segment_of_a_secret_s_shape() -> None:
    token, handle = new_token(), "ses_" + "h" * 43
    assert blank_path(f"/a/{token}/b/x{handle}") == "/a/<secret>/b/<secret>"
    assert blank_path("/a/" + handle.replace("_", "%5F")) == "/a/<secret>"
    assert blank_path("/operator/datasets/d") == "/operator/datasets/d"
    assert blank_path("") == ""


def test_an_unknown_operator_path_needs_the_token_before_it_is_not_found(server: Server) -> None:
    assert server.client.get("/operator/x").status_code == 401
    found = server.get("/operator/x")
    assert found.status_code == 404
    assert codes(found) == ["NOT_FOUND"]


# --- Operator names (D262) --------------------------------------------------------------------


@pytest.mark.parametrize(
    "header",
    [
        None,
        "%0A",
        "Ada%0ALovelace",
        "%1B",
        "Ada%E2%80%A8",
        "Zo%C3",
        "Zo%GG",
        "Ada Lovelace",
        "Zoë".encode(),
        "a" * 201,
        "%C2%85",
        "%41da",
        "Zo%c3%ab",
        "Eve%E2%80%AEtsop",
        "Ada%E2%81%A6",
        "Ada%D8%9C",
    ],
    ids=repr,
)
def test_an_operator_request_needs_one_valid_name(server: Server, header: Any) -> None:
    headers: list[tuple[str, Any]] = [("Authorization", f"Bearer {server.token}")]
    if header is not None:
        headers.append(("Aibi-Operator", header))
    response = server.client.get("/operator/datasets", headers=headers)
    assert response.status_code == 400
    assert codes(response) == ["OPERATOR_REQUIRED"]


def test_a_repeated_name_is_refused(server: Server) -> None:
    headers = [
        ("Authorization", f"Bearer {server.token}"),
        ("Aibi-Operator", "ada"),
        ("Aibi-Operator", "grace"),
    ]
    assert server.client.get("/operator/datasets", headers=headers).status_code == 400


def test_a_name_of_200_characters_is_accepted(server: Server) -> None:
    assert server.get("/operator/datasets", operator="a" * 200).status_code == 200


def test_a_unicode_name_is_recorded_as_the_operator_gave_it(server: Server, sites: Any) -> None:
    response = server.post(
        "/operator/datasets/d/import",
        {"source": {"path": str(sites)}},
        headers={"Aibi-Operator": "Zo%C3%AB"},
    )
    assert response.status_code == 200, response.text
    actors = server.store.db.connection.execute("SELECT actor FROM audit").fetchall()
    assert actors == [("operator:Zoë",)]


_NAME_CHARACTERS = st.characters(
    blacklist_categories=("Cs",),
    blacklist_characters=[chr(c) for c in (*range(0x20), *range(0x7F, 0xA0), 0x2028, 0x2029)],
)


@given(st.text(_NAME_CHARACTERS, min_size=1, max_size=200).filter(valid_name))
def test_a_name_round_trips_through_its_header(name: str) -> None:
    encoded = encode_operator(name)
    assert encoded.isascii()
    assert attribution(encoded) == "operator:" + name


@given(st.text(max_size=300))
def test_reading_a_name_header_never_raises(header: str) -> None:
    found = attribution(header)
    assert found is None or found.startswith("operator:")


def test_a_name_with_bidi_formatting_is_not_a_name() -> None:
    assert valid_name("Ada")
    for point in (0x061C, 0x200E, 0x200F, 0x202A, 0x202E, 0x2066, 0x2069):
        assert not valid_name("Ada" + chr(point)), hex(point)


SECRET_NAMES = ("aibi_" + "t" * 43, "ses_" + "h" * 43, "Ada ses_" + "h" * 43 + " Lovelace")


def test_a_name_of_a_token_s_or_a_handle_s_shape_is_not_a_name() -> None:
    for name in SECRET_NAMES:
        assert not valid_name(name)
        assert attribution(encode_operator(name)) is None
    assert valid_name("ses_" + "h" * 42)


@pytest.mark.parametrize("kind", ["token", "handle", "inside"])
def test_an_operator_named_like_a_secret_is_refused_without_being_quoted(
    server: Server, kind: str
) -> None:
    name = server.token if kind == "token" else SECRET_NAMES[1 if kind == "handle" else 2]
    response = server.get("/operator/datasets", operator=name)
    assert response.status_code == 400
    assert codes(response) == ["OPERATOR_REQUIRED"]
    assert name not in response.text
    assert server.post("/operator/datasets/d/session/open", operator=name).status_code == 400


def test_a_name_holding_a_percent_encoded_secret_is_not_a_name(server: Server) -> None:
    for encoded in ("%5F", "%255F", "%25255F"):
        name = server.token.replace("_", encoded, 1)
        assert not valid_name(name)
        response = server.get("/operator/datasets", operator=name)
        assert response.status_code == 400
        assert codes(response) == ["OPERATOR_REQUIRED"]
    assert not valid_name("Ada " + "%73es_" + "h" * 43)
    assert valid_name("Ada 100%5F")


def test_loopback_hosts_are_localhost_127_8_and_ipv6_one() -> None:
    assert all(is_loopback_host(host) for host in ("localhost", "127.0.0.1", "127.9.9.9", "::1"))
    assert is_loopback_host("[::1]")
    assert not any(is_loopback_host(host) for host in ("10.0.0.5", "localhost.", "example.org"))


# --- CSRF (D263) --------------------------------------------------------------------------------


def test_the_csrf_token_is_keyed_by_the_process_and_the_token_hash() -> None:
    digest = token_digest(hash_token(new_token()))
    token = csrf_token(b"k" * 32, digest)
    assert csrf_ok(token, b"k" * 32, digest)
    assert not csrf_ok(token, b"j" * 32, digest)
    assert not csrf_ok(token, b"k" * 32, token_digest(hash_token(new_token())))
    assert not csrf_ok(None, b"k" * 32, digest)


def _csrf(server: Server) -> str:
    response = server.get("/operator/csrf", headers={"Origin": OWN})
    assert response.status_code == 200, response.text
    token: str = response.json()["csrf"]
    return token


def test_a_browser_s_unsafe_request_needs_the_csrf_token(server: Server, sites: Any) -> None:
    body = {"source": {"path": str(sites)}}
    path = "/operator/datasets/d/import"
    missing = server.post(path, body, headers={"Origin": OWN})
    assert missing.status_code == 403
    assert codes(missing) == ["CSRF_REQUIRED"]
    wrong = server.post(path, body, headers={"Origin": OWN, "Aibi-CSRF": "x" * 43})
    assert codes(wrong) == ["CSRF_REQUIRED"]
    right = server.post(path, body, headers={"Origin": OWN, "Aibi-CSRF": _csrf(server)})
    assert right.status_code == 200, right.text


@pytest.mark.parametrize(
    "fetch",
    [
        {"Sec-Fetch-Site": "same-origin", "Sec-Fetch-Mode": "cors"},
        {"Sec-Fetch-Mode": "cors"},
        {"Sec-Fetch-Dest": "empty"},
        {"Origin": OWN},
    ],
    ids=["site and mode", "mode alone", "dest alone", "origin alone"],
)
def test_a_browser_s_read_needs_the_csrf_token_too(server: Server, fetch: dict[str, str]) -> None:
    assert codes(server.get("/operator/datasets", headers=fetch)) == ["CSRF_REQUIRED"]
    allowed = server.get("/operator/datasets", headers={**fetch, "Aibi-CSRF": _csrf(server)})
    assert allowed.status_code == 200


def test_a_request_without_origin_or_fetch_metadata_needs_the_bearer_token_alone(
    server: Server,
) -> None:
    assert server.get("/operator/datasets").status_code == 200


def test_a_new_process_refuses_the_old_csrf_token(server: Server) -> None:
    old = _csrf(server)
    policy = Policy.of(server.config, csrf_key=b"j" * 32)
    with TestClient(create_app(policy, server.services), base_url=OWN) as client:
        response = client.get(
            "/operator/datasets", headers={**server.headers(), "Origin": OWN, "Aibi-CSRF": old}
        )
    assert codes(response) == ["CSRF_REQUIRED"]


def test_a_body_that_is_not_json_is_refused_before_it_is_read(server: Server) -> None:
    response = server.client.post(
        "/operator/datasets/d/session/open",
        content=b"{}",
        headers={**server.headers(), "Content-Type": "text/plain"},
    )
    assert response.status_code == 415
    assert codes(response) == ["UNSUPPORTED_MEDIA_TYPE"]
    upload = server.client.post(
        "/operator/datasets/d/uploads",
        params={"extension": "csv"},
        content=b"a\n1\n",
        headers={**server.headers(), "Content-Type": "application/json"},
    )
    assert upload.status_code == 415

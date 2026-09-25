"""Refusals over HTTP (SPEC §8.6, D265): the status of each code, the one error shape for
Starlette's own errors and FastAPI's parameter errors, and nothing of the request quoted."""

from typing import Any

import pytest

from aibi.core.api.errors import refused, status_of
from aibi.core.schema.output import text
from aibi.core.schema.refusals import Limit, Refusal, RefusalCode

Built = Any


def refusal(code: str, limit: str | None = None) -> Refusal:
    return Refusal(
        code=code,
        path=None,
        message=[text("x")],
        limit=None if limit is None else Limit(name=limit, max=1),
    )


@pytest.mark.parametrize(
    ("code", "status"),
    [
        ("HOST_NOT_ALLOWED", 400),
        ("OPERATOR_REQUIRED", 400),
        ("TOKEN_REQUIRED", 401),
        ("ORIGIN_NOT_ALLOWED", 403),
        ("CSRF_REQUIRED", 403),
        ("NOT_FOUND", 404),
        ("UNKNOWN_RELEASE", 404),
        ("UNKNOWN_PROPOSAL", 404),
        ("UNKNOWN_DATASET", 404),
        ("METHOD_NOT_ALLOWED", 405),
        ("DATASET_BUSY", 409),
        ("CONFLICT", 409),
        ("NO_SESSION", 409),
        ("NO_CHANGE", 409),
        ("DATASET_EXISTS", 409),
        ("RELEASE_WITHDRAWN", 409),
        ("ERASURE_BLOCKED", 409),
        ("UNSUPPORTED_MEDIA_TYPE", 415),
        ("INTERNAL_ERROR", 500),
        ("INVALID_JSON", 422),
        ("PATH_NOT_CONFINED", 422),
        ("KEY_NOT_UNIQUE", 422),
        ("birds.NO_PROTOCOL", 422),
    ],
)
def test_each_code_has_its_status(code: str, status: int) -> None:
    assert status_of(refusal(code)) == status


@pytest.mark.parametrize(
    ("limit", "status"),
    [
        ("request_bytes", 413),
        ("operator_requests", 429),
        ("api_requests", 429),
        ("token_failures", 429),
        ("concurrent_imports", 503),
        ("nesting_depth", 422),
        ("import_bytes", 422),
        ("reader_workers", 422),
        (None, 422),
    ],
)
def test_a_limit_s_status_is_by_its_name(limit: str | None, status: int) -> None:
    assert status_of(refusal("LIMIT_EXCEEDED", limit)) == status


def test_every_core_code_is_mapped_to_a_client_or_server_error() -> None:
    for code in RefusalCode:
        assert 400 <= status_of(refusal(code)) < 600


def test_a_refusal_response_has_the_first_refusal_s_status() -> None:
    response = refused([refusal("CONFLICT"), refusal("INVALID_VALUE")])
    assert response.status_code == 409
    assert response.media_type == "application/json"
    busy = refused([refusal("LIMIT_EXCEEDED", "concurrent_imports")])
    assert (busy.status_code, busy.headers["retry-after"]) == (503, "30")


def test_a_path_parameter_is_named_not_echoed(built: Built) -> None:
    response = built.client.get("/operator/datasets/Not-An-Id", headers=built.headers())
    assert response.status_code == 422
    [found] = response.json()["refusals"]
    assert found["code"] == "INVALID_VALUE"
    assert "dataset" in found["message"][0]["text"]
    assert "Not-An-Id" not in response.text
    query = built.client.get(
        "/operator/datasets/d/queue", params={"release": "zero-x"}, headers=built.headers()
    )
    assert query.status_code == 422
    assert "zero-x" not in query.text
    assert "release" in query.text


def test_a_bad_handle_in_a_body_is_refused_without_being_quoted(built: Built) -> None:
    handle = "ses_" + "q" * 44
    body = {"handle": handle, "expected": "sha256:" + "0" * 64}
    response = built.client.post(
        "/operator/datasets/d/session/discard", json=body, headers=built.headers()
    )
    assert response.status_code == 422
    assert response.json()["refusals"][0]["path"] == "/handle"
    assert handle not in response.text
    wrong = {"handle": handle[:-1], "expected": "sha256:" + "0" * 64}
    unknown = built.client.post(
        "/operator/datasets/d/session/discard", json=wrong, headers=built.headers()
    )
    assert (unknown.status_code, unknown.json()["refusals"][0]["code"]) == (
        404,
        "UNKNOWN_DATASET",
    )
    assert handle[:-1] not in unknown.text


def test_starlette_s_own_errors_are_refusals(built: Built) -> None:
    missing = built.client.get("/nowhere")
    assert missing.json()["refusals"][0]["code"] == "NOT_FOUND"
    method = built.client.post("/api/health")
    assert method.status_code == 405
    [found] = method.json()["refusals"]
    assert found["code"] == "METHOD_NOT_ALLOWED"
    assert [a["text"] for a in found["alternatives"]] == ["GET"]
    assert method.headers["allow"] == "GET"
    trailing = built.client.get("/api/health/")
    assert trailing.status_code == 404

"""What the operator router exchanges (SPEC §11.2, D260–D269): request bodies read by
``load_request`` with paths into the body as written, the unions' own alternatives and named
limits, and models that quote no secret."""

import hashlib
import json
import secrets
from collections.abc import Callable
from typing import Any

import pytest

from aibi.core.importers.uploads import EXTENSIONS
from aibi.core.schema.export import SCHEMAS
from aibi.core.schema.limits import MAX_CHANGE_EDITS
from aibi.core.schema.loading import load_request
from aibi.core.schema.operator import (
    UPLOAD_EXTENSIONS,
    Empty,
    EraseRequest,
    ImportRequest,
    PathSource,
    SessionChange,
    SessionEnd,
    UploadSource,
    WithdrawRequest,
    stored_secrets,
)
from aibi.core.schema.output import data, text
from aibi.core.schema.refusals import Refusal, RefusalCode, blank, blank_secrets, holds_secret

HANDLE = "ses_" + "h" * 43
DRAFT = "sha256:" + "0" * 64
LABEL = {"op": "set", "descriptor": "t", "pointer": "/label", "value": "T"}


def refusals(model: type[Any], body: object) -> list[tuple[str, str | None]]:
    written = body if isinstance(body, bytes) else json.dumps(body).encode()
    loaded = load_request(written, model)
    assert loaded.value is None
    return [(str(refusal.code), refusal.path) for refusal in loaded.refusals]


def test_a_valid_body_is_loaded() -> None:
    loaded = load_request(json.dumps({"source": {"path": "/data/x.csv"}}).encode(), ImportRequest)
    assert loaded.refusals == []
    assert loaded.value is not None
    assert loaded.value.source == PathSource(path="/data/x.csv")
    upload = {"source": {"upload": "a" * 64 + ".xlsx"}, "original_name": "Plots.xlsx"}
    uploaded = load_request(json.dumps(upload).encode(), ImportRequest).value
    assert uploaded is not None
    assert isinstance(uploaded.source, UploadSource)


def test_refusals_point_into_the_body_as_written() -> None:
    edits = [LABEL, LABEL, LABEL, {**LABEL, "pointer": "label"}]
    body = {"handle": HANDLE, "expected": DRAFT, "edits": edits}
    assert refusals(SessionChange, body) == [("INVALID_VALUE", "/edits/3/pointer")]
    assert refusals(ImportRequest, {"source": {"file": "x"}}) == [("UNKNOWN_KIND", "/source")]
    assert refusals(ImportRequest, {"source": {"path": "/x", "upload": "y"}}) == [
        ("UNKNOWN_KIND", "/source")
    ]
    assert refusals(ImportRequest, {"source": {"path": "/x", "extra": 1}}) == [
        ("UNKNOWN_MEMBER", "/source/extra")
    ]
    assert refusals(ImportRequest, {}) == [("MISSING_MEMBER", "/source")]


@pytest.mark.parametrize(
    ("model", "body", "member", "tags"),
    [
        (
            SessionChange,
            {"handle": HANDLE, "expected": DRAFT, "edits": [{"op": "move"}]},
            "/edits/0",
            ["set", "remove", "confirm", "put", "remove_descriptor", "accept"],
        ),
        (ImportRequest, {"source": "/x"}, "/source", ["path", "upload"]),
    ],
)
def test_an_unknown_kind_lists_its_union_s_own_members(
    model: type[Any], body: object, member: str, tags: list[str]
) -> None:
    loaded = load_request(json.dumps(body).encode(), model)
    [refusal] = loaded.refusals
    assert (refusal.code, refusal.path) == ("UNKNOWN_KIND", member)
    assert [getattr(item, "text", None) for item in refusal.alternatives] == tags


def test_json_problems_are_refused_before_validation() -> None:
    assert refusals(Empty, b"{") == [("INVALID_JSON", None)]
    assert refusals(Empty, b'{"a": 1, "a": 2}') == [("DUPLICATE_KEY", "/a")]
    assert refusals(Empty, b'{"a": NaN}') == [("NON_FINITE_NUMBER", "/a")]
    assert refusals(Empty, b"\xef\xbb\xbf{}") == [("INVALID_JSON", None)]
    assert refusals(Empty, b"[]") == [("WRONG_TYPE", "")]
    nested = b'{"a": ' + b"[" * 65 + b"]" * 65 + b"}"
    loaded = load_request(nested, Empty)
    [refusal] = loaded.refusals
    assert refusal.code == "LIMIT_EXCEEDED"
    assert refusal.limit is not None
    assert refusal.limit.name == "nesting_depth"


def test_a_change_keeps_the_bounds_of_a_change_request() -> None:
    too_many = {"handle": HANDLE, "expected": DRAFT, "edits": [LABEL] * (MAX_CHANGE_EDITS + 1)}
    loaded = load_request(json.dumps(too_many).encode(), SessionChange)
    [refusal] = loaded.refusals
    assert (refusal.code, refusal.path) == ("LIMIT_EXCEEDED", "/edits")
    assert refusal.limit is not None
    assert (refusal.limit.name, refusal.limit.max) == ("change_edits", MAX_CHANGE_EDITS)
    assert refusals(SessionChange, {"handle": HANDLE, "expected": DRAFT, "edits": []}) == [
        ("INVALID_VALUE", "/edits")
    ]


def test_a_handle_is_in_its_form_and_never_quoted() -> None:
    long = HANDLE + "x"
    loaded = load_request(json.dumps({"handle": long, "expected": DRAFT}).encode(), SessionEnd)
    assert [(r.code, r.path) for r in loaded.refusals] == [("INVALID_VALUE", "/handle")]
    assert long not in loaded.refusals[0].model_dump_json()


def test_no_repr_of_an_end_of_session_shows_its_handle() -> None:
    ended = SessionEnd(handle=HANDLE, expected=DRAFT)
    assert HANDLE not in repr(ended)
    assert HANDLE not in str(ended)


@pytest.mark.parametrize("secret", [HANDLE, "aibi_" + "t" * 43], ids=["handle", "token"])
def test_a_member_name_of_a_secret_s_shape_is_blanked_in_its_refusal(secret: str) -> None:
    written = json.dumps({"handle": HANDLE, "expected": DRAFT, f"x{secret}y": 1}).encode()
    loaded = load_request(written, SessionEnd)
    assert [(r.code, r.path) for r in loaded.refusals] == [("UNKNOWN_MEMBER", "/x<secret>y")]
    assert secret not in loaded.refusals[0].model_dump_json()
    duplicated = load_request(b'{"%s": 1, "%s": 2}' % (secret.encode(), secret.encode()), Empty)
    assert [(r.code, r.path) for r in duplicated.refusals] == [("DUPLICATE_KEY", "/<secret>")]
    assert secret not in duplicated.refusals[0].model_dump_json()


def test_blanking_covers_a_refusal_s_path_message_and_alternatives() -> None:
    token = "aibi_" + "t" * 43
    written = Refusal(
        code=RefusalCode.INVALID_VALUE,
        path=f"/{HANDLE}",
        message=[text(f"not {token}"), data(HANDLE)],
        alternatives=[text(token), data(f"x{HANDLE}")],
    )
    blanked = blank_secrets(written)
    assert blanked.path == "/<secret>"
    assert blanked.message == [text("not <secret>"), data("<secret>")]
    assert blanked.alternatives == [text("<secret>"), data("x<secret>")]
    clean = Refusal(code=RefusalCode.INVALID_VALUE, path="/a", message=[text("b")])
    assert blank_secrets(clean) is clean


@pytest.mark.parametrize(
    "written", [f"/fields/aibi%5F{'t' * 43}", f"/ses%5F{'h' * 43}", f"/x/ses%255F{'h' * 43}"]
)
def test_blanking_covers_a_secret_s_shape_that_is_percent_encoded(written: str) -> None:
    refused = Refusal(
        code=RefusalCode.UNKNOWN_MEMBER,
        path=written,
        message=[text(f"no member {written} here"), data(written.rsplit("/", 1)[1])],
    )
    blanked = blank_secrets(refused)
    parent = written.rsplit("/", 1)[0]
    assert blanked.path == f"{parent}/<secret>"
    assert blanked.message == [text(f"no member {parent}/<secret> here"), data("<secret>")]
    assert blank("50% off, see /a%2Fb") == "50% off, see /a%2Fb"


def a_token() -> str:
    return "aibi_" + secrets.token_urlsafe(32)


def test_stored_text_is_refused_for_the_configured_token_wherever_it_sits() -> None:
    token = a_token()
    digest = hashlib.sha256(token.encode()).digest()
    named = ImportRequest.model_validate(
        {"source": {"path": "/x"}, "name": f"token_{token}", "original_name": f"a-{token}-b"}
    )
    assert [(r.code, r.path) for r in stored_secrets(named, digest)] == [
        ("INVALID_VALUE", "/name"),
        ("INVALID_VALUE", "/original_name"),
    ]
    assert stored_secrets(named) == []
    other = ImportRequest.model_validate({"source": {"path": "/x"}, "name": f"token_{a_token()}"})
    assert stored_secrets(other, digest) == []


def test_an_erasure_key_is_1_to_16_scalars() -> None:
    assert load_request(b'{"table": "t", "key": ["m01", 7, 1.5, true]}', EraseRequest).value
    assert refusals(EraseRequest, {"table": "t", "key": []}) == [("INVALID_VALUE", "/key")]
    assert refusals(EraseRequest, {"table": "t", "key": ["k"] * 17}) == [("LIMIT_EXCEEDED", "/key")]
    assert refusals(EraseRequest, {"table": "t", "key": [None, {"k": 1}]}) == [
        ("WRONG_TYPE", "/key/0"),
        ("WRONG_TYPE", "/key/1"),
    ]
    assert refusals(EraseRequest, {"table": "T", "key": ["k"]}) == [("INVALID_VALUE", "/table")]


def test_a_release_is_a_label_or_a_manifest_hash() -> None:
    assert load_request(b'{"release": 2}', WithdrawRequest).value == WithdrawRequest(release=2)
    assert refusals(WithdrawRequest, {"release": 0}) == [("INVALID_VALUE", "/release")]
    assert refusals(WithdrawRequest, {"release": True}) == [("WRONG_TYPE", "/release")]
    assert refusals(WithdrawRequest, {"release": "latest"}) == [("INVALID_VALUE", "/release")]


def test_every_length_cap_of_these_models_names_its_limit(
    length_caps: Callable[[object], list[tuple[str, bool]]],
) -> None:
    models = (ImportRequest, PathSource, UploadSource, EraseRequest, SessionEnd, WithdrawRequest)
    own = {model.__name__ for model in models} | {"SessionChange"}
    for model in (*models, SessionChange):
        caps = [(where, named) for where, named in length_caps(model) if where.split(".")[0] in own]
        assert [where for where, named in caps if not named] == [], model.__name__
    assert ("EraseRequest.key", True) in length_caps(EraseRequest)


def test_the_upload_extensions_are_the_upload_area_s() -> None:
    assert UPLOAD_EXTENSIONS == EXTENSIONS


def test_the_refusal_schema_has_the_new_codes() -> None:
    written = json.dumps(SCHEMAS["refusal.schema.json"]())
    for code in (
        "HOST_NOT_ALLOWED",
        "TOKEN_REQUIRED",
        "CSRF_REQUIRED",
        "LENGTH_REQUIRED",
        "INTERNAL_ERROR",
    ):
        assert f'"{code}"' in written


@pytest.mark.parametrize(
    "value",
    [
        HANDLE,
        f"see {HANDLE}.",
        f"/fields/{HANDLE}",
        f"({HANDLE})",
        "aibi_" + "t" * 43,
        "aibi%5F" + "t" * 43,
        f"x={HANDLE.replace('_', '%255F')}",
    ],
)
def test_input_is_refused_for_a_secret_s_shape_that_stands_alone(value: str) -> None:
    assert holds_secret(value)


@pytest.mark.parametrize(
    "value",
    [
        "courses_completed_before_enrollment_in_the_program_2024",
        "responses_2024_survey_final_version_cleaned_export_v3.csv",
        "addresses_" + "a" * 43,
        HANDLE + "h",
        "-" + HANDLE,
        "_" + HANDLE,
        HANDLE + "_v2",
        "aibi_" + "t" * 43 + "-x",
        "x" + "aibi_" + "t" * 43,
    ],
)
def test_a_longer_word_that_holds_a_secret_s_shape_is_not_refused_but_is_blanked(
    value: str,
) -> None:
    assert not holds_secret(value)
    assert "<secret>" in blank(value)

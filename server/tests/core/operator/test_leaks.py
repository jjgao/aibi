"""Secrets stay where they belong (SPEC §11.2, §14, D244, D261, D267, D269): after curation
through the CLI and over HTTP, the curator token, every session handle and an erasure's key are in
no app DB or WAL byte, blob, audit entry, log record, refusal or CLI output; a handle is in the
state file alone, and no ``repr`` shows a handle, a key, the token's digest or the CSRF key."""

import io
import logging
import zipfile
from pathlib import Path
from typing import Any

import pytest

from aibi.core.api.protection import Policy
from aibi.core.operator import cli
from aibi.core.operator.auth import new_token
from aibi.core.operator.client import OperatorClient
from aibi.core.schema.operator import EraseRequest, SessionChange, SessionOpened

Server = Any
MakeServer = Any
KEY = "site-00042"


def _files(root: Path) -> list[bytes]:
    return [path.read_bytes() for path in sorted(root.rglob("*")) if path.is_file()]


def test_secrets_are_nowhere_but_where_they_belong(
    make_server: MakeServer, surveys: Any, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    server = make_server(registry=surveys)
    state = tmp_path / "state"
    outputs: list[str] = []
    shown: list[str] = []

    def run(*argv: str, operator: str = "Ada Lovelace") -> int:
        out, err = io.StringIO(), io.StringIO()
        code = cli.main(
            list(argv),
            client=server.client,
            environ={
                "AIBI_TOKEN": server.token,
                "AIBI_OPERATOR": operator,
                "XDG_STATE_HOME": str(state),
            },
            stdout=out,
            stderr=err,
        )
        (shown if "--show-handle" in argv else outputs).append(out.getvalue() + err.getvalue())
        return code

    sites = b"site_id,name\ns1,North\n" + KEY.encode() + b",Hidden\n"
    archive = tmp_path / "survey.zip"
    with zipfile.ZipFile(archive, "w") as written:
        written.writestr("sites.csv", sites)
        written.writestr("visits.csv", b"visit_id,site_id\nv1,s1\nv2," + KEY.encode() + b"\n")
    assert run("import", "d", str(archive), "--upload", "--pack", "surveys") == 0
    assert run("session", "open", "d", "--show-handle") == 0
    first = shown[-1].split("handle ", 1)[1].strip()
    state_file = state / "aibi" / "sessions.json"
    assert first in state_file.read_text()
    assert run("session", "set", "d", "sites", "/label", "--text", "Sites") == 0
    assert run("session", "accept", "d", "1") == 0
    assert run("session", "publish", "d") == 0
    assert run("session", "take-over", "d") == 1
    assert run("session", "open", "d", "--show-handle") == 0
    assert run("session", "take-over", "d", "--show-handle", operator="Grace Hopper") == 0
    assert run("session", "set", "d", "visits", "/label", "--text", "V") == 0
    assert run("session", "discard", "d") == 0
    handles = [
        line.split("handle ", 1)[1].strip()
        for text in shown
        for line in text.splitlines()
        if line.strip().startswith("handle ")
    ]
    assert len(handles) == 3
    assert handles[0] == first

    refusals: list[str] = []
    opened = server.open("d")
    handles.append(opened["handle"])
    stale = server.change("d", {**opened, "handle": handles[0]}, {"op": "accept", "proposal": 2})
    malformed = server.change("d", {**opened, "handle": opened["handle"] + "x"})
    wrong = server.client.get(
        "/operator/datasets", headers={"Authorization": f"Bearer {new_token()}"}
    )
    erased = server.post("/operator/datasets/d/erase", {"table": "sites", "key": [KEY]})
    for response in (stale, malformed, wrong, erased):
        assert response.status_code >= 400
        refusals.append(response.text)

    secrets = [server.token, *handles]
    data = server.root / "data"
    stored = [path.read_bytes() for path in data.glob("app.db*")]
    blobs = _files(data / "blobs")
    audit = [row[0] for row in server.store.db.connection.execute("SELECT detail FROM audit")]
    records = [record.getMessage() for record in caplog.records]
    for secret in secrets:
        encoded = secret.encode()
        assert not any(encoded in content for content in stored + blobs), secret
        assert not any(secret in text for text in audit + records + refusals + outputs), secret
    for text in records + refusals:
        assert KEY not in text
    assert not any(handle in state_file.read_text() for handle in handles)
    for path in tmp_path.rglob("*"):
        if path.is_file() and path != state_file:
            content = path.read_bytes()
            assert not any(handle.encode() in content for handle in handles), path


def test_no_repr_shows_a_secret(server: Server) -> None:
    handle = "ses_" + "s" * 43
    opened = SessionOpened(
        dataset="d", session=1, base="sha256:" + "0" * 64, draft="sha256:" + "0" * 64, handle=handle
    )
    change = SessionChange.model_validate(
        {
            "handle": handle,
            "expected": "sha256:" + "0" * 64,
            "edits": [{"op": "accept", "proposal": 1}],
        }
    )
    erase = EraseRequest(table="sites", key=[KEY])
    policy = Policy.of(server.config, csrf_key=b"\x01" * 32)
    client = OperatorClient(server.client, token=server.token, operator="ada")
    for shown in (repr(opened), repr(change), str(change), repr(erase), repr(client)):
        assert handle not in shown
        assert KEY not in shown
        assert server.token not in shown
    written = repr(policy)
    assert policy.token_digest.hex() not in written
    assert repr(policy.token_digest) not in written
    assert repr(policy.csrf_key) not in written
    assert repr(server.services).count("aibi_") == 0

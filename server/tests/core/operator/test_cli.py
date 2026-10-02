"""The operator CLI (SPEC §11.2, D262, D267, D268): curation round trips through ``aibi`` against
the application in process and over TCP, the token, the operator's name, the session state file,
escaped output and exit codes."""

import io
import json
import os
import socket
import stat
import subprocess
import sys
import textwrap
import threading
import time
import zipfile
from pathlib import Path
from typing import Any

import httpx
import pytest
import uvicorn

from aibi.core.api.serve import uvicorn_config
from aibi.core.operator import cli
from aibi.core.schema.jsonio import parse_json

Server = Any
MakeServer = Any
Cli = Any
Ran = Any


def audit(server: Server, dataset: str) -> list[tuple[str, str]]:
    rows = server.store.db.connection.execute(
        "SELECT action, actor FROM audit WHERE dataset = ? ORDER BY id", (dataset,)
    ).fetchall()
    return [(str(row[0]), str(row[1])) for row in rows]


def done(ran: Ran) -> Ran:
    assert ran.code == 0, (ran.out, ran.err)
    return ran


# --- Round trips ----------------------------------------------------------------------------------


def test_a_full_curation_round_trip_through_the_cli(
    server: Server, library: Path, run_cli: Cli
) -> None:
    assert "Published lib @1" in done(run_cli("import", "lib", str(library))).out
    done(run_cli("session", "open", "lib"))
    assert run_cli.state_file.exists()
    done(run_cli("session", "set", "lib", "members", "/label", "--text", "Library members"))
    done(run_cli("session", "confirm", "lib", "members", "/fields/primary_key"))
    done(run_cli("session", "confirm", "lib", "rel:loans_loans.member_id", "--evidence", "Seen"))
    assert "Published lib @2" in done(run_cli("session", "publish", "lib")).out
    curated = {d.id: d for d in server.store.descriptors(server.store.resolve("lib").manifest)}
    assert curated["members"].label == "Library members"
    assert curated["members"].curation["/fields/primary_key"].status == "asserted"
    relationship = curated["rel:loans_loans.member_id"].curation
    assert {entry.status for entry in relationship.values()} == {"asserted"}
    assert {entry.evidence for entry in relationship.values()} == {"Seen"}
    assert {entry.by for entry in relationship.values()} == {"operator:Ada Lovelace"}
    assert "Withdrew @2 of lib" in done(run_cli("withdraw", "lib", "2")).out

    assert [(label.label, label.withdrawn) for label in server.store.labels("lib")] == [
        (1, False),
        (2, True),
    ]
    actions = audit(server, "lib")
    assert [action for action, _ in actions] == [
        "import",
        "open",
        "change",
        "change",
        "change",
        "publish",
        "withdraw",
    ]
    assert {actor for _, actor in actions} == {"operator:Ada Lovelace"}
    state = json.loads(run_cli.state_file.read_text())
    assert state == {"format": "aibi.cli-sessions/1", "sessions": {}}


def _zip(path: Path, files: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return path


def test_a_second_round_trip_with_uploads_proposals_and_a_take_over(
    make_server: MakeServer, make_cli: Any, surveys: Any, tmp_path: Path
) -> None:
    server = make_server(registry=surveys)
    run = make_cli(server, tmp_path / "ada")
    local = _zip(
        tmp_path / "field survey.zip",
        {"plots.csv": b"plot_id,area\np1,3\np2,5\n", "counts.csv": b"count_id,plot_id\nc1,p1\n"},
    )
    uploaded = done(run("upload", "d", str(local)))
    assert "Uploaded" in uploaded.out
    imported = done(run("import", "d", str(local), "--upload", "--pack", "surveys"))
    assert "Published d @1" in imported.out
    assert "proposals: 1, 2" in imported.out
    queue = done(run("queue", "d")).out
    assert "proposal 1: counts /definition by importer:surveys@1.0.0" in queue
    assert "proposal 2: plots /definition" in queue

    done(run("session", "open", "d"))
    done(run("session", "accept", "d", "1"))
    assert "Rejected proposal 2 of d" in done(run("reject", "d", "2")).out
    assert (
        "Rejected 0 open proposals of d; kept 1 that the open draft accepted and holds"
        in done(run("reject-all", "d", "--kind", "importer")).out
    )
    assert run("reject-all", "d").code == 2
    queued = done(run("queue", "d")).out
    assert "proposal 1: counts /definition by importer:surveys@1.0.0 (accepted in the draft)" in (
        queued
    )
    assert "proposal 2" not in queued

    grace = make_cli(server, tmp_path / "grace", operator="Grace Hopper")
    done(grace("session", "take-over", "d"))
    stale = run("session", "set", "d", "plots", "/label", "--text", "Plots")
    assert stale.code == 1
    assert "refused: CONFLICT" in stale.err
    assert "aibi session take-over d" in stale.err
    assert "Published d @2" in done(grace("session", "publish", "d")).out
    assert [p.id for p in server.store.db.proposals("d", status="accepted")] == [1]


def test_every_edit_command_changes_the_draft(
    server: Server, sites: Path, run_cli: Cli, tmp_path: Path
) -> None:
    done(run_cli("import", "d", str(sites)))
    done(run_cli("session", "open", "d"))
    done(run_cli("session", "remove", "d", "sites", "/fields/grain"))
    done(run_cli("session", "remove-descriptor", "d", "rel:visits.site_id"))
    descriptor = next(d for d in server.store.descriptors(server.store.resolve("d").manifest))
    written = {
        key: value
        for key, value in descriptor.model_dump(mode="json").items()
        if key not in ("version", "curation")
    }
    written["label"] = "The dataset"
    (tmp_path / "put.json").write_text(json.dumps(written))
    done(run_cli("session", "put", "d", str(tmp_path / "put.json")))
    change = {"edits": [{"op": "set", "descriptor": "visits", "pointer": "/label", "value": "V"}]}
    done(run_cli("session", "change", "d", "-", stdin=json.dumps(change)))
    done(run_cli("session", "set", "d", "visits.count", "/fields/units", '"birds"'))
    draft = server.store.resolve("d", "draft").manifest
    found = {d.id: d for d in server.store.descriptors(draft)}
    assert "rel:visits.site_id" not in found
    assert found["dataset"].label == "The dataset"
    assert found["visits"].label == "V"
    assert found["visits.count"].fields.units == "birds"
    assert "grain" not in found["sites"].fields.model_fields_set
    discarded = done(run_cli("session", "discard", "d"))
    assert "Discarded" in discarded.out


def test_a_file_that_is_not_utf_8_is_a_usage_error(
    server: Server, sites: Path, run_cli: Cli, tmp_path: Path
) -> None:
    done(run_cli("import", "d", str(sites)))
    done(run_cli("session", "open", "d"))
    written = tmp_path / "latin.json"
    written.write_bytes(b'{"edits": [], "label": "Caf\xe9"}')
    for action in ("change", "put"):
        ran = run_cli("session", action, "d", str(written))
        assert ran.code == 2, (action, ran.err)
        assert "is not UTF-8 text" in ran.err
        assert "Traceback" not in ran.err


# --- Erasure (D223, D269) -------------------------------------------------------------------------


def test_an_erasure_key_is_read_from_standard_input(
    server: Server, sites: Path, run_cli: Cli
) -> None:
    done(run_cli("import", "d", str(sites)))
    blocked = run_cli("erase", "d", "sites", stdin='["s3"]')
    assert blocked.code == 1
    assert "refused: ERASURE_BLOCKED" in blocked.err
    (sites / "sites.csv").write_bytes(b"site_id,name\ns1,North\ns2,South\n")
    (sites / "visits.csv").write_bytes(b"visit_id,site_id,count\nv1,s1,3\n")
    done(run_cli("reimport", "d", str(sites)))
    erased = done(run_cli("erase", "d", "sites", stdin='["s3"]\n'))
    assert erased.out.startswith("Erased from d: withdrew @1")
    assert "s3" not in erased.out + erased.err
    assert [label.withdrawn for label in server.store.labels("d")] == [True, False]


@pytest.mark.parametrize("written", ["", "s3", '"s3"', "[]", '{"key": ["s3"]}', '[["s3"]]', "[1"])
def test_an_erasure_key_that_is_not_a_json_array_of_values_is_a_usage_error(
    run_cli: Cli, written: str
) -> None:
    ran = run_cli("erase", "d", "sites", stdin=written)
    assert ran.code == 2
    assert "erasure key" in ran.err


def test_an_erasure_key_is_never_an_argument_nor_repeated(run_cli: Cli) -> None:
    for key in ("s3", '["s3"]'):
        ran = run_cli("erase", "d", "sites", key, stdin='["s3"]')
        assert ran.code == 2
        assert "unrecognized arguments, not repeated here" in ran.err
        assert "s3" not in ran.err


@pytest.mark.parametrize(
    ("argv", "kept"),
    [
        (("session", "publish", "d", "--handle", "s3cr3t"), "invalid choice"),
        (("queue", "d", "--release", "s3cr3t"), "invalid int value"),
        (("session", "set", "d", "sites", "/label", "--e=s3cr3t"), "ambiguous option"),
    ],
)
def test_a_usage_error_quoting_a_value_keeps_only_its_fixed_part(
    run_cli: Cli, argv: tuple[str, ...], kept: str
) -> None:
    transport = _NoRequest()
    with httpx.Client(transport=transport, base_url="http://127.0.0.1:8000") as client:
        ran = run_cli(*argv, client=client)
    assert ran.code == 2
    assert kept in ran.err
    assert "not repeated here; see --help" in ran.err
    assert "s3cr3t" not in ran.out + ran.err
    assert transport.sent == []


def _refusing(status: int, code: str) -> httpx.MockTransport:
    handle = "ses_" + "h" * 43

    def handler(request: httpx.Request) -> httpx.Response:
        refusal = {
            "code": code,
            "path": f"/fields/{handle}",
            "message": [{"text": f"Unknown member {handle}"}],
            "alternatives": [{"text": handle}],
        }
        return httpx.Response(status, json={"refusals": [refusal]})

    return httpx.MockTransport(handler)


@pytest.mark.parametrize(("status", "code"), [(422, "UNKNOWN_MEMBER"), (409, "CONFLICT")])
def test_a_refusal_holding_a_secret_s_shape_is_printed_blanked(
    run_cli: Cli, status: int, code: str
) -> None:
    handle = "ses_" + "h" * 43
    with httpx.Client(transport=_refusing(status, code), base_url="http://x") as client:
        ran = run_cli(
            "session",
            "set",
            "d",
            "sites",
            "/label",
            "--text",
            "x",
            "--expected",
            "sha256:" + "0" * 64,
            client=client,
            AIBI_HANDLE="ses_" + "a" * 43,
        )
    assert ran.code == 1
    assert f"{code} at /fields/<secret>: Unknown member <secret>" in ran.err
    assert handle not in ran.out + ran.err
    assert "h" * 43 not in ran.out + ran.err
    assert ("take-over" in ran.err) == (code == "CONFLICT")


def test_on_a_terminal_the_erasure_key_is_asked_for(
    server: Server, sites: Path, run_cli: Cli, tmp_path: Path
) -> None:
    done(run_cli("import", "d", str(sites)))
    asked: list[str] = []

    def prompt(question: str) -> str:
        asked.append(question)
        return '["s1"]'

    out, err = io.StringIO(), io.StringIO()
    code = cli.main(
        ["erase", "d", "sites"],
        client=server.client,
        environ={
            "AIBI_TOKEN": server.token,
            "AIBI_OPERATOR": "Ada",
            "XDG_STATE_HOME": str(tmp_path),
        },
        stdin=_Terminal(),
        stdout=out,
        stderr=err,
        prompt=prompt,
    )
    assert code == 1
    assert "ERASURE_BLOCKED" in err.getvalue()
    assert asked == ["Erasure key, a JSON array of the key's values: "]


# --- The token and the operator (D261, D262, D268) ------------------------------------------------


class _Terminal(io.StringIO):
    def isatty(self) -> bool:
        return True


def test_the_token_comes_from_the_environment_or_a_prompt_on_a_terminal(
    server: Server, run_cli: Cli, tmp_path: Path
) -> None:
    assert done(run_cli("status")).out == "No dataset has a release.\n"
    asked: list[str] = []
    out, err = io.StringIO(), io.StringIO()

    def prompt(question: str) -> str:
        asked.append(question)
        return server.token

    code = cli.main(
        ["status"],
        client=server.client,
        environ={"AIBI_OPERATOR": "Ada", "XDG_STATE_HOME": str(tmp_path)},
        stdin=_Terminal(),
        stdout=out,
        stderr=err,
        prompt=prompt,
    )
    assert (code, asked) == (0, ["Curator token: "])
    assert server.token not in out.getvalue() + err.getvalue()


def test_without_a_token_or_a_terminal_the_cli_stops_before_any_request(run_cli: Cli) -> None:
    ran = run_cli("status", token="")
    assert ran.code == 2
    assert "AIBI_TOKEN" in ran.err


def test_a_value_that_is_not_a_token_is_refused_without_being_repeated(run_cli: Cli) -> None:
    ran = run_cli("status", token="hunter2")
    assert ran.code == 2
    assert "hunter2" not in ran.err


def test_the_token_is_never_a_flag_and_never_printed(server: Server, run_cli: Cli) -> None:
    ran = run_cli("--token", server.token, "status")
    assert ran.code == 2
    assert server.token not in ran.out + ran.err
    handle = "ses_" + "h" * 43
    typo = run_cli("session", "publish", "d", "--handel", handle)
    assert typo.code == 2
    assert handle not in typo.out + typo.err
    for argv in (("status",), ("--json", "status"), ("session", "open", "nothing")):
        ran = run_cli(*argv)
        assert server.token not in ran.out + ran.err


def test_the_operator_is_named_by_flag_or_environment_and_never_defaulted(
    server: Server, sites: Path, run_cli: Cli
) -> None:
    missing = run_cli("status", operator=None)
    assert missing.code == 2
    assert "--operator" in missing.err
    assert run_cli("status", operator="bad\nname").code == 2
    done(run_cli("--operator", "Grace Hopper", "import", "d", str(sites), operator=None))
    assert audit(server, "d") == [("import", "operator:Grace Hopper")]


# --- Session state (D267) ------------------------------------------------------------------------


def test_the_state_file_is_private_and_the_flags_override_it(
    server: Server, sites: Path, run_cli: Cli
) -> None:
    done(run_cli("import", "d", str(sites)))
    opened = done(run_cli("--json", "session", "open", "d", "--show-handle"))
    handle = str(parse_json(opened.out)["handle"])  # type: ignore[index]
    path = run_cli.state_file
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    kept = json.loads(path.read_text())["sessions"]["http://127.0.0.1:8000"]["d"]
    assert kept["handle"] == handle
    first = kept["draft"]
    done(run_cli("session", "set", "d", "sites", "/label", "--text", "One"))
    path.write_text(json.dumps({"format": "aibi.cli-sessions/1", "sessions": {}}))
    setting = ("session", "set", "d", "sites", "/label", "--text", "Two")
    assert run_cli(*setting).code == 2
    stale = run_cli(*setting, "--handle", "-", "--expected", first, stdin=handle + "\n")
    assert stale.code == 1
    assert "CONFLICT" in stale.err
    draft = server.store.resolve("d", "draft").manifest
    done(run_cli(*setting, "--expected", draft, AIBI_HANDLE=handle))
    kept = json.loads(path.read_text())["sessions"]["http://127.0.0.1:8000"]["d"]
    assert kept == {"handle": handle, "draft": server.store.resolve("d", "draft").manifest}


def test_a_given_handle_and_expected_draft_override_the_state_file_s(
    server: Server, sites: Path, run_cli: Cli, make_cli: Any, tmp_path: Path
) -> None:
    done(run_cli("import", "d", str(sites)))
    done(run_cli("session", "open", "d"))
    grace = make_cli(server, tmp_path / "grace", operator="Grace Hopper")
    opened = done(grace("--json", "session", "take-over", "d", "--show-handle")).out
    handle = str(parse_json(opened)["handle"])  # type: ignore[index]
    kept = json.loads(run_cli.state_file.read_text())["sessions"]["http://127.0.0.1:8000"]["d"]
    setting = ("session", "set", "d", "sites", "/label")
    assert "CONFLICT" in run_cli(*setting, "--text", "Stale").err
    done(run_cli(*setting, "--text", "One", "--handle", "-", stdin=handle + "\n"))
    after = json.loads(run_cli.state_file.read_text())["sessions"]["http://127.0.0.1:8000"]["d"]
    assert after["handle"] == handle
    stale = run_cli(*setting, "--text", "Two", AIBI_HANDLE=kept["handle"])
    assert (stale.code, "CONFLICT" in stale.err) == (1, True)
    old = run_cli(*setting, "--text", "Two", "--expected", kept["draft"])
    assert (old.code, "CONFLICT" in old.err) == (1, True)
    done(run_cli(*setting, "--text", "Two"))


def test_a_handle_is_never_an_argument_and_one_not_in_its_form_is_refused(
    server: Server, sites: Path, run_cli: Cli
) -> None:
    done(run_cli("import", "d", str(sites)))
    opened = done(run_cli("--json", "session", "open", "d", "--show-handle")).out
    handle = str(parse_json(opened)["handle"])  # type: ignore[index]
    given = run_cli("session", "discard", "d", "--handle", handle)
    assert given.code == 2
    assert handle not in given.out + given.err
    for environ in ({"AIBI_HANDLE": "ses_short"}, {"AIBI_HANDLE": handle + "x"}):
        refused = run_cli("session", "discard", "d", **environ)
        assert refused.code == 2
        assert "not a session handle" in refused.err
        assert handle not in refused.err
    both = run_cli("session", "change", "d", "-", "--handle", "-", stdin=handle + "\n{}")
    assert both.code == 2
    assert "AIBI_HANDLE" in both.err


def test_on_a_terminal_the_handle_is_asked_for_without_echo(
    server: Server, sites: Path, run_cli: Cli, tmp_path: Path
) -> None:
    done(run_cli("import", "d", str(sites)))
    opened = done(run_cli("--json", "session", "open", "d", "--show-handle")).out
    handle = str(parse_json(opened)["handle"])  # type: ignore[index]
    draft = str(parse_json(opened)["draft"])  # type: ignore[index]
    asked: list[str] = []

    def prompt(question: str) -> str:
        asked.append(question)
        return handle

    out, err = io.StringIO(), io.StringIO()
    code = cli.main(
        ["session", "discard", "d", "--handle", "-", "--expected", draft],
        client=server.client,
        environ={
            "AIBI_TOKEN": server.token,
            "AIBI_OPERATOR": "Ada",
            "XDG_STATE_HOME": str(tmp_path / "elsewhere"),
        },
        stdin=_Terminal(),
        stdout=out,
        stderr=err,
        prompt=prompt,
    )
    assert (code, asked) == (0, ["Session handle: "]), err.getvalue()
    assert handle not in out.getvalue() + err.getvalue()
    assert server.store.db.open_session_of("d") is None


def test_an_existing_state_directory_is_made_private(
    server: Server, sites: Path, run_cli: Cli
) -> None:
    run_cli.state_file.parent.mkdir(parents=True, mode=0o755)
    os.chmod(run_cli.state_file.parent, 0o755)
    done(run_cli("import", "d", str(sites)))
    done(run_cli("session", "open", "d"))
    assert stat.S_IMODE(run_cli.state_file.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(run_cli.state_file.stat().st_mode) == 0o600


def test_a_session_command_without_a_handle_is_a_usage_error(run_cli: Cli) -> None:
    ran = run_cli("session", "publish", "d")
    assert ran.code == 2
    assert "open or take over" in ran.err


def test_a_damaged_state_file_is_reported(run_cli: Cli) -> None:
    run_cli.state_file.parent.mkdir(parents=True)
    run_cli.state_file.write_text("{")
    ran = run_cli("session", "publish", "d")
    assert ran.code == 2
    assert "not JSON" in ran.err


# --- Output (A6) ----------------------------------------------------------------------------------


def test_text_from_data_is_printed_escaped(server: Server, sites: Path, run_cli: Cli) -> None:
    done(run_cli("import", "d", str(sites)))
    done(run_cli("session", "open", "d"))
    label = "Evil\x1b[31m" + chr(0x202E) + "red"
    done(run_cli("session", "set", "d", "sites", "/label", "--text", label))
    shown = done(run_cli("show", "d", "sites", "--release", "draft")).out
    assert "\\u001b[31m" in shown
    assert "\\u202e" in shown
    assert all(chr(point) not in shown for point in (0x1B, 0x202E))
    assert cli.escaped("a\x1b[31mb" + chr(0x202E) + "\n") == "a\\x1b[31mb\\u202e\\x0a"


@pytest.mark.parametrize(
    "point",
    [
        *(0x00, 0x1F, 0x7F, 0x80, 0x85, 0x9F, 0x061C, 0x200E, 0x200F, 0x2028, 0x2029),
        *range(0x202A, 0x202F),
        *range(0x2066, 0x206A),
    ],
    ids=hex,
)
def test_every_control_separator_and_bidi_character_is_escaped(point: int) -> None:
    written = "a" + chr(point) + "b"
    assert (
        cli.escaped(written)
        == "a" + (f"\\x{point:02x}" if point < 0x100 else f"\\u{point:04x}") + "b"
    )
    assert chr(point) not in cli.json_text(written)
    assert parse_json(cli.json_text(written)) == written


class _NoRequest(httpx.BaseTransport):
    def __init__(self) -> None:
        self.sent: list[httpx.Request] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.sent.append(request)
        return httpx.Response(599)


def test_an_operator_named_like_a_secret_is_a_usage_error_that_does_not_repeat_it(
    server: Server, run_cli: Cli
) -> None:
    transport = _NoRequest()
    with httpx.Client(transport=transport, base_url="http://127.0.0.1:8000") as client:
        for name in (server.token, "ses_" + "h" * 43, f"Ada {server.token}"):
            ran = run_cli("status", operator=name, client=client)
            assert ran.code == 2
            assert "holds no token or handle" in ran.err
            assert name not in ran.out + ran.err
            flagged = run_cli("--operator", name, "status", operator=None, client=client)
            assert flagged.code == 2
            assert name not in flagged.out + flagged.err
    assert transport.sent == []


@pytest.mark.parametrize(
    "argv",
    [
        ("session", "publish", "{handle}"),
        ("status", "{handle}"),
        ("show", "d", "{handle}"),
        ("show", "d", "dataset", "--release", "{token}"),
        ("session", "set", "d", "sites", "/fields/{token}", "--text", "x"),
        ("session", "set", "d", "sites", "/label", "--text", "{token}"),
        ("session", "set", "d", "sites", "/label", "--text", "{encoded}"),
        ("withdraw", "d", "{handle}"),
    ],
)
def test_an_argument_of_a_secret_s_shape_is_refused_before_any_request(
    server: Server, run_cli: Cli, argv: tuple[str, ...]
) -> None:
    handle = "ses_" + "h" * 43
    encoded = server.token.replace("_", "%255F")
    given = [part.format(handle=handle, token=server.token, encoded=encoded) for part in argv]
    transport = _NoRequest()
    with httpx.Client(transport=transport, base_url="http://127.0.0.1:8000") as client:
        ran = run_cli(*given, client=client)
    assert ran.code == 2
    assert "secrets are never arguments" in ran.err
    for secret in (handle, server.token):
        assert secret not in ran.out + ran.err
    assert transport.sent == []


LONG_FILE = "responses_2024_survey_final_version_cleaned_export_v3"
LONG_COLUMN = "courses_completed_before_enrollment_in_the_program_2024"
"""Ordinary names that hold a handle's shape inside a word: ``ses_`` and 43 or 47 more."""


def test_names_holding_a_secret_s_shape_inside_a_word_are_arguments_like_any_other(
    server: Server, run_cli: Cli, tmp_path: Path
) -> None:
    written = f"record_id,{LONG_COLUMN}\nr1,3\nr2,5\n".encode()
    (server.imports / f"{LONG_FILE}.csv").write_bytes(written)
    local = tmp_path / f"{LONG_FILE}.csv"
    local.write_bytes(written)
    assert (
        "Published things @1"
        in done(run_cli("import", "things", str(server.imports / f"{LONG_FILE}.csv"))).out
    )
    assert "Published others @1" in done(run_cli("import", "others", str(local), "--upload")).out
    column = f"{LONG_FILE}.{LONG_COLUMN}"
    assert LONG_COLUMN in done(run_cli("show", "things", column)).out
    done(run_cli("session", "open", "things"))
    done(run_cli("session", "set", "things", column, "/label", "--text", f"All {LONG_COLUMN}"))
    assert "Published things @2" in done(run_cli("session", "publish", "things")).out
    curated = {d.id: d for d in server.store.descriptors(server.store.resolve("things").manifest)}
    assert curated[column].label == f"All {LONG_COLUMN}"
    assert {d.id for d in server.store.descriptors(server.store.resolve("others").manifest)} >= {
        column
    }


def test_a_dataset_that_is_not_an_identifier_is_a_usage_error_that_does_not_repeat_it(
    run_cli: Cli,
) -> None:
    transport = _NoRequest()
    with httpx.Client(transport=transport, base_url="http://127.0.0.1:8000") as client:
        for dataset in ('["s1"]', "a__b", "Sites", "../x"):
            ran = run_cli("erase", dataset, "sites", stdin='["s1"]', client=client)
            assert ran.code == 2
            assert "a dataset is an identifier" in ran.err
            assert dataset not in ran.err
    assert transport.sent == []


def test_a_state_file_error_blanks_a_secret_s_shape_in_its_path(
    run_cli: Cli, make_cli: Any, server: Server, tmp_path: Path
) -> None:
    handle = "ses_" + "h" * 43
    run = make_cli(server, tmp_path / handle)
    run.state_file.parent.mkdir(parents=True)
    run.state_file.write_text("{")
    ran = run("session", "publish", "d")
    assert ran.code == 2
    assert "not JSON" in ran.err
    assert "<secret>" in ran.err
    assert handle not in ran.err


def test_a_name_with_bidi_formatting_is_a_usage_error(run_cli: Cli) -> None:
    ran = run_cli("--operator", "Eve" + chr(0x202E) + "tsop", "status", operator=None)
    assert ran.code == 2
    assert chr(0x202E) not in ran.err


def test_json_output_is_the_value_the_server_sent(
    server: Server, sites: Path, run_cli: Cli
) -> None:
    done(run_cli("import", "d", str(sites)))
    done(run_cli("session", "open", "d"))
    shifty = "Eve" + chr(0x202E) + chr(0x85)
    done(run_cli("session", "set", "d", "sites", "/label", "--text", shifty))
    answer = done(run_cli("--json", "show", "d", "sites", "--release", "draft")).out
    assert chr(0x202E) not in answer
    assert chr(0x85) not in answer
    shown = server.get("/operator/datasets/d/descriptors/sites", params={"release": "draft"})
    assert parse_json(answer) == shown.json()
    opened = done(run_cli("--json", "session", "take-over", "d")).out
    assert "handle" not in parse_json(opened)  # type: ignore[operator]


# --- Exit codes and transport (D268) --------------------------------------------------------------


def test_a_token_goes_over_plain_http_only_to_a_loopback_host(run_cli: Cli) -> None:
    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(500)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        ran = run_cli("--server", "http://10.0.0.5:8000", "status", client=client)
        assert ran.code == 2
        assert "https" in ran.err
        assert run_cli("--server", "ftp://127.0.0.1", "status", client=client).code == 2
    assert sent == []


def test_a_server_that_cannot_be_reached_exits_3(run_cli: Cli) -> None:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    ran = run_cli("--server", f"http://127.0.0.1:{port}", "status", client=None)
    assert ran.code == 3
    assert "cannot reach" in ran.err


def test_an_interrupted_command_says_the_server_may_finish(
    run_cli: Cli, monkeypatch: pytest.MonkeyPatch
) -> None:
    def interrupted(*args: Any, **kwargs: Any) -> Any:
        raise KeyboardInterrupt

    monkeypatch.setattr(cli.OperatorClient, "datasets", interrupted)
    ran = run_cli("status")
    assert ran.code == 130
    assert "may still finish" in ran.err


def test_the_cli_s_own_client_takes_no_proxy_and_follows_no_redirect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("ALL_PROXY", "http://127.0.0.1:9")
    with cli._http("http://127.0.0.1:8000", None) as http:  # pyright: ignore[reportPrivateUsage]
        assert http.trust_env is False
        assert http.follow_redirects is False


def test_a_redirect_is_not_followed_or_read_as_an_answer(run_cli: Cli) -> None:
    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(
            302, json={"datasets": []}, headers={"Location": "http://127.0.0.1:9/x"}
        )

    with httpx.Client(transport=httpx.MockTransport(handler), base_url="http://x") as client:
        ran = run_cli("status", client=client)
    assert ran.code == 3
    assert "redirect" in ran.err
    assert len(sent) == 1


def test_an_answer_that_cannot_be_decoded_is_unreachable_and_no_coding_is_asked_for(
    run_cli: Cli,
) -> None:
    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200, content=b"not gzip at all", headers={"Content-Encoding": "gzip"})

    with httpx.Client(transport=httpx.MockTransport(handler), base_url="http://x") as client:
        ran = run_cli("status", client=client)
    assert ran.code == 3
    assert "cannot read the server's answer (DecodingError)" in ran.err
    assert "Traceback" not in ran.err
    assert [request.headers["accept-encoding"] for request in sent] == ["identity"]


def test_an_answer_that_is_not_the_router_s_is_unreachable(run_cli: Cli) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html>proxy</html>")

    with httpx.Client(transport=httpx.MockTransport(handler), base_url="http://x") as client:
        assert run_cli("status", client=client).code == 3


def test_the_cli_loads_no_server_module() -> None:
    probe = textwrap.dedent(
        """
        import sys
        import aibi.core.operator.cli
        forbidden = ("aibi.core.store", "aibi.core.importers", "aibi.core.engine",
                     "aibi.core.api", "aibi.core.operator.router")
        frameworks = ("fastapi", "starlette", "uvicorn", "anyio")
        loaded = sorted(m for m in sys.modules if m.startswith(forbidden)
                        or m.split(".")[0] in frameworks)
        if loaded:
            raise SystemExit("the CLI loaded " + ", ".join(loaded))
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=False, timeout=120
    )
    assert result.returncode == 0, result.stderr


def test_the_cli_talks_to_a_real_server_over_tcp(
    server: Server, run_cli: Cli, caplog: pytest.LogCaptureFixture
) -> None:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    served = uvicorn.Server(uvicorn_config(server.config, server.app))
    thread = threading.Thread(target=served.run, kwargs={"sockets": [sock]}, daemon=True)
    caplog.set_level("DEBUG")
    thread.start()
    try:
        deadline = time.monotonic() + 20
        while not served.started:
            assert time.monotonic() < deadline, "uvicorn did not start"
            time.sleep(0.01)
        url = f"http://127.0.0.1:{port}"
        ran = run_cli("--server", url, "status", client=None)
        assert ran.code == 0, ran.err
        assert ran.out == "No dataset has a release.\n"
        with httpx.Client(trust_env=False) as client:
            attacker = client.get(f"{url}/api/health", headers={"Host": "attacker.example"})
            health = client.get(f"{url}/api/health", params={"token": server.token})
            handle = "ses_" + "h" * 43
            body = {"handle": handle, "expected": "sha256:" + "0" * 64}
            ended = client.post(
                f"{url}/operator/datasets/d/session/discard",
                json=body,
                headers={**server.headers(), "Content-Type": "application/json"},
            )
        assert attacker.status_code == 400
        assert attacker.json()["refusals"][0]["code"] == "HOST_NOT_ALLOWED"
        assert "server" not in health.headers
        assert (ended.status_code, ended.json()["refusals"][0]["code"]) == (404, "UNKNOWN_DATASET")
    finally:
        served.should_exit = True
        thread.join(30)
        sock.close()
    assert not thread.is_alive()
    server_side = ("uvicorn", "fastapi", "starlette", "aibi")
    records = [
        record.getMessage() for record in caplog.records if record.name.startswith(server_side)
    ]
    assert any("/api/health" in record for record in records)
    for secret in (server.token, handle, "token="):
        assert not any(secret in record for record in records), secret


def test_the_cli_prunes_the_log_and_says_what_an_unknown_issuance_is(
    server: Server, make_cli: Any, tmp_path: Path
) -> None:
    run = make_cli(server, tmp_path / "ada")
    pruned = done(run("prune", "--before", "2026-01-01T00:00:00Z")).out
    assert "Pruned 0 count_cohort issuances recorded before 2026-01-01T00:00:00.000000Z" in pruned
    unknown = run("issuance", "iss:" + "0" * 26)
    assert unknown.code == 1
    assert "NOT_FOUND" in unknown.out + unknown.err

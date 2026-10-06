"""Names that are not Unicode text at the operator's edge: the CLI's arguments and environment
(D268), and the HTTP routes, which answer 422 or 200 and never 500, logging no name (D266, D398).

A lone surrogate is what Python reads for an argument, an environment value or a file name in
bytes that are not UTF-8: no request can carry it, so the CLI refuses it before anything else."""

import argparse
import io
import logging
import os
import socket
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

import pytest
from tests.core.operator.harness import Cli, Server

from aibi.core.operator import cli

SURROGATE = "\udcff"
SENTINEL = "SENTINELNAME"


# --- The CLI's arguments ---------------------------------------------------------------------


def _parsers(
    parser: argparse.ArgumentParser, path: tuple[str, ...] = ()
) -> Iterator[tuple[tuple[str, ...], argparse.ArgumentParser]]:
    yield path, parser
    for action in parser._actions:  # pyright: ignore[reportPrivateUsage]
        if isinstance(action, argparse._SubParsersAction):  # pyright: ignore[reportPrivateUsage]
            choices: dict[str, argparse.ArgumentParser] = action.choices  # pyright: ignore
            for name, sub in choices.items():
                yield from _parsers(sub, (*path, name))


def _argument_kinds() -> list[tuple[tuple[str, ...], str]]:
    """Every argument any command takes a value for, by the command and the argument's name."""
    found: list[tuple[tuple[str, ...], str]] = []
    for path, parser in _parsers(cli._parser()):  # pyright: ignore[reportPrivateUsage]
        for action in parser._actions:  # pyright: ignore[reportPrivateUsage]
            if isinstance(action, argparse._HelpAction | argparse._SubParsersAction):  # pyright: ignore
                continue
            if action.nargs == 0:
                continue
            found.append((path, action.option_strings[0] if action.option_strings else action.dest))
    return found


def _argv(path: tuple[str, ...], argument: str, value: str) -> list[str]:
    if argument.startswith("--"):
        return [*path, argument, value]
    return [*path, value]


@pytest.mark.parametrize(
    ("path", "argument"),
    _argument_kinds(),
    ids=[f"{' '.join(p)}:{a}" for p, a in _argument_kinds()],
)
def test_an_argument_that_is_not_utf8_is_a_usage_error_naming_nothing(
    run_cli: Cli, path: tuple[str, ...], argument: str
) -> None:
    ran = run_cli(*_argv(path, argument, f"{SENTINEL}{SURROGATE}"))
    assert ran.code == cli.USAGE
    assert ran.err == "aibi: an argument is not UTF-8 text; give names and paths as UTF-8\n"
    assert ran.out == ""


@pytest.mark.parametrize(
    "argv",
    [
        ["\udcff"],
        ["status\udcff"],
        ["--x\udcff"],
        ["--server", "\udcff"],
        ["session", "o\udcff"],
        ["session", "--x\udcff"],
        ["status", "--x\udcff"],
        ["status", "--\udcff"],
        ["show", "d", "--release", "\udcff"],
    ],
    ids=repr,
)
def test_a_bad_command_subcommand_or_flag_name_is_a_usage_error_naming_nothing(
    run_cli: Cli, argv: list[str]
) -> None:
    """Where argparse would echo it (``invalid choice``, ``unrecognized arguments``)."""
    ran = run_cli(*argv)
    assert ran.code == cli.USAGE
    assert ran.err == "aibi: an argument is not UTF-8 text; give names and paths as UTF-8\n"
    assert ran.out == ""


def test_an_argument_with_a_noncharacter_is_sent_and_the_server_answers(run_cli: Cli) -> None:
    ran = run_cli("show", "d", "t\uffff")
    assert "not UTF-8" not in ran.err
    assert ran.code == cli.REFUSED


# --- The CLI's environment ----------------------------------------------------------------------


class Recording(Mapping[str, str]):
    """An environment that records every variable read from it."""

    def __init__(self, values: Mapping[str, str]) -> None:
        self.given = dict(values)
        self.read: set[str] = set()

    def __getitem__(self, key: str) -> str:
        self.read.add(key)
        return self.given[key]

    def __contains__(self, key: object) -> bool:
        if isinstance(key, str):
            self.read.add(key)
        return key in self.given

    def __iter__(self) -> Iterator[str]:
        raise AssertionError("the CLI reads variables by name, never the whole environment")

    def __len__(self) -> int:
        return len(self.given)


def _placeholder(action: argparse.Action) -> str:
    """A value that parses for a positional argument."""
    if action.type is int:
        return "1"
    if action.type is Path:
        return "/x.csv"
    return "-" if action.dest == "file" else "d"


def _commands() -> list[list[str]]:
    """Every command the CLI has, by reflection over its parser: its path and a value for each
    positional argument, so that a command added later is run by the test below."""
    found: list[list[str]] = []
    for path, parser in _parsers(cli._parser()):  # pyright: ignore[reportPrivateUsage]
        actions = parser._actions  # pyright: ignore[reportPrivateUsage]
        if any(isinstance(action, argparse._SubParsersAction) for action in actions):  # pyright: ignore
            continue
        found.append(
            [
                *path,
                *(
                    _placeholder(action)
                    for action in actions
                    if not action.option_strings and action.required
                ),
            ]
        )
    return found


COMMANDS = _commands()


def _run(server: Server, argv: list[str], environ: Mapping[str, str]) -> int:
    return cli.main(
        argv,
        client=server.client,
        environ=environ,
        stdin=io.StringIO("[]"),
        stdout=io.StringIO(),
        stderr=io.StringIO(),
    )


def test_the_commands_the_environment_test_runs_are_every_command() -> None:
    """The count and the names come from the parser's subparsers, walked here without ``_parsers``
    and ``_commands``, so that the test can fail when they stop reaching a command."""
    leaves: list[tuple[str, ...]] = []

    def walk(parser: argparse.ArgumentParser, path: tuple[str, ...]) -> None:
        subparsers = [
            action
            for action in parser._actions  # pyright: ignore[reportPrivateUsage]
            if isinstance(action, argparse._SubParsersAction)  # pyright: ignore
        ]
        if not subparsers:
            leaves.append(path)
        for action in subparsers:
            for name, child in action.choices.items():  # pyright: ignore
                walk(child, (*path, name))  # pyright: ignore

    walk(cli._parser(), ())  # pyright: ignore[reportPrivateUsage]
    assert len(leaves) >= 11
    assert len(COMMANDS) == len(leaves)
    for leaf in leaves:
        assert [argv for argv in COMMANDS if tuple(argv[: len(leaf)]) == leaf] != []


def test_every_variable_the_cli_reads_is_checked_or_a_path(server: Server, tmp_path: Path) -> None:
    """Every variable read through the environment the CLI is given. ``HOME`` is not among them:
    ``Path.home()`` reads the process's own, only for the default state directory, a local path
    never sent, so it is exempt like ``XDG_STATE_HOME`` (``cli.PATHS``)."""
    read: set[str] = set()
    for argv in COMMANDS:
        environ = Recording(
            {
                "AIBI_TOKEN": server.token,
                "AIBI_OPERATOR": "Ada",
                "AIBI_HANDLE": "ses_" + "a" * 43,
                "XDG_STATE_HOME": str(tmp_path),
            }
        )
        _run(server, argv, environ)
        read |= environ.read
    assert read == {*cli.SENT, *cli.PATHS}
    assert not set(cli.SENT) & set(cli.PATHS)


@pytest.mark.parametrize("variable", cli.SENT)
def test_a_sent_variable_that_is_not_utf8_is_a_usage_error_naming_no_value(
    server: Server, tmp_path: Path, variable: str
) -> None:
    environ = {
        "AIBI_TOKEN": server.token,
        "AIBI_OPERATOR": "Ada",
        "XDG_STATE_HOME": str(tmp_path),
        variable: f"{SENTINEL}{SURROGATE}",
    }
    err = io.StringIO()
    code = cli.main(
        ["status"],
        client=server.client,
        environ=environ,
        stdin=io.StringIO(),
        stdout=io.StringIO(),
        stderr=err,
    )
    assert code == cli.USAGE
    assert err.getvalue() == f"aibi: {variable} is not UTF-8 text\n"


@pytest.mark.parametrize("variable", cli.PATHS)
def test_a_path_variable_that_is_not_utf8_is_used_as_the_file_system_gives_it(
    server: Server, tmp_path: Path, variable: str
) -> None:
    state = os.path.join(os.fsencode(tmp_path), b"s\xff")
    environ = {
        "AIBI_TOKEN": server.token,
        "AIBI_OPERATOR": "Ada",
        variable: os.fsdecode(state),
    }
    assert _run(server, ["status"], environ) == cli.DONE


# --- The HTTP routes ----------------------------------------------------------------------------


def _no_name_logged(caplog: pytest.LogCaptureFixture) -> None:
    for record in caplog.records:
        written = record.getMessage() + (record.exc_text or "")
        assert SENTINEL not in written, written
        assert "\\udcff" not in written, written
        assert SURROGATE not in written, written


def _entries(directory: Path) -> None:
    """A directory with one entry of each kind whose name is not text, and one that is."""
    folder = os.fsencode(directory)
    with open(os.path.join(folder, b"ok.csv"), "wb") as file:
        file.write(b"a,b\n1,2\n")
    kinds = ((b".h", b".csv"), (b"m_", b".xls"), (b"m_", b".bin"), (b"m_", b".zip"))
    for stem, extension in kinds:
        name = stem + SENTINEL.encode() + b"\xff" + extension
        with open(os.path.join(folder, name), "wb") as file:
            file.write(b"x")
    os.mkdir(os.path.join(folder, b"dir" + SENTINEL.encode() + b"\xff"))
    os.symlink(b"ok.csv", os.path.join(folder, b"link" + SENTINEL.encode() + b"\xff"))
    os.mkfifo(os.path.join(folder, b"fifo" + SENTINEL.encode() + b"\xff"))
    sock = socket.socket(socket.AF_UNIX)
    try:
        sock.bind(os.path.join(folder, b"sock" + SENTINEL.encode() + b"\xff"))
    finally:
        sock.close()


def test_a_directory_with_entries_whose_names_are_not_text_imports_with_notes(
    server: Server, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    directory = server.imports / "entries"
    directory.mkdir()
    _entries(directory)
    response = server.post("/operator/datasets/e/import", {"source": {"path": str(directory)}})
    assert response.status_code == 200, response.text
    _no_name_logged(caplog)


@pytest.mark.parametrize(
    ("name", "content"),
    [
        (b"bad" + SENTINEL.encode() + b"\xff.csv", b"a,b\n1,2\n"),
        (b"bad" + SENTINEL.encode() + "\uffff".encode() + b".csv", b"a,b\n1,2\n"),
        (b"head.csv", ("a" + SENTINEL + "\uffff,b\n1,2\n").encode()),
    ],
    ids=["a file's name", "a noncharacter in a file's name", "a header"],
)
def test_a_name_that_is_not_text_is_a_422_never_a_500(
    server: Server, caplog: pytest.LogCaptureFixture, name: bytes, content: bytes
) -> None:
    caplog.set_level(logging.DEBUG)
    directory = server.imports / "one"
    directory.mkdir()
    with open(os.path.join(os.fsencode(directory), name), "wb") as file:
        file.write(content)
    response = server.post("/operator/datasets/o/import", {"source": {"path": str(directory)}})
    assert response.status_code == 422, response.text
    assert response.json()["refusals"][0]["code"] == "UNPARSEABLE_SOURCE"
    _no_name_logged(caplog)


def _upload(server: Server, extension: str) -> Any:
    return server.client.post(
        "/operator/datasets/u/uploads",
        params={"extension": extension},
        content=b"a,b\n1,2\n",
        headers={**server.headers(), "Content-Type": "application/octet-stream"},
    )


@pytest.mark.parametrize("extension", ["cs\uffff", "\ufdd0", "c\U0001fffe"])
def test_an_upload_s_extension_that_is_not_text_is_unsupported(
    server: Server, caplog: pytest.LogCaptureFixture, extension: str
) -> None:
    caplog.set_level(logging.DEBUG)
    response = _upload(server, extension)
    assert response.status_code == 422, response.text
    assert response.json()["refusals"][0]["code"] == "UNSUPPORTED_FORMAT"


def test_an_uploaded_file_whose_header_is_not_text_is_refused_when_imported(
    server: Server, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    uploaded = server.client.post(
        "/operator/datasets/h/uploads",
        params={"extension": "csv"},
        content=("a" + SENTINEL + "\uffff,b\n1,2\n").encode(),
        headers={**server.headers(), "Content-Type": "application/octet-stream"},
    )
    assert uploaded.status_code == 200, uploaded.text
    response = server.post(
        "/operator/datasets/h/import", {"source": {"upload": uploaded.json()["upload"]}}
    )
    assert response.status_code == 422, response.text
    _no_name_logged(caplog)


def test_an_original_name_with_a_lone_surrogate_is_invalid_json(server: Server) -> None:
    body = '{"source": {"upload": "x.csv"}, "original_name": "a\\udcff.csv"}'
    response = server.client.post(
        "/operator/datasets/j/import",
        content=body.encode(),
        headers={**server.headers(), "Content-Type": "application/json"},
    )
    assert response.status_code == 422, response.text
    assert response.json()["refusals"][0]["code"] == "INVALID_JSON"

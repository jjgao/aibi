"""Imports from named database connections through the operator router and the CLI (SPEC §11.2,
§13.1, §14, D305): a request names a connection of the configuration, never a host, a path or a
credential."""

import logging
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

LIBRARY = """
CREATE TABLE authors (id INTEGER PRIMARY KEY, name TEXT);
CREATE TABLE books (code TEXT PRIMARY KEY, author_id INTEGER REFERENCES authors (id));
CREATE VIEW named AS SELECT name FROM authors;
INSERT INTO authors VALUES (1, 'Ash'), (2, 'Brook');
INSERT INTO books VALUES ('b1', 1), ('b2', 2);
"""


def write_library(directory: Path) -> Path:
    path = directory / "library.sqlite"
    with sqlite3.connect(path) as connection:
        connection.executescript(LIBRARY)
    return path


@pytest.fixture
def with_library(make_server: Callable[..., Any]) -> Any:
    server = make_server(
        databases={
            "archive": {"kind": "sqlite", "path": "imports/library.sqlite"},
            "ledger": {"kind": "postgres", "url_env": "AIBI_LEDGER_URL"},
        }
    )
    write_library(server.imports)
    return server


def test_an_import_names_a_connection_and_publishes_its_snapshot(with_library: Any) -> None:
    answered = with_library.post(
        "/operator/datasets/library/import", {"source": {"connection": "archive"}}
    )
    assert answered.status_code == 200, answered.text
    assert answered.json()["label"] == 1
    shown = with_library.get("/operator/datasets/library/descriptors/dataset")
    assert shown.status_code == 200, shown.text
    fields = shown.json()["descriptor"]["fields"]
    assert fields["source"] == {"kind": "database", "location": "library.sqlite"}
    assert fields["name"] == "archive"


def test_an_unknown_connection_is_refused_listing_the_configured_ones(with_library: Any) -> None:
    answered = with_library.post(
        "/operator/datasets/library/import", {"source": {"connection": "elsewhere"}}
    )
    assert answered.status_code == 422, answered.text
    [refusal] = answered.json()["refusals"]
    assert refusal["code"] == "INVALID_VALUE"
    assert [alternative["text"] for alternative in refusal["alternatives"]] == [
        "archive",
        "ledger",
    ]


def test_a_request_can_give_a_connection_no_host_path_or_credential(with_library: Any) -> None:
    for source in (
        {"connection": "archive", "path": "/etc/passwd"},
        {"connection": "archive", "url": "postgresql://h/d"},
        {"connection": "postgresql://reader:hunter2@h/d"},
    ):
        answered = with_library.post("/operator/datasets/library/import", {"source": source})
        assert answered.status_code == 422, answered.text
        assert "hunter2" not in answered.text


def test_a_server_connection_s_credential_reaches_no_answer_or_log(
    with_library: Any, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("AIBI_LEDGER_URL", "postgresql://reader:hunter2@127.0.0.1:1/ledger")
    caplog.set_level(logging.DEBUG)
    answered = with_library.post(
        "/operator/datasets/ledger/import", {"source": {"connection": "ledger"}}
    )
    assert answered.status_code == 422, answered.text
    assert answered.json()["refusals"][0]["code"] == "UNPARSEABLE_SOURCE"
    assert "hunter2" not in answered.text
    assert "hunter2" not in caplog.text
    audit = with_library.get("/operator/datasets/ledger")
    assert "hunter2" not in audit.text


def test_a_server_connection_without_its_variable_is_refused_naming_the_variable(
    with_library: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("AIBI_LEDGER_URL", raising=False)
    answered = with_library.post(
        "/operator/datasets/ledger/import", {"source": {"connection": "ledger"}}
    )
    assert answered.status_code == 422, answered.text
    [refusal] = answered.json()["refusals"]
    assert refusal["code"] == "INVALID_VALUE"
    assert {"data": "AIBI_LEDGER_URL"} in refusal["message"]


def test_the_cli_imports_and_re_imports_a_named_connection(
    with_library: Any, make_cli: Callable[..., Any], tmp_path: Path
) -> None:
    run = make_cli(with_library, tmp_path / "state")
    ran = run("import", "library", "archive", "--connection")
    assert ran.code == 0, ran.err
    assert "Published library @1" in ran.out
    with sqlite3.connect(with_library.imports / "library.sqlite") as writer:
        writer.execute("INSERT INTO authors VALUES (3, 'Cole')")
    again = run("reimport", "library", "archive", "--connection")
    assert again.code == 0, again.err
    assert "Published library @2" in again.out
    both = run("import", "other", "archive", "--connection", "--upload")
    assert both.code == 2

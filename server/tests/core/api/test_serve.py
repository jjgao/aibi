"""``aibi-server`` (SPEC §14, D253, D254, D261, D268): uvicorn's settings, the store's lock, the
configuration check, and making and hashing curator tokens."""

import copy
import http.client
import io
import logging
import os
import socket
import sqlite3
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import uvicorn
import uvicorn.config

from aibi.core.api import logs, serve
from aibi.core.api.config import BASE, ServerConfig
from aibi.core.api.connections import GuardedProtocol
from aibi.core.api.serve import WithoutSecrets, main, run, services_of, uvicorn_config
from aibi.core.engine import worker
from aibi.core.operator.auth import TOKEN_RE, hash_token, new_token
from aibi.core.schema.pack_api import PackRegistry
from aibi.core.store.appdb import LOG_PAGE_BYTES
from aibi.core.store.store import APP_DB, Store, StoreLockedError

Write = Callable[..., Path]
HASH = "sha256:" + "b" * 64


def config_of(root: Path, **server: Any) -> ServerConfig:
    (root / "imports").mkdir(exist_ok=True)
    written = {
        "server": server,
        "curator": {"token_hash": HASH},
        "storage": {"data": "data", "imports": ["imports"]},
        "imports": {"concurrent": 3, "import_bytes": 99},
    }
    return ServerConfig.model_validate(written, context={BASE: root})


async def _app(scope: Any, receive: Any, send: Any) -> None:
    raise AssertionError("never called")  # pragma: no cover


def test_uvicorn_trusts_no_forwarded_header_and_names_no_server(tmp_path: Path) -> None:
    built = uvicorn_config(config_of(tmp_path, max_connections=7), _app)
    assert (built.host, built.port) == ("127.0.0.1", 8000)
    assert built.proxy_headers is False
    assert built.server_header is False
    assert built.limit_concurrency == 7
    assert built.ssl_certfile is None
    assert built.ssl_keyfile is None
    assert built.timeout_graceful_shutdown == 30
    assert built.ws == "none"
    assert isinstance(built.http, type)
    assert issubclass(built.http, GuardedProtocol)
    assert (
        built.http.request_head_seconds,
        built.http.max_connections_per_client,
        built.http.send_seconds,
    ) == (10, 1, 30)
    assert any(
        isinstance(found, WithoutSecrets) for found in logging.getLogger("uvicorn.access").filters
    )


def test_uvicorn_serves_tls_with_the_configured_files(tmp_path: Path) -> None:
    for name in ("cert.pem", "key.pem"):
        (tmp_path / name).write_text("x")
    config = config_of(tmp_path, bind="10.0.0.5", tls_certificate="cert.pem", tls_key="key.pem")
    built = uvicorn_config(config, _app)
    assert built.ssl_certfile == str(tmp_path / "cert.pem")
    assert built.ssl_keyfile == str(tmp_path / "key.pem")


def _access(path: str) -> logging.LogRecord:
    return logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        1,
        '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1:5", "GET", path, "1.1", 401),
        None,
    )


def test_the_access_log_leaves_query_strings_out() -> None:
    record = _access("/api/health?token=aibi_x")
    assert WithoutSecrets().filter(record)
    assert record.getMessage() == '127.0.0.1:5 - "GET /api/health HTTP/1.1" 401'
    other = logging.LogRecord("x", logging.INFO, __file__, 1, "plain %s", ("?q",), None)
    assert WithoutSecrets().filter(other)
    assert other.getMessage() == "plain ?q"


TOKEN = "aibi_" + "t" * 43
HANDLE = "ses_" + "h" * 43


@pytest.mark.parametrize(
    "segment",
    [
        TOKEN,
        HANDLE,
        f"x{TOKEN}y",
        TOKEN.replace("_", "%5F"),
        TOKEN.replace("_", "%255F"),
        HANDLE.replace("s", "%73", 1),
        TOKEN.replace("_", "%25255F"),
    ],
)
def test_the_access_log_blanks_a_path_segment_of_a_secret_s_shape(segment: str) -> None:
    record = _access(f"/operator/datasets/{segment}/queue")
    assert WithoutSecrets().filter(record)
    assert (
        record.getMessage() == '127.0.0.1:5 - "GET /operator/datasets/<secret>/queue HTTP/1.1" 401'
    )


def test_the_access_log_blanks_secrets_in_a_record_of_any_shape() -> None:
    encoded = TOKEN.replace("_", "%5F")
    odd = logging.LogRecord(
        "uvicorn.access", logging.INFO, __file__, 1, "%s asked %s", (HANDLE, f"/a/{encoded}"), None
    )
    assert WithoutSecrets().filter(odd)
    assert odd.getMessage() == "<secret> asked /a/<secret>"
    bare = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1, f"x {TOKEN}", None, None)
    assert WithoutSecrets().filter(bare)
    assert bare.getMessage() == "x <secret>"
    mapped = logging.LogRecord(
        "uvicorn.access", logging.INFO, __file__, 1, "%(who)s", ({"who": TOKEN},), None
    )
    assert WithoutSecrets().filter(mapped)
    assert mapped.getMessage() == "<secret>"
    shaped = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        1,
        '%s - "%s %s HTTP/%s" %d',
        (TOKEN, "GET", "/api/health", "1.1", 200),
        None,
    )
    assert WithoutSecrets().filter(shaped)
    assert shaped.getMessage() == '<secret> - "GET /api/health HTTP/1.1" 200'


def test_the_access_log_blanks_arguments_of_any_type_and_its_format() -> None:
    listed = _access("/api/health")
    listed.args = ([TOKEN], "GET", "/api/health", "1.1", 401)
    assert WithoutSecrets().filter(listed)
    assert listed.getMessage() == '<secret> - "GET /api/health HTTP/1.1" 401'
    raw = _access("/api/health")
    raw.args = (TOKEN.encode(), "GET", "/api/health", "1.1", 200)
    raw.msg = f"{HANDLE} %s - %s %s %s %d"
    assert WithoutSecrets().filter(raw)
    assert TOKEN not in raw.getMessage()
    assert HANDLE not in raw.getMessage()
    unformatted = _access("/api/health")
    unformatted.args = ("127.0.0.1:5", "GET", "/api/health", "1.1", TOKEN)
    assert WithoutSecrets().filter(unformatted)
    assert unformatted.args == ("<secret>", "-", "<secret>", "-", 0)
    joined = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        1,
        "%s%s%s%s%d",
        ("aibi", "_" + "t" * 20, "t" * 23, "", 200),
        None,
    )
    assert WithoutSecrets().filter(joined)
    assert joined.getMessage() == "<secret>-<secret>-200"


def test_exceptions_and_stacks_are_logged_blanked() -> None:
    try:
        raise RuntimeError(f"quoting {TOKEN}")
    except RuntimeError:
        failed = sys.exc_info()
    record = logging.LogRecord("aibi", logging.ERROR, __file__, 1, "failed", None, failed)
    record.stack_info = f"Stack (most recent call last):\n  {HANDLE}"
    assert WithoutSecrets().filter(record)
    assert record.exc_info is None
    assert "RuntimeError: quoting <secret>" in (record.exc_text or "")
    assert record.stack_info == "Stack (most recent call last):\n  <secret>"
    written = logging.Formatter().format(record)
    assert TOKEN not in written
    assert HANDLE not in written
    broken = logging.LogRecord("aibi", logging.INFO, __file__, 1, f"{TOKEN} %s %s", ("x",), None)
    assert WithoutSecrets().filter(broken)
    assert broken.getMessage() == "<secret> %s %s"


def test_the_filter_is_on_every_logger_that_writes_requests_and_every_handler() -> None:
    logs.install()
    logs.install()
    for name in logs.LOGGERS:
        filters = logging.getLogger(name).filters
        assert sum(isinstance(found, WithoutSecrets) for found in filters) == 1, name
    configured = logs.logging_config(copy.deepcopy(uvicorn.config.LOGGING_CONFIG))
    assert configured["filters"][logs.FILTER] == {"()": WithoutSecrets}
    assert set(configured["handlers"]) == {"default", "access"}
    assert all(handler["filters"] == [logs.FILTER] for handler in configured["handlers"].values())
    assert configured["loggers"]["aibi"] == {
        "handlers": ["default"],
        "level": "INFO",
        "propagate": False,
    }
    assert configured["root"] == {"level": "WARNING", "handlers": ["default"]}
    assert "filters" not in uvicorn.config.LOGGING_CONFIG
    assert {"", "mcp", "mcp.server.lowlevel.server"} <= set(logs.LOGGERS)


@pytest.mark.parametrize(
    "name",
    [
        "uvicorn.error",
        "uvicorn.access",
        "aibi",
        "aibi.core.api.protection",
        "",
        "mcp.server.streamable_http",
    ],
)
def test_a_record_of_uvicorn_s_or_the_server_s_loggers_is_blanked_before_any_handler(
    name: str,
) -> None:
    logs.install()
    token = new_token()
    seen: list[str] = []

    class Keeping(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            seen.append(record.getMessage())

    logger = logging.getLogger(name)
    kept = Keeping()
    logger.addHandler(kept)
    try:
        logger.error("cannot answer %s", f"/operator/{token}")
    finally:
        logger.removeHandler(kept)
    assert seen == ["cannot answer /operator/<secret>"]


def test_a_path_of_no_secret_s_shape_is_logged_as_it_came() -> None:
    for path in ("/api/health", "/operator/datasets/d/queue", "/api/aibi_short", "/a%2Fb"):
        record = _access(path)
        assert WithoutSecrets().filter(record)
        assert record.getMessage() == f'127.0.0.1:5 - "GET {path} HTTP/1.1" 401'


def test_a_real_server_s_access_log_holds_no_secret_from_a_path(
    make_app: Any, caplog: pytest.LogCaptureFixture
) -> None:
    built = make_app()
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    served = uvicorn.Server(uvicorn_config(built.config, built.app))
    thread = threading.Thread(target=served.run, kwargs={"sockets": [sock]}, daemon=True)
    caplog.set_level("DEBUG")
    thread.start()
    secrets = [built.token, HANDLE]
    paths = [
        f"/api/{built.token}",
        f"/operator/datasets/{built.token}",
        f"/operator/datasets/{HANDLE}/queue",
        "/api/" + built.token.replace("_", "%5F"),
        "/operator/datasets/" + built.token.replace("_", "%255F"),
        "/api/" + "".join(f"%{ord(c):02X}" for c in HANDLE),
        "/api/health",
    ]
    try:
        deadline = time.monotonic() + 20
        while not served.started:
            assert time.monotonic() < deadline, "uvicorn did not start"
            time.sleep(0.01)
        statuses: list[int] = []
        for path in paths:
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
            try:
                connection.request("GET", path, headers={"Aibi-Operator": "ada"})
                answered = connection.getresponse()
                answered.read()
                statuses.append(answered.status)
            finally:
                connection.close()
    finally:
        served.should_exit = True
        thread.join(30)
        sock.close()
    assert not thread.is_alive()
    assert statuses == [404, 401, 401, 404, 401, 404, 200]
    logged = [record.getMessage() for record in caplog.records if record.name == "uvicorn.access"]
    assert len(logged) == len(paths)
    assert sum("<secret>" in line for line in logged) == len(paths) - 1
    for secret in (*secrets, built.token[5:], HANDLE[4:]):
        assert not any(secret in line for line in logged), secret


def test_the_server_runs_under_umask_077(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[int] = []

    def locked(path: Path, **given: Any) -> Store:
        mask = os.umask(0o077)
        os.umask(mask)
        seen.append(mask)
        raise StoreLockedError

    monkeypatch.setattr(serve, "Store", locked)
    assert run(config_of(tmp_path), stderr=io.StringIO()) == 1
    assert seen == [0o077]


def test_the_server_refuses_to_run_while_another_process_has_the_store(tmp_path: Path) -> None:
    config = config_of(tmp_path)
    before = os.umask(0o022)
    os.umask(before)
    holder = Store(config.storage.data)
    try:
        err = io.StringIO()
        assert run(config, stderr=err) == 1
        assert "another server has the store open" in err.getvalue()
    finally:
        holder.close()
    after = os.umask(0o022)
    os.umask(after)
    assert after == before


def test_the_server_refuses_to_run_on_an_app_db_of_another_page_size_with_its_message(
    tmp_path: Path,
) -> None:
    """The derivation log's accounting charges pages of ``LOG_PAGE_BYTES`` bytes, so ``serve``
    exits with 1 and the store's message on an app DB of another page size, and no traceback
    (D300)."""
    config = config_of(tmp_path)
    config.storage.data.mkdir(parents=True)
    made = sqlite3.connect(config.storage.data / APP_DB)
    made.execute("PRAGMA page_size = 8192")
    made.execute("PRAGMA journal_mode = WAL")
    made.execute("CREATE TABLE kept (x)")
    made.close()
    err = io.StringIO()
    assert run(config, stderr=err) == 1
    assert err.getvalue() == (
        f"aibi-server: The app DB's pages are of 8192 bytes; the derivation log's accounting "
        f"needs {LOG_PAGE_BYTES} (D300): rebuild it with that page size\n"
    )


def test_the_services_are_the_configuration_s(tmp_path: Path) -> None:
    config = config_of(tmp_path, max_body_bytes=1234)
    store = Store(config.storage.data)
    try:
        services = services_of(config, store, PackRegistry((), core_version="0.0.1"))
    finally:
        store.close()
    assert services.import_directories == (Path(os.path.realpath(tmp_path / "imports")),)
    assert services.limits.import_bytes == 99
    assert services.concurrent_imports == 3
    assert services.upload_idle_seconds == 60
    assert services.upload_min_bytes_per_second == 32 * 1024
    assert services.max_body_bytes == 1234
    assert services.uploads.root == tmp_path / "data" / "uploads"


def run_main(*argv: str, stdin: str = "", **environ: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = main(list(argv), environ=environ, stdin=io.StringIO(stdin), stdout=out, stderr=err)
    return code, out.getvalue(), err.getvalue()


def test_a_new_token_is_printed_once_with_its_hash() -> None:
    code, out, _ = run_main("new-token")
    assert code == 0
    token = next(word for word in out.split() if word.startswith("aibi_"))
    assert TOKEN_RE.fullmatch(token)
    assert f'token_hash = "{hash_token(token)}"' in out


def test_a_token_read_from_standard_input_is_hashed() -> None:
    token = new_token()
    assert run_main("hash-token", stdin=token + "\n") == (
        0,
        f'token_hash = "{hash_token(token)}"\n',
        "",
    )
    code, out, err = run_main("hash-token", stdin="hunter2\n")
    assert (code, out) == (2, "")
    assert "hunter2" not in err


def test_the_check_prints_what_the_configuration_resolves_to_and_no_secret(
    write_config: Write, tmp_path: Path
) -> None:
    text = f'[curator]\ntoken_hash = "{HASH}"\n[storage]\ndata = "data"\nimports = ["imports"]\n'
    path = write_config(text)
    code, out, _ = run_main("check", "--config", str(path))
    assert code == 0
    assert "hosts: 127.0.0.1, [::1], localhost" in out
    assert "origins: http://127.0.0.1:8000, http://[::1]:8000, http://localhost:8000" in out
    assert "CORS origins: none (CORS off)" in out
    assert (
        "max body bytes: 8388608; max connections: 64, 16 a client; request head seconds: 10; "
        "send seconds: 30" in out
    )
    assert (
        "concurrent imports: 2; upload idle seconds: 60; upload min bytes per second: 32768" in out
    )
    assert f"data: {os.path.realpath(tmp_path / 'data')}" in out
    assert HASH not in out
    assert "b" * 64 not in out
    assert run_main("check", AIBI_CONFIG=str(path))[0] == 0


def test_a_configuration_is_required_and_its_problems_are_listed(write_config: Write) -> None:
    code, _, err = run_main("check")
    assert code == 2
    assert "--config or AIBI_CONFIG" in err
    path = write_config('[server]\nport = "x"\n')
    code, _, err = run_main("check", "--config", str(path))
    assert code == 2
    assert "server.port" in err
    assert "curator: missing" in err
    assert run_main("--help")[0] == 0
    assert run_main("nothing")[0] == 2


@pytest.mark.usefixtures("tmp_path")
def test_serve_needs_a_configuration() -> None:
    assert run_main("serve")[0] == 2


def test_a_system_without_proc_serves_without_query_workers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No worker's memory could be watched, so none runs: ``count_cohort`` refuses as
    ``NOT_SUPPORTED`` and the rest is served (D293, D300)."""
    config = config_of(tmp_path)
    assert serve.workers_of(config) is not None
    monkeypatch.setattr(worker, "_resident", lambda pid: None)
    assert serve.workers_of(config) is None


class _Stopped:
    """uvicorn's server, stopped as soon as it runs."""

    def __init__(self, config: Any) -> None:
        self.config = config

    def run(self) -> None:
        return None


def test_check_and_serve_say_that_count_cohort_is_disabled_without_query_workers(
    write_config: Write, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    text = f'[curator]\ntoken_hash = "{HASH}"\n[storage]\ndata = "data"\nimports = ["imports"]\n'
    path = write_config(text)
    code, out, _ = run_main("check", "--config", str(path))
    assert (code, serve.NO_WORKERS in out) == (0, False)
    assert "query workers: 2; query seconds: 25" in out
    monkeypatch.setattr(worker, "_resident", lambda pid: None)
    code, out, _ = run_main("check", "--config", str(path))
    assert (code, f"query workers: none; {serve.NO_WORKERS}\n" in out) == (0, True)
    monkeypatch.setattr(serve.uvicorn, "Server", _Stopped)
    err = io.StringIO()
    assert run(config_of(tmp_path), stderr=err) == 0
    assert err.getvalue() == f"aibi-server: {serve.NO_WORKERS}\n"


def test_the_check_says_how_long_each_kind_of_issuance_is_kept(write_config: Write) -> None:
    text = f'[curator]\ntoken_hash = "{HASH}"\n[storage]\ndata = "data"\nimports = ["imports"]\n'
    for log, said in (
        (
            "keep_result_issuances_days = 0",
            "count issuances kept: 30 days; result issuances kept: until pruned;",
        ),
        (
            "keep_count_issuances_days = 0",
            "count issuances kept: until pruned; result issuances kept: 365 days;",
        ),
    ):
        code, out, _ = run_main("check", "--config", str(write_config(f"{text}[log]\n{log}\n")))
        assert (code, said in out) == (0, True)


def test_the_check_refuses_a_period_the_log_could_not_count_back(write_config: Write) -> None:
    text = (
        f'[curator]\ntoken_hash = "{HASH}"\n[storage]\ndata = "data"\nimports = ["imports"]\n'
        "[log]\nkeep_count_issuances_days = 36501\n"
    )
    code, _, err = run_main("check", "--config", str(write_config(text)))
    assert code == 2
    assert "log.keep_count_issuances_days" in err

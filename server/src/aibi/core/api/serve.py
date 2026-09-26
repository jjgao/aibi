"""``aibi-server``: run the server, check its configuration, and make curator tokens (SPEC §11.2,
§14, D253, D254, D261, D268).

- ``serve`` reads the configuration (``--config`` or ``AIBI_CONFIG``), sets the umask to 077 (the
  data files hold datasets, and only the server's user needs them), opens the store, which refuses
  while another process has it open (D221) or its app DB has pages of another size than the
  derivation log charges (D300), exiting with 1 and a message, and prunes the derivation log's
  expired ``count_cohort`` issuances (``[log]``, D300), and runs uvicorn until it is stopped.
- ``check`` reads the configuration and prints what it resolves to: the bind, the hosts and
  origins allowed, the directories and the limits, and that ``count_cohort`` is disabled on a
  system without the query workers' ``/proc`` (which ``serve`` writes to standard error too);
  never the token's hash.
- ``new-token`` makes a curator token and prints it, once, with the hash the configuration holds.
- ``hash-token`` reads a token from standard input, or a prompt that does not echo on a terminal,
  and prints its hash; a token is never an argument.

uvicorn runs without trusting ``X-Forwarded-*`` headers, so the client is the connection's
address and the scheme the connection's (on a loopback bind, uvicorn's default would let any
local process pick its rate-limit key), without a ``Server`` header, with ``max_connections`` as
its concurrency limit, its h11 protocol guarded by a deadline for each request's head and a cap
on each client's connections (``api.connections``), and TLS when configured. Its logs leave
query strings out and blank anything of a token's or a handle's shape, as written or
percent-decoded (``api.logs``), so that nothing a client puts in a URL or a body by mistake, a
token say, reaches a log. Until packs are loaded by configuration (M4), the server registers
none.
"""

import argparse
import copy
import getpass
import os
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO

import uvicorn
import uvicorn.config
from starlette.types import ASGIApp

import aibi
from aibi.core.api import logs
from aibi.core.api.app import create_app
from aibi.core.api.config import ConfigError, ServerConfig, load_config
from aibi.core.api.connections import guarded_protocol
from aibi.core.api.logs import WithoutSecrets
from aibi.core.api.protection import Policy
from aibi.core.api.rates import Buckets, Rate, client_key
from aibi.core.catalog.service import Catalog
from aibi.core.engine.worker import Workers
from aibi.core.importers.uploads import UploadArea
from aibi.core.mcp.calls import Calls
from aibi.core.operator.auth import hash_token, new_token, token_digest
from aibi.core.operator.router import Services
from aibi.core.schema.pack_api import PackRegistry
from aibi.core.store.appdb import PageSizeError
from aibi.core.store.store import Store, StoreLockedError

GRACEFUL_SHUTDOWN = 30
"""Seconds uvicorn waits for running requests when it is stopped."""


def services_of(config: ServerConfig, store: Store, registry: PackRegistry) -> Services:
    """What the operator router calls, from the configuration and the open store."""
    return Services(
        store=store,
        uploads=UploadArea(config.storage.data),
        import_directories=tuple(Path(os.path.realpath(path)) for path in config.storage.imports),
        limits=config.imports.limits(),
        registry=registry,
        concurrent_imports=config.imports.concurrent,
        upload_idle_seconds=config.imports.upload_idle_seconds,
        upload_min_bytes_per_second=config.imports.upload_min_bytes_per_second,
        max_body_bytes=config.server.max_body_bytes,
        token_digest=token_digest(config.curator.token_hash),
        connections={
            name: connection.connection(name) for name, connection in config.databases.items()
        },
    )


@dataclass(frozen=True)
class Proposals:
    """The rate of ``propose_descriptor`` calls per client (D277), keyed as request protection
    keys its rates (``client_key``)."""

    buckets: Buckets

    @property
    def per_minute(self) -> int:
        return self.buckets.rate.per_minute

    def take(self, client: str) -> float | None:
        return self.buckets.take(client_key(client))


NO_WORKERS = (
    "count_cohort is disabled: this system has no /proc, so no query worker's memory can be watched"
)
"""What ``check`` prints, and ``serve`` writes to standard error, without query workers."""


def workers_of(config: ServerConfig) -> Workers | None:
    """The query workers of ``[queries]`` (D293); ``None`` on a system without ``/proc``, where
    no worker's memory can be watched, so that the server still serves the catalogue and
    ``count_cohort`` refuses as ``NOT_SUPPORTED`` (D300)."""
    try:
        return Workers(config.queries.limits())
    except RuntimeError:
        return None


def tools_of(config: ServerConfig, store: Store, registry: PackRegistry | None) -> Calls:
    """The server's tool calls (D277, D278): its catalogue, with the floor and the model cards of
    the configuration and the query workers of ``[queries]`` (``workers_of``; D293, D300); its
    bodies' deadlines, ``tool_body_idle_seconds`` and an upload's rate (D266); the rate of
    proposals per client; and clients keyed as the rates key them (D259), for their share of the
    places and of agents' proposals."""
    catalog = Catalog(
        store,
        registry=registry,
        floor=config.disclosure.min_cell_count_floor,
        models=tuple(config.models),
        token_digest=token_digest(config.curator.token_hash),
        workers=workers_of(config),
    )
    proposals = config.server.rates.proposals
    return Calls(
        catalog,
        body_idle_seconds=config.server.tool_body_idle_seconds,
        body_min_bytes_per_second=config.imports.upload_min_bytes_per_second,
        max_body_bytes=config.server.max_body_bytes,
        proposals=Proposals(Buckets(Rate(proposals.per_minute, proposals.burst))),
        client_key=client_key,
    )


def uvicorn_config(
    config: ServerConfig, app: ASGIApp, *, logging_config: dict[str, Any] | None = None
) -> uvicorn.Config:
    """uvicorn's configuration (D254). ``logging_config`` is applied to the process's logging
    (uvicorn's own when the server runs); ``None`` leaves logging as it is."""
    server = config.server
    tls = config.tls and server.tls_certificate is not None and server.tls_key is not None
    built = uvicorn.Config(
        app,
        host=server.bind,
        port=server.port,
        proxy_headers=False,
        server_header=False,
        limit_concurrency=server.max_connections,
        http=guarded_protocol(
            request_head_seconds=server.request_head_seconds,
            max_connections_per_client=server.connections_per_client,
            send_seconds=server.send_seconds,
        ),
        ssl_certfile=str(server.tls_certificate) if tls else None,
        ssl_keyfile=str(server.tls_key) if tls else None,
        timeout_graceful_shutdown=GRACEFUL_SHUTDOWN,
        ws="none",
        log_config=logging_config,
    )
    logs.install()
    return built


def run(config: ServerConfig, *, stderr: TextIO | None = None) -> int:
    """Serve until stopped; 1 if another process has the store open, or its app DB has pages
    of another size than the derivation log's accounting charges (``PageSizeError``, D300),
    whose message is written to standard error."""
    err = sys.stderr if stderr is None else stderr
    previous = os.umask(0o077)
    try:
        try:
            store = Store(config.storage.data, log=config.log.limits())
        except StoreLockedError:
            err.write("aibi-server: another server has the store open\n")
            return 1
        except PageSizeError as error:
            err.write(f"aibi-server: {error}\n")
            return 1
        try:
            if workers_of(config) is None:
                err.write(f"aibi-server: {NO_WORKERS}\n")
            registry = PackRegistry((), core_version=aibi.__version__)
            app = create_app(
                Policy.of(config),
                services_of(config, store, registry),
                tools=tools_of(config, store, registry),
            )
            logging_config = logs.logging_config(copy.deepcopy(uvicorn.config.LOGGING_CONFIG))
            uvicorn.Server(uvicorn_config(config, app, logging_config=logging_config)).run()
        finally:
            store.close()
        return 0
    finally:
        os.umask(previous)


def check(config: ServerConfig) -> list[str]:
    """What the configuration resolves to, without the token's hash."""
    server = config.server
    policy = Policy.of(config, csrf_key=bytes(32))
    lines = [
        f"bind: {server.bind}:{server.port} ({'TLS' if config.tls else 'no TLS'})",
        "hosts: " + ", ".join(sorted(policy.hosts)),
        "origins: " + ", ".join(sorted(policy.origins)),
        "CORS origins: " + (", ".join(sorted(policy.cors_origins)) or "none (CORS off)"),
        f"data: {os.path.realpath(config.storage.data)}",
        "import directories: "
        + (", ".join(os.path.realpath(path) for path in config.storage.imports) or "none"),
        f"max body bytes: {server.max_body_bytes}; max connections: {server.max_connections}, "
        f"{server.connections_per_client} a client; request head seconds: "
        f"{server.request_head_seconds}; send seconds: {server.send_seconds}",
        f"concurrent imports: {config.imports.concurrent}; upload idle seconds: "
        f"{config.imports.upload_idle_seconds}; upload min bytes per second: "
        f"{config.imports.upload_min_bytes_per_second}",
        f"tool body idle seconds: {server.tool_body_idle_seconds}",
    ]
    for name, rate in (
        ("operator", server.rates.operator),
        ("api", server.rates.api),
        ("token failures", server.rates.token_failures),
        ("proposals", server.rates.proposals),
        ("page", server.rates.page),
    ):
        lines.append(f"rate, {name}: {rate.per_minute} a minute, bursts of {rate.burst}")
    log = config.log.limits()
    days = log.keep_count_issuances_days
    kept = "until pruned" if days is None else f"{days} days"
    lines.append(f"count issuances kept: {kept}; log bytes: {log.log_bytes}")
    queries = config.queries.limits()
    lines.append(
        f"query workers: {queries.query_workers}; query seconds: {queries.query_seconds}; "
        f"query memory: {queries.query_memory}; query threads: {queries.query_threads}"
        if workers_of(config) is not None
        else f"query workers: none; {NO_WORKERS}"
    )
    floor = config.disclosure.min_cell_count_floor
    lines.append(f"disclosure floor: {'none' if floor is None else floor}")
    lines += [
        f"database {name}: {found.kind}"
        + ("" if found.schema_ is None else f", schema {found.schema_}")
        for name, found in config.databases.items()
    ]
    lines += [f"model card: {card.id}" for card in config.models]
    return lines


def _config_path(given: Path | None, environ: Mapping[str, str]) -> Path | None:
    if given is not None:
        return given
    named = environ.get("AIBI_CONFIG")
    return Path(named) if named else None


def main(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    prompt: Callable[[str], str] = getpass.getpass,
) -> int:
    """Run ``aibi-server`` with ``argv``."""
    environ = os.environ if environ is None else environ
    out = sys.stdout if stdout is None else stdout
    err = sys.stderr if stderr is None else stderr
    source = sys.stdin if stdin is None else stdin
    parser = argparse.ArgumentParser(prog="aibi-server", description="Run an aibi server.")
    commands = parser.add_subparsers(dest="command", required=True)
    for name, summary in (("serve", "run the server"), ("check", "check the configuration")):
        command = commands.add_parser(name, help=summary)
        command.add_argument("--config", type=Path, help="the configuration file (AIBI_CONFIG)")
    commands.add_parser("new-token", help="make a curator token and print its hash")
    commands.add_parser("hash-token", help="print the hash of a token read from standard input")
    try:
        args = parser.parse_args(argv)
    except SystemExit as stopped:
        return stopped.code if isinstance(stopped.code, int) else 2
    if args.command == "new-token":
        token = new_token()
        out.write(
            f"curator token (shown once; give it to operators as AIBI_TOKEN): {token}\n"
            f'[curator]\ntoken_hash = "{hash_token(token)}"\n'
        )
        return 0
    if args.command == "hash-token":
        given = prompt("Curator token: ") if source.isatty() else source.readline()
        try:
            out.write(f'token_hash = "{hash_token(given.strip())}"\n')
        except ValueError as error:
            err.write(f"aibi-server: {error}\n")
            return 2
        return 0
    path = _config_path(args.config, environ)
    if path is None:
        err.write("aibi-server: name the configuration with --config or AIBI_CONFIG\n")
        return 2
    try:
        config = load_config(path)
    except ConfigError as error:
        err.write("aibi-server: the configuration is refused:\n")
        err.writelines(f"  {problem}\n" for problem in error.problems)
        return 2
    if args.command == "check":
        out.writelines(line + "\n" for line in check(config))
        return 0
    return run(config, stderr=err)


__all__ = [
    "GRACEFUL_SHUTDOWN",
    "NO_WORKERS",
    "Proposals",
    "WithoutSecrets",
    "check",
    "main",
    "run",
    "services_of",
    "tools_of",
    "uvicorn_config",
]

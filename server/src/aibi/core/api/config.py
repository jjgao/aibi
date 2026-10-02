"""The server's configuration (SPEC §11.2, §14, D253, D254).

The server reads one TOML file at start, named by ``--config`` or ``AIBI_CONFIG``; it takes effect
at the next start, and nothing reloads it. ``load_config`` resolves the path once, component by
component, and refuses it if a symbolic link on the way lies in a directory its group or others
can write (unless the directory has the sticky bit and the link is the server's user's or
root's), or if the file's own directory is writable so without the sticky bit: they could make
themselves operators by swapping the link or renaming a file of their own over it. It then opens
the resolved file without following a link (``O_NOFOLLOW``), checks that descriptor (``fstat``:
a regular file, that neither its group nor others can write, owned by the server's user or
root) and reads the file from it, so the file checked is the file read. Directories further up
are the deployment's to protect. It validates the file with ``ServerConfig``: strict and
closed, so an unknown key is refused, and every problem is reported at once with its TOML path
(``ConfigError``). Relative paths are resolved against the file's directory. Its sections:

- ``[server]``: ``bind`` (an IP literal or ``localhost``; ``127.0.0.1`` by default) and
  ``port``; ``hostnames`` and ``public_origins`` that the Host and Origin checks allow besides
  the loopback names and the server's own origins; ``cors_origins``, off when empty;
  ``tls_certificate`` and ``tls_key``; ``max_body_bytes``; ``max_connections``,
  ``max_connections_per_client``, fewer (a quarter of them by default),
  ``request_head_seconds``, how long a connection may go without a request under way, and
  ``send_seconds``, how long a client may take too little of the answers waiting for it (D254,
  ``api.connections``); and ``[server.rates]``, the requests a client may make per minute, in
  bursts (D259).
- ``[curator] token_hash``: ``sha256:<hex>`` of the curator token, never the token (D261).
- ``[storage]``: ``data``, the store's directory, which holds the upload area (D234), and
  ``imports``, the import directories (D232).
- ``[imports]``: the limits of every import (D233), the server's and never a request's;
  ``concurrent``, how many uploads, imports, re-imports and erasures run at once, at most
  ``MAX_CONCURRENT_IMPORTS``; ``upload_idle_seconds``, how long an upload may send nothing before
  it is refused; and ``upload_min_bytes_per_second``, the slowest an upload may average, which
  with its length sets its deadline (D266).
- ``[disclosure] min_cell_count_floor``: the deployment's floor for *k* (§8.4), 2 or more.
- ``[databases.<identifier>]``: named connections, by shape only until database snapshots use
  them (#13): a SQLite or DuckDB file inside an import directory, or for Postgres and MySQL the
  name of the environment variable that holds the URL. Credentials are never in the file (§14).
- ``[[models]]``: model cards (§5.9), validated as descriptors, each id once.

Binding (D254): a bind other than the loopback interface needs both TLS files, and a key file
that others can read is refused; a wildcard bind needs a hostname as well. Import directories
must exist and be directories, and must neither hold nor lie inside the data directory, compared
by real paths, so that no import's confinement root holds the store or its uploads being written.
"""

import errno
import ipaddress
import os
import stat
import tomllib
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Annotated, Literal, Self, cast

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictInt,
    StringConstraints,
    ValidationError,
    ValidationInfo,
    model_validator,
)
from pydantic_core import ErrorDetails, PydanticCustomError

from aibi.core.api.origins import LOOPBACK_HOSTS, hostname, is_loopback, origin
from aibi.core.schema.descriptors import ModelCardDescriptor
from aibi.core.schema.ids import Identifier
from aibi.core.schema.limits import MAX_BODY_BYTES, ImportLimits

_DEFAULT_LIMITS = ImportLimits()
WORKER_THREADS = 40
"""The worker threads that the reads, the operations and the parsing of bodies share: anyio's
default limiter, which the server leaves as it is."""
MAX_CONCURRENT_IMPORTS = WORKER_THREADS // 2
"""The most ``imports.concurrent`` may be, so that uploads, imports, re-imports and erasures,
each holding a worker thread while it runs, leave half the threads to everything else (D266)."""
BASE = "base"
"""The validation context's key for the directory relative paths are resolved against."""


class ConfigError(Exception):
    """A configuration refused, with every problem found, each with its TOML path."""

    def __init__(self, problems: Sequence[str]) -> None:
        super().__init__("\n".join(problems))
        self.problems = tuple(problems)


class _Config(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)


def _path(value: object, info: ValidationInfo) -> object:
    if not isinstance(value, str) or not value or "\0" in value:
        raise PydanticCustomError("path_type", "A path is a string that is not empty")
    context = cast(dict[str, object], info.context or {})
    base = context.get(BASE)
    given = Path(value)
    return given if given.is_absolute() or not isinstance(base, Path) else base / given


ConfigPath = Annotated[Path, BeforeValidator(_path)]
"""A path, relative to the configuration file's directory unless absolute."""

PositiveInt = Annotated[StrictInt, Field(ge=1)]


def _hostname(value: object) -> object:
    found = hostname(value) if isinstance(value, str) else None
    if found is None:
        raise PydanticCustomError(
            "hostname",
            "A hostname is a DNS name or an IP literal, without a port, a wildcard or a "
            "trailing dot",
        )
    return found


def _origin(value: object) -> object:
    found = origin(value) if isinstance(value, str) else None
    if found is None:
        raise PydanticCustomError(
            "origin", "An origin is scheme://host[:port], http or https, with nothing after it"
        )
    return found


def _bind(value: object) -> object:
    if value == "localhost":
        return value
    try:
        return str(ipaddress.ip_address(value)) if isinstance(value, str) else None
    except ValueError:
        pass
    raise PydanticCustomError("bind", "A bind address is an IP literal or localhost")


Hostname = Annotated[str, BeforeValidator(_hostname)]
Origin = Annotated[str, BeforeValidator(_origin)]
EnvName = Annotated[str, StringConstraints(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")]


class Rate(_Config):
    """Requests a client may make per minute, and at once (D259)."""

    per_minute: PositiveInt
    burst: PositiveInt


class Rates(_Config):
    operator: Rate = Rate(per_minute=600, burst=50)
    api: Rate = Rate(per_minute=3000, burst=200)
    token_failures: Rate = Rate(per_minute=10, burst=10)


class ServerSection(_Config):
    bind: Annotated[str, BeforeValidator(_bind)] = "127.0.0.1"
    port: Annotated[StrictInt, Field(ge=1, le=65535)] = 8000
    hostnames: list[Hostname] = Field(default_factory=list[str])
    public_origins: list[Origin] = Field(default_factory=list[str])
    cors_origins: list[Origin] = Field(default_factory=list[str])
    tls_certificate: ConfigPath | None = None
    tls_key: ConfigPath | None = None
    max_body_bytes: PositiveInt = MAX_BODY_BYTES
    max_connections: Annotated[StrictInt, Field(ge=2)] = 64
    max_connections_per_client: PositiveInt | None = None
    """A quarter of ``max_connections``, at least 1, when not given."""
    request_head_seconds: PositiveInt = 10
    send_seconds: PositiveInt = 30
    rates: Rates = Rates()

    @model_validator(mode="after")
    def _tls_pair(self) -> Self:
        if (self.tls_certificate is None) != (self.tls_key is None):
            raise PydanticCustomError(
                "tls_pair", "tls_certificate and tls_key are given together, or neither"
            )
        return self

    @property
    def connections_per_client(self) -> int:
        """The connections one client may have open (``api.connections``)."""
        given = self.max_connections_per_client
        return max(1, self.max_connections // 4) if given is None else given

    @model_validator(mode="after")
    def _per_client(self) -> Self:
        if self.connections_per_client >= self.max_connections:
            raise PydanticCustomError(
                "per_client",
                "max_connections_per_client is fewer than max_connections, so that no one client "
                "holds every connection",
            )
        return self


class Curator(_Config):
    token_hash: Annotated[str, StringConstraints(pattern=r"^sha256:[0-9a-f]{64}$")]
    """``aibi-server new-token`` prints it with the token."""


class Storage(_Config):
    data: ConfigPath
    imports: list[ConfigPath] = Field(default_factory=list[Path])


class Imports(_Config):
    """Every limit of ``ImportLimits`` (D233), with its defaults, and the settings of uploads,
    imports, re-imports and erasures over HTTP (D266)."""

    import_bytes: PositiveInt = _DEFAULT_LIMITS.import_bytes
    archive_members: PositiveInt = _DEFAULT_LIMITS.archive_members
    archive_bytes: PositiveInt = _DEFAULT_LIMITS.archive_bytes
    member_bytes: PositiveInt = _DEFAULT_LIMITS.member_bytes
    archive_ratio: PositiveInt = _DEFAULT_LIMITS.archive_ratio
    import_tables: PositiveInt = _DEFAULT_LIMITS.import_tables
    table_columns: PositiveInt = _DEFAULT_LIMITS.table_columns
    import_cells: PositiveInt = _DEFAULT_LIMITS.import_cells
    reader_memory: PositiveInt = _DEFAULT_LIMITS.reader_memory
    reader_seconds: PositiveInt = _DEFAULT_LIMITS.reader_seconds
    reader_workers: PositiveInt = _DEFAULT_LIMITS.reader_workers
    decoded_bytes: PositiveInt = _DEFAULT_LIMITS.decoded_bytes
    concurrent: Annotated[StrictInt, Field(ge=1, le=MAX_CONCURRENT_IMPORTS)] = 2
    upload_idle_seconds: PositiveInt = 60
    upload_min_bytes_per_second: PositiveInt = 32 * 1024

    def limits(self) -> ImportLimits:
        return ImportLimits(**self.model_dump(exclude=set(HTTP_SETTINGS)))


HTTP_SETTINGS = ("concurrent", "upload_idle_seconds", "upload_min_bytes_per_second")
"""The settings of ``[imports]`` that are not limits of an import (D266)."""


class Disclosure(_Config):
    min_cell_count_floor: Annotated[StrictInt, Field(ge=2)] | None = None


class DatabaseConnection(_Config):
    """A named connection's shape (§14): a file for SQLite and DuckDB, the name of the variable
    that holds the URL for Postgres and MySQL; never a credential."""

    kind: Literal["postgres", "mysql", "sqlite", "duckdb"]
    path: ConfigPath | None = None
    url_env: EnvName | None = None

    @model_validator(mode="after")
    def _shape(self) -> Self:
        files = self.kind in ("sqlite", "duckdb")
        if files and (self.path is None or self.url_env is not None):
            raise PydanticCustomError(
                "connection", "A SQLite or DuckDB connection names a path, and no url_env"
            )
        if not files and (self.url_env is None or self.path is not None):
            raise PydanticCustomError(
                "connection",
                "A Postgres or MySQL connection names url_env, the environment variable that "
                "holds its URL, and no path",
            )
        return self


class ServerConfig(_Config):
    server: ServerSection = ServerSection()
    curator: Curator
    storage: Storage
    imports: Imports = Imports()
    disclosure: Disclosure = Disclosure()
    databases: dict[Identifier, DatabaseConnection] = Field(
        default_factory=dict[str, DatabaseConnection]
    )
    models: list[ModelCardDescriptor] = Field(default_factory=list[ModelCardDescriptor])

    @property
    def loopback(self) -> bool:
        """Whether the server is bound to the loopback interface alone."""
        return is_loopback(self.server.bind)

    @property
    def tls(self) -> bool:
        """Whether the server serves TLS itself."""
        return self.server.tls_certificate is not None and self.server.tls_key is not None

    @property
    def wildcard(self) -> bool:
        return (
            self.server.bind != "localhost"
            and ipaddress.ip_address(self.server.bind).is_unspecified
        )

    def hosts(self) -> frozenset[str]:
        """The hosts the Host check allows (D256): the loopback names, the bound address when it
        is a specific one, and the configured hostnames."""
        bound: set[str] = set()
        if not self.wildcard and self.server.bind != "localhost":
            found = hostname(self.server.bind)
            if found is not None:
                bound.add(found)
        return LOOPBACK_HOSTS | bound | set(self.server.hostnames)


def _toml_path(loc: Iterable[int | str]) -> str:
    written = ""
    for element in loc:
        if isinstance(element, int):
            written += f"[{element}]"
        elif element == "[key]":
            continue
        else:
            key = element if element.replace("_", "a").replace("-", "a").isalnum() else None
            part = key if key is not None and element.isascii() else f'"{element}"'
            written += f".{part}" if written else part
    return written or "(the file)"


_MESSAGES = {"extra_forbidden": "unknown key", "missing": "missing"}


def _problem(details: ErrorDetails) -> str:
    message = _MESSAGES.get(details["type"], details["msg"])
    return f"{_toml_path(details['loc'])}: {message}"


def _real(path: Path) -> Path:
    return Path(os.path.realpath(path))


def _file_problems(config: ServerConfig) -> list[str]:
    """What the file system and the sections together refuse (D253, D254)."""
    problems: list[str] = []
    server = config.server
    if not config.loopback and not config.tls:
        problems.append(
            "server.bind: a bind other than the loopback interface needs tls_certificate and "
            "tls_key (SPEC §14)"
        )
    if config.wildcard and not server.hostnames:
        problems.append("server.hostnames: a wildcard bind needs at least one hostname")
    for name, path in (("tls_certificate", server.tls_certificate), ("tls_key", server.tls_key)):
        if path is None:
            continue
        try:
            status = os.stat(path)
        except OSError:
            problems.append(f"server.{name}: no such file")
            continue
        if not stat.S_ISREG(status.st_mode):
            problems.append(f"server.{name}: not a regular file")
        elif name == "tls_key" and status.st_mode & stat.S_IROTH:
            problems.append("server.tls_key: others can read the key file")
    hosts = config.hosts()
    for index, allowed in enumerate(server.public_origins):
        if _origin_host(allowed) not in hosts:
            problems.append(
                f"server.public_origins[{index}]: its host is neither a loopback name, the bound "
                "address nor a configured hostname"
            )
    data = _real(config.storage.data)
    imports: list[Path] = []
    for index, directory in enumerate(config.storage.imports):
        where = f"storage.imports[{index}]"
        if not directory.is_dir():
            problems.append(f"{where}: not an existing directory")
            continue
        real = _real(directory)
        imports.append(real)
        if real.is_relative_to(data) or data.is_relative_to(real):
            problems.append(f"{where}: it holds, or lies inside, the data directory")
    for name, connection in config.databases.items():
        if connection.path is None:
            continue
        real = _real(connection.path)
        if not any(real.is_relative_to(directory) for directory in imports):
            problems.append(f"databases.{name}.path: not inside an import directory")
    seen: set[str] = set()
    for index, card in enumerate(config.models):
        if card.id in seen:
            problems.append(f"models[{index}].id: the model card is registered twice")
        seen.add(card.id)
    return problems


def _origin_host(allowed: str) -> str:
    """The host of a normalised origin."""
    authority = allowed.partition("://")[2]
    if authority.startswith("["):
        return authority[: authority.index("]") + 1]
    return authority.partition(":")[0]


MAX_LINKS = 40
"""The symbolic links followed in resolving the configuration's path, as the kernel's limit."""
_OTHERS_WRITE = stat.S_IWGRP | stat.S_IWOTH


def _resolved(path: Path) -> tuple[Path, list[tuple[Path, Path]]]:
    """The real path of ``path``, resolved one component at a time as the kernel resolves it,
    and each symbolic link met on the way with the directory that holds it (``OSError`` if a
    component is missing or there are too many links)."""
    pending = list(reversed((Path.cwd() / path).parts[1:]))
    current = Path("/")
    links: list[tuple[Path, Path]] = []
    while pending:
        part = pending.pop()
        if part == "..":
            current = current.parent
            continue
        candidate = current / part
        if not stat.S_ISLNK(os.lstat(candidate).st_mode):
            current = candidate
            continue
        links.append((current, candidate))
        if len(links) > MAX_LINKS:
            raise OSError(errno.ELOOP, os.strerror(errno.ELOOP))
        target = Path(os.readlink(candidate))
        if target.is_absolute():
            current = Path("/")
        pending.extend(reversed(target.parts[1:] if target.is_absolute() else target.parts))
    return current, links


def _replaceable(directory: os.stat_result) -> bool:
    """Whether others than its owner may rename or remove any entry of a directory."""
    return bool(directory.st_mode & _OTHERS_WRITE) and not directory.st_mode & stat.S_ISVTX


def _read_file(path: Path) -> tuple[Path, dict[str, object]]:
    """The configuration file's real path and its TOML, once the file-system checks pass."""
    owners = (os.geteuid(), 0)
    try:
        real, links = _resolved(path)
        for holder, link in links:
            directory = os.stat(holder)
            if _replaceable(directory) or (
                directory.st_mode & _OTHERS_WRITE and os.lstat(link).st_uid not in owners
            ):
                raise ConfigError(
                    [
                        f"{path}: a symbolic link on its way lies in a directory its group or "
                        "others can write, and so could be swapped"
                    ]
                )
        if _replaceable(os.stat(real.parent)):
            raise ConfigError(
                [f"{path}: its group or others can write its directory, and so replace it"]
            )
        descriptor = os.open(real, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    except OSError as error:
        raise ConfigError([f"{path}: cannot be read ({error.strerror})"]) from None
    with os.fdopen(descriptor, "rb") as file:
        status = os.fstat(file.fileno())
        if not stat.S_ISREG(status.st_mode):
            raise ConfigError([f"{path}: not a regular file"])
        if status.st_mode & _OTHERS_WRITE:
            raise ConfigError([f"{path}: its group or others can write it; chmod go-w it"])
        if status.st_uid not in owners:
            raise ConfigError([f"{path}: owned by another user; the server's user or root owns it"])
        try:
            return real, tomllib.load(file)
        except (tomllib.TOMLDecodeError, UnicodeDecodeError) as error:
            raise ConfigError([f"{path}: not TOML ({error})"]) from None


def load_config(path: Path) -> ServerConfig:
    """The configuration in the TOML file at ``path``; ``ConfigError`` with every problem."""
    real, written = _read_file(path)
    try:
        config = ServerConfig.model_validate(written, context={BASE: real.parent})
    except ValidationError as error:
        raise ConfigError(
            [_problem(details) for details in error.errors(include_url=False, include_input=False)]
        ) from None
    problems = _file_problems(config)
    if problems:
        raise ConfigError(problems)
    return config


__all__ = [
    "BASE",
    "HTTP_SETTINGS",
    "MAX_CONCURRENT_IMPORTS",
    "MAX_LINKS",
    "WORKER_THREADS",
    "ConfigError",
    "Curator",
    "DatabaseConnection",
    "Disclosure",
    "Imports",
    "Rate",
    "Rates",
    "ServerConfig",
    "ServerSection",
    "Storage",
    "load_config",
]

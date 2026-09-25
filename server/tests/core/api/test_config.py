"""The server's configuration (SPEC §14, D253, D254): the example loads, and what a deployment
must not do is refused, every problem at once with its TOML path."""

import dataclasses
import errno
import os
import shutil
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import anyio
import anyio.to_thread
import pytest

from aibi.core.api import config as config_module
from aibi.core.api.config import (
    HTTP_SETTINGS,
    MAX_CONCURRENT_IMPORTS,
    WORKER_THREADS,
    ConfigError,
    Imports,
    load_config,
)
from aibi.core.schema.limits import ImportLimits

EXAMPLE = Path(__file__).resolve().parents[3] / "aibi.example.toml"
HASH = "sha256:" + "a" * 64
Write = Callable[..., Path]


def minimal(**sections: str) -> str:
    """A configuration with the required sections, and ``sections`` written after them."""
    written = {
        "curator": f'token_hash = "{HASH}"',
        "storage": 'data = "data"\nimports = ["imports"]',
        **sections,
    }
    return "\n".join(f"[{name}]\n{body}\n" for name, body in written.items())


def problems(write_config: Write, text: str, **given: Any) -> list[str]:
    with pytest.raises(ConfigError) as refused:
        load_config(write_config(text, **given))
    return list(refused.value.problems)


def test_the_example_configuration_loads(tmp_path: Path) -> None:
    copied = tmp_path / "aibi.toml"
    shutil.copy(EXAMPLE, copied)
    os.chmod(copied, 0o600)
    (tmp_path / "imports").mkdir()
    config = load_config(copied)
    assert (config.server.bind, config.server.port) == ("127.0.0.1", 8000)
    assert config.loopback
    assert not config.tls
    assert config.storage.data == tmp_path / "data"
    assert config.storage.imports == [tmp_path / "imports"]
    assert config.imports.limits() == ImportLimits()
    assert config.imports.concurrent == 2
    assert config.imports.upload_idle_seconds == 60
    assert config.imports.upload_min_bytes_per_second == 32 * 1024
    assert (config.server.max_connections, config.server.connections_per_client) == (64, 16)
    assert (config.server.request_head_seconds, config.server.send_seconds) == (10, 30)
    assert config.hosts() == {"localhost", "127.0.0.1", "[::1]"}


def test_the_import_settings_are_every_import_limit() -> None:
    names = {field.name for field in dataclasses.fields(ImportLimits)}
    assert set(Imports.model_fields) == names | set(HTTP_SETTINGS)
    assert Imports(import_bytes=5, reader_workers=3).limits() == ImportLimits(
        import_bytes=5, reader_workers=3
    )


def test_unknown_keys_and_a_malformed_hash_are_refused(write_config: Write) -> None:
    text = minimal(server='bindd = "127.0.0.1"').replace(HASH, "sha256:ABC")
    found = problems(write_config, text)
    assert "server.bindd: unknown key" in found
    assert any(problem.startswith("curator.token_hash:") for problem in found)


def test_a_bind_other_than_loopback_needs_tls(write_config: Write) -> None:
    found = problems(write_config, minimal(server='bind = "10.0.0.5"'))
    assert found == [
        "server.bind: a bind other than the loopback interface needs tls_certificate and "
        "tls_key (SPEC §14)"
    ]
    half = problems(write_config, minimal(server='bind = "10.0.0.5"\ntls_key = "key.pem"'))
    assert any("given together" in problem for problem in half)
    assert problems(write_config, minimal(server='bind = "example.org"'))


def test_a_wildcard_bind_needs_a_hostname_and_tls(write_config: Write, tmp_path: Path) -> None:
    for name in ("cert.pem", "key.pem"):
        (tmp_path / name).write_text("x")
        os.chmod(tmp_path / name, 0o600)
    tls = 'tls_certificate = "cert.pem"\ntls_key = "key.pem"'
    found = problems(write_config, minimal(server=f'bind = "0.0.0.0"\n{tls}'))
    assert found == ["server.hostnames: a wildcard bind needs at least one hostname"]
    config = load_config(
        write_config(minimal(server=f'bind = "::"\nhostnames = ["aibi.example.org"]\n{tls}'))
    )
    assert config.tls
    assert config.hosts() == {"localhost", "127.0.0.1", "[::1]", "aibi.example.org"}


def test_a_tls_key_others_can_read_is_refused(write_config: Write, tmp_path: Path) -> None:
    for name in ("cert.pem", "key.pem"):
        (tmp_path / name).write_text("x")
    os.chmod(tmp_path / "key.pem", 0o644)
    server = 'bind = "10.0.0.5"\ntls_certificate = "cert.pem"\ntls_key = "key.pem"'
    assert problems(write_config, minimal(server=server)) == [
        "server.tls_key: others can read the key file"
    ]
    os.chmod(tmp_path / "key.pem", 0o640)
    config = load_config(write_config(minimal(server=server)))
    assert config.hosts() == {"localhost", "127.0.0.1", "[::1]", "10.0.0.5"}
    missing = server.replace("cert.pem", "gone.pem")
    assert problems(write_config, minimal(server=missing)) == [
        "server.tls_certificate: no such file"
    ]


@pytest.mark.parametrize("case", ["missing", "a file", "inside", "holding", "through a link"])
def test_an_import_directory_must_exist_apart_from_the_data(
    write_config: Write, tmp_path: Path, case: str
) -> None:
    (tmp_path / "data").mkdir()
    (tmp_path / "a file").write_text("x")
    (tmp_path / "data" / "inner").mkdir()
    (tmp_path / "link").symlink_to(tmp_path / "data")
    directory = {
        "missing": "nowhere",
        "a file": "a file",
        "inside": "data/inner",
        "holding": ".",
        "through a link": "link",
    }[case]
    text = minimal(storage=f'data = "data"\nimports = ["imports", "{directory}"]')
    [found] = problems(write_config, text)
    assert found.startswith("storage.imports[1]: ")


def test_a_configuration_its_group_or_others_can_write_is_refused(write_config: Write) -> None:
    for mode in (0o620, 0o602):
        [found] = problems(write_config, minimal(), mode=mode)
        assert "group or others can write" in found


def test_a_configuration_another_user_owns_is_refused(
    write_config: Write, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = write_config(minimal())
    if os.geteuid() == 0:
        os.chown(path, 4242, -1)
    else:
        monkeypatch.setattr(os, "geteuid", lambda: path.stat().st_uid + 1)
    with pytest.raises(ConfigError) as refused:
        load_config(path)
    assert list(refused.value.problems) == [
        f"{path}: owned by another user; the server's user or root owns it"
    ]


def test_a_root_owned_configuration_is_accepted(
    write_config: Write, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = write_config(minimal())
    if os.geteuid() == 0:
        os.chown(path, 0, -1)
    else:
        real_fstat = os.fstat

        def as_root(descriptor: int) -> os.stat_result:
            found = list(real_fstat(descriptor))
            found[4] = 0
            return os.stat_result(found)

        monkeypatch.setattr(os, "fstat", as_root)
    monkeypatch.setattr(os, "geteuid", lambda: 4242)
    assert load_config(path).curator.token_hash == HASH


def test_a_link_to_the_configuration_in_a_directory_others_can_write_is_refused(
    write_config: Write, tmp_path: Path
) -> None:
    path = write_config(minimal())
    shared = tmp_path / "open"
    shared.mkdir()
    os.chmod(shared, 0o777)
    (shared / "aibi.toml").symlink_to(path)
    with pytest.raises(ConfigError) as refused:
        load_config(shared / "aibi.toml")
    assert list(refused.value.problems) == [
        f"{shared / 'aibi.toml'}: a symbolic link on its way lies in a directory its group or "
        "others can write, and so could be swapped"
    ]
    (tmp_path / "via").symlink_to(shared)
    with pytest.raises(ConfigError, match="symbolic link on its way"):
        load_config(tmp_path / "via" / "aibi.toml")
    os.chmod(shared, 0o1777)
    assert load_config(shared / "aibi.toml").curator.token_hash == HASH
    os.chmod(shared, 0o700)
    assert load_config(shared / "aibi.toml").curator.token_hash == HASH


def test_a_link_in_a_sticky_directory_is_accepted_only_if_its_owner_is_trusted(
    write_config: Write, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = write_config(minimal())
    shared = tmp_path / "sticky"
    shared.mkdir()
    os.chmod(shared, 0o1777)
    link = shared / "aibi.toml"
    link.symlink_to(path)
    owner = link.lstat().st_uid
    monkeypatch.setattr(os, "geteuid", lambda: owner)
    assert load_config(link).curator.token_hash == HASH
    if owner == 0:
        os.lchown(link, 4242, -1)
    else:
        monkeypatch.setattr(os, "geteuid", lambda: owner + 1)
    with pytest.raises(ConfigError, match="symbolic link on its way"):
        load_config(link)


def test_the_directory_checked_is_the_real_one_the_file_is_read_from(
    write_config: Write, tmp_path: Path
) -> None:
    shared = tmp_path / "open"
    shared.mkdir()
    real = shared / "aibi.toml"
    real.write_text(minimal())
    real.chmod(0o600)
    (tmp_path / "imports").mkdir(exist_ok=True)
    os.chmod(shared, 0o777)
    (tmp_path / "aibi.toml").symlink_to(real)
    with pytest.raises(ConfigError) as refused:
        load_config(tmp_path / "aibi.toml")
    assert list(refused.value.problems) == [
        f"{tmp_path / 'aibi.toml'}: its group or others can write its directory, and so replace it"
    ]
    os.chmod(shared, 0o700)


def test_a_link_in_a_private_directory_is_followed_and_paths_are_the_real_file_s(
    tmp_path: Path,
) -> None:
    here = tmp_path / "etc"
    (here / "imports").mkdir(parents=True)
    real = here / "aibi.toml"
    real.write_text(minimal())
    real.chmod(0o600)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "linked.toml").symlink_to(Path("..") / "etc" / "aibi.toml")
    config = load_config(elsewhere / "linked.toml")
    assert config.storage.data == here / "data"
    assert config.storage.imports == [here / "imports"]


def test_a_loop_of_links_is_refused_without_hanging(tmp_path: Path) -> None:
    (tmp_path / "a.toml").symlink_to(tmp_path / "b.toml")
    (tmp_path / "b.toml").symlink_to(tmp_path / "a.toml")
    found: list[list[str]] = []

    def loading() -> None:
        try:
            load_config(tmp_path / "a.toml")
        except ConfigError as refused:
            found.append(list(refused.problems))

    thread = threading.Thread(target=loading, daemon=True)
    thread.start()
    thread.join(10)
    assert not thread.is_alive()
    assert found == [[f"{tmp_path / 'a.toml'}: cannot be read ({os.strerror(errno.ELOOP)})"]]


def test_a_link_swapped_in_after_the_path_was_resolved_is_not_followed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    here = tmp_path / "etc"
    (here / "imports").mkdir(parents=True)
    real = here / "aibi.toml"
    real.write_text(minimal())
    real.chmod(0o600)
    swapped = tmp_path / "aibi.toml"
    swapped.symlink_to(real)
    monkeypatch.setattr(config_module, "_resolved", lambda path: (swapped, []))
    with pytest.raises(ConfigError) as refused:
        load_config(swapped)
    assert list(refused.value.problems) == [
        f"{swapped}: cannot be read ({os.strerror(errno.ELOOP)})"
    ]


def test_a_configuration_that_is_a_fifo_is_refused_without_blocking(tmp_path: Path) -> None:
    fifo = tmp_path / "aibi.toml"
    os.mkfifo(fifo, 0o600)
    with pytest.raises(ConfigError, match="not a regular file"):
        load_config(fifo)


def test_connections_per_client_are_fewer_than_connections(write_config: Write) -> None:
    [found] = problems(
        write_config, minimal(server="max_connections = 8\nmax_connections_per_client = 8")
    )
    assert found.startswith("server: max_connections_per_client is fewer than max_connections")
    [found] = problems(write_config, minimal(server="max_connections = 1"))
    assert found.startswith("server.max_connections: ")
    text = minimal(server="max_connections = 8\nmax_connections_per_client = 7")
    assert load_config(write_config(text)).server.connections_per_client == 7
    for connections, per_client in ((2, 1), (7, 1), (8, 2), (100, 25)):
        text = minimal(server=f"max_connections = {connections}")
        assert load_config(write_config(text)).server.connections_per_client == per_client


def test_concurrent_imports_leave_half_the_worker_threads_to_everything_else(
    write_config: Write,
) -> None:
    async def threads() -> float:
        return anyio.to_thread.current_default_thread_limiter().total_tokens

    assert MAX_CONCURRENT_IMPORTS * 2 == WORKER_THREADS == anyio.run(threads)
    [found] = problems(write_config, minimal(imports=f"concurrent = {MAX_CONCURRENT_IMPORTS + 1}"))
    assert found.startswith("imports.concurrent: ")
    text = minimal(imports=f"concurrent = {MAX_CONCURRENT_IMPORTS}")
    assert load_config(write_config(text)).imports.concurrent == MAX_CONCURRENT_IMPORTS


def test_a_configuration_in_a_directory_others_can_write_is_refused_unless_it_is_sticky(
    write_config: Write, tmp_path: Path
) -> None:
    path = write_config(minimal())
    for mode in (0o770, 0o707):
        os.chmod(tmp_path, mode)
        with pytest.raises(ConfigError) as refused:
            load_config(path)
        assert list(refused.value.problems) == [
            f"{path}: its group or others can write its directory, and so replace it"
        ]
    os.chmod(tmp_path, 0o1777)
    assert load_config(path).curator.token_hash == HASH
    os.chmod(tmp_path, 0o700)


def test_model_cards_are_descriptors_each_registered_once(write_config: Write) -> None:
    card = (
        '[[models]]\nkind = "model"\nid = "model:helper"\nversion = "1.0.0"\nlabel = "Helper"\n'
        '[models.fields]\nprovider = "p"\nmodel = "m"\nmodel_version = "1"\n'
        'purpose = ["drafts"]\nlimitations = "Few"\n'
        f'configuration_digest = "{HASH}"\n'
    )
    config = load_config(write_config(minimal() + card))
    assert [model.id for model in config.models] == ["model:helper"]
    assert problems(write_config, minimal() + card + card, name="twice.toml") == [
        "models[1].id: the model card is registered twice"
    ]
    curated = card + '[models.curation."/label"]\nstatus = "asserted"\n'
    assert any(
        problem.startswith("models[0].curation")
        for problem in problems(write_config, minimal() + curated, name="curated.toml")
    )


@pytest.mark.parametrize(
    ("section", "path"),
    [
        ('[databases.sales]\nkind = "postgres"\nurl_env = "X"\npassword = "hunter2"', "password"),
        ('[databases.sales]\nkind = "postgres"', "databases.sales"),
        ('[databases.sales]\nkind = "postgres"\nurl_env = "X"\npath = "x.db"', "databases.sales"),
        ('[databases.old]\nkind = "sqlite"\npath = "elsewhere/old.sqlite"', "databases.old.path"),
        ('[databases.old]\nkind = "sqlite"', "databases.old"),
        ('[databases."Bad Name"]\nkind = "postgres"\nurl_env = "X"', 'databases."Bad Name"'),
    ],
)
def test_database_connections_are_named_shapes_without_credentials(
    write_config: Write, section: str, path: str
) -> None:
    found = problems(write_config, minimal() + section)
    assert any(path in problem for problem in found), found
    assert not any("hunter2" in problem for problem in found)


def test_a_sqlite_file_inside_an_import_directory_is_accepted(write_config: Write) -> None:
    text = minimal() + '[databases.old]\nkind = "duckdb"\npath = "imports/old.duckdb"\n'
    assert load_config(write_config(text)).databases["old"].kind == "duckdb"


def test_the_disclosure_floor_is_at_least_2(write_config: Write) -> None:
    [found] = problems(write_config, minimal(disclosure="min_cell_count_floor = 1"))
    assert found.startswith("disclosure.min_cell_count_floor:")
    config = load_config(write_config(minimal(disclosure="min_cell_count_floor = 5")))
    assert config.disclosure.min_cell_count_floor == 5


@pytest.mark.parametrize(
    ("server", "path"),
    [
        ('public_origins = ["https://elsewhere.example.org"]', "server.public_origins[0]"),
        (
            'hostnames = ["aibi.example.org"]\npublic_origins = ["https://aibi.example.org/x"]',
            "server.public_origins[0]",
        ),
        ('cors_origins = ["notebook.example.org"]', "server.cors_origins[0]"),
        ('hostnames = ["*.example.org"]', "server.hostnames[0]"),
        ('hostnames = ["aibi.example.org:443"]', "server.hostnames[0]"),
    ],
)
def test_hosts_and_origins_are_checked(write_config: Write, server: str, path: str) -> None:
    found = problems(write_config, minimal(server=server))
    assert [problem.partition(":")[0] for problem in found] == [path]


def test_every_problem_is_reported_at_once(write_config: Write) -> None:
    text = minimal(server="bindd = 1\nport = 0", disclosure="min_cell_count_floor = 1")
    found = problems(write_config, text.replace(HASH, "nope"))
    assert [problem.partition(":")[0] for problem in found] == [
        "server.port",
        "server.bindd",
        "curator.token_hash",
        "disclosure.min_cell_count_floor",
    ]


def test_relative_paths_are_the_file_s_directory_s(tmp_path: Path) -> None:
    here = tmp_path / "etc"
    (here / "imports").mkdir(parents=True)
    path = here / "aibi.toml"
    path.write_text(minimal())
    os.chmod(path, 0o600)
    config = load_config(path)
    assert config.storage.data == here / "data"
    assert config.storage.imports == [here / "imports"]


def test_a_file_that_is_no_toml_is_refused(write_config: Write, tmp_path: Path) -> None:
    [found] = problems(write_config, "[server\n")
    assert "not TOML" in found
    with pytest.raises(ConfigError, match="cannot be read"):
        load_config(tmp_path / "missing.toml")

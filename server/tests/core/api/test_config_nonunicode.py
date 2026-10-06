"""The configuration's paths and connections must be Unicode text (D253, D310, D398): the server
does not start with a data or import directory whose real path is not text (UTF-8, and no
noncharacter, as the import's resolve checks it), or a connection whose ``path`` or ``schema`` is
not text. Before D398 a CSV below such an import directory imported; the
remedy is to rename the directory."""

import os
from collections.abc import Callable
from pathlib import Path

import pytest

from aibi.core.api.config import ConfigError, load_config

HASH = "sha256:" + "a" * 64
Write = Callable[..., Path]


def _problems(write_config: Write, storage: str, databases: str = "") -> list[str]:
    text = f'[curator]\ntoken_hash = "{HASH}"\n[storage]\n{storage}\n{databases}'
    with pytest.raises(ConfigError) as refused:
        load_config(write_config(text))
    return list(refused.value.problems)


ENDINGS = [b"\xff", "\uffff".encode()]
"""A name that is not UTF-8, and one that is UTF-8 but holds a noncharacter."""


def _not_text(tmp_path: Path, link: str, ending: bytes) -> None:
    """``link`` in ``tmp_path``, a link to a directory whose name is not text."""
    target = os.path.join(os.fsencode(tmp_path), b"r" + ending + link.encode())
    os.mkdir(target)
    os.symlink(target, tmp_path / link)


@pytest.mark.parametrize("ending", ENDINGS, ids=["not-utf8", "noncharacter"])
def test_an_import_directory_whose_real_path_is_not_text_stops_the_server(
    write_config: Write, tmp_path: Path, ending: bytes
) -> None:
    _not_text(tmp_path, "linked", ending)
    found = _problems(write_config, 'data = "data"\nimports = ["imports", "linked"]')
    assert found == [
        "storage.imports[1]: its real path is not Unicode text; rename the directory (D253, D398)"
    ]


@pytest.mark.parametrize("ending", ENDINGS, ids=["not-utf8", "noncharacter"])
def test_a_data_directory_whose_real_path_is_not_text_stops_the_server(
    write_config: Write, tmp_path: Path, ending: bytes
) -> None:
    _not_text(tmp_path, "data", ending)
    found = _problems(write_config, 'data = "data"\nimports = ["imports"]')
    assert found == [
        "storage.data: its real path is not Unicode text; rename the directory (D253, D398)"
    ]


@pytest.mark.parametrize(
    ("member", "connection", "problem"),
    [
        ("path", 'kind = "sqlite"\npath = "imports/x\\uffff.sqlite"', "path"),
        ("schema", 'kind = "duckdb"\npath = "imports/x.duckdb"\nschema = "s\\ufdd0"', "schema"),
    ],
)
def test_a_connection_s_path_or_schema_that_is_not_text_stops_the_server(
    write_config: Write, member: str, connection: str, problem: str
) -> None:
    found = _problems(
        write_config, 'data = "data"\nimports = ["imports"]', f"[databases.c]\n{connection}\n"
    )
    assert f"databases.c.{problem}: not Unicode text" in found


def test_a_text_configuration_loads(write_config: Write, tmp_path: Path) -> None:
    text = (
        f'[curator]\ntoken_hash = "{HASH}"\n[storage]\ndata = "data"\nimports = ["imports"]\n'
        '[databases.c]\nkind = "duckdb"\npath = "imports/x\\u00e9.duckdb"\nschema = "s\\u00e9"\n'
    )
    assert load_config(write_config(text)).databases["c"].schema_ == "s\u00e9"

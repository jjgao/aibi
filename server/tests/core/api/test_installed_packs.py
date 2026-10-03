"""The packs the server installs from its configuration (SPEC §10.1, D253, D293, D387, D389).

Every test pack here is a module written under ``tmp_path`` with a name of its own
(``aibi_test_pack_…``, unloaded after each test by ``conftest._process_state``), about libraries,
orchards and the weather. Two canaries stand for what a module's code says: a token-shaped one,
which ``WithoutSecrets`` blanks wherever it goes, and a plain one, which it does not, and which
therefore may reach standard error only in a log record the module wrote, never in a problem or
a warning. The tests of DuckDB's refusal run in fresh interpreters, since pytest's process may
have loaded it.
"""

import importlib
import io
import logging
import logging.config
import os
import signal
import socket
import subprocess
import sys
import textwrap
import time
import traceback
import uuid
import warnings
from collections.abc import Callable
from pathlib import Path
from typing import Any, ClassVar, cast

import httpx
import pytest
import uvicorn.config
from fastapi.testclient import TestClient

from aibi.core.api import logs, packs, serve
from aibi.core.api.config import BASE, ConfigError, ServerConfig, load_config
from aibi.core.catalog import index
from aibi.core.operator.auth import hash_token, new_token
from aibi.core.schema import pack_api
from aibi.core.schema.pack_api import Pack, PackError, PackRegistry
from aibi.core.store.store import Store

TOKEN = new_token()
"""The token-shaped canary."""
PLAIN = "CANARY-plain-7f3a"
"""The plain canary, which ``WithoutSecrets`` leaves as it is."""
HASH = hash_token(new_token())
HEADER = """\
import logging
import warnings
from aibi.core.schema.pack_api import Pack, PackManifest


def manifest(id, version="1.0.0"):
    return PackManifest.model_validate(
        {"id": id, "version": version, "results_version": 1, "requires_core": ">=0.0.1"}
    )
"""
"""What every test pack's module starts with."""

Write = Callable[..., Path]
MakePack = Callable[..., str]


@pytest.fixture
def make_pack(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> MakePack:
    """A test pack's module, written from ``source`` after ``HEADER``; its unique name."""
    directory = tmp_path / "packs"
    directory.mkdir()
    monkeypatch.syspath_prepend(str(directory))

    def make(source: str, *, header: bool = True) -> str:
        name = f"aibi_test_pack_{uuid.uuid4().hex}"
        written = (HEADER if header else "") + textwrap.dedent(source)
        (directory / f"{name}.py").write_text(written, encoding="utf-8")
        importlib.invalidate_caches()
        return name

    return make


def config_of(tmp_path: Path, *modules: str) -> ServerConfig:
    (tmp_path / "imports").mkdir(exist_ok=True)
    written: dict[str, Any] = {
        "curator": {"token_hash": HASH},
        "storage": {"data": "data", "imports": ["imports"]},
        "packs": {"modules": list(modules)},
    }
    return ServerConfig.model_validate(written, context={BASE: tmp_path})


def loaded(tmp_path: Path, *modules: str) -> tuple[PackRegistry | None, str]:
    err = io.StringIO()
    registry = packs.registry_of(config_of(tmp_path, *modules), err)
    return registry, err.getvalue()


def no_canary(text: str) -> None:
    assert TOKEN not in text
    assert TOKEN[5:] not in text
    assert PLAIN not in text


def config_text(*modules: str) -> str:
    listed = ", ".join(f'"{name}"' for name in modules)
    return (
        f'[curator]\ntoken_hash = "{HASH}"\n[storage]\ndata = "data"\nimports = ["imports"]\n'
        f"[packs]\nmodules = [{listed}]\n"
    )


def run_main(*argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = serve.main(list(argv), environ={}, stdin=io.StringIO(), stdout=out, stderr=err)
    return code, out.getvalue(), err.getvalue()


LIBRARY = """
PACK = Pack(manifest=manifest("library", "1.2.0"), concepts=())
"""


# --- The configuration --------------------------------------------------------------------------


def test_every_problem_of_packs_is_reported_at_once_with_its_path(write_config: Write) -> None:
    names = ['"m0"', '"m0"', '"caf\u00e9"', "3", '".relative"', '"aibi.core"', '"aibi.core.x"']
    names += ['"a.b."', f'"{"m" * 201}"'] + [f'"n{index}"' for index in range(8)]
    text = (
        '[curator]\ntoken_hash = 5\n[storage]\ndata = "data"\n'
        f"[packs]\nmodules = [{', '.join(names)}]\nextra = 1\n"
    )
    with pytest.raises(ConfigError) as raised:
        load_config(write_config(text))
    assert set(raised.value.problems) == {
        "curator.token_hash: Input should be a valid string",
        "packs.extra: unknown key",
        "packs.modules: at most 16 modules",
        "packs.modules[1]: the module is given twice",
        "packs.modules[2]: not a dotted name of ASCII identifiers, such as 'mypacks.library'",
        "packs.modules[3]: a module's name is a string",
        "packs.modules[4]: not a dotted name of ASCII identifiers, such as 'mypacks.library'",
        "packs.modules[5]: a module of aibi.core is the core's, not a pack's",
        "packs.modules[6]: a module of aibi.core is the core's, not a pack's",
        "packs.modules[7]: not a dotted name of ASCII identifiers, such as 'mypacks.library'",
        "packs.modules[8]: a module's name has at most 200 characters",
    }


@pytest.mark.parametrize(
    ("section", "problem"),
    [
        ("packs = 5\n", "packs: [packs] is a table"),
        ('[packs]\nmodules = "library"\n', "packs.modules: a list of module names"),
    ],
)
def test_packs_of_the_wrong_shape_are_refused(
    write_config: Write, section: str, problem: str
) -> None:
    text = f'{section}[curator]\ntoken_hash = "{HASH}"\n[storage]\ndata = "data"\n'
    with pytest.raises(ConfigError) as raised:
        load_config(write_config(text))
    assert raised.value.problems == (problem,)


def test_no_packs_or_packs_without_modules_install_none(write_config: Write) -> None:
    base = f'[curator]\ntoken_hash = "{HASH}"\n[storage]\ndata = "data"\n'
    assert load_config(write_config(base)).packs.modules == ()
    assert load_config(write_config(base + "[packs]\n")).packs.modules == ()
    named = load_config(write_config(base + '[packs]\nmodules = ["aibi.corex", "a._b1"]\n'))
    assert named.packs.modules == ("aibi.corex", "a._b1")


def test_the_limits_of_modules_hold_at_their_boundary(write_config: Write) -> None:
    sixteen = [f"n{index}" for index in range(16)]
    assert load_config(write_config(config_text(*sixteen))).packs.modules == tuple(sixteen)
    with pytest.raises(ConfigError) as raised:
        load_config(write_config(config_text(*sixteen, "n16")))
    assert raised.value.problems == ("packs.modules: at most 16 modules",)
    longest = "m" * 99 + "." + "n" * 100
    assert len(longest) == 200
    assert load_config(write_config(config_text(longest))).packs.modules == (longest,)
    with pytest.raises(ConfigError) as raised:
        load_config(write_config(config_text(longest + "n")))
    assert raised.value.problems == (
        "packs.modules[0]: a module's name has at most 200 characters",
    )


# --- One module's failures ----------------------------------------------------------------------


def test_a_module_that_raises_at_import_fails_with_its_type_alone(
    tmp_path: Path, make_pack: MakePack
) -> None:
    name = make_pack(f'raise ValueError("{TOKEN} {PLAIN}")\n')
    registry, err = loaded(tmp_path, name)
    assert registry is None
    assert (
        f"  the module {name} failed to import: it raised ValueError "
        f"(python -c 'import {name}' shows why)\n" in err
    )
    no_canary(err)


@pytest.mark.parametrize(
    ("source", "problem"),
    [
        ("raise SystemExit(3)\n", "asked to exit while it was imported"),
        (
            f"class Stop(KeyboardInterrupt):\n    pass\nraise Stop('{PLAIN}')\n",
            "failed to import: it raised an exception of its own",
        ),
        (
            "class Meta(type):\n"
            "    def __eq__(cls, other):\n"
            f"        raise ValueError('{PLAIN}')\n"
            "    def __hash__(cls):\n"
            f"        raise ValueError('{PLAIN}')\n"
            "    @property\n"
            "    def __name__(cls):\n"
            f"        raise ValueError('{PLAIN}')\n"
            "class Odd(ValueError, metaclass=Meta):\n    pass\n"
            f"raise Odd('{PLAIN}')\n",
            "failed to import: it raised an exception of its own",
        ),
    ],
    ids=["exit", "interrupt-subclass", "metaclass"],
)
def test_what_a_module_raises_at_import_is_its_failure(
    tmp_path: Path, make_pack: MakePack, source: str, problem: str
) -> None:
    name = make_pack(source)
    try:
        registry, err = loaded(tmp_path, name)
    except BaseException as error:  # a passed subclass would stop the whole session
        pytest.fail(f"{type(error).__name__} passed")
    assert registry is None
    assert f"  the module {name} {problem}" in err
    no_canary(err)


@pytest.mark.parametrize(
    ("kind", "code", "said"),
    [
        (KeyboardInterrupt, 130, "aibi-server: interrupted\n"),
        (MemoryError, 1, "aibi-server: out of memory while loading packs\n"),
    ],
)
def test_an_interrupt_or_memory_at_import_passes_as_a_new_instance(
    tmp_path: Path,
    make_pack: MakePack,
    write_config: Write,
    kind: type[BaseException],
    code: int,
    said: str,
) -> None:
    name = make_pack(f"warnings.warn('{PLAIN}')\nraise {kind.__name__}('{PLAIN}')\n")
    with pytest.raises(kind) as raised:
        packs.registry_of(config_of(tmp_path, name), io.StringIO())
    assert type(raised.value) is kind
    assert raised.value.args == ()
    assert raised.value.__context__ is None
    assert raised.value.__cause__ is None
    sys.modules.pop(name, None)
    found, out, err = run_main("check", "--config", str(write_config(config_text(name))))
    assert (found, out, err) == (code, "", said)


@pytest.mark.parametrize(
    ("source", "problem"),
    [
        ("", "has no pack: it defines no PACK"),
        (
            f"def __getattr__(name):\n    raise ValueError('{PLAIN}')\n",
            "has no pack: it defines no PACK",
        ),
        (
            "from dataclasses import dataclass\n"
            "@dataclass(frozen=True)\n"
            "class Mine(Pack):\n    pass\n"
            'PACK = Mine(manifest=manifest("library"))\n',
            "is not a pack: its PACK is not exactly a Pack",
        ),
        ("PACK = object()\n", "is not a pack: its PACK is not exactly a Pack"),
        (
            "class Key(str):\n"
            "    def __eq__(self, other):\n"
            f"        raise ValueError('{PLAIN}')\n"
            "    __hash__ = str.__hash__\n"
            'globals()[Key("PACK")] = Pack(manifest=manifest("library"))\n',
            "has no pack: its namespace is not a plain mapping of names",
        ),
        (
            "import sys\nsys.modules[__name__] = object()\n",
            "has no pack: it is not a plain module",
        ),
        (
            "import sys, types\n"
            "class Swapped(types.ModuleType):\n"
            "    @property\n"
            "    def __dict__(self):\n"
            f"        raise ValueError('{PLAIN}')\n"
            'PACK = Pack(manifest=manifest("library"))\n'
            "sys.modules[__name__].__class__ = Swapped\n",
            "has no pack: it is not a plain module",
        ),
    ],
    ids=["none", "getattr", "subclass", "object", "str-key", "replaced", "swapped-class"],
)
def test_a_module_without_exactly_a_pack_is_refused_without_running_its_code(
    tmp_path: Path, make_pack: MakePack, source: str, problem: str
) -> None:
    name = make_pack(source)
    registry, err = loaded(tmp_path, name)
    assert registry is None
    assert f"  the module {name} {problem}\n" in err
    no_canary(err)


def test_a_module_that_replaces_its_entry_with_another_module_gives_that_module_s_pack(
    tmp_path: Path, make_pack: MakePack
) -> None:
    name = make_pack(
        "import sys, types\n"
        "other = types.ModuleType('other')\n"
        'other.PACK = Pack(manifest=manifest("orchard"))\n'
        "sys.modules[__name__] = other\n"
    )
    registry, err = loaded(tmp_path, name)
    assert registry is not None, err
    assert registry.ids == ("orchard",)


def test_a_pack_whose_members_raise_has_its_problems_named_by_its_module(
    tmp_path: Path, make_pack: MakePack
) -> None:
    name = make_pack(
        "class Schemas(dict):\n"
        "    def items(self):\n"
        f"        raise ValueError('{TOKEN} {PLAIN}')\n"
        'PACK = Pack(manifest=manifest("library"), extension_schemas=Schemas(dataset={}),\n'
        '            leaf_kinds={"elsewhere.kind": object()})\n'
    )
    registry, err = loaded(tmp_path, name)
    assert registry is None
    label = f"the module {name} (pack library)"
    assert f"  {label}: its extension schemas could not be read\n" in err
    assert f"  {label}: leaf kind elsewhere.kind is not library.<name>\n" in err
    no_canary(err)


def test_a_pack_whose_manifest_cannot_be_read_is_named_by_its_module(
    tmp_path: Path, make_pack: MakePack
) -> None:
    name = make_pack("PACK = Pack(manifest=None)\n")
    registry, err = loaded(tmp_path, name)
    assert registry is None
    assert f"  the module {name}: its manifest" in err


def test_labels_name_one_module_for_each_pack() -> None:
    pack = Pack(
        manifest=pack_api.PackManifest(
            id="library", version="1.0.0", results_version=1, requires_core=">=0.0.1"
        )
    )
    for labels in (["a", "b"], [], [pack_api.PackManifest]):
        with pytest.raises(ValueError, match="labels name one module"):
            PackRegistry([pack], core_version="0.0.1", labels=labels)  # type: ignore[list-item]


# --- What a module's code says ------------------------------------------------------------------


def test_warnings_are_counted_and_log_records_filtered_at_import(
    tmp_path: Path, make_pack: MakePack
) -> None:
    name = make_pack(
        f'warnings.warn("{TOKEN} {PLAIN}")\n'
        f'warnings.warn("{TOKEN} {PLAIN}", DeprecationWarning)\n'
        f'logging.getLogger("orchard.child").warning("logged %s %s", "{TOKEN}", "{PLAIN}")\n'
        'quiet = logging.getLogger("orchard.quiet")\n'
        "quiet.propagate = False\n"
        f'quiet.warning("quietly {TOKEN} {PLAIN}")\n'
        'PACK = Pack(manifest=manifest("orchard"))\n'
    )
    registry, err = loaded(tmp_path, name)
    assert registry is not None
    assert "aibi-server: 2 warnings while the packs were loaded, not shown\n" in err
    assert f"logged <secret> {PLAIN}\n" in err
    assert f"quietly <secret> {PLAIN}\n" in err
    assert TOKEN not in err
    assert TOKEN[5:] not in err
    assert err.count(PLAIN) == 2


def test_warnings_raised_while_a_pack_is_registered_are_counted_never_shown(
    tmp_path: Path, make_pack: MakePack
) -> None:
    name = make_pack(
        "class Schemas(dict):\n"
        "    def items(self):\n"
        f'        warnings.warn("{TOKEN} {PLAIN}")\n'
        "        return super().items()\n"
        'PACK = Pack(manifest=manifest("orchard"), extension_schemas=Schemas())\n'
    )
    registry, err = loaded(tmp_path, name)
    assert registry is not None
    assert "aibi-server: 1 warning while the packs were loaded, not shown\n" in err
    no_canary(err)


def test_a_module_s_warnings_filters_do_not_outlast_its_import(
    tmp_path: Path, make_pack: MakePack
) -> None:
    before = list(warnings.filters)
    name = make_pack(
        'warnings.filterwarnings("ignore", message="orchard")\n'
        "warnings.showwarning = print\n"
        'PACK = Pack(manifest=manifest("orchard"))\n'
    )
    registry, _ = loaded(tmp_path, name)
    assert registry is not None
    assert warnings.filters == before
    assert warnings.showwarning is not print


def test_the_filtered_handlers_write_to_the_given_stream_once(
    tmp_path: Path, make_pack: MakePack, monkeypatch: pytest.MonkeyPatch
) -> None:
    elsewhere = io.StringIO()
    monkeypatch.setattr(sys, "stderr", elsewhere)
    name = make_pack(
        f'logging.getLogger("weather").warning("rain {TOKEN}")\n'
        'PACK = Pack(manifest=manifest("weather"))\n'
    )
    err = io.StringIO()
    config = config_of(tmp_path, name)
    assert packs.registry_of(config, err) is not None
    assert packs.registry_of(config, err) is not None
    logging.getLogger("weather").warning("again %s", TOKEN)
    written = err.getvalue()
    assert written.count("rain <secret>\n") == 1
    assert written.count("again <secret>\n") == 1
    assert elsewhere.getvalue() == ""
    roots = [one for one in logging.getLogger().handlers if type(one) is logs.RootHandler]
    assert len(roots) == 1


def test_the_last_resort_stays_filtered_after_uvicorn_s_logging(tmp_path: Path) -> None:
    err = io.StringIO()
    assert packs.registry_of(config_of(tmp_path), err) is not None
    resort = logging.lastResort
    assert type(resort) is logs.LastResort
    logging.config.dictConfig(logs.logging_config(dict(uvicorn.config.LOGGING_CONFIG)))
    assert logging.lastResort is resort
    quiet = logging.getLogger("weather.quiet")
    quiet.propagate = False
    try:
        quiet.warning("late %s %s", TOKEN, PLAIN)
    finally:
        quiet.propagate = True
    assert f"late <secret> {PLAIN}\n" in err.getvalue()
    assert TOKEN not in err.getvalue()


def test_the_filtered_handlers_move_to_the_stream_given_last(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    elsewhere = io.StringIO()
    monkeypatch.setattr(sys, "stderr", elsewhere)
    first, second = io.StringIO(), io.StringIO()
    config = config_of(tmp_path)
    assert packs.registry_of(config, first) is not None
    assert packs.registry_of(config, second) is not None
    logging.getLogger("weather").warning("rain %s", TOKEN)
    quiet = logging.getLogger("weather.quiet")
    quiet.propagate = False
    quiet.warning("snow %s", TOKEN)
    assert first.getvalue() == ""
    assert "rain <secret>\n" in second.getvalue()
    assert "snow <secret>\n" in second.getvalue()
    assert elsewhere.getvalue() == ""
    roots = [one for one in logging.getLogger().handlers if type(one) is logs.RootHandler]
    assert len(roots) == 1
    assert type(logging.lastResort) is logs.LastResort


def test_a_hook_s_warning_at_run_time_goes_to_logging_filtered(
    tmp_path: Path, make_pack: MakePack, monkeypatch: pytest.MonkeyPatch
) -> None:
    elsewhere = io.StringIO()
    monkeypatch.setattr(sys, "stderr", elsewhere)
    name = make_pack(
        f"""
        class Leaf:
            @property
            def schema(self):
                return {{"type": "object"}}

            def compile(self, leaf, release, pack_version):
                warnings.warn("{TOKEN} {PLAIN}")
                return []

            def summary(self, leaf):
                return []

        PACK = Pack(manifest=manifest("weather"), leaf_kinds={{"weather.leaf": Leaf()}})
        """
    )
    err = io.StringIO()
    registry = packs.registry_of(config_of(tmp_path, name), err)
    assert registry is not None
    before = len(err.getvalue())
    with warnings.catch_warnings():
        warnings.simplefilter("always")
        hook = registry.leaf_kind("weather.leaf")
        assert hook is not None
        hook.call(lambda leaf: cast(Any, leaf).compile(None, None, "1.0.0"))
    later = err.getvalue()[before:]
    assert f"UserWarning: <secret> {PLAIN}\n" in later
    assert TOKEN not in later
    assert TOKEN[5:] not in later
    assert elsewhere.getvalue() == ""


def _bad_access_record(logger: logging.Logger) -> None:
    """A record of uvicorn's access logger that does not format, even blanked."""
    logger.warning("%s %s %s %s %s %s " + PLAIN, TOKEN, PLAIN, 3, 4, 5)


@pytest.mark.parametrize("propagate", [True, False], ids=["root", "last-resort"])
def test_a_record_that_cannot_be_written_is_one_line_of_the_core_s(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, propagate: bool
) -> None:
    elsewhere = io.StringIO()
    monkeypatch.setattr(sys, "stderr", elsewhere)
    err = io.StringIO()
    assert packs.registry_of(config_of(tmp_path), err) is not None
    root = logging.getLogger()
    access = logging.getLogger(logs.ACCESS)
    access.handlers[:] = []
    access.propagate = propagate
    saved = root.handlers[:]
    # Only the core's handler: pytest's own fails a test whose record cannot be written.
    root.handlers[:] = [one for one in saved if type(one) is logs.RootHandler]
    try:
        _bad_access_record(access)
    finally:
        root.handlers[:] = saved
    assert err.getvalue() == logs.NOT_WRITTEN
    assert elsewhere.getvalue() == ""


@pytest.mark.parametrize("made", [logs.RootHandler, logs.LastResort, logs.StreamHandler])
def test_each_quiet_handler_reports_an_error_by_its_line_alone(
    monkeypatch: pytest.MonkeyPatch, made: type[logs.StreamHandler]
) -> None:
    elsewhere = io.StringIO()
    monkeypatch.setattr(sys, "stderr", elsewhere)
    stream = io.StringIO()
    handler = made(stream)
    record = logging.LogRecord("o", logging.WARNING, __file__, 1, f"{PLAIN} %d", (TOKEN,), None)
    handler.handle(record)
    assert stream.getvalue() == logs.NOT_WRITTEN
    assert elsewhere.getvalue() == ""


class _Failing(io.StringIO):
    """A stream whose writes, or flushes, fail, counting its attempts."""

    def __init__(self, *, flush_fails: bool = False) -> None:
        super().__init__()
        self.flush_fails = flush_fails
        self.attempts = 0

    def write(self, text: str, /) -> int:
        self.attempts += 1
        if not self.flush_fails:
            raise OSError(f"disk full {TOKEN}")
        return super().write(text)

    def flush(self) -> None:
        if self.flush_fails:
            raise OSError(f"disk full {TOKEN}")


@pytest.mark.parametrize("flush_fails", [False, True], ids=["write", "flush"])
@pytest.mark.parametrize("made", [logs.RootHandler, logs.LastResort, logs.StreamHandler])
def test_a_quiet_handler_whose_stream_fails_raises_nothing_and_reports_nothing_there(
    monkeypatch: pytest.MonkeyPatch, made: type[logs.StreamHandler], flush_fails: bool
) -> None:
    elsewhere = io.StringIO()
    monkeypatch.setattr(sys, "stderr", elsewhere)
    stream = _Failing(flush_fails=flush_fails)
    handler = made(stream)
    record = logging.LogRecord("o", logging.WARNING, __file__, 1, f"{PLAIN} %d", (TOKEN,), None)
    handler.handle(record)
    assert stream.attempts >= 1
    assert stream.getvalue() == (logs.NOT_WRITTEN if flush_fails else "")
    assert elsewhere.getvalue() == ""


class _Bare(logs.Quiet):
    """A ``Quiet`` handler with no stream, which reports every record as one it could not write."""

    def emit(self, record: logging.LogRecord) -> None:
        self.handleError(record)


def test_a_quiet_handler_with_no_stream_writes_nothing_and_raises_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    elsewhere = io.StringIO()
    monkeypatch.setattr(sys, "stderr", elsewhere)
    handler = _Bare()
    assert not hasattr(handler, "stream")
    record = logging.LogRecord("o", logging.WARNING, __file__, 1, f"{PLAIN} %d", (TOKEN,), None)
    handler.handle(record)
    assert elsewhere.getvalue() == ""


def test_the_last_resort_writes_warnings_and_above_only(tmp_path: Path) -> None:
    err = io.StringIO()
    assert packs.registry_of(config_of(tmp_path), err) is not None
    assert logging.lastResort is not None
    assert logging.lastResort.level == logging.WARNING
    quiet = logging.getLogger("weather.resort")
    quiet.propagate = False
    quiet.setLevel(logging.INFO)
    assert quiet.handlers == []
    quiet.info("fog")
    assert err.getvalue() == ""
    quiet.warning("rain")
    assert err.getvalue() == "rain\n"


def test_installing_the_finder_twice_leaves_one() -> None:
    first = packs.install_finder()
    assert packs.install_finder() is first
    assert [one for one in sys.meta_path if type(one) is packs.NoDuckDB] == [first]
    assert sys.meta_path[0] is first


# --- Several modules ----------------------------------------------------------------------------


def test_one_bad_module_fails_the_start_and_hides_no_other_problem(
    tmp_path: Path, make_pack: MakePack
) -> None:
    good = make_pack(LIBRARY)
    bad = make_pack("raise ValueError()\n")
    worse = make_pack("")
    registry, err = loaded(tmp_path, bad, good, worse)
    assert registry is None
    assert f"  the module {bad} failed to import: it raised ValueError" in err
    assert f"  the module {worse} has no pack: it defines no PACK\n" in err
    assert good not in err


def test_two_modules_that_register_one_pack_are_both_named(
    tmp_path: Path, make_pack: MakePack
) -> None:
    first = make_pack(
        'PACK = Pack(manifest=manifest("orchard"), ontology_systems={"TREES": bool})\n'
    )
    second = make_pack(
        'PACK = Pack(manifest=manifest("orchard"), ontology_systems={"TREES": bool})\n'
    )
    third = make_pack(
        'PACK = Pack(manifest=manifest("weather"), ontology_systems={"TREES": bool})\n'
    )
    registry, err = loaded(tmp_path, first, second, third)
    assert registry is None
    assert f"  pack orchard is registered by the modules {first} and {second}\n" in err
    assert (
        f"  ontology system TREES is registered by the module {first} (pack orchard) and the "
        f"module {third} (pack weather)\n" in err
    )


ANALYSES = """
from aibi.core.schema.descriptors import AnalysisDescriptor


class Rates:
    def __init__(self, id, cited):
        self.id, self.cited = id, cited

    @property
    def entry(self):
        return AnalysisDescriptor.model_validate({
            "kind": "analysis", "id": self.id, "version": "1.0.0", "label": "Loan rates",
            "fields": {
                "requires": [{"role": "cohorts", "min": 1, "max": 6}],
                "params": {"type": "object"}, "returns": {"type": "object"},
                "methods": {}, "assumptions": [], "uses_reference": False,
                "assumes_independent_groups": True, "cross_dataset": None,
                "caveats": self.cited,
            },
        })

    def run(self, inputs):
        return {}
"""


def test_a_pack_may_cite_a_code_another_module_s_pack_declares(
    tmp_path: Path, make_pack: MakePack
) -> None:
    shelf = make_pack(
        ANALYSES + 'PACK = Pack(manifest=manifest("shelf"), '
        'analyses=(Rates("shelf.rates", ["stock.SHARED"]),))\n'
    )
    stock = make_pack(
        "from aibi.core.schema.caveats import Severity\n"
        'PACK = Pack(manifest=manifest("stock"), caveat_codes={"stock.SHARED": Severity.WARN})\n'
    )
    registry, err = loaded(tmp_path, shelf, stock)
    assert registry is not None, err
    assert registry.ids == ("shelf", "stock")
    registry, err = loaded(tmp_path, shelf)
    assert registry is None
    assert (
        f"  the module {shelf} (pack shelf): analysis shelf.rates cites stock.SHARED, which no "
        "registered pack declares\n" in err
    )


def test_the_modules_order_changes_nothing_installed(tmp_path: Path, make_pack: MakePack) -> None:
    first = make_pack(LIBRARY)
    second = make_pack(
        "from aibi.core.schema.descriptors import ConceptDescriptor\n"
        "PACK = Pack(manifest=manifest('orchard', '2.0'), concepts=(ConceptDescriptor(\n"
        "    kind='concept', id='orchard:ripe', version=1, label='Ripe',\n"
        "    fields={'sort': 'value'}),))\n"
    )
    one, _ = loaded(tmp_path, first, second)
    other, _ = loaded(tmp_path, second, first)
    assert one is not None
    assert other is not None
    assert one.ids == other.ids == ("library", "orchard")
    for pack_id in one.ids:
        assert one.pack(pack_id).manifest == other.pack(pack_id).manifest
    assert one.concepts() == other.concepts()
    assert index.basis("m", 5, one) == index.basis("m", 5, other)
    assert (
        packs.listing(one)
        == packs.listing(other)
        == [
            "library 1.2.0 (results 1)",
            "orchard 2.0 (results 1)",
        ]
    )


def test_a_pack_asking_to_exit_at_registration_fails_the_start_naming_its_module(
    tmp_path: Path, make_pack: MakePack
) -> None:
    bad = make_pack("raise ValueError()\n")
    first = make_pack(
        'PACK = Pack(manifest=manifest("library"), extension_schemas={"nothing": {}})\n'
    )
    stopping = make_pack(
        "class Schemas(dict):\n"
        "    def items(self):\n"
        "        raise SystemExit(4)\n"
        'PACK = Pack(manifest=manifest("orchard"), extension_schemas=Schemas())\n'
    )
    registry, err = loaded(tmp_path, bad, first, stopping)
    assert registry is None
    assert f"  the module {bad} failed to import" in err
    assert (
        f"  the module {stopping} (pack orchard): its code asked to exit while the packs were "
        "registered, so the registration's other problems are not known\n" in err
    )
    assert "nothing" not in err


def test_an_interrupt_at_registration_passes(
    tmp_path: Path, make_pack: MakePack, write_config: Write
) -> None:
    name = make_pack(
        "class Schemas(dict):\n"
        "    def items(self):\n"
        f"        raise KeyboardInterrupt('{PLAIN}')\n"
        'PACK = Pack(manifest=manifest("orchard"), extension_schemas=Schemas())\n'
    )
    path = write_config(config_text(name))
    assert run_main("check", "--config", str(path)) == (130, "", "aibi-server: interrupted\n")


def test_a_core_bug_while_a_pack_s_problem_is_written_has_no_pack_context(
    tmp_path: Path, make_pack: MakePack, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(*given: object) -> str:
        raise RuntimeError("a core bug")

    monkeypatch.setattr(pack_api._Reads, "_problem", broken)  # pyright: ignore[reportPrivateUsage]
    name = make_pack(
        "class Schemas(dict):\n"
        "    def items(self):\n"
        f"        raise ValueError('{TOKEN} {PLAIN}')\n"
        'PACK = Pack(manifest=manifest("orchard"), extension_schemas=Schemas())\n'
    )
    with pytest.raises(RuntimeError) as raised:
        packs.registry_of(config_of(tmp_path, name), io.StringIO())
    assert raised.value.__context__ is None
    written = "".join(traceback.format_exception(raised.value))
    assert "a core bug" in written
    no_canary(written)


def test_only_the_registry_s_own_pack_error_is_its_problem(
    tmp_path: Path, make_pack: MakePack, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(*given: object, **named: object) -> PackRegistry:
        raise PackError(["the registry's own"])

    monkeypatch.setattr(packs, "PackRegistry", broken)
    registry, err = loaded(tmp_path, make_pack(LIBRARY))
    assert registry is None
    assert "  the registry's own\n" in err


# --- Wiring -------------------------------------------------------------------------------------


class _Stopped:
    """uvicorn's server, stopped as soon as it runs; the application it was given kept."""

    apps: ClassVar[list[Any]] = []

    def __init__(self, config: Any) -> None:
        self.config = config

    def run(self) -> None:
        _Stopped.apps.append(self.config.app)


def test_a_failed_start_opens_no_store(
    make_pack: MakePack, write_config: Write, monkeypatch: pytest.MonkeyPatch
) -> None:
    opened: list[Path] = []

    def store(path: Path, **given: Any) -> Store:
        opened.append(path)
        raise AssertionError("the store was opened")

    monkeypatch.setattr(serve, "Store", store)
    path = write_config(config_text(make_pack("raise ValueError()\n")))
    code, _, err = run_main("serve", "--config", str(path))
    assert code == 2
    assert "aibi-server: the packs are refused:\n" in err
    assert opened == []
    code, out, err = run_main("check", "--config", str(path))
    assert (code, out) == (2, "")
    assert "failed to import" in err


def test_the_packs_are_imported_before_the_umask_is_set(
    make_pack: MakePack, write_config: Write, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[int] = []

    def store(path: Path, **given: Any) -> Store:
        mask = os.umask(0o077)
        os.umask(mask)
        seen.append(mask)
        raise serve.StoreLockedError

    monkeypatch.setattr(serve, "Store", store)
    name = make_pack("import os\nos.umask(0o002)\n" + LIBRARY)
    path = write_config(config_text(name))
    before = os.umask(0o022)
    try:
        assert run_main("serve", "--config", str(path))[0] == 1
    finally:
        os.umask(before)
    assert seen == [0o077]


def test_check_and_serve_list_the_installed_packs(
    make_pack: MakePack, write_config: Write, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = write_config(config_text(make_pack(LIBRARY)))
    code, out, _ = run_main("check", "--config", str(path))
    assert code == 0
    assert out.endswith("pack: library 1.2.0 (results 1)\n")
    monkeypatch.setattr(serve.uvicorn, "Server", _Stopped)
    code, _, err = run_main("serve", "--config", str(path))
    assert code == 0
    assert "aibi-server: pack: library 1.2.0 (results 1)\n" in err
    empty = write_config(config_text(), name="empty.toml")
    assert run_main("check", "--config", str(empty))[1].endswith("packs: none\n")


def test_the_services_and_tools_take_the_installed_registry(
    make_pack: MakePack, write_config: Write, monkeypatch: pytest.MonkeyPatch
) -> None:
    name = make_pack(
        ANALYSES + 'PACK = Pack(manifest=manifest("shelf"), '
        'analyses=(Rates("shelf.rates", ["SMALL_N"]),))\n'
    )
    built: list[Any] = []
    create_app = serve.create_app

    def recorded(policy: Any, services: Any, **given: Any) -> Any:
        built.append((services, given["tools"]))
        return create_app(policy, services, **given)

    monkeypatch.setattr(serve, "create_app", recorded)
    monkeypatch.setattr(serve.uvicorn, "Server", _Stopped)
    _Stopped.apps.clear()
    path = write_config(config_text(name))
    assert run_main("serve", "--config", str(path))[0] == 0
    ((services, tools),) = built
    assert services.registry.ids == ("shelf",)
    assert tools.catalog.registry is services.registry
    with TestClient(_Stopped.apps[0], base_url="http://127.0.0.1:8000") as client:
        listed = client.post("/api/tools/list_analyses", json={}).json()
    assert "shelf.rates" in [one["id"] for one in listed["analyses"]]


# --- DuckDB (D293) ------------------------------------------------------------------------------


@pytest.mark.parametrize("fullname", ["duckdb", "_duckdb", "duckdb.typing", "_duckdb.functional"])
def test_the_finder_refuses_duckdb_by_raising(fullname: str) -> None:
    finder = packs.NoDuckDB()
    with pytest.raises(ImportError, match=r"does not load duckdb \(D293\)"):
        finder.find_spec(fullname, None)
    assert finder.refused == 1


@pytest.mark.parametrize(
    "fullname",
    ["duckdb_extension_postgres_scanner", "adbc_driver_duckdb", "sqlglot.dialects.duckdb", "duck"],
)
def test_the_finder_leaves_other_modules(fullname: str) -> None:
    assert packs.NoDuckDB().find_spec(fullname, None) is None


def _fresh(
    tmp_path: Path, write_config: Write, probe: str, *sources: str
) -> subprocess.CompletedProcess[str]:
    """``probe`` run in a fresh interpreter, ``CONFIG`` the path of a configuration naming the
    test pack modules written from ``sources``, which ``PACKS`` holds."""
    directory = tmp_path / "fresh"
    directory.mkdir(exist_ok=True)
    names: list[str] = []
    for source in sources:
        name = f"aibi_test_pack_{uuid.uuid4().hex}"
        (directory / f"{name}.py").write_text(HEADER + textwrap.dedent(source), encoding="utf-8")
        names.append(name)
    path = write_config(config_text(*names))
    script = (
        f"import sys\nsys.path.insert(0, {str(directory)!r})\nCONFIG = {str(path)!r}\n"
        f"PACKS = {names!r}\n" + textwrap.dedent(probe)
    )
    return subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=120, check=False
    )


CHECK = """
from aibi.core.api.serve import main
code = main(["check", "--config", CONFIG])
print("status", code, "duckdb" in sys.modules, "_duckdb" in sys.modules)
"""


@pytest.mark.parametrize("module", ["duckdb", "_duckdb", "duckdb.typing"])
def test_a_module_that_loads_duckdb_fails_the_start(
    tmp_path: Path, write_config: Write, module: str
) -> None:
    ran = _fresh(tmp_path, write_config, CHECK, f"import {module}\n" + LIBRARY)
    assert ran.stdout.endswith("status 2 False False\n"), ran.stderr
    assert "failed to import: it raised ImportError" in ran.stderr
    assert "it tried to load duckdb, and the server's process does not load duckdb (D293)" in (
        ran.stderr
    )


def test_a_hook_that_loads_duckdb_at_run_time_fails_in_its_guard(
    tmp_path: Path, write_config: Write
) -> None:
    probe = """
    import io
    from aibi.core.api import packs
    from aibi.core.api.config import load_config
    from aibi.core.schema.guards import PackFailed
    registry = packs.registry_of(load_config(__import__("pathlib").Path(CONFIG)), sys.stderr)
    hook = registry.leaf_kind("lazy.leaf")
    try:
        hook.call(lambda leaf: leaf.compile(None, None, "1.0.0"))
    except PackFailed:
        print("PACK_FAILED")
    try:
        import _duckdb
    except ImportError:
        print("refused")
    print("duckdb" in sys.modules, "_duckdb" in sys.modules)
    """
    leaf = """
    class Leaf:
        @property
        def schema(self):
            return {"type": "object"}

        def compile(self, leaf, release, pack_version):
            import duckdb
            return []

        def summary(self, leaf):
            return []

    PACK = Pack(manifest=manifest("lazy"), leaf_kinds={"lazy.leaf": Leaf()})
    """
    ran = _fresh(tmp_path, write_config, probe, leaf)
    assert ran.stdout == "PACK_FAILED\nrefused\nFalse False\n", ran.stderr


def test_a_server_with_packs_or_none_starts_without_duckdb_importing_each_once(
    tmp_path: Path, write_config: Write
) -> None:
    counted = tmp_path / "imported"
    source = f"with open({str(counted)!r}, 'a') as f:\n    f.write('once\\n')\n" + LIBRARY
    ran = _fresh(tmp_path, write_config, CHECK, source)
    assert ran.stdout.endswith("pack: library 1.2.0 (results 1)\nstatus 0 False False\n")
    assert counted.read_text() == "once\n"
    ran = _fresh(tmp_path, write_config, CHECK)
    assert ran.stdout.endswith("packs: none\nstatus 0 False False\n"), ran.stderr


def test_a_module_s_duckdb_note_is_its_own(tmp_path: Path, write_config: Write) -> None:
    probe = 'print("packs", *PACKS)\n' + CHECK
    ran = _fresh(tmp_path, write_config, probe, "import duckdb\n" + LIBRARY, "raise ValueError()\n")
    _, first, second = ran.stdout.splitlines()[0].split(" ")
    assert ran.stdout.endswith("status 2 False False\n"), ran.stderr
    assert (
        f"  the module {first} failed to import: it raised ImportError (python -c 'import "
        f"{first}' shows why); it tried to load duckdb, and {packs.REFUSED_DUCKDB}\n"
    ) in ran.stderr
    assert (
        f"  the module {second} failed to import: it raised ValueError (python -c 'import "
        f"{second}' shows why)\n"
    ) in ran.stderr


def test_duckdb_blocked_by_a_none_entry_is_not_loaded(tmp_path: Path, write_config: Write) -> None:
    probe = 'sys.modules["duckdb"] = None\nsys.modules["_duckdb"] = None\n' + CHECK
    ran = _fresh(tmp_path, write_config, probe, LIBRARY)
    assert ran.stdout.endswith("pack: library 1.2.0 (results 1)\nstatus 0 True True\n"), ran.stderr
    assert packs.LOADED_DUCKDB not in ran.stderr


@pytest.mark.parametrize(
    ("submodule", "refused"),
    [
        ("duckdb.typing", True),
        ("_duckdb.functional", True),
        ("duckdb_extension_postgres_scanner", False),
    ],
)
def test_a_submodule_of_duckdb_loaded_is_duckdb_loaded_even_beside_none_entries(
    tmp_path: Path, write_config: Write, submodule: str, refused: bool
) -> None:
    probe = (
        "import types\n"
        'sys.modules["duckdb"] = None\nsys.modules["_duckdb"] = None\n'
        f"sys.modules[{submodule!r}] = types.ModuleType({submodule!r})\n" + CHECK
    )
    ran = _fresh(tmp_path, write_config, probe, LIBRARY)
    if refused:
        assert ran.stdout.startswith("status 2 "), ran.stderr
        assert f"  {packs.LOADED_DUCKDB}\n" in ran.stderr
    else:
        assert ran.stdout.endswith("status 0 True True\n"), ran.stderr
        assert packs.LOADED_DUCKDB not in ran.stderr


def test_a_record_a_module_logs_that_does_not_format_reaches_no_raw_stderr(
    tmp_path: Path, write_config: Write
) -> None:
    source = f"""
    logging.getLogger("orchard").warning("{TOKEN}", 1, 2, 3, 4, 5)  # {PLAIN}
    quiet = logging.getLogger("orchard.quiet")
    quiet.propagate = False
    quiet.warning("lost %d %d %d %d %d", "{PLAIN}", "{TOKEN}", "c", "d", "e")  # {TOKEN} {PLAIN}
    access = logging.getLogger("uvicorn.access")
    access.warning("%s %s %s %s %s %s", "{TOKEN}", "{PLAIN}", 3, 4, 5)  # {TOKEN} {PLAIN}
    access.propagate = False
    access.warning("%s %s %s %s %s %s", "{TOKEN}", "{PLAIN}", 3, 4, 5)  # {TOKEN} {PLAIN}
    PACK = Pack(manifest=manifest("orchard"))
    """
    ran = _fresh(tmp_path, write_config, CHECK, source)
    assert ran.stdout.endswith("status 0 False False\n"), ran.stderr
    assert "<secret>\n" in ran.stderr
    assert "lost %d %d %d %d %d\n" in ran.stderr
    assert ran.stderr.count(logs.NOT_WRITTEN) == 2
    assert "--- Logging error ---" not in ran.stderr
    assert "Traceback" not in ran.stderr
    no_canary(ran.stderr)


def test_a_fresh_server_with_a_pack_serves_and_stops(tmp_path: Path) -> None:
    directory = tmp_path / "fresh"
    directory.mkdir()
    name = f"aibi_test_pack_{uuid.uuid4().hex}"
    (directory / f"{name}.py").write_text(HEADER + LIBRARY, encoding="utf-8")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
    (tmp_path / "imports").mkdir()
    config = tmp_path / "aibi.toml"
    config.write_text(f"[server]\nport = {port}\n" + config_text(name), encoding="utf-8")
    config.chmod(0o600)
    probe = (
        f"import sys\nsys.path.insert(0, {str(directory)!r})\n"
        "from aibi.core.api.serve import main\nsys.exit(main(sys.argv[1:]))\n"
    )
    server = subprocess.Popen(
        [sys.executable, "-c", probe, "serve", "--config", str(config)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    maps = Path(f"/proc/{server.pid}/maps")
    try:
        with httpx.Client(
            base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=20
        ) as client:
            deadline = time.monotonic() + 60
            while True:
                try:
                    if client.get("/api/health").status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                assert server.poll() is None, "the server stopped"
                assert time.monotonic() < deadline, "the server did not start"
                time.sleep(0.05)
            listed = client.post("/api/tools/list_analyses", json={})
            assert listed.status_code == 200
        if maps.exists():
            assert "duckdb" not in maps.read_text()
    finally:
        server.send_signal(signal.SIGTERM)
        _, logged = server.communicate(timeout=60)
    written = logged.decode("utf-8", "replace")
    # uvicorn shuts down, then raises the signal it caught again, as its default handler would.
    assert server.returncode == -signal.SIGTERM, written
    assert "Finished server process" in written
    assert "aibi-server: pack: library 1.2.0 (results 1)\n" in written
    assert "Traceback" not in written


@pytest.mark.parametrize("module", ["duckdb", "_duckdb"])
def test_duckdb_loaded_before_the_packs_fails_the_start(
    tmp_path: Path, write_config: Write, module: str
) -> None:
    ran = _fresh(tmp_path, write_config, f"import {module}\n" + CHECK, LIBRARY)
    assert ran.stdout.startswith("status 2 "), ran.stderr
    assert f"  {packs.LOADED_DUCKDB}\n" in ran.stderr

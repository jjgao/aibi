"""Names that are not Unicode text: the class tests (SPEC A6, §8.1, §14, D225, D398).

- ``data()`` is total over every string, against an oracle that knows nothing of the code: the
  escape's format and the set of noncharacters are written here, not read from ``output``.
- No module of the core but ``schema/output.py`` reaches the escape: the old ``escaped`` would
  make a name the server's text, and pre-escaped text can be cut inside an escape.
- Every handler on the import path that can catch a ``ValidationError`` (a reader's fault: an
  output built from a name nobody checked) re-raises it first, or is allowed with a reason; and
  each handler that re-raises it is shown to, by injecting a ``ValidationError`` and every class
  the handler is declared to catch.
"""

import ast
import builtins
import io
import random
import sqlite3
import zipfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import cast

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError
from tests.core._ast import defined, scopes, string
from tests.core.importers.builders import Entry, build_xlsx, build_zip

import aibi
from aibi.core.importers import archives, sheets, snapshot, worker
from aibi.core.importers.errors import ImportRefused
from aibi.core.schema.limits import ImportLimits
from aibi.core.schema.output import DataSegment, data, shown
from aibi.core.store import parquet

CORE = Path(aibi.__file__).resolve().parent / "core"
LIMIT = 200
"""A data token's most characters, written here rather than read from ``output``."""


# --- data() is total ---------------------------------------------------------------------------


def _not_text(character: str) -> bool:
    """A lone surrogate or a noncharacter: U+D800-DFFF, U+FDD0-FDEF, and the last two code points
    of every plane."""
    code = ord(character)
    return 0xD800 <= code <= 0xDFFF or 0xFDD0 <= code <= 0xFDEF or code & 0xFFFE == 0xFFFE


def _oracle(value: str) -> tuple[str, bool | None]:
    """What ``data`` holds: each character, or its escape (``\\u`` and four lower-case hex digits,
    ``\\U`` and eight past the BMP), kept while the whole fits in ``LIMIT`` characters; marked
    truncated when something was left out."""
    kept: list[str] = []
    size = 0
    for character in value:
        code = ord(character)
        if not _not_text(character):
            piece = character
        elif code <= 0xFFFF:
            piece = "\\u" + format(code, "04x")
        else:
            piece = "\\U" + format(code, "08x")
        if size + len(piece) > LIMIT:
            return "".join(kept), True
        kept.append(piece)
        size += len(piece)
    return "".join(kept), None


def _a1_data(value: str) -> tuple[str, bool | None]:
    """A1's ``data`` on Unicode text: the first ``LIMIT`` characters, marked when cut."""
    return (value[:LIMIT], True) if len(value) > LIMIT else (value, None)


BAD = [
    "\ud800",
    "\udbff",
    "\udc00",
    "\udc80",
    "\udcff",
    "\udfff",
    "\ufdd0",
    "\ufdef",
    "\ufffe",
    "\uffff",
    "\U0001fffe",
    "\U0001ffff",
    "\U0010fffe",
    "\U0010ffff",
]
GOOD = ["a", "\\", "u", "\u00e9", "\ufdcf", "\ufdf0", "\ufffd", "\U0001f600", "\U0001fffd", "\0"]


def _check(value: str) -> None:
    got = data(value)
    assert (got.data, got.truncated) == _oracle(value), ascii(value)
    assert DataSegment.model_validate(got.model_dump()) == got
    if all(not _not_text(character) for character in value):
        assert (got.data, got.truncated) == _a1_data(value)
    assert shown(value) == got


@given(
    st.text(
        alphabet=st.one_of(
            st.characters(codec=None, categories=None),
            st.sampled_from(BAD + GOOD),
            st.integers(0xD800, 0xDFFF).map(chr),
        ),
        max_size=260,
    )
)
def test_data_is_total_and_the_identity_on_text(value: str) -> None:
    _check(value)


def test_data_cuts_only_between_escapes_at_every_offset_near_the_limit() -> None:
    for prefix in ("a", "\u00e9", "\U0001f600"):
        for bad in BAD:
            for length in range(LIMIT - 12, LIMIT + 3):
                _check(prefix * length + bad + "z")
                _check(prefix * length + bad * 3)


def test_data_is_the_identity_on_random_text() -> None:
    rng = random.Random(398)
    for _ in range(5_000):
        length = rng.choice([0, 1, 20, 199, 200, 201, 400])
        value = "".join(
            chr(rng.choice([rng.randrange(0x20, 0x7F), rng.randrange(0xA0, 0xD800)]))
            for _ in range(length)
        )
        got = data(value)
        assert (got.data, got.truncated) == _a1_data(value)


def test_data_of_a_very_long_name_that_is_not_text_is_cut_early() -> None:
    got = data("\udcff" * 1_000_000)
    assert got.data == "\\udcff" * (LIMIT // 6)
    assert got.truncated is True


@pytest.mark.parametrize("value", [["a"], None, 1, b"a", bytearray(b"a"), ("a",), {"a"}])
def test_data_of_what_is_not_a_str_raises_a_type_error(value: object) -> None:
    """The model would coerce a list of one character, or a bytes, to a token."""
    with pytest.raises(TypeError, match="a data token is made of a str"):
        data(value)  # pyright: ignore[reportArgumentType]


# --- No module but output reaches the escape -----------------------------------------------------


def _private_names(path: Path) -> frozenset[str]:
    """Every ``_``-prefixed name ``path`` defines at its top level: a function, a class, a
    constant, an annotated one."""
    names: set[str] = set()
    for node in ast.parse(path.read_text()).body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return frozenset(name for name in names if name.startswith("_") and not name.startswith("__"))


OUTPUT = CORE / "schema/output.py"
BANNED = frozenset({"escaped"}) | _private_names(OUTPUT)
"""The retired ``escaped``, and every private name of ``schema.output``: ``_escape`` writes an
escape and ``_shown`` makes a token of an escaped name, so either, used elsewhere, makes
pre-escaped text the server's or cuts it again. Taken from the module, so that a name added
there is banned without anyone listing it."""


def test_the_ban_covers_the_names_that_escape() -> None:
    assert {"escaped", "_escape", "_shown", "_END", "_DUMPING"} <= BANNED


def _is_output(node: ast.expr, aliases: set[str]) -> bool:
    """Whether ``node`` is the ``schema.output`` module: a name it was imported as (or
    ``output``), or the attribute ``output`` of a package."""
    return (isinstance(node, ast.Name) and node.id in aliases) or (
        isinstance(node, ast.Attribute) and node.attr == "output"
    )


def banned_references(tree: ast.AST) -> list[int]:
    """The lines of ``tree`` that reach a banned name: an import of it under any alias, an
    attribute of that name on ``output`` however the module was named, an attribute or a string
    of that name on anything else (``getattr``, ``importlib``) or the bare name (after
    ``import *``) where the module defines nothing of that name itself. Other modules do: the
    CLI and the catalogue pages an ``escaped`` that escapes for a terminal or for HTML, a class
    its own ``_END`` or ``_members``. What a module defines is taken over the whole module: a
    ``_shown`` assigned anywhere in it exempts a bare name or an attribute of that name on
    anything but ``output``, so a module that defines a name of its own should not also reach
    ``output``'s by a bare name."""
    own = defined(tree)
    aliases = {"output"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            aliases.update(a.asname for a in node.names if a.asname and a.name.endswith(".output"))
        elif isinstance(node, ast.ImportFrom):
            aliases.update(a.asname or a.name for a in node.names if a.name == "output")
    found: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import | ast.ImportFrom):
            for alias in node.names:
                if alias.name.rsplit(".", 1)[-1] in BANNED:
                    found.append(node.lineno)
        elif isinstance(node, ast.Attribute):
            if node.attr in BANNED - own or (
                node.attr in BANNED and _is_output(node.value, aliases)
            ):
                found.append(node.lineno)
        elif isinstance(node, ast.Call):
            # getattr(output, "_shown"): by a string, so own names count only on ``output``
            if (
                isinstance(node.func, ast.Name)
                and node.func.id == "getattr"
                and len(node.args) >= 2
                and string(node.args[1]) in BANNED
                and _is_output(node.args[0], aliases)
            ):
                found.append(node.lineno)
        elif (isinstance(node, ast.Name) and node.id in BANNED - own) or (
            isinstance(node, ast.Constant) and node.value in BANNED - own
        ):
            found.append(node.lineno)
    return found


def _core_modules() -> Iterator[Path]:
    for path in sorted(CORE.rglob("*.py")):
        if path != OUTPUT:
            yield path


def test_no_module_of_the_core_but_output_reaches_the_escape() -> None:
    found = [
        f"{path.relative_to(CORE)}:{line}"
        for path in _core_modules()
        for line in banned_references(ast.parse(path.read_text()))
    ]
    assert found == []


@pytest.mark.parametrize(
    "source",
    [
        "from aibi.core.schema.output import _escape",
        "from aibi.core.schema.output import _escape as e",
        "from aibi.core.importers.errors import escaped",
        "from aibi.core.schema import output\noutput._escape('x')",
        "import aibi.core.schema.output as o\no._escape",
        "from aibi.core.schema import output\ngetattr(output, '_escape')",
        "import importlib\nimportlib.import_module('aibi.core.schema.output')._escape",
        "import importlib\ngetattr(importlib.import_module('aibi.core.schema.output'), 'escaped')",
        "from aibi.core.schema import output\nf = output.escaped",
        "def g(x):\n    return escaped(x)",
        "def escaped(x):\n    return x\ny = output._escape",
        "from aibi.core.schema.output import _shown",
        "from aibi.core.schema.output import _shown as s\ns('x')",
        "from aibi.core.schema import output\ntext(output._shown(n).data)",
        "import aibi.core.schema.output as o\no._shown",
        "from aibi.core.schema import output\ngetattr(output, '_shown')",
        "import importlib\nimportlib.import_module('aibi.core.schema.output')._shown",
        "import importlib\ngetattr(importlib.import_module('aibi.core.schema.output'), '_shown')",
        "from aibi.core.schema.output import *\n_shown('x')",
        "from aibi.core.schema import output\noutput._check_scalar",
        "def _shown(x):\n    return x\nfrom aibi.core.schema import output\noutput._shown(n)",
        (
            "def _shown(x):\n    return x\nfrom aibi.core.schema import output as o\n"
            "getattr(o, '_shown')"
        ),
        "from aibi.core.schema import output\noutput._DUMPING",
        (
            "def _shown(x):\n    return x\nimport aibi.core.schema.output\n"
            "aibi.core.schema.output._shown(n)"
        ),
        "def _shown(x):\n    return x\nimport aibi.core.schema.output as o\no._shown(n)",
        "def _shown(x):\n    return x\nfrom aibi.core.schema.output import _shown as s",
    ],
)
def test_the_ban_finds_every_way_to_reach_the_escape(source: str) -> None:
    assert banned_references(ast.parse(source))


def test_the_ban_finds_each_private_name_of_output() -> None:
    for name in _private_names(OUTPUT):
        assert banned_references(ast.parse(f"from aibi.core.schema import output\noutput.{name}"))


def test_a_class_s_own_private_name_is_not_banned() -> None:
    source = (
        "class A:\n    _END = 1\n    def _members(self): ...\n"
        "    def f(self):\n        return self._END, self._members()"
    )
    assert banned_references(ast.parse(source)) == []


def test_an_instance_attribute_of_a_banned_name_that_a_class_assigns_is_not_banned() -> None:
    source = "class A:\n    def f(self):\n        self._END = 1\n        return self._END"
    assert banned_references(ast.parse(source)) == []


def test_a_module_s_own_function_of_the_name_is_not_banned() -> None:
    source = "def escaped(x):\n    return x\n__all__ = ['escaped']\nescaped('y')"
    assert banned_references(ast.parse(source)) == []


# --- Every handler on the import path re-raises a ValidationError ------------------------------

IMPORT_PATH = (*sorted((CORE / "importers").glob("*.py")), CORE / "store/parquet.py")
IMPORT_PATH += (CORE / "store/sources.py",)
CATCHES_VALIDATION_ERROR = {kind.__name__ for kind in ValidationError.__mro__[1:-1]}
"""The classes a handler that catches a ``ValidationError`` names: its bases but ``object``."""


@dataclass(frozen=True)
class Handler:
    file: str
    function: str
    index: int
    """Its place among the handlers of its function that catch a ``ValidationError``."""
    classes: tuple[str, ...]
    """What it catches, as written (``<bare>`` for a bare ``except``)."""
    reraised: bool
    """Whether an earlier handler of its ``try`` catches ``ValidationError`` and only re-raises."""
    line: int


def _written(node: ast.expr | None) -> tuple[str, ...]:
    if node is None:
        return ("<bare>",)
    if isinstance(node, ast.Tuple):
        return tuple(name for element in node.elts for name in _written(element))
    return (ast.unparse(node),)


def _re_raises(handler: ast.ExceptHandler) -> bool:
    return (
        "ValidationError" in {name.rsplit(".", 1)[-1] for name in _written(handler.type)}
        and len(handler.body) == 1
        and isinstance(handler.body[0], ast.Raise)
        and handler.body[0].exc is None
    )


def scan(source: str, file: str) -> list[Handler]:
    """The handlers of a module that catch a ``ValidationError``, by function."""
    tree = ast.parse(source)
    where = scopes(tree)
    found: list[Handler] = []
    counts: dict[str, int] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try | ast.TryStar):
            continue
        reraised = False
        for handler in node.handlers:
            classes = _written(handler.type)
            if _re_raises(handler):
                reraised = True
            bare = {name.rsplit(".", 1)[-1] for name in classes}
            if bare & (CATCHES_VALIDATION_ERROR | {"<bare>"}):
                function = where[id(node)]
                index = counts.get(function, 0)
                counts[function] = index + 1
                found.append(Handler(file, function, index, classes, reraised, handler.lineno))
    return found


ALLOWED: dict[tuple[str, str, int], str] = {
    ("importers/confine.py", "Confinement._resolved", 0): (
        "realpath and stat raise OSError, and ValueError only for a path the pre-check let "
        "through; nothing here builds an output"
    ),
    ("importers/databases.py", "_reason", 0): (
        "re-validates the segments a reader child sent: whatever fails is the child's fault, "
        "raised as ReaderError, never masked as a refusal"
    ),
    ("importers/snapshot.py", "_opened", 0): "cleanup that re-raises what it caught",
    ("importers/uploads.py", "UploadArea.put", 0): "cleanup that re-raises what it caught",
    ("importers/urls.py", "_host", 0): "ipaddress parsing a host, which builds no output",
    ("importers/urls.py", "_host", 1): "ipaddress parsing a host, which builds no output",
    ("importers/worker.py", "_answer", 0): (
        "the child's reporting loop: the handler before it sends a ValidationError by its class "
        "name, and this one sends any other exception as a fault (ReaderError)"
    ),
    ("importers/worker.py", "_answer", 1): "the child's reporting loop: a panic, by its class",
    ("importers/worker.py", "serve", 0): (
        "the child reading a request it cannot unpickle (it holds server text, which no child "
        "unpickles): sent as a fault (ReaderError), never as the file's"
    ),
    ("importers/worker.py", "_peak", 0): "reading /proc, which builds no output",
    ("importers/worker.py", "_launch", 0): "cleanup that re-raises what it caught",
    ("importers/worker.py", "Reader._start", 0): "cleanup that re-raises what it caught",
    ("importers/worker.py", "Reader.run", 0): (
        "the parent reading an answer: anything it raises is ReaderError, a fault, never a refusal"
    ),
    ("store/sources.py", "decode", 0): (
        "a typed snapshot the server wrote, damaged: nothing here builds an output from a name"
    ),
}
"""The handlers on the import path that catch a ``ValidationError`` without re-raising it first,
each with why that is right."""


def _handlers() -> list[Handler]:
    return [
        handler
        for path in IMPORT_PATH
        for handler in scan(path.read_text(), path.relative_to(CORE).as_posix())
    ]


def test_every_handler_that_catches_a_validation_error_re_raises_it_or_is_allowed() -> None:
    handlers = _handlers()
    keys = {(h.file, h.function, h.index) for h in handlers}
    unexplained = [
        f"{h.file}:{h.line} {h.function}"
        for h in handlers
        if not h.reraised and (h.file, h.function, h.index) not in ALLOWED
    ]
    assert unexplained == []
    assert sorted(set(ALLOWED) - keys) == []
    assert all(not h.reraised for h in handlers if (h.file, h.function, h.index) in ALLOWED)


def test_the_scan_sees_a_bare_except_and_a_single_value_error() -> None:
    source = (
        "def f():\n    try:\n        pass\n    except ValueError:\n        pass\n"
        "def g():\n    try:\n        pass\n    except ValidationError:\n        raise\n"
        "    except:\n        pass\n"
    )
    found = scan(source, "probe.py")
    assert [(h.function, h.classes, h.reraised) for h in found] == [
        ("f", ("ValueError",), False),
        ("g", ("<bare>",), True),
    ]


# --- Injection into each handler that re-raises ---------------------------------------------------


def _invalid() -> ValidationError:
    try:
        DataSegment(data="\uffffSENTINEL-NAME")
    except ValidationError as error:
        return error
    raise AssertionError("a noncharacter is valid")


def _zip() -> bytes:
    return build_zip([Entry("a.csv", b"a\n1\n")])


def _sqlite(tmp_path: Path) -> snapshot.Target:
    path = tmp_path / "s.sqlite"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE t (a INT)")
    connection.close()
    return snapshot.Target("sqlite", path=str(path), location="s.sqlite")


def _parquet() -> bytes:
    buffer = io.BytesIO()
    pq.write_table(pa.table({"a": [1]}), buffer)
    return buffer.getvalue()


ZIP = _zip()
XLSX = build_xlsx([("s", [["h"], ["v"]])])
PARQUET = _parquet()
TABLE = pa.table({"a": [1]})
"""Built before anything is patched: building them uses what a test patches."""

Patch = Callable[[pytest.MonkeyPatch, Callable[..., object]], None]


@dataclass(frozen=True)
class Injection:
    classes: tuple[str, ...]
    """What the handler must catch, as written: the scan must find exactly these."""
    module: ModuleType
    patch: Patch
    call: Callable[[Path], object]
    refusal: type[Exception]
    """What a caught library exception becomes."""


def _raising(error: BaseException) -> Callable[..., object]:
    def raise_it(*_args: object, **_kwargs: object) -> object:
        raise error

    return raise_it


INJECTIONS: dict[tuple[str, str, int], Injection] = {
    ("importers/archives.py", "_walk", 0): Injection(
        ("Exception",),
        archives,
        lambda m, f: m.setattr(archives.zipfile, "ZipFile", f),
        lambda _: archives.members(ZIP, ImportLimits()),
        ImportRefused,
    ),
    ("importers/archives.py", "_walk", 1): Injection(
        ("Exception",),
        archives,
        lambda m, f: m.setattr(archives, "_limit", f),
        lambda _: archives.members(ZIP, ImportLimits()),
        ImportRefused,
    ),
    ("importers/sheets.py", "read_workbook", 0): Injection(
        ("CalamineError", "OSError", "ValueError", "OverflowError"),
        sheets,
        lambda m, f: m.setattr(sheets.CalamineWorkbook, "from_filelike", staticmethod(f)),
        lambda _: sheets.read_workbook(XLSX, ImportLimits(), 100),
        ImportRefused,
    ),
    ("importers/snapshot.py", "read_snapshot", 0): Injection(
        ("Exception",),
        snapshot,
        lambda m, f: m.setattr(snapshot, "_sqlite", f),
        lambda tmp: snapshot.read_snapshot(_sqlite(tmp), ImportLimits()),
        ImportRefused,
    ),
    ("store/parquet.py", "read_source", 0): Injection(
        ("pa.ArrowException", "OSError", "ValueError", "OverflowError", "ZoneInfoNotFoundError"),
        parquet,
        lambda m, f: m.setattr(parquet, "_read_source", f),
        lambda _: parquet.read_source(PARQUET, 100),
        parquet.UnreadableParquetError,
    ),
    ("store/parquet.py", "from_arrow", 0): Injection(
        ("pa.ArrowException", "OSError", "ValueError", "OverflowError", "ZoneInfoNotFoundError"),
        parquet,
        lambda m, f: m.setattr(parquet, "_schema", f),
        lambda _: parquet.from_arrow(TABLE),
        parquet.UnreadableParquetError,
    ),
    ("store/parquet.py", "_values", 0): Injection(
        ("pa.ArrowException", "OSError", "ValueError", "OverflowError", "ZoneInfoNotFoundError"),
        parquet,
        lambda m, f: m.setattr(parquet, "_value", f),
        lambda _: parquet.read_source(PARQUET, 100),
        parquet.UnreadableParquetError,
    ),
}
"""How to reach each handler that re-raises a ``ValidationError``, and what it must catch."""


def _resolve(module: ModuleType, written: str) -> type[BaseException]:
    head, *rest = written.split(".")
    found: object = vars(module)[head] if head in vars(module) else getattr(builtins, head)
    for name in rest:
        found = getattr(found, name)
    return cast(type[BaseException], found)


def _library(kind: type[BaseException]) -> BaseException:
    if kind is Exception:
        return KeyError("lib")  # a library's own exception, of no class the handler names
    return kind("lib")


def _cases() -> list[tuple[tuple[str, str, int], str]]:
    return [(key, name) for key, found in INJECTIONS.items() for name in found.classes]


def test_every_handler_that_re_raises_has_an_injection_and_catches_what_it_declares() -> None:
    reraising = {(h.file, h.function, h.index): h.classes for h in _handlers() if h.reraised}
    assert set(reraising) == set(INJECTIONS)
    for key, classes in reraising.items():
        assert tuple(name for name in classes if name != "ValidationError") == (
            INJECTIONS[key].classes
        ), key


@pytest.mark.parametrize(("key", "name"), _cases(), ids=[f"{k[0]}:{k[1]}-{n}" for k, n in _cases()])
def test_a_library_exception_is_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, key: tuple[str, str, int], name: str
) -> None:
    injection = INJECTIONS[key]
    injection.patch(monkeypatch, _raising(_library(_resolve(injection.module, name))))
    with pytest.raises(injection.refusal) as raised:
        injection.call(tmp_path)
    if isinstance(raised.value, parquet.UnreadableParquetError) and key[1] == "_values":
        assert raised.value.column == 0


@pytest.mark.parametrize("key", list(INJECTIONS), ids=[f"{k[0]}:{k[1]}" for k in INJECTIONS])
def test_a_validation_error_is_never_masked(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, key: tuple[str, str, int]
) -> None:
    error = _invalid()
    INJECTIONS[key].patch(monkeypatch, _raising(error))
    with pytest.raises(ValidationError) as raised:
        INJECTIONS[key].call(tmp_path)
    assert raised.value is error


def test_zipfile_is_patched_only_in_the_test() -> None:
    assert archives.zipfile.ZipFile is zipfile.ZipFile


@pytest.mark.parametrize(
    "given",
    [{"data": "\uffffSENTINEL-NAME"}, {"data": "x", "SENTINEL-NAME": 1}],
    ids=["a name in the input", "a name in the location"],
)
def test_the_child_reports_a_validation_error_by_its_class_alone(given: dict[str, object]) -> None:
    with worker.Reader(ImportLimits()) as reader, pytest.raises(worker.ReaderError) as raised:
        reader.run(DataSegment.model_validate, given)
    assert str(raised.value) == "ValidationError"

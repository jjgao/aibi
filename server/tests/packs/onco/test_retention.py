"""The pack keeps nothing past a call, by the routes these guards name (M4.2a-1b, D438): a
process-level cache or list would hold the cells and ids of an erased dataset for the server's life.

The guards are for trusted pack code that keeps a copy of a call's values by accident or
carelessness; a pack set on evading them is not their concern (D438). Three guards, none of which
names a module, a function or a column, and none of which holds the class of retention (D438 says
what none holds, and which mutants of its table, ``retention_mutants/table.py``, live):

- ``banned``, over **every** module under ``packs/onco`` (the whole tree, whatever is added to
  it): a **deny-list**. The AST holds no ``functools`` (every cache the standard library has), no
  ``global``, no ``globals()`` or ``__import__``, binds no mutable container made by a literal or
  by a named maker (``list()``, ``dict()``, ``set()``, ``deque()``...) at module level or in a class
  body, and gives no ``def`` or ``lambda`` a default argument of one. A frozen constant is
  allowed (a tuple, a ``frozenset``, a compiled regex, a ``MappingProxyType``, a number or a string,
  and ``__all__``), but so is every value the lists above do not name: a ``SimpleNamespace``, an
  ``io.StringIO``, a ``queue.Queue``, an instance of a class of the pack, a ``nonlocal`` binding
  and an underscored class attribute made by a call pass the AST and the run-time check alike. At
  run time (``module_state``, also a deny-list), no module of the pack binds a value that is one
  of the mutable container types or of a type of ``functools``, nor does any function or method of
  the pack hold one in its ``__defaults__``, its ``__kwdefaults__`` or a closure cell;
- the sentinel retention test: each kind of column given many short distinct values, through
  ``clinical.read`` and through ``PACK.importer``, at two sizes; once the call has returned and
  its result is dropped, what ``tracemalloc`` still holds must not grow with the size. It sees
  what grows with the file. A retention that does not grow with it (a memo of the last 128
  values, the first file's reader, a dataset's first ids, a study's meta bytes) it does not see;
- the marker scan (``retention_scan.py``, run by ``test_nothing_of_a_file_is_reachable...``): in
  a fresh interpreter, nine calls (two imports, two reads, an import and a read that are refused,
  the validator, which accepts a release and refuses it with its ``type_of_cancer`` edited, the
  facet and the proposer), each with a marker of its own in every value (``FIELDS``, which
  ``test_every_value_of_a_call_carries_its_marker`` holds); **after each call** the heap is
  walked from ``gc.get_objects()`` and ``sys.modules`` and no holder it reads may hold a marker of
  a call made so far: a form's code points, contiguous and in order, in any case, reversed, in
  UTF-8, UTF-16 or UTF-32, as hex or base64 of UTF-8, in a ``str``, the bytes of a buffer or an
  ``int``, an ``array`` of any type code, an ``io.StringIO`` or ``io.BytesIO``, and the elements
  of a ``list``, a ``tuple``, a ``deque`` or a dict's keys or values. Its own reads are pinned in
  a child of their own (``retention_pins.py``: every holder in every encoding, each route, a
  value kept by one call and dropped by the next, and objects that raise on any read but the base
  type's own, which the walk must pass without running their code).
"""

import ast
import gc
import importlib
import importlib.util
import inspect
import json
import os
import pkgutil
import subprocess
import sys
import tracemalloc
import types
from collections import deque
from collections.abc import Callable, Iterator, Mapping, MutableMapping, MutableSequence, MutableSet
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

import aibi
from aibi.core.importers.confine import Confinement
from aibi.core.schema.limits import ImportLimits
from aibi.core.schema.pack_api import ImportOptions, Refused

MakeStudy = Callable[..., Path]
PACK = Path(__file__).resolve().parents[3] / "src" / "aibi" / "packs" / "onco"
MODULES = sorted(PACK.glob("**/*.py"))

FROZEN_MAKERS = frozenset({"tuple", "frozenset", "compile", "MappingProxyType"})
MUTABLE_MAKERS = frozenset(
    {"list", "dict", "set", "bytearray", "deque", "defaultdict", "OrderedDict", "Counter", "array"}
)
"""What a call that makes a mutable container is named: by the callee's own name, whatever it is
imported as (``collections.deque``, ``deque``)."""
BANNED_MODULES = frozenset({"functools", "weakref", "importlib"})
"""``functools`` holds every cache (``lru_cache``, ``cache``, ``cached_property``), and the other
two reach it, or hold a cell, without naming it."""
BANNED_NAMES = frozenset({"globals", "__import__", "eval", "exec", "lru_cache", "cache"})
"""``globals()`` reaches a module's state without a name, and the others reach a cache."""
LANGUAGE = frozenset({"__all__"})


def _callee(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _mutable(value: ast.expr) -> bool:
    """Whether an expression bound at module level makes a mutable container."""
    if isinstance(value, ast.List | ast.Dict | ast.Set | ast.ListComp | ast.DictComp | ast.SetComp):
        return True
    if isinstance(value, ast.Call):
        return _callee(value.func) in MUTABLE_MAKERS
    if isinstance(value, ast.Tuple):  # a tuple holding one is as mutable as the one it holds
        return any(_mutable(element) for element in value.elts)
    return False


def _bound(statements: list[ast.stmt]) -> Iterator[tuple[str, ast.expr]]:
    """The names bound, with their values, by the statements run at import: module level, the
    branches of an ``if`` or ``try`` there, and a class body (which is not a function's)."""
    for node in statements:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    yield target.id, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            if isinstance(node.target, ast.Name):
                yield node.target.id, node.value
        elif isinstance(node, ast.ClassDef):
            yield from _bound(node.body)
        elif isinstance(node, ast.If | ast.For | ast.While):
            yield from _bound(node.body + node.orelse)
        elif isinstance(node, ast.With):
            yield from _bound(node.body)
        elif isinstance(node, ast.Try):
            yield from _bound(
                node.body
                + node.orelse
                + node.finalbody
                + [s for h in node.handlers for s in h.body]
            )


def banned(source: str) -> list[str]:
    """What in ``source`` keeps state past a call: each item says what and where."""
    tree = ast.parse(source)
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found += [
                f"line {node.lineno}: imports {alias.name}"
                for alias in node.names
                if alias.name.split(".")[0] in BANNED_MODULES
            ]
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[0] in BANNED_MODULES:
                found.append(f"line {node.lineno}: imports from {node.module}")
        elif isinstance(node, ast.Global):
            found.append(f"line {node.lineno}: global {', '.join(node.names)}")
        elif isinstance(node, ast.Name) and node.id in BANNED_NAMES:
            found.append(f"line {node.lineno}: uses {node.id}")
        elif isinstance(node, ast.Attribute) and node.attr in BANNED_NAMES:
            found.append(f"line {node.lineno}: uses {node.attr}")
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda):
            defaults = [*node.args.defaults, *node.args.kw_defaults]
            found += [
                f"line {default.lineno}: a mutable default argument"
                for default in defaults
                if default is not None and _mutable(default)
            ]
    for name, value in _bound(tree.body):
        if name not in LANGUAGE and _mutable(value):
            found.append(f"line {value.lineno}: {name} is a mutable container")
    return found


@pytest.mark.parametrize("module", MODULES, ids=lambda path: str(path.relative_to(PACK)))
def test_no_module_of_the_pack_keeps_state_past_a_call(module: Path) -> None:
    assert banned(module.read_text(encoding="utf-8")) == []


def test_the_pack_s_tree_is_what_the_guard_reads() -> None:
    """The guard reads every file under the pack, the subpackages' too, and the pack has the
    modules the suite knows (a new file is read without a change here)."""
    assert len(MODULES) >= 16
    assert PACK / "clinical.py" in MODULES
    assert PACK / "attributes.py" in MODULES


CACHES = {
    "functools import": "import functools\n",
    "functools from-import": "from functools import lru_cache as memo\n",
    "functools aliased": "import functools as f\n",
    "a cache called through a module name": "import functools\n@functools.cache\ndef f(): ...\n",
    "cached_property": "from functools import cached_property\n",
    "weakref": "import weakref\n",
    "importlib reaching functools": "import importlib\nm = importlib.import_module('functools')\n",
    "dunder import": "m = __import__('functools')\n",
    "global": "X = 0\ndef f():\n    global X\n    X += 1\n",
    "globals()": "def f():\n    globals()['X'] = 1\n",
    "a list": "SEEN = []\n",
    "a dict": "SEEN = {}\n",
    "a set": "SEEN = {1}\n",
    "a dict comprehension": "SEEN = {i: i for i in range(3)}\n",
    "list()": "SEEN = list()\n",
    "dict()": "SEEN = dict(a=1)\n",
    "set()": "SEEN = set()\n",
    "a deque": "import collections\nSEEN = collections.deque()\n",
    "a defaultdict": "from collections import defaultdict\nSEEN = defaultdict(list)\n",
    "an annotated list": "SEEN: list[str] = []\n",
    "a list in a tuple": "SEEN = (1, [])\n",
    "a list in a class body": "class C:\n    seen = []\n",
    "a list in a branch": "import sys\nif sys:\n    SEEN = []\n",
    "a list in a try": "try:\n    import x\nexcept ImportError:\n    SEEN = []\n",
    "a list default": "def f(x, seen=[]): ...\n",
    "a dict default": "def f(x, seen={}): ...\n",
    "a set default": "def f(x, seen={1}): ...\n",
    "a keyword-only default": "def f(x, *, seen={}): ...\n",
    "a default made by dict()": "def f(x, seen=dict()): ...\n",
    "a deque default": "import collections\ndef f(x, seen=collections.deque()): ...\n",
    "a defaultdict default": (
        "from collections import defaultdict\ndef f(x, seen=defaultdict(list)): ...\n"
    ),
    "a bytearray default": "def f(x, seen=bytearray()): ...\n",
    "a list comprehension default": "def f(x, seen=[i for i in range(3)]): ...\n",
    "a list in a tuple default": "def f(x, seen=(1, [])): ...\n",
    "a lambda default": "f = lambda x, seen=[]: seen\n",
    "a method default": "class C:\n    def f(self, seen=[]): ...\n",
    "an async default": "async def f(x, seen={}): ...\n",
    "a nested default": "def f():\n    def g(seen=[]): ...\n    return g\n",
}
FROZEN = (
    "import re\nfrom types import MappingProxyType\n"
    "A = (1, 2)\nB = frozenset({1})\nC = re.compile('x')\nD = MappingProxyType({'a': 1})\n"
    "E = 'x'\nF = 1\nG = frozenset(['a'])\nH = tuple([1])\n"
    "class K:\n    names = ('a',)\n"
    "__all__ = ['A', 'B']\n"
    "def f():\n    seen = []\n    return seen\n"
    "def g(a=(), b=None, c=1, d='x', e=frozenset(), f=(1, 2), *, h=0.5, i=b''): ...\n"
    "k = lambda x, y=(): x\n"
)


@pytest.mark.parametrize("source", CACHES.values(), ids=CACHES.keys())
def test_the_guard_refuses_each_way_to_keep_state(source: str) -> None:
    assert banned(source) != []


def test_the_guard_allows_a_frozen_constant_and_a_local_list() -> None:
    assert banned(FROZEN) == []


def _loaded() -> list[ModuleType]:
    package = importlib.import_module("aibi.packs.onco")
    return [package] + [
        importlib.import_module(f"aibi.packs.onco.{info.name}")
        for info in pkgutil.walk_packages(package.__path__)
    ]


def _state(owner: str, name: str, value: object) -> str | None:
    if isinstance(value, MutableSequence | MutableMapping | MutableSet | bytearray | deque):
        return f"{owner}.{name} is a mutable container"
    if type(value).__module__ == "functools":
        return f"{owner}.{name} is of a type of functools ({type(value).__name__})"
    return None


def _functions(value: object) -> Iterator[types.FunctionType]:
    """The functions a class or module attribute is or wraps: a function, a static or class
    method's, a property's accessors."""
    if isinstance(value, types.FunctionType):
        yield value
    elif isinstance(value, staticmethod | classmethod):
        yield from _functions(value.__func__)
    elif isinstance(value, property):
        for accessor in (value.fget, value.fset, value.fdel):
            yield from _functions(accessor)


def _held(
    owner: str, function: types.FunctionType, seen: set[int], source: str | None
) -> Iterator[str]:
    """What a function holds past a call: a mutable container or a ``functools`` object in a
    default, a keyword default or a closure cell, and the same of each function a cell or a
    default holds that is written in ``source``, the module's file (the functions that
    ``dataclasses`` and ``NamedTuple`` generate, and the wrappers of the standard library, are
    written elsewhere: their state is the library's, which the source guard cannot read)."""
    if id(function) in seen or function.__code__.co_filename != source:
        return
    seen.add(id(function))
    where = f"{owner}.{function.__qualname__}"
    held: list[tuple[str, object]] = [
        (f"__defaults__[{index}]", value) for index, value in enumerate(function.__defaults__ or ())
    ]
    held += [(f"__kwdefaults__[{key!r}]", v) for key, v in (function.__kwdefaults__ or {}).items()]
    for name, cell in zip(function.__code__.co_freevars, function.__closure__ or (), strict=True):
        try:
            held.append((f"closure cell {name}", cell.cell_contents))
        except ValueError:  # a cell not yet filled
            continue
    for label, value in held:
        if message := _state(where, label, value):
            yield message
        for inner in _functions(value):
            yield from _held(owner, inner, seen, source)


def module_state(module: ModuleType) -> list[str]:
    """What ``module`` holds past a call, as the run-time guard reads it: a value it binds that is
    a mutable container or of a type of ``functools`` (at module level, and in a class it defines
    for each name that is not underscored: the others are the machinery of ``NamedTuple`` and
    dataclasses, and the source guard reads them), and, for every function and method it defines
    (underscored ones too), a mutable container or ``functools`` object in its ``__defaults__``,
    its ``__kwdefaults__`` or a closure cell."""
    found: list[str] = []
    seen: set[int] = set()
    owned = module.__name__
    source = getattr(module, "__file__", None)
    for name, value in vars(module).items():
        if name.startswith("__"):
            if name == "__all__":
                assert all(isinstance(item, str) for item in value)
            continue
        if message := _state(owned, name, value):
            found.append(message)
        for function in _functions(value):
            found += _held(owned, function, seen, source)
        if inspect.isclass(value) and value.__module__ == owned:
            for attribute, held in vars(value).items():
                if not attribute.startswith("_"):
                    message = _state(f"{owned}.{name}", attribute, held)
                    if message:
                        found.append(message)
                for function in _functions(held):
                    found += _held(f"{owned}.{name}", function, seen, source)
    return found


def test_a_loaded_module_binds_no_mutable_container_and_no_cache() -> None:
    """The values themselves, whatever made them (a call of a function that returns a dict, a
    decorator from anywhere), and what each function holds in a default or a closure
    (``module_state``)."""
    modules = _loaded()
    assert len(modules) >= 16
    assert [message for module in modules for message in module_state(module)] == []


def _module(source: str) -> ModuleType:
    module = types.ModuleType("scratch_pack")
    module.__file__ = "scratch_pack.py"
    exec(compile(source, "scratch_pack.py", "exec"), module.__dict__)
    return module


STATES = {
    "a module-level list": "SEEN = []\n",
    "a module-level lru_cache": ("import functools\n@functools.lru_cache\ndef f(x): return x\n"),
    "a list default": "def f(x, seen=[]): ...\n",
    "a dict default": "def f(x, seen={}): ...\n",
    "a keyword-only default": "def f(x, *, seen={}): ...\n",
    "a default made by a call": ("def make(): return {}\ndef f(x, seen=make()): ...\n"),
    "a deque default": "import collections\ndef f(x, seen=collections.deque()): ...\n",
    "a bytearray default": "def f(x, seen=bytearray()): ...\n",
    "a lambda default": "f = lambda x, seen=[]: seen\n",
    "a method default": "class C:\n    def f(self, seen={}): ...\n",
    "an underscored method default": "class C:\n    def _f(self, seen=[]): ...\n",
    "a static method default": "class C:\n    @staticmethod\n    def f(seen=[]): ...\n",
    "a class method default": "class C:\n    @classmethod\n    def f(cls, seen=[]): ...\n",
    "a property default": ("class C:\n    @property\n    def p(self, seen=[]): ...\n"),
    "a closure over a list": (
        "def make():\n    seen = []\n    def f(x):\n        seen.append(x)\n    return f\n"
        "F = make()\n"
    ),
    "a closure over a dict, in a method": (
        "def make():\n    seen = {}\n    def f(self):\n        return seen\n    return f\n"
        "class C:\n    f = make()\n"
    ),
    "a closure over a deque": (
        "import collections\ndef make():\n    q = collections.deque()\n    return lambda: q\n"
        "F = make()\n"
    ),
    "a closure over an lru_cache wrapper": (
        "import functools\ndef make():\n    @functools.lru_cache\n    def g(x): return x\n"
        "    return lambda x: g(x)\nF = make()\n"
    ),
    "a closure over a function with a list default": (
        "def make():\n    def g(seen=[]): ...\n    return lambda: g\nF = make()\n"
    ),
}
FROZEN_STATE = (
    "import re\nfrom types import MappingProxyType\n"
    "A = (1, 2)\nB = frozenset({1})\nC = re.compile('x')\nD = MappingProxyType({'a': 1})\n"
    "def f(a=(), b=None, c=1, d='x', e=frozenset(), g=(1, 2), *, h=0.5, i=b''): ...\n"
    "def make():\n    seen = []\n    k = 3\n    def g():\n        return k\n    return g\n"
    "G = make()\n"
    "class K:\n    names = ('a',)\n    def m(self, x=(), y=None): ...\n"
    "    @staticmethod\n    def s(z=1): ...\n    @property\n    def p(self): return 1\n"
)


@pytest.mark.parametrize("source", STATES.values(), ids=STATES.keys())
def test_the_run_time_guard_refuses_each_way_a_module_holds_state(source: str) -> None:
    assert module_state(_module(source)) != []


def test_the_run_time_guard_allows_frozen_defaults_and_closures() -> None:
    assert module_state(_module(FROZEN_STATE)) == []


# --- What a call leaves behind, by the size of the file ---------------------------------------

SMALL, LARGE = 10_000, 100_000
GROWTH = {"blocks": 100, "bytes": 16 * 1024}
"""The most what a call retains may grow between the two sizes, by each measure: a few blocks, or
a few kilobytes, of the interpreter's and the test's own. A cache of a value of the file, a list of
its ids, holds a block (50 bytes or more) for each row or more: 90,000 blocks more at 10,000 and
100,000 rows, 225 kilobytes at 500 and 5,000."""


def blocks_retained(call: Callable[[], object]) -> int:
    """The memory blocks ``call`` still holds once it has returned, and its result, or the refusal
    it raised, is dropped (``sys.getallocatedblocks``, after ``gc.collect``): the short values of
    this test are blocks of their own, which nothing frees while something holds them. It costs
    nothing to count, so the sizes are large; it does not see an object of more than 512 bytes (a
    file's bytes, a long cell), which ``bytes_retained`` does."""
    gc.collect()
    before = sys.getallocatedblocks()
    try:
        call()
    except Refused as error:
        error.with_traceback(None)
    gc.collect()
    return sys.getallocatedblocks() - before


def bytes_retained(call: Callable[[], object]) -> int:
    """The bytes ``call`` still holds once it has returned and its result, or the refusal it
    raised, is dropped (``tracemalloc``, which sees every allocation, a file's bytes among them,
    and slows the call six times over, so the sizes are small)."""
    gc.collect()
    tracemalloc.start()
    try:
        before = tracemalloc.get_traced_memory()[0]
        try:
            call()
        except Refused as error:
            error.with_traceback(None)
        gc.collect()
        return tracemalloc.get_traced_memory()[0] - before
    finally:
        tracemalloc.stop()


MEASURES = {"blocks": blocks_retained, "bytes": bytes_retained}


def header(names: list[str], types: list[str]) -> str:
    """The four header rows and the names: every column's label and definition its own."""
    labels = "\t".join(f"Label {n}" for n in names)
    definitions = "\t".join(f"Definition of {n}" for n in names)
    rows = [labels, definitions, "\t".join(types), "\t".join("1" for _ in names)]
    return "".join(f"#{row}\n" for row in rows) + "\t".join(names) + "\n"


def base3(number: int, digits: int) -> str:
    """``number`` as ``digits`` characters of ``[``, ``]`` and ``|``: 3^``digits`` distinct."""
    marks = "[]|"
    out = []
    for _ in range(digits):
        number, rest = divmod(number, 3)
        out.append(marks[rest])
    return "".join(out)


def patients(rows: int, extra: int = 0, *, spellings: bool = False) -> bytes:
    """A patient file of ``rows`` rows, a short distinct value in every kind of column: the
    key (``P<i>``), a STRING (``n<i>``), a NUMBER, the OS and DFS status columns, an
    ``X_STATUS`` (its label ``<0 or 1>:L<i>``) beside its months, a BOOLEAN with missing
    spellings, and a STRING of missing spellings (40 distinct, or with ``spellings`` one for each
    row: more than a column may have, so the file is refused at its end), and ``extra`` more
    columns of their own names, labels and definitions."""
    names = [
        "PATIENT_ID", "NOTE", "AGE", "OS_STATUS", "DFS_STATUS", "PFS_STATUS", "PFS_MONTHS",
        "SMOKER", "MISS", *(f"W{k}" for k in range(extra)),
    ]  # fmt: skip
    types = ["STRING", "STRING", "NUMBER", "STRING", "STRING", "STRING", "NUMBER", "BOOLEAN",
             "STRING", *("STRING" for _ in range(extra))]  # fmt: skip
    out = [header(names, types)]
    for i in range(rows):
        missing = "N" + base3(i, 11) + "A" if spellings else "N" + "|" * (i % 40) + "A"
        smoker = ("TRUE", "FALSE", "[Not Available]", "NA")[i % 4]
        row = [
            f"P{i}", f"n{i}", f"{i}.{i % 7}", ("0:LIVING", "1:DECEASED")[i % 2],
            ("0:DiseaseFree", "1:Recurred")[i % 2], f"{i % 2}:L{i}", f"{i}.75", smoker, missing,
            *(f"w{i}" for _ in range(extra)),
        ]  # fmt: skip
        out.append("\t".join(row) + "\n")
    return "".join(out).encode()


def samples(rows: int) -> bytes:
    names = ["PATIENT_ID", "SAMPLE_ID", "TYPE", "TMB"]
    out = [header(names, ["STRING", "STRING", "STRING", "NUMBER"])]
    out += (f"P{i}\tS{i}\t{('a', 'b')[i % 2]}\t{i}.5\n" for i in range(rows))
    return "".join(out).encode()


META = {
    "meta_study.txt": (
        b"type_of_cancer: mixed\ncancer_study_identifier: m\nname: m\ndescription: m\n"
    ),
    "meta_clinical_patient.txt": (
        b"cancer_study_identifier: m\ngenetic_alteration_type: CLINICAL\n"
        b"datatype: PATIENT_ATTRIBUTES\ndata_filename: data_clinical_patient.txt\n"
    ),
    "meta_clinical_sample.txt": (
        b"cancer_study_identifier: m\ngenetic_alteration_type: CLINICAL\n"
        b"datatype: SAMPLE_ATTRIBUTES\ndata_filename: data_clinical_sample.txt\n"
    ),
}

Shape = tuple[int, int, bool]
"""A file's rows, its extra columns and whether its missing spellings overflow."""
WIDE: list[Shape] = [(20, 100, False), (20, 1_000, False)]
SIZES: dict[tuple[str, str], list[Shape]] = {
    ("blocks", "tall"): [(SMALL, 0, False), (LARGE, 0, False)],
    ("blocks", "wide"): WIDE,
    ("blocks", "spellings"): [(SMALL // 2, 0, True), (SMALL * 2, 0, True)],
}
IMPORTED: dict[tuple[str, str], list[Shape]] = {
    **SIZES,
    ("bytes", "tall"): [(SMALL // 20, 0, False), (SMALL // 2, 0, False)],
}
"""The shapes of the reader's test and the importer's: the importer's has the bytes ``tracemalloc``
sees too (the file's bytes, which no block counts, are read inside the call: a read is given
its bytes by its caller, so nothing it holds of them is new)."""
WARM: Shape = (200, 2, False)
HEAVY: dict[tuple[str, str], list[Shape]] = {
    ("blocks", "tall"): [(LARGE, 0, False), (1_000_000, 0, False)]
}


def _grown(held: Callable[[Shape], int], measure: str, shapes: list[Shape]) -> None:
    held((*WARM[:2], shapes[0][2]))  # a first call makes what the interpreter caches
    small, large = held(shapes[0]), held(shapes[1])
    assert large - small < GROWTH[measure], (measure, small, large)


def _read(measure: str) -> Callable[[Shape], int]:
    def held(shape: Shape) -> int:
        from aibi.packs.onco import clinical

        rows, extra, spellings = shape
        patient = patients(rows, extra, spellings=spellings)
        sample = samples(rows)
        limits = ImportLimits()

        def call() -> None:
            found = clinical.read(patient, "p.txt", "patient", limits)
            clinical.read(
                sample, "s.txt", "sample", limits, cells_before=found.cells, patient=found
            )

        return MEASURES[measure](call)

    return held


def _imported(make_study: MakeStudy, measure: str) -> Callable[[Shape], int]:
    def held(shape: Shape) -> int:
        rows, extra, spellings = shape
        files: Mapping[str | bytes, object] = {
            **META,
            "data_clinical_patient.txt": patients(rows, extra, spellings=spellings),
            "data_clinical_sample.txt": samples(rows),
        }
        study = make_study(files)
        reader = Confinement.of(study.parent)
        options = ImportOptions(
            dataset="m", reader=reader, limits=ImportLimits(), at="2026-01-01T00:00:00Z"
        )
        source = reader.confine(study)
        importer: Any = importlib.import_module("aibi.packs.onco").PACK.importer
        return MEASURES[measure](lambda: importer.import_source(source, options))

    return held


@pytest.mark.parametrize(("measure", "shape"), SIZES, ids=lambda key: key)
def test_a_read_retains_nothing_that_grows_with_the_file(measure: str, shape: str) -> None:
    _grown(_read(measure), measure, SIZES[(measure, shape)])


@pytest.mark.measurement
@pytest.mark.parametrize(("measure", "shape"), HEAVY, ids=lambda key: key)
def test_a_read_retains_nothing_that_grows_with_a_large_file(measure: str, shape: str) -> None:
    _grown(_read(measure), measure, HEAVY[(measure, shape)])


@pytest.mark.parametrize(("measure", "shape"), IMPORTED, ids=lambda key: key)
def test_an_import_retains_nothing_that_grows_with_the_study(
    make_study: MakeStudy, measure: str, shape: str
) -> None:
    _grown(_imported(make_study, measure), measure, IMPORTED[(measure, shape)])


@pytest.mark.measurement
@pytest.mark.parametrize(("measure", "shape"), HEAVY, ids=lambda key: key)
def test_an_import_retains_nothing_that_grows_with_a_large_study(
    make_study: MakeStudy, measure: str, shape: str
) -> None:
    _grown(_imported(make_study, measure), measure, HEAVY[(measure, shape)])


# --- The marker scan: what of a file is reachable after each call -----------------------------

SCAN = Path(__file__).resolve().with_name("retention_scan.py")
SOURCE = Path(aibi.__file__).resolve().parents[1]
"""The ``src`` of the ``aibi`` this process imported: the scan's child imports the same tree."""
RUN = (
    "import runpy, sys; sys.path.insert(0, sys.argv[1]); sys.argv = sys.argv[2:]; "
    "runpy.run_path(sys.argv[0], run_name='__main__')"
)
"""A child's code that runs a script (``argv[2]``) with a tree's ``src`` (``argv[1]``) first on
its path: under ``-I`` the editable install would otherwise give the child the installed tree,
which a replay of D438's table, run against a copy, is not."""
PINS = Path(__file__).resolve().with_name("retention_pins.py")
TABLE = Path(__file__).resolve().parent / "retention_mutants" / "table.py"
OUTCOMES = [
    "ok", "ok", "ok", "ok", "EXCEL_DATE", "MISSING_FORM", "TYPE_OF_CANCER", "ok", "ok",
]  # fmt: skip
"""The scan's nine calls: two imports, two reads, an import refused at its last patient row, a
read refused at its end, the validator (which refuses the dataset's edited ``type_of_cancer``),
the facet and the proposer."""


def test_nothing_of_a_file_is_reachable_after_any_call(scan: ModuleType) -> None:
    """The class: in a fresh interpreter, no holder the walk reads holds a value of any of nine
    calls, after any of them (the module's docstring has the design): one walk after each call,
    each seeing the canary, and the pack the one this process imported."""
    ran = subprocess.run(
        [sys.executable, "-I", "-c", RUN, str(SOURCE), str(SCAN)],
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert ran.returncode == 0, ran.stderr[-2000:]
    report = scan.report_of(ran.stdout)
    assert report["pid"] != os.getpid()  # another process, whatever the test does
    assert report["fresh_start"] is True  # one that had not loaded the pack
    assert Path(report["pack"]) == PACK  # the pack of this tree
    assert report["outcomes"] == OUTCOMES  # every call did what the scan means it to
    assert report["walks"] == len(OUTCOMES)  # a walk after each call
    assert report["canary_seen"] is True  # and each walk sees a str key of a dict in this heap
    assert report["hits"] == []


@pytest.fixture(scope="module")
def pins() -> Mapping[str, Any]:
    """``retention_pins.py``'s report, from a child of its own (a heap without the suite's)."""
    ran = subprocess.run(
        [sys.executable, "-I", str(PINS)],
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert ran.returncode == 0, ran.stderr[-2000:]
    found = json.loads(ran.stdout.strip().splitlines()[-1])
    assert isinstance(found, dict)
    return found


KILLED_BY_THE_MATRIX = frozenset(
    {"NB03", "NB05", "NB06", "NB16", "C11", "C11i", "C12", "C13", "C15", "C16", "C17", "C18"}
    | {"C40", "C41", "C42", "C43", "C44", "C45", "C46", "C46b", "C47", "C48", "C49", "C50"}
)
"""The mutants of D438's table that keep a whole marker in one of the matrix's holders."""


def test_the_walk_reads_a_form_as_a_class_of_holders_and_encodings(pins: Mapping[str, Any]) -> None:
    """``retention_pins.CELLS``: every holder the walk reads, each holding each form in each
    encoding it can (text, UTF-8, UTF-16 and UTF-32 of either order, code points), and each
    transform the walk undoes, is found by its own read; the mutants that keep a marker so are
    each a cell of it, named."""
    matrix = pins["matrix"]
    assert matrix["missing"] == []
    assert len(matrix["cells"]) >= 320
    named = matrix["named"]
    assert set(named) == KILLED_BY_THE_MATRIX
    assert [name for name, cells in named.items() if len(cells) != 3] == []


def test_the_walk_finds_each_route_and_nothing_once_it_is_released(
    pins: Mapping[str, Any],
) -> None:
    """``retention_pins.ROUTES``, ``FOLLOWED``, ``PARTS`` and ``NAMES``: each way an object holds a
    marker is found, with the chain that holds it, and so is a marker behind an instance of each
    type the walk follows alone (its ``PLAIN`` and its three sequences: every one has such a
    route, or is named in ``UNFOLLOWED`` with why none can be its alone), in each part of a code
    object the walk reads (``CODE_READ``: every one has a route) alone, in a code object nested in
    another, in a class's name alone and in its qualified name alone, and in the names of a class
    a metaclass made; without the dict keys, which
    ``gc.get_referents`` does not give for a dict of str keys, the routes that hold a marker as
    such a key alone are missed, and the walk with them finds those; and once everything is
    released, nothing is found."""
    routes = pins["routes"]
    assert routes["found"] == routes["routes"]
    assert len(routes["routes"]) == 42
    assert routes["followed without a route"] == 0
    assert routes["code parts without a route"] == []
    assert routes["default chained"] is True
    assert routes["missed without keys"] == sorted(
        {"a dict's key", "a default argument", "a closure cell", "a module's name"}
        | {"a defaultdict's key"}
    )
    assert routes["after release"] == 0


def test_the_walk_s_leaves_are_the_atoms_alone(scan: ModuleType) -> None:
    """``LEAVES``, the types whose objects the walk neither reads nor follows, are the five atoms
    that hold nothing (a float, a bool, ``None``, a complex, ``Ellipsis``): no type it reads or
    follows is one."""
    atoms = (float, bool, type(None), complex, type(...))
    assert frozenset(id(kind) for kind in atoms) == scan.LEAVES
    followed = (tuple, list, dict, str, bytes, bytearray, int, memoryview, range, slice, deque)
    assert not scan.LEAVES & ({id(kind) for kind in followed} | set(scan.PLAIN))


def test_a_walk_after_each_call_finds_what_a_later_call_drops(pins: Mapping[str, Any]) -> None:
    """NB04d's shape through the scan's own loop (``drive``): a value kept by one call and dropped
    by the next is found by the walk after the first call, and by no walk after the last."""
    assert pins["order"] == {"outcomes": ["ok", "ok", "ok"], "walk after each": [1], "one walk": 0}


@pytest.fixture(scope="module")
def handed(scan: ModuleType, tmp_path_factory: pytest.TempPathFactory) -> tuple[Any, Any]:
    """``retention_scan.handed``: the scan's calls, every entry point of the pack recording what
    it receives; the fields, and the entry points called."""
    found: tuple[Any, Any] = scan.handed(tmp_path_factory.mktemp("handed"))
    return found


def test_every_value_of_a_call_carries_its_marker(
    scan: ModuleType, handed: tuple[Any, Any]
) -> None:
    """``FIELDS``: each value each of the nine calls hands the pack, as the pack's entry points
    receive it (every argument but the operator's reader and limits: the study's files and every
    option of an import, the bytes, names and roles of a read, every field of a release handed to
    a hook and every leaf of a descriptor in it that differs from the import's own, a code handed
    to an ontology system), carries the call's marker, but for the vocabulary, numbers and
    absences the table names; a field added, a value that loses its marker, or a call site that
    hands a value of its own (a fixed dataset id, a renamed descriptor), fails here."""
    assert handed[0] == scan.FIELDS


def test_the_scan_drives_every_entry_point_of_the_pack(
    scan: ModuleType, handed: tuple[Any, Any]
) -> None:
    """``retention_scan.entry_points``, derived from ``Pack``'s fields and the protocols they name
    (not listed by hand): each entry point of the pack is driven by the scan's calls (``DRIVEN``)
    or excluded with why (``EXCLUDED``), never both, and every one ``DRIVEN`` names is called."""
    pack = importlib.import_module("aibi.packs.onco").PACK
    points = set(scan.entry_points(pack))
    assert points == scan.DRIVEN | set(scan.EXCLUDED)
    assert not scan.DRIVEN & set(scan.EXCLUDED)
    assert all(reason for reason in scan.EXCLUDED.values())
    assert handed[1] - {"clinical.read"} == scan.DRIVEN


def _load(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_marker_table_notices_a_value_that_lost_its_marker(tmp_path: Path) -> None:
    """S12 of D438's table, a builder mutant: the study meta's ``name`` written without its marker
    is the one field the check reports."""
    [s12] = [m for m in _load(TABLE, "retention_table").MUTANTS if m.name == "S12"]
    [edit] = s12.edits
    source = SCAN.read_text(encoding="utf-8")
    assert source.count(edit.anchor) == 1
    mutated = tmp_path / "retention_scan.py"
    mutated.write_text(source.replace(edit.anchor, edit.replacement), encoding="utf-8")
    places = tmp_path / "places"
    places.mkdir()
    found = _load(mutated, "retention_scan_s12").unmarked(places)
    clean = _load(SCAN, "retention_scan_clean").FIELDS
    assert {k: v for k, v in found.items() if clean.get(k) != v} == {
        "meta_study.txt: name": frozenset({"Name"})
    }


def _refusal(label: str) -> Any:
    """A function that raises, naming what the walk called."""

    def refuse(*_: object, **__: object) -> Any:
        raise RuntimeError(f"the walk called {label}")

    return refuse


def test_the_walk_runs_no_code_of_an_object_it_walks(pins: Mapping[str, Any]) -> None:
    """The walk runs no code of what it walks, in ``retention_pins.py``'s child. ``twins``: the
    matrix again, every holder and every part of it a generated twin, a subclass of the walked
    base type that overrides every callable or data descriptor of its base, under a metaclass
    generated so from ``type`` (but what makes a class), and every holder under a twin module and
    a twin dict, which a hit's chain names; armed for the walk, each records that it ran and
    raises, so that a swallowed raise is still seen: every cell is found, by its own read, and
    nothing ran. The generator checks itself first: every slot of each base overridden, one slot
    of each twin recording and raising, every part of every cell a twin. ``HOSTILE``: objects
    that raise on what a walk could reach besides (a metaclass's attribute lookup, a module named
    by a ``str`` subclass, a key that collides with ``__name__``, a class named by one), each
    found, every step of each chain a plain ``str``; and holders whose read raises
    ``ValueError`` (closed, released, never initialised) are passed by. Issue #140 found such an
    object in the suite's own heap (``tests/core/schema/test_guards.py``'s ``_Owned``, whose
    metaclass raises in ``__hash__``), which these hold the class of. What this does not hold:
    ``gc.collect()`` may run a finalizer, and the interpreter's own traverse of an object is the
    interpreter's."""
    twins = pins["twins"]
    assert (twins["bare slots"], twins["silent twins"], twins["untwinned"]) == ([], [], [])
    assert (twins["error"], twins["ran"], twins["missing"], twins["armed"]) == (None, [], [], True)
    assert twins["cells"] == len(pins["matrix"]["cells"])
    hostile = pins["hostile"]
    assert hostile["found"] == hostile["held"]
    assert len(hostile["held"]) == 21
    assert hostile["plain chains"] is True


def test_the_walk_refuses_a_sys_modules_that_is_not_a_dict(
    scan: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``sys.modules`` is read through ``dict.values``, which is safe for a dict alone: any other
    mapping fails the walk, saying so, before one of its methods runs."""

    class _Rebound(dict):  # type: ignore[type-arg]
        values = items = keys = __iter__ = _refusal("sys.modules")

    monkeypatch.setattr(sys, "modules", _Rebound(sys.modules))
    with pytest.raises(RuntimeError, match=r"sys\.modules is a _Rebound, not a dict"):
        scan.reachable([scan.Marker.new()])


def test_the_walk_asks_no_module_but_asyncio_for_its_referents(
    scan: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The exception of issue #140 is as narrow as it says, on every interpreter: the ``_asyncio``
    module is walked through its dict alone, and every other module in ``sys.modules`` through
    ``gc.get_referents``, as before. A spy on ``gc.get_referents`` raises if it is asked for the
    ``_asyncio`` module, so that a regression fails here at once, and never hands the module's
    dead objects to this process (where, on CPython before 3.12.6, it would hang at exit)."""
    import _asyncio

    real = gc.get_referents

    def spy(*objects: object) -> list[object]:
        assert not any(o is _asyncio for o in objects), (
            "the walk asked the _asyncio module for its referents (issue #140)"
        )
        return real(*objects)

    monkeypatch.setattr(gc, "get_referents", spy)
    assert scan._referents(_asyncio) == [vars(_asyncio)]
    for name, module in list(sys.modules.items()):
        if isinstance(module, ModuleType) and module is not _asyncio:
            assert scan._referents(module) == real(module), name


def test_the_walk_survives_a_process_that_has_awaited_a_future() -> None:
    """Issue #140: on CPython before 3.12.6 (3.12.3 to 3.12.5 checked) the ``_asyncio`` module's
    traverse hands out the dead objects of its free list of ``FutureIter``, and a walk of the heap
    in a process that has awaited a future (the suite's ``asyncio`` tests) hangs or faults. The
    walk runs in a child that has, and must end; the child dumps its traceback and exits after 20
    s, so that a hang fails in 20 s and the failure names issue #140 and the interpreter (the dump
    proves only a hang: it is in finalization, with no Python frame)."""
    code = (
        "import faulthandler\n"
        "faulthandler.dump_traceback_later(20, exit=True)\n"
        "import asyncio, importlib.util, sys\n"
        "spec = importlib.util.spec_from_file_location('retention_scan', sys.argv[1])\n"
        "scan = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(scan)\n"
        "async def main():\n"
        "    loop = asyncio.get_running_loop()\n"
        "    for _ in range(3):\n"
        "        future = loop.create_future()\n"
        "        loop.call_soon(future.set_result, 1)\n"
        "        await future\n"
        "asyncio.run(main())\n"
        "assert scan.reachable([scan.Marker.new()]) == []\n"
        "asyncio.run(main())\n"
        "print('walked')\n"
    )
    ran = subprocess.run(
        [sys.executable, "-I", "-c", code, str(SCAN)],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert ran.returncode == 0, f"issue #140 on CPython {sys.version}: {ran.stderr[-2000:]}"
    assert ran.stdout.strip() == "walked"

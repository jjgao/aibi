"""The pack keeps nothing past a call (M4.2a-1b, D438): a process-level cache or list would hold the
cells and ids of an erased dataset for the server's life.

Three guards, none of which names a module, a function or a column. The first holds the routes it
names, by name (a spelling of cache, a kind of binding); the second what grows with the file; the
third, the marker scan, the class: anything of a file reachable after a call, however it is kept:

- ``banned``, over **every** module under ``packs/onco`` (the whole tree, whatever is added to
  it): the AST holds no ``functools`` (every cache the standard library has), no ``global``, no
  ``globals()`` or ``__import__``, binds no mutable container (a list, dict or set, written or
  made by ``list()``, ``dict()``, ``set()``, ``deque()``...) at module level or in a class body,
  and gives no ``def`` or ``lambda`` a mutable default argument. A frozen constant is allowed: a
  tuple, a ``frozenset``, a compiled regex, a ``MappingProxyType`` (an immutable mapping), a
  number or a string, and ``__all__``. And, at run time, no module of the pack binds a value
  that is a mutable container or of a type of ``functools`` (``module_state``), whatever the
  call that made it, nor does any function or method of the pack hold one in its ``__defaults__``,
  its ``__kwdefaults__`` or a closure cell;
- the sentinel retention test: each kind of column given many short distinct values, through
  ``clinical.read`` and through ``PACK.importer``, at two sizes; once the call has returned and
  its result is dropped, what ``tracemalloc`` still holds must not grow with the size. A cache
  of a value of the file, in any module, grows with the file; so does a module-level list of ids.
  A retention that does not grow with the file (a memo of the last 128 values, the first file's
  reader, a dataset's first ids, a study's meta bytes) it does not see;
- the marker scan (``retention_scan.py``, run by ``test_nothing_of_a_file_is_reachable...``): in
  a fresh interpreter, two imports and two reads and an import and a read that are refused, each
  with a marker of its own in every value (ids, cells, names, labels, definitions, missing
  spellings, meta values, file and dataset names); once the calls have returned, the heap is
  walked from ``gc.get_objects()`` and ``sys.modules``, through every referent and every dict's
  keys, and no ``str``, ``bytes``, ``bytearray`` or ``array`` may hold one.

The marker scan does not see state held outside the Python heap (a cache that a C extension keeps
for itself) or an object reachable only through the frames of a thread that is still running, nor
a value that cannot carry a marker (a status the format fixes): what grows with the rows of those is
the sentinel's to see.
"""

import ast
import gc
import importlib
import inspect
import os
import pkgutil
import subprocess
import sys
import tracemalloc
import types
from array import array
from collections import deque
from collections.abc import Callable, Iterator, Mapping, MutableMapping, MutableSequence, MutableSet
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

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


# --- The marker scan: what of a file is reachable once the calls have returned ---------------

SCAN = Path(__file__).resolve().with_name("retention_scan.py")
OUTCOMES = ["ok", "ok", "ok", "ok", "EXCEL_DATE", "MISSING_FORM"]
"""The scan's six calls: two imports, two reads, an import refused at its last patient row, a read
refused at its end."""


def test_nothing_of_a_file_is_reachable_after_the_calls(scan: ModuleType) -> None:
    """The class: in a fresh interpreter, no ``str``, ``bytes``, ``bytearray`` or ``array`` in the
    heap holds a value of any of six calls (the module's docstring has the design)."""
    ran = subprocess.run(
        [sys.executable, "-I", str(SCAN)],
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert ran.returncode == 0, ran.stderr[-2000:]
    report = scan.report_of(ran.stdout)
    assert report["pid"] != os.getpid()  # another process, whatever the test does
    assert report["fresh_start"] is True  # one that had not loaded the pack
    assert report["outcomes"] == OUTCOMES  # every call did what the scan means it to
    assert report["canary_seen"] is True  # and its walk sees a str key of a dict in this heap
    assert report["hits"] == []


def _make_dict_key(text: str) -> object:
    return {text + "k": 1}


def _make_dict_value(text: str) -> object:
    return {"k": text + "v"}


def _make_list(text: str) -> object:
    return [1, [text]]


def _make_set(text: str) -> object:
    return {(1, text + "s")}


def _make_default(text: str) -> object:
    def f(x: int, memo: dict[str, int] = {text + "d": 1}) -> int:  # noqa: B006
        return x + len(memo)

    return f


def _make_kwdefault(text: str) -> object:
    def f(x: int, *, memo: list[str] = [text]) -> int:  # noqa: B006
        return x + len(memo)

    return f


def _make_closure(text: str) -> object:
    held = {text + "c": 1}
    return lambda: held


def _make_attribute(text: str) -> object:
    class Holder:
        pass

    holder = Holder()
    holder.kept = [text]  # type: ignore[attr-defined]
    return holder


def _make_class_attribute(text: str) -> object:
    return type("Holder", (), {"kept": (text,)})


def _make_bytes(text: str) -> object:
    return [b"xx" + text.encode() + b"yy"]


def _make_bytearray(text: str) -> object:
    return [bytearray(text.encode())]


def _make_array(text: str) -> object:
    return [array("b", text.encode())]


def _make_module(text: str) -> object:
    module = types.ModuleType("scratch_held")
    module.__dict__[text + "n"] = 1
    sys.modules["scratch_held"] = module
    return module


HOLDERS: Mapping[str, tuple[Callable[[str], object], str]] = {
    "a dict's key": (_make_dict_key, "lower"),
    "a dict's value": (_make_dict_value, "lower"),
    "a list in a list": (_make_list, "lower"),
    "a set of tuples": (_make_set, "lower"),
    "a default argument": (_make_default, "lower"),
    "a keyword default": (_make_kwdefault, "lower"),
    "a closure cell": (_make_closure, "lower"),
    "an instance's attribute": (_make_attribute, "lower"),
    "a class attribute": (_make_class_attribute, "lower"),
    "bytes": (_make_bytes, "lower"),
    "a bytearray": (_make_bytearray, "lower"),
    "an array": (_make_array, "lower"),
    "a module's name": (_make_module, "lower"),
    "the upper case form": (_make_list, "upper"),
    "the marks form": (_make_set, "marks"),
}
"""Each way an object holds a marker, with the form of the marker it holds."""
KEYED = frozenset({"a dict's key", "a default argument", "a closure cell", "a module's name"})
"""The holders in which a marker is the key of a dict of str keys, and nothing else."""
KEPT: list[object] = []
"""Where the walk's unit test holds what it hides: a heap object, which a frame's locals are not."""


def test_the_walk_finds_each_way_of_holding_a_marker_and_nothing_once_it_is_released(
    scan: ModuleType,
) -> None:
    """One walk for every holder (a walk reads the whole of the process's heap, and each is a
    marker of its own): each is found, with the chain that holds it; without the dict keys, which
    ``gc.get_referents`` does not give for a dict of str keys, the holders that hold a marker as
    such a key are missed, and the walk with them finds those; and once everything is released,
    nothing is found."""
    names = list(HOLDERS)
    markers = [scan.Marker.new() for _ in names]
    try:
        for marker, (make, form) in zip(markers, HOLDERS.values(), strict=True):
            KEPT.append(make(scan.fresh(getattr(marker, form))))
        found = scan.reachable(markers)
        assert sorted({names[hit.call] for hit in found}) == sorted(names)
        default = names.index("a default argument")
        assert any(
            "_make_default" in step for hit in found if hit.call == default for step in hit.chain
        )
        without = {names[hit.call] for hit in scan.reachable(markers, keys=False)}
        assert set(names) - without == KEYED
        KEPT.clear()
        sys.modules.pop("scratch_held", None)
        assert scan.reachable(markers) == []
    finally:
        KEPT.clear()
        sys.modules.pop("scratch_held", None)

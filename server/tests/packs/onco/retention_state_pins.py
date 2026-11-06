"""The state guards' own pins (issue #136, PR B; D438), run by ``test_retention`` in a child of
their own: a fresh interpreter, the tree's ``src`` first on its path, the pack imported here.

- **Content, not representation** (``probes``, first, before any allow-list pass, which would
  materialise what it reads and mask a snapshot that reads a representation): a third party's
  ``vars()``, ``inspect.signature``, ``typing.get_type_hints``, ``copy``, ``deepcopy`` and
  ``pickle`` (each of the last three on a class of its own, the first it does, so that each
  writes the ``__slotnames__`` it writes; and a class with ``__slots__``, whose list is not
  empty), all run between two snapshots of the pack, change no digest (a difference no probe can
  undo: each only materialises or writes).
- **The clean pack** (``clean``): the allow-list refuses nothing of the pack, and every rule of
  ``RULES`` admits something, of the pack or of ``FROZEN_STATE``; every member of the stop set is
  met inside a foreign holder.
- **The cases of PR A's run-time deny-list** (``STATES``), each judged by the allow-list as a
  module of the pack's: each is refused, and ``FROZEN_STATE`` is not.
- **After every call, against the first** (``ORDER``): a state set by a call, kept by the next
  and reset by the third, through ``drive``, the scan's own loop, and ``Guard``: found after the
  first two calls by both guards, and not after the third.
- **The snapshot's reach** (``ROUTES``): a value changed through each holder it enters (a module
  value, a class attribute, an instance's attribute, a function's ``__dict__``, a default, a
  keyword default, a closure cell, a set, a mappingproxy's dict, a typing object's metadata, a
  bound method's ``__self__``, a pydantic model's private dict, and a core class and a core
  function the pack imports) changes its digest.
- **Digests only** (``digests``): no path or digest of a snapshot holds a value's text, a key's
  among them.
- **The brute force** (``brute``): every mutable builtin container, by each of its mutating
  bound methods, bound in each holder (a module value, a class attribute, a default, a keyword
  default, a closure cell, a tuple's element, an attribute of the exempt importer): the
  allow-list refuses each, and the snapshot sees each method's call change it. The snapshot reads
  each cell's own holder alone (its function, class, tuple or bound method, or the importer).
- **Each rule's narrowing** (``RULE_TWINS``): for every rule of ``RULES``, objects just outside
  it, each refused where it is and nothing else of its module.
- **The stop set looked through** (``STOP_MADE`` by ``STOP_HOLDERS``): each member holding a
  stateful object of the pack's, in an alias's metadata, a foreign frozen model and the exempt
  ``_ADAPTER``: each refused.
- **Each leaf** (``leaves``): every leaf the snapshot reads, in each of seven holders, replaced in
  place or anew by one of its size, length and type: the snapshot sees each.
- **No value in a report** (``disclosure``): a reject's path and kind hold neither a key's text
  nor the name of a class named by a value, and a key's label is a keyed digest.
- **Totality** (``TWINS``, last: they stay armed): one hostile twin for each predicate of the
  rules (a metaclass that runs code on a comparison, a hash, an attribute or a check; a fake
  ``__dataclass_params__``, ``model_config`` or ``_fields``; a spoofed ``__class__``; a
  ``__dict__`` property; hostile keys, lists, dicts, a ``str`` subclass, a method's ``__self__``),
  through the allow-list and the snapshot: no twin's code runs, and each that no rule admits is
  refused by its type.

It prints one JSON line: what was found of each, by name, never a value.
"""

import copy
import importlib.util
import inspect
import io
import json
import pickle
import re
import secrets
import sys
import typing
from collections.abc import Callable
from pathlib import Path
from types import (
    FunctionType,
    GetSetDescriptorType,
    MemberDescriptorType,
    ModuleType,
    SimpleNamespace,
)
from typing import Any

HERE = Path(__file__).resolve().parent
STATE = HERE / "retention_state.py"
SCAN = HERE / "retention_scan.py"


def _load(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def module(state: Any, name: str, source: str) -> ModuleType:
    """``source`` run as a module of the pack's (``aibi.packs.onco.<name>``), in ``sys.modules``."""
    made = ModuleType(f"{state.PREFIX}.{name}")
    sys.modules[made.__name__] = made
    exec(compile(source, f"<{name}>", "exec"), made.__dict__)
    return made


# --- Content, not representation -----------------------------------------------------------------

SLOTTED = (
    "import abc\n"
    "class Slotted:\n    __slots__ = ('a', '__b')\n\n    def __init__(self):\n"
    "        self.a = 1\n        self.__b = 2\n"
    "class Kind(abc.ABC):\n    pass\n"
)
"""A class of the pack's with ``__slots__`` (one of them private): ``copyreg`` writes a list of
its names, which is not empty; and an ABC of the pack's, whose cache ``isinstance`` writes.
``ALIASED`` is a type alias, whose value is computed and cached when first read: the snapshot
alone is pinned on it (the allow-list refuses a type alias of the pack's)."""
ALIASED = "type Alias = int\n"


def probes(pack: Any, slotted: ModuleType, aliased: ModuleType) -> dict[str, Callable[[], object]]:
    from aibi.packs.onco import build, facets, study

    return {
        "vars(PACK)": lambda: vars(pack),
        "inspect.signature(facet)": lambda: inspect.signature(facets.facet),
        "typing.get_type_hints(facet)": lambda: typing.get_type_hints(facets.facet),
        "vars(_ADAPTER)": lambda: vars(build._ADAPTER),  # pyright: ignore[reportPrivateUsage]
        "copy.copy(importer)": lambda: copy.copy(pack.importer),
        "copy.deepcopy(validator)": lambda: copy.deepcopy(pack.validator),
        "pickle.dumps(PATH_SITES[0])": lambda: pickle.dumps(study.PATH_SITES[0]),
        "copy.deepcopy(manifest)": lambda: copy.deepcopy(pack.manifest),
        "pickle.dumps(manifest)": lambda: pickle.dumps(pack.manifest),
        "copy.copy(Slotted())": lambda: copy.copy(slotted.Slotted()),
        "isinstance(object(), Kind)": lambda: isinstance(object(), slotted.Kind),
        "Alias.__value__": lambda: aliased.Alias.__value__,
    }


def representation(state: Any, modules: list[ModuleType], names: frozenset[str]) -> Any:
    from aibi.packs.onco import PACK

    slotted = module(state, "_pins_slotted", SLOTTED)
    aliased = module(state, "_pins_aliased", ALIASED)
    roots = [*modules, slotted, aliased]
    before = state.snapshot(roots, names)
    ran = probes(PACK, slotted, aliased)
    for probe in ran.values():
        probe()
    found = state.differences(before, state.snapshot(roots, names))
    judged = state.allowed([*modules, slotted], state.exemptions(modules), names)
    return {
        "probes": list(ran),
        "differences": len(found),
        "rejects": [list(r) for r in judged.rejects],
    }


# --- The clean pack, and PR A's cases ------------------------------------------------------------

FROZEN_STATE = (
    "import re\nfrom types import MappingProxyType\nimport aibi.core.schema.pack_api as P\n"
    "A = (1, 2)\nB = frozenset({1})\nC = re.compile('x')\nD = MappingProxyType({'a': 1})\n"
    "J = ','.join\nL = len\nN = (1).__add__\n"
    "def f(a=(), b=None, c=1, d='x', e=frozenset(), g=(1, 2), *, h=0.5, i=b''): ...\n"
    "def make():\n    seen = []\n    k = 3\n    def g():\n        return k\n    return g\n"
    "G = make()\n"
    "class K:\n    names = ('a',)\n    def m(self, x=(), y=None): ...\n"
    "    @staticmethod\n    def s(z=1): ...\n    @property\n    def p(self): return 1\n"
)
"""PR A's frozen module, and a builtin bound to nothing (``len``) and to an atom (``','.join``,
``(1).__add__``): what the allow-list admits."""
STATES = {
    "a module-level list": "SEEN = []\n",
    "a module-level SimpleNamespace": "import types\nSEEN = types.SimpleNamespace()\n",
    "a module-level lru_cache": "import functools\n@functools.lru_cache\ndef f(x): return x\n",
    "a list default": "def f(x, seen=[]): ...\n",
    "a dict default": "def f(x, seen={}): ...\n",
    "a keyword-only default": "def f(x, *, seen={}): ...\n",
    "a default made by a call": "def make(): return {}\ndef f(x, seen=make()): ...\n",
    "a deque default": "import collections\ndef f(x, seen=collections.deque()): ...\n",
    "a bytearray default": "def f(x, seen=bytearray()): ...\n",
    "a lambda default": "f = lambda x, seen=[]: seen\n",
    "a method default": "class C:\n    def f(self, seen={}): ...\n",
    "an underscored method default": "class C:\n    def _f(self, seen=[]): ...\n",
    "a static method default": "class C:\n    @staticmethod\n    def f(seen=[]): ...\n",
    "a class method default": "class C:\n    @classmethod\n    def f(cls, seen=[]): ...\n",
    "a property default": "class C:\n    @property\n    def p(self, seen=[]): ...\n",
    "an underscored class attribute made by a call": "class C:\n    _seen = dict()\n",
    "a dunder of the module's own": "__cache__ = []\n",
    "a __slotnames__ that is not copyreg's": "class C:\n    __slotnames__ = ['seen']\n",
    "a module made as an attribute bag": "import types\nSEEN = types.ModuleType('seen')\n",
    "a NamedTuple field's default (itertools.count)": (
        "import itertools, typing\nclass Row(typing.NamedTuple):\n"
        "    n: object = itertools.count()\n"
    ),
    "a plain class's _fields (a hashlib object)": (
        "import hashlib\nclass Seen:\n    _fields = hashlib.sha256()\n"
    ),
    "a __hash__ closing over a counter": (
        "import itertools\ndef make():\n    c = itertools.count()\n    def h(self):\n"
        "        return next(c)\n    return h\nclass K:\n    __hash__ = make()\n"
    ),
    "a dataclass field's default_factory bound to a hashlib object": (
        "import dataclasses, hashlib\n@dataclasses.dataclass(frozen=True)\nclass D:\n"
        "    a: object = dataclasses.field(default_factory=hashlib.sha256().copy)\n"
    ),
    "a hashlib object as a module dict's key": (
        "import hashlib\nglobals()[hashlib.sha256()] = 1\n"
    ),
    "a closure over a dict, its function saying another module": (
        "def make():\n    seen = {}\n    def keep(x):\n        seen[x] = 1\n    return keep\n"
        "KEEP = make()\nKEEP.__module__ = 'json'\n"
    ),
    "a partial over a Counter, met first in an alias's metadata, then bound": (
        "import collections, functools, typing\n"
        "ALIAS = typing.Annotated[str, functools.partial(print, collections.Counter())]\n"
        "KEPT = ALIAS.__metadata__[0]\n"
    ),
    "a function made with globals of its own, closing over a dict": (
        "import types\ndef make():\n    seen = {}\n    def keep(x):\n        seen[x] = 1\n"
        "    return keep\nKEEP = types.FunctionType(make().__code__, {}, 'keep', None,"
        " make().__closure__)\n"
    ),
    "a class saying another module, holding a dict": (
        "class C:\n    __module__ = 'json'\n    seen = dict()\n"
    ),
    "a partial over a hashlib object's update, in an alias's metadata": (
        "import functools, hashlib, typing\n"
        "ALIAS = typing.Annotated[str, functools.partial(hashlib.sha256().update)]\n"
    ),
    "a bound method of a stateful instance, met first in an alias's metadata": (
        "import hashlib, typing\nclass Acc:\n    def __init__(self):\n"
        "        self.h = hashlib.sha256()\n    def add(self, x):\n        self.h.update(x)\n"
        "ALIAS = typing.Annotated[str, Acc().add]\nADD = ALIAS.__metadata__[0]\n"
    ),
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
"""PR A's run-time deny-list's cases (``module_state``, which the allow-list replaces), and what
the deny-list let through (a ``SimpleNamespace``, an underscored class attribute): each refused. A
``nonlocal`` counter holds an ``int``, which is admitted: the source guard (``banned``) refuses
every ``nonlocal``."""


def clean(state: Any, modules: list[ModuleType], names: frozenset[str]) -> Any:
    exempt = state.exemptions(modules)
    judged = state.allowed(modules, exempt, names)
    frozen = module(state, "_pins_frozen", FROZEN_STATE)
    admitted = state.allowed([frozen], exempt, state.names_of([FROZEN_STATE]))
    stop = {state._Kinds().of(kind).name for kind, _ in state.stop_set()}
    refused = {}
    for index, (case, source) in enumerate(STATES.items()):
        made = module(state, f"_pins_state_{index}", source)
        refused[case] = len(state.allowed([made], exempt, names).rejects)
    return {
        "rejects": [list(r) for r in judged.rejects],
        "admitted": judged.admitted,
        "frozen rejects": [list(r) for r in admitted.rejects],
        "frozen admitted": admitted.admitted,
        "stopped": judged.stopped,
        "stop set": sorted(stop),
        "exempt": len(exempt),
        "states": refused,
        "rules": [rule.name for rule in state.RULES],
        "legs": {rule.name: ["admission", *rule.reads] for rule in state.RULES},
    }


MANIFEST = (
    "from aibi.core.schema.pack_api import PackManifest\n"
    "X = PackManifest(id='p', version='0.0.1', results_version=1, requires_core='>=0')\n"
)
ACC = (
    "import hashlib\nclass Acc:\n    def __init__(self):\n        self.h = hashlib.sha256()\n"
    "    def add(self, x):\n        self.h.update(x)\n"
)
RULE_TWINS: dict[str, dict[str, dict[str, str]]] = {
    "atom": {"admission": {"a str subclass": "class S(str):\n    pass\nX = S('a')\n"}},
    "tuple or frozenset": {
        "admission": {"a tuple subclass": "class T(tuple):\n    pass\nX = T()\n"},
        "a tuple's items": {"a tuple holding a list": "X = (1, list())\n"},
        "a frozenset's members": {
            "a frozenset holding a stateful object": ACC + "X = frozenset({Acc()})\n",
        },
    },
    "pattern": {"admission": {"a match": "import re\nX = re.compile('x').match('x')\n"}},
    "library module": {
        "admission": {
            "a module made, not registered": "import types\nX = types.ModuleType('x')\n",
            "a module made and registered under a foreign name": (
                "import sys, types\n"
                "X = sys.modules.setdefault('_pins_made_x', types.ModuleType('_pins_made_x'))\n"
            ),
            "an instance of a module subclass, registered": (
                "import sys, types\nclass M(types.ModuleType):\n    pass\n"
                "X = sys.modules.setdefault('_pins_made_m', M('_pins_made_m'))\n"
            ),
        },
    },
    "core module": {
        "admission": {
            "a module made under a name that only starts as aibi's": (
                "import sys, types\n"
                "X = sys.modules.setdefault('aibix_pins', types.ModuleType('aibix_pins'))\n"
            ),
        },
    },
    "pack module": {
        "admission": {
            "a module made under the pack's prefix, not a root": (
                "import sys, types\nX = sys.modules.setdefault('aibi.packs.onco._pins_made',"
                " types.ModuleType('aibi.packs.onco._pins_made'))\n"
            ),
        },
    },
    "class": {
        "admission": {"an instance of a class of the pack's": "class C:\n    pass\nX = C()\n"},
        "a pack class's own dict's values": {
            "a class of the pack's holding a list": "class C:\n    seen = list()\n",
        },
        "a pack class's name and qualified name": {
            "a class whose qualified name holds a list": (
                "class S(str):\n    pass\nclass C:\n    pass\nC.__qualname__ = S('C')\n"
                "C.__qualname__.seen = list()\n"
            ),
        },
        "a pack class's own dict's keys": {
            "a class keyed by a stateful object": (
                "import hashlib\nclass C:\n    locals()[hashlib.sha256()] = 1\n"
            ),
        },
    },
    "function": {
        "admission": {
            "a partial at module level": "import functools\nX = functools.partial(print)\n"
        },
        "its defaults": {
            "a function with a list default": "def f(x, seen=list()):\n    return x\n"
        },
        "its keyword defaults": {
            "a function with a list keyword default": "def f(*, seen=list()):\n    return seen\n",
        },
        "its closure cells": {
            "a closure over a list": (
                "def make():\n    seen = []\n    def g():\n        return seen\n    return g\n"
                "G = make()\n"
            ),
        },
        "its __dict__": {
            "a function's __dict__ keyed by a stateful object": (
                "import hashlib\ndef f():\n    return 1\nf.__dict__[hashlib.sha256()] = 1\n"
            ),
            "a function's __dict__ holding a list": "def f():\n    return 1\nf.seen = list()\n",
        },
        "its names, module, doc, annotations and type parameters": {
            "a function whose doc is a list": "def f():\n    return 1\nf.__doc__ = list()\n",
            "a function whose annotations hold a list": (
                "def f():\n    return 1\nf.__annotations__ = {'a': list()}\n"
            ),
        },
    },
    "descriptor": {
        "admission": {
            "a cached_property": (
                "import functools\nclass C:\n    @functools.cached_property\n"
                "    def p(self):\n        return 1\n"
            ),
        },
        "each function": {
            "a static method with a list default": (
                "class C:\n    @staticmethod\n    def f(seen=list()):\n        return seen\n"
            ),
            "a property whose getter has a list default": (
                "class C:\n    @property\n    def p(self, seen=list()):\n        return seen\n"
            ),
        },
        "the wrapper's own dict": {
            "a static method's own dict holding a list": (
                "class C:\n    @staticmethod\n    def f():\n        return 1\n"
                "C.__dict__['f'].__dict__['seen'] = list()\n"
            ),
        },
        "the wrapper's __annotations__": {
            "a static method's __annotations__ holding a list": (
                "class C:\n    @staticmethod\n    def f():\n        return 1\n"
                "C.__dict__['f'].__dict__['__annotations__'] = {'a': list()}\n"
            ),
        },
    },
    "slot descriptor": {
        "admission": {
            "a property in a class's dict, a getter with a list default": (
                "class C:\n    d = property(lambda self, seen=list(): seen)\n"
            ),
        },
    },
    "unbound builtin": {
        "admission": {
            "a builtin bound to a list": "X = list().append\n",
            "a builtin bound to a module made": "import types\nX = types.ModuleType('s').__dir__\n",
        },
    },
    "bound callable": {
        "admission": {"a method-wrapper of a list": "X = list().__len__\n"},
        "its __self__": {"a method bound to a stateful instance": ACC + "X = Acc().add\n"},
        "its function": {
            "a method whose function has a list default": (
                "import types\nX = types.MethodType(lambda x, seen=list(): x, 1)\n"
            ),
        },
    },
    "typing": {
        "admission": {
            "a ParamSpec": "import typing\nX = typing.ParamSpec('X')\n",
            "a type alias of the pack's": "type X = int\n",
        },
        "everything it refers to, inside a foreign holder": {
            "a list in an alias's metadata": "import typing\nX = typing.Annotated[str, list()]\n",
        },
    },
    "mappingproxy": {
        "admission": {
            "a mappingproxy over an OrderedDict": (
                "import collections, types\nX = types.MappingProxyType(collections.OrderedDict())\n"
            ),
        },
        "its keys": {
            "a key that is a stateful object": (
                "import hashlib, types\nX = types.MappingProxyType({hashlib.sha256(): 1})\n"
            ),
        },
        "its values": {
            "a value that is a list": "import types\nX = types.MappingProxyType({'k': list()})\n",
        },
    },
    "pack NamedTuple": {
        "admission": {
            "a NamedTuple of another package's": (
                "import urllib.parse\nX = urllib.parse.urlsplit('a')\n"
            ),
        },
        "its items": {
            "a NamedTuple holding a list": (
                "import typing\nclass R(typing.NamedTuple):\n    a: object\nX = R(list())\n"
            ),
        },
        "its own dict": {
            "a NamedTuple subclass's own dict holding a list": (
                "import typing\nclass R(typing.NamedTuple):\n    a: int\nclass R2(R):\n    pass\n"
                "X = R2(1)\nX.seen = list()\n"
            ),
        },
    },
    "pack frozen dataclass": {
        "admission": {
            "a dataclass not frozen": (
                "import dataclasses\n@dataclasses.dataclass\nclass D:\n    a: int = 1\nX = D()\n"
            ),
        },
        "its own dict": {
            "a frozen dataclass holding a list": (
                "import dataclasses\n@dataclasses.dataclass(frozen=True)\nclass D:\n"
                "    a: object = None\nX = D(list())\n"
            ),
            "a frozen dataclass in an alias's metadata, holding a partial": (
                "import dataclasses, functools, typing\n@dataclasses.dataclass(frozen=True)\n"
                "class D:\n    a: object = None\n"
                "X = typing.Annotated[str, D(functools.partial(print))]\n"
            ),
        },
    },
    "frozen pydantic model": {
        "admission": {
            "a model not frozen": (
                "import sys, types\n_f = types.ModuleType('retention_pins_foreign')\n"
                "sys.modules[_f.__name__] = _f\n"
                "exec('import pydantic\\nclass M(pydantic.BaseModel):"
                "\\n    model_config = pydantic.ConfigDict(frozen=False)\\n    a: int = 1\\n', "
                "_f.__dict__)\nX = _f.M()\ndel _f\n"
            ),
        },
        "its fields": {
            "a model's field holding a stateful object": (
                MANIFEST + "import hashlib\nX.__dict__['id'] = hashlib.sha256()\n"
            ),
        },
        "its fields set": {
            "a fields set that is a set subclass": (
                MANIFEST + "class S(set):\n    pass\n"
                "object.__setattr__(X, '__pydantic_fields_set__', S({'id'}))\n"
            ),
        },
        "its extra": {
            "a model's extra holding a stateful object": (
                MANIFEST + "import hashlib\n"
                "object.__setattr__(X, '__pydantic_extra__', {'k': hashlib.sha256()})\n"
            ),
        },
        "its private dict": {
            "a model's private dict holding a list": (
                MANIFEST + "object.__setattr__(X, '__pydantic_private__', {'_p': list()})\n"
            ),
        },
    },
    "exempt": {
        "admission": {
            "another instance of an exempt object's class": (
                "from aibi.packs.onco.importer import OncoImporter\nX = OncoImporter()\n"
            ),
        },
        "all it holds, as a frozen model's": {
            "a stateful object on the exempt importer": (
                "import hashlib\n"
                "X = __import__('aibi.packs.onco', fromlist=['PACK']).PACK.importer\n"
                "X.__dict__['_pins_seen'] = hashlib.sha256()\n"
                "def UNDO():\n    del X.__dict__['_pins_seen']\n"
            ),
        },
    },
    "JSON container": {
        "admission": {
            "a list subclass in a model's extra": (
                MANIFEST + "class L(list):\n    pass\n"
                "object.__setattr__(X, '__pydantic_extra__', {'k': L()})\n"
            ),
        },
        "its keys": {
            "a stateful key in a model's JSON": (
                MANIFEST + "import hashlib\n"
                "object.__setattr__(X, '__pydantic_extra__', {'k': {hashlib.sha256(): 1}})\n"
            ),
        },
        "its items": {
            "a stateful object in a model's JSON": (
                MANIFEST + "import hashlib\n"
                "object.__setattr__(X, '__pydantic_extra__', {'k': [hashlib.sha256()]})\n"
            ),
        },
    },
    "fields set": {
        "admission": {
            "a frozenset subclass in the fields set's slot": (
                MANIFEST + "class F(frozenset):\n    pass\n"
                "object.__setattr__(X, '__pydantic_fields_set__', F({'id'}))\n"
            ),
        },
        "each member, an exact str": {
            "a fields set holding an object": (
                MANIFEST + "object.__setattr__(X, '__pydantic_fields_set__', {object()})\n"
            ),
        },
    },
    "stop set": {
        "admission": {
            "a partial met first in an alias's metadata, then bound by the module": (
                "import functools, typing\n"
                "ALIAS = typing.Annotated[str, functools.partial(print)]\n"
                "X = ALIAS.__metadata__[0]\n"
            ),
        },
        "everything it holds, through plain containers and other stop-set objects": {
            "a partial over a stateful method, in an alias's metadata": (
                ACC + "import functools, typing\n"
                "X = typing.Annotated[str, functools.partial(Acc().add)]\n"
            ),
        },
        "as a callable's __self__": {
            "a partial's __call__ over a stateful method, in an alias's metadata": (
                ACC + "import functools, typing\n"
                "X = typing.Annotated[str, functools.partial(Acc().add).__call__]\n"
            ),
        },
    },
    "module machinery": {
        "admission": {
            "a __spec__ that is a namespace": "import types\n__spec__ = types.SimpleNamespace()\n",
        },
        "the shape of __loader__, __spec__ and __builtins__": {
            "a __loader__ that is an object": "__loader__ = object()\n",
            "a __builtins__ that is a copy": "__builtins__ = dict(__builtins__)\n",
        },
        "each item of __path__": {"a __path__ that holds an object": "__path__ = [object()]\n"},
        "the annotations' keys and values": {
            "a module's __annotations__ holding a list": "__annotations__ = {'a': list()}\n",
        },
    },
    "__all__": {
        "admission": {"an __all__ that is a tuple": "__all__ = ('X',)\nX = 1\n"},
        "each item, an exact str": {"an __all__ holding an int": "__all__ = ['X', 1]\nX = 1\n"},
    },
    "class machinery": {
        "admission": {
            "a plain class's _fields": "import hashlib\nclass C:\n    _fields = hashlib.sha256()\n",
        },
        "__annotations__": {
            "a class's __annotations__ holding a list": (
                "class C:\n    __annotations__ = {'a': list()}\n"
            ),
        },
        "__dataclass_fields__": {
            "a dataclass field's default factory": (
                "import dataclasses, hashlib\n@dataclasses.dataclass(frozen=True)\nclass D:\n"
                "    a: object = dataclasses.field(default_factory=hashlib.sha256().copy)\n"
            ),
            "a dataclass field's metadata holding a list": (
                "import dataclasses\n@dataclasses.dataclass(frozen=True)\nclass D:\n"
                "    a: int = dataclasses.field(default=1, metadata={'k': list()})\n"
            ),
        },
        "__dataclass_params__": {
            "a class's __dataclass_params__ that is a list": (
                "class C:\n    __dataclass_params__ = list()\n"
            ),
            "a dataclass's params holding a list": (
                "import dataclasses\n@dataclasses.dataclass(frozen=True)\nclass D:\n"
                "    a: int = 1\nD.__dataclass_params__.repr = list()\n"
            ),
        },
        "_field_defaults": {
            "a NamedTuple's default": (
                "import itertools, typing\nclass R(typing.NamedTuple):\n"
                "    n: object = itertools.count()\n"
            ),
            "a plain class's _field_defaults holding a list": (
                "class C:\n    _field_defaults = {'a': list()}\n"
            ),
        },
        "_fields": {
            "a tuple subclass's _fields holding an int": (
                "class C(tuple):\n    _fields = ('a', 1)\n"
            ),
        },
        "_abc_impl": {"a class's _abc_impl that is a list": "class C:\n    _abc_impl = list()\n"},
    },
    "__slotnames__": {
        "admission": {
            "a __slotnames__ not copyreg's": "class C:\n    __slotnames__ = ['seen']\n",
        },
        "each item, against what copyreg computes": {
            "a __slotnames__ with a name too many": (
                "class C:\n    __slots__ = ('a',)\n    __slotnames__ = ['a', 'b']\n"
            ),
        },
    },
    "repr_running": {
        "admission": {
            "an empty set in a closure that is not dataclasses'": (
                "def make():\n    s = set()\n    def g():\n        return s\n    return g\n"
                "G = make()\n"
            ),
        },
        "that it is empty": {
            "a dataclass's repr guard holding a name": (
                "import dataclasses\n@dataclasses.dataclass(frozen=True)\nclass D:\n"
                "    a: int = 1\n[c.cell_contents for c in D.__repr__.__closure__"
                " if type(c.cell_contents) is set][0].add('seen')\n"
            ),
        },
    },
}
"""For each rule of ``RULES``, its admission and each part of what it reads (its legs), objects
just outside it: each refused there, and nothing else of its module (each leg's narrowing is
pinned, whatever mutant widens or drops it)."""
SUBJECTS = {
    "a class of the pack's holding a list": "C", "a class keyed by a stateful object": "C",
    "a class whose qualified name holds a list": "C", "a function whose doc is a list": "f",
    "a function whose annotations hold a list": "f",
    "a function with a list default": "f", "a function with a list keyword default": "f",
    "a closure over a list": "G", "a function's __dict__ keyed by a stateful object": "f",
    "a function's __dict__ holding a list": "f", "a cached_property": "C",
    "a static method with a list default": "C", "a property whose getter has a list default": "C",
    "a static method's own dict holding a list": "C",
    "a static method's __annotations__ holding a list": "C",
    "a property in a class's dict, a getter with a list default": "C",
    "a __spec__ that is a namespace": "__spec__", "a __loader__ that is an object": "__loader__",
    "a __builtins__ that is a copy": "__builtins__", "a __path__ that holds an object": "__path__",
    "a module's __annotations__ holding a list": "__annotations__",
    "an __all__ that is a tuple": "__all__", "an __all__ holding an int": "__all__",
    "a plain class's _fields": "C", "a class's __annotations__ holding a list": "C",
    "a dataclass field's default factory": "D", "a dataclass field's metadata holding a list": "D",
    "a class's __dataclass_params__ that is a list": "C", "a NamedTuple's default": "R",
    "a dataclass's params holding a list": "D",
    "a plain class's _field_defaults holding a list": "C",
    "a tuple subclass's _fields holding an int": "C", "a class's _abc_impl that is a list": "C",
    "a __slotnames__ not copyreg's": "C", "a __slotnames__ with a name too many": "C",
    "an empty set in a closure that is not dataclasses'": "G",
    "a dataclass's repr guard holding a name": "D",
}  # fmt: skip
"""The name each twin's refusal is under, where it is not ``X``."""


def rule_twins(state: Any, modules: list[ModuleType]) -> Any:
    """Each twin's refusals under its subject (``hit``) and elsewhere in its module (``other``),
    by rule, leg and case; a twin that changed shared state undoes it (``UNDO``) once judged."""
    exempt = state.exemptions(modules)
    found: dict[str, dict[str, dict[str, dict[str, int]]]] = {}
    number = 0
    for rule, legs in RULE_TWINS.items():
        for leg, cases in legs.items():
            for case, source in cases.items():
                number += 1
                made = module(state, f"_pins_twin_{number}", source)
                try:
                    refused = state.allowed([made], exempt, state.names_of([source])).rejects
                finally:
                    undo = made.__dict__.get("UNDO")
                    if undo is not None:
                        undo()
                subject = f"{made.__name__}:{SUBJECTS.get(case, 'X')}"
                hit = [r for r in refused if r.path.startswith(subject)]
                counts = {"hit": len(hit), "other": len(refused) - len(hit)}
                found.setdefault(rule, {}).setdefault(leg, {})[case] = counts
    return found


STOP_HOLDERS = {
    "an alias's metadata": "import typing\nALIAS = typing.Annotated[str, HELD]\n",
    "a foreign frozen model's private dict": (
        MANIFEST.replace("X =", "MODEL =")
        + "object.__setattr__(MODEL, '__pydantic_private__', {'_p': HELD})\n"
    ),
}
STOP_MADE = {
    "builtins.method": "HELD = Acc().add\n",
    "functools.partial": "import functools\nHELD = functools.partial(Acc().add)\n",
    "pydantic.types.Tag": (
        "import pydantic\nHELD = pydantic.Tag('t')\nobject.__setattr__(HELD, 'tag', Acc())\n"
    ),
    "pydantic.types.Discriminator": "import pydantic\nHELD = pydantic.Discriminator(Acc().add)\n",
    "pydantic.json_schema.WithJsonSchema": (
        "import pydantic\nHELD = pydantic.WithJsonSchema({'k': [Acc()]})\n"
    ),
    "pydantic.functional_validators.PlainValidator": (
        "from pydantic.functional_validators import PlainValidator\n"
        "HELD = PlainValidator(Acc().add)\n"
    ),
    "aibi.core.schema.output._AnyOf": (
        "from aibi.core.schema import output\nHELD = output._AnyOf()\nHELD.kept = Acc()\n"
    ),
    "pydantic_core._pydantic_core.SchemaValidator": (
        "import pydantic_core\nfrom pydantic_core import core_schema\n"
        "HELD = pydantic_core.SchemaValidator(core_schema.no_info_plain_validator_function("
        "Acc().add))\n"
    ),
    "pydantic_core._pydantic_core.SchemaSerializer": (
        "import pydantic_core\nfrom pydantic_core import core_schema\n"
        "HELD = pydantic_core.SchemaSerializer(core_schema.any_schema(serialization="
        "core_schema.plain_serializer_function_ser_schema(Acc().add)))\n"
    ),
}
"""Each member of the stop set, made to hold a stateful object of the pack's (``Acc``), but
``SkipJsonSchema``, which holds nothing (no slot, no dict)."""
STOP_EMPTY = {"pydantic.json_schema.SkipJsonSchema": "it has no slot and no dict: it holds nothing"}


def stop_through(state: Any, modules: list[ModuleType]) -> Any:
    """Each stop-set member holding a stateful object of the pack's, in each foreign holder (an
    alias's metadata, a foreign frozen model, the exempt ``_ADAPTER``): each refused."""
    from aibi.packs.onco import build

    exempt = state.exemptions(modules)
    found: dict[str, dict[str, int]] = {}
    for index, (member, made) in enumerate(STOP_MADE.items()):
        for at, (holder, held) in enumerate(STOP_HOLDERS.items()):
            source = ACC + made + held + "del HELD\n"
            made_module = module(state, f"_pins_stop_{index}_{at}", source)
            refused = state.allowed([made_module], exempt, state.names_of([source])).rejects
            found.setdefault(member, {})[holder] = len(refused)
        source = ACC + made
        made_module = module(state, f"_pins_stop_{index}_adapter", source)
        adapter = vars(build._ADAPTER)  # pyright: ignore[reportPrivateUsage]
        adapter["_retention_pin"] = made_module.HELD
        del made_module.__dict__["HELD"]
        try:
            refused = state.allowed([build], exempt, state.names_of([source])).rejects
        finally:
            del adapter["_retention_pin"]
        found[member]["the exempt _ADAPTER's dict"] = len(refused)
    return {"found": found, "empty": sorted(STOP_EMPTY)}


# --- No value in a report -----------------------------------------------------------------------

DISCLOSED = "def make(name):\n    return type(name, (), {})()\n"
"""A class made at call time and named by a value (held in the module under a key that is one)."""


def disclosure(state: Any) -> Any:
    """A reject of an instance of a class named by random text, held under a key of random text:
    neither text is in the reject's path or kind; and a key's label is the process's keyed
    digest (an unkeyed one of a short key is brute-forced from a report), never the bare one."""
    import hashlib

    text = "".join(secrets.choice("abcdefghjkmnpqrstuvwxyz") for _ in range(16))
    made = module(state, "_pins_disclosure", DISCLOSED)
    made.__dict__[text + "k"] = made.make(text + "c")
    refused = state.allowed([made], {}, state.names_of([DISCLOSED])).rejects
    shown = [f"{r.path} {r.kind}" for r in refused]
    bare = hashlib.blake2b(b"ab1", digest_size=8).hexdigest()
    return {
        "rejects": len(refused),
        "holding": len([line for line in shown if text in line.casefold()]),
        "keyed": state.label("ab1", frozenset()) not in (f"#{bare}", "ab1"),
    }


# --- A reject's path, before and after a third party's read -------------------------------------

STABLE = (
    "import typing\nclass Plain:\n    pass\nP = Plain()\nP.a = 1\n"
    "X = typing.Annotated[str, list()]\nY = typing.Annotated[str, P]\n"
)
"""Typing objects and an instance whose dicts are not yet materialised (3.12 keeps an instance's
attributes inline until something asks for its ``__dict__``), each holding something refused."""


def stable(state: Any) -> Any:
    """The rejects of ``STABLE`` before and after ``vars()`` of each object: the same paths (an
    object's dict is materialised before its referents are read), so that a reject the
    allow-list had before the first call is never reported again after one."""
    made = module(state, "_pins_stable", STABLE)
    names = state.names_of([STABLE])
    before = sorted(r.path for r in state.allowed([made], {}, names).rejects)
    for value in (made.X, made.Y, made.P):
        vars(value)
    after = sorted(r.path for r in state.allowed([made], {}, names).rejects)
    return {"rejects": len(before), "same": before == after}


# --- Each kind of module, holding state ----------------------------------------------------------

MODULE_KINDS: dict[str, tuple[str, tuple[bool, bool]]] = {
    "a module made, not registered": ("import types\nX = types.ModuleType('x')\n", (True, True)),
    "a module made, registered under a foreign name": (
        "import sys, types\n"
        "X = sys.modules.setdefault('_pins_kind_a', types.ModuleType('_pins_kind_a'))\n",
        (True, True),
    ),
    "a module made, registered under the pack's prefix": (
        "import sys, types\nX = sys.modules.setdefault('aibi.packs.onco._pins_kind_b',"
        " types.ModuleType('aibi.packs.onco._pins_kind_b'))\n",
        (True, True),
    ),
    "a module subclass's instance, registered": (
        "import sys, types\nclass M(types.ModuleType):\n    pass\n"
        "X = sys.modules.setdefault('_pins_kind_c', M('_pins_kind_c'))\n",
        (True, True),
    ),
    "a module whose spec names another module": (
        "import sys, types, importlib.machinery as m\nX = types.ModuleType('_pins_kind_d')\n"
        "X.__spec__ = m.ModuleSpec('_pins_other', m.BuiltinImporter)\n"
        "X.__loader__ = m.BuiltinImporter\nsys.modules[X.__name__] = X\n",
        (True, True),
    ),
    "a module whose loader is None": (
        "import sys, types, importlib.machinery as m\nX = types.ModuleType('_pins_kind_e')\n"
        "X.__spec__ = m.ModuleSpec('_pins_kind_e', None)\nX.__loader__ = None\n"
        "sys.modules[X.__name__] = X\n",
        (True, True),
    ),
    "a pack module the import system made, not a root, holding a list": (
        "import importlib.util, os, sys, tempfile\n"
        "_p = os.path.join(tempfile.mkdtemp(), 'made.py')\n"
        "with open(_p, 'w') as _f:\n    _f.write('seen = []\\n')\n"
        "_s = importlib.util.spec_from_file_location('aibi.packs.onco._pins_kind_f', _p)\n"
        "X = importlib.util.module_from_spec(_s)\nsys.modules[X.__name__] = X\n"
        "_s.loader.exec_module(X)\ndel _p, _f, _s\n",
        (True, True),
    ),
    "a core module": ("import aibi.core.schema.jsonio as X\n", (False, True)),
    "a library module": ("import json as X\n", (False, False)),
    "a root": ("import sys\nX = sys.modules[__name__]\n", (False, True)),
}
"""Each kind of module a pack module may bind, and what the guards do when a call sets a value on
it: (refused by the allow-list, seen by the snapshot). A library module's attributes are named in
D438 as unheld (another library's state, as its classes' and functions' are)."""


def module_kinds(state: Any, modules: list[ModuleType]) -> Any:
    """Each kind of ``MODULE_KINDS``: the allow-list's refusal of the module, and whether the
    snapshot sees a value a call then sets on it (removed again once seen)."""
    exempt = state.exemptions(modules)
    found: dict[str, dict[str, list[bool]]] = {}
    for index, (kind, (source, expected)) in enumerate(MODULE_KINDS.items()):
        made = module(state, f"_pins_kinds_{index}", source)
        names = state.names_of([source])
        held = made.X
        refused = state.allowed([made], exempt, names).rejects
        before = state.snapshot([made], names)
        held._pins_seen = "ab"
        try:
            seen = bool(state.differences(before, state.snapshot([made], names)))
        finally:
            del held._pins_seen
        inside = f"{held.__name__}:"  # a module judged as a root: its own names' rejects
        hit = any(r.path.startswith((f"{made.__name__}:X", inside)) for r in refused)
        found[kind] = {"found": [hit, seen], "expected": list(expected)}
    return found


# --- Every attribute read of a walked object, made hostile ---------------------------------------

HOSTILE = (
    "ARMED = [False]\nRAN = []\n"
    "def hostile(base):\n"
    "    def spy(name):\n"
    "        def method(self, *args, **kwargs):\n"
    "            if ARMED[0]:\n"
    "                RAN.append(name)\n"
    "                raise RuntimeError(name)\n"
    "            return getattr(base, name)(self, *args, **kwargs)\n"
    "        return method\n"
    "    names = ('__len__', '__iter__', '__eq__', '__ne__', '__hash__', '__getitem__',\n"
    "             '__contains__', '__bool__', '__lt__', '__repr__', '__str__', 'keys', 'items',\n"
    "             'values', 'get', '__index__', 'startswith', 'endswith', '__reversed__', 'copy',\n"
    "             '__add__', '__format__', '__mul__')\n"
    "    found = {n: spy(n) for n in names if hasattr(base, n)}\n"
    "    found['__getattribute__'] = spy('__getattribute__')\n"
    "    return type('H' + base.__name__, (base,), found)\n"
    "S, T, D, L, F = hostile(str), hostile(tuple), hostile(dict), hostile(list), hostile(set)\n"
)
"""A subclass of each base type the guards read, each of its methods recording its call and
raising once armed."""
READERS: dict[str, dict[str, tuple[str, str]] | str] = {
    "_FUNC_DEFAULTS": {"__defaults__": ("def X(a=1): ...\nX.__defaults__ = T((1,))\n", "X")},
    "_FUNC_KWDEFAULTS": {
        "__kwdefaults__": ("def X(*, a=1): ...\nX.__kwdefaults__ = D(a=1)\n", "X"),
    },
    "_FUNC_DICT": {"__dict__": ("def X(): ...\nX.__dict__ = D()\n", "X")},
    "_FUNC_CLOSURE": {
        "__closure__": (
            "import types\ndef make():\n    v = 1\n    def g():\n        return v\n    return g\n"
            "X = types.FunctionType(make().__code__, globals(), 'g', None,"
            " T(make().__closure__))\n",
            "X",
        ),
    },
    "_FUNC_ANNOTATIONS": {"__annotations__": ("def X(): ...\nX.__annotations__ = D()\n", "X")},
    "_FUNC_MODULE": {"__module__": ("def X(): ...\nX.__module__ = S('m')\n", "X")},
    "_FUNC_QUALNAME": {
        "__qualname__": ("def X(): ...\nX.__qualname__ = S('q')\n", "X"),
        "__name__": ("def X(): ...\nX.__name__ = S('n')\n", "X"),
        "__doc__": ("def X(): ...\nX.__doc__ = S('d')\n", "X"),
    },
    "_CODE_FILENAME": {
        "co_filename": ("def X(): ...\nX.__code__ = X.__code__.replace(co_filename=S('f'))\n", ""),
    },
    "_TYPE_QUALNAME": {"a class's __qualname__": ("class X: ...\nX.__qualname__ = S('q')\n", "X")},
    "_TYPE_NAME": {"a class's __name__": ("class X: ...\nX.__name__ = S('n')\n", "X")},
    "_PARAMS_FROZEN": {
        "a _DataclassParams never initialised": (
            "import dataclasses\n@dataclasses.dataclass(frozen=True)\nclass C:\n    a: int = 1\n"
            "C.__dataclass_params__ = dataclasses._DataclassParams.__new__("
            "dataclasses._DataclassParams)\nX = C()\n",
            "X",
        ),
    },
    "_MEMBER_GET": {
        "a model slot planted from another class": (
            "class O:\n    __slots__ = ('__pydantic_extra__',)\n"
            "class C:\n    model_config = {'frozen': True}\n"
            "    __pydantic_extra__ = O.__dict__['__pydantic_extra__']\nX = C()\n",
            "X",
        ),
    },
    "_GETSET_GET": {
        "an instance's __dict__ that is a dict subclass": (
            "class C:\n    pass\nX = C()\nX.__dict__ = D(a=1)\n",
            "X",
        ),
    },
    "class dict entries": {
        "__slots__": ("class X:\n    __slots__ = T(('a',))\n", "X"),
        "model_config": ("class X:\n    model_config = D(frozen=True)\nY = X()\n", "X"),
        "_fields": ("class X(tuple):\n    _fields = T(('a',))\n", "X"),
        "__annotations__": ("class X:\n    __annotations__ = D()\n", "X"),
        "__dataclass_fields__": ("class X:\n    __dataclass_fields__ = D()\n", "X"),
        "_field_defaults": ("class X:\n    _field_defaults = D()\n", "X"),
        "__slotnames__": ("class X:\n    __slotnames__ = L()\n", "X"),
        "__module__": ("class X:\n    __module__ = S('m')\n", "X"),
        "_abc_impl": ("class X:\n    _abc_impl = L()\n", "X"),
        "__dataclass_params__": ("class X:\n    __dataclass_params__ = L()\n", "X"),
    },
    "module dict entries": {
        "__all__": ("__all__ = L(['ARMED'])\n", "__all__"),
        "__path__": ("__path__ = L()\n", "__path__"),
        "__annotations__": ("__annotations__ = D()\n", "__annotations__"),
        "__builtins__": ("__builtins__ = D(__builtins__)\n", "__builtins__"),
        "__spec__": (
            "import importlib.machinery\nclass Spec(importlib.machinery.ModuleSpec):\n    pass\n"
            "__spec__ = Spec('x', None)\n",
            "__spec__",
        ),
    },
    "a model's slots": {
        "__pydantic_fields_set__": (
            "from aibi.core.schema.pack_api import PackManifest\n"
            "X = PackManifest(id='p', version='0.0.1', results_version=1, requires_core='>=0')\n"
            "object.__setattr__(X, '__pydantic_fields_set__', F({'id'}))\n",
            "X",
        ),
        "__pydantic_extra__": (
            "from aibi.core.schema.pack_api import PackManifest\n"
            "X = PackManifest(id='p', version='0.0.1', results_version=1, requires_core='>=0')\n"
            "object.__setattr__(X, '__pydantic_extra__', D(k=1))\n",
            "X",
        ),
    },
    "a mappingproxy's dict": {
        "the dict behind it": ("import types\nX = types.MappingProxyType(D(k=1))\n", "X"),
    },
    "_SPEC_DICT": {
        "a module spec's __dict__ that is a dict subclass": (
            "import sys, types, importlib.machinery as m\nX = types.ModuleType('_pins_h_spec')\n"
            "_s = m.ModuleSpec('_pins_h_spec', None)\n_s.__dict__ = D(name='_pins_h_spec')\n"
            "X.__spec__ = _s\nX.__loader__ = m.BuiltinImporter\nsys.modules[X.__name__] = X\n"
            "del _s\n",
            "X",
        ),
    },
    "_TYPE_DICT": "a class's dict is read behind its mappingproxy, and is an exact dict always",
    "_TYPE_MRO": "the interpreter's own tuple: what a metaclass's mro() returns is copied into one",
    "_TYPE_MODULE": "read of a static type alone, whose name C code sets",
    "_TYPE_FLAGS": "an int the interpreter keeps",
    "_FUNC_CODE": "an exact code object: the setter takes no other type",
    "_FUNC_GLOBALS": "compared by identity alone, never read",
    "_FUNC_BUILTINS": "compared by identity alone, never read",
    "_MODULE_DICT": "a module's dict cannot be replaced, and is an exact dict",
    "_BUILTIN_SELF": "an object, judged by its own type",
    "_WRAPPER_SELF": "an object, judged by its own type",
    "_METHOD_SELF": "an object, judged by its own type",
    "_METHOD_FUNC": "an object, judged by its own type",
    "_CELL": "an object, judged by its own type; a closure's items are checked to be cells",
    "_ARRAY_TYPECODE": "a one-character str the array type keeps in C",
    "_ARRAY_TOBYTES": "the array type's own method, run on the array's C buffer",
    "_SUBCLASS": "type's own C subclass check, given types alone",
    "_DICT_ITEMS": "dict's own C method: a subclass's items are read from its C table",
    "_DICT_LEN": "dict's own C method, on its C table",
    "_LIST_COPY": "list's own C slice, on its C array",
    "_TUPLE_COPY": "tuple's own C slice, on its C array",
    "_STR_COPY": "str's own C slice, on its C buffer",
    "_BYTES_COPY": "bytes' own C slice, on its C buffer",
    "_BYTEARRAY_HEX": "bytearray's own C method, on its C buffer",
    "_MEMORYVIEW_TOBYTES": "an exact memoryview's own C method",
    "_STRINGIO_GETVALUE": "StringIO's own C method, on its C buffer",
    "_BYTESIO_GETVALUE": "BytesIO's own C method, on its C buffer",
    "_INT_INDEX": "int's own C slot, on its C digits",
    "_FLOAT_HEX": "float's own C method, on its C double",
    "_COMPLEX_PARTS": "floats a complex keeps in C",
}
"""Each reader of a walked object's attributes (``retention_state``'s ``_TYPE_*``, ``_FUNC_*``,
``_CODE_*``, ``_MODULE_*``, ``_BUILTIN_*``, ``_WRAPPER_*``, ``_METHOD_*``, ``_CELL``,
``_PARAMS_*``, ``_ARRAY_*``, ``_COMPLEX_*``, the getset and member readers) and each name read
from a class's, a module's or a model's dict: a twin setting it to a hostile subclass of the
type it is read as (its subject, the name it is refused under, or ``""`` where it holds nothing
and is not refused), or why no other type can be there."""


def _reader(name: str, value: object) -> bool:
    """Whether a module-level name of ``retention_state`` is a reader: a descriptor's getter, a
    getset or member descriptor, or a base type's own method (any callable but a Python function
    or a class), or a tuple of them."""
    if not re.fullmatch(r"_[A-Z][A-Z0-9_]*", name):
        return False
    held = value if type(value) is tuple else (value,)
    kinds = (GetSetDescriptorType, MemberDescriptorType)
    return bool(held) and all(
        type(item) in kinds or (callable(item) and not isinstance(item, FunctionType | type))
        for item in held
    )


def hostile_readers(state: Any, modules: list[ModuleType]) -> Any:
    """Each twin of ``READERS``, armed, through the allow-list and the snapshot: what of the
    hostile types ran, what raised, and whether the twin was refused under its subject."""
    exempt = state.exemptions(modules)
    found: dict[str, dict[str, Any]] = {}
    number = 0
    for reader, twins in READERS.items():
        if isinstance(twins, str):
            continue
        for twin, (source, subject) in twins.items():
            number += 1
            made = module(state, f"_pins_hostile_{number}", HOSTILE + source)
            names = state.names_of([HOSTILE + source])
            made.ARMED[0] = True
            error = None
            refused: list[Any] = []
            try:
                refused = state.allowed([made], exempt, names).rejects
                state.snapshot([made], names)
            except Exception as raised:  # what a twin raised, or the guard's own failure
                error = type(raised).__name__
            finally:
                made.ARMED[0] = False
            hit = bool(subject) and any(
                r.path.startswith(f"{made.__name__}:{subject}") for r in refused
            )
            ran = list(made.RAN)
            found.setdefault(reader, {})[twin] = {
                "ran": ran,
                "error": error,
                "refused": hit,
                "subject": bool(subject),
            }
    readers = sorted(n for n, v in vars(state).items() if _reader(n, v))
    return {"found": found, "readers": readers, "covered": sorted(READERS)}


# --- Each leaf, replaced in place by one of its size ---------------------------------------------

LEAF_HOLDERS = (
    "import array, io\n"
    "class Plain:\n    pass\n"
    "class K:\n    v = None\n"
    "P = Plain()\nP.v = None\nV = None\nL = [None]\nD = {'k': None}\n"
    "def make():\n    v = None\n    def g():\n        return v\n    return g\nG = make()\n"
    "def W(*, v=None):\n    return v\n"
    "class S(str):\n    pass\nclass LL(list):\n    pass\nclass DD(dict):\n    pass\n"
)


def _holders() -> dict[str, tuple[Callable[[ModuleType], object], Callable[..., None]]]:
    def cell(m: ModuleType, value: object) -> None:
        m.G.__closure__[0].cell_contents = value

    def keyword(m: ModuleType, value: object) -> None:
        m.W.__kwdefaults__["v"] = value

    return {
        "a module value": (lambda m: m.V, lambda m, v: m.__dict__.__setitem__("V", v)),
        "a list item": (lambda m: m.L[0], lambda m, v: m.L.__setitem__(0, v)),
        "a dict value": (lambda m: m.D["k"], lambda m, v: m.D.__setitem__("k", v)),
        "an instance attribute": (lambda m: m.P.v, lambda m, v: setattr(m.P, "v", v)),
        "a class attribute": (lambda m: m.K.v, lambda m, v: setattr(m.K, "v", v)),
        "a closure cell": (lambda m: m.G(), cell),
        "a keyword default": (lambda m: m.W(), keyword),
    }


def _leaves() -> dict[str, tuple[Callable[[ModuleType], object], Callable[[ModuleType, Any], Any]]]:
    """Each leaf the snapshot reads: (what to hold, what replaces it in place or anew, of its
    size, its length and its type), the change returning the new value for the holder (or
    ``None`` where the leaf was changed in place)."""
    from array import array as _array

    def own(kind: str, value: object) -> Callable[[ModuleType], object]:
        def made(m: ModuleType) -> object:
            item = getattr(m, kind)(value)
            item.x = 1
            return item

        return made

    def bump(_: ModuleType, item: Any) -> None:
        item.x = 2

    def setter(new: object) -> Callable[[ModuleType, Any], object]:
        return lambda _m, _item: new

    def refill(new: Any) -> Callable[[ModuleType, Any], None]:
        def change(_: ModuleType, item: Any) -> None:
            item.clear()
            item.update(new)

        return change

    def write(text: str) -> Callable[[ModuleType, Any], None]:
        def change(_: ModuleType, item: Any) -> None:
            item.seek(0)
            item.write(text)

        return change

    return {
        "str": (lambda _m: "ab", setter("cd")),
        "bytes": (lambda _m: b"ab", setter(b"cd")),
        "int": (lambda _m: 12, setter(34)),
        "float": (lambda _m: 1.5, setter(2.5)),
        "complex": (lambda _m: 1j, setter(2j)),
        "bool": (lambda _m: True, setter(False)),
        "bytearray": (lambda _m: bytearray(b"ab"), lambda _m, i: i.__setitem__(slice(None), b"cd")),
        "array": (lambda _m: _array("b", [1, 2]), lambda _m, i: i.__setitem__(0, 3)),
        "StringIO": (lambda _m: io.StringIO("ab"), write("cd")),
        "BytesIO": (lambda _m: io.BytesIO(b"ab"), write(b"cd")),  # type: ignore[arg-type]
        "a set's atom": (lambda _m: {"a"}, refill({"b"})),
        "a set's member that is not an atom": (lambda _m: {(1,)}, refill({(2,)})),
        "a frozenset's atom": (lambda _m: frozenset({"a"}), setter(frozenset({"b"}))),
        "a dict's key that is not a str": (
            lambda _m: {1: 0},
            refill({2: 0}),
        ),
        "a str subclass's own dict": (own("S", "ab"), bump),
        "a list subclass's own dict": (own("LL", [1]), bump),
        "a dict subclass's own dict": (own("DD", {"k": 1}), bump),
    }


def leaves(state: Any) -> Any:
    """Each leaf in each holder, replaced (or changed in place) by one of its size: the snapshot
    sees each; and a class's ``__slotnames__`` that is not ``copyreg``'s, changed in place."""
    missed = []
    cases = 0
    for at, (holder, (_, put)) in enumerate(_holders().items()):
        for index, (leaf, (make, change)) in enumerate(_leaves().items()):
            made = module(state, f"_pins_leaf_{at}_{index}", LEAF_HOLDERS)
            names = state.names_of([LEAF_HOLDERS])
            item = make(made)
            put(made, item)
            before = state.snapshot([made], names)
            new = change(made, item)
            if new is not None:
                put(made, new)
            cases += 1
            if not state.differences(before, state.snapshot([made], names)):
                missed.append(f"{leaf} in {holder}")
    made = module(state, "_pins_leaf_slotnames", "class C:\n    __slotnames__ = ['a']\n")
    names = state.names_of(["C"])
    before = state.snapshot([made], names)
    made.C.__dict__["__slotnames__"][0] = "b"
    if not state.differences(before, state.snapshot([made], names)):
        missed.append("a __slotnames__ not copyreg's")
    return {"cases": cases + 1, "missed": missed}


# --- After every call, against the first ---------------------------------------------------------


def order(state: Any, scan: Any) -> Any:
    """A state a first call sets, a second keeps and a third resets, through ``drive``."""
    made = module(state, "_pins_order", "VALUE = 0\n")
    guard = state.Guard([made])

    def set_() -> str:
        made.__dict__.update(KEPT=SimpleNamespace(n=1), VALUE=1)
        return "ok"

    def reset() -> str:
        del made.__dict__["KEPT"]
        made.__dict__["VALUE"] = 0
        return "ok"

    outcomes = scan.drive([set_, lambda: "ok", reset], guard.after)
    return {
        "outcomes": outcomes,
        "allow after": sorted({after for after, _ in guard.rejects}),
        "snapshot after": [after for after, _, _ in guard.changed],
    }


# --- The snapshot's reach ------------------------------------------------------------------------

ROUTES_SOURCE = (
    "import typing\nfrom dataclasses import dataclass\nfrom types import MappingProxyType\n"
    "from aibi.core.schema.descriptors import ParseSettings\n"
    "from aibi.core.schema.jsonio import is_text\n"
    "from aibi.core.schema.pack_api import PackManifest\n"
    "MANIFEST = PackManifest(id='pins', version='0.0.1', results_version=1, requires_core='>=0')\n"
    "VALUE = 0\n"
    "@dataclass(frozen=True)\nclass Frozen:\n    a: int = 1\nFROZEN = Frozen()\n"
    "class Plain:\n    pass\nPLAIN = Plain()\n"
    "def defaults(x=[0], *, y={'k': 0}): ...\n"
    "def make():\n    held = 0\n    def inner():\n        return held\n    return inner\n"
    "CLOSURE = make()\nSET = {0}\nPROXY = MappingProxyType({'k': [0]})\n"
    "ALIAS = typing.Annotated[str, [0]]\nAPPEND = [].append\n"
    "import types\nimport aibi.core.schema.jsonio as JSONIO\n"
    "type.__setattr__(ParseSettings, '_pins_bag', types.ModuleType('bag'))\n"
)
ROUTES: dict[str, Callable[[ModuleType], object]] = {
    "a module value": lambda m: setattr(m, "VALUE", 1),
    "a class attribute": lambda m: setattr(m.Frozen, "extra", 1),
    "an instance's attribute": lambda m: setattr(m.PLAIN, "a", 1),
    "a frozen instance's own dict": lambda m: object.__setattr__(m.FROZEN, "b", 1),
    "a function's __dict__": lambda m: setattr(m.defaults, "extra", 1),
    "a default": lambda m: m.defaults.__defaults__[0].append(1),
    "a keyword default": lambda m: m.defaults.__kwdefaults__["y"].update(k=1),
    "a closure cell": lambda m: setattr(m.CLOSURE.__closure__[0], "cell_contents", 1),
    "a set": lambda m: m.SET.add(1),
    "a mappingproxy's dict": lambda m: m.PROXY["k"].append(1),
    "a typing object's metadata": lambda m: m.ALIAS.__metadata__[0].append(1),
    "a bound method's __self__": lambda m: m.APPEND(1),
    "a pydantic model's private dict": lambda m: object.__setattr__(
        m.MANIFEST, "__pydantic_private__", {"_p": 1}
    ),
    "a core class": lambda m: type.__setattr__(m.ParseSettings, "_retention_pin", 1),
    "a core function": lambda m: m.is_text.__dict__.update(_retention_pin=1),
    "a module made, on a core class": lambda m: setattr(m.ParseSettings._pins_bag, "seen", 1),
    "a core module": lambda m: setattr(m.JSONIO, "_retention_pin", 1),
}
"""Each holder the snapshot enters, with a change of a value through it."""


def routes(state: Any) -> Any:
    made = module(state, "_pins_routes", ROUTES_SOURCE)
    names = state.names_of([ROUTES_SOURCE])
    before = state.snapshot([made], names)
    found: dict[str, bool] = {}
    for name, change in ROUTES.items():
        change(made)
        now = state.snapshot([made], names)
        found[name] = bool(state.differences(before, now))
        before = now
    return found


def digests(state: Any) -> Any:
    """A snapshot of a value and a key that are random text: neither is in any path, and every
    digest is an exact ``int`` (a hash, never the text)."""
    text = "".join(secrets.choice("abcdefghjkmnpqrstuvwxyz") for _ in range(16))
    made = module(state, "_pins_digests", "HELD = {}\n")
    made.HELD[text + "k"] = text + "v"
    made.__dict__["TEXT"] = (text + "t")[:-1]
    found = state.snapshot([made], state.names_of(["HELD = {}\n"]))
    held = [p for p, d in found.items() if text in p.casefold() or text in str(d).casefold()]
    shapes = all(type(digest) is int for digest in found.values())
    return {"nodes": len(found), "holding": len(held), "digests": shapes}


# --- The brute force -----------------------------------------------------------------------------

CONTAINERS = {
    "list": ("[]", {"append": "('v',)", "extend": "(['v'],)", "insert": "(0, 'v')",
                    "__setitem__": "(slice(0, 0), ['v'])", "__iadd__": "(['v'],)"}),
    "dict": ("{}", {"__setitem__": "('k', 'v')", "update": "({'k': 'v'},)",
                    "setdefault": "('k', 'v')"}),
    "set": ("set()", {"add": "('v',)", "update": "(['v'],)"}),
    "deque": ("_c.deque(maxlen=128)", {"append": "('v',)", "appendleft": "('v',)",
                                       "extend": "(['v'],)"}),
    "bytearray": ("bytearray()", {"append": "(1,)", "extend": "(b'v',)", "__iadd__": "(b'v',)"}),
    "array": ("_ar.array('b')", {"append": "(1,)", "extend": "([1],)", "frombytes": "(b'v',)"}),
    "namespace": ("_ns.SimpleNamespace()", {"__setattr__": "('k', 'v')"}),
    "ordereddict": ("_c.OrderedDict()", {"__setitem__": "('k', 'v')",
                                         "update": "({'k': 'v'},)"}),
    "defaultdict": ("_c.defaultdict(list)", {"__getitem__": "('k',)"}),
    "counter": ("_c.Counter()", {"update": "(['v'],)"}),
}  # fmt: skip
"""Each mutable builtin container, with its mutating bound methods and the arguments of a call."""
HOLDERS = ("module", "class", "default", "kwdefault", "cell", "tuple", "exempt")


def brute_source() -> tuple[str, list[tuple[str, str, str, str]]]:
    """The brute force's module: each cell binds ``<container>.<method>`` in one holder, and
    ``MUTATIONS`` calls each once; the cells, by tag, container, method and holder."""
    lines = [
        "import array as _ar", "import collections as _c", "import types as _ns",
        "import aibi.packs.onco as _pack", "class _K:", "    pass", "MUTATIONS = []",
    ]  # fmt: skip
    cells = []
    for container, (make, methods) in CONTAINERS.items():
        for method, arguments in methods.items():
            for holder in HOLDERS:
                tag = f"{container}_{method.strip('_')}_{holder}"
                bound = f"({make}).{method}"
                if holder == "module":
                    lines.append(f"M_{tag} = {bound}")
                    call = owner = f"M_{tag}"
                elif holder == "class":
                    lines.append(f"setattr(_K, {tag!r}, {bound})")
                    call, owner = f"_K.__dict__[{tag!r}]", "_K"
                elif holder == "default":
                    lines.append(f"def F_{tag}(_m={bound}):\n    return _m")
                    call, owner = f"F_{tag}()", f"F_{tag}"
                elif holder == "kwdefault":
                    lines.append(f"def W_{tag}(*, _m={bound}):\n    return _m")
                    call, owner = f"W_{tag}()", f"W_{tag}"
                elif holder == "cell":
                    lines.append(
                        f"def _mk_{tag}():\n    m = {bound}\n    def g():\n        return m\n"
                        f"    return g\nG_{tag} = _mk_{tag}()"
                    )
                    call, owner = f"G_{tag}()", f"G_{tag}"
                elif holder == "tuple":
                    lines.append(f"T_{tag} = ({bound},)")
                    call, owner = f"T_{tag}[0]", f"T_{tag}"
                else:
                    lines.append(f"_pack.PACK.importer.__dict__[{tag!r}] = {bound}")
                    call = f"_pack.PACK.importer.__dict__[{tag!r}]"
                    owner = "_pack.PACK.importer"
                lines.append(
                    f"MUTATIONS.append(({tag!r}, lambda: ({call})(*{arguments}), {owner}))"
                )
                cells.append((tag, container, method, holder))
    return "\n".join(lines) + "\n", cells


def brute(state: Any, modules: list[ModuleType], names: frozenset[str]) -> Any:
    source, cells = brute_source()
    made = module(state, "_pins_brute", source)
    names = names | state.names_of([source])
    judged = state.allowed([made, modules[0]], state.exemptions(modules), names)
    paths = [reject.path for reject in judged.rejects]
    allow_missed = [
        tag
        for tag, *_ in cells
        if not any(re.search(rf"(?<![A-Za-z0-9]){tag}(?![A-Za-z0-9])", p) for p in paths)
    ]
    snapshot_missed = []
    nodes = 0
    for tag, mutate, owner in made.MUTATIONS:
        before = state.snapshot([owner], names)
        mutate()
        now = state.snapshot([owner], names)
        nodes = max(nodes, len(now))
        if not state.differences(before, now):
            snapshot_missed.append(tag)
    return {
        "cells": len(cells),
        "mutations": len(made.MUTATIONS),
        "containers": sorted({c for _, c, _, _ in cells}),
        "methods": len({(c, m) for _, c, m, _ in cells}),
        "holders": sorted({h for *_, h in cells}),
        "allow missed": allow_missed,
        "snapshot missed": snapshot_missed,
        "nodes": nodes,
    }


# --- Totality ------------------------------------------------------------------------------------

TWINS = """
import dataclasses
import types

RAN = []
ARMED = [False]


def _boom(name):
    if ARMED[0]:
        RAN.append(name)
        raise RuntimeError("a twin ran: " + name)


class _Meta(type):
    def __eq__(cls, other):
        _boom("metaclass __eq__")
        return type.__eq__(cls, other)

    def __hash__(cls):
        _boom("metaclass __hash__")
        return type.__hash__(cls)

    def __getattribute__(cls, name):
        _boom("metaclass __getattribute__")
        return type.__getattribute__(cls, name)

    def __instancecheck__(cls, obj):
        _boom("metaclass __instancecheck__")
        return False

    def __subclasscheck__(cls, sub):
        _boom("metaclass __subclasscheck__")
        return False


class TMeta(metaclass=_Meta):
    pass


T_META_INSTANCE = TMeta()


class _Params:
    @property
    def frozen(self):
        _boom("a fake __dataclass_params__'s frozen")
        return True

    def __eq__(self, other):
        _boom("a fake __dataclass_params__'s __eq__")
        return False

    __hash__ = object.__hash__


class TFakeDataclass:
    __dataclass_params__ = _Params()
    __dataclass_fields__ = {}


T_FAKE_DC = TFakeDataclass()


class _HDict(dict):
    def get(self, *a):
        _boom("model_config.get")

    def __getitem__(self, k):
        _boom("model_config.__getitem__")

    def __contains__(self, k):
        _boom("model_config.__contains__")


class TFakeModel:
    model_config = _HDict(frozen=True)


T_FAKE_MODEL = TFakeModel()


class _NotDict:
    def __getattr__(self, n):
        _boom("a model_config that is not a dict")


class TFakeModel2:
    model_config = _NotDict()


T_FAKE_MODEL2 = TFakeModel2()


class TFakeTuple(tuple):
    _fields = ("a",)

    def __getitem__(self, i):
        _boom("tuple __getitem__")

    def __iter__(self):
        _boom("tuple __iter__")

    def __len__(self):
        _boom("tuple __len__")


T_FAKE_NT = TFakeTuple(("x",))


class TSpoof:
    @property
    def __class__(self):
        _boom("a spoofed __class__")
        return tuple


T_SPOOF = TSpoof()


class TDictProp:
    @property
    def __dict__(self):
        _boom("a __dict__ property")
        return {}


T_DICT_PROP = TDictProp()


class _HKey:
    def __hash__(self):
        _boom("a key's __hash__")
        return 1

    def __eq__(self, other):
        _boom("a key's __eq__")
        return False

    def __repr__(self):
        _boom("a key's __repr__")
        return "k"


_K1 = _HKey()


class _HList(list):
    def __getitem__(self, i):
        _boom("list __getitem__")

    def __iter__(self):
        _boom("list __iter__")

    def __len__(self):
        _boom("list __len__")


class _HDict2(dict):
    def items(self):
        _boom("dict items")

    def __iter__(self):
        _boom("dict __iter__")

    def keys(self):
        _boom("dict keys")


class _Hostile:
    def __getattribute__(self, n):
        _boom("an instance's __getattribute__")
        return object.__getattribute__(self, n)

    def __getattr__(self, n):
        _boom("an instance's __getattr__")

    def __repr__(self):
        _boom("an instance's __repr__")
        return "h"


def _f(self):
    return None


T_METHOD = types.MethodType(_f, _Hostile())


@dataclasses.dataclass(frozen=True)
class TDC:
    a: object = None


T_DC_HOSTILE = TDC(_Hostile())
T_CONTAINERS = (_HList([1]), _HDict2({_K1: 1}), frozenset({_K1}), TMeta, T_META_INSTANCE)


class _HStr(str):
    def __eq__(self, o):
        _boom("a str subclass's __eq__")
        return False

    __hash__ = str.__hash__

    def __str__(self):
        _boom("a str subclass's __str__")
        return ""


T_STR = _HStr("abc")


def t_defaults(a=T_META_INSTANCE, *, b=_HKey()):
    return a, b
"""
"""One hostile twin for each predicate of the rules; armed, each records that it ran and
raises."""


def twins(state: Any, modules: list[ModuleType]) -> Any:
    made = module(state, "_pins_twins", TWINS)
    names = state.names_of([TWINS])
    exempt = state.exemptions(modules)
    made.ARMED[0] = True
    error = None
    rejects: list[str] = []
    nodes = 0
    try:
        rejects = [reject.path for reject in state.allowed([made], exempt, names).rejects]
        nodes = len(state.snapshot([made], names))
    except Exception as raised:  # what a twin raised, or the guard's own failure
        error = f"{type(raised).__name__}: {raised}"
    finally:
        made.ARMED[0] = False
    return {"ran": list(made.RAN), "error": error, "rejects": sorted(rejects), "nodes": nodes}


def main() -> None:
    fresh_start = "aibi.packs.onco" not in sys.modules
    state = _load(STATE, "retention_state")
    scan = _load(SCAN, "retention_scan")
    modules = state.pack_modules()
    names = state.pack_names(modules)
    report = {"fresh_start": fresh_start}
    report["representation"] = representation(state, modules, names)  # first: see the docstring
    report["clean"] = clean(state, modules, names)
    report["order"] = order(state, scan)
    report["routes"] = routes(state)
    report["digests"] = digests(state)
    report["brute"] = brute(state, modules, names)
    report["rule twins"] = rule_twins(state, modules)
    report["stop through"] = stop_through(state, modules)
    report["leaves"] = leaves(state)
    report["disclosure"] = disclosure(state)
    report["stable"] = stable(state)
    report["module kinds"] = module_kinds(state, modules)
    report["hostile readers"] = hostile_readers(state, modules)
    report["twins"] = twins(state, modules)  # last: armed
    print(json.dumps(report))


if __name__ == "__main__":
    main()
